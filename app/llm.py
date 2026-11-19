"""LLM parser (Anthropic). Returns None when no API key is configured."""
import json
import os
from datetime import date, datetime
from typing import Optional

from .schemas import Item

SYSTEM = """Tu es un assistant personnel. Transforme le message de l'utilisateur (français, parfois mêlé d'arabe tunisien ou d'anglais) en éléments structurés.
Réponds UNIQUEMENT avec un JSON : {"items":[{"type":"task|reminder|event|email_draft","title":str,"when":ISO8601 local ou null,"to":str|null,"subject":str|null,"body":str|null}]}.
- reminder: l'utilisateur veut être rappelé. event: réunion/rdv/appel avec une heure. task: chose à faire. email_draft: brouillon de mail complet (objet + corps courts, professionnels).
- Résous les dates relatives (demain, lundi, dans 2h) à partir de la date actuelle fournie. N'invente aucun détail absent."""


def parse_llm(text: str, now: datetime, default_day: Optional[date] = None) -> Optional[list[Item]]:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=os.getenv("LLM_MODEL", "claude-haiku-5-5"),
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": (
            f"Date actuelle : {now.isoformat(timespec='minutes')}\n"
            + (f"Jour choisi dans le calendrier : {default_day.isoformat()} (si un élément donne une heure "
               f"sans jour, place-le ce jour-là ; sans heure du tout, laisse when à null)\n" if default_day else "")
            + f"Message : {text}")}],
    )
    raw = msg.content[0].text.strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return [Item(**i) for i in json.loads(raw)["items"]]
