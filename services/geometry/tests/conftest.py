import json
from pathlib import Path

import pytest

from app.models import Program

DATA = Path(__file__).resolve().parents[1] / "data"

# The geometry/validator/standards invariants run against two plots with an
# IDENTICAL program: the original over-constrained 16x12 (tight) and a roomy
# 20x15 (53% site coverage). Parametrising over both separates fixture
# artifacts from real code defects. The expensive M2 generate fan-out, the
# adjacency objective-bookkeeping tests and the golden signature stay tight-only
# (see their files). The side-by-side lives in tests/test_report.py.
FIXTURES = ["program.example.json", "program_roomy.json"]

# Per-fixture solver time budget. The roomy plot is ~50-100x slower to solve to
# OPTIMAL: with the footprint free to slide in the setback, CP-SAT finds the
# optimum in <1s but then spends ~9s PROVING it (translational symmetry — a hard
# corner-anchor collapses it to 0.3s; see test_report's module docstring). The
# geometry-invariant tests only need a FEASIBLE layout (every hard constraint
# holds in any feasible solution), so roomy is capped low to keep the suite
# quick; tests that need the true optimum pass their own budget.
SOLVE_TIME_S = {"program.example.json": 12.0, "program_roomy.json": 3.0}


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


@pytest.fixture(scope="session", params=FIXTURES, ids=["tight", "roomy"])
def program_file(request) -> str:
    return request.param


@pytest.fixture(scope="session")
def program(program_file) -> Program:
    return Program.model_validate(_load(program_file))


@pytest.fixture(scope="session")
def solve_time_s(program_file) -> float:
    return SOLVE_TIME_S[program_file]


@pytest.fixture(scope="session")
def tight_program() -> Program:
    # NOT parametrised: the golden signature and the M2 generate fan-out are
    # calibrated to this exact plot.
    return Program.model_validate(_load("program.example.json"))
