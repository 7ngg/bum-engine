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

from .models import ZoneId

# Solver operates on these eight free rectangles.
ZONE_ORDER: list[ZoneId] = [
    "living",
    "dining",
    "kitchen_laundry",
    "master_suite",
    "children",
    "office",
    "entry",
    "garage",
]

# Zones the slicer subdivides into finer rooms (composites).
COMPOSITE_ZONES: set[ZoneId] = {"master_suite", "children", "kitchen_laundry", "entry"}

# --- objective classification -------------------------------------------------
# "public" rooms are penalised when not on the south edge; "service" rooms are
# rewarded for being north. Derived from the program category so the brief can
# override, with these as the canonical defaults.
PUBLIC_ZONES: set[ZoneId] = {"living", "dining"}
SERVICE_ZONES: set[ZoneId] = {"garage", "kitchen_laundry"}

# --- adjacency rules (hard + soft), canonical --------------------------------
# Required: must share a wall of at least this length (metres).
REQUIRED_ADJ: list[tuple[ZoneId, ZoneId]] = [
    ("kitchen_laundry", "dining"),
    ("dining", "living"),
]
REQUIRED_SHARE_M = 1.5

# Forbidden: must be separated by at least this gap (metres).
FORBIDDEN_ADJ: list[tuple[ZoneId, ZoneId]] = [
    ("master_suite", "kitchen_laundry"),
    ("garage", "living"),
]
FORBIDDEN_GAP_M = 0.5

# Soft: rewarded when the pair shares any wall.
SOFT_ADJ: list[tuple[ZoneId, ZoneId]] = [
    ("entry", "living"),
    ("entry", "office"),
    ("entry", "children"),
    ("garage", "entry"),
    ("kitchen_laundry", "entry"),
    ("children", "living"),
]

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
}
