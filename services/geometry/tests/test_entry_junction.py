"""THE ENTRY JUNCTION — the architect's two complaints from his review of the
subdivision-variant SVGs (2026-08-05), pinned as tests.

NOTHING HERE IS SHIPPED. All three flags default OFF and the production plan is
unchanged; this file records what was measured and guards the machinery from
rotting. The full round — including why F is not shipped — is in CLAUDE.md under
`validator.py`.

  1. "Eve girisden korodora birbasa kecid olmalidi. Adam mecburduki mudrooma
     kecsin sonra karidora getsin. Mudroom ve foye ikiside coridora kecmelidi."
     -- there must be a DIRECT passage from the entry to the corridor; today a
     person is forced through the Mudroom; BOTH entry rooms must connect.
  2. "Foyeden direk bedrooma kecid cox menasizdir."
     -- a door from the Foyer straight into a bedroom is senseless.

Complaint 1 is a GEOMETRY defect and costs the footprint rung (F is proven
INFEASIBLE at 201.50, 208.00 and 216.00; its only reachable footprint is 217.00,
above the architect's own 45% target). Complaint 2 is a PARENT-SELECTION defect
and costs nothing. The two tests below assert exactly that split.
"""

import pytest

from app import geom, slicer, solver, validator
from app.validator import ACCESS_DOOR_M, access_tree
from app.models import Room


def _tree(rooms):
    edges, reached, root = access_tree(rooms)
    depth = {root: 0}
    parent = {}
    for a, b in edges:
        depth[b] = depth[a] + 1
        parent[b] = a
    names = [r.name for r in rooms]
    return names, edges, parent, depth, root


# --------------------------------------------------------------------------
# complaint 1 + 2, asserted POSITIVELY on the real shipped plan
# --------------------------------------------------------------------------
@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_entry_junction_KNOWN_LIVE_DEFECT(roomy_program, preset):
    """Both complaints, stated as the facts they are, so a FIX shows up as a
    failure here rather than as silence.

    Asserted positively for the same reason placement.KNOWN_LIVE_DEFECT_CODES
    is: a defect that is only ever described in a comment gets fixed by accident
    and nobody notices which change did it.
    """
    r = solver.solve(roomy_program, preset, seed=1, time_limit_s=60, workers=1)
    assert r.feasible and r.status == "OPTIMAL"
    lay = slicer.build_layout(r, roomy_program)
    R = {rm.name: tuple(rm.rect_m) for rm in lay.rooms}
    corr = R["Corridor"]

    # COMPLAINT 1. The corridor's whole 1.50 m top edge lands on the Mudroom, so
    # the Foyer touches it at a corner point only -- zero shared wall.
    foyer_edge = geom.shared_edge(corr, R["Foyer"])
    assert foyer_edge is None or foyer_edge.length < ACCESS_DOOR_M, (
        "the Foyer now reaches the corridor -- complaint 1 is FIXED; update this "
        "test, CLAUDE.md's entry-junction block, and check the footprint rung"
    )
    mud_edge = geom.shared_edge(corr, R["Mudroom"])
    assert mud_edge is not None and mud_edge.length >= ACCESS_DOOR_M
    # and the arithmetic that makes complaint 1 expensive: the shared boundary
    # between corridor and entry zone IS the corridor's width.
    assert min(corr[2] - corr[0], corr[3] - corr[1]) < 2 * ACCESS_DOOR_M

    # COMPLAINT 2. Bedroom 3 hangs off the Foyer at depth 1 ...
    names, _edges, parent, depth, _root = _tree(lay.rooms)
    b3 = names.index("Bedroom 3")
    assert names[parent[b3]] == "Foyer" and depth[b3] == 1, (
        "Bedroom 3 no longer hangs off the Foyer -- complaint 2 is FIXED"
    )
    assert {"Foyer", "Bedroom 3"} in [{d.from_, d.to} for d in lay.doors]
    # ... WHILE HOLDING A QUALIFYING CORRIDOR WALL. This is the whole point: the
    # geometry already offers the right parent, so complaint 2 is free to fix.
    assert geom.adjacent(corr, R["Bedroom 3"], ACCESS_DOOR_M)


# --------------------------------------------------------------------------
# P, on synthetic geometry so the rule is tested and not the packing
# --------------------------------------------------------------------------
def _room(name, cat, rect):
    return Room(name=name, category=cat, zone="entry", rect_m=list(rect))


# Mudroom | Foyer along the top, a Corridor spanning below them, and a Bedroom
# down the east side touching BOTH the root Foyer and the Corridor over a full
# door width -- the situation the rule is about, and the one the shipped plan is
# in except that there the Foyer misses the corridor.
_PLAN = [
    _room("Foyer", "circ", (3, 3, 6, 6)),
    _room("Mudroom", "service", (0, 3, 3, 6)),
    _room("Corridor", "circ", (0, 0, 6, 3)),
    _room("Bedroom 3", "private", (6, 0, 10, 6)),
]


def test_P_reparents_the_bedroom_and_the_default_does_not(monkeypatch):
    names, _e, parent, depth, _r = _tree(_PLAN)
    b3 = names.index("Bedroom 3")
    assert names[parent[b3]] == "Foyer" and depth[b3] == 1  # today

    monkeypatch.setattr(validator, "_PREFER_CIRCULATION_PARENT", True)
    names, _e, parent, depth, _r = _tree(_PLAN)
    b3 = names.index("Bedroom 3")
    assert names[parent[b3]] == "Corridor", "P must hand the bedroom to the corridor"
    # every room still reachable -- the deferral has a fallback pass precisely
    # so it can never strand anything
    _edges, reached, _root = access_tree(_PLAN)
    assert len(reached) == len(_PLAN)


def test_P_never_strands_a_bedroom_whose_only_circulation_is_the_root(monkeypatch):
    """A room whose ONE circulation neighbour is the root must keep the root as
    its parent -- the deferral is a preference, never a veto."""
    plan = [
        _room("Foyer", "circ", (0, 0, 4, 4)),
        _room("Bedroom 3", "private", (4, 0, 8, 4)),
    ]
    monkeypatch.setattr(validator, "_PREFER_CIRCULATION_PARENT", True)
    names, _e, parent, _d, _r = _tree(plan)
    assert names[parent[names.index("Bedroom 3")]] == "Foyer"
    _edges, reached, _root = access_tree(plan)
    assert len(reached) == 2


# --------------------------------------------------------------------------
# the durable rule for complaint 2
# --------------------------------------------------------------------------
def test_entry_parented_bedroom_is_an_error_when_a_corridor_wall_exists(monkeypatch):
    monkeypatch.setattr(validator, "_PREFER_CIRCULATION_PARENT", True)
    errors, warnings = [], []
    names = [r.name for r in _PLAN]
    cats = [r.category for r in _PLAN]
    rects = [tuple(r.rect_m) for r in _PLAN]
    # hand it the OFFENDING tree directly (Foyer -> Bedroom 3), not the one P builds
    edges = [(names.index("Foyer"), names.index("Bedroom 3"))]
    validator._check_entry_parented_private(rects, names, cats, edges, errors, warnings)
    assert len(errors) == 1 and "Bedroom 3" in errors[0] and "Foyer" in errors[0]
    assert warnings == []


def test_entry_parented_bedroom_is_only_a_warning_with_no_corridor(monkeypatch):
    """A small house with no corridor must not be hard-failed for a topology it
    cannot avoid."""
    monkeypatch.setattr(validator, "_PREFER_CIRCULATION_PARENT", True)
    plan = [
        _room("Foyer", "circ", (0, 0, 4, 4)),
        _room("Bedroom 3", "private", (4, 0, 8, 4)),
    ]
    errors, warnings = [], []
    names = [r.name for r in plan]
    validator._check_entry_parented_private(
        [tuple(r.rect_m) for r in plan], names, [r.category for r in plan],
        [(0, 1)], errors, warnings,
    )
    assert errors == []
    assert len(warnings) == 1 and "Bedroom 3" in warnings[0]


# --------------------------------------------------------------------------
# F: the price, recorded
# --------------------------------------------------------------------------
@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_F_is_infeasible_at_the_shipped_rung(roomy_program, preset, monkeypatch):
    """F (Foyer<->Corridor at room level) cannot pack at 208.

    This is the finding that stopped the round, and it is GEOMETRY, not the
    conservative Foyer core: re-measured with the core widened to the whole
    entry-minus-Mudroom remainder it is equally infeasible (CLAUDE.md). F's only
    reachable footprint anywhere in [201.50, 220.00] is 217.00 m2 -- 45.21% site
    coverage, above the architect's own 45% target.
    """
    monkeypatch.setattr(solver, "_ENTRY_FOYER_CORRIDOR", True)
    monkeypatch.setattr(solver, "_ENTRY_MUDROOM_CORRIDOR", True)
    orig = solver._Footprint

    class _Pinned(orig):
        def __init__(self, m, W, H):
            super().__init__(m, W, H)
            m.Add(self.area == 832)  # 208.00 m2 on the 0.5 m grid

    monkeypatch.setattr(solver, "_Footprint", _Pinned)
    r = solver.solve(roomy_program, preset, seed=1, time_limit_s=120, workers=1)
    assert r.status == "INFEASIBLE", (
        f"F now packs at 208 ({r.status}) -- re-run the rung sweep; the reason "
        "this round did not ship may no longer hold"
    )


@pytest.mark.parametrize("preset", ["gW_eN", "gE_eN"])
def test_M_alone_costs_nothing_but_is_not_byte_identical(roomy_program, preset, monkeypatch):
    """Mudroom<->Corridor is already delivered by _force_backbone_reaches_foyer,
    so M buys nothing -- same objective, same zone SHAPES, same validity.

    IT IS NOT BYTE-IDENTICAL, THOUGH, AND THE REASON IS WORTH KEEPING. On gW_eN
    the whole house comes out translated 3.00 m WEST: the plot is 20 m wide with
    a 2 m side setback, so a 13 m footprint has 3 m of legal slack in x and the
    objective never references absolute x. That makes x-translation a free
    symmetry here, and any change to the model at all lets CP-SAT break the tie
    differently. gE_eN does not move. So this asserts SHAPE equality, not
    coordinate equality -- a coordinate assertion would be testing which of two
    equally optimal positions the solver happened to pick.
    """
    base = solver.solve(roomy_program, preset, seed=1, time_limit_s=60, workers=1)
    monkeypatch.setattr(solver, "_ENTRY_MUDROOM_CORRIDOR", True)
    with_m = solver.solve(roomy_program, preset, seed=1, time_limit_s=60, workers=1)

    assert with_m.objective == base.objective
    shape = lambda res: sorted(
        (z.zone, round(z.x1 - z.x0, 3), round(z.y1 - z.y0, 3)) for z in res.rects
    )
    assert shape(with_m) == shape(base)
    # and the whole thing is a rigid translation, not a repack
    off = {
        (round(a.x0 - b.x0, 3), round(a.y0 - b.y0, 3))
        for a, b in zip(sorted(with_m.rects, key=lambda z: z.zone),
                        sorted(base.rects, key=lambda z: z.zone))
    }
    assert len(off) == 1, f"M repacked rather than translated: {off}"
    assert validator.validate(slicer.build_layout(with_m, roomy_program), roomy_program).ok
