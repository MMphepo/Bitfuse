"""Account/wallet services — the bridge between the Django business engine and Blnk."""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction

from accounts.blnk_client import BlnkClient
import random
import string
from accounts.models import Notification, PlatformAccount, Transfer, User, Wallet

logger = logging.getLogger(__name__)


def ensure_user_wallets(user: User) -> tuple[Wallet, Wallet]:
    """Lazily create the user's Blnk ledger + MWK/USDT wallets if missing.

    Returns (mwk_wallet, usdt_wallet).
    """
    client = BlnkClient()
    mwk_wallet = Wallet.objects.filter(user=user, currency="MWK").first()
    usdt_wallet = Wallet.objects.filter(user=user, currency="USDT").first()

    if mwk_wallet and usdt_wallet:
        return mwk_wallet, usdt_wallet

    # Create (or reuse) the user's Blnk ledger.
    ledger_id = user.blnk_ledger_id
    if not ledger_id:
        ledger = client.create_ledger(f"Bitfuse User — {user.username}", {"user_id": str(user.id)})
        ledger_id = ledger["ledger_id"]
        user.blnk_ledger_id = ledger_id
        user.save(update_fields=["blnk_ledger_id"])

    if not mwk_wallet:
        balance = client.create_balance(ledger_id, "MWK", {"user_id": str(user.id), "currency": "MWK"})
        mwk_wallet = Wallet.objects.create(
            user=user, currency="MWK", blnk_balance_id=balance["balance_id"]
        )

    if not usdt_wallet:
        balance = client.create_balance(ledger_id, "USDT", {"user_id": str(user.id), "currency": "USDT"})
        usdt_wallet = Wallet.objects.create(
            user=user, currency="USDT", blnk_balance_id=balance["balance_id"]
        )

    return mwk_wallet, usdt_wallet


def fetch_wallet_balance(user: User) -> dict:
    """Return real numeric Blnk balances for a user: {MWK: Decimal, USDT: Decimal}.

    Raises exception if Blnk is unreachable so views can differentiate Blnk outages from 0 balances.
    """
    mwk_wallet, usdt_wallet = ensure_user_wallets(user)
    client = BlnkClient()

    def _extract_val(val) -> Decimal | None:
        if val is None:
            return None
        if isinstance(val, (int, float, str, Decimal)):
            return Decimal(str(val))
        if isinstance(val, dict):
            for subkey in ["amount", "balance", "available", "value", "current"]:
                if subkey in val and val[subkey] is not None:
                    res = _extract_val(val[subkey])
                    if res is not None:
                        return res
        return None

    def _amount(balance_id: str, precision: int) -> Decimal:
        data = client.get_balance(balance_id)
        logger.debug(f"[BLNK] Balance payload for {balance_id}: {data}")

        raw_balance = None
        # Check standard fields: balance, available_balance, current_balance
        for key in ["balance", "available_balance", "current_balance"]:
            if key in data and data[key] is not None:
                parsed = _extract_val(data[key])
                if parsed is not None:
                    raw_balance = parsed
                    break

        if raw_balance is None or raw_balance == Decimal("0"):
            # Fallback to credit_balance - debit_balance + inflight_balance if present
            credit = _extract_val(data.get("credit_balance")) or Decimal("0")
            debit = _extract_val(data.get("debit_balance")) or Decimal("0")
            inflight = _extract_val(data.get("inflight_balance")) or Decimal("0")
            calc = (credit - debit) + inflight
            if calc != Decimal("0"):
                raw_balance = calc

        if raw_balance is None:
            raw_balance = Decimal("0")

        return (raw_balance / Decimal(precision)).quantize(
            Decimal("0.01") if precision == settings.CURRENCY_PRECISION["MWK"] else Decimal("0.000001")
        )

    balances = {
        "MWK": _amount(mwk_wallet.blnk_balance_id, settings.CURRENCY_PRECISION["MWK"]),
        "USDT": _amount(usdt_wallet.blnk_balance_id, settings.CURRENCY_PRECISION["USDT"]),
    }

    return balances


def ensure_frozen_balance() -> PlatformAccount:
    """Ensure the platform's USDT frozen/escrow balance exists in Blnk.

    Backfills `PlatformAccount.usdt_frozen_balance_id` for existing rows.
    """
    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    if platform.usdt_frozen_balance_id:
        return platform

    client = BlnkClient()
    frozen = client.create_balance(
        platform.ledger_id, "USDT", {"role": "platform_usdt_frozen"}
    )
    platform.usdt_frozen_balance_id = frozen["balance_id"]
    platform.save(update_fields=["usdt_frozen_balance_id"])
    return platform


@db_transaction.atomic
def get_or_create_platform_account(client=None) -> PlatformAccount:
    """Idempotently fetch or create the PlatformAccount ledger and balance mapping.

    - Directly returns the existing PlatformAccount row from DB if present.
    - Creates missing platform ledger and balances once only when DB row is absent.
    """
    platform = PlatformAccount.objects.select_for_update().first()
    if platform:
        return platform

    if not client:
        client = BlnkClient()

    try:
        ledger = client.create_ledger("Bitfuse Platform Account")
        ledger_id = ledger["ledger_id"]
    except Exception as e:
        raise RuntimeError(f"Failed to create Blnk platform ledger: {str(e)}")

    mwk_float_id = client.create_balance(ledger_id, "MWK", {"role": "platform_mwk_float"})["balance_id"]
    usdt_float_id = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_float"})["balance_id"]
    mwk_contra_id = client.create_balance(ledger_id, "MWK", {"role": "external_mwk_contra"})["balance_id"]
    usdt_contra_id = client.create_balance(ledger_id, "USDT", {"role": "external_usdt_contra"})["balance_id"]
    usdt_frozen_id = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_frozen"})["balance_id"]

    platform = PlatformAccount.objects.create(
        ledger_id=ledger_id,
        mwk_float_balance_id=mwk_float_id,
        usdt_float_balance_id=usdt_float_id,
        mwk_external_contra_id=mwk_contra_id,
        usdt_external_contra_id=usdt_contra_id,
        usdt_frozen_balance_id=usdt_frozen_id,
    )

    return platform


def generate_transfer_reference():
    while True:
        ref = "BF-TRF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not Transfer.objects.filter(reference=ref).exists():
            return ref


def perform_p2p_transfer(sender: User, recipient_username: str, amount: Decimal, currency: str, idempotency_key: str) -> Transfer:
    """Perform an internal peer-to-peer balance transfer between Bitfuse users cleanly and atomically."""
    if not idempotency_key or not str(idempotency_key).strip():
        raise ValueError("An idempotency_key is required for P2P transfers.")

    existing_transfer = Transfer.objects.filter(idempotency_key=idempotency_key).first()
    if existing_transfer:
        return existing_transfer

    if currency not in settings.CURRENCY_PRECISION:
        raise ValueError(f"Unsupported currency '{currency}'.")

    if amount <= Decimal("0"):
        raise ValueError("Transfer amount must be strictly positive.")

    # Check precision
    precision_factor = settings.CURRENCY_PRECISION[currency]
    expected_places = 2 if currency == "MWK" else 6
    if amount.as_tuple().exponent < -expected_places:
        raise ValueError(f"Amount exceeds maximum decimal precision ({expected_places} decimal places).")

    recipient = User.objects.filter(username=recipient_username, is_active=True).first()
    if not recipient:
        raise ValueError(f"Recipient user '{recipient_username}' not found.")

    if recipient.pk == sender.pk:
        raise ValueError("Cannot transfer funds to yourself.")

    balances = fetch_wallet_balance(sender)
    sender_bal = balances.get(currency, Decimal("0"))
    if sender_bal < amount:
        raise ValueError(f"Insufficient {currency} balance. Available: {sender_bal}, requested: {amount}.")

    ref = generate_transfer_reference()
    sender_mwk, sender_usdt = ensure_user_wallets(sender)
    recip_mwk, recip_usdt = ensure_user_wallets(recipient)

    sender_blnk_id = sender_mwk.blnk_balance_id if currency == "MWK" else sender_usdt.blnk_balance_id
    recip_blnk_id = recip_mwk.blnk_balance_id if currency == "MWK" else recip_usdt.blnk_balance_id

    client = BlnkClient()
    blnk_amount = int(amount * precision_factor)

    try:
        blnk_txn = client.create_transaction(
            amount=blnk_amount,
            currency=currency,
            precision=precision_factor,
            reference=f"{ref}-p2p",
            source=sender_blnk_id,
            destination=recip_blnk_id,
            description=f"P2P Transfer from {sender.username} to {recipient.username}",
        )
    except Exception as exc:
        raise RuntimeError(f"Blnk ledger transfer failed: {str(exc)}")

    with db_transaction.atomic():
        transfer = Transfer.objects.create(
            reference=ref,
            sender=sender,
            recipient=recipient,
            amount=amount,
            currency=currency,
            status="Completed",
            idempotency_key=idempotency_key,
            blnk_tx_id=blnk_txn.get("transaction_id", ""),
        )

        Notification.objects.create(
            user=sender,
            level="info",
            title="Transfer Sent",
            body=f"You transferred {amount} {currency} to {recipient.username}.",
            reference=ref,
        )
        Notification.objects.create(
            user=recipient,
            level="info",
            title="Transfer Received",
            body=f"You received {amount} {currency} from {sender.username}.",
            reference=ref,
        )

    return transfer
