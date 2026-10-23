from datetime import date
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.services import can_access_branch
from apps.members.models import Member
from .models import (
    StaffDocument,
    StaffProfile,
    StaffShift,
    TrainerMemberAssignment,
    TrainerProfile,
    TrainingSession,
)


def schedule_training_session(
    actor,
    trainer,
    member,
    branch,
    session_date,
    start_time,
    end_time,
    session_type=TrainingSession.SessionType.PERSONAL_TRAINING,
    notes="",
    assignment=None,
):
    """
    Concurrency-safe transactional scheduling of personal training sessions.
    Locks trainer and member rows to prevent double-booking race conditions.
    """
    if end_time <= start_time:
        raise ValidationError({"end_time": "Session end time must be after start time."})

    with transaction.atomic():
        # Acquire row locks in consistent hierarchy: 1. Branch, 2. TrainerProfile, 3. Member
        from apps.branches.models import Branch
        locked_branch = Branch.objects.select_for_update().get(pk=branch.pk)
        locked_trainer = (
            TrainerProfile.objects.select_for_update()
            .select_related("staff")
            .get(pk=trainer.pk)
        )
        locked_member = Member.objects.select_for_update().get(pk=member.pk)

        if not locked_trainer.is_assigned_to_branch(branch):
            raise ValidationError({"branch": f"Trainer {locked_trainer.full_name} is not authorized for branch {branch.name}."})

        # Check trainer scheduling conflict
        trainer_conflicts = TrainingSession.objects.filter(
            trainer=locked_trainer,
            session_date=session_date,
            status=TrainingSession.SessionStatus.SCHEDULED,
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if trainer_conflicts.exists():
            raise ValidationError({"start_time": f"Trainer {locked_trainer.full_name} already has a conflicting scheduled session at this time."})

        # Check member scheduling conflict
        member_conflicts = TrainingSession.objects.filter(
            member=locked_member,
            session_date=session_date,
            status=TrainingSession.SessionStatus.SCHEDULED,
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if member_conflicts.exists():
            raise ValidationError({"member": f"{locked_member.full_name} is already scheduled for another training session at this time."})

        session = TrainingSession.objects.create(
            trainer=locked_trainer,
            member=locked_member,
            branch=branch,
            assignment=assignment,
            session_date=session_date,
            start_time=start_time,
            end_time=end_time,
            session_type=session_type,
            trainer_notes=notes,
            status=TrainingSession.SessionStatus.SCHEDULED,
            created_by=actor,
        )

        record_event(
            actor,
            "training_session_scheduled",
            session,
            {
                "employee_number": locked_trainer.employee_number,
                "member_number": locked_member.member_number,
                "branch_code": branch.code,
            },
        )
        return session


def assign_trainer_to_member(
    actor,
    trainer,
    member,
    branch,
    start_date=None,
    end_date=None,
    notes="",
):
    """
    Concurrency-safe transactional assignment of a trainer to a member.
    Enforces max_active_clients capacity limit and branch authorization.
    """
    start_date = start_date or timezone.localdate()
    if end_date and end_date < start_date:
        raise ValidationError({"end_date": "End date cannot precede assignment start date."})

    with transaction.atomic():
        # Acquire row lock on TrainerProfile to serialize concurrent assignments
        locked_trainer = (
            TrainerProfile.objects.select_for_update()
            .select_related("staff")
            .get(pk=trainer.pk)
        )
        locked_member = Member.objects.select_for_update().get(pk=member.pk)

        if not locked_trainer.is_assigned_to_branch(branch):
            raise ValidationError({"branch": f"Trainer {locked_trainer.full_name} is not authorized for branch {branch.name}."})

        # Verify capacity under row lock
        active_clients_count = TrainerMemberAssignment.objects.filter(
            trainer=locked_trainer,
            status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
        ).count()

        from apps.core.services import resolve_setting
        effective_capacity = resolve_setting(branch, "trainer_max_active_clients", default=locked_trainer.max_active_clients)
        effective_max = min(locked_trainer.max_active_clients, effective_capacity) if locked_trainer.max_active_clients else effective_capacity

        if active_clients_count >= effective_max:
            raise ValidationError(
                {"trainer": f"Trainer {locked_trainer.full_name} has reached maximum client capacity ({effective_max})."}
            )

        # Prevent duplicate active assignment
        if TrainerMemberAssignment.objects.filter(
            trainer=locked_trainer,
            member=locked_member,
            status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
        ).exists():
            raise ValidationError(
                {"member": f"{locked_member.full_name} already has an active assignment with Trainer {locked_trainer.full_name}."}
            )

        assignment = TrainerMemberAssignment.objects.create(
            trainer=locked_trainer,
            member=locked_member,
            branch=branch,
            assigned_by=actor,
            start_date=start_date,
            end_date=end_date,
            status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
            notes=notes,
            created_by=actor,
        )

        record_event(
            actor,
            "trainer_member_assigned",
            assignment,
            {
                "employee_number": locked_trainer.employee_number,
                "member_number": locked_member.member_number,
                "branch_code": branch.code,
            },
        )
        return assignment


def terminate_staff(actor, staff_profile, termination_date=None, reason=""):
    """
    Auditable staff termination workflow:
    - Revokes login credentials (is_active=False, is_staff=False).
    - Revokes all active branch permissions.
    - Sets trainer profile status to INACTIVE.
    - Cancels future scheduled shifts and personal training sessions.
    - Preserves all historical attendance, training, and billing records.
    """
    if not (is_owner(actor) or actor.is_superuser):
        if not (has_role(actor, "Branch Manager") and can_access_branch(actor, staff_profile.primary_branch)):
            raise PermissionDenied("You do not have permission to terminate this staff member.")

    termination_date = termination_date or timezone.localdate()
    if staff_profile.hire_date and termination_date < staff_profile.hire_date:
        raise ValidationError({"termination_date": "Termination date cannot be before hire date."})

    with transaction.atomic():
        # Update staff profile
        staff_profile.employment_status = StaffProfile.EmploymentStatus.TERMINATED
        staff_profile.termination_date = termination_date
        if reason:
            staff_profile.notes = f"{staff_profile.notes}\n[Termination Reason]: {reason}".strip()
        staff_profile.save(update_fields=["employment_status", "termination_date", "notes"])

        # Immediately revoke user authentication & staff access
        user = staff_profile.user
        user.is_active = False
        user.is_staff = False
        user.save(update_fields=["is_active", "is_staff"])

        # Revoke all branch assignments
        user.branch_access.filter(is_active=True).update(is_active=False, is_primary=False)

        # Handle trainer deactivation if applicable
        if hasattr(staff_profile, "trainer_profile"):
            trainer = staff_profile.trainer_profile
            trainer.status = TrainerProfile.TrainerStatus.INACTIVE
            trainer.save(update_fields=["status"])

            # Cancel active client pairings
            trainer.member_assignments.filter(
                status=TrainerMemberAssignment.AssignmentStatus.ACTIVE
            ).update(status=TrainerMemberAssignment.AssignmentStatus.CANCELLED)

            # Cancel upcoming scheduled sessions
            upcoming_sessions = trainer.sessions.filter(
                status=TrainingSession.SessionStatus.SCHEDULED,
                session_date__gte=termination_date,
            )
            for session in upcoming_sessions:
                session.cancel_session(user=actor, reason="Trainer employment terminated.")

        # Cancel upcoming scheduled shifts
        staff_profile.shifts.filter(
            status=StaffShift.ShiftStatus.SCHEDULED,
            date__gte=termination_date,
        ).update(status=StaffShift.ShiftStatus.CANCELLED)

        record_event(
            actor,
            "staff_terminated",
            staff_profile,
            {
                "employee_number": staff_profile.employee_number,
                "reason": reason or "Employment terminated",
            },
        )
        return staff_profile


def authorize_staff_document_download(user, document):
    """
    Object-level authorization check for accessing private staff employment documents.
    """
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        raise PermissionDenied("Authentication required.")

    # Owner and Superuser have full access
    if is_owner(user) or user.is_superuser:
        return True

    # The staff member themselves can view/download their own document
    if document.staff.user_id == user.id:
        return True

    # Branch Manager can access documents for staff at their authorized branches
    if has_role(user, "Branch Manager"):
        staff_branches = document.staff.get_assigned_branches()
        for b in staff_branches:
            if can_access_branch(user, b):
                return True

    raise PermissionDenied("You are not authorized to view or download this staff document.")
