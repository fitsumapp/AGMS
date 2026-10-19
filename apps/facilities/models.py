from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.branches.models import Branch


class LockerZone(models.Model):
    """Locker zone representing physical room or section within a branch."""
    class GenderRestriction(models.TextChoices):
        ALL = "ALL", _("All Members / Unisex")
        MALE = "MALE", _("Men's Only")
        FEMALE = "FEMALE", _("Women's Only")
        VIP = "VIP", _("VIP Executive Only")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="locker_zones",
        verbose_name=_("Branch"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Zone Name"))
    code = models.CharField(max_length=30, verbose_name=_("Zone Code"))
    gender_restriction = models.CharField(
        max_length=20,
        choices=GenderRestriction.choices,
        default=GenderRestriction.ALL,
        verbose_name=_("Access Restriction"),
    )
    description = models.TextField(blank=True, verbose_name=_("Description"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "name"]
        verbose_name = _("Locker Zone")
        verbose_name_plural = _("Locker Zones")
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="unique_locker_zone_per_branch"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code}) — {self.branch.name}"


class Locker(models.Model):
    """Individual locker unit within a zone."""
    class LockerType(models.TextChoices):
        STANDARD = "STANDARD", _("Standard Locker")
        LARGE = "LARGE", _("Large Locker")
        EXECUTIVE = "EXECUTIVE", _("Executive / Full-Length")
        DOUBLE_TIER = "DOUBLE_TIER", _("Double-Tier Locker")

    class LockerStatus(models.TextChoices):
        AVAILABLE = "AVAILABLE", _("Available")
        OCCUPIED = "OCCUPIED", _("Occupied")
        RESERVED = "RESERVED", _("Reserved")
        OUT_OF_SERVICE = "OUT_OF_SERVICE", _("Out of Service")
        MAINTENANCE = "MAINTENANCE", _("Under Maintenance")

    class LockType(models.TextChoices):
        KEY = "KEY", _("Physical Key")
        COMBINATION = "COMBINATION", _("Combination Dial")
        RFID_CARD = "RFID_CARD", _("RFID / Smart Card")
        DIGITAL_PAD = "DIGITAL_PAD", _("Digital Keypad Pin")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="lockers",
        verbose_name=_("Branch"),
    )
    zone = models.ForeignKey(
        LockerZone,
        on_delete=models.PROTECT,
        related_name="lockers",
        verbose_name=_("Locker Zone"),
    )
    locker_number = models.CharField(max_length=50, verbose_name=_("Locker Number"))
    locker_type = models.CharField(
        max_length=30,
        choices=LockerType.choices,
        default=LockerType.STANDARD,
        verbose_name=_("Locker Type"),
    )
    status = models.CharField(
        max_length=30,
        choices=LockerStatus.choices,
        default=LockerStatus.AVAILABLE,
        db_index=True,
        verbose_name=_("Status"),
    )
    lock_type = models.CharField(
        max_length=30,
        choices=LockType.choices,
        default=LockType.KEY,
        verbose_name=_("Lock Type"),
    )
    key_card_code = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Key / Card / Combination Code"),
    )
    daily_fee_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Daily Fee (ETB)"),
    )
    monthly_fee_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Monthly Fee (ETB)"),
    )
    deposit_amount_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Key Deposit (ETB)"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "locker_number"]
        verbose_name = _("Locker")
        verbose_name_plural = _("Lockers")
        constraints = [
            models.UniqueConstraint(fields=["branch", "locker_number"], name="unique_locker_number_per_branch"),
        ]

    def __str__(self):
        return f"Locker {self.locker_number} ({self.zone.name}) — {self.get_status_display()}"

    def clean(self):
        super().clean()
        if self.zone and self.branch and self.zone.branch != self.branch:
            raise ValidationError({"zone": _("Selected locker zone belongs to a different branch.")})


class LockerAssignment(models.Model):
    """Assignment record linking a locker to a registered member."""
    class AssignmentType(models.TextChoices):
        DAILY = "DAILY", _("Daily Temporary")
        MONTHLY = "MONTHLY", _("Monthly Rental")
        MEMBERSHIP_LINKED = "MEMBERSHIP_LINKED", _("Membership Package Linked")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        COMPLETED = "COMPLETED", _("Completed / Released")
        OVERDUE = "OVERDUE", _("Overdue")
        CANCELLED = "CANCELLED", _("Cancelled")

    assignment_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="locker_assignments",
        verbose_name=_("Branch"),
    )
    locker = models.ForeignKey(
        Locker,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("Locker"),
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="locker_assignments",
        verbose_name=_("Member"),
    )
    assignment_type = models.CharField(
        max_length=30,
        choices=AssignmentType.choices,
        default=AssignmentType.DAILY,
        verbose_name=_("Assignment Type"),
    )
    membership = models.ForeignKey(
        "memberships.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_assignments",
        verbose_name=_("Linked Membership"),
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        verbose_name=_("Status"),
    )
    start_date = models.DateField(default=timezone.now, verbose_name=_("Start Date"))
    end_date = models.DateField(null=True, blank=True, verbose_name=_("End Date / Expiry"))
    returned_date = models.DateField(null=True, blank=True, verbose_name=_("Returned Date"))
    key_issued = models.BooleanField(default=True, verbose_name=_("Key / Card Issued"))
    key_returned = models.BooleanField(default=False, verbose_name=_("Key / Card Returned"))
    key_card_code = models.CharField(max_length=100, blank=True, verbose_name=_("Issued Key/Card Code"))
    
    fee_charged_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Rental Fee (ETB)"),
    )
    deposit_charged_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Deposit Charged (ETB)"),
    )
    deposit_refunded_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Deposit Refunded (ETB)"),
    )
    invoice = models.ForeignKey(
        "billing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_assignments",
        verbose_name=_("Billing Invoice"),
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="locker_assignments_issued",
        verbose_name=_("Assigned By"),
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_assignments_released",
        verbose_name=_("Released By"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Locker Assignment")
        verbose_name_plural = _("Locker Assignments")
        constraints = [
            # Concurrency guarantee: At most ONE active assignment per physical locker
            models.UniqueConstraint(
                fields=["locker"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_assignment_per_locker",
            ),
        ]

    def __str__(self):
        return f"{self.assignment_number} — Locker {self.locker.locker_number} to {self.member.first_name} {self.member.last_name} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.locker and self.branch and self.locker.branch != self.branch:
            raise ValidationError({"locker": _("Selected locker belongs to a different branch.")})
        if self.member and self.branch and getattr(self.member, "home_branch", None) != self.branch:
            # Check if member belongs to branch
            if self.member.home_branch != self.branch:
                raise ValidationError({"member": _("Member's home branch does not match assignment branch.")})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date cannot precede assignment start date.")})


class LockerIncident(models.Model):
    """Incident record for lost keys, broken locks, or locker damages."""
    class IncidentType(models.TextChoices):
        LOST_KEY = "LOST_KEY", _("Lost Physical Key / Card")
        DAMAGED_LOCK = "DAMAGED_LOCK", _("Damaged Lock / Mechanism")
        DAMAGED_DOOR = "DAMAGED_DOOR", _("Damaged Door / Interior")
        UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS", _("Unauthorized Break-In / Tampering")
        OVERDUE_CLEARED = "OVERDUE_CLEARED", _("Overdue Belongings Cleared to Storage")

    incident_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="locker_incidents",
        verbose_name=_("Branch"),
    )
    locker = models.ForeignKey(
        Locker,
        on_delete=models.PROTECT,
        related_name="incidents",
        verbose_name=_("Locker"),
    )
    assignment = models.ForeignKey(
        LockerAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
        verbose_name=_("Locker Assignment"),
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_incidents",
        verbose_name=_("Member"),
    )
    incident_type = models.CharField(
        max_length=40,
        choices=IncidentType.choices,
        default=IncidentType.LOST_KEY,
        verbose_name=_("Incident Type"),
    )
    incident_date = models.DateField(default=timezone.now, verbose_name=_("Incident Date"))
    penalty_fee_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Penalty Fee (ETB)"),
    )
    deposit_forfeited_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Deposit Forfeited (ETB)"),
    )
    is_settled = models.BooleanField(default=False, verbose_name=_("Is Settled"))
    invoice = models.ForeignKey(
        "billing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Settlement Invoice"),
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name=_("Recorded By"),
    )
    notes = models.TextField(blank=True, verbose_name=_("Incident Details"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Locker Incident")
        verbose_name_plural = _("Locker Incidents")

    def __str__(self):
        return f"{self.incident_number} — {self.get_incident_type_display()} (Locker {self.locker.locker_number})"


class EquipmentCategory(models.Model):
    """Categorization for gym equipment and asset tracking."""
    name = models.CharField(max_length=100, unique=True, verbose_name=_("Category Name"))
    code = models.CharField(max_length=30, unique=True, verbose_name=_("Category Code"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    default_maintenance_interval_days = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Default Maintenance Interval (Days)"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Equipment Category")
        verbose_name_plural = _("Equipment Categories")

    def __str__(self):
        return f"{self.name} ({self.code})"


class FacilityArea(models.Model):
    """Physical areas within a gym branch (e.g. Cardio Hall, Shower, Studio)."""
    class AreaType(models.TextChoices):
        CHANGING_ROOM = "CHANGING_ROOM", _("Changing / Locker Room")
        SHOWER = "SHOWER", _("Shower & Restroom Zone")
        TOILET = "TOILET", _("Restrooms")
        TRAINING_HALL = "TRAINING_HALL", _("Main Gym / Weight Floor")
        STUDIO = "STUDIO", _("Group Fitness Studio")
        RECEPTION = "RECEPTION", _("Reception & Lobby")
        STORAGE = "STORAGE", _("Storage & Utility Room")
        POOL_SPA = "POOL_SPA", _("Swimming Pool & Sauna/Spa")
        OUTDOOR = "OUTDOOR", _("Outdoor Workout Zone")
        OTHER = "OTHER", _("Other Facility Area")

    class Condition(models.TextChoices):
        EXCELLENT = "EXCELLENT", _("Excellent")
        GOOD = "GOOD", _("Good")
        FAIR = "FAIR", _("Fair / Minor Wear")
        NEEDS_ATTENTION = "NEEDS_ATTENTION", _("Needs Attention / Repair")
        OUT_OF_SERVICE = "OUT_OF_SERVICE", _("Out of Service")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="facility_areas",
        verbose_name=_("Branch"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Area Name"))
    code = models.CharField(max_length=30, verbose_name=_("Area Code"))
    area_type = models.CharField(
        max_length=30,
        choices=AreaType.choices,
        default=AreaType.TRAINING_HALL,
        verbose_name=_("Area Type"),
    )
    floor = models.CharField(max_length=50, blank=True, verbose_name=_("Floor Level"))
    capacity = models.PositiveIntegerField(null=True, blank=True, verbose_name=_("Maximum Capacity"))
    condition = models.CharField(
        max_length=30,
        choices=Condition.choices,
        default=Condition.GOOD,
        verbose_name=_("Current Condition"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "name"]
        verbose_name = _("Facility Area")
        verbose_name_plural = _("Facility Areas")
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="unique_facility_area_per_branch"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code}) — {self.branch.name}"


class Equipment(models.Model):
    """Gym equipment and high-value capital assets."""
    class OperationalStatus(models.TextChoices):
        OPERATIONAL = "OPERATIONAL", _("Operational")
        UNDER_MAINTENANCE = "UNDER_MAINTENANCE", _("Under Maintenance")
        OUT_OF_SERVICE = "OUT_OF_SERVICE", _("Out of Service")
        RETIRED = "RETIRED", _("Retired")
        DISPOSED = "DISPOSED", _("Disposed")

    class EquipmentCondition(models.TextChoices):
        EXCELLENT = "EXCELLENT", _("Excellent")
        GOOD = "GOOD", _("Good")
        FAIR = "FAIR", _("Fair")
        POOR = "POOR", _("Poor")
        CRITICAL = "CRITICAL", _("Critical / Unsafe")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="equipment",
        verbose_name=_("Branch"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="equipment",
        verbose_name=_("Location in Facility"),
    )
    category = models.ForeignKey(
        EquipmentCategory,
        on_delete=models.PROTECT,
        related_name="equipment",
        verbose_name=_("Category"),
    )
    asset_code = models.CharField(max_length=60, unique=True, db_index=True, verbose_name=_("Asset Code"))
    name = models.CharField(max_length=150, verbose_name=_("Equipment Name"))
    manufacturer = models.CharField(max_length=100, blank=True, verbose_name=_("Manufacturer"))
    model_number = models.CharField(max_length=100, blank=True, verbose_name=_("Model Number"))
    serial_number = models.CharField(max_length=100, blank=True, verbose_name=_("Serial Number"))
    
    purchase_date = models.DateField(null=True, blank=True, verbose_name=_("Purchase Date"))
    purchase_cost_etb = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Purchase Cost (ETB)"),
    )
    supplier = models.ForeignKey(
        "finance.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="equipment_supplied",
        verbose_name=_("Supplier"),
    )
    warranty_expiry_date = models.DateField(null=True, blank=True, verbose_name=_("Warranty Expiry Date"))
    
    status = models.CharField(
        max_length=30,
        choices=OperationalStatus.choices,
        default=OperationalStatus.OPERATIONAL,
        db_index=True,
        verbose_name=_("Operational Status"),
    )
    condition = models.CharField(
        max_length=30,
        choices=EquipmentCondition.choices,
        default=EquipmentCondition.GOOD,
        verbose_name=_("Condition"),
    )
    assigned_staff = models.ForeignKey(
        "staff.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="equipment_managed",
        verbose_name=_("Assigned Technician / Staff"),
    )
    last_maintenance_date = models.DateField(null=True, blank=True, verbose_name=_("Last Maintenance Date"))
    next_maintenance_due = models.DateField(null=True, blank=True, db_index=True, verbose_name=_("Next Maintenance Due"))
    total_downtime_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Total Downtime (Hours)"),
    )
    image = models.ImageField(upload_to="equipment/images/", null=True, blank=True, verbose_name=_("Asset Photo"))
    manual_document = models.FileField(upload_to="equipment/manuals/", null=True, blank=True, verbose_name=_("User Manual / Document"))
    notes = models.TextField(blank=True, verbose_name=_("Notes"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "name"]
        verbose_name = _("Equipment Asset")
        verbose_name_plural = _("Equipment Assets")

    def __str__(self):
        return f"{self.name} [{self.asset_code}] — {self.branch.name} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.facility_area and self.branch and self.facility_area.branch != self.branch:
            raise ValidationError({"facility_area": _("Selected facility area belongs to a different branch.")})


class MaintenanceSchedule(models.Model):
    """Recurring preventive maintenance plan."""
    class Frequency(models.TextChoices):
        DAILY = "DAILY", _("Daily")
        WEEKLY = "WEEKLY", _("Weekly")
        BIWEEKLY = "BIWEEKLY", _("Bi-Weekly (Every 2 Weeks)")
        MONTHLY = "MONTHLY", _("Monthly")
        QUARTERLY = "QUARTERLY", _("Quarterly (Every 3 Months)")
        BIANNUAL = "BIANNUAL", _("Semi-Annual (Every 6 Months)")
        ANNUAL = "ANNUAL", _("Annual")
        CUSTOM = "CUSTOM", _("Custom Interval")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="maintenance_schedules",
        verbose_name=_("Branch"),
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="schedules",
        verbose_name=_("Specific Equipment"),
    )
    category = models.ForeignKey(
        EquipmentCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedules",
        verbose_name=_("Equipment Category"),
    )
    title = models.CharField(max_length=150, verbose_name=_("Schedule Title"))
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
        verbose_name=_("Frequency"),
    )
    interval_days = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Interval in Days"),
    )
    checklist = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of inspection items and check points, e.g. ['Check belt tension', 'Inspect motor brushes', 'Lubricate deck']"),
        verbose_name=_("Maintenance Checklist"),
    )
    assigned_technician = models.ForeignKey(
        "staff.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_schedules",
        verbose_name=_("Assigned Technician"),
    )
    estimated_cost_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Estimated Cost (ETB)"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    last_run_date = models.DateField(null=True, blank=True, verbose_name=_("Last Run Date"))
    next_run_date = models.DateField(db_index=True, verbose_name=_("Next Due Date"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "next_run_date"]
        verbose_name = _("Maintenance Schedule")
        verbose_name_plural = _("Maintenance Schedules")

    def __str__(self):
        target = self.equipment.name if self.equipment else (self.category.name if self.category else "General")
        return f"{self.title} ({target}) — Due: {self.next_run_date}"


class FaultReport(models.Model):
    """Issue reported by staff, trainer, or reception."""
    class Priority(models.TextChoices):
        LOW = "LOW", _("Low Priority")
        MEDIUM = "MEDIUM", _("Medium Priority")
        HIGH = "HIGH", _("High Priority")
        URGENT = "URGENT", _("Urgent / Safety Hazard")

    class Status(models.TextChoices):
        REPORTED = "REPORTED", _("Reported")
        ASSIGNED = "ASSIGNED", _("Assigned")
        IN_PROGRESS = "IN_PROGRESS", _("In Progress")
        RESOLVED = "RESOLVED", _("Resolved")
        CLOSED = "CLOSED", _("Closed")
        CANCELLED = "CANCELLED", _("Cancelled")

    report_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="fault_reports",
        verbose_name=_("Branch"),
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="fault_reports",
        verbose_name=_("Affected Equipment"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fault_reports",
        verbose_name=_("Affected Area"),
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fault_reports_filed",
        verbose_name=_("Reported By"),
    )
    reported_at = models.DateTimeField(default=timezone.now, verbose_name=_("Reported At"))
    title = models.CharField(max_length=150, verbose_name=_("Fault Title"))
    description = models.TextField(verbose_name=_("Fault Description"))
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
        verbose_name=_("Priority"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REPORTED,
        db_index=True,
        verbose_name=_("Status"),
    )
    resolution_notes = models.TextField(blank=True, verbose_name=_("Resolution Notes"))
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Resolved At"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Fault Report")
        verbose_name_plural = _("Fault Reports")

    def __str__(self):
        return f"{self.report_number}: {self.title} ({self.get_status_display()})"


class MaintenanceWorkOrder(models.Model):
    """Work order tracking execution of preventive maintenance or fault repair."""
    class OrderType(models.TextChoices):
        PREVENTIVE = "PREVENTIVE", _("Preventive Maintenance")
        CORRECTIVE = "CORRECTIVE", _("Corrective / Fault Repair")
        INSPECTION = "INSPECTION", _("Safety & Condition Inspection")
        EMERGENCY = "EMERGENCY", _("Emergency Breakdown")

    class Status(models.TextChoices):
        REPORTED = "REPORTED", _("Reported / Scheduled")
        ASSIGNED = "ASSIGNED", _("Assigned to Technician")
        IN_PROGRESS = "IN_PROGRESS", _("In Progress")
        RESOLVED = "RESOLVED", _("Resolved / Completed")
        CLOSED = "CLOSED", _("Closed & Verified")
        CANCELLED = "CANCELLED", _("Cancelled")

    class Priority(models.TextChoices):
        LOW = "LOW", _("Low")
        MEDIUM = "MEDIUM", _("Medium")
        HIGH = "HIGH", _("High")
        URGENT = "URGENT", _("Urgent")
        CRITICAL = "CRITICAL", _("Critical")

    work_order_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name=_("Branch"),
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name=_("Equipment Asset"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name=_("Facility Area"),
    )
    schedule = models.ForeignKey(
        MaintenanceSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name=_("Source Schedule"),
    )
    fault_report = models.ForeignKey(
        FaultReport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        verbose_name=_("Source Fault Report"),
    )
    order_type = models.CharField(
        max_length=30,
        choices=OrderType.choices,
        default=OrderType.PREVENTIVE,
        verbose_name=_("Work Order Type"),
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.REPORTED,
        db_index=True,
        verbose_name=_("Status"),
    )
    priority = models.CharField(
        max_length=30,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        verbose_name=_("Priority"),
    )
    title = models.CharField(max_length=150, verbose_name=_("Title"))
    description = models.TextField(verbose_name=_("Work Description"))
    checklist_results = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Detailed checklist task items and boolean completion status."),
        verbose_name=_("Checklist Results"),
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders_reported",
        verbose_name=_("Reported By"),
    )
    reported_at = models.DateTimeField(default=timezone.now, verbose_name=_("Reported Date/Time"))
    assigned_technician = models.ForeignKey(
        "staff.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_orders",
        verbose_name=_("Assigned Technician"),
    )
    assigned_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Assigned Date/Time"))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Started Date/Time"))
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Resolved Date/Time"))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Closed Date/Time"))
    
    # Financial maintenance costs (ETB)
    service_cost_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Service / Labor Cost (ETB)"),
    )
    parts_cost_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Spare Parts Cost (ETB)"),
    )
    total_cost_etb = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Total Maintenance Cost (ETB)"),
    )
    downtime_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Equipment Downtime (Hours)"),
    )
    spare_parts_used = models.TextField(blank=True, verbose_name=_("Spare Parts & Materials Used"))
    resolution_notes = models.TextField(blank=True, verbose_name=_("Resolution Notes & Actions Taken"))
    
    # Authoritative Phase 10 Finance integration
    finance_expense = models.ForeignKey(
        "finance.Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_work_orders",
        verbose_name=_("Finance Expense Record"),
    )
    is_reopened = models.BooleanField(default=False, verbose_name=_("Is Reopened"))
    reopened_reason = models.TextField(blank=True, verbose_name=_("Reopening Reason"))
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders_reopened",
        verbose_name=_("Reopened By"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Maintenance Work Order")
        verbose_name_plural = _("Maintenance Work Orders")

    def __str__(self):
        return f"{self.work_order_number}: {self.title} [{self.get_status_display()}]"

    def clean(self):
        super().clean()
        if self.equipment and self.branch and self.equipment.branch != self.branch:
            raise ValidationError({"equipment": _("Selected equipment belongs to a different branch.")})
        if self.facility_area and self.branch and self.facility_area.branch != self.branch:
            raise ValidationError({"facility_area": _("Selected facility area belongs to a different branch.")})


class CleaningTaskSchedule(models.Model):
    """Recurring cleaning routine template and schedule."""
    class Frequency(models.TextChoices):
        HOURLY = "HOURLY", _("Hourly")
        TWICE_DAILY = "TWICE_DAILY", _("Twice Daily")
        DAILY = "DAILY", _("Daily")
        WEEKLY = "WEEKLY", _("Weekly")
        BIWEEKLY = "BIWEEKLY", _("Bi-Weekly")
        MONTHLY = "MONTHLY", _("Monthly")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cleaning_schedules",
        verbose_name=_("Branch"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.CASCADE,
        related_name="cleaning_schedules",
        verbose_name=_("Facility Area"),
    )
    task_name = models.CharField(max_length=150, verbose_name=_("Task Name"))
    frequency = models.CharField(
        max_length=30,
        choices=Frequency.choices,
        default=Frequency.DAILY,
        verbose_name=_("Frequency"),
    )
    checklist = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of cleaning tasks, e.g. ['Mop floor', 'Disinfect bench pads', 'Restock paper towels', 'Empty trash']"),
        verbose_name=_("Cleaning Checklist"),
    )
    assigned_staff = models.ForeignKey(
        "staff.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_cleaning_schedules",
        verbose_name=_("Assigned Staff Member"),
    )
    estimated_duration_minutes = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Estimated Duration (Minutes)"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Is Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "facility_area", "task_name"]
        verbose_name = _("Cleaning Task Template / Schedule")
        verbose_name_plural = _("Cleaning Task Schedules")

    def __str__(self):
        return f"{self.task_name} ({self.facility_area.name}) — {self.get_frequency_display()}"


class CleaningTaskExecution(models.Model):
    """Execution instance of a scheduled cleaning task."""
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        IN_PROGRESS = "IN_PROGRESS", _("In Progress")
        COMPLETED = "COMPLETED", _("Completed")
        MISSED = "MISSED", _("Missed / Overdue")
        CANCELLED = "CANCELLED", _("Cancelled")

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cleaning_executions",
        verbose_name=_("Branch"),
    )
    schedule = models.ForeignKey(
        CleaningTaskSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executions",
        verbose_name=_("Source Schedule"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.PROTECT,
        related_name="cleaning_executions",
        verbose_name=_("Facility Area"),
    )
    task_name = models.CharField(max_length=150, verbose_name=_("Task Name"))
    assigned_staff = models.ForeignKey(
        "staff.StaffProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cleaning_tasks",
        verbose_name=_("Assigned Staff"),
    )
    scheduled_date = models.DateField(default=timezone.now, verbose_name=_("Scheduled Date"))
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("Status"),
    )
    checklist_completed = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Checklist Progress"),
    )
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Completed At"))
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_cleanings",
        verbose_name=_("Verified By"),
    )
    verified_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Verified At"))
    notes = models.TextField(blank=True, verbose_name=_("Notes / Observations"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_date", "-created_at"]
        verbose_name = _("Cleaning Task Execution")
        verbose_name_plural = _("Cleaning Tasks")

    def __str__(self):
        return f"{self.task_name} at {self.facility_area.name} ({self.scheduled_date}) — {self.get_status_display()}"


class FacilityInspection(models.Model):
    """Formal walkthrough and compliance inspection."""
    class InspectionType(models.TextChoices):
        DAILY_WALKTHROUGH = "DAILY_WALKTHROUGH", _("Daily Facility Walkthrough")
        SAFETY_AUDIT = "SAFETY_AUDIT", _("Safety & Hazard Audit")
        HYGIENE_INSPECTION = "HYGIENE_INSPECTION", _("Hygiene & Sanitation Inspection")
        FIRE_SAFETY = "FIRE_SAFETY", _("Fire & Emergency Equipment")
        HVAC_EQUIPMENT = "HVAC_EQUIPMENT", _("Ventilation & HVAC Check")

    inspection_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="facility_inspections",
        verbose_name=_("Branch"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.PROTECT,
        related_name="inspections",
        verbose_name=_("Facility Area"),
    )
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inspections_conducted",
        verbose_name=_("Inspector"),
    )
    inspection_type = models.CharField(
        max_length=30,
        choices=InspectionType.choices,
        default=InspectionType.DAILY_WALKTHROUGH,
        verbose_name=_("Inspection Type"),
    )
    inspection_date = models.DateField(default=timezone.now, verbose_name=_("Inspection Date"))
    passed = models.BooleanField(default=True, verbose_name=_("Passed Inspection"))
    score_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        verbose_name=_("Score Percentage (%)"),
    )
    checklist_results = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Checklist Findings"),
    )
    deficiencies_found = models.TextField(blank=True, verbose_name=_("Deficiencies / Hazards Found"))
    escalation_work_order = models.ForeignKey(
        MaintenanceWorkOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inspection_escalations",
        verbose_name=_("Escalated Work Order"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inspection_date", "-created_at"]
        verbose_name = _("Facility Inspection")
        verbose_name_plural = _("Facility Inspections")

    def __str__(self):
        status_text = "PASSED" if self.passed else "FAILED"
        return f"{self.inspection_number}: {self.facility_area.name} ({status_text}, {self.score_percent}%)"


class IncidentReport(models.Model):
    """Facility safety, property damage, or injury incidents."""
    class IncidentType(models.TextChoices):
        SAFETY_HAZARD = "SAFETY_HAZARD", _("Safety Hazard / Slip & Fall")
        INJURY = "INJURY", _("Member / Staff Injury")
        PROPERTY_DAMAGE = "PROPERTY_DAMAGE", _("Facility / Property Damage")
        EQUIPMENT_FAILURE = "EQUIPMENT_FAILURE", _("Equipment Catastrophic Failure")
        THEFT = "THEFT", _("Theft / Missing Property")
        WATER_LEAK = "WATER_LEAK", _("Water Leak / Flooding")
        POWER_OUTAGE = "POWER_OUTAGE", _("Electrical / Power Interruption")
        BIOHAZARD = "BIOHAZARD", _("Biohazard / Contamination")

    class Severity(models.TextChoices):
        LOW = "LOW", _("Low (Minor issue)")
        MEDIUM = "MEDIUM", _("Medium (Moderate attention)")
        HIGH = "HIGH", _("High (Serious concern)")
        CRITICAL = "CRITICAL", _("Critical (Emergency / Hospitalization)")

    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        INVESTIGATING = "INVESTIGATING", _("Under Investigation")
        ACTION_TAKEN = "ACTION_TAKEN", _("Corrective Action Taken")
        CLOSED = "CLOSED", _("Closed")

    incident_number = models.CharField(max_length=60, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="facility_incidents",
        verbose_name=_("Branch"),
    )
    facility_area = models.ForeignKey(
        FacilityArea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
        verbose_name=_("Facility Area"),
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facility_incidents",
        verbose_name=_("Involved Equipment"),
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="facility_incidents_reported",
        verbose_name=_("Reported By"),
    )
    reported_at = models.DateTimeField(default=timezone.now, verbose_name=_("Date & Time of Incident"))
    incident_type = models.CharField(
        max_length=30,
        choices=IncidentType.choices,
        default=IncidentType.SAFETY_HAZARD,
        verbose_name=_("Incident Type"),
    )
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        verbose_name=_("Severity Level"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name=_("Status"),
    )
    description = models.TextField(verbose_name=_("Detailed Description"))
    immediate_action_taken = models.TextField(blank=True, verbose_name=_("Immediate Action Taken"))
    resolution_summary = models.TextField(blank=True, verbose_name=_("Resolution Summary"))
    witness_names = models.CharField(max_length=255, blank=True, verbose_name=_("Witness Names / Contacts"))
    police_or_medical_involved = models.BooleanField(
        default=False,
        verbose_name=_("Police / Medical Emergency Services Involved"),
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facility_incidents_resolved",
        verbose_name=_("Resolved By"),
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Resolved Date/Time"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_at"]
        verbose_name = _("Incident Report")
        verbose_name_plural = _("Incident Reports")

    def __str__(self):
        return f"{self.incident_number}: {self.get_incident_type_display()} ({self.get_severity_display()})"
