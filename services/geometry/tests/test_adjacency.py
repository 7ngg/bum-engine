"""Program.adjacency is now live: solver.py reads it instead of hardcoded
zones.py constants. REQUIRED_ADJ stays hard and non-LLM-controllable."""

"""TIGHT-ONLY: these check the solver's adjacency objective bookkeeping (empty
adjacency reproduces the defaults; a new pair moves the objective; an
already-required pair doesn't double-count; a contradictory avoid degrades
gracefully). That's plot-independent solver logic and needs the TRUE optimum
for the objective comparisons, so it runs on the fast tight fixture only —
the roomy plot would add ~10s/solve for no new signal (see test_report)."""

import copy
import json
from pathlib import Path

import pytest

from app.models import Program
from app.solver import solve

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def raw_program() -> dict:
    return json.loads((DATA / "program.example.json").read_text())


def test_empty_adjacency_reproduces_default_objective(raw_program, tight_program):
    program = tight_program
    # program.example.json's adjacency is spelled out explicitly, but it's
    # content-identical to zones.DEFAULT_DESIRABLE/DEFAULT_SEMI/DEFAULT_AVOID.
    # A program with adjacency stripped entirely must solve to the same
    # objective via the fallback path.
    stripped = copy.deepcopy(raw_program)
    stripped["adjacency"] = {}
    prog_stripped = Program.model_validate(stripped)

    base = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    via_defaults = solve(prog_stripped, "gW_eN", seed=1, time_limit_s=12, workers=1)

    assert base.feasible and via_defaults.feasible
    assert via_defaults.objective == base.objective


def test_extra_desirable_pair_changes_objective(raw_program, tight_program):
    program = tight_program
    # office/master_suite share no wall requirement today (neither required
    # nor in the default desirable/semi lists) — rewarding it is new information.
    base = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)

    augmented = copy.deepcopy(raw_program)
    augmented["adjacency"]["desirable"].append(["office", "master_suite"])
    prog2 = Program.model_validate(augmented)
    r2 = solve(prog2, "gW_eN", seed=1, time_limit_s=12, workers=1)

    assert base.feasible and r2.feasible
    assert r2.objective != base.objective


def test_required_adj_pair_in_desirable_does_not_inflate_objective(raw_program, tight_program):
    program = tight_program
    # kitchen_laundry-dining is hard-required (zones.REQUIRED_ADJ). Listing it
    # again under desirable must not double-count: solver.py's dedupe against
    # REQUIRED_ADJ should make this a no-op on the objective.
    base = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)

    augmented = copy.deepcopy(raw_program)
    augmented["adjacency"]["desirable"].append(["kitchen_laundry", "dining"])
    prog2 = Program.model_validate(augmented)
    r2 = solve(prog2, "gW_eN", seed=1, time_limit_s=12, workers=1)

    assert r2.feasible
    assert r2.objective == base.objective


def test_hallucinated_avoid_pair_falls_back_and_warns(raw_program):
    # dining-living is hard-required (REQUIRED_ADJ). Also forbidding it via
    # avoid is a direct contradiction -> guaranteed infeasible on attempt 1.
    bad = copy.deepcopy(raw_program)
    bad["adjacency"]["avoid"].append(["dining", "living"])
    prog = Program.model_validate(bad)

    r = solve(prog, "gW_eN", seed=1, time_limit_s=10, workers=1)

    assert r.feasible, "must degrade gracefully, never hard-fail the request"
    assert any("infeasible" in w and "avoid" in w for w in r.warnings)
    assert any("dining" in w and "living" in w for w in r.warnings)
