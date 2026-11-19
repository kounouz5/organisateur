from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.rules import parse_rules

NOW = datetime(2026, 11, 14, 12, 0)  # samedi


def test_reminder_tomorrow_with_time():
    items = parse_rules("rappelle-moi d'appeler le fournisseur demain à 10h", NOW)
    assert items[0].type == "reminder"
    assert items[0].when == "2026-11-15T10:00"
    assert "fournisseur" in items[0].title.lower()


def test_event_weekday():
    items = parse_rules("réunion avec Sami lundi à 14h30", NOW)
    assert items[0].type == "event"
    assert items[0].when == "2026-11-16T14:30"


def test_email_draft():
    items = parse_rules("envoie un mail à Sami pour la facture", NOW)
    assert items[0].type == "email_draft"
    assert items[0].to == "Sami"
    assert items[0].subject == "La facture"


def test_relative_hours():
    items = parse_rules("rappelle-moi de sortir le colis dans 2 heures", NOW)
    assert items[0].when == "2026-11-14T14:00"


def test_multiple_items_split():
    items = parse_rules("rappelle-moi d'appeler Ali demain à 9h, puis envoie un mail à Sami pour le devis", NOW)
    assert [i.type for i in items] == ["reminder", "email_draft"]


def test_no_trailing_comma_before_puis():
    items = parse_rules("envoie un mail à Ali pour le devis, puis finir le rapport", NOW)
    assert items[0].subject == "Le devis"
    assert items[1].title == "Finir le rapport"


def test_api_falls_back_to_rules(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = TestClient(app).post("/parse", json={"text": "acheter du pain demain", "now": "2026-11-14T12:00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "rules" and body["items"][0]["type"] == "task"
