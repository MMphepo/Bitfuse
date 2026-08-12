from django.db import migrations


def backfill_payment_reference(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.filter(payment_reference=""):
        order.payment_reference = order.reference_number.replace("-", "")
        order.save(update_fields=["payment_reference"])


def clear_payment_reference(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.update(payment_reference="")


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_orderauditlog_ordersettlement_order_expires_at_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_payment_reference, clear_payment_reference),
    ]
