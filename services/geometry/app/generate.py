"""Fan out presets x seeds, gate on the validator, rank, keep top-N distinct.

This is the M2 pipeline: the only variants that leave here are ones the
validator has already passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import placement, subdivide
from .models import Layout, Program
from .presets import PRESETS
from .solver import KITCHEN_FALLBACK_TAG, SolveResult, solve
from .slicer import FinalRoom, _cut_score, build_layout, zone_members
from .svg import render
from .validator import validate
from .zones import COMPOSITE_ZONES

DEFAULT_SEEDS = [1, 2, 3, 4]

# Composite zones in a FIXED order, so the alternatives a program yields are a
# deterministic function of the program and the seed.
_COMPOSITE_ORDER = tuple(z for z in ("kitchen_laundry", "master_suite",
                                     "children", "entry")
                         if z in COMPOSITE_ZONES)


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
    # Which SUBDIVISION of that arrangement this plan is, 0-based, 0 = the cut
    # the four cutters produce. A SEPARATE field on purpose: `arrangement`
    # answers "same zone layout?" and two plans that differ only inside a zone
    # genuinely ARE the same zone layout, so overloading it would make the field
    # lie in the one case it exists to describe. The pair
    # (arrangement, subdivision) identifies a plan; `arrangement` alone still
    # answers the handedness question it always did.
    subdivision: int = 0

    def to_dict(self) -> dict:
        d = self.layout.dump()
        d["svg"] = self.svg
        d["coverage"] = round(self.coverage, 4)
        d["arrangement"] = self.arrangement
        d["subdivision"] = self.subdivision
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


def _rect_multiset(layout: Layout, mx: bool, my: bool) -> list[tuple]:
    """Every room's rect, normalised to the house's own bounding box and
    optionally mirrored. NAMES ARE DELIBERATELY DROPPED — see _plan_distance."""
    rects = [r.rect_m for r in layout.rooms]
    bx0 = min(r[0] for r in rects)
    by0 = min(r[1] for r in rects)
    w = max(r[2] for r in rects) - bx0
    h = max(r[3] for r in rects) - by0
    out = []
    for x0, y0, x1, y1 in rects:
        a, b = x0 - bx0, x1 - bx0
        c, e = y0 - by0, y1 - by0
        if mx:
            a, b = w - b, w - a
        if my:
            c, e = h - e, h - c
        out.append((round(a, 3), round(c, 3), round(b, 3), round(e, 3)))
    return sorted(out)


def _subdivision_key(layout: Layout) -> tuple:
    """Canonical identity of the plan's ROOM geometry, up to the same four
    symmetries `_arrangement_key` uses — so the same cut seen mirrored is one
    subdivision, and a pure relabelling (children's Bedroom 2/3 swap) is too."""
    return min(tuple(_rect_multiset(layout, mx, my))
               for mx in (False, True) for my in (False, True))


def _geometry_mismatch(ga: list[tuple], gb: list[tuple]) -> int:
    """How many of `ga`'s rects have no counterpart in `gb` (with multiplicity)."""
    from collections import Counter

    ca, cb = Counter(ga), Counter(gb)
    return sum((ca - cb).values())


def _plan_distance(a: Variant, b: Variant) -> int:
    """How different two plans LOOK: facade-role mismatches PLUS rooms whose
    rectangle has no counterpart in the other plan — both measured under the SAME
    symmetry, minimised over the four isometries of a rectangle.

    WHY _facade_distance ALONE IS NOT ENOUGH ANY MORE. It was built to compare
    plans that differ in ZONE placement and it answers exactly that: which room
    sits on which face of the house. Subdivision alternatives at a single
    arrangement routinely change no facade role at all, so it scores them 0 and
    _pick_distinct drops them as duplicates. Measured on roomy @208, of the five
    score-equal alternatives the filter accepts, TWO are visibly different plans
    that it cannot see: the master service strip's divider moving 0.5 m (Master
    Bathroom 7.50 <-> 6.25 against the Walk-in Closet) and the entry's moving
    0.5 m (Mudroom 3.00 -> 4.00 against the Foyer). Both are real to anyone
    reading the drawing.

    WHY NAMES ARE DROPPED FROM THE GEOMETRY TERM, which is the other half and the
    part that keeps this honest. The children zone offers an alternative in which
    Bedroom 2 and Bedroom 3 SWAP: identical rectangles, identical areas, only the
    two labels exchanged. The drawing is byte-identical. A name-keyed measure
    would score that 2 and ship a relabelling as a second design; comparing rect
    MULTISETS scores it 0 and it is correctly dropped. So this extension makes
    the measure see more, not simply see differently — the one relabelling in the
    set stays invisible, exactly as it should.

    Both terms use the same (mx, my) so the minimum is coherent; taking each
    term's own minimum over different symmetries would be comparing two plans in
    two orientations at once. The symmetry minimisation itself is inherited from
    _facade_distance, and it is load-bearing for the reason recorded there: a
    near-mirror must not score as maximally distant."""
    ra, rb = _facade_roles(a.layout), _facade_roles(b.layout)
    keys = set(ra) | set(rb)
    ga = _rect_multiset(a.layout, False, False)
    return min(
        sum(1 for k in keys if ra.get(k, "") != _flip_role(rb.get(k, ""), mx, my))
        + _geometry_mismatch(ga, _rect_multiset(b.layout, mx, my))
        for mx in (False, True)
        for my in (False, True)
    )


def subdivision_variants(
    result: SolveResult, base: Layout
) -> list[tuple[str, dict[str, list[FinalRoom]]]]:
    """Alternative subdivisions of an ALREADY-SOLVED arrangement: (zone, override)
    pairs whose layout the validator passes with no errors.

    One composite zone is varied at a time, against the solved rectangles the
    solver already committed to. No re-solve happens and no shape table is
    touched, so this cannot move the packing, the objective or the golden.

    THE SCORE-EQUAL RESTRICTION IS THE LOAD-BEARING PART. Only alternatives whose
    `_cut_score` equals the default cut's are offered. The solver's objective
    contains a `cut_penalty` term tabulated for the cut it expected, so an
    alternative scoring differently would make the variant's reported objective
    wrong for that variant — and `slicer._penalty_disagreement` would say so,
    correctly. Restricting to ties means every returned plan carries the solve's
    own objective exactly, with every term identical, and that guard stays silent
    by construction rather than by suppression.

    Measured on roomy @208, both presets: 25 alternatives pass the placement
    filter, 5 of them are score-equal, and all 5 rebuild validator-clean."""
    out: list[tuple[str, dict[str, list[FinalRoom]]]] = []
    fx0, fy0, fx1, fy1 = result.footprint_m or (0.0, 0.0, 0.0, 0.0)
    by_zone = {z.zone: z for z in result.rects}
    default: dict[str, list[FinalRoom]] = {}
    for rm in base.rooms:
        if rm.zone in _COMPOSITE_ORDER:
            default.setdefault(rm.zone, []).append(
                FinalRoom(rm.name, rm.category, rm.zone, tuple(rm.rect_m))
            )
    for zone in _COMPOSITE_ORDER:
        zr = by_zone.get(zone)
        base_cut = default.get(zone)
        if zr is None or not base_cut:
            continue
        rect = tuple(zr.rect_m)
        members = zone_members(zone)
        if len(base_cut) < len(members):
            continue  # the default cut degraded here; nothing to vary
        base_score = _cut_score([(r.name, r.rect) for r in base_cut])
        base_key = subdivide.canonical(base_cut)
        faces = set()
        if abs(rect[3] - fy1) < _EPS:
            faces.add("N")
        if abs(rect[1] - fy0) < _EPS:
            faces.add("S")
        if abs(rect[0] - fx0) < _EPS:
            faces.add("W")
        if abs(rect[2] - fx1) < _EPS:
            faces.add("E")
        ctx = placement.Context(
            zone=zone,
            corridor_side=result.corridor_sides.get(zone),
            director_side=result.cut_sides.get(zone),
            exterior_faces=frozenset(faces),
        )
        rooms = [subdivide.SubRoom(r.name, r.category) for r in base_cut]
        for cand in subdivide.subdivisions(
            (0.0, 0.0, rect[2] - rect[0], rect[3] - rect[1]), rooms, zone=zone
        ):
            shifted = [
                FinalRoom(c.name, c.category, zone,
                          (c.rect[0] + rect[0], c.rect[1] + rect[1],
                           c.rect[2] + rect[0], c.rect[3] + rect[1]))
                for c in cand
            ]
            if subdivide.canonical(shifted) == base_key:
                continue
            if _cut_score([(r.name, r.rect) for r in shifted]) != base_score:
                continue
            if placement.violations(shifted, rect, ctx):
                continue
            out.append((zone, {zone: shifted}))
    return out


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
                min(_plan_distance(c, s) for s in chosen),
                c.layout.objective,
                c.coverage,
            ),
        )
        rest.remove(pick)
        if min(_plan_distance(pick, s) for s in chosen) == 0:
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
    best_src: dict[str, SolveResult] = {}
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
                best_src[preset] = sr
            else:
                spares.append(var)

    # SUBDIVISION FAN-OUT. The zone packing yields exactly one arrangement on this
    # program (six exhausted levers), so handedness is the only diversity the
    # preset axis can offer. Subdivision is the axis that measured positive, and
    # this is where it enters: for each preset's best plan, rebuild the SAME
    # solved arrangement with each score-equal alternative subdivision the
    # placement filter accepts, and let the picker compete them against the
    # originals. Only the per-preset bests are expanded -- the spares are by
    # construction the same packing under another seed, so expanding them would
    # multiply duplicates and cost solve-time for nothing.
    for preset, base_var in list(best_per_preset.items()):
        sr = best_src.get(preset)
        if sr is None:
            continue
        for i, (_zone, override) in enumerate(
            subdivision_variants(sr, base_var.layout), start=1
        ):
            alt = build_layout(sr, program, overrides=override)
            alt.warnings = sr.warnings + alt.warnings
            av = validate(alt, program)
            if not av.ok or av.errors:
                continue
            sig = _signature(alt)
            if sig in seen:
                continue
            seen.add(sig)
            alt.warnings = av.warnings
            spares.append(Variant(layout=alt, svg=render(alt),
                                  coverage=av.coverage, subdivision=i))

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

    # ...and WHICH SUBDIVISION of it, renumbered densely over the RETURNED set.
    # The index each alternative carried until now was its position in the
    # enumeration, which is stable but sparse — the first alternative actually
    # returned was #5, because four earlier ones lost the dedupe or the picker.
    # An id that skips is an id a UI cannot show. Keyed on the rect multiset
    # canonicalised over the four symmetries, exactly as `arrangement` is, so the
    # same cut seen mirrored keeps ONE id; and the default cut is numbered first
    # so the shipped plan is always subdivision 0.
    sub_ids: dict[tuple, int] = {}
    for v in sorted(res.variants, key=lambda x: x.subdivision != 0):
        sk = _subdivision_key(v.layout)
        if sk not in sub_ids:
            sub_ids[sk] = len(sub_ids)
    for v in res.variants:
        v.subdivision = sub_ids[_subdivision_key(v.layout)]

    n_arrangements = len(keys)
    if n_arrangements < min(n, 3):
        # Count ARRANGEMENTS, not variants. Saying "2 distinct variants" when
        # both are the same house mirrored overstates what was found, and this
        # warning is the only thing standing between that fact and the UI.
        extra = ""
        if len(res.variants) > n_arrangements:
            n_sub = len({(v.arrangement, v.subdivision) for v in res.variants})
            how = "in different orientations"
            if n_sub > n_arrangements:
                # Say which axis the extra plans actually came from. "Different
                # orientations" was the only possibility before the subdivision
                # fan-out existed; asserting it now would misdescribe a plan whose
                # rooms really are cut differently.
                how = ("in different orientations and/or with a different internal "
                       "subdivision")
            extra = (
                f" ({len(res.variants)} variants returned, but they are the same "
                f"arrangement {how}, not different designs)"
            )
        res.warnings.append(
            f"only {n_arrangements} distinct arrangement(s) found; wanted {n}{extra}"
        )
    return res
