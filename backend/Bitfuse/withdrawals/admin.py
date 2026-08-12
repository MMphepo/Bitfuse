from django.contrib import admin
from .models import Withdrawal, WithdrawalConfig


@admin.register(WithdrawalConfig)
class WithdrawalConfigAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "withdrawal_fee",
        "min_usdt_withdrawal",
        "max_usdt_withdrawal",
        "withdrawals_frozen",
        "updated_at",
    ]
    list_editable = ["withdrawals_frozen", "withdrawal_fee", "min_usdt_withdrawal", "max_usdt_withdrawal"]

    def has_add_permission(self, request):
        # Prevent multiple config rows
        return not WithdrawalConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "amount",
        "fee",
        "net_amount",
        "network",
        "destination_address",
        "status",
        "transaction_hash",
        "created_at",
        "updated_at",
    ]
    list_filter = ["status", "network", "asset", "created_at"]
    search_fields = ["id", "user__username", "user__email", "destination_address", "transaction_hash"]
    readonly_fields = [
        "id",
        "user",
        "asset",
        "network",
        "amount",
        "fee",
        "net_amount",
        "destination_address",
        "status",
        "transaction_hash",
        "failure_reason",
        "blnk_transaction_refs",
        "created_at",
        "updated_at",
        "broadcast_at",
        "confirmed_at",
    ]

    def has_add_permission(self, request):
        return False
