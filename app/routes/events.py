from fastapi import APIRouter, HTTPException

from .. import services
from ..models import EventIn, EventPatch

router = APIRouter(prefix="/api/events", tags=["events"])


def _bad(e: ValueError):
    return HTTPException(422, str(e))


@router.get("")
def list_events(start: str | None = None, end: str | None = None):
    return services.list_events(start, end)


@router.post("", status_code=201)
def create_event(body: EventIn):
    try:
        return services.create_event(**body.model_dump())
    except ValueError as e:
        raise _bad(e)


@router.patch("/{eid}")
def update_event(eid: int, body: EventPatch):
    try:
        row = services.update_event(eid, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise _bad(e)
    if not row:
        raise HTTPException(404, "Event not found")
    return row


@router.delete("/{eid}", status_code=204)
def delete_event(eid: int):
    if not services.delete_event(eid):
        raise HTTPException(404, "Event not found")
