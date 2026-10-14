from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.roles import has_role, is_owner
from .models import BUSINESS_ROLES, Branch, UserBranchAccess

SESSION_BRANCH_KEY = "active_branch_id"


def accessible_branches(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return Branch.objects.none()
    branches = Branch.objects.filter(is_active=True)
    if is_owner(user):
        return branches
    if not user.groups.filter(name__in=BUSINESS_ROLES).exists():
        return Branch.objects.none()
    return branches.filter(user_access__user=user, user_access__is_active=True).distinct()


def can_access_branch(user, branch):
    return bool(branch and branch.is_active and accessible_branches(user).filter(pk=branch.pk).exists())


def can_manage_branch(user, branch):
    return bool(branch and (is_owner(user) or
        (has_role(user, "Branch Manager") and can_access_branch(user, branch))))


def is_branch_manager(user, branch):
    return bool(user and user.is_active and has_role(user, "Branch Manager") and
        UserBranchAccess.objects.filter(user=user, branch=branch, is_active=True).exists())


def scoped_queryset(queryset, user, branch_field="branch"):
    """Use for all future branch-owned business querysets, regardless of session selection."""
    return queryset.filter(**{f"{branch_field}__in": accessible_branches(user)})


def active_branch(request):
    branches = accessible_branches(request.user)
    selected = request.session.get(SESSION_BRANCH_KEY)
    branch = None
    if selected:
        try:
            branch = branches.filter(pk=UUID(str(selected))).first()
        except (ValueError, TypeError):
            pass
        if not branch:
            request.session.pop(SESSION_BRANCH_KEY, None)
    if not branch and not is_owner(request.user):
        branch = branches.first()
        if branch:
            request.session[SESSION_BRANCH_KEY] = str(branch.pk)
    return branch


def select_branch(request, branch_id):
    from .audit import record_event
    if branch_id == "all" and is_owner(request.user):
        request.session.pop(SESSION_BRANCH_KEY, None)
        record_event(request.user, "branch_context_all", request.user)
        return None
    try:
        branch = accessible_branches(request.user).get(pk=UUID(str(branch_id)))
    except (ValueError, TypeError, Branch.DoesNotExist):
        record_event(request.user, "branch_switch_denied", request.user,
            {"reason": "unauthorized_or_inactive"})
        raise PermissionDenied("This branch is unavailable.")
    request.session[SESSION_BRANCH_KEY] = str(branch.pk)
    record_event(request.user, "branch_context_selected", branch)
    return branch


@transaction.atomic
def assign_branch(*, actor, user, branch, primary=False):
    from .audit import record_event
    if not is_owner(actor):
        raise PermissionDenied("Only an owner or technical superuser may assign branch access.")
    if not branch.is_active:
        raise ValidationError("Cannot assign an inactive branch.")
    if user.is_superuser or not user.is_staff or not user.is_active or not user.groups.filter(name__in=BUSINESS_ROLES).exists():
        raise ValidationError("An active business role is required for branch access.")
    if primary:
        UserBranchAccess.objects.filter(user=user, is_active=True, is_primary=True).update(is_primary=False)
    assignment, created = UserBranchAccess.objects.update_or_create(user=user, branch=branch,
        defaults={"is_active": True, "is_primary": primary, "assigned_by": actor})
    record_event(actor, "branch_access_assigned" if created else "branch_access_updated", assignment,
        {"branch_code": branch.code})
    return assignment


@transaction.atomic
def revoke_branch(*, actor, assignment):
    from .audit import record_event
    if not is_owner(actor):
        raise PermissionDenied
    assignment.is_active = False
    assignment.is_primary = False
    assignment.save(update_fields=["is_active", "is_primary", "updated_at"])
    record_event(actor, "branch_access_revoked", assignment, {"branch_code": assignment.branch.code})
