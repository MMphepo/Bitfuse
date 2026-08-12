from django.core.management.base import BaseCommand
from withdrawals.services.withdrawal_service import monitor_broadcast_withdrawals


class Command(BaseCommand):
    help = "Scans blockchain for pending ('BROADCAST') withdrawals and updates status on-chain & in Blnk ledger."

    def handle(self, *args, **options):
        self.stdout.write("Starting withdrawal monitor...")
        try:
            processed = monitor_broadcast_withdrawals()
            self.stdout.write(self.style.SUCCESS(f"Withdrawal monitoring run complete. Processed {processed} transition(s)."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Withdrawal monitoring failed: {str(e)}"))
