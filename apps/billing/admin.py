from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.accounts.roles import is_owner
from apps.branches.services import accessible_branches
from .forms import InvoiceCreateForm, RecordPaymentForm, RefundRequestForm
from .models import (
    FinancialAuditEvent,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAdjustment,
    PaymentMethod,
    Receipt,
    Refund,
)
from .receipts import generate_invoice_pdf, generate_receipt_pdf
from .services import (
    approve_refund,
    create_invoice,
    issue_invoice,
    record_payment,
    reject_payment,
    request_refund,
    verify_payment,
    void_invoice,
)


class InvoiceItemInline(TabularInline):
    model = InvoiceItem
    extra = 0
    classes = ("collapse",)
    collapsible = True
    fields = ("description", "item_type", "quantity", "unit_price", "discount_amount", "tax_amount", "line_total")
    readonly_fields = ("line_total",)


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = (
        "invoice_number",
        "member_display",
        "branch",
        "total_amount_display",
        "paid_amount_display",
        "balance_due_display",
        "status_badge",
        "issue_date",
        "due_date",
        "invoice_actions",
    )
    list_filter = ("status", "branch", "issue_date", "due_date")
    search_fields = (
        "invoice_number",
        "member__first_name",
        "member__last_name",
        "member__member_number",
        "notes",
    )
    readonly_fields = (
        "invoice_number",
        "subtotal",
        "paid_amount",
        "balance_due",
        "created_by",
        "issued_by",
        "created_at",
        "updated_at",
    )
    inlines = [InvoiceItemInline]

    fieldsets = (
        ("Invoice Header / የደረሰኝ ራስጌ", {
            "classes": ("collapse",),
            "description": "Invoice tracking number, recipient member, and branch context.",
            "fields": (
                ("invoice_number", "branch"),
                ("member", "status"),
                ("issue_date", "due_date"),
            ),
        }),
        ("Financial Totals / የክፍያ ድምር እና ቀሪ", {
            "classes": ("collapse",),
            "description": "Calculated subtotal, discounts, tax, and outstanding balance in ETB.",
            "fields": (
                ("subtotal", "total_amount"),
                ("paid_amount", "balance_due"),
            ),
        }),
        ("Associated Subscriptions & Notes / የተያያዙ አባልነቶች እና ማስታወሻዎች", {
            "classes": ("collapse",),
            "fields": (
                ("membership", "renewal"),
                "notes",
            ),
        }),
        ("Audit Metadata / ኦዲት እና ቁጥጥር", {
            "classes": ("collapse",),
            "description": "System issuance and user timestamps.",
            "fields": (
                ("created_by", "issued_by"),
                ("created_at", "updated_at"),
            ),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("member", "branch", "membership", "renewal")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(branch__in=accessible_branches(request.user))

    @display(description="Member")
    def member_display(self, obj):
        return format_html(
            "<strong>{}</strong><br/><small class='text-gray-500'>{}</small>",
            obj.member.full_name,
            obj.member.member_number,
        )

    @display(description="Total")
    def total_amount_display(self, obj):
        return f"{obj.total_amount:,.2f} ETB"

    @display(description="Paid")
    def paid_amount_display(self, obj):
        return f"{obj.paid_amount:,.2f} ETB"

    @display(description="Balance Due")
    def balance_due_display(self, obj):
        if obj.balance_due > 0:
            return format_html("<span class='text-red-600 font-semibold'>{:.2f} ETB</span>", obj.balance_due)
        return format_html("<span class='text-green-600 font-semibold'>0.00 ETB</span>")

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            Invoice.Status.DRAFT: "bg-gray-100 text-gray-800",
            Invoice.Status.ISSUED: "bg-blue-100 text-blue-800",
            Invoice.Status.PARTIALLY_PAID: "bg-amber-100 text-amber-800",
            Invoice.Status.PAID: "bg-green-100 text-green-800",
            Invoice.Status.VOID: "bg-gray-200 text-gray-600 line-through",
            Invoice.Status.REFUNDED: "bg-purple-100 text-purple-800",
            Invoice.Status.PARTIALLY_REFUNDED: "bg-purple-50 text-purple-700",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html("<span class='px-2 py-1 text-xs font-semibold rounded-full {}'>{}</span>", cls, obj.get_status_display())

    @display(description="Actions")
    def invoice_actions(self, obj):
        links = []
        if obj.status in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID) and obj.balance_due > 0:
            pay_url = reverse("admin:billing_invoice_record_payment", args=[obj.pk])
            links.append(f"<a href='{pay_url}' class='text-teal-600 font-semibold hover:underline mr-2'>Pay</a>")

        pdf_url = reverse("admin:billing_invoice_pdf", args=[obj.pk])
        links.append(f"<a href='{pdf_url}' target='_blank' class='text-blue-600 hover:underline'>PDF</a>")
        return format_html("".join(links))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:invoice_id>/pay/", self.admin_site.admin_view(self.record_payment_view), name="billing_invoice_record_payment"),
            path("<uuid:invoice_id>/pdf/", self.admin_site.admin_view(self.pdf_invoice_view), name="billing_invoice_pdf"),
            path("create-invoice/", self.admin_site.admin_view(self.create_invoice_view), name="billing_invoice_create_invoice"),
        ]
        return custom + urls

    def record_payment_view(self, request, invoice_id):
        invoice = get_object_or_404(Invoice, pk=invoice_id)
        if request.method == "POST":
            form = RecordPaymentForm(request.POST, invoice=invoice)
            if form.is_valid():
                try:
                    payment = record_payment(
                        actor=request.user,
                        invoice=invoice,
                        amount=form.cleaned_data["amount"],
                        payment_method=form.cleaned_data["payment_method"],
                        transaction_reference=form.cleaned_data.get("transaction_reference", ""),
                        notes=form.cleaned_data.get("notes", ""),
                    )
                    messages.success(request, f"Payment {payment.payment_number} recorded ({payment.get_status_display()}).")
                    return HttpResponseRedirect(reverse("admin:billing_invoice_changelist"))
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))
        else:
            form = RecordPaymentForm(invoice=invoice)

        context = {
            **self.admin_site.each_context(request),
            "invoice": invoice,
            "form": form,
            "title": f"Record Payment for {invoice.invoice_number}",
        }
        return render(request, "admin/billing/record_payment.html", context)

    def pdf_invoice_view(self, request, invoice_id):
        invoice = get_object_or_404(Invoice, pk=invoice_id)
        pdf_bytes = generate_invoice_pdf(invoice)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.invoice_number}.pdf"'
        return response

    def create_invoice_view(self, request):
        if request.method == "POST":
            form = InvoiceCreateForm(request.POST, user=request.user)
            if form.is_valid():
                try:
                    items = [
                        {
                            "description": form.cleaned_data["item_description"],
                            "unit_price": form.cleaned_data["unit_price"],
                            "quantity": form.cleaned_data["quantity"],
                        }
                    ]
                    invoice = create_invoice(
                        actor=request.user,
                        member=form.cleaned_data["member"],
                        branch=form.cleaned_data["branch"],
                        items=items,
                        due_date=form.cleaned_data.get("due_date"),
                        discount_amount=form.cleaned_data.get("discount_amount") or 0,
                        tax_amount=form.cleaned_data.get("tax_amount") or 0,
                        notes=form.cleaned_data.get("notes", ""),
                        issue_now=True,
                    )
                    messages.success(request, f"Invoice {invoice.invoice_number} successfully created and issued.")
                    return HttpResponseRedirect(reverse("admin:billing_invoice_changelist"))
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))
        else:
            form = InvoiceCreateForm(user=request.user)

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Create Customer Invoice",
        }
        return render(request, "admin/billing/create_invoice.html", context)


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = (
        "payment_number",
        "invoice_link",
        "member_display",
        "branch",
        "amount_display",
        "payment_method",
        "transaction_reference",
        "status_badge",
        "paid_at",
        "payment_actions",
    )
    list_filter = ("status", "payment_method", "branch", "paid_at")
    search_fields = (
        "payment_number",
        "transaction_reference",
        "invoice__invoice_number",
        "invoice__member__first_name",
        "invoice__member__last_name",
        "notes",
    )
    readonly_fields = (
        "payment_number",
        "invoice",
        "branch",
        "amount",
        "payment_method",
        "transaction_reference",
        "received_by",
        "verified_by",
        "verified_at",
        "paid_at",
        "idempotency_key",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        ("Payment Transaction / የክፍያ ዝውውር መረጃ", {
            "classes": ("collapse",),
            "description": "Receipt number, associated invoice, amount, and payment method.",
            "fields": (
                ("payment_number", "branch"),
                ("invoice", "payment_method"),
                ("amount", "status"),
                "transaction_reference",
            ),
        }),
        ("Verification & Verification Audit / ማረጋገጫ እና ቁጥጥር", {
            "classes": ("collapse",),
            "description": "Verification authorization, collection staff, and notes.",
            "fields": (
                ("received_by", "paid_at"),
                ("verified_by", "verified_at"),
                "notes",
            ),
        }),
        ("Security & Audit Metadata / የደህንነት እና ኦዲት መረጃ", {
            "classes": ("collapse",),
            "fields": (
                "idempotency_key",
                ("created_at", "updated_at"),
            ),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("invoice", "branch", "payment_method", "invoice__member")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(branch__in=accessible_branches(request.user))

    @display(description="Invoice")
    def invoice_link(self, obj):
        url = reverse("admin:billing_invoice_change", args=[obj.invoice.pk])
        return format_html("<a href='{}' class='text-teal-600 font-semibold'>{}</a>", url, obj.invoice.invoice_number)

    @display(description="Member")
    def member_display(self, obj):
        return obj.invoice.member.full_name

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            Payment.Status.PENDING: "bg-amber-100 text-amber-800",
            Payment.Status.VERIFIED: "bg-green-100 text-green-800",
            Payment.Status.REJECTED: "bg-red-100 text-red-800",
            Payment.Status.VOIDED: "bg-gray-200 text-gray-600",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html("<span class='px-2 py-1 text-xs font-semibold rounded-full {}'>{}</span>", cls, obj.get_status_display())

    @display(description="Actions")
    def payment_actions(self, obj):
        links = []
        if obj.status == Payment.Status.PENDING:
            v_url = reverse("admin:billing_payment_verify", args=[obj.pk])
            links.append(f"<a href='{v_url}' class='text-teal-600 font-semibold hover:underline mr-2'>Verify</a>")

        if obj.status == Payment.Status.VERIFIED:
            rec_url = reverse("admin:billing_payment_receipt_pdf", args=[obj.pk])
            links.append(f"<a href='{rec_url}' target='_blank' class='text-blue-600 font-semibold hover:underline mr-2'>Receipt</a>")
            ref_url = reverse("admin:billing_payment_request_refund", args=[obj.pk])
            links.append(f"<a href='{ref_url}' class='text-purple-600 hover:underline'>Refund</a>")

        return format_html("".join(links))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:payment_id>/verify/", self.admin_site.admin_view(self.verify_payment_view), name="billing_payment_verify"),
            path("<uuid:payment_id>/refund/", self.admin_site.admin_view(self.request_refund_view), name="billing_payment_request_refund"),
            path("<uuid:payment_id>/receipt-pdf/", self.admin_site.admin_view(self.pdf_receipt_view), name="billing_payment_receipt_pdf"),
        ]
        return custom + urls

    def verify_payment_view(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        if request.method == "POST":
            action_type = request.POST.get("action")
            notes = request.POST.get("notes", "")
            try:
                if action_type == "verify":
                    verify_payment(actor=request.user, payment=payment, notes=notes)
                    messages.success(request, f"Payment {payment.payment_number} verified successfully.")
                elif action_type == "reject":
                    reason = request.POST.get("reason", "")
                    reject_payment(actor=request.user, payment=payment, reason=reason)
                    messages.warning(request, f"Payment {payment.payment_number} marked as REJECTED.")
                return HttpResponseRedirect(reverse("admin:billing_payment_changelist"))
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        context = {
            **self.admin_site.each_context(request),
            "payment": payment,
            "title": f"Review Payment {payment.payment_number}",
        }
        return render(request, "admin/billing/verify_payment.html", context)

    def request_refund_view(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        if request.method == "POST":
            form = RefundRequestForm(request.POST, payment=payment)
            if form.is_valid():
                try:
                    ref = request_refund(
                        actor=request.user,
                        payment=payment,
                        amount=form.cleaned_data["amount"],
                        reason=form.cleaned_data["reason"],
                    )
                    messages.success(request, f"Refund request {ref.refund_number} submitted.")
                    return HttpResponseRedirect(reverse("admin:billing_refund_changelist"))
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))
        else:
            form = RefundRequestForm(payment=payment)

        context = {
            **self.admin_site.each_context(request),
            "payment": payment,
            "form": form,
            "title": f"Request Refund for {payment.payment_number}",
        }
        return render(request, "admin/billing/request_refund.html", context)

    def pdf_receipt_view(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        pdf_bytes = generate_receipt_pdf(payment)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="receipt_{payment.payment_number}.pdf"'
        return response


@admin.register(PaymentMethod)
class PaymentMethodAdmin(ModelAdmin):
    list_display = ("name", "code", "method_type", "requires_reference", "requires_verification", "is_active")
    list_filter = ("method_type", "is_active")
    search_fields = ("name", "code")

    fieldsets = (
        ("Method Identity / የክፍያ ዘዴ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("name", "code"),
                "method_type",
            ),
        }),
        ("Policy & Controls / ደንቦች እና ቁጥጥር", {
            "classes": ("collapse",),
            "fields": (
                ("requires_reference", "requires_verification"),
                "is_active",
            ),
        }),
    )


@admin.register(Refund)
class RefundAdmin(ModelAdmin):
    list_display = (
        "refund_number",
        "payment_link",
        "invoice_link",
        "amount_display",
        "status_badge",
        "requested_by",
        "approved_by",
        "created_at",
        "refund_actions",
    )
    list_filter = ("status", "created_at")
    search_fields = ("refund_number", "payment__payment_number", "invoice__invoice_number", "reason")
    readonly_fields = (
        "refund_number",
        "payment",
        "invoice",
        "amount",
        "requested_by",
        "approved_by",
        "completed_by",
        "approved_at",
        "completed_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        ("Refund Request / የተመላሽ ጥያቄ መረጃ", {
            "classes": ("collapse",),
            "description": "Refund tracking ID, linked payment, invoice, and reimbursable amount.",
            "fields": (
                ("refund_number", "status"),
                ("payment", "invoice"),
                "amount",
                "reason",
            ),
        }),
        ("Approval & Completion / የማጽደቅ እና የማጠናቀቅ ሂደት", {
            "classes": ("collapse",),
            "description": "Manager authorization timestamps and completion audit.",
            "fields": (
                ("requested_by", "approved_by"),
                ("approved_at", "completed_at"),
                "completed_by",
            ),
        }),
        ("Audit Metadata / ኦዲት እና ቁጥጥር", {
            "classes": ("collapse",),
            "fields": (
                ("created_at", "updated_at"),
            ),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("payment", "invoice", "requested_by", "approved_by")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(invoice__branch__in=accessible_branches(request.user))

    @display(description="Payment")
    def payment_link(self, obj):
        url = reverse("admin:billing_payment_change", args=[obj.payment.pk])
        return format_html("<a href='{}' class='text-teal-600 font-semibold'>{}</a>", url, obj.payment.payment_number)

    @display(description="Invoice")
    def invoice_link(self, obj):
        url = reverse("admin:billing_invoice_change", args=[obj.invoice.pk])
        return format_html("<a href='{}' class='text-teal-600 font-semibold'>{}</a>", url, obj.invoice.invoice_number)

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            Refund.Status.REQUESTED: "bg-amber-100 text-amber-800",
            Refund.Status.APPROVED: "bg-blue-100 text-blue-800",
            Refund.Status.COMPLETED: "bg-green-100 text-green-800",
            Refund.Status.REJECTED: "bg-red-100 text-red-800",
            Refund.Status.FAILED: "bg-gray-200 text-gray-700",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html("<span class='px-2 py-1 text-xs font-semibold rounded-full {}'>{}</span>", cls, obj.get_status_display())

    @display(description="Actions")
    def refund_actions(self, obj):
        if obj.status == Refund.Status.REQUESTED:
            url = reverse("admin:billing_refund_approve", args=[obj.pk])
            return format_html("<a href='{}' class='text-teal-600 font-semibold hover:underline'>Review & Approve</a>", url)
        return "—"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:refund_id>/approve/", self.admin_site.admin_view(self.approve_refund_view), name="billing_refund_approve"),
        ]
        return custom + urls

    def approve_refund_view(self, request, refund_id):
        refund = get_object_or_404(Refund, pk=refund_id)
        if request.method == "POST":
            try:
                approve_refund(actor=request.user, refund=refund)
                messages.success(request, f"Refund {refund.refund_number} approved and completed.")
                return HttpResponseRedirect(reverse("admin:billing_refund_changelist"))
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        context = {
            **self.admin_site.each_context(request),
            "refund": refund,
            "title": f"Approve Refund {refund.refund_number}",
        }
        return render(request, "admin/billing/approve_refund.html", context)


@admin.register(Receipt)
class ReceiptAdmin(ModelAdmin):
    list_display = ("receipt_number", "payment_link", "amount_display", "issued_at", "issued_by", "void_status", "receipt_actions")
    search_fields = ("receipt_number", "payment__payment_number", "payment__invoice__member__first_name")
    readonly_fields = ("receipt_number", "payment", "issued_at", "issued_by", "receipt_snapshot", "voided_at")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:receipt_id>/pdf/", self.admin_site.admin_view(self.pdf_receipt_view), name="billing_receipt_pdf"),
        ]
        return custom + urls

    def pdf_receipt_view(self, request, receipt_id):
        receipt = get_object_or_404(Receipt, pk=receipt_id)
        pdf_bytes = generate_receipt_pdf(receipt)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="receipt_{receipt.receipt_number}.pdf"'
        return response

    @display(description="Payment")
    def payment_link(self, obj):
        url = reverse("admin:billing_payment_change", args=[obj.payment.pk])
        return format_html("<a href='{}' class='text-teal-600 font-semibold'>{}</a>", url, obj.payment.payment_number)

    @display(description="Amount")
    def amount_display(self, obj):
        return f"{obj.payment.amount:,.2f} ETB"

    @display(description="Void")
    def void_status(self, obj):
        if obj.is_void:
            return format_html("<span class='text-red-600 font-semibold'>VOIDED</span>")
        return format_html("<span class='text-green-600 font-semibold'>VALID</span>")

    @display(description="Actions")
    def receipt_actions(self, obj):
        pdf_url = reverse("admin:billing_receipt_pdf", args=[obj.pk])
        return format_html(
            "<a href='{}' target='_blank' class='inline-flex items-center px-2 py-1 text-xs font-semibold rounded bg-teal-50 text-teal-700 hover:bg-teal-100'>"
            "<span class='material-symbols-outlined text-xs mr-1'>picture_as_pdf</span> PDF</a>",
            pdf_url,
        )


@admin.register(FinancialAuditEvent)
class FinancialAuditEventAdmin(ModelAdmin):
    list_display = ("event_type", "invoice", "payment", "actor", "branch", "created_at")
    list_filter = ("event_type", "branch", "created_at")
    search_fields = ("invoice__invoice_number", "payment__payment_number", "actor__email")
    readonly_fields = ("event_type", "invoice", "payment", "actor", "branch", "metadata", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
