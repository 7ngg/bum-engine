"""Validator gate rules — the test oracle."""

import copy

import pytest

from app.generate import generate
from app.models import Layout
from app.solver import solve
from app.slicer import build_layout
from app.validator import validate


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
