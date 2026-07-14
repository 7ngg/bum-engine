"""End-to-end: >=3 distinct validator-passing variants with correct adjacencies.

TIGHT-ONLY (uses tight_program, not the parametrised `program`). generate()
fans out PRESETS x seeds = 16 solves; on the roomy plot each solve costs ~5-10s
(translational symmetry — see test_report), so a roomy fan-out is ~2 min and
would blow test_generation_under_time_budget's 30s ceiling. That ceiling and
the >=3-distinct-variant expectations are calibrated to this demo fixture; the
roomy plot's per-solve behaviour is exercised in test_report instead.
"""

from app import geom
from app.generate import generate
from app.schema_io import validate_layout, validate_program
from app.validator import validate


def _rooms(layout, name):
    return [tuple(r.rect_m) for r in layout.rooms if r.name == name]


def test_at_least_three_distinct_passing_variants(tight_program):
    g = generate(tight_program, n=4)
    assert len(g.variants) >= 3
    presets = {v.layout.preset for v in g.variants}
    assert len(presets) >= 3, "variants should be visually distinct presets"


def test_all_variants_validate_and_are_schema_valid(tight_program):
    g = generate(tight_program, n=4)
    for v in g.variants:
        assert validate(v.layout, tight_program).ok
        assert validate_layout(v.layout.dump()) == []


def test_dod_adjacencies(tight_program):
    g = generate(tight_program, n=1)
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

    assert geom.adjacent(kitchen, dining)
    assert geom.adjacent(dining, living)
    assert geom.adjacent(kitchen, laundry)
    assert geom.adjacent(mbed, mbath) or geom.adjacent(mbed, wic)
    # bath between the two children's bedrooms
    assert geom.adjacent(bath, bed2) and geom.adjacent(bath, bed3)
    # master not adjacent to kitchen
    for m in (mbed, mbath, wic):
        assert geom.shared_edge(m, kitchen) is None


def test_generation_under_time_budget(tight_program):
    import time

    t = time.time()
    generate(tight_program, n=3)
    assert time.time() - t < 30  # generous CI ceiling; typically ~3s


def test_program_example_schema_valid(tight_program):
    assert validate_program(tight_program.model_dump()) == []
