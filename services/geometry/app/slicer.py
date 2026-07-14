"""Slice macro-zones into finished rooms and emit explicit walls/doors/windows.

Composite cuts (so internal adjacencies hold by construction):
  master_suite -> Master Bedroom (exterior) + Master Bathroom + Walk-in Closet
  children     -> Bedroom + Bathroom (middle) + Bedroom, beds along exterior wall
  kitchen_laundry -> Kitchen (kept next to Dining) + Laundry (away from Dining)
  entry        -> Foyer + Mudroom (toward the Garage)
Terrace projects south off the Living room.

Walls are rasterised on the 0.5 m grid: a wall unit-edge exists wherever two
grid cells belong to different rooms (interior) or a room meets the outside
(exterior). Collinear unit-edges merge into wall runs. Doors follow a spanning
tree over the room-adjacency graph rooted at the Foyer, guaranteeing every room
is reachable and every door sits on a real shared wall.
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


def _slice_master(r: ZoneRect) -> list[FinalRoom]:
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    mbath = standards.ROOMS["Master Bathroom"]
    wic = standards.ROOMS["Walk-in Closet"]
    mbed = standards.ROOMS["Master Bedroom"]
    # North service strip (Bathroom | Closet) deep enough for both; Bedroom below
    # takes ALL surplus depth. Bathroom gets its min width; Closet takes the rest.
    service = _ceil_snap(max(mbath.min_h_m, wic.min_h_m))  # min-carrying -> ceil
    bath_w = _ceil_snap(mbath.min_w_m)                     # min-carrying -> ceil
    if (
        (h - service) < mbed.min_h_m
        or w < mbed.min_w_m
        or (w - bath_w) < wic.min_w_m
    ):
        return [FinalRoom("Master Bedroom", "private", r.zone, (x0, y0, x1, y1))]
    sy = y1 - service  # position: aligned edge - aligned dim, no snap needed
    mid = x0 + bath_w
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


def _slice_kitchen(r: ZoneRect, side: str | None) -> list[FinalRoom]:
    # `side` is the direction of Dining, DECIDED BY THE SOLVER (result.cut_sides)
    # and read here — not re-derived from _side_of. The solver constrained the
    # zone's (w, h) to a shape legal for the cut on this axis (legal_pairs), so
    # the slice below is guaranteed legal.
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    kitchen = standards.ROOMS["Kitchen"]
    laundry = standards.ROOMS["Laundry"]
    if side is None:
        return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
    # Laundry gets its min strip (ceil-snapped) on the cut axis; Kitchen keeps
    # the dining side and takes ALL the surplus. No magic fraction.
    if side in ("N", "S"):
        depth = _ceil_snap(laundry.min_h_m)  # Laundry Y-depth
        if (h - depth) < kitchen.min_h_m or w < max(kitchen.min_w_m, laundry.min_w_m):
            return [FinalRoom("Kitchen", "wet", r.zone, (x0, y0, x1, y1))]
        if side == "S":  # dining south -> kitchen south, laundry north
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
    if side == "W":  # dining west -> kitchen west, laundry east
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


def _slice_entry(r: ZoneRect, side: str | None) -> list[FinalRoom]:
    # `side` is the direction of the Garage. entry uses the BOTH-axis-legal
    # intersection table (legal_pairs), so its slice is legal on either axis and
    # the side may be read straight from geometry (_side_of) in slice_zones — no
    # cut-axis solver var is needed here (unlike kitchen_laundry).
    x0, y0, x1, y1 = r.rect_m
    w, h = x1 - x0, y1 - y0
    mud = standards.ROOMS["Mudroom"]
    foy = standards.ROOMS["Foyer"]
    if side is None:
        return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
    # Mudroom (garage-side buffer) gets its min strip (ceil-snapped); Foyer takes
    # the rest.
    if side in ("W", "E"):
        depth = _ceil_snap(mud.min_w_m)  # Mudroom X-depth
        if (w - depth) < foy.min_w_m or h < max(foy.min_h_m, mud.min_h_m):
            return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
        if side == "W":
            mx = x0 + depth
            return [
                FinalRoom("Mudroom", "service", r.zone, (x0, y0, mx, y1)),
                FinalRoom("Foyer", "circ", r.zone, (mx, y0, x1, y1)),
            ]
        mx = x1 - depth
        return [
            FinalRoom("Foyer", "circ", r.zone, (x0, y0, mx, y1)),
            FinalRoom("Mudroom", "service", r.zone, (mx, y0, x1, y1)),
        ]
    depth = _ceil_snap(mud.min_h_m)  # Mudroom Y-depth
    if (h - depth) < foy.min_h_m or w < max(foy.min_w_m, mud.min_w_m):
        return [FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, y1))]
    if side == "S":
        my = y0 + depth
        return [
            FinalRoom("Mudroom", "service", r.zone, (x0, y0, x1, my)),
            FinalRoom("Foyer", "circ", r.zone, (x0, my, x1, y1)),
        ]
    my = y1 - depth
    return [
        FinalRoom("Foyer", "circ", r.zone, (x0, y0, x1, my)),
        FinalRoom("Mudroom", "service", r.zone, (x0, my, x1, y1)),
    ]


_SIMPLE_NAME: dict[str, tuple[str, Category]] = {
    "living": ("Living", "living"),
    "dining": ("Dining", "living"),
    "office": ("Office", "office"),
    "garage": ("Garage", "service"),
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
    rooms: list[FinalRoom] = []
    for zr in result.rects:
        z = zr.zone
        if z == "master_suite":
            rooms += _slice_master(zr)
        elif z == "children":
            rooms += _slice_children(zr)
        elif z == "kitchen_laundry":
            rooms += _slice_kitchen(zr, kl_side)
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
_ZONE_ROOM = {"living": "Living", "dining": "Dining", "office": "Office", "garage": "Garage"}

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


def _door_on(rec: _WallRec, rooms: list[FinalRoom], frm: str, to: str) -> Door:
    e = rec.edge
    width = min(DOOR_W, max(0.7, e.length - 0.2))
    if e.orient == "V":
        center = [e.fixed, e.mid]
    else:
        center = [e.mid, e.fixed]
    return Door(**{"from": frm, "to": to, "wall_id": rec.wall.id, "center": center, "width_m": width, "height_m": DOOR_H})


def _build_doors(
    rooms: list[FinalRoom], recs: list[_WallRec]
) -> tuple[list[Door], Door, list[str]]:
    n = len(rooms)
    warnings: list[str] = []
    # adjacency graph over rooms sharing a wall >= MIN_DOOR_WALL
    adj: dict[int, list[int]] = {i: [] for i in range(n)}
    for r in recs:
        if -1 not in (r.a, r.b) and r.edge.length >= MIN_DOOR_WALL - geom.EPS:
            adj[r.a].append(r.b)
            adj[r.b].append(r.a)

    # root at Foyer, else Living, else 0
    root = next((i for i, rm in enumerate(rooms) if rm.name == "Foyer"), None)
    if root is None:
        root = next((i for i, rm in enumerate(rooms) if rm.name == "Living"), 0)

    doors: list[Door] = []
    seen = {root}
    stack = [root]
    while stack:
        cur = stack.pop()
        for nb in sorted(set(adj[cur])):
            if nb in seen:
                continue
            rec = _interior_wall_between(recs, cur, nb)
            if rec is None:
                continue
            doors.append(_door_on(rec, rooms, rooms[cur].name, rooms[nb].name))
            seen.add(nb)
            stack.append(nb)

    for i, rm in enumerate(rooms):
        if i not in seen:
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
# windows + terrace
# ---------------------------------------------------------------------------


def _build_windows(rooms: list[FinalRoom], recs: list[_WallRec]) -> list[Window]:
    windows: list[Window] = []
    for i, rm in enumerate(rooms):
        if rm.category not in WINDOW_CATEGORIES and rm.name not in WINDOW_ROOMS:
            continue
        for r in recs:
            if not r.wall.exterior or i not in (r.a, r.b):
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


def _build_terrace(rooms: list[FinalRoom], recs: list[_WallRec]) -> tuple[Terrace | None, Door | None]:
    living = next((rm for rm in rooms if rm.name == "Living"), None)
    if living is None:
        return None, None
    x0, y0, x1, y1 = living.rect
    terrace = Terrace(rect_m=[x0, y0 - TERRACE_DEPTH_M, x1, y0])
    # door on Living's south exterior wall (y == y0), if present
    li = rooms.index(living)
    south = [
        r
        for r in recs
        if r.wall.exterior and li in (r.a, r.b) and r.edge.orient == "H" and abs(r.edge.fixed - y0) < geom.EPS
    ]
    door = None
    if south:
        south.sort(key=lambda r: r.edge.length, reverse=True)
        door = _door_on(south[0], rooms, "Living", "Terrace")
    return terrace, door


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------


def build_layout(result: SolveResult, program: Program, wall_height_m: float = 2.7) -> Layout:
    rooms = slice_zones(result)
    recs = _build_walls(rooms, result.plot_w_m, result.plot_d_m, wall_height_m)
    doors, entry, warnings = _build_doors(rooms, recs)
    windows = _build_windows(rooms, recs)
    terrace, terrace_door = _build_terrace(rooms, recs)
    if terrace_door is not None:
        doors.append(terrace_door)

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
