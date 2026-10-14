"""Preset regions.

`id` is what the frontend sends as `region=<id>`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    lat: float
    lon: float


# Kept deliberately short; adding more presets is a separate decision.
REGIONS: list[Region] = [
    Region(id="dhaka", name="Dhaka", lat=23.81, lon=90.41),
    Region(id="chattogram", name="Chattogram", lat=22.36, lon=91.78),
    Region(id="new-delhi", name="New Delhi", lat=28.61, lon=77.21),
    Region(id="mumbai", name="Mumbai", lat=19.08, lon=72.88),
    Region(id="karachi", name="Karachi", lat=24.86, lon=67.01),
]

BY_ID = {r.id: r for r in REGIONS}


def get_region(region_id: str) -> Region | None:
    """Return the preset with this id, or None so the route can return 422."""
    return BY_ID.get(region_id)
