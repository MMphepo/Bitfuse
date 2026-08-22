import logging
from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone
from django.conf import settings
from decouple import config

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount
from accounts.services import ensure_user_wallets
from withdrawals.models import Withdrawal, DepositRecord, WithdrawalNetworkConfig
from withdrawals.services.blockchain import get_blockchain_provider
from withdrawals.services.blockchain.bsc import BscProvider, to_checksum_address

logger = logging.getLogger(__name__)


def check_bsc_gas_balance() -> dict:
    """Monitor native BNB operational gas balance for the BSC hot wallet."""
    bsc_provider = BscProvider()
    treasury_address = bsc_provider.signer.get_address()
    balance = bsc_provider.get_native_bnb_balance(treasury_address)

    threshold = Decimal(config("BSC_GAS_ALERT_THRESHOLD", default="0.1"))
    alert = balance < threshold

    if alert:
        logger.warning(f"LOW GAS ALERT: BSC operational wallet {treasury_address} BNB balance ({balance} BNB) below threshold {threshold} BNB!")

    return {
        "wallet_address": treasury_address,
        "bnb_balance": balance,
        "threshold": threshold,
        "alert": alert,
    }


def process_bsc_deposit_event(event_data: dict) -> DepositRecord:
    """Process incoming BSC BEP-20 USDT transfer event independently.

    Verification criteria:
    1. network == BSC
    2. token contract == configured approved BSC USDT contract
    3. recipient == Bitfuse deposit address
    4. amount > 0
    5. idempotency key (event_id: chain_id:tx_hash:log_index) prevents duplicate crediting.
    """
    event_id = event_data["event_id"]
    tx_hash = event_data["tx_hash"]
    log_index = event_data.get("log_index", 0)
    from_address = to_checksum_address(event_data["from_address"])
    to_address = to_checksum_address(event_data["to_address"])
    amount = Decimal(str(event_data["amount"]))
    block_number = int(event_data["block_number"])
    confirmations = int(event_data.get("confirmations", 0))
    user = event_data.get("user")  # optional associated user if deposit address mapped

    bsc_provider = BscProvider()

    # 1. Independent on-chain verification of the receipt & transfer log
    if not bsc_provider.verify_transfer(tx_hash, to_address, amount):
        raise ValueError(f"Deposit transfer log verification failed for tx {tx_hash}")

    confirmations_required = int(config("BSC_CONFIRMATIONS_REQUIRED", default="12"))

    with db_transaction.atomic():
        deposit, created = DepositRecord.objects.select_for_update().get_or_create(
            event_id=event_id,
            defaults={
                "user": user,
                "network": "BSC",
                "asset": "USDT",
                "tx_hash": tx_hash,
                "log_index": log_index,
                "from_address": from_address,
                "to_address": to_address,
                "amount": amount,
                "block_number": block_number,
                "confirmations": confirmations,
                "status": "PENDING",
            }
        )

        if deposit.status == "CREDITED":
            logger.info(f"Deposit {event_id} already CREDITED.")
            return deposit

        deposit.confirmations = confirmations

        if confirmations >= confirmations_required and user and deposit.status != "CREDITED":
            # Credit Blnk user balance
            platform = PlatformAccount.objects.first()
            if not platform:
                raise RuntimeError("PlatformAccount not found.")

            _, usdt_wallet = ensure_user_wallets(user)
            blnk_client = BlnkClient()
            precision_usdt = settings.CURRENCY_PRECISION["USDT"]
            raw_amount = int(amount * precision_usdt)

            deposit_ref = f"deposit-credit-{deposit.event_id}"
            txn = blnk_client.create_transaction(
                amount=raw_amount,
                currency="USDT",
                precision=precision_usdt,
                reference=deposit_ref,
                source=platform.usdt_float_balance_id,
                destination=usdt_wallet.blnk_balance_id,
                description=f"Deposit credit for {deposit.event_id}",
            )

            deposit.status = "CREDITED"
            deposit.blnk_transaction_id = txn["transaction_id"]
            deposit.save(update_fields=["status", "blnk_transaction_id", "confirmations"])
            logger.info(f"Successfully credited deposit {deposit.event_id} to user {user}")

        else:
            deposit.status = "CONFIRMED" if confirmations >= confirmations_required else "PENDING"
            deposit.save(update_fields=["status", "confirmations"])

        return deposit


def run_reconciliation() -> dict:
    """Periodic audit service comparing Bitfuse records, BSC transactions, and Blnk ledger entries."""
    discrepancies = []

    # 1. Check completed withdrawals have valid broadcast/tx hashes
    broadcast_stuck = Withdrawal.objects.filter(status="BROADCAST", broadcast_at__lt=timezone.now() - timezone.timedelta(minutes=30))
    for w in broadcast_stuck:
        discrepancies.append({
            "type": "STUCK_BROADCAST_WITHDRAWAL",
            "id": str(w.id),
            "tx_hash": w.transaction_hash,
            "message": f"Withdrawal {w.id} has been in BROADCAST state for > 30 mins."
        })

    # 2. Check failed withdrawals without refund references
    failed_no_refund = Withdrawal.objects.filter(status="FAILED", blnk_transaction_refs=[])
    for w in failed_no_refund:
        discrepancies.append({
            "type": "FAILED_WITHDRAWAL_NO_REFUND_REF",
            "id": str(w.id),
            "message": f"Withdrawal {w.id} failed but has no Blnk ledger reference."
        })

    if discrepancies:
        logger.error(f"Reconciliation found {len(discrepancies)} discrepancies: {discrepancies}")

    return {
        "timestamp": timezone.now().isoformat(),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
    }
