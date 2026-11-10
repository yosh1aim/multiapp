from fastapi import APIRouter

from .. import services

router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("")
def progress(note_id: int | None = None):
    return services.progress(note_id)
