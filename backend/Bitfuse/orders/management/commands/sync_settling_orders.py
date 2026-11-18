from django.core.management.base import BaseCommand
from orders.models import Order
from orders.services import complete_buy_order, complete_sell_order


class Command(BaseCommand):
    help = "Sync buy and sell orders currently SETTLING by checking their Blnk transaction status."

    def handle(self, *args, **options):
        settling_orders = Order.objects.filter(status=Order.SETTLING)
        initial_count = settling_orders.count()
        completed_count = 0

        for order in settling_orders:
            try:
                updated_order = (
                    complete_buy_order(order) if order.order_type == "buy" else complete_sell_order(order)
                )
                if updated_order.status == Order.COMPLETED:
                    completed_count += 1
            except Exception as exc:
                self.stderr.write(f"Error checking order {order.reference_number}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(f"Processed {initial_count} settling order(s); {completed_count} transitioned to COMPLETED.")
        )
