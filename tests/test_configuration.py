import os
from pathlib import Path
import subprocess
import sys
import pytest
from django.contrib import admin
from unfold.admin import ModelAdmin

def test_all_admins_use_unfold():
    assert {model._meta.label for model in admin.site._registry} == {
        "accounts.User", "accounts.StaffAccount", "auth.Group", "core.GymProfile",
        "core.GymSettings", "core.GymBranding", "core.GymPolicy", "branches.Branch",
        "branches.UserBranchAccess", "branches.BranchSettings", "branches.AuditEvent",
        "members.Member",
        "memberships.MembershipPackage", "memberships.Membership",
        "memberships.MembershipFreeze", "memberships.MembershipRenewal",
        "memberships.MembershipEvent",
        "billing.Invoice", "billing.Payment", "billing.PaymentMethod",
        "billing.Refund", "billing.Receipt", "billing.FinancialAuditEvent",
        "attendance.Attendance", "attendance.MemberCredential", "attendance.AccessDevice",
        "attendance.AttendanceOverride", "attendance.AccessAttempt",
        "analytics.ReportExportLog", "analytics.ReportPreset",
        "staff.StaffProfile", "staff.TrainerProfile", "staff.TrainerMemberAssignment",
        "staff.TrainingSession", "staff.ShiftTemplate", "staff.StaffShift", "staff.LeaveRequest",
        "classes.ClassType", "classes.ClassScheduleTemplate", "classes.ClassSession", "classes.ClassBooking",
    }
    assert all(isinstance(value, ModelAdmin) for value in admin.site._registry.values())

@pytest.mark.parametrize("missing", ["DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"])
def test_startup_fails_closed(missing):
    env = os.environ.copy()
    env[missing] = ""
    result = subprocess.run([sys.executable, "manage.py", "check"], cwd=Path(__file__).resolve().parent.parent,
        env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert f"Required environment variable {missing} is missing" in result.stderr

@pytest.mark.parametrize("override", [{"DJANGO_ALLOWED_HOSTS": "*"}, {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://gym.example.test"}])
def test_production_rejects_unsafe_configuration(override):
    env = os.environ.copy()
    env.update(DJANGO_ALLOWED_HOSTS="gym.example.test", DJANGO_CSRF_TRUSTED_ORIGINS="https://gym.example.test")
    env.update(override)
    result = subprocess.run([sys.executable, "manage.py", "check", "--settings=config.settings.production"],
        cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
