"""How tall a building is, and -- just as important -- how that is known.

OpenStreetMap records height two ways when it records it at all: a
``height`` tag in metres (sometimes with units, sometimes in feet), or a
count of ``building:levels``. Most buildings have neither. In the
Southern India extract, 8,512 of 7,426,382 buildings carry a height tag
and 60,685 a level count: well under one percent between them.

So every height returned here comes with its source:

  measured   a height tag, parsed
  levels     a level count at 3 m a storey, the convention OSM 3D
             renderers share
  assumed    nothing recorded; a nominal height by building type, which
             the view draws differently so no one mistakes it for data

The nominal heights are deliberately modest. A view that guessed tall
would suggest a skyline nobody measured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Height", "resolve_height", "parse_length"]

METRES_PER_LEVEL = 3.0

# Nominal heights for buildings with nothing recorded, by building tag.
# Low on purpose: an assumed height should never be the tallest thing
# in the scene.
_NOMINAL = {
    "hut": 2.5, "shed": 2.5, "garage": 2.5, "garages": 2.5, "kiosk": 2.5,
    "carport": 2.5, "roof": 3.0, "toilets": 3.0, "cabin": 3.0,
    "house": 4.0, "residential": 4.0, "detached": 4.0, "bungalow": 4.0,
    "semidetached_house": 6.0, "terrace": 6.0,
    "apartments": 9.0, "hotel": 9.0, "dormitory": 9.0,
    "commercial": 6.0, "retail": 6.0, "office": 9.0,
    "industrial": 7.0, "warehouse": 7.0, "factory": 7.0,
    "school": 6.0, "university": 9.0, "college": 9.0, "hospital": 9.0,
    "temple": 8.0, "church": 8.0, "mosque": 8.0, "religious": 8.0,
}
_DEFAULT_NOMINAL = 4.0

_NUMBER = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(m|metres?|meters?|ft|feet|'|)?\s*$",
                     re.IGNORECASE)
_FEET_INCHES = re.compile(r"^\s*(\d+)\s*'\s*(\d+)\s*\"?\s*$")


@dataclass(frozen=True)
class Height:
    metres: float
    base_metres: float
    source: str          # "measured" | "levels" | "assumed"


def parse_length(value: str | None) -> float | None:
    """A length tag in metres, or None when it cannot be read honestly.

    Accepts "12", "12.5", "12,5", "12 m", "40 ft", "40'", "12'6\"".
    Refuses anything else rather than guessing at it: "approx 10",
    "10-12", "tall".
    """
    if value is None:
        return None
    text = str(value).strip()
    m = _FEET_INCHES.match(text)
    if m:
        return (int(m.group(1)) * 12 + int(m.group(2))) * 0.0254
    m = _NUMBER.match(text)
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").lower()
    if unit in ("ft", "feet", "'"):
        number *= 0.3048
    # A negative or absurd height is a typo, not a building.
    if not 0.0 <= number <= 900.0:
        return None
    return number


def _levels(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        levels = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if not 0 <= levels <= 200:
        return None
    return levels


def resolve_height(tags: dict[str, str]) -> Height:
    """The best height OSM supports for a building, and where it came from."""
    base = parse_length(tags.get("min_height"))
    if base is None:
        min_level = _levels(tags.get("building:min_level"))
        base = min_level * METRES_PER_LEVEL if min_level is not None else 0.0

    height = parse_length(tags.get("height"))
    if height is not None and height > base:
        return Height(height, base, "measured")

    levels = _levels(tags.get("building:levels"))
    if levels is not None and levels > 0:
        roof = _levels(tags.get("roof:levels")) or 0.0
        return Height(base + (levels + roof) * METRES_PER_LEVEL, base, "levels")

    kind = (tags.get("building") or tags.get("building:part") or "").lower()
    return Height(base + _NOMINAL.get(kind, _DEFAULT_NOMINAL), base, "assumed")
