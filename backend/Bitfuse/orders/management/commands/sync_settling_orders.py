from django.core.management.base import BaseCommand
from orders.models import Order
from orders.services import complete_buy_order


class Command(BaseCommand):
    help = "Sync status for buy orders currently in SETTLING status by checking Blnk transaction status."

    def handle(self, *args, **options):
        settling_orders = Order.objects.filter(order_type="buy", status=Order.SETTLING)
        initial_count = settling_orders.count()
        completed_count = 0

        for order in settling_orders:
            try:
                updated_order = complete_buy_order(order)
                if updated_order.status == Order.COMPLETED:
                    completed_count += 1
            except Exception as exc:
                self.stderr.write(f"Error checking order {order.reference_number}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(f"Processed {initial_count} settling order(s); {completed_count} transitioned to COMPLETED.")
        )
