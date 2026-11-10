from fastapi import APIRouter, HTTPException

from .. import services
from ..models import NoteIn

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("")
def list_notes(start: str, end: str):
    """Notes in a date range, so the calendar can mark days that have one."""
    return services.list_notes(start, end)


@router.get("/{date}")
def get_note(date: str):
    try:
        return services.get_note(date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")


@router.put("/{date}")
def put_note(date: str, body: NoteIn):
    try:
        return services.set_note(date, body.body)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
