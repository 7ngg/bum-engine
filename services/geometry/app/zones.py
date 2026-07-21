"""Canonical macro-zone metadata shared by solver, slicer, validator, SVG.

Coordinate convention (fixed, solver-internal):
  origin = plot SW corner, +x = east, +y = north.
  y = 0     -> "south": garden / daylight side. Public rooms and the master
               suite want to be here.
  y = depth -> "north": street / service side. Garage, entry and wet/service
               rooms want to be here.
The program's `orientation` field records how this internal frame maps to the
real compass; the Revit builder may rotate accordingly. Solver geometry always
uses this frame so the hard-zoning rules below are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import standards
from .models import Program, Space, ZoneId

# Solver operates on these free rectangles. `circulation` is live as of Task 5:
# it is not in the brief — solver.solve() injects a derived corridor Space (see
# inject_circulation below) so `present` picks it up here.
ZONE_ORDER: list[ZoneId] = [
    "living",
    "dining",
    "kitchen_laundry",
    "master_suite",
    "children",
    "office",
    "entry",
    "garage",
    "circulation",
]

# Zones the slicer subdivides into finer rooms (composites).
COMPOSITE_ZONES: set[ZoneId] = {"master_suite", "children", "kitchen_laundry", "entry"}

# --- objective classification -------------------------------------------------
# "public" rooms are penalised when not on the south edge; "service" rooms are
# rewarded for being north. Derived from the program category so the brief can
# override, with these as the canonical defaults.
PUBLIC_ZONES: set[ZoneId] = {"living", "dining"}
SERVICE_ZONES: set[ZoneId] = {"garage", "kitchen_laundry"}
# Circulation is neither public (daylight-seeking) nor service (street-seeking):
# it fills the interior and connects. Kept explicit so the objective never
# rewards or penalises the corridor for its y-position.
CIRCULATION_ZONES: set[ZoneId] = {"circulation"}

# --- adjacency rules (hard + soft) -------------------------------------------
# REQUIRED_ADJ is a hard invariant, NOT LLM-controllable: solver.py applies it
# unconditionally regardless of what a Program supplies. This keeps the LLM
# from ever being able to make the model infeasible by wishing an adjacency
# away, and from taking credit twice for something already guaranteed.
REQUIRED_ADJ: list[tuple[ZoneId, ZoneId]] = [
    ("kitchen_laundry", "dining"),
    ("dining", "living"),
]
REQUIRED_SHARE_M = 1.5

# DEFAULT_* are fallbacks solver.py uses only when the corresponding list on
# Program.adjacency is empty (i.e. the LLM/caller didn't specify one) — once
# populated, Program.adjacency is the live source solver.py reads.
FORBIDDEN_GAP_M = 0.5

DEFAULT_AVOID: list[tuple[ZoneId, ZoneId]] = [
    ("master_suite", "kitchen_laundry"),
    ("garage", "living"),
]

DEFAULT_DESIRABLE: list[tuple[ZoneId, ZoneId]] = [
    ("entry", "living"),
    ("entry", "office"),
    ("entry", "children"),
    ("garage", "entry"),
    ("kitchen_laundry", "entry"),
    ("children", "living"),
]

DEFAULT_SEMI: list[tuple[ZoneId, ZoneId]] = []

# Default human-readable room name per zone (used when a zone is not sliced).
ZONE_DISPLAY: dict[ZoneId, str] = {
    "living": "Living",
    "dining": "Dining",
    "kitchen_laundry": "Kitchen",
    "master_suite": "Master Bedroom",
    "children": "Bedroom",
    "office": "Office",
    "entry": "Foyer",
    "garage": "Garage",
    "circulation": "Corridor",
}


# --- circulation (Task 5) ----------------------------------------------------
# The corridor is a DERIVED zone, never from the brief: sized here, injected into
# the program before solving, then placed by the solver like any other zone.

CORRIDOR_W = 1.2  # standards.Corridor min width (m); the spine's short side
ACCESS_DOOR_M = 0.9  # min shared-wall length for an access-graph edge (a doorway)

# Zone -> the room whose standards.py flags represent the zone for access rules
# (requires_circulation_access / allowed_ensuite_parents). A composite zone is
# represented by its circulation-facing room.
ZONE_PRIMARY_ROOM: dict[ZoneId, str] = {
    "living": "Living",
    "dining": "Dining",
    "kitchen_laundry": "Kitchen",
    "master_suite": "Master Bedroom",
    "children": "Bedroom",
    "office": "Office",
    "entry": "Foyer",
    "garage": "Garage",
}


def _circulation_served_count(program: Program) -> int:
    """How many present zones want direct circulation access (drives the corridor
    size). Reads requires_circulation_access off each zone's primary room."""
    n = 0
    for zid, room in ZONE_PRIMARY_ROOM.items():
        spec = standards.ROOMS.get(room)
        if spec is not None and spec.requires_circulation_access and program.space(zid) is not None:
            n += 1
    return n


def corridor_target_m2(footprint_target_m2: float, door_count: int) -> float:
    """Derived corridor area (NOT from the brief). A ~1.2 m spine long enough to
    give each circulation-served zone a doorway, floored at ~half the house's
    long span so a small program still gets a real hall. Sized to roughly fill
    the free-rectangle packing void (~4-8 m2 historically). KNOB: revisit the
    constants once real plans are visible."""
    span = (footprint_target_m2 * 1.3) ** 0.5  # ~ the house's long side
    return round(CORRIDOR_W * max(float(door_count), 0.5 * span), 1)



def inject_circulation(program: Program) -> tuple[Program, list[str]]:
    """Return (program with a derived `circulation` Space appended, warnings).
    No-op if the program already carries one. The corridor's target is derived
    (corridor_target_m2); its minima come from standards.Corridor. reconcile then
    holds it fixed (like the garage) while the other habitable targets rescale."""
    if program.space("circulation") is not None:
        return program, []
    served = _circulation_served_count(program)
    tgt = corridor_target_m2(program.footprint_target_m2, served)
    corridor = Space(
        id="circulation",
        target_m2=tgt,
        min_w_m=CORRIDOR_W,
        min_h_m=CORRIDOR_W,
        category="circ",
    )
    new_spaces = list(program.spaces) + [corridor]
    reconciled = program.model_copy(update={"spaces": new_spaces})
    return reconciled, [
        f"injected circulation zone (derived target {tgt:.1f} m2, {served} served zones)"
    ]
