from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.services import can_access_branch
from .models import Member, MemberDocument


def download_member_document(request, document_id):
    """
    Securely download a member's private document.
    Enforces staff authentication, role authorization, and branch scoping.
    Prevents direct-object-reference and cross-branch vulnerabilities.
    """
    if not request.user.is_authenticated or not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied("Authentication required.")

    # Disallow Accountant, Trainer, and POS staff from member private documents
    if (has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff")) and not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
        raise PermissionDenied("Your role does not permit downloading member private documents.")

    document = (
        MemberDocument.objects.filter(pk=document_id)
        .select_related("member__home_branch")
        .first()
    )
    if not document or not document.file:
        raise Http404("Document not found.")

    if not is_owner(request.user) and not can_access_branch(request.user, document.member.home_branch):
        record_event(
            request.user,
            "document_download_unauthorized",
            document,
            {"member_number": document.member.member_number, "reason": "unauthorized_branch"},
        )
        raise PermissionDenied("You are not authorized to access documents for this branch.")

    try:
        file_handle = document.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("Document file missing from private storage.")

    record_event(
        request.user,
        "member_document_downloaded",
        document,
        {
            "member_number": document.member.member_number,
            "document_type": document.document_type,
        },
    )

    response = FileResponse(file_handle, as_attachment=True, filename=document.file.name.split("/")[-1])
    response["Cache-Control"] = "private, no-store, must-revalidate"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def view_member_photo(request, member_id):
    """Securely serve a member's private profile photo to authorized staff."""
    if not request.user.is_authenticated or not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied("Authentication required.")

    member = Member.objects.filter(pk=member_id).select_related("home_branch").first()
    if not member or not member.photo:
        raise Http404("Photo not found.")

    if not is_owner(request.user) and not can_access_branch(request.user, member.home_branch):
        raise PermissionDenied("You are not authorized to view photos for members of this branch.")

    try:
        file_handle = member.photo.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("Photo file missing.")

    response = FileResponse(file_handle)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
