from django.core.management.base import BaseCommand

from accounts.blnk_client import BlnkClient
from accounts.models import PlatformAccount


from accounts.services import get_or_create_platform_account


class Command(BaseCommand):
    help = "Creates or fetches the platform's float, external-contra, and frozen-escrow balances in Blnk idempotently."

    def handle(self, *args, **kwargs):
        client = BlnkClient()
        existed = PlatformAccount.objects.exists()

        platform = get_or_create_platform_account(client=client)

        if existed:
            self.stdout.write("Platform account already exists — idempotent check completed.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Platform account created with ledger_id={platform.ledger_id}."))
