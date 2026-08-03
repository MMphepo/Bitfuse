from django.core.management.base import BaseCommand
from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount


class Command(BaseCommand):
    help = "Creates the platform's float and external-contra balances in Blnk, once."

    def handle(self, *args, **kwargs):
        if PlatformAccount.objects.exists():
            self.stdout.write("Platform account already exists — skipping.")
            return

        client = BlnkClient()
        ledger = client.create_ledger("Bitfuse Platform Account")
        ledger_id = ledger["ledger_id"]

        mwk_float = client.create_balance(ledger_id, "MWK", {"role": "platform_mwk_float"})
        usdt_float = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_float"})
        mwk_contra = client.create_balance(ledger_id, "MWK", {"role": "external_mwk_contra"})

        PlatformAccount.objects.create(
            ledger_id=ledger_id,
            mwk_float_balance_id=mwk_float["balance_id"],
            usdt_float_balance_id=usdt_float["balance_id"],
            mwk_external_contra_id=mwk_contra["balance_id"],
        )
        self.stdout.write(self.style.SUCCESS("Platform account created."))
