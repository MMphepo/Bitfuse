"""Account/wallet services — the bridge between the Django business engine and Blnk."""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount, User, Wallet

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
    """Return real numeric Blnk balances for a user: {MWK: Decimal, USDT: Decimal}."""
    mwk_wallet, usdt_wallet = ensure_user_wallets(user)
    client = BlnkClient()

    def _amount(balance_id: str, precision: int) -> Decimal:
        try:
            data = client.get_balance(balance_id)
            return (Decimal(str(data["balance"])) / Decimal(precision)).quantize(
                Decimal("0.01") if precision == settings.CURRENCY_PRECISION["MWK"] else Decimal("0.000001")
            )
        except Exception:
            return Decimal("0")

    return {
        "MWK": _amount(mwk_wallet.blnk_balance_id, settings.CURRENCY_PRECISION["MWK"]),
        "USDT": _amount(usdt_wallet.blnk_balance_id, settings.CURRENCY_PRECISION["USDT"]),
    }


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

    - Resolves from the database first.
    - If missing from DB, attempts to reconcile with existing Blnk ledger/balances.
    - If completely missing from Blnk, creates them once.
    - Uses database locking to prevent concurrent race conditions.
    """
    # Use select_for_update to serialize concurrent requests and avoid race conditions
    platform = PlatformAccount.objects.select_for_update().first()
    if platform:
        # Verify it still exists in Blnk if not testing
        if getattr(settings, "TESTING", False):
            return platform

        # Test if the balance ID actually exists in Blnk
        if not client:
            client = BlnkClient()
        try:
            client.get_balance(platform.usdt_float_balance_id)
            return platform
        except Exception:
            # If lookup fails, we can proceed to reconcile or recreate
            pass

    if not client:
        client = BlnkClient()

    # Find existing platform ledger/balances in Blnk (reconciliation)
    ledger_id = None
    mwk_float_id = None
    usdt_float_id = None
    mwk_contra_id = None
    usdt_contra_id = None
    usdt_frozen_id = None

    try:
        ledgers = client.list_ledgers()
        # Look for ledger named "Bitfuse Platform Account"
        for led in ledgers:
            if led.get("name") == "Bitfuse Platform Account":
                ledger_id = led.get("ledger_id")
                break
    except Exception as e:
        logger.error(f"Error listing ledgers from Blnk: {str(e)}")

    if ledger_id:
        # If ledger exists, find existing balances
        try:
            balances = client.list_balances()
            for bal in balances:
                if bal.get("ledger_id") == ledger_id:
                    meta = bal.get("meta_data", {}) or {}
                    role = meta.get("role")
                    if role == "platform_mwk_float":
                        mwk_float_id = bal.get("balance_id")
                    elif role == "platform_usdt_float":
                        usdt_float_id = bal.get("balance_id")
                    elif role == "external_mwk_contra":
                        mwk_contra_id = bal.get("balance_id")
                    elif role == "external_usdt_contra":
                        usdt_contra_id = bal.get("balance_id")
                    elif role == "platform_usdt_frozen":
                        usdt_frozen_id = bal.get("balance_id")
        except Exception as e:
            logger.error(f"Error listing balances from Blnk: {str(e)}")

    # If ledger doesn't exist, create it
    if not ledger_id:
        try:
            ledger = client.create_ledger("Bitfuse Platform Account")
            ledger_id = ledger["ledger_id"]
        except Exception as e:
            raise RuntimeError(f"Failed to create Blnk platform ledger: {str(e)}")

    # Create missing balances as needed
    if not mwk_float_id:
        mwk_float_id = client.create_balance(ledger_id, "MWK", {"role": "platform_mwk_float"})["balance_id"]
    if not usdt_float_id:
        usdt_float_id = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_float"})["balance_id"]
    if not mwk_contra_id:
        mwk_contra_id = client.create_balance(ledger_id, "MWK", {"role": "external_mwk_contra"})["balance_id"]
    if not usdt_contra_id:
        usdt_contra_id = client.create_balance(ledger_id, "USDT", {"role": "external_usdt_contra"})["balance_id"]
    if not usdt_frozen_id:
        usdt_frozen_id = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_frozen"})["balance_id"]

    # Persist or update the PlatformAccount mapping
    if platform:
        platform.ledger_id = ledger_id
        platform.mwk_float_balance_id = mwk_float_id
        platform.usdt_float_balance_id = usdt_float_id
        platform.mwk_external_contra_id = mwk_contra_id
        platform.usdt_external_contra_id = usdt_contra_id
        platform.usdt_frozen_balance_id = usdt_frozen_id
        platform.save()
    else:
        platform = PlatformAccount.objects.create(
            ledger_id=ledger_id,
            mwk_float_balance_id=mwk_float_id,
            usdt_float_balance_id=usdt_float_id,
            mwk_external_contra_id=mwk_contra_id,
            usdt_external_contra_id=usdt_contra_id,
            usdt_frozen_balance_id=usdt_frozen_id,
        )

    return platform
