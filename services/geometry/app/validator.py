"""The validator: the export gate and the test oracle.

A layout must pass every hard check to be eligible for ranking/export. Soft
issues surface as structured warnings. Rules (from the plan):
  - no overlapping rooms
  - every door sits on a real shared wall >= 0.8 m
  - master suite not adjacent to the kitchen
  - garage not adjacent to the living room
  - all rooms inside the plot
  - min dimensions met
  - coverage >= ~0.9
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geom, standards
from .models import Layout, Program
from .solver import KITCHEN_FALLBACK_TAG

# Absolute narrowest any room may be (the corridor/passage minimum). The
# per-room Neufert minima (checked hard below) are stricter; this is the floor
# for a room the standards table doesn't cover. Raised from 0.9 in Task 3 now
# that the slicer guarantees standards-legal rooms.
MIN_ROOM_M = 1.2
MIN_DOOR_WALL = 0.8
# Coverage is now measured against the house FOOTPRINT (the bounding box of the
# rooms), not the plot: a house really is ~100% internally covered, and the
# plot now carries setback the house doesn't fill. 0.95 leaves headroom for the
# small void that free-rectangle packing can't avoid — the space Task 3's
# circulation will occupy.
COVERAGE_MIN = 0.95
MASTER_ROOMS = {"Master Bedroom", "Master Bathroom", "Walk-in Closet"}
KITCHEN_ROOMS = {"Kitchen"}
GARAGE_ROOMS = {"Garage"}
LIVING_ROOMS = {"Living"}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: float = 0.0

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "coverage": round(self.coverage, 4)}


def _rooms_named(layout: Layout, names: set[str]) -> list[geom.Rect]:
    return [tuple(r.rect_m) for r in layout.rooms if r.name in names]


def validate(layout: Layout, program: Program | None = None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = list(layout.warnings)

    W = layout.plot.width_m
    D = layout.plot.depth_m
    rects = [tuple(r.rect_m) for r in layout.rooms]

    # 1. containment
    for rm in layout.rooms:
        x0, y0, x1, y1 = rm.rect_m
        if x0 < -geom.EPS or y0 < -geom.EPS or x1 > W + geom.EPS or y1 > D + geom.EPS:
            errors.append(f"room {rm.name!r} outside plot: {rm.rect_m}")

    # 2. no overlaps
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            ov = geom.overlap_area(rects[i], rects[j])
            if ov > 1e-3:
                errors.append(
                    f"rooms {layout.rooms[i].name!r} and {layout.rooms[j].name!r} overlap by {ov:.2f} m2"
                )

    # 3. min dimensions
    for rm in layout.rooms:
        x0, y0, x1, y1 = rm.rect_m
        if (x1 - x0) < MIN_ROOM_M - geom.EPS or (y1 - y0) < MIN_ROOM_M - geom.EPS:
            errors.append(f"room {rm.name!r} below min dimension: {x1 - x0:.2f} x {y1 - y0:.2f} m")

    # 4. coverage — against the house FOOTPRINT (bounding box of the rooms),
    #    which the zones tile; the residual plot area is setback. Also assert
    #    the footprint sits within the plot and holds no large unassigned void.
    covered = sum(geom.area(r) for r in rects)
    if rects:
        fx0 = min(r[0] for r in rects)
        fy0 = min(r[1] for r in rects)
        fx1 = max(r[2] for r in rects)
        fy1 = max(r[3] for r in rects)
        footprint_area = (fx1 - fx0) * (fy1 - fy0)
        coverage = covered / footprint_area if footprint_area else 0.0
        # footprint wholly within the plot
        if fx0 < -geom.EPS or fy0 < -geom.EPS or fx1 > W + geom.EPS or fy1 > D + geom.EPS:
            errors.append(f"footprint {[fx0, fy0, fx1, fy1]} extends outside plot {W}x{D}")
        # no large unassigned region inside the footprint (the rooms fill it).
        # A small residual is the un-modelled circulation Task 3 will place.
        if coverage < COVERAGE_MIN - geom.EPS:
            errors.append(
                f"footprint coverage {coverage:.3f} below minimum {COVERAGE_MIN} "
                f"({footprint_area - covered:.1f} m2 unassigned inside the house)"
            )
    else:
        coverage = 0.0
        errors.append("no rooms")

    # 5. forbidden adjacencies (master<->kitchen, garage<->living)
    _check_forbidden(layout, MASTER_ROOMS, KITCHEN_ROOMS, "master suite", "kitchen", errors)
    _check_forbidden(layout, GARAGE_ROOMS, LIVING_ROOMS, "garage", "living", errors)

    # 6. doors on real shared walls >= 0.8 m
    wall_by_id = {w.id: w for w in layout.walls}
    all_doors = list(layout.doors) + [layout.entry]
    for d in all_doors:
        w = wall_by_id.get(d.wall_id)
        if w is None:
            errors.append(f"door {d.from_}->{d.to} references missing wall {d.wall_id!r}")
            continue
        wall_len = ((w.end[0] - w.start[0]) ** 2 + (w.end[1] - w.start[1]) ** 2) ** 0.5
        if wall_len < MIN_DOOR_WALL - geom.EPS:
            errors.append(f"door {d.from_}->{d.to} on wall {d.wall_id!r} only {wall_len:.2f} m (<0.8)")
        if d.width_m > wall_len + geom.EPS:
            errors.append(f"door {d.from_}->{d.to} width {d.width_m} exceeds wall {wall_len:.2f} m")

    # 7. required adjacencies present (DoD): kitchen-dining, dining-living, master-ensuite
    _require_adjacent(layout, "Kitchen", "Dining", warnings)
    _require_adjacent(layout, "Dining", "Living", warnings)
    _require_adjacent(layout, "Master Bedroom", "Master Bathroom", warnings)

    # 8. Neufert dimensional standards — now a HARD gate (Task 3). The slicer's
    # ceil-snap + dimension cuts + legal-shape tables guarantee every sliced room
    # meets its per-room minimum, so a violation here is a real defect, not the
    # unavoidable sliver it was under Tasks 1-2.
    _check_neufert_standards(layout, errors)

    # 9. Access graph (Task 5 Phase 2): the plan must admit a legal, bedroom-free
    # access tree from the front door — the hard gate that turns "corridor exists"
    # into "every room is reachable and no bedroom is a passage".
    errors.extend(validate_plan(layout, program))

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, coverage=coverage)


ACCESS_DOOR_M = 0.9  # an access-graph edge needs a shared wall a door fits in


def _is_public(name: str, category: str) -> bool:
    """The social zone that must not be reached through a private/bedroom wing:
    the living/dining rooms plus the (open-plan) Kitchen. Used for the mirror of
    the privacy rule — a public room's tree-parent must be circulation or another
    public room (the Kitchen, being no_through, is blocked from being a real
    passage by the transit guard, so it can be a public sibling but never route
    another room through itself)."""
    return category == "living" or name == "Kitchen"


def _access_root(names: list[str]) -> int | None:
    if not names:
        return None
    root = next((i for i, nm in enumerate(names) if nm == "Foyer"), None)
    if root is None:
        root = next((i for i, nm in enumerate(names) if nm == "Living"), 0)
    return root


def access_tree(rooms) -> tuple[list[tuple[int, int]], set[int], int | None]:
    """THE access graph, realised at room level — the SINGLE source of truth for
    both the doors (slicer._build_doors places exactly one door per edge) and this
    module's reachability gate (validate_plan). A spanning tree rooted at the
    entry room (Foyer, else Living), grown over real shared walls >=
    ACCESS_DOOR_M, under a TWO-TIER parent rule so no private room is entered
    through a habitable one:
      - a no_through_traffic room is a LEAF: nothing is reached through it except
        its own ensuite children (a master bath opens off the master bedroom);
      - TIER 1 (privacy): a no_through PRIVATE room (a bedroom) may be entered
        ONLY from a circulation room (category "circ": Corridor/Foyer). This
        forbids Living->Master Bedroom, the SNiP violation of routing the bedroom
        wing through the living room. A no_through WET room (the Kitchen) is NOT
        bound by tier 1 — it opens off the dining/living in an open plan;
      - TIER 2 (ensuite): an ensuite room (allowed_ensuite_parents) is entered
        ONLY from a designated parent, never straight off the corridor;
      - MIRROR (public): a public/social room (Living, Dining, Kitchen) is entered
        ONLY from a circulation room or another public room, never through a
        private/bedroom one. Without this, Living<-Master Bedroom would pass (the
        living room is not no_through) — the same directional hole, mirrored.
    Returns (edges, reached, root): `edges` are (parent_idx, child_idx) pairs, one
    per non-root reached room; `reached` is the set of reachable indices; `root`
    is the entry-room index. Because the doors ARE these edges, a door can never
    exist that this tree did not produce, and the door builder and the validator
    can never disagree. Deterministic: neighbours are visited in index order."""
    names = [rm.name for rm in rooms]
    cats = [rm.category for rm in rooms]
    n = len(names)
    if n == 0:
        return [], set(), None
    # accept both the slicer's FinalRoom (.rect) and the Layout's Room (.rect_m)
    rects = [tuple(getattr(rm, "rect_m", None) or rm.rect) for rm in rooms]

    def no_through(i: int) -> bool:
        s = standards.ROOMS.get(names[i])
        return bool(s and s.no_through_traffic)

    def parents(i: int) -> tuple[str, ...]:
        s = standards.ROOMS.get(names[i])
        return s.allowed_ensuite_parents if s else ()

    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if geom.adjacent(rects[i], rects[j], ACCESS_DOOR_M):
                adj[i].add(j)
                adj[j].add(i)

    root = _access_root(names)
    edges: list[tuple[int, int]] = []
    reached = {root}
    stack = [root]
    while stack:
        cur = stack.pop()
        cur_blocks_transit = cur != root and no_through(cur)
        for nb in sorted(adj[cur]):
            if nb in reached:
                continue
            nb_parents = parents(nb)
            if nb_parents:
                if names[cur] not in nb_parents:
                    continue  # tier 2: ensuite room only from its designated parent
            elif no_through(nb) and cats[nb] == "private":
                if cats[cur] != "circ":
                    continue  # tier 1 (privacy): a bedroom only opens off circulation
            elif _is_public(names[nb], cats[nb]):
                if cats[cur] != "circ" and not _is_public(names[cur], cats[cur]):
                    continue  # mirror: a public room only from circulation or another public room
            if cur_blocks_transit and names[cur] not in nb_parents:
                continue  # no_through room: only its ensuite children pass through
            edges.append((cur, nb))
            reached.add(nb)
            stack.append(nb)
    return edges, reached, root


def validate_plan(layout: Layout, program: Program | None = None) -> list[str]:
    """Hard gate: the layout must admit the access_tree with EVERY room reachable
    from the entry — nothing stranded behind a no_through_traffic bedroom, no
    ensuite opening onto the corridor. Because slicer._build_doors builds its
    doors from the SAME access_tree, a clean result here also proves the placed
    doors reach every room. A layout that fails is dropped by validate()."""
    rooms = layout.rooms
    if not rooms:
        return ["plan has no rooms"]
    edges, reached, _root = access_tree(rooms)
    names = [rm.name for rm in rooms]
    cats = [rm.category for rm in rooms]
    errors: list[str] = []

    unreached = sorted({names[i] for i in range(len(rooms)) if i not in reached})
    if unreached:
        errors.append(
            "access graph: rooms unreachable from the entry without transiting a "
            f"no_through_traffic room: {unreached}"
        )

    # Mirror of access_tree's tier-1 rule (the guard whose absence let Living->
    # Master pass): assert directly on the tree that no PRIVATE no_through room is
    # entered from a non-circulation room. access_tree already enforces this by
    # construction, so this is belt-and-suspenders against a future regression in
    # the traversal, and it names the offending edge instead of a vague "isolated".
    for parent, child in edges:
        spec = standards.ROOMS.get(names[child])
        if (
            spec
            and spec.no_through_traffic
            and cats[child] == "private"
            and not spec.allowed_ensuite_parents
            and cats[parent] != "circ"
        ):
            errors.append(
                f"access graph: private room {names[child]!r} is entered from "
                f"{names[parent]!r} (not circulation) - bedroom wing routed through a habitable room"
            )
        # Symmetric mirror: a public/social room must be entered from circulation
        # or another public room, never through a private/no_through one. valid=True
        # would otherwise miss Living<-Master Bedroom (Living is not no_through).
        elif (
            _is_public(names[child], cats[child])
            and cats[parent] != "circ"
            and not _is_public(names[parent], cats[parent])
        ):
            errors.append(
                f"access graph: public room {names[child]!r} is entered from "
                f"{names[parent]!r} (private/non-public) - social zone routed through a private room"
            )

    # Kitchen-direct invariant (Task 6): Living must never be an ANCESTOR of
    # Kitchen on the access tree — the client-reported pathology of kitchen
    # traffic routed through the living room via the Dining->Living chain.
    # _is_public's mirror rule above permits Kitchen<-Living (Living IS
    # public), so that rule alone can't catch this; this asserts the
    # stronger, kitchen-specific invariant directly. The one authorized
    # exception is generate.py's flagged area-limitation fallback: when the
    # footprint was too small for solver.py's kitchen-direct constraint, the
    # caller already disclosed that via a KITCHEN_FALLBACK_TAG-prefixed
    # warning, and shipping the plan visibly flagged beats shipping nothing.
    if "Kitchen" in names and not any(w.startswith(KITCHEN_FALLBACK_TAG) for w in layout.warnings):
        kidx = names.index("Kitchen")
        if kidx in reached:
            parent_of = {c: p for p, c in edges}
            cur = kidx
            while cur in parent_of:
                cur = parent_of[cur]
                if names[cur] == "Living":
                    errors.append(
                        "access graph: Kitchen is reached via Living (through-living "
                        "routing) - kitchen must be corridor-direct unless flagged as "
                        "an area limitation"
                    )
                    break
    return errors


def _check_neufert_standards(layout: Layout, errors: list[str]) -> None:
    for rm in layout.rooms:
        spec = standards.ROOMS.get(rm.name)
        if spec is None:
            continue
        x0, y0, x1, y1 = rm.rect_m
        w, h = x1 - x0, y1 - y0
        area = w * h
        if w < spec.min_w_m - geom.EPS:
            errors.append(f"room {rm.name!r} width {w:.2f} m below Neufert min {spec.min_w_m} m")
        if h < spec.min_h_m - geom.EPS:
            errors.append(f"room {rm.name!r} depth {h:.2f} m below Neufert min {spec.min_h_m} m")
        if area < spec.min_area_m2 - geom.EPS:
            errors.append(f"room {rm.name!r} area {area:.2f} m2 below Neufert min {spec.min_area_m2} m2")
        short_side = min(w, h)
        aspect = max(w, h) / short_side if short_side > geom.EPS else float("inf")
        if aspect > spec.max_aspect + geom.EPS:
            errors.append(f"room {rm.name!r} aspect {aspect:.2f} exceeds Neufert max {spec.max_aspect}")


def _check_forbidden(
    layout: Layout, group_a: set[str], group_b: set[str], la: str, lb: str, errors: list[str]
) -> None:
    ra = _rooms_named(layout, group_a)
    rb = _rooms_named(layout, group_b)
    for a in ra:
        for b in rb:
            if geom.shared_edge(a, b) is not None or geom.overlap_area(a, b) > geom.EPS:
                errors.append(f"forbidden adjacency: {la} touches {lb}")
                return


def _require_adjacent(layout: Layout, name_a: str, name_b: str, warnings: list[str]) -> None:
    ra = _rooms_named(layout, {name_a})
    rb = _rooms_named(layout, {name_b})
    if not ra or not rb:
        return  # room may be absent (e.g. un-sliced); not a hard failure here
    for a in ra:
        for b in rb:
            if geom.adjacent(a, b, MIN_DOOR_WALL):
                return
    warnings.append(f"expected adjacency {name_a}<->{name_b} not found")
