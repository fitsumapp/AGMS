from django.db import connection, transaction
from .models import BillingSequence


@transaction.atomic
def _generate_next_billing_number(sequence_name: str, prefix: str) -> str:
    """
    Generate an atomic, sequential, concurrency-safe business number.
    Uses PostgreSQL transaction advisory lock (pg_advisory_xact_lock)
    combined with row-level locking (SELECT ... FOR UPDATE) on BillingSequence.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            # 64-bit integer hash lock automatically released at end of transaction
            cursor.execute(f"SELECT pg_advisory_xact_lock(hashtext('billing_sequence_{sequence_name}'))")

    seq, _ = BillingSequence.objects.select_for_update().get_or_create(
        name=sequence_name,
        defaults={"last_number": 0},
    )
    seq.last_number += 1
    seq.save(update_fields=["last_number", "updated_at"])
    return f"{prefix}-{seq.last_number:06d}"


def generate_next_invoice_number(prefix="INV") -> str:
    return _generate_next_billing_number("invoice_number", prefix)


def generate_next_payment_number(prefix="PAY") -> str:
    return _generate_next_billing_number("payment_number", prefix)


def generate_next_receipt_number(prefix="REC") -> str:
    return _generate_next_billing_number("receipt_number", prefix)


def generate_next_refund_number(prefix="REF") -> str:
    return _generate_next_billing_number("refund_number", prefix)
