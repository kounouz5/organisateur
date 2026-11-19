"""Local AI (Ollama) brain, with a scripted fake model: no download, no network."""
import pytest
from fastapi.testclient import TestClient

from app import assistant
from app.main import app

NOW = "2026-11-18T08:00:00"  # mercredi


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANIZER_DB", str(tmp_path / "test.db"))
    return TestClient(app)


def fake_model(monkeypatch, script):
    """Replace Ollama by a list of answers played in order; records what the model was sent."""
    sent = []

    def chat(messages, tools, timeout=None):
        sent.append(messages)
        return script.pop(0)

    monkeypatch.setattr(assistant, "local_available", lambda: True)
    monkeypatch.setattr(assistant, "_ollama_chat", chat)
    monkeypatch.setattr(assistant, "warm_up", lambda: None)
    return sent


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def say(text):
    return {"role": "assistant", "content": text}


def ask(client, message, cid=None):
    return client.post("/assistant", json={"message": message, "now": NOW, "conversation_id": cid}).json()


def test_free_sentence_is_understood_and_added(client, monkeypatch):
    sent = fake_model(monkeypatch, [
        call("add_from_text", text="appeler le dentiste vendredi à 9h30"),
        say("C'est noté, je t'ai mis l'appel au dentiste vendredi à 9h30."),
    ])
    r = ask(client, "faut vraiment que je pense à appeler le dentiste vendredi genre 9h30")
    assert r["engine"] == "local" and r["changed"]
    item = client.get("/items").json()[0]
    assert item["when"] == "2026-11-20T09:30" and "dentiste" in item["title"].lower()
    # the model received the context with the ready-made days and the system rules
    first = sent[0]
    assert first[0]["role"] == "system" and "add_from_text" in first[0]["content"]
    assert "vendredi = 2026-11-20" in first[-1]["content"]


def test_complete_and_move_with_natural_date(client, monkeypatch):
    client.post("/add", json={"text": "finir le rapport", "now": NOW})
    fake_model(monkeypatch, [
        call("update_item", id=1, when="demain à 14h"),
        say("C'est déplacé à demain 14h."),
    ])
    r = ask(client, "décale le rapport à demain aprèm 14h")
    assert r["changed"] and client.get("/items").json()[0]["when"] == "2026-11-19T14:00"

    fake_model(monkeypatch, [call("update_item", id=1, done="true"), say("Bravo !")])
    assert ask(client, "c'est bon j'ai bouclé le rapport")["changed"]
    assert client.get("/items").json()[0]["done"] is True


def test_reading_mail_is_not_a_change(client, monkeypatch):
    fake_model(monkeypatch, [call("list_unread_emails"), say("Ta boîte mail n'est pas encore connectée.")])
    r = ask(client, "quoi de neuf dans mes mails ?")
    assert r["engine"] == "local" and not r["changed"]


def test_conversation_is_remembered(client, monkeypatch):
    sent = fake_model(monkeypatch, [say("Salut ! Ça va et toi ?"), say("Super, bonne journée !")])
    cid = ask(client, "salut ça va")["conversation_id"]
    ask(client, "oui très bien", cid)
    second = sent[1]
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "user"]
    assert second[1]["content"] == "salut ça va"           # old turns: no repeated context
    assert second[-1]["content"].startswith("[Contexte]")    # latest turn: fresh context


def test_falls_back_to_rules_if_ollama_fails(client, monkeypatch):
    monkeypatch.setattr(assistant, "local_available", lambda: True)

    def broken(messages, tools, timeout=None):
        raise ConnectionError("Ollama arrêté")

    monkeypatch.setattr(assistant, "_ollama_chat", broken)
    r = ask(client, "qu'est-ce que j'ai aujourd'hui ?")
    assert r["engine"] == "rules" and "Rien de prévu" in r["reply"]


def test_slow_local_ai_falls_back_and_is_paused(client, monkeypatch):
    """A model that takes too long must never leave the person waiting."""
    import time
    calls = []

    def slow(messages, tools, timeout=None):
        calls.append(timeout)
        raise TimeoutError("trop lent")

    monkeypatch.setenv("OLLAMA_MODEL", "lent:1b")
    monkeypatch.setattr(assistant, "_ollama_chat", slow)
    assistant._local_state.update(t=time.time(), ok=True)          # "Ollama is there"
    t = time.time()
    r = ask(client, "qu'est-ce que j'ai aujourd'hui ?")
    assert r["engine"] == "rules" and time.time() - t < 5
    assert calls and calls[0] <= assistant.LOCAL_BUDGET_S          # the call got a time limit
    assert assistant.mode() == "free"                              # paused: next messages are instant


def test_local_ai_is_opt_in(client):
    """Without OLLAMA_MODEL, the local AI is never used, even if Ollama runs."""
    assistant._local_state.update(t=0.0, ok=True)
    assert assistant.local_available() is False
    assert client.get("/assistant/briefing").json()["mode"] == "free"


def test_mode_is_free_without_ollama(client):
    assert client.get("/assistant/briefing").json()["mode"] == "free"
