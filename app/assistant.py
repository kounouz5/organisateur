"""Conversational assistant: answers questions about the schedule, plans the day,
adds / moves / completes items.

Two brains, like the parser:
- Claude (when ANTHROPIC_API_KEY is set): free conversation + tools.
- Offline rules (always available): the common requests in French.
Replies are plain sentences (no markdown, no emoji) because the page reads them aloud.
"""
import json
import os
import re
import threading
import time as clock
import unicodedata
import uuid
from datetime import date, datetime, time, timedelta
from typing import Optional

from . import db, mail
from .rules import _resolve_when, parse_rules
from .schemas import Item, SavedItem

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
TYPE_WORD = {"task": "tâche", "reminder": "rappel", "event": "rendez-vous", "email_draft": "mail"}

# conversation_id -> state ({"messages": [...]} for Claude, {"pending": [...]} for offline)
_CONVERSATIONS: dict[str, dict] = {}


# ---------------------------------------------------------------- helpers
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def say_time(dt: datetime) -> str:
    return f"{dt.hour}h{dt.minute:02d}" if dt.minute else f"{dt.hour}h"


def say_day(d: date, today: date) -> str:
    n = (d - today).days
    if n == 0:
        return "aujourd'hui"
    if n == 1:
        return "demain"
    if n == -1:
        return "hier"
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def _when(i: SavedItem) -> Optional[datetime]:
    return datetime.fromisoformat(i.when) if i.when else None


def _describe(i: SavedItem, with_time: bool = True) -> str:
    w = _when(i)
    base = i.title if i.type != "email_draft" else f"{i.title}" + (f" ({i.subject})" if i.subject else "")
    return f"à {say_time(w)}, {base}" if (with_time and w) else base


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " et " + parts[-1]


def _todo() -> list[SavedItem]:
    return [i for i in db.list_items() if not i.done]


def _on_day(items: list[SavedItem], d: date) -> list[SavedItem]:
    return sorted((i for i in items if i.when and _when(i).date() == d), key=lambda i: i.when)


def _late(items: list[SavedItem], now: datetime) -> list[SavedItem]:
    return [i for i in items if i.when and _when(i).date() < now.date()]


def _find(text: str, items: list[SavedItem]) -> Optional[SavedItem]:
    """Item whose title shares the most words with the text."""
    words = {w for w in re.findall(r"[a-z0-9]+", _norm(text)) if len(w) > 2}
    best, score = None, 0
    for i in items:
        s = len(words & {w for w in re.findall(r"[a-z0-9]+", _norm(i.title)) if len(w) > 2})
        if s > score:
            best, score = i, s
    return best


# ---------------------------------------------------------------- briefing
def briefing(now: datetime, day: Optional[date] = None) -> str:
    """Short spoken summary of a day (today by default)."""
    todo = _todo()
    d = day or now.date()
    parts = []
    if d == now.date():
        h = now.hour
        parts.append("Bonjour !" if h < 18 else "Bonsoir !")
        late = _late(todo, now)
        if late:
            parts.append(f"Tu as {len(late)} chose{'s' if len(late) > 1 else ''} en retard : "
                         f"{_join([i.title for i in late[:3]])}.")
    items = _on_day(todo, d)
    when = say_day(d, now.date())
    if items:
        parts.append(f"{when.capitalize()}, tu as {len(items)} chose{'s' if len(items) > 1 else ''} : "
                     f"{_join([_describe(i) for i in items])}.")
    else:
        parts.append(f"Rien de prévu {when}.")
    undated = [i for i in todo if not i.when]
    if undated and d == now.date():
        parts.append(f"Et {len(undated)} tâche{'s' if len(undated) > 1 else ''} sans date. "
                     "Dis-moi « organise ma journée » si tu veux que je les place.")
    box = mail.cached()  # only what is already loaded: the briefing never waits for the mail server
    if d == now.date() and box and box.get("ok") and box["unread"]:
        n = box["unread"]
        parts.append(f"Tu as aussi {n} mail{'s' if n > 1 else ''} non lu{'s' if n > 1 else ''}.")
    return " ".join(parts)


def mail_summary() -> str:
    box = mail.unread(max_age=300)  # reuse what the page loaded in the last 5 minutes: instant answer
    if not box["configured"]:
        return ("Ta boîte mail n'est pas encore connectée. Va dans la section Mails "
                "et clique sur « Connecter mon email ».")
    if not box["ok"]:
        return "Je n'arrive pas à lire tes mails. " + box.get("error", "")
    n = box["unread"]
    if not n:
        return "Aucun nouveau mail, ta boîte est à jour."
    parts = [f"Tu as {n} mail{'s' if n > 1 else ''} non lu{'s' if n > 1 else ''}."]
    for m in box["emails"][:3]:
        parts.append(f"De {m['from_name']} : {m['subject']}.")
    if n > 3:
        parts.append(f"Et {n - 3} autre{'s' if n > 4 else ''}.")
    return " ".join(parts)


# ---------------------------------------------------------------- planning
def _free_slots(d: date, busy: list[datetime], now: datetime, count: int) -> list[datetime]:
    """Free one-hour slots between 9h and 18h, skipping busy hours and the past."""
    taken = {b.hour for b in busy}
    out = []
    for h in [9, 10, 11, 14, 15, 16, 17, 12, 13]:  # morning first, lunch last
        slot = datetime.combine(d, time(h, 0))
        if h in taken or slot <= now:
            continue
        out.append(slot)
        if len(out) == count:
            break
    return sorted(out)


def plan_day(now: datetime, d: Optional[date] = None) -> tuple[str, list[dict]]:
    """Returns (spoken plan, proposed changes [{id, when}]) for undated tasks."""
    d = d or now.date()
    todo = _todo()
    lines = []
    late = _late(todo, now) if d == now.date() else []
    if late:
        lines.append(f"D'abord, rattrape ce qui est en retard : {_join([i.title for i in late])}.")
    fixed = _on_day(todo, d)
    if fixed:
        lines.append(f"Ce qui est déjà prévu {say_day(d, now.date())} : {_join([_describe(i) for i in fixed])}.")
    undated = [i for i in todo if not i.when][:6]
    proposal = []
    if undated:
        slots = _free_slots(d, [_when(i) for i in fixed], now, len(undated))
        for item, slot in zip(undated, slots):
            proposal.append({"id": item.id, "when": slot.isoformat(timespec="minutes"), "title": item.title})
        if proposal:
            lines.append("Pour le reste, je te propose : " +
                         _join([f"{p['title']} à {say_time(datetime.fromisoformat(p['when']))}" for p in proposal]) +
                         ". Tu veux que je les mette dans ton calendrier ? Réponds oui ou non.")
        else:
            lines.append("Ta journée est pleine, je te conseille de garder les tâches sans date pour demain.")
    if not lines:
        return f"Rien à organiser {say_day(d, now.date())}, tu es libre !", []
    if not proposal:
        lines.append("Commence par ce qui est le plus tôt, et coche chaque chose quand c'est fait.")
    return " ".join(lines), proposal


# ---------------------------------------------------------------- offline brain
HELP = ("Je peux te dire ce que tu as à faire, par exemple « qu'est-ce que j'ai demain ? », "
        "ou te lire tes nouveaux mails : « est-ce que j'ai des mails ? ». "
        "Je peux organiser ta journée : dis « organise ma journée ». "
        "Je peux ajouter quelque chose : « rappelle-moi d'appeler Sami à 15h ». "
        "Je peux déplacer : « mets le rapport à 11h », ou cocher : « j'ai fini le rapport ». "
        "Et je te préviens à voix haute quand un rappel arrive, si la page est ouverte.")


def _offline(message: str, now: datetime, day: Optional[date], state: dict) -> tuple[str, bool]:
    t = _norm(message).strip(" .!")
    todo = _todo()
    target_day = day or now.date()

    # Answer to a proposal ("oui" / "non")
    pending = state.pop("pending", None)
    if pending and re.match(r"^(oui|ok|d'?accord|vas[- ]y|go|parfait|ouais|yes)\b", t):
        for p in pending:
            db.update_item(p["id"], {"when": p["when"]})
        return f"C'est fait, j'ai placé {len(pending)} tâche{'s' if len(pending) > 1 else ''} dans ton calendrier.", True
    if pending and re.match(r"^(non|pas maintenant|laisse|annule)\b", t):
        return "D'accord, je ne change rien.", False

    if re.search(r"^(aide|help)$|que (peux|sais)[- ]tu|tu (peux|sais) faire|comment (ca|tu) marche", t):
        return HELP, False

    # Short replies with nothing pending: never turn them into tasks
    if re.fullmatch(r"(oui|non|ok|okay|d'?accord|ouais|nan|bof|ah|euh|hmm|bien|cool|vas[- ]y)", t):
        return "D'accord. Dis-moi ce que tu veux faire, ou demande « que peux-tu faire ? ».", False

    # Greetings ("bonjour", "salut, ça va ?", "bonjour il est 16h") -> summary of the day,
    # unless the sentence clearly asks to note something.
    action = re.search(r"\b(rappelle|ajoute|note|il faut|je dois|n'?oublie|reunion|rdv|rendez|mail|appel|envoie)", t)
    if re.match(r"(bonjour|salut|coucou|bonsoir|hello|cc|hey)\b", t) and not action:
        return briefing(now, day), False

    # Questions about the mailbox ("j'ai des mails ?", "quoi de neuf dans mes emails")
    # — but not "envoie un mail à Ali", which is a draft to add.
    if (re.search(r"\b(mails?|e-?mails?|courriers?|boite)\b", t)
            and not re.search(r"\b(envoie|ecris|redige|prepare|rappelle|fini|termine|fait|mets|deplace|repond)", t)):
        return mail_summary(), False

    if re.search(r"\b(merci|super|genial|parfait)\b", t) and len(t) < 25:
        return "Avec plaisir ! Je suis là si tu as besoin.", False

    # Mark as done
    if re.search(r"\b(j'?ai (fini|termine|fait)|c'?est fait|marque .* (fait|termine)|coche)\b", t):
        item = _find(message, todo)
        if not item:
            return "Je n'ai pas trouvé cette tâche. Tu peux me redire son nom ?", False
        db.update_item(item.id, {"done": True})
        left = len(todo) - 1
        return f"Bravo, « {item.title} » est terminé ! Il te reste {left} chose{'s' if left > 1 else ''} à faire.", True

    # Move / reschedule
    m = re.search(r"\b(mets|met|place|deplace|decale|reporte|bouge)\b(.*)", t)
    if m:
        rest = m.group(2)
        item = _find(rest, todo + [i for i in db.list_items() if i.done])
        when = _resolve_when(rest, now, target_day if not re.search(
            r"demain|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|aujourd", rest) else None)
        if not item:
            return "Je n'ai pas trouvé cette tâche. Tu peux me redire son nom ?", False
        if not when:
            return f"À quel moment veux-tu mettre « {item.title} » ? Par exemple : demain à 10h.", False
        db.update_item(item.id, {"when": when.isoformat(timespec="minutes")})
        return f"C'est noté, « {item.title} » est maintenant {say_day(when.date(), now.date())} à {say_time(when)}.", True

    # Plan the day
    if re.search(r"\b(organise|planifie|par quoi (je )?commence|priorit|mon planning|ma journee)", t):
        d = now.date() + timedelta(days=1) if "demain" in t else target_day
        text, proposal = plan_day(now, d)
        if proposal:
            state["pending"] = proposal
        return text, False

    # Questions about the schedule
    if "?" in message or re.match(r"^(qu'?est[- ]ce|quoi|j'?ai quoi|que (dois|faut)|montre|liste|dis[- ]moi|c'?est quoi|qu'?ai)", t):
        if "retard" in t:
            late = _late(todo, now)
            return (f"En retard : {_join([i.title for i in late])}." if late else "Rien en retard, bravo !"), False
        if "semaine" in t:
            week = sorted((i for i in todo if i.when and 0 <= (_when(i).date() - now.date()).days <= 7),
                          key=lambda i: i.when)
            if not week:
                return "Rien de prévu cette semaine.", False
            return "Cette semaine : " + _join([f"{say_day(_when(i).date(), now.date())} {_describe(i)}" for i in week]) + ".", False
        if "demain" in t:
            return briefing(now, now.date() + timedelta(days=1)), False
        return briefing(now, day), False

    # Otherwise: something to add
    items = parse_rules(message, now, day)
    if day:
        from .main import spread_on_day
        spread_on_day(items, day.isoformat(), message)
    saved = db.add_items(items)
    parts = []
    for i in saved:
        w = _when(i)
        parts.append(f"{TYPE_WORD[i.type]} « {i.title} »" + (f" {say_day(w.date(), now.date())} à {say_time(w)}" if w else ""))
    return "C'est noté : " + _join(parts) + ".", True


# ---------------------------------------------------------------- Claude brain
SYSTEM = """Tu es l'assistant personnel d'une application d'organisation du travail. Tu parles français, \
avec des phrases courtes et chaleureuses, tutoiement.
Tes réponses sont LUES À VOIX HAUTE : pas de markdown, pas de listes à puces, pas d'emoji, 1 à 4 phrases.
Tu aides la personne à savoir quoi faire, à organiser sa journée (propose un ordre et des heures réalistes, \
le plus urgent et ce qui est en retard d'abord), et à ajouter, déplacer ou terminer des éléments.
Utilise les outils pour toute modification ; ne prétends jamais avoir modifié quelque chose sans outil. \
Avant de supprimer, demande confirmation. Avant de placer plusieurs tâches, propose le plan et attends un oui.
Chaque message utilisateur commence par un bloc [Contexte] avec l'heure actuelle et la liste à jour des éléments \
(id, type, titre, date, fait) ; c'est la seule source de vérité."""

TOOLS = [
    {
        "name": "add_from_text",
        "description": "Ajoute un ou plusieurs éléments à partir d'une phrase en français, par exemple "
                       "« appeler Sami demain à 15h » ou « réunion lundi à 10h, puis finir le rapport ». "
                       "L'application comprend elle-même les dates et le type. Outil préféré pour ajouter.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "add_item",
        "description": "Ajoute un élément (tâche, rappel, événement ou brouillon de mail).",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["task", "reminder", "event", "email_draft"]},
                "title": {"type": "string"},
                "when": {"type": "string", "description": "Date et heure locales YYYY-MM-DDTHH:MM, ou absent"},
                "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            },
            "required": ["type", "title"],
        },
    },
    {
        "name": "update_item",
        "description": "Modifie un élément existant : titre, date (when, null pour enlever), type ou done (terminé).",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "title": {"type": "string"},
                "when": {"type": ["string", "null"],
                         "description": "YYYY-MM-DDTHH:MM, ou une expression comme « demain à 10h »"},
                "type": {"type": "string", "enum": ["task", "reminder", "event", "email_draft"]},
                "done": {"type": "boolean"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_unread_emails",
        "description": "Liste les mails non lus de la boîte de la personne (expéditeur, objet, date, début du message). "
                       "Lecture seule. À utiliser seulement quand la personne parle de ses mails.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_item",
        "description": "Supprime un élément. Seulement après confirmation explicite de la personne.",
        "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    },
]


READ_ONLY_TOOLS = {"list_unread_emails"}


def _context(now: datetime, day: Optional[date]) -> str:
    """Fresh state of the app, put in front of the user's message (the model's only source of truth)."""
    recent = now.date() - timedelta(days=2)
    rows = [{"id": i.id, "type": i.type, "titre": i.title, "date": i.when, "fait": i.done}
            for i in db.list_items() if not i.done or (i.when and _when(i).date() >= recent)][:80]
    # Small models are bad at date arithmetic: give them the next days ready-made.
    marks = []
    for k in range(8):
        d = now.date() + timedelta(days=k)
        tag = " (aujourd'hui)" if k == 0 else " (demain)" if k == 1 else ""
        marks.append(f"{WEEKDAYS[d.weekday()]} = {d.isoformat()}{tag}")
    jour = f"\nJour sélectionné dans le calendrier : {day.isoformat()}" if day else ""
    return (f"[Contexte]\nMaintenant : {WEEKDAYS[now.weekday()]} {now.isoformat(timespec='minutes')}{jour}\n"
            f"Jours à venir : {', '.join(marks)}\n"
            f"Éléments : {json.dumps(rows, ensure_ascii=False)}\n[Fin du contexte]\n\n")


def _run_tool(name: str, args: dict, now: Optional[datetime] = None, day: Optional[date] = None) -> tuple[str, bool]:
    now = now or datetime.now()
    try:
        if name == "add_from_text":
            text = str(args.get("text", "")).strip()
            if not text:
                return "Erreur : texte vide.", False
            items = parse_rules(text, now, day)
            if day:
                from .main import spread_on_day
                spread_on_day(items, day.isoformat(), text)
            saved = db.add_items(items)
            return "Ajouté : " + json.dumps([{"id": i.id, "type": i.type, "titre": i.title, "date": i.when}
                                             for i in saved], ensure_ascii=False), True
        if name == "add_item":
            saved = db.add_items([Item(**{k: v for k, v in args.items() if k in Item.model_fields})])[0]
            return f"Ajouté avec l'id {saved.id}.", True
        if name == "update_item":
            item_id = int(args.pop("id"))
            if args.get("when"):
                try:
                    datetime.fromisoformat(args["when"])
                except ValueError:  # "demain à 10h" -> resolved by the app's own date parser
                    when = _resolve_when(str(args["when"]), now)
                    if not when:
                        return f"Erreur : date « {args['when']} » incomprise, utilise AAAA-MM-JJTHH:MM.", False
                    args["when"] = when.isoformat(timespec="minutes")
            if isinstance(args.get("done"), str):
                args["done"] = args["done"].lower() in ("true", "1", "oui", "yes")
            updated = db.update_item(item_id, args)
            return ("Modifié : " + json.dumps({"id": updated.id, "titre": updated.title, "date": updated.when,
                                               "fait": updated.done}, ensure_ascii=False)
                    if updated else f"Aucun élément avec l'id {item_id}."), bool(updated)
        if name == "list_unread_emails":
            box = mail.unread(max_age=300)
            if not box["configured"]:
                return "Boîte mail non connectée (section Mails de l'application).", True
            if not box["ok"]:
                return box.get("error", "Erreur de lecture."), False
            rows = [{k: m[k] for k in ("from_name", "subject", "date", "snippet")} for m in box["emails"]]
            return json.dumps({"non_lus": box["unread"], "mails": rows}, ensure_ascii=False), True
        if name == "delete_item":
            ok = db.delete_item(int(args["id"]))
            return ("Supprimé." if ok else "Élément introuvable."), ok
    except (ValueError, TypeError, KeyError) as e:
        return f"Erreur : {e}", False
    return f"Outil inconnu : {name}", False


def _claude(message: str, now: datetime, day: Optional[date], state: dict) -> tuple[str, bool]:
    import anthropic

    client = anthropic.Anthropic()
    msgs = state.setdefault("messages", [])
    msgs.append({"role": "user", "content": _context(now, day) + message})
    changed = False
    for _ in range(8):  # tool loop, bounded
        resp = client.beta.messages.create(
            model=os.getenv("ASSISTANT_MODEL", "claude-opus-5-5"),
            max_tokens=4000,
            system=SYSTEM,
            tools=TOOLS,
            messages=msgs,
            output_config={"effort": "low"},          # short chat replies
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                       # if the model declines, retry on a fallback model
        )
        msgs.append({"role": "assistant", "content": resp.content})  # keep blocks unchanged
        if resp.stop_reason == "refusal":
            return "Désolé, je ne peux pas t'aider pour ça.", changed
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            return text or "D'accord.", changed
        results = []
        for b in uses:
            out, ok = _run_tool(b.name, dict(b.input), now, day)
            changed |= ok and b.name not in READ_ONLY_TOOLS
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": not ok})
        msgs.append({"role": "user", "content": results})
    return "J'ai fait plusieurs changements, regarde ta liste.", changed


# ---------------------------------------------------------------- local brain (Ollama, free)
SYSTEM_LOCAL = """Tu es l'assistant vocal d'une application d'organisation du travail.
Tu réponds TOUJOURS en français, en tutoyant, en 1 à 3 phrases courtes. Pas de markdown, pas de liste, \
pas d'emoji : tes réponses sont lues à voix haute.
Tu aides la personne à savoir quoi faire, à organiser sa journée, à ajouter, déplacer ou terminer des tâches, \
et à connaître ses nouveaux mails.
Règles :
- Pour AJOUTER, appelle add_from_text avec la phrase de la personne, sans la reformuler (ex : "appeler Sami demain à 15h").
- Pour terminer, déplacer ou renommer, appelle update_item avec l'id trouvé dans le contexte. \
Pour une date, donne "AAAA-MM-JJTHH:MM" ou une expression comme "demain à 10h". Pour terminer : done = true.
- Pour les mails, appelle list_unread_emails, puis résume (expéditeur et objet).
- Pour supprimer, demande d'abord confirmation, puis appelle delete_item.
- Pour organiser une journée : propose un ordre avec des heures réalistes (ce qui est en retard et urgent d'abord), \
puis attends que la personne dise oui avant de modifier quoi que ce soit.
- N'invente jamais de tâche, de date ou de mail : utilise uniquement le [Contexte] et les résultats des outils.
- Si la personne parle d'autre chose, réponds simplement et gentiment.
Le [Contexte] au début du message donne l'heure, les jours à venir et la liste à jour des éléments."""


def _ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def local_model() -> str:
    return os.getenv("OLLAMA_MODEL", "").strip()


LOCAL_BUDGET_S = 20.0   # never let the person wait longer than this for the local AI
_local_state = {"t": 0.0, "ok": False}


def local_available() -> bool:
    """Local AI is OPT-IN: used only when OLLAMA_MODEL is set (in .env) and Ollama has that model.
    On a laptop CPU some models take minutes to answer, so the fast rules stay the default."""
    if not local_model():
        return False
    if clock.time() - _local_state["t"] < 20:
        return _local_state["ok"]
    try:
        import httpx
        names = [m.get("name", "") for m in httpx.get(_ollama_url() + "/api/tags", timeout=1.5).json().get("models", [])]
        ok = local_model() in names or f"{local_model()}:latest" in names
    except Exception:
        ok = False
    _local_state.update(t=clock.time(), ok=ok)
    return ok


def warm_up() -> None:
    """Load the model in the background so the first answer is faster."""
    def run():
        try:
            import httpx
            httpx.post(_ollama_url() + "/api/generate", json={"model": local_model(), "keep_alive": "30m"}, timeout=180)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def _ollama_chat(messages: list, tools: list, timeout: float = LOCAL_BUDGET_S) -> dict:
    import httpx
    payload = {"model": local_model(), "messages": messages, "tools": tools, "stream": False,
               "keep_alive": "30m", "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 300}}
    if "qwen3" in local_model():
        payload["think"] = False  # faster, and no reasoning text read aloud
    r = httpx.post(_ollama_url() + "/api/chat", json=payload, timeout=max(1.0, timeout))
    r.raise_for_status()
    return r.json()["message"]


def _recent(msgs: list, users: int = 6) -> list:
    """The last few exchanges only, so a small model stays fast and focused."""
    starts = [k for k, m in enumerate(msgs) if m["role"] == "user"]
    return msgs[starts[-users]:] if len(starts) > users else list(msgs)


def _local(message: str, now: datetime, day: Optional[date], state: dict) -> tuple[str, bool]:
    msgs = state.setdefault("local_messages", [])
    msgs.append({"role": "user", "content": message})
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"]}} for t in TOOLS]
    changed = False
    deadline = clock.time() + LOCAL_BUDGET_S
    for _ in range(6):  # tool loop, bounded
        left = deadline - clock.time()
        if left <= 1:
            raise TimeoutError("IA locale trop lente")   # -> chat() answers with the fast rules
        convo = _recent(msgs)
        last_user = max(k for k, m in enumerate(convo) if m["role"] == "user")
        convo[last_user] = {"role": "user", "content": _context(now, day) + convo[last_user]["content"]}
        out = _ollama_chat([{"role": "system", "content": SYSTEM_LOCAL}] + convo, tools, timeout=left)
        calls = out.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": out.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            text = re.sub(r"(?s)<think>.*?</think>", "", out.get("content") or "").strip()
            return text or "D'accord.", changed
        for c in calls:
            fn = c.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            name = fn.get("name", "")
            result, ok = _run_tool(name, dict(args), now, day)
            changed |= ok and name not in READ_ONLY_TOOLS
            msgs.append({"role": "tool", "content": result, "tool_name": name})
    return "J'ai fait plusieurs changements, regarde ta liste.", changed


def mode() -> str:
    """Which brain answers: 'claude' (paid), 'local' (free AI on this PC) or 'free' (rules)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return "local" if local_available() else "free"


# ---------------------------------------------------------------- entry point
def chat(message: str, now: datetime, day: Optional[date], conversation_id: Optional[str]) -> dict:
    cid = conversation_id if conversation_id in _CONVERSATIONS else uuid.uuid4().hex
    state = _CONVERSATIONS.setdefault(cid, {})
    engine, m = "rules", mode()
    try:
        if m == "claude":
            reply, changed = _claude(message, now, day, state)
            engine = "llm"
        elif m == "local":
            reply, changed = _local(message, now, day, state)
            engine = "local"
        else:
            reply, changed = _offline(message, now, day, state)
    except Exception:  # API down, Ollama stopped... -> the rules always answer
        state.pop("messages", None)
        state.pop("local_messages", None)
        if m == "local":  # too slow or stopped: don't try the local AI again for 10 minutes
            _local_state.update(t=clock.time() + 600, ok=False)
        reply, changed = _offline(message, now, day, state)
        engine = "rules"
    return {"conversation_id": cid, "reply": reply, "changed": changed, "engine": engine}
