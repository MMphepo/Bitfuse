import logging
from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone
from django.conf import settings
from rest_framework.exceptions import PermissionDenied

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount
from accounts.services import ensure_user_wallets, fetch_wallet_balance
from withdrawals.models import Withdrawal, WithdrawalConfig
from withdrawals.services.blockchain import get_blockchain_provider

logger = logging.getLogger(__name__)


class WithdrawalError(Exception):
    """Business rule or validation failure for withdrawals."""
    pass


def check_kyc_status(user):
    """Ensure user is fully verified before allowing withdrawals."""
    if getattr(user, "verification_status", "unverified") != "verified":
        raise PermissionDenied("Complete identity verification before withdrawing from Bitfuse.")


def get_withdrawal_quote(user, amount: Decimal) -> dict:
    """Calculate withdrawal fees, limits, and net amount for the user."""
    check_kyc_status(user)

    config_obj = WithdrawalConfig.get_current()
    if config_obj.withdrawals_frozen:
        raise WithdrawalError("Withdrawals are temporarily frozen by the administrator.")

    if amount <= Decimal("0"):
        raise WithdrawalError("Withdrawal amount must be greater than zero.")

    if amount < config_obj.min_usdt_withdrawal:
        raise WithdrawalError(f"Amount is below the minimum withdrawal limit of {config_obj.min_usdt_withdrawal} USDT.")

    if amount > config_obj.max_usdt_withdrawal:
        raise WithdrawalError(f"Amount exceeds the maximum withdrawal limit of {config_obj.max_usdt_withdrawal} USDT.")

    fee = config_obj.withdrawal_fee
    net_amount = amount - fee
    if net_amount <= Decimal("0"):
        raise WithdrawalError("Withdrawal amount is too small to cover the applicable fee.")

    return {
        "asset": "USDT",
        "network": "TRON",
        "amount": amount,
        "fee": fee,
        "net_amount": net_amount,
    }


def initiate_withdrawal(user, asset: str, network: str, amount: Decimal, destination_address: str) -> Withdrawal:
    """Initiates the secure USDT withdrawal system for verified users.

    Ensures KYC status, balance checks, TRON address validation, Blnk locking,
    blockchain transaction broadcasting, and idempotency protection.
    """
    # 1. Validation gates
    check_kyc_status(user)

    if asset.strip().upper() != "USDT":
        raise WithdrawalError("Only USDT withdrawals are supported at this stage.")

    if network.strip().upper() != "TRON":
        raise WithdrawalError("Only TRON (TRC-20) network is supported.")

    config_obj = WithdrawalConfig.get_current()
    if config_obj.withdrawals_frozen:
        raise WithdrawalError("Withdrawals are temporarily frozen by the administrator.")

    # Check limit boundaries
    if amount <= Decimal("0"):
        raise WithdrawalError("Withdrawal amount must be greater than zero.")
    if amount < config_obj.min_usdt_withdrawal:
        raise WithdrawalError(f"Amount is below the minimum withdrawal limit of {config_obj.min_usdt_withdrawal} USDT.")
    if amount > config_obj.max_usdt_withdrawal:
        raise WithdrawalError(f"Amount exceeds the maximum withdrawal limit of {config_obj.max_usdt_withdrawal} USDT.")

    fee = config_obj.withdrawal_fee
    net_amount = amount - fee
    if net_amount <= Decimal("0"):
        raise WithdrawalError("Withdrawal amount is too small to cover the applicable fee.")

    # Validate destination address
    provider = get_blockchain_provider(network)
    if not provider.validate_address(destination_address):
        raise WithdrawalError(f"The address '{destination_address}' is not a valid TRON address.")

    # Fetch Blnk Wallet and balance
    mwk_wallet, usdt_wallet = ensure_user_wallets(user)
    balances = fetch_wallet_balance(user)
    available_usdt = balances.get("USDT", Decimal("0"))

    # Check that user has enough USDT to pay amount (which already includes the fee, or net_amount + fee = amount)
    # Note: user inputs withdrawal amount. Total balance deducted from user is 'amount'.
    # Example: user inputs 50. Amount is 50. Fee is 0.50. Net amount is 49.50.
    # Total user balance deducted is 50 (available_usdt >= amount).
    if available_usdt < amount:
        raise WithdrawalError(f"Insufficient USDT balance. Available: {available_usdt} USDT, Required: {amount} USDT.")

    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    if not platform.usdt_frozen_balance_id:
        raise RuntimeError("Platform USDT frozen balance ID is not initialized in PlatformAccount.")

    blnk_client = BlnkClient()
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]
    raw_amount = int(amount * precision_usdt)

    withdrawal = None

    try:
        # Wrap database operations in atomic transaction & use select_for_update on PlatformAccount or a lock
        with db_transaction.atomic():
            # Create withdrawal record
            withdrawal = Withdrawal.objects.create(
                user=user,
                asset="USDT",
                network="TRON",
                amount=amount,
                fee=fee,
                net_amount=net_amount,
                destination_address=destination_address,
                status="PENDING",
            )

            # Step 1: Reserve funds (move user wallet -> platform frozen escrow)
            # Idempotent reference: withdrawal-reserve-<id>
            reserve_ref = f"withdrawal-reserve-{withdrawal.id}"
            try:
                txn = blnk_client.create_transaction(
                    amount=raw_amount,
                    currency="USDT",
                    precision=precision_usdt,
                    reference=reserve_ref,
                    source=usdt_wallet.blnk_balance_id,
                    destination=platform.usdt_frozen_balance_id,
                    description=f"Reserve USDT for withdrawal {withdrawal.id}",
                )
                withdrawal.blnk_transaction_refs.append(txn["transaction_id"])
                withdrawal.status = "PROCESSING"
                withdrawal.save(update_fields=["status", "blnk_transaction_refs"])
            except Exception as e:
                logger.error(f"Blnk reservation failed for withdrawal {withdrawal.id}: {str(e)}")
                withdrawal.status = "FAILED"
                withdrawal.failure_reason = f"Internal ledger reservation failure: {str(e)}"
                withdrawal.save(update_fields=["status", "failure_reason"])
                raise WithdrawalError(f"Ledger reservation failed: {str(e)}")

        # Step 2: Build and Broadcast blockchain transaction (outside of active database locks/transactions to prevent holding connections open)
        try:
            tx_data = provider.build_transfer_transaction(destination_address, net_amount)
            tx_hash = provider.broadcast_transaction(tx_data)

            # Step 3: Finalize Ledger movement (move platform frozen -> platform external contra)
            # Since the broadcast was successful, finalization must occur.
            with db_transaction.atomic():
                locked_withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)
                locked_withdrawal.status = "BROADCAST"
                locked_withdrawal.transaction_hash = tx_hash
                locked_withdrawal.broadcast_at = timezone.now()

                finalize_ref = f"withdrawal-finalize-{withdrawal.id}"
                try:
                    txn_finalize = blnk_client.create_transaction(
                        amount=raw_amount,
                        currency="USDT",
                        precision=precision_usdt,
                        reference=finalize_ref,
                        source=platform.usdt_frozen_balance_id,
                        destination=platform.usdt_external_contra_id,
                        description=f"Finalize external transfer for withdrawal {withdrawal.id}",
                    )
                    locked_withdrawal.blnk_transaction_refs.append(txn_finalize["transaction_id"])
                except Exception as blnk_err:
                    # Log finalization error, but don't mark as FAILED because funds have already been broadcast on-chain!
                    # This will be picked up by the monitoring or reconciliation task.
                    logger.error(f"Blnk finalization failed but TX broadcasted. Withdrawal {withdrawal.id}: {str(blnk_err)}")

                locked_withdrawal.save(update_fields=["status", "transaction_hash", "broadcast_at", "blnk_transaction_refs"])
                return locked_withdrawal

        except Exception as chain_err:
            logger.error(f"Blockchain broadcast failed for withdrawal {withdrawal.id}: {str(chain_err)}")
            # Definite blockchain failure before broadcast: release the reserved funds back to user
            with db_transaction.atomic():
                locked_withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)
                locked_withdrawal.status = "FAILED"
                locked_withdrawal.failure_reason = f"Blockchain broadcast failed: {str(chain_err)}"

                refund_ref = f"withdrawal-refund-{withdrawal.id}"
                try:
                    txn_refund = blnk_client.create_transaction(
                        amount=raw_amount,
                        currency="USDT",
                        precision=precision_usdt,
                        reference=refund_ref,
                        source=platform.usdt_frozen_balance_id,
                        destination=usdt_wallet.blnk_balance_id,
                        description=f"Refund failed withdrawal {withdrawal.id}",
                    )
                    locked_withdrawal.blnk_transaction_refs.append(txn_refund["transaction_id"])
                except Exception as blnk_refund_err:
                    logger.critical(f"FATAL: Blnk refund failed for withdrawal {withdrawal.id}: {str(blnk_refund_err)}")

                locked_withdrawal.save(update_fields=["status", "failure_reason", "blnk_transaction_refs"])
                return locked_withdrawal

    except Exception as exc:
        if withdrawal and withdrawal.status == "PENDING":
            withdrawal.status = "FAILED"
            withdrawal.failure_reason = str(exc)
            withdrawal.save(update_fields=["status", "failure_reason"])
        raise exc


def monitor_broadcast_withdrawals() -> int:
    """Scan and process withdrawals in 'BROADCAST' status.

    Checks blockchain status and either:
    - Marks as 'CONFIRMED' if on-chain transaction succeeded.
    - Refunds user and marks as 'FAILED' if on-chain transaction definitively failed.
    - Keeps as 'BROADCAST' if still pending.

    Returns the number of processed/terminal transitions.
    """
    broadcast_txs = Withdrawal.objects.filter(status="BROADCAST")
    processed_count = 0

    platform = PlatformAccount.objects.first()
    if not platform:
        logger.error("PlatformAccount not configured.")
        return 0

    blnk_client = BlnkClient()
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    for w in broadcast_txs:
        # Wrap each monitoring check inside an atomic block with row locking to ensure idempotency and prevent concurrent race conditions
        with db_transaction.atomic():
            locked_w = Withdrawal.objects.select_for_update().get(id=w.id)
            if locked_w.status != "BROADCAST":
                continue  # already processed by another worker

            if not locked_w.transaction_hash:
                continue

            try:
                provider = get_blockchain_provider(locked_w.network)
                tx_status = provider.get_transaction_status(locked_w.transaction_hash)
            except Exception as e:
                logger.error(f"Error querying blockchain status for withdrawal {locked_w.id}: {str(e)}")
                continue

            if tx_status == "SUCCESS":
                locked_w.status = "CONFIRMED"
                locked_w.confirmed_at = timezone.now()
                locked_w.save(update_fields=["status", "confirmed_at"])
                processed_count += 1
                logger.info(f"Withdrawal {locked_w.id} successfully CONFIRMED on-chain.")

            elif tx_status == "FAILED":
                # Definitive failure: refund the user's USDT wallet from platform external contra
                locked_w.status = "FAILED"
                locked_w.failure_reason = "Blockchain transaction failed on-chain."

                raw_amount = int(locked_w.amount * precision_usdt)
                _, usdt_wallet = ensure_user_wallets(locked_w.user)
                refund_ref = f"withdrawal-refund-{locked_w.id}"

                try:
                    txn_refund = blnk_client.create_transaction(
                        amount=raw_amount,
                        currency="USDT",
                        precision=precision_usdt,
                        reference=refund_ref,
                        source=platform.usdt_external_contra_id,
                        destination=usdt_wallet.blnk_balance_id,
                        description=f"Refund failed on-chain withdrawal {locked_w.id}",
                    )
                    locked_w.blnk_transaction_refs.append(txn_refund["transaction_id"])
                except Exception as refund_err:
                    logger.critical(f"FATAL: Blnk refund failed for failed on-chain withdrawal {locked_w.id}: {str(refund_err)}")

                locked_w.save(update_fields=["status", "failure_reason", "blnk_transaction_refs"])
                processed_count += 1
                logger.warn(f"Withdrawal {locked_w.id} FAILED on-chain, refunded user balance.")

    return processed_count
