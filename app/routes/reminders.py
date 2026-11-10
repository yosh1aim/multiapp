from fastapi import APIRouter, HTTPException

from .. import services
from ..models import ReminderIn, ReminderPatch, SnoozeIn

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("")
def list_reminders():
    return services.list_reminders()


@router.post("", status_code=201)
def create_reminder(body: ReminderIn):
    try:
        return services.create_reminder(body.text, body.due_at, body.repeat)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.patch("/{rid}")
def update_reminder(rid: int, body: ReminderPatch):
    try:
        row = services.update_reminder(rid, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not row:
        raise HTTPException(404, "Reminder not found")
    return row


@router.post("/{rid}/complete")
def complete(rid: int):
    row = services.complete_reminder(rid)
    if not row:
        raise HTTPException(404, "Reminder not found")
    return row


@router.post("/{rid}/snooze")
def snooze(rid: int, body: SnoozeIn):
    row = services.snooze_reminder(rid, body.minutes)
    if not row:
        raise HTTPException(404, "Reminder not found")
    return row


@router.delete("/{rid}", status_code=204)
def delete(rid: int):
    services.delete_reminder(rid)
