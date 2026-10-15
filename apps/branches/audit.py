from .models import AuditEvent


ALLOWED_AUDIT_KEYS = {
    "field", "role", "branch_code", "reason",
    "member_number", "status", "old_branch_code", "new_branch_code",
    "document_type", "note_category", "detail",
}


def record_event(actor, action, target, metadata=None):
    if metadata and set(metadata) - ALLOWED_AUDIT_KEYS:
        raise ValueError("Audit metadata contains unsupported keys.")
    return AuditEvent.objects.create(actor=actor, action=action,
        target_type=target._meta.label, target_id=target.pk, metadata=metadata or {})
