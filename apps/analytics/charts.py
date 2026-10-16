from decimal import Decimal
from typing import Dict, List, Optional
from django.utils.html import escape
from django.utils.safestring import mark_safe


def render_svg_bar_chart(
    data: List[Dict],
    label_key: str,
    value_key: str,
    height: int = 240,
    bar_color: str = "#0F766E",
    is_currency: bool = False,
    empty_message: str = "No data available for selected period",
) -> str:
    """
    Render a responsive, pure SVG vertical bar chart.
    """
    if not data or all((d.get(value_key) or 0) == 0 for d in data):
        return f"""
        <div class="erp-chart-empty" style="height: {height}px; display: flex; align-items: center; justify-content: center; background: rgba(241, 245, 249, 0.4); border-radius: 8px; border: 1px dashed #CBD5E1; color: #64748B; font-size: 0.875rem;">
            <div style="text-align: center;">
                <span class="material-symbols-outlined" style="font-size: 2rem; color: #94A3B8; display: block; margin-bottom: 0.25rem;">bar_chart</span>
                {escape(empty_message)}
            </div>
        </div>
        """

    values = [float(d.get(value_key) or 0) for d in data]
    max_val = max(values) if values else 1.0
    if max_val == 0:
        max_val = 1.0

    width = 650
    padding_left = 60
    padding_right = 20
    padding_top = 25
    padding_bottom = 45
    chart_w = width - padding_left - padding_right
    chart_h = height - padding_top - padding_bottom

    n = len(data)
    slot_w = chart_w / max(n, 1)
    bar_w = min(max(slot_w * 0.65, 8), 36)

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" class="erp-svg-chart" style="width: 100%; height: auto; max-height: {height}px; overflow: visible;" xmlns="http://www.w3.org/2000/svg">',
        # Background gridlines (4 lines)
    ]

    for i in range(4):
        y_val = max_val * (1 - i / 3)
        y_pos = padding_top + (chart_h * (i / 3))
        lbl = f"{y_val:,.0f} ETB" if is_currency else f"{y_val:,.0f}"
        svg_parts.append(
            f'<line x1="{padding_left}" y1="{y_pos:.1f}" x2="{width - padding_right}" y2="{y_pos:.1f}" stroke="#E2E8F0" stroke-dasharray="3 3" />'
        )
        svg_parts.append(
            f'<text x="{padding_left - 8}" y="{y_pos + 4:.1f}" font-size="10" fill="#94A3B8" text-anchor="end">{escape(lbl)}</text>'
        )

    # Baseline
    svg_parts.append(
        f'<line x1="{padding_left}" y1="{padding_top + chart_h}" x2="{width - padding_right}" y2="{padding_top + chart_h}" stroke="#CBD5E1" stroke-width="1.5" />'
    )

    # Bars
    for idx, d in enumerate(data):
        val = float(d.get(value_key) or 0)
        h = (val / max_val) * chart_h
        x = padding_left + (idx * slot_w) + (slot_w - bar_w) / 2
        y = padding_top + chart_h - h

        lbl = str(d.get(label_key) or "")
        val_str = f"{val:,.2f} ETB" if is_currency else f"{val:,.0f}"

        # Bar element with hover title
        svg_parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="3" fill="{bar_color}" opacity="0.88">'
            f'<title>{escape(lbl)}: {val_str}</title>'
            f'</rect>'
        )

        # Skip label if too dense, else show label
        step = max(1, n // 10)
        if idx % step == 0 or idx == n - 1:
            svg_parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{padding_top + chart_h + 16}" font-size="10" fill="#64748B" text-anchor="middle">{escape(lbl)}</text>'
            )

    svg_parts.append('</svg>')
    return mark_safe("".join(svg_parts))


def render_svg_donut_chart(
    data: List[Dict],
    label_key: str,
    value_key: str,
    colors: Optional[List[str]] = None,
    empty_message: str = "No distribution data available",
) -> str:
    """
    Render a clean SVG Donut / Pie chart with side legend.
    """
    palette = colors or ["#0F766E", "#0EA5E9", "#D97706", "#22C55E", "#8B5CF6", "#EF4444", "#F59E0B"]

    valid_items = [d for d in data if (d.get(value_key) or 0) > 0]
    total = sum(float(d.get(value_key) or 0) for d in valid_items)

    if not valid_items or total == 0:
        return f"""
        <div class="erp-chart-empty" style="height: 220px; display: flex; align-items: center; justify-content: center; background: rgba(241, 245, 249, 0.4); border-radius: 8px; border: 1px dashed #CBD5E1; color: #64748B; font-size: 0.875rem;">
            <div style="text-align: center;">
                <span class="material-symbols-outlined" style="font-size: 2rem; color: #94A3B8; display: block; margin-bottom: 0.25rem;">pie_chart</span>
                {escape(empty_message)}
            </div>
        </div>
        """

    # Compute SVG stroke-dasharray segments for r=40 (circumference ≈ 251.32)
    circumference = 251.32
    cx, cy = 60, 60
    r = 40

    segments = []
    legend_items = []
    accumulated_pct = 0.0

    for idx, item in enumerate(valid_items):
        val = float(item.get(value_key) or 0)
        pct = (val / total) * 100
        dash_len = (pct / 100.0) * circumference
        color = palette[idx % len(palette)]
        offset = -(accumulated_pct / 100.0) * circumference
        accumulated_pct += pct

        lbl = str(item.get(label_key) or "Unknown")
        segments.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="transparent" stroke="{color}" '
            f'stroke-width="16" stroke-dasharray="{dash_len:.2f} {circumference:.2f}" stroke-dashoffset="{offset:.2f}">'
            f'<title>{escape(lbl)}: {val:,.0f} ({pct:.1f}%)</title>'
            f'</circle>'
        )

        legend_items.append(f"""
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.8rem; margin-bottom: 0.35rem;">
            <div style="display: flex; align-items: center; gap: 0.4rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: {color}; flex-shrink: 0;"></span>
                <span style="color: #334155; font-weight: 500;">{escape(lbl)}</span>
            </div>
            <strong style="color: #0F172A; margin-left: 0.5rem;">{pct:.1f}%</strong>
        </div>
        """)

    svg_donut = f"""
    <div style="display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; padding: 0.5rem 0;">
        <div style="width: 130px; height: 130px; flex-shrink: 0;">
            <svg viewBox="0 0 120 120" style="transform: rotate(-90deg); width: 100%; height: 100%;" xmlns="http://www.w3.org/2000/svg">
                {''.join(segments)}
            </svg>
        </div>
        <div style="flex: 1; min-width: 140px;">
            {''.join(legend_items)}
        </div>
    </div>
    """
    return mark_safe(svg_donut)


def render_svg_horizontal_bar(
    data: List[Dict],
    label_key: str,
    value_key: str,
    bar_color: str = "#0F766E",
    empty_message: str = "No ranking data available",
) -> str:
    """
    Render horizontal progress bars for package popularity or branch comparison.
    """
    if not data or all((d.get(value_key) or 0) == 0 for d in data):
        return f"""
        <div class="erp-chart-empty" style="height: 180px; display: flex; align-items: center; justify-content: center; background: rgba(241, 245, 249, 0.4); border-radius: 8px; border: 1px dashed #CBD5E1; color: #64748B; font-size: 0.875rem;">
            <div style="text-align: center;">
                <span class="material-symbols-outlined" style="font-size: 2rem; color: #94A3B8; display: block; margin-bottom: 0.25rem;">leaderboard</span>
                {escape(empty_message)}
            </div>
        </div>
        """

    max_val = max(float(d.get(value_key) or 0) for d in data)
    if max_val == 0:
        max_val = 1.0

    rows = []
    for d in data:
        val = float(d.get(value_key) or 0)
        pct = (val / max_val) * 100.0
        lbl = str(d.get(label_key) or "")
        rows.append(f"""
        <div style="margin-bottom: 0.75rem;">
            <div style="display: flex; justify-content: space-between; font-size: 0.825rem; margin-bottom: 0.25rem;">
                <span style="font-weight: 500; color: #334155;">{escape(lbl)}</span>
                <strong style="color: #0F766E;">{val:,.0f}</strong>
            </div>
            <div style="background: #E2E8F0; border-radius: 9999px; height: 8px; overflow: hidden;">
                <div style="background: {bar_color}; width: {pct:.1f}%; height: 100%; border-radius: 9999px; transition: width 0.3s ease;"></div>
            </div>
        </div>
        """)

    return mark_safe(f'<div class="erp-horizontal-bars" style="padding: 0.5rem 0;">{"".join(rows)}</div>')
