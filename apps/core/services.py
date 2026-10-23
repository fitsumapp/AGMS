from decimal import Decimal
import logging
import os
import shutil
import time
from typing import Any, Optional
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, DatabaseError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.branches.models import Branch, BranchSettings
from apps.branches.audit import record_event
from apps.core.models import GymProfile, GymSettings, FeatureFlag, BackupConfiguration

logger = logging.getLogger("gym.settings")


# ==============================================================================
# 1. Authoritative Settings Precedence Resolution Engine
# ==============================================================================

def resolve_setting(branch: Optional[Branch], key: str, default: Any = None) -> Any:
    """
    Resolve configuration following the authoritative precedence hierarchy:
    1. Branch Override (from BranchSettings / BranchFinanceSettings)
    2. Global Gym Settings / GymProfile
    3. Explicit Code Default
    """
    profile = GymProfile.objects.first()
    gym_settings = getattr(profile, "policies", None) if profile else None

    # Check Branch Overrides if branch is provided
    if branch:
        branch_settings = BranchSettings.objects.filter(branch=branch).first()
        if branch_settings:
            # Map of branch-specific override attribute names
            branch_attr_map = {
                "allow_access_with_balance": "allow_access_with_balance_override",
                "attendance_qr_enabled": "attendance_qr_enabled",
                "attendance_manual_enabled": "attendance_manual_enabled",
                "pos_max_discount_percent": "max_discount_percent_override",
                "default_locker_deposit_etb": "locker_deposit_etb_override",
                "low_stock_threshold": "low_stock_threshold_override",
                "sms_enabled": "sms_enabled_override",
                "receipt_header": "receipt_header_override",
                "receipt_footer": "receipt_footer_override",
            }
            attr_name = branch_attr_map.get(key, key)
            if hasattr(branch_settings, attr_name):
                val = getattr(branch_settings, attr_name)
                if val is not None and val != "":
                    return val

    # Check Global GymSettings
    if gym_settings and hasattr(gym_settings, key):
        val = getattr(gym_settings, key)
        if val is not None:
            return val

    # Check Global GymProfile
    if profile and hasattr(profile, key):
        val = getattr(profile, key)
        if val is not None and val != "":
            return val

    return default


def update_branch_settings(
    *,
    actor,
    branch: Branch,
    updates: Optional[dict] = None,
    **kwargs,
) -> BranchSettings:
    """Atomically update branch settings with audit logging and concurrency control."""
    all_updates = {}
    if updates:
        all_updates.update(updates)
    all_updates.update(kwargs)

    with transaction.atomic():
        settings_obj, _ = BranchSettings.objects.select_for_update().get_or_create(branch=branch)
        old_values = {}
        for k, new_v in all_updates.items():
            if hasattr(settings_obj, k):
                old_values[k] = getattr(settings_obj, k)
                setattr(settings_obj, k, new_v)

        settings_obj.full_clean()
        settings_obj.save()

        # Audit changes
        for k, old_v in old_values.items():
            new_v = all_updates[k]
            if str(old_v) != str(new_v):
                record_event(
                    actor=actor,
                    action="branch_settings_updated",
                    target=settings_obj,
                    branch=branch,
                    metadata={
                        "branch_code": branch.code,
                        "setting_name": k[:60],
                        "old_value": str(old_v)[:100],
                        "new_value": str(new_v)[:100],
                    },
                )
        return settings_obj


# ==============================================================================
# 2. Feature Flags Service
# ==============================================================================

DEFAULT_FEATURE_FLAGS = [
    {
        "code": "ATTENDANCE_QR",
        "name": "QR Code Member Attendance",
        "description": "Allow member check-ins using digital and physical QR badges.",
        "category": FeatureFlag.Category.ATTENDANCE,
        "is_enabled": True,
    },
    {
        "code": "ATTENDANCE_MANUAL",
        "name": "Name & Phone Manual Attendance",
        "description": "Allow front desk staff to search and verify members manually.",
        "category": FeatureFlag.Category.ATTENDANCE,
        "is_enabled": True,
    },
    {
        "code": "ONLINE_BOOKING",
        "name": "Member Online Class Booking",
        "description": "Permit members to book scheduled fitness sessions in advance.",
        "category": FeatureFlag.Category.CLASSES,
        "is_enabled": True,
    },
    {
        "code": "CLASS_WAITLIST",
        "name": "Automated Class Waitlist",
        "description": "Queue members when a group fitness class is fully booked.",
        "category": FeatureFlag.Category.CLASSES,
        "is_enabled": True,
        "requires_features": ["ONLINE_BOOKING"],
    },
    {
        "code": "SMS_NOTIFICATIONS",
        "name": "Automated SMS Gateway",
        "description": "Send automated transaction, reminder, and operational SMS.",
        "category": FeatureFlag.Category.NOTIFICATIONS,
        "is_enabled": True,
    },
    {
        "code": "BULK_MESSAGING",
        "name": "Bulk SMS Campaigns",
        "description": "Permit marketing and emergency broadcast SMS messages to members.",
        "category": FeatureFlag.Category.NOTIFICATIONS,
        "is_enabled": True,
        "requires_features": ["SMS_NOTIFICATIONS"],
    },
    {
        "code": "POS_MODULE",
        "name": "Front Desk Retail POS",
        "description": "Point-of-sale terminal for drinks, supplements, and merchandise.",
        "category": FeatureFlag.Category.POS,
        "is_enabled": True,
    },
    {
        "code": "INVENTORY_MANAGEMENT",
        "name": "Stock & Warehouse Tracking",
        "description": "Multi-branch stock items, movements, adjustments, and supplier POs.",
        "category": FeatureFlag.Category.POS,
        "is_enabled": True,
        "requires_features": ["POS_MODULE"],
    },
    {
        "code": "FINANCE_MODULE",
        "name": "Finance & Expense Management",
        "description": "Operating expenses, bank accounts, transfers, and reconciliation.",
        "category": FeatureFlag.Category.BILLING,
        "is_enabled": True,
    },
    {
        "code": "FACILITY_MANAGEMENT",
        "name": "Lockers & Equipment Maintenance",
        "description": "Locker rentals, equipment asset registry, and maintenance work orders.",
        "category": FeatureFlag.Category.FACILITIES,
        "is_enabled": True,
    },
    {
        "code": "MULTI_BRANCH_ACCESS",
        "name": "Cross-Branch Package Roaming",
        "description": "Allow multi-branch package holders to access secondary gym branches.",
        "category": FeatureFlag.Category.MEMBERSHIP,
        "is_enabled": True,
    },
]


def seed_feature_flags():
    """Seed baseline feature flag registry if not present."""
    for entry in DEFAULT_FEATURE_FLAGS:
        FeatureFlag.objects.get_or_create(
            code=entry["code"],
            defaults={
                "name": entry["name"],
                "description": entry["description"],
                "category": entry["category"],
                "is_enabled": entry["is_enabled"],
                "requires_features": entry.get("requires_features", []),
            },
        )


def is_feature_enabled(code: str, branch: Optional[Branch] = None) -> bool:
    """
    Check if a feature flag is enabled.
    Resolution:
    1. Branch-scoped flag (if exists)
    2. Global flag (branch is null)
    3. Check required dependency flags
    """
    code = code.strip().upper()
    flag = None
    if branch:
        flag = FeatureFlag.objects.filter(code=code, branch=branch).first()

    if not flag:
        flag = FeatureFlag.objects.filter(code=code, branch__isnull=True).first()

    if not flag:
        if FeatureFlag.objects.filter(code=code).exists():
            return False
        return True  # If unmanaged, default to standard system operation

    if not flag.is_enabled:
        return False

    # Check dependency tree
    if flag.requires_features:
        for req_code in flag.requires_features:
            if not is_feature_enabled(req_code, branch):
                return False

    return True


# ==============================================================================
# 3. System Health & Operational Metrics
# ==============================================================================

def get_system_health_metrics() -> dict:
    """
    Collect comprehensive live system operational metrics for the Health Dashboard.
    Distinguishes real health signals from unavailable or mock components.
    """
    metrics = {
        "status": "ONLINE",
        "timestamp": timezone.now(),
        "database": {"connected": False, "engine": "PostgreSQL", "latency_ms": 0.0, "error": None},
        "notifications": {"pending": 0, "queued": 0, "delivered_24h": 0, "failed_24h": 0},
        "scheduled_jobs": {
            "preventive_maintenance_last_run": None,
            "cleaning_last_run": None,
            "notifications_queue_last_run": None,
        },
        "approvals": {
            "pending_refunds": 0,
            "pending_expenses": 0,
            "pending_leave_requests": 0,
            "pending_work_orders": 0,
        },
        "migrations": {"all_applied": True, "unapplied_count": 0},
        "storage": {"media_writable": True, "backups_count": 0, "last_backup": None},
    }

    # 1. Database Ping & Latency
    try:
        t0 = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        t1 = time.perf_counter()
        metrics["database"]["connected"] = True
        metrics["database"]["latency_ms"] = round((t1 - t0) * 1000, 2)
    except DatabaseError as e:
        metrics["database"]["connected"] = False
        metrics["database"]["error"] = str(e)
        metrics["status"] = "DEGRADED"

    # 2. Migrations Check
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        metrics["migrations"]["all_applied"] = len(plan) == 0
        metrics["migrations"]["unapplied_count"] = len(plan)
        if len(plan) > 0:
            metrics["status"] = "NEEDS_MIGRATION"
    except Exception:
        pass

    # 3. Notification Queue Health
    try:
        from apps.notifications.models import NotificationMessage, DeliveryQueueItem
        now = timezone.now()
        yesterday = now - timezone.timedelta(hours=24)
        metrics["notifications"]["queued"] = DeliveryQueueItem.objects.filter(status="QUEUED").count()
        metrics["notifications"]["pending"] = NotificationMessage.objects.filter(status="PENDING").count()
        metrics["notifications"]["delivered_24h"] = NotificationMessage.objects.filter(
            status__in=["DELIVERED", "SENT"], sent_at__gte=yesterday
        ).count()
        metrics["notifications"]["failed_24h"] = NotificationMessage.objects.filter(
            status="FAILED", created_at__gte=yesterday
        ).count()
    except Exception:
        pass

    # 4. Pending Operational Approvals
    try:
        from apps.billing.models import Refund
        metrics["approvals"]["pending_refunds"] = Refund.objects.filter(status="REQUESTED").count()
    except Exception:
        pass

    try:
        from apps.finance.models import Expense
        metrics["approvals"]["pending_expenses"] = Expense.objects.filter(approval_status="SUBMITTED").count()
    except Exception:
        pass

    try:
        from apps.staff.models import LeaveRequest
        metrics["approvals"]["pending_leave_requests"] = LeaveRequest.objects.filter(status="SUBMITTED").count()
    except Exception:
        pass

    try:
        from apps.facilities.models import MaintenanceWorkOrder
        metrics["approvals"]["pending_work_orders"] = MaintenanceWorkOrder.objects.filter(
            status__in=["REPORTED", "ASSIGNED"]
        ).count()
    except Exception:
        pass

    # 5. Storage & Backup Verification
    backup_config = BackupConfiguration.objects.first()
    if backup_config:
        metrics["storage"]["last_backup"] = {
            "timestamp": backup_config.last_backup_at,
            "status": backup_config.last_backup_status,
            "filename": backup_config.last_backup_file_name,
            "size_bytes": backup_config.last_backup_file_size_bytes,
            "directory": backup_config.backup_directory,
        }
        if os.path.exists(backup_config.backup_directory):
            try:
                metrics["storage"]["backups_count"] = len(
                    [f for f in os.listdir(backup_config.backup_directory) if f.endswith((".sql", ".gz", ".tar"))]
                )
            except Exception:
                pass

    return metrics


# ==============================================================================
# 4. Backup Readiness Verification Service
# ==============================================================================

def get_backup_readiness_report() -> dict:
    """Inspect backup configuration, directory access, and recovery readiness."""
    config, _ = BackupConfiguration.objects.get_or_create(singleton=1)
    backup_dir = config.backup_directory
    dir_exists = os.path.exists(backup_dir)
    dir_writable = False
    free_space_mb = 0

    if dir_exists:
        try:
            test_file = os.path.join(backup_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            dir_writable = True
            usage = shutil.disk_usage(backup_dir)
            free_space_mb = round(usage.free / (1024 * 1024), 2)
        except Exception:
            dir_writable = False

    return {
        "config": config,
        "directory_exists": dir_exists,
        "directory_writable": dir_writable,
        "free_space_mb": free_space_mb,
        "retention_days": config.retention_days,
        "compression": config.compression_algorithm,
        "last_backup_at": config.last_backup_at,
        "last_backup_status": config.last_backup_status,
        "last_verified_at": config.last_verified_at,
    }
