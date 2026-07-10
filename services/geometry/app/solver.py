"""CP-SAT macro-zone placement solver.

The solver owns geometry. It places the ~8 macro-zones as free axis-aligned
rectangles on a 0.5 m grid, guaranteeing (hard) plot-fit, non-overlap, exact
min-dimensions, required/forbidden adjacencies and zoning; and optimising
(soft) coverage, desirable adjacencies and daylight orientation.

All lengths are handled internally in grid units of GRID_M metres. Results are
returned in metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .models import Program, ZoneId
from .presets import Pins, resolve
from . import zones as Z

GRID_M = 0.5  # one grid cell edge, metres
AREA_LO = 0.72  # min area as fraction of target
AREA_HI = 1.45  # max area as fraction of target


def _u(meters: float) -> int:
    """Metres -> grid units, rounded to nearest cell."""
    return int(round(meters / GRID_M))


def _ceil_u(meters: float) -> int:
    return int(math.ceil(meters / GRID_M - 1e-9))


@dataclass
class ZoneRect:
    zone: ZoneId
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def rect_m(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass
class SolveResult:
    status: str
    feasible: bool
    objective: float
    preset: str
    seed: int
    rects: list[ZoneRect]
    plot_w_m: float
    plot_d_m: float
    wall_time_s: float


class _ZoneVars:
    __slots__ = ("x0", "y0", "x1", "y1", "w", "h", "area", "xi", "yi")

    def __init__(self, m: cp_model.CpModel, zid: str, W: int, H: int, min_w: int, min_h: int):
        self.x0 = m.NewIntVar(0, W, f"{zid}_x0")
        self.y0 = m.NewIntVar(0, H, f"{zid}_y0")
        self.x1 = m.NewIntVar(0, W, f"{zid}_x1")
        self.y1 = m.NewIntVar(0, H, f"{zid}_y1")
        self.w = m.NewIntVar(min_w, W, f"{zid}_w")
        self.h = m.NewIntVar(min_h, H, f"{zid}_h")
        m.Add(self.x1 == self.x0 + self.w)
        m.Add(self.y1 == self.y0 + self.h)
        self.area = m.NewIntVar(min_w * min_h, W * H, f"{zid}_area")
        m.AddMultiplicationEquality(self.area, [self.w, self.h])
        self.xi = m.NewIntervalVar(self.x0, self.w, self.x1, f"{zid}_xi")
        self.yi = m.NewIntervalVar(self.y0, self.h, self.y1, f"{zid}_yi")


def _apply_pins(m: cp_model.CpModel, zv: _ZoneVars, pins: Pins, W: int, H: int) -> None:
    if pins.south:
        m.Add(zv.y0 == 0)
    if pins.north:
        m.Add(zv.y1 == H)
    if pins.west:
        m.Add(zv.x0 == 0)
    if pins.east:
        m.Add(zv.x1 == W)
    if pins.max_y1_frac is not None:
        m.Add(zv.y1 <= int(math.floor(pins.max_y1_frac * H)))


def _share_wall(
    m: cp_model.CpModel, a: _ZoneVars, b: _ZoneVars, min_len: int, tag: str
) -> cp_model.IntVar:
    """Reified boolean: true => a and b share a wall segment >= min_len units.

    Four configs (a west/east/south/north of b). The bool is one-directional
    (share -> real adjacency); the solver only sets it true when it can realise
    a config, which forces a genuine shared wall. Good enough for both a hard
    requirement (force share == 1) and a soft reward (reward only if realised).
    """
    y_lo = m.NewIntVar(0, 10_000, f"{tag}_ylo")
    y_hi = m.NewIntVar(0, 10_000, f"{tag}_yhi")
    m.AddMaxEquality(y_lo, [a.y0, b.y0])
    m.AddMinEquality(y_hi, [a.y1, b.y1])
    x_lo = m.NewIntVar(0, 10_000, f"{tag}_xlo")
    x_hi = m.NewIntVar(0, 10_000, f"{tag}_xhi")
    m.AddMaxEquality(x_lo, [a.x0, b.x0])
    m.AddMinEquality(x_hi, [a.x1, b.x1])

    configs = []
    # a west of b: a.x1 == b.x0, vertical wall, y-overlap >= min_len
    c = m.NewBoolVar(f"{tag}_aWb")
    m.Add(a.x1 == b.x0).OnlyEnforceIf(c)
    m.Add(y_hi - y_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    # a east of b: b.x1 == a.x0
    c = m.NewBoolVar(f"{tag}_aEb")
    m.Add(b.x1 == a.x0).OnlyEnforceIf(c)
    m.Add(y_hi - y_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    # a south of b: a.y1 == b.y0, horizontal wall, x-overlap >= min_len
    c = m.NewBoolVar(f"{tag}_aSb")
    m.Add(a.y1 == b.y0).OnlyEnforceIf(c)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    # a north of b: b.y1 == a.y0
    c = m.NewBoolVar(f"{tag}_aNb")
    m.Add(b.y1 == a.y0).OnlyEnforceIf(c)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)

    share = m.NewBoolVar(f"{tag}_share")
    # share == OR(configs)
    m.AddMaxEquality(share, configs)
    return share


def _forbid_adjacent(
    m: cp_model.CpModel, a: _ZoneVars, b: _ZoneVars, gap: int, tag: str
) -> None:
    """Force a and b apart by >= gap units in at least one axis."""
    sels = []
    s = m.NewBoolVar(f"{tag}_aWb")
    m.Add(a.x1 + gap <= b.x0).OnlyEnforceIf(s)
    sels.append(s)
    s = m.NewBoolVar(f"{tag}_aEb")
    m.Add(b.x1 + gap <= a.x0).OnlyEnforceIf(s)
    sels.append(s)
    s = m.NewBoolVar(f"{tag}_aSb")
    m.Add(a.y1 + gap <= b.y0).OnlyEnforceIf(s)
    sels.append(s)
    s = m.NewBoolVar(f"{tag}_aNb")
    m.Add(b.y1 + gap <= a.y0).OnlyEnforceIf(s)
    sels.append(s)
    m.AddBoolOr(sels)


def solve(
    program: Program,
    preset: str,
    seed: int = 0,
    time_limit_s: float = 12.0,
    workers: int = 8,
) -> SolveResult:
    spec = resolve(preset)
    W = _u(program.plot.width_m)
    H = _u(program.plot.depth_m)
    plot_cells = W * H

    m = cp_model.CpModel()

    present: list[ZoneId] = [z for z in Z.ZONE_ORDER if program.space(z) is not None]
    zv: dict[ZoneId, _ZoneVars] = {}
    for zid in present:
        sp = program.space(zid)
        assert sp is not None
        min_w = max(1, _ceil_u(sp.min_w_m))
        min_h = max(1, _ceil_u(sp.min_h_m))
        v = _ZoneVars(m, zid, W, H, min_w, min_h)
        # area window in cells
        target_cells = sp.target_m2 / (GRID_M * GRID_M)
        lo = max(min_w * min_h, int(math.floor(AREA_LO * target_cells)))
        hi = min(plot_cells, int(math.ceil(AREA_HI * target_cells)))
        m.Add(v.area >= lo)
        m.Add(v.area <= hi)
        # aspect: w <= 3h, h <= 3w
        m.Add(v.w <= 3 * v.h)
        m.Add(v.h <= 3 * v.w)
        # hard zoning pins
        _apply_pins(m, v, spec.pins.get(zid, Pins()), W, H)
        zv[zid] = v

    # non-overlap over all zones
    m.AddNoOverlap2D([v.xi for v in zv.values()], [v.yi for v in zv.values()])

    share_min = _ceil_u(Z.REQUIRED_SHARE_M)
    gap_u = _ceil_u(Z.FORBIDDEN_GAP_M)

    # required adjacency (hard)
    for a, b in Z.REQUIRED_ADJ:
        if a in zv and b in zv:
            sh = _share_wall(m, zv[a], zv[b], share_min, f"req_{a}_{b}")
            m.Add(sh == 1)

    # forbidden adjacency (hard)
    for a, b in Z.FORBIDDEN_ADJ:
        if a in zv and b in zv:
            _forbid_adjacent(m, zv[a], zv[b], gap_u, f"fbd_{a}_{b}")

    # soft adjacency (reward). Any shared wall >= one grid cell counts.
    soft_bools = []
    for a, b in Z.SOFT_ADJ:
        if a in zv and b in zv:
            soft_bools.append(_share_wall(m, zv[a], zv[b], 1, f"soft_{a}_{b}"))

    # --- objective (scaled by plot_cells to keep integer coefficients) --------
    # human objective = 12*coverage_pct + 40*soft_met - 3*public_non_south
    #                 + 2*service_northness ; coverage_pct = 100*area/plot.
    total_area = m.NewIntVar(0, plot_cells, "total_area")
    m.Add(total_area == sum(v.area for v in zv.values()))

    obj_terms: list[cp_model.LinearExpr] = [12 * 100 * total_area]

    if soft_bools:
        obj_terms.append(plot_cells * 40 * sum(soft_bools))

    # public rooms penalised when not on the south edge (y0 > 0)
    for zid in present:
        sp = program.space(zid)
        is_public = zid in Z.PUBLIC_ZONES or (sp is not None and sp.category == "living")
        if is_public:
            nz = m.NewBoolVar(f"{zid}_nonsouth")
            m.Add(zv[zid].y0 >= 1).OnlyEnforceIf(nz)
            m.Add(zv[zid].y0 == 0).OnlyEnforceIf(nz.Not())
            obj_terms.append(-plot_cells * 3 * nz)

    # service rooms rewarded for being north (larger y0)
    for zid in present:
        sp = program.space(zid)
        is_service = zid in Z.SERVICE_ZONES or (sp is not None and sp.category == "service")
        if is_service:
            obj_terms.append(plot_cells * 2 * zv[zid].y0)

    m.Maximize(sum(obj_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    status = solver.Solve(m)

    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    rects: list[ZoneRect] = []
    if feasible:
        for zid in present:
            v = zv[zid]
            rects.append(
                ZoneRect(
                    zone=zid,
                    x0=solver.Value(v.x0) * GRID_M,
                    y0=solver.Value(v.y0) * GRID_M,
                    x1=solver.Value(v.x1) * GRID_M,
                    y1=solver.Value(v.y1) * GRID_M,
                )
            )
    human_obj = solver.ObjectiveValue() / plot_cells if feasible else float("-inf")

    return SolveResult(
        status=solver.StatusName(status),
        feasible=feasible,
        objective=human_obj,
        preset=preset,
        seed=seed,
        rects=rects,
        plot_w_m=program.plot.width_m,
        plot_d_m=program.plot.depth_m,
        wall_time_s=solver.WallTime(),
    )
