import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.models import PlatformAccount, Wallet
from accounts.blnk_client import BlnkClient
from accounts.services import ensure_user_wallets, get_or_create_platform_account

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Safely reconciles Bitfuse Django financial references against the current Blnk ledger instance."

    def handle(self, *args, **options):
        self.stdout.write("=== Bitfuse Blnk Reconciliation ===")
        self.stdout.write("")

        client = BlnkClient()
        errors = []

        # 1. Reconcile Platform Account
        platform_ledger_status = "ok"
        mwk_float_status = "ok"
        usdt_float_status = "ok"
        mwk_contra_status = "ok"
        usdt_contra_status = "ok"
        usdt_frozen_status = "ok"

        try:
            platform = PlatformAccount.objects.first()
            if not platform or not platform.ledger_id or not client.ledger_exists(platform.ledger_id):
                platform_ledger_status = "repaired"
                mwk_float_status = "repaired"
                usdt_float_status = "repaired"
                mwk_contra_status = "repaired"
                usdt_contra_status = "repaired"
                usdt_frozen_status = "repaired"
                platform = get_or_create_platform_account(client=client)
            else:
                if not platform.mwk_float_balance_id or not client.balance_exists(platform.mwk_float_balance_id):
                    mwk_float_status = "repaired"
                if not platform.usdt_float_balance_id or not client.balance_exists(platform.usdt_float_balance_id):
                    usdt_float_status = "repaired"
                if not platform.mwk_external_contra_id or not client.balance_exists(platform.mwk_external_contra_id):
                    mwk_contra_status = "repaired"
                if not platform.usdt_external_contra_id or not client.balance_exists(platform.usdt_external_contra_id):
                    usdt_contra_status = "repaired"
                if not platform.usdt_frozen_balance_id or not client.balance_exists(platform.usdt_frozen_balance_id):
                    usdt_frozen_status = "repaired"

                platform = get_or_create_platform_account(client=client)
        except Exception as exc:
            err_msg = f"PlatformAccount reconciliation error: {exc}"
            errors.append(err_msg)
            self.stdout.write(self.style.ERROR(err_msg))

        self.stdout.write("Platform account:")
        self.stdout.write(f"  Ledger: {platform_ledger_status}")
        self.stdout.write(f"  MWK float: {mwk_float_status}")
        self.stdout.write(f"  USDT float: {usdt_float_status}")
        self.stdout.write(f"  MWK contra: {mwk_contra_status}")
        self.stdout.write(f"  USDT contra: {usdt_contra_status}")
        self.stdout.write(f"  USDT frozen: {usdt_frozen_status}")
        self.stdout.write("")

        # 2. Reconcile Active Users
        users_checked = 0
        ledgers_repaired = 0
        mwk_repaired = 0
        usdt_repaired = 0

        users = User.objects.filter(is_active=True)

        for user in users:
            users_checked += 1
            try:
                prev_ledger = user.blnk_ledger_id
                mwk_w = Wallet.objects.filter(user=user, currency="MWK").first()
                usdt_w = Wallet.objects.filter(user=user, currency="USDT").first()

                prev_mwk_bal = mwk_w.blnk_balance_id if mwk_w else None
                prev_usdt_bal = usdt_w.blnk_balance_id if usdt_w else None

                mwk_wallet, usdt_wallet = ensure_user_wallets(user)

                user.refresh_from_db()
                if prev_ledger != user.blnk_ledger_id:
                    ledgers_repaired += 1
                if not prev_mwk_bal or prev_mwk_bal != mwk_wallet.blnk_balance_id:
                    mwk_repaired += 1
                if not prev_usdt_bal or prev_usdt_bal != usdt_wallet.blnk_balance_id:
                    usdt_repaired += 1

            except Exception as exc:
                err_msg = f"User '{user.username}' (id={user.id}) reconciliation error: {exc}"
                errors.append(err_msg)
                logger.error(f"[BLNK_RECONCILIATION_ERROR] {err_msg}")

        self.stdout.write("Users:")
        self.stdout.write(f"  Checked: {users_checked}")
        self.stdout.write(f"  Ledgers repaired: {ledgers_repaired}")
        self.stdout.write(f"  MWK wallets repaired: {mwk_repaired}")
        self.stdout.write(f"  USDT wallets repaired: {usdt_repaired}")
        self.stdout.write("")

        self.stdout.write(f"Errors: {len(errors)}")
        if errors:
            for err in errors:
                self.stdout.write(self.style.ERROR(f"  - {err}"))

        self.stdout.write("")
        if errors:
            self.stdout.write(self.style.ERROR("Status: FAILED"))
        else:
            self.stdout.write(self.style.SUCCESS("Status: SUCCESS"))
