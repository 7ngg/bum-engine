"""Slice macro-zones into finished rooms and emit explicit walls/doors/windows.

Composite cuts (so internal adjacencies hold by construction):
  master_suite -> Master Bedroom (exterior) + Master Bathroom + Walk-in Closet
  children     -> Bedroom + Bathroom (middle) + Bedroom, beds along exterior wall
  kitchen_laundry -> Kitchen (kept next to Dining) + Laundry (away from Dining)
  entry        -> Foyer + Mudroom (toward the Garage)
Terrace runs along the south facade, spanning the contiguous run of
daylight-required, non-service rooms that includes Living (Office + Living on
the 184 fixture), with one door per room it spans.

Walls are rasterised on the 0.5 m grid: a wall unit-edge exists wherever two
grid cells belong to different rooms (interior) or a room meets the outside
(exterior). Collinear unit-edges merge into wall runs. Doors follow a spanning
tree over the room-adjacency graph rooted at the Foyer, guaranteeing every room
is reachable and every door sits on a real shared wall; a small number of
SECONDARY doors are then added ON TOP of that tree (see _secondary_doors) to
break the pure-tree detours a spanning set forces. Which END of its wall each
door sits at is R7 (see _position_doors).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom
from . import standards
from .models import (
    Category,
    Door,
    Layout,
    Program,
    Room,
    Terrace,
    Wall,
    Window,
)
from .solver import GRID_M, SolveResult, ZoneRect
from . import zones as Z

EXT_WALL_M = 0.30
INT_WALL_M = 0.15
DOOR_W = 0.9
DOOR_H = 2.1
WIN_W = 1.5
WIN_H = 1.2
WIN_SILL = 0.9
TERRACE_DEPTH_M = 3.0
MIN_DOOR_WALL = 0.8

# room categories that deserve a window on any exterior wall
WINDOW_CATEGORIES: set[Category] = {"living", "private", "office"}
WINDOW_ROOMS = {"Kitchen"}  # name-based exceptions


def _snap(v: float) -> float:
    """Round to the nearest grid line. ONLY for a free midpoint that carries no
    minimum (the divider between two equal-minimum rooms). A dimension that
    carries a Neufert minimum must never use this — round() can land BELOW the
    minimum (round(2.2/0.5)*0.5 = 2.0); use _ceil_snap for those."""
    return round(v / GRID_M) * GRID_M


def _ceil_snap(v: float) -> float:
    """Round a minimum UP to the grid, so a room given exactly its standards
    minimum still clears it after snapping (matches solver._ceil_u)."""
    return math.ceil(v / GRID_M - 1e-9) * GRID_M


@dataclass
class FinalRoom:
    name: str
    category: Category
    zone: str
    rect: geom.Rect


@dataclass
class _WallRec:
    wall: Wall
    a: int  # room index or -1 (OUTSIDE)
    b: int
    edge: geom.Edge


# ---------------------------------------------------------------------------
# slicing
# ---------------------------------------------------------------------------


def _grid_steps(lo: float, hi: float):
    """Grid-aligned candidate depths in [lo, hi], smallest first. `lo` is
    ceil-snapped (it carries a minimum), `hi` is floor-snapped (it is a
    ceiling). Empty when the band cannot be realised on the grid at all —
    callers MUST treat that as 'no legal cut', never as 'use lo anyway'."""
    a = _ceil_snap(lo)
    steps = []
    v = a
    while v <= hi + geom.EPS:
        steps.append(round(v, 10))
        v += GRID_M
    return steps


def _in_band(name: str, rect: geom.Rect) -> bool:
    """Does this sub-room satisfy BOTH sources: the Neufert/SNiP shape floor
    (min dims, min area, aspect) AND the architect's area band?

    This is the single predicate that makes slicing area-aware. It is also what
    legal_pairs() probes through, so a zone SHAPE whose cut would violate a band
    is never offered to the solver in the first place — the band is enforced at
    two levels that cannot disagree, because they call this same function."""
    if not _room_legal(name, rect):
        return False
    x0, y0, x1, y1 = rect
    a = (x1 - x0) * (y1 - y0)
    return (
        a >= standards.area_floor(name) - geom.EPS
        and a <= standards.area_ceiling(name) + geom.EPS
    )


# ---------------------------------------------------------------------------
# THE ARCHITECT'S DISTRIBUTION PRIORITY, applied to the composite CUT
# (round 4, 2026-08-04 -- rulings quoted verbatim in standards.py).
#
# THIS IS WHERE THE MEASURED DEFECT LIVED. Every composite cutter below
# enumerates grid-legal candidate cuts in ascending order of the ANCILLARY
# room's strip depth and returns THE FIRST one that is in band. That rule reads
# as "give the headline room the surplus" and it is the opposite in practice,
# because the ancillary strip spans the zone's whole CROSS-dimension: its area
# is (its own minimum depth) x (the zone's width), which grows with the zone,
# while the headline room gets only what is left. Measured at roomy @192:
#   kitchen_laundry 18.00 -> Kitchen 10.00 (= its floor, +0.00), Laundry +4.00
#   children        32.00 -> Bedroom 2/3 12.00 (= floor, +0.00), Bathroom +3.92
#   master_suite    30.25 -> Master Bedroom +0.50, Walk-in Closet +3.30
#
# So the fix is not a new constraint: every one of those cuts was already legal
# and so were better ones. It is that FIRST-LEGAL-WINS is not a decision rule.
# _cut_score replaces it with the architect's own rule -- score every legal
# candidate by tier-weighted distance from ideal, keep the best -- and because
# it only ever CHOOSES AMONG CUTS THE OLD CODE ALREADY ACCEPTED, it cannot make
# a legal shape illegal, cannot change legal_pairs (which probes these same
# functions), and cannot break a hard constraint.
#
# Ordering note: the enumeration order of each cutter is preserved and used as
# the tie-break (`min` keeps the first minimum), so a zone whose candidates all
# score equally slices exactly as it did before, byte for byte.
# ---------------------------------------------------------------------------


def _cut_score(rects: list[tuple[str, geom.Rect]]) -> tuple[int, int]:
    """(weighted shortfall, weighted excess) for a candidate cut, in the units
    solver.py's objective uses -- grid cells (0.25 m2), weighted by
    standards.TIER_W_BELOW / TIER_W_ABOVE. Lower is better, shortfall first.

    Two keys rather than one signed number, and the order matters: the first is
    Ruling 2 (surplus goes to the highest tier still short of its ideal), the
    second is Ruling 1's "ideal olcuye catdiqdan sonra sistem dayanmalidir" --
    among cuts that leave nobody short, prefer the one that overshoots least.
    Both are computed over the SAME per-room-type constants the solver uses, so
    the zone-level term and the room-level cut cannot disagree about priority.
    """
    below = above = 0.0
    for name, rect in rects:
        x0, y0, x1, y1 = rect
        cells = ((x1 - x0) * (y1 - y0)) / (GRID_M * GRID_M)
        ideal_cells = standards.area_ideal(name) / (GRID_M * GRID_M)
        tier = standards.priority_tier(name)
        if cells < ideal_cells:
            below += standards.TIER_W_BELOW[tier] * (ideal_cells - cells)
        else:
            above += standards.TIER_W_ABOVE[tier] * (cells - ideal_cells)
    return (int(round(below)), int(round(above)))


def _best_cut(cands: list[list[FinalRoom]]) -> list[FinalRoom] | None:
    """The candidate whose rooms sit closest to their ideals, by _cut_score.
    `min` is stable, so an exact tie keeps the earliest candidate -- i.e. the
    one the previous first-legal-wins rule would have returned."""
    if not cands:
        return None
    return min(cands, key=lambda rooms: _cut_score([(r.name, r.rect) for r in rooms]))


def _side_of(r: geom.Rect, other: geom.Rect) -> str:
    """Which side of r the other rect lies on: 'N','S','E','W'."""
    e = geom.shared_edge(r, other)
    if e is not None:
        if e.orient == "V":
            return "E" if abs(e.fixed - r[2]) < geom.EPS else "W"
        return "N" if abs(e.fixed - r[3]) < geom.EPS else "S"
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    ox, oy = (other[0] + other[2]) / 2, (other[1] + other[3]) / 2
    if abs(ox - cx) >= abs(oy - cy):
        return "E" if ox >= cx else "W"
    return "N" if oy >= cy else "S"


def _slice_master(r: ZoneRect, corridor_side: str | None = None) -> list[FinalRoom]:
    """corridor_side is the solver's SolveResult.corridor_sides["master_suite"]
    ("N"/"S"/"E"/"W", or None if unrecorded/unattached) — which side of this
    zone the corridor lands on (_force_master_corridor_overlap in solver.py). The
    service strip (Bathroom | Closet) must NOT be the room that fronts the
    corridor, else the corridor's forced overlap reaches only the ensuite/
    closet and the Bedroom itself becomes unreachable.

    Default cut: service strip NORTH, Bedroom the full-width SOUTH band. The
    full-width Bedroom already touches BOTH the E and W walls, so an E or W
    (or unrecorded/None) corridor already fronts it — no change needed there.
    An S corridor touches the zone's y0 edge, which is the Bedroom's edge too
    — also already correct. Only N flips: the corridor would otherwise front
    the service strip's y1 edge, so the Bedroom moves to the NORTH band and
    the service strip to the SOUTH.
    """
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    mbath = standards.ROOMS["Master Bathroom"]
    wic = standards.ROOMS["Walk-in Closet"]
    mbed = standards.ROOMS["Master Bedroom"]
    # AREA-AWARE STRIP DEPTH (Phase 1). This used to be a FIXED 2.5 m service
    # strip — ceil_snap(max(bath.min_h, closet.min_h)) — with the Bedroom taking
    # all surplus depth. That is why a 27.5 m2 suite handed the Bedroom 15.0 m2
    # and the bath+closet 12.5, while the architect's own minimums are
    # 16 + 5 + 4 = 25 with room to spare: the cut never looked at an area at all.
    #
    # Now both free dimensions are SEARCHED on the grid, smallest strip first
    # (the Bedroom is the room that wants the surplus), and the first combination
    # that puts ALL THREE rooms inside their bands wins. Ascending order also
    # handles the opposite failure by itself: if the smallest strip leaves the
    # Bedroom ABOVE its 30 m2 ceiling, a deeper strip is tried until it isn't.
    #
    # RULING 1/2 (2026-08-04): "smallest strip first" was still only a heuristic
    # about the strip's DEPTH, and it never looked at the strip's WIDTH split at
    # all -- bath_w ascending handed the Walk-in Closet every metre the Master
    # Bathroom did not claim, which is how a tier-3 closet came out +3.30 while
    # the tier-1 Bedroom took +0.50. Every candidate is now collected and scored
    # by _cut_score instead of the first one being returned.
    service_lo = max(mbath.min_h_m, wic.min_h_m)
    bath_lo = mbath.min_w_m
    cands: list[list[FinalRoom]] = []
    for service in _grid_steps(service_lo, h - _ceil_snap(mbed.min_h_m)):
        for bath_w in _grid_steps(bath_lo, w - _ceil_snap(wic.min_w_m)):
            bed_rect = ((x0, y0, x1, y1 - service) if corridor_side != "N"
                        else (x0, y0 + service, x1, y1))
            sy0, sy1 = ((y1 - service, y1) if corridor_side != "N" else (y0, y0 + service))
            if (
                _in_band("Master Bedroom", bed_rect)
                and _in_band("Master Bathroom", (x0, sy0, x0 + bath_w, sy1))
                and _in_band("Walk-in Closet", (x0 + bath_w, sy0, x1, sy1))
            ):
                if corridor_side == "N":
                    cands.append([
                        FinalRoom("Master Bathroom", "wet", r.zone, (x0, sy0, x0 + bath_w, sy1)),
                        FinalRoom("Walk-in Closet", "private", r.zone, (x0 + bath_w, sy0, x1, sy1)),
                        FinalRoom("Master Bedroom", "private", r.zone, bed_rect),
                    ])
                else:
                    cands.append([
                        FinalRoom("Master Bedroom", "private", r.zone, bed_rect),
                        FinalRoom("Master Bathroom", "wet", r.zone, (x0, sy0, x0 + bath_w, sy1)),
                        FinalRoom("Walk-in Closet", "private", r.zone, (x0 + bath_w, sy0, x1, sy1)),
                    ])
    best = _best_cut(cands)
    if best is not None:
        return best
    # No grid-legal three-way cut. Fall back to the whole zone as one Bedroom,
    # exactly as before — legal_pairs() probes this function, so the solver is
    # not offered a shape that lands here in the first place.
    return [FinalRoom("Master Bedroom", "private", r.zone, (x0, y0, x1, y1))]


def _slice_children(r: ZoneRect, corridor_side: str | None = None) -> list[FinalRoom]:
    """Cut children into Bedroom 2 | Bathroom | Bedroom 3, banded ACROSS the face
    the corridor attached to.

    `corridor_side` is the solver's SolveResult.corridor_sides["children"] --
    which side of the zone the corridor took -- and it exists for exactly the
    reason _slice_master's does (5da1490): the band direction is only correct
    relative to the corridor, and until now it was correct by hard-coding rather
    than by knowing. "W"/"E"/None keep the historical horizontal bands
    byte-for-byte; "N"/"S" take the transposed branch below.
    """
    if corridor_side in ("N", "S"):
        return _slice_children_ns(r)
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    bathroom = standards.ROOMS["Bathroom"]
    bed = standards.ROOMS["Bedroom"]
    # Three horizontal bands so both beds run along the (vertical) exterior wall.
    #
    # AREA-AWARE (Phase 1). The middle Bathroom used to take its min DEPTH
    # unconditionally and the two beds split whatever remained, so on a 3.5 m
    # wide zone both beds came out at 10.5 m2 against a 12 m2 minimum. Both the
    # bathroom depth AND the bed divider are now searched on the grid.
    #
    # The Bathroom is the room with a real CEILING here (9 m2) and it spans the
    # full zone width, so on a wide zone its minimum DEPTH times that width can
    # exceed its maximum area outright. That is a real contradiction and this
    # function does not round it away: it returns no 3-way cut for such a shape,
    # and because legal_pairs() probes it, the solver is never offered that zone
    # width at all.
    #
    # The full width is FORCED, not chosen — with the corridor strictly E/W of
    # this zone (CB3), the only 3-rectangle tilings that give all three rooms a
    # wall on that face are three full-width bands; a narrow Bathroom needs a
    # 4th room or an L-shaped bedroom. Proof and counts in standards.py's
    # ARCHITECT_AREA_BANDS note. So the depth is the only lever, and it is the
    # Bathroom's own min_h_m — which is why that number is now sourced rather
    # than derived. At 1.7 -> 2.0 m snapped, widths up to 4.0 m clear the 9 m2
    # ceiling (4.0 x 2.0 = 8.0) and the aspect cap binds before the band does.
    #
    # RULING 1/2 (2026-08-04): the Bathroom band's depth is now chosen by
    # _cut_score over all legal candidates rather than by taking the shallowest,
    # so a tier-3 Bathroom stops absorbing depth the tier-2 beds are short of.
    # The even-split preference below is kept as the enumeration order, which is
    # also the tie-break, so an all-equal zone slices exactly as it did before.
    out: list[list[FinalRoom]] = []
    for bath_h in _grid_steps(bathroom.min_h_m, h - 2 * _ceil_snap(bed.min_h_m)):
        rest = h - bath_h
        # Prefer the most even split (the two beds share a minimum), then walk
        # outward — an uneven split is still better than no split.
        mid = _snap(rest / 2)
        cands = sorted(
            _grid_steps(bed.min_h_m, rest - _ceil_snap(bed.min_h_m)),
            key=lambda t: (abs(t - mid), t),
        )
        for top in cands:
            a, b = y0 + top, y0 + top + bath_h
            if (
                _in_band("Bedroom 2", (x0, y0, x1, a))
                and _in_band("Bathroom", (x0, a, x1, b))
                and _in_band("Bedroom 3", (x0, b, x1, y1))
            ):
                out.append([
                    FinalRoom("Bedroom 2", "private", r.zone, (x0, y0, x1, a)),
                    FinalRoom("Bathroom", "wet", r.zone, (x0, a, x1, b)),
                    FinalRoom("Bedroom 3", "private", r.zone, (x0, b, x1, y1)),
                ])
    best = _best_cut(out)
    if best is not None:
        return best
    return [FinalRoom("Children Bedroom", "private", r.zone, (x0, y0, x1, y1))]


def _slice_children_ns(r: ZoneRect) -> list[FinalRoom]:
    """The TRANSPOSE of _slice_children, for a corridor on the zone's N or S face.

    Same logic with x and y exchanged: three full-DEPTH vertical bands, left to
    right, Bedroom 2 | Bathroom | Bedroom 3. Every band runs the whole depth of
    the zone, so every band touches the corridor's horizontal face over its own
    full width -- which is what preserves CB3's three guarantees on this axis:
    the Bathroom is corridor-DIRECT, and Bedroom 2 and Bedroom 3 each front the
    corridor themselves rather than being reached through it.

    The 96,580-tiling enumeration that forced full-width bands on the E/W axis
    transposes with the geometry, not against it: its premise was "all three
    rooms need a wall on the corridor's face", and rotating which face that is
    rotates the surviving topology with it. It does not admit new topologies --
    a narrow Bathroom still needs a 4th room or an L-shaped bedroom here too.

    ONE thing does NOT simply transpose, and it is the reason this is a separate
    function rather than an axis flag threaded through the original: the Bathroom
    standard is ORIENTED (standards.py: min_w_m 2.4 is ALONG the fixture run,
    min_h_m 1.7 is the depth in front of it). Rotating the room 90 degrees
    rotates the fixture run with it -- the bath and basin now run along the
    zone's DEPTH and the activity space is measured across the band's width. So
    the Bathroom is checked against a transposed rect: the room is the same room,
    installed the other way round, and pretending otherwise would either reject
    every legal vertical bathroom (2.4 demanded across a band that only needs
    1.7) or, worse, accept an illegal one. The beds need no such care -- the
    Bedroom envelope is square (2.44 x 2.44).
    """
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    bathroom = standards.ROOMS["Bathroom"]
    bed = standards.ROOMS["Bedroom"]

    def bath_ok(rect: geom.Rect) -> bool:
        # transposed check: swap the rect's own w/h before measuring it against
        # the oriented standard (see the docstring).
        bx0, by0, bx1, by1 = rect
        return _in_band("Bathroom", (0.0, 0.0, by1 - by0, bx1 - bx0))

    # The Bathroom's band WIDTH is its depth-in-front (min_h_m), because its
    # fixture run lies along the zone's depth here -- the mirror of the E/W
    # branch, where the band's depth was min_h_m and the run lay along the width.
    out: list[list[FinalRoom]] = []
    for bath_w in _grid_steps(bathroom.min_h_m, w - 2 * _ceil_snap(bed.min_w_m)):
        rest = w - bath_w
        mid = _snap(rest / 2)
        cands = sorted(
            _grid_steps(bed.min_w_m, rest - _ceil_snap(bed.min_w_m)),
            key=lambda t: (abs(t - mid), t),
        )
        for left in cands:
            a, b = x0 + left, x0 + left + bath_w
            if (
                _in_band("Bedroom 2", (x0, y0, a, y1))
                and bath_ok((a, y0, b, y1))
                and _in_band("Bedroom 3", (b, y0, x1, y1))
            ):
                out.append([
                    FinalRoom("Bedroom 2", "private", r.zone, (x0, y0, a, y1)),
                    FinalRoom("Bathroom", "wet", r.zone, (a, y0, b, y1)),
                    FinalRoom("Bedroom 3", "private", r.zone, (b, y0, x1, y1)),
                ])
    best = _best_cut(out)  # Ruling 1/2, as in _slice_children
    if best is not None:
        return best
    return [FinalRoom("Children Bedroom", "private", r.zone, (x0, y0, x1, y1))]


def _slice_kitchen(
    r: ZoneRect, side: str | None, corridor_side: str | None = None
) -> list[FinalRoom]:
    # `side` is the direction of Dining, DECIDED BY THE SOLVER (result.cut_sides)
    # and read here — not re-derived from _side_of. It ALSO fixes which AXIS the
    # zone was cut on (N/S vs W/E): the solver constrained the zone's (w, h) to a
    # shape legal for THIS axis (legal_pairs), so the axis itself must stay
    # `side`-derived, never flipped by corridor_side below.
    #
    # `corridor_side` is a SEPARATE signal (result.corridor_sides["kitchen_laundry"]):
    # which side the corridor attached to. When it lands on the SAME axis as the
    # cut, the corridor's shared wall sits on exactly one sub-room's edge (just
    # like the N/S dining split does), so Kitchen goes there instead of wherever
    # Dining would put it — Kitchen must be corridor-direct, and this doesn't
    # touch kitchen_laundry<->dining (a ZONE-level required adjacency, unaffected
    # by which sub-room sits where). When corridor_side is on the ORTHOGONAL
    # axis (or absent), both sub-rooms already span that whole edge under either
    # cut, so which one the corridor's overlap actually lands on is a matter of
    # exact position, not order — not something reordering can fix — so we fall
    # through to the dining placement unchanged.
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    kitchen = standards.ROOMS["Kitchen"]
    laundry = standards.ROOMS["Laundry"]
    if side is None:
        return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
    axis_ns = side in ("N", "S")
    place_side = side
    if corridor_side is not None and (corridor_side in ("N", "S")) == axis_ns:
        place_side = corridor_side
    # AREA-AWARE (Phase 1). The Laundry strip used to be pinned at its minimum
    # depth with the Kitchen taking all the surplus; the depth is now searched on
    # the grid, smallest first, so the Kitchen still gets the surplus but the
    # Laundry's own 4-10 m2 band is respected on both ends. Ascending order means
    # a Laundry over its 10 m2 ceiling never survives — it is the first candidate
    # to be rejected, and the search moves on rather than shipping it.
    #
    # RULING 1/2 (2026-08-04). "Smallest strip first, Kitchen takes the surplus"
    # was true of the strip's DEPTH and false of its AREA: the strip spans the
    # zone's full cross-dimension, so the Laundry's minimum-depth candidate was
    # already 2.0 x 4.0 = 8.00 m2 against its own 4 m2 floor while the Kitchen
    # sat on exactly 10.00. This is the single clearest instance of the defect
    # Ruling 2 names. All legal depths are now scored by _cut_score.
    out: list[list[FinalRoom]] = []
    if axis_ns:
        for depth in _grid_steps(laundry.min_h_m, h - _ceil_snap(kitchen.min_h_m)):
            if place_side == "S":  # kitchen south, laundry north
                k_rect, l_rect = (x0, y0, x1, y1 - depth), (x0, y1 - depth, x1, y1)
            else:
                k_rect, l_rect = (x0, y0 + depth, x1, y1), (x0, y0, x1, y0 + depth)
            if _in_band("Kitchen", k_rect) and _in_band("Laundry", l_rect):
                k = FinalRoom("Kitchen", "wet", r.zone, k_rect)
                lr = FinalRoom("Laundry", "service", r.zone, l_rect)
                out.append([k, lr] if place_side == "S" else [lr, k])
        best = _best_cut(out)
        if best is not None:
            return best
        return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
    for depth in _grid_steps(laundry.min_w_m, w - _ceil_snap(kitchen.min_w_m)):
        if place_side == "W":  # kitchen west, laundry east
            k_rect, l_rect = (x0, y0, x1 - depth, y1), (x1 - depth, y0, x1, y1)
        else:
            k_rect, l_rect = (x0 + depth, y0, x1, y1), (x0, y0, x0 + depth, y1)
        if _in_band("Kitchen", k_rect) and _in_band("Laundry", l_rect):
            k = FinalRoom("Kitchen", "wet", r.zone, k_rect)
            lr = FinalRoom("Laundry", "service", r.zone, l_rect)
            out.append([k, lr] if place_side == "W" else [lr, k])
    best = _best_cut(out)
    if best is not None:
        return best
    return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]


def _split_off_wc(
    zone: str, rect: geom.Rect, along_x: bool, mud_side: str | None = None
) -> list[FinalRoom] | None:
    """Carve the Guest WC off one end of the Foyer remainder.

    Returns [Guest WC, Foyer] or [Foyer, Guest WC] — always in GEOMETRIC order,
    low coordinate first, so _slice_entry's `[mud_room, *split]` /
    `[*split, mud_room]` stays a truthful description of the strip order — or
    None if the remainder cannot give the WC its minimum without dropping the
    Foyer below its own.

    WHICH END, and why it is not arbitrary: the Foyer has two jobs the WC must
    not take from it — it carries the front door (so it needs the STREET-facing
    exterior wall, +y in the solver's fixed frame) and it is the access-tree
    ROOT, so it must stay connected to the rest of the house. Both presets pin
    the entry zone to the north edge, so on the axis PERPENDICULAR to the
    Mudroom cut the WC takes the y0 (south) / x0 (west) end and the Foyer keeps
    the north wall. South is also where kitchen_laundry sits, which is what puts
    the WC against the wet cluster the architect and the Posobie both ask for —
    see _slice_entry.

    `mud_side` is the end the Mudroom took, and it exists because of a defect
    this function shipped: when the split runs along the SAME axis as the
    Mudroom cut, taking the low end unconditionally drops the WC BETWEEN the
    Mudroom and the Foyer. Guest WC is no_through_traffic, so that severs the
    Foyer from the Mudroom -> Corridor chain and orphans the whole house from
    its own root. Measured at target 192, preset gW_eN: strip order came out
    Mudroom | Guest WC | Foyer and access_tree reached 3 of 16 rooms; the mirror
    preset gE_eN reached 16 of 16 purely because its Mudroom sits at the far end
    and the same low-end rule happened to land the WC correctly. So: when the
    Mudroom holds the low end of this axis, the WC takes the HIGH end. The
    resulting invariant, which now holds in all eight (side x axis) cases, is
    that the FOYER IS ALWAYS ADJACENT TO THE MUDROOM, over at least the Foyer's
    own minimum dimension (>= 1.5 m, comfortably above ACCESS_DOOR_M).

    AREA-AWARE (Phase 1). The depth used to be pinned at ceil_snap(1.3) = 1.5 m
    and the WC spanned the remainder's FULL width, which on a 3.0 m wide entry
    gave 3.0 x 1.5 = 4.5 m2 against the architect's 3.5 m2 ceiling. The depth is
    now searched on the grid and the result must land inside the band, so the
    3.5 ceiling is enforced rather than discovered afterwards. The WC still spans
    the full width of the remainder — it stays a BAND, not a corner cut, because
    a corner would leave the Foyer L-shaped and every downstream stage (wall
    rasterisation, door hosting, the Revit builder) assumes rectangles. On this
    plot the band lands at 2.0 x 1.5 = 3.0 m2, which is the architect's own
    suggested figure.
    """
    x0, y0, x1, y1 = rect
    wc = standards.ROOMS["Guest WC"]
    foy = standards.ROOMS["Foyer"]

    def try_axis(ax: bool):
        if ax:
            steps = _grid_steps(wc.min_w_m, (x1 - x0) - _ceil_snap(foy.min_w_m))
            if mud_side == "W":  # Mudroom holds x0 -> WC takes the x1 end
                mk = lambda d: ((x1 - d, y0, x1, y1), (x0, y0, x1 - d, y1))
            else:
                mk = lambda d: ((x0, y0, x0 + d, y1), (x0 + d, y0, x1, y1))
        else:
            steps = _grid_steps(wc.min_h_m, (y1 - y0) - _ceil_snap(foy.min_h_m))
            if mud_side == "S":  # Mudroom holds y0 -> WC takes the y1 end
                mk = lambda d: ((x0, y1 - d, x1, y1), (x0, y0, x1, y1 - d))
            else:
                mk = lambda d: ((x0, y0, x1, y0 + d), (x0, y0 + d, x1, y1))
        # RULING 1/2 (2026-08-04): both sub-rooms here are tier 3, so this scorer
        # only ever expresses "overshoot least" (Ruling 1's stop-at-ideal) and
        # never reprioritises anything -- but it goes through the same
        # _cut_score as the other three cutters so the rule lives in one place.
        out: list[list[FinalRoom]] = []
        for depth in steps:
            wc_rect, foy_rect = mk(depth)
            if _in_band("Guest WC", wc_rect) and _in_band("Foyer", foy_rect):
                wc_room = FinalRoom("Guest WC", "wet", zone, wc_rect)
                foy_room = FinalRoom("Foyer", "circ", zone, foy_rect)
                lo_first = (wc_rect[0], wc_rect[1]) < (foy_rect[0], foy_rect[1])
                out.append([wc_room, foy_room] if lo_first else [foy_room, wc_room])
        return _best_cut(out)

    # `along_x` is the PREFERRED axis (it is the one whose end-choice reasoning
    # is documented above), but the other axis is tried as a fallback rather than
    # giving up. Why this matters: the architect's 3.5 m2 WC ceiling is only ~1.5
    # grid cells of area, so on a 0.5 m grid the ONLY realisable WC rectangles are
    # 1.5 x 1.5 = 2.25 and 1.5 x 2.0 = 3.0 (2.5 x 1.5 = 3.75 is already over).
    # Committing to one axis therefore threw away half of an already tiny set and
    # collapsed the entry zone to a SINGLE legal shape — measured, and with exact
    # tiling on it made the whole plan INFEASIBLE. Both sub-rooms stay rectangles
    # and the WC still lands against the Foyer either way, so the fallback costs
    # no guarantee — but only now that `mud_side` picks its end. Measured: 6
    # legal entry shapes on the preferred axis alone vs 25 with the fallback, so
    # dropping the fallback is not an option; picking the right end is.
    return try_axis(along_x) or try_axis(not along_x)


def _slice_entry(r: ZoneRect, side: str | None) -> list[FinalRoom]:
    # `side` is the direction of the Garage. entry uses the BOTH-axis-legal
    # intersection table (legal_pairs), so its slice is legal on either axis and
    # the side may be read straight from geometry (_side_of) in slice_zones — no
    # cut-axis solver var is needed here (unlike kitchen_laundry).
    #
    # THREE rooms since the guest-WC task: Mudroom (garage-side buffer) |
    # Guest WC | Foyer. The уборная is a sub-room of the entry zone rather than
    # a zone of its own because that is what makes BOTH of its requirements hold
    # at ROOM level by construction: it is cut adjacent to the Foyer, which is
    # circulation, so a guest reaches it without passing a bedroom; and it sits
    # on the zone's south flank, against kitchen_laundry. Zone-level adjacency
    # has produced four separate defects in this project, so neither guarantee
    # is left to a zone-level share constraint.
    #
    # RECORDED TENSION: the Posobie's zoning guidance groups the sanitary unit
    # with the BEDROOM group, while its rural-house guidance puts the bath near
    # the kitchen and entrance and says the уборная should follow the bath. The
    # architect's ruling is binding and matches the second reading, so the WC
    # goes by the entrance. If the first reading ever wins, this is the function
    # to change.
    #
    # The zone's legal (w, h) table is NOT hand-updated for the third room:
    # legal_pairs() probes this very function (_slice_probe -> _legal_1), so the
    # envelope re-derives itself. zones.inject_guest_wc funds the area.
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    mud = standards.ROOMS["Mudroom"]
    foy = standards.ROOMS["Foyer"]
    if side is None:
        return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
    # Mudroom (garage-side buffer) gets its min strip (ceil-snapped); the Foyer
    # remainder then gives up its far end to the WC, if it can afford to.
    if side in ("W", "E"):
        depth = _ceil_snap(mud.min_w_m)  # Mudroom X-depth
        if (w - depth) < foy.min_w_m or h < max(foy.min_h_m, mud.min_h_m):
            return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
        if side == "W":
            mx = x0 + depth
            mud_room = FinalRoom("Mudroom", "service", r.zone, (x0, y0, mx, y1))
            rest = (mx, y0, x1, y1)
        else:
            mx = x1 - depth
            mud_room = FinalRoom("Mudroom", "service", r.zone, (mx, y0, x1, y1))
            rest = (x0, y0, mx, y1)
        # W/E cut -> the Mudroom strip runs the full depth, so the WC splits the
        # remainder along y (south end), keeping the Foyer's north wall free.
        # `mud_side` matters only if that fails and the x fallback runs: there the
        # WC must take the end AWAY from the Mudroom or it severs the Foyer.
        split = _split_off_wc(r.zone, rest, along_x=False, mud_side=side)
        if split is None:
            return [mud_room, FinalRoom("Foyer", "circ", r.zone, rest)] if side == "W" else [
                FinalRoom("Foyer", "circ", r.zone, rest), mud_room]
        return [mud_room, *split] if side == "W" else [*split, mud_room]
    depth = _ceil_snap(mud.min_h_m)  # Mudroom Y-depth
    if (h - depth) < foy.min_h_m or w < max(foy.min_w_m, mud.min_w_m):
        return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
    if side == "S":
        my = y0 + depth
        mud_room = FinalRoom("Mudroom", "service", r.zone, (x0, y0, x1, my))
        rest = (x0, my, x1, y1)
    else:
        my = y1 - depth
        mud_room = FinalRoom("Mudroom", "service", r.zone, (x0, my, x1, y1))
        rest = (x0, y0, x1, my)
    # N/S cut -> the Mudroom strip runs the full width, so the WC splits the
    # remainder along x (west end); the y fallback takes the end away from the
    # Mudroom (see _split_off_wc's `mud_side`).
    split = _split_off_wc(r.zone, rest, along_x=True, mud_side=side)
    if split is None:
        return [mud_room, FinalRoom("Foyer", "circ", r.zone, rest)] if side == "S" else [
            FinalRoom("Foyer", "circ", r.zone, rest), mud_room]
    return [mud_room, *split] if side == "S" else [*split, mud_room]


_SIMPLE_NAME: dict[str, tuple[str, Category]] = {
    "living": ("Living", "living"),
    "dining": ("Dining", "living"),
    "office": ("Office", "office"),
    "garage": ("Garage", "service"),
    # circulation (Task 5): the corridor is one Room, never cut.
    "circulation": ("Corridor", "circ"),
}


def slice_zones(result: SolveResult) -> list[FinalRoom]:
    by_zone = {r.zone: r for r in result.rects}
    dining = by_zone.get("dining")
    garage = by_zone.get("garage")
    cut_sides = getattr(result, "cut_sides", {}) or {}
    # kitchen_laundry cut axis is the SOLVER's decision (it constrained the shape
    # to match). Fall back to geometry only if the solver didn't record one.
    kl_side = cut_sides.get("kitchen_laundry")
    if kl_side is None and "kitchen_laundry" in by_zone and dining is not None:
        kl_side = _side_of(tuple(by_zone["kitchen_laundry"].rect_m), tuple(dining.rect_m))
    # entry's cut axis is now the SOLVER's decision too (solver._tie_axis_to_position
    # binds it to the garage's centroid side, and its shape table is the union of
    # the two axes). Read it, exactly as kitchen_laundry does, so the shape the
    # solver constrained and the cut the slicer performs cannot disagree. Fall
    # back to geometry only when the solver did not record one — an older result,
    # or a program with no garage.
    entry_side = cut_sides.get("entry")
    if entry_side is None and "entry" in by_zone and garage is not None:
        entry_side = _side_of(tuple(by_zone["entry"].rect_m), tuple(garage.rect_m))
    corridor_sides = getattr(result, "corridor_sides", {}) or {}
    master_corridor_side = corridor_sides.get("master_suite")
    kl_corridor_side = corridor_sides.get("kitchen_laundry")
    # children's corridor side is only recorded when the four-way disjunction is
    # on (see solver._force_vertical_cover_center); None otherwise, which is the
    # historical horizontal cut.
    child_corridor_side = corridor_sides.get("children")
    degraded: list[str] = []
    rooms: list[FinalRoom] = []
    for zr in result.rects:
        z = zr.zone
        if z in _COMPOSITE:
            if z == "master_suite":
                cut = _slice_master(zr, master_corridor_side)
            elif z == "children":
                cut = _slice_children(zr, child_corridor_side)
            elif z == "kitchen_laundry":
                cut = _slice_kitchen(zr, kl_side, kl_corridor_side)
            else:
                cut = _slice_entry(zr, entry_side)
            # Every composite cutter still DEGRADES rather than raising when the
            # zone cannot hold all its members (a single Master Bedroom, a single
            # Children Bedroom, a Kitchen with no Laundry, an entry with no Guest
            # WC). Degrading is the right behaviour -- a warning that names the
            # problem is worth more than an exception that kills an otherwise
            # valid solve -- but it must not be SILENT, which is what it was.
            # One central check instead of four scattered emitters: it cannot be
            # forgotten when a fifth cutter is added, and it reads the intended
            # decomposition from zone_members() rather than a hand-kept list.
            msg = _degradation_warning(z, zr, cut)
            if msg is not None:
                degraded.append(msg)
            rooms += cut
        elif z in _SIMPLE_NAME:
            name, cat = _SIMPLE_NAME[z]
            rooms.append(FinalRoom(name, cat, z, tuple(zr.rect_m)))
        else:
            name = Z.ZONE_DISPLAY.get(z, z.title())
            rooms.append(FinalRoom(name, "living", z, tuple(zr.rect_m)))
    # Same channel band_conflicts uses: SolveResult.warnings, which generate.py
    # prepends to Layout.warnings before validate() seeds its own list from it.
    # Deduped because slice_zones is called more than once per result on some
    # paths (build_layout, plus SVG/report readers) and a repeated line reads
    # like two separate defects.
    for msg in degraded:
        if msg not in result.warnings:
            result.warnings.append(msg)
    return rooms


# ---------------------------------------------------------------------------
# legal (w, h) tables (Phase 3)
#
# For each COMPOSITE zone, the FULL set of grid (w, h) pairs whose slice is
# standards-legal — NOT a min-area bounding box. The legal region is a staircase,
# and a box over a staircase (min_w from one corner, min_h from another) admits
# illegal shapes. The solver pins the zone's (w, h) to this set exactly, with
# AddAllowedAssignments.
#
# kitchen_laundry / entry cut along whichever axis their director (dining /
# garage) lies on, so each pair is tagged with the axis it is legal for: ns=1 for
# the N/S cut, ns=0 for the W/E cut. This is the UNION of the two axes, not the
# intersection — a shape legal only for the N/S cut is admitted as long as the
# solver commits to the N/S axis (and records it for the slicer). The old
# min_w/min_h hedged both axes at once and paid 22.5 m2 for a zone whose rooms
# need 12.5 (N/S) or 13.5 (W/E).
# ---------------------------------------------------------------------------

_NS_REP, _WE_REP = "S", "W"  # representative sides for the N/S and W/E cut axes
_STEPS = range(2, 31)        # candidate dimension in grid units: 1.0 .. 15.0 m

# Composite zones the slicer subdivides.
_COMPOSITE = {"master_suite", "children", "kitchen_laundry", "entry"}
# kitchen_laundry and entry get a UNION table + a solver cut-axis var: the
# intersection (both-axis-legal) would cost kitchen_laundry 22.5 vs the 12.5/13.5
# single axes. master/children are single-axis and need neither.
# PHASE 1: `entry` joined kitchen_laundry here. It used the INTERSECTION (legal
# on BOTH cut axes) because its cut side was read straight from geometry, and at
# the time "0.85*target nearly binds there anyway" made that cheap. The
# architect's 3.5 m2 Guest WC ceiling ended that: on a 0.5 m grid the only WC
# rectangles under it are 1.5 x 1.5 and 1.5 x 2.0, and requiring one shape to
# work on BOTH axes collapsed the whole entry table to a SINGLE rectangle
# (3.5 x 4.0). Measured: with exact tiling on, one fixed entry rectangle makes
# the entire plan INFEASIBLE. Each axis alone admits 11-14 shapes, so the union
# plus a solver cut-axis var — exactly the kitchen_laundry pattern — is what
# makes the band adoptable at all.
_AXIAL = {"kitchen_laundry", "entry"}

# CB3 GENERALISATION, default OFF. When True, `children` joins _AXIAL: its shape
# table becomes the union of the two band directions plus a solver cut-axis bit,
# solver._force_vertical_cover_center offers the corridor all FOUR faces instead
# of only E/W, and _slice_children reads the winning side and transposes.
#
# Why it is a flag and not just the new behaviour: CB3's E/W-only axis is the
# proven blocker for three separate constraints (kitchen daylight, the Guest WC
# wet core, room-level Kitchen<->Dining), so the change has to be measurable
# against the exact pre-change packing. With this False, every path below is the
# historical one byte-for-byte -- children stays out of _AXIAL, keeps its
# single-orientation table, and never receives a corridor_side.
_CHILD_AXIAL: bool = False


def _is_axial(zone_id: str) -> bool:
    return zone_id in _AXIAL or (zone_id == "children" and _CHILD_AXIAL)

# non-composite zone -> its single room standard (envelope = that room).
_ZONE_ROOM = {
    "living": "Living",
    "dining": "Dining",
    "office": "Office",
    "garage": "Garage",
    "circulation": "Corridor",
}

_PAIRS_CACHE: dict[str, object] = {}
_MINIMA_CACHE: dict[str, object] = {}


def _room_legal(name: str, rect: geom.Rect) -> bool:
    spec = standards.ROOMS.get(name)
    if spec is None:
        return True
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    if w < spec.min_w_m - geom.EPS or h < spec.min_h_m - geom.EPS:
        return False
    if w * h < spec.min_area_m2 - geom.EPS:
        return False
    short = min(w, h)
    aspect = max(w, h) / short if short > geom.EPS else float("inf")
    return aspect <= spec.max_aspect + geom.EPS


def _slice_probe(zone_id: str, w: float, h: float, side: str | None) -> list[FinalRoom]:
    zr = ZoneRect(zone_id, 0.0, 0.0, w, h)
    if zone_id == "master_suite":
        return _slice_master(zr)
    if zone_id == "children":
        # `side` is the CORRIDOR's side here (the analogue of the director side
        # kitchen_laundry/entry key off), so the probe must pass it once children
        # is axial -- otherwise the N/S band direction is never probed and its
        # half of the union table comes back empty. With the flag off, children
        # ignores `side` exactly as before.
        return _slice_children(zr, side if _CHILD_AXIAL else None)
    if zone_id == "kitchen_laundry":
        return _slice_kitchen(zr, side)
    if zone_id == "entry":
        return _slice_entry(zr, side)
    return []


def _legal_1(zone_id: str, w: float, h: float, side: str | None) -> bool:
    """Is this zone shape one the slicer can cut into a FULL, IN-BAND set of
    rooms?

    Two conditions, and the second was added in Phase 1 after it bit us:
      - every emitted room is inside BOTH its Neufert shape floor and the
        architect's area band (_in_band), and
      - the cut is COMPLETE — it emits as many rooms as the zone's intended
        decomposition, not a degraded one.

    Without the completeness test a shape whose entry cut silently dropped the
    Guest WC still counted as legal (2 rooms >= 2), the solver picked it, and the
    plan shipped a 13.5 m2 Foyer and no WC at all. A partial cut is not a legal
    cut; it is the slicer telling us this shape does not work."""
    rooms = _slice_probe(zone_id, w, h, side)
    if len(rooms) < len(zone_members(zone_id)):
        return False
    return len(rooms) >= 2 and all(_in_band(rm.name, rm.rect) for rm in rooms)


def legal_pairs(zone_id: str):
    """Grid-unit legal (w, h) table for a COMPOSITE zone, else None. Cached.

    kitchen_laundry -> list[(wu, hu, ns)]: the UNION of the two cut axes, ns=1 for
      the N/S cut and ns=0 for the W/E cut (the solver picks (w,h) AND ns together
      and ties ns to the Dining side).
    entry           -> list[(wu, hu)]: the INTERSECTION (legal on BOTH axes), so
      the cut is axis-agnostic.
    master_suite / children -> list[(wu, hu)]: single orientation."""
    if zone_id in _PAIRS_CACHE:
        return _PAIRS_CACHE[zone_id]
    if zone_id not in _COMPOSITE:
        _PAIRS_CACHE[zone_id] = None
        return None
    if _is_axial(zone_id):
        pairs: list = []
        for wu in _STEPS:
            for hu in _STEPS:
                w, h = wu * GRID_M, hu * GRID_M
                if _legal_1(zone_id, w, h, _NS_REP):
                    pairs.append((wu, hu, 1))
                if _legal_1(zone_id, w, h, _WE_REP):
                    pairs.append((wu, hu, 0))
    else:  # master_suite / children: single orientation
        pairs = [
            (wu, hu)
            for wu in _STEPS
            for hu in _STEPS
            if _legal_1(zone_id, wu * GRID_M, hu * GRID_M, None)
        ]
    _PAIRS_CACHE[zone_id] = pairs
    return pairs


_MEMBERS_CACHE: dict[str, tuple[str, ...]] = {}
_BAND_CACHE: dict[str, object] = {}


def zone_members(zone_id: str) -> tuple[str, ...]:
    """The room names this zone slices into, READ OFF THE REAL CUTTERS rather
    than hand-listed. Probes every legal (w, h) and keeps the FULLEST split —
    a composite zone degrades to fewer rooms when it is too small, and the band
    must be derived from the intended decomposition, not from a degenerate one.

    Deriving membership instead of tabulating it is the same discipline as
    compute_zone_minima: change a cutter and the band re-derives itself, so the
    two cannot drift apart silently."""
    if zone_id in _MEMBERS_CACHE:
        return _MEMBERS_CACHE[zone_id]
    room = _ZONE_ROOM.get(zone_id)
    if room is not None:
        _MEMBERS_CACHE[zone_id] = (room,)
        return _MEMBERS_CACHE[zone_id]
    # Probed over the RAW grid against the Neufert floor only — deliberately NOT
    # via legal_pairs(), which now calls back into this function through
    # _legal_1's completeness test. Membership is "what does the cutter emit when
    # it succeeds", a question that must be answerable before the band is applied.
    best: tuple[str, ...] = ()
    for wu in _STEPS:
        for hu in _STEPS:
            # Probe BOTH representative sides. master_suite/children ignore
            # `side`, but kitchen_laundry and entry key their whole cut off it —
            # passing None there makes _slice_entry return a bare Foyer and
            # silently understate the zone's membership (and hence its band).
            for side in (_NS_REP, _WE_REP):
                rooms = _slice_probe(zone_id, wu * GRID_M, hu * GRID_M, side)
                if len(rooms) > len(best) and all(_room_legal(r.name, r.rect) for r in rooms):
                    best = tuple(r.name for r in rooms)
    _MEMBERS_CACHE[zone_id] = best
    return best


def zone_band(zone_id: str) -> tuple[float, float] | None:
    """(lo, hi) area band for a solver zone: the sum of its member rooms' own
    architect bands. None when no member carries one.

    This is what replaces AREA_LO/AREA_HI. A proportional window around a
    reconciled target could not express "this zone holds a 16-30 bedroom, a 5-12
    bath and a 4-12 closet"; summing the members can, and it is the only form in
    which the architect's table can reach the solver at all."""
    if zone_id in _BAND_CACHE:
        return _BAND_CACHE[zone_id]
    members = zone_members(zone_id)
    bands = [standards.architect_band(n) for n in members]
    out = None
    if members and all(b is not None for b in bands):
        out = (round(sum(b[0] for b in bands), 4), round(sum(b[1] for b in bands), 4))
    _BAND_CACHE[zone_id] = out
    return out


_PENALTY_CACHE: dict[str, object] = {}


def cut_penalty_pairs(zone_id: str):
    """legal_pairs(zone_id) with ONE EXTRA COLUMN: the tier-weighted penalty
    (_cut_score) of the best legal cut at that shape. None for non-composites.

    This is what lets the solver optimise the architect's Ruling 1/2 EXACTLY
    rather than through a proxy. A zone-AREA target cannot express what he asked
    for, and it is the ancillary band that makes the difference: the Laundry
    spans the zone's whole cross-dimension at its own grid-snapped 2.0 m minimum
    width, so its area is 2.0 x (the zone's other side) and the Kitchen gets only
    the remainder. The zone's SHAPE therefore decides the split, at a FIXED area.
    Measured on the real table, kitchen_laundry at 18.00 m2:

        4.5 x 4.0  ->  Kitchen 10.00 (its floor), Laundry 8.00   penalty 31360
        6.0 x 3.0  ->  Kitchen 12.00,             Laundry 6.00   penalty 20480

    Same 18.00 m2, same zone, 10880 of penalty between them and the plan already
    on the worse one. An area target scores those two identically; this table
    does not. (At 22.50 m2 the spread is wider still: 4.5 x 5.0 gives Kitchen
    12.50 / Laundry 10.00 at penalty 20640, while 7.5 x 3.0 gives Kitchen 16.50 /
    Laundry 6.00 at penalty 1360 -- both rooms at or past ideal.)

    The solver already pins each composite zone's (w, h) to legal_pairs with
    AddAllowedAssignments, so the penalty rides along as one more column of that
    same table and enters the objective directly. No approximation: the number
    the solver minimises is the real room-level shortfall of the cut the slicer
    will actually perform.

    The axial zones (kitchen_laundry, entry, and children under _CHILD_AXIAL)
    are probed on the axis's REPRESENTATIVE side, exactly as legal_pairs is. That
    is exact for this purpose and not merely consistent: the other side of an
    axis is the same cut mirrored (_slice_kitchen's place_side, _slice_master's
    "N" flip, _split_off_wc's mud_side all swap WHICH END a band takes, never its
    size), so every candidate's room AREAS are identical on both sides."""
    if zone_id in _PENALTY_CACHE:
        return _PENALTY_CACHE[zone_id]
    pairs = legal_pairs(zone_id)
    out = None
    if pairs:
        out = []
        for t in pairs:
            wu, hu = t[0], t[1]
            side = (_NS_REP if t[2] else _WE_REP) if len(t) == 3 else None
            rooms = _slice_probe(zone_id, wu * GRID_M, hu * GRID_M, side)
            below, above = _cut_score([(r.name, r.rect) for r in rooms])
            out.append((*t, below + above))
    _PENALTY_CACHE[zone_id] = out
    return out


_IDEAL_CACHE: dict[str, object] = {}


def zone_ideal(zone_id: str) -> float | None:
    """The ideal AREA for a NON-COMPOSITE solver zone -- the architect's Ruling 1
    lifted from the room to the zone the solver actually places -- or None for a
    composite zone and for any zone with no architect band.

    None for composites ON PURPOSE, and it is not a gap: a composite's ideal
    cannot honestly be stated as an area at all, because its split is decided by
    the zone's SHAPE (the ancillary band spans the whole cross-dimension, so two
    shapes of equal area cut completely differently -- see cut_penalty_pairs for
    the measured 18.00 m2 example). Composites are scored per shape by
    cut_penalty_pairs instead, which is exact where an area target would be a
    proxy. A non-composite zone IS one room, so for those the area is the room's
    area and this is exact too.

    Clamped into the zone's own realisable window -- the grid-snapped shape floor
    below, the architect's ceiling above. That clamp is what keeps this safe to
    put in the objective where Phase 1's adherence term was not: a target the
    zone cannot reach is a penalty no solution can avoid, i.e. a constant with a
    weight on it rather than a preference."""
    if zone_id in _IDEAL_CACHE:
        return _IDEAL_CACHE[zone_id]
    out: float | None = None
    room = _ZONE_ROOM.get(zone_id)
    if room is not None:
        band = standards.architect_band(room)
        if band is not None:
            spec = standards.ROOMS.get(room)
            floor = standards.area_floor(room)
            if spec is not None:
                floor = max(floor, _ceil_snap(spec.min_w_m) * _ceil_snap(spec.min_h_m))
            out = min(max(standards.area_ideal(room), floor), band[1])
    _IDEAL_CACHE[zone_id] = out
    return out


def _degradation_warning(zone_id: str, zr: ZoneRect, emitted: list[FinalRoom]) -> str | None:
    """One line naming the zone, its area, and every member the cut had to drop —
    or None when the cut was complete.

    This is the diagnostic the Phase 1 brief asked for. It is a WARNING and not
    an exception on purpose: by the time a cutter degrades, the solve is already
    OPTIMAL and the rest of the plan is fine, so killing it destroys more
    information than it preserves. What must not happen is what happened before
    — a Guest WC quietly vanishing from a shipped plan with nothing said.

    If this ever fires in production it is a SOLVER/SLICER DISAGREEMENT, not a
    cut to retry: _legal_1 probes these same cutters through _slice_probe and
    already refuses any zone shape whose cut is incomplete or out of band, so a
    shape that reaches here should have been unreachable. Read it as "the shape
    table and the cutter have drifted apart", and fix the table."""
    members = zone_members(zone_id)
    if not members:
        return None
    got = {r.name for r in emitted}
    missing = [n for n in members if n not in got]
    if not missing:
        return None
    x0, y0, x1, y1 = zr.rect_m
    w, h = x1 - x0, y1 - y0
    need = "; ".join(
        f"{n} (needs >= {standards.area_floor(n):.2f} m2, <= {standards.area_ceiling(n):.2f} m2, "
        f"min dims {standards.ROOMS[n].min_w_m} x {standards.ROOMS[n].min_h_m} m)"
        if n in standards.ROOMS else n
        for n in missing
    )
    return (
        f"slicer: zone {zone_id!r} at {w:.1f} x {h:.1f} m = {w * h:.2f} m2 could not be cut "
        f"into all of [{', '.join(members)}] — dropped {need}. It ships "
        f"{len(emitted)} room(s) instead of {len(members)}. legal_pairs()/_legal_1 is "
        f"supposed to keep this shape away from the solver, so this is a solver/slicer "
        f"disagreement rather than a zone that is merely small."
    )


def compute_zone_minima(zone_id: str):
    """standards.ZoneMinima for a NON-composite zone (its room standard), else
    None — composite zones use legal_pairs() instead. Cached."""
    if zone_id in _MINIMA_CACHE:
        return _MINIMA_CACHE[zone_id]
    result = None
    if zone_id not in _COMPOSITE:
        room = _ZONE_ROOM.get(zone_id)
        if room is not None:
            s = standards.ROOMS[room]
            result = standards.ZoneMinima(s.min_w_m, s.min_h_m, s.min_area_m2, s.max_aspect)
    _MINIMA_CACHE[zone_id] = result
    return result


# ---------------------------------------------------------------------------
# wall rasterisation
# ---------------------------------------------------------------------------


def _cell_room(rooms: list[FinalRoom], cx: float, cy: float) -> int:
    for i, rm in enumerate(rooms):
        x0, y0, x1, y1 = rm.rect
        if x0 - geom.EPS <= cx <= x1 + geom.EPS and y0 - geom.EPS <= cy <= y1 + geom.EPS:
            if x0 < cx < x1 and y0 < cy < y1:
                return i
    return -1


def _build_walls(
    rooms: list[FinalRoom], plot_w: float, plot_d: float, height: float
) -> list[_WallRec]:
    W = int(round(plot_w / GRID_M))
    H = int(round(plot_d / GRID_M))
    occ = [[-1] * H for _ in range(W)]
    for i in range(W):
        cx = (i + 0.5) * GRID_M
        for j in range(H):
            cy = (j + 0.5) * GRID_M
            occ[i][j] = _cell_room(rooms, cx, cy)

    recs: list[_WallRec] = []
    n = 0

    def occ_at(i: int, j: int) -> int:
        if 0 <= i < W and 0 <= j < H:
            return occ[i][j]
        return -1

    # vertical wall lines at x = gx*GRID
    for gx in range(W + 1):
        j = 0
        while j < H:
            left, right = occ_at(gx - 1, j), occ_at(gx, j)
            if left == right:
                j += 1
                continue
            j0 = j
            while j < H and occ_at(gx - 1, j) == left and occ_at(gx, j) == right:
                j += 1
            lo, hi = j0 * GRID_M, j * GRID_M
            exterior = left == -1 or right == -1
            fixed = gx * GRID_M
            n += 1
            recs.append(
                _WallRec(
                    Wall(
                        id=f"w{n}",
                        start=[fixed, lo],
                        end=[fixed, hi],
                        thickness_m=EXT_WALL_M if exterior else INT_WALL_M,
                        height_m=height,
                        exterior=exterior,
                    ),
                    a=left,
                    b=right,
                    edge=geom.Edge("V", fixed, lo, hi),
                )
            )

    # horizontal wall lines at y = gy*GRID
    for gy in range(H + 1):
        i = 0
        while i < W:
            down, up = occ_at(i, gy - 1), occ_at(i, gy)
            if down == up:
                i += 1
                continue
            i0 = i
            while i < W and occ_at(i, gy - 1) == down and occ_at(i, gy) == up:
                i += 1
            lo, hi = i0 * GRID_M, i * GRID_M
            exterior = down == -1 or up == -1
            fixed = gy * GRID_M
            n += 1
            recs.append(
                _WallRec(
                    Wall(
                        id=f"w{n}",
                        start=[lo, fixed],
                        end=[hi, fixed],
                        thickness_m=EXT_WALL_M if exterior else INT_WALL_M,
                        height_m=height,
                        exterior=exterior,
                    ),
                    a=down,
                    b=up,
                    edge=geom.Edge("H", fixed, lo, hi),
                )
            )
    return recs


# ---------------------------------------------------------------------------
# doors (spanning tree) + entry
# ---------------------------------------------------------------------------


def _interior_wall_between(recs: list[_WallRec], a: int, b: int) -> _WallRec | None:
    best: _WallRec | None = None
    for r in recs:
        if {r.a, r.b} == {a, b} and -1 not in (r.a, r.b):
            if best is None or r.edge.length > best.edge.length:
                best = r
    return best


def _door_pos(lo: float, hi: float, width: float, jamb: float, anchor: str = "lo") -> float:
    """Position (scalar along the span, orientation-agnostic) for a door of
    `width` on a wall span [lo, hi]. A centered door splits the wall into two
    stub runs, neither long enough for furniture (architect review, Task 7);
    instead push the door's near jamb `jamb` clear of ONE end corner, so the
    swing tucks against that corner's perpendicular return wall and the other
    side keeps one continuous usable run. `jamb` is derived from the host
    wall's own thickness_m (not a new constant) — a thicker wall's corner
    return plausibly wants more clearance, and every wall run's endpoints are
    real corners by construction (rasterised from cell-adjacency changes, see
    module docstring), so either end always has a genuine perpendicular wall
    to tuck against.

    WHICH end is `anchor` ("lo" or "hi"), chosen by _position_doors (R7) — see
    that function for the rule and its Neufert basis. It used to be hardwired
    to "lo": deterministic, but an arbitrary pick between two ends Neufert
    does not distinguish, and the architect's round-3 review flagged the
    Mudroom->Garage door as sitting at the wrong one.

    Falls back to the midpoint when [lo, hi] is too short to fit jamb+width
    without the door overflowing the far end — a short span has no usable run
    to preserve either way, so there is nothing an offset would buy (and both
    anchors then coincide, keeping the fallback end-agnostic).
    """
    if hi - lo + geom.EPS < jamb + width:
        return (lo + hi) / 2.0
    if anchor == "hi":
        return hi - jamb - width / 2.0
    return lo + jamb + width / 2.0


def _door_center(rec: _WallRec, width: float, anchor: str) -> list[float]:
    e = rec.edge
    pos = _door_pos(e.lo, e.hi, width, rec.wall.thickness_m, anchor)
    return [e.fixed, pos] if e.orient == "V" else [pos, e.fixed]


def _door_on(
    rec: _WallRec,
    rooms: list[FinalRoom],
    frm: str,
    to: str,
    anchor: str = "lo",
    secondary: bool = False,
) -> Door:
    e = rec.edge
    width = min(DOOR_W, max(0.7, e.length - 0.2))
    return Door(**{
        "from": frm, "to": to, "wall_id": rec.wall.id,
        "center": _door_center(rec, width, anchor),
        "width_m": width, "height_m": DOOR_H, "secondary": secondary,
    })


def _build_doors(
    rooms: list[FinalRoom], recs: list[_WallRec]
) -> tuple[list[Door], Door, list[str]]:
    """Doors ARE the access graph (Task 5 Phase 3). This does NOT recompute a
    tree — it consumes validator.access_tree (the single source of truth) and
    hosts one Door per edge on `_interior_wall_between` (a wall between two real
    rooms, so an interior door can NEVER land on an exterior wall). The single
    front door (entry->OUTSIDE) and the terrace door are the only openings on
    exterior walls, added separately.

    access_tree obeys the rules validate_plan gates on — a no_through_traffic room
    is a LEAF (never a corridor), an ensuite room is entered only from its parent
    — so the access path never transits a bedroom. That is the root-cause fix for
    the children hall-Bathroom bug: the middle Bathroom, placed against the
    corridor by the solver, is reached directly; the beds get their own doors to
    circulation, never routing the Bathroom behind a bedroom.
    """
    from .validator import access_tree  # lazy: the shared access-tree authority

    warnings: list[str] = []
    # The doors ARE the access tree's edges — not a second tree recomputed here.
    # validator.access_tree is the single source of truth (root, leaf/ensuite
    # rules, adjacency); we only turn each of its edges into a hosted Door on the
    # interior wall between the two rooms. So a door can never exist that the
    # access tree (which validate_plan gates on) did not produce.
    edges, reached, _root = access_tree(rooms)

    doors: list[Door] = []
    for i, j in edges:
        rec = _interior_wall_between(recs, i, j)
        if rec is None:
            warnings.append(
                f"access edge {rooms[i].name!r}<->{rooms[j].name!r} has no interior wall"
            )
            continue
        doors.append(_door_on(rec, rooms, rooms[i].name, rooms[j].name))

    for i, rm in enumerate(rooms):
        if i not in reached:
            warnings.append(f"room {rm.name!r} not reachable by a door (isolated)")

    # main entry: exterior wall of Foyer (prefer north/street), else Mudroom/Living
    entry = _build_entry(rooms, recs, warnings)
    return doors, entry, warnings


def _build_entry(rooms: list[FinalRoom], recs: list[_WallRec], warnings: list[str]) -> Door:
    def ext_walls_of(name: str) -> list[_WallRec]:
        idx = next((i for i, rm in enumerate(rooms) if rm.name == name), None)
        if idx is None:
            return []
        out = [r for r in recs if r.wall.exterior and idx in (r.a, r.b) and r.edge.length >= MIN_DOOR_WALL]
        return out

    for host in ("Foyer", "Mudroom", "Living"):
        walls = ext_walls_of(host)
        if not walls:
            continue
        # prefer a north-facing (higher y) horizontal wall = street side
        walls.sort(key=lambda r: (r.edge.orient != "H", -r.edge.fixed if r.edge.orient == "H" else 0))
        return _door_on(walls[0], rooms, "OUTSIDE", host)

    warnings.append("no exterior wall available for the main entry")
    # fall back to the longest exterior wall overall
    ext = [r for r in recs if r.wall.exterior]
    ext.sort(key=lambda r: r.edge.length, reverse=True)
    host_idx = ext[0].a if ext[0].a != -1 else ext[0].b
    return _door_on(ext[0], rooms, "OUTSIDE", rooms[host_idx].name)


# ---------------------------------------------------------------------------
# R7: which END of its host wall a door sits at
# ---------------------------------------------------------------------------
#
# Neufert settles the HINGE side ("when located in corner of rm door should be
# hinged at side nearer corner") but says nothing about WHICH corner a door
# should be pushed toward when its wall has a usable corner at both ends. That
# left _door_pos hardwired to the `lo` end: deterministic, but an arbitrary tie
# broken by the rasterizer's scan order rather than by anything architectural —
# and the architect's round-3 review flagged the resulting Mudroom->Garage
# placement as sitting at the wrong end of its wall.
#
#   R7: a door sits at the end of its host wall NEARER THE OPENING BY WHICH ITS
#       HOST ROOM IS ENTERED — the previous door on the access route.
#
# Doors are positioned in access-tree order outward from the front door, so
# each leg of a route is the shortest hop from the one before and the walked
# path through the house stops zig-zagging from wall end to wall end. This is
# the same "circulation is the thing being designed" principle the corridor
# spine already encodes, applied at door scale, and it needs no furniture: the
# reference point is another door, which we have.
#
# The front door itself has no predecessor on the route and keeps the `lo`
# anchor. So does any door whose parent room was never reached (a skipped
# access edge), which keeps the fallback total.


def _nearer_anchor(rec: _WallRec, width: float, ref: list[float]) -> str:
    """Which anchor end puts the door closer to `ref`. Ties keep "lo", and the
    comparison is strict, so the choice is a pure function of the geometry."""
    best, best_d = "lo", None
    for anchor in ("lo", "hi"):
        d = math.dist(_door_center(rec, width, anchor), ref)
        if best_d is None or d < best_d - geom.EPS:
            best, best_d = anchor, d
    return best


def _swing_compromises(
    rooms: list[FinalRoom],
    recs: list[_WallRec],
    doors: list[Door],
    entry: Door,
    terrace: Terrace | None,
) -> list[str]:
    """Run the real swing assignment on a throwaway warnings list and return
    only the messages that mean it had to CONCEDE something: a hinge forced to
    the far end (R5 lost), a facing overridden (R2/R3 lost), a leaf with nowhere
    legal to go, a residual collision, or a leaf left obstructing a corridor.

    Used as a cost function, so R7 and the secondary doors can be checked
    against the same detector that gates the finished plan instead of a
    separate approximation of it.
    """
    probe: list[str] = []
    _assign_swings(rooms, recs, list(doors), entry, terrace, probe)
    marks = ("hinge moved to the FAR end", "facing overridden to",
             "UNRESOLVED swing collision", "no swing fits",
             "swings into circulation")
    return [w for w in probe if any(m in w for m in marks)]


def _position_doors(
    rooms: list[FinalRoom],
    recs: list[_WallRec],
    doors: list[Door],
    entry: Door,
    terrace: Terrace | None,
) -> None:
    """Apply R7 to every interior/terrace door, in place.

    `doors` arrives in access-tree edge order (see _build_doors), which is a
    valid topological order: a room's own incoming door is always appended
    before any door leading out of it. So the forward pass needs no iteration
    to a fixed point, and no two doors' positions can depend circularly on
    each other.

    R7 IS A PREFERENCE, NOT A NORM — the same standing as R5 (hinge at the
    nearer end) and below R1/R4 (facing), and it loses the same way. Pulling
    each door toward the previous one on the route also pulls doors TOWARD each
    other, and in a small entrance hall that is enough to make two leaves
    fight: on gE_eN the front door and Foyer->Corridor ended up 0.96 m apart,
    and the swing resolver could only clear them by opening the corridor door
    INTO the corridor — precisely what Neufert forbids and R1 exists to stop.
    So the second pass costs the R7 choice against the real swing detector and
    reverts, door by door, any placement that made it concede. Deterministic:
    doors are revisited in list order and a revert is kept only on a strict
    improvement.
    """
    rec_by_id = {r.wall.id: r for r in recs}
    # The room each door is entered FROM has a known incoming opening; the
    # entry door seeds the walk at the root.
    incoming: dict[str, Door] = {}
    if entry.to:
        incoming[entry.to] = entry
    moved: list[tuple[Door, _WallRec, list[float]]] = []
    for d in doors:
        rec = rec_by_id.get(d.wall_id)
        ref = incoming.get(d.from_)
        if rec is not None and ref is not None:
            before = list(d.center)
            d.center = _door_center(rec, d.width_m, _nearer_anchor(rec, d.width_m, ref.center))
            if d.center != before:
                moved.append((d, rec, before))
        incoming.setdefault(d.to, d)

    cost = len(_swing_compromises(rooms, recs, doors, entry, terrace))
    for d, rec, before in moved:
        if cost == 0:
            break
        after = d.center
        d.center = before
        trial = len(_swing_compromises(rooms, recs, doors, entry, terrace))
        if trial < cost:
            cost = trial  # keep the revert: R7 lost to the swing rules here
        else:
            d.center = after


# ---------------------------------------------------------------------------
# secondary doors: connections BEYOND the access-tree spanning set
# ---------------------------------------------------------------------------
#
# _build_doors hosts exactly one door per access-tree edge. A spanning tree over
# n rooms has n-1 edges and no cycles, so every trip between two rooms is forced
# up and back down the tree — which is why carrying a plate from the Kitchen to
# the Dining room walks Kitchen -> Corridor -> Living -> Dining even though the
# Kitchen and the Living room share a 2.5 m wall. Real dwellings have rings, not
# pure trees.
#
# NORM BASIS — SNiP 2.08.01-89 Posobie, apartment-planning section:
#   "Возможно создание дополнительных связей между смежными помещениями,
#    улучшающих функциональную и пространственную организацию квартир"
#   (additional connections between adjacent rooms may be created, improving
#   the functional and spatial organisation of apartments).
# That clause PERMITS the ring. What makes a particular ring REQUIRED is the
# separate clause carried by each FUNCTIONAL_PAIRS entry below.
#
# Secondary doors are strictly ADDITIVE. validator.access_tree is recomputed
# from room adjacency, never from the door list, so adding one cannot change the
# tree, the reachability gate, or the kitchen-direct invariant. Delete every
# door with secondary=True and the plan is exactly as connected as before — that
# is the invariant, and tests/test_secondary_doors.py asserts it directly.


@dataclass(frozen=True)
class FunctionalPair:
    """Two rooms the norms require to be within `max_hops` doors of each other.

    THE QUANTITY THAT IS THRESHOLDED HERE IS THE POINT. The first cut of this
    module gated a secondary door on how far apart the two rooms IT JOINS were
    (SECONDARY_MIN_HOPS = 3), a number calibrated by reading hop counts off one
    tree. That number does not survive a repack. Adding the guest WC re-parents
    the Kitchen from the Foyer to the Corridor — an improvement in itself — and
    that alone moved Kitchen<->Living from 3 hops to 2, disqualifying the door
    even though the door still delivers the identical result on the journey that
    motivated it:

        HEAD:     Kitchen->Living 3 hops,  Kitchen->Dining 4,  with door 2
        with WC:  Kitchen->Living 2 hops,  Kitchen->Dining 3,  with door 2

    A threshold whose meaning changes when the tree changes is thresholding the
    wrong quantity. So the requirement is stated on the JOURNEY SHORTENED, not
    on the PAIR JOINED: a door earns its place by bringing some named pair
    within its required distance, whatever route it takes to do so. That is
    stable across repacks, because it is a statement about the finished plan
    rather than about one traversal of it.

    `why` is the citation, not a comment: every pair here has to come from a
    norm, and the reason travels with the requirement so a later reader can
    check it rather than trust it.
    """

    a: str
    b: str
    max_hops: int
    why: str


# The named requirements. Seeded with the one clause the architect's round-3
# review turned up; the structure is a table so the next clause is a new row
# with its own citation rather than another special case in the selector.
FUNCTIONAL_PAIRS: tuple[FunctionalPair, ...] = (
    FunctionalPair(
        "Kitchen", "Dining", 2,
        "SNiP 2.08.01-89 Posobie, apartment-planning section: where the main "
        "dining zone sits OUTSIDE the kitchen and has NO DIRECT CONNECTION to "
        "it, the kitchen must carry a supplementary 2-3 seat dining area. This "
        "engine has no furniture model and so cannot provide that remedy; the "
        "connection is the only one of the two it can build, which makes the "
        "connection a requirement here rather than a preference. 2 hops = "
        "Kitchen -> one intervening room -> Dining, i.e. the food crosses at "
        "most one other room.",
    ),
)

# The generic path is GONE. Until this commit a candidate could also qualify by
# being >= SECONDARY_MIN_HOPS (3) from its partner and touching a habitable room.
# Measured on both feasible presets with the WC in place, it admitted NOTHING:
# every candidate it reached — Dining<->Garage, Living<->Laundry, Living<->Master
# Bathroom / Walk-in Closet, Kitchen<->Guest WC — was already refused by the
# ensuite or social-room rules, and the only pairs it uniquely reached
# (Laundry<->Garage, Laundry<->Mudroom) failed its own habitable-end test. It was
# dead machinery resting on a judgement call ("at least one end must be
# habitable") that no norm backed, so it is deleted rather than carried. A
# connection now earns its place one way only: by satisfying a cited requirement
# in FUNCTIONAL_PAIRS.
MAX_SECONDARY_DOORS = 2      # a house with rings, not an open plan by accident
SECONDARY_MIN_FREE_WALL_M = 1.5  # usable furniture run both rooms must keep


def _opening_spans(
    doors: list[Door], windows: list[Window], recs: list[_WallRec]
) -> dict[str, list[tuple[float, float]]]:
    """wall_id -> the [lo, hi] spans its openings consume, along the wall."""
    orient = {r.wall.id: r.edge.orient for r in recs}
    spans: dict[str, list[tuple[float, float]]] = {}
    for o in list(doors) + list(windows):
        ori = orient.get(o.wall_id)
        if ori is None:
            continue
        t = o.center[1] if ori == "V" else o.center[0]
        spans.setdefault(o.wall_id, []).append((t - o.width_m / 2, t + o.width_m / 2))
    return spans


def _longest_free_wall_run(
    idx: int, recs: list[_WallRec], spans: dict[str, list[tuple[float, float]]]
) -> float:
    """Longest uninterrupted opening-free wall segment bounding room `idx`.

    Neufert's room layouts all assume a continuous run to place furniture
    against (bed head, sofa back, worktop). A room whose every wall is chopped
    into stubs by openings is unfurnishable, so this is what a new door has to
    leave behind — measured per wall RUN, since a segment cannot continue
    around a corner.
    """
    best = 0.0
    for r in recs:
        if idx not in (r.a, r.b):
            continue
        pos = r.edge.lo
        for c0, c1 in sorted(spans.get(r.wall.id, [])):
            best = max(best, min(c0, r.edge.hi) - pos)
            pos = max(pos, c1)
        best = max(best, r.edge.hi - pos)
    return best


def _secondary_refused(rooms: list[FinalRoom], i: int, j: int) -> str | None:
    """None if a door between these two rooms breaks no access rule, else why.

    A tree edge is DIRECTED (parent -> child) and access_tree applies its tier
    rules to the child only. A secondary door is undirected — it can be walked
    either way — so every rule is applied in BOTH directions here. That is
    strictly stronger than the tree's own test, which is the point: the tree
    gets to assume a direction, an added ring does not.
    """
    from .validator import _is_public  # lazy: same reason as _build_doors

    for a, b in ((i, j), (j, i)):
        na, nb = rooms[a].name, rooms[b].name
        ca, cb = rooms[a].category, rooms[b].category
        spec = standards.ROOMS.get(nb)
        # TIER 2 (ensuite): an ensuite opens off its designated parent, full stop.
        if spec and spec.allowed_ensuite_parents and na not in spec.allowed_ensuite_parents:
            return (f"{nb} is an ensuite of {'/'.join(spec.allowed_ensuite_parents)}; "
                    f"a second door from {na} would open it onto the wrong room")
        # TIER 1 (privacy): a no_through PRIVATE room only ever opens off
        # circulation. NOTE the `private` half: access_tree's tier 1 reads
        # `no_through(nb) and cats[nb] == "private"`, and its docstring is
        # explicit that a no_through WET room (the Kitchen) is NOT bound by it —
        # "it opens off the dining/living in an open plan". Verified against
        # validator.py before relying on it; that exemption is exactly what
        # makes Kitchen<->Living a legal ring.
        if spec and spec.no_through_traffic and cb == "private" and ca != "circ":
            return f"{nb} is a no-through private room; it may only open off circulation, not {na}"
        # MIRROR (public): a social room is entered from circulation or another
        # social room, never through a private/service one.
        if _is_public(nb, cb) and ca != "circ" and not _is_public(na, ca):
            return (f"{nb} is a social room; it may only open off circulation or another "
                    f"social room, not {na}")
    return None


def _secondary_doors(
    rooms: list[FinalRoom],
    recs: list[_WallRec],
    doors: list[Door],
    entry: Door,
    terrace: Terrace | None,
    windows: list[Window],
) -> tuple[list[Door], list[dict]]:
    """Pick up to MAX_SECONDARY_DOORS additional connections. Returns
    (doors, report); the report records EVERY candidate and its verdict, for
    tests and review, and is deliberately not pushed into layout.warnings —
    a norm-sanctioned improvement is not a defect to warn about.

    Eligibility, all of which must hold:
      E1 the two rooms share an interior wall >= ACCESS_DOOR_M;
      F  adding it brings a VIOLATED FunctionalPair within its required hop
         distance — the only way in, and the reason it is stable across repacks
         (see FunctionalPair for what changed and why);
      E4 no access rule is broken in either direction (_secondary_refused);
      E5 both rooms keep a >= SECONDARY_MIN_FREE_WALL_M furniture run;
      E6 the new leaf's swing collides with nothing, and forces no concession
         from the existing all-pairs detector in _assign_swings.

    Ranked by requirements fixed, then total hops saved, then host-wall width.
    """
    from collections import deque
    from .validator import ACCESS_DOOR_M

    n = len(rooms)
    idx_of = {rm.name: i for i, rm in enumerate(rooms)}
    graph: dict[int, set[int]] = {i: set() for i in range(n)}
    for d in doors:
        a, b = idx_of.get(d.from_), idx_of.get(d.to)
        if a is not None and b is not None:
            graph[a].add(b)
            graph[b].add(a)

    def hops(src: int, extra: tuple[int, int] | None = None) -> dict[int, int]:
        """BFS from `src`, optionally with one candidate edge added. `extra` is
        how a candidate is scored on the journey it would shorten rather than on
        the two rooms it happens to join."""
        seen = {src: 0}
        q = deque([src])
        while q:
            cur = q.popleft()
            nbrs = set(graph[cur])
            if extra is not None:
                if cur == extra[0]:
                    nbrs.add(extra[1])
                elif cur == extra[1]:
                    nbrs.add(extra[0])
            for nb in sorted(nbrs):
                if nb not in seen:
                    seen[nb] = seen[cur] + 1
                    q.append(nb)
        return seen

    # Which named requirements does the plan currently FAIL? A pair whose rooms
    # are not both present imposes nothing (a sliced-out room is legitimate).
    violated: list[tuple[FunctionalPair, int, int, int]] = []
    for fp in FUNCTIONAL_PAIRS:
        ia, ib = idx_of.get(fp.a), idx_of.get(fp.b)
        if ia is None or ib is None:
            continue
        d0 = hops(ia).get(ib, -1)
        if d0 < 0 or d0 > fp.max_hops:
            violated.append((fp, ia, ib, d0))

    report: list[dict] = []
    ranked: list[tuple] = []
    for i in range(n):
        di = hops(i)
        for j in range(i + 1, n):
            rec = _interior_wall_between(recs, i, j)
            if rec is None or rec.edge.length < ACCESS_DOOR_M - geom.EPS:
                continue  # E1: not a real shared wall a door fits in
            if j in graph[i]:
                continue  # already a tree door here
            h = di.get(j, -1)
            # F: does this candidate FIX a violated requirement?
            fixes = []
            for fp, ia, ib, d0 in violated:
                d1 = hops(ia, extra=(i, j)).get(ib, -1)
                if 0 <= d1 <= fp.max_hops and (d0 < 0 or d1 < d0):
                    fixes.append({"pair": f"{fp.a}<->{fp.b}", "max_hops": fp.max_hops,
                                  "before": d0, "after": d1})
            row = {"a": rooms[i].name, "b": rooms[j].name, "wall_id": rec.wall.id,
                   "wall_len": round(rec.edge.length, 2), "hops": h,
                   "fixes": fixes, "accepted": False, "reason": ""}
            if not fixes:
                row["reason"] = (
                    "F fixes no violated functional requirement"
                    + (f" ({len(violated)} outstanding)" if violated else " (none outstanding)")
                )
            else:
                refused = _secondary_refused(rooms, i, j)
                row["reason"] = f"E4 {refused}" if refused else ""
            report.append(row)
            if not row["reason"]:
                gain = sum(f["before"] - f["after"] for f in fixes if f["before"] > 0)
                ranked.append((
                    -len(fixes), -gain,   # most requirements fixed, biggest hop saving
                    -rec.edge.length,     # then the widest host wall
                    rooms[i].name, rooms[j].name,
                    i, j, rec, row,
                ))

    ranked.sort(key=lambda t: t[:5])
    accepted: list[Door] = []
    for _nf, _g, _l, _na, _nb, i, j, rec, row in ranked:
        if len(accepted) >= MAX_SECONDARY_DOORS:
            row["reason"] = f"capped at {MAX_SECONDARY_DOORS} secondary doors"
            continue
        width = min(DOOR_W, max(0.7, rec.edge.length - 0.2))
        # R7 for a door with no tree parent: it exists to shorten the walk
        # between these two rooms, so it goes where that walk is shortest —
        # nearest existing opening of one room plus nearest of the other.
        best, best_cost = "lo", None
        for anchor in ("lo", "hi"):
            c = _door_center(rec, width, anchor)
            cost = 0.0
            for k in (i, j):
                near = [math.dist(c, d.center) for d in list(doors) + accepted + [entry]
                        if rooms[k].name in (d.from_, d.to)]
                cost += min(near) if near else 0.0
            if best_cost is None or cost < best_cost - geom.EPS:
                best, best_cost = anchor, cost
        cand = _door_on(rec, rooms, rooms[i].name, rooms[j].name, anchor=best, secondary=True)

        # E5: both rooms keep a usable furniture run WITH the new door in place
        spans = _opening_spans(list(doors) + accepted + [cand], windows, recs)
        runs = {k: _longest_free_wall_run(k, recs, spans) for k in (i, j)}
        short = [k for k in (i, j) if runs[k] < SECONDARY_MIN_FREE_WALL_M - geom.EPS]
        if short:
            row["reason"] = ("E5 " + ", ".join(
                f"{rooms[k].name} would keep only {runs[k]:.2f} m of free wall" for k in short))
            continue
        row["free_runs"] = {rooms[k].name: round(runs[k], 2) for k in (i, j)}

        # E6: reuse the real all-pairs swing detector on the complete door set.
        # Any concession counts, not just an unresolved overlap: a door that
        # only fits by flipping someone else's leaf into the corridor has not
        # "collided with nothing", it has spent a norm to buy a shortcut.
        clash = _swing_compromises(rooms, recs, list(doors) + accepted + [cand], entry, terrace)
        if clash:
            row["reason"] = f"E6 swing: {clash[0]}"
            continue
        row["accepted"] = True
        row["anchor"] = best
        row["center"] = [round(v, 3) for v in cand.center]
        accepted.append(cand)
    return accepted, report


# ---------------------------------------------------------------------------
# door swing: hinge side, facing, and arc-collision resolution
# ---------------------------------------------------------------------------
#
# The architect's standing complaints — "qapilar divara acilmir" (doors don't
# open against the wall) and three circled doors in a row whose swings collide —
# were never a Revit bug. layout.json simply carried no hinge or facing data, so
# RevitBuilder called NewFamilyInstance and let Revit derive hand and facing from
# the wall alone: uniform, and arbitrary with respect to the room. Hinge side,
# facing and arc collision are pure geometry; nothing here needs furniture.
#
# Swept region model: the leaf is hinged at one jamb and sweeps a QUARTER DISC of
# radius = leaf width, from the closed position (lying in the doorway, along the
# wall, pointing at the far jamb) to the open position (perpendicular to the
# wall, inside the room it opens into). Note this quarter lies on the FAR-JAMB
# side of the hinge, so hinging at the end nearer a corner puts the open leaf
# parallel to and ~one jamb-offset from that corner's return wall — which is
# exactly "opens flat against the wall".

SWING_ARC_SEGMENTS = 16  # ~1 mm sagitta error at a 0.9 m leaf; well under GRID_M

# Neufert, Architects' Data — the norms this module encodes:
#   "doors which open into corridor must not cause obstruction within corridor"
#       -> R1: a door between circulation and anything else opens into the
#          NON-circulation side, always. Not negotiable, not a preference.
#   "when located in corner of rm door should be hinged at side nearer corner";
#   "doors should be hung with hinges toward corner"
#       -> R5: hinge at the nearer wall end, so the open leaf folds flat against
#          the perpendicular return wall.
#   "Doorswings should not conflict with each other"
#       -> R6: zero arc-arc overlap, hard.
#   "In small rm, such as wc cubicles, side-hung doors should open outwards or
#    sliding doors should be used" — tempered by Neufert's own warning about
#    outward swings into corridors being hazardous
#       -> R4: outward only where the receiving space is not circulation, or is
#          wide enough (>= CORRIDOR_CLEAR_MIN_M) to absorb the leaf.
#   Corridors >= 1200 mm so a user can stand clear to open a door
#       -> CORRIDOR_CLEAR_MIN_M, the R4 gate and the R2 tie-break metric.

CORRIDOR_CLEAR_MIN_M = 1.2   # Neufert corridor minimum; also the R4 outward gate
SMALL_ROOM_MARGIN_M = 0.5    # R4: leaf width + this much clear depth, or swing out


def _wall_unit(wall: Wall) -> tuple[float, float]:
    sx, sy = wall.start
    ex, ey = wall.end
    length = math.hypot(ex - sx, ey - sy)
    return ((ex - sx) / length, (ey - sy) / length)


def _hinge_frame(door: Door, wall: Wall, hinge: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """(hinge point, unit vector along the wall from the hinge toward the far jamb)."""
    ux, uy = _wall_unit(wall)
    half = door.width_m / 2.0
    if hinge == "start":
        return (door.center[0] - ux * half, door.center[1] - uy * half), (ux, uy)
    return (door.center[0] + ux * half, door.center[1] + uy * half), (-ux, -uy)


def _into_normal(door: Door, wall: Wall, rect: geom.Rect) -> tuple[float, float] | None:
    """Unit normal off the wall pointing into `rect`."""
    ux, uy = _wall_unit(wall)
    for nx, ny in ((-uy, ux), (uy, -ux)):
        px = door.center[0] + nx * 1e-3
        py = door.center[1] + ny * 1e-3
        if rect[0] - geom.EPS <= px <= rect[2] + geom.EPS and rect[1] - geom.EPS <= py <= rect[3] + geom.EPS:
            return (nx, ny)
    return None


def _nearer_end(door: Door, wall: Wall) -> str:
    """Which wall endpoint the door sits closer to. wall.start is always edge.lo
    and wall.end always edge.hi (see _build_walls), so this is unambiguous."""
    ds = math.dist(door.center, wall.start)
    de = math.dist(door.center, wall.end)
    return "start" if ds <= de else "end"


def _clear_depth(door: Door, wall: Wall, rect: geom.Rect) -> float:
    """Clear dimension of `rect` measured PERPENDICULAR to the door's host wall.

    This is the depth the leaf has to sweep into — the number both R2 ("which
    side can absorb the leaf") and R4 ("is this room too small to swing inward")
    actually care about. A room can be long and still unable to take a door if
    it is narrow across the doorway, so the wall-parallel dimension is the wrong
    measure and is deliberately not used.
    """
    ux, uy = _wall_unit(wall)
    # perpendicular axis: if the wall runs along x, the depth is in y, and vice
    # versa. Walls here are always axis-aligned (the rasterizer emits no others).
    if abs(ux) > abs(uy):
        return rect[3] - rect[1]
    return rect[2] - rect[0]


def swing_wedge(
    hinge_pt: tuple[float, float],
    d_along: tuple[float, float],
    d_into: tuple[float, float],
    radius: float,
    segments: int = SWING_ARC_SEGMENTS,
) -> list[tuple[float, float]]:
    """Convex polygon approximating the swept quarter disc (hinge + arc points)."""
    a0 = math.atan2(d_along[1], d_along[0])
    a1 = math.atan2(d_into[1], d_into[0])
    sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi  # shortest, always +/-90 deg
    pts = [hinge_pt]
    for k in range(segments + 1):
        a = a0 + sweep * k / segments
        pts.append((hinge_pt[0] + radius * math.cos(a), hinge_pt[1] + radius * math.sin(a)))
    return pts


def _wedge_fits(hinge_pt, d_along, d_into, radius: float, rect: geom.Rect) -> bool:
    """Does the whole quarter disc lie inside `rect`?

    The disc's extent from the hinge is exactly `radius` along each of the two
    axis-aligned directions and nothing beyond, so checking those two extreme
    points against an axis-aligned rect is necessary AND sufficient. A wedge that
    fits cannot cross a wall, because every wall lies on a room boundary.
    """
    for dx, dy in (d_along, d_into):
        px, py = hinge_pt[0] + dx * radius, hinge_pt[1] + dy * radius
        if not (rect[0] - geom.EPS <= px <= rect[2] + geom.EPS and rect[1] - geom.EPS <= py <= rect[3] + geom.EPS):
            return False
    return True


def _convex_overlap(p: list, q: list, tol: float = 1e-9) -> bool:
    """Separating-axis test. Touching counts as clear, only positive area is a hit."""
    for poly in (p, q):
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            ax, ay = -(y1 - y0), (x1 - x0)
            norm = math.hypot(ax, ay)
            if norm < 1e-12:
                continue
            ax, ay = ax / norm, ay / norm
            pmin = min(ax * x + ay * y for x, y in p)
            pmax = max(ax * x + ay * y for x, y in p)
            qmin = min(ax * x + ay * y for x, y in q)
            qmax = max(ax * x + ay * y for x, y in q)
            if pmax < qmin + tol or qmax < pmin + tol:
                return False
    return True


def _choose_facing(
    door: Door,
    wall: Wall,
    cat_by_name: dict[str, str],
    rect_by_name: dict[str, geom.Rect],
    warnings: list[str],
) -> tuple[str, bool, str]:
    """Which side the leaf opens into — R1..R4. Returns (target, locked, why).

    `locked` means the facing is a NORM, not a preference: collision resolution
    (R6) may flip the hinge but must never flip a locked facing.

    This replaces the pre-1.3.0 rule "open into the access tree's child". That
    rule was wrong for a reason worth recording: the access tree is rooted at
    the Foyer, so it emits edges like Foyer->Corridor whose CHILD is itself
    circulation — and the door then opened into the corridor, exactly what
    Neufert forbids. Corridor->Bedroom came out right only by accident of
    direction. Facing is now derived from what the two sides ARE, so it no
    longer depends on which way the tree happened to be walked.
    """
    sides = [s for s in (door.to, door.from_) if s in rect_by_name]
    if not sides:
        return door.to, True, "no adjoining rect (degenerate)"
    if len(sides) == 1:
        # the main entry: `from` is OUTSIDE and has no rect. An entry leaf
        # cannot sweep onto the street, so this is forced, not chosen.
        return sides[0], True, "only one side is an enclosed space (entry door)"

    circ_sides = [s for s in sides if cat_by_name.get(s) == "circ"]

    if len(circ_sides) == 1:
        # R1 (hard): never open into circulation.
        target = next(s for s in sides if s not in circ_sides)
        locked = True
        why = f"R1 {circ_sides[0]} is circulation, so it opens into {target}"
    elif len(circ_sides) == 2:
        # R2: both sides circulation — the leaf goes where there is room for it.
        depths = {s: _clear_depth(door, wall, rect_by_name[s]) for s in sides}
        target = max(sides, key=lambda s: (depths[s], s))
        other = next(s for s in sides if s != target)
        locked = False
        why = (f"R2 both sides circulation; {target} has {depths[target]:.2f} m clear "
               f"vs {other} {depths[other]:.2f} m")
    else:
        # R3: neither side is circulation — keep entering the tree's child.
        target = door.to if door.to in rect_by_name else sides[0]
        locked = False
        why = f"R3 neither side is circulation; opens into the room entered ({target})"

    # R4: a small wet room cannot take its own leaf (Neufert's wc-cubicle case).
    # Outward is allowed only where the receiving space is not circulation, or
    # is a corridor wide enough to absorb the leaf.
    if cat_by_name.get(target) == "wet":
        depth = _clear_depth(door, wall, rect_by_name[target])
        if depth < door.width_m + SMALL_ROOM_MARGIN_M:
            other = next((s for s in sides if s != target), None)
            if other is None:
                warnings.append(
                    f"door {door.from_}->{door.to} on {door.wall_id}: {target} is only "
                    f"{depth:.2f} m clear (needs {door.width_m + SMALL_ROOM_MARGIN_M:.2f} m) "
                    f"and has no other side to open into")
            else:
                other_ok = (cat_by_name.get(other) != "circ"
                            or _clear_depth(door, wall, rect_by_name[other]) >= CORRIDOR_CLEAR_MIN_M)
                if other_ok:
                    why = (f"R4 {target} is only {depth:.2f} m clear, too small for an "
                           f"inward leaf; swings out into {other}")
                    target, locked = other, True
                else:
                    warnings.append(
                        f"door {door.from_}->{door.to} on {door.wall_id}: {target} is only "
                        f"{depth:.2f} m clear so the leaf should swing out, but {other} is "
                        f"circulation under {CORRIDOR_CLEAR_MIN_M} m — neither direction "
                        f"satisfies Neufert; left opening into {target}, needs a sliding leaf")
    return target, locked, why


def _assign_swings(
    rooms: list[FinalRoom],
    recs: list[_WallRec],
    doors: list[Door],
    entry: Door,
    terrace: Terrace | None,
    warnings: list[str],
) -> None:
    """Set `hinge` and `swing_into` on every door, resolving swing collisions.

    Facing comes from `_choose_facing` (R1..R4) and is a norm; the hinge end
    (R5, nearer end so the leaf folds against the return wall) is a preference.
    So the option list per door is, in precedence order:
        (nearer end, rule facing)   <- R5 satisfied
        (farther end, rule facing)  <- R6 conceded the hinge
        (nearer end, other side)    <- only when the facing is NOT locked
        (farther end, other side)
    R6 therefore tries every hinge combination before any facing flip, and a
    facing locked by R1/R4 is simply never offered an alternative — the norm is
    enforced by construction rather than by the search happening to prefer it.

    An option is admissible only if the whole wedge fits inside the target room,
    which is simultaneously the no-crossing-a-wall check.
    """
    wall_by_id = {r.wall.id: r.wall for r in recs}
    rect_by_name: dict[str, geom.Rect] = {rm.name: rm.rect for rm in rooms}
    cat_by_name: dict[str, str] = {rm.name: rm.category for rm in rooms}
    if terrace is not None:
        rect_by_name["Terrace"] = tuple(terrace.rect_m)
        cat_by_name["Terrace"] = "outdoor"
    circ_names = {rm.name for rm in rooms if rm.category == "circ"}

    all_doors = list(doors) + [entry]
    options: list[list[tuple]] = []  # per door: [(hinge, target, wedge), ...]
    facings: list[tuple[str, bool, str]] = []

    for d in all_doors:
        wall = wall_by_id.get(d.wall_id)
        opts: list[tuple] = []
        if wall is None:
            facings.append((d.to, True, "host wall missing"))
        else:
            target, locked, why = _choose_facing(d, wall, cat_by_name, rect_by_name, warnings)
            facings.append((target, locked, why))
            near = _nearer_end(d, wall)
            far = "end" if near == "start" else "start"
            order = [target] if locked else [target, *(s for s in (d.to, d.from_) if s != target)]
            for cand in order:
                rect = rect_by_name.get(cand)
                if rect is None:
                    continue  # "OUTSIDE" has no rect; never swing there
                for hinge in (near, far):
                    hp, along = _hinge_frame(d, wall, hinge)
                    into = _into_normal(d, wall, rect)
                    if into is None:
                        continue
                    if not _wedge_fits(hp, along, into, d.width_m, rect):
                        continue
                    opts.append((hinge, cand, swing_wedge(hp, along, into, d.width_m)))
        if not opts:
            # nothing admissible: keep the rule-preferred answer and say so
            near = _nearer_end(d, wall) if wall is not None else "start"
            target = facings[-1][0]
            warnings.append(
                f"door {d.from_}->{d.to} on {d.wall_id}: no swing fits {target} "
                f"without crossing a wall; defaulted to hinge {near}, opening into {target}"
            )
            opts = [(near, target, [])]
        options.append(opts)

    choice = [0] * len(all_doors)

    def collisions(sel: list[int]) -> list[tuple[int, int]]:
        # ALL pairs. Two wedges in different rooms are in practice separated by
        # the wall between them, but testing only same-room pairs would make
        # "zero arc-arc overlaps" a claim about the shortcut rather than about
        # the geometry. At 17 doors the full O(n^2) is free, so assert the
        # stronger property directly.
        hits = []
        for i in range(len(all_doors)):
            hi_, ti, wi = options[i][sel[i]]
            if not wi:
                continue
            for j in range(i + 1, len(all_doors)):
                hj, tj, wj = options[j][sel[j]]
                if not wj:
                    continue
                if _convex_overlap(wi, wj):
                    hits.append((i, j))
        return hits

    found = collisions(choice)
    initial = list(found)
    for _ in range(4 * len(all_doors) + 8):
        current = collisions(choice)
        if not current:
            break
        moved = False
        for i, j in current:
            for idx in (i, j):
                for alt in range(choice[idx] + 1, len(options[idx])):
                    trial = list(choice)
                    trial[idx] = alt
                    if len(collisions(trial)) < len(current):
                        choice = trial
                        moved = True
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break

    for k, d in enumerate(all_doors):
        hinge, target, _ = options[k][choice[k]]
        d.hinge = hinge
        d.swing_into = target
        wall = wall_by_id.get(d.wall_id)
        if wall is not None and hinge != _nearer_end(d, wall):
            warnings.append(
                f"door {d.from_}->{d.to} on {d.wall_id}: hinge moved to the FAR end of "
                f"the wall (not the nearer one) to clear a swing collision"
            )
        ruled, locked, _why = facings[k]
        if target != ruled:
            warnings.append(
                f"door {d.from_}->{d.to} on {d.wall_id}: facing overridden to {target} "
                f"(the rule chose {ruled}) to clear a swing collision"
            )
        # R1 is enforced by construction above (a locked facing is never offered
        # an alternative), so this can now only fire for an unlocked R2/R3 door.
        # It stays as a backstop: if it ever prints, the invariant broke.
        # A LOCKED facing is exempt: the entry door's only enclosed side is the
        # Foyer, and an entry leaf sweeping onto the street is not the fix.
        if (not locked and target in circ_names
                and not (d.from_ in circ_names and d.to in circ_names)):
            warnings.append(
                f"door {d.from_}->{d.to} swings into circulation ({target}) - "
                f"review: an outward leaf here obstructs the corridor"
            )

    for i, j in collisions(choice):
        a, b = all_doors[i], all_doors[j]
        warnings.append(
            f"UNRESOLVED swing collision: {a.from_}->{a.to} ({a.wall_id}) and "
            f"{b.from_}->{b.to} ({b.wall_id}) both swing into {a.swing_into}"
        )
    if initial and not collisions(choice):
        warnings.append(
            f"resolved {len(initial)} swing collision(s) by hinge/facing choice"
        )


# ---------------------------------------------------------------------------
# windows + terrace
# ---------------------------------------------------------------------------


def _build_windows(rooms: list[FinalRoom], recs: list[_WallRec]) -> list[Window]:
    # The rasterizer's wall.exterior flag means "touches an unowned cell",
    # which includes an INTERIOR VOID cell, not just the outdoors -- a wall
    # can be flagged exterior while facing a sealed pocket with no daylight.
    # Windows may only go on the TRUE building perimeter: the footprint bbox
    # (the same bounding box validator.py measures coverage against).
    fx0 = min(rm.rect[0] for rm in rooms)
    fy0 = min(rm.rect[1] for rm in rooms)
    fx1 = max(rm.rect[2] for rm in rooms)
    fy1 = max(rm.rect[3] for rm in rooms)

    def _on_true_perimeter(edge: geom.Edge) -> bool:
        if edge.orient == "V":
            return abs(edge.fixed - fx0) < geom.EPS or abs(edge.fixed - fx1) < geom.EPS
        return abs(edge.fixed - fy0) < geom.EPS or abs(edge.fixed - fy1) < geom.EPS

    windows: list[Window] = []
    for i, rm in enumerate(rooms):
        if rm.category not in WINDOW_CATEGORIES and rm.name not in WINDOW_ROOMS:
            continue
        for r in recs:
            if not r.wall.exterior or i not in (r.a, r.b):
                continue
            if not _on_true_perimeter(r.edge):
                continue
            if r.edge.length < 1.2:
                continue
            width = min(WIN_W, r.edge.length - 0.4)
            if r.edge.orient == "V":
                center = [r.edge.fixed, r.edge.mid]
            else:
                center = [r.edge.mid, r.edge.fixed]
            windows.append(
                Window(room=rm.name, wall_id=r.wall.id, center=center, width_m=width, height_m=WIN_H, sill_m=WIN_SILL)
            )
    return windows


def _opens_onto_terrace(room: FinalRoom) -> bool:
    """Is this room one the terrace should serve (and get a door to)?

    A room qualifies when it needs daylight (standards.requires_exterior_wall)
    and is not a service room. That predicate, not a name list, is what
    separates the rooms the norms want opening onto the outdoor space from the
    ones they do not:
      - IN:  Living, Office, Kitchen, bedrooms — all daylight-required habitable
             rooms. SNiP 2.08.01-89's Posobie names the kitchen and the common
             room together as the preferred connection, and Neufert's worked
             figure shows dining AND living both opening onto the terrace.
      - OUT: Master Bathroom, Walk-in Closet, Bathroom, Laundry (no daylight
             requirement); Foyer/Mudroom/Corridor (circulation); Garage (needs
             an exterior wall but is service, so the category test excludes it).
    Because it is derived rather than hardcoded, a repack that puts a bedroom on
    the south facade extends the terrace to it automatically.
    """
    std = standards.ROOMS.get(room.name)
    return bool(std and std.requires_exterior_wall) and room.category != "service"


def _build_terrace(
    rooms: list[FinalRoom], recs: list[_WallRec]
) -> tuple[Terrace | None, list[Door]]:
    """Terrace along the south facade, spanning every qualifying room it touches.

    It used to span the Living room alone and serve exactly one door. Both cited
    sources call for an outdoor space serving several social rooms — SNiP
    2.08.01-89 Posobie ("connect the adjacent outdoor area from the common room
    and kitchen"), Neufert (outdoor dining in front of the dining or living
    room, with a figure showing both). So: take the maximal CONTIGUOUS run of
    qualifying rooms along the south facade that includes Living, and span that.

    Contiguity matters — the terrace is one rectangle, so it can only be widened
    across rooms that actually abut each other. A qualifying room separated from
    Living by a bathroom is not reachable without either swallowing the bathroom
    frontage or emitting a second disjoint rectangle, and the schema carries one
    terrace rect.

    Depth stays TERRACE_DEPTH_M = 3.0 m: clears the SNiP Posobie's >= 1.8 m
    veranda depth and Neufert's 3000 mm minimum width for an outdoor dining
    space with a bench along one wall.
    """
    living = next((rm for rm in rooms if rm.name == "Living"), None)
    if living is None:
        return None, []

    # The true south facade of the building, not merely Living's own y0.
    fy0 = min(rm.rect[1] for rm in rooms)
    on_south = [
        rm
        for rm in rooms
        if abs(rm.rect[1] - fy0) < geom.EPS and _opens_onto_terrace(rm)
    ]
    if living not in on_south:
        # Living does not front the facade (it sits behind another room); keep
        # the terrace on Living itself rather than inventing one elsewhere.
        on_south = [living]
    on_south.sort(key=lambda rm: rm.rect[0])

    # maximal contiguous run containing Living
    li = on_south.index(living)
    lo = li
    while lo > 0 and abs(on_south[lo - 1].rect[2] - on_south[lo].rect[0]) < geom.EPS:
        lo -= 1
    hi = li
    while hi + 1 < len(on_south) and abs(on_south[hi].rect[2] - on_south[hi + 1].rect[0]) < geom.EPS:
        hi += 1
    span = on_south[lo : hi + 1]

    x0 = span[0].rect[0]
    x1 = span[-1].rect[2]
    y0 = span[0].rect[1]
    terrace = Terrace(rect_m=[x0, y0 - TERRACE_DEPTH_M, x1, y0])

    # one door per room the terrace now spans, on that room's south exterior wall
    doors: list[Door] = []
    for rm in span:
        idx = rooms.index(rm)
        south = [
            r
            for r in recs
            if r.wall.exterior
            and idx in (r.a, r.b)
            and r.edge.orient == "H"
            and abs(r.edge.fixed - y0) < geom.EPS
        ]
        if not south:
            continue
        south.sort(key=lambda r: r.edge.length, reverse=True)
        doors.append(_door_on(south[0], rooms, rm.name, "Terrace"))
    return terrace, doors


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------


def build_layout(result: SolveResult, program: Program, wall_height_m: float = 2.7) -> Layout:
    rooms = slice_zones(result)
    recs = _build_walls(rooms, result.plot_w_m, result.plot_d_m, wall_height_m)
    doors, entry, warnings = _build_doors(rooms, recs)
    windows = _build_windows(rooms, recs)
    terrace, terrace_doors = _build_terrace(rooms, recs)
    doors.extend(terrace_doors)
    # R7 before anything reads a door CENTRE: _secondary_doors measures travel
    # between existing openings, and _assign_swings derives every hinge point
    # and wedge from the centre, so both would otherwise be computed against
    # positions that are about to move.
    _position_doors(rooms, recs, doors, entry, terrace)
    secondary, _report = _secondary_doors(rooms, recs, doors, entry, terrace, windows)
    doors.extend(secondary)
    # hinge/facing last: it needs the terrace rect and the complete door set,
    # since a collision is only detectable across all doors sharing a room.
    _assign_swings(rooms, recs, doors, entry, terrace, warnings)

    return Layout(
        preset=result.preset,
        seed=result.seed,
        objective=round(result.objective, 3),
        levels=program.floors,
        wall_height_m=wall_height_m,
        plot=program.plot,
        orientation=program.orientation,
        rooms=[Room(name=r.name, category=r.category, rect_m=list(r.rect), zone=r.zone) for r in rooms],
        walls=[r.wall for r in recs],
        doors=doors,
        windows=windows,
        entry=entry,
        terrace=terrace,
        warnings=warnings,
    )
