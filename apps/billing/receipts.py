import io
from decimal import Decimal
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generate_invoice_pdf(invoice) -> bytes:
    """Generate professional PDF for an invoice."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#164E63"),
    )
    subtitle_style = ParagraphStyle(
        "InvoiceSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
    )
    label_style = ParagraphStyle(
        "FieldLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748B"),
    )
    value_style = ParagraphStyle(
        "FieldValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0F172A"),
    )

    elements = []

    # Header
    header_data = [
        [
            Paragraph(f"<b>{invoice.branch.name}</b><br/>{invoice.branch.address or ''}", subtitle_style),
            Paragraph(f"<b>INVOICE</b><br/><font size=12 color='#0F766E'><b>{invoice.invoice_number}</b></font>", title_style),
        ]
    ]
    header_table = Table(header_data, colWidths=[4.0 * inch, 3.5 * inch])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_table)
    elements.append(Spacer(1, 15))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceAfter=15))

    # Meta Info
    meta_data = [
        [
            Paragraph("<b>BILLED TO:</b>", label_style),
            Paragraph("<b>INVOICE DETAILS:</b>", label_style),
        ],
        [
            Paragraph(
                f"<b>{invoice.member.full_name}</b><br/>"
                f"Member #: {invoice.member.member_number}<br/>"
                f"Phone: {invoice.member.phone or 'N/A'}",
                value_style,
            ),
            Paragraph(
                f"Issue Date: {invoice.issue_date.strftime('%Y-%m-%d')}<br/>"
                f"Due Date: {invoice.due_date.strftime('%Y-%m-%d')}<br/>"
                f"Status: <b>{invoice.get_status_display()}</b>",
                value_style,
            ),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[4.0 * inch, 3.5 * inch])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(meta_table)
    elements.append(Spacer(1, 20))

    # Items Table
    items_data = [
        [
            Paragraph("<b>Description</b>", label_style),
            Paragraph("<b>Qty</b>", label_style),
            Paragraph("<b>Unit Price (ETB)</b>", label_style),
            Paragraph("<b>Discount (ETB)</b>", label_style),
            Paragraph("<b>Total (ETB)</b>", label_style),
        ]
    ]

    for item in invoice.items.all():
        items_data.append([
            Paragraph(item.description, value_style),
            Paragraph(str(item.quantity), value_style),
            Paragraph(f"{item.unit_price:,.2f}", value_style),
            Paragraph(f"{item.discount_amount:,.2f}", value_style),
            Paragraph(f"{item.line_total:,.2f}", value_style),
        ])

    items_table = Table(items_data, colWidths=[3.2 * inch, 0.6 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 15))

    # Totals Summary
    totals_data = [
        [Paragraph("Subtotal:", label_style), Paragraph(f"{invoice.subtotal:,.2f} ETB", value_style)],
        [Paragraph("Discount:", label_style), Paragraph(f"-{invoice.discount_amount:,.2f} ETB", value_style)],
        [Paragraph("Tax:", label_style), Paragraph(f"{invoice.tax_amount:,.2f} ETB", value_style)],
        [Paragraph("<b>Total Due:</b>", label_style), Paragraph(f"<b>{invoice.total_amount:,.2f} ETB</b>", value_style)],
        [Paragraph("Paid to Date:", label_style), Paragraph(f"{invoice.paid_amount:,.2f} ETB", value_style)],
        [Paragraph("<b>Outstanding Balance:</b>", label_style), Paragraph(f"<b><font color='#DC2626'>{invoice.balance_due:,.2f} ETB</font></b>", value_style)],
    ]
    totals_table = Table(totals_data, colWidths=[2.0 * inch, 1.8 * inch])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    wrapper_table = Table([["", totals_table]], colWidths=[3.7 * inch, 3.8 * inch])
    elements.append(wrapper_table)

    if invoice.notes:
        elements.append(Spacer(1, 15))
        elements.append(Paragraph(f"<b>Notes:</b> {invoice.notes}", subtitle_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def generate_receipt_pdf(payment) -> bytes:
    """Generate official payment receipt PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F766E"),
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
    )
    label_style = ParagraphStyle(
        "FieldLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748B"),
    )
    value_style = ParagraphStyle(
        "FieldValue",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0F172A"),
    )

    from .models import Receipt
    if isinstance(payment, Receipt):
        receipt = payment
        payment = receipt.payment
    else:
        receipt = getattr(payment, "receipt", None)

    receipt_num = receipt.receipt_number if receipt else f"REC-{payment.payment_number}"

    elements = []

    # Header
    header_data = [
        [
            Paragraph(f"<b>{payment.branch.name}</b><br/>{payment.branch.address or ''}", subtitle_style),
            Paragraph(f"<b>PAYMENT RECEIPT</b><br/><font size=12 color='#0F766E'><b>{receipt_num}</b></font>", title_style),
        ]
    ]
    header_table = Table(header_data, colWidths=[4.0 * inch, 3.5 * inch])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_table)
    elements.append(Spacer(1, 15))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceAfter=15))

    # Meta Details
    meta_data = [
        [
            Paragraph("<b>RECEIVED FROM:</b>", label_style),
            Paragraph("<b>RECEIPT DETAILS:</b>", label_style),
        ],
        [
            Paragraph(
                f"<b>{payment.invoice.member.full_name}</b><br/>"
                f"Member #: {payment.invoice.member.member_number}<br/>"
                f"Phone: {payment.invoice.member.phone or 'N/A'}",
                value_style,
            ),
            Paragraph(
                f"Receipt #: <b>{receipt_num}</b><br/>"
                f"Payment Ref: {payment.payment_number}<br/>"
                f"Invoice #: {payment.invoice.invoice_number}<br/>"
                f"Date: {payment.paid_at.strftime('%Y-%m-%d %H:%M')}<br/>"
                f"Status: <b>{payment.get_status_display()}</b>",
                value_style,
            ),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[4.0 * inch, 3.5 * inch])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(meta_table)
    elements.append(Spacer(1, 20))

    # Payment Summary Table
    pay_data = [
        [
            Paragraph("<b>Payment Method</b>", label_style),
            Paragraph("<b>Transaction Reference</b>", label_style),
            Paragraph("<b>Amount Paid (ETB)</b>", label_style),
        ],
        [
            Paragraph(payment.payment_method.name, value_style),
            Paragraph(payment.transaction_reference or "N/A (Cash / In-Person)", value_style),
            Paragraph(f"<b>{payment.amount:,.2f} ETB</b>", value_style),
        ],
    ]
    pay_table = Table(pay_data, colWidths=[2.5 * inch, 3.0 * inch, 2.0 * inch])
    pay_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(pay_table)
    elements.append(Spacer(1, 20))

    # Invoice Balance Status
    inv = payment.invoice
    inv_data = [
        [Paragraph("Invoice Total:", label_style), Paragraph(f"{inv.total_amount:,.2f} ETB", value_style)],
        [Paragraph("Total Amount Paid:", label_style), Paragraph(f"{inv.paid_amount:,.2f} ETB", value_style)],
        [Paragraph("Remaining Balance:", label_style), Paragraph(f"<b>{inv.balance_due:,.2f} ETB</b>", value_style)],
    ]
    inv_table = Table(inv_data, colWidths=[2.0 * inch, 1.8 * inch])
    inv_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    wrapper_table = Table([["", inv_table]], colWidths=[3.7 * inch, 3.8 * inch])
    elements.append(wrapper_table)
    elements.append(Spacer(1, 25))

    # Attestation
    verified_by_name = payment.verified_by.get_full_name() if payment.verified_by else "System Administrator"
    elements.append(Paragraph(
        f"<i>Received by: {payment.received_by.get_full_name() if payment.received_by else 'Staff'} | "
        f"Verified by: {verified_by_name} | "
        f"Official computerized receipt issued on {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}</i>",
        subtitle_style,
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
