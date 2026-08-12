from django.contrib import admin, messages

from .models import Order, OrderAuditLog, OrderSettlement
from .services import OrderError, verify_payment


class OrderAuditLogInline(admin.TabularInline):
    model = OrderAuditLog
    extra = 0
    can_delete = False
    readonly_fields = ["actor", "action", "from_status", "to_status", "note", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "reference_number", "user", "order_type", "usdt_amount", "mwk_amount",
        "fee_amount", "payment_method", "payment_transaction_id", "status", "created_at",
    ]
    list_filter = ["order_type", "status", "payment_method"]
    search_fields = ["reference_number", "payment_reference", "payment_transaction_id", "user__username"]
    readonly_fields = [
        "id", "reference_number", "payment_reference", "user", "order_type", "mwk_amount",
        "usdt_amount", "rate", "fee_percent", "fee_amount", "payment_transaction_id",
        "payment_submitted_at", "blnk_transaction_refs", "created_at", "completed_at",
    ]
    inlines = [OrderAuditLogInline]
    actions = ["approve_payment"]

    @admin.action(description="Approve mobile money payment and settle")
    def approve_payment(self, request, queryset):
        for order in queryset.filter(order_type="buy"):
            try:
                verify_payment(order, request.user)
            except (OrderError, RuntimeError) as exc:
                self.message_user(request, f"{order.reference_number}: {exc}", level=messages.ERROR)


@admin.register(OrderSettlement)
class OrderSettlementAdmin(admin.ModelAdmin):
    list_display = ["order", "usdt_credited", "mwk_received", "settled_by", "created_at"]
    readonly_fields = [f.name for f in OrderSettlement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OrderAuditLog)
class OrderAuditLogAdmin(admin.ModelAdmin):
    list_display = ["order", "action", "actor", "from_status", "to_status", "created_at"]
    list_filter = ["action"]
    readonly_fields = [f.name for f in OrderAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
