from django.contrib.auth.models import Group, Permission

from apps.branches.models import BUSINESS_ROLES


ROLE_PERMISSIONS = {
    "Gym Owner": {
        "branches": {"add_branch", "change_branch", "view_branch", "view_branchsettings",
            "change_branchsettings", "add_userbranchaccess", "change_userbranchaccess", "view_userbranchaccess"},
        "accounts": {"add_staffaccount", "change_staffaccount", "view_staffaccount"},
        "core": {"view_gymbranding", "change_gymbranding"},
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
    },
    "Branch Manager": {
        "branches": {"view_branch", "change_branch", "view_branchsettings", "change_branchsettings"},
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
    },
    "Receptionist": {
        "branches": {"view_branch"},
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
    },
    "Trainer": {"branches": {"view_branch"}},
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
