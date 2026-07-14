"""Typed data contracts for program.json and layout.json (schema v1.0.0).

These pydantic models are the in-process mirror of the JSON Schemas in
/schemas. They validate shape and types; the JSON Schemas remain the wire
contract used across service boundaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1.0"
SUPPORTED_VERSIONS = ("1.0.0", "1.1.0")

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


class Program(BaseModel):
    version: Literal["1.0.0", "1.1.0"] = SCHEMA_VERSION
    plot: Plot
    orientation: Orientation
    target_area_m2: float = Field(gt=0)
    floors: int = Field(ge=1, le=4)
    spaces: list[Space] = Field(min_length=1)
    adjacency: Adjacency = Field(default_factory=Adjacency)

    def space(self, zone_id: ZoneId) -> Space | None:
        for s in self.spaces:
            if s.id == zone_id:
                return s
        return None


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
