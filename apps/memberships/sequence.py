from django.db import connection, transaction
from .models import MembershipSequence


@transaction.atomic
def generate_next_membership_number(prefix="MSHIP"):
    """
    Generate an atomic, sequential, concurrency-safe membership number.
    Format: MSHIP-000001, MSHIP-000002, etc.
    Uses PostgreSQL transaction advisory lock (pg_advisory_xact_lock)
    combined with row-level locking (SELECT ... FOR UPDATE) on MembershipSequence
    to guarantee zero duplicate numbers and zero race conditions under concurrent
    assignments, even before the sequence row is initialized.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            # 64-bit integer hash lock automatically released at end of transaction
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('membership_number_sequence'))")

    seq, _ = MembershipSequence.objects.select_for_update().get_or_create(
        name="membership_number",
        defaults={"last_number": 0},
    )
    seq.last_number += 1
    seq.save(update_fields=["last_number", "updated_at"])
    return f"{prefix}-{seq.last_number:06d}"
