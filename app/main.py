from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

import os  # noqa: E402
import re  # noqa: E402
from typing import Optional  # noqa: E402

from . import assistant, db, mail  # noqa: E402
from .llm import parse_llm  # noqa: E402
from .rules import parse_rules  # noqa: E402
from .schemas import (AddResponse, ChatRequest, ChatResponse, Item, ItemUpdate,  # noqa: E402
                      MailConnect, ParseRequest, ParseResponse, SavedItem)

app = FastAPI(title="Organisateur", version="0.2.0")
STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def _parse(req: ParseRequest) -> ParseResponse:
    now = datetime.fromisoformat(req.now) if req.now else datetime.now()
    day = date.fromisoformat(req.day) if req.day else None
    try:
        items = parse_llm(req.text, now, day)
    except Exception:  # API down, bad JSON... -> degrade gracefully
        items = None
    if items is not None:
        return ParseResponse(items=items, engine="llm")
    return ParseResponse(items=parse_rules(req.text, now, day), engine="rules")


@app.post("/parse", response_model=ParseResponse)
def parse(req: ParseRequest) -> ParseResponse:
    """Analyse only (nothing saved)."""
    return _parse(req)


@app.post("/add", response_model=AddResponse)
def add(req: ParseRequest) -> AddResponse:
    """Analyse the sentence and save every item it contains, in one step."""
    parsed = _parse(req)
    if req.day:
        spread_on_day(parsed.items, req.day, req.text)
    return AddResponse(items=db.add_items(parsed.items), engine=parsed.engine)


def spread_on_day(items: list[Item], day: str, text: str) -> None:
    """Items added to a calendar day without an hour go to the next free hour (9h, 10h, ...)
    instead of all piling up at 9h."""
    busy = {i.when[11:13] for i in db.list_items() if i.when and i.when.startswith(day)}
    busy |= {i.when[11:13] for i in items if i.when and i.when.startswith(day)}
    for item in items:
        if item.when is None:
            hour = next((h for h in range(9, 19) if f"{h:02d}" not in busy), 9)
            item.when = f"{day}T{hour:02d}:00"
            busy.add(f"{hour:02d}")


@app.post("/assistant", response_model=ChatResponse)
def assistant_chat(req: ChatRequest):
    """Talk with the assistant (questions, planning, add / move / complete)."""
    now = datetime.fromisoformat(req.now) if req.now else datetime.now()
    day = date.fromisoformat(req.day) if req.day else None
    return assistant.chat(req.message, now, day, req.conversation_id)


@app.get("/assistant/briefing")
def assistant_briefing(now: Optional[str] = None, day: Optional[str] = None):
    """Spoken summary of a day, used when the assistant opens."""
    n = datetime.fromisoformat(now) if now else datetime.now()
    m = assistant.mode()
    if m == "local":
        assistant.warm_up()  # load the local AI now, so the first question is answered faster
    return {"reply": assistant.briefing(n, date.fromisoformat(day) if day else None),
            "mode": m, "model": assistant.local_model() if m == "local" else None}


@app.get("/mail")
def mail_unread(refresh: bool = False):
    """Unread emails of the connected mailbox (read-only)."""
    return mail.unread(max_age=0 if refresh else 60)


@app.post("/mail/connect")
def mail_connect(req: MailConnect):
    try:
        return mail.connect(req.address, req.password, req.host)
    except mail.MailError as e:
        raise HTTPException(400, str(e))


@app.delete("/mail/connect", status_code=204)
def mail_disconnect():
    mail.disconnect()
    return Response(status_code=204)


@app.get("/items", response_model=list[SavedItem])
def list_items():
    return db.list_items()


@app.post("/items", response_model=list[SavedItem])
def create_items(items: list[Item]):
    """Save items as-is (used by 'undo' after a delete)."""
    return db.add_items(items)


@app.patch("/items/{item_id}", response_model=SavedItem)
def update_item(item_id: int, upd: ItemUpdate):
    item = db.update_item(item_id, upd.model_dump(exclude_unset=True))
    if not item:
        raise HTTPException(404, "Élément introuvable")
    return item


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    if not db.delete_item(item_id):
        raise HTTPException(404, "Élément introuvable")
    return Response(status_code=204)


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.get("/items/{item_id}/ics")
def item_ics(item_id: int):
    """Calendar file (.ics) so the item can be added to Google/Outlook/Apple calendar."""
    item = db.get_item(item_id)
    if not item or not item.when:
        raise HTTPException(404, "Élément introuvable ou sans date")
    start = datetime.fromisoformat(item.when)
    end = start + timedelta(hours=1 if item.type == "event" else 0, minutes=0 if item.type == "event" else 15)
    fmt = "%Y%m%dT%H%M%S"
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Organisateur//FR", "BEGIN:VEVENT",
        f"UID:item-{item.id}@organisateur", f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z",
        f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{_ics_escape(item.title)}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT10M", f"DESCRIPTION:{_ics_escape(item.title)}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", "",
    ])
    return Response(ics, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="element-{item.id}.ics"'})
