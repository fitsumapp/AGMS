from decimal import Decimal
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.accounts.roles import is_owner
from .models import (
    AccountAdjustment,
    AccountTransfer,
    BillingSettlementBatch,
    BranchFinanceSettings,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    FinanceAuditEvent,
    FinancialAccount,
    FinancialReconciliation,
    SettledBillingPayment,
    SettledBillingRefund,
    Supplier,
)
from .permissions import get_user_finance_branches
from .services import (
    approve_expense,
    close_reconciliation,
    reject_expense,
    reopen_reconciliation,
    submit_expense,
    void_expense,
)


@admin.register(BranchFinanceSettings)
class BranchFinanceSettingsAdmin(ModelAdmin):
    list_display = (
        "branch",
        "manager_approval_limit_display",
        "receptionist_can_create_expenses",
        "allow_self_approval",
        "allow_negative_account_balance",
        "updated_at",
    )
    list_filter = ("branch", "receptionist_can_create_expenses", "allow_self_approval")

    @display(description="Manager Approval Limit")
    def manager_approval_limit_display(self, obj):
        return f"{obj.manager_approval_limit:,.2f} ETB"

    def has_module_permission(self, request):
        return is_owner(request.user) or request.user.is_superuser


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(ModelAdmin):
    list_display = ("name", "code", "order", "is_active_badge", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "description")
    ordering = ("order", "name")

    @display(description="Active", boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active


@admin.register(Supplier)
class SupplierAdmin(ModelAdmin):
    list_display = (
        "name",
        "supplier_type_badge",
        "contact_person",
        "phone",
        "email",
        "masked_tax_id_display",
        "branch_scope",
        "is_active_badge",
    )
    list_filter = ("supplier_type", "is_active", "branch")
    search_fields = ("name", "contact_person", "phone", "email", "tax_identification_number")

    @display(description="Supplier Type")
    def supplier_type_badge(self, obj):
        return obj.get_supplier_type_display()

    @display(description="TIN / VAT")
    def masked_tax_id_display(self, obj):
        return obj.masked_tax_id or "—"

    @display(description="Branch Scope")
    def branch_scope(self, obj):
        return obj.branch.name if obj.branch else "Gym-wide"

    @display(description="Active", boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(models.Q(branch__in=user_branches) | models.Q(branch__isnull=True))


@admin.register(FinancialAccount)
class FinancialAccountAdmin(ModelAdmin):
    list_display = (
        "name",
        "account_type_badge",
        "branch_display",
        "bank_name",
        "masked_account_number_display",
        "current_balance_display",
        "is_active_badge",
    )
    list_filter = ("account_type", "is_active", "branch")
    search_fields = ("name", "bank_name", "account_number")

    @display(description="Type")
    def account_type_badge(self, obj):
        return obj.get_account_type_display()

    @display(description="Branch")
    def branch_display(self, obj):
        return obj.branch.name if obj.branch else "Gym-wide"

    @display(description="Account #")
    def masked_account_number_display(self, obj):
        return obj.masked_account_number or "—"

    @display(description="Current Balance")
    def current_balance_display(self, obj):
        color = "text-emerald-600 dark:text-emerald-400" if obj.current_balance >= 0 else "text-rose-600 dark:text-rose-400"
        return format_html('<span class="font-bold {}">{:,.2f} {}</span>', color, obj.current_balance, obj.currency)

    @display(description="Active", boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(branch__in=user_branches)


@admin.register(Expense)
class ExpenseAdmin(ModelAdmin):
    list_display = (
        "reference",
        "expense_date",
        "branch",
        "category",
        "supplier",
        "total_amount_display",
        "paid_amount_display",
        "balance_due_display",
        "approval_status_badge",
        "payment_status_badge",
        "receipt_download_link",
    )
    list_filter = ("approval_status", "payment_status", "branch", "category", "expense_date")
    search_fields = ("reference", "description", "supplier__name")
    actions = ["submit_for_approval_action", "approve_expense_action"]
    date_hierarchy = "expense_date"

    @display(description="Total")
    def total_amount_display(self, obj):
        return f"{obj.total_amount:,.2f} {obj.currency}"

    @display(description="Paid")
    def paid_amount_display(self, obj):
        return f"{obj.paid_amount:,.2f} {obj.currency}"

    @display(description="Balance Due")
    def balance_due_display(self, obj):
        if obj.balance_due == Decimal("0.00"):
            return format_html('<span class="text-emerald-600 font-semibold">0.00 ETB</span>')
        return format_html('<span class="text-amber-600 font-bold">{:,.2f} ETB</span>', obj.balance_due)

    @display(description="Approval")
    def approval_status_badge(self, obj):
        colors = {
            Expense.ApprovalStatus.DRAFT: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
            Expense.ApprovalStatus.SUBMITTED: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
            Expense.ApprovalStatus.APPROVED: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
            Expense.ApprovalStatus.REJECTED: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
            Expense.ApprovalStatus.VOIDED: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
        }
        cls = colors.get(obj.approval_status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_approval_status_display())

    @display(description="Payment")
    def payment_status_badge(self, obj):
        colors = {
            Expense.PaymentStatus.UNPAID: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
            Expense.PaymentStatus.PARTIALLY_PAID: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
            Expense.PaymentStatus.PAID: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
        }
        cls = colors.get(obj.payment_status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_payment_status_display())

    @display(description="Receipt")
    def receipt_download_link(self, obj):
        if obj.receipt_attachment:
            url = reverse("expense-receipt-download", kwargs={"expense_id": obj.id})
            return format_html('<a href="{}" target="_blank" class="text-teal-600 hover:underline flex items-center gap-1"><span class="material-symbols-outlined text-sm">attachment</span> View</a>', url)
        return "—"

    @action(description="Submit selected draft expenses for approval")
    def submit_for_approval_action(self, request, queryset):
        count = 0
        for exp in queryset.filter(approval_status=Expense.ApprovalStatus.DRAFT):
            try:
                submit_expense(request.user, exp)
                count += 1
            except ValidationError as e:
                self.message_user(request, f"{exp.reference}: {e}", level=messages.ERROR)
        self.message_user(request, f"Submitted {count} expense(s) for approval.", level=messages.SUCCESS)

    @action(description="Approve selected expenses")
    def approve_expense_action(self, request, queryset):
        count = 0
        for exp in queryset.filter(approval_status__in=[Expense.ApprovalStatus.SUBMITTED, Expense.ApprovalStatus.DRAFT]):
            try:
                approve_expense(request.user, exp)
                count += 1
            except ValidationError as e:
                self.message_user(request, f"{exp.reference}: {e}", level=messages.ERROR)
        self.message_user(request, f"Approved {count} expense(s).", level=messages.SUCCESS)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(branch__in=user_branches)


@admin.register(ExpensePayment)
class ExpensePaymentAdmin(ModelAdmin):
    list_display = (
        "payment_number",
        "payment_date",
        "expense_link",
        "financial_account",
        "amount_display",
        "status_badge",
        "recorded_by",
        "attachment_link",
    )
    list_filter = ("status", "payment_date", "financial_account")
    search_fields = ("payment_number", "expense__reference", "transaction_reference")

    @display(description="Expense")
    def expense_link(self, obj):
        return f"{obj.expense.reference} ({obj.expense.category.name})"

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            ExpensePayment.Status.PENDING: "bg-amber-100 text-amber-800",
            ExpensePayment.Status.VERIFIED: "bg-emerald-100 text-emerald-800",
            ExpensePayment.Status.VOIDED: "bg-rose-100 text-rose-800",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())

    @display(description="Voucher / Attachment")
    def attachment_link(self, obj):
        if obj.attachment:
            url = reverse("payment-attachment-download", kwargs={"payment_id": obj.id})
            return format_html('<a href="{}" target="_blank" class="text-teal-600 hover:underline flex items-center gap-1"><span class="material-symbols-outlined text-sm">attachment</span> View</a>', url)
        return "—"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(expense__branch__in=user_branches)


@admin.register(AccountTransfer)
class AccountTransferAdmin(ModelAdmin):
    list_display = (
        "transfer_number",
        "transfer_date",
        "source_account",
        "destination_account",
        "amount_display",
        "status_badge",
        "initiated_by",
    )
    list_filter = ("status", "transfer_date")
    search_fields = ("transfer_number", "reference")

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            AccountTransfer.Status.PENDING: "bg-amber-100 text-amber-800",
            AccountTransfer.Status.COMPLETED: "bg-emerald-100 text-emerald-800",
            AccountTransfer.Status.CANCELLED: "bg-rose-100 text-rose-800",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(models.Q(source_account__branch__in=user_branches) | models.Q(destination_account__branch__in=user_branches))


@admin.register(AccountAdjustment)
class AccountAdjustmentAdmin(ModelAdmin):
    list_display = (
        "adjustment_number",
        "adjustment_date",
        "account",
        "adjustment_type_badge",
        "amount_display",
        "reason",
        "performed_by",
    )
    list_filter = ("adjustment_type", "adjustment_date")
    search_fields = ("adjustment_number", "reason")

    @display(description="Type")
    def adjustment_type_badge(self, obj):
        cls = "text-emerald-600 font-bold" if obj.adjustment_type == AccountAdjustment.AdjustmentType.CREDIT else "text-rose-600 font-bold"
        return format_html('<span class="{}">{}</span>', cls, obj.get_adjustment_type_display())

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f} ETB"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(account__branch__in=user_branches)


@admin.register(FinancialReconciliation)
class FinancialReconciliationAdmin(ModelAdmin):
    list_display = (
        "reconciliation_number",
        "reconciliation_date",
        "branch",
        "account",
        "expected_balance_display",
        "actual_counted_display",
        "variance_display",
        "status_badge",
        "reconciled_by",
    )
    list_filter = ("status", "reconciliation_date", "branch")
    search_fields = ("reconciliation_number", "account__name")
    actions = ["close_reconciliation_action"]

    @display(description="Expected")
    def expected_balance_display(self, obj):
        return f"{obj.expected_balance:,.2f} ETB"

    @display(description="Actual Counted")
    def actual_counted_display(self, obj):
        return f"{obj.actual_counted_balance:,.2f} ETB"

    @display(description="Variance")
    def variance_display(self, obj):
        if obj.variance == Decimal("0.00"):
            return format_html('<span class="text-emerald-600 font-bold">0.00 ETB (Matched)</span>')
        return format_html('<span class="text-rose-600 font-bold">{:,.2f} ETB</span>', obj.variance)

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            FinancialReconciliation.Status.DRAFT: "bg-gray-100 text-gray-800",
            FinancialReconciliation.Status.SUBMITTED: "bg-sky-100 text-sky-800",
            FinancialReconciliation.Status.APPROVED_CLOSED: "bg-emerald-100 text-emerald-800",
            FinancialReconciliation.Status.DISCREPANCY_REJECTED: "bg-rose-100 text-rose-800",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())

    @action(description="Approve and close selected reconciliations")
    def close_reconciliation_action(self, request, queryset):
        count = 0
        for rec in queryset.filter(status__in=[FinancialReconciliation.Status.DRAFT, FinancialReconciliation.Status.SUBMITTED]):
            try:
                close_reconciliation(request.user, rec)
                count += 1
            except ValidationError as e:
                self.message_user(request, f"{rec.reconciliation_number}: {e}", level=messages.ERROR)
        self.message_user(request, f"Closed {count} reconciliation period(s).", level=messages.SUCCESS)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == FinancialReconciliation.Status.APPROVED_CLOSED:
            return [f.name for f in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == FinancialReconciliation.Status.APPROVED_CLOSED:
            return False
        return super().has_delete_permission(request, obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(branch__in=user_branches)


@admin.register(FinanceAuditEvent)
class FinanceAuditEventAdmin(ModelAdmin):
    list_display = ("created_at", "event_type", "actor", "branch", "target_type", "target_id", "action_summary")
    list_filter = ("event_type", "branch", "created_at")
    search_fields = ("target_id", "action_summary")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SettledBillingPaymentInline(TabularInline):
    model = SettledBillingPayment
    extra = 0
    can_delete = False
    readonly_fields = ("payment", "amount", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


class SettledBillingRefundInline(TabularInline):
    model = SettledBillingRefund
    extra = 0
    can_delete = False
    readonly_fields = ("refund", "amount", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BillingSettlementBatch)
class BillingSettlementBatchAdmin(ModelAdmin):
    list_display = (
        "batch_number",
        "branch",
        "financial_account",
        "payment_method_type",
        "period_start",
        "period_end",
        "payment_count",
        "total_payments_display",
        "refund_count",
        "total_refunds_display",
        "net_collections_display",
        "settled_by",
        "created_at",
    )
    list_filter = ("payment_method_type", "branch", "financial_account", "period_start")
    search_fields = ("batch_number", "financial_account__name")
    readonly_fields = (
        "batch_number",
        "branch",
        "financial_account",
        "payment_method_type",
        "period_start",
        "period_end",
        "payment_count",
        "total_payments",
        "refund_count",
        "total_refunds",
        "net_collections",
        "adjustment",
        "settled_by",
        "created_at",
    )
    inlines = [SettledBillingPaymentInline, SettledBillingRefundInline]

    @display(description="Total Collections")
    def total_payments_display(self, obj):
        return f"{obj.total_payments:,.2f} ETB"

    @display(description="Total Refunds")
    def total_refunds_display(self, obj):
        return f"{obj.total_refunds:,.2f} ETB"

    @display(description="Net Settled")
    def net_collections_display(self, obj):
        return f"{obj.net_collections:,.2f} ETB"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        user_branches = get_user_finance_branches(request.user)
        return qs.filter(branch__in=user_branches)
