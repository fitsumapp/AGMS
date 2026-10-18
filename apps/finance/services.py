from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
    FinanceSequence,
    FinancialAccount,
    FinancialReconciliation,
    SettledBillingPayment,
    SettledBillingRefund,
    Supplier,
)
from .permissions import (
    can_approve_expense,
    can_close_reconciliation,
    can_conduct_reconciliation,
    can_create_expense,
    can_manage_accounts,
    can_record_expense_payment,
    get_user_finance_branches,
)


def generate_next_finance_number(sequence_name: str, prefix: str) -> str:
    """Atomic, concurrency-safe sequential identifier generator with PostgreSQL row lock."""
    with transaction.atomic():
        seq, _ = FinanceSequence.objects.select_for_update().get_or_create(
            name=sequence_name,
            defaults={"last_number": 0},
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number", "updated_at"])
        return f"{prefix}-{seq.last_number:06d}"


def get_or_create_branch_finance_settings(branch) -> BranchFinanceSettings:
    """Retrieve or create default finance settings for a branch."""
    settings, _ = BranchFinanceSettings.objects.get_or_create(
        branch=branch,
        defaults={
            "manager_approval_limit": Decimal("10000.00"),
            "receptionist_can_create_expenses": False,
            "allow_self_approval": False,
            "allow_negative_account_balance": False,
        },
    )
    return settings


def check_closed_reconciliation(branch, account, transaction_date):
    """
    Ensure the transaction_date does not fall inside an already approved and closed reconciliation period.
    Users cannot backdate transactions into closed periods.
    Any corrections must be posted in an open period.
    """
    if not transaction_date:
        return
    
    filters = {
        "status": FinancialReconciliation.Status.APPROVED_CLOSED,
        "period_start__lte": transaction_date,
        "period_end__gte": transaction_date,
    }
    if account:
        filters["account"] = account
    elif branch:
        filters["branch"] = branch
        
    closed_recon = FinancialReconciliation.objects.filter(**filters).first()
    if closed_recon:
        target_name = account.name if account else (branch.name if branch else "gym")
        raise ValidationError(
            _(f"Cannot post or modify transactions for date {transaction_date}: "
              f"Reconciliation {closed_recon.reconciliation_number} on '{target_name}' "
              f"for period [{closed_recon.period_start} to {closed_recon.period_end}] is closed and locked.")
        )


def create_expense(
    actor,
    branch,
    category: ExpenseCategory,
    description: str,
    amount: Decimal,
    tax_amount: Decimal = Decimal("0.00"),
    supplier: Supplier = None,
    expense_date=None,
    payment_due_date=None,
    receipt_attachment=None,
    internal_notes: str = "",
    submit_for_approval: bool = False,
) -> Expense:
    """Create a new expense record adhering to branch scoping and permission checks."""
    if not can_create_expense(actor, branch):
        raise ValidationError(_("You do not have permission to create expenses for this branch."))

    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": _("Expense amount must be strictly positive.")})
    if tax_amount < Decimal("0.00"):
        raise ValidationError({"tax_amount": _("Tax amount cannot be negative.")})

    if not category.is_active:
        raise ValidationError({"category": _("Selected expense category is currently inactive.")})

    if supplier:
        if not supplier.is_active:
            raise ValidationError({"supplier": _("Selected supplier is currently inactive.")})
        if supplier.branch and supplier.branch != branch:
            raise ValidationError({"supplier": _("Selected supplier is restricted to a different branch.")})

    with transaction.atomic():
        ref = generate_next_finance_number("expense", "EXP")
        total = amount + tax_amount
        exp_date = expense_date or timezone.localdate()

        init_status = (
            Expense.ApprovalStatus.SUBMITTED
            if submit_for_approval
            else Expense.ApprovalStatus.DRAFT
        )

        expense = Expense.objects.create(
            reference=ref,
            branch=branch,
            category=category,
            supplier=supplier,
            description=description,
            expense_date=exp_date,
            currency="ETB",
            amount=amount,
            tax_amount=tax_amount,
            total_amount=total,
            paid_amount=Decimal("0.00"),
            balance_due=total,
            payment_due_date=payment_due_date,
            approval_status=init_status,
            payment_status=Expense.PaymentStatus.UNPAID,
            created_by=actor,
            submitted_at=timezone.now() if submit_for_approval else None,
            receipt_attachment=receipt_attachment,
            internal_notes=internal_notes,
        )

        FinanceAuditEvent.objects.create(
            event_type=(
                FinanceAuditEvent.EventType.EXPENSE_SUBMITTED
                if submit_for_approval
                else FinanceAuditEvent.EventType.EXPENSE_CREATED
            ),
            actor=actor,
            branch=branch,
            target_type="Expense",
            target_id=str(expense.id),
            action_summary=f"Created expense {expense.reference} ({total} ETB)",
            metadata={"amount": str(amount), "tax": str(tax_amount), "total": str(total)},
        )

        return expense


def submit_expense(actor, expense: Expense) -> Expense:
    """Submit a draft expense for management approval."""
    with transaction.atomic():
        locked = Expense.objects.select_for_update().get(pk=expense.pk)
        if locked.approval_status != Expense.ApprovalStatus.DRAFT:
            raise ValidationError(_("Only draft expenses can be submitted for approval."))

        if not can_create_expense(actor, locked.branch):
            raise ValidationError(_("You do not have permission to submit expenses for this branch."))

        locked.approval_status = Expense.ApprovalStatus.SUBMITTED
        locked.submitted_at = timezone.now()
        locked.save(update_fields=["approval_status", "submitted_at", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.EXPENSE_SUBMITTED,
            actor=actor,
            branch=locked.branch,
            target_type="Expense",
            target_id=str(locked.id),
            action_summary=f"Submitted expense {locked.reference} for approval",
        )
        return locked


def approve_expense(actor, expense: Expense) -> Expense:
    """Approve an expense request with server-side limit and self-approval verification."""
    with transaction.atomic():
        locked = Expense.objects.select_for_update().get(pk=expense.pk)
        if locked.approval_status not in [Expense.ApprovalStatus.SUBMITTED, Expense.ApprovalStatus.DRAFT]:
            raise ValidationError(_(f"Cannot approve expense in {locked.get_approval_status_display()} state."))

        if not can_approve_expense(actor, locked):
            b_settings = get_or_create_branch_finance_settings(locked.branch)
            if locked.created_by_id == actor.id and not b_settings.allow_self_approval and not is_owner(actor) and not actor.is_superuser:
                raise ValidationError(_("Policy violation: You cannot approve your own expense submission."))
            if locked.total_amount > b_settings.manager_approval_limit and not is_owner(actor) and not actor.is_superuser:
                raise ValidationError(_(
                    f"Expense amount ({locked.total_amount} ETB) exceeds your Branch Manager approval limit of {b_settings.manager_approval_limit} ETB. Requires Gym Owner approval."
                ))
            raise ValidationError(_("You do not have authorization to approve this expense."))

        locked.approval_status = Expense.ApprovalStatus.APPROVED
        locked.approved_by = actor
        locked.approved_at = timezone.now()
        locked.rejection_reason = ""
        locked.save(update_fields=["approval_status", "approved_by", "approved_at", "rejection_reason", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.EXPENSE_APPROVED,
            actor=actor,
            branch=locked.branch,
            target_type="Expense",
            target_id=str(locked.id),
            action_summary=f"Approved expense {locked.reference} ({locked.total_amount} ETB)",
        )
        return locked


def reject_expense(actor, expense: Expense, reason: str) -> Expense:
    """Reject a submitted expense request."""
    if not reason or not reason.strip():
        raise ValidationError({"reason": _("A rejection reason is mandatory.")})

    with transaction.atomic():
        locked = Expense.objects.select_for_update().get(pk=expense.pk)
        if locked.approval_status not in [Expense.ApprovalStatus.SUBMITTED, Expense.ApprovalStatus.DRAFT]:
            raise ValidationError(_(f"Cannot reject expense in {locked.get_approval_status_display()} state."))

        if not can_approve_expense(actor, locked):
            raise ValidationError(_("You do not have authorization to reject this expense."))

        locked.approval_status = Expense.ApprovalStatus.REJECTED
        locked.rejected_by = actor
        locked.rejected_at = timezone.now()
        locked.rejection_reason = reason.strip()
        locked.save(update_fields=["approval_status", "rejected_by", "rejected_at", "rejection_reason", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.EXPENSE_REJECTED,
            actor=actor,
            branch=locked.branch,
            target_type="Expense",
            target_id=str(locked.id),
            action_summary=f"Rejected expense {locked.reference}: {reason.strip()}",
        )
        return locked


def void_expense(actor, expense: Expense, reason: str) -> Expense:
    """Void an approved or partially processed expense under strict reversal rules."""
    if not reason or not reason.strip():
        raise ValidationError({"reason": _("A void reason is required.")})

    with transaction.atomic():
        locked = Expense.objects.select_for_update().get(pk=expense.pk)
        if locked.approval_status == Expense.ApprovalStatus.VOIDED:
            raise ValidationError(_("Expense is already voided."))

        # If any active payments exist, they must be voided first!
        active_payments = locked.payments.filter(status=ExpensePayment.Status.VERIFIED).count()
        if active_payments > 0:
            raise ValidationError(_("Cannot void an expense with active payments. Reverse/void the payments first."))

        if not is_owner(actor) and not actor.is_superuser:
            if not can_approve_expense(actor, locked):
                raise ValidationError(_("Only authorized management can void an expense."))

        locked.approval_status = Expense.ApprovalStatus.VOIDED
        locked.voided_by = actor
        locked.voided_at = timezone.now()
        locked.void_reason = reason.strip()
        locked.save(update_fields=["approval_status", "voided_by", "voided_at", "void_reason", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.EXPENSE_VOIDED,
            actor=actor,
            branch=locked.branch,
            target_type="Expense",
            target_id=str(locked.id),
            action_summary=f"Voided expense {locked.reference}: {reason.strip()}",
        )
        return locked


def record_expense_payment(
    actor,
    expense: Expense,
    financial_account: FinancialAccount,
    amount: Decimal,
    payment_date=None,
    payment_method=None,
    transaction_reference: str = "",
    attachment=None,
    notes: str = "",
    idempotency_key: str = None,
) -> ExpensePayment:
    """
    Disburse payment against an approved expense with row-level locks,
    balance checks, account deduction, and idempotent verification.
    """
    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": _("Payment amount must be strictly positive.")})

    with transaction.atomic():
        # Establish deterministic lock order to prevent deadlocks:
        # First lock FinancialAccount, then Expense
        locked_account = FinancialAccount.objects.select_for_update().get(pk=financial_account.pk)
        locked_expense = Expense.objects.select_for_update().get(pk=expense.pk)

        if not can_record_expense_payment(actor, locked_expense):
            raise ValidationError(_("You do not have authorization to record payments for this branch."))

        if locked_expense.approval_status != Expense.ApprovalStatus.APPROVED:
            raise ValidationError(_(
                f"Expense must be APPROVED before payment can be recorded. Current status: {locked_expense.get_approval_status_display()}."
            ))

        if amount > locked_expense.balance_due:
            raise ValidationError({
                "amount": _(f"Payment amount ({amount} ETB) exceeds outstanding balance due ({locked_expense.balance_due} ETB).")
            })

        # Check account negative balance restriction
        b_settings = get_or_create_branch_finance_settings(locked_expense.branch)
        allow_neg = locked_account.allow_negative or b_settings.allow_negative_account_balance
        if not allow_neg and (locked_account.current_balance - amount < Decimal("0.00")):
            raise ValidationError({
                "financial_account": _(
                    f"Insufficient funds in account '{locked_account.name}'. Available: {locked_account.current_balance} ETB, Requested: {amount} ETB."
                )
            })

        # Check idempotency key if provided
        key = idempotency_key or f"exppay-{locked_expense.id}-{uuid.uuid4().hex[:12]}"
        if ExpensePayment.objects.filter(idempotency_key=key).exists():
            raise ValidationError({"idempotency_key": _("This payment has already been processed (duplicate idempotency key).")})

        pay_num = generate_next_finance_number("expense_payment", "EXPPAY")
        p_date = payment_date or timezone.localdate()
        check_closed_reconciliation(locked_expense.branch, locked_account, p_date)

        payment = ExpensePayment.objects.create(
            payment_number=pay_num,
            expense=locked_expense,
            financial_account=locked_account,
            payment_date=p_date,
            amount=amount,
            payment_method=payment_method,
            transaction_reference=transaction_reference.strip(),
            attachment=attachment,
            status=ExpensePayment.Status.VERIFIED,
            recorded_by=actor,
            verified_by=actor,
            notes=notes,
            idempotency_key=key,
        )

        # Deduct from account balance
        locked_account.current_balance -= amount
        locked_account.save(update_fields=["current_balance", "updated_at"])

        # Update expense totals
        locked_expense.paid_amount += amount
        locked_expense.balance_due -= amount
        if locked_expense.balance_due == Decimal("0.00"):
            locked_expense.payment_status = Expense.PaymentStatus.PAID
        else:
            locked_expense.payment_status = Expense.PaymentStatus.PARTIALLY_PAID
        locked_expense.save(update_fields=["paid_amount", "balance_due", "payment_status", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.PAYMENT_RECORDED,
            actor=actor,
            branch=locked_expense.branch,
            target_type="ExpensePayment",
            target_id=str(payment.id),
            action_summary=f"Disbursed {amount} ETB from {locked_account.name} for expense {locked_expense.reference}",
            metadata={"payment_number": pay_num, "remaining_balance": str(locked_expense.balance_due)},
        )

        return payment


def void_expense_payment(
    actor,
    payment: ExpensePayment,
    reason: str,
    restore_account_balance: bool = True,
) -> ExpensePayment:
    """
    Void a verified expense payment and optionally restore account and expense balances.
    - If restore_account_balance=True (default): Re-credits the financial account (physical cash/bank return).
    - If restore_account_balance=False: Administrative cancellation of an erroneous posting where funds were not disbursed/recovered.
    Enforces closed-period check, row locking, and prevents duplicate void requests.
    """
    if not reason or not reason.strip():
        raise ValidationError({"reason": _("A void reason is mandatory.")})

    with transaction.atomic():
        locked_payment = ExpensePayment.objects.select_for_update().get(pk=payment.pk)
        if locked_payment.status != ExpensePayment.Status.VERIFIED:
            raise ValidationError(_("Only verified payments can be voided."))

        locked_account = FinancialAccount.objects.select_for_update().get(pk=locked_payment.financial_account_id)
        locked_expense = Expense.objects.select_for_update().get(pk=locked_payment.expense_id)

        if not can_record_expense_payment(actor, locked_expense):
            raise ValidationError(_("You do not have authorization to void payments for this branch."))

        # Enforce closed-period protection: Cannot silently void transactions in closed reconciliation periods
        check_closed_reconciliation(locked_expense.branch, locked_account, locked_payment.payment_date)

        amount = locked_payment.amount

        # Re-credit financial account only if physical funds are restored
        if restore_account_balance:
            locked_account.current_balance += amount
            locked_account.save(update_fields=["current_balance", "updated_at"])

        # Re-adjust expense paid and balance
        locked_expense.paid_amount -= amount
        locked_expense.balance_due += amount
        if locked_expense.paid_amount == Decimal("0.00"):
            locked_expense.payment_status = Expense.PaymentStatus.UNPAID
        else:
            locked_expense.payment_status = Expense.PaymentStatus.PARTIALLY_PAID
        locked_expense.save(update_fields=["paid_amount", "balance_due", "payment_status", "updated_at"])

        # Update payment record
        locked_payment.status = ExpensePayment.Status.VOIDED
        locked_payment.voided_by = actor
        locked_payment.voided_at = timezone.now()
        locked_payment.void_reason = reason.strip()
        locked_payment.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

        action_mode = "Balance Restored" if restore_account_balance else "Administrative Cancellation (No Balance Restored)"
        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.PAYMENT_VOIDED,
            actor=actor,
            branch=locked_expense.branch,
            target_type="ExpensePayment",
            target_id=str(locked_payment.id),
            action_summary=f"Voided payment {locked_payment.payment_number} ({amount} ETB) [{action_mode}]: {reason.strip()}",
            metadata={"restore_account_balance": restore_account_balance, "amount": str(amount)},
        )

        return locked_payment


def transfer_funds(
    actor,
    source_account: FinancialAccount,
    destination_account: FinancialAccount,
    amount: Decimal,
    transfer_date=None,
    reference: str = "",
    notes: str = "",
) -> AccountTransfer:
    """Internal funds transfer between accounts (Does NOT inflate total gym revenue or expenses)."""
    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": _("Transfer amount must be strictly positive.")})

    if source_account.id == destination_account.id:
        raise ValidationError({"destination_account": _("Source and destination accounts must be distinct.")})

    if not can_manage_accounts(actor):
        raise ValidationError(_("You do not have permission to initiate internal transfers."))

    with transaction.atomic():
        # Prevent deadlock with deterministic lock ordering by UUID
        first_id, second_id = (
            (source_account.id, destination_account.id)
            if str(source_account.id) < str(destination_account.id)
            else (destination_account.id, source_account.id)
        )
        acc1 = FinancialAccount.objects.select_for_update().get(pk=first_id)
        acc2 = FinancialAccount.objects.select_for_update().get(pk=second_id)

        locked_src = acc1 if acc1.id == source_account.id else acc2
        locked_dst = acc2 if acc2.id == destination_account.id else acc1

        if not locked_src.allow_negative and (locked_src.current_balance - amount < Decimal("0.00")):
            raise ValidationError({
                "amount": _(f"Insufficient funds in source account '{locked_src.name}'. Available: {locked_src.current_balance} ETB.")
            })

        trf_num = generate_next_finance_number("account_transfer", "TRF")
        t_date = transfer_date or timezone.localdate()
        check_closed_reconciliation(locked_src.branch, locked_src, t_date)
        check_closed_reconciliation(locked_dst.branch, locked_dst, t_date)

        transfer = AccountTransfer.objects.create(
            transfer_number=trf_num,
            source_account=locked_src,
            destination_account=locked_dst,
            amount=amount,
            transfer_date=t_date,
            reference=reference.strip(),
            status=AccountTransfer.Status.COMPLETED,
            initiated_by=actor,
            approved_by=actor,
            notes=notes,
        )

        locked_src.current_balance -= amount
        locked_src.save(update_fields=["current_balance", "updated_at"])

        locked_dst.current_balance += amount
        locked_dst.save(update_fields=["current_balance", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.TRANSFER_COMPLETED,
            actor=actor,
            branch=locked_src.branch,
            target_type="AccountTransfer",
            target_id=str(transfer.id),
            action_summary=f"Transferred {amount} ETB from {locked_src.name} to {locked_dst.name}",
        )

        return transfer


def perform_account_adjustment(
    actor,
    account: FinancialAccount,
    adjustment_type: str,
    amount: Decimal,
    reason: str,
    adjustment_date=None,
) -> AccountAdjustment:
    """Audited balance adjustment for a cash or bank account with mandatory justification."""
    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": _("Adjustment amount must be strictly positive.")})
    if not reason or not reason.strip():
        raise ValidationError({"reason": _("A justification reason is mandatory for balance adjustments.")})

    if not can_manage_accounts(actor):
        raise ValidationError(_("You do not have permission to perform account adjustments."))

    with transaction.atomic():
        locked = FinancialAccount.objects.select_for_update().get(pk=account.pk)
        adj_num = generate_next_finance_number("account_adjustment", "ADJ")
        adj_date = adjustment_date or timezone.localdate()
        check_closed_reconciliation(locked.branch, locked, adj_date)

        if adjustment_type == AccountAdjustment.AdjustmentType.DEBIT:
            if not locked.allow_negative and (locked.current_balance - amount < Decimal("0.00")):
                raise ValidationError({"amount": _(f"Debit adjustment would cause negative balance ({locked.current_balance - amount} ETB).")})
            locked.current_balance -= amount
        elif adjustment_type == AccountAdjustment.AdjustmentType.CREDIT:
            locked.current_balance += amount
        else:
            raise ValidationError({"adjustment_type": _("Invalid adjustment type.")})

        locked.save(update_fields=["current_balance", "updated_at"])

        adjustment = AccountAdjustment.objects.create(
            adjustment_number=adj_num,
            account=locked,
            adjustment_type=adjustment_type,
            amount=amount,
            adjustment_date=adj_date,
            reason=reason.strip(),
            performed_by=actor,
            approved_by=actor,
        )

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.ADJUSTMENT_APPLIED,
            actor=actor,
            branch=locked.branch,
            target_type="AccountAdjustment",
            target_id=str(adjustment.id),
            action_summary=f"Applied {adjustment_type} adjustment of {amount} ETB on {locked.name}: {reason.strip()}",
        )

        return adjustment


def conduct_reconciliation(
    actor,
    branch,
    account: FinancialAccount,
    actual_counted_balance: Decimal,
    reconciliation_date=None,
    period_start=None,
    period_end=None,
    variance_reason: str = "",
    notes: str = "",
    submit_for_closure: bool = False,
) -> FinancialReconciliation:
    """Conduct a cash or bank reconciliation comparing counted balance against expected ledger balance."""
    if not can_conduct_reconciliation(actor, branch):
        raise ValidationError(_("You do not have permission to reconcile accounts for this branch."))

    rec_date = reconciliation_date or timezone.localdate()
    p_start = period_start or rec_date
    p_end = period_end or rec_date

    with transaction.atomic():
        # Prevent creating overlapping reconciliation for an already closed period
        overlapping_closed = FinancialReconciliation.objects.filter(
            account=account,
            status=FinancialReconciliation.Status.APPROVED_CLOSED,
            period_start__lte=p_end,
            period_end__gte=p_start,
        ).first()
        if overlapping_closed:
            raise ValidationError(
                _(f"A closed reconciliation period ({overlapping_closed.reconciliation_number}) "
                  f"already exists for '{account.name}' covering [{overlapping_closed.period_start} to {overlapping_closed.period_end}].")
            )

        # Expected balance calculation based on current account balance:
        # Opening balance at period start, plus inflows minus outflows
        opening = account.opening_balance
        
        # Calculate inflows & outflows for this account
        inflows = Decimal("0.00")
        outflows = Decimal("0.00")

        # Inflows from transfers
        trf_in = AccountTransfer.objects.filter(
            destination_account=account,
            status=AccountTransfer.Status.COMPLETED,
            transfer_date__gte=p_start,
            transfer_date__lte=p_end,
        ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
        inflows += trf_in

        # Outflows from transfers
        trf_out = AccountTransfer.objects.filter(
            source_account=account,
            status=AccountTransfer.Status.COMPLETED,
            transfer_date__gte=p_start,
            transfer_date__lte=p_end,
        ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
        outflows += trf_out

        # Outflows from expense payments
        exp_pays = ExpensePayment.objects.filter(
            financial_account=account,
            status=ExpensePayment.Status.VERIFIED,
            payment_date__gte=p_start,
            payment_date__lte=p_end,
        ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
        outflows += exp_pays

        # Inflows/Outflows from adjustments
        cred_adj = AccountAdjustment.objects.filter(
            account=account,
            adjustment_type=AccountAdjustment.AdjustmentType.CREDIT,
            adjustment_date__gte=p_start,
            adjustment_date__lte=p_end,
        ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
        inflows += cred_adj

        deb_adj = AccountAdjustment.objects.filter(
            account=account,
            adjustment_type=AccountAdjustment.AdjustmentType.DEBIT,
            adjustment_date__gte=p_start,
            adjustment_date__lte=p_end,
        ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
        outflows += deb_adj

        # If it's a branch cash on hand account, include verified cash collections from Phase 05 Billing
        if account.account_type == FinancialAccount.AccountType.CASH_ON_HAND and account.branch:
            from apps.billing.models import Payment, Refund
            billing_cash = Payment.objects.filter(
                branch=account.branch,
                payment_method__method_type="CASH",
                status=Payment.Status.VERIFIED,
                paid_at__date__gte=p_start,
                paid_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            inflows += billing_cash

            billing_refunds = Refund.objects.filter(
                payment__branch=account.branch,
                payment__payment_method__method_type="CASH",
                status=Refund.Status.COMPLETED,
                completed_at__date__gte=p_start,
                completed_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            outflows += billing_refunds
        elif account.account_type == FinancialAccount.AccountType.BANK and account.branch:
            from apps.billing.models import Payment, Refund
            billing_bank = Payment.objects.filter(
                branch=account.branch,
                payment_method__method_type="BANK_TRANSFER",
                status=Payment.Status.VERIFIED,
                paid_at__date__gte=p_start,
                paid_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            inflows += billing_bank

            billing_bank_refunds = Refund.objects.filter(
                payment__branch=account.branch,
                payment__payment_method__method_type="BANK_TRANSFER",
                status=Refund.Status.COMPLETED,
                completed_at__date__gte=p_start,
                completed_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            outflows += billing_bank_refunds
        elif account.account_type == FinancialAccount.AccountType.WALLET and account.branch:
            from apps.billing.models import Payment, Refund
            billing_wallet = Payment.objects.filter(
                branch=account.branch,
                payment_method__method_type__in=["TELEBIRR", "MPESA"],
                status=Payment.Status.VERIFIED,
                paid_at__date__gte=p_start,
                paid_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            inflows += billing_wallet

            billing_wallet_refunds = Refund.objects.filter(
                payment__branch=account.branch,
                payment__payment_method__method_type__in=["TELEBIRR", "MPESA"],
                status=Refund.Status.COMPLETED,
                completed_at__date__gte=p_start,
                completed_at__date__lte=p_end,
            ).aggregate(tot=models.Sum("amount"))["tot"] or Decimal("0.00")
            outflows += billing_wallet_refunds

        expected = opening + inflows - outflows
        variance = actual_counted_balance - expected

        if submit_for_closure and variance != Decimal("0.00") and not variance_reason.strip():
            raise ValidationError({"variance_reason": _("A detailed explanation is required when there is a cash/bank discrepancy.")})

        rec_num = generate_next_finance_number("reconciliation", "RECON")
        recon = FinancialReconciliation.objects.create(
            reconciliation_number=rec_num,
            branch=branch,
            account=account,
            reconciliation_date=rec_date,
            period_start=p_start,
            period_end=p_end,
            opening_balance=opening,
            total_inflows=inflows,
            total_outflows=outflows,
            expected_balance=expected,
            actual_counted_balance=actual_counted_balance,
            variance=variance,
            variance_reason=variance_reason.strip(),
            status=(
                FinancialReconciliation.Status.SUBMITTED
                if submit_for_closure
                else FinancialReconciliation.Status.DRAFT
            ),
            reconciled_by=actor,
            notes=notes,
        )

        FinanceAuditEvent.objects.create(
            event_type=(
                FinanceAuditEvent.EventType.RECONCILIATION_SUBMITTED
                if submit_for_closure
                else FinanceAuditEvent.EventType.EXPENSE_CREATED
            ),
            actor=actor,
            branch=branch,
            target_type="FinancialReconciliation",
            target_id=str(recon.id),
            action_summary=f"Conducted reconciliation {recon.reconciliation_number} on {account.name} (Variance: {variance} ETB)",
        )

        return recon


def close_reconciliation(actor, reconciliation: FinancialReconciliation) -> FinancialReconciliation:
    """Approve and close a financial reconciliation period."""
    with transaction.atomic():
        locked = FinancialReconciliation.objects.select_for_update().get(pk=reconciliation.pk)
        if locked.status == FinancialReconciliation.Status.APPROVED_CLOSED:
            raise ValidationError(_("This reconciliation period is already closed."))

        if not can_close_reconciliation(actor, locked.branch):
            raise ValidationError(_("You do not have authorization to approve and close reconciliations for this branch."))

        if locked.variance != Decimal("0.00") and not locked.variance_reason.strip():
            raise ValidationError({"variance_reason": _("Cannot close reconciliation with unaddressed variance.")})

        locked.status = FinancialReconciliation.Status.APPROVED_CLOSED
        locked.approved_by = actor
        locked.closed_at = timezone.now()
        locked.save(update_fields=["status", "approved_by", "closed_at", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.RECONCILIATION_CLOSED,
            actor=actor,
            branch=locked.branch,
            target_type="FinancialReconciliation",
            target_id=str(locked.id),
            action_summary=f"Approved & closed reconciliation {locked.reconciliation_number} for {locked.account.name}",
        )

        return locked


def reopen_reconciliation(actor, reconciliation: FinancialReconciliation, reason: str) -> FinancialReconciliation:
    """Reopen a previously closed reconciliation period (Restricted to Gym Owner & Super Admin)."""
    if not is_owner(actor) and not actor.is_superuser:
        raise ValidationError(_("Only a Gym Owner can reopen a closed reconciliation period."))

    if not reason or not reason.strip():
        raise ValidationError({"reason": _("A justification reason is required to reopen a closed reconciliation period.")})

    with transaction.atomic():
        locked = FinancialReconciliation.objects.select_for_update().get(pk=reconciliation.pk)
        if locked.status != FinancialReconciliation.Status.APPROVED_CLOSED:
            raise ValidationError(_("Only approved & closed reconciliations can be reopened."))

        locked.status = FinancialReconciliation.Status.DRAFT
        locked.closed_at = None
        locked.save(update_fields=["status", "closed_at", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.RECONCILIATION_REOPENED,
            actor=actor,
            branch=locked.branch,
            target_type="FinancialReconciliation",
            target_id=str(locked.id),
            action_summary=f"Reopened reconciliation {locked.reconciliation_number}: {reason.strip()}",
        )

        return locked


def post_verified_billing_batch_to_account(
    actor,
    branch,
    account: FinancialAccount,
    start_date,
    end_date,
    payment_method_type: str = "CASH",
    notes: str = "",
) -> dict:
    """
    Authoritatively settle verified customer billing collections and completed refunds into a financial account.
    - Durable exactly-once posting: Tracks every settled payment/refund with database UNIQUE constraints.
    - Overlap-safe: Only unsettled payments and refunds in the specified range are processed.
    - Idempotent: Attempting to settle a range with zero unsettled transactions cleanly reports that all transactions are already settled.
    - Atomically updates account current_balance by net verified revenue (collections - refunds).
    - Preserves Phase 05 Billing as the sole authoritative source of truth.
    - Enforces closed-period check and branch isolation.
    """
    if not can_manage_accounts(actor):
        raise ValidationError(_("You do not have permission to post settlements to financial accounts."))

    if account.branch and account.branch != branch:
        raise ValidationError({"account": _("Financial account is assigned to a different branch.")})

    check_closed_reconciliation(branch, account, end_date)

    from apps.billing.models import Payment, Refund

    with transaction.atomic():
        locked_account = FinancialAccount.objects.select_for_update().get(pk=account.pk)

        # 1. Fetch candidate UNSETTLED verified customer payments
        candidate_payment_ids = list(
            Payment.objects.filter(
                branch=branch,
                payment_method__method_type=payment_method_type,
                status=Payment.Status.VERIFIED,
                paid_at__date__gte=start_date,
                paid_at__date__lte=end_date,
            ).exclude(
                id__in=SettledBillingPayment.objects.values("payment_id")
            ).order_by("id").values_list("id", flat=True)
        )

        unsettled_payments = []
        if candidate_payment_ids:
            # Row lock on billing_payment without outer join
            locked_payments = list(
                Payment.objects.filter(id__in=candidate_payment_ids)
                .select_for_update()
                .order_by("id")
            )
            # Re-verify settled status under row lock in case of concurrent commit
            already_settled_pids = set(
                SettledBillingPayment.objects.filter(
                    payment_id__in=[p.id for p in locked_payments]
                ).values_list("payment_id", flat=True)
            )
            unsettled_payments = [p for p in locked_payments if p.id not in already_settled_pids]

        # 2. Fetch candidate UNSETTLED completed refunds
        candidate_refund_ids = list(
            Refund.objects.filter(
                payment__branch=branch,
                payment__payment_method__method_type=payment_method_type,
                status=Refund.Status.COMPLETED,
                completed_at__date__gte=start_date,
                completed_at__date__lte=end_date,
            ).exclude(
                id__in=SettledBillingRefund.objects.values("refund_id")
            ).order_by("id").values_list("id", flat=True)
        )

        unsettled_refunds = []
        if candidate_refund_ids:
            # Row lock on billing_refund without outer join
            locked_refunds = list(
                Refund.objects.filter(id__in=candidate_refund_ids)
                .select_for_update()
                .order_by("id")
            )
            # Re-verify settled status under row lock in case of concurrent commit
            already_settled_rids = set(
                SettledBillingRefund.objects.filter(
                    refund_id__in=[r.id for r in locked_refunds]
                ).values_list("refund_id", flat=True)
            )
            unsettled_refunds = [r for r in locked_refunds if r.id not in already_settled_rids]

        payment_count = len(unsettled_payments)
        refund_count = len(unsettled_refunds)

        if payment_count == 0 and refund_count == 0:
            raise ValidationError(
                _(f"No unsettled verified {payment_method_type} transactions found for {branch.name} "
                  f"between {start_date} and {end_date}. All transactions in this date range have already been settled.")
            )

        total_payments = sum((p.amount for p in unsettled_payments), Decimal("0.00"))
        total_refunds = sum((r.amount for r in unsettled_refunds), Decimal("0.00"))
        net_collections = total_payments - total_refunds

        # 3. Create durable BillingSettlementBatch
        batch_num = generate_next_finance_number("billing_settlement_batch", "SETTLE")
        batch = BillingSettlementBatch.objects.create(
            batch_number=batch_num,
            branch=branch,
            financial_account=locked_account,
            payment_method_type=payment_method_type,
            period_start=start_date,
            period_end=end_date,
            payment_count=payment_count,
            total_payments=total_payments,
            refund_count=refund_count,
            total_refunds=total_refunds,
            net_collections=net_collections,
            settled_by=actor,
            notes=notes,
        )

        # 4. Durable per-transaction tracking records (Database UNIQUE constraint protects against any double-settlement)
        for p in unsettled_payments:
            SettledBillingPayment.objects.create(
                payment=p,
                settlement_batch=batch,
                amount=p.amount,
            )

        for r in unsettled_refunds:
            SettledBillingRefund.objects.create(
                refund=r,
                settlement_batch=batch,
                amount=r.amount,
            )

        # 5. Create AccountAdjustment and update balance if net is non-zero
        adjustment = None
        if net_collections != Decimal("0.00"):
            adj_type = AccountAdjustment.AdjustmentType.CREDIT if net_collections > Decimal("0.00") else AccountAdjustment.AdjustmentType.DEBIT
            abs_net = abs(net_collections)

            adj_num = generate_next_finance_number("account_adjustment", "ADJ")
            adjustment = AccountAdjustment.objects.create(
                adjustment_number=adj_num,
                account=locked_account,
                adjustment_type=adj_type,
                amount=abs_net,
                adjustment_date=end_date,
                reason=f"Authoritative billing settlement [{batch.batch_number}]: {payment_count} payments ({total_payments} ETB), {refund_count} refunds ({total_refunds} ETB)",
                performed_by=actor,
                approved_by=actor,
            )
            batch.adjustment = adjustment
            batch.save(update_fields=["adjustment", "updated_at"])

            if adj_type == AccountAdjustment.AdjustmentType.CREDIT:
                locked_account.current_balance += abs_net
            else:
                if not locked_account.allow_negative and (locked_account.current_balance - abs_net < Decimal("0.00")):
                    raise ValidationError({"account": _("Debit settlement would cause a negative balance.")})
                locked_account.current_balance -= abs_net
            locked_account.save(update_fields=["current_balance", "updated_at"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.ADJUSTMENT_APPLIED,
            actor=actor,
            branch=branch,
            target_type="BillingSettlementBatch",
            target_id=str(batch.id),
            action_summary=f"Settled {batch.batch_number} ({net_collections} ETB, {payment_count} pays, {refund_count} refs) into {locked_account.name}",
            metadata={"batch_number": batch.batch_number, "net": str(net_collections)},
        )

        return {
            "settlement_reference": batch.batch_number,
            "batch": batch,
            "payment_count": payment_count,
            "total_payments": total_payments,
            "refund_count": refund_count,
            "total_refunds": total_refunds,
            "net_collections": net_collections,
            "adjustment": adjustment,
        }
