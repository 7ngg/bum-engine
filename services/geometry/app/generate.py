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

    def to_dict(self) -> dict:
        d = self.layout.dump()
        d["svg"] = self.svg
        d["coverage"] = round(self.coverage, 4)
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


def _facade_distance(a: Variant, b: Variant) -> int:
    """How many rooms sit on a different face of the house in `a` than in `b`.

    A plain Hamming distance over _facade_roles. Two plans that are translations
    of each other score 0 (the footprint bbox is the frame, so translation is
    invisible to it, which is the point); a mirror scores high, because a mirror
    genuinely does move the garage from the west face to the east one.
    """
    ra, rb = _facade_roles(a.layout), _facade_roles(b.layout)
    return sum(1 for k in set(ra) | set(rb) if ra.get(k) != rb.get(k))


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
        chosen.append(pick)
        rest.remove(pick)
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
    chosen = _pick_distinct(ranked, n) + spares
    res.variants = chosen[:n]
    if len(res.variants) < min(n, 3):
        res.warnings.append(f"only {len(res.variants)} distinct passing variant(s); wanted {n}")
    return res
