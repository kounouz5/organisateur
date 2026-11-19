





















import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANIZER_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return TestClient(app)


def test_add_saves_every_item(client):
    r = client.post("/add", json={"text": "réunion avec Sami lundi à 14h30, puis acheter du papier",
                                  "now": "2026-11-14T12:00:00"})
    assert r.status_code == 200
    added = r.json()["items"]
    assert [i["type"] for i in added] == ["event", "task"]
    assert all(i["id"] and i["done"] is False for i in added)

    listed = client.get("/items").json()
    assert {i["id"] for i in listed} == {i["id"] for i in added}


def test_update_and_complete(client):
    item = client.post("/add", json={"text": "finir le rapport"}).json()["items"][0]
    r = client.patch(f"/items/{item['id']}", json={"title": "Finir le rapport final", "done": True})
    assert r.status_code == 200
    assert r.json()["title"] == "Finir le rapport final" and r.json()["done"] is True
    # Fields not sent stay unchanged
    assert r.json()["type"] == "task"


def test_delete(client):
    item = client.post("/add", json={"text": "acheter du pain"}).json()["items"][0]
    assert client.delete(f"/items/{item['id']}").status_code == 204
    assert client.get("/items").json() == []
    assert client.delete(f"/items/{item['id']}").status_code == 404


def test_ics_export(client):
    item = client.post("/add", json={"text": "réunion avec Sami lundi à 14h30",
                                     "now": "2026-11-14T12:00:00"}).json()["items"][0]
    r = client.get(f"/items/{item['id']}/ics")
    assert r.status_code == 200
    assert "DTSTART:20261116T143000" in r.text and "BEGIN:VEVENT" in r.text


def test_add_on_calendar_day(client):
    r = client.post("/add", json={"text": "acheter du papier, puis réunion avec Ali lundi à 15h",
                                  "now": "2026-11-14T12:00:00", "day": "2026-11-20"})
    items = r.json()["items"]
    assert items[0]["when"] == "2026-11-20T09:00"   # no date said -> the chosen day
    assert items[1]["when"] == "2026-11-16T15:00"   # a date said -> kept


def test_undated_items_spread_on_free_hours(client):
    client.post("/add", json={"text": "réunion avec Ali à 10h", "now": "2026-11-14T12:00:00", "day": "2026-11-20"})
    r = client.post("/add", json={"text": "acheter du papier, puis finir le rapport, puis appel à 9h",
                                  "now": "2026-11-14T12:00:00", "day": "2026-11-20"})
    whens = [i["when"][11:] for i in r.json()["items"]]
    assert whens == ["11:00", "12:00", "09:00"]   # 9h said explicitly, 10h already taken


def test_time_only_goes_on_calendar_day(client):
    r = client.post("/add", json={"text": "rendez-vous dentiste à 16h",
                                  "now": "2026-11-14T18:00:00", "day": "2026-11-20"})
    assert r.json()["items"][0]["when"] == "2026-11-20T16:00"


def test_ics_needs_a_date(client):
    item = client.post("/add", json={"text": "acheter du pain"}).json()["items"][0]
    assert client.get(f"/items/{item['id']}/ics").status_code == 404
