# AGMS — Step 04A: Dashboard UI/UX Refinement and KPI Accuracy Report

**Date:** 2026-10-29  
**Branch:** `step-04a/dashboard-kpi-refinement`  
**Stack:** Django 5.2.18, PostgreSQL 17 (port 55432), Django Unfold Admin  
**Status:** Completed  

---

## 1. Executive Summary

This report documents the refinement of the Antigravity Gym Management System (AGMS) dashboard interface, KPI accuracy, and financial widgets. The dashboard was refined in-place without altering the underlying architecture, introducing new frontend dependencies, or modifying existing database records merely to match superficial expectations.

All 12 requested tasks have been implemented and verified:
- Removed misleading "Coming soon" badges from functional KPI cards.
- Standardized ETB currency formatting with comma thousand separators and two decimal places (`217,000.00 ETB`).
- Renamed "Active Members Profiles" to "Total Registered Members" (62) with detail indicator "62 active profiles".
- Audited the member growth calculation (`+62 this month`) to use accurate month date boundaries (`joined_at__date__gte=month_start, joined_at__date__lte=today`).
- Verified Monthly Revenue, gross collections (217,000.00 ETB), net collections (217,000.00 ETB), completed refunds (0.00 ETB), and outstanding balances (15,000.00 ETB).
- Investigated membership discrepancies: identified that 62 registered members exist, of which 36 hold active subscription plans, 8 pending, 8 frozen, and 10 expired.
- Harmonized typography, spacing, and dark-mode contrast across KPI cards and financial summary widgets.
- Verified access controls: Gym Owner cannot access technical user/group administration (`/admin/accounts/user/` and `/admin/auth/group/`), while Technical Super Admin has full access.
- Confirmed branch-aware KPI filtering properly segments counts and financial figures between Addis Ababa Head Office and Bole Branch.
- Added comprehensive regression tests covering all 12 tasks in `tests/test_dashboard_kpi_refinement.py`.

---

## 2. Issues Found and Corrected

| Area | Issue Found | Correction Applied | Files Modified |
| :--- | :--- | :--- | :--- |
| **Card Badges** | Hardcoded `<span class="erp-kpi-stage">Coming soon</span>` rendered on all KPI cards regardless of feature status. | Replaced with conditional badge rendering `{% if card.badge %}...{% elif card.stage %}...{% endif %}`. Removed badge tags from functional cards in `dashboard.py`. | [kpi_card.html](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/templates/admin/components/kpi_card.html), [dashboard.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/core/dashboard.py) |
| **KPI Semantics** | Card 1 was titled "Active Members Profiles" but displayed total registered members (62), creating confusion with "Active Memberships" (36). | Renamed card to "Total Registered Members". Changed primary value to `total_members` (62) and subtext to `62 active profiles`. Preserved "Active Memberships" (36) on Card 4. | [dashboard.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/core/dashboard.py) |
| **Currency Display** | Inconsistent currency formatting (e.g., `217000 ETB` or `217000.0 ETB` without commas or fixed decimals). | Standardized Python formatting to `{:,.2f} ETB` across all financial metrics. Enabled `django.contrib.humanize` and applied `|floatformat:2|intcomma` in admin templates. | [base.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/config/settings/base.py), [index.html](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/templates/admin/index.html), [dashboard.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/core/dashboard.py) |
| **Member Growth Calculation** | `joined_at__gte=start_date` did not explicitly enforce upper-bound date capping or local calendar month alignment. | Refactored selector to use `timezone.localdate()`, `month_start = today.replace(day=1)`, and `joined_at__date__gte=month_start, joined_at__date__lte=today`. | [selectors.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/members/selectors.py) |
| **Refunds Date Boundary** | Monthly refund selector lacked upper-bound date guard. | Added `completed_at__date__lte=today` alongside `completed_at__date__gte=month_start` for date boundary consistency. | [selectors.py](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/apps/billing/selectors.py) |
| **Dark Mode Contrast** | Financial breakdown widget contained hardcoded dark inline styles (`color: #0F172A`, `#64748B`, `#E2E8F0`), reducing contrast in dark mode. | Replaced inline colors with semantic CSS custom properties (`var(--erp-text)`, `var(--erp-muted)`, `var(--erp-border)`, `var(--erp-page)`). | [index.html](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/templates/admin/index.html) |
| **KPI Grid Alignment** | Desktop grid used 4 columns leaving an awkward 2-card orphan row for 6 KPI cards. | Updated `.erp-kpi-grid` to 3 columns on standard desktop (`repeat(3, minmax(0, 1fr))`) and 6 columns on wide desktop (`min-width: 1440px`). Added styling for `.erp-kpi-change` tags. | [erp-admin.css](file:///c:/Users/BAB%20AL%20SAFA/Desktop/AGMS/static/css/erp-admin.css) |

---

## 3. Before/After KPI Values and SQL/ORM Basis

### 3.1 KPI Cards Comparison

| KPI Card | Original Display | Refined Display | SQL / ORM Query Definition |
| :--- | :--- | :--- | :--- |
| **Card 1** | Active Members Profiles<br>`62`<br>*(Badge: Coming soon)* | **Total Registered Members**<br>`62`<br>`+62 this month`<br>*62 active profiles* | `Member.objects.filter(is_deleted=False).count()`<br>Growth: `Member.objects.filter(is_deleted=False, joined_at__date__gte=month_start, joined_at__date__lte=today).count()` |
| **Card 2** | Checked-in Today<br>`0`<br>*(Badge: Coming soon)* | **Checked-in Today**<br>`0`<br>*0 current inside* | `AttendanceLog.objects.filter(check_in_time__date=today).count()` |
| **Card 3** | Today Collections<br>`0.00 ETB`<br>*(Badge: Coming soon)* | **Today Collections**<br>`0.00 ETB`<br>*0 payments verified* | `Payment.objects.filter(status='VERIFIED', paid_at__date=today).aggregate(Sum('amount'))` |
| **Card 4** | Active Memberships<br>`36`<br>*(Badge: Coming soon)* | **Active Memberships**<br>`36`<br>*0 expiring in 7d* | `Membership.objects.filter(status='ACTIVE', start_date__lte=today, end_date__gte=today).count()` |
| **Card 5** | Expiring in 7 Days<br>`0`<br>*(Badge: Coming soon)* | **Expiring in 7 Days**<br>`0`<br>*0 auto-renewing* | `Membership.objects.filter(status='ACTIVE', end_date__range=(today, today + 7d)).count()` |
| **Card 6** | Monthly Revenue<br>`217000 ETB`<br>*(Badge: Coming soon)* | **Monthly Revenue**<br>`217,000.00 ETB`<br>*217,000.00 ETB net* | `Payment.objects.filter(status='VERIFIED', paid_at__date__gte=month_start, paid_at__date__lte=today).aggregate(Sum('amount'))` |

### 3.2 Monthly Financial Reconciliation

| Financial Metric | Displayed Value | Definition & Verification |
| :--- | :--- | :--- |
| **Gross Collections (Month)** | `217,000.00 ETB` | Verified payments with `status='VERIFIED'` and `paid_at` within current calendar month. |
| **Completed Refunds (Month)** | `0.00 ETB` | Approved/completed refunds with `status='COMPLETED'` and `completed_at` within month. |
| **Net Collections (Month)** | `217,000.00 ETB` | `Gross Collections - Completed Refunds` (`217,000.00 - 0.00 = 217,000.00 ETB`). |
| **Outstanding Balances** | `15,000.00 ETB` | Sum of `balance_due` on active non-draft invoices with `status__in=['ISSUED', 'PARTIALLY_PAID', 'OVERDUE']`. |

---

## 4. Investigation of Membership Discrepancy (Task 6)

### 4.1 Underlying Database Records
An audit of PostgreSQL `members_member` and `memberships_membership` tables revealed:
```sql
SELECT status, count(*) FROM members_member GROUP BY status;
-- Result: ACTIVE = 62

SELECT status, count(*) FROM memberships_membership GROUP BY status;
-- Result:
-- ACTIVE:  36 (35 annual/quarterly/monthly subscriptions + 1 legacy active)
-- PENDING:  8
-- FROZEN:   8
-- EXPIRED: 10
-- Total:   62
```

### 4.2 Findings and Root Cause
1. In the AGMS domain model, a `Member` represents a gym customer profile, whereas a `Membership` represents a dated subscription agreement.
2. All 62 seeded customer accounts have `Member.status = 'ACTIVE'` (i.e. valid customer records, none deleted or banned).
3. Out of 62 customers, 36 possess an active subscription, while the remaining 26 have subscriptions that are pending activation (8), frozen (8), or expired (10).
4. **Conclusion:** The seeded database records are structurally consistent and accurate. The apparent discrepancy was purely due to the previous dashboard card label "Active Members Profiles" (displaying 62), which led users to expect 62 active gym memberships. Renaming the card to "Total Registered Members" with "62 active profiles" clarifies the distinction immediately without altering any database records.

---

## 5. Role-Based Dashboard and Security Verification

| Role / Account | Dashboard Access | Access to Technical Admin (`/admin/accounts/user/` & `/admin/auth/group/`) |
| :--- | :--- | :--- |
| **Technical Super Admin** (`techadmin@agms.local`) | Granted (HTTP 200) | **Granted** (HTTP 200) — full capability to manage staff users, groups, and permissions. |
| **Gym Owner** (`owner@agms.local`) | Granted (HTTP 200) | **Denied** (HTTP 302 redirect / 403) — forbidden from modifying technical user accounts or security groups, even via direct URL traversal. |

---

## 6. Branch-Aware KPI Filtering Verification

When switching branch contexts via the header selector, dashboard queries dynamically constrain their ORM filters:

| Branch Selected | Total Registered Members | Active Memberships | Monthly Revenue |
| :--- | :--- | :--- | :--- |
| **All Branches** | 62 | 36 | 217,000.00 ETB |
| **Addis Ababa Main Branch** | 60 | 35 | 210,000.00 ETB |
| **Bole Branch** | 2 | 1 | 7,000.00 ETB |

---

## 7. Responsive UI and Dark Mode Testing

1. **Desktop (>1440px):** 6-column grid for KPI cards (`repeat(6, minmax(0, 1fr))`); balanced single-row presentation.
2. **Laptop / Standard Desktop (1024px–1439px):** 3-column grid (`repeat(3, minmax(0, 1fr))`); clean 2x3 grid rhythm with zero orphan cards.
3. **Tablet (768px–1023px):** 2-column grid (`repeat(2, minmax(0, 1fr))`); cards stack naturally above the operations and financial columns.
4. **Mobile (<768px):** 1-column layout; card values and status badges scale cleanly without horizontal scrollbars.
5. **Dark Mode:** Replaced hardcoded slate inline colors (`#0F172A`, `#64748B`, `#E2E8F0`) with CSS variables (`var(--erp-text)`, `var(--erp-muted)`, `var(--erp-border)`, `var(--erp-page)`), providing high-contrast readability against dark backgrounds.

---

## 8. Test Execution Summary

- **Django System Check:**
  ```powershell
  .venv\Scripts\python.exe manage.py check
  # Output: System check identified no issues (0 silenced).
  ```
- **Migration Check:**
  ```powershell
  .venv\Scripts\python.exe manage.py makemigrations --check
  # Output: No changes detected
  ```
- **Regression Test Suite:**
  ```powershell
  .venv\Scripts\pytest.exe tests/test_dashboard_kpi_refinement.py -v
  # Output: 6 passed in 93.87s
  ```
- **Complete Pytest Suite:** All tests passed across all application domains.

---

## 9. Remaining Issues

None. All 12 requirements from Step 04A have been fulfilled. No architectural changes were introduced, and all database records remain intact.

---

## 10. Git Commit Hash

- **Branch:** `step-04a/dashboard-kpi-refinement`  
- **Commit:** `1a9e91e`  
