import uuid
import sys
import os
import time
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
        self.stdout.write("==================================================")
        self.stdout.write("STARTING USER SEEDER AND BLNK BALANCE VERIFICATION")
        self.stdout.write("==================================================")

        # Set environment variable to prevent recursion
        os.environ["SEEDING_IN_PROGRESS"] = "True"

        is_testing = "test" in sys.argv or getattr(settings, "TESTING", False)
        self.stdout.write(f"is_testing: {is_testing}")
        self.stdout.write(f"sys.argv: {sys.argv}")
        self.stdout.write(f"settings.TESTING: {getattr(settings, 'TESTING', None)}")

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

        self.stdout.write(f"PlatformAccount ID: {platform.id}")
        self.stdout.write(f"platform.ledger_id: {platform.ledger_id}")
        self.stdout.write(f"platform.usdt_float_balance_id: {platform.usdt_float_balance_id}")
        self.stdout.write(f"platform.usdt_external_contra_id: {platform.usdt_external_contra_id}")

        blnk_client = BlnkClient()

        # Step 1: Fund the platform USDT float from external contra so it has sufficient funds
        if not is_testing:
            try:
                # Check if the float balance exists first
                float_data = blnk_client.get_balance(platform.usdt_float_balance_id)
                current_float = Decimal(str(float_data.get("balance", "0"))) / Decimal("1000000")
                self.stdout.write(f"Current Platform USDT Float Balance: {current_float} USDT")

                # We need at least 1000 USDT to comfortably seed users
                if current_float < Decimal("1000.00"):
                    fund_amount = Decimal("2000.00")
                    raw_fund = int(fund_amount * Decimal("1000000"))
                    self.stdout.write(f"Funding platform USDT float with {fund_amount} USDT from external contra...")

                    tx = blnk_client.create_transaction(
                        amount=raw_fund,
                        currency="USDT",
                        precision=1000000,
                        reference=f"seed-fund-platform-float-{uuid.uuid4()}",
                        source=platform.usdt_external_contra_id,
                        destination=platform.usdt_float_balance_id,
                        description="Seed funding of platform USDT float balance",
                    )
                    self.stdout.write(f"Platform Float Funding Tx Status: {tx.get('status')} | Tx ID: {tx.get('transaction_id')}")

                    # Wait a moment for queue to process
                    time.sleep(1.0)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not fund platform USDT float: {str(e)}"))

        users_processed = 0
        users_successfully_credited = 0
        users_already_funded = 0
        failed_credits = 0
        failures = []

        for i in range(1, 6):
            username = f"seeded_user_{i}"
            email = f"seeded_user_{i}@example.com"
            # Using +265999... prefix to avoid clashes with tests/superusers
            phone = f"+26599900000{i}"
            users_processed += 1

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
                self.stdout.write(f"Created user: {username}")
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

                self.stdout.write(f"USDT Wallet ID: {usdt_wallet.blnk_balance_id}")
                self.stdout.write(f"Transfer from {platform.usdt_float_balance_id} to {usdt_wallet.blnk_balance_id}")
                self.stdout.write(f"Amount: {diff} USDT (raw: {raw_diff}) with precision 6")

                try:
                    if not is_testing:
                        tx_ref = f"seed-credit-{user.id}-{raw_diff}-{uuid.uuid4()}"
                        tx = blnk_client.create_transaction(
                            amount=raw_diff,
                            currency="USDT",
                            precision=settings.CURRENCY_PRECISION["USDT"],
                            reference=tx_ref,
                            source=platform.usdt_float_balance_id,
                            destination=usdt_wallet.blnk_balance_id,
                            description="Seeded USDT credit balance adjust",
                        )
                        self.stdout.write(f"Blnk Tx Response: Status={tx.get('status')} | Tx ID={tx.get('transaction_id')}")

                        # Wait and poll for Blnk queue asynchronous processing (up to 3 seconds)
                        new_balance = current_usdt
                        for attempt in range(15):
                            time.sleep(0.2)
                            post_balances = fetch_wallet_balance(user)
                            new_balance = post_balances.get("USDT", Decimal("0"))
                            if new_balance >= target_usdt:
                                break

                        self.stdout.write(f"User {username} balance after transfer check: {new_balance} USDT")

                        if new_balance >= target_usdt:
                            users_successfully_credited += 1
                        else:
                            failed_credits += 1
                            failures.append(f"{username}: Balance did not reach target (remained {new_balance})")
                    else:
                        self.stdout.write(f"[Mocked Seeder Balance Edit] added {diff} USDT to reach target {target_usdt} USDT.")
                        users_successfully_credited += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Could not edit {username} Blnk balance: {str(e)}"))
                    failed_credits += 1
                    failures.append(f"{username}: {str(e)}")
            else:
                self.stdout.write(f"User {username} already has sufficient balance: {current_usdt} USDT.")
                users_already_funded += 1

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

        # Reset environment variable
        os.environ["SEEDING_IN_PROGRESS"] = "False"

        self.stdout.write("==================================================")
        self.stdout.write("SEEDING SUMMARY")
        self.stdout.write("==================================================")
        self.stdout.write(f"Users processed: {users_processed}")
        self.stdout.write(f"Users successfully credited: {users_successfully_credited}")
        self.stdout.write(f"Users already funded: {users_already_funded}")
        self.stdout.write(f"Failed credits: {failed_credits}")

        if failed_credits > 0:
            self.stdout.write(self.style.ERROR("FAILURES DETECTED:"))
            for fail in failures:
                self.stdout.write(self.style.ERROR(f"- {fail}"))
            self.stdout.write(self.style.WARNING("WARNING: One or more credits failed. Ensure Blnk server is running and funded."))
        else:
            self.stdout.write(self.style.SUCCESS("All user seeding completed successfully!"))
