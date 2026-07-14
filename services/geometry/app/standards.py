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
        min_w_m=2.4, min_h_m=3.0, min_area_m2=7.0, max_aspect=2.5,
        requires_exterior_wall=True, requires_circulation_access=True,
        # Neufert p55: the worktop-cooker-sink work sequence "should never be
        # broken by full-height fitments, doors or passageways."
        no_through_traffic=True,
    ),
    "Laundry": RoomStandard(
        # DERIVED (not in Neufert): 1.2 m appliance run + 1.0 m clearance
        # (min_w/min_area). min_h_m not separately sourced.
        min_w_m=1.8, min_h_m=2.0, min_area_m2=3.5, max_aspect=2.2,
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
        min_w_m=2.1, min_h_m=2.2, min_area_m2=4.5, max_aspect=2.0,
        # GUESS: ensuite baths are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    "Walk-in Closet": RoomStandard(
        # DERIVED (not in Neufert) min_w: 600mm wardrobe + 900mm passage +
        # 600mm wardrobe. min_h_m/min_area_m2/max_aspect remain GUESS —
        # Neufert dressing-room minimums vary widely with storage layout.
        min_w_m=2.1, min_h_m=2.0, min_area_m2=3.5, max_aspect=2.5,
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
        min_w_m=2.1, min_h_m=2.2, min_area_m2=4.5, max_aspect=2.0,
        # GUESS: family bathrooms are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Bedroom",),
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
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Garage",),
    ),
    "Garage": RoomStandard(
        # Single-bay minimums; program.example.json's own min_w/min_h_m
        # (5.4x5.4) already cover a double bay on top of this floor.
        min_w_m=3.0, min_h_m=5.0, min_area_m2=15.0, max_aspect=2.2,
        requires_exterior_wall=True, requires_circulation_access=True,
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
