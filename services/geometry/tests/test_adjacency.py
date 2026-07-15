"""Program.adjacency is now live: solver.py reads it instead of hardcoded
zones.py constants. REQUIRED_ADJ stays hard and non-LLM-controllable."""

"""ROOMY-ONLY: these check the solver's adjacency objective bookkeeping (empty
adjacency reproduces the defaults; a new pair moves the objective; an
already-required pair doesn't double-count; a contradictory avoid degrades
gracefully). That's plot-independent solver logic and needs the TRUE optimum for
the objective comparisons; the tight plot is retired (it can no longer host
circulation), so these run on roomy with a budget long enough to prove OPTIMAL.
roomy's adjacency block is content-identical to the tight one."""

import copy
import json
from pathlib import Path

import pytest

from app.models import Program
from app.solver import solve

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def raw_program() -> dict:
    return json.loads((DATA / "program_roomy.json").read_text())


def test_empty_adjacency_reproduces_default_objective(raw_program, roomy_program):
    program = roomy_program
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


def test_extra_desirable_pair_changes_objective(raw_program, roomy_program):
    program = roomy_program
    # office/children (neither required nor a default desirable/semi pair) is a
    # REALIZABLE reward: on roomy gW_eN both zones pack into the NE quadrant and
    # share a wall, so rewarding their adjacency is counted and the objective
    # moves (+40, the desirable weight). NB the reward does NOT relocate zones:
    # Task 5's hard access constraints pin the whole plan, so a soft desirable can
    # only score a *coincidentally realized* adjacency, never pull two zones
    # together (an audit found EVERY non-adjacent pair is a +0 no-op on this plot).
    # That is why office/dining — the plausible-looking alternative — is the WRONG
    # pair: office packs north, dining south, and no reward can move them, so it
    # would be a silent no-op that never trips this assert.
    base = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)

    augmented = copy.deepcopy(raw_program)
    augmented["adjacency"]["desirable"].append(["office", "children"])
    prog2 = Program.model_validate(augmented)
    r2 = solve(prog2, "gW_eN", seed=1, time_limit_s=12, workers=1)

    assert base.feasible and r2.feasible
    assert r2.objective != base.objective


def test_required_adj_pair_in_desirable_does_not_inflate_objective(raw_program, roomy_program):
    program = roomy_program
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
