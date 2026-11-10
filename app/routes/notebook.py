from fastapi import APIRouter, HTTPException

from .. import services
from ..models import ChecklistSettings, ItemIn, ItemPatch, OrderIn, PageIn

router = APIRouter(prefix="/api/notebook", tags=["notebook"])


def _422(e: ValueError):
    return HTTPException(422, str(e))


def _page_or_404(pid: int) -> dict:
    page = services.get_page(pid)
    if not page:
        raise HTTPException(404, "Note not found")
    return page


@router.get("")
def list_pages(q: str | None = None):
    return services.list_pages(q)


@router.post("", status_code=201)
def create_page(body: PageIn):
    try:
        return services.create_page(body.body, body.kind)
    except ValueError as e:
        raise _422(e)


@router.put("/{pid}")
def update_page(pid: int, body: PageIn):
    row = services.update_page(pid, body.body)
    if not row:
        raise HTTPException(404, "Note not found")
    return row


@router.delete("/{pid}", status_code=204)
def delete_page(pid: int):
    services.delete_page(pid)


# ----- checklists -----
@router.put("/{pid}/settings")
def checklist_settings(pid: int, body: ChecklistSettings):
    _page_or_404(pid)
    try:
        return services.set_checklist_settings(pid, body.reset_mode, body.reset_time)
    except ValueError as e:
        raise _422(e)


@router.post("/{pid}/reset")
def reset(pid: int):
    _page_or_404(pid)
    return services.reset_checklist(pid)


@router.post("/{pid}/items", status_code=201)
def add_item(pid: int, body: ItemIn):
    item = services.add_item(pid, body.text, body.after_id)
    if not item:
        raise HTTPException(404, "Checklist not found")
    return item


@router.put("/{pid}/order", status_code=204)
def reorder(pid: int, body: OrderIn):
    _page_or_404(pid)
    try:
        services.reorder_items(pid, body.ids)
    except ValueError as e:
        raise _422(e)


@router.patch("/items/{iid}")
def update_item(iid: int, body: ItemPatch):
    item = services.update_item(iid, **body.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(404, "Item not found")
    return item


@router.delete("/items/{iid}", status_code=204)
def delete_item(iid: int):
    services.delete_item(iid)
