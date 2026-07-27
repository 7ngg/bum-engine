"""Terrace extent and connections.

The architect asked for a bigger terrace with access from more than one room
("Boyuk ver, ofis ve master bedroomdanda cixis olardi"). The design basis:

  - SNiP 2.08.01-89, Posobie: veranda depth recommended >= 1.8 m; the preferred
    solutions give access from the kitchen, or from the kitchen and the common
    room together; for ground-floor units, connect the adjacent outdoor area
    from the common room and kitchen.
  - Neufert, Architects' Data: outdoor dining spaces belong on the wind-
    protected sunny side in front of the dining or living room, minimum width
    3000 mm with a bench seat along one wall; the worked figure shows dining
    AND living both opening onto the terrace.

The DEPTH was already correct at TERRACE_DEPTH_M = 3.0 (clears both minimums).
The deficiency was EXTENT and CONNECTIONS: it spanned the Living room alone and
served one door. These tests pin the corrected behaviour.

Note the Master Bedroom is NOT reachable by this change: it does not touch the
south facade in either preset, because the corridor attaches to master_suite
from the north, which puts the bathroom/closet service strip south. Giving it
terrace access is a solver change, not a slicer one.
"""

import pytest

from app import standards
from app.slicer import TERRACE_DEPTH_M, build_layout
from app.solver import solve

PRESETS = ["gW_eN", "gE_eN"]

# SNiP 2.08.01-89 Posobie: recommended minimum veranda depth.
SNIP_MIN_VERANDA_DEPTH_M = 1.8


def _layout(roomy_program, preset):
    r = solve(roomy_program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible, f"{preset} infeasible"
    return build_layout(r, roomy_program)


def _spanned(layout):
    """Rooms whose south edge lies on the terrace's north edge, within its x-span."""
    tx0, _, tx1, ty1 = layout.terrace.rect_m
    return [
        rm
        for rm in layout.rooms
        if abs(rm.rect_m[1] - ty1) < 1e-9
        and rm.rect_m[0] >= tx0 - 1e-9
        and rm.rect_m[2] <= tx1 + 1e-9
    ]


@pytest.mark.parametrize("preset", PRESETS)
def test_terrace_spans_habitable_south_rooms_only(roomy_program, preset):
    """Every qualifying room on the south facade is spanned; no wet/service one is.

    Qualifying = needs daylight (standards.requires_exterior_wall) and is not a
    service room. That is the predicate _opens_onto_terrace uses, and it is what
    keeps Master Bathroom and Walk-in Closet from stretching the terrace across
    frontage the norms never intended it to occupy.
    """
    layout = _layout(roomy_program, preset)
    assert layout.terrace is not None

    fy0 = min(rm.rect_m[1] for rm in layout.rooms)
    on_south = [rm for rm in layout.rooms if abs(rm.rect_m[1] - fy0) < 1e-9]

    def qualifies(rm):
        std = standards.ROOMS.get(rm.name)
        return bool(std and std.requires_exterior_wall) and rm.category != "service"

    spanned = {rm.name for rm in _spanned(layout)}
    assert spanned, "terrace spans no room at all"

    # nothing non-qualifying got swept in
    for rm in layout.rooms:
        if rm.name in spanned:
            assert qualifies(rm), (
                f"{preset}: terrace spans {rm.name} (category {rm.category}), "
                "which is not a daylight-required habitable room"
            )

    # every qualifying south room CONTIGUOUS with the spanned run is included
    tx0, _, tx1, _ = layout.terrace.rect_m
    for rm in on_south:
        if not qualifies(rm):
            continue
        touches_run = rm.rect_m[2] >= tx0 - 1e-9 and rm.rect_m[0] <= tx1 + 1e-9
        if touches_run:
            assert rm.name in spanned, (
                f"{preset}: {rm.name} fronts the terrace run but is not spanned"
            )


@pytest.mark.parametrize("preset", PRESETS)
def test_every_spanned_room_has_a_terrace_door(roomy_program, preset):
    layout = _layout(roomy_program, preset)
    spanned = {rm.name for rm in _spanned(layout)}
    with_doors = {d.from_ for d in layout.doors if d.to == "Terrace"}
    assert spanned == with_doors, (
        f"{preset}: rooms spanned {sorted(spanned)} but terrace doors from "
        f"{sorted(with_doors)}"
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_terrace_depth_meets_snip_minimum(roomy_program, preset):
    """SNiP 2.08.01-89 Posobie recommends a veranda depth of at least 1.8 m.

    Neufert is stricter for an outdoor dining space with a bench along one wall
    (3000 mm), which TERRACE_DEPTH_M = 3.0 also meets. Asserted against the SNiP
    figure so the test states the binding regulatory floor, not our own default.
    """
    layout = _layout(roomy_program, preset)
    x0, y0, x1, y1 = layout.terrace.rect_m
    assert (y1 - y0) >= SNIP_MIN_VERANDA_DEPTH_M, (
        f"{preset}: terrace depth {y1 - y0} m is below the SNiP minimum "
        f"{SNIP_MIN_VERANDA_DEPTH_M} m"
    )
    assert (y1 - y0) == pytest.approx(TERRACE_DEPTH_M)
