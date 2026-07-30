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
    # Service strip (Bathroom | Closet) deep enough for both; Bedroom takes ALL
    # surplus depth. Bathroom gets its min width; Closet takes the rest.
    service = _ceil_snap(max(mbath.min_h_m, wic.min_h_m))  # min-carrying -> ceil
    bath_w = _ceil_snap(mbath.min_w_m)                     # min-carrying -> ceil
    if (
        (h - service) < mbed.min_h_m
        or w < mbed.min_w_m
        or (w - bath_w) < wic.min_w_m
    ):
        return [FinalRoom("Master Bedroom", "private", r.zone, (x0, y0, x1, y1))]
    mid = x0 + bath_w
    if corridor_side == "N":
        sy = y0 + service  # service strip SOUTH, Bedroom the NORTH band
        return [
            FinalRoom("Master Bathroom", "wet", r.zone, (x0, y0, mid, sy)),
            FinalRoom("Walk-in Closet", "private", r.zone, (mid, y0, x1, sy)),
            FinalRoom("Master Bedroom", "private", r.zone, (x0, sy, x1, y1)),
        ]
    sy = y1 - service  # position: aligned edge - aligned dim, no snap needed
    return [
        FinalRoom("Master Bedroom", "private", r.zone, (x0, y0, x1, sy)),
        FinalRoom("Master Bathroom", "wet", r.zone, (x0, sy, mid, y1)),
        FinalRoom("Walk-in Closet", "private", r.zone, (mid, sy, x1, y1)),
    ]


def _slice_children(r: ZoneRect) -> list[FinalRoom]:
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    bathroom = standards.ROOMS["Bathroom"]
    bed = standards.ROOMS["Bedroom"]
    # three horizontal bands so both beds run along the (vertical) exterior wall.
    # Middle Bathroom gets its min DEPTH (ceil-snapped); the two beds split the
    # remaining depth. The divider between the two equal-minimum beds is a free
    # midpoint -> _snap; we assert each resulting bed clears the Bedroom minimum.
    bath_h = _ceil_snap(bathroom.min_h_m)  # min-carrying -> ceil (2.2 -> 2.5)
    rest = h - bath_h
    top = _snap(rest / 2)  # free midpoint between the two beds
    bot = rest - top
    if w < max(bed.min_w_m, bathroom.min_w_m) or top < bed.min_h_m or bot < bed.min_h_m:
        return [FinalRoom("Children Bedroom", "private", r.zone, (x0, y0, x1, y1))]
    a = y0 + top
    b = a + bath_h
    return [
        FinalRoom("Bedroom 2", "private", r.zone, (x0, y0, x1, a)),
        FinalRoom("Bathroom", "wet", r.zone, (x0, a, x1, b)),
        FinalRoom("Bedroom 3", "private", r.zone, (x0, b, x1, y1)),
    ]


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
    # Laundry gets its min strip (ceil-snapped) on the cut axis; Kitchen keeps
    # the `place_side` edge and takes ALL the surplus. No magic fraction.
    if axis_ns:
        depth = _ceil_snap(laundry.min_h_m)  # Laundry Y-depth
        if (h - depth) < kitchen.min_h_m or w < max(kitchen.min_w_m, laundry.min_w_m):
            return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
        if place_side == "S":  # kitchen south, laundry north
            ky = y1 - depth
            return [
                FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, ky)),
                FinalRoom("Laundry", "service", r.zone, (x0, ky, x1, y1)),
            ]
        ly = y0 + depth
        return [
            FinalRoom("Laundry", "service", r.zone, (x0, y0, x1, ly)),
            FinalRoom("Kitchen", "wet", r.zone, (x0, ly, x1, y1)),
        ]
    depth = _ceil_snap(laundry.min_w_m)  # Laundry X-depth
    if (w - depth) < kitchen.min_w_m or h < max(kitchen.min_h_m, laundry.min_h_m):
        return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
    if place_side == "W":  # kitchen west, laundry east
        kx = x1 - depth
        return [
            FinalRoom("Kitchen", "wet", r.zone, (x0, y0, kx, y1)),
            FinalRoom("Laundry", "service", r.zone, (kx, y0, x1, y1)),
        ]
    lx = x0 + depth
    return [
        FinalRoom("Laundry", "service", r.zone, (x0, y0, lx, y1)),
        FinalRoom("Kitchen", "wet", r.zone, (lx, y0, x1, y1)),
    ]


def _split_off_wc(
    zone: str, rect: geom.Rect, along_x: bool
) -> list[FinalRoom] | None:
    """Carve the Guest WC off the SOUTH (or WEST) end of the Foyer remainder.

    Returns [Guest WC, Foyer] in that order, or None if the remainder cannot
    give the WC its minimum without dropping the Foyer below its own.

    WHICH END, and why it is not arbitrary: the Foyer has two jobs the WC must
    not take from it — it carries the front door (so it needs the STREET-facing
    exterior wall, +y in the solver's fixed frame) and it fronts the corridor.
    Both presets pin the entry zone to the north edge, so the WC takes the y0
    (south) end and the Foyer keeps the north wall and its corridor contact.
    South is also where kitchen_laundry sits, which is what puts the WC against
    the wet cluster the architect and the Posobie both ask for — see
    _slice_entry. On an N/S-cut zone the same reasoning runs along x and the WC
    takes the x0 end.
    """
    x0, y0, x1, y1 = rect
    wc = standards.ROOMS["Guest WC"]
    foy = standards.ROOMS["Foyer"]
    if along_x:
        depth = _ceil_snap(wc.min_w_m)  # WC X-depth
        if (x1 - x0) - depth < foy.min_w_m or (y1 - y0) < max(foy.min_h_m, wc.min_h_m):
            return None
        wx = x0 + depth
        return [
            FinalRoom("Guest WC", "wet", zone, (x0, y0, wx, y1)),
            FinalRoom("Foyer", "circ", zone, (wx, y0, x1, y1)),
        ]
    depth = _ceil_snap(wc.min_h_m)  # WC Y-depth
    if (y1 - y0) - depth < foy.min_h_m or (x1 - x0) < max(foy.min_w_m, wc.min_w_m):
        return None
    wy = y0 + depth
    return [
        FinalRoom("Guest WC", "wet", zone, (x0, y0, x1, wy)),
        FinalRoom("Foyer", "circ", zone, (x0, wy, x1, y1)),
    ]


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
        split = _split_off_wc(r.zone, rest, along_x=False)
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
    # remainder along x (west end).
    split = _split_off_wc(r.zone, rest, along_x=True)
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
    # to match). entry's is read straight from geometry (its table is legal on
    # both axes). Fall back to geometry only if the solver didn't record one.
    kl_side = cut_sides.get("kitchen_laundry")
    if kl_side is None and "kitchen_laundry" in by_zone and dining is not None:
        kl_side = _side_of(tuple(by_zone["kitchen_laundry"].rect_m), tuple(dining.rect_m))
    entry_side = None
    if "entry" in by_zone and garage is not None:
        entry_side = _side_of(tuple(by_zone["entry"].rect_m), tuple(garage.rect_m))
    corridor_sides = getattr(result, "corridor_sides", {}) or {}
    master_corridor_side = corridor_sides.get("master_suite")
    kl_corridor_side = corridor_sides.get("kitchen_laundry")
    rooms: list[FinalRoom] = []
    for zr in result.rects:
        z = zr.zone
        if z == "master_suite":
            rooms += _slice_master(zr, master_corridor_side)
        elif z == "children":
            rooms += _slice_children(zr)
        elif z == "kitchen_laundry":
            rooms += _slice_kitchen(zr, kl_side, kl_corridor_side)
        elif z == "entry":
            rooms += _slice_entry(zr, entry_side)
        elif z in _SIMPLE_NAME:
            name, cat = _SIMPLE_NAME[z]
            rooms.append(FinalRoom(name, cat, z, tuple(zr.rect_m)))
        else:
            name = Z.ZONE_DISPLAY.get(z, z.title())
            rooms.append(FinalRoom(name, "living", z, tuple(zr.rect_m)))
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
# kitchen_laundry alone gets a UNION table + a solver cut-axis var: its
# intersection (both-axis-legal) would cost 22.5 vs the 12.5/13.5 single axes.
# entry uses the intersection (0.85*target nearly binds there anyway), so its cut
# stays axis-agnostic and needs no solver var; master/children are single-axis.
_AXIAL = {"kitchen_laundry"}

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
        return _slice_children(zr)
    if zone_id == "kitchen_laundry":
        return _slice_kitchen(zr, side)
    if zone_id == "entry":
        return _slice_entry(zr, side)
    return []


def _legal_1(zone_id: str, w: float, h: float, side: str | None) -> bool:
    rooms = _slice_probe(zone_id, w, h, side)
    return len(rooms) >= 2 and all(_room_legal(rm.name, rm.rect) for rm in rooms)


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
    if zone_id in _AXIAL:
        pairs: list = []
        for wu in _STEPS:
            for hu in _STEPS:
                w, h = wu * GRID_M, hu * GRID_M
                if _legal_1(zone_id, w, h, _NS_REP):
                    pairs.append((wu, hu, 1))
                if _legal_1(zone_id, w, h, _WE_REP):
                    pairs.append((wu, hu, 0))
    elif zone_id == "entry":  # intersection: legal on BOTH axes
        pairs = [
            (wu, hu)
            for wu in _STEPS
            for hu in _STEPS
            if _legal_1(zone_id, wu * GRID_M, hu * GRID_M, _NS_REP)
            and _legal_1(zone_id, wu * GRID_M, hu * GRID_M, _WE_REP)
        ]
    else:  # master_suite / children: single orientation
        pairs = [
            (wu, hu)
            for wu in _STEPS
            for hu in _STEPS
            if _legal_1(zone_id, wu * GRID_M, hu * GRID_M, None)
        ]
    _PAIRS_CACHE[zone_id] = pairs
    return pairs


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
