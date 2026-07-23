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

    # rank distinct presets first, then backfill from spares (different seeds)
    ranked = sorted(best_per_preset.values(), key=lambda c: (c.layout.objective, c.coverage), reverse=True)
    spares.sort(key=lambda c: (c.layout.objective, c.coverage), reverse=True)
    chosen = ranked + spares
    res.variants = chosen[:n]
    if len(res.variants) < min(n, 3):
        res.warnings.append(f"only {len(res.variants)} distinct passing variant(s); wanted {n}")
    return res
