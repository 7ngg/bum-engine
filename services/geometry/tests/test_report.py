"""Task 3 outcomes, verified on BOTH fixtures.

The slicer's ceil-snap + dimension cuts + legal-shape tables make every sliced
room meet its per-room Neufert minimum, so:
  - Neufert is now a HARD gate and produces ZERO violations (incl. the children
    Bathroom, whose depth ceil-snaps 2.2 -> 2.5 m);
  - the kitchen_laundry UNION table (its cut axis is a solver var) takes the
    house off the old 1.15 footprint rail (184 m2) down to ~1.10 (176 m2). The
    remaining gap to target is capped by the brief's declared kitchen min_w=4.0
    and the 0.85*target area floor (garage-dominated) — a program-level
    reconciliation (Task 4), not a slicer defect.
"""

import pytest

from app import solver as S
from app.slicer import build_layout
from app.validator import validate


def _solve(program):
    return S.solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)


def _footprint_area(r):
    fx0, fy0, fx1, fy1 = r.footprint_m
    return (fx1 - fx0) * (fy1 - fy0)


@pytest.fixture(scope="session")
def solved(program):
    return _solve(program)


def test_zero_neufert_violations_and_valid(program, solved):
    v = validate(build_layout(solved, program), program)
    assert [e for e in v.errors if "Neufert" in e] == [], v.errors
    assert v.ok, v.errors


def test_footprint_off_the_old_rail(program, solved):
    # The old min_w/min_h hedge pinned the house at the 1.15 rail (184 m2). The
    # union tables drop it below that on both plots.
    ratio = _footprint_area(solved) / program.footprint_target_m2
    assert ratio < 1.15 - 1e-6, ratio


def test_solver_records_kitchen_cut_axis(program, solved):
    # The slicer no longer infers the cut from _side_of; the solver commits to it.
    assert solved.cut_sides.get("kitchen_laundry") in ("N", "S", "E", "W")


def test_habitable_excludes_only_the_garage(program, solved):
    layout = build_layout(solved, program)
    # program estimate (Task 3): every space target except the garage, by id.
    assert program.habitable_area_m2 == sum(
        s.target_m2 for s in program.spaces if s.id != "garage"
    )
    # as-sliced: the Garage room is excluded, nothing else.
    non_garage = sum(
        (r.rect_m[2] - r.rect_m[0]) * (r.rect_m[3] - r.rect_m[1])
        for r in layout.rooms
        if r.name != "Garage"
    )
    assert layout.habitable_area_m2 == pytest.approx(non_garage)
    assert any(r.name == "Garage" for r in layout.rooms)
    assert layout.habitable_area_m2 < _footprint_area(solved)
