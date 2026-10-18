from django.core.management.base import BaseCommand
from apps.notifications.services import dispatch_scheduled_reminders


class Command(BaseCommand):
    help = "Dispatch scheduled membership and billing reminders (intended for daily cron schedule)."

    def handle(self, *args, **options):
        self.stdout.write("Inspecting upcoming membership expiries and overdue invoices...")
        counts = dispatch_scheduled_reminders()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reminders scheduled:\n"
                f"  - 7 Days Before Expiry: {counts['expiry_7d']}\n"
                f"  - 3 Days Before Expiry: {counts['expiry_3d']}\n"
                f"  - 1 Day Before Expiry:  {counts['expiry_1d']}\n"
                f"  - Expired Today:        {counts['expired']}\n"
                f"  - Overdue Invoices:     {counts['overdue_invoices']}\n"
            )
        )
