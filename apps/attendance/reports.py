import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def generate_attendance_csv(queryset) -> bytes:
    """Generate CSV bytes for an attendance queryset."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Member Number",
        "Member Name",
        "Branch",
        "Check-in Datetime",
        "Check-out Datetime",
        "Duration (Mins)",
        "Check-in Method",
        "Check-out Method",
        "Status",
        "Package",
        "Recorded By",
    ])

    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Africa/Addis_Ababa")

    for att in queryset.select_related("member", "branch", "membership", "membership__package", "recorded_by"):
        cin = att.check_in_datetime.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if att.check_in_datetime else ""
        cout = att.check_out_datetime.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if att.check_out_datetime else ""
        duration = att.duration_minutes or ""
        pkg_name = att.membership.package.name if att.membership and att.membership.package else "N/A"
        recorded_by = att.recorded_by.email if att.recorded_by else "System"

        writer.writerow([
            att.member.member_number,
            att.member.full_name,
            att.branch.code,
            cin,
            cout,
            duration,
            att.get_check_in_method_display(),
            att.get_check_out_method_display() if att.check_out_method else "",
            att.get_status_display(),
            pkg_name,
            recorded_by,
        ])

    return output.getvalue().encode("utf-8")


def generate_attendance_pdf(queryset, title="Attendance Report", branch_name="All Branches", date_range_label="Current Scope") -> bytes:
    """Generate dynamic PDF report for attendance records."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    elements = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor("#0F766E")  # Dark Teal
    text_dark = colors.HexColor("#0F172A")
    bg_subtle = colors.HexColor("#F8FAFC")

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=primary_color,
    )

    meta_style = ParagraphStyle(
        "ReportMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#64748B"),
    )

    cell_style = ParagraphStyle(
        "CellText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=text_dark,
    )

    header_cell_style = ParagraphStyle(
        "HeaderCellText",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )

    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Africa/Addis_Ababa")

    # Header block
    elements.append(Paragraph(f"AGMS ERP — {title.upper()}", title_style))
    elements.append(Paragraph(f"Branch: {branch_name} | Range: {date_range_label} | Generated: {timezone.now().astimezone(tz).strftime('%Y-%m-%d %H:%M')}", meta_style))
    elements.append(Spacer(1, 14))

    # Table Data
    table_data = [[
        Paragraph("Member ID", header_cell_style),
        Paragraph("Member Name", header_cell_style),
        Paragraph("Branch", header_cell_style),
        Paragraph("Check-in", header_cell_style),
        Paragraph("Check-out", header_cell_style),
        Paragraph("Mins", header_cell_style),
        Paragraph("Method", header_cell_style),
        Paragraph("Status", header_cell_style),
        Paragraph("Package", header_cell_style),
    ]]

    rows = list(queryset[:200].select_related("member", "branch", "membership", "membership__package"))
    for att in rows:
        cin = att.check_in_datetime.astimezone(tz).strftime("%m/%d %H:%M") if att.check_in_datetime else "-"
        cout = att.check_out_datetime.astimezone(tz).strftime("%H:%M") if att.check_out_datetime else "-"
        dur = str(att.duration_minutes) if att.duration_minutes is not None else "-"
        pkg = att.membership.package.name if att.membership and att.membership.package else "-"

        table_data.append([
            Paragraph(att.member.member_number, cell_style),
            Paragraph(att.member.full_name[:20], cell_style),
            Paragraph(att.branch.code, cell_style),
            Paragraph(cin, cell_style),
            Paragraph(cout, cell_style),
            Paragraph(dur, cell_style),
            Paragraph(att.check_in_method, cell_style),
            Paragraph(att.status, cell_style),
            Paragraph(pkg[:18], cell_style),
        ])

    col_widths = [1.0 * inch, 1.8 * inch, 0.7 * inch, 1.1 * inch, 0.8 * inch, 0.6 * inch, 0.9 * inch, 1.0 * inch, 1.6 * inch]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), primary_color),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, bg_subtle]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)

    # Summary
    elements.append(Spacer(1, 14))
    elements.append(Paragraph(f"Total Records Displayed: {len(rows)} (Filtered)", meta_style))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
