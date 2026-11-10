from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .. import claude
from ..models import ChatIn

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/status")
def status():
    return {"configured": claude.configured()}


@router.get("/history")
def history():
    return claude.history_for_ui()


@router.delete("/history", status_code=204)
def clear():
    claude.clear_history()


@router.post("")
async def chat(body: ChatIn):
    return StreamingResponse(
        claude.stream_chat(body.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
