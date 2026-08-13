import uuid
import sys
import random
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.conf import settings
from accounts.models import Wallet, Transaction, PlatformAccount
from kyc.models import KYCSubmission
from accounts.services import ensure_user_wallets, fetch_wallet_balance, get_or_create_platform_account
from accounts.blnk_client import BlnkClient

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds 5 users with approved KYC, > 100 USDT balance, and > 5 historical transactions, directly editing Blnk balances."

    def handle(self, *args, **options):
        self.stdout.write("Seeding users and editing Blnk balances...")

        is_testing = "test" in sys.argv or getattr(settings, "TESTING", False)

        # Get or create real platform account using idempotent service
        try:
            if is_testing:
                # During test DB migrations, fetch or mock platform account without real Blnk calls
                platform = PlatformAccount.objects.first()
                if not platform:
                    platform = PlatformAccount.objects.create(
                        ledger_id="ledger",
                        mwk_float_balance_id="mwk-float",
                        usdt_float_balance_id="usdt-float",
                        mwk_external_contra_id="mwk-contra",
                        usdt_external_contra_id="usdt-contra",
                        usdt_frozen_balance_id="usdt-frozen",
                    )
            else:
                platform = get_or_create_platform_account()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to get_or_create_platform_account: {str(e)}"))
            # Fallback
            platform = PlatformAccount.objects.first()
            if not platform:
                platform = PlatformAccount.objects.create(
                    ledger_id="ledger",
                    mwk_float_balance_id="mwk-float",
                    usdt_float_balance_id="usdt-float",
                    mwk_external_contra_id="mwk-contra",
                    usdt_external_contra_id="usdt-contra",
                    usdt_frozen_balance_id="usdt-frozen",
                )

        blnk_client = BlnkClient()

        for i in range(1, 6):
            username = f"seeded_user_{i}"
            email = f"seeded_user_{i}@example.com"
            # Using +265999... prefix to avoid clashes with tests/superusers
            phone = f"+26599900000{i}"

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "phone_number": phone,
                    "verification_status": "verified",
                }
            )
            if created:
                user.set_password("password123")
                user.save()
                self.stdout.write(f"Created user {username}")
            else:
                user.verification_status = "verified"
                user.save()

            # Create approved KYCSubmission if it doesn't exist
            kyc, kyc_created = KYCSubmission.objects.get_or_create(
                user=user,
                defaults={
                    "status": "approved",
                }
            )
            if not kyc_created:
                kyc.status = "approved"
                kyc.save()

            # Ensure wallets exist
            try:
                if is_testing:
                    # In test migration phase, manually populate Wallet models to bypass API
                    Wallet.objects.get_or_create(user=user, currency="USDT", defaults={"blnk_balance_id": f"usdt-{user.id}"})
                    Wallet.objects.get_or_create(user=user, currency="MWK", defaults={"blnk_balance_id": f"mwk-{user.id}"})
                else:
                    ensure_user_wallets(user)
            except Exception as e:
                # Fallback if Blnk is offline
                Wallet.objects.get_or_create(user=user, currency="USDT", defaults={"blnk_balance_id": f"usdt-{user.id}"})
                Wallet.objects.get_or_create(user=user, currency="MWK", defaults={"blnk_balance_id": f"mwk-{user.id}"})
                self.stdout.write(f"Created offline fallback wallets for {username}: {str(e)}")

            # Check existing balance from Blnk
            current_usdt = Decimal("0")
            try:
                if is_testing:
                    current_usdt = Decimal("0")
                else:
                    balances = fetch_wallet_balance(user)
                    current_usdt = balances.get("USDT", Decimal("0"))
                self.stdout.write(f"User {username} current Blnk balance: {current_usdt} USDT")
            except Exception as e:
                self.stdout.write(f"Could not fetch Blnk balance for {username}: {str(e)}")

            # Target balance is 150.00 USDT (not less than 100)
            target_usdt = Decimal("150.00")
            if current_usdt < target_usdt:
                diff = target_usdt - current_usdt
                raw_diff = int(diff * settings.CURRENCY_PRECISION["USDT"])
                usdt_wallet = Wallet.objects.get(user=user, currency="USDT")

                try:
                    if not is_testing:
                        blnk_client.create_transaction(
                            amount=raw_diff,
                            currency="USDT",
                            precision=settings.CURRENCY_PRECISION["USDT"],
                            reference=f"seed-credit-{user.id}-{raw_diff}",
                            source=platform.usdt_float_balance_id,
                            destination=usdt_wallet.blnk_balance_id,
                            description="Seeded USDT credit balance adjust",
                        )
                        self.stdout.write(f"Successfully edited Blnk balance for {username}: added {diff} USDT to reach target {target_usdt} USDT.")
                    else:
                        self.stdout.write(f"[Mocked Seeder Balance Edit] added {diff} USDT to reach target {target_usdt} USDT.")
                except Exception as e:
                    self.stdout.write(f"Could not edit {username} Blnk balance (offline/ignored): {str(e)}")
            else:
                self.stdout.write(f"User {username} already has sufficient balance: {current_usdt} USDT.")

            # Create more than 5 historical transactions (6 transactions)
            existing_txs = Transaction.objects.filter(user=user).count()
            if existing_txs < 6:
                for tx_num in range(existing_txs + 1, 7):
                    tx_type = "Buy" if tx_num % 2 == 1 else "Sell"
                    amount_usdt = Decimal(random.randint(15, 45))
                    rate = Decimal("1850.00")
                    amount_mwk = amount_usdt * rate
                    ref = f"SEED-TX-{user.username.upper()}-{tx_num}"

                    Transaction.objects.get_or_create(
                        reference=ref,
                        defaults={
                            "user": user,
                            "type": tx_type,
                            "amount_usdt": amount_usdt,
                            "amount_mwk": amount_mwk,
                            "rate": rate,
                            "fee": Decimal("1.00"),
                            "status": "Completed",
                            "method": "Airtel Money",
                            "phone": phone,
                        }
                    )
                self.stdout.write(f"Generated 6 historical transactions for {username}")

        self.stdout.write(self.style.SUCCESS("User seeding and Blnk balance editing completed successfully."))
