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

from dataclasses import dataclass

# Below 1200mm two people cannot pass; 1200mm is also the wheelchair
# door-opening minimum (Neufert). Confirmed, not a guess.
DOOR_CLEAR_WIDTH_M = 0.9  # 900mm doorset: the wheelchair-access doorset minimum


@dataclass(frozen=True)
class RoomStandard:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float
    requires_exterior_wall: bool
    requires_circulation_access: bool
    # Neufert p47/p55: no door path may transit this room (worktop-cooker-sink
    # sequence for Kitchen; through-bedroom isn't a legal circulation mode at
    # all). Data only in this task — Task 3's spanning tree consumes it.
    no_through_traffic: bool = False
    allowed_ensuite_parents: tuple[str, ...] = ()


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
        # Neufert p60: tumble drier goes against an outside wall for vapour extraction.
        requires_exterior_wall=True,
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
        # DERIVED (not in Neufert): 1700 bath + activity space; cf. Neufert
        # prefab bathroom unit 2875x2110mm. min_h_m not separately sourced.
        # min_area raised to min_w*min_h (2.1*2.2=4.62).
        min_w_m=2.1, min_h_m=2.2, min_area_m2=4.62, max_aspect=2.0,
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


# ---------------------------------------------------------------------------
# Zone envelopes: the minimum solver-zone rectangle that slices into legal
# rooms. The solver constrains ZONES; slicer.py cuts each composite ZONE into
# ROOMS. Nothing between them checked that the cut children meet their minima,
# so composite zones were emitting sub-Neufert slivers. This maps each of the
# eight solver zones to the minimum (w, h) that, run through the ACTUAL
# _slice_* cut in slicer.py, yields legal rooms — plus a max_aspect loose
# enough to admit the intended cut. Calibrated by scanning the real slicer
# over the grid; the numbers are cited to the room minima they derive from.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneMinima:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float


# Zone ids the slicer subdivides (mirror of zones.COMPOSITE_ZONES; duplicated
# here as bare strings to keep this module free of app-internal imports).
_COMPOSITE = {"master_suite", "children", "kitchen_laundry", "entry"}

# Which room standard a NON-composite zone maps to directly.
_ZONE_ROOM = {
    "living": "Living",
    "dining": "Dining",
    "office": "Office",
    "garage": "Garage",
}

_COMPOSITE_MINIMA: dict[str, ZoneMinima] = {
    # master_suite -> Master Bedroom (full-width, south) + a north service strip
    # cut laterally at the midpoint into Master Bathroom + Walk-in Closet
    # (_slice_master). Each of the two halves is w/2, so w >= 2*2.1 = 4.2, but
    # the midpoint snaps to the 0.5 m grid (banker's rounding) so w=4.5 yields a
    # 2.0 m half; w=5.0 is the first width giving two >=2.1 m halves. The strip
    # height is service = min(2.5, 0.45h); it must reach the Bathroom's 2.2 m
    # min, and the bedroom below (h - service) must reach 2.84 m — satisfied
    # first at h=5.5 (service snaps to 2.5, bedroom = 3.0). 27.5 m2 < 1.20*26.
    "master_suite": ZoneMinima(5.0, 5.5, 27.5, 2.0),
    # children -> Bedroom 2 + Bathroom + Bedroom 3 as three horizontal bands
    # (_slice_children). Bands need h >= 7.0 for the two beds to clear 2.44 m
    # (2.5 m each) and their 7.43 m2. The MIDDLE Bathroom's depth is
    # bath = snap(max(1.8, 0.24h)); the 0.24 fraction + snap pin it at 2.0 m for
    # every h < ~9.5, so Bathroom.depth < 2.2 survives as a warning here — a
    # slicer-fraction defect (Task 3), NOT fixable by zone size within the area
    # budget. aspect up to ~3.7 must be allowed so the tall band-stack fits.
    "children": ZoneMinima(3.0, 7.0, 21.0, 4.0),
    # kitchen_laundry -> Kitchen (keeps the dining side) + Laundry, cut along
    # whichever axis dining lies on (_slice_kitchen). The cut axis is decided
    # during the solve, so the envelope must satisfy BOTH: the N/S cut needs
    # h>=5.0 (Kitchen keeps 3.0 m depth), the W/E cut needs w>=4.5 (Kitchen
    # keeps 2.4+ m width after a 2.0 m Laundry). 22.5 m2 < 1.20*20.
    "kitchen_laundry": ZoneMinima(4.5, 5.0, 22.5, 2.5),
    # entry -> Foyer + Mudroom (Mudroom toward the garage), cut along whichever
    # axis the garage lies on (_slice_entry). (3.0, 4.5) is the smallest that
    # clears Mudroom's 1.8 m depth in the N/S cut while fitting 1.20*12 = 14.4.
    # Foyer/Mudroom max_aspect is 3.0, but a W/E cut halves the width so a tall
    # entry strip makes them slender (aspect > 3) — the solver can stretch entry
    # past this min, so that aspect warning is a shape defect (Task 3). aspect
    # cap kept loose (4.0) so the strip can still tile the plan.
    "entry": ZoneMinima(3.0, 4.5, 13.5, 4.0),
}


def zone_minima(zone_id: str) -> ZoneMinima | None:
    """Minimum solver-zone envelope (w, h, area, aspect) for a solver zone id.

    Composite zones return the calibrated envelope that slices legally (above);
    non-composite zones return their room standard directly. Unknown ids (e.g.
    the inert "circulation") return None, leaving the solver on its declared
    Space minima and default aspect.
    """
    if zone_id in _COMPOSITE_MINIMA:
        return _COMPOSITE_MINIMA[zone_id]
    room = _ZONE_ROOM.get(zone_id)
    if room is None:
        return None
    s = ROOMS[room]
    return ZoneMinima(s.min_w_m, s.min_h_m, s.min_area_m2, s.max_aspect)
