import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import ROOT, init_db

load_dotenv(ROOT / ".env")

from . import scheduler  # noqa: E402  (after load_dotenv so env is populated)
from .routes import chat, events, notebook, notes, progress, reminders, settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler.run_forever())  # first tick = catch-up of missed reminders
    yield
    task.cancel()


app = FastAPI(title="multiapp", lifespan=lifespan)
for r in (events, reminders, notes, notebook, progress, chat, settings):
    app.include_router(r.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
