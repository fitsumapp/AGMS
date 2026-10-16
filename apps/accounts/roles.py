from django.contrib.auth.models import Group, Permission

from apps.branches.models import BUSINESS_ROLES


ROLE_PERMISSIONS = {
    "Gym Owner": {
        "branches": {"add_branch", "change_branch", "view_branch", "view_branchsettings",
            "change_branchsettings", "add_userbranchaccess", "change_userbranchaccess", "view_userbranchaccess"},
        "accounts": {"add_staffaccount", "change_staffaccount", "view_staffaccount"},
        "core": {"view_gymbranding", "change_gymbranding", "view_gympolicy", "change_gympolicy"},
        "members": {
            "add_member", "change_member", "view_member", "delete_member",
            "add_memberemergencycontact", "change_memberemergencycontact", "view_memberemergencycontact", "delete_memberemergencycontact",
            "add_memberdocument", "change_memberdocument", "view_memberdocument", "delete_memberdocument",
            "add_membernote", "change_membernote", "view_membernote", "delete_membernote",
            "view_memberstatushistory",
        },
        "memberships": {
            "add_membershippackage", "change_membershippackage", "delete_membershippackage", "view_membershippackage",
            "add_packagebranch", "change_packagebranch", "delete_packagebranch", "view_packagebranch",
            "add_membership", "change_membership", "delete_membership", "view_membership",
            "add_membershiprenewal", "change_membershiprenewal", "delete_membershiprenewal", "view_membershiprenewal",
            "add_membershipfreeze", "change_membershipfreeze", "delete_membershipfreeze", "view_membershipfreeze",
            "add_membershipadjustment", "change_membershipadjustment", "delete_membershipadjustment", "view_membershipadjustment",
            "view_membershipevent",
        },
        "billing": {
            "add_invoice", "change_invoice", "delete_invoice", "view_invoice",
            "add_invoiceitem", "change_invoiceitem", "delete_invoiceitem", "view_invoiceitem",
            "add_payment", "change_payment", "delete_payment", "view_payment",
            "add_paymentmethod", "change_paymentmethod", "delete_paymentmethod", "view_paymentmethod",
            "add_refund", "change_refund", "delete_refund", "view_refund",
            "add_receipt", "change_receipt", "delete_receipt", "view_receipt",
            "add_paymentadjustment", "change_paymentadjustment", "delete_paymentadjustment", "view_paymentadjustment",
            "view_financialauditevent",
        },
        "attendance": {
            "add_membercredential", "change_membercredential", "delete_membercredential", "view_membercredential",
            "add_accessdevice", "change_accessdevice", "delete_accessdevice", "view_accessdevice",
            "add_attendance", "change_attendance", "delete_attendance", "view_attendance",
            "add_attendanceoverride", "change_attendanceoverride", "delete_attendanceoverride", "view_attendanceoverride",
            "view_accessattempt",
        },
        "analytics": {
            "add_reportpreset", "change_reportpreset", "delete_reportpreset", "view_reportpreset",
            "view_reportexportlog", "delete_reportexportlog",
        },
        "staff": {
            "add_staffprofile", "change_staffprofile", "delete_staffprofile", "view_staffprofile",
            "add_staffdocument", "change_staffdocument", "delete_staffdocument", "view_staffdocument",
            "add_trainerprofile", "change_trainerprofile", "delete_trainerprofile", "view_trainerprofile",
            "add_trainermemberassignment", "change_trainermemberassignment", "delete_trainermemberassignment", "view_trainermemberassignment",
            "add_trainingsession", "change_trainingsession", "delete_trainingsession", "view_trainingsession",
            "add_shifttemplate", "change_shifttemplate", "delete_shifttemplate", "view_shifttemplate",
            "add_staffshift", "change_staffshift", "delete_staffshift", "view_staffshift",
            "add_leaverequest", "change_leaverequest", "delete_leaverequest", "view_leaverequest",
        },
    },
    "Branch Manager": {
        "branches": {"view_branch", "change_branch", "view_branchsettings", "change_branchsettings"},
        "core": {"view_gympolicy"},
        "members": {
            "add_member", "change_member", "view_member",
            "add_memberemergencycontact", "change_memberemergencycontact", "view_memberemergencycontact", "delete_memberemergencycontact",
            "add_memberdocument", "change_memberdocument", "view_memberdocument", "delete_memberdocument",
            "add_membernote", "change_membernote", "view_membernote",
            "view_memberstatushistory",
        },
        "memberships": {
            "view_membershippackage", "view_packagebranch",
            "add_membership", "change_membership", "view_membership",
            "add_membershiprenewal", "change_membershiprenewal", "view_membershiprenewal",
            "add_membershipfreeze", "change_membershipfreeze", "view_membershipfreeze",
            "view_membershipadjustment",
            "view_membershipevent",
        },
        "billing": {
            "add_invoice", "change_invoice", "view_invoice",
            "add_payment", "change_payment", "view_payment",
            "view_paymentmethod",
            "add_refund", "view_refund",
            "view_receipt",
        },
        "attendance": {
            "add_membercredential", "change_membercredential", "view_membercredential",
            "add_accessdevice", "change_accessdevice", "view_accessdevice",
            "add_attendance", "change_attendance", "view_attendance",
            "add_attendanceoverride", "view_attendanceoverride",
            "view_accessattempt",
        },
        "analytics": {
            "add_reportpreset", "change_reportpreset", "view_reportpreset", "view_reportexportlog",
        },
        "staff": {
            "view_staffprofile", "change_staffprofile",
            "add_staffdocument", "view_staffdocument",
            "view_trainerprofile", "change_trainerprofile",
            "add_trainermemberassignment", "change_trainermemberassignment", "view_trainermemberassignment",
            "add_trainingsession", "change_trainingsession", "delete_trainingsession", "view_trainingsession",
            "add_shifttemplate", "change_shifttemplate", "view_shifttemplate",
            "add_staffshift", "change_staffshift", "view_staffshift",
            "add_leaverequest", "change_leaverequest", "view_leaverequest",
        },
    },
    "Receptionist": {
        "branches": {"view_branch"},
        "core": {"view_gympolicy"},
        "members": {
            "add_member", "change_member", "view_member",
            "add_memberemergencycontact", "change_memberemergencycontact", "view_memberemergencycontact",
            "add_memberdocument", "view_memberdocument",
            "add_membernote", "view_membernote",
            "view_memberstatushistory",
        },
        "memberships": {
            "view_membershippackage", "view_packagebranch",
            "add_membership", "view_membership",
            "add_membershiprenewal", "view_membershiprenewal",
            "add_membershipfreeze", "view_membershipfreeze",
            "view_membershipevent",
        },
        "billing": {
            "add_invoice", "view_invoice",
            "add_payment", "view_payment",
            "view_paymentmethod",
            "view_receipt",
        },
        "attendance": {
            "add_membercredential", "view_membercredential",
            "add_attendance", "change_attendance", "view_attendance",
            "view_accessattempt",
        },
        "analytics": {
            "view_reportpreset",
        },
        "staff": {
            "view_staffprofile",
            "view_trainerprofile",
            "view_trainermemberassignment",
            "add_trainingsession", "change_trainingsession", "view_trainingsession",
            "view_staffshift",
            "add_leaverequest", "view_leaverequest",
        },
    },
    "Accountant": {
        "branches": {"view_branch"},
        "members": {"view_member"},
        "memberships": {
            "view_membershippackage",
            "view_membership",
            "view_membershiprenewal",
            "view_membershipevent",
        },
        "billing": {
            "view_invoice",
            "change_payment", "view_payment",
            "view_paymentmethod",
            "change_refund", "view_refund",
            "view_receipt",
            "view_financialauditevent",
        },
        "attendance": {
            "view_attendance",
            "view_accessattempt",
        },
        "analytics": {
            "add_reportpreset", "change_reportpreset", "view_reportpreset", "view_reportexportlog",
        },
        "staff": {
            "view_staffprofile",
            "view_trainerprofile",
            "view_staffshift",
            "view_leaverequest",
        },
    },
    "Trainer": {
        "branches": {"view_branch"},
        "attendance": {"view_attendance"},
        "staff": {
            "view_trainerprofile", "change_trainerprofile",
            "view_trainermemberassignment",
            "add_trainingsession", "change_trainingsession", "view_trainingsession",
            "view_staffshift",
            "add_leaverequest", "view_leaverequest",
        },
    },
    "POS Staff": {"branches": {"view_branch"}},
}


def seed_roles():
    """Set only implemented workflow permissions, replacing stale grants on these six groups."""
    results = {}
    for role in BUSINESS_ROLES:
        group, _ = Group.objects.get_or_create(name=role)
        configured = ROLE_PERMISSIONS[role]
        permissions = list(Permission.objects.filter(content_type__app_label__in=configured)
            .select_related("content_type"))
        expected = {(app, codename) for app, names in configured.items() for codename in names}
        selected = [permission for permission in permissions
            if (permission.content_type.app_label, permission.codename) in expected]
        if {(p.content_type.app_label, p.codename) for p in selected} != expected:
            raise RuntimeError(f"Permissions missing for {role}; run migrations first.")
        group.permissions.set(selected)
        results[role] = len(selected)
    return results


def has_role(user, role):
    return bool(user.is_authenticated and user.is_active and user.is_staff and
        user.groups.filter(name=role).exists())


def is_owner(user):
    return bool(user.is_authenticated and user.is_active and
        (user.is_superuser or has_role(user, "Gym Owner")))
