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

from app.slicer import (
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

    Two leaves can only foul each other if they sweep the same space, so arcs are
    compared per target room. Touching counts as clear; only positive overlap is
    a collision.
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
        if ti != tj:
            continue
        assert not _convex_overlap(wi, wj), (
            f"{preset}: swing collision in {ti} between "
            f"{doors[i].from_}->{doors[i].to} ({doors[i].wall_id}) and "
            f"{doors[j].from_}->{doors[j].to} ({doors[j].wall_id})"
        )


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
