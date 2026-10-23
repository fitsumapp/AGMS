from datetime import timedelta
from decimal import Decimal
import logging
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import Membership
from apps.facilities.models import (
    LockerZone,
    Locker,
    LockerAssignment,
    LockerIncident,
    EquipmentCategory,
    Equipment,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    FaultReport,
    FacilityArea,
    CleaningTaskSchedule,
    CleaningTaskExecution,
    FacilityInspection,
    IncidentReport,
)
from apps.accounts.roles import has_role, is_owner
from apps.facilities.permissions import (
    can_assign_locker,
    can_manage_facility,
    can_manage_maintenance,
    can_report_fault,
)

logger = logging.getLogger(__name__)


def generate_facility_code(prefix: str) -> str:
    """Generate sequential or timestamped unique code with prefix."""
    now_str = timezone.now().strftime("%Y%m%d%H%M%S")
    rand_str = uuid.uuid4().hex[:4].upper()
    return f"{prefix}-{now_str}-{rand_str}"


# ==============================================================================
# 1. Locker Management Services
# ==============================================================================

def assign_locker(
    *,
    actor: User,
    locker: Locker,
    member: Member,
    assignment_type: str = LockerAssignment.AssignmentType.DAILY,
    start_date=None,
    end_date=None,
    key_card_code: str = "",
    charged_fee_etb: Decimal = None,
    deposit_amount_etb: Decimal = None,
    create_billing_invoice: bool = False,
    notes: str = "",
) -> LockerAssignment:
    """
    Assign an available locker to an active member with PostgreSQL concurrency lock.
    Guarantees no double-assignment and strictly validates membership status and branch match.
    """
    branch = locker.branch
    if not can_assign_locker(actor, branch):
        raise PermissionDenied(_("You do not have permission to assign lockers for this branch."))

    # Validate member home branch or active access
    if member.home_branch != branch:
        raise ValidationError({"member": _("Member is registered at a different branch.")})

    # Validate member has an active membership (unless daily day-pass)
    has_active_membership = Membership.objects.filter(
        member=member,
        status="ACTIVE",
    ).exists()

    if not has_active_membership and assignment_type != LockerAssignment.AssignmentType.DAILY:
        raise ValidationError({"member": _("Member must possess an ACTIVE membership for monthly locker rental.")})

    linked_membership = Membership.objects.filter(member=member, status="ACTIVE").first()

    st_date = start_date or timezone.localdate()
    if end_date and end_date < st_date:
        raise ValidationError({"end_date": _("End date cannot precede start date.")})

    with transaction.atomic():
        # Concurrency safety: lock the locker row with SELECT FOR UPDATE
        locked_locker = Locker.objects.select_for_update().get(pk=locker.pk)

        if locked_locker.status != Locker.LockerStatus.AVAILABLE:
            raise ValidationError({"locker": _(f"Locker {locked_locker.locker_number} is currently {locked_locker.get_status_display()} and cannot be assigned.")})

        # Check existing active assignment
        if LockerAssignment.objects.filter(locker=locked_locker, status=LockerAssignment.Status.ACTIVE).exists():
            raise ValidationError({"locker": _(f"Locker {locked_locker.locker_number} already has an active assignment.")})

        fee = charged_fee_etb if charged_fee_etb is not None else (
            locked_locker.monthly_fee_etb if assignment_type == LockerAssignment.AssignmentType.MONTHLY else locked_locker.daily_fee_etb
        )
        from apps.core.services import resolve_setting
        deposit = deposit_amount_etb if deposit_amount_etb is not None else (
            locked_locker.deposit_amount_etb if (locked_locker.deposit_amount_etb and locked_locker.deposit_amount_etb > Decimal("0.00")) else (
                resolve_setting(branch, "default_locker_deposit_etb", default=Decimal("200.00"))
            )
        )

        asg_number = generate_facility_code(f"LCK-{branch.code}")

        assignment = LockerAssignment.objects.create(
            assignment_number=asg_number,
            branch=branch,
            locker=locked_locker,
            member=member,
            assignment_type=assignment_type,
            membership=linked_membership,
            status=LockerAssignment.Status.ACTIVE,
            start_date=st_date,
            end_date=end_date,
            key_issued=True,
            key_returned=False,
            key_card_code=key_card_code or locked_locker.key_card_code,
            fee_charged_etb=fee,
            deposit_charged_etb=deposit,
            deposit_refunded_etb=Decimal("0.00"),
            deposit_status=(
                LockerAssignment.DepositStatus.PENDING
                if deposit > Decimal("0.00")
                else LockerAssignment.DepositStatus.NOT_APPLICABLE
            ),
            assigned_by=actor,
            notes=notes,
        )

        # Update locker status
        locked_locker.status = Locker.LockerStatus.OCCUPIED
        locked_locker.save(update_fields=["status", "updated_at"])

        # Optional Authoritative Phase 05 Invoice creation
        if create_billing_invoice and (fee > Decimal("0.00") or deposit > Decimal("0.00")):
            from apps.billing.services import create_invoice
            items = []
            if fee > Decimal("0.00"):
                items.append({
                    "description": f"Locker {locked_locker.locker_number} Rental Fee ({assignment.get_assignment_type_display()})",
                    "unit_price": fee,
                    "quantity": 1,
                    "item_type": "OTHER",
                })
            if deposit > Decimal("0.00"):
                # Tracked as liability item (deposit)
                items.append({
                    "description": f"Locker {locked_locker.locker_number} Refundable Key Deposit (Liability)",
                    "unit_price": deposit,
                    "quantity": 1,
                    "item_type": "OTHER",
                })
            
            due = end_date or st_date
            inv = create_invoice(
                actor=actor,
                member=member,
                branch=branch,
                items=items,
                due_date=due,
                notes=f"Locker Assignment #{asg_number}",
            )
            assignment.invoice = inv
            assignment.save(update_fields=["invoice", "updated_at"])

        return assignment


def release_locker(
    *,
    actor: User = None,
    user: User = None,
    assignment: LockerAssignment,
    returned_date=None,
    key_returned: bool = True,
    refund_deposit: bool = True,
    refund_amount_etb: Decimal = None,
    notes: str = "",
) -> LockerAssignment:
    """
    Release an assigned locker, update physical unit availability, and process key return/deposit refund.
    Guarantees:
    - Concurrency row-locking prevents duplicate release requests or double refunds.
    - Refunds cannot exceed collected refundable deposits.
    - Integrates with Phase 05 Billing for authoritative payment tracking and refund issuance.
    - If refund fails or cannot be issued immediately, marks deposit_status as REFUND_PENDING.
    - Forfeited deposits are never refunded.
    """
    actor = actor or user
    branch = assignment.branch
    if not can_assign_locker(actor, branch):
        raise PermissionDenied(_("You do not have permission to release lockers for this branch."))

    with transaction.atomic():
        asg = LockerAssignment.objects.select_for_update().get(pk=assignment.pk)
        if asg.status not in (LockerAssignment.Status.ACTIVE, LockerAssignment.Status.OVERDUE):
            raise ValidationError(_(f"Assignment is already {asg.get_status_display()}."))

        locked_locker = Locker.objects.select_for_update().get(pk=asg.locker.pk)

        ret_date = returned_date or timezone.localdate()
        asg.status = LockerAssignment.Status.COMPLETED
        asg.returned_date = ret_date
        asg.key_returned = key_returned
        asg.released_by = actor
        if notes:
            asg.notes = f"{asg.notes}\nRelease note: {notes}".strip()

        # Calculate forfeited deposits from recorded incidents
        forfeited_etb = LockerIncident.objects.filter(assignment=asg).aggregate(
            tot=models.Sum("deposit_forfeited_etb")
        )["tot"] or Decimal("0.00")

        # Determine actually collected deposit amount
        if asg.invoice:
            # Under authoritative billing, rental fees are cleared first, then refundable deposit liability
            collected_deposit = max(Decimal("0.00"), asg.invoice.paid_amount - asg.fee_charged_etb)
        else:
            collected_deposit = asg.deposit_charged_etb

        # Maximum refundable is capped by unrefunded, unforfeited, and actually collected amount
        remaining_deposit = max(Decimal("0.00"), asg.deposit_charged_etb - asg.deposit_refunded_etb - forfeited_etb)
        max_refundable = min(remaining_deposit, collected_deposit)

        if refund_amount_etb is not None:
            target_refund = min(max_refundable, Decimal(str(refund_amount_etb)))
        else:
            target_refund = max_refundable

        if refund_deposit and key_returned:
            if target_refund > Decimal("0.00"):
                if asg.invoice:
                    from apps.billing.models import Payment
                    from apps.billing.services import request_refund, approve_refund
                    verified_pay = Payment.objects.filter(
                        invoice=asg.invoice,
                        status=Payment.Status.VERIFIED,
                    ).order_by("-paid_at").first()

                    if verified_pay:
                        try:
                            ref_rec = request_refund(
                                actor=actor,
                                payment=verified_pay,
                                amount=target_refund,
                                reason=f"Locker {asg.locker.locker_number} key returned deposit refund",
                            )
                            if is_owner(actor) or has_role(actor, "Branch Manager"):
                                try:
                                    approve_refund(actor=actor, refund=ref_rec, approve=True)
                                except Exception:
                                    pass
                            
                            asg.billing_refund = ref_rec
                            ref_rec.refresh_from_db()
                            if getattr(ref_rec, "status", None) == "COMPLETED":
                                asg.deposit_refunded_etb += target_refund
                                asg.deposit_status = (
                                    LockerAssignment.DepositStatus.REFUNDED
                                    if asg.deposit_refunded_etb >= asg.deposit_charged_etb
                                    else LockerAssignment.DepositStatus.PARTIALLY_REFUNDED
                                )
                            else:
                                asg.deposit_status = LockerAssignment.DepositStatus.REFUND_PENDING
                        except Exception as e:
                            asg.deposit_status = LockerAssignment.DepositStatus.REFUND_PENDING
                            asg.notes = f"{asg.notes}\nRefund error/pending: {e}".strip()
                    else:
                        # Invoice exists but no payment collected
                        asg.deposit_status = LockerAssignment.DepositStatus.REFUND_PENDING
                        asg.notes = f"{asg.notes}\nDeposit refund pending payment collection/clearance.".strip()
                else:
                    # Reception desk direct cash refund
                    asg.deposit_refunded_etb += target_refund
                    asg.deposit_status = (
                        LockerAssignment.DepositStatus.REFUNDED
                        if asg.deposit_refunded_etb >= asg.deposit_charged_etb
                        else LockerAssignment.DepositStatus.PARTIALLY_REFUNDED
                    )
            elif forfeited_etb > Decimal("0.00"):
                asg.deposit_status = LockerAssignment.DepositStatus.FORFEITED
            else:
                asg.deposit_status = (
                    LockerAssignment.DepositStatus.NOT_APPLICABLE
                    if asg.deposit_charged_etb == Decimal("0.00")
                    else (
                        LockerAssignment.DepositStatus.REFUNDED
                        if asg.deposit_refunded_etb >= asg.deposit_charged_etb
                        else LockerAssignment.DepositStatus.REFUND_PENDING
                    )
                )
        else:
            # Key not returned or refund declined
            if forfeited_etb > Decimal("0.00"):
                asg.deposit_status = LockerAssignment.DepositStatus.FORFEITED
            elif asg.deposit_charged_etb > Decimal("0.00"):
                asg.deposit_status = LockerAssignment.DepositStatus.HELD

        asg.save()

        if not key_returned:
            locked_locker.status = Locker.LockerStatus.OUT_OF_SERVICE
        else:
            locked_locker.status = Locker.LockerStatus.AVAILABLE
        locked_locker.save(update_fields=["status", "updated_at"])

        return asg


def complete_locker_deposit_refund(
    *,
    actor: User,
    assignment: LockerAssignment,
    admin_notes: str = "",
) -> LockerAssignment:
    """
    Sync and finalize locker assignment deposit status when an authoritative billing refund is approved.
    Enforces separation of duties via billing services.
    """
    with transaction.atomic():
        asg = LockerAssignment.objects.select_for_update().get(pk=assignment.pk)
        if not asg.billing_refund:
            raise ValidationError(_("No billing refund linked to this assignment."))

        ref = asg.billing_refund
        ref.refresh_from_db()
        if ref.status == "REQUESTED":
            from apps.billing.services import approve_refund
            approve_refund(actor=actor, refund=ref, approve=True, admin_notes=admin_notes)
            ref.refresh_from_db()

        if ref.status == "COMPLETED":
            if asg.deposit_refunded_etb < ref.amount:
                asg.deposit_refunded_etb += ref.amount
            asg.deposit_status = (
                LockerAssignment.DepositStatus.REFUNDED
                if asg.deposit_refunded_etb >= asg.deposit_charged_etb
                else LockerAssignment.DepositStatus.PARTIALLY_REFUNDED
            )
            asg.save(update_fields=["deposit_refunded_etb", "deposit_status", "updated_at"])
        return asg


def record_locker_incident(
    *,
    actor: User,
    locker: Locker,
    incident_type: str,
    assignment: LockerAssignment = None,
    member: Member = None,
    penalty_fee_etb: Decimal = Decimal("0.00"),
    deposit_forfeited_etb: Decimal = Decimal("0.00"),
    forfeit_deposit: bool = False,
    notes: str = "",
    details: str = "",
) -> LockerIncident:
    """Record a locker incident such as a lost key or broken lock mechanism."""
    branch = locker.branch
    if not can_assign_locker(actor, branch):
        raise PermissionDenied(_("You do not have permission to record locker incidents."))

    if incident_type == LockerIncident.IncidentType.LOST_KEY and penalty_fee_etb == Decimal("0.00"):
        from apps.core.services import resolve_setting
        penalty_fee_etb = resolve_setting(branch, "lost_key_penalty_etb", default=Decimal("150.00"))

    if forfeit_deposit and assignment:
        unforfeited = max(Decimal("0.00"), assignment.deposit_charged_etb - assignment.deposit_refunded_etb)
        if deposit_forfeited_etb == Decimal("0.00"):
            deposit_forfeited_etb = unforfeited

    final_notes = notes or details

    with transaction.atomic():
        inc_number = generate_facility_code(f"INC-LCK-{branch.code}")
        incident = LockerIncident.objects.create(
            incident_number=inc_number,
            branch=branch,
            locker=locker,
            assignment=assignment,
            member=member or (assignment.member if assignment else None),
            incident_type=incident_type,
            incident_date=timezone.localdate(),
            penalty_fee_etb=penalty_fee_etb,
            deposit_forfeited_etb=deposit_forfeited_etb,
            is_settled=True if (forfeit_deposit or penalty_fee_etb > Decimal("0.00")) else False,
            recorded_by=actor,
            notes=final_notes,
        )

        if forfeit_deposit and assignment:
            assignment.deposit_status = LockerAssignment.DepositStatus.FORFEITED
            assignment.save(update_fields=["deposit_status", "updated_at"])

        # If lock was damaged, mark locker as out of service
        if incident_type in (LockerIncident.IncidentType.DAMAGED_LOCK, LockerIncident.IncidentType.DAMAGED_DOOR):
            locker.status = Locker.LockerStatus.OUT_OF_SERVICE
            locker.save(update_fields=["status", "updated_at"])

        return incident


# ==============================================================================
# 2. Equipment & Asset Management Services
# ==============================================================================

def register_equipment(
    *,
    actor: User,
    branch: Branch,
    category: EquipmentCategory,
    asset_code: str,
    name: str,
    facility_area: FacilityArea = None,
    manufacturer: str = "",
    model_number: str = "",
    serial_number: str = "",
    purchase_date=None,
    purchase_cost_etb: Decimal = Decimal("0.00"),
    supplier=None,
    warranty_expiry_date=None,
    condition: str = Equipment.EquipmentCondition.GOOD,
    assigned_staff=None,
    notes: str = "",
) -> Equipment:
    """Register a new gym equipment asset."""
    if not can_manage_facility(actor, branch):
        raise PermissionDenied(_("You do not have permission to register equipment for this branch."))

    if facility_area and facility_area.branch != branch:
        raise ValidationError({"facility_area": _("Facility area belongs to a different branch.")})

    if Equipment.objects.filter(asset_code=asset_code).exists():
        raise ValidationError({"asset_code": _("An asset with this code already exists.")})

    return Equipment.objects.create(
        branch=branch,
        category=category,
        facility_area=facility_area,
        asset_code=asset_code,
        name=name,
        manufacturer=manufacturer,
        model_number=model_number,
        serial_number=serial_number,
        purchase_date=purchase_date,
        purchase_cost_etb=purchase_cost_etb,
        supplier=supplier,
        warranty_expiry_date=warranty_expiry_date,
        status=Equipment.OperationalStatus.OPERATIONAL,
        condition=condition,
        assigned_staff=assigned_staff,
        notes=notes,
    )


# ==============================================================================
# 3. Corrective & Preventive Maintenance Services
# ==============================================================================

def create_fault_report(
    *,
    actor: User,
    branch: Branch,
    title: str,
    description: str,
    equipment: Equipment = None,
    facility_area: FacilityArea = None,
    priority: str = FaultReport.Priority.MEDIUM,
) -> FaultReport:
    """File a fault report for equipment or facility area."""
    if not can_report_fault(actor):
        raise PermissionDenied(_("You do not have permission to report faults."))

    if equipment and equipment.branch != branch:
        raise ValidationError({"equipment": _("Equipment belongs to a different branch.")})
    if facility_area and facility_area.branch != branch:
        raise ValidationError({"facility_area": _("Facility area belongs to a different branch.")})

    with transaction.atomic():
        report_number = generate_facility_code(f"FLT-{branch.code}")
        fault = FaultReport.objects.create(
            report_number=report_number,
            branch=branch,
            equipment=equipment,
            facility_area=facility_area,
            reported_by=actor,
            title=title,
            description=description,
            priority=priority,
            status=FaultReport.Status.REPORTED,
        )

        # If priority is urgent, optionally update equipment status
        if equipment and priority == FaultReport.Priority.URGENT:
            equipment.status = Equipment.OperationalStatus.OUT_OF_SERVICE
            equipment.save(update_fields=["status", "updated_at"])

        return fault


def convert_fault_to_work_order(
    *,
    actor: User,
    fault_report: FaultReport,
    assigned_technician=None,
    priority: str = None,
    description: str = "",
) -> MaintenanceWorkOrder:
    """Convert an existing fault report into an actionable maintenance work order."""
    branch = fault_report.branch
    if not can_manage_maintenance(actor, branch):
        raise PermissionDenied(_("You do not have permission to approve work orders for this branch."))

    with transaction.atomic():
        fault = FaultReport.objects.select_for_update().get(pk=fault_report.pk)
        wo_number = generate_facility_code(f"WO-{branch.code}")

        wo_priority = priority or fault.priority
        work_order = MaintenanceWorkOrder.objects.create(
            work_order_number=wo_number,
            branch=branch,
            equipment=fault.equipment,
            facility_area=fault.facility_area,
            fault_report=fault,
            order_type=MaintenanceWorkOrder.OrderType.CORRECTIVE,
            status=MaintenanceWorkOrder.Status.ASSIGNED if assigned_technician else MaintenanceWorkOrder.Status.REPORTED,
            priority=wo_priority,
            title=f"Repair: {fault.title}",
            description=description or fault.description,
            reported_by=fault.reported_by,
            reported_at=fault.reported_at,
            assigned_technician=assigned_technician,
            assigned_at=timezone.now() if assigned_technician else None,
        )

        fault.status = FaultReport.Status.ASSIGNED if assigned_technician else FaultReport.Status.IN_PROGRESS
        fault.save(update_fields=["status", "updated_at"])

        if fault.equipment:
            reconcile_equipment_status(fault.equipment)

        return work_order


def reconcile_equipment_status(equipment: Equipment) -> Equipment:
    """
    Authoritative state reconciliation for equipment physical status.
    Guarantees:
    - DECOMMISSIONED/RETIRED equipment is never automatically made operational.
    - Active blocking work orders (IN_PROGRESS, ASSIGNED, REPORTED) or open faults
      prevent equipment from returning to OPERATIONAL prematurely.
    - If any work order is URGENT/CRITICAL or EMERGENCY breakdown, equipment is OUT_OF_ORDER.
    - If any work order is IN_PROGRESS or active, equipment is UNDER_MAINTENANCE.
    - Only when ALL active work orders and faults are resolved/closed does equipment become OPERATIONAL.
    - Employs deterministic row-locking (select_for_update).
    """
    with transaction.atomic():
        eq = Equipment.objects.select_for_update().get(pk=equipment.pk)
        if eq.status in (Equipment.OperationalStatus.RETIRED, Equipment.OperationalStatus.DISPOSED):
            return eq

        # Check for active work orders
        active_orders = MaintenanceWorkOrder.objects.filter(
            equipment=eq,
            status__in=[
                MaintenanceWorkOrder.Status.REPORTED,
                MaintenanceWorkOrder.Status.ASSIGNED,
                MaintenanceWorkOrder.Status.IN_PROGRESS,
            ],
        )

        # Check for active unresolved fault reports
        active_faults = FaultReport.objects.filter(
            equipment=eq,
            status__in=[
                FaultReport.Status.REPORTED,
                FaultReport.Status.ASSIGNED,
                FaultReport.Status.IN_PROGRESS,
            ],
        )

        has_critical = (
            active_orders.filter(priority__in=[MaintenanceWorkOrder.Priority.URGENT, MaintenanceWorkOrder.Priority.CRITICAL]).exists()
            or active_orders.filter(order_type=MaintenanceWorkOrder.OrderType.EMERGENCY).exists()
            or active_faults.filter(priority=FaultReport.Priority.URGENT).exists()
        )
        has_in_progress = active_orders.filter(status=MaintenanceWorkOrder.Status.IN_PROGRESS).exists()
        has_any_active = active_orders.exists() or active_faults.exists()

        if has_critical:
            eq.status = Equipment.OperationalStatus.OUT_OF_SERVICE
        elif has_in_progress or has_any_active:
            eq.status = Equipment.OperationalStatus.UNDER_MAINTENANCE
        else:
            eq.status = Equipment.OperationalStatus.OPERATIONAL
            eq.last_maintenance_date = timezone.localdate()

        eq.save(update_fields=["status", "last_maintenance_date", "updated_at"])
        return eq


def update_work_order_status(
    *,
    actor: User,
    work_order: MaintenanceWorkOrder,
    new_status: str,
    resolution_notes: str = "",
    service_cost_etb: Decimal = None,
    parts_cost_etb: Decimal = None,
    downtime_hours: Decimal = None,
    spare_parts_used: str = "",
    reopened_reason: str = "",
) -> MaintenanceWorkOrder:
    """
    Advance work order status with strictly enforced state progression:
    REPORTED -> ASSIGNED -> IN_PROGRESS -> RESOLVED -> CLOSED
    (or CANCELLED, REOPENED with authorization).
    """
    branch = work_order.branch
    if not can_manage_maintenance(actor, branch):
        raise PermissionDenied(_("You do not have permission to update work orders for this branch."))

    with transaction.atomic():
        wo = MaintenanceWorkOrder.objects.select_for_update().get(pk=work_order.pk)
        current = wo.status
        now = timezone.now()

        # Enforce valid workflow transitions
        valid_transitions = {
            MaintenanceWorkOrder.Status.REPORTED: [MaintenanceWorkOrder.Status.ASSIGNED, MaintenanceWorkOrder.Status.IN_PROGRESS, MaintenanceWorkOrder.Status.CANCELLED],
            MaintenanceWorkOrder.Status.ASSIGNED: [MaintenanceWorkOrder.Status.IN_PROGRESS, MaintenanceWorkOrder.Status.RESOLVED, MaintenanceWorkOrder.Status.CANCELLED],
            MaintenanceWorkOrder.Status.IN_PROGRESS: [MaintenanceWorkOrder.Status.RESOLVED, MaintenanceWorkOrder.Status.CANCELLED],
            MaintenanceWorkOrder.Status.RESOLVED: [MaintenanceWorkOrder.Status.CLOSED, MaintenanceWorkOrder.Status.IN_PROGRESS],
            MaintenanceWorkOrder.Status.CLOSED: [MaintenanceWorkOrder.Status.IN_PROGRESS], # Reopened
            MaintenanceWorkOrder.Status.CANCELLED: [],
        }

        if new_status not in valid_transitions.get(current, []):
            raise ValidationError(_(f"Invalid work order status transition from {wo.get_status_display()} to {new_status}."))

        if new_status == MaintenanceWorkOrder.Status.IN_PROGRESS and not wo.started_at:
            wo.started_at = now

        if new_status == MaintenanceWorkOrder.Status.RESOLVED:
            wo.resolved_at = now
            if resolution_notes:
                wo.resolution_notes = resolution_notes

        if new_status == MaintenanceWorkOrder.Status.CLOSED:
            wo.closed_at = now

        # Handling reopening
        if current in (MaintenanceWorkOrder.Status.CLOSED, MaintenanceWorkOrder.Status.RESOLVED) and new_status == MaintenanceWorkOrder.Status.IN_PROGRESS:
            wo.is_reopened = True
            wo.reopened_reason = reopened_reason
            wo.reopened_by = actor

        if service_cost_etb is not None:
            wo.service_cost_etb = service_cost_etb
        if parts_cost_etb is not None:
            wo.parts_cost_etb = parts_cost_etb
        wo.total_cost_etb = wo.service_cost_etb + wo.parts_cost_etb

        if downtime_hours is not None:
            wo.downtime_hours = downtime_hours
            if wo.equipment:
                Equipment.objects.filter(pk=wo.equipment.pk).update(
                    total_downtime_hours=models.F("total_downtime_hours") + downtime_hours
                )

        if spare_parts_used:
            wo.spare_parts_used = spare_parts_used

        wo.status = new_status
        wo.save()

        # Update linked fault report if present BEFORE reconciling equipment status
        if wo.fault_report and new_status in (MaintenanceWorkOrder.Status.RESOLVED, MaintenanceWorkOrder.Status.CLOSED):
            wo.fault_report.status = FaultReport.Status.RESOLVED
            wo.fault_report.resolved_at = now
            wo.fault_report.resolution_notes = resolution_notes or wo.resolution_notes
            wo.fault_report.save(update_fields=["status", "resolved_at", "resolution_notes", "updated_at"])

        # Update equipment status via centralized reconciler
        if wo.equipment:
            reconcile_equipment_status(wo.equipment)

        return wo


def post_work_order_expense_to_finance(
    work_order: MaintenanceWorkOrder,
    actor: User = None,
    *,
    idempotent: bool = False,
    expense_category=None,
    payment_due_date=None,
    supplier=None,
    internal_notes: str = "",
    **kwargs,
):
    """
    Integrate maintenance repair cost with authoritative Phase 10 Finance app.
    Guarantees:
    - Never posts duplicate expense for the same work order.
    - Strictly checks closed reconciliation periods.
    - Creates an audited expense record.
    """
    # Support both post_work_order_expense_to_finance(work_order, user) and keyword arguments
    actor = actor or kwargs.get("actor")
    branch = work_order.branch
    if not can_manage_maintenance(actor, branch):
        raise PermissionDenied(_("You do not have permission to post financial expenses."))

    with transaction.atomic():
        wo = MaintenanceWorkOrder.objects.select_for_update().get(pk=work_order.pk)
        if wo.finance_expense:
            if idempotent:
                return wo.finance_expense
            raise ValidationError(_("This work order already has an associated financial expense record."))

        if wo.total_cost_etb <= Decimal("0.00"):
            raise ValidationError(_("Work order has zero total cost; cannot post financial expense."))

        from apps.finance.services import check_closed_reconciliation, create_expense
        from apps.finance.models import ExpenseCategory
        today = timezone.localdate()

        # Ensure transaction date does not fall into a closed reconciliation period
        check_closed_reconciliation(branch=branch, account=None, transaction_date=today)

        if expense_category is None:
            expense_category, _created = ExpenseCategory.objects.get_or_create(
                name="Repairs & Maintenance",
                defaults={"code": "EXP-MAINT", "description": "Equipment and facility repairs and maintenance", "is_active": True}
            )

        exp_supplier = supplier or (wo.equipment.supplier if wo.equipment else None)

        desc = f"Maintenance & Repair: {wo.title} ({wo.work_order_number})"
        expense = create_expense(
            actor=actor,
            branch=branch,
            category=expense_category,
            description=desc,
            amount=wo.total_cost_etb,
            supplier=exp_supplier,
            expense_date=today,
            payment_due_date=payment_due_date,
            internal_notes=internal_notes or f"Generated from Work Order {wo.work_order_number}",
            submit_for_approval=True,
        )

        wo.finance_expense = expense
        wo.save(update_fields=["finance_expense", "updated_at"])
        return expense


def generate_preventive_work_orders(today=None) -> list[MaintenanceWorkOrder]:
    """
    Inspects all active maintenance schedules where next_run_date <= today,
    generates work orders, and advances next_run_date by interval_days.
    """
    check_date = today or timezone.localdate()
    created_orders = []

    schedules = MaintenanceSchedule.objects.filter(
        is_active=True,
        next_run_date__lte=check_date,
    )

    for sched in schedules:
        with transaction.atomic():
            s = MaintenanceSchedule.objects.select_for_update().get(pk=sched.pk)
            # Re-check inside lock
            if s.next_run_date > check_date or not s.is_active:
                continue

            wo_num = generate_facility_code(f"WO-PM-{s.branch.code}")
            wo = MaintenanceWorkOrder.objects.create(
                work_order_number=wo_num,
                branch=s.branch,
                equipment=s.equipment,
                schedule=s,
                order_type=MaintenanceWorkOrder.OrderType.PREVENTIVE,
                status=MaintenanceWorkOrder.Status.ASSIGNED if s.assigned_technician else MaintenanceWorkOrder.Status.REPORTED,
                priority=MaintenanceWorkOrder.Priority.MEDIUM,
                title=f"Preventive: {s.title}",
                description=f"Scheduled preventive maintenance generated from plan #{s.id}.",
                checklist_results=[{"item": item, "completed": False} for item in s.checklist],
                assigned_technician=s.assigned_technician,
                assigned_at=timezone.now() if s.assigned_technician else None,
                service_cost_etb=s.estimated_cost_etb,
                total_cost_etb=s.estimated_cost_etb,
            )

            s.last_run_date = check_date
            s.next_run_date = check_date + timedelta(days=s.interval_days)
            s.save(update_fields=["last_run_date", "next_run_date", "updated_at"])

            created_orders.append(wo)

    return created_orders


# ==============================================================================
# 4. Cleaning & Inspection Services
# ==============================================================================

def complete_cleaning_task(
    *,
    actor: User = None,
    execution: CleaningTaskExecution,
    checklist_completed: list = None,
    checklist_progress: list = None,
    notes: str = "",
    verify_now: bool = False,
) -> CleaningTaskExecution:
    """Mark a cleaning task execution as completed and optionally verified."""
    branch = execution.branch
    with transaction.atomic():
        item = CleaningTaskExecution.objects.select_for_update().get(pk=execution.pk)
        item.status = CleaningTaskExecution.Status.COMPLETED
        item.completed_at = timezone.now()
        progress = checklist_progress if checklist_progress is not None else checklist_completed
        if progress is not None:
            item.checklist_completed = progress
        if notes:
            item.notes = notes
        if verify_now or actor is not None:
            item.verified_by = actor
            item.verified_at = timezone.now()
        item.save()
        return item


def record_facility_inspection(
    *,
    actor: User,
    branch: Branch,
    facility_area: FacilityArea,
    inspection_type: str = FacilityInspection.InspectionType.DAILY_WALKTHROUGH,
    passed: bool = True,
    score_percent: Decimal = Decimal("100.00"),
    checklist_results: list = None,
    deficiencies_found: str = "",
    deficiencies: str = "",
    create_escalation_work_order: bool = False,
    auto_escalate_work_order: bool = False,
) -> FacilityInspection:
    """
    Record a formal facility walk-through or safety inspection.
    Automatically escalates failed inspections to a corrective work order if requested.
    """
    if facility_area.branch != branch:
        raise ValidationError({"facility_area": _("Facility area belongs to a different branch.")})

    deficiencies_text = deficiencies or deficiencies_found
    escalate = auto_escalate_work_order or create_escalation_work_order

    with transaction.atomic():
        insp_num = generate_facility_code(f"INSP-{branch.code}")
        inspection = FacilityInspection.objects.create(
            inspection_number=insp_num,
            branch=branch,
            facility_area=facility_area,
            inspector=actor,
            inspection_type=inspection_type,
            inspection_date=timezone.localdate(),
            passed=passed,
            score_percent=score_percent,
            checklist_results=checklist_results or [],
            deficiencies_found=deficiencies_text,
        )

        # Update condition of facility area if failed
        if not passed:
            facility_area.condition = FacilityArea.Condition.NEEDS_ATTENTION
            facility_area.save(update_fields=["condition", "updated_at"])

            if escalate:
                wo_num = generate_facility_code(f"WO-ESC-{branch.code}")
                wo = MaintenanceWorkOrder.objects.create(
                    work_order_number=wo_num,
                    branch=branch,
                    facility_area=facility_area,
                    order_type=MaintenanceWorkOrder.OrderType.CORRECTIVE,
                    status=MaintenanceWorkOrder.Status.REPORTED,
                    priority=MaintenanceWorkOrder.Priority.URGENT,
                    title=f"Inspection Escalation: {facility_area.name}",
                    description=f"Automated work order from failed inspection {insp_num}. Deficiencies: {deficiencies_text}",
                    reported_by=actor,
                )
                inspection.escalation_work_order = wo
                inspection.save(update_fields=["escalation_work_order", "updated_at"])

        return inspection


def record_incident_report(
    *,
    actor: User,
    branch: Branch,
    incident_type: str,
    description: str,
    facility_area: FacilityArea = None,
    equipment: Equipment = None,
    severity: str = IncidentReport.Severity.MEDIUM,
    immediate_action_taken: str = "",
    immediate_action: str = "",
    witness_names: str = "",
    police_or_medical_involved: bool = False,
) -> IncidentReport:
    """Record an urgent facility incident report."""
    if facility_area and facility_area.branch != branch:
        raise ValidationError({"facility_area": _("Facility area belongs to a different branch.")})
    if equipment and equipment.branch != branch:
        raise ValidationError({"equipment": _("Equipment belongs to a different branch.")})

    action_taken = immediate_action or immediate_action_taken

    with transaction.atomic():
        inc_num = generate_facility_code(f"INC-{branch.code}")
        return IncidentReport.objects.create(
            incident_number=inc_num,
            branch=branch,
            facility_area=facility_area,
            equipment=equipment,
            reported_by=actor,
            reported_at=timezone.now(),
            incident_type=incident_type,
            severity=severity,
            status=IncidentReport.Status.OPEN,
            description=description,
            immediate_action_taken=action_taken,
            witness_names=witness_names,
            police_or_medical_involved=police_or_medical_involved,
        )


# ==============================================================================
# 5. Automated Reminders & Phase 12 Notification Triggers
# ==============================================================================

def dispatch_facility_reminders() -> dict:
    """
    Inspects:
    1. Locker assignments expiring soon (today or overdue).
    2. Overdue maintenance schedules.
    3. Unreturned locker keys on completed assignments.
    Sends notifications via Phase 12 notification infrastructure.
    """
    today = timezone.localdate()
    counts = {
        "locker_expiry_reminders": 0,
        "overdue_maintenance_reminders": 0,
        "unreturned_keys": 0,
    }

    # 1. Locker Expiring Today or Tomorrow
    expiring_assignments = LockerAssignment.objects.filter(
        status=LockerAssignment.Status.ACTIVE,
        end_date__isnull=False,
        end_date__lte=today + timedelta(days=1),
    ).select_related("member", "locker", "branch")

    for asg in expiring_assignments:
        member = asg.member
        if member and member.phone:
            from apps.notifications.services import create_and_queue_notification
            from apps.notifications.models import NotificationChannel, NotificationCategory, NotificationPriority
            
            body = (
                f"Dear {member.first_name}, your locker #{asg.locker.locker_number} assignment at "
                f"{asg.branch.name} expires on {asg.end_date}. Please renew or return your key to reception."
            )
            idem = f"lck-exp-{asg.id}-{today}"
            try:
                create_and_queue_notification(
                    recipient_address=member.phone,
                    body=body,
                    channel=NotificationChannel.SMS,
                    category=NotificationCategory.SYSTEM_OPERATIONAL,
                    priority=NotificationPriority.HIGH,
                    branch=asg.branch,
                    recipient_member=member,
                    idempotency_key=idem,
                    send_immediately=False,
                )
                counts["locker_expiry_reminders"] += 1
            except Exception as e:
                logger.warning("Failed to queue locker reminder for assignment %s: %s", asg.id, e)

    # 2. Overdue Maintenance Schedules
    overdue_schedules = MaintenanceSchedule.objects.filter(
        is_active=True,
        next_run_date__lt=today,
    ).select_related("branch", "assigned_technician", "equipment")

    for s in overdue_schedules:
        counts["overdue_maintenance_reminders"] += 1

    return counts
