"""Offline French parser: works without any API key (fallback + tests)."""
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from .schemas import Item

DAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
SPLIT_RE = re.compile(r"\s*(?:[.;!?]|\bet puis\b|\bpuis\b|\bensuite\b|\bet aussi\b)\s*", re.I)
TIME_RE = re.compile(r"\b(?:à|a)\s*(\d{1,2})\s*(?:h|:)\s*(\d{2})?", re.I)
IN_RE = re.compile(r"\bdans\s+(\d+)\s*(minutes?|min|heures?|h|jours?)\b", re.I)


def _resolve_when(text: str, now: datetime, default_day: Optional[date] = None) -> Optional[datetime]:
    t = text.lower()
    base: Optional[datetime] = None

    m = IN_RE.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith("min"):
            return now + timedelta(minutes=n)
        if unit.startswith("h"):
            return now + timedelta(hours=n)
        base = (now + timedelta(days=n)).replace(hour=9, minute=0, second=0, microsecond=0)

    if base is None:
        if "après-demain" in t or "apres-demain" in t:
            base = now + timedelta(days=2)
        elif "demain" in t:
            base = now + timedelta(days=1)
        elif "aujourd'hui" in t or "ce soir" in t:
            base = now
        else:
            for name, idx in DAYS.items():
                if re.search(rf"\b{name}\b", t):
                    delta = (idx - now.weekday()) % 7 or 7
                    base = now + timedelta(days=delta)
                    break

    on_default_day = False
    if base is None and default_day is not None and TIME_RE.search(t):
        # An hour but no day in the sentence: use the day picked in the calendar.
        # (No hour at all -> None, and the app picks a free hour on that day.)
        base, on_default_day = datetime.combine(default_day, time(9, 0)), True

    tm = TIME_RE.search(t)
    if tm:
        base = base or now
        base = base.replace(hour=int(tm.group(1)), minute=int(tm.group(2) or 0), second=0, microsecond=0)
        if base < now and not on_default_day and not any(k in t for k in ("demain", "après", "apres")):
            base += timedelta(days=1)
    elif base is not None:
        base = base.replace(hour=9, minute=0, second=0, microsecond=0)
    return base


def _clean_title(s: str) -> str:
    s = re.sub(r"^(?:rappelle[- ]moi|rappel|n'oublie pas|il faut que je|je dois|pense à|penser à)\s*(?:de|d'|à|que)?\s*", "", s, flags=re.I)
    s = TIME_RE.sub("", s)
    s = re.sub(r"\b(demain|après-demain|apres-demain|aujourd'hui|ce soir|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b", "", s, flags=re.I)
    s = IN_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,-")
    return s[:1].upper() + s[1:] if s else "Sans titre"


def parse_rules(text: str, now: Optional[datetime] = None, default_day: Optional[date] = None) -> list[Item]:
    now = now or datetime.now()
    items: list[Item] = []
    for chunk in filter(None, (c.strip(" ,") for c in SPLIT_RE.split(text))):
        low = chunk.lower()
        when = _resolve_when(chunk, now, default_day)
        iso = when.isoformat(timespec="minutes") if when else None

        mail = re.search(r"(?:mail|e-mail|email|message)\s+(?:à|a)\s+([\wÀ-ÿ'\- ]+?)(?:\s+(?:pour|au sujet|concernant|demain|aujourd)|$)", low)
        if re.search(r"\b(mail|e-mail|email)\b", low) and re.search(r"\b(envoie|écris|ecris|rédige|redige|prépare|prepare)\b", low):
            to = mail.group(1).strip().title() if mail else None
            about = re.search(r"(?:pour|au sujet de|concernant)\s+(.+)$", chunk, re.I)
            subject = about.group(1).strip().capitalize() if about else "À compléter"
            items.append(Item(type="email_draft", title=f"Mail à {to}" if to else "Brouillon de mail",
                              when=iso, to=to, subject=subject,
                              body=f"Bonjour{' ' + to if to else ''},\n\n[À compléter : {subject}]\n\nCordialement,"))
        elif re.search(r"\b(réunion|reunion|rendez-vous|rdv|meeting|appel|call|visio)\b", low):
            items.append(Item(type="event", title=_clean_title(chunk), when=iso))
        elif re.search(r"\b(rappelle|rappel|n'oublie pas)\b", low):
            items.append(Item(type="reminder", title=_clean_title(chunk), when=iso))
        else:
            items.append(Item(type="task", title=_clean_title(chunk), when=iso))
    return items
