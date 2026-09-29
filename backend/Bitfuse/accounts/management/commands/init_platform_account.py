from django.core.management.base import BaseCommand
from accounts.services import get_or_create_platform_account


class Command(BaseCommand):
    help = "Creates or repairs the platform's Blnk ledger and float/contra/frozen balances."

    def handle(self, *args, **kwargs):
        try:
            platform = get_or_create_platform_account()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Platform account initialized/reconciled cleanly. Ledger ID: {platform.ledger_id}"
                )
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed to initialize platform account: {exc}"))
            raise exc
