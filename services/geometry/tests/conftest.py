import json
from pathlib import Path

import pytest

from app.models import Program

DATA = Path(__file__).resolve().parents[1] / "data"

# Roomy (20x24, 480 m2) is the PRIMARY fixture as of Task 5: with a live
# circulation zone the geometry/validator/standards invariants all run against
# it. The plot is 24 m deep so the ~16 m house clears the front+rear setback
# envelope (Phase 4). The original 16x12 tight plot is retired from the sweep —
# it can no longer host a connected circulation tree AND (Phase 4) both exceeds
# the 0.5 coverage cap and cannot fit the setbacks — so it survives only as a
# dedicated infeasibility guard (see test_solver.test_tight_is_illegal_brief).
FIXTURES = ["program_roomy.json"]

# Solver time budget per fixture. The invariant tests only need a FEASIBLE layout
# (every hard constraint holds in any feasible solution). Phase 4's setbacks broke
# the plot's translational symmetry, so roomy now even PROVES optimal in ~1.2 s at
# workers=1; this budget is very comfortable. Tests that need the true optimum
# (golden, adjacency deltas) pass their own budget.
SOLVE_TIME_S = {"program_roomy.json": 8.0}


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


@pytest.fixture(scope="session", params=FIXTURES, ids=["roomy"])
def program_file(request) -> str:
    return request.param


@pytest.fixture(scope="session")
def program(program_file) -> Program:
    return Program.model_validate(_load(program_file))


@pytest.fixture(scope="session")
def solve_time_s(program_file) -> float:
    return SOLVE_TIME_S[program_file]


@pytest.fixture(scope="session")
def roomy_program() -> Program:
    # The primary fixture, non-parametrised, for the golden signature, the
    # generate fan-out and the adjacency objective-bookkeeping tests.
    return Program.model_validate(_load("program_roomy.json"))


@pytest.fixture(scope="session")
def tight_program() -> Program:
    # Retired to a single xfail: the illegal 16x12 brief that can no longer host
    # circulation (and, per Phase 4, exceeds the coverage cap).
    return Program.model_validate(_load("program.example.json"))
