"""API endpoints.

Phase 1 ships only the endpoints that need no data: health and the preset
region list. `/api/analysis` is added in Phase 2.
"""

from fastapi import APIRouter

from .regions import REGIONS
from .schemas import HealthOut, PresetRegion

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness check. Also used by Render and the frontend to detect a
    service that is still waking up from a free-tier cold start."""
    return HealthOut(status="ok")


@router.get("/regions", response_model=list[PresetRegion])
async def list_regions() -> list[PresetRegion]:
    """The preset regions the frontend offers in its dropdown."""
    return [PresetRegion(id=r.id, name=r.name, lat=r.lat, lon=r.lon) for r in REGIONS]
