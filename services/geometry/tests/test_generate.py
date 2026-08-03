"""End-to-end: >=3 distinct validator-passing variants with correct adjacencies.

ROOMY-ONLY (uses roomy_program, not the parametrised `program`). generate() fans
out PRESETS x seeds; the tight plot can no longer host circulation, and only the
two entry-NORTH presets (gW_eN, gE_eN) produce a VALID plan on roomy once the
master bedroom must open off the corridor (the entry-west pair fail validation —
see test_solver). So >=3 distinct variants come from those two presets plus seed
variation. The access-graph gate (validate_plan) also runs inside validate().
"""

import pytest

from app import geom
from app.generate import generate
from app.schema_io import validate_layout, validate_program
from app.validator import validate


def _rooms(layout, name):
    return [tuple(r.rect_m) for r in layout.rooms if r.name == name]


def test_at_least_three_distinct_passing_variants(roomy_program):
    g = generate(roomy_program, n=4)
    assert len(g.variants) >= 3
    presets = {v.layout.preset for v in g.variants}
    # Only 2 presets (gW_eN, gE_eN) yield valid plans once the master must open off
    # the corridor (Task 5); distinctness comes from those two plus seed variation.
    assert len(presets) >= 2, "variants should be visually distinct"


def test_all_variants_validate_and_are_schema_valid(roomy_program):
    g = generate(roomy_program, n=4)
    for v in g.variants:
        assert validate(v.layout, roomy_program).ok
        assert validate_layout(v.layout.dump()) == []


def test_dod_adjacencies(roomy_program):
    g = generate(roomy_program, n=1)
    lay = g.variants[0].layout
    kitchen = _rooms(lay, "Kitchen")[0]
    dining = _rooms(lay, "Dining")[0]
    living = _rooms(lay, "Living")[0]
    laundry = _rooms(lay, "Laundry")[0]
    mbed = _rooms(lay, "Master Bedroom")[0]
    mbath = _rooms(lay, "Master Bathroom")[0]
    wic = _rooms(lay, "Walk-in Closet")[0]
    bed2 = _rooms(lay, "Bedroom 2")[0]
    bed3 = _rooms(lay, "Bedroom 3")[0]
    bath = _rooms(lay, "Bathroom")[0]

    # Kitchen<->Dining is checked separately below
    # (test_dod_kitchen_dining_adjacency_KNOWN_REGRESSION, xfail) -- it no
    # longer holds as of b990700; see that test for why.
    assert geom.adjacent(dining, living)
    assert geom.adjacent(kitchen, laundry)
    assert geom.adjacent(mbed, mbath) or geom.adjacent(mbed, wic)
    # bath between the two children's bedrooms
    assert geom.adjacent(bath, bed2) and geom.adjacent(bath, bed3)
    # master not adjacent to kitchen
    for m in (mbed, mbath, wic):
        assert geom.shared_edge(m, kitchen) is None


def test_dod_kitchen_dining_adjacency_holds_in_the_shipped_variant(roomy_program):
    """UN-XFAILED 2026-08-03. Was a strict xfail recording that Kitchen<->Dining
    do not share a room-level wall in the variant generate() ships first.

    WHAT CHANGED, precisely -- and it is NOT that the underlying defect was
    fixed: the architect's through-living ruling (see
    validator._living_substitutes_for_corridor) admitted gW_eW, which was being
    rejected on a false positive of the old ancestor-chain rule. gW_eW scores
    638.40625 against gW_eN's 534.65625, so it is now the top-ranked variant --
    and in gW_eW's packing the Kitchen and the Dining room DO share a wall.

    The per-preset defect is untouched and still recorded, still strict-xfail,
    in the two places that assert it against a FIXED preset rather than against
    whatever generate() ranks first:
        tests/test_golden.py::test_golden_kitchen_dining_room_level_KNOWN_REGRESSION
        tests/test_validator.py::test_kitchen_dining_room_level_KNOWN_REGRESSION
    Both still xfail on gW_eN/gE_eN. So this test is now a statement about the
    plan we actually SHIP, and those two remain the statement about the model.

    workers=1 is pinned deliberately: this asserts a property of the top-RANKED
    variant, so it must not be exposed to the thread-portfolio nondeterminism
    that generate()'s production workers=8 default carries (see CLAUDE.md).
    """
    g = generate(roomy_program, n=1, workers=1)
    lay = g.variants[0].layout
    assert lay.preset == "gW_eW", (
        f"the ranking this test depends on moved: top variant is {lay.preset!r}"
    )
    kitchen = _rooms(lay, "Kitchen")[0]
    dining = _rooms(lay, "Dining")[0]
    assert geom.adjacent(kitchen, dining)


def test_generation_under_time_budget(roomy_program):
    import time

    t = time.time()
    generate(roomy_program, n=3)
    # Phase 4's setback envelope broke the 20x24 plot's translational symmetry, so
    # the full PRESETS x seeds fan-out (each solve now PROVES optimal in ~1.2 s)
    # completes in ~12 s — back under the original ceiling that the setback-less
    # interim plot had blown through.
    assert time.time() - t < 60


def test_program_example_schema_valid(roomy_program):
    assert validate_program(roomy_program.model_dump()) == []
