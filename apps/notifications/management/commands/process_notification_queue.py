import time
from django.core.management.base import BaseCommand
from apps.notifications.services import process_queue


class Command(BaseCommand):
    help = "Process pending delivery queue items using transaction-safe skip_locked row locking."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=50, help="Max items to process in one batch")
        parser.add_argument("--daemon", action="store_true", help="Run continuously in a loop")
        parser.add_argument("--interval", type=int, default=5, help="Sleep interval in seconds for daemon mode")
        parser.add_argument("--recover-stale", action="store_true", default=True, help="Recover items abandoned in PROCESSING status due to worker crash")
        parser.add_argument("--stale-threshold", type=int, default=300, help="Seconds before a locked item is considered stale (default 300s)")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        daemon = options["daemon"]
        interval = options["interval"]
        recover_stale = options["recover_stale"]
        stale_threshold = options["stale_threshold"]

        if daemon:
            self.stdout.write(self.style.SUCCESS(f"Starting notification queue worker (interval={interval}s)..."))
            try:
                while True:
                    stats = process_queue(
                        batch_size=batch_size,
                        recover_stale=recover_stale,
                        stale_threshold_seconds=stale_threshold,
                    )
                    if stats["processed"] > 0:
                        self.stdout.write(
                            f"Processed: {stats['processed']} (Succeeded: {stats['succeeded']}, Failed: {stats['failed']})"
                        )
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Worker stopped by user."))
        else:
            stats = process_queue(
                batch_size=batch_size,
                recover_stale=recover_stale,
                stale_threshold_seconds=stale_threshold,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queue processing completed: {stats['processed']} processed "
                    f"({stats['succeeded']} succeeded, {stats['failed']} failed)."
                )
            )
