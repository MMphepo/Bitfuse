from django.core.management.base import BaseCommand
from withdrawals.services.workers import check_bsc_gas_balance, run_reconciliation
from withdrawals.services.withdrawal_service import monitor_broadcast_withdrawals


class Command(BaseCommand):
    help = "Run withdrawal confirmation monitoring, gas check, and reconciliation background tasks."

    def handle(self, *args, **options):
        self.stdout.write("Running withdrawal background tasks...")

        # 1. Confirmation monitoring
        processed = monitor_broadcast_withdrawals()
        self.stdout.write(f"Processed {processed} broadcast withdrawals.")

        # 2. Gas monitoring
        gas_info = check_bsc_gas_balance()
        self.stdout.write(f"BSC Gas Info: {gas_info['bnb_balance']} BNB (Alert: {gas_info['alert']})")

        # 3. Reconciliation
        recon = run_reconciliation()
        self.stdout.write(f"Reconciliation completed. Discrepancies: {recon['discrepancy_count']}")
