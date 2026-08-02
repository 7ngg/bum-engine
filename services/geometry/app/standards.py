"""Neufert-derived per-room dimensional standards.

Keyed by the finished room NAME slicer.py actually emits (see
`zones.ZONE_DISPLAY` and the `_slice_*` composite cutters in slicer.py) —
not by the coarser `Category` or macro `ZoneId`. A macro zone like
"kitchen_laundry" covers two rooms (Kitchen, Laundry) with unrelated
dimensional envelopes, and Category "service" covers both Garage and
Laundry likewise, so neither is fine-grained enough for this table.

Values are drawn from Neufert's Architects' Data (residential room
minimums), mainly p44 table 1a (USA FHA minimum room sizes). Some entries
have no direct Neufert figure and are `# DERIVED (not in Neufert)` from
Neufert's component clearances (door swing, appliance runs, fixture
envelopes) instead — still sourced, just not a single published number.
Anything still not confidently sourced either way stays `# GUESS`.

Pure data: no imports from any other app module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Below 1200mm two people cannot pass; 1200mm is also the wheelchair
# door-opening minimum (Neufert). Confirmed, not a guess.
DOOR_CLEAR_WIDTH_M = 0.9  # 900mm doorset: the wheelchair-access doorset minimum


# ---------------------------------------------------------------------------
# THE ARCHITECT'S ROOM AREA TABLE (min, max m2).
#
# SOURCE: the project architect, review round 3 (2026-08-01), supplied alongside
# his size diagnosis -- "you don't separate the rooms correctly, that's why we
# get the out-of-size issue" -- and a site-coverage target of ~40% (192 m2 on
# this 480 m2 plot). It is his table, not Neufert's and not the Posobie's: the
# entries below are a practitioner's buildable ranges for this house type, and
# where they disagree with a norm minimum the norm still wins (the norm is a
# floor; this table is a working envelope on top of it).
#
# This is the FIRST maximum area anywhere in the codebase. Until Phase 1 the
# only ceiling was solver.AREA_HI, a multiple of a per-zone TARGET rather than
# anything about the room type -- which is why a 21.25 m2 corridor and a 15.0 m2
# master bedroom could coexist in one plan and nothing could see it.
#
# Entries the slicer does not currently emit (Pantry, Technical Room, Terrace,
# Balcony) are recorded for completeness and deliberately NOT added to ROOMS --
# test_standards_cover_every_room_name_slicer_can_emit asserts ROOMS is exactly
# the emittable set, and that invariant is worth more than the placeholder.
ARCHITECT_AREA_BANDS: dict[str, tuple[float, float]] = {
    "Foyer": (4, 10),
    "Mudroom": (3, 8),
    "Living": (20, 45),
    "Dining": (12, 25),
    "Kitchen": (10, 22),
    "Pantry": (2, 6),                 # not emitted by the slicer
    "Master Bedroom": (16, 30),
    "Master Bathroom": (5, 12),
    "Walk-in Closet": (4, 12),
    "Bedroom 2": (12, 20),
    "Bedroom 3": (12, 20),
    # BATHROOM: HIS NUMBER, VERBATIM -- and it stands. It was briefly blamed for
    # an infeasibility that turned out to be OUR envelope, so read this before
    # touching it; the temptation to "just raise it" is the whole point.
    #
    # _slice_children puts the Bathroom in the middle band spanning the children
    # zone's full WIDTH -- and that is FORCED, not a style choice. Brute force
    # over all 96580 partitions of a rectangle into three rectangles: exactly
    # 16320 give all three rooms a wall on one shared vertical face (what CB3 /
    # _force_vertical_cover_center needs, since the corridor must lie strictly E
    # or W of the zone), and ALL 16320 are three full-width horizontal bands.
    # There is no 3-room cut with a narrow Bathroom. So the zone width times the
    # Bathroom's minimum DEPTH is what has to fit under this 9 m2 ceiling.
    #
    # That depth used to ceil-snap to 2.5 m, capping the zone at 9/2.5 = 3.6 ->
    # 3.5 m and making 8.75 / 10.00 the only realisable areas -- with nothing in
    # between, exact tiling (COVERAGE_MIN = 1.00) was INFEASIBLE at every target
    # 184-220 on both presets. The 2.5 came from a min_h_m of 2.2 that the
    # Bathroom RoomStandard itself flagged as "not separately sourced". Sourcing
    # it properly (the fixture run: 1.7 m deep, see the RoomStandard below) drops
    # the snap to 2.0, admits a 4.0 m wide zone, and both presets solve with the
    # Bathroom at 4.0 x 2.0 = 8.00 m2 -- inside his band, with headroom.
    #
    # A 10.0 override was written here and then REVERTED. Changing his numbers is
    # his ruling to make, and it turned out we never needed it.
    "Bathroom": (4, 9),
    "Guest WC": (1.5, 3.5),
    "Office": (10, 20),
    "Laundry": (4, 10),
    "Corridor": (6, 25),
    # GARAGE OVERRIDE -- his table says 18, this table says 29.2.
    # His 18 m2 floor is a SINGLE bay. The brief specifies a double garage and
    # the Garage entry below already encodes the two-car figure (the example
    # program's own 5.4 x 5.4 = 29.16 -> 29.2 m2). Adopting 18 verbatim would
    # have silently downgraded the house to one car -- measured in the Phase 0
    # audit, where the garage fell 31.50 -> 24.50 m2 at target 192 and to 18.00
    # at the packing floor. His table has NO two-car row, so this override is
    # PENDING HIS CONFIRMATION; if he says 18 is deliberate, drop the override
    # and the two-car guarantee with it.
    "Garage": (29.2, 40),
    "Technical Room": (3, 8),         # not emitted by the slicer
    "Terrace": (10, 40),              # a projection, not a room in `rooms[]`
    "Balcony": (3, 12),               # not emitted by the slicer
}


@dataclass(frozen=True)
class RoomStandard:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float
    requires_exterior_wall: bool
    requires_circulation_access: bool
    # Upper area bound, from ARCHITECT_AREA_BANDS. `inf` means "no ceiling
    # stated" -- never 0, so an unpopulated room can only ever be unbounded, not
    # accidentally impossible. min_area_m2 above stays the NORM floor; the
    # architect's own minimum is applied separately (see architect_band) so the
    # two sources never get conflated -- that conflation is exactly what
    # [[clear_vs_nominal_dims]] cost us once already.
    max_area_m2: float = float("inf")
    # Neufert p47/p55: no door path may transit this room (worktop-cooker-sink
    # sequence for Kitchen; through-bedroom isn't a legal circulation mode at
    # all). Data only in this task — Task 3's spanning tree consumes it.
    no_through_traffic: bool = False
    allowed_ensuite_parents: tuple[str, ...] = ()
    # Mechanical extraction/venting (e.g. tumble-drier vapour) -- a DIFFERENT
    # requirement from requires_exterior_wall's daylight (KEO) test. Data
    # only for now: no validator gate reads this yet.
    requires_exterior_vent: bool = False


_BEDROOM = RoomStandard(
    # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.44 m, min area 7.43 m2.
    min_w_m=2.44, min_h_m=2.44, min_area_m2=7.43, max_aspect=2.0,
    requires_exterior_wall=True, requires_circulation_access=True,
    no_through_traffic=True,  # p47: through-bedroom is not a legal circulation mode
)

ROOMS: dict[str, RoomStandard] = {
    "Living": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 3.51 m, min area 14.9 m2.
        min_w_m=3.51, min_h_m=3.51, min_area_m2=14.9, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Dining": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.54 m, min area 9.3 m2.
        min_w_m=2.54, min_h_m=2.54, min_area_m2=9.3, max_aspect=2.2,
        # GUESS: Neufert doesn't mandate a dedicated exterior wall for dining;
        # commonly borrows the living room's daylight in an open plan.
        requires_exterior_wall=False,
        # Neufert p66: hall/corridor access is explicitly NOT necessary for
        # dining; kitchen access is essential instead (kitchen_laundry-dining
        # is already a hard REQUIRED_ADJ edge, so that's covered separately).
        requires_circulation_access=False,
    ),
    "Kitchen": RoomStandard(
        # min_area raised to min_w*min_h (2.4*3.0=7.2) so the column states the
        # real floor; the axis mins were already the binding constraint.
        min_w_m=2.4, min_h_m=3.0, min_area_m2=7.2, max_aspect=2.5,
        requires_exterior_wall=True, requires_circulation_access=True,
        # Neufert p55: the worktop-cooker-sink work sequence "should never be
        # broken by full-height fitments, doors or passageways."
        no_through_traffic=True,
    ),
    "Laundry": RoomStandard(
        # DERIVED (not in Neufert): 1.2 m appliance run + 1.0 m clearance
        # (min_w/min_area). min_h_m not separately sourced.
        # min_area raised to min_w*min_h (1.8*2.0=3.6). max_aspect 2.5: a
        # galley utility room is legitimately long and narrow (it is the thin
        # strip left when the kitchen keeps the dining side of the zone).
        min_w_m=1.8, min_h_m=2.0, min_area_m2=3.6, max_aspect=2.5,
        # NOT a daylight (KEO) requirement -- SNiP 2.08.01-89 classes Laundry
        # as auxiliary (podsobnoe) and explicitly permits windowless service
        # spaces on mechanical ventilation, unlike habitable rooms/kitchens.
        # Neufert p60's "against an outside wall" is about tumble-drier vapour
        # EXTRACTION, not KEO daylight, so it maps to requires_exterior_vent,
        # not requires_exterior_wall. That vent requirement is recorded here
        # but not yet enforced by any validator gate.
        requires_exterior_wall=False,
        requires_exterior_vent=True,
        requires_circulation_access=False,
        allowed_ensuite_parents=("Kitchen",),
    ),
    "Master Bedroom": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.84 m, min area 11.15 m2.
        min_w_m=2.84, min_h_m=2.84, min_area_m2=11.15, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
        no_through_traffic=True,  # p47: through-bedroom is not a legal circulation mode
    ),
    "Master Bathroom": RoomStandard(
        # DERIVED (not in Neufert): 1700 bath + activity space; cf. Neufert
        # prefab bathroom unit 2875x2110mm. min_h_m not separately sourced.
        # min_area raised to min_w*min_h (2.1*2.2=4.62).
        min_w_m=2.1, min_h_m=2.2, min_area_m2=4.62, max_aspect=2.0,
        # GUESS: ensuite baths are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    "Walk-in Closet": RoomStandard(
        # DERIVED (not in Neufert) min_w: 600mm wardrobe + 900mm passage +
        # 600mm wardrobe. min_h_m/max_aspect remain GUESS — Neufert
        # dressing-room minimums vary widely with storage layout.
        # min_area raised to min_w*min_h (2.1*2.0=4.2).
        min_w_m=2.1, min_h_m=2.0, min_area_m2=4.2, max_aspect=2.5,
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    # Children's-zone slicer output: the un-split fallback is named
    # "Children Bedroom"; the split case names its two beds "Bedroom 2"/
    # "Bedroom 3" (see slicer.py::_slice_children). "Bedroom" is kept too
    # as the generic key. All four share one envelope.
    "Bedroom": _BEDROOM,
    "Bedroom 2": _BEDROOM,
    "Bedroom 3": _BEDROOM,
    "Children Bedroom": _BEDROOM,
    "Bathroom": RoomStandard(
        # ORIENTED, and deliberately so: min_w_m is ALONG the fixture run and
        # min_h_m is the depth in front of it. _slice_children is the only
        # producer of a "Bathroom" and it always lays the room out that way -- a
        # full-width middle band, shallow in y -- so the two are not
        # interchangeable here the way a near-square envelope let them be.
        #
        # Neufert's fixture run: bath 1500x700 end to end, basin >= 550x420
        # beside it, so the run wants ~2.4 m of wall. Depth is the 700 bath plus
        # ~1000 of activity space in front of it = 1.7 m. cf. the Neufert prefab
        # bathroom unit at 2875x2110.
        #
        # WAS 2.1 x 2.2 with min_area 4.62, carrying the note "min_h_m not
        # separately sourced" -- and that unsourced 2.2 was the whole problem.
        # It ceil-snapped the band depth to 2.5 m, which multiplied by the zone
        # width blew through the architect's 9 m2 ceiling at any width >= 4.0 m
        # and made the entire plan INFEASIBLE (see ARCHITECT_AREA_BANDS above).
        # 1.7 snaps to 2.0 and the contradiction disappears. max_aspect 2.0 is
        # what now caps the zone at 4.0 m wide (4.0 x 2.0 is exactly 2.0), so the
        # ceiling is no longer the binding constraint on either side.
        min_w_m=2.4, min_h_m=1.7, min_area_m2=4.08, max_aspect=2.0,
        # GUESS: family bathrooms are commonly internal/mechanically vented.
        requires_exterior_wall=False,
        # Hall bathroom, NOT Jack-and-Jill: _slice_children places it BETWEEN
        # Bedroom 2 and Bedroom 3, so it cannot be an ensuite of one bedroom —
        # the other could only reach it by transiting the first, which
        # Bedroom.no_through_traffic forbids. So it needs circulation access
        # and has no ensuite parent. (Also, the slicer emits "Bedroom 2"/
        # "Bedroom 3", never bare "Bedroom", so the old parent never matched.)
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
    ),
    "Guest WC": RoomStandard(
        # The common уборная (SNiP 2.08.01-89 Posobie, "Санитарные узлы",
        # cl. 3.5). The Posobie distinguishes a РАЗДЕЛЬНЫЙ sanitary unit (a
        # block of bathroom + уборная) from a СОВМЕЩЁННЫЙ (combined) one; this
        # is the уборная half, provided on its own so a guest is not sent
        # through the bedroom wing.
        #
        # DERIVED (not a quoted room minimum). The only ROOM-size figure the
        # Posobie clause gives is the FIXTURE it must hold: "уборная -
        # помещение, рассчитанное на установку унитаза с габаритом в плане не
        # менее 670x400 мм" — a pan of at least 670 x 400 mm. The room minimum
        # is derived from that plus Neufert's activity space: 400 + 2x200 mm
        # lateral = 800 mm wide, 670 + 600 mm in front of the pan = 1270 mm
        # deep. That lands on the 800 x 1200 mm separate-WC figure conventional
        # in this norm family, which is the cross-check, not the source.
        #
        # TWO PROJECT FLOORS then raise it above the norm, and both are worth
        # knowing about before anyone "fixes" these numbers downward:
        #   - validator.MIN_ROOM_M = 1.2 hard-rejects ANY room under 1.2 m on
        #     either axis. A norm-legal 0.80 m WC would fail our own validator.
        #   - GRID_M = 0.5 means the slicer ceil-snaps every minimum, so 1.2 ->
        #     1.5 and the smallest WC this engine can actually build is
        #     1.5 x 1.5 = 2.25 m2.
        # So the built room is ~1.9x the norm's floor area. That is a property
        # of the 0.5 m grid, not generosity, and it is why min_area_m2 below is
        # the DERIVED 1.2 x 1.3 rather than anything larger — the table states
        # the norm, and the grid does what the grid does.
        min_w_m=1.2, min_h_m=1.3, min_area_m2=1.56, max_aspect=2.0,
        # Подсобное (auxiliary) room: SNiP 2.08.01-89 classes it with the
        # service spaces, not the habitable ones, so no KEO daylight duty —
        # exactly the Laundry precedent (51fcd4d). Mechanical extraction
        # instead, which is what requires_exterior_vent records.
        requires_exterior_wall=False,
        requires_exterior_vent=True,
        # THE ENTIRE POINT of this room: a guest must reach it without
        # transiting a bedroom. Entered from circulation, never an ensuite.
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
        # A WC is never a passage.
        no_through_traffic=True,
    ),
    "Office": RoomStandard(
        # Neufert p44 table 1a, "other habitable room": min dim 2.44 m, min area 7.43 m2.
        min_w_m=2.44, min_h_m=2.44, min_area_m2=7.43, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Foyer": RoomStandard(
        # DERIVED (not in Neufert) min_w: corridor 1.2 m + door swing.
        # min_h_m/min_area_m2/max_aspect remain GUESS — Neufert gives a
        # range for entrance halls, not one figure.
        min_w_m=1.5, min_h_m=1.8, min_area_m2=3.0, max_aspect=3.0,
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
    "Mudroom": RoomStandard(
        # DERIVED (not in Neufert) min_w: 600mm hanging + 900mm passage.
        # min_h_m/min_area_m2/max_aspect remain GUESS — not a distinct
        # Neufert category; sized like a small entrance lobby.
        min_w_m=1.5, min_h_m=1.8, min_area_m2=3.0, max_aspect=3.0,
        requires_exterior_wall=False,
        # The mudroom IS the garage->house buffer: _slice_entry places it
        # toward the garage so the sequence is Foyer -> Mudroom -> Garage.
        # It therefore takes circulation access directly; the garage hangs
        # off it (see Garage), not the reverse.
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
    ),
    "Garage": RoomStandard(
        # Single-bay minimums; program.example.json's own min_w/min_h_m
        # (5.4x5.4) already cover a double bay on top of this floor.
        # AXIS-BOUND, not rotation-invariant: the garage is pinned to the
        # street (north) edge and the car drives in along the depth (y) axis,
        # so min_h_m=5.0 is the driving length and min_w_m=3.0 the bay width;
        # a rotated 5.0-wide x 3.0-deep garage is too shallow to park in.
        min_w_m=3.0, min_h_m=5.0, min_area_m2=15.0, max_aspect=2.2,
        requires_exterior_wall=True,
        # Reached through the mudroom/foyer, not directly off circulation.
        requires_circulation_access=False,
        allowed_ensuite_parents=("Mudroom", "Foyer"),
    ),
    "Corridor": RoomStandard(
        # Confirmed, not a guess: below 1200mm two people cannot pass;
        # 1200mm is also the wheelchair door-opening minimum (Neufert).
        min_w_m=1.2,
        # GUESS: a corridor's length is layout-driven, not a fixed Neufert
        # minimum; min_h_m/min_area_m2 just mirror the width floor.
        min_h_m=1.2, min_area_m2=1.44,
        max_aspect=8.0,  # a spine is meant to be long and thin
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
}

# Slicer fallback names that share a band with a table entry. "Bedroom" and
# "Children Bedroom" are what _slice_children emits when it CANNOT split the
# zone into two beds + bathroom; they are the same room type as "Bedroom 2" and
# take the same envelope.
_BAND_ALIAS = {"Bedroom": "Bedroom 2", "Children Bedroom": "Bedroom 2"}

# Fold the architect's ceilings into ROOMS in ONE place rather than hand-copying
# a number into 18 entries, so the table above stays the single source and the
# two cannot drift.
ROOMS = {
    name: (
        replace(spec, max_area_m2=ARCHITECT_AREA_BANDS[_BAND_ALIAS.get(name, name)][1])
        if _BAND_ALIAS.get(name, name) in ARCHITECT_AREA_BANDS
        else spec
    )
    for name, spec in ROOMS.items()
}


def architect_band(room: str) -> tuple[float, float] | None:
    """The architect's (min, max) area band for a room name, or None if he gave
    no figure for it. The MIN here is his, deliberately separate from
    RoomStandard.min_area_m2 (the Neufert/SNiP norm floor): the two answer
    different questions and a room must clear BOTH."""
    key = _BAND_ALIAS.get(room, room)
    return ARCHITECT_AREA_BANDS.get(key)


def area_floor(room: str) -> float:
    """The binding minimum area for a room: the higher of the norm floor and the
    architect's minimum. This is the number the slicer must actually hit."""
    spec = ROOMS.get(room)
    norm = spec.min_area_m2 if spec is not None else 0.0
    band = architect_band(room)
    return max(norm, band[0]) if band is not None else norm


def area_ceiling(room: str) -> float:
    spec = ROOMS.get(room)
    return spec.max_area_m2 if spec is not None else float("inf")


# ---------------------------------------------------------------------------
# Zone envelopes: the minimum solver-zone rectangle that slices into legal
# rooms. The solver constrains ZONES; slicer.py cuts each composite ZONE into
# ROOMS. Nothing between them checked that the cut children meet their minima,
# so composite zones were emitting sub-Neufert slivers.
#
# These are now COMPUTED, not hand-calibrated: slicer.compute_zone_minima runs
# the REAL cutters over candidate (w, h) on the grid and returns the smallest
# envelope whose slice is fully standards-legal. So the envelope re-derives
# automatically when GRID_M or a cut rule changes, instead of a constant table
# drifting silently out of sync with the slicer.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneMinima:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float


def zone_minima(zone_id: str) -> ZoneMinima | None:
    """Minimum solver-zone envelope (w, h, area, aspect) for a solver zone id,
    computed from the actual slicer. Unknown ids (e.g. the inert "circulation")
    return None, leaving the solver on its declared Space minima and default
    aspect. Imported lazily because slicer.py imports this module."""
    from . import slicer

    return slicer.compute_zone_minima(zone_id)
