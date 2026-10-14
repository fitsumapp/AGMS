from .models import AuditEvent


def record_event(actor, action, target, metadata=None):
    if metadata and set(metadata) - {"field", "role", "branch_code", "reason"}:
        raise ValueError("Audit metadata contains unsupported keys.")
    return AuditEvent.objects.create(actor=actor, action=action,
        target_type=target._meta.label, target_id=target.pk, metadata=metadata or {})
