"""Neufert-derived per-room dimensional standards.

Keyed by the finished room NAME slicer.py actually emits (see
`zones.ZONE_DISPLAY` and the `_slice_*` composite cutters in slicer.py) —
not by the coarser `Category` or macro `ZoneId`. A macro zone like
"kitchen_laundry" covers two rooms (Kitchen, Laundry) with unrelated
dimensional envelopes, and Category "service" covers both Garage and
Laundry likewise, so neither is fine-grained enough for this table.

Values are drawn from Neufert's Architects' Data (residential room
minimums). Anything not confidently sourced is flagged `# GUESS` for
manual check against a physical copy.

Pure data: no imports from any other app module.
"""

from __future__ import annotations

from dataclasses import dataclass

DOOR_CLEAR_WIDTH_M = 0.9  # standard swing-door clear opening width


@dataclass(frozen=True)
class RoomStandard:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float
    requires_exterior_wall: bool
    requires_circulation_access: bool
    allowed_ensuite_parents: tuple[str, ...] = ()


_BEDROOM = RoomStandard(
    min_w_m=2.7, min_h_m=3.0, min_area_m2=9.0, max_aspect=2.0,
    requires_exterior_wall=True, requires_circulation_access=True,
)

ROOMS: dict[str, RoomStandard] = {
    "Living": RoomStandard(
        min_w_m=3.6, min_h_m=4.0, min_area_m2=16.0, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Dining": RoomStandard(
        min_w_m=2.7, min_h_m=3.3, min_area_m2=9.0, max_aspect=2.2,
        # GUESS: Neufert doesn't mandate a dedicated exterior wall for dining;
        # commonly borrows the living room's daylight in an open plan.
        requires_exterior_wall=False, requires_circulation_access=True,
    ),
    "Kitchen": RoomStandard(
        min_w_m=2.4, min_h_m=3.0, min_area_m2=7.0, max_aspect=2.5,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Laundry": RoomStandard(
        # GUESS: Neufert utility-room minimums vary widely by appliance
        # count; taken as a small single-run layout.
        min_w_m=1.8, min_h_m=2.0, min_area_m2=3.5, max_aspect=2.2,
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Kitchen",),
    ),
    "Master Bedroom": RoomStandard(
        min_w_m=3.6, min_h_m=4.0, min_area_m2=14.0, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Master Bathroom": RoomStandard(
        min_w_m=2.0, min_h_m=2.2, min_area_m2=4.5, max_aspect=2.0,
        # GUESS: ensuite baths are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    "Walk-in Closet": RoomStandard(
        # GUESS: Neufert dressing-room minimums vary widely with storage layout.
        min_w_m=1.8, min_h_m=2.0, min_area_m2=3.5, max_aspect=2.5,
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
        min_w_m=1.8, min_h_m=2.2, min_area_m2=4.0, max_aspect=2.0,
        # GUESS: family bathrooms are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Bedroom",),
    ),
    "Office": RoomStandard(
        # GUESS: home-office isn't a distinct Neufert residential category;
        # taken from its small-study figures.
        min_w_m=2.5, min_h_m=2.5, min_area_m2=7.0, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Foyer": RoomStandard(
        # GUESS: entrance-hall minimums; Neufert gives a range, not one figure.
        min_w_m=1.5, min_h_m=1.8, min_area_m2=3.0, max_aspect=3.0,
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
    "Mudroom": RoomStandard(
        # GUESS: not a distinct Neufert category; sized like a small entrance lobby.
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
        min_w_m=1.2,
        # GUESS: a corridor's length is layout-driven, not a fixed Neufert
        # minimum; min_h_m/min_area_m2 just mirror the width floor.
        min_h_m=1.2, min_area_m2=1.44,
        max_aspect=8.0,  # a spine is meant to be long and thin
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
}
