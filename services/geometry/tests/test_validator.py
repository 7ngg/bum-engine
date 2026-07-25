"""Validator gate rules — the test oracle."""

import copy

import pytest

from app import geom, standards
from app.generate import generate
from app.models import Layout
from app.solver import KITCHEN_FALLBACK_TAG, solve
from app.slicer import build_layout
from app.validator import ACCESS_DOOR_M, validate, validate_plan


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


# --- kitchen-direct-to-corridor (Task 6) --------------------------------------
# Client-reported pathology: the Kitchen was only reachable via the
# Dining->Living chain, routing kitchen traffic through the living room. Fixed
# by a hard corridor<->kitchen_laundry wall in solver.py, feasible on roomy
# once its footprint moved to 184 m2 (two independent sweeps found that
# threshold). These three tests cover: the invariant itself, that the
# constraint (not the geometry) is what delivers it, and that a footprint too
# small for it gets a disclosed fallback instead of a silently flawed plan.


def _kitchen_ancestors(layout) -> list[str]:
    from app.validator import access_tree

    names = [rm.name for rm in layout.rooms]
    edges, reached, _root = access_tree(layout.rooms)
    kidx = names.index("Kitchen")
    assert kidx in reached, "Kitchen must be reachable at all"
    parent_of = {c: p for p, c in edges}
    ancestors = []
    cur = kidx
    while cur in parent_of:
        cur = parent_of[cur]
        ancestors.append(names[cur])
    return ancestors


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_kitchen_direct_to_corridor(program, preset):
    # On roomy (184 m2), the hard constraint must deliver a REAL shared wall
    # between Kitchen and Corridor, and Kitchen's access-tree path must never
    # route through Living.
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    assert r.kitchen_direct
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert geom.adjacent(rooms["Kitchen"], rooms["Corridor"], ACCESS_DOOR_M), (
        "Kitchen must share a real corridor wall"
    )
    ancestors = _kitchen_ancestors(layout)
    assert "Living" not in ancestors, f"Kitchen routed through Living: {ancestors}"
    assert validate_plan(layout, program) == []


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_kitchen_direct_constraint_is_load_bearing(program, preset):
    # Proves the constraint (not a coincidence of the 184 m2 geometry) is what
    # delivers kitchen-direct: with it OFF, UNFLAGGED (no disclosure -- unlike
    # generate.py's fallback, which always appends KITCHEN_FALLBACK_TAG), the
    # plan reverts to the Dining->Living chain and the gate must reject it.
    # Stops a silent revert of the constraint later.
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1, force_kitchen_direct=False)
    assert r.feasible, "dropping kitchen-direct should still solve on roomy"
    assert not r.kitchen_direct
    layout = build_layout(r, program)
    ancestors = _kitchen_ancestors(layout)
    assert "Living" in ancestors, (
        f"expected the unconstrained solve to revert to routing via Living, got {ancestors}"
    )
    errors = validate_plan(layout, program)
    assert any("Kitchen" in e and "Living" in e for e in errors), (
        f"expected the kitchen-direct gate to reject the unflagged fallback, got {errors}"
    )


def test_kitchen_direct_fallback_flags_area_limitation(program):
    # A footprint too small for kitchen-direct must not ship a flawed plan
    # SILENTLY: generate.py retries without the constraint and flags the
    # result. Uses 160 m2 (roomy's old target, proven infeasible for
    # kitchen-direct by two independent sweeps), not the `program` fixture
    # (now 184 m2, where kitchen-direct is feasible and no fallback fires).
    small = program.model_copy(update={"footprint_target_m2": 160.0})
    g = generate(small, n=2, seeds=[1])
    assert len(g.variants) >= 1, "the fallback must still deliver a plan, not nothing"
    for v in g.variants:
        assert any(w.startswith(KITCHEN_FALLBACK_TAG) for w in v.layout.warnings), (
            "fallback plan must carry the area-limitation diagnostic"
        )
        assert validate(v.layout, small).ok, "the flagged fallback must still pass the gate"


# --- corridor-to-Master-Bedroom, room level (Step 1 of the master-window fix) -
# The solver only guarantees a ZONE-level corridor<->master_suite touch
# (_force_vertical_overlap); which SUB-ROOM receives it is the slicer's call.
# _slice_master now reads the solver's recorded corridor_sides["master_suite"]
# so the Bedroom (not just the Bathroom/Closet service strip) is always the
# room that fronts the corridor. This must hold at the ROOM level, not just
# "some master-suite room touches the corridor" — a plan where the corridor
# only fronts the Master Bathroom or Walk-in Closet leaves the Bedroom itself
# unreachable and must fail this test.


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_master_bedroom_direct_to_corridor(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert geom.adjacent(rooms["Corridor"], rooms["Master Bedroom"], ACCESS_DOOR_M), (
        "Corridor must share a real wall with Master Bedroom specifically, "
        "not merely with some room in the master suite"
    )


# --- corridor-to-Kitchen, room level (Step 1b of the master-window fix) ------
# Same gap as master_suite, still open for kitchen_laundry until this commit:
# the solver only guarantees a ZONE-level corridor<->kitchen_laundry touch (the
# force_kitchen_direct block in solver.py); which SUB-ROOM receives it is the
# slicer's call. At footprint 208 on the (pre-fix) roomy fixture this already
# shipped broken -- the forced touch landed on the Laundry sub-room, and
# validate_plan rejected the plan with "Kitchen is reached via Living". This
# test pins the room-level guarantee at 184 (where it happens to already hold)
# so it can't silently regress, and the 208 case is covered directly by
# tests/test_slicer.py's same-axis-override unit test since roomy's fixture
# footprint is 184, not 208.


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_kitchen_direct_to_corridor_room_level(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert geom.adjacent(rooms["Corridor"], rooms["Kitchen"], ACCESS_DOOR_M), (
        "Corridor must share a real wall with Kitchen specifically, "
        "not merely with some room in the kitchen_laundry zone (e.g. Laundry)"
    )


# --- Master Bedroom exterior wall + window (the master-bedroom-window fix) ---
# Prior commits (5da1490, 66f3506) guaranteed the corridor fronts the Master
# BEDROOM room, not just the master_suite zone or its service strip -- but
# left the Bedroom itself with no guarantee of ever reaching a real exterior
# wall. On the roomy 184 m2 plan the Bedroom was fully landlocked (no window
# -> SNiP/Posobie cl. 2.8 daylight violation, architect-confirmed). This
# commit makes the master corridor attachment a 4-way disjunction (any side,
# not just E/W) and adds a bedroom-AWARE habitable-perimeter constraint for
# master_suite only (see _force_master_corridor_overlap /
# _require_master_bedroom_perimeter in solver.py), so the Bedroom's exterior
# wall is a CONSTRAINT, not left to the objective's taste.
#
# test_master_bedroom_direct_to_corridor above already covers "Corridor
# shares >= ACCESS_DOOR_M with Master Bedroom, room-to-room" -- one of the
# four guarantees this commit buys is already guarded by that existing test,
# so it is not duplicated here.


def _true_perimeter_contact(rect: geom.Rect, all_rooms: list[geom.Rect]) -> float:
    """Facade length of `rect` against the TRUE building perimeter (the
    bounding box of every room), not the rasterized wall.exterior flag -- a
    rasterized "exterior" wall can face an interior void the slicer didn't
    assign, which is not a real facade."""
    fx0 = min(r[0] for r in all_rooms)
    fy0 = min(r[1] for r in all_rooms)
    fx1 = max(r[2] for r in all_rooms)
    fy1 = max(r[3] for r in all_rooms)
    x0, y0, x1, y1 = rect
    total = 0.0
    if abs(y0 - fy0) < geom.EPS:
        total += x1 - x0
    if abs(y1 - fy1) < geom.EPS:
        total += x1 - x0
    if abs(x0 - fx0) < geom.EPS:
        total += y1 - y0
    if abs(x1 - fx1) < geom.EPS:
        total += y1 - y0
    return total


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_master_bedroom_has_true_perimeter_facade(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rects = [tuple(rm.rect_m) for rm in layout.rooms]
    mb = next(tuple(rm.rect_m) for rm in layout.rooms if rm.name == "Master Bedroom")
    facade = _true_perimeter_contact(mb, rects)
    assert facade >= 1.5, f"Master Bedroom must have a real exterior wall, got {facade:.2f} m"


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_master_bedroom_has_window(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    n = len([w for w in layout.windows if w.room == "Master Bedroom"])
    assert n >= 1, "Master Bedroom must have at least one window"


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_master_bedroom_fix_validates_fully_green(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    v = validate(layout, program)
    assert v.ok and v.errors == [], v.errors


# --- KNOWN REGRESSION, documented not fixed: room-level Kitchen<->Dining -----
# Measured (Step 2 sweep, /tmp/step2_corridor_any_side_habitable_perimeter.patch)
# and confirmed again on this commit: at the roomy 184 m2 fixture, BOTH
# presets, Kitchen and Dining no longer share a wall at the room level --
# Laundry sits between them (a 2.0 m gap). Root cause: 66f3506 made
# _slice_kitchen let the corridor-facing side win over the dining-facing side
# when the two conflict; this commit's repacking (the corridor is now free to
# front master_suite from the north) changes kitchen_laundry's own corridor
# side into conflict with its dining side at 184, where before this commit it
# wasn't. REQUIRED_ADJ (kitchen_laundry<->dining) is enforced at the ZONE
# level only, so validate() cannot see this and reports fully green anyway.
# Not fixed here -- see the commit message. This xfail exists so the
# regression is VISIBLE in the suite, not silently absent. If this
# unexpectedly passes, strict xfail turns that into a failure -- report it
# immediately, it would mean the regression did not materialise here.
@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN REGRESSION (master-bedroom-window fix): Kitchen<->Dining no "
        "longer share a wall at the room level on the roomy 184 m2 fixture -- "
        "Laundry sits between them. REQUIRED_ADJ is zone-level only so "
        "validate() doesn't catch it. Not fixed by this commit; see its "
        "message for the measured gap."
    ),
)
@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_kitchen_dining_room_level_KNOWN_REGRESSION(program, preset):
    r = solve(program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible
    layout = build_layout(r, program)
    rooms = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    assert geom.adjacent(rooms["Kitchen"], rooms["Dining"], ACCESS_DOOR_M), (
        "Kitchen must share a real wall with Dining at the room level"
    )
