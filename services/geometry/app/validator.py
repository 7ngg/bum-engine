"""The validator: the export gate and the test oracle.

A layout must pass every hard check to be eligible for ranking/export. Soft
issues surface as structured warnings. Rules (from the plan):
  - no overlapping rooms
  - every door sits on a real shared wall >= 0.8 m
  - master suite not adjacent to the kitchen
  - garage not adjacent to the living room
  - all rooms inside the plot
  - min dimensions met
  - coverage >= ~0.9
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geom, standards
from .models import Layout, Program

MIN_ROOM_M = 0.9
MIN_DOOR_WALL = 0.8
# Coverage is now measured against the house FOOTPRINT (the bounding box of the
# rooms), not the plot: a house really is ~100% internally covered, and the
# plot now carries setback the house doesn't fill. 0.95 leaves headroom for the
# small void that free-rectangle packing can't avoid — the space Task 3's
# circulation will occupy.
COVERAGE_MIN = 0.95
MASTER_ROOMS = {"Master Bedroom", "Master Bathroom", "Walk-in Closet"}
KITCHEN_ROOMS = {"Kitchen"}
GARAGE_ROOMS = {"Garage"}
LIVING_ROOMS = {"Living"}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: float = 0.0

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "coverage": round(self.coverage, 4)}


def _rooms_named(layout: Layout, names: set[str]) -> list[geom.Rect]:
    return [tuple(r.rect_m) for r in layout.rooms if r.name in names]


def validate(layout: Layout, program: Program | None = None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = list(layout.warnings)

    W = layout.plot.width_m
    D = layout.plot.depth_m
    rects = [tuple(r.rect_m) for r in layout.rooms]

    # 1. containment
    for rm in layout.rooms:
        x0, y0, x1, y1 = rm.rect_m
        if x0 < -geom.EPS or y0 < -geom.EPS or x1 > W + geom.EPS or y1 > D + geom.EPS:
            errors.append(f"room {rm.name!r} outside plot: {rm.rect_m}")

    # 2. no overlaps
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            ov = geom.overlap_area(rects[i], rects[j])
            if ov > 1e-3:
                errors.append(
                    f"rooms {layout.rooms[i].name!r} and {layout.rooms[j].name!r} overlap by {ov:.2f} m2"
                )

    # 3. min dimensions
    for rm in layout.rooms:
        x0, y0, x1, y1 = rm.rect_m
        if (x1 - x0) < MIN_ROOM_M - geom.EPS or (y1 - y0) < MIN_ROOM_M - geom.EPS:
            errors.append(f"room {rm.name!r} below min dimension: {x1 - x0:.2f} x {y1 - y0:.2f} m")

    # 4. coverage — against the house FOOTPRINT (bounding box of the rooms),
    #    which the zones tile; the residual plot area is setback. Also assert
    #    the footprint sits within the plot and holds no large unassigned void.
    covered = sum(geom.area(r) for r in rects)
    if rects:
        fx0 = min(r[0] for r in rects)
        fy0 = min(r[1] for r in rects)
        fx1 = max(r[2] for r in rects)
        fy1 = max(r[3] for r in rects)
        footprint_area = (fx1 - fx0) * (fy1 - fy0)
        coverage = covered / footprint_area if footprint_area else 0.0
        # footprint wholly within the plot
        if fx0 < -geom.EPS or fy0 < -geom.EPS or fx1 > W + geom.EPS or fy1 > D + geom.EPS:
            errors.append(f"footprint {[fx0, fy0, fx1, fy1]} extends outside plot {W}x{D}")
        # no large unassigned region inside the footprint (the rooms fill it).
        # A small residual is the un-modelled circulation Task 3 will place.
        if coverage < COVERAGE_MIN - geom.EPS:
            errors.append(
                f"footprint coverage {coverage:.3f} below minimum {COVERAGE_MIN} "
                f"({footprint_area - covered:.1f} m2 unassigned inside the house)"
            )
    else:
        coverage = 0.0
        errors.append("no rooms")

    # 5. forbidden adjacencies (master<->kitchen, garage<->living)
    _check_forbidden(layout, MASTER_ROOMS, KITCHEN_ROOMS, "master suite", "kitchen", errors)
    _check_forbidden(layout, GARAGE_ROOMS, LIVING_ROOMS, "garage", "living", errors)

    # 6. doors on real shared walls >= 0.8 m
    wall_by_id = {w.id: w for w in layout.walls}
    all_doors = list(layout.doors) + [layout.entry]
    for d in all_doors:
        w = wall_by_id.get(d.wall_id)
        if w is None:
            errors.append(f"door {d.from_}->{d.to} references missing wall {d.wall_id!r}")
            continue
        wall_len = ((w.end[0] - w.start[0]) ** 2 + (w.end[1] - w.start[1]) ** 2) ** 0.5
        if wall_len < MIN_DOOR_WALL - geom.EPS:
            errors.append(f"door {d.from_}->{d.to} on wall {d.wall_id!r} only {wall_len:.2f} m (<0.8)")
        if d.width_m > wall_len + geom.EPS:
            errors.append(f"door {d.from_}->{d.to} width {d.width_m} exceeds wall {wall_len:.2f} m")

    # 7. required adjacencies present (DoD): kitchen-dining, dining-living, master-ensuite
    _require_adjacent(layout, "Kitchen", "Dining", warnings)
    _require_adjacent(layout, "Dining", "Living", warnings)
    _require_adjacent(layout, "Master Bedroom", "Master Bathroom", warnings)

    # 8. Neufert dimensional standards — instrumentation only (Task 1). Today's
    # slicer still cuts slivers well below these minimums (MIN_ROOM_M=0.9 is
    # the only room-level gate); warning rather than rejecting lets us measure
    # the defect before Task 2 fixes the slicer/solver and Task 3 tightens
    # this to a hard reject.
    _check_neufert_standards(layout, warnings)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, coverage=coverage)


def _check_neufert_standards(layout: Layout, warnings: list[str]) -> None:
    for rm in layout.rooms:
        spec = standards.ROOMS.get(rm.name)
        if spec is None:
            continue
        x0, y0, x1, y1 = rm.rect_m
        w, h = x1 - x0, y1 - y0
        area = w * h
        if w < spec.min_w_m - geom.EPS:
            warnings.append(f"room {rm.name!r} width {w:.2f} m below Neufert min {spec.min_w_m} m")
        if h < spec.min_h_m - geom.EPS:
            warnings.append(f"room {rm.name!r} depth {h:.2f} m below Neufert min {spec.min_h_m} m")
        if area < spec.min_area_m2 - geom.EPS:
            warnings.append(f"room {rm.name!r} area {area:.2f} m2 below Neufert min {spec.min_area_m2} m2")
        short_side = min(w, h)
        aspect = max(w, h) / short_side if short_side > geom.EPS else float("inf")
        if aspect > spec.max_aspect + geom.EPS:
            warnings.append(f"room {rm.name!r} aspect {aspect:.2f} exceeds Neufert max {spec.max_aspect}")


def _check_forbidden(
    layout: Layout, group_a: set[str], group_b: set[str], la: str, lb: str, errors: list[str]
) -> None:
    ra = _rooms_named(layout, group_a)
    rb = _rooms_named(layout, group_b)
    for a in ra:
        for b in rb:
            if geom.shared_edge(a, b) is not None or geom.overlap_area(a, b) > geom.EPS:
                errors.append(f"forbidden adjacency: {la} touches {lb}")
                return


def _require_adjacent(layout: Layout, name_a: str, name_b: str, warnings: list[str]) -> None:
    ra = _rooms_named(layout, {name_a})
    rb = _rooms_named(layout, {name_b})
    if not ra or not rb:
        return  # room may be absent (e.g. un-sliced); not a hard failure here
    for a in ra:
        for b in rb:
            if geom.adjacent(a, b, MIN_DOOR_WALL):
                return
    warnings.append(f"expected adjacency {name_a}<->{name_b} not found")
