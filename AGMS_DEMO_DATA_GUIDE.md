# AGMS Demo Data Guide: Realistic Gym Operational Environment

**System:** Antigravity Gym Management System (AGMS)  
**Technology:** Python 5.2 / Django, PostgreSQL 17, Django Unfold Admin  
**Command:** `python manage.py seed_gym_demo_data`  
**Location:** [`apps/core/management/commands/seed_gym_demo_data.py`](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/core/management/commands/seed_gym_demo_data.py)  
**Status:** Tested & Verified Idempotent on Localhost  

---

## 1. Overview & Purpose

The `seed_gym_demo_data` management command populates a comprehensive, realistic, and fully operational multi-branch gym dataset into the local PostgreSQL database. It is engineered specifically for localhost End-to-End (E2E) workflow testing, User Acceptance Testing (UAT), and stakeholder demonstrations.

### Key Guarantees
1. **Strict Idempotency:** The command uses `get_or_create` with deterministic lookups (`email`, `username`, `phone_number`, `code`, `name`, `idempotency_key`, `product_number`). Re-running the command safely returns existing records without raising uniqueness violations or duplicating entities.
2. **Zero Production Risk:** No real customer personal data is included. All phone numbers follow standardized Ethiopian format prefixes (`+251911...`), and all email addresses use the reserved `.test` TLD (`@example.test`).
3. **Data Integrity:** All foreign key relationships adhere to tenant and branch boundaries, financial ledger integrity rules (valid decimal math, non-negative amounts, unique payment idempotency keys), and staff job profiles.

---

## 2. Command Execution

To seed or refresh demo data, run:

```powershell
# Using the project virtual environment
.venv\Scripts\python.exe manage.py seed_gym_demo_data
```

### Options
- `--members <count>`: Number of fictional members to generate (default: `60`, supports `10` to `200`).
- `-v {0,1,2,3}`: Standard Django verbosity control.

Example run:
```powershell
.venv\Scripts\python.exe manage.py seed_gym_demo_data --members 75 -v 2
```

---

## 3. Seeded Entities & Architecture

| Entity Domain | Model(s) | Count | Details |
|---|---|---|---|
| **Organization** | `GymProfile` | 1 | Global singleton: *Antigravity Gym HQ* |
| **Branches** | `Branch` | 2 | `ADD-1` (Addis Main, Bole Atlas) & `BOLE` (Bole Express, Mega Building) |
| **Staff & Roles** | `User`, `StaffProfile`, `TrainerProfile`, `UserBranchAccess` | 14 | 1 Owner, 2 Branch Managers, 2 Receptionists, 2 Accountants, 4 Trainers, 3 POS Staff |
| **Membership Packages** | `MembershipPackage`, `PackageBranch` | 5 | Monthly Standard, Annual VIP, Student Pass, Cross-Fit Specialty, Multi-Branch Access Pass |
| **Members** | `Member`, `MemberEmergencyContact` | 60 | Realistic Ethiopian names, active contacts, emergency contacts, profile photos |
| **Memberships** | `Membership`, `MembershipFreeze` | 60 | 35 Active, 10 Expired, 8 Frozen (with approved freeze records), 7 Pending |
| **Billing & Payments** | `Invoice`, `InvoiceLineItem`, `Payment`, `PaymentAllocation`, `Receipt` | 60 | Invoices with PAID, PARTIALLY_PAID, PENDING statuses; unique idempotency keys |
| **Attendance** | `Attendance`, `MemberQRCredential` | 105 | Recent check-ins across Addis & Bole branches; QR tokens with rotating hashes |
| **Classes & Schedules** | `ClassType`, `ClassSchedule`, `ClassSession`, `ClassBooking` | 8 sessions, 35 bookings | Morning Spin, Evening HIIT, Strength & Conditioning, Vinyasa Yoga |
| **Facilities & Lockers** | `LockerZone`, `Locker`, `LockerAssignment` | 30 lockers, 15 assignments | Men's & Women's locker rooms with deposits and active assignments |
| **Equipment & Maintenance**| `EquipmentCategory`, `Equipment`, `MaintenanceSchedule`, `MaintenanceWorkOrder` | 12 machines, 4 work orders | Treadmills, Squat Racks, Cable Towers, Dumbbell Sets with preventive maintenance |
| **Inventory & POS** | `Category`, `Product`, `StockItem`, `CashierShift`, `Sale`, `SaleItem` | 3 products, 50-100 units/branch, 6 sales | Whey Protein, BCAA Recovery, Shaker Bottle with active cashier shifts |
| **Operational Finance** | `ExpenseCategory`, `Expense`, `ExpensePayment` | 4 expenses | Utilities, Maintenance Supplies, Marketing, Cleaning Services |

---

## 4. Demo Staff Accounts & Credentials

All demo accounts share the standard development password:
**`DemoPassword2026!`**

| Role | Username / Email | Branch Access | Scope & Capabilities |
|---|---|---|---|
| **Gym Owner** | `demo_owner@example.test` | Global (All Branches) | Superuser / Owner access; enterprise dashboard, global financial reports, all branch settings. |
| **Branch Manager (Addis)** | `demo_manager_addis@example.test` | Addis Main (`ADD-1`) | Branch manager for Addis; member registration, staff oversight, class scheduling, expense requests. |
| **Branch Manager (Bole)** | `demo_manager_bole@example.test` | Bole Express (`BOLE`) | Branch manager for Bole; branch-isolated operations. Cannot access Addis records. |
| **Receptionist (Addis)** | `demo_receptionist_addis@example.test` | Addis Main (`ADD-1`) | Member check-in, member onboarding, QR verification, locker assignments. |
| **Receptionist (Bole)** | `demo_receptionist_bole@example.test` | Bole Express (`BOLE`) | Front desk reception for Bole Express. |
| **Accountant (Central)** | `demo_accountant@example.test` | Both Branches | Invoices, payments, receipts, expense approvals, financial period reconciliation. |
| **Trainer (Senior Lead)** | `demo_trainer_1@example.test` | Addis Main (`ADD-1`) | Class instruction, session attendance tracking, assigned member fitness plans. |
| **Trainer (Cross-Fit Lead)**| `demo_trainer_2@example.test` | Bole Express (`BOLE`) | Class instructor for Bole Express. |
| **POS Cashier (Addis)** | `demo_pos_addis@example.test` | Addis Main (`ADD-1`) | Retail sales, shift opening/closing, stock inspection, receipt issuance. |

---

## 5. Branch Isolation & Multi-Branch Architecture

The AGMS multi-branch model uses `UserBranchAccess` and `TenantModel` / `BranchQuerySet` filters:
1. **Addis Main Branch (`ADD-1`):** Primary full-service facility located at Bole Atlas, Addis Ababa.
2. **Bole Express Branch (`BOLE`):** Satellite fitness club located at Mega Building, Bole Road.
3. **Cross-Branch Verification:**
   - Single-branch members attempting to check in at unauthorized branches are blocked with an explicit cross-branch access denial policy.
   - Branch Managers and front-desk staff can only view and manage records associated with their assigned branch.
   - The Gym Owner and Central Accountant have authorized multi-branch visibility.

---

## 6. Safe Reset & Environment Isolation

### Anti-Pattern: Never Direct-Delete Financial or Operational Records
Direct cascade deletion of members, invoices, payments, or attendance logs (`.delete()`) violates financial auditability and ledger immutability constraints. In AGMS, posted financial transactions and receipts are protected by foreign key cascades and audit integrity rules.

### Safe Reset Strategy: Isolated Disposable Demo Databases
When a clean slate is required for demonstration or exploratory testing, **always use an isolated disposable PostgreSQL database** rather than issuing destructive deletes against a shared or persistent environment:

```powershell
# 1. Create a dedicated disposable demo database
psql -U postgres -h 127.0.0.1 -p 55432 -c "CREATE DATABASE gym_demo_disposable OWNER gym_local;"

# 2. Run migrations in the disposable database
$env:POSTGRES_DB="gym_demo_disposable"
.venv\Scripts\python.exe manage.py migrate

# 3. Seed realistic demo data into the disposable database
.venv\Scripts\python.exe manage.py seed_gym_demo_data

# 4. When testing is finished, drop the entire disposable database cleanly
psql -U postgres -h 127.0.0.1 -p 55432 -c "DROP DATABASE gym_demo_disposable;"
```

This guarantees 100% isolation, prevents accidental loss of development or pre-production data, and preserves complete financial ledger integrity.
