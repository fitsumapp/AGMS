import time
from django.core.management.base import BaseCommand
from apps.notifications.services import process_queue


class Command(BaseCommand):
    help = "Process pending delivery queue items using transaction-safe skip_locked row locking."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50, help="Max items to process in one batch")
        parser.add_argument("--daemon", action="store_true", help="Run continuously in a loop")
        parser.add_argument("--interval", type=int, default=5, help="Sleep interval in seconds for daemon mode")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        daemon = options["daemon"]
        interval = options["interval"]

        if daemon:
            self.stdout.write(self.style.SUCCESS(f"Starting notification queue worker (interval={interval}s)..."))
            try:
                while True:
                    stats = process_queue(batch_size=batch_size)
                    if stats["processed"] > 0:
                        self.stdout.write(
                            f"Processed: {stats['processed']} (Succeeded: {stats['succeeded']}, Failed: {stats['failed']})"
                        )
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Worker stopped by user."))
        else:
            stats = process_queue(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queue processing completed: {stats['processed']} processed "
                    f"({stats['succeeded']} succeeded, {stats['failed']} failed)."
                )
            )
