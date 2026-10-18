from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.analytics.dates import DateFilterRange, get_gym_timezone, get_today_in_gym_tz
from apps.attendance.models import AccessAttempt, Attendance
from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipPackage, MembershipRenewal


@dataclass
class ReportColumn:
    key: str
    label: str
    align: str = "left"  # left, center, right
    is_currency: bool = False
    is_date: bool = False


@dataclass
class ReportDefinition:
    code: str
    title: str
    category: str  # financial, memberships, attendance
    description: str
    columns: List[ReportColumn]
    extractor: Callable[[Any, DateFilterRange], List[Dict[str, Any]]]
    allowed_roles: List[str]


# =========================================================================
# FINANCIAL EXTRACTORS (7 Reports)
# =========================================================================

def extract_payment_collections(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    payments = (
        Payment.objects.filter(
            branch__in=branch_qs,
            status=Payment.Status.VERIFIED,
            paid_at__gte=date_range.start_datetime,
            paid_at__lte=date_range.end_datetime,
        )
        .select_related("invoice", "invoice__member", "branch", "payment_method", "verified_by")
        .order_by("-paid_at")
    )
    rows = []
    for p in payments:
        dt = p.paid_at.astimezone(tz) if p.paid_at else None
        member_name = p.invoice.member.full_name if p.invoice and p.invoice.member else "N/A"
        member_num = p.invoice.member.member_number if p.invoice and p.invoice.member else "N/A"
        rows.append({
            "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A",
            "payment_number": p.payment_number,
            "invoice_number": p.invoice.invoice_number if p.invoice else "N/A",
            "member": f"{member_name} ({member_num})",
            "branch": p.branch.name if p.branch else "N/A",
            "method": p.payment_method.name if p.payment_method else "N/A",
            "reference": p.reference_number or "-",
            "amount": p.amount,
            "verified_by": p.verified_by.email if p.verified_by else "System",
        })
    return rows


def extract_invoice_register(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    invoices = (
        Invoice.objects.filter(
            branch__in=branch_qs,
            issue_date__gte=date_range.start_date,
            issue_date__lte=date_range.end_date,
        )
        .exclude(status=Invoice.Status.VOID)
        .select_related("member", "branch")
        .order_by("-issue_date", "-created_at")
    )
    rows = []
    for inv in invoices:
        rows.append({
            "invoice_number": inv.invoice_number,
            "issue_date": inv.issue_date.strftime("%Y-%m-%d"),
            "due_date": inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "-",
            "member": f"{inv.member.full_name} ({inv.member.member_number})",
            "branch": inv.branch.name if inv.branch else "-",
            "total_amount": inv.total_amount,
            "amount_paid": inv.amount_paid,
            "balance_due": inv.balance_due,
            "status": inv.get_status_display(),
        })
    return rows


def extract_outstanding_balances(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    today = get_today_in_gym_tz()
    invoices = (
        Invoice.objects.filter(
            branch__in=branch_qs,
            status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        )
        .select_related("member", "branch")
        .order_by("due_date", "-balance_due")
    )
    rows = []
    for inv in invoices:
        due = inv.due_date or inv.issue_date
        days_overdue = max(0, (today - due).days)
        rows.append({
            "invoice_number": inv.invoice_number,
            "member": f"{inv.member.full_name} ({inv.member.member_number})",
            "phone": inv.member.phone or "-",
            "branch": inv.branch.name if inv.branch else "-",
            "issue_date": inv.issue_date.strftime("%Y-%m-%d"),
            "due_date": due.strftime("%Y-%m-%d"),
            "days_overdue": days_overdue,
            "total_amount": inv.total_amount,
            "balance_due": inv.balance_due,
        })
    return rows


def extract_refund_report(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    refunds = (
        Refund.objects.filter(
            invoice__branch__in=branch_qs,
            status=Refund.Status.COMPLETED,
            completed_at__gte=date_range.start_datetime,
            completed_at__lte=date_range.end_datetime,
        )
        .select_related("invoice", "invoice__member", "invoice__branch", "processed_by")
        .order_by("-completed_at")
    )
    rows = []
    for r in refunds:
        dt = r.completed_at.astimezone(tz) if r.completed_at else None
        member = r.invoice.member if r.invoice else None
        rows.append({
            "date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A",
            "refund_number": r.refund_number,
            "invoice_number": r.invoice.invoice_number if r.invoice else "-",
            "member": f"{member.full_name} ({member.member_number})" if member else "-",
            "branch": r.invoice.branch.name if r.invoice and r.invoice.branch else "-",
            "reason": r.reason or "-",
            "amount": r.amount,
            "processed_by": r.processed_by.email if r.processed_by else "System",
        })
    return rows


def extract_payment_methods(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    payments = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__gte=date_range.start_datetime,
        paid_at__lte=date_range.end_datetime,
    )
    total_all = payments.aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    grouped = (
        payments.values("payment_method__name", "payment_method__code")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    rows = []
    for g in grouped:
        amt = g["total"] or Decimal("0.00")
        pct = (amt / total_all * 100) if total_all > Decimal("0.00") else Decimal("0.00")
        rows.append({
            "method_name": g["payment_method__name"] or "Unknown",
            "method_code": g["payment_method__code"] or "-",
            "transaction_count": g["count"],
            "total_collected": amt,
            "share_percentage": f"{pct:.1f}%",
        })
    return rows


def extract_branch_collections(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    payments = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__gte=date_range.start_datetime,
        paid_at__lte=date_range.end_datetime,
    )
    refunds = Refund.objects.filter(
        invoice__branch__in=branch_qs,
        status=Refund.Status.COMPLETED,
        completed_at__gte=date_range.start_datetime,
        completed_at__lte=date_range.end_datetime,
    )

    pay_map = {
        row["branch__id"]: row
        for row in payments.values("branch__id", "branch__name", "branch__code")
        .annotate(gross=Sum("amount"), count=Count("id"))
    }
    ref_map = {
        row["invoice__branch__id"]: row["ref_total"]
        for row in refunds.values("invoice__branch__id")
        .annotate(ref_total=Sum("amount"))
    }

    rows = []
    for b in branch_qs:
        p_data = pay_map.get(b.pk, {"gross": Decimal("0.00"), "count": 0})
        gross = p_data["gross"] or Decimal("0.00")
        cnt = p_data["count"]
        ref = ref_map.get(b.pk, Decimal("0.00")) or Decimal("0.00")
        net = gross - ref
        rows.append({
            "branch_name": b.name,
            "branch_code": b.code,
            "transaction_count": cnt,
            "gross_collections": gross,
            "refunds_issued": ref,
            "net_collections": net,
        })
    rows.sort(key=lambda r: r["gross_collections"], reverse=True)
    return rows


def extract_daily_cash_summary(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    payments = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__gte=date_range.start_datetime,
        paid_at__lte=date_range.end_datetime,
    ).select_related("payment_method")

    from collections import defaultdict
    daily = defaultdict(lambda: {"cash": Decimal("0.00"), "bank": Decimal("0.00"), "mobile": Decimal("0.00"), "total": Decimal("0.00"), "count": 0})

    for p in payments:
        if p.paid_at:
            d = p.paid_at.astimezone(tz).date()
            amt = p.amount or Decimal("0.00")
            daily[d]["total"] += amt
            daily[d]["count"] += 1
            code = (p.payment_method.code if p.payment_method else "").upper()
            if code == "CASH":
                daily[d]["cash"] += amt
            elif "BANK" in code:
                daily[d]["bank"] += amt
            else:
                daily[d]["mobile"] += amt

    rows = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        info = daily[cur_d]
        rows.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "transaction_count": info["count"],
            "cash_amount": info["cash"],
            "bank_transfer_amount": info["bank"],
            "electronic_amount": info["mobile"],
            "daily_total": info["total"],
        })
        cur_d += timezone.timedelta(days=1)
    rows.reverse()
    return rows


# =========================================================================
# MEMBERSHIP EXTRACTORS (8 Reports)
# =========================================================================

def extract_member_registration(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    members = (
        Member.objects.filter(
            home_branch__in=branch_qs,
            joined_at__gte=date_range.start_datetime,
            joined_at__lte=date_range.end_datetime,
        )
        .select_related("home_branch")
        .order_by("-joined_at")
    )
    rows = []
    for m in members:
        dt = m.joined_at.astimezone(tz) if m.joined_at else None
        rows.append({
            "joined_date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A",
            "member_number": m.member_number,
            "name": m.full_name,
            "phone": m.phone or "-",
            "gender": m.get_gender_display(),
            "home_branch": m.home_branch.name if m.home_branch else "-",
            "status": m.get_status_display(),
        })
    return rows


def extract_active_memberships(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    today = get_today_in_gym_tz()
    memberships = (
        Membership.objects.filter(
            home_branch__in=branch_qs,
            status=Membership.Status.ACTIVE,
        )
        .select_related("member", "package", "home_branch")
        .order_by("end_date")
    )
    rows = []
    for ms in memberships:
        rem_days = max(0, (ms.end_date - today).days) if ms.end_date else 0
        rows.append({
            "membership_number": ms.membership_number,
            "member": f"{ms.member.full_name} ({ms.member.member_number})",
            "phone": ms.member.phone or "-",
            "package": ms.package.name if ms.package else "Custom",
            "branch": ms.home_branch.name if ms.home_branch else "-",
            "start_date": ms.start_date.strftime("%Y-%m-%d") if ms.start_date else "-",
            "end_date": ms.end_date.strftime("%Y-%m-%d") if ms.end_date else "-",
            "days_remaining": rem_days,
        })
    return rows


def extract_expired_memberships(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    today = get_today_in_gym_tz()
    memberships = (
        Membership.objects.filter(
            home_branch__in=branch_qs,
            status=Membership.Status.EXPIRED,
        )
        .select_related("member", "package", "home_branch")
        .order_by("-end_date")
    )
    rows = []
    for ms in memberships:
        days_ago = max(0, (today - ms.end_date).days) if ms.end_date else 0
        rows.append({
            "membership_number": ms.membership_number,
            "member": f"{ms.member.full_name} ({ms.member.member_number})",
            "phone": ms.member.phone or "-",
            "package": ms.package.name if ms.package else "-",
            "branch": ms.home_branch.name if ms.home_branch else "-",
            "expiry_date": ms.end_date.strftime("%Y-%m-%d") if ms.end_date else "-",
            "days_expired": days_ago,
        })
    return rows


def extract_frozen_memberships(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    memberships = (
        Membership.objects.filter(
            home_branch__in=branch_qs,
            status=Membership.Status.FROZEN,
        )
        .select_related("member", "package", "home_branch")
        .order_by("end_date")
    )
    rows = []
    for ms in memberships:
        active_freeze = ms.freezes.filter(status="ACTIVE").first() if hasattr(ms, "freezes") else None
        f_start = active_freeze.requested_start_date.strftime("%Y-%m-%d") if active_freeze and active_freeze.requested_start_date else "-"
        f_end = active_freeze.requested_end_date.strftime("%Y-%m-%d") if active_freeze and active_freeze.requested_end_date else "-"
        reason = active_freeze.reason if active_freeze else "Frozen"
        rows.append({
            "membership_number": ms.membership_number,
            "member": f"{ms.member.full_name} ({ms.member.member_number})",
            "package": ms.package.name if ms.package else "-",
            "branch": ms.home_branch.name if ms.home_branch else "-",
            "freeze_start": f_start,
            "freeze_end": f_end,
            "freeze_reason": reason,
        })
    return rows


def extract_membership_renewals(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    renewals = (
        MembershipRenewal.objects.filter(
            membership__home_branch__in=branch_qs,
            renewal_status=MembershipRenewal.RenewalStatus.COMPLETED,
            created_at__gte=date_range.start_datetime,
            created_at__lte=date_range.end_datetime,
        )
        .select_related("membership", "membership__member", "membership__package", "membership__home_branch", "approved_by")
        .order_by("-created_at")
    )
    rows = []
    for ren in renewals:
        dt = (ren.completed_at or ren.created_at).astimezone(tz)
        ms = ren.membership
        member = ms.member if ms else None
        rows.append({
            "renewal_date": dt.strftime("%Y-%m-%d %H:%M") if dt else "N/A",
            "membership_number": ms.membership_number if ms else "-",
            "member": f"{member.full_name} ({member.member_number})" if member else "-",
            "package": ms.package.name if ms and ms.package else "-",
            "branch": ms.home_branch.name if ms and ms.home_branch else "-",
            "new_end_date": ren.new_end_date.strftime("%Y-%m-%d") if ren.new_end_date else "-",
            "processed_by": ren.approved_by.get_full_name() if ren.approved_by else "System",
        })
    return rows


def extract_package_performance(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    packages = MembershipPackage.objects.all().order_by("name")
    active_m_counts = {
        row["package__id"]: row["cnt"]
        for row in Membership.objects.filter(home_branch__in=branch_qs, status=Membership.Status.ACTIVE)
        .values("package__id")
        .annotate(cnt=Count("id"))
    }
    range_m_counts = {
        row["package__id"]: row["cnt"]
        for row in Membership.objects.filter(
            home_branch__in=branch_qs,
            start_date__gte=date_range.start_date,
            start_date__lte=date_range.end_date,
        )
        .values("package__id")
        .annotate(cnt=Count("id"))
    }
    rows = []
    for pkg in packages:
        act = active_m_counts.get(pkg.pk, 0)
        rng = range_m_counts.get(pkg.pk, 0)
        price = pkg.price or Decimal("0.00")
        rows.append({
            "package_name": pkg.name,
            "package_code": pkg.code,
            "tier": pkg.get_tier_display(),
            "price": price,
            "active_subscriptions": act,
            "activated_in_period": rng,
            "estimated_value": price * act,
        })
    rows.sort(key=lambda r: r["active_subscriptions"], reverse=True)
    return rows


def extract_upcoming_expiries(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    today = get_today_in_gym_tz()
    thirty_days = today + timedelta(days=30)
    memberships = (
        Membership.objects.filter(
            home_branch__in=branch_qs,
            status=Membership.Status.ACTIVE,
            end_date__gt=today,
            end_date__lte=thirty_days,
        )
        .select_related("member", "package", "home_branch")
        .order_by("end_date")
    )
    rows = []
    for ms in memberships:
        days_left = (ms.end_date - today).days
        rows.append({
            "membership_number": ms.membership_number,
            "member": ms.member.full_name,
            "member_number": ms.member.member_number,
            "phone": ms.member.phone or "-",
            "package": ms.package.name if ms.package else "-",
            "branch": ms.home_branch.name if ms.home_branch else "-",
            "end_date": ms.end_date.strftime("%Y-%m-%d"),
            "days_left": days_left,
        })
    return rows


def extract_branch_memberships(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    rows = []
    for b in branch_qs:
        tot_members = Member.objects.filter(home_branch=b).count()
        act_sub = Membership.objects.filter(home_branch=b, status=Membership.Status.ACTIVE).count()
        exp_sub = Membership.objects.filter(home_branch=b, status=Membership.Status.EXPIRED).count()
        frz_sub = Membership.objects.filter(home_branch=b, status=Membership.Status.FROZEN).count()
        pnd_sub = Membership.objects.filter(home_branch=b, status=Membership.Status.PENDING).count()
        rows.append({
            "branch_name": b.name,
            "branch_code": b.code,
            "total_registered_members": tot_members,
            "active_subscriptions": act_sub,
            "expired_subscriptions": exp_sub,
            "frozen_subscriptions": frz_sub,
            "pending_subscriptions": pnd_sub,
        })
    rows.sort(key=lambda r: r["active_subscriptions"], reverse=True)
    return rows


# =========================================================================
# ATTENDANCE EXTRACTORS (7 Reports)
# =========================================================================

def extract_daily_attendance_summary(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    range_att = Attendance.objects.filter(
        branch__in=branch_qs,
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    from collections import defaultdict
    daily_stats = defaultdict(lambda: {"total": 0, "qr": 0, "manual": 0, "members": set()})

    for att in range_att.values("check_in_datetime", "check_in_method", "member_id"):
        dt = att["check_in_datetime"]
        if dt:
            d = dt.astimezone(tz).date()
            daily_stats[d]["total"] += 1
            daily_stats[d]["members"].add(att["member_id"])
            if att["check_in_method"] == Attendance.Method.QR:
                daily_stats[d]["qr"] += 1
            elif att["check_in_method"] == Attendance.Method.MANUAL:
                daily_stats[d]["manual"] += 1

    rows = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        info = daily_stats[cur_d]
        rows.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "total_entries": info["total"],
            "unique_visitors": len(info["members"]),
            "qr_entries": info["qr"],
            "manual_entries": info["manual"],
        })
        cur_d += timedelta(days=1)
    rows.reverse()
    return rows


def extract_attendance_by_branch(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    range_att = Attendance.objects.filter(
        branch__in=branch_qs,
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    stats = (
        range_att.values("branch__id", "branch__name", "branch__code")
        .annotate(
            total=Count("id"),
            unique_m=Count("member", distinct=True),
            qr_cnt=Count("id", filter=Q(check_in_method=Attendance.Method.QR)),
            man_cnt=Count("id", filter=Q(check_in_method=Attendance.Method.MANUAL)),
        )
        .order_by("-total")
    )
    rows = []
    for s in stats:
        rows.append({
            "branch_name": s["branch__name"],
            "branch_code": s["branch__code"],
            "total_entries": s["total"],
            "unique_visitors": s["unique_m"],
            "qr_entries": s["qr_cnt"],
            "manual_entries": s["man_cnt"],
        })
    return rows


def extract_qr_vs_manual(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    range_att = Attendance.objects.filter(
        branch__in=branch_qs,
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    total_all = range_att.count()
    grouped = (
        range_att.values("check_in_method")
        .annotate(total=Count("id"), unique_m=Count("member", distinct=True))
        .order_by("-total")
    )
    rows = []
    for g in grouped:
        meth = g["check_in_method"]
        tot = g["total"]
        pct = (tot / total_all * 100) if total_all > 0 else 0.0
        label = "QR Code Scan" if meth == Attendance.Method.QR else "Front-Desk Manual Search"
        rows.append({
            "method": label,
            "method_code": meth,
            "total_entries": tot,
            "unique_visitors": g["unique_m"],
            "share_percentage": f"{pct:.1f}%",
        })
    return rows


def extract_member_visit_history(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    records = (
        Attendance.objects.filter(
            branch__in=branch_qs,
            check_in_datetime__gte=date_range.start_datetime,
            check_in_datetime__lte=date_range.end_datetime,
        )
        .select_related("member", "branch")
        .order_by("-check_in_datetime")[:500]  # bounded query
    )
    rows = []
    for a in records:
        in_dt = a.check_in_datetime.astimezone(tz) if a.check_in_datetime else None
        out_dt = a.check_out_datetime.astimezone(tz) if a.check_out_datetime else None
        
        dur = "-"
        if a.check_out_datetime and a.check_in_datetime:
            mins = max(0, int((a.check_out_datetime - a.check_in_datetime).total_seconds() // 60))
            est = " (est.)" if a.is_estimated_duration else ""
            dur = f"{mins} min{est}"

        rows.append({
            "check_in_time": in_dt.strftime("%Y-%m-%d %H:%M") if in_dt else "-",
            "member": f"{a.member.full_name} ({a.member.member_number})",
            "branch": a.branch.name if a.branch else "-",
            "method": a.get_check_in_method_display(),
            "check_out_time": out_dt.strftime("%H:%M") if out_dt else "Inside",
            "duration": dur,
            "status": a.get_status_display(),
        })
    return rows


def extract_peak_hours(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    range_att = Attendance.objects.filter(
        branch__in=branch_qs,
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    hour_counts = {h: 0 for h in range(6, 23)}
    total = 0
    for dt in range_att.values_list("check_in_datetime", flat=True):
        if dt:
            h = dt.astimezone(tz).hour
            if h in hour_counts:
                hour_counts[h] += 1
                total += 1

    rows = []
    for h in sorted(hour_counts.keys()):
        cnt = hour_counts[h]
        pct = (cnt / total * 100) if total > 0 else 0.0
        rows.append({
            "hour_of_day": f"{h:02d}:00 – {h+1:02d}:00",
            "total_entries": cnt,
            "volume_percentage": f"{pct:.1f}%",
        })
    rows.sort(key=lambda r: r["total_entries"], reverse=True)
    return rows


def extract_access_denials(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    denials = (
        AccessAttempt.objects.filter(
            branch__in=branch_qs,
            timestamp__gte=date_range.start_datetime,
            timestamp__lte=date_range.end_datetime,
            result=AccessAttempt.Result.DENIED,
        )
        .select_related("branch", "member")
        .order_by("-timestamp")[:500]
    )
    rows = []
    for d in denials:
        dt = d.timestamp.astimezone(tz) if d.timestamp else None
        member_str = f"{d.member.full_name} ({d.member.member_number})" if d.member else "Unidentified"
        rows.append({
            "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "N/A",
            "branch": d.branch.name if d.branch else "-",
            "member": member_str,
            "reason_code": d.reason_code or "DENIED",
            "denial_reason": d.denial_reason or "-",
        })
    return rows


def extract_verified_vs_estimated(branch_qs, date_range: DateFilterRange) -> List[Dict]:
    tz = get_gym_timezone()
    att_qs = Attendance.objects.filter(
        branch__in=branch_qs,
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    from collections import defaultdict
    daily = defaultdict(lambda: {"verified_count": 0, "verified_total_mins": 0, "auto_closed_count": 0})

    for a in att_qs:
        if a.check_in_datetime:
            d = a.check_in_datetime.astimezone(tz).date()
            if a.status == Attendance.Status.COMPLETED and not a.is_estimated_duration and a.check_out_datetime:
                mins = max(0, int((a.check_out_datetime - a.check_in_datetime).total_seconds() // 60))
                daily[d]["verified_count"] += 1
                daily[d]["verified_total_mins"] += mins
            elif a.status == Attendance.Status.AUTO_CLOSED or a.is_estimated_duration:
                daily[d]["auto_closed_count"] += 1

    rows = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        info = daily[cur_d]
        v_cnt = info["verified_count"]
        v_avg = round(info["verified_total_mins"] / v_cnt) if v_cnt > 0 else 0
        rows.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "verified_checkouts": v_cnt,
            "avg_verified_duration": f"{v_avg} min" if v_cnt > 0 else "-",
            "auto_closed_estimated_sessions": info["auto_closed_count"],
        })
        cur_d += timedelta(days=1)
    rows.reverse()
    return rows


# =========================================================================
# MASTER REPORT REGISTRY DEFINITIONS (22 REPORTS)
# =========================================================================

REPORT_REGISTRY: Dict[str, ReportDefinition] = {
    # Financial (7)
    "payment_collections": ReportDefinition(
        code="payment_collections",
        title="Payment Collections Register",
        category="financial",
        description="Detailed record of all verified payment collections including payment method, transaction references, and verifying cashier.",
        columns=[
            ReportColumn(key="date", label="Date & Time", is_date=True),
            ReportColumn(key="payment_number", label="Payment #"),
            ReportColumn(key="invoice_number", label="Invoice #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="method", label="Payment Method"),
            ReportColumn(key="reference", label="Reference"),
            ReportColumn(key="amount", label="Amount (ETB)", align="right", is_currency=True),
            ReportColumn(key="verified_by", label="Verified By"),
        ],
        extractor=extract_payment_collections,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "invoice_register": ReportDefinition(
        code="invoice_register",
        title="Invoice Register",
        category="financial",
        description="Comprehensive audit of all member billing invoices, total billings, amounts collected, and remaining balances.",
        columns=[
            ReportColumn(key="invoice_number", label="Invoice #"),
            ReportColumn(key="issue_date", label="Issue Date", is_date=True),
            ReportColumn(key="due_date", label="Due Date", is_date=True),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="total_amount", label="Total (ETB)", align="right", is_currency=True),
            ReportColumn(key="amount_paid", label="Paid (ETB)", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Balance (ETB)", align="right", is_currency=True),
            ReportColumn(key="status", label="Status", align="center"),
        ],
        extractor=extract_invoice_register,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "outstanding_balances": ReportDefinition(
        code="outstanding_balances",
        title="Outstanding Balances & Aging Report",
        category="financial",
        description="Members with unpaid and overdue invoices with contact details and days overdue for collections follow-up.",
        columns=[
            ReportColumn(key="invoice_number", label="Invoice #"),
            ReportColumn(key="member", label="Member Name"),
            ReportColumn(key="phone", label="Phone"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="issue_date", label="Issue Date", is_date=True),
            ReportColumn(key="due_date", label="Due Date", is_date=True),
            ReportColumn(key="days_overdue", label="Days Past Due", align="right"),
            ReportColumn(key="total_amount", label="Total (ETB)", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Balance Due (ETB)", align="right", is_currency=True),
        ],
        extractor=extract_outstanding_balances,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "refund_report": ReportDefinition(
        code="refund_report",
        title="Refunds Register",
        category="financial",
        description="Detailed record of approved and completed customer refunds, reasons, and authorizers.",
        columns=[
            ReportColumn(key="date", label="Date Completed", is_date=True),
            ReportColumn(key="refund_number", label="Refund #"),
            ReportColumn(key="invoice_number", label="Invoice #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="reason", label="Reason"),
            ReportColumn(key="amount", label="Refunded (ETB)", align="right", is_currency=True),
            ReportColumn(key="processed_by", label="Processed By"),
        ],
        extractor=extract_refund_report,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "payment_methods": ReportDefinition(
        code="payment_methods",
        title="Payment Methods Distribution",
        category="financial",
        description="Summary of collection volumes and transaction counts broken down by payment gateway and tender type.",
        columns=[
            ReportColumn(key="method_name", label="Payment Method"),
            ReportColumn(key="method_code", label="Code", align="center"),
            ReportColumn(key="transaction_count", label="Transaction Count", align="right"),
            ReportColumn(key="total_collected", label="Total Collected (ETB)", align="right", is_currency=True),
            ReportColumn(key="share_percentage", label="Share %", align="right"),
        ],
        extractor=extract_payment_methods,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "branch_collections": ReportDefinition(
        code="branch_collections",
        title="Branch Collections Comparison",
        category="financial",
        description="Comparative financial performance across branches showing gross receipts, refund payouts, and net cash collections.",
        columns=[
            ReportColumn(key="branch_name", label="Branch Name"),
            ReportColumn(key="branch_code", label="Code", align="center"),
            ReportColumn(key="transaction_count", label="Transactions", align="right"),
            ReportColumn(key="gross_collections", label="Gross Collections (ETB)", align="right", is_currency=True),
            ReportColumn(key="refunds_issued", label="Refunds (ETB)", align="right", is_currency=True),
            ReportColumn(key="net_collections", label="Net Cash (ETB)", align="right", is_currency=True),
        ],
        extractor=extract_branch_collections,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "daily_cash": ReportDefinition(
        code="daily_cash",
        title="Daily Cash & Tender Summary",
        category="financial",
        description="Day-by-day cash drawer reconciliation separating physical cash, bank transfers, and electronic mobile payments.",
        columns=[
            ReportColumn(key="date", label="Date", is_date=True),
            ReportColumn(key="transaction_count", label="Transactions", align="right"),
            ReportColumn(key="cash_amount", label="Cash (ETB)", align="right", is_currency=True),
            ReportColumn(key="bank_transfer_amount", label="Bank Transfer (ETB)", align="right", is_currency=True),
            ReportColumn(key="electronic_amount", label="Telebirr / Mobile (ETB)", align="right", is_currency=True),
            ReportColumn(key="daily_total", label="Total Receipts (ETB)", align="right", is_currency=True),
        ],
        extractor=extract_daily_cash_summary,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),

    # Memberships (8)
    "member_registration": ReportDefinition(
        code="member_registration",
        title="Member Registrations Log",
        category="memberships",
        description="Log of new member profiles created within the selected date range.",
        columns=[
            ReportColumn(key="joined_date", label="Joined Date", is_date=True),
            ReportColumn(key="member_number", label="Member #"),
            ReportColumn(key="name", label="Full Name"),
            ReportColumn(key="phone", label="Phone"),
            ReportColumn(key="gender", label="Gender", align="center"),
            ReportColumn(key="home_branch", label="Home Branch"),
            ReportColumn(key="status", label="Profile Status", align="center"),
        ],
        extractor=extract_member_registration,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "active_memberships": ReportDefinition(
        code="active_memberships",
        title="Active Memberships Register",
        category="memberships",
        description="Active paid subscriptions with package details and remaining duration.",
        columns=[
            ReportColumn(key="membership_number", label="Subscription #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="phone", label="Phone"),
            ReportColumn(key="package", label="Package"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="start_date", label="Start Date", is_date=True),
            ReportColumn(key="end_date", label="Expiry Date", is_date=True),
            ReportColumn(key="days_remaining", label="Days Left", align="right"),
        ],
        extractor=extract_active_memberships,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "expired_memberships": ReportDefinition(
        code="expired_memberships",
        title="Expired Memberships Roster",
        category="memberships",
        description="Subscriptions that have lapsed and require renewal outreach.",
        columns=[
            ReportColumn(key="membership_number", label="Subscription #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="phone", label="Phone"),
            ReportColumn(key="package", label="Package"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="expiry_date", label="Expiry Date", is_date=True),
            ReportColumn(key="days_expired", label="Days Expired", align="right"),
        ],
        extractor=extract_expired_memberships,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "frozen_memberships": ReportDefinition(
        code="frozen_memberships",
        title="Frozen Memberships Register",
        category="memberships",
        description="Memberships currently on hold with freeze intervals and documented justifications.",
        columns=[
            ReportColumn(key="membership_number", label="Subscription #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="package", label="Package"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="freeze_start", label="Freeze Start", is_date=True),
            ReportColumn(key="freeze_end", label="Freeze End", is_date=True),
            ReportColumn(key="freeze_reason", label="Reason"),
        ],
        extractor=extract_frozen_memberships,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "membership_renewals": ReportDefinition(
        code="membership_renewals",
        title="Membership Renewals Log",
        category="memberships",
        description="Log of successful subscription renewals approved during the reporting period.",
        columns=[
            ReportColumn(key="renewal_date", label="Renewal Date", is_date=True),
            ReportColumn(key="membership_number", label="Subscription #"),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="package", label="Package"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="new_end_date", label="New Expiry Date", is_date=True),
            ReportColumn(key="processed_by", label="Processed By"),
        ],
        extractor=extract_membership_renewals,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "package_performance": ReportDefinition(
        code="package_performance",
        title="Package Performance & Popularity",
        category="memberships",
        description="Subscription counts, new enrollments, and estimated value generated per membership plan.",
        columns=[
            ReportColumn(key="package_name", label="Package Name"),
            ReportColumn(key="package_code", label="Code", align="center"),
            ReportColumn(key="tier", label="Tier", align="center"),
            ReportColumn(key="price", label="Price (ETB)", align="right", is_currency=True),
            ReportColumn(key="active_subscriptions", label="Active Base", align="right"),
            ReportColumn(key="activated_in_period", label="New in Period", align="right"),
            ReportColumn(key="estimated_value", label="Active Value (ETB)", align="right", is_currency=True),
        ],
        extractor=extract_package_performance,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "upcoming_expiries": ReportDefinition(
        code="upcoming_expiries",
        title="Upcoming Expiries Action List (Next 30 Days)",
        category="memberships",
        description="Active memberships nearing expiration for front-desk renewal reminders and sales calls.",
        columns=[
            ReportColumn(key="membership_number", label="Subscription #"),
            ReportColumn(key="member", label="Member Name"),
            ReportColumn(key="phone", label="Phone"),
            ReportColumn(key="package", label="Package"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="end_date", label="Expiry Date", is_date=True),
            ReportColumn(key="days_left", label="Days Remaining", align="right"),
        ],
        extractor=extract_upcoming_expiries,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "branch_memberships": ReportDefinition(
        code="branch_memberships",
        title="Branch Membership Roster Summary",
        category="memberships",
        description="Branch-level breakdown of total members, active subscriptions, and retention metrics.",
        columns=[
            ReportColumn(key="branch_name", label="Branch Name"),
            ReportColumn(key="branch_code", label="Code", align="center"),
            ReportColumn(key="total_registered_members", label="Registered Members", align="right"),
            ReportColumn(key="active_subscriptions", label="Active", align="right"),
            ReportColumn(key="expired_subscriptions", label="Expired", align="right"),
            ReportColumn(key="frozen_subscriptions", label="Frozen", align="right"),
            ReportColumn(key="pending_subscriptions", label="Pending", align="right"),
        ],
        extractor=extract_branch_memberships,
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),

    # Attendance (7)
    "daily_attendance": ReportDefinition(
        code="daily_attendance",
        title="Daily Attendance Summary",
        category="attendance",
        description="Day-by-day aggregate foot traffic, unique visitors, and verification method breakdowns.",
        columns=[
            ReportColumn(key="date", label="Date", is_date=True),
            ReportColumn(key="total_entries", label="Total Check-ins", align="right"),
            ReportColumn(key="unique_visitors", label="Unique Visitors", align="right"),
            ReportColumn(key="qr_entries", label="QR Scans", align="right"),
            ReportColumn(key="manual_entries", label="Manual Searches", align="right"),
        ],
        extractor=extract_daily_attendance_summary,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "attendance_by_branch": ReportDefinition(
        code="attendance_by_branch",
        title="Attendance Traffic by Branch",
        category="attendance",
        description="Comparative branch check-in counts and verification methods during the reporting period.",
        columns=[
            ReportColumn(key="branch_name", label="Branch Name"),
            ReportColumn(key="branch_code", label="Code", align="center"),
            ReportColumn(key="total_entries", label="Total Check-ins", align="right"),
            ReportColumn(key="unique_visitors", label="Unique Visitors", align="right"),
            ReportColumn(key="qr_entries", label="QR Scans", align="right"),
            ReportColumn(key="manual_entries", label="Manual Searches", align="right"),
        ],
        extractor=extract_attendance_by_branch,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "qr_vs_manual": ReportDefinition(
        code="qr_vs_manual",
        title="QR vs Manual Entry Methods Comparison",
        category="attendance",
        description="Analysis of access method adoption comparing self-service QR credentials with receptionist search.",
        columns=[
            ReportColumn(key="method", label="Verification Method"),
            ReportColumn(key="total_entries", label="Total Check-ins", align="right"),
            ReportColumn(key="unique_visitors", label="Unique Visitors", align="right"),
            ReportColumn(key="share_percentage", label="Share %", align="right"),
        ],
        extractor=extract_qr_vs_manual,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "member_visit_history": ReportDefinition(
        code="member_visit_history",
        title="Member Visit History Audit Log",
        category="attendance",
        description="Chronological log of check-in and checkout events, facility durations, and verified vs estimated sessions.",
        columns=[
            ReportColumn(key="check_in_time", label="Check-in Time", is_date=True),
            ReportColumn(key="member", label="Member"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="method", label="Entry Method"),
            ReportColumn(key="check_out_time", label="Checkout Time"),
            ReportColumn(key="duration", label="Duration", align="right"),
            ReportColumn(key="status", label="Session Status", align="center"),
        ],
        extractor=extract_member_visit_history,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "peak_hours": ReportDefinition(
        code="peak_hours",
        title="Peak Attendance Hours Distribution",
        category="attendance",
        description="Hourly breakdown of gym visits across operating hours to optimize staffing and equipment capacity.",
        columns=[
            ReportColumn(key="hour_of_day", label="Operating Hour (EAT)"),
            ReportColumn(key="total_entries", label="Total Entries", align="right"),
            ReportColumn(key="volume_percentage", label="Volume %", align="right"),
        ],
        extractor=extract_peak_hours,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "access_denials": ReportDefinition(
        code="access_denials",
        title="Access Denials & Security Audit",
        category="attendance",
        description="Audit log of rejected entry attempts, policy violations (expired, frozen, cross-branch), and error codes.",
        columns=[
            ReportColumn(key="timestamp", label="Timestamp (EAT)", is_date=True),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="member", label="Member (if identified)"),
            ReportColumn(key="reason_code", label="Policy Code", align="center"),
            ReportColumn(key="denial_reason", label="Rejection Reason"),
        ],
        extractor=extract_access_denials,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    "verified_vs_estimated_duration": ReportDefinition(
        code="verified_vs_estimated_duration",
        title="Verified vs Estimated Duration Audit",
        category="attendance",
        description="Quality report verifying the integrity of facility duration analytics by separating physical checkouts from auto-closed sessions.",
        columns=[
            ReportColumn(key="date", label="Date", is_date=True),
            ReportColumn(key="verified_checkouts", label="Verified Physical Checkouts", align="right"),
            ReportColumn(key="avg_verified_duration", label="Avg Verified Duration", align="right"),
            ReportColumn(key="auto_closed_estimated_sessions", label="Auto-Closed (Estimated)", align="right"),
        ],
        extractor=extract_verified_vs_estimated,
        allowed_roles=["Gym Owner", "Branch Manager", "Receptionist", "Accountant"],
    ),
    # =========================================================================
    # PHASE 10: FINANCE & EXPENSE REPORTS (10 Reports)
    # =========================================================================
    "expense_register": ReportDefinition(
        code="expense_register",
        title="Expense Register",
        category="financial",
        description="Comprehensive audit register of all operational expenses, approval statuses, and payments.",
        columns=[
            ReportColumn(key="date", label="Expense Date", is_date=True),
            ReportColumn(key="reference", label="Reference"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="category", label="Category"),
            ReportColumn(key="supplier", label="Supplier"),
            ReportColumn(key="description", label="Description"),
            ReportColumn(key="amount", label="Net Amount", align="right", is_currency=True),
            ReportColumn(key="tax", label="Tax (ETB)", align="right", is_currency=True),
            ReportColumn(key="total_amount", label="Total Amount", align="right", is_currency=True),
            ReportColumn(key="paid_amount", label="Paid Amount", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Balance Due", align="right", is_currency=True),
            ReportColumn(key="approval_status", label="Approval"),
            ReportColumn(key="payment_status", label="Payment"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_expense_register"]).extract_expense_register(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "expense_category_summary": ReportDefinition(
        code="expense_category_summary",
        title="Expense Category Summary",
        category="financial",
        description="Aggregated operational spending categorized by expense type with budget share analysis.",
        columns=[
            ReportColumn(key="category", label="Category Name"),
            ReportColumn(key="code", label="Code"),
            ReportColumn(key="expense_count", label="Count", align="right"),
            ReportColumn(key="total_amount", label="Total Approved", align="right", is_currency=True),
            ReportColumn(key="paid_amount", label="Total Disbursed", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Outstanding", align="right", is_currency=True),
            ReportColumn(key="share_pct", label="Share %", align="right"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_expense_category_summary"]).extract_expense_category_summary(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "supplier_payments": ReportDefinition(
        code="supplier_payments",
        title="Supplier & Vendor Payments Report",
        category="financial",
        description="Breakdown of purchases, disbursements, and outstanding liabilities by vendor/contractor.",
        columns=[
            ReportColumn(key="supplier_name", label="Supplier Name"),
            ReportColumn(key="supplier_type", label="Category / Type"),
            ReportColumn(key="tin_vat", label="TIN / VAT Number"),
            ReportColumn(key="expenses_count", label="Expenses Count", align="right"),
            ReportColumn(key="total_billed", label="Total Invoiced", align="right", is_currency=True),
            ReportColumn(key="total_paid", label="Total Paid", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Outstanding Balance", align="right", is_currency=True),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_supplier_payments"]).extract_supplier_payments(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "outstanding_expenses": ReportDefinition(
        code="outstanding_expenses",
        title="Outstanding Expenses & Payables",
        category="financial",
        description="Aging schedule and payment due dates for approved expenses awaiting disbursement.",
        columns=[
            ReportColumn(key="reference", label="Reference"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="category", label="Category"),
            ReportColumn(key="supplier", label="Supplier"),
            ReportColumn(key="expense_date", label="Expense Date", is_date=True),
            ReportColumn(key="due_date", label="Due Date", is_date=True),
            ReportColumn(key="is_overdue", label="Aging Status"),
            ReportColumn(key="total_amount", label="Total Amount", align="right", is_currency=True),
            ReportColumn(key="paid_amount", label="Paid Amount", align="right", is_currency=True),
            ReportColumn(key="balance_due", label="Outstanding Balance", align="right", is_currency=True),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_outstanding_expenses"]).extract_outstanding_expenses(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "cash_flow_statement": ReportDefinition(
        code="cash_flow_statement",
        title="Cash Flow Statement (Cash Basis)",
        category="financial",
        description="Authoritative cash inflows, refunds, operational expense disbursements, and net cash movement.",
        columns=[
            ReportColumn(key="component", label="Cash Flow Component"),
            ReportColumn(key="amount", label="Amount (ETB)", align="right", is_currency=True),
            ReportColumn(key="flow_type", label="Classification"),
            ReportColumn(key="notes", label="Accounting Basis / Explanation"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_cash_flow_statement"]).extract_cash_flow_statement(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "account_movements": ReportDefinition(
        code="account_movements",
        title="Cash & Bank Account Movements",
        category="financial",
        description="Transaction ledger of all cash box, petty cash, and bank account debits, credits, and transfers.",
        columns=[
            ReportColumn(key="date", label="Date", is_date=True),
            ReportColumn(key="account", label="Account Name"),
            ReportColumn(key="account_type", label="Account Type"),
            ReportColumn(key="movement_type", label="Movement Type"),
            ReportColumn(key="reference", label="Reference Number"),
            ReportColumn(key="details", label="Transaction Details"),
            ReportColumn(key="inflow", label="Inflow (ETB)", align="right", is_currency=True),
            ReportColumn(key="outflow", label="Outflow (ETB)", align="right", is_currency=True),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_account_movements"]).extract_account_movements(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "daily_reconciliation": ReportDefinition(
        code="daily_reconciliation",
        title="Daily Cash & Bank Reconciliation",
        category="financial",
        description="Comparison of actual physical cash/bank balances against expected balances with variance audit.",
        columns=[
            ReportColumn(key="date", label="Reconciliation Date", is_date=True),
            ReportColumn(key="reconciliation_number", label="Reconciliation ID"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="account", label="Account"),
            ReportColumn(key="opening_balance", label="Opening (ETB)", align="right", is_currency=True),
            ReportColumn(key="inflows", label="Inflows", align="right", is_currency=True),
            ReportColumn(key="outflows", label="Outflows", align="right", is_currency=True),
            ReportColumn(key="expected_balance", label="Expected (ETB)", align="right", is_currency=True),
            ReportColumn(key="actual_counted", label="Actual Counted", align="right", is_currency=True),
            ReportColumn(key="variance", label="Variance", align="right", is_currency=True),
            ReportColumn(key="status", label="Status"),
            ReportColumn(key="reconciled_by", label="Conducted By"),
            ReportColumn(key="variance_reason", label="Variance Explanation"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_daily_reconciliation"]).extract_daily_reconciliation(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "branch_financial_summary": ReportDefinition(
        code="branch_financial_summary",
        title="Branch Financial Performance Summary",
        category="financial",
        description="Cross-branch comparison of member collections, refunds, operational expenses, and net cash.",
        columns=[
            ReportColumn(key="branch_name", label="Branch Name"),
            ReportColumn(key="branch_code", label="Branch Code"),
            ReportColumn(key="verified_collections", label="Collections", align="right", is_currency=True),
            ReportColumn(key="completed_refunds", label="Refunds", align="right", is_currency=True),
            ReportColumn(key="paid_expenses", label="Paid Expenses", align="right", is_currency=True),
            ReportColumn(key="net_cash_flow", label="Net Cash Movement", align="right", is_currency=True),
            ReportColumn(key="outstanding_liabilities", label="Unpaid Liabilities", align="right", is_currency=True),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_branch_financial_summary"]).extract_branch_financial_summary(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "income_expense_summary": ReportDefinition(
        code="income_expense_summary",
        title="Income and Expense Summary (Cash Basis)",
        category="financial",
        description="Summary of gross collections, customer refunds, operational expenses, and net cash surplus/deficit.",
        columns=[
            ReportColumn(key="period", label="Reporting Period"),
            ReportColumn(key="gross_collections", label="Gross Collections", align="right", is_currency=True),
            ReportColumn(key="customer_refunds", label="Refunds Outflow", align="right", is_currency=True),
            ReportColumn(key="net_collections", label="Net Collections", align="right", is_currency=True),
            ReportColumn(key="operating_expenses_paid", label="Expenses Disbursed", align="right", is_currency=True),
            ReportColumn(key="net_operating_cash", label="Net Operating Cash", align="right", is_currency=True),
            ReportColumn(key="unpaid_expense_liabilities", label="Unpaid Liabilities", align="right", is_currency=True),
            ReportColumn(key="accounting_note", label="Accounting Basis"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_income_expense_summary"]).extract_income_expense_summary(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
    "approval_audit_report": ReportDefinition(
        code="approval_audit_report",
        title="Expense Approval Audit Trail",
        category="financial",
        description="Audit trail of all expense submissions, management approvals, rejections, and void actions.",
        columns=[
            ReportColumn(key="reference", label="Expense Reference"),
            ReportColumn(key="branch", label="Branch"),
            ReportColumn(key="category", label="Category"),
            ReportColumn(key="amount", label="Total Amount", align="right", is_currency=True),
            ReportColumn(key="submitted_by", label="Submitted By"),
            ReportColumn(key="decision_by", label="Decision By"),
            ReportColumn(key="status", label="Final Decision"),
            ReportColumn(key="turnaround_time", label="Turnaround"),
            ReportColumn(key="notes", label="Decision Reason / Notes"),
        ],
        extractor=lambda b, d: __import__("apps.finance.reports", fromlist=["extract_approval_audit_report"]).extract_approval_audit_report(b, d),
        allowed_roles=["Gym Owner", "Branch Manager", "Accountant"],
    ),
}


def get_available_reports_for_user(user) -> List[ReportDefinition]:
    """Return report definitions permitted for user's assigned roles."""
    if user.is_superuser or is_owner(user):
        return list(REPORT_REGISTRY.values())

    from apps.accounts.roles import has_role
    allowed = []
    for r in REPORT_REGISTRY.values():
        if any(has_role(user, role) for role in r.allowed_roles):
            allowed.append(r)
    return allowed
