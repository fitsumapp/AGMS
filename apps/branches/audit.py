import uuid
from .models import AuditEvent


ALLOWED_AUDIT_KEYS = {
    "field", "role", "branch_code", "reason",
    "member_number", "status", "old_branch_code", "new_branch_code",
    "document_type", "note_category", "detail",
    "membership_number", "package_code", "payment_clearance",
    "freeze_days", "start_date", "end_date", "new_end_date",
    "rejection_reason", "idempotency_key",
    "invoice_number", "payment_number", "refund_number",
    "amount", "total_amount", "paid_amount", "balance_due", "payment_method",
    "check_in_method", "check_out_method", "device_code", "result", "reason_code",
    "override_reason", "credential_type", "attendance_id",
    "employee_number", "session_id", "class_name", "booking_id",
    # Phase 14 additions:
    "setting_name", "old_value", "new_value", "feature_code",
    "import_type", "records_imported", "records_skipped", "records_failed",
    "backup_status", "file_name", "file_size", "action_summary",
}


def record_event(actor, action, target, metadata=None, branch=None):
    if metadata and set(metadata) - ALLOWED_AUDIT_KEYS:
        raise ValueError("Audit metadata contains unsupported keys.")
    
    target_pk = target.pk
    if not isinstance(target_pk, uuid.UUID):
        try:
            target_pk = uuid.UUID(str(target_pk))
        except (ValueError, AttributeError):
            target_pk = uuid.uuid5(uuid.NAMESPACE_DNS, f"{target._meta.label}:{target_pk}")

    evt_branch = branch
    if not evt_branch and hasattr(target, "branch") and getattr(target, "branch", None):
        evt_branch = target.branch
    elif not evt_branch and hasattr(target, "home_branch") and getattr(target, "home_branch", None):
        evt_branch = target.home_branch

    return AuditEvent.objects.create(
        actor=actor,
        branch=evt_branch,
        action=action,
        target_type=target._meta.label,
        target_id=target_pk,
        metadata=metadata or {},
    )
