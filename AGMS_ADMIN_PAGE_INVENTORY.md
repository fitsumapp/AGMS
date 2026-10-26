# AGMS — Django Unfold Admin Page Inventory

**Audit Scope**: Complete localhost inventory of all ModelAdmin changelists, add forms, and custom admin views across Phases 01–14.
**Environment**: Django 5.1.7 + PostgreSQL 18.3 + Django Unfold Admin.
**Audit Branch**: udit/localhost-ui-ux
**Baseline Verification**: 403 automated tests passing (pytest -q), 0 system check issues.

## Executive Metrics

- **Total ModelAdmin Registrations**: 89
- **Total Changelist Pages**: 89
- **Total Add Form Endpoints**: 89
- **Total Custom Admin Views & Dashboards**: 30
- **Total Audited Admin Page Views**: 208 views
- **Automated Role Audits Conducted**: 8 Roles (Super Admin, Owner, Manager, Receptionist, Accountant, Trainer, POS Staff, Anonymous)
- **Visual / Browser UAT Verified**: 100% of core operational flows (Desktop 1366px, Tablet 768px, Mobile 375px)

---

## 1. ModelAdmin Inventory (Changelists & Forms)

| # | Module | Model / Page Name | Changelist URL | Add Form URL | Intended Roles | Desktop (1366px) | Tablet (768px) | Mobile (375px) | Light/Dark Theme | Fix Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CORE | Gym Profile | /admin/core/gymprofile/ | /admin/core/gymprofile/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 2 | CORE | Gym Settings | /admin/core/gymsettings/ | /admin/core/gymsettings/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 3 | CORE | Gym policy | /admin/core/gympolicy/ | /admin/core/gympolicy/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 4 | CORE | Gym branding | /admin/core/gymbranding/ | /admin/core/gymbranding/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 5 | CORE | Feature Flag | /admin/core/featureflag/ | /admin/core/featureflag/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 6 | CORE | Backup Configuration | /admin/core/backupconfiguration/ | /admin/core/backupconfiguration/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 7 | ACCOUNTS | user | /admin/accounts/user/ | /admin/accounts/user/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 8 | AUTH | group | /admin/auth/group/ | /admin/auth/group/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 9 | ACCOUNTS | Staff account | /admin/accounts/staffaccount/ | /admin/accounts/staffaccount/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 10 | BRANCHES | branch | /admin/branches/branch/ | /admin/branches/branch/add/ | All Staff | PASS | PASS | PASS | PASS | Verified |
| 11 | BRANCHES | user branch access | /admin/branches/userbranchaccess/ | /admin/branches/userbranchaccess/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 12 | BRANCHES | branch settings | /admin/branches/branchsettings/ | /admin/branches/branchsettings/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 13 | BRANCHES | audit event | /admin/branches/auditevent/ | /admin/branches/auditevent/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 14 | MEMBERS | member | /admin/members/member/ | /admin/members/member/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 15 | MEMBERSHIPS | membership package | /admin/memberships/membershippackage/ | /admin/memberships/membershippackage/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 16 | MEMBERSHIPS | membership | /admin/memberships/membership/ | /admin/memberships/membership/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 17 | MEMBERSHIPS | membership freeze | /admin/memberships/membershipfreeze/ | /admin/memberships/membershipfreeze/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 18 | MEMBERSHIPS | membership renewal | /admin/memberships/membershiprenewal/ | /admin/memberships/membershiprenewal/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 19 | MEMBERSHIPS | Membership event | /admin/memberships/membershipevent/ | /admin/memberships/membershipevent/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 20 | BILLING | invoice | /admin/billing/invoice/ | /admin/billing/invoice/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 21 | BILLING | payment | /admin/billing/payment/ | /admin/billing/payment/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 22 | BILLING | payment method | /admin/billing/paymentmethod/ | /admin/billing/paymentmethod/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 23 | BILLING | refund | /admin/billing/refund/ | /admin/billing/refund/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 24 | BILLING | receipt | /admin/billing/receipt/ | /admin/billing/receipt/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 25 | BILLING | financial audit event | /admin/billing/financialauditevent/ | /admin/billing/financialauditevent/add/ | Superadmin, Owner, Accountant | PASS | PASS | PASS | PASS | Verified |
| 26 | ATTENDANCE | attendance | /admin/attendance/attendance/ | /admin/attendance/attendance/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 27 | ATTENDANCE | member credential | /admin/attendance/membercredential/ | /admin/attendance/membercredential/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 28 | ATTENDANCE | access device | /admin/attendance/accessdevice/ | /admin/attendance/accessdevice/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 29 | ATTENDANCE | attendance override | /admin/attendance/attendanceoverride/ | /admin/attendance/attendanceoverride/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 30 | ATTENDANCE | access attempt | /admin/attendance/accessattempt/ | /admin/attendance/accessattempt/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 31 | ANALYTICS | Report Export Log | /admin/analytics/reportexportlog/ | /admin/analytics/reportexportlog/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 32 | ANALYTICS | Report Preset | /admin/analytics/reportpreset/ | /admin/analytics/reportpreset/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 33 | STAFF | staff profile | /admin/staff/staffprofile/ | /admin/staff/staffprofile/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 34 | STAFF | trainer profile | /admin/staff/trainerprofile/ | /admin/staff/trainerprofile/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 35 | STAFF | trainer member assignment | /admin/staff/trainermemberassignment/ | /admin/staff/trainermemberassignment/add/ | Superadmin, Owner, Manager, Receptionist, Trainer | PASS | PASS | PASS | PASS | Verified |
| 36 | STAFF | training session | /admin/staff/trainingsession/ | /admin/staff/trainingsession/add/ | Superadmin, Owner, Manager, Receptionist, Trainer | PASS | PASS | PASS | PASS | Verified |
| 37 | STAFF | shift template | /admin/staff/shifttemplate/ | /admin/staff/shifttemplate/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 38 | STAFF | staff shift | /admin/staff/staffshift/ | /admin/staff/staffshift/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 39 | STAFF | leave request | /admin/staff/leaverequest/ | /admin/staff/leaverequest/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 40 | CLASSES | class type | /admin/classes/classtype/ | /admin/classes/classtype/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 41 | CLASSES | class schedule template | /admin/classes/classscheduletemplate/ | /admin/classes/classscheduletemplate/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 42 | CLASSES | class session | /admin/classes/classsession/ | /admin/classes/classsession/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 43 | CLASSES | class booking | /admin/classes/classbooking/ | /admin/classes/classbooking/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 44 | FINANCE | Branch finance settings | /admin/finance/branchfinancesettings/ | /admin/finance/branchfinancesettings/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 45 | FINANCE | Expense category | /admin/finance/expensecategory/ | /admin/finance/expensecategory/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 46 | FINANCE | Supplier & Vendor | /admin/finance/supplier/ | /admin/finance/supplier/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 47 | FINANCE | Cash & Bank Account | /admin/finance/financialaccount/ | /admin/finance/financialaccount/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 48 | FINANCE | Expense | /admin/finance/expense/ | /admin/finance/expense/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 49 | FINANCE | Expense payment | /admin/finance/expensepayment/ | /admin/finance/expensepayment/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 50 | FINANCE | Account transfer | /admin/finance/accounttransfer/ | /admin/finance/accounttransfer/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 51 | FINANCE | Account adjustment | /admin/finance/accountadjustment/ | /admin/finance/accountadjustment/add/ | Superadmin, Owner, Accountant | PASS | PASS | PASS | PASS | Verified |
| 52 | FINANCE | Financial reconciliation | /admin/finance/financialreconciliation/ | /admin/finance/financialreconciliation/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 53 | FINANCE | Finance audit event | /admin/finance/financeauditevent/ | /admin/finance/financeauditevent/add/ | Superadmin, Owner, Accountant | PASS | PASS | PASS | PASS | Verified |
| 54 | FINANCE | Billing settlement batch | /admin/finance/billingsettlementbatch/ | /admin/finance/billingsettlementbatch/add/ | Super Admin | PASS | PASS | PASS | PASS | Verified |
| 55 | INVENTORY | Product Category | /admin/inventory/productcategory/ | /admin/inventory/productcategory/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Pos | PASS | PASS | PASS | PASS | Verified |
| 56 | INVENTORY | Product | /admin/inventory/product/ | /admin/inventory/product/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Pos | PASS | PASS | PASS | PASS | Verified |
| 57 | INVENTORY | Stock Overview | /admin/inventory/stockitem/ | /admin/inventory/stockitem/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Pos | PASS | PASS | PASS | PASS | Verified |
| 58 | INVENTORY | Stock Movement | /admin/inventory/stockmovement/ | /admin/inventory/stockmovement/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 59 | INVENTORY | Purchase Order | /admin/inventory/purchaseorder/ | /admin/inventory/purchaseorder/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 60 | INVENTORY | Goods Received Note | /admin/inventory/goodsreceivednote/ | /admin/inventory/goodsreceivednote/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 61 | INVENTORY | Stock Transfer | /admin/inventory/stocktransfer/ | /admin/inventory/stocktransfer/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 62 | INVENTORY | Stock Adjustment | /admin/inventory/stockadjustment/ | /admin/inventory/stockadjustment/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 63 | INVENTORY | Stock Count | /admin/inventory/stockcount/ | /admin/inventory/stockcount/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 64 | INVENTORY | Cashier Shift | /admin/inventory/cashiershift/ | /admin/inventory/cashiershift/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Pos | PASS | PASS | PASS | PASS | Verified |
| 65 | INVENTORY | POS Sale | /admin/inventory/sale/ | /admin/inventory/sale/add/ | Superadmin, Owner, Manager, Receptionist, Accountant, Pos | PASS | PASS | PASS | PASS | Verified |
| 66 | INVENTORY | POS Settlement Batch | /admin/inventory/possettlementbatch/ | /admin/inventory/possettlementbatch/add/ | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 67 | NOTIFICATIONS | Provider Configuration | /admin/notifications/providerconfig/ | /admin/notifications/providerconfig/add/ | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 68 | NOTIFICATIONS | Message Template | /admin/notifications/messagetemplate/ | /admin/notifications/messagetemplate/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 69 | NOTIFICATIONS | Notification Preference | /admin/notifications/notificationpreference/ | /admin/notifications/notificationpreference/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 70 | NOTIFICATIONS | Automation Rule | /admin/notifications/automationrule/ | /admin/notifications/automationrule/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 71 | NOTIFICATIONS | Bulk Campaign | /admin/notifications/bulkcampaign/ | /admin/notifications/bulkcampaign/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 72 | NOTIFICATIONS | Notification Message | /admin/notifications/notificationmessage/ | /admin/notifications/notificationmessage/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 73 | NOTIFICATIONS | Delivery Queue Item | /admin/notifications/deliveryqueueitem/ | /admin/notifications/deliveryqueueitem/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 74 | NOTIFICATIONS | Scheduled Notification | /admin/notifications/schedulednotification/ | /admin/notifications/schedulednotification/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 75 | NOTIFICATIONS | In-App Notification | /admin/notifications/inappnotification/ | /admin/notifications/inappnotification/add/ | All Staff | PASS | PASS | PASS | PASS | Verified |
| 76 | FACILITIES | Locker Zone | /admin/facilities/lockerzone/ | /admin/facilities/lockerzone/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 77 | FACILITIES | Locker | /admin/facilities/locker/ | /admin/facilities/locker/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 78 | FACILITIES | Locker Assignment | /admin/facilities/lockerassignment/ | /admin/facilities/lockerassignment/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 79 | FACILITIES | Locker Incident | /admin/facilities/lockerincident/ | /admin/facilities/lockerincident/add/ | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 80 | FACILITIES | Equipment Category | /admin/facilities/equipmentcategory/ | /admin/facilities/equipmentcategory/add/ | Superadmin, Owner, Manager, Trainer | PASS | PASS | PASS | PASS | Fixed (equipment_count) |
| 81 | FACILITIES | Facility Area | /admin/facilities/facilityarea/ | /admin/facilities/facilityarea/add/ | Superadmin, Owner, Manager, Receptionist, Trainer | PASS | PASS | PASS | PASS | Verified |
| 82 | FACILITIES | Equipment Asset | /admin/facilities/equipment/ | /admin/facilities/equipment/add/ | Superadmin, Owner, Manager, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 83 | FACILITIES | Maintenance Schedule | /admin/facilities/maintenanceschedule/ | /admin/facilities/maintenanceschedule/add/ | Superadmin, Owner, Manager, Trainer | PASS | PASS | PASS | PASS | Verified |
| 84 | FACILITIES | Fault Report | /admin/facilities/faultreport/ | /admin/facilities/faultreport/add/ | Superadmin, Owner, Manager, Receptionist, Trainer | PASS | PASS | PASS | PASS | Verified |
| 85 | FACILITIES | Maintenance Work Order | /admin/facilities/maintenanceworkorder/ | /admin/facilities/maintenanceworkorder/add/ | Superadmin, Owner, Manager, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 86 | FACILITIES | Cleaning Task Template / Schedule | /admin/facilities/cleaningtaskschedule/ | /admin/facilities/cleaningtaskschedule/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 87 | FACILITIES | Cleaning Task Execution | /admin/facilities/cleaningtaskexecution/ | /admin/facilities/cleaningtaskexecution/add/ | Superadmin, Owner, Manager, Receptionist | PASS | PASS | PASS | PASS | Verified |
| 88 | FACILITIES | Facility Inspection | /admin/facilities/facilityinspection/ | /admin/facilities/facilityinspection/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 89 | FACILITIES | Incident Report | /admin/facilities/incidentreport/ | /admin/facilities/incidentreport/add/ | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |

---

## 2. Custom Admin Views & Action Endpoints Inventory

| # | Module | View Name | URL Pattern | Reverse Name | Intended Roles | Desktop | Tablet | Mobile | Light/Dark | Status / Fix |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CORE | Main Dashboard | /admin/ | admin:index | All Staff | PASS | PASS | PASS | PASS | Verified |
| 2 | ANALYTICS | Executive Analytics Dashboard | /admin/analytics/dashboard/ | analytics-dashboard | All Staff | PASS | PASS | PASS | PASS | Verified |
| 3 | ANALYTICS | Financial Analytics | /admin/analytics/financial/ | analytics-financial | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 4 | ANALYTICS | Membership Analytics | /admin/analytics/memberships/ | analytics-memberships | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 5 | ANALYTICS | Attendance Analytics | /admin/analytics/attendance/ | analytics-attendance | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 6 | ANALYTICS | Analytics Report Center | /admin/analytics/reports/ | analytics-reports | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Fixed (	ransaction_reference) |
| 7 | ATTENDANCE | Attendance Live Dashboard | /admin/attendance/attendance/dashboard/ | admin:attendance_dashboard | All Staff | PASS | PASS | PASS | PASS | Verified |
| 8 | ATTENDANCE | QR Code Check-in | /admin/attendance/attendance/checkin/ | admin:attendance_qr_checkin | All Staff | PASS | PASS | PASS | PASS | Verified |
| 9 | ATTENDANCE | Manual Attendance Check-in | /admin/attendance/attendance/manual-checkin/ | admin:attendance_manual_checkin | All Staff | PASS | PASS | PASS | PASS | Verified |
| 10 | ATTENDANCE | Attendance Reports | /admin/attendance/attendance/reports/ | admin:attendance_reports | All Staff | PASS | PASS | PASS | PASS | Verified |
| 11 | BILLING | Create Invoice | /admin/billing/invoice/create-invoice/ | admin:billing_invoice_create_invoice | All Staff | PASS | PASS | PASS | PASS | Verified |
| 12 | CLASSES | Class Calendar | /admin/classes/calendar/ | class-calendar | All Staff | PASS | PASS | PASS | PASS | Verified |
| 13 | CLASSES | Class Reports | /admin/classes/reports/ | class-reports | All Staff | PASS | PASS | PASS | PASS | Verified |
| 14 | STAFF | Trainer Performance Analytics | /admin/staff/performance/ | staff-performance | All Staff | PASS | PASS | PASS | PASS | Verified |
| 15 | STAFF | Staff Reports | /admin/staff/reports/ | staff-reports | All Staff | PASS | PASS | PASS | PASS | Verified |
| 16 | FINANCE | Financial Overview | /admin/finance/overview/ | finance-overview | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 17 | INVENTORY | POS Checkout Terminal | /admin/pos/checkout/ | pos-checkout | Superadmin, Owner, Manager, Receptionist, Pos | PASS | PASS | PASS | PASS | Verified |
| 18 | INVENTORY | POS Reports | /admin/pos/reports/ | pos-reports | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 19 | NOTIFICATIONS | Notification Dashboard | /admin/notifications/dashboard/ | notification-dashboard | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 20 | NOTIFICATIONS | Send Single SMS | /admin/notifications/sendsms/ | send-sms | Superadmin, Owner, Manager, Receptionist, Accountant | PASS | PASS | PASS | PASS | Verified |
| 21 | NOTIFICATIONS | Bulk SMS Campaign | /admin/notifications/bulkcampaign/ | bulk-campaign | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 22 | NOTIFICATIONS | Communication Reports | /admin/notifications/reports/ | notification-reports | Superadmin, Owner, Manager, Accountant | PASS | PASS | PASS | PASS | Verified |
| 23 | FACILITIES | Facility Dashboard | /admin/facilities/dashboard/ | facility-dashboard | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 24 | FACILITIES | Facility Reports | /admin/facilities/reports/ | facility-reports | Superadmin, Owner, Manager, Receptionist, Accountant, Trainer | PASS | PASS | PASS | PASS | Verified |
| 25 | CORE | System Health | /admin/operations/system-health/ | system-health | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 26 | CORE | Backup Readiness | /admin/operations/backup-readiness/ | backup-readiness | Superadmin, Owner | PASS | PASS | PASS | PASS | Verified |
| 27 | CORE | Data Import & Export | /admin/operations/data-management/ | data-management | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 28 | CORE | Operational Audit Reports | /admin/operations/reports/ | operational-reports | Superadmin, Owner, Manager | PASS | PASS | PASS | PASS | Verified |
| 29 | BRANCHES | Branch Switcher | /admin/branches/switch/ | switch-branch | System / POST | PASS | PASS | PASS | PASS | Verified (POST Mutation) |
| 30 | CORE | Gym Logo Provider | /admin/branding/logo/ | gym-logo | System / POST | PASS | PASS | PASS | PASS | Verified |

---

## 3. Inventory Verification Summary

- **Zero Unhandled 500 Exceptions**: Every registered ModelAdmin and custom view responds with proper HTTP semantics (200, 302, 403, or 405).
- **Zero Raw Unrendered Template Tags**: No {{ or {% leaks detected in HTML output across all rendered changelists and views.
- **Role Boundary Enforcement**: Role-based access control (RBAC) and branch isolation verified across all 8 user personas.
- **Django Unfold Design System**: 100% of changelists utilize Unfold table containers, responsive wrappers, and standard action ribbons.