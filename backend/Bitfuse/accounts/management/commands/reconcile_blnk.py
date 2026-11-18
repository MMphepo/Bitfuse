import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.models import PlatformAccount, Wallet
from accounts.blnk_client import BlnkClient

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Read-only audit of Bitfuse financial references against the current Blnk ledger instance."

    def handle(self, *args, **options):
        self.stdout.write("=== Bitfuse Blnk Reconciliation ===")
        self.stdout.write("")

        client = BlnkClient()
        errors = []

        # 1. Audit Platform Account without replacing any ledger references.
        platform_ledger_status = "ok"
        mwk_float_status = "ok"
        usdt_float_status = "ok"
        mwk_contra_status = "ok"
        usdt_contra_status = "ok"
        usdt_frozen_status = "ok"

        platform = PlatformAccount.objects.first()
        if not platform:
            platform_ledger_status = "missing"
            errors.append("PlatformAccount is not initialized.")
        else:
            if not platform.ledger_id or not client.ledger_exists(platform.ledger_id):
                platform_ledger_status = "missing"
                errors.append(f"Platform ledger is missing from Blnk: {platform.ledger_id or '(empty ID)'}")
            for field, label in (
                ("mwk_float_balance_id", "MWK float"),
                ("usdt_float_balance_id", "USDT float"),
                ("mwk_external_contra_id", "MWK contra"),
                ("usdt_external_contra_id", "USDT contra"),
                ("usdt_frozen_balance_id", "USDT frozen"),
            ):
                balance_id = getattr(platform, field)
                if not balance_id or not client.balance_exists(balance_id):
                    errors.append(f"Platform {label} balance is missing from Blnk: {balance_id or '(empty ID)'}")
                    if field == "mwk_float_balance_id":
                        mwk_float_status = "missing"
                    elif field == "usdt_float_balance_id":
                        usdt_float_status = "missing"
                    elif field == "mwk_external_contra_id":
                        mwk_contra_status = "missing"
                    elif field == "usdt_external_contra_id":
                        usdt_contra_status = "missing"
                    else:
                        usdt_frozen_status = "missing"

        self.stdout.write("Platform account:")
        self.stdout.write(f"  Ledger: {platform_ledger_status}")
        self.stdout.write(f"  MWK float: {mwk_float_status}")
        self.stdout.write(f"  USDT float: {usdt_float_status}")
        self.stdout.write(f"  MWK contra: {mwk_contra_status}")
        self.stdout.write(f"  USDT contra: {usdt_contra_status}")
        self.stdout.write(f"  USDT frozen: {usdt_frozen_status}")
        self.stdout.write("")

        # 2. Audit active user references without provisioning or rewriting them.
        users_checked = 0
        invalid_user_references = 0

        users = User.objects.filter(is_active=True)

        for user in users:
            users_checked += 1
            if not user.blnk_ledger_id or not client.ledger_exists(user.blnk_ledger_id):
                err_msg = f"User '{user.username}' (id={user.id}) references missing Blnk ledger {user.blnk_ledger_id or '(empty ID)'}."
                errors.append(err_msg)
                logger.error(f"[BLNK_RECONCILIATION_ERROR] {err_msg}")
                invalid_user_references += 1
                continue

            for currency in ("MWK", "USDT"):
                wallet = Wallet.objects.filter(user=user, currency=currency).first()
                if not wallet or not wallet.blnk_balance_id or not client.balance_exists(wallet.blnk_balance_id):
                    balance_id = wallet.blnk_balance_id if wallet else ""
                    err_msg = (
                        f"User '{user.username}' (id={user.id}) {currency} wallet references "
                        f"missing Blnk balance {balance_id or '(empty ID)'}."
                    )
                    errors.append(err_msg)
                    logger.error(f"[BLNK_RECONCILIATION_ERROR] {err_msg}")
                    invalid_user_references += 1

        self.stdout.write("Users:")
        self.stdout.write(f"  Checked: {users_checked}")
        self.stdout.write(f"  Invalid references: {invalid_user_references}")
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
