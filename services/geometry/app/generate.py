"""Fan out presets x seeds, gate on the validator, rank, keep top-N distinct.

This is the M2 pipeline: the only variants that leave here are ones the
validator has already passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Layout, Program
from .presets import PRESETS
from .solver import KITCHEN_FALLBACK_TAG, solve
from .slicer import build_layout
from .svg import render
from .validator import validate

DEFAULT_SEEDS = [1, 2, 3, 4]


@dataclass
class Variant:
    layout: Layout
    svg: str
    coverage: float
    # Which ARRANGEMENT this plan is, 0-based. Two variants sharing an id are the
    # same zone packing seen in a different orientation (mirrored) — the same
    # house flipped, not a second design. The UI must say "same layout, two
    # orientations" rather than presenting them as two designs. Set by generate();
    # a hand-built Variant defaults to 0.
    arrangement: int = 0

    def to_dict(self) -> dict:
        d = self.layout.dump()
        d["svg"] = self.svg
        d["coverage"] = round(self.coverage, 4)
        d["arrangement"] = self.arrangement
        return d


@dataclass
class GenerateResult:
    variants: list[Variant] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempted: int = 0
    passed: int = 0

    def to_dict(self) -> dict:
        return {
            "variants": [v.to_dict() for v in self.variants],
            "warnings": self.warnings,
            "attempted": self.attempted,
            "passed": self.passed,
        }


def _signature(layout: Layout) -> tuple:
    """Distinctness key: preset + coarse room footprint."""
    rooms = tuple(
        sorted((r.zone or r.name, round(r.rect_m[0]), round(r.rect_m[1]), round(r.rect_m[2]), round(r.rect_m[3])) for r in layout.rooms)
    )
    return (layout.preset, rooms)


_EPS = 1e-6


def _arrangement_key(layout: Layout) -> tuple:
    """Canonical identity of the plan's ZONE packing, up to translation and the
    four dimension-preserving isometries of a rectangle.

    Two layouts with the same key are THE SAME ARRANGEMENT in different
    orientations — the same house flipped. This is decided exactly, at zone
    level, with no distance threshold anywhere, and zone level is what makes it
    exact: gW_eN and gE_eN differ at ROOM level (the Master Bathroom and the
    Walk-in Closet swap ends of the master service strip, room-level distance 2)
    but their nine zones — living, dining, kitchen_laundry, master_suite,
    children, office, entry, garage, circulation — are an exact mirror image of
    each other. Measured on roomy: gW_eN, gE_eN and gE_eW all share one key;
    gW_eW does not (room-level distance 7-9 from the other three).

    A threshold over _facade_distance would have needed a magic constant sitting
    between 2 and 7 with nothing but this one program to justify it. This needs
    no constant and cannot drift.
    """
    zones: dict[str, tuple[float, float, float, float]] = {}
    for rm in layout.rooms:
        key = rm.zone or rm.name
        x0, y0, x1, y1 = rm.rect_m
        if key in zones:
            a = zones[key]
            zones[key] = (min(a[0], x0), min(a[1], y0), max(a[2], x1), max(a[3], y1))
        else:
            zones[key] = (x0, y0, x1, y1)
    bx0 = min(v[0] for v in zones.values())
    by0 = min(v[1] for v in zones.values())
    bx1 = max(v[2] for v in zones.values())
    by1 = max(v[3] for v in zones.values())
    w, h = bx1 - bx0, by1 - by0
    forms = []
    for mx in (False, True):
        for my in (False, True):
            items = []
            for nm, (x0, y0, x1, y1) in zones.items():
                a, b = x0 - bx0, x1 - bx0
                c, e = y0 - by0, y1 - by0
                if mx:
                    a, b = w - b, w - a
                if my:
                    c, e = h - e, h - c
                items.append((nm, round(a, 3), round(c, 3), round(b, 3), round(e, 3)))
            forms.append((round(w, 3), round(h, 3), tuple(sorted(items))))
    return min(forms)


def _facade_roles(layout: Layout) -> dict[str, str]:
    """Each room -> the set of HOUSE-FOOTPRINT edges it touches ("NW", "S", "" for
    a landlocked room). This is the room's facade role, and the list of them is
    what a human actually reads when comparing two plans side by side: which zone
    sits on which face of the house.

    Measured against the bounding box of the rooms (the house), never the plot —
    the plot is mostly setback, and two plans that place the garage on opposite
    faces of the same-sized house must not come out identical because both sit in
    the middle of the same 20x24 site.
    """
    rects = [r.rect_m for r in layout.rooms]
    bx0 = min(r[0] for r in rects)
    by0 = min(r[1] for r in rects)
    bx1 = max(r[2] for r in rects)
    by1 = max(r[3] for r in rects)
    roles: dict[str, str] = {}
    for r in layout.rooms:
        x0, y0, x1, y1 = r.rect_m
        s = ""
        if abs(y1 - by1) < _EPS:
            s += "N"
        if abs(y0 - by0) < _EPS:
            s += "S"
        if abs(x0 - bx0) < _EPS:
            s += "W"
        if abs(x1 - bx1) < _EPS:
            s += "E"
        roles[r.name] = s
    return roles


_ROLE_ORDER = "NSWE"


def _flip_role(role: str, mx: bool, my: bool) -> str:
    """A facade role as seen after mirroring the house in x and/or y."""
    s = set(role)
    if mx:
        s = {"E" if c == "W" else "W" if c == "E" else c for c in s}
    if my:
        s = {"S" if c == "N" else "N" if c == "S" else c for c in s}
    return "".join(c for c in _ROLE_ORDER if c in s)


def _facade_distance(a: Variant, b: Variant) -> int:
    """How many rooms sit on a different face of the house in `a` than in `b`,
    MINIMISED over the four isometries of a rectangle that preserve its
    dimensions (identity, mirror-x, mirror-y, rotate-180).

    THE MINIMISATION IS THE WHOLE POINT, and it is a correction. The first cut of
    this took the plain Hamming distance and argued that a mirror "genuinely does
    move the garage from the west face to the east one". That is true about the
    SITE and false about the DRAWING, and the drawing is what gets reviewed. It
    scored gW_eN against gE_eN at 10 — near the top of the range — when the two
    are the same house flipped: 14 of their 16 rooms are exact mirror images,
    with identical areas, identical aspect ratios, an identical access tree and
    identical door counts, differing only in that the Master Bathroom and the
    Walk-in Closet swap ends of the master service strip. An architect opening
    both sees one plan twice. So the measure had to stop rewarding that, and
    under the minimum those two now score 2, which is what they are.

    Translation is already invisible: roles are measured against the house's own
    bounding box, so sliding the same packing across the plot changes nothing.
    """
    ra, rb = _facade_roles(a.layout), _facade_roles(b.layout)
    keys = set(ra) | set(rb)
    return min(
        sum(1 for k in keys if ra.get(k, "") != _flip_role(rb.get(k, ""), mx, my))
        for mx in (False, True)
        for my in (False, True)
    )


def _pick_distinct(cands: list[Variant], n: int) -> list[Variant]:
    """Greedy MAXIMIN over _facade_distance: take the best-scoring variant, then
    repeatedly take whichever remaining candidate is FURTHEST from everything
    already taken (ties -> higher objective, then coverage).

    Why not just `cands[:n]` ranked by objective, as this did before: the
    objective is an area/adjacency score, not a diversity score, and the top of
    that ranking is routinely several near-identical packings. On roomy the
    entry-W and entry-S families score 638.41 and 618.84 against the control's
    534.66 — the objective LIKES them better — so a pure-objective top-3 returns
    the two highest-scoring plans even when they differ in 2 rooms out of 16,
    while a genuinely different arrangement sits at rank 4. The fan-out exists
    for visual diversity; this selects for it directly instead of hoping the
    objective ordering happens to deliver it.

    A candidate at distance 0 from something already chosen is DROPPED, not
    returned as filler. Distance 0 means the identical packing arrived under a
    second label — which really happens: gE_eN and gE_eW produce a byte-identical
    plan, and every seed in DEFAULT_SEEDS reproduces it, because the solve proves
    OPTIMAL and the seed then has nothing left to vary. _signature cannot catch
    that: its key starts with layout.preset, so the same house under two preset
    names is two signatures. Returning n plans when the program only admits fewer
    is the defect, not a feature — the caller is told via GenerateResult.warnings.

    Deterministic for a fixed candidate list: `max` returns the first maximal
    element and `cands` arrives already sorted.
    """
    if not cands:
        return []
    chosen = [cands[0]]
    rest = list(cands[1:])
    while rest and len(chosen) < n:
        pick = max(
            rest,
            key=lambda c: (
                min(_facade_distance(c, s) for s in chosen),
                c.layout.objective,
                c.coverage,
            ),
        )
        rest.remove(pick)
        if min(_facade_distance(pick, s) for s in chosen) == 0:
            continue  # identical packing under another preset/seed label
        chosen.append(pick)
    return chosen


def generate(
    program: Program,
    n: int = 3,
    seeds: list[int] | None = None,
    time_limit_s: float = 12.0,
    workers: int = 8,
) -> GenerateResult:
    seeds = seeds or DEFAULT_SEEDS
    res = GenerateResult()
    seen: set[tuple] = set()
    # best passing variant per preset (drives visual diversity) + spares
    best_per_preset: dict[str, Variant] = {}
    spares: list[Variant] = []

    for preset in PRESETS:
        for seed in seeds:
            res.attempted += 1
            sr = solve(program, preset, seed=seed, time_limit_s=time_limit_s, workers=workers)
            if not sr.feasible:
                # solver.py's kitchen-direct constraint (the hard corridor<->
                # kitchen_laundry wall) can make an otherwise-buildable footprint
                # infeasible when the plot is too small for the corridor to also
                # reach the kitchen. Mirrors solve()'s own avoid-drop retry: try
                # again with kitchen-direct off, and if THAT succeeds, ship it —
                # visibly flagged as a real area limitation, never silently.
                sr = solve(
                    program, preset, seed=seed, time_limit_s=time_limit_s,
                    workers=workers, force_kitchen_direct=False,
                )
                if not sr.feasible:
                    continue
                sr.warnings.append(
                    f"{KITCHEN_FALLBACK_TAG}: footprint too small for the kitchen "
                    "to reach circulation directly; routed via dining/living "
                    "instead (area limitation, not a defect)"
                )
            layout = build_layout(sr, program)
            # Surface solver-level diagnostics (e.g. the kitchen-fallback tag)
            # BEFORE validating: validate_plan's authorized-exception check
            # reads layout.warnings, and validate() seeds its own warnings list
            # from layout.warnings at call time — so this has to happen first,
            # not after, or the fallback's own disclosure would be invisible to
            # the gate it's meant to satisfy.
            layout.warnings = sr.warnings + layout.warnings
            v = validate(layout, program)
            if not v.ok:
                continue
            res.passed += 1
            sig = _signature(layout)
            if sig in seen:
                continue
            seen.add(sig)
            layout.warnings = v.warnings
            var = Variant(layout=layout, svg=render(layout), coverage=v.coverage)
            cur = best_per_preset.get(preset)
            if cur is None or layout.objective > cur.layout.objective:
                if cur is not None:
                    spares.append(cur)
                best_per_preset[preset] = var
            else:
                spares.append(var)

    # rank distinct presets first, then backfill from spares (different seeds).
    # The per-preset bests go through the maximin picker so the returned set is
    # chosen for how DIFFERENT the plans look, not only for how well they score;
    # spares (same preset, another seed) stay a plain objective-ordered backfill
    # because they are by construction the least diverse thing on offer.
    ranked = sorted(best_per_preset.values(), key=lambda c: (c.layout.objective, c.coverage), reverse=True)
    spares.sort(key=lambda c: (c.layout.objective, c.coverage), reverse=True)
    # ONE pool, not `_pick_distinct(ranked) + spares`. Appending the spares after
    # the picker would put the duplicates straight back: on roomy every spare is
    # the same packing as some preset-best, so the old concatenation padded the
    # result to n with copies. Feeding both through the picker lets a genuinely
    # different spare compete, and lets a duplicate one be dropped.
    res.variants = _pick_distinct(ranked + spares, n)

    # Tag each returned plan with WHICH ARRANGEMENT it is, so a caller can tell
    # "two designs" from "one design, two orientations". Both are worth shipping
    # — garage west vs garage east is a real site decision, it picks the driveway
    # side — but they must not be PRESENTED as two designs.
    keys: dict[tuple, int] = {}
    for v in res.variants:
        k = _arrangement_key(v.layout)
        if k not in keys:
            keys[k] = len(keys)
        v.arrangement = keys[k]

    n_arrangements = len(keys)
    if n_arrangements < min(n, 3):
        # Count ARRANGEMENTS, not variants. Saying "2 distinct variants" when
        # both are the same house mirrored overstates what was found, and this
        # warning is the only thing standing between that fact and the UI.
        extra = ""
        if len(res.variants) > n_arrangements:
            extra = (
                f" ({len(res.variants)} variants returned, but they are the same "
                f"arrangement in different orientations, not different designs)"
            )
        res.warnings.append(
            f"only {n_arrangements} distinct arrangement(s) found; wanted {n}{extra}"
        )
    return res
