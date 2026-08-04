"""Golden-file layout stability, split two ways:

- test_golden_invariants_portable: environment-portable properties (status,
  objective value, room-name set, REQUIRED_ADJ satisfied, no overlaps,
  coverage). These hold on every toolchain.
- test_golden_exact_coordinates: the full per-room coordinate signature,
  gated on the exact ortools/python toolchain it was captured with. CP-SAT's
  objective has tied optima (see test_adjacency.py's dedupe tests for why),
  so exact coordinates were never portable across ortools builds/platforms
  even at workers=1 — only the objective VALUE is. The golden file records
  the toolchain it was generated on; this test skips rather than false-fails
  when that toolchain doesn't match the one running it.
"""

import json
import sys
from pathlib import Path

import ortools
import pytest

from app import geom
from app import zones as Z
from app.slicer import build_layout
from app.solver import solve

GOLDEN = Path(__file__).resolve().parent / "golden" / "gW_eN_seed1.json"

# Task 5: the golden freezes the ROOMY plot (20x24; the tight plot can no longer
# host a circulation tree). Corridor is a live zone and the door graph is the
# access tree, so objective, counts and coordinates all moved from Task 4. Phase
# 4's setback envelope broke the plot's translational symmetry, so the solve now
# PROVES optimal in ~1.2 s at workers=1 — deterministic, no longer the 30 s+
# symmetry grind of the setback-less interim plot. These portable invariants plus
# the access-graph checks are the primary guard; the exact-coordinate signature
# is trustworthy again now that the structure has settled.
#
# EXPECTED_OBJECTIVE moved -15.5625 -> 288.0 in b990700 (the master-bedroom-
# perimeter fix). Verified term-by-term before re-baselining (not a guess):
# the +303.5625 swing is fully accounted for by the objective's own
# components, dominated by fp_dev (+96.0) and ADHERE (+171.875) -- the new
# packing's footprint (200 m2) sits much closer to the 184 m2 target than the
# old one (208 m2), sharply cutting both deviation penalties -- plus one
# newly-realized desirable adjacency (+40.0, 1 -> 2 met). The remainder
# (coverage -19.375, public_non_south -3.0, service_northness +18.0,
# soft_min +0.0625) nets to the rest and is consistent with the repacking
# (smaller total zone area, different service-zone position). No term moved
# in a way the repacking doesn't explain.
#
# EXPECTED_OBJECTIVE moved 288.0 -> -97.5625 in 9edf61a (exact tiling:
# COVERAGE_MIN 0.95 -> 1.00, AREA_HI 1.20 -> 1.50, which fixed the ragged
# envelope). Verified term-by-term before re-baselining, on the same standard as
# the b990700 move above -- the recomputed breakdown reproduces the solver's own
# objective to the digit in BOTH arms, and the term deltas sum exactly to the
# -385.5625 swing:
#     fp_dev             -120.0000   footprint grows 200 -> 210 m2, further from
#                                    the 184 m2 target, to give the zones a
#                                    rectangle they can tile exactly
#     adhere             -296.8750   zones pushed off their reconciled targets
#                                    to fill the footprint with no void
#     coverage            +49.3750   more of the footprint is covered (all of it)
#     service_northness   -18.0000   service zones sit further south in the
#                                    new packing
#     soft_min             -0.0625
#     desirable/semi       +0.0000   adjacencies did not move (2 met -> 2 met)
#     ------------------------------
#     total              -385.5625
# The objective got WORSE by design: exact tiling is bought with footprint and
# area-adherence, and neither is free. Nothing here is unexplained by the
# repacking.
#
# EXPECTED_OBJECTIVE moved -97.5625 -> 534.65625 in Phase 1 (architect area
# bands). THE TWO NUMBERS ARE NOT COMPARABLE, and no term-delta table is given
# for this move for one specific reason: **Phase 1 DELETED the adherence (ADHERE)
# L1 term entirely**. It carried 58% of the penalty mass and it was penalising a
# zone for obeying a hard constraint -- once the architect's per-room-type bands
# became hard constraints, paying an L1 penalty for sitting inside your own band
# was double-charging. Every prior baseline in this file (288.0, -97.5625, and
# the -296.8750 adhere delta above) was computed WITH that term, so subtracting
# them from 534.65625 measures nothing. The new value is verified by
# reconstructing every SURVIVING term from the solved rects and reproducing the
# solver's own objective to the digit (roomy, gW_eN, seed 1, workers=1,
# time_limit_s=12 -- the exact call this test makes), plot_cells = 1920:
#     coverage fill      12*100*total_area      967200    +503.7500 /cell
#     footprint dev      -3*PC*fp_dev          -218880    -114.0000 /cell
#     desirable adj      PC*40*n (2 met)        153600     +80.0000 /cell
#     semi adj           PC*15*n (0 met)             0      +0.0000 /cell
#     public non-south   -PC*3*n                 -5760      -3.0000 /cell
#     service northness  PC*2*sum(dy)           130560     +68.0000 /cell
#     soft brief minima  -60*shortfall            -180      -0.0938 /cell
#     ----------------------------------------------------------------
#     SUM                                      1026540    +534.6562 /cell
# which is the solver's reported 534.65625. Footprint 208.00 -> 201.50 m2, void
# 0.00, coverage 1.0000, all 16 rooms inside their architect bands.
# EXPECTED_OBJECTIVE moved 534.65625 -> 477.99427083333336 on 2026-08-04 (the
# architect's three-value / distribution-priority commit). THIS IS THE EASIEST
# RE-BASELINE IN THIS FILE'S HISTORY TO VERIFY, because THE PACKING DID NOT MOVE:
# the solve returns byte-for-byte the same zone rectangles as at 534.65625, so
# every surviving term above is UNCHANGED and the entire -56.66197916666667 swing
# is the new ideal/tier penalty evaluated on the old solution. (The house does
# translate by (-0.5, -0.5) inside the plot -- a tie the setbacks leave free and
# every objective term is invariant to -- which is why the exact-coordinate
# golden below moved while nothing about the plan did.)
#
# Reconstructed from the solved rects, plot_cells = 1920, raw units:
#     kitchen_laundry  4.5x4.0  cut-table    31360   Kitchen 10.00, Laundry 8.00
#     master_suite     5.5x5.5  cut-table    34080   MBed 16.50, MBath 6.25, WIC 7.50
#     children         4.0x8.0  cut-table    15309   Bed2 12.00, Bath 8.00, Bed3 12.00
#     entry            5.5x2.0  cut-table     1562   Mud 3.00, Foyer 5.00, WC 3.00
#     living           4.5x6.5  area         15600   29.25 vs ideal 32.50, tier 1
#     circulation      1.5x8.0  area          3840   12.00 vs ideal  6.00, tier 3
#     garage           7.5x5.0  area          5280   37.50 vs ideal 29.25, tier 3
#     office           3.0x4.0  area          1600   12.00 vs ideal 12.50, tier 2
#     dining           3.0x6.5  area           160   19.50 vs ideal 18.50, tier 1
#     ------------------------------------------------------------------
#     TOTAL                                 108791   = 56.66197916666667 /cell
# and 534.65625 - 56.66197916666667 = 477.9942708333333, the solver's reported
# value. Terms sum; nothing is unexplained.
#
# WHAT IT MEANT THAT NOTHING MOVED. It is not that the term is too weak. At that
# footprint the packing had no degrees of freedom left: pinning fp.area to
# exactly 201.50 m2 and raising standards.TIER_W_BELOW 333x (to 400000, i.e.
# dominating even the 76800-per-bool adjacency rewards) still returns the
# IDENTICAL zone vector, OPTIMAL, on both feasible presets. The room areas at
# roomy @192 were decided by the hard constraints, not by any objective.
#
# WHAT RULING 4 (site coverage 45%) WOULD DO TO THIS NUMBER -- MEASURED
# 2026-08-04, RECORDED, **NOT APPLIED**. Raising program_roomy.json's
# footprint_target_m2 192.0 -> 216.0 moves EXPECTED_OBJECTIVE
# 477.99427083333336 -> 651.8692708333333 and the footprint 201.50 -> 216.00 m2
# (41.98% -> 45.00% site coverage), void still 0.00, coverage 1.0000, all 16
# rooms still inside their architect bands, counts unchanged (16 rooms, 48
# walls, 18 doors, 9 windows), six rooms grow and ten do not: Living +5.75,
# Master Bedroom +2.75, Garage +2.50, Kitchen +2.00 (its FIRST move off its
# 10.00 floor on any lever tried), Office +1.50.
#
# The fixture is NOT at 216 because that arm kills a negative control --
# test_solver.py::test_children_bathroom_direct_needs_center_cover, which at 216
# finds the gW_eW Bathroom corridor-direct even with the constraint OFF (proven
# at OPTIMAL, time_limit_s=240, not a load artefact) -- and because the Garage
# inflates to its 40.00 ceiling. Which rung to ship is an open decision; see
# CLAUDE.md's footprint-ladder table. Everything below is the verification that
# was done for that arm, kept so the re-baseline is a one-line change when the
# decision lands. The ten term deltas sum exactly to the +173.875 swing:
#     coverage fill      +36.25000   the house is 58 cells bigger and still
#                                    tiled exactly (12*100 per cell)
#     footprint dev     +114.00000   THE DOMINANT TERM: 201.50 sat 38 cells off
#                                    the old 192 target and paid 3*1920 each;
#                                    216.00 is exactly on the new target, so
#                                    fp_dev goes 38 -> 0
#     service northness   +4.00000   service zones sit further north in the
#                                    taller footprint
#     cut penalty        +11.87500   kitchen_laundry 31360 -> 21760 (Kitchen
#                                    10.00 -> 12.00, off its floor for the first
#                                    time) and master_suite 34080 -> 20880
#                                    (Master Bedroom 16.50 -> 19.25)
#     ideal shortfall     +8.95833   living 13 cells short -> 0, office 2 -> 0;
#                                    every NON-composite zone reaches its ideal
#     ideal overshoot     -1.20834   garage 33 -> 43 cells over (37.50 -> 40.00,
#                                    its ceiling) and living 0 -> 10 over
#     desirable/semi      +0.00000   adjacencies did not move (2 met -> 2 met)
#     public non-south    +0.00000
#     soft brief minima   +0.00000
#     ------------------------------
#     total             +173.87500
# Reconstructed at 216, plot_cells = 1920, raw units: coverage 1036800, fp_dev 0,
# desirable 153600, semi 0, public non-south -5760, service northness 138240,
# soft minima -180, cut penalty -59511 (kitchen_laundry 21760, master_suite
# 20880, children 15309, entry 1562), ideal shortfall 0, ideal overshoot -11600
# (garage 6880, circulation 3840, office 320, living 400, dining 160)
# = 1251589 / 1920 = 651.8692708333333, the solver's reported value.
#
# NOTE ON time_limit_s=12 BELOW: at 216 that solve proves OPTIMAL in ~5 s on an
# idle machine, but it returns FEASIBLE (and a different packing) if anything
# else is using the CPU -- e.g. a second pytest run. That is the load-canary
# behaviour CLAUDE.md documents, not a drift; run the suite alone.
EXPECTED_OBJECTIVE = 477.99427083333336
EXPECTED_ROOM_NAMES = {
    "Living", "Dining", "Kitchen", "Laundry",
    "Master Bedroom", "Master Bathroom", "Walk-in Closet",
    "Bedroom 2", "Bedroom 3", "Bathroom",
    "Office", "Foyer", "Mudroom", "Garage", "Corridor",
    # 16th room, added with the guest WC (architect review round 3, point 5):
    # zones.inject_guest_wc derives it from SNiP 2.08.01-89 Posobie cl. 3.5 the
    # way the corridor is derived, and slicer._slice_entry cuts it as the entry
    # zone's third room (Mudroom | Guest WC | Foyer).
    "Guest WC",
}


def _current_toolchain() -> dict:
    return {"ortools": ortools.__version__, "python": f"{sys.version_info.major}.{sys.version_info.minor}"}


def _room_signature(layout) -> dict:
    return {
        "preset": layout.preset,
        "seed": layout.seed,
        "n_rooms": len(layout.rooms),
        "n_walls": len(layout.walls),
        "n_doors": len(layout.doors),
        "n_windows": len(layout.windows),
        "rooms": sorted(
            [r.name, round(r.rect_m[0], 2), round(r.rect_m[1], 2), round(r.rect_m[2], 2), round(r.rect_m[3], 2)]
            for r in layout.rooms
        ),
    }


def _load_golden() -> dict | None:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else None


def test_golden_invariants_portable(roomy_program):
    program = roomy_program
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    assert r.status == "OPTIMAL"
    assert r.objective == EXPECTED_OBJECTIVE

    layout = build_layout(r, program)
    assert {rm.name for rm in layout.rooms} == EXPECTED_ROOM_NAMES

    rects = [tuple(rm.rect_m) for rm in layout.rooms]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert geom.overlap_area(rects[i], rects[j]) < 1e-6, "rooms must not overlap"

    def room(name: str) -> geom.Rect:
        return next(tuple(rm.rect_m) for rm in layout.rooms if rm.name == name)

    # REQUIRED_ADJ, at the post-slice room level. Kitchen<->Dining is checked
    # separately below (test_golden_kitchen_dining_room_level_KNOWN_REGRESSION,
    # xfail) -- it no longer holds as of b990700; see that test for why.
    assert geom.adjacent(room("Dining"), room("Living"), Z.REQUIRED_SHARE_M)

    # Coverage is now measured against the footprint (room bounding box), which
    # the zones tile to ~0.97; the exact value depends on which tied optimum the
    # toolchain lands on, so assert the portable gate, not an exact figure.
    fx0 = min(rc[0] for rc in rects)
    fy0 = min(rc[1] for rc in rects)
    fx1 = max(rc[2] for rc in rects)
    fy1 = max(rc[3] for rc in rects)
    coverage = sum(geom.area(rc) for rc in rects) / ((fx1 - fx0) * (fy1 - fy0))
    assert coverage >= 0.95
    # the house no longer fills the plot — there is real setback now
    assert (fx1 - fx0) * (fy1 - fy0) < layout.plot.width_m * layout.plot.depth_m - geom.EPS


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN REGRESSION (b990700, the master-bedroom-window fix): "
        "Kitchen<->Dining no longer share a wall at the room level on the "
        "roomy 184 m2 fixture -- Laundry sits between them. Root cause: "
        "66f3506 made _slice_kitchen let the corridor-facing side win over "
        "the dining-facing side on conflict; b990700's repacking (freeing "
        "the corridor to front master_suite from the north) put "
        "kitchen_laundry's corridor side and dining side into that conflict "
        "at 184, where before b990700 it wasn't. REQUIRED_ADJ is zone-level "
        "only, so validate() can't see it. Not fixed; see b990700's commit "
        "message and tests/test_validator.py's "
        "test_kitchen_dining_room_level_KNOWN_REGRESSION for the same fact. "
        "PHASE 1: the root cause named above is superseded. It is not that the "
        "repacking happened to create the corridor/dining conflict -- that "
        "conflict is the only configuration the model can pack at all. See "
        "test_validator.py::test_kitchen_dining_two_hops_KNOWN_DEFECT for the "
        "16-config enumeration and the blocker."
    ),
)
def test_golden_kitchen_dining_room_level_KNOWN_REGRESSION(roomy_program):
    program = roomy_program
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    layout = build_layout(r, program)

    def room(name: str) -> geom.Rect:
        return next(tuple(rm.rect_m) for rm in layout.rooms if rm.name == name)

    assert geom.adjacent(room("Kitchen"), room("Dining"), Z.REQUIRED_SHARE_M)


_golden = _load_golden()
_toolchain_mismatch = _golden is not None and _golden.get("toolchain") != _current_toolchain()


@pytest.mark.skipif(
    _toolchain_mismatch,
    reason=(
        f"golden captured on {_golden.get('toolchain') if _golden else None}, "
        f"running on {_current_toolchain()}; CP-SAT's tied optima make exact "
        f"coordinates non-portable across toolchains (see test_golden_invariants_portable)"
    ),
)
def test_golden_exact_coordinates(roomy_program):
    program = roomy_program
    r = solve(program, "gW_eN", seed=1, time_limit_s=12, workers=1)
    sig = _room_signature(build_layout(r, program))
    if _golden is None:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps({"toolchain": _current_toolchain(), "signature": sig}, indent=2))
        return
    assert sig == _golden["signature"], "layout drifted from golden; review and update if intended"
