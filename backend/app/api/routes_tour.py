"""Guided walkthrough for evaluators: public, sandboxed, one step per request, nothing kept between requests."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import NotFound
from app.services import tour

router = APIRouter(prefix="/api")


@router.get("/tour", tags=["tour"])
def tour_outline():
    """The steps. No login needed: the walkthrough never touches the institute's data or sends email."""
    return tour.start()


@router.post("/tour/steps/{index}", tags=["tour"])
def run_tour_step(index: int):
    """Runs step `index` (0-based) on a fresh private sandbox, replaying the earlier steps first."""
    if not 0 <= index < len(tour.STEPS):
        raise NotFound("No such step")
    return tour.run_step(index)
