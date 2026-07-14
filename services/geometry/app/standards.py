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
