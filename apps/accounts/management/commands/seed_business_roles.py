from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.roles import seed_roles


class Command(BaseCommand):
    help = "Create or refresh the six business roles with implemented, scoped permissions."

    @transaction.atomic
    def handle(self, *args, **options):
        for role, count in seed_roles().items():
            self.stdout.write(f"{role}: {count} permissions")
