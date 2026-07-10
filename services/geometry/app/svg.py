"""Render a Layout to a standalone SVG preview for the UI.

Internal frame is y-up; SVG is y-down, so we flip. The terrace (south of the
building, y<0) expands the viewBox downward.
"""

from __future__ import annotations

from .models import Layout

_FILL = {
    "living": "#cfe8cf",
    "private": "#dbe4f0",
    "wet": "#cfe6ec",
    "service": "#e8e0cf",
    "circ": "#efe7d8",
    "office": "#e6dcef",
    "outdoor": "#d9efd9",
}
PAD = 1.0
SCALE = 30  # px per metre


def render(layout: Layout) -> str:
    W = layout.plot.width_m
    D = layout.plot.depth_m
    min_y = 0.0
    if layout.terrace is not None:
        min_y = min(min_y, layout.terrace.rect_m[1])
    total_h = D - min_y
    vb_w = (W + 2 * PAD) * SCALE
    vb_h = (total_h + 2 * PAD) * SCALE

    def fx(x: float) -> float:
        return (x + PAD) * SCALE

    def fy(y: float) -> float:
        # flip: world y-up -> svg y-down, offset so min_y sits at bottom
        return (total_h - (y - min_y) + PAD) * SCALE

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w:.0f} {vb_h:.0f}" '
        f'font-family="sans-serif">',
        f'<rect x="0" y="0" width="{vb_w:.0f}" height="{vb_h:.0f}" fill="#ffffff"/>',
    ]

    # terrace
    if layout.terrace is not None:
        x0, y0, x1, y1 = layout.terrace.rect_m
        parts.append(
            f'<rect x="{fx(x0):.1f}" y="{fy(y1):.1f}" width="{(x1 - x0) * SCALE:.1f}" '
            f'height="{(y1 - y0) * SCALE:.1f}" fill="#d9efd9" stroke="#8ab88a" '
            f'stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<text x="{fx((x0 + x1) / 2):.1f}" y="{fy((y0 + y1) / 2):.1f}" '
            f'font-size="11" text-anchor="middle" fill="#4a6a4a">Terrace</text>'
        )

    # rooms
    for r in layout.rooms:
        x0, y0, x1, y1 = r.rect_m
        parts.append(
            f'<rect x="{fx(x0):.1f}" y="{fy(y1):.1f}" width="{(x1 - x0) * SCALE:.1f}" '
            f'height="{(y1 - y0) * SCALE:.1f}" fill="{_FILL.get(r.category, "#eee")}" '
            f'stroke="#9aa0a6" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{fx((x0 + x1) / 2):.1f}" y="{fy((y0 + y1) / 2):.1f}" '
            f'font-size="10" text-anchor="middle" dominant-baseline="middle" '
            f'fill="#333">{_esc(r.name)}</text>'
        )

    # walls
    for w in layout.walls:
        sw = 3 if w.exterior else 1.5
        parts.append(
            f'<line x1="{fx(w.start[0]):.1f}" y1="{fy(w.start[1]):.1f}" '
            f'x2="{fx(w.end[0]):.1f}" y2="{fy(w.end[1]):.1f}" '
            f'stroke="#333" stroke-width="{sw}" stroke-linecap="square"/>'
        )

    # doors (green) + windows (blue)
    for d in list(layout.doors) + [layout.entry]:
        cx, cy = d.center
        parts.append(
            f'<circle cx="{fx(cx):.1f}" cy="{fy(cy):.1f}" r="3.5" '
            f'fill="#2e7d32"/>'
        )
    for wd in layout.windows:
        cx, cy = wd.center
        parts.append(
            f'<circle cx="{fx(cx):.1f}" cy="{fy(cy):.1f}" r="2.5" '
            f'fill="#1565c0"/>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
