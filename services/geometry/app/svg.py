"""Render a Layout to a standalone SVG preview for the UI.

Internal frame is y-up; SVG is y-down, so we flip. The terrace (south of the
building, y<0) expands the viewBox downward.
"""

from __future__ import annotations

from .models import Layout
from .slicer import _hinge_frame, _into_normal

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

    # doors: leaf at 90 degrees + quarter swing arc (green), windows (blue).
    #
    # Doors used to be drawn as plain dots, which is why the web preview could
    # not show the swing defect the architect saw in Revit — the two views were
    # literally not displaying the same property. The hinge point and swept
    # direction come from the SAME helpers the slicer and validator use, so the
    # picture cannot drift from the geometry that was checked.
    walls_by_id = {w.id: w for w in layout.walls}
    rect_by_name = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    if layout.terrace is not None:
        rect_by_name["Terrace"] = tuple(layout.terrace.rect_m)

    for d in list(layout.doors) + [layout.entry]:
        cx, cy = d.center
        wall = walls_by_id.get(d.wall_id)
        rect = rect_by_name.get(d.swing_into)
        if wall is None or rect is None:
            # nothing to swing into (or no host): fall back to the old dot
            parts.append(
                f'<circle cx="{fx(cx):.1f}" cy="{fy(cy):.1f}" r="3.5" fill="#2e7d32"/>')
            continue

        hinge_pt, along = _hinge_frame(d, wall, d.hinge)
        into = _into_normal(d, wall, rect)
        if into is None:
            parts.append(
                f'<circle cx="{fx(cx):.1f}" cy="{fy(cy):.1f}" r="3.5" fill="#2e7d32"/>')
            continue

        r = d.width_m
        hx, hy = fx(hinge_pt[0]), fy(hinge_pt[1])
        # closed leaf tip (lying in the doorway) and open leaf tip (at 90 deg)
        sx = fx(hinge_pt[0] + along[0] * r)
        sy = fy(hinge_pt[1] + along[1] * r)
        ox = fx(hinge_pt[0] + into[0] * r)
        oy = fy(hinge_pt[1] + into[1] * r)
        # sweep direction in SVG space (y is flipped, so decide it there)
        cross = (sx - hx) * (oy - hy) - (sy - hy) * (ox - hx)
        sweep = 1 if cross > 0 else 0
        rpx = r * SCALE

        parts.append(  # the arc the leaf sweeps
            f'<path d="M {sx:.1f} {sy:.1f} A {rpx:.1f} {rpx:.1f} 0 0 {sweep} '
            f'{ox:.1f} {oy:.1f}" fill="none" stroke="#2e7d32" '
            f'stroke-width="1" stroke-dasharray="3,2"/>')
        parts.append(  # the leaf itself, standing open at 90 degrees
            f'<line x1="{hx:.1f}" y1="{hy:.1f}" x2="{ox:.1f}" y2="{oy:.1f}" '
            f'stroke="#2e7d32" stroke-width="2.5" stroke-linecap="round"/>')
        parts.append(  # hinge
            f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="2" fill="#2e7d32"/>')
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
