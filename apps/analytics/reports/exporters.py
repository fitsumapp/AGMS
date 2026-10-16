import csv
import io
from decimal import Decimal
from typing import Any, Dict, List
from django.http import HttpResponse
from django.utils import timezone

from apps.analytics.dates import get_gym_timezone
from .registry import ReportDefinition


def sanitize_csv_cell(value: Any) -> Any:
    """
    Mitigate CSV Formula Injection (CWE-1236).
    If a cell begins with '=', '+', '-', '@', '\t', or '\r', prepend a single quote.
    """
    if value is None:
        return ""
    str_val = str(value)
    if str_val and str_val[0] in ("=", "+", "-", "@", "\t", "\r"):
        # If it's a negative number like -50.00, check if it's purely a numeric float/int
        try:
            float(str_val)
            return str_val
        except ValueError:
            return f"'{str_val}"
    return str_val


def export_to_csv(
    report_def: ReportDefinition,
    rows: List[Dict[str, Any]],
    gym_name: str,
    branch_label: str,
    date_label: str,
) -> HttpResponse:
    """Generate secure CSV export with formula injection mitigation."""
    output = io.StringIO()
    writer = csv.writer(output, dialect="excel")

    # Header metadata comments
    writer.writerow([f"# Gym ERP: {gym_name}"])
    writer.writerow([f"# Report: {report_def.title}"])
    writer.writerow([f"# Scope: {branch_label}"])
    writer.writerow([f"# Period: {date_label}"])
    writer.writerow([f"# Generated At: {timezone.now().astimezone(get_gym_timezone()):%Y-%m-%d %H:%M:%S EAT}"])
    writer.writerow([])

    # Table columns header
    headers = [col.label for col in report_def.columns]
    writer.writerow(headers)

    # Data rows
    for r in rows:
        row_vals = []
        for col in report_def.columns:
            val = r.get(col.key)
            if col.is_currency and isinstance(val, (Decimal, int, float)):
                row_vals.append(f"{val:.2f}")
            else:
                row_vals.append(sanitize_csv_cell(val))
        writer.writerow(row_vals)

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    filename = f"{report_def.code}_{timezone.now():%Y%m%d_%H%M}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_to_xlsx(
    report_def: ReportDefinition,
    rows: List[Dict[str, Any]],
    gym_name: str,
    branch_label: str,
    date_label: str,
) -> HttpResponse:
    """Generate styled Excel spreadsheet (.xlsx) using openpyxl."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = report_def.title[:30]

    # Colors & Styles
    dark_teal = "0F766E"
    light_teal_fill = "F0FDFA"
    header_fill = PatternFill(start_color=dark_teal, end_color=dark_teal, fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    meta_font = Font(name="Segoe UI", size=10, bold=False, color="475569")
    title_font = Font(name="Segoe UI", size=14, bold=True, color="0F172A")
    regular_font = Font(name="Segoe UI", size=10)
    total_font = Font(name="Segoe UI", size=10, bold=True, color="0F172A")

    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )
    total_border = Border(
        top=Side(style="thin", color="0F766E"),
        bottom=Side(style="double", color="0F766E"),
    )

    # 1. Metadata Block
    ws.append([gym_name])
    ws.cell(row=1, column=1).font = title_font

    ws.append([f"Report: {report_def.title}"])
    ws.cell(row=2, column=1).font = Font(name="Segoe UI", size=11, bold=True, color="0F766E")

    ws.append([f"Branch Scope: {branch_label}   |   Date Range: {date_label}"])
    ws.cell(row=3, column=1).font = meta_font

    tz_now = timezone.now().astimezone(get_gym_timezone())
    ws.append([f"Generated At: {tz_now:%Y-%m-%d %H:%M:%S} Africa/Addis_Ababa"])
    ws.cell(row=4, column=1).font = meta_font

    ws.append([])  # Blank row 5

    # 2. Table Column Headers (Row 6)
    header_row_idx = 6
    headers = [col.label for col in report_def.columns]
    ws.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row_idx, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        col_def = report_def.columns[col_idx - 1]
        align_choice = "right" if col_def.align == "right" else "center" if col_def.align == "center" else "left"
        cell.alignment = Alignment(horizontal=align_choice, vertical="center")

    # 3. Data Rows
    current_row = header_row_idx + 1
    totals: Dict[int, Decimal] = {}

    for r in rows:
        row_vals = []
        for c_idx, col in enumerate(report_def.columns, start=1):
            val = r.get(col.key)
            if col.is_currency:
                dec_val = Decimal(str(val)) if val is not None else Decimal("0.00")
                row_vals.append(float(dec_val))
                totals[c_idx] = totals.get(c_idx, Decimal("0.00")) + dec_val
            elif col.align == "right" and isinstance(val, (int, float)):
                row_vals.append(val)
                totals[c_idx] = totals.get(c_idx, Decimal("0.00")) + Decimal(str(val))
            else:
                row_vals.append(sanitize_csv_cell(val))

        ws.append(row_vals)

        # Style data cells
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=c_idx)
            cell.font = regular_font
            cell.border = thin_border
            col_def = report_def.columns[c_idx - 1]
            if col_def.is_currency:
                cell.number_format = '#,##0.00 "ETB"'
                cell.alignment = Alignment(horizontal="right")
            elif col_def.align == "right":
                cell.alignment = Alignment(horizontal="right")
            elif col_def.align == "center":
                cell.alignment = Alignment(horizontal="center")

        current_row += 1

    # 4. Summary Totals Row (if any currency columns exist)
    if any(col.is_currency for col in report_def.columns) and rows:
        summary_vals = []
        for c_idx, col in enumerate(report_def.columns, start=1):
            if c_idx == 1:
                summary_vals.append("Total Summary")
            elif col.is_currency:
                summary_vals.append(float(totals.get(c_idx, Decimal("0.00"))))
            else:
                summary_vals.append("")
        ws.append(summary_vals)
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=c_idx)
            cell.font = total_font
            cell.border = total_border
            col_def = report_def.columns[c_idx - 1]
            if col_def.is_currency:
                cell.number_format = '#,##0.00 "ETB"'
                cell.alignment = Alignment(horizontal="right")

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename = f"{report_def.code}_{timezone.now():%Y%m%d_%H%M}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def export_to_pdf(
    report_def: ReportDefinition,
    rows: List[Dict[str, Any]],
    gym_name: str,
    branch_label: str,
    date_label: str,
    user=None,
) -> HttpResponse:
    """Generate high-resolution printable PDF document using ReportLab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output = io.BytesIO()
    # Use landscape A4 (842 x 595 points)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0F766E"),
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
    )
    header_cell_style = ParagraphStyle(
        "HeaderCell",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1,
    )
    data_cell_style = ParagraphStyle(
        "DataCell",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
    )
    data_cell_right = ParagraphStyle(
        "DataCellRight",
        parent=data_cell_style,
        alignment=2,
    )

    elements = []

    # 1. Header Information
    tz_now = timezone.now().astimezone(get_gym_timezone())
    user_str = user.email if user and user.is_authenticated else "System"

    elements.append(Paragraph(f"{gym_name} — {report_def.title}", title_style))
    elements.append(
        Paragraph(
            f"<b>Scope:</b> {branch_label} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Date Range:</b> {date_label} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Generated:</b> {tz_now:%Y-%m-%d %H:%M} EAT by {user_str}",
            subtitle_style,
        )
    )
    elements.append(Spacer(1, 14))

    # 2. Table Construction
    headers = [Paragraph(col.label, header_cell_style) for col in report_def.columns]
    table_data = [headers]

    totals: Dict[int, Decimal] = {}
    has_currency = any(col.is_currency for col in report_def.columns)

    for r in rows:
        row_cells = []
        for c_idx, col in enumerate(report_def.columns):
            val = r.get(col.key)
            if col.is_currency and val is not None:
                dec_val = Decimal(str(val))
                totals[c_idx] = totals.get(c_idx, Decimal("0.00")) + dec_val
                txt = f"{dec_val:,.2f} ETB"
                style = data_cell_right
            elif col.align == "right":
                txt = str(val or "")
                style = data_cell_right
            else:
                txt = str(val or "")
                style = data_cell_style
            row_cells.append(Paragraph(txt, style))
        table_data.append(row_cells)

    # Totals Row
    if has_currency and rows:
        tot_cells = []
        for c_idx, col in enumerate(report_def.columns):
            if c_idx == 0:
                tot_cells.append(Paragraph("<b>TOTAL</b>", data_cell_style))
            elif col.is_currency:
                amt = totals.get(c_idx, Decimal("0.00"))
                tot_cells.append(Paragraph(f"<b>{amt:,.2f} ETB</b>", data_cell_right))
            else:
                tot_cells.append(Paragraph("", data_cell_style))
        table_data.append(tot_cells)

    # Compute column widths to evenly distribute across available width (~782 points)
    num_cols = len(report_def.columns)
    col_w = 782 / num_cols
    col_widths = [col_w] * num_cols

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
    ]

    # Alternating row shading
    num_rows = len(table_data)
    for row_i in range(1, num_rows - (1 if has_currency and rows else 0)):
        if row_i % 2 == 0:
            t_style.append(("BACKGROUND", (0, row_i), (-1, row_i), colors.HexColor("#F8FAFC")))

    if has_currency and rows:
        t_style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E6FFFA")))
        t_style.append(("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#0F766E")))

    t.setStyle(TableStyle(t_style))
    elements.append(t)

    elements.append(Spacer(1, 14))
    elements.append(
        Paragraph(
            "Confidential Business Document — AGMS Gym ERP System. All rights reserved.",
            subtitle_style,
        )
    )

    doc.build(elements)
    output.seek(0)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    filename = f"{report_def.code}_{timezone.now():%Y%m%d_%H%M}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
