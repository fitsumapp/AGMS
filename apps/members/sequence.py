from django.db import connection, transaction
from .models import MemberSequence


@transaction.atomic
def generate_next_member_number(prefix="MEM"):
    """
    Generate an atomic, sequential, concurrency-safe member number.
    Format: MEM-000001, MEM-000002, etc.
    Uses PostgreSQL transaction advisory lock (pg_advisory_xact_lock)
    combined with row-level locking (SELECT ... FOR UPDATE) on MemberSequence
    to guarantee zero duplicate numbers and zero race conditions under concurrent
    registrations even when the sequence row has not yet been initialized.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            # 64-bit integer hash lock automatically released at end of transaction
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('member_number_sequence'))")

    seq, _ = MemberSequence.objects.select_for_update().get_or_create(
        name="member_number",
        defaults={"last_number": 0},
    )
    seq.last_number += 1
    seq.save(update_fields=["last_number", "updated_at"])
    return f"{prefix}-{seq.last_number:06d}"

