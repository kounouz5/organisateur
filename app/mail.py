"""Read the user's mailbox over IMAP (free, works with Gmail, Outlook, Yahoo, ...).

READ-ONLY on purpose: the inbox is opened with readonly=True and messages are fetched
with BODY.PEEK, so nothing is sent, deleted or marked as read.

The account is saved in data/mail.json next to the database, only on this computer.
MAIL_ADDRESS / MAIL_PASSWORD / MAIL_IMAP_HOST in .env work too.
"""
import email
import html
import imaplib
import json
import os
import re
import socket
import threading
import time
from email.policy import default as POLICY
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Optional

from . import db

HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com", "outlook.fr": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com", "hotmail.fr": "outlook.office365.com",
    "live.com": "outlook.office365.com", "live.fr": "outlook.office365.com", "msn.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com", "yahoo.fr": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "gmx.com": "imap.gmx.com", "gmx.fr": "imap.gmx.com",
    "orange.fr": "imap.orange.fr", "laposte.net": "imap.laposte.net",
}
WEBMAIL = {
    "imap.gmail.com": "https://mail.google.com/",
    "outlook.office365.com": "https://outlook.live.com/mail/",
    "imap.mail.yahoo.com": "https://mail.yahoo.com/",
    "imap.mail.me.com": "https://www.icloud.com/mail",
}

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "key": None, "data": None}


class MailError(Exception):
    """Error with a message that can be shown to the user as-is (French)."""


def _cfg_path() -> Path:
    return db._db_path().parent / "mail.json"


def guess_host(address: str) -> str:
    domain = address.rsplit("@", 1)[-1].lower().strip()
    return HOSTS.get(domain, f"imap.{domain}")


def get_config() -> Optional[dict]:
    p = _cfg_path()
    if p.exists():
        cfg = json.loads(p.read_text("utf-8"))
    else:
        cfg = {"address": os.getenv("MAIL_ADDRESS"), "password": os.getenv("MAIL_PASSWORD"),
               "host": os.getenv("MAIL_IMAP_HOST")}
    if not cfg.get("address") or not cfg.get("password"):
        return None
    cfg["host"] = cfg.get("host") or guess_host(cfg["address"])
    cfg["port"] = int(cfg.get("port") or os.getenv("MAIL_IMAP_PORT") or 993)
    return cfg


def _connect(cfg: dict) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=15)
    except (OSError, socket.timeout) as e:
        raise MailError(f"Impossible de joindre le serveur {cfg['host']}. "
                        "Vérifie ta connexion Internet, ou indique le bon serveur IMAP.") from e
    try:
        conn.login(cfg["address"], cfg["password"])
    except imaplib.IMAP4.error as e:
        try:
            conn.logout()
        except Exception:
            pass
        raise MailError("Connexion refusée : adresse ou mot de passe incorrect. Pour Gmail, Outlook et Yahoo, "
                        "il faut un « mot de passe d'application », pas ton mot de passe habituel.") from e
    return conn


def _text(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text).strip()
    except Exception:  # truncated or badly encoded message: no preview, no crash
        return ""


def summarize(uid: str, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=POLICY)
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        date = parsedate_to_datetime(str(msg["Date"])).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    except Exception:
        date = None
    return {
        "uid": uid,
        "id": str(msg.get("Message-ID") or f"uid-{uid}").strip(),
        "from_name": name or addr or "Inconnu",
        "from_addr": addr,
        "subject": str(msg.get("Subject", "")).strip() or "(sans objet)",
        "date": date,
        "snippet": _text(msg)[:200],
    }


def fetch_unread(cfg: dict, limit: int = 15) -> tuple[int, list[dict]]:
    conn = _connect(cfg)
    try:
        conn.select("INBOX", readonly=True)                    # read-only: never changes the mailbox
        _, data = conn.uid("search", None, "UNSEEN")
        uids = data[0].split() if data and data[0] else []
        emails = []
        for uid in reversed(uids[-limit:]):                    # newest first
            _, parts = conn.uid("fetch", uid, "(BODY.PEEK[]<0.40000>)")  # PEEK: stays unread
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if raw:
                emails.append(summarize(uid.decode(), raw))
        return len(uids), emails
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def unread(max_age: float = 60) -> dict:
    """Unread emails of the connected account (cached for max_age seconds)."""
    cfg = get_config()
    if not cfg:
        return {"configured": False, "ok": False, "unread": 0, "emails": []}
    base = {"configured": True, "address": cfg["address"], "webmail": WEBMAIL.get(cfg["host"])}
    with _lock:
        if _cache["data"] and _cache["key"] == cfg["address"] and time.time() - _cache["t"] < max_age:
            return _cache["data"]
        try:
            count, emails = fetch_unread(cfg)
            data = {**base, "ok": True, "unread": count, "emails": emails}
        except MailError as e:
            data = {**base, "ok": False, "error": str(e), "unread": 0, "emails": []}
        except (imaplib.IMAP4.error, OSError) as e:
            data = {**base, "ok": False, "error": f"Erreur de lecture de la boîte mail : {e}", "unread": 0, "emails": []}
        _cache.update(t=time.time(), key=cfg["address"], data=data)
        return data


def cached() -> Optional[dict]:
    """Last result without touching the network (None if never fetched)."""
    return _cache["data"] if get_config() else None


def connect(address: str, password: str, host: Optional[str] = None) -> dict:
    """Check the login works, then save the account on this computer."""
    password = password.strip()
    if re.fullmatch(r"[A-Za-z]{4}( [A-Za-z]{4}){3}", password):   # Gmail shows "abcd efgh ijkl mnop"
        password = password.replace(" ", "")
    cfg = {"address": address.strip(), "password": password, "host": (host or "").strip() or guess_host(address)}
    cfg["port"] = 993
    _connect(cfg).logout()
    p = _cfg_path()
    p.write_text(json.dumps({k: cfg[k] for k in ("address", "password", "host")}), "utf-8")
    _cache.update(t=0, key=None, data=None)
    return unread(max_age=0)


def disconnect() -> None:
    _cfg_path().unlink(missing_ok=True)
    _cache.update(t=0, key=None, data=None)
