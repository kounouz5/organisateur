"""SQLite storage for saved items (stdlib only, no extra dependency)."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from .schemas import Item, SavedItem

FIELDS = ("type", "title", "when", "to", "subject", "body", "done")


def _db_path() -> Path:
    p = Path(os.getenv("ORGANIZER_DB", Path(__file__).resolve().parent.parent / "data" / "organizer.db"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _conn():
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    try:
        c.execute(
            """CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL, title TEXT NOT NULL, "when" TEXT,
                "to" TEXT, subject TEXT, body TEXT,
                done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"""
        )
        yield c
        c.commit()
    finally:
        c.close()


def _row(r: sqlite3.Row) -> SavedItem:
    d = dict(r)
    d["done"] = bool(d["done"])
    return SavedItem(**d)


def list_items() -> list[SavedItem]:
    with _conn() as c:
        rows = c.execute('SELECT * FROM items ORDER BY done, "when" IS NULL, "when", id').fetchall()
    return [_row(r) for r in rows]


def get_item(item_id: int) -> Optional[SavedItem]:
    with _conn() as c:
        r = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _row(r) if r else None


def add_items(items: list[Item], done: bool = False) -> list[SavedItem]:
    now = datetime.now().isoformat(timespec="seconds")
    ids = []
    with _conn() as c:
        for i in items:
            cur = c.execute(
                'INSERT INTO items (type, title, "when", "to", subject, body, done, created_at) VALUES (?,?,?,?,?,?,?,?)',
                (i.type, i.title, i.when, i.to, i.subject, i.body, int(done), now),
            )
            ids.append(cur.lastrowid)
    return [get_item(i) for i in ids]


def update_item(item_id: int, changes: dict) -> Optional[SavedItem]:
    changes = {k: v for k, v in changes.items() if k in FIELDS}
    if changes:
        cols = ", ".join(f'"{k}" = ?' for k in changes)
        vals = [int(v) if k == "done" else v for k, v in changes.items()]
        with _conn() as c:
            c.execute(f"UPDATE items SET {cols} WHERE id = ?", (*vals, item_id))
    return get_item(item_id)


def delete_item(item_id: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM items WHERE id = ?", (item_id,)).rowcount > 0
