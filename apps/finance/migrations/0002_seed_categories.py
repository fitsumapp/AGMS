from django.db import migrations

DEFAULT_CATEGORIES = [
    {"code": "RENT", "name": "Rent", "order": 1, "description": "Facility lease and rental payments"},
    {"code": "UTILITIES", "name": "Utilities", "order": 2, "description": "Water, electricity, power, sewage"},
    {"code": "SALARIES", "name": "Staff Salaries", "order": 3, "description": "Payroll for administrative, front desk, and support staff"},
    {"code": "EQUIPMENT_MAINT", "name": "Equipment Maintenance", "order": 4, "description": "Gym machinery repair, weights servicing, spare parts"},
    {"code": "CLEANING", "name": "Cleaning & Sanitation", "order": 5, "description": "Detergents, towel washing, janitorial supplies"},
    {"code": "INTERNET_COMM", "name": "Internet and Communication", "order": 6, "description": "Broadband, phone lines, SMS gateways"},
    {"code": "MARKETING", "name": "Marketing & Advertising", "order": 7, "description": "Social media campaigns, printing, promotional flyers"},
    {"code": "OFFICE_SUPPLIES", "name": "Office Supplies", "order": 8, "description": "Stationery, printer toner, paper, administrative items"},
    {"code": "SECURITY", "name": "Security", "order": 9, "description": "Security personnel, CCTV maintenance, access controls"},
    {"code": "TRANSPORT", "name": "Transport & Logistics", "order": 10, "description": "Fuel, vehicle maintenance, staff transport allowances"},
    {"code": "TRAINER_PAYMENTS", "name": "Trainer Payments", "order": 11, "description": "External trainer commissions, guest instructor stipends"},
    {"code": "OTHER", "name": "Other Expenses", "order": 12, "description": "Miscellaneous and incidental operational costs"},
]


def seed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    for cat in DEFAULT_CATEGORIES:
        ExpenseCategory.objects.get_or_create(
            code=cat["code"],
            defaults={
                "name": cat["name"],
                "order": cat["order"],
                "description": cat["description"],
                "is_active": True,
            },
        )


def unseed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    codes = [cat["code"] for cat in DEFAULT_CATEGORIES]
    ExpenseCategory.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
