from typing import List, Optional, Tuple
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from apps.accounts.roles import is_owner
from apps.branches.models import Branch
from apps.branches.services import accessible_branches, active_branch


def resolve_analytics_branch_scope(
    user,
    requested_branch_id: Optional[str] = None,
) -> Tuple[QuerySet, Optional[Branch], str]:
    """
    Resolve the branch scope for analytics queries and reports.
    Returns:
      (branch_queryset, selected_single_branch_or_None, scope_label)
    
    Security & Anti-Leakage Rules:
    - Gym Owner: May select 'all' (returns all active branches) or a specific active branch.
    - Branch Manager: May select 'all' (returns only their assigned branches) or a specific assigned branch.
    - Non-manager staff: Restricted to their assigned branch(es).
    - If user supplies a branch ID outside their accessible branches, raise PermissionDenied.
    """
    user_branches = accessible_branches(user)
    if not user_branches.exists():
        raise PermissionDenied("You do not have access to any active gym branches.")

    is_gym_owner = is_owner(user) or user.is_superuser

    if not requested_branch_id or requested_branch_id.strip() in ("", "all", "ALL", "None"):
        if is_gym_owner:
            return user_branches, None, "All Branches (Facility-wide)"
        elif user_branches.count() > 1:
            return user_branches, None, f"All Assigned Branches ({user_branches.count()})"
        else:
            first_branch = user_branches.first()
            return user_branches, first_branch, first_branch.name

    # Specific branch requested: validate against user_branches
    target = user_branches.filter(pk=requested_branch_id).first()
    if not target:
        # Branch exists in system but user is not authorized, or invalid UUID
        raise PermissionDenied("You are not authorized to access analytics for this branch.")

    return user_branches.filter(pk=target.pk), target, target.name
