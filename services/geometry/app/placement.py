"""THE PLACEMENT FILTER — the constraints the four bespoke cutters hold BY
CONSTRUCTION, restated as explicit predicates over an enumerated candidate.

`subdivide.subdivisions()` answers "which partitions of this rectangle are
LEGAL", where legal means every room clears its Neufert shape floor and its
architect band. That is a small fraction of what a cutter guarantees. The rest --
which sub-room fronts the corridor, that an ensuite touches its parent, that no
route transits a no_through_traffic room, that a daylight room reaches the
facade -- is currently encoded as the SHAPE OF THE CODE inside
`_slice_master` / `_slice_children` / `_slice_kitchen` / `_slice_entry`, and
disappears the moment those are replaced by a general enumerator. This module is
that missing half.

MACHINERY ONLY, like `subdivide` itself: nothing here is wired into
`slice_zones`, no solver constraint reads it, and the golden cannot move because
of it.

WHAT IS CHECKED, and where each rule comes from. Codes are stable so a caller can
count eliminations per constraint:

  C1  corridor-facing room     _force_master_corridor_overlap (master_suite),
                               _force_corridor_overlaps_kitchen (kitchen_laundry),
                               _force_vertical_cover_center (children),
                               _force_backbone_reaches_foyer (entry)
  C2  ensuite parents          standards.allowed_ensuite_parents
  C3  corridor-side flip       the positional half of C1 (same predicate)
  C4  CB3 all-rooms-on-face    _force_vertical_cover_center + the 96,580-tiling
                               enumeration, RESTATED (see below)
  C5  no_through_traffic       merged into C14 -- they are one property
  C6  rectangles only          structural
  C7  grid alignment           structural
  C8  exact tiling, no void    structural
  C9  completeness             structural (_legal_1's second condition)
  C10 both area sources        structural (_in_band; already applied upstream)
  C11 oriented standards       see ORIENTATION below -- the subtle one
  C12 geometric room order     structural
  C13 daylight                 standards.requires_exterior_wall
  C14 no through traffic       standards.no_through_traffic + validator.access_tree
  C15 constant offsets         NOT a predicate: an OUTPUT requirement. See
                               guarantee_vector()
  C16 build == slice time      guarded elsewhere (slicer._penalty_disagreement)

FOUND BEYOND THAT LIST while implementing, and each is real:

  C17 per-room circulation access. `requires_circulation_access` is a per-ROOM
      flag, not a per-zone one, and C1 only ever constrained ONE room per zone.
      Checked separately and deliberately weakly -- see the predicate.
  C18 the DIRECTOR face. Two cutters place a room against a neighbour that is not
      the corridor: `_slice_kitchen` puts the Kitchen on the Dining face
      (`_force_kitchen_holds_dining_face`) and `_slice_entry` puts the Mudroom on
      the Garage face (which is what makes `Garage.allowed_ensuite_parents =
      (Mudroom, Foyer)` reachable). Same shape of rule, different director, and
      neither is the corridor -- so it is not C1.
  C19 a door needs a wall. Every adjacency C2/C14/C17 rely on must be at least
      ACCESS_DOOR_M, and every wall a door is hosted on at least MIN_DOOR_WALL.
      Implicit in the others today; made a named constant here.

OUT OF SCOPE FOR A ZONE-LEVEL FILTER, named so nobody assumes they are covered:
the forbidden room pairs (master<->kitchen, garage<->living) are CROSS-ZONE and
validator._check_forbidden already owns them; `_force_guest_wc_reaches_wet_core`
is cross-zone (entry <-> kitchen_laundry); `Garage.allowed_ensuite_parents` is
cross-zone (garage <-> entry). A filter over one zone's candidate cannot see any
of them, and pretending otherwise would be worse than leaving them out.

ORIENTATION (C11), handled explicitly because it is the rule most likely to be
silently wrong. `standards.RoomStandard` carries min_w_m and min_h_m as
AXIS-BOUND figures, and `slicer._room_legal` compares them to the rect's w and h
without ever rotating. For most rooms that is right and intended --
`Garage.min_h_m = 5.0` is the DRIVING LENGTH along y and the standard's own
comment says "AXIS-BOUND, not rotation-invariant: a rotated 5.0-wide x 3.0-deep
garage is too shallow to park in". For the Bathroom it is not: min_w_m 2.4 is
along the fixture run and min_h_m 1.7 is the depth in front of it, so the same
room installed the other way round is equally legal -- which is exactly why
`slicer._slice_children_ns` transposes the rect before checking it.

So orientation is a PER-ROOM property, and this module states it as data
(ROTATABLE) rather than leaving it implicit in which cutter happens to run.
ROTATABLE contains only "Bathroom", and only because the code already rotates
that one room; every other room keeps today's axis-bound test exactly. This is
deliberately NOT a change to standards.py: adding an orientation field there
would change `_room_legal`, hence `_in_band`, hence `legal_pairs`, hence the
solver's shape table and the golden. `rotation_would_admit()` measures what a
rotatable Bathroom would buy, without applying it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geom, standards
from .slicer import FinalRoom, _in_band, _room_legal
from .slicer import tier_tie_break_key as _tier_tie_break_key
from .solver import GRID_M
from .validator import MIN_EXTERIOR_WALL_M
from .zones import ACCESS_DOOR_M

# C19. A shared wall shorter than this cannot host a door, so it is not an access
# edge at all -- validator.access_tree uses the same figure.
MIN_ACCESS_WALL_M = ACCESS_DOOR_M

# C1. Which sub-room the SOLVER forces onto the corridor face, per zone, and the
# constraint that does it. Transcribed from solver.py, not invented:
#   master_suite    _force_master_corridor_overlap -- the E/W overlap floor is the
#                   service strip depth PLUS a door width precisely so the contact
#                   cannot sit entirely in the Bathroom|Closet strip.
#   kitchen_laundry _force_corridor_overlaps_kitchen -- reifies the Kitchen band
#                   and forces the corridor's segment onto it.
#   children        _force_vertical_cover_center -- the corridor must cover the
#                   zone's CENTRAL band, which is the Bathroom; the two beds are
#                   then reached THROUGH it (legal: Bathroom is not
#                   no_through_traffic).
#   entry           _force_backbone_reaches_foyer -- the backbone contact must
#                   reach the MUDROOM band, which by _split_off_wc's
#                   Foyer-adjacent-to-Mudroom invariant reaches the Foyer, the
#                   access-tree root. Keyed off the GARAGE side, not the corridor,
#                   so it is checked as C18 rather than here.
CORRIDOR_FACING_ROOM: dict[str, str] = {
    "master_suite": "Master Bedroom",
    "kitchen_laundry": "Kitchen",
    "children": "Bathroom",
}

# C18. Which sub-room must hold the DIRECTOR's face (the neighbouring zone the
# cut axis is keyed to), per zone.
DIRECTOR_FACING_ROOM: dict[str, str] = {
    "kitchen_laundry": "Kitchen",   # Dining; _force_kitchen_holds_dining_face
    "entry": "Mudroom",             # Garage; _slice_entry's garage-side strip
}

# C4. Zones where EVERY room must have a wall on the corridor face. Today this is
# children alone, and it is the restatement of CB3: the hardcoded rule is "three
# full-width bands with a central Bathroom", which is one TOPOLOGY; the property
# the 96,580-tiling enumeration actually established is "all three rooms have a
# wall on the corridor's face". Restating it this way is what would let a
# different children subdivision exist at all -- and CB3 is one of the two
# constraints measured to block the Kitchen's ideal.
ALL_ROOMS_FACE_CORRIDOR: frozenset[str] = frozenset({"children"})

# C11. Rooms whose two minimum dimensions may be swapped, i.e. the room may be
# installed rotated. See the module docstring: exactly the set the existing code
# already rotates, no more.
ROTATABLE: frozenset[str] = frozenset({"Bathroom"})


@dataclass(frozen=True)
class Context:
    """Everything a placement rule needs that the rectangle itself cannot say.

    Every field is optional and an absent field DISABLES the rules that need it
    rather than guessing -- a probe with no solve behind it (legal_pairs, the
    oracle sweep) genuinely does not know where the corridor is, and inventing a
    side would silently filter shapes the solver is entitled to pick.
    """

    zone: str
    corridor_side: str | None = None
    director_side: str | None = None
    # Faces of THIS ZONE that lie on the house perimeter. None means unknown, in
    # which case the daylight rule is skipped; frozenset() means "none of them".
    exterior_faces: frozenset[str] | None = None


def _faces(room: geom.Rect, zone: geom.Rect) -> set[str]:
    x0, y0, x1, y1 = room
    zx0, zy0, zx1, zy1 = zone
    out = set()
    if abs(y1 - zy1) < geom.EPS:
        out.add("N")
    if abs(y0 - zy0) < geom.EPS:
        out.add("S")
    if abs(x0 - zx0) < geom.EPS:
        out.add("W")
    if abs(x1 - zx1) < geom.EPS:
        out.add("E")
    return out


def _face_run(room: geom.Rect, zone: geom.Rect, face: str) -> float:
    """How much wall the room contributes to that face of the zone, 0 if none."""
    if face not in _faces(room, zone):
        return 0.0
    x0, y0, x1, y1 = room
    return (x1 - x0) if face in ("N", "S") else (y1 - y0)


def _access_graph(cand: list[FinalRoom]) -> dict[int, set[int]]:
    n = len(cand)
    g: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if geom.adjacent(cand[i].rect, cand[j].rect, MIN_ACCESS_WALL_M):
                g[i].add(j)
                g[j].add(i)
    return g


def _no_through(name: str) -> bool:
    spec = standards.ROOMS.get(name)
    return bool(spec is not None and spec.no_through_traffic)


def _entry_index(cand: list[FinalRoom], ctx: Context) -> int | None:
    """Where a route into this zone starts: the room the corridor fronts if the
    zone declares one, else any circulation-category room, else None."""
    want = CORRIDOR_FACING_ROOM.get(ctx.zone)
    if want is not None:
        for i, r in enumerate(cand):
            if r.name == want:
                return i
    for i, r in enumerate(cand):
        if r.category == "circ":
            return i
    return None


def violations(cand: list[FinalRoom], rect: geom.Rect, ctx: Context) -> list[str]:
    """Every placement rule this candidate breaks, tagged with its code. Empty
    means the candidate is admissible. Returning the full list rather than a bool
    is what makes "which constraint eliminates the most candidates" answerable."""
    out: list[str] = []
    by_name = {r.name: r for r in cand}
    zx0, zy0, zx1, zy1 = rect

    # --- structural (C6-C10, C12) -------------------------------------------
    total = 0.0
    for r in cand:
        x0, y0, x1, y1 = r.rect
        if not (x1 > x0 and y1 > y0):
            out.append(f"C6 {r.name} is not a positive-area rectangle")
        for v in (x0, y0, x1, y1):
            if abs(v / GRID_M - round(v / GRID_M)) > 1e-6:
                out.append(f"C7 {r.name} corner {v} is off the {GRID_M} m grid")
                break
        if not (zx0 - geom.EPS <= x0 and x1 <= zx1 + geom.EPS
                and zy0 - geom.EPS <= y0 and y1 <= zy1 + geom.EPS):
            out.append(f"C8 {r.name} is not contained in the zone")
        total += (x1 - x0) * (y1 - y0)
        if not _in_band(r.name, r.rect):
            out.append(f"C10 {r.name} is outside its shape floor or its architect band")
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            if geom.overlap_area(cand[i].rect, cand[j].rect) > geom.EPS:
                out.append(f"C8 {cand[i].name} overlaps {cand[j].name}")
    if abs(total - (zx1 - zx0) * (zy1 - zy0)) > geom.EPS:
        out.append(f"C8 rooms tile {total:.4f} m2 of a "
                   f"{(zx1 - zx0) * (zy1 - zy0):.4f} m2 zone (void or overlap)")
    if len(by_name) != len(cand):
        out.append("C9 the candidate repeats a room name")
    # C12 IS NOT A PLACEMENT CONSTRAINT, and asserting it as one was a category
    # error caught by the oracle. `_split_off_wc` documents "GEOMETRIC order, low
    # coordinate first" as a convention for ITS OWN PAIR, so that _slice_entry's
    # `[mud_room, *split]` stays a truthful description of a 1-D strip sequence.
    # It does not generalise to a 2-D partition and the shipping code does not
    # follow it there: `_slice_master` under an "N" corridor returns
    # [Master Bathroom, Walk-in Closet, Master Bedroom], while a global sort by
    # (x0, y0) would give [Bathroom, Bedroom, Closet]. Nothing downstream depends
    # on the order either -- `_build_walls` rasterises by position and
    # `slice_zones` only concatenates. So the real invariant is that the
    # ENUMERATOR's own output order is geometric and deterministic, which is
    # asserted in test_subdivide, and that this filter preserves it, which it does
    # by construction.

    # --- C11 oriented standards ---------------------------------------------
    for r in cand:
        if _room_legal(r.name, r.rect):
            continue
        x0, y0, x1, y1 = r.rect
        if r.name in ROTATABLE and _room_legal(r.name, (0.0, 0.0, y1 - y0, x1 - x0)):
            continue  # legal installed the other way round
        out.append(f"C11 {r.name} does not meet its standard in its orientation")

    # --- C1/C3 the corridor-facing room -------------------------------------
    want = CORRIDOR_FACING_ROOM.get(ctx.zone)
    if ctx.corridor_side is not None and want is not None and want in by_name:
        run = _face_run(by_name[want].rect, rect, ctx.corridor_side)
        if run < MIN_ACCESS_WALL_M - geom.EPS:
            out.append(
                f"C1 {want} must front the corridor on the {ctx.corridor_side} face "
                f"but has only {run:.2f} m there")

    # --- C4 CB3, restated ----------------------------------------------------
    if ctx.corridor_side is not None and ctx.zone in ALL_ROOMS_FACE_CORRIDOR:
        for r in cand:
            if _face_run(r.rect, rect, ctx.corridor_side) < MIN_ACCESS_WALL_M - geom.EPS:
                out.append(
                    f"C4 {r.name} has no wall on the corridor's {ctx.corridor_side} "
                    f"face (CB3 needs every room in this zone to hold one)")

    # --- C18 the director-facing room ---------------------------------------
    dwant = DIRECTOR_FACING_ROOM.get(ctx.zone)
    if ctx.director_side is not None and dwant is not None and dwant in by_name:
        run = _face_run(by_name[dwant].rect, rect, ctx.director_side)
        if run < MIN_ACCESS_WALL_M - geom.EPS:
            out.append(
                f"C18 {dwant} must hold the director's {ctx.director_side} face "
                f"but has only {run:.2f} m there")

    # --- C2 ensuite parents --------------------------------------------------
    for r in cand:
        spec = standards.ROOMS.get(r.name)
        if spec is None or not spec.allowed_ensuite_parents:
            continue
        if spec.requires_circulation_access:
            continue  # entered from circulation, not from a parent
        parents = [p for p in spec.allowed_ensuite_parents if p in by_name]
        if not parents:
            continue  # its parent lives in another zone -- cross-zone, not ours
        if not any(geom.adjacent(r.rect, by_name[p].rect, MIN_ACCESS_WALL_M)
                   for p in parents):
            out.append(
                f"C2 {r.name} is an ensuite of {'/'.join(parents)} but shares no "
                f"door-width wall with one")

    # --- C13 daylight --------------------------------------------------------
    if ctx.exterior_faces is not None:
        for r in cand:
            spec = standards.ROOMS.get(r.name)
            if spec is None or not spec.requires_exterior_wall:
                continue
            best = max((_face_run(r.rect, rect, f) for f in ctx.exterior_faces),
                       default=0.0)
            if best < MIN_EXTERIOR_WALL_M - geom.EPS:
                out.append(
                    f"C13 {r.name} requires an exterior wall but reaches only "
                    f"{best:.2f} m of the zone's exterior faces")

    # --- C5/C14 no route may TRANSIT a no_through_traffic room ---------------
    start = _entry_index(cand, ctx)
    if start is not None and len(cand) > 1:
        g = _access_graph(cand)
        seen = {start}
        stack = [start]
        while stack:
            i = stack.pop()
            # a no_through room may be entered but not passed through -- unless it
            # is where the route starts
            if i != start and _no_through(cand[i].name):
                continue
            for j in g[i]:
                if j not in seen:
                    seen.add(j)
                    stack.append(j)
        unreached = [cand[i].name for i in range(len(cand)) if i not in seen]
        if unreached:
            out.append(
                f"C14 {', '.join(unreached)} unreachable from {cand[start].name} "
                f"without transiting a no_through_traffic room")

    # --- C17 per-room circulation access ------------------------------------
    # Deliberately WEAK, and the weakness is the point: a room flagged
    # requires_circulation_access must be reachable without crossing a HABITABLE
    # room, which is validator.validate_plan's actual rule -- not "must touch the
    # corridor face", which would forbid the children beds being reached through
    # their Bathroom, a routing _force_vertical_cover_center explicitly intends.
    if start is not None and len(cand) > 1:
        habitable = {"living", "office", "private"}
        g = _access_graph(cand)
        seen = {start}
        stack = [start]
        while stack:
            i = stack.pop()
            if i != start and (_no_through(cand[i].name)
                               or cand[i].category in habitable):
                continue
            for j in g[i]:
                if j not in seen:
                    seen.add(j)
                    stack.append(j)
        for i, r in enumerate(cand):
            spec = standards.ROOMS.get(r.name)
            if spec is None or not spec.requires_circulation_access:
                continue
            if i not in seen:
                out.append(
                    f"C17 {r.name} requires circulation access but is only "
                    f"reachable across a habitable room")
    return out


def admissible(cand: list[FinalRoom], rect: geom.Rect, ctx: Context) -> bool:
    return not violations(cand, rect, ctx)


def filter_candidates(
    cands: list[list[FinalRoom]], rect: geom.Rect, ctx: Context
) -> list[list[FinalRoom]]:
    """The enumerator's output, minus everything a cutter would never have built.
    Order is preserved, so the filter cannot change which of two equally scoring
    candidates wins."""
    return [c for c in cands if admissible(c, rect, ctx)]


# ---------------------------------------------------------------------------
# C15 — the guarantee vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Guarantee:
    """What the solver needs to keep reifying off, per (shape, side).

    solver.py's rule is explicit: "A band whose offset is not a constant cannot
    be reified the way _force_corridor_overlaps_kitchen reifies the Kitchen band
    off a constant Laundry strip." Six constraints depend on such a constant --
    service_u, l_ns/l_we, mud_x/mud_y, wc_u, band_u -- and a general subdivider
    turns every one of them into a searched quantity.

    This is the replacement: for one zone SHAPE, per room, the offset and extent
    of that room's band measured from each face. If those are CONSTANT across
    every admissible candidate at that shape, the six constraints port unchanged
    with a table column standing in for the Python constant -- which is exactly
    what AddAllowedAssignments already does for (w, h, ns, cut_penalty)."""

    room: str
    # face -> (offset, depth, run). `offset` is the PERPENDICULAR distance from
    # that face to the room's near edge -- which is exactly what
    # _force_corridor_overlaps_kitchen's l_ns/l_we and
    # _force_backbone_reaches_foyer's mud_x/mud_y are today. `depth` is the
    # room's perpendicular extent, `run` its wall length ON that face (0 when it
    # does not touch it), which is what an overlap floor is measured against.
    bands: dict[str, tuple[float, float, float]] = field(default_factory=dict)


def guarantee_vector(cand: list[FinalRoom], rect: geom.Rect) -> dict[str, Guarantee]:
    zx0, zy0, zx1, zy1 = rect
    out: dict[str, Guarantee] = {}
    for r in cand:
        x0, y0, x1, y1 = r.rect
        w, h = x1 - x0, y1 - y0
        out[r.name] = Guarantee(r.name, {
            "S": (round(y0 - zy0, 6), round(h, 6), round(_face_run(r.rect, rect, "S"), 6)),
            "N": (round(zy1 - y1, 6), round(h, 6), round(_face_run(r.rect, rect, "N"), 6)),
            "W": (round(x0 - zx0, 6), round(w, 6), round(_face_run(r.rect, rect, "W"), 6)),
            "E": (round(zx1 - x1, 6), round(w, 6), round(_face_run(r.rect, rect, "E"), 6)),
        })
    return out


# ---------------------------------------------------------------------------
# the single tie-break rule (PROPOSED, not applied)
# ---------------------------------------------------------------------------


# ADOPTED, and it now lives in slicer.py beside _cut_score and _best_cut, which
# is the pair it belongs to. Re-exported here because this module proposed it and
# the placement tests exercise it; see slicer.tier_tie_break_key for the rule and
# for which of its four parts are the architect's and which are ours.
tier_tie_break_key = _tier_tie_break_key


def rotation_would_admit(name: str, rect: geom.Rect) -> bool:
    """C11 measurement hook: would treating `name` as rotatable make this rect
    legal when the axis-bound test rejects it? Used to price the orientation
    policy without changing it."""
    if _room_legal(name, rect):
        return False
    x0, y0, x1, y1 = rect
    return _room_legal(name, (0.0, 0.0, y1 - y0, x1 - x0))
