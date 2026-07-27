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
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .models import Program, ZoneId
from .presets import Pins, resolve
from . import reconcile
from . import standards
from . import zones as Z

GRID_M = 0.5  # one grid cell edge, metres
AREA_LO = 0.85  # min zone area as fraction of its target
AREA_HI = 1.20  # max zone area as fraction of its target
FOOTPRINT_LO = 0.95  # min house footprint as fraction of program.target_area_m2
FOOTPRINT_HI = 1.15  # max house footprint as fraction of program.target_area_m2
# Default floor on the fraction of the footprint the zones must tile. It is a
# FRACTION FLOOR, not a tiling constraint: at 0.95 it permits up to 5% of the
# footprint to be dead area, in any shape and anywhere — interior pockets AND
# notches bitten out of the facade. Callers override it via solve(coverage_min=);
# at 1.0 it becomes exact tiling (total_area == fp.area, since containment +
# non-overlap already cap total_area from above), i.e. no holes and no notches.
COVERAGE_MIN = 0.95
DEFAULT_MAX_ASPECT = 3.0  # fallback when neither Space nor standards gives one
TARGET_AREA_TOLERANCE = 0.15  # sum(space targets) vs target_area_m2 mismatch warn

# Tag prefix generate.py's fallback prepends to SolveResult.warnings (and, via
# build_layout, Layout.warnings) when it had to drop the kitchen-direct
# constraint below. validator.py's validate_plan checks for this same prefix
# to allow the one authorized exception to its Living-off-kitchen-path gate —
# a single source of truth so the two never drift apart.
KITCHEN_FALLBACK_TAG = "kitchen-not-corridor-direct"


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
    warnings: list[str] = field(default_factory=list)
    # House footprint (metres) within the plot; the residual strip is setback.
    # None when the solve was infeasible.
    footprint_m: tuple[float, float, float, float] | None = None
    # Cut axis the SOLVER chose for a composite zone, as the director's side
    # ("N"/"S"/"E"/"W"). The slicer reads this instead of re-deriving it, so the
    # zone's (w,h) — constrained to a shape legal for THIS axis — always matches
    # the cut. Currently only kitchen_laundry (see legal_pairs / _AXIAL).
    cut_sides: dict[str, str] = field(default_factory=dict)
    # Side ("N"/"S"/"E"/"W") the corridor attached to a zone, read off the
    # solver's own disjunction bools (_force_master_corridor_overlap for
    # master_suite, _share_wall's side_bools for kitchen_laundry) instead of the slicer
    # re-deriving it from geometry. Populated for master_suite and
    # kitchen_laundry only — the slicer must not assume any other key is present.
    corridor_sides: dict[str, str] = field(default_factory=dict)
    # Whether the hard corridor<->kitchen_laundry wall (see the access block in
    # _solve_once) was enforced on THIS solve. True means a feasible result is
    # guaranteed corridor-direct by construction; False means generate.py's
    # fallback dropped it because the footprint was too small (see
    # KITCHEN_FALLBACK_TAG) and the plan may route the kitchen via dining/living.
    kitchen_direct: bool = True


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


class _Footprint:
    """The buildable house rectangle inside the plot. Zones tile THIS, not the
    plot; the residual plot strip is setback (a garden the old plot-filling
    solver never left room for). Grid-aligned even though the plot is
    real-valued, which also sidesteps the plot-quantisation bug (a plot
    dimension that isn't a multiple of GRID_M no longer truncates area)."""

    __slots__ = ("x0", "y0", "x1", "y1", "w", "h", "area")

    def __init__(self, m: cp_model.CpModel, W: int, H: int):
        self.x0 = m.NewIntVar(0, W, "fp_x0")
        self.y0 = m.NewIntVar(0, H, "fp_y0")
        self.x1 = m.NewIntVar(0, W, "fp_x1")
        self.y1 = m.NewIntVar(0, H, "fp_y1")
        self.w = m.NewIntVar(1, W, "fp_w")
        self.h = m.NewIntVar(1, H, "fp_h")
        m.Add(self.x1 == self.x0 + self.w)
        m.Add(self.y1 == self.y0 + self.h)
        self.area = m.NewIntVar(1, W * H, "fp_area")
        m.AddMultiplicationEquality(self.area, [self.w, self.h])


def _apply_pins(m: cp_model.CpModel, zv: _ZoneVars, pins: Pins, fp: _Footprint) -> None:
    # Pins now anchor to FOOTPRINT edges, not plot edges: "garage on the street
    # side", "living on the daylight side" mean the edges of the house, so that
    # the house can sit anywhere in the plot with setback around it.
    if pins.south:
        m.Add(zv.y0 == fp.y0)
    if pins.north:
        m.Add(zv.y1 == fp.y1)
    if pins.west:
        m.Add(zv.x0 == fp.x0)
    if pins.east:
        m.Add(zv.x1 == fp.x1)
    if pins.max_y1_frac is not None:
        # zv.y1 - fp.y0 <= frac * fp.h  (fraction of the footprint's own height)
        m.Add(100 * (zv.y1 - fp.y0) <= int(round(100 * pins.max_y1_frac)) * fp.h)


def _add_perimeter_complete(
    m: cp_model.CpModel,
    fp: _Footprint,
    zv: dict,
    W: int,
    H: int,
) -> None:
    """Force every unit segment of all four footprint edges to be covered by a
    zone, i.e. the building ENVELOPE is a clean rectangle with no notches.

    Deliberately WEAKER than exact tiling: interior pockets stay legal (that is
    COVERAGE_MIN's job). It targets the architect's complaint directly — a ragged
    outline reads as a defect in any drawing, an interior pocket usually does not.

    Encoding, per edge, for each grid position `i` along that edge:
      in_i    <-> position i lies inside the footprint's span on that axis
      cov_z_i  -> zone z touches this edge AND its span covers position i
      in_i     -> OR_z cov_z_i
    `cov` needs only the forward implication: the disjunction forces at least one
    to be true, and a true one is then constrained to be a genuine cover, so a
    spuriously-set bool can never fake coverage. The `touch` and `in` bools are
    full equivalences because both directions are load-bearing.

    The footprint rectangle is itself variable, which is why membership has to be
    reified per position rather than read off a constant span. The x-membership
    bools are shared between the south and north edges (and y between west and
    east) — same span, same answer.
    """
    zones = list(zv.values())

    def _touch(var, bound, hi_side: bool, tag: str):
        """b <-> (var == bound). Containment already gives the one-sided bound,
        so the negative branch is a plain inequality, never a reified `!=`."""
        b = m.NewBoolVar(tag)
        m.Add(var == bound).OnlyEnforceIf(b)
        if hi_side:  # var <= bound by containment
            m.Add(var <= bound - 1).OnlyEnforceIf(b.Not())
        else:        # var >= bound by containment
            m.Add(var >= bound + 1).OnlyEnforceIf(b.Not())
        return b

    def _inside(lo_var, hi_var, i: int, tag: str):
        """b <-> (lo_var <= i AND hi_var >= i+1): position i is inside the span."""
        lo = m.NewBoolVar(f"{tag}_lo")
        m.Add(lo_var <= i).OnlyEnforceIf(lo)
        m.Add(lo_var >= i + 1).OnlyEnforceIf(lo.Not())
        hi = m.NewBoolVar(f"{tag}_hi")
        m.Add(hi_var >= i + 1).OnlyEnforceIf(hi)
        m.Add(hi_var <= i).OnlyEnforceIf(hi.Not())
        b = m.NewBoolVar(tag)
        m.AddBoolOr([lo.Not(), hi.Not(), b])
        m.AddImplication(b, lo)
        m.AddImplication(b, hi)
        return b

    # touch bools: one per zone per edge, reused across every position on it
    touch_s = [_touch(z.y0, fp.y0, False, f"pc_tS_{k}") for k, z in enumerate(zones)]
    touch_n = [_touch(z.y1, fp.y1, True, f"pc_tN_{k}") for k, z in enumerate(zones)]
    touch_w = [_touch(z.x0, fp.x0, False, f"pc_tW_{k}") for k, z in enumerate(zones)]
    touch_e = [_touch(z.x1, fp.x1, True, f"pc_tE_{k}") for k, z in enumerate(zones)]

    # membership bools, shared: the south and north edges run along the same x
    # span, west and east along the same y span.
    in_x = [_inside(fp.x0, fp.x1, i, f"pc_inx{i}") for i in range(W)]
    in_y = [_inside(fp.y0, fp.y1, j, f"pc_iny{j}") for j in range(H)]

    def _edge(inside_bools, touch, along_lo, along_hi, tag: str):
        for i, inside in enumerate(inside_bools):
            covers = []
            for k, z in enumerate(zones):
                c = m.NewBoolVar(f"pc_{tag}_c{i}_{k}")
                m.AddImplication(c, touch[k])
                m.Add(along_lo(z) <= i).OnlyEnforceIf(c)
                m.Add(along_hi(z) >= i + 1).OnlyEnforceIf(c)
                covers.append(c)
            m.AddBoolOr(covers).OnlyEnforceIf(inside)

    _edge(in_x, touch_s, lambda z: z.x0, lambda z: z.x1, "S")
    _edge(in_x, touch_n, lambda z: z.x0, lambda z: z.x1, "N")
    _edge(in_y, touch_w, lambda z: z.y0, lambda z: z.y1, "W")
    _edge(in_y, touch_e, lambda z: z.y0, lambda z: z.y1, "E")


def _share_wall(
    m: cp_model.CpModel,
    a: _ZoneVars,
    b: _ZoneVars,
    min_len: int,
    tag: str,
    side_bools: dict[str, cp_model.IntVar] | None = None,
) -> cp_model.IntVar:
    """Reified boolean: true => a and b share a wall segment >= min_len units.

    Four configs (a west/east/south/north of b). The bool is one-directional
    (share -> real adjacency); the solver only sets it true when it can realise
    a config, which forces a genuine shared wall. Good enough for both a hard
    requirement (force share == 1) and a soft reward (reward only if realised).

    If `side_bools` is given, it is populated with the four directional bools
    keyed by the side of `b` that `a` occupies ("W"/"E"/"S"/"N") — same shape
    as _tie_cut_axis's return — so a caller can read back post-solve which
    config was actually realised (see SolveResult.corridor_sides) without
    changing the constraint itself or any other caller's return contract.
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
    if side_bools is not None:
        side_bools["W"] = c
    # a east of b: b.x1 == a.x0
    c = m.NewBoolVar(f"{tag}_aEb")
    m.Add(b.x1 == a.x0).OnlyEnforceIf(c)
    m.Add(y_hi - y_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    if side_bools is not None:
        side_bools["E"] = c
    # a south of b: a.y1 == b.y0, horizontal wall, x-overlap >= min_len
    c = m.NewBoolVar(f"{tag}_aSb")
    m.Add(a.y1 == b.y0).OnlyEnforceIf(c)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    if side_bools is not None:
        side_bools["S"] = c
    # a north of b: b.y1 == a.y0
    c = m.NewBoolVar(f"{tag}_aNb")
    m.Add(b.y1 == a.y0).OnlyEnforceIf(c)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(c)
    configs.append(c)
    if side_bools is not None:
        side_bools["N"] = c

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


def _tie_cut_axis(
    m: cp_model.CpModel, a: _ZoneVars, b: _ZoneVars, min_len: int, ns: cp_model.IntVar, tag: str
) -> dict[str, cp_model.IntVar]:
    """Force a required adjacency between a (a composite zone) and b (its
    director) AND bind `ns` to the axis: ns=1 iff b is North/South of a. Returns
    the four directional bools (b relative to a) so the caller can read the exact
    side post-solve. This is what lets the slicer stop inferring the cut axis: the
    solver commits to it here, consistently with a's shape table."""
    y_lo = m.NewIntVar(0, 10_000, f"{tag}_ylo")
    y_hi = m.NewIntVar(0, 10_000, f"{tag}_yhi")
    m.AddMaxEquality(y_lo, [a.y0, b.y0])
    m.AddMinEquality(y_hi, [a.y1, b.y1])
    x_lo = m.NewIntVar(0, 10_000, f"{tag}_xlo")
    x_hi = m.NewIntVar(0, 10_000, f"{tag}_xhi")
    m.AddMaxEquality(x_lo, [a.x0, b.x0])
    m.AddMinEquality(x_hi, [a.x1, b.x1])

    bE = m.NewBoolVar(f"{tag}_bE")  # director east of a  -> W/E cut
    m.Add(a.x1 == b.x0).OnlyEnforceIf(bE)
    m.Add(y_hi - y_lo >= min_len).OnlyEnforceIf(bE)
    bW = m.NewBoolVar(f"{tag}_bW")  # director west
    m.Add(b.x1 == a.x0).OnlyEnforceIf(bW)
    m.Add(y_hi - y_lo >= min_len).OnlyEnforceIf(bW)
    bN = m.NewBoolVar(f"{tag}_bN")  # director north of a -> N/S cut
    m.Add(a.y1 == b.y0).OnlyEnforceIf(bN)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(bN)
    bS = m.NewBoolVar(f"{tag}_bS")  # director south
    m.Add(b.y1 == a.y0).OnlyEnforceIf(bS)
    m.Add(x_hi - x_lo >= min_len).OnlyEnforceIf(bS)

    m.AddBoolOr([bE, bW, bN, bS])          # the adjacency itself (hard-required)
    m.AddMaxEquality(ns, [bN, bS])         # ns == (director is N or S)
    return {"E": bE, "W": bW, "N": bN, "S": bS}


def _force_master_corridor_overlap(
    m: cp_model.CpModel,
    corr: _ZoneVars,
    zone: _ZoneVars,
    ew_overlap: int,
    ns_overlap: int,
    tag: str,
) -> dict[str, cp_model.IntVar]:
    """Force `corr` (corridor) adjacent to `zone` (master suite) on ANY of its
    four sides — a reified disjunction, not a fixed E/W pair — with per-side
    overlap semantics so each side's floor is exactly as long as it needs to
    be to guarantee a genuine Bedroom-facing wall, not just an ensuite/closet
    touch:

    E/W: vertical wall, y-overlap >= `ew_overlap` (~ the north bath/closet
    strip's depth + a full door width). The strip is shorter than that, so an
    overlap this long CANNOT sit entirely in the strip — it must reach the
    Bedroom band by at least a door width. (Do not pass just the Bedroom's own
    min depth here — it clears the strip by only ~0.5m, under ACCESS_DOOR_M,
    which access_tree can then refuse to award a door for. See the call site.)

    N/S: horizontal wall, x-overlap >= `ns_overlap` (~ACCESS_DOOR_M only). On
    these sides _slice_master (5da1490) makes the Bedroom the FULL-WIDTH band
    facing the corridor (flipped to the north band for "N"; already the south
    band for "S"), so there is no strip to clear — any door-width overlap
    already lands entirely on the Bedroom.

    Same family as _force_vertical_cover_center; constrains only overlap
    length (no pin) so it composes with the children center-cover.

    Returns the {"E","W","N","S"} disjunction bools so a caller can read back
    the winning side post-solve (same precedent as _tie_cut_axis /
    SolveResult.cut_sides) and hand it to the slicer instead of the slicer
    re-deriving it from geometry.
    """
    y_lo = m.NewIntVar(0, 10_000, f"{tag}_ylo")
    y_hi = m.NewIntVar(0, 10_000, f"{tag}_yhi")
    m.AddMaxEquality(y_lo, [corr.y0, zone.y0])
    m.AddMinEquality(y_hi, [corr.y1, zone.y1])
    x_lo = m.NewIntVar(0, 10_000, f"{tag}_xlo")
    x_hi = m.NewIntVar(0, 10_000, f"{tag}_xhi")
    m.AddMaxEquality(x_lo, [corr.x0, zone.x0])
    m.AddMinEquality(x_hi, [corr.x1, zone.x1])

    bE = m.NewBoolVar(f"{tag}_bE")  # corridor east of zone: corr.x0 == zone.x1
    m.Add(corr.x0 == zone.x1).OnlyEnforceIf(bE)
    m.Add(y_hi - y_lo >= ew_overlap).OnlyEnforceIf(bE)
    bW = m.NewBoolVar(f"{tag}_bW")  # corridor west of zone: corr.x1 == zone.x0
    m.Add(corr.x1 == zone.x0).OnlyEnforceIf(bW)
    m.Add(y_hi - y_lo >= ew_overlap).OnlyEnforceIf(bW)
    bN = m.NewBoolVar(f"{tag}_bN")  # corridor north of zone: corr.y0 == zone.y1
    m.Add(corr.y0 == zone.y1).OnlyEnforceIf(bN)
    m.Add(x_hi - x_lo >= ns_overlap).OnlyEnforceIf(bN)
    bS = m.NewBoolVar(f"{tag}_bS")  # corridor south of zone: corr.y1 == zone.y0
    m.Add(corr.y1 == zone.y0).OnlyEnforceIf(bS)
    m.Add(x_hi - x_lo >= ns_overlap).OnlyEnforceIf(bS)

    m.AddBoolOr([bE, bW, bN, bS])
    return {"E": bE, "W": bW, "N": bN, "S": bS}


def _require_master_bedroom_perimeter(
    m: cp_model.CpModel,
    zone: _ZoneVars,
    fp: _Footprint,
    side_bools: dict[str, cp_model.IntVar],
    tag: str,
) -> None:
    """Bedroom-AWARE habitable-perimeter guarantee for master_suite: the
    Master Bedroom must have a real exterior wall (SNiP/Posobie cl. 2.8, KEO
    >= 0.5% — a bedroom with no daylight is a code violation), and that
    guarantee must be a CONSTRAINT, not an accident of the objective (measured
    in Step 2: master_suite got a facade "for free" under
    _force_master_corridor_overlap alone, but only because the objective
    happened to prefer it — nothing forced it).

    A plain zone-level perimeter check (zone touches the footprint on SOME
    edge) is NOT sufficient: with the corridor north, the zone's north edge
    already touches the corridor and its only OTHER footprint contact could
    be the south service strip's edge — a check that isn't aware of which
    room is on which side would pass while the Bedroom itself stays
    landlocked. This ties the required facade edge to whichever face the
    Bedroom actually occupies GIVEN which side won the corridor disjunction
    (side_bools, from _force_master_corridor_overlap):
      bN/bS -> the Bedroom is the full-width band on that same side (N: the
               _slice_master flip puts it north; S: already the south band)
               -- so its only possible exterior faces are the zone's WEST or
               EAST edge (its N/S edges are the corridor and the service
               strip, neither exterior).
      bE/bW -> the Bedroom is the (unflipped) south band, and the corridor
               already claims the E or W face respectively -- so its only
               possible exterior faces are the zone's SOUTH edge, or whichever
               of WEST/EAST the corridor did NOT claim.
    One-directional implications (side_bool -> facade-OR), same idiom as the
    rest of this file: the solver may only set a facade bool True when the
    equality is achievable, so this can never be satisfied by a lie.

    Deliberately NOT applied to living/children/office/kitchen_laundry: Step 2
    measured that adding the same plain zone-level requirement to all four
    reshuffles the packing enough to strand Bedroom 3/Garage from the access
    tree and reopen the kitchen-direct regression, at every footprint tried.
    Master_suite only.
    """
    west = m.NewBoolVar(f"{tag}_west")
    m.Add(zone.x0 == fp.x0).OnlyEnforceIf(west)
    east = m.NewBoolVar(f"{tag}_east")
    m.Add(zone.x1 == fp.x1).OnlyEnforceIf(east)
    south = m.NewBoolVar(f"{tag}_south")
    m.Add(zone.y0 == fp.y0).OnlyEnforceIf(south)

    bN, bS, bE, bW = side_bools["N"], side_bools["S"], side_bools["E"], side_bools["W"]
    m.AddBoolOr([west, east, bN.Not()])
    m.AddBoolOr([west, east, bS.Not()])
    m.AddBoolOr([south, west, bE.Not()])
    m.AddBoolOr([south, east, bW.Not()])


def _force_vertical_cover_center(
    m: cp_model.CpModel, corr: _ZoneVars, zone: _ZoneVars, band_u: int, tag: str
) -> None:
    """Force `corr` (corridor) to share a vertical E/W wall with `zone` (children)
    whose overlap COVERS the zone's central `band_u`-unit slice — the middle
    Bathroom band. children is sliced bed2 / Bathroom / bed3 top-to-bottom with
    the Bathroom centred (and full-width), so its ONLY non-bedroom, non-exterior
    edge is the interior vertical edge at that central band; covering it gives the
    Bathroom a DIRECT corridor wall. The two beds (against the exterior wall,
    sharing a full-width wall with the Bathroom) are then reached THROUGH the
    Bathroom — legal, since the Bathroom is no_through_traffic=False — so no path
    ever transits a bedroom. Root-cause fix for the Task-2a hall-bathroom bug.
    A 2-unit (x2-written) margin absorbs the slice's half-grid centring drift."""
    cW = m.NewBoolVar(f"{tag}_cW")  # corridor west of zone: corr.x1 == zone.x0
    cE = m.NewBoolVar(f"{tag}_cE")  # corridor east of zone: zone.x1 == corr.x0
    m.Add(corr.x1 == zone.x0).OnlyEnforceIf(cW)
    m.Add(zone.x1 == corr.x0).OnlyEnforceIf(cE)
    m.AddBoolOr([cW, cE])
    m.Add(2 * corr.y0 <= zone.y0 + zone.y1 - band_u - 2)
    m.Add(2 * corr.y1 >= zone.y0 + zone.y1 + band_u + 2)


def _force_corridor_overlaps_kitchen(
    m: cp_model.CpModel,
    corr: _ZoneVars,
    kl: _ZoneVars,
    dining_side: dict[str, cp_model.IntVar],
    corridor_side: dict[str, cp_model.IntVar],
    l_ns: int,
    l_we: int,
    overlap: int,
    tag: str,
) -> None:
    """Room-level Kitchen-direct hardening (K). The zone-level access constraint
    only promises corridor <-> kitchen_laundry ZONE >= a door; WHICH sub-room
    (Kitchen or Laundry) receives the corridor's shared segment was left to
    _slice_kitchen (66f3506's slicer heuristic). This forces that segment onto the
    KITCHEN sub-rect in CP-SAT, so a repack can never route the corridor onto the
    Laundry strip and strand the kitchen behind the Dining->Living chain.

    _slice_kitchen cuts an `l_ns` (N/S cut) or `l_we` (W/E cut) unit Laundry strip
    off the DINING end of the cut axis; Kitchen takes the rest. When the corridor
    lands on the cut axis its shared edge already spans the full width of whichever
    end room it fronts and the existing zone-level share suffices (Kitchen is that
    end by _slice_kitchen's place_side rule), so those configs are AUTO-satisfied
    and add nothing here. The gap is the ORTHOGONAL configs: there both sub-rooms
    present on the corridor's edge and nothing stopped the overlap landing on the
    Laundry band. This reifies the Kitchen band from the Dining side (=
    _slice_kitchen's place_side when the corridor is orthogonal) and forces the
    corridor's shared segment to cover >= `overlap` of it. Same one-directional,
    reified-implication idiom as _force_master_corridor_overlap /
    _require_master_bedroom_perimeter.
    """
    dN, dS, dE, dW = dining_side["N"], dining_side["S"], dining_side["E"], dining_side["W"]
    cN, cS, cE, cW = corridor_side["N"], corridor_side["S"], corridor_side["E"], corridor_side["W"]
    BIG = 10_000
    # explicit affine helper vars (kl.y1 - l_ns etc.) so the Min/Max equalities
    # take plain vars, not affine exprs
    kl_y1_mL = m.NewIntVar(0, BIG, f"{tag}_kly1mL")
    m.Add(kl_y1_mL == kl.y1 - l_ns)
    kl_y0_pL = m.NewIntVar(0, BIG, f"{tag}_kly0pL")
    m.Add(kl_y0_pL == kl.y0 + l_ns)
    kl_x1_mL = m.NewIntVar(0, BIG, f"{tag}_klx1mL")
    m.Add(kl_x1_mL == kl.x1 - l_we)
    kl_x0_pL = m.NewIntVar(0, BIG, f"{tag}_klx0pL")
    m.Add(kl_x0_pL == kl.x0 + l_we)

    # corridor on a vertical (W/E) wall of kl, cut is N/S (Dining N or S): the
    # corridor's shared segment is a Y range; require it to cover the Kitchen Y band
    y_lo = m.NewIntVar(0, BIG, f"{tag}_ylo")
    m.AddMaxEquality(y_lo, [corr.y0, kl.y0])
    y_hi = m.NewIntVar(0, BIG, f"{tag}_yhi")
    m.AddMinEquality(y_hi, [corr.y1, kl.y1])
    kit_hi_S = m.NewIntVar(0, BIG, f"{tag}_kithiS")  # dS: Kitchen = [kl.y0, kl.y1 - L]
    m.AddMinEquality(kit_hi_S, [corr.y1, kl_y1_mL])
    kit_lo_N = m.NewIntVar(0, BIG, f"{tag}_kitloN")  # dN: Kitchen = [kl.y0 + L, kl.y1]
    m.AddMaxEquality(kit_lo_N, [corr.y0, kl_y0_pL])
    for cside in (cW, cE):
        m.Add(kit_hi_S - y_lo >= overlap).OnlyEnforceIf([cside, dS])
        m.Add(y_hi - kit_lo_N >= overlap).OnlyEnforceIf([cside, dN])

    # corridor on a horizontal (N/S) wall of kl, cut is W/E (Dining W or E): the
    # shared segment is an X range; require it to cover the Kitchen X band
    x_lo = m.NewIntVar(0, BIG, f"{tag}_xlo")
    m.AddMaxEquality(x_lo, [corr.x0, kl.x0])
    x_hi = m.NewIntVar(0, BIG, f"{tag}_xhi")
    m.AddMinEquality(x_hi, [corr.x1, kl.x1])
    kit_hi_W = m.NewIntVar(0, BIG, f"{tag}_kithiW")  # dW: Kitchen = [kl.x0, kl.x1 - L]
    m.AddMinEquality(kit_hi_W, [corr.x1, kl_x1_mL])
    kit_lo_E = m.NewIntVar(0, BIG, f"{tag}_kitloE")  # dE: Kitchen = [kl.x0 + L, kl.x1]
    m.AddMaxEquality(kit_lo_E, [corr.x0, kl_x0_pL])
    for cside in (cN, cS):
        m.Add(kit_hi_W - x_lo >= overlap).OnlyEnforceIf([cside, dW])
        m.Add(x_hi - kit_lo_E >= overlap).OnlyEnforceIf([cside, dE])


# Test seam (Task 5 Phase 2). Production is always True: children is connected to
# the corridor by _force_vertical_cover_center, guaranteeing the hall Bathroom is
# corridor-DIRECT. Set False to fall back to a plain children<->{corridor,entry}
# disjunction — used only by test_solver to prove the guarantee is LOAD-BEARING:
# without it the gE_eW handedness (entry-west + children-west) becomes feasible
# but the Bathroom loses its direct corridor wall. See test_children_bathroom_*.
_CHILD_CENTER_COVER: bool = True

_REQUIRED_PAIRS: set[frozenset] = {frozenset(p) for p in Z.REQUIRED_ADJ}
# The access graph (Task 5 Phase 2) uses only relaxed disjunctions, so no pair is
# hard-forced and the soft-adjacency loop needs no extra skips beyond REQUIRED_ADJ.
_ACCESS_FORCED_PAIRS: set[frozenset] = set()


def _is_required_pair(a: ZoneId, b: ZoneId) -> bool:
    """True if (a, b) is already a hard-required adjacency (zones.REQUIRED_ADJ).

    Used to skip re-rewarding as soft something the model already guarantees —
    otherwise a Program that lists an already-required pair under desirable/semi
    would silently inflate the objective by a constant for every feasible
    solution, without changing which layout gets chosen.
    """
    return frozenset((a, b)) in _REQUIRED_PAIRS


def check_program_area(program: Program) -> list[str]:
    """Warn when the space targets don't reconcile with target_area_m2.

    Gemini fills both independently and nothing reconciles them (it emits 176 m2
    of spaces against its own declared 160 m2). The footprint is sized from
    target_area_m2, so a large mismatch means the zones won't tile it cleanly.
    """
    total = sum(s.target_m2 for s in program.spaces)
    tgt = program.target_area_m2
    if tgt > 0 and abs(total - tgt) / tgt > TARGET_AREA_TOLERANCE:
        return [
            f"space targets sum to {total:.0f} m2 but target_area_m2 is {tgt:.0f} "
            f"({abs(total - tgt) / tgt * 100:.0f}% off, > {TARGET_AREA_TOLERANCE * 100:.0f}%)"
        ]
    return []


def solve(
    program: Program,
    preset: str,
    seed: int = 0,
    time_limit_s: float = 12.0,
    workers: int = 8,
    force_kitchen_direct: bool = True,
    coverage_min: float | None = None,
    perimeter_complete: bool = False,
) -> SolveResult:
    """Solve, retrying once if a caller-supplied `avoid` edge makes it infeasible.

    Program.adjacency is the live source for soft desirable/semi rewards and
    the hard avoid gap; REQUIRED_ADJ stays hard and non-LLM-controllable
    regardless (see zones.py). Empty lists on Program.adjacency fall back to
    zones.DEFAULT_*, reproducing prior (pre-adjacency-wiring) behaviour
    exactly. A hallucinated `avoid` pair must degrade gracefully, never
    hard-fail the request: if the first attempt is infeasible and the caller
    actually supplied `avoid` edges (as opposed to us defaulting them), retry
    once with those edges dropped and warn.

    `force_kitchen_direct` (default True) applies the hard corridor<->
    kitchen_laundry wall (see _solve_once). generate.py's fallback passes
    False for a second attempt when a first True attempt was infeasible —
    solve() itself does not retry on this axis, unlike the avoid-drop above,
    because dropping it silently would ship the exact through-living
    pathology the constraint exists to prevent; the caller must opt in and
    flag the result (KITCHEN_FALLBACK_TAG).

    `coverage_min` (default None -> module COVERAGE_MIN) and
    `perimeter_complete` (default False) are the envelope-quality knobs; both
    defaults reproduce prior behaviour exactly, adding no variables to the model.
    See COVERAGE_MIN and _add_perimeter_complete.
    """
    # Site setbacks (Task 5 Phase 4) are mapped in the fixed internal frame where
    # street = north and garden = south, i.e. orientation "N". Rather than
    # silently place a garden on the street for a rotated plot, refuse a non-N
    # orientation loudly — a named TODO, not a wrong plan. (See models.Site.)
    if program.orientation != "N":
        raise NotImplementedError(
            "setback orientation mapping only implemented for N-facing plots "
            f"(got orientation {program.orientation!r}); see Task 6"
        )
    # Inject the derived circulation corridor as a live zone (Task 5) BEFORE
    # reconciling, then reconcile the brief to the footprint budget: the space
    # targets are rescaled to sum to footprint_target_m2, holding the garage AND
    # the corridor (its target is derived, not a rescalable estimate), so the
    # solver's area windows and the target-adherence term are measured against a
    # self-consistent program with the hall carved out of the habitable budget.
    program, circ_warnings = Z.inject_circulation(program)
    program, recon_warnings = reconcile.reconcile_program(program, held=("garage", "circulation"))

    llm_avoid = program.adjacency.avoid
    desirable = program.adjacency.desirable or Z.DEFAULT_DESIRABLE
    semi = program.adjacency.semi or Z.DEFAULT_SEMI
    avoid = llm_avoid or Z.DEFAULT_AVOID

    area_warnings = check_program_area(program)  # now measured on the reconciled program

    result = _solve_once(
        program, preset, seed, time_limit_s, workers, avoid, desirable, semi, force_kitchen_direct,
        coverage_min, perimeter_complete,
    )
    if not result.feasible and llm_avoid:
        result = _solve_once(
            program, preset, seed, time_limit_s, workers, [], desirable, semi, force_kitchen_direct,
            coverage_min, perimeter_complete,
        )
        result.warnings.append(
            f"solve was infeasible with requested avoid-adjacency {llm_avoid}; retried with it dropped"
        )
    result.warnings = circ_warnings + recon_warnings + area_warnings + result.warnings
    return result


def _solve_once(
    program: Program,
    preset: str,
    seed: int,
    time_limit_s: float,
    workers: int,
    avoid_pairs: list,
    desirable_pairs: list,
    semi_pairs: list,
    force_kitchen_direct: bool = True,
    coverage_min: float | None = None,
    perimeter_complete: bool = False,
) -> SolveResult:
    cov_min = COVERAGE_MIN if coverage_min is None else coverage_min
    spec = resolve(preset)
    W = _u(program.plot.width_m)
    H = _u(program.plot.depth_m)
    plot_cells = W * H

    m = cp_model.CpModel()

    # The house footprint: a grid-aligned rectangle inside the plot that the
    # zones tile. target_area_m2 (previously dead data) sizes it; the residual
    # plot area is setback.
    fp = _Footprint(m, W, H)
    house_cells = program.target_area_m2 / (GRID_M * GRID_M)
    m.Add(fp.area >= int(math.floor(FOOTPRINT_LO * house_cells)))
    m.Add(fp.area <= min(plot_cells, int(math.ceil(FOOTPRINT_HI * house_cells))))

    # Site setbacks + coverage cap (Task 5 Phase 4). Fixed internal frame:
    # street = north (+y), garden = south (-y). The front setback is the street
    # (north) edge the garage/entry pin and orientation labels; the rear is the
    # garden (south) edge Living pins to, so the south pin finally means a real
    # setback. Setbacks are minimum distances -> _ceil_u (round the gap UP). The
    # coverage cap is a hard ceiling on the footprint; it is what makes an
    # over-stuffed brief (the tight plot at ~87%) correctly infeasible, and its
    # asymmetric front/rear + side minima also shrink the translational slack that
    # made the roomy solve slow.
    site = program.site
    m.Add(fp.x0 >= _ceil_u(site.setback_side_m))
    m.Add(fp.x1 <= W - _ceil_u(site.setback_side_m))
    m.Add(fp.y0 >= _ceil_u(site.setback_rear_m))       # garden, south
    m.Add(fp.y1 <= H - _ceil_u(site.setback_front_m))  # street, north
    m.Add(fp.area <= int(math.floor(site.max_coverage_ratio * plot_cells)))

    from . import slicer  # lazy: slicer imports solver (GRID_M, ZoneRect)

    present: list[ZoneId] = [z for z in Z.ZONE_ORDER if program.space(z) is not None]
    zv: dict[ZoneId, _ZoneVars] = {}
    ns_vars: dict[ZoneId, cp_model.IntVar] = {}  # kitchen_laundry cut-axis bit
    # per-zone info the objective consumes: (zid, v, target_cells, min_w, min_h,
    # brief_w_pref, brief_h_pref) — all dims in grid units.
    zinfo: list[tuple] = []
    for zid in present:
        sp = program.space(zid)
        assert sp is not None
        target_cells = sp.target_m2 / (GRID_M * GRID_M)
        target_cells_i = int(round(target_cells))
        lo_area = int(math.floor(AREA_LO * target_cells))
        hi_area = min(plot_cells, int(math.ceil(AREA_HI * target_cells)))
        # The brief's per-space minima are now SOFT preferences (Task 4b), not
        # hard filters; the HARD floor is the Neufert-legal shape. Keep the brief
        # value for the soft objective term below.
        pref_w = max(1, _ceil_u(sp.min_w_m))
        pref_h = max(1, _ceil_u(sp.min_h_m))

        lp = slicer.legal_pairs(zid)
        if lp:
            # COMPOSITE: pin (w, h) to the standards-legal table (Phase 3). No
            # brief-min filter — a brief min above Neufert is a preference now, so
            # the union's tighter shapes (e.g. the 2.5 m N/S kitchen) stay legal.
            min_w = max(1, min(t[0] for t in lp))
            min_h = max(1, min(t[1] for t in lp))
            # AREA_HI caps sprawl relative to the target, but must never fall
            # below the zone's smallest legal shape. When reconcile shrinks a
            # target under its Neufert floor (Task 5: a suite carved down to pay
            # for the corridor), 1.2*target can dip below the smallest legal pair
            # and strangle the AllowedAssignments set to empty -> INFEASIBLE. The
            # hard legal floor wins; the adherence term carries the over-target.
            floor_area = min(t[0] * t[1] for t in lp)
            v = _ZoneVars(m, zid, W, H, min_w, min_h)
            m.Add(v.area >= lo_area)
            m.Add(v.area <= max(hi_area, floor_area))
            if len(lp[0]) == 3:  # axial (kitchen_laundry): (w, h, ns)
                ns = m.NewBoolVar(f"{zid}_ns")
                m.AddAllowedAssignments([v.w, v.h, ns], lp)
                ns_vars[zid] = ns
            else:
                m.AddAllowedAssignments([v.w, v.h], lp)
        else:
            # NON-COMPOSITE: HARD floor is the Neufert room standard; the brief
            # minimum (if larger) is a soft preference below, not a hard widening.
            zm = standards.zone_minima(zid)
            if zm is not None:
                min_w_m, min_h_m = zm.min_w_m, zm.min_h_m
                aspect = sp.max_aspect if sp.max_aspect is not None else zm.max_aspect
            else:
                min_w_m, min_h_m = sp.min_w_m, sp.min_h_m
                aspect = sp.max_aspect if sp.max_aspect is not None else DEFAULT_MAX_ASPECT
            min_w = max(1, _ceil_u(min_w_m))
            min_h = max(1, _ceil_u(min_h_m))
            v = _ZoneVars(m, zid, W, H, min_w, min_h)
            # Same floor-wins clamp as the composite branch: a Neufert floor
            # above 1.2*target (reconciled below its minimum) must not produce an
            # empty [>=floor, <=hi] window.
            floor_area = min_w * min_h
            hi = max(hi_area, floor_area)
            if zid == "circulation":
                # The corridor is the flex void-filler and the access spine: the
                # Phase-2 full-span children constraint can force it tall (a 1.2 m
                # spine covering a ~7.5 m room stack needs ~11 m2 > 1.2*target).
                # Uncap its upper area so topology wins; the L1 adherence term
                # still pulls it back toward the derived target when free to.
                hi = plot_cells
            m.Add(v.area >= max(floor_area, lo_area))
            m.Add(v.area <= hi)
            asp_i = int(round(100 * aspect))
            m.Add(100 * v.w <= asp_i * v.h)
            m.Add(100 * v.h <= asp_i * v.w)

        # zone contained within the footprint (not the plot)
        m.Add(v.x0 >= fp.x0)
        m.Add(v.x1 <= fp.x1)
        m.Add(v.y0 >= fp.y0)
        m.Add(v.y1 <= fp.y1)
        # hard zoning pins (to footprint edges)
        _apply_pins(m, v, spec.pins.get(zid, Pins()), fp)
        zv[zid] = v
        zinfo.append((zid, v, target_cells_i, min_w, min_h, pref_w, pref_h))

    # non-overlap over all zones
    m.AddNoOverlap2D([v.xi for v in zv.values()], [v.yi for v in zv.values()])

    share_min = _ceil_u(Z.REQUIRED_SHARE_M)
    gap_u = _ceil_u(Z.FORBIDDEN_GAP_M)

    # required adjacency (hard, always-on — see zones.REQUIRED_ADJ docstring).
    # For a composite zone with a cut-axis var (kitchen_laundry), tie the axis to
    # the director's side here so the shape table and the cut stay consistent.
    cut_axis_bools: dict[ZoneId, dict[str, cp_model.IntVar]] = {}
    for a, b in Z.REQUIRED_ADJ:
        if a in zv and b in zv:
            if a in ns_vars:
                cut_axis_bools[a] = _tie_cut_axis(
                    m, zv[a], zv[b], share_min, ns_vars[a], f"req_{a}_{b}"
                )
            else:
                sh = _share_wall(m, zv[a], zv[b], share_min, f"req_{a}_{b}")
                m.Add(sh == 1)

    # forbidden adjacency (hard). Program-controlled: program.adjacency.avoid,
    # defaulting to zones.DEFAULT_AVOID when the caller left it empty.
    for pair in avoid_pairs:
        a, b = pair[0], pair[1]
        if a in zv and b in zv:
            _forbid_adjacent(m, zv[a], zv[b], gap_u, f"fbd_{a}_{b}")

    # soft adjacency (reward). Any shared wall >= one grid cell counts.
    # desirable/semi are program.adjacency.{desirable,semi}, defaulting to
    # zones.DEFAULT_{DESIRABLE,SEMI}. Pairs already hard-required are skipped
    # here — they're guaranteed already, so rewarding them again would just
    # inflate the objective by a constant without changing the chosen layout.
    desirable_bools = []
    for pair in desirable_pairs:
        a, b = pair[0], pair[1]
        if a in zv and b in zv and not _is_required_pair(a, b) and frozenset((a, b)) not in _ACCESS_FORCED_PAIRS:
            desirable_bools.append(_share_wall(m, zv[a], zv[b], 1, f"des_{a}_{b}"))

    semi_bools = []
    for pair in semi_pairs:
        a, b = pair[0], pair[1]
        if a in zv and b in zv and not _is_required_pair(a, b) and frozenset((a, b)) not in _ACCESS_FORCED_PAIRS:
            semi_bools.append(_share_wall(m, zv[a], zv[b], 1, f"semi_{a}_{b}"))

    # --- access graph (Task 5 Phase 2): tree-first circulation ----------------
    # Every zone shares a real >=0.9 m wall with the corridor OR with a zone
    # already circulation-connected (AddBoolOr — a relaxed disjunction, never a
    # rigid star or a single forced spine). Connectivity is guaranteed BY
    # CONSTRUCTION via a mutually-constrained backbone {circulation, entry,
    # living}: each present backbone member shares a wall with another member, so
    # a 3-node graph in which no node can be isolated is always ONE component;
    # every other zone then attaches to a backbone member, so the whole access
    # graph is connected. This is what keeps it packable where the hard-forced
    # edges were INFEASIBLE (garage<->entry on the tight plot; entry<->corridor on
    # the entry-west presets). validator.validate_plan re-proves connectivity on
    # the built geometry and adds the no-through-traffic + hall-Bathroom rules;
    # kitchen/dining reach the backbone through the REQUIRED_ADJ chain to living.
    door_u = _ceil_u(Z.ACCESS_DOOR_M)
    circ: ZoneId = "circulation"
    # Which side of a zone the corridor attached to, read back post-solve from
    # the bools _force_master_corridor_overlap / _share_wall(..., side_bools=...) return
    # (see SolveResult.corridor_sides). Populated for master_suite and
    # kitchen_laundry.
    corridor_side_bools: dict[ZoneId, dict[str, cp_model.IntVar]] = {}

    def _attach(zone: ZoneId, targets: list[ZoneId], tag: str) -> None:
        opts = [
            _share_wall(m, zv[zone], zv[t], door_u, f"{tag}_{t}")
            for t in targets
            if t in zv and t != zone
        ]
        if opts:
            m.AddBoolOr(opts)

    if circ in zv:
        _attach(circ, ["entry", "living"], "acc_circ")        # backbone anchor
        _attach("entry", [circ, "living"], "acc_entry")       # backbone
        _attach("living", [circ, "entry"], "acc_living")      # backbone
        # G: Garage's access parent must be a room the tier-2 rule actually
        # permits (Garage.allowed_ensuite_parents = Mudroom/Foyer, both inside the
        # entry zone), never circulation -- where allowed_ensuite_parents blocks
        # Corridor->Garage and strands it (audit finding; proven in an earlier
        # sweep). _slice_entry derives the mudroom side from the real garage-facing
        # edge and Mudroom spans that whole end, so a zone-level garage<->entry
        # >= door wall already lands entirely on Mudroom (or the Foyer, if the entry
        # zone was too small to slice) at ROOM level -- no room-level pin on Mudroom
        # itself is needed. Dropping `circ` from the disjunction is the whole fix;
        # the entry attach was already the intended parent in the original OR.
        if "entry" in zv:
            _attach("garage", ["entry"], "acc_garage")
        else:
            _attach("garage", [circ], "acc_garage")
        if "master_suite" in zv:
            # master is NOT a plain attach: force the corridor to front the
            # Bedroom band (whichever side wins — see
            # _force_master_corridor_overlap), past the Bathroom|Closet
            # service strip, so the private suite is entered from
            # circulation, never through the living room (the SNiP violation
            # the render caught). Same family as the children center-cover.
            #
            # The E/W overlap floor must clear the service strip by a FULL
            # DOOR WIDTH, not by whatever margin the Bedroom's own min depth
            # happens to leave. This used to be plain
            # `_ceil_u(Master Bedroom.min_h_m)` (2.84m raw, 3.0m grid-ceiled)
            # — which only exceeds the ~2.5m strip depth by 0.5m. That 0.5m
            # guaranteed spillover is BELOW ACCESS_DOOR_M (0.9m), so
            # access_tree's geom.adjacent(..., ACCESS_DOOR_M) check can refuse
            # to award a door even though this "guaranteed" touch is realised
            # — a latent bug that doesn't show up while the solver has enough
            # slack to over-satisfy the constraint, but confirmed to bite for
            # real: an unrelated packing-pressure zone elsewhere in the model
            # was enough to push the optimizer onto exactly a 0.5m spillover,
            # leaving Master Bedroom/Bathroom/Walk-in Closet unreachable
            # despite the "guaranteed" circulation touch. Compute the true
            # floor instead: strip depth + door width. service_u mirrors
            # slicer._slice_master's `service` strip-depth formula exactly
            # (keep them in sync if either standard changes).
            service_u = _ceil_u(max(
                standards.ROOMS["Master Bathroom"].min_h_m,
                standards.ROOMS["Walk-in Closet"].min_h_m,
            ))
            mbed_u = service_u + door_u
            corridor_side_bools["master_suite"] = _force_master_corridor_overlap(
                m, zv[circ], zv["master_suite"], mbed_u, door_u, "acc_master"
            )
            # Master Bedroom must have a real exterior wall (SNiP/Posobie
            # cl. 2.8 daylight requirement) — as a CONSTRAINT, not left to the
            # objective's taste (see _require_master_bedroom_perimeter).
            _require_master_bedroom_perimeter(
                m, zv["master_suite"], fp, corridor_side_bools["master_suite"], "hab_master"
            )
        if "children" in zv and _CHILD_CENTER_COVER:
            # children is NOT a plain attach: force the corridor to front the
            # central Bathroom band so the hall Bathroom is corridor-DIRECT (beds
            # reach through it). This also connects children to the backbone.
            bath_u = _ceil_u(standards.ROOMS["Bathroom"].min_h_m)
            _force_vertical_cover_center(m, zv[circ], zv["children"], bath_u, "acc_child")
        elif "children" in zv:  # diagnostic path: plain disjunction, no Bathroom-direct guarantee
            _attach("children", [circ, "entry"], "acc_child")
        _attach("office", [circ, "entry", "living"], "acc_office")
        if "kitchen_laundry" in zv and force_kitchen_direct:
            # Client-reported pathology: on undersized footprints the Kitchen was
            # only reachable via the Dining->Living chain (REQUIRED_ADJ), so
            # kitchen traffic routed through the living room. standards.py already
            # flags Kitchen requires_circulation_access=True; this closes the gap
            # by forcing a real corridor<->kitchen_laundry wall (>= ACCESS_DOOR_M),
            # the same technique master/children use above. Two independent
            # sweeps (plot-scaled and fixed-20x24-plot) proved this constraint is
            # INFEASIBLE below ~184 m2 footprint and OPTIMAL at/above it on both
            # feasible presets (gW_eN, gE_eN), with master's south daylight pin
            # and children's center-cover untouched, and Living never on the
            # kitchen's access-tree path.
            kl_corridor_bools: dict[str, cp_model.IntVar] = {}
            sh = _share_wall(
                m, zv[circ], zv["kitchen_laundry"], door_u, "acc_kitchen", kl_corridor_bools
            )
            m.Add(sh == 1)
            corridor_side_bools["kitchen_laundry"] = kl_corridor_bools
            # K: the zone-level share above only promises corridor<->kitchen_laundry
            # ZONE; harden it to the KITCHEN ROOM so the corridor's shared segment
            # can never land on the Laundry strip instead (which would leave the
            # kitchen reachable only via Dining->Living). Reified from the Dining
            # cut side (cut_axis_bools) + the corridor side (kl_corridor_bools).
            if "kitchen_laundry" in cut_axis_bools:
                l_ns = _ceil_u(standards.ROOMS["Laundry"].min_h_m)  # N/S-cut strip
                l_we = _ceil_u(standards.ROOMS["Laundry"].min_w_m)  # W/E-cut strip
                _force_corridor_overlaps_kitchen(
                    m, zv[circ], zv["kitchen_laundry"],
                    cut_axis_bools["kitchen_laundry"], kl_corridor_bools,
                    l_ns, l_we, door_u, "acc_kitchen_room",
                )

    # --- objective (scaled by plot_cells to keep integer coefficients) --------
    # human objective = 12*coverage_pct + 40*desirable_met + 15*semi_met
    #                 - 3*public_non_south + 2*service_northness ;
    #                 coverage_pct = 100*area/plot.
    total_area = m.NewIntVar(0, plot_cells, "total_area")
    m.Add(total_area == sum(v.area for v in zv.values()))

    # Zones must tile at least `cov_min` of the footprint. Contained +
    # non-overlapping already give total_area <= fp.area, so at cov_min == 1.0
    # this is exact tiling.
    #
    # HISTORY — the comment that used to sit here claimed exact tiling was
    # infeasible, because "a small (~3%) void always remains: that void is the
    # un-modelled circulation Task 3 will place". Circulation has since become a
    # real, placed, solved zone with its own rectangle in SolveResult.rects, so
    # the slack no longer reserves space for anything — it just admits dead
    # pockets and facade notches (the architect's "lazimsiz girinti cixintilar").
    # The infeasibility claim was never re-tested after that change; `coverage_min`
    # exists so it can be.
    m.Add(100 * total_area >= int(round(100 * cov_min)) * fp.area)

    # The ENVELOPE must be a clean rectangle even where the interior is still
    # allowed pockets. Logically strictly weaker than exact tiling — but MEASURED
    # (roomy 184, gW_eN/gE_eN, seed 1): NOT cheaper. At the stock area band it is
    # proven INFEASIBLE, same as exact tiling, and for the same reason: the
    # binding constraint is AREA_LO/AREA_HI, not the packing. Isolating the edges
    # one at a time, S / N / W / E are each satisfiable alone and S+N together,
    # but W+E together is infeasible — the zone widths cannot reach both side
    # facades at every row inside a [0.85, 1.20] area band.
    if perimeter_complete:
        _add_perimeter_complete(m, fp, zv, W, H)

    # coverage term now measures FOOTPRINT fill (total_area is bounded by the
    # footprint, itself bounded to ~target_area_m2 — so this no longer pays to
    # inflate zones out to the plot edge as it did against plot_cells).
    obj_terms: list[cp_model.LinearExpr] = [12 * 100 * total_area]

    # Pull the footprint toward target_area_m2 rather than to the top of its
    # band. Weighted (3*plot_cells/cell) to beat the two terms that would
    # otherwise inflate the house — the fill reward (1200/cell) and the
    # service-northness reward (2*plot_cells/cell of northness, which pays for a
    # taller footprint) — while staying below the soft-adjacency reward so a
    # genuinely better-connected plan can still spend a little area. When the
    # zones CAN tile a smaller rectangle this centres the house at target,
    # leaving a real setback; when they cannot (e.g. the example program, whose
    # Neufert-legal zone minima only tile at ~1.15x target) the band's hard
    # upper rail binds instead and the setback is whatever slack remains.
    target_cells_i = int(round(house_cells))
    fp_dev = m.NewIntVar(0, plot_cells, "fp_dev")
    m.Add(fp_dev >= fp.area - target_cells_i)
    m.Add(fp_dev >= target_cells_i - fp.area)
    obj_terms.append(-3 * plot_cells * fp_dev)

    if desirable_bools:
        obj_terms.append(plot_cells * 40 * sum(desirable_bools))
    if semi_bools:
        obj_terms.append(plot_cells * 15 * sum(semi_bools))

    # public rooms penalised when not on the footprint's south (daylight) edge
    for zid in present:
        sp = program.space(zid)
        is_public = zid in Z.PUBLIC_ZONES or (sp is not None and sp.category == "living")
        if is_public:
            nz = m.NewBoolVar(f"{zid}_nonsouth")
            m.Add(zv[zid].y0 >= fp.y0 + 1).OnlyEnforceIf(nz)
            m.Add(zv[zid].y0 == fp.y0).OnlyEnforceIf(nz.Not())
            obj_terms.append(-plot_cells * 3 * nz)

    # service rooms rewarded for being toward the footprint's north (street) side
    for zid in present:
        sp = program.space(zid)
        is_service = zid in Z.SERVICE_ZONES or (sp is not None and sp.category == "service")
        if is_service:
            obj_terms.append(plot_cells * 2 * (zv[zid].y0 - fp.y0))

    # target-adherence (Task 4): hold each zone near its RECONCILED target area,
    # L1 = |achieved - target|. Task 3 showed area sloshing 30% inside the
    # [0.85,1.20] band for free (dining 1.18x tight / 0.86x roomy on ONE program),
    # which makes ranking meaningless. ADHERE per deviation-cell is set ABOVE the
    # coverage-fill reward (1200/cell) and the service-northness reward — the two
    # terms that inflate zones to the band ceiling for nothing — so area stops
    # sloshing; but it stays BELOW the soft-adjacency reward (40*plot_cells/bool)
    # so a genuinely better-connected plan can still spend a few m2 off-target.
    # It must DECISIVELY beat coverage (not just edge it): coverage rewards
    # total_area globally (1200/cell), so a thin margin leaves zones parked just
    # above target — measured: dining only reaches its reconciled target at
    # ADHERE >= 6000. Kept below the soft-adjacency reward (40*plot_cells/bool ~=
    # 30k) so a desirable can still pull a zone ~1 m2 off target when it pays.
    ADHERE = 6000
    for zid, v, tcells, *_ in zinfo:
        dev = m.NewIntVar(0, plot_cells, f"{zid}_adh")
        m.Add(dev >= v.area - tcells)
        m.Add(dev >= tcells - v.area)
        obj_terms.append(-ADHERE * dev)

    # soft brief-minima (Task 4b): a brief min ABOVE the Neufert floor is a weak
    # preference, not a veto. Penalise only the shortfall below it, and weakly, so
    # a tighter Neufert-legal shape that packs better (the 2.5 m N/S kitchen vs
    # the LLM's 4.0 m guess) is still chosen when the packing/adherence gain pays.
    SOFT_MIN = 60
    for zid, v, tcells, min_w, min_h, pref_w, pref_h in zinfo:
        if pref_w > min_w:
            sw = m.NewIntVar(0, W, f"{zid}_sw")
            m.Add(sw >= pref_w - v.w)
            obj_terms.append(-SOFT_MIN * sw)
        if pref_h > min_h:
            sh = m.NewIntVar(0, H, f"{zid}_sh")
            m.Add(sh >= pref_h - v.h)
            obj_terms.append(-SOFT_MIN * sh)

    m.Maximize(sum(obj_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    status = solver.Solve(m)

    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    rects: list[ZoneRect] = []
    footprint_m: tuple[float, float, float, float] | None = None
    cut_sides: dict[str, str] = {}
    corridor_sides: dict[str, str] = {}
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
        footprint_m = (
            solver.Value(fp.x0) * GRID_M,
            solver.Value(fp.y0) * GRID_M,
            solver.Value(fp.x1) * GRID_M,
            solver.Value(fp.y1) * GRID_M,
        )
        # the cut axis the solver committed to, as the director's side
        for zid, bools in cut_axis_bools.items():
            for side, bvar in bools.items():
                if solver.Value(bvar) == 1:
                    cut_sides[zid] = side
                    break
        # which side the corridor attached to, for zones that recorded bools
        for zid, bools in corridor_side_bools.items():
            for side, bvar in bools.items():
                if solver.Value(bvar) == 1:
                    corridor_sides[zid] = side
                    break
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
        kitchen_direct=force_kitchen_direct,
        footprint_m=footprint_m,
        cut_sides=cut_sides,
        corridor_sides=corridor_sides,
    )
