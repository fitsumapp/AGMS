from django.db import transaction
from .models import MemberSequence


@transaction.atomic
def generate_next_member_number(prefix="MEM"):
    """
    Generate an atomic, sequential, concurrency-safe member number.
    Format: MEM-000001, MEM-000002, etc.
    Uses row-locking (SELECT ... FOR UPDATE) on MemberSequence table
    to guarantee zero duplicate numbers under concurrent registrations.
    """
    seq, _ = MemberSequence.objects.select_for_update().get_or_create(
        name="member_number",
        defaults={"last_number": 0},
    )
    seq.last_number += 1
    seq.save(update_fields=["last_number", "updated_at"])
    return f"{prefix}-{seq.last_number:06d}"
