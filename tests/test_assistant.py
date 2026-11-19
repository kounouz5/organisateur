import pytest
from fastapi.testclient import TestClient

from app.main import app

NOW = "2026-11-17T08:00:00"  # mardi


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANIZER_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return TestClient(app)


def say(client, message, cid=None, **kw):
    r = client.post("/assistant", json={"message": message, "now": NOW, "conversation_id": cid, **kw})
    assert r.status_code == 200
    return r.json()


def test_adds_from_conversation(client):
    r = say(client, "rappelle-moi d'appeler Sami demain à 15h")
    assert r["changed"] and "Appeler Sami" in r["reply"] and "15h" in r["reply"]
    assert client.get("/items").json()[0]["when"] == "2026-11-18T15:00"


def test_tells_what_is_planned(client):
    say(client, "réunion avec Ali aujourd'hui à 14h30")
    r = say(client, "qu'est-ce que j'ai aujourd'hui ?")
    assert "Réunion avec Ali" in r["reply"] and "14h30" in r["reply"] and not r["changed"]


def test_plans_day_and_applies_on_yes(client):
    say(client, "réunion avec Ali aujourd'hui à 9h")
    say(client, "finir le rapport")
    r = say(client, "organise ma journée")
    assert "Finir le rapport à 10h" in r["reply"] and "oui" in r["reply"]
    r = say(client, "oui", r["conversation_id"])
    assert r["changed"]
    rapport = next(i for i in client.get("/items").json() if i["title"] == "Finir le rapport")
    assert rapport["when"] == "2026-11-17T10:00"


def test_marks_done(client):
    say(client, "finir le rapport")
    r = say(client, "j'ai fini le rapport")
    assert r["changed"] and client.get("/items").json()[0]["done"] is True


def test_moves_item(client):
    say(client, "finir le rapport")
    r = say(client, "mets le rapport demain à 11h")
    assert r["changed"]
    assert client.get("/items").json()[0]["when"] == "2026-11-18T11:00"


def test_small_talk_is_not_saved_as_a_task(client):
    for msg in ["non", "Bonjour maintenant c'est 16h33", "salut ça va ?", "ok"]:
        assert not say(client, msg)["changed"]
    assert client.get("/items").json() == []


def test_greeting_with_a_request_still_adds(client):
    r = say(client, "bonjour, rappelle-moi d'appeler Ali à 15h")
    assert r["changed"]


def test_briefing(client):
    say(client, "réunion avec Ali aujourd'hui à 14h")
    text = client.get("/assistant/briefing", params={"now": NOW}).json()["reply"]
    assert text.startswith("Bonjour") and "Réunion avec Ali" in text
