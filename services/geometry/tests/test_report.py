"""Task 3 outcomes, re-verified on the roomy fixture (the tight plot is retired).

The slicer's ceil-snap + dimension cuts + legal-shape tables make every sliced
room meet its per-room Neufert minimum, so Neufert is a HARD gate with ZERO
violations (incl. the children Bathroom, whose depth ceil-snaps 2.2 -> 2.5 m).

Task 5 note: the footprint packs somewhere inside [FOOTPRINT_LO, FOOTPRINT_HI]
of target_area_m2, not pinned to a specific ratio -- which point in the band it
lands on is an artifact of whichever global optimum the solver finds, and has
already moved twice: ~1.130 (208 m2) after Task 5/6 added the live circulation
zone and the kitchen-direct constraint, then ~1.087 (200 m2) after b990700
added the master-bedroom-perimeter constraint, which changed the optimal
packing enough to pull the footprint closer to target. Assert band membership,
not the exact ratio.
"""

import pytest

from app import solver as S
from app.slicer import build_layout
from app.validator import validate


def _solve(program):
    # Phase 4's setback envelope broke the plot's translational symmetry, so the
    # 20x24 roomy solve PROVES optimal in ~1.2 s at workers=1; 12 s is ample.
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


def test_footprint_within_band(program, solved):
    # See module docstring: the exact ratio is a packing artifact that has
    # already moved once (1.130 -> 1.087) when b990700 changed which global
    # optimum the solver lands on; band membership is the real invariant.
    ratio = _footprint_area(solved) / program.footprint_target_m2
    assert S.FOOTPRINT_LO <= ratio <= S.FOOTPRINT_HI, ratio


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
