import base64
import hashlib
import io
import secrets
from datetime import datetime
from typing import Optional, Tuple
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

import qrcode
from qrcode.image.pil import PilImage

from .models import MemberCredential


def generate_raw_token(prefix: str = "AGMS-QR-v1") -> str:
    """Generate a high-entropy cryptographically secure token."""
    entropy = secrets.token_urlsafe(32)
    return f"{prefix}-{entropy}"


def hash_token(raw_token: str) -> str:
    """Compute deterministic SHA-256 hash of a credential token."""
    cleaned = (raw_token or "").strip()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


@transaction.atomic
def issue_member_credential(
    member,
    credential_type: str = MemberCredential.CredentialType.QR,
    issued_by=None,
    expiry_date: Optional[datetime] = None,
    notes: str = "",
) -> Tuple[MemberCredential, str]:
    """
    Issue a new credential for a member.
    Automatically revokes existing active credentials of the same type.
    Returns (credential_record, raw_token_string).
    """
    if not member:
        raise ValidationError("Member is required to issue a credential.")

    # Revoke existing active credentials of the same type
    MemberCredential.objects.filter(
        member=member,
        credential_type=credential_type,
        status=MemberCredential.Status.ACTIVE,
    ).update(
        status=MemberCredential.Status.REVOKED,
        revoked_date=timezone.now(),
        revoked_reason="Superseded by new credential issuance.",
    )

    raw_token = generate_raw_token(prefix=f"AGMS-{credential_type}")
    token_hash = hash_token(raw_token)

    credential = MemberCredential.objects.create(
        member=member,
        credential_type=credential_type,
        token_hash=token_hash,
        status=MemberCredential.Status.ACTIVE,
        issued_date=timezone.now(),
        expiry_date=expiry_date,
        issued_by=issued_by,
        notes=notes,
    )
    return credential, raw_token


@transaction.atomic
def reissue_member_credential(
    member,
    credential_type: str = MemberCredential.CredentialType.QR,
    reissued_by=None,
    reason: str = "Card replacement / re-print",
) -> Tuple[MemberCredential, str]:
    """
    Securely reissues a new credential for a member.
    Atomically revokes any existing active credentials of the same type with the specified reason.
    Returns (new_credential, new_raw_token).
    """
    if not member:
        raise ValidationError("Member is required to reissue a credential.")

    # Explicitly revoke old credentials with custom reason and timestamp
    old_active = MemberCredential.objects.filter(
        member=member,
        credential_type=credential_type,
        status=MemberCredential.Status.ACTIVE,
    )
    for old in old_active:
        revoke_credential(old, reason=reason, revoked_by=reissued_by)

    return issue_member_credential(
        member=member,
        credential_type=credential_type,
        issued_by=reissued_by,
        notes=f"Reissued. Reason: {reason}",
    )


@transaction.atomic
def revoke_credential(credential: MemberCredential, reason: str = "", revoked_by=None) -> MemberCredential:
    """Revoke an active credential."""
    if credential.status != MemberCredential.Status.ACTIVE:
        raise ValidationError(f"Credential is already {credential.status}.")

    credential.status = MemberCredential.Status.REVOKED
    credential.revoked_date = timezone.now()
    credential.revoked_reason = reason.strip() or "Revoked by staff."
    credential.save(update_fields=["status", "revoked_date", "revoked_reason", "updated_at"])
    return credential


def lookup_credential(raw_token: str) -> Optional[MemberCredential]:
    """Look up a member credential by its raw token string."""
    if not raw_token or not raw_token.strip():
        return None
    th = hash_token(raw_token.strip())
    try:
        return MemberCredential.objects.select_related("member", "member__home_branch").get(token_hash=th)
    except MemberCredential.DoesNotExist:
        return None


def generate_qr_code_base64(raw_token: str) -> str:
    """Generate a base64-encoded PNG data URI for the raw token."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(raw_token)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#0F766E", back_color="white")  # Dark teal branding
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64_str}"
