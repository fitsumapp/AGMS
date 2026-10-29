# AGMS — Complete Form Inventory (Step 04B)

**Project:** Antigravity Gym Management System (AGMS)  
**Date:** 2026-10-29  
**Branch:** `step-04b/global-form-modernization`  
**Stack:** Django 5.2, PostgreSQL 17, Django Unfold Admin  

---

## 1. Overview & Scope

This inventory audits all form surfaces across the entire Antigravity Gym Management System (AGMS). The system contains 88 distinct form interfaces spanning Django ModelAdmin add/change forms, inline formsets, custom action forms, standalone operational views, and modal dialogs.

### Summary Statistics
- **Total Form Surfaces Documented:** 88
- **ModelAdmin Add / Change Forms:** 46
- **Inline Formsets:** 21
- **Custom Admin Action Forms:** 11
- **Standalone Operational / Custom View Forms:** 10
- **Modules Covered:** 12

---

## 2. Form Inventory by Module

### Module 1: Members and Registration
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M1-01** | Member Add Form | `MemberRegistrationForm` | ModelAdmin Add | First/Middle/Last Name, Gender, DOB, Photo, Phone, Alt Phone, Email, Address, City, Home Branch, Source, Referral, Duplicate Confirmation Checkbox | Emergency Contacts, Documents, Notes, Status History |
| **M1-02** | Member Change Form | `MemberChangeForm` | ModelAdmin Change | Name, Gender, DOB, Photo, Phone, Alt Phone, Email, Address, City, Status, Home Branch, Read-only Member Number & QR Badge | Emergency Contacts, Documents, Notes, Status History |
| **M1-03** | Member Emergency Contact | `MemberEmergencyContactInline` | TabularInline | Full Name, Relationship, Phone, Alt Phone, Is Primary (radio/checkbox) | — |
| **M1-04** | Member Document | `MemberDocumentInline` | TabularInline | Document Type, Document Title, File Upload, Download Link, Uploaded At, Uploaded By | — |
| **M1-05** | Member Note | `MemberNoteInline` | StackedInline | Category (General/Sensitive/Restricted), Note Textarea, Created By, Created At | — |
| **M1-06** | Member Status History | `MemberStatusHistoryInline` | TabularInline | Read-only Previous Status, New Status, Reason, Changed By, Changed At | — |
| **M1-07** | Change Status Action | `MemberStatusChangeForm` / `change_status.html` | Custom Action Form | New Status (Select), Reason (Textarea mandatory for Suspension) | — |
| **M1-08** | Transfer Branch Action | `MemberTransferBranchForm` / `transfer_branch.html` | Custom Action Form | New Home Branch (ModelChoiceField), Reason (Textarea) | — |
| **M1-09** | Member Changelist Search & Filter | `admin/search_form.html`, `filter.html` | Admin Search & Filter | Query Input (name, number, phone), Branch Filter, Status Filter, Source Filter | — |

---

### Module 2: Membership Packages and Subscriptions
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M2-01** | Membership Package | `MembershipPackageForm` | ModelAdmin Add/Change | Code, Name, Description, Duration Value, Duration Unit, Price, Currency, Cross-Branch Toggle, Freeze Allowed Toggle, Max Freeze Days, Active Toggle | `PackageBranchInline` |
| **M2-02** | Package Branch Availability | `PackageBranchInline` | TabularInline | Branch (FK), Is Enabled (Checkbox) | — |
| **M2-03** | Membership Assignment | `MembershipAssignmentForm` | ModelAdmin Add/Change | Member (FK Searchable), Package (FK), Branch (FK), Start Date (DatePicker), End Date (DatePicker), Status (Select), Auto Renew Toggle | Renewals, Freezes, Adjustments, Events |
| **M2-04** | Membership Renewal Form | `MembershipRenewalForm` / `renew_membership.html` | Custom Form & Inline | New End Date, Package / Rate, Discount, Notes | — |
| **M2-05** | Membership Freeze Request | `MembershipFreezeForm` / `freeze_membership.html` | Custom Form & Inline | Freeze Start Date, Freeze End Date, Reason (Textarea) | — |
| **M2-06** | Approve / Reject Freeze | `approve_freeze.html` | Custom Action Form | Approval Status, Approval Notes, Adjustment Days | — |
| **M2-07** | Membership Adjustment | `MembershipAdjustmentInline` | TabularInline | Adjustment Type, Days Added, Reason, Adjusted By | — |
| **M2-08** | Membership Event Audit | `MembershipEventInline` | TabularInline | Read-only Event Type, Timestamp, Details, Actor | — |

---

### Module 3: Billing, Payments, Refunds and Receipts
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M3-01** | Create Invoice (Manual) | `InvoiceCreateForm` / `create_invoice.html` | Custom Add View | Member, Branch, Due Date, Notes, Item Description, Quantity, Unit Price | — |
| **M3-02** | Invoice Admin Form | ModelAdmin | ModelAdmin Add/Change | Invoice Number (RO), Member, Branch, Subtotal (RO), Total Amount, Paid Amount (RO), Balance Due (RO), Status, Issue Date, Due Date, Notes | `InvoiceItemInline` |
| **M3-03** | Invoice Item Line | `InvoiceItemInline` | TabularInline | Description, Item Type, Quantity, Unit Price, Discount Amount, Tax Amount, Line Total (RO) | — |
| **M3-04** | Record Payment | `RecordPaymentForm` / `record_payment.html` | Custom Form / Add | Invoice (FK), Payment Method (Select), Amount (Decimal), Tendered Amount, Change Amount, Transaction Reference, Notes | — |
| **M3-05** | Verify / Reject Payment | `verify_payment.html` | Custom Action Form | Status (Verified / Rejected), Verification Notes, Verified At | — |
| **M3-06** | Request Refund | `RefundRequestForm` / `request_refund.html` | Custom Form / Add | Payment (FK), Refund Amount, Reason Category, Reason Detail (Textarea) | — |
| **M3-07** | Approve / Complete Refund | `approve_refund.html` | Custom Action Form | Approval Status (Approved / Rejected), Settlement Account, Approval Notes | — |
| **M3-08** | Payment Method Admin | ModelAdmin | ModelAdmin Add/Change | Code, Name, Method Type, Surcharge Percentage, Is Active, Is Digital | — |
| **M3-09** | Receipt Admin | ModelAdmin | ModelAdmin View/RO | Receipt Number, Payment (FK), Member, Total Amount, Issued At | — |
| **M3-10** | Financial Audit Event | ModelAdmin | ModelAdmin View/RO | Event Type, Actor, Amount, Timestamp, Metadata JSON | — |

---

### Module 4: Attendance and Access Control
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M4-01** | QR Check-in Terminal | `QRCheckinForm` / `checkin.html` | Standalone POS/Terminal | Credential QR Token Input, Manual Override Trigger | — |
| **M4-02** | Manual Check-in Terminal | `ManualCheckinForm` / `manual_checkin.html` | Standalone Terminal | Member Lookup (Number/Phone/Name), Branch, Notes | — |
| **M4-03** | Attendance Override | `ManualOverrideForm` | Modal / Action Form | Member (FK), Branch, Reason (Select), Justification (Textarea) | — |
| **M4-04** | Member Credential Reissue | `reissue_credential.html` | Custom Action Form | Credential Type, Reason for Reissue, Confirmation | — |
| **M4-05** | Member Credential Revoke | `revoke_credential.html` | Custom Action Form | Revocation Reason, Invalidate Previous Codes Checkbox | — |
| **M4-06** | Access Device Admin | ModelAdmin | ModelAdmin Add/Change | Device Name, Device Code, Branch, IP Address, Device Type, Status, Is Active | — |
| **M4-07** | Attendance Record Form | ModelAdmin | ModelAdmin Change/RO | Member, Membership, Branch, Check-in Time, Check-out Time, Status, Notes | — |
| **M4-08** | Attendance Reports Filter | `AttendanceReportFilterForm` / `reports.html` | Filter Form | Date Range (Start/End), Branch, Status, Attendance Method, Format (CSV/PDF) | — |

---

### Module 5: Staff and Trainers
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M5-01** | Staff Profile Form | ModelAdmin | ModelAdmin Add/Change | User (FK), Employee Number, Job Title, Department, Employment Type, Employment Status, Primary Branch, Hire Date, Termination Date | `StaffDocumentInline` |
| **M5-02** | Staff Document | `StaffDocumentInline` | TabularInline | Title, Document Type, File Upload, Download Link (RO), Uploaded At, Uploaded By | — |
| **M5-03** | Trainer Profile Form | ModelAdmin | ModelAdmin Add/Change | Staff Profile (FK), Specialization, Bio, Commission Rate, Hourly Rate, Is Available For Booking, Rating | — |
| **M5-04** | Trainer Member Assignment | ModelAdmin | ModelAdmin Add/Change | Trainer (FK), Member (FK), Package (FK), Assigned Date, Status, Notes | — |
| **M5-05** | Training Session Form | ModelAdmin | ModelAdmin Add/Change | Trainer, Member, Branch, Scheduled Time, Duration (Mins), Status, Session Notes | — |
| **M5-06** | Shift Template Form | ModelAdmin | ModelAdmin Add/Change | Name, Branch, Start Time, End Time, Days of Week, Color Code | — |
| **M5-07** | Staff Shift Schedule Form | ModelAdmin | ModelAdmin Add/Change | Staff (FK), Branch, Shift Date, Start Time, End Time, Actual Check-in, Actual Check-out, Status | — |
| **M5-08** | Leave Request Form | ModelAdmin | ModelAdmin Add/Change | Staff (FK), Leave Type, Start Date, End Date, Reason, Approval Status, Approver Notes | — |
| **M5-09** | Staff Reports Filter | `reports.html` | Custom Filter Form | Department, Branch, Employment Status, Date Range, Export Format | — |

---

### Module 6: Classes and Bookings
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M6-01** | Class Type Form | ModelAdmin | ModelAdmin Add/Change | Name, Slug, Category, Description, Difficulty Level, Default Duration, Default Capacity, Waitlist Capacity, Is Active, Branches (Multi-Select) | — |
| **M6-02** | Class Schedule Template | ModelAdmin | ModelAdmin Add/Change | Class Type (FK), Branch (FK), Room, Trainer (FK), Day of Week, Start Time, End Time, Capacity, Is Active | `ClassScheduleExceptionInline` |
| **M6-03** | Schedule Exception | `ClassScheduleExceptionInline` | TabularInline | Exception Date (DatePicker), Reason, Is Cancelled (Checkbox) | — |
| **M6-04** | Class Session Form | ModelAdmin | ModelAdmin Add/Change | Template (FK), Class Type, Branch, Room, Trainer, Session Date, Start Time, End Time, Capacity, Status | `ClassBookingInline` |
| **M6-05** | Class Booking Form / Inline | `ClassBookingInline` & ModelAdmin | TabularInline & Change | Session (FK), Member (FK), Booking Status, Waitlist Position, Booked At, Attendance Marked At, Notes | — |
| **M6-06** | Class Calendar & Filter | `calendar.html` | Interactive View | Branch Select, Class Type Filter, Trainer Filter, Week Navigation | — |
| **M6-07** | Class Reports Filter | `reports.html` | Filter Form | Date Range, Branch, Class Type, Export Format | — |

---

### Module 7: Finance and Expenses
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M7-01** | Branch Finance Settings | ModelAdmin | ModelAdmin Add/Change | Branch (FK), Manager Approval Limit (ETB), Receptionist Can Create Expenses Toggle, Allow Self-Approval Toggle, Allow Negative Balance Toggle | — |
| **M7-02** | Expense Category Form | ModelAdmin | ModelAdmin Add/Change | Name, Code, Description, Order, Is Active (Checkbox) | — |
| **M7-03** | Supplier Form | ModelAdmin | ModelAdmin Add/Change | Name, Supplier Type, Contact Person, Phone, Email, Address, TIN / VAT, Branch Scope, Is Active | — |
| **M7-04** | Financial Account Form | ModelAdmin | ModelAdmin Add/Change | Name, Account Number, Account Type (Cash/Bank/Digital), Bank Name, Currency, Opening Balance, Current Balance (RO), Branch, Is Active | — |
| **M7-05** | Expense Form | ModelAdmin | ModelAdmin Add/Change | Expense Number (RO), Branch, Category, Supplier, Amount, Currency, Expense Date, Description, Receipt Attachment, Approval Status, Approved By (RO) | `ExpensePaymentInline`, `FinanceAuditEventInline` |
| **M7-06** | Expense Payment | `ExpensePaymentInline` & ModelAdmin | TabularInline & Add/Change | Financial Account (FK), Payment Method, Amount, Paid Date, Reference Number, Notes | — |
| **M7-07** | Account Transfer Form | ModelAdmin | ModelAdmin Add/Change | Transfer Number (RO), From Account, To Account, Amount, Transfer Date, Fee Amount, Description, Status | — |
| **M7-08** | Financial Reconciliation | ModelAdmin | ModelAdmin Add/Change | Account (FK), Reconciliation Date, Statement Opening Balance, Statement Closing Balance, System Calculated Balance, Discrepancy Amount, Notes, Is Closed | — |
| **M7-09** | Billing Settlement Batch | ModelAdmin | ModelAdmin Add/Change | Batch Number, Branch, Settlement Date, Gross Amount, Refund Deductions, Net Amount, Target Account, Status | Settled Payments, Settled Refunds |

---

### Module 8: POS and Inventory
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M8-01** | Product Category Form | ModelAdmin | ModelAdmin Add/Change | Name, Code, Description, Parent Category, Is Active | — |
| **M8-02** | Product Form | ModelAdmin | ModelAdmin Add/Change | SKU, Barcode, Name, Category, Description, Cost Price, Selling Price, Tax Rate, Unit of Measure, Reorder Level, Is Active | `StockBatchInline` |
| **M8-03** | Stock Item Form | ModelAdmin | ModelAdmin Add/Change | Product (FK), Branch (FK), Quantity On Hand, Reserved Quantity, Available Quantity (RO), Bin Location | — |
| **M8-04** | Stock Batch Form / Inline | `StockBatchInline` | TabularInline | Batch Number, Quantity, Expiry Date, Cost Price, Received Date | — |
| **M8-05** | Purchase Order Form | ModelAdmin | ModelAdmin Add/Change | PO Number (RO), Supplier (FK), Branch (FK), Order Date, Expected Delivery Date, Total Cost (RO), Status, Notes | `PurchaseOrderItemInline` |
| **M8-06** | Purchase Order Item Line | `PurchaseOrderItemInline` | TabularInline | Product (FK), Quantity Ordered, Quantity Received, Unit Cost, Total Cost (RO) | — |
| **M8-07** | Goods Received Note | ModelAdmin | ModelAdmin Add/Change | GRN Number (RO), Purchase Order (FK), Supplier, Branch, Received Date, Delivery Note Number, Status | `GoodsReceivedItemInline` |
| **M8-08** | Stock Transfer Form | ModelAdmin | ModelAdmin Add/Change | Transfer Number (RO), Source Branch, Destination Branch, Dispatch Date, Receive Date, Status, Tracking Notes | `StockTransferItemInline` |
| **M8-09** | Stock Adjustment Form | ModelAdmin | ModelAdmin Add/Change | Adjustment Number (RO), Branch, Reason (Select), Status, Total Variance Value, Approved By | `StockAdjustmentItemInline` |
| **M8-10** | Stock Count / Audit Form | ModelAdmin | ModelAdmin Add/Change | Count Number (RO), Branch, Scheduled Date, Count Status, Total Discrepancy Value, Reconciled By | `StockCountItemInline` |
| **M8-11** | Cashier Shift Form | ModelAdmin | ModelAdmin Add/Change | Cashier (FK), Branch, Opening Float (ETB), Closing Cash Counted, Expected Cash, Discrepancy, Start Time, End Time, Status | — |
| **M8-12** | POS Checkout Terminal | `pos_checkout.html` | Interactive POS App | Barcode Scanner Input, Search Product, Quantity Controls, Cart Table, Member Selector, Payment Modal (Cash/Bank/Card), Receipt Printer Dialog | — |
| **M8-13** | Sale Record & Return | `SaleAdmin`, `SaleReturnAdmin` | ModelAdmin Add/Change | Sale Number, Customer/Member, Branch, Subtotal, Discount, Tax, Total, Status | `SaleItemInline`, `SalePaymentInline`, `SaleReturnItemInline` |

---

### Module 9: Notifications and Communication
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M9-01** | Provider Config Form | ModelAdmin | ModelAdmin Add/Change | Name, Provider Type (SMS/Email/Push), API URL, API Key, Sender ID, Cost Per SMS (ETB), Branch Scope, Is Default, Is Active | — |
| **M9-02** | Message Template Form | ModelAdmin | ModelAdmin Add/Change | Code, Name, Category, Channel, Language, Subject, Body Textarea with placeholder chips, Branch, Is Active | — |
| **M9-03** | Send SMS Form | `send_sms.html` | Custom Standalone Form | Recipient Type (Member/Custom Phone), Recipient Lookup, Template Select, Message Body Textarea, Character Counter, Send Now / Schedule | — |
| **M9-04** | Bulk Messaging Campaign | `bulk_campaign.html` | Custom Standalone Form | Campaign Title, Target Audience (All / Active Members / Expiring / Specific Branch), Channel, Message Template, Body, Schedule Date/Time, Safety Confirmation Checkbox | — |
| **M9-05** | Automation Rule Form | ModelAdmin | ModelAdmin Add/Change | Name, Trigger Event (Registration / Expiring / Birthday), Days Offset, Template (FK), Channel, Branch, Is Active | — |
| **M9-06** | Notification Preference | ModelAdmin | ModelAdmin Add/Change | Member (FK) / User (FK), Allow SMS, Allow Email, Allow In-App, Opt Out Marketing | — |

---

### Module 10: Facilities, Lockers and Equipment
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M10-01** | Locker Zone Form | ModelAdmin | ModelAdmin Add/Change | Name, Code, Branch (FK), Gender Restriction, Location Description, Is Active | `LockerInline` |
| **M10-02** | Locker Form / Inline | `LockerInline` & ModelAdmin | TabularInline & Add/Change | Locker Number, Zone, Branch, Locker Type, Lock Type, Daily Fee (ETB), Monthly Fee (ETB), Deposit Amount, Status (RO), Is Active | — |
| **M10-03** | Locker Assignment Form | ModelAdmin | ModelAdmin Add/Change | Assignment Number (RO), Member (FK), Locker (FK), Start Date, End Date, Rental Frequency, Fee Paid, Deposit Paid, Key Card RFID, Status | `LockerIncidentInline` |
| **M10-04** | Locker Incident / Lost Key | `LockerIncidentInline` | TabularInline | Incident Number, Incident Type (Lost Key / Damage), Penalty Fee (ETB), Is Settled, Incident Date | — |
| **M10-05** | Equipment Category Form | ModelAdmin | ModelAdmin Add/Change | Name, Code, Description, Maintenance Interval Days, Is Active | — |
| **M10-06** | Facility Area Form | ModelAdmin | ModelAdmin Add/Change | Name, Code, Branch, Floor Level, Square Meters, Max Occupancy, Is Active | — |
| **M10-07** | Equipment Form | ModelAdmin | ModelAdmin Add/Change | Equipment Code, Serial Number, Name, Category, Branch, Facility Area, Purchase Date, Purchase Price, Warranty Expiry, Status, Next Maintenance Date | — |
| **M10-08** | Maintenance Schedule Form | ModelAdmin | ModelAdmin Add/Change | Equipment (FK), Title, Interval Frequency, Assigned Technician / Vendor, Estimated Cost, Next Due Date, Is Active | — |
| **M10-09** | Fault Report Form | ModelAdmin | ModelAdmin Add/Change | Equipment (FK), Reported By, Severity (Low/Medium/High/Critical), Description, Reported Date, Status | — |
| **M10-10** | Maintenance Work Order | ModelAdmin | ModelAdmin Add/Change | Work Order Number (RO), Equipment, Fault Report, Contractor / Supplier, Cost (ETB), Start Date, Completion Date, Status, Post to Finance Action | — |
| **M10-11** | Cleaning Task & Inspection | ModelAdmin | ModelAdmin Add/Change | Facility Area, Task Name, Scheduled Time, Assigned Staff, Inspector, Score, Status | — |
| **M10-12** | Incident Report Form | ModelAdmin | ModelAdmin Add/Change | Incident Number (RO), Branch, Facility Area, Member Involved, Staff Reporter, Severity, Detailed Description, Action Taken, Police / Medical Contacted | — |

---

### Module 11: Administration and Settings
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M11-01** | Gym Profile Form | ModelAdmin | ModelAdmin Change | Legal Name, Commercial Reg Number, Tax ID, Phone, Email, Website, Address, Logo Upload, Favicon Upload, Default Currency, Default Timezone, Language, Receipt Header/Footer, Invoice Terms, Min Age, Photo/Emergency Required Toggles | — |
| **M11-02** | Branch Management Form | ModelAdmin | ModelAdmin Add/Change | Code, Name, Description, Manager (FK), Phone, Email, Address, City, Opening Date, Is Active | — |
| **M11-03** | Branch Settings Form | ModelAdmin | ModelAdmin Add/Change | Operating Hours (Mon-Sun Open/Close), Max Capacity, Check-in Grace Period, QR Check-in Allowed Toggle, Manual Check-in Allowed Toggle | — |
| **M11-04** | Gym Policy Form | ModelAdmin | ModelAdmin Add/Change | Policy Type, Title, Version, Content Textarea, Effective Date, Mandatory Acceptance Toggle, Is Active | — |
| **M11-05** | Feature Flag Form | ModelAdmin | ModelAdmin Add/Change | Code, Name, Description, Is Enabled Globally, Enabled Branches (Multi-Select), Rollout Percentage | — |
| **M11-06** | Backup Configuration Form | ModelAdmin | ModelAdmin Add/Change | Backup Destination, Retention Days, Storage Bucket, Schedule Cron, Encrypt Backups Toggle, Last Backup Status (RO) | — |
| **M11-07** | Data Management / Import | `data_management.html` | Custom Standalone Form | Import Target (Members / Inventory / Catalog), File Upload (CSV), Overwrite Existing Checkbox, Dry Run Checkbox | — |
| **M11-08** | Data Export Form | `data_management.html` | Custom Standalone Form | Export Model (Members / Financials / Sales), Date Range, Branch Scope, Export Format (CSV / JSON) | — |
| **M11-09** | Switch Branch Form | `branch_selector.html` | Global Topbar Form | Target Branch (Select Dropdown), Submit Token | — |

---

### Module 12: Technical Administration
| ID | Form / Model | Form Class / Template | Type | Primary Fields / Controls | Inline Formsets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M12-01** | Technical User Add Form | `AccountCreationForm` | ModelAdmin Add | Email, First Name, Last Name, Password, Confirm Password | — |
| **M12-02** | Technical User Change Form | `AccountChangeForm` | ModelAdmin Change | Email, First Name, Last Name, Phone, Password Reset Link, Is Active, Is Staff, Is Superuser, Groups (FilterHorizontal), Permissions (FilterHorizontal) | — |
| **M12-03** | Admin Password Change | `AdminPasswordChangeForm` | Unfold Password Form | Old Password, New Password, Confirm New Password | — |
| **M12-04** | Technical Group Form | `GroupAdmin` | ModelAdmin Add/Change | Group Name, Permissions Multi-Select Dual Listbox | — |
| **M12-05** | Business User (StaffAccount) | `StaffCreationForm` / `StaffChangeForm` | ModelAdmin Add/Change | Email, First Name, Last Name, Phone, Password, Role (Select: Owner/Manager/Receptionist/Accountant/Trainer/POS) | — |
| **M12-06** | User Branch Access Form | ModelAdmin | ModelAdmin Add/Change | User (FK), Branch (FK), Is Active Toggle, Is Primary Branch Toggle | — |
| **M12-07** | Report Preset Form | ModelAdmin | ModelAdmin Add/Change | Preset Name, Report Type, Filter Criteria JSON, Date Preset, Branch Scope | — |
| **M12-08** | Audit Event Viewer | ModelAdmin | ModelAdmin View/RO | Actor, Action, Target Model, Target ID, Timestamp, Changes JSON, Branch Code | — |

---

## 3. Form Architecture Patterns Observed

1. **Django Unfold ModelAdmin Forms:**
   Standard change forms rely on `unfold/change_form.html`, which delegates to `admin/includes/fieldset.html`, `unfold/helpers/fieldset_row.html`, and `admin/submit_line.html`.
2. **Tabular & Stacked Inlines:**
   Inline formsets (`TabularInline` and `StackedInline`) are rendered sequentially below fieldsets in the change-form template.
3. **Custom Action & Standalone Views:**
   Custom views extend `admin/base_site.html` and define distinct panels using `.erp-panel`, `.erp-overline`, standard inputs, and action buttons.
4. **Fieldset Row Structure:**
   Two-column tuple definitions in ModelAdmin fieldsets (e.g. `("first_name", "last_name")`) map to grid flex-rows in Unfold.

This inventory provides the baseline for Phase B (Design System Tokens) and Phase C through H (Layouts, Inlines, Controls, Actions, and Feedback).
