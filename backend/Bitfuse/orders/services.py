import random
import string
from decimal import Decimal

from django.utils import timezone
from django.conf import settings

from accounts.blnk_client import BlnkClient
from accounts.models import Wallet, PlatformAccount, Rate


def generate_reference():
    """Generate a unique reference number like BF-A3XK9M2P."""
    return "BF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def price_buy_order(mwk_amount: Decimal):
    """Calculate USDT amount for a buy order given MWK amount."""
    rate = Rate.current()
    fee = rate.buy_fee_percent / Decimal(100)
    usdt_amount = (mwk_amount / rate.buy_rate) * (1 - fee)
    return usdt_amount.quantize(Decimal("0.000001")), rate.buy_rate, rate.buy_fee_percent


def price_sell_order(usdt_amount: Decimal):
    """Calculate MWK payout for a sell order given USDT amount."""
    rate = Rate.current()
    fee = rate.sell_fee_percent / Decimal(100)
    mwk_amount = (usdt_amount * rate.sell_rate) * (1 - fee)
    return mwk_amount.quantize(Decimal("0.01")), rate.sell_rate, rate.sell_fee_percent


def complete_buy_order(order):
    """Call once an admin has confirmed the user's mobile money payment arrived.

    Leg 1: Record the Kwacha that arrived via mobile money into the platform's MWK float
           (from external contra → platform MWK float).
    Leg 2: Release USDT from the platform float to the buyer's USDT wallet.
    """
    client = BlnkClient()
    platform = PlatformAccount.objects.first()
    user_usdt_wallet = Wallet.objects.get(user=order.user, currency="USDT")

    precision_mwk = settings.CURRENCY_PRECISION["MWK"]
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    # Leg 1: cash-in from mobile money
    txn1 = client.create_transaction(
        amount=int(order.mwk_amount * precision_mwk),
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
        destination=user_usdt_wallet.blnk_balance_id,
        description=f"USDT released for order {order.reference_number}",
    )

    order.status = "completed"
    order.completed_at = timezone.now()
    order.blnk_transaction_refs = [txn1["transaction_id"], txn2["transaction_id"]]
    order.save()


def complete_sell_order(order):
    """Call once an admin has confirmed the user's USDT deposit landed on-chain.

    Leg 1: Record the USDT that arrived on-chain into the platform's USDT float
           (from USDT external contra → platform USDT float).
    Leg 2: Record the Kwacha paid out to the seller via mobile money
           (from platform MWK float → external contra).
    The actual mobile money transfer is done manually by the admin outside the app.
    """
    client = BlnkClient()
    platform = PlatformAccount.objects.first()

    precision_mwk = settings.CURRENCY_PRECISION["MWK"]
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    # Leg 1: USDT on-chain deposit recorded via the external contra
    txn1 = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-usdt-in",
        source=platform.usdt_external_contra_id,
        destination=platform.usdt_float_balance_id,
        description=f"On-chain USDT deposit confirmed for order {order.reference_number}",
    )

    # Leg 2: record MWK payout (platform MWK float → external contra)
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
