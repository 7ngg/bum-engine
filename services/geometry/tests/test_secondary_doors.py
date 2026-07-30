"""Secondary doors (Part B) and the door-end rule R7 (Part C).

Architect review, round 3:

  (1) "Metbexden yemeyi bu qeder uzun trayektoriya uzerinden yemey otagina
      aparmaq duzgun deyil. En azindan metbexin ashagisindan qapi qoyulmali
      idi." — carrying food from the Kitchen to the Dining room over such a
      long path is wrong; at minimum a door should be placed at the BOTTOM of
      the Kitchen.

      MEASURED at HEAD 50293f3 on the 184 fixture, both feasible presets:
      Kitchen -> Foyer -> Corridor -> Living -> Dining, FOUR door-graph hops,
      while the Kitchen's south wall borders the Living room over 2.50 m and
      carries no door at all.

      ROOT CAUSE: _build_doors hosts exactly one door per validator.access_tree
      edge, and that is a SPANNING TREE — n rooms, n-1 doors, no cycles. Every
      trip is therefore forced up and back down the tree. Real dwellings have
      rings. SNiP 2.08.01-89 Posobie's apartment-planning section permits them
      explicitly ("возможно создание дополнительных связей между смежными
      помещениями, улучшающих функциональную и пространственную организацию
      квартир") and treats this very separation as a defect: where the dining
      zone sits outside the kitchen with no direct connection, the kitchen must
      carry a supplementary 2-3 seat dining area instead.

  (4) "Bu qapi kitabdaki melumatlari ve dersleri esas gotursek sehv
      yerleshdirilib. Divardan olmali idi." — the Mudroom->Garage door is at
      the wrong END of its wall.

      MEASURED at HEAD: 4.00 m wall, near jamb 0.15 m from the LO end, because
      slicer._door_pos was hardwired to offset toward `lo`. Deterministic, but
      an arbitrary tie broken by the rasterizer's scan order.

NOTE ON THE ID "w10": the architect's note calls this door w10, but wall ids are
assigned by rasterisation order (_build_walls) and are NOT stable across presets
or layouts — at HEAD the Mudroom->Garage door is on w8 (gW_eN) / w20 (gE_eN),
and w10 is Kitchen->Laundry. These tests therefore identify the door by its room
pair, which is unambiguous, and never by id.
"""

from collections import deque

import pytest

from app.slicer import (
    MAX_SECONDARY_DOORS,
    SECONDARY_MIN_FREE_WALL_M,
    SECONDARY_MIN_HOPS,
    _convex_overlap,
    _hinge_frame,
    _into_normal,
    _longest_free_wall_run,
    _opening_spans,
    build_layout,
    swing_wedge,
)
from app.solver import solve

PRESETS = ["gW_eN", "gE_eN"]


def _layout(roomy_program, preset):
    r = solve(roomy_program, preset, seed=1, time_limit_s=12, workers=1)
    assert r.feasible, f"{preset} infeasible"
    return build_layout(r, roomy_program)


def _graph(layout, include_secondary=True):
    g = {rm.name: set() for rm in layout.rooms}
    for d in layout.doors:
        if d.secondary and not include_secondary:
            continue
        if d.from_ in g and d.to in g:
            g[d.from_].add(d.to)
            g[d.to].add(d.from_)
    return g


def _hops(g, a, b):
    if a == b:
        return 0
    seen = {a}
    q = deque([(a, 0)])
    while q:
        cur, k = q.popleft()
        for nb in sorted(g[cur]):
            if nb in seen:
                continue
            if nb == b:
                return k + 1
            seen.add(nb)
            q.append((nb, k + 1))
    return -1


# ---------------------------------------------------------------------------
# 1. the Kitchen gets its door at the bottom
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", PRESETS)
def test_secondary_door_exists_between_kitchen_and_living(roomy_program, preset):
    """The architect's complaint (1), asserted directly.

    If the eligibility rule ever rejects this pair the test still has to say so
    with the DOCUMENTED reason rather than silently passing, so the failure
    message carries the verdict _secondary_doors recorded for it.
    """
    layout = _layout(roomy_program, preset)
    kl = [d for d in layout.doors
          if {d.from_, d.to} == {"Kitchen", "Living"} and d.secondary]
    assert len(kl) == 1, (
        f"{preset}: expected exactly one secondary Kitchen<->Living door, got {len(kl)}"
    )
    door = kl[0]

    # it is on the Kitchen's SOUTH wall -- "metbexin ashagisindan", the bottom
    kitchen = next(rm for rm in layout.rooms if rm.name == "Kitchen")
    assert door.center[1] == pytest.approx(kitchen.rect_m[1]), (
        f"{preset}: the door must sit on the Kitchen's south edge "
        f"y={kitchen.rect_m[1]}, found {door.center}"
    )
    assert door.width_m >= 0.9 - 1e-9


@pytest.mark.parametrize("preset", PRESETS)
def test_kitchen_to_dining_is_at_most_two_hops(roomy_program, preset):
    """Was 4 hops at HEAD (Kitchen->Foyer->Corridor->Living->Dining)."""
    layout = _layout(roomy_program, preset)
    g = _graph(layout)
    assert _hops(g, "Kitchen", "Dining") <= 2
    # and the improvement is entirely the secondary door's doing
    assert _hops(_graph(layout, include_secondary=False), "Kitchen", "Dining") == 4


# ---------------------------------------------------------------------------
# 2. secondary doors are additive: never load-bearing for reachability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", PRESETS)
def test_removing_secondary_doors_leaves_every_room_reachable(roomy_program, preset):
    """The invariant that makes secondary doors safe. validator.access_tree is
    recomputed from room ADJACENCY, never from the door list, so a secondary
    door cannot change the tree, the reachability gate or the kitchen-direct
    rule. This asserts the consequence on the doors themselves."""
    layout = _layout(roomy_program, preset)
    assert any(d.secondary for d in layout.doors), "no secondary door to remove"

    g = _graph(layout, include_secondary=False)
    seen = {"Foyer"}
    q = deque(["Foyer"])
    while q:
        cur = q.popleft()
        for nb in g[cur]:
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    missing = sorted({rm.name for rm in layout.rooms} - seen)
    assert not missing, f"{preset}: unreachable without secondary doors: {missing}"


@pytest.mark.parametrize("preset", PRESETS)
def test_secondary_doors_are_capped_and_leave_usable_walls(roomy_program, preset):
    layout = _layout(roomy_program, preset)
    sec = [d for d in layout.doors if d.secondary]
    assert 1 <= len(sec) <= MAX_SECONDARY_DOORS

    # E5: every room touched by a secondary door keeps a furniture run
    from app.slicer import FinalRoom, _build_walls

    rooms = [FinalRoom(rm.name, rm.category, rm.zone or "", tuple(rm.rect_m))
             for rm in layout.rooms]
    recs = _build_walls(rooms, layout.plot.width_m, layout.plot.depth_m,
                        layout.wall_height_m)
    spans = _opening_spans(list(layout.doors) + [layout.entry], layout.windows, recs)
    for d in sec:
        for name in (d.from_, d.to):
            idx = next(i for i, rm in enumerate(rooms) if rm.name == name)
            run = _longest_free_wall_run(idx, recs, spans)
            assert run >= SECONDARY_MIN_FREE_WALL_M - 1e-9, (
                f"{preset}: {name} keeps only {run:.2f} m of uninterrupted wall"
            )


def test_secondary_hop_threshold_is_three():
    """E2's threshold is read off the measured graph, not assumed: on the 184
    fixture Kitchen->Living is 3 hops and Kitchen->Dining is 4. At 2 the rule
    would also fire on pairs one room apart (Corridor<->Kitchen, Bedroom
    2<->Bathroom), buying a step and costing a wall."""
    assert SECONDARY_MIN_HOPS == 3


# ---------------------------------------------------------------------------
# 3. swings still clean, tree doors and secondary doors alike
# ---------------------------------------------------------------------------


def _wedges(layout):
    walls = {w.id: w for w in layout.walls}
    rects = {rm.name: tuple(rm.rect_m) for rm in layout.rooms}
    if layout.terrace is not None:
        rects["Terrace"] = tuple(layout.terrace.rect_m)
    out = []
    for d in list(layout.doors) + [layout.entry]:
        wall, rect = walls.get(d.wall_id), rects.get(d.swing_into)
        assert wall is not None and rect is not None
        hinge_pt, along = _hinge_frame(d, wall, d.hinge)
        into = _into_normal(d, wall, rect)
        assert into is not None
        out.append((d, rect, swing_wedge(hinge_pt, along, into, d.width_m)))
    return out


@pytest.mark.parametrize("preset", PRESETS)
def test_all_pairs_swing_check_still_clean_with_secondary_doors(roomy_program, preset):
    layout = _layout(roomy_program, preset)
    wedges = _wedges(layout)
    assert any(d.secondary for d, _, _ in wedges), "secondary door not in the checked set"

    # zero arc-arc overlaps, ALL pairs
    for i in range(len(wedges)):
        for j in range(i + 1, len(wedges)):
            da, _, wa = wedges[i]
            db, _, wb = wedges[j]
            assert not _convex_overlap(wa, wb), (
                f"{preset}: {da.from_}->{da.to} and {db.from_}->{db.to} swings overlap"
            )

    # zero arc-wall crossings: a wedge inside its room cannot cross a wall,
    # because every wall lies on a room boundary
    for d, rect, wedge in wedges:
        for px, py in wedge:
            assert rect[0] - 1e-6 <= px <= rect[2] + 1e-6, f"{d.from_}->{d.to} leaves {d.swing_into}"
            assert rect[1] - 1e-6 <= py <= rect[3] + 1e-6, f"{d.from_}->{d.to} leaves {d.swing_into}"


# ---------------------------------------------------------------------------
# 4. R7 -- which end of the wall a door sits at
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", PRESETS)
def test_mudroom_garage_door_sits_at_the_street_end_under_r7(roomy_program, preset):
    """R7: a door sits at the end of its host wall NEARER THE OPENING BY WHICH
    ITS HOST ROOM IS ENTERED — the previous door on the access route, with doors
    positioned in access-tree order outward from the front door.

    Applied to the sequence the architect flagged: the front door is on the
    NORTH (street) facade, so R7 pulls Foyer->Mudroom to the north end of its
    wall, and that in turn pulls Mudroom->Garage to the north end of ITS wall.
    That is the end the architect asked for, and it is reached without any
    furniture model — the reference point is another door.

    At HEAD both doors sat at the `lo` (south) end, 3.4 m away, so entering the
    garage meant walking south down the mudroom and back north again.
    """
    layout = _layout(roomy_program, preset)
    walls = {w.id: w for w in layout.walls}
    door = next(d for d in layout.doors if {d.from_, d.to} == {"Mudroom", "Garage"})
    wall = walls[door.wall_id]

    lo, hi = wall.start, wall.end
    d_lo = ((door.center[0] - lo[0]) ** 2 + (door.center[1] - lo[1]) ** 2) ** 0.5
    d_hi = ((door.center[0] - hi[0]) ** 2 + (door.center[1] - hi[1]) ** 2) ** 0.5
    assert d_hi < d_lo, (
        f"{preset}: Mudroom->Garage on {door.wall_id} is {d_lo:.2f} m from the lo end "
        f"and {d_hi:.2f} m from the hi end; R7 requires the hi (street) end"
    )
    # the reference that put it there
    ref = next(d for d in layout.doors if {d.from_, d.to} == {"Foyer", "Mudroom"})
    assert abs(ref.center[1] - door.center[1]) < 1e-9, (
        "R7 should place the two doors on the same cross-line through the Mudroom"
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_r7_never_moves_the_front_door(roomy_program, preset):
    """The entry has no predecessor on the access route, so it keeps the `lo`
    anchor and stays where _build_entry put it (north/street-facing Foyer wall)."""
    layout = _layout(roomy_program, preset)
    walls = {w.id: w for w in layout.walls}
    wall = walls[layout.entry.wall_id]
    assert wall.exterior
    jamb = wall.thickness_m
    lo = wall.start if wall.start <= wall.end else wall.end
    along = 0 if abs(wall.start[0] - wall.end[0]) > 1e-9 else 1
    expected = lo[along] + jamb + layout.entry.width_m / 2
    assert layout.entry.center[along] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. sub-standard door leaves (validator warning, pending the corridor fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", PRESETS)
def test_narrow_door_warning_fires_for_the_corridor_dead_end(roomy_program, preset):
    """TRIPWIRE. slicer._door_on sets width = min(0.9, wall_len - 0.2), so a host
    wall under 1.10 m silently yields a leaf below standards.DOOR_CLEAR_WIDTH_M
    (0.9 m, the wheelchair doorset minimum) and below the ACCESS_DOOR_M the
    access graph assumed when it awarded the edge.

    On the 184 fixture this fires for exactly the doors at the Corridor's south
    end, where the spine dead-ends against two rooms and each gets only part of
    its 2.0-2.5 m width:
      gW_eN  Corridor 2.0 m wide -> Living 1.00 m + Master Bedroom 1.00 m,
             so BOTH doors come out at 0.80 m;
      gE_eN  Corridor 2.5 m wide -> Living 1.00 m + Master Bedroom 1.50 m,
             so only Corridor->Living is undersized.
    That is the corridor's dead-end T (architect review round 3, point 3),
    separately scoped.

    WHEN THIS TEST STOPS FIRING, the T has been fixed and the check in
    validator.py should be PROMOTED from warnings.append to errors.append --
    a 0.80 m leaf is then a real defect rather than a known consequence.
    """
    from app.standards import DOOR_CLEAR_WIDTH_M
    from app.validator import validate

    layout = _layout(roomy_program, preset)
    res = validate(layout, roomy_program)
    narrow = [w for w in res.warnings if "clear doorset minimum" in w]
    assert narrow, "the narrow-leaf tripwire stopped firing -- promote it to a hard error"

    offenders = {
        (d.from_, d.to) for d in list(layout.doors) + [layout.entry]
        if d.width_m < DOOR_CLEAR_WIDTH_M - 1e-9
    }
    expected = {
        "gW_eN": {("Corridor", "Living"), ("Corridor", "Master Bedroom")},
        "gE_eN": {("Corridor", "Living")},
    }[preset]
    assert offenders == expected, f"{preset}: unexpected sub-standard doors {offenders}"
    assert len(narrow) == len(expected)
    # still only a warning -- the plan must remain exportable
    assert res.ok, f"{preset}: the narrow-leaf check must not be a hard error yet"
