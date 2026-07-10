"""Preset = {garage side W/E} x {entry placement N/W}.

Each preset resolves the hard-zoning *pins* (which plot edge a zone must touch)
into a concrete per-zone directive the solver turns into linear constraints.
Running the four presets across several seeds yields the variant fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ZoneId

PRESETS: list[str] = ["gW_eN", "gW_eW", "gE_eN", "gE_eW"]


@dataclass(frozen=True)
class Pins:
    """Edge constraints for one zone (all optional)."""

    south: bool = False  # y0 == 0
    north: bool = False  # y1 == H
    west: bool = False  # x0 == 0
    east: bool = False  # x1 == W
    max_y1_frac: float | None = None  # y1 <= frac * H


@dataclass(frozen=True)
class PresetSpec:
    name: str
    garage_side: str  # "W" | "E"
    entry_side: str  # "N" | "W"
    pins: dict[ZoneId, Pins] = field(default_factory=dict)


def resolve(name: str) -> PresetSpec:
    """Build the per-zone pin table for a preset name like 'gW_eN'."""
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; expected one of {PRESETS}")
    garage_side = "W" if "gW" in name else "E"
    entry_side = "N" if "eN" in name else "W"

    pins: dict[ZoneId, Pins] = {
        # Public + master anchored to the south (daylight/garden) edge.
        "living": Pins(south=True),
        "master_suite": Pins(south=True, max_y1_frac=0.62),
        # Garage on the street (north) edge and one side.
        "garage": Pins(north=True, west=(garage_side == "W"), east=(garage_side == "E")),
        # Children on the wall opposite the garage.
        "children": Pins(west=(garage_side == "E"), east=(garage_side == "W")),
        # Entry to the north or the west per preset.
        "entry": Pins(north=(entry_side == "N"), west=(entry_side == "W")),
    }
    return PresetSpec(name=name, garage_side=garage_side, entry_side=entry_side, pins=pins)
