from django.core.management.base import BaseCommand

from accounts.models import Rate


class Command(BaseCommand):
    help = "Seeds the current buy/sell rates for the platform (defaults: buy 4220, sell 4050)."

    def add_arguments(self, parser):
        parser.add_argument("--buy-rate", type=float, default=4220.0, help="MWK charged per 1 USDT bought")
        parser.add_argument("--sell-rate", type=float, default=4050.0, help="MWK paid per 1 USDT sold")
        parser.add_argument("--buy-fee", type=float, default=1.0, help="Buy fee percent")
        parser.add_argument("--sell-fee", type=float, default=1.0, help="Sell fee percent")

    def handle(self, *args, **kwargs):
        rate = Rate.objects.create(
            buy_rate=kwargs["buy_rate"],
            sell_rate=kwargs["sell_rate"],
            buy_fee_percent=kwargs["buy_fee"],
            sell_fee_percent=kwargs["sell_fee"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Rate seeded → buy {rate.buy_rate} MWK/USDT (fee {rate.buy_fee_percent}%) | "
                f"sell {rate.sell_rate} MWK/USDT (fee {rate.sell_fee_percent}%)"
            )
        )

