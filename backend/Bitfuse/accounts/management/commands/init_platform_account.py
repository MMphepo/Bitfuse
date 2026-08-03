from django.core.management.base import BaseCommand

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount


class Command(BaseCommand):
    help = "Creates the platform's float, external-contra, and frozen-escrow balances in Blnk, once."

    def handle(self, *args, **kwargs):
        client = BlnkClient()

        platform = PlatformAccount.objects.first()
        if platform:
            # Backfill the frozen escrow balance if it was created before this field existed.
            if not platform.usdt_frozen_balance_id:
                frozen = client.create_balance(
                    platform.ledger_id, "USDT", {"role": "platform_usdt_frozen"}
                )
                platform.usdt_frozen_balance_id = frozen["balance_id"]
                platform.save(update_fields=["usdt_frozen_balance_id"])
                self.stdout.write(
                    self.style.SUCCESS("Added frozen escrow balance to existing platform account.")
                )
            else:
                self.stdout.write("Platform account already exists — skipping.")
            return

        ledger = client.create_ledger("Bitfuse Platform Account")
        ledger_id = ledger["ledger_id"]

        mwk_float = client.create_balance(ledger_id, "MWK", {"role": "platform_mwk_float"})
        usdt_float = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_float"})
        mwk_contra = client.create_balance(ledger_id, "MWK", {"role": "external_mwk_contra"})
        usdt_contra = client.create_balance(ledger_id, "USDT", {"role": "external_usdt_contra"})
        frozen = client.create_balance(ledger_id, "USDT", {"role": "platform_usdt_frozen"})

        PlatformAccount.objects.create(
            ledger_id=ledger_id,
            mwk_float_balance_id=mwk_float["balance_id"],
            usdt_float_balance_id=usdt_float["balance_id"],
            mwk_external_contra_id=mwk_contra["balance_id"],
            usdt_external_contra_id=usdt_contra["balance_id"],
            usdt_frozen_balance_id=frozen["balance_id"],
        )
        self.stdout.write(self.style.SUCCESS("Platform account created."))
