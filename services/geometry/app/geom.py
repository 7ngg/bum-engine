"""Small axis-aligned-rectangle geometry helpers shared across the service.

Rect = (x0, y0, x1, y1) with x0<x1, y0<y1, metres.
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-6

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Edge:
    orient: str  # "V" (vertical, const x) | "H" (horizontal, const y)
    fixed: float  # x for V, y for H
    lo: float  # y-range for V, x-range for H
    hi: float

    @property
    def length(self) -> float:
        return self.hi - self.lo

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0


def area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def overlap_area(a: Rect, b: Rect) -> float:
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx > EPS and dy > EPS:
        return dx * dy
    return 0.0


def shared_edge(a: Rect, b: Rect) -> Edge | None:
    """Return the shared wall segment if a and b are edge-adjacent, else None.

    Adjacent = touching along a line with positive overlap (not merely a
    corner). Overlapping interiors do not count.
    """
    if overlap_area(a, b) > EPS:
        return None
    # vertical shared wall
    for fx in (a[2], a[0]):
        if abs(fx - b[0]) < EPS or abs(fx - b[2]) < EPS:
            lo = max(a[1], b[1])
            hi = min(a[3], b[3])
            if hi - lo > EPS:
                # ensure the rects are actually on opposite sides of fx
                if (abs(a[2] - b[0]) < EPS) or (abs(b[2] - a[0]) < EPS):
                    return Edge("V", fx, lo, hi)
    # horizontal shared wall
    for fy in (a[3], a[1]):
        if abs(fy - b[1]) < EPS or abs(fy - b[3]) < EPS:
            lo = max(a[0], b[0])
            hi = min(a[2], b[2])
            if hi - lo > EPS:
                if (abs(a[3] - b[1]) < EPS) or (abs(b[3] - a[1]) < EPS):
                    return Edge("H", fy, lo, hi)
    return None


def gap(a: Rect, b: Rect) -> float:
    """Minimum separation distance between two rects (0 if touching/overlapping)."""
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    if dx == 0.0 and dy == 0.0:
        return 0.0
    if dx > 0.0 and dy > 0.0:
        return (dx * dx + dy * dy) ** 0.5
    return max(dx, dy)


def adjacent(a: Rect, b: Rect, min_len: float = 0.0) -> bool:
    """True if a and b share a wall of at least min_len metres."""
    e = shared_edge(a, b)
    return e is not None and e.length >= min_len - EPS
