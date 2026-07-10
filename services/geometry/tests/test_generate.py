"""End-to-end: >=3 distinct validator-passing variants with correct adjacencies."""

from app import geom
from app.generate import generate
from app.schema_io import validate_layout, validate_program
from app.validator import validate


def _rooms(layout, name):
    return [tuple(r.rect_m) for r in layout.rooms if r.name == name]


def test_at_least_three_distinct_passing_variants(program):
    g = generate(program, n=4)
    assert len(g.variants) >= 3
    presets = {v.layout.preset for v in g.variants}
    assert len(presets) >= 3, "variants should be visually distinct presets"


def test_all_variants_validate_and_are_schema_valid(program):
    g = generate(program, n=4)
    for v in g.variants:
        assert validate(v.layout, program).ok
        assert validate_layout(v.layout.dump()) == []


def test_dod_adjacencies(program):
    g = generate(program, n=1)
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


def test_generation_under_time_budget(program):
    import time

    t = time.time()
    generate(program, n=3)
    assert time.time() - t < 30  # generous CI ceiling; typically ~3s


def test_program_example_schema_valid(program):
    assert validate_program(program.model_dump()) == []
