"""General guillotine subdivider — MACHINERY ONLY, deliberately NOT the
production path yet.

`slice_zones` still calls the four bespoke cutters in `slicer.py`. This module
adds the general enumerator that would eventually replace them, plus nothing
else: no solver constraint reads it, no layout is built from it, and the golden
signature cannot move because of it. What it buys today is a MEASUREMENT that
could not be taken any other way — how many distinct legal subdivisions actually
exist at a given zone rectangle, versus the one the bespoke cutter happens to
find. The four cutters are the reference oracle (tests/test_subdivide.py), so the
enumerator is pinned to agree with them on their own ground before it is ever
trusted anywhere else.

WHAT IT ENUMERATES. Every binary GUILLOTINE partition of the rectangle to depth
<= 2 (so up to four leaves), over both axes, every grid-aligned offset and every
assignment of the supplied rooms to leaves, filtered by `slicer._in_band` — the
same single legality predicate the cutters and `legal_pairs()` already share, so
a shape this module calls legal is legal by exactly the definition already in
force. Depth 2 is not an arbitrary cap: it is what covers all four current
cutters, measured in the Phase 0 audit --

    kitchen_laundry  1 cut                       (Kitchen | Laundry)
    children         2 parallel cuts             (Bedroom 2 | Bathroom | Bedroom 3)
    master_suite     1 cut + 1 perpendicular     (Bedroom / [Bathroom | Closet])
    entry            1 cut + 1 perpendicular     (Mudroom / [WC | Foyer])
                     with an axis fallback

-- and it also covers the cases the cutters cannot express at all: three bedrooms
in the children zone (four leaves, reachable as four parallel bands when the two
inner cuts share the outer axis), a kitchen with no laundry, an entry with no
mudroom, one bedroom instead of two.

THE ROOM LIST IS AN INPUT, and that is the point of the signature. Today
`slicer.zone_members()` DERIVES membership by probing the cutter for "the fullest
split it can emit", which makes a legitimately laundry-less brief indistinguishable
from a zone that is merely too small. Handing the room list in removes that
conflation. The ZONE list is deliberately NOT opened here: `models.ZoneId` stays a
closed nine-value Literal, because `layout.schema.json` already treats room and
zone names as free strings, so nothing downstream needs it.

WHAT IT DOES NOT DO YET, recorded so nobody assumes otherwise:
  - It applies no PLACEMENT constraint. Which sub-room fronts the corridor, which
    end the Mudroom takes, that the Kitchen holds the dining face, that the Guest
    WC never lands between Mudroom and Foyer -- every one of those is a rule about
    WHICH candidate to pick, not about which candidates exist, and each is
    currently held by construction inside a bespoke cutter (see the Phase 0
    audit's sixteen implicit constraints). They must be applied as filters on this
    enumerator's output before it can become the production path.
  - It uses `_in_band` as-is, which is rotation-naive. `standards.Bathroom` is an
    ORIENTED standard (min_w_m 2.4 is along the fixture run, min_h_m 1.7 is the
    depth in front of it) and `slicer._slice_children_ns` transposes the rect
    before checking it. That transpose is a per-room property this enumerator does
    not yet carry; it matches every cutter reachable in production today
    (`slicer._CHILD_AXIAL` is False, so `_slice_children_ns` is dead code), and it
    is the first thing to add when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Sequence

from . import geom, standards
from .models import Category
from .slicer import FinalRoom, _in_band
from .solver import GRID_M

# Deepest nesting of guillotine cuts along any root-to-leaf path. 2 gives up to
# four leaves, which covers every current cutter and the four-room children zone.
MAX_DEPTH = 2

# Axis order is FIXED and load-bearing for tie-breaks: "y" first reproduces the
# band direction of _slice_master and _slice_children, which is what lets
# _best_cut over this enumerator's output agree with the cutters' own pick when
# two candidates score identically. See tests/test_subdivide.py.
_AXES = ("y", "x")


@dataclass(frozen=True)
class SubRoom:
    """One room the subdivider must place. `name` keys everything else --
    standards.ROOMS for the shape floor, architect_band for the area band,
    priority_tier for the weight -- so this carries only what a name cannot."""

    name: str
    category: Category


@dataclass(frozen=True)
class _Spec:
    """Per-name constants, hoisted out of the recursion."""

    min_w: float
    min_h: float
    floor: float
    ceiling: float


def _specs(names: Iterable[str]) -> dict[str, _Spec]:
    out: dict[str, _Spec] = {}
    for n in names:
        s = standards.ROOMS.get(n)
        out[n] = _Spec(
            min_w=s.min_w_m if s is not None else 0.0,
            min_h=s.min_h_m if s is not None else 0.0,
            floor=standards.area_floor(n),
            ceiling=standards.area_ceiling(n),
        )
    return out


def _fits(rect: geom.Rect, names: Sequence[str], spec: dict[str, _Spec]) -> bool:
    """Cheap necessary condition: could this rect possibly hold these rooms?

    Three prunes, all sound (they can only reject partitions `_in_band` would
    reject anyway), and together they are what keeps the enumeration tractable --
    a 15 x 15 m probe rect is rejected at the root for master_suite because its
    225 m2 is far above the members' summed ceiling.
      1. every room's rect is contained in this one, so this one must clear the
         LARGEST min_w and min_h among them -- and per-axis, not rotated, because
         the standards are oriented;
      2. the leaves tile this rect exactly, so its area must lie between the
         summed floors and the summed ceilings;
      3. (implied by 2 for a single room) it must clear that room's own floor.
    """
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    lo = hi = 0.0
    for n in names:
        s = spec[n]
        if w < s.min_w - geom.EPS or h < s.min_h - geom.EPS:
            return False
        lo += s.floor
        hi += s.ceiling
    a = w * h
    return a >= lo - geom.EPS and a <= hi + geom.EPS


def _enumerate(
    rect: geom.Rect,
    names: tuple[str, ...],
    depth: int,
    spec: dict[str, _Spec],
    out: list[list[tuple[str, geom.Rect]]],
) -> None:
    """All guillotine partitions of `rect` assigning exactly `names` to leaves,
    using at most `depth` further cuts along any path. Appends to `out`."""
    if len(names) == 1:
        if _in_band(names[0], rect):
            out.append([(names[0], rect)])
        return
    if depth <= 0:
        return
    x0, y0, x1, y1 = rect
    wu = int(round((x1 - x0) / GRID_M))
    hu = int(round((y1 - y0) / GRID_M))
    n_names = len(names)
    idx = range(n_names)
    for axis in _AXES:
        steps = hu if axis == "y" else wu
        for cut in range(1, steps):
            off = cut * GRID_M
            if axis == "y":
                lo_rect = (x0, y0, x1, y0 + off)
                hi_rect = (x0, y0 + off, x1, y1)
            else:
                lo_rect = (x0, y0, x0 + off, y1)
                hi_rect = (x0 + off, y0, x1, y1)
            for k in range(1, n_names):
                for lo_idx in combinations(idx, k):
                    lo_set = set(lo_idx)
                    lo_names = tuple(names[i] for i in lo_idx)
                    hi_names = tuple(names[i] for i in idx if i not in lo_set)
                    if not _fits(lo_rect, lo_names, spec):
                        continue
                    if not _fits(hi_rect, hi_names, spec):
                        continue
                    lo_parts: list[list[tuple[str, geom.Rect]]] = []
                    _enumerate(lo_rect, lo_names, depth - 1, spec, lo_parts)
                    if not lo_parts:
                        continue
                    hi_parts: list[list[tuple[str, geom.Rect]]] = []
                    _enumerate(hi_rect, hi_names, depth - 1, spec, hi_parts)
                    for a in lo_parts:
                        for b in hi_parts:
                            out.append(a + b)


def canonical(rooms: Sequence[FinalRoom]) -> tuple:
    """Identity of a subdivision, independent of the order a producer emits it
    in. This is what makes the four cutters comparable with the enumerator at
    all: `_slice_master` returns [Bathroom, Closet, Bedroom] under an "N"
    corridor and [Bedroom, Bathroom, Closet] otherwise, and both describe the
    same partition."""
    return tuple(sorted((r.name, tuple(r.rect)) for r in rooms))


def subdivisions(
    rect: geom.Rect,
    rooms: Sequence[SubRoom],
    side: str | None = None,
    zone: str = "",
    max_depth: int = MAX_DEPTH,
) -> list[list[FinalRoom]]:
    """Every distinct legal guillotine subdivision of `rect` into `rooms`.

    Deduplicated (the same partition is reachable through more than one slicing
    tree once two cuts share an axis) keeping FIRST-SEEN order, so the order is a
    deterministic function of the arguments -- which the build-time/slice-time
    contract depends on (see slicer._penalty_disagreement).

    Rooms inside each candidate come back in GEOMETRIC order, low coordinate
    first, matching `slicer._split_off_wc`'s existing convention.

    `side` is accepted and currently unused. It is kept in the signature because
    the bespoke cutters key their PLACEMENT rules off it -- Kitchen toward Dining,
    Mudroom toward the Garage, the master service strip away from the corridor --
    and those are constraints on WHICH candidate is chosen, not on which exist.
    This enumerator already subsumes the axis and mirror choices `side` encodes,
    so it needs no filtering to be complete; it will need it to be correct as a
    production path.
    """
    names = tuple(r.name for r in rooms)
    if not names:
        return []
    cat = {r.name: r.category for r in rooms}
    spec = _specs(names)
    rect = (rect[0], rect[1], rect[2], rect[3])
    if not _fits(rect, names, spec):
        return []
    raw: list[list[tuple[str, geom.Rect]]] = []
    _enumerate(rect, names, max_depth, spec, raw)
    seen: dict[tuple, list[FinalRoom]] = {}
    for parts in raw:
        parts = sorted(parts, key=lambda p: (p[1][0], p[1][1]))
        key = tuple(sorted((n, tuple(rc)) for n, rc in parts))
        if key not in seen:
            seen[key] = [FinalRoom(n, cat[n], zone, rc) for n, rc in parts]
    return list(seen.values())
