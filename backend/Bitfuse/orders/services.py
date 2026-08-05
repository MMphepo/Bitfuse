"""Order services — pricing, USDT locking, and Blnk settlement flows.

Architecture principle: the backend owns the business logic; Blnk owns the
financial truth. Every money movement goes through Blnk, and the Django
database records the business outcome.
"""

import random
import string
from decimal import Decimal

from django.utils import timezone
from django.conf import settings

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount, Rate, Transaction, Wallet
from accounts.services import ensure_frozen_balance, ensure_user_wallets

BUY_RATE_DEFAULT = Decimal("4220.00")  # MWK charged per 1 USDT bought
SELL_RATE_DEFAULT = Decimal("4050.00")  # MWK paid per 1 USDT sold
FEE_DEFAULT = Decimal("1.00")  # percent


def generate_reference():
    """Generate a unique reference number like BF-A3XK9M2P."""
    return "BF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _to_decimal(value) -> Decimal:
    """Robustly convert int/float/str/Decimal/None to Decimal.

    Prevents "can't multiply sequence by non-int" type errors when a client
    sends an amount as a string or when DRF passes through a raw value.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _current_rate() -> Rate:
    """Return the live Rate row with safe defaults (buy 4220 / sell 4050, fee 1%)."""
    return Rate.current()


def price_buy_order(usdt_amount):
    """Calculate MWK payable for a buy order given the USDT amount.

    Returns (mwk_total_payable, fee_amount, rate, fee_percent).
    mwk_total_payable = usdt * buy_rate (fee included in displayed total).
    """
    usdt_amount = _to_decimal(usdt_amount)

    rate = _current_rate()
    buy_rate = _to_decimal(rate.buy_rate)
    buy_fee_percent = _to_decimal(rate.buy_fee_percent)
    mwk_amount = usdt_amount * buy_rate
    fee_amount = (mwk_amount * buy_fee_percent / Decimal(100)).quantize(Decimal("0.01"))
    total_payable = mwk_amount + fee_amount
    return (
        total_payable.quantize(Decimal("0.01")),
        fee_amount,
        buy_rate,
        buy_fee_percent,
    )


def price_sell_order(usdt_amount):
    """Calculate MWK net payout for a sell order given the USDT amount.

    Returns (mwk_net_payout, fee_amount, rate, fee_percent).
    """
    usdt_amount = _to_decimal(usdt_amount)

    rate = _current_rate()
    sell_rate = _to_decimal(rate.sell_rate)
    sell_fee_percent = _to_decimal(rate.sell_fee_percent)
    mwk_gross = usdt_amount * sell_rate
    fee_amount = (mwk_gross * sell_fee_percent / Decimal(100)).quantize(Decimal("0.01"))
    net_payout = mwk_gross - fee_amount
    return (
        net_payout.quantize(Decimal("0.01")),
        fee_amount,
        sell_rate,
        sell_fee_percent,
    )


def lock_sell_order(order):
    """Lock the seller's USDT into the platform's frozen escrow balance.

    Called at order creation: user USDT wallet → platform frozen balance.
    This protects the seller and guarantees the funds are available on completion.
    """
    client = BlnkClient()
    platform = ensure_frozen_balance()
    _, usdt_wallet = ensure_user_wallets(order.user)

    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    txn = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-lock",
        source=usdt_wallet.blnk_balance_id,
        destination=platform.usdt_frozen_balance_id,
        description=f"USDT locked for sell order {order.reference_number}",
    )
    order.blnk_transaction_refs.append(txn["transaction_id"])
    order.save(update_fields=["blnk_transaction_refs"])


def _write_history(order, method: str, phone: str, fee_amount: Decimal):
    """Write an immutable application-database history record for an order."""
    Transaction.objects.create(
        user=order.user,
        type="Buy" if order.order_type == "buy" else "Sell",
        amount_usdt=order.usdt_amount,
        amount_mwk=order.mwk_amount,
        rate=order.rate,
        fee=fee_amount,
        status="Completed",
        method=method,
        phone=phone,
        reference=order.reference_number,
    )


def complete_buy_order(order):
    """Call once an admin has confirmed the user's mobile money payment arrived.

    Leg 1: Record the Kwacha (amount + fee) that arrived via mobile money into the
           platform's MWK float (from external contra → platform MWK float).
    Leg 2: Release USDT from the platform float to the buyer's USDT wallet.
    """
    client = BlnkClient()
    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    _, usdt_wallet = ensure_user_wallets(order.user)

    precision_mwk = settings.CURRENCY_PRECISION["MWK"]
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    total_mwk = order.mwk_amount + order.fee_amount

    # Leg 1: cash-in from mobile money (amount + fee)
    txn1 = client.create_transaction(
        amount=int(total_mwk * precision_mwk),
        currency="MWK",
        precision=precision_mwk,
        reference=f"{order.reference_number}-cash-in",
        source=platform.mwk_external_contra_id,
        destination=platform.mwk_float_balance_id,
        description=f"Mobile money payment received for order {order.reference_number}",
    )

    # Leg 2: release USDT to buyer
    txn2 = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-usdt-out",
        source=platform.usdt_float_balance_id,
        destination=usdt_wallet.blnk_balance_id,
        description=f"USDT released for order {order.reference_number}",
    )

    order.status = "completed"
    order.completed_at = timezone.now()
    order.blnk_transaction_refs = [txn1["transaction_id"], txn2["transaction_id"]]
    order.save()

    _write_history(order, order.payment_method or "Airtel Money", order.phone or "", order.fee_amount)


def complete_sell_order(order):
    """Call once an admin has confirmed the payout was sent to the seller.

    The seller's USDT is already locked in the platform frozen escrow (lock_sell_order).

    Leg 1: Move USDT from frozen escrow → platform USDT float.
    Leg 2: Record the MWK net payout (platform MWK float → external contra).
    """
    client = BlnkClient()
    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    ensure_frozen_balance()

    precision_mwk = settings.CURRENCY_PRECISION["MWK"]
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    # Leg 1: frozen escrow → platform USDT float
    txn1 = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-escrow-out",
        source=platform.usdt_frozen_balance_id,
        destination=platform.usdt_float_balance_id,
        description=f"Frozen USDT released for order {order.reference_number}",
    )

    # Leg 2: MWK net payout (platform MWK float → external contra)
    txn2 = client.create_transaction(
        amount=int(order.mwk_amount * precision_mwk),
        currency="MWK",
        precision=precision_mwk,
        reference=f"{order.reference_number}-cash-out",
        source=platform.mwk_float_balance_id,
        destination=platform.mwk_external_contra_id,
        description=f"Mobile money payout for order {order.reference_number}",
    )

    order.status = "completed"
    order.completed_at = timezone.now()
    order.blnk_transaction_refs = [txn1["transaction_id"], txn2["transaction_id"]]
    order.save()

    _write_history(order, order.payment_method or "Airtel Money", order.phone or "", order.fee_amount)
