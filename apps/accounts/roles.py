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
        "finance": {
            "add_branchfinancesettings", "change_branchfinancesettings", "delete_branchfinancesettings", "view_branchfinancesettings",
            "add_expensecategory", "change_expensecategory", "delete_expensecategory", "view_expensecategory",
            "add_supplier", "change_supplier", "delete_supplier", "view_supplier",
            "add_financialaccount", "change_financialaccount", "delete_financialaccount", "view_financialaccount",
            "add_expense", "change_expense", "delete_expense", "view_expense",
            "add_expensepayment", "change_expensepayment", "delete_expensepayment", "view_expensepayment",
            "add_accounttransfer", "change_accounttransfer", "delete_accounttransfer", "view_accounttransfer",
            "add_accountadjustment", "change_accountadjustment", "delete_accountadjustment", "view_accountadjustment",
            "add_financialreconciliation", "change_financialreconciliation", "delete_financialreconciliation", "view_financialreconciliation",
            "view_financeauditevent",
        },
        "inventory": {
            "add_productcategory", "change_productcategory", "delete_productcategory", "view_productcategory",
            "add_product", "change_product", "delete_product", "view_product",
            "add_stockitem", "change_stockitem", "delete_stockitem", "view_stockitem",
            "add_stockbatch", "change_stockbatch", "delete_stockbatch", "view_stockbatch",
            "add_stockmovement", "change_stockmovement", "delete_stockmovement", "view_stockmovement",
            "add_purchaseorder", "change_purchaseorder", "delete_purchaseorder", "view_purchaseorder",
            "add_purchaseorderitem", "change_purchaseorderitem", "delete_purchaseorderitem", "view_purchaseorderitem",
            "add_goodsreceivednote", "change_goodsreceivednote", "delete_goodsreceivednote", "view_goodsreceivednote",
            "add_goodsreceiveditem", "change_goodsreceiveditem", "delete_goodsreceiveditem", "view_goodsreceiveditem",
            "add_stocktransfer", "change_stocktransfer", "delete_stocktransfer", "view_stocktransfer",
            "add_stocktransferitem", "change_stocktransferitem", "delete_stocktransferitem", "view_stocktransferitem",
            "add_stockadjustment", "change_stockadjustment", "delete_stockadjustment", "view_stockadjustment",
            "add_stockadjustmentitem", "change_stockadjustmentitem", "delete_stockadjustmentitem", "view_stockadjustmentitem",
            "add_stockcount", "change_stockcount", "delete_stockcount", "view_stockcount",
            "add_stockcountitem", "change_stockcountitem", "delete_stockcountitem", "view_stockcountitem",
            "add_cashiershift", "change_cashiershift", "delete_cashiershift", "view_cashiershift",
            "add_sale", "change_sale", "delete_sale", "view_sale",
            "add_saleitem", "change_saleitem", "delete_saleitem", "view_saleitem",
            "add_salepayment", "change_salepayment", "delete_salepayment", "view_salepayment",
            "add_salereceipt", "change_salereceipt", "delete_salereceipt", "view_salereceipt",
            "add_salereturn", "change_salereturn", "delete_salereturn", "view_salereturn",
            "add_salereturnitem", "change_salereturnitem", "delete_salereturnitem", "view_salereturnitem",
            "add_possettlementbatch", "change_possettlementbatch", "delete_possettlementbatch", "view_possettlementbatch",
            "add_possettledpayment", "change_possettledpayment", "delete_possettledpayment", "view_possettledpayment",
            "add_possettledreturn", "change_possettledreturn", "delete_possettledreturn", "view_possettledreturn",
        },
        "notifications": {
            "add_providerconfig", "change_providerconfig", "delete_providerconfig", "view_providerconfig",
            "add_messagetemplate", "change_messagetemplate", "delete_messagetemplate", "view_messagetemplate",
            "add_notificationpreference", "change_notificationpreference", "delete_notificationpreference", "view_notificationpreference",
            "add_automationrule", "change_automationrule", "delete_automationrule", "view_automationrule",
            "add_bulkcampaign", "change_bulkcampaign", "delete_bulkcampaign", "view_bulkcampaign",
            "add_notificationmessage", "change_notificationmessage", "delete_notificationmessage", "view_notificationmessage",
            "add_deliveryqueueitem", "change_deliveryqueueitem", "delete_deliveryqueueitem", "view_deliveryqueueitem",
            "add_schedulednotification", "change_schedulednotification", "delete_schedulednotification", "view_schedulednotification",
            "add_inappnotification", "change_inappnotification", "delete_inappnotification", "view_inappnotification",
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
        "finance": {
            "view_branchfinancesettings",
            "view_expensecategory",
            "add_supplier", "change_supplier", "view_supplier",
            "view_financialaccount",
            "add_expense", "change_expense", "view_expense",
            "add_expensepayment", "view_expensepayment",
            "add_accounttransfer", "view_accounttransfer",
            "add_financialreconciliation", "change_financialreconciliation", "view_financialreconciliation",
        },
        "inventory": {
            "view_productcategory", "add_product", "change_product", "view_product",
            "view_stockitem", "change_stockitem", "view_stockbatch", "change_stockbatch", "view_stockmovement",
            "add_purchaseorder", "change_purchaseorder", "view_purchaseorder", "add_purchaseorderitem", "change_purchaseorderitem", "view_purchaseorderitem",
            "add_goodsreceivednote", "view_goodsreceivednote", "add_goodsreceiveditem", "view_goodsreceiveditem",
            "add_stocktransfer", "change_stocktransfer", "view_stocktransfer", "add_stocktransferitem", "change_stocktransferitem", "view_stocktransferitem",
            "add_stockadjustment", "change_stockadjustment", "view_stockadjustment", "add_stockadjustmentitem", "change_stockadjustmentitem", "view_stockadjustmentitem",
            "add_stockcount", "change_stockcount", "view_stockcount", "add_stockcountitem", "change_stockcountitem", "view_stockcountitem",
            "add_cashiershift", "change_cashiershift", "view_cashiershift",
            "add_sale", "change_sale", "view_sale", "add_saleitem", "change_saleitem", "view_saleitem", "add_salepayment", "view_salepayment", "view_salereceipt",
            "add_salereturn", "change_salereturn", "view_salereturn", "add_salereturnitem", "view_salereturnitem",
            "add_possettlementbatch", "view_possettlementbatch",
        },
        "notifications": {
            "add_messagetemplate", "change_messagetemplate", "view_messagetemplate",
            "add_notificationpreference", "change_notificationpreference", "view_notificationpreference",
            "add_automationrule", "change_automationrule", "view_automationrule",
            "add_bulkcampaign", "change_bulkcampaign", "view_bulkcampaign",
            "add_notificationmessage", "view_notificationmessage",
            "view_deliveryqueueitem",
            "view_schedulednotification",
            "view_inappnotification", "change_inappnotification",
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
        "finance": {
            "add_expense", "view_expense",
        },
        "inventory": {
            "view_productcategory", "view_product", "view_stockitem",
            "add_cashiershift", "change_cashiershift", "view_cashiershift",
            "add_sale", "view_sale", "add_saleitem", "view_saleitem",
            "add_salepayment", "view_salepayment", "view_salereceipt",
        },
        "notifications": {
            "view_messagetemplate",
            "add_notificationmessage", "view_notificationmessage",
            "view_notificationpreference", "change_notificationpreference",
            "view_inappnotification", "change_inappnotification",
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
        "finance": {
            "view_branchfinancesettings",
            "add_expensecategory", "change_expensecategory", "view_expensecategory",
            "add_supplier", "change_supplier", "view_supplier",
            "add_financialaccount", "change_financialaccount", "view_financialaccount",
            "add_expense", "change_expense", "view_expense",
            "add_expensepayment", "change_expensepayment", "view_expensepayment",
            "add_accounttransfer", "change_accounttransfer", "view_accounttransfer",
            "add_accountadjustment", "change_accountadjustment", "view_accountadjustment",
            "add_financialreconciliation", "change_financialreconciliation", "view_financialreconciliation",
            "view_financeauditevent",
        },
        "inventory": {
            "view_productcategory", "view_product", "view_stockitem", "view_stockmovement",
            "view_purchaseorder", "view_purchaseorderitem", "view_goodsreceivednote", "view_goodsreceiveditem",
            "view_stocktransfer", "view_stockadjustment", "view_stockcount", "view_cashiershift",
            "view_sale", "view_saleitem", "view_salepayment", "view_salereceipt",
            "add_salereturn", "change_salereturn", "view_salereturn", "add_salereturnitem", "view_salereturnitem",
            "add_possettlementbatch", "change_possettlementbatch", "view_possettlementbatch",
        },
        "notifications": {
            "view_messagetemplate",
            "add_notificationmessage", "view_notificationmessage",
            "view_inappnotification", "change_inappnotification",
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
        "notifications": {
            "view_inappnotification", "change_inappnotification",
        },
    },
    "POS Staff": {
        "branches": {"view_branch"},
        "inventory": {
            "view_productcategory", "view_product", "view_stockitem",
            "add_cashiershift", "change_cashiershift", "view_cashiershift",
            "add_sale", "change_sale", "view_sale", "add_saleitem", "view_saleitem",
            "add_salepayment", "view_salepayment", "view_salereceipt", "view_salereturn",
        },
        "notifications": {
            "view_inappnotification", "change_inappnotification",
        },
    },
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
