"""Typed data contracts for program.json and layout.json (schema v1.0.0).

These pydantic models are the in-process mirror of the JSON Schemas in
/schemas. They validate shape and types; the JSON Schemas remain the wire
contract used across service boundaries.
"""

from __future__ import annotations

import warnings
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator

# The layout.json wire version — shared with schemas/layout.schema.json and
# revit/RevitBuilder/LayoutModel.cs, both unchanged this task, so it stays 1.1.0.
SCHEMA_VERSION = "1.1.0"
# program.json forked to 1.2.0 for the target_area_m2 -> footprint_target_m2
# rename (additive; 1.0.0/1.1.0 still load). The layout contract did not change,
# so the two versions are no longer in lockstep.
PROGRAM_SCHEMA_VERSION = "1.2.0"
SUPPORTED_VERSIONS = ("1.0.0", "1.1.0", "1.2.0")

ZoneId = Literal[
    "living",
    "dining",
    "kitchen_laundry",
    "master_suite",
    "children",
    "office",
    "entry",
    "garage",
    # 1.1.0, inert: no preset places it, gemini.py never emits it, no
    # program uses it yet. Exists so a corridor is expressible at all.
    "circulation",
]

Category = Literal["living", "private", "wet", "service", "circ", "office", "outdoor"]
Orientation = Literal["N", "E", "S", "W"]

# ---------------------------------------------------------------------------
# program.json
# ---------------------------------------------------------------------------


class Plot(BaseModel):
    width_m: float = Field(gt=0, le=200)
    depth_m: float = Field(gt=0, le=200)


class Space(BaseModel):
    id: ZoneId
    target_m2: float = Field(gt=0)
    min_w_m: float = Field(gt=0)
    min_h_m: float = Field(gt=0)
    category: Category
    tags: list[str] = Field(default_factory=list)
    # 1.1.0, optional, not on the wire for existing documents. Per-zone
    # override of the standards.py table; solver/slicer enforcement lands
    # in a later task, so these are currently unread.
    max_aspect: float | None = Field(default=None, gt=0)
    min_area_m2: float | None = Field(default=None, gt=0)


class Adjacency(BaseModel):
    desirable: list[list[ZoneId]] = Field(default_factory=list)
    semi: list[list[ZoneId]] = Field(default_factory=list)
    avoid: list[list[ZoneId]] = Field(default_factory=list)


# Categories that are gross footprint but NOT habitable floor ("общая
# площадь"): the garage (service) and any outdoor space are excluded when
# summing habitable area.
_NON_HABITABLE = ("service", "outdoor")


class Program(BaseModel):
    version: Literal["1.0.0", "1.1.0", "1.2.0"] = PROGRAM_SCHEMA_VERSION
    plot: Plot
    orientation: Orientation
    # GROSS FOOTPRINT, GARAGE INCLUDED — the area the solver's footprint band
    # bounds (a rectangle that physically contains the garage). 1.2.0 renamed
    # this from target_area_m2; that name stays a read alias (below) and an
    # accepted input key, so 1.0.0/1.1.0 documents and solver.py still resolve.
    footprint_target_m2: float = Field(
        gt=0, validation_alias=AliasChoices("footprint_target_m2", "target_area_m2")
    )
    floors: int = Field(ge=1, le=4)
    spaces: list[Space] = Field(min_length=1)
    adjacency: Adjacency = Field(default_factory=Adjacency)

    model_config = {"populate_by_name": True}

    def space(self, zone_id: ZoneId) -> Space | None:
        for s in self.spaces:
            if s.id == zone_id:
                return s
        return None

    @property
    def target_area_m2(self) -> float:
        """Deprecated 1.0.0/1.1.0 name for footprint_target_m2. Kept so
        solver.py and older documents keep resolving unchanged."""
        return self.footprint_target_m2

    @property
    def habitable_area_m2(self) -> float:
        """Общая площадь: sum of space targets over habitable categories
        (excludes service/outdoor — i.e. the garage). Derived at load and
        reported; not a solver constraint yet."""
        return sum(s.target_m2 for s in self.spaces if s.category not in _NON_HABITABLE)

    @model_validator(mode="after")
    def _warn_footprint_reconciliation(self) -> "Program":
        # Gemini fills footprint_target_m2 and the per-space targets
        # independently; nothing else reconciles them. Warn (not error) on a
        # gross mismatch so the footprint band isn't silently un-tileable.
        total = sum(s.target_m2 for s in self.spaces)
        ft = self.footprint_target_m2
        if ft > 0 and abs(total - ft) > 0.15 * ft:
            warnings.warn(
                f"space targets sum to {total:.0f} m2 but footprint_target_m2 is "
                f"{ft:.0f} m2 ({abs(total - ft) / ft * 100:.0f}% off); the footprint "
                f"band may not tile cleanly",
                stacklevel=2,
            )
        return self


# ---------------------------------------------------------------------------
# layout.json
# ---------------------------------------------------------------------------

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1
Point = tuple[float, float]


class Room(BaseModel):
    name: str
    category: Category
    rect_m: list[float]  # [x0, y0, x1, y1]
    zone: str | None = None


class Wall(BaseModel):
    id: str
    start: list[float]  # [x, y]
    end: list[float]
    thickness_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    exterior: bool


class Door(BaseModel):
    from_: str = Field(alias="from")
    to: str
    wall_id: str
    center: list[float]
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)

    model_config = {"populate_by_name": True}


class Window(BaseModel):
    room: str
    wall_id: str
    center: list[float]
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    sill_m: float = Field(ge=0)


class Terrace(BaseModel):
    rect_m: list[float]


class Layout(BaseModel):
    version: Literal["1.0.0", "1.1.0"] = SCHEMA_VERSION
    preset: str
    seed: int
    objective: float
    levels: int = Field(ge=1)
    wall_height_m: float = Field(gt=0)
    plot: Plot
    orientation: Orientation
    rooms: list[Room]
    walls: list[Wall]
    doors: list[Door]
    windows: list[Window]
    entry: Door
    terrace: Terrace | None = None
    warnings: list[str] = Field(default_factory=list)

    def dump(self) -> dict:
        """Serialize with `from` alias restored for wire/schema compatibility."""
        return self.model_dump(by_alias=True, exclude_none=False)
