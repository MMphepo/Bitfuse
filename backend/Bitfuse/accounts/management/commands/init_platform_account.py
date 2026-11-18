from django.core.management.base import BaseCommand
from accounts.services import get_or_create_platform_account


class Command(BaseCommand):
    help = "Initializes the platform's Blnk ledger and balances, or validates the existing mapping."

    def handle(self, *args, **kwargs):
        try:
            platform = get_or_create_platform_account()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Platform account initialized and validated. Ledger ID: {platform.ledger_id}"
                )
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed to initialize platform account: {exc}"))
            raise exc
