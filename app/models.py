from pydantic import BaseModel, Field


class ReminderIn(BaseModel):
    text: str = Field(min_length=1)
    due_at: str
    repeat: str = "none"


class ReminderPatch(BaseModel):
    text: str | None = None
    due_at: str | None = None
    repeat: str | None = None
    done: bool | None = None


class SnoozeIn(BaseModel):
    minutes: int = Field(default=10, ge=1, le=7 * 24 * 60)


class EventIn(BaseModel):
    title: str = Field(min_length=1)
    start: str
    end: str | None = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    remind_min: int = Field(default=15, ge=-1, le=60 * 24 * 14)  # -1 = no reminder


class EventPatch(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    all_day: bool | None = None
    location: str | None = None
    description: str | None = None
    remind_min: int | None = Field(default=None, ge=-1, le=60 * 24 * 14)


class NoteIn(BaseModel):
    body: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1)


class PageIn(BaseModel):
    body: str = ""
    kind: str = "text"  # only used when creating: 'text' | 'checklist'


class ChecklistSettings(BaseModel):
    reset_mode: str
    reset_time: str = "06:00"


class ItemIn(BaseModel):
    text: str = ""
    after_id: int | None = None


class ItemPatch(BaseModel):
    text: str | None = None
    comment: str | None = None
    done: bool | None = None


class OrderIn(BaseModel):
    ids: list[int]
