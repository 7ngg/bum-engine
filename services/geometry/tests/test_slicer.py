"""Direct unit tests for slicer composite-zone cuts, focused on the
corridor_side coupling added alongside SolveResult.corridor_sides.

These call _slice_master/_slice_kitchen directly (bypassing solve()) because
some paths are not yet reachable through a real solve: the solver's
_force_vertical_overlap only ever disjuncts master_suite onto the corridor's
W/E side (never N/S), and kitchen_laundry's same-axis corridor/dining conflict
only shows up above the roomy fixture's 184 m2 on specific presets (see the
208 m2 diagnostic in the commit that added this file). Direct calls give these
currently-dead-in-production branches real coverage.
"""

from app import geom
from app.slicer import _slice_kitchen, _slice_master
from app.solver import ZoneRect
from app.validator import ACCESS_DOOR_M


def _rects(rooms):
    return {r.name: r.rect for r in rooms}


# PROBE SIZE, moved 6.0 x 6.0 -> 5.0 x 5.0 in PHASE 1 (architect area bands).
# _slice_kitchen is now AREA-AWARE: it only emits two rooms if BOTH land inside
# their architect bands (Kitchen 10-22 m2, Laundry 4-10 m2), so a probe zone must
# have an area the two bands can actually cover -- at most 32 m2, and shaped so
# each room clears its own minimum. 6.0 x 6.0 = 36 m2 exceeds that, every
# candidate cut is rejected, and _slice_kitchen falls through to its
# single-Kitchen return. That is what produced the KeyError 'Laundry'.
#
# 5.0 x 5.0 = 25 m2 is the nearest square that still cuts (verified across a
# 3.0-12.0 m sweep on both axes: 35 sizes cut, and this is the closest to the
# original). It is used by ALL FOUR probes in this file, not just the one that
# was failing: the other three compare _slice_kitchen's output with and without a
# corridor_side, and at 6.0 x 6.0 they were comparing [Kitchen] against [Kitchen]
# -- trivially equal, and no longer testing the override at all. Same one-line
# fixture cause, so they are corrected here too rather than left silently vacuous.
# Verified at 5.0 x 5.0: ("W") and ("W","E") both yield Kitchen+Laundry with the
# override flipping which end each takes; ("S") and ("S","W") yield Kitchen+Laundry
# byte-identical, which is the orthogonal-axis property that test asserts.
_PROBE = (5.0, 5.0)


def test_slice_master_corridor_side_north_flips_service_south():
    zr = ZoneRect("master_suite", 0.0, 0.0, 6.0, 6.0)
    rooms = _rects(_slice_master(zr, "N"))
    assert set(rooms) == {"Master Bedroom", "Master Bathroom", "Walk-in Closet"}
    bed, bath, closet = rooms["Master Bedroom"], rooms["Master Bathroom"], rooms["Walk-in Closet"]

    # Bedroom is the full-width NORTH band; the service strip sits SOUTH.
    assert bed[0] == 0.0 and bed[2] == 6.0, "Bedroom must span the full width"
    assert bed[3] == 6.0, "Bedroom must reach the zone's north edge"
    assert bath[1] == 0.0 and closet[1] == 0.0, "service strip must sit at the south edge"
    assert bed[1] == bath[3] == closet[3], "Bedroom's south edge must be the service strip's north edge"

    for a, b in ((bed, bath), (bed, closet), (bath, closet)):
        assert geom.overlap_area(a, b) == 0.0, f"{a} and {b} must not overlap"

    # the ensuite parent relation must survive the flip: Bedroom still fronts
    # BOTH Bathroom and Closet with a real, door-worthy wall.
    assert geom.adjacent(bed, bath, ACCESS_DOOR_M)
    assert geom.adjacent(bed, closet, ACCESS_DOOR_M)


def test_slice_kitchen_corridor_side_same_axis_overrides_dining_side():
    # Cut axis is W/E (dining west of the zone -> today's default places
    # Kitchen west, Laundry east). corridor_side="E" is on the SAME (W/E)
    # axis but disagrees with the dining side -- corridor must win.
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, *_PROBE)

    default_rooms = _rects(_slice_kitchen(zr, "W"))
    assert default_rooms["Kitchen"][0] == 0.0, "sanity: Kitchen is the WEST room by default"
    assert default_rooms["Laundry"][2] == _PROBE[0], "sanity: Laundry is the EAST room by default"

    rooms = _rects(_slice_kitchen(zr, "W", "E"))
    kitchen, laundry = rooms["Kitchen"], rooms["Laundry"]
    assert kitchen[2] == _PROBE[0], "corridor wins: Kitchen must front the EAST edge"
    assert laundry[0] == 0.0, "Laundry pushed to the WEST edge"
    assert geom.overlap_area(kitchen, laundry) == 0.0
    assert geom.adjacent(kitchen, laundry, 0.1)


def test_slice_kitchen_corridor_side_orthogonal_axis_unchanged():
    # Cut axis is N/S (dining south -> Kitchen south by default). corridor_side
    # "W" is on the ORTHOGONAL axis: under an N/S cut both sub-rooms already
    # span the full width, so reordering can't target one specifically over
    # the other -- the dining placement must pass through byte-identical.
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, *_PROBE)
    without = [(r.name, r.rect) for r in _slice_kitchen(zr, "S")]
    with_corridor = [(r.name, r.rect) for r in _slice_kitchen(zr, "S", "W")]
    assert {n for n, _ in without} == {"Kitchen", "Laundry"}, (
        "probe must actually cut, or this comparison is vacuous (see _PROBE)"
    )
    assert without == with_corridor


def test_slice_kitchen_corridor_side_none_unchanged():
    zr = ZoneRect("kitchen_laundry", 0.0, 0.0, *_PROBE)
    without = [(r.name, r.rect) for r in _slice_kitchen(zr, "W")]
    with_none = [(r.name, r.rect) for r in _slice_kitchen(zr, "W", None)]
    assert {n for n, _ in without} == {"Kitchen", "Laundry"}, (
        "probe must actually cut, or this comparison is vacuous (see _PROBE)"
    )
    assert without == with_none
