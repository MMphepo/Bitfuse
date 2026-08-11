"""Mobile money payment rails Bitfuse accepts for buy orders.

Bitfuse does not (yet) talk to Airtel/TNM directly: the customer pays the
Bitfuse merchant number from their own handset and reports the transaction ID
back to us, and an admin verifies it against the mobile money statement.
"""

import re

from django.conf import settings

AIRTEL_MONEY = "airtel_money"
TNM_MPAMBA = "tnm_mpamba"


def _config(name, default):
    return getattr(settings, name, default)


def payment_methods():
    """Return the merchant details a buyer needs, keyed by payment method."""
    return {
        AIRTEL_MONEY: {
            "code": AIRTEL_MONEY,
            "label": "Airtel Money",
            "business_code": _config("AIRTEL_MONEY_BUSINESS_CODE", ""),
            "account_name": _config("AIRTEL_MONEY_ACCOUNT_NAME", "Bitfuse"),
            "transaction_id_example": "CM123456789",
            "instructions": [
                "Open Airtel Money on your phone.",
                "Choose Make Payments → Pay Merchant.",
                "Enter the Bitfuse business code shown above.",
                "Enter the exact amount and use the Bitfuse reference as the narration.",
                "Approve the payment and wait for the Airtel confirmation SMS.",
                "Return to Bitfuse and enter the transaction ID from that SMS.",
            ],
        },
        TNM_MPAMBA: {
            "code": TNM_MPAMBA,
            "label": "TNM Mpamba",
            "business_code": _config("TNM_MPAMBA_BUSINESS_CODE", ""),
            "account_name": _config("TNM_MPAMBA_ACCOUNT_NAME", "Bitfuse"),
            "transaction_id_example": "MP123456789",
            "instructions": [
                "Open TNM Mpamba on your phone.",
                "Choose Pay Merchant.",
                "Enter the Bitfuse business code shown above.",
                "Enter the exact amount and use the Bitfuse reference as the narration.",
                "Approve the payment and wait for the Mpamba confirmation SMS.",
                "Return to Bitfuse and enter the transaction ID from that SMS.",
            ],
        },
    }


def is_supported(method: str) -> bool:
    return method in payment_methods()


def method_details(method: str):
    return payment_methods().get(method)


# Mobile money transaction IDs are alphanumeric, may contain dots/dashes, and
# always carry at least one digit. This only rejects obvious junk — it is never
# treated as proof that the payment happened.
TRANSACTION_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{5,31}$")

PLACEHOLDER_TRANSACTION_IDS = {"TEST", "TEST123", "123456", "000000", "ABCDEF", "N/A", "NONE"}


def normalise_transaction_id(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def transaction_id_error(value: str):
    """Return a human-readable error for an invalid transaction ID, else None."""
    normalised = normalise_transaction_id(value)
    if not normalised:
        return "Enter the transaction ID from your mobile money confirmation SMS."
    if not TRANSACTION_ID_RE.match(normalised):
        return (
            "That does not look like a mobile money transaction ID. "
            "Copy it exactly as it appears in your confirmation SMS."
        )
    if not any(char.isdigit() for char in normalised):
        return "A mobile money transaction ID contains digits. Check your confirmation SMS."
    if normalised in PLACEHOLDER_TRANSACTION_IDS:
        return "Enter the real transaction ID from your confirmation SMS."
    return None
