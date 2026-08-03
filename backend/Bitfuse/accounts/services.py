"""Account/wallet services — the bridge between the Django business engine and Blnk."""

from decimal import Decimal

from django.conf import settings

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount, User, Wallet


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

