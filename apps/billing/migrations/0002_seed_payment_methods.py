from django.db import migrations


def seed_payment_methods(apps, schema_editor):
    PaymentMethod = apps.get_model("billing", "PaymentMethod")
    methods = [
        {
            "code": "CASH",
            "name": "Cash",
            "method_type": "CASH",
            "is_active": True,
            "requires_reference": False,
            "requires_verification": False,
        },
        {
            "code": "BANK_TRANSFER",
            "name": "Bank Transfer",
            "method_type": "BANK_TRANSFER",
            "is_active": True,
            "requires_reference": True,
            "requires_verification": True,
        },
        {
            "code": "TELEBIRR",
            "name": "Telebirr",
            "method_type": "TELEBIRR",
            "is_active": True,
            "requires_reference": True,
            "requires_verification": True,
        },
        {
            "code": "MPESA",
            "name": "M-PESA",
            "method_type": "MPESA",
            "is_active": True,
            "requires_reference": True,
            "requires_verification": True,
        },
    ]
    for data in methods:
        PaymentMethod.objects.update_or_create(code=data["code"], defaults=data)


def unseed_payment_methods(apps, schema_editor):
    PaymentMethod = apps.get_model("billing", "PaymentMethod")
    PaymentMethod.objects.filter(code__in=["CASH", "BANK_TRANSFER", "TELEBIRR", "MPESA"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_payment_methods, unseed_payment_methods),
    ]
