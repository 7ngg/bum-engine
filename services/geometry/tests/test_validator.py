"""Validator gate rules — the test oracle."""

import copy

import pytest

from app import geom, standards
from app.generate import generate
from app.models import Layout
from app.solver import solve
from app.slicer import build_layout
from app.validator import validate, validate_plan


@pytest.fixture(scope="module")
def layout(request):
    prog = request.getfixturevalue("program")
    tl = request.getfixturevalue("solve_time_s")  # roomy is capped (feasible is enough)
    r = solve(prog, "gW_eN", seed=1, time_limit_s=tl)
    return build_layout(r, prog)


def test_clean_layout_passes(layout, program):
    v = validate(layout, program)
    assert v.ok, v.errors


def test_overlap_rejected(layout, program):
    bad = layout.model_copy(deep=True)
    # force two rooms to overlap
    bad.rooms[1].rect_m = list(bad.rooms[0].rect_m)
    v = validate(bad, program)
    assert not v.ok
    assert any("overlap" in e for e in v.errors)


def test_master_kitchen_adjacency_rejected(program, solve_time_s):
    r = solve(program, "gW_eN", seed=1, time_limit_s=solve_time_s)
    lay = build_layout(r, program)
    bad = lay.model_copy(deep=True)
    kitchen = next(rm for rm in bad.rooms if rm.name == "Kitchen")
    master = next(rm for rm in bad.rooms if rm.name == "Master Bedroom")
    # move kitchen flush against the master bedroom
    x0, y0, x1, y1 = master.rect_m
    kitchen.rect_m = [x1, y0, x1 + 2.0, y0 + 2.0]
    v = validate(bad, program)
    assert not v.ok
    assert any("master" in e and "kitchen" in e for e in v.errors)


def test_low_coverage_rejected(layout, program):
    # Coverage is now measured against the footprint (room bounding box), so
    # "low coverage" means a big unassigned void INSIDE that box: two small
    # rooms at opposite corners give a large bbox that is mostly empty.
    bad = layout.model_copy(deep=True)
    r0 = bad.rooms[0].model_copy(deep=True)
    r0.rect_m = [0.0, 0.0, 2.0, 2.0]
    r1 = bad.rooms[1].model_copy(deep=True)
    r1.rect_m = [10.0, 10.0, 12.0, 12.0]
    bad.rooms = [r0, r1]
    v = validate(bad, program)
    assert not v.ok
    assert any("coverage" in e for e in v.errors)


def test_door_on_missing_wall_rejected(layout, program):
    bad = layout.model_copy(deep=True)
    bad.doors[0].wall_id = "does_not_exist"
    v = validate(bad, program)
    assert not v.ok
    assert any("missing wall" in e for e in v.errors)


# --- access graph (Task 5 Phase 2) -------------------------------------------


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_children_bathroom_is_corridor_direct_by_name(program, preset):
    # Guardrail 1 (the Task-2a root-cause fix): the children's hall Bathroom has a
    # DIRECT DOOR to the Corridor and is NOT reached through a bedroom. Assert the
    # SPECIFIC edge by name — global reachability can be green while the Bathroom
    # still transits bed2, so test the exact property directly.
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert "Bathroom" in rooms and "Corridor" in rooms

    # (a) a real >=0.9 m wall between the Bathroom and the Corridor
    assert geom.adjacent(rooms["Bathroom"], rooms["Corridor"], 0.9), (
        "children Bathroom must share a >=0.9 m wall directly with the Corridor"
    )
    # (b) an actual DOOR on that edge (Phase 3: doors ARE the access graph)
    bath_doors = {frozenset((d.from_, d.to)) for d in layout.doors if "Bathroom" in (d.from_, d.to)}
    assert frozenset(("Corridor", "Bathroom")) in bath_doors, (
        f"expected a Corridor<->Bathroom door, got Bathroom doors {bath_doors}"
    )
    # (c) the Bathroom has NO door to a bedroom-as-corridor: its only non-through
    # neighbour is the Corridor, so it is never reached through a bed
    non_through_neighbours = {
        name
        for name, rect in rooms.items()
        if name != "Bathroom"
        and geom.adjacent(rooms["Bathroom"], rect, 0.9)
        and not (standards.ROOMS.get(name) and standards.ROOMS[name].no_through_traffic)
    }
    assert non_through_neighbours == {"Corridor"}, (
        f"Bathroom's only non-through access must be the Corridor, got {non_through_neighbours}"
    )


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_access_tree_routing_invariant(program, preset):
    # The tree itself must route no private room through a habitable one, and no
    # public room through a private one (the two directional holes that valid=True
    # alone missed). Assert on the tree's parent map, across presets:
    #   - a no_through PRIVATE room (bedroom) parent is circulation or its ensuite-of
    #   - a PUBLIC room (Living/Dining/Kitchen) parent is circulation or public
    from app.validator import access_tree, _is_public

    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    rooms = build_layout(r, program).rooms
    names = [rm.name for rm in rooms]
    cats = [rm.category for rm in rooms]
    edges, reached, _root = access_tree(rooms)
    assert len(reached) == len(rooms), "every room must be reachable"
    parent = {b: a for a, b in edges}

    for i, name in enumerate(names):
        if i not in parent:
            continue
        pa = parent[i]
        spec = standards.ROOMS.get(name)
        if spec and spec.no_through_traffic and cats[i] == "private" and not spec.allowed_ensuite_parents:
            assert cats[pa] == "circ", f"{name} (private) entered from {names[pa]}, not circulation"
        if _is_public(name, cats[i]):
            assert cats[pa] == "circ" or _is_public(names[pa], cats[pa]), (
                f"{name} (public) entered from {names[pa]} (private/non-public)"
            )


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_doors_are_exactly_the_access_tree_edges(program, preset):
    # Source-of-truth guarantee: the interior doors are EXACTLY validator's
    # access_tree edges — the door builder consumes that one tree, it does not
    # recompute its own. So a door can never exist that the access graph (which
    # validate_plan gates on) did not produce, and the two can never disagree.
    from app.validator import access_tree

    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    edges, _reached, _root = access_tree(layout.rooms)
    tree = {frozenset((layout.rooms[i].name, layout.rooms[j].name)) for i, j in edges}
    interior = {
        frozenset((d.from_, d.to))
        for d in layout.doors
        if d.from_ != "OUTSIDE" and "Terrace" not in (d.from_, d.to)
    }
    assert interior == tree, f"doors diverge from access tree: {interior ^ tree}"


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_no_interior_door_on_exterior_wall(program, preset):
    # Bug #1, fixed at the root: every interior door hosts on a wall between two
    # real rooms, so no interior door can land on an exterior wall. The only
    # openings on exterior walls are the single front door and the terrace door.
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    exterior_wall_ids = {w.id for w in layout.walls if w.exterior}
    for d in layout.doors:
        if d.from_ == "OUTSIDE" or "Terrace" in (d.from_, d.to):
            continue  # the two deliberate exterior openings
        assert d.wall_id not in exterior_wall_ids, (
            f"interior door {d.from_}->{d.to} landed on an exterior wall {d.wall_id}"
        )


def test_validate_plan_rejects_bedroom_transit(layout, program):
    # A layout where a bedroom is the sole route to another room must fail the
    # access-graph gate. Collapse the Corridor onto the Bathroom's footprint so the
    # only remaining Bathroom neighbours are the two no_through bedrooms.
    assert validate_plan(layout, program) == []  # the real plan is clean
    bad = layout.model_copy(deep=True)
    corridor = next(rm for rm in bad.rooms if rm.name == "Corridor")
    bath = next(rm for rm in bad.rooms if rm.name == "Bathroom")
    # shove the corridor far away so it no longer fronts the Bathroom
    corridor.rect_m = [bath.rect_m[0], bath.rect_m[1] - 0.01, bath.rect_m[2], bath.rect_m[1]]
    errs = validate_plan(bad, program)
    assert any("unreachable" in e for e in errs), errs
