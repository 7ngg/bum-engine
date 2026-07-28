"""Door hinge side, facing, and swing-arc collision.

The architect raised this in every review: "qapilar divara acilmir" (doors don't
open against the wall), "qapilar belirsiz paylanib" (doors distributed
unclearly), and in the first review he circled three doors in a row whose swings
collide.

It was never a Revit bug. layout.json carried no hinge or facing field, so
RevitBuilder placed each door with NewFamilyInstance and let Revit derive hand
and facing from the host wall alone — uniformly, and arbitrarily with respect to
the room. The information was not wrong; it was never computed. Hinge side,
facing and arc collision are pure geometry and need no furniture model.

Measured on the 184 fixture (see the commit message): with doors swinging back
into the approach side, which is half of what an arbitrary facing produces,
there are 3 real collisions on gW_eN and 2 on gE_eN — among them two Corridor
doors fouling each other. With the facing rule they are all gone. The hinge rule
is what makes the open leaf sit flat against the wall; the FACING rule is what
removes the collisions.
"""

import itertools

import pytest

from app.models import Door, Wall
from app.slicer import (
    _choose_facing,
    _clear_depth,
    _convex_overlap,
    _hinge_frame,
    _into_normal,
    _nearer_end,
    _wedge_fits,
    build_layout,
    swing_wedge,
)
from app.solver import solve

PRESETS = ["gW_eN", "gE_eN"]


def _layout(roomy_program, preset):
    r = solve(roomy_program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible, f"{preset} infeasible"
    return build_layout(r, roomy_program)


def _ctx(layout):
    walls = {w.id: w for w in layout.walls}
    rects = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    if layout.terrace is not None:
        rects["Terrace"] = tuple(layout.terrace.rect_m)
    return walls, rects, list(layout.doors) + [layout.entry]


def _wedge(door, walls, rects):
    wall = walls[door.wall_id]
    rect = rects[door.swing_into]
    hinge_pt, along = _hinge_frame(door, wall, door.hinge)
    into = _into_normal(door, wall, rect)
    assert into is not None, f"{door.from_}->{door.to}: swing_into room not on this wall"
    return hinge_pt, along, into, rect


@pytest.mark.parametrize("preset", PRESETS)
def test_every_door_carries_hinge_and_swing_into(roomy_program, preset):
    layout = _layout(roomy_program, preset)
    _, _, doors = _ctx(layout)
    for d in doors:
        assert d.hinge in ("start", "end"), f"{d.from_}->{d.to}: hinge {d.hinge!r}"
        assert d.swing_into, f"{d.from_}->{d.to}: swing_into unset"
        assert d.swing_into in (d.from_, d.to), (
            f"{d.from_}->{d.to}: swings into {d.swing_into!r}, which is neither end"
        )


@pytest.mark.parametrize("preset", PRESETS)
def test_hinge_is_at_the_nearer_wall_end(roomy_program, preset):
    """The direct fix for "qapilar divara acilmir".

    Hinging at the end nearer the door puts the open leaf parallel to that
    corner's return wall. A door hinged at the far end is only acceptable if the
    layout says why — the resolver emits a warning naming it.
    """
    layout = _layout(roomy_program, preset)
    walls, _, doors = _ctx(layout)
    for d in doors:
        near = _nearer_end(d, walls[d.wall_id])
        if d.hinge != near:
            assert any(
                d.wall_id in w and "FAR end" in w for w in layout.warnings
            ), f"{d.from_}->{d.to} hinged at the far end with no warning explaining it"


@pytest.mark.parametrize("preset", PRESETS)
def test_no_two_swing_arcs_overlap(roomy_program, preset):
    """The three-doors-in-a-row check.

    ALL pairs, not just doors sharing a target room. Two leaves in different
    rooms are in practice separated by the wall between them, but restricting
    the test to same-room pairs would assert the shortcut instead of the
    geometry. Touching counts as clear; only positive overlap is a collision.
    """
    layout = _layout(roomy_program, preset)
    walls, rects, doors = _ctx(layout)
    wedges = []
    for d in doors:
        hp, along, into, _ = _wedge(d, walls, rects)
        wedges.append((d.swing_into, swing_wedge(hp, along, into, d.width_m)))

    for i, j in itertools.combinations(range(len(doors)), 2):
        ti, wi = wedges[i]
        tj, wj = wedges[j]
        assert not _convex_overlap(wi, wj), (
            f"{preset}: swing collision between "
            f"{doors[i].from_}->{doors[i].to} ({doors[i].wall_id}, into {ti}) and "
            f"{doors[j].from_}->{doors[j].to} ({doors[j].wall_id}, into {tj})"
        )


@pytest.mark.parametrize("preset", PRESETS)
def test_no_door_opens_into_circulation(roomy_program, preset):
    """R1, Neufert: "doors which open into corridor must not cause obstruction
    within corridor". A door with exactly one circulation side opens into the
    OTHER side, always.

    Two exemptions, both structural rather than discretionary:
      - both sides circulation (Foyer<->Corridor) — R2 picks, R1 is silent;
      - the main entry, whose only enclosed side IS the Foyer. A leaf sweeping
        onto the street is not the alternative.
    """
    layout = _layout(roomy_program, preset)
    _, rects, doors = _ctx(layout)
    circ = {rm.name for rm in layout.rooms if rm.category == "circ"}
    for d in doors:
        if d.swing_into not in circ:
            continue
        both = d.from_ in circ and d.to in circ
        entry_only_side = len([s for s in (d.from_, d.to) if s in rects]) == 1
        assert both or entry_only_side, (
            f"{preset}: {d.from_}->{d.to} ({d.wall_id}) opens into circulation "
            f"{d.swing_into} — R1 requires the non-circulation side"
        )


@pytest.mark.parametrize("preset", PRESETS)
def test_both_circulation_door_opens_into_the_wider_side(roomy_program, preset):
    """R2: where both sides are circulation the leaf goes to whichever space can
    absorb it — the greater clear width measured perpendicular to the door."""
    layout = _layout(roomy_program, preset)
    walls, rects, doors = _ctx(layout)
    circ = {rm.name for rm in layout.rooms if rm.category == "circ"}
    seen = 0
    for d in doors:
        if not (d.from_ in circ and d.to in circ):
            continue
        seen += 1
        wall = walls[d.wall_id]
        depths = {s: _clear_depth(d, wall, rects[s]) for s in (d.from_, d.to)}
        assert d.swing_into == max(depths, key=lambda s: (depths[s], s)), (
            f"{preset}: {d.from_}->{d.to} opens into {d.swing_into} but the "
            f"clear depths are {depths}"
        )
    assert seen, f"{preset}: fixture no longer has a circulation<->circulation door"


@pytest.mark.parametrize("preset", PRESETS)
def test_no_swing_arc_crosses_a_wall(roomy_program, preset):
    """Every wall lies on a room boundary, so a wedge that stays inside the room
    it opens into cannot cross one. Checking containment IS the wall test."""
    layout = _layout(roomy_program, preset)
    walls, rects, doors = _ctx(layout)
    for d in doors:
        hp, along, into, rect = _wedge(d, walls, rects)
        assert _wedge_fits(hp, along, into, d.width_m, rect), (
            f"{preset}: {d.from_}->{d.to} ({d.wall_id}) sweeps outside "
            f"{d.swing_into} {rect} — the leaf crosses a wall"
        )


def _mk(from_, to, center, width=0.9):
    return Door(**{"from": from_, "to": to, "wall_id": "w1",
                   "center": list(center), "width_m": width, "height_m": 2.1})


# A horizontal wall along y=0 from x=0 to x=6; "below" is y<0, "above" is y>0.
_WALL = Wall(id="w1", start=[0.0, 0.0], end=[6.0, 0.0], thickness_m=0.15,
             height_m=2.7, exterior=False)


def test_choose_facing_r1_never_opens_into_circulation():
    """R1 overrides the access-tree direction in BOTH directions: the tree edge
    Corridor->Bedroom and the reversed Bedroom->Corridor must land on the same
    side. That equivalence is the whole point — the old rule followed the tree
    and so gave different answers for the same wall."""
    rects = {"Corridor": (0.0, -1.5, 6.0, 0.0), "Bedroom": (0.0, 0.0, 6.0, 4.0)}
    cats = {"Corridor": "circ", "Bedroom": "private"}
    for from_, to in (("Corridor", "Bedroom"), ("Bedroom", "Corridor")):
        target, locked, why = _choose_facing(
            _mk(from_, to, (3.0, 0.0)), _WALL, cats, rects, [])
        assert target == "Bedroom", f"{from_}->{to} chose {target}: {why}"
        assert locked, "R1 is a norm; it must be locked against a facing flip"


def test_choose_facing_r2_picks_the_side_that_can_absorb_the_leaf():
    """Both sides circulation: the wider one perpendicular to the door wins."""
    rects = {"Corridor": (0.0, -1.2, 6.0, 0.0), "Foyer": (0.0, 0.0, 6.0, 3.0)}
    cats = {"Corridor": "circ", "Foyer": "circ"}
    target, locked, why = _choose_facing(
        _mk("Foyer", "Corridor", (3.0, 0.0)), _WALL, cats, rects, [])
    assert target == "Foyer", why
    assert "R2" in why and not locked


def test_choose_facing_r3_keeps_the_access_tree_child():
    rects = {"Living": (0.0, -5.0, 6.0, 0.0), "Dining": (0.0, 0.0, 6.0, 4.0)}
    cats = {"Living": "living", "Dining": "living"}
    target, _locked, why = _choose_facing(
        _mk("Living", "Dining", (3.0, 0.0)), _WALL, cats, rects, [])
    assert target == "Dining" and "R3" in why, why


def test_choose_facing_r4_small_wet_room_swings_out_when_it_can():
    """Neufert's wc-cubicle case. 1.0 m of clear depth cannot take a 0.9 m leaf
    (needs 0.9 + 0.5), so it swings out — and here it may, because the receiving
    Corridor is 1.4 m, over the 1.2 m minimum."""
    rects = {"WC": (0.0, 0.0, 6.0, 1.0), "Corridor": (0.0, -1.4, 6.0, 0.0)}
    cats = {"WC": "wet", "Corridor": "circ"}
    target, locked, why = _choose_facing(
        _mk("Corridor", "WC", (3.0, 0.0)), _WALL, cats, rects, [])
    assert target == "Corridor", why
    assert "R4" in why and locked


def test_choose_facing_r4_warns_instead_of_silently_picking():
    """Same cubicle, but the corridor is only 1.0 m — under Neufert's minimum, so
    an outward leaf there is the hazard he warns about. Neither direction is
    compliant, so the layout must SAY so rather than quietly choose."""
    rects = {"WC": (0.0, 0.0, 6.0, 1.0), "Corridor": (0.0, -1.0, 6.0, 0.0)}
    cats = {"WC": "wet", "Corridor": "circ"}
    warnings: list[str] = []
    target, _locked, _why = _choose_facing(
        _mk("Corridor", "WC", (3.0, 0.0)), _WALL, cats, rects, warnings)
    assert target == "WC", "must not silently swing out into a sub-minimum corridor"
    assert any("sliding leaf" in w for w in warnings), warnings


def test_choose_facing_entry_door_cannot_swing_onto_the_street():
    """`from` is OUTSIDE and has no rect, so the Foyer is the only enclosed side.
    R1 would nominally push the leaf away from circulation; there is nowhere to
    push it, and that is correct — entry doors open in."""
    rects = {"Foyer": (0.0, 0.0, 6.0, 3.0)}
    cats = {"Foyer": "circ"}
    target, locked, _why = _choose_facing(
        _mk("OUTSIDE", "Foyer", (3.0, 0.0)), _WALL, cats, rects, [])
    assert target == "Foyer" and locked


def _segments_cross(p1, p2, q1, q2) -> bool:
    """Proper segment intersection: shared endpoints and collinear touching do
    not count, only a genuine crossing. Two doors on the same wall share that
    wall's line, so a collinear "hit" would be a false positive."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1, d2 = cross(q1, q2, p1), cross(q1, q2, p2)
    d3, d4 = cross(p1, p2, q1), cross(p1, p2, q2)
    eps = 1e-9
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
           ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps))


def _leaf_at_90(door, walls, rects):
    """The open leaf: a segment from the hinge, perpendicular to the wall, into
    the room it swings into, one leaf-width long."""
    hp, _along, into, _rect = _wedge(door, walls, rects)
    return hp, (hp[0] + into[0] * door.width_m, hp[1] + into[1] * door.width_m)


def _clear_opening(door, walls):
    """The doorway itself: the segment in the wall between the two jambs."""
    wall = walls[door.wall_id]
    hp_s, _ = _hinge_frame(door, wall, "start")
    hp_e, _ = _hinge_frame(door, wall, "end")
    return hp_s, hp_e


@pytest.mark.parametrize("preset", PRESETS)
def test_open_leaf_does_not_block_another_doorway(roomy_program, preset):
    """A leaf standing at 90 degrees must not park across another door's clear
    opening. Distinct from arc-arc overlap: two arcs can be disjoint while one
    fully-open leaf still stands in front of the neighbouring doorway, which is
    the "three doors block each other" complaint in its tightest form."""
    layout = _layout(roomy_program, preset)
    walls, rects, doors = _ctx(layout)
    for i, j in itertools.permutations(range(len(doors)), 2):
        a, b = doors[i], doors[j]
        la, lb = _leaf_at_90(a, walls, rects)
        oa, ob = _clear_opening(b, walls)
        assert not _segments_cross(la, lb, oa, ob), (
            f"{preset}: the open leaf of {a.from_}->{a.to} ({a.wall_id}) blocks "
            f"the clear opening of {b.from_}->{b.to} ({b.wall_id})"
        )


def test_leaf_blocking_detector_detects():
    """Guard the guard, same reason as the arc detector below: a segment test
    that never fires would make the blocking test vacuous."""
    # a leaf sweeping across a doorway that lies in its path
    assert _segments_cross((0.0, 0.0), (0.0, 1.0), (-0.5, 0.5), (0.5, 0.5))
    # parallel, well clear
    assert not _segments_cross((0.0, 0.0), (0.0, 1.0), (2.0, 0.0), (2.0, 1.0))
    # collinear along one wall: two doors on the same wall must not read as a hit
    assert not _segments_cross((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0))
    # sharing an endpoint only (leaf hinged exactly at the neighbour's jamb)
    assert not _segments_cross((0.0, 0.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0))


def test_collision_detector_detects(roomy_program):
    """Guard the guard: a detector that never fires would make the three tests
    above vacuous. Synthetic wedges with known answers."""
    a = swing_wedge((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), 0.9)
    near = swing_wedge((0.5, 0.0), (1.0, 0.0), (0.0, 1.0), 0.9)
    far = swing_wedge((3.0, 0.0), (1.0, 0.0), (0.0, 1.0), 0.9)
    touching = swing_wedge((0.9, 0.0), (1.0, 0.0), (0.0, 1.0), 0.9)
    opposite = swing_wedge((0.0, 0.0), (1.0, 0.0), (0.0, -1.0), 0.9)

    assert _convex_overlap(a, near), "overlapping leaves must be detected"
    assert not _convex_overlap(a, far), "3 m apart is not a collision"
    assert not _convex_overlap(a, touching), "exactly touching is clear, not a hit"
    assert not _convex_overlap(a, opposite), "opposite sides of a wall never foul"
