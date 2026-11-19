"""Email tests with a fake IMAP server (no real mailbox, no network)."""
import imaplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app import mail
from app.main import app


def make_mail(sender, subject, body, html=False):
    m = EmailMessage()
    m["From"] = sender
    m["Subject"] = subject
    m["Date"] = "Wed, 18 Nov 2026 09:30:00 +0100"
    m["Message-ID"] = f"<{abs(hash(subject))}@test>"
    m.set_content(body, subtype="html" if html else "plain")   # html: HTML-only email
    return m.as_bytes()


class FakeIMAP:
    """Plays the IMAP server. Records what the app asked, to check it stays read-only."""
    inbox: dict = {}
    password = "app-password"
    calls: list = []

    def __init__(self, host, port, timeout=None):
        FakeIMAP.calls.append(("connect", host))

    def login(self, user, password):
        if password != FakeIMAP.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, box, readonly=False):
        FakeIMAP.calls.append(("select", box, readonly))
        return "OK", [str(len(self.inbox)).encode()]

    def uid(self, cmd, *args):
        FakeIMAP.calls.append(("uid", cmd, args))
        if cmd == "search":
            return "OK", [b" ".join(self.inbox)]
        if cmd == "fetch":   # like a real server: one answer containing every requested UID
            out = []
            for uid in args[0].split(b","):
                out += [(b"1 (UID " + uid + b" BODY[]<0> {100}", self.inbox[uid]), b")"]
            return "OK", out
        raise AssertionError(f"commande inattendue : {cmd}")

    def logout(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANIZER_DB", str(tmp_path / "test.db"))
    for var in ("ANTHROPIC_API_KEY", "MAIL_ADDRESS", "MAIL_PASSWORD", "MAIL_IMAP_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.calls = []
    FakeIMAP.inbox = {
        b"11": make_mail("Sami Ben Ali <sami@exemple.tn>", "Facture de novembre", "Bonjour, voici la facture."),
        b"12": make_mail("=?utf-8?q?L=C3=A9a?= <lea@exemple.fr>", "Réunion déplacée",
                         "<style>p{}</style><p>La r&eacute;union passe &agrave; 15h.</p>", html=True),
    }
    mail._cache.update(t=0, key=None, data=None)
    return TestClient(app)


def connect(client, password="app-password"):
    return client.post("/mail/connect", json={"address": "moi@gmail.com", "password": password})


def test_not_connected_by_default(client):
    assert client.get("/mail").json()["configured"] is False


def test_connect_and_read_unread(client):
    r = connect(client)
    assert r.status_code == 200
    box = r.json()
    assert box["ok"] and box["unread"] == 2 and box["webmail"] == "https://mail.google.com/"
    newest, older = box["emails"]
    assert newest["from_name"] == "Léa" and newest["subject"] == "Réunion déplacée"
    assert newest["snippet"] == "La réunion passe à 15h."          # HTML cleaned, accents decoded
    assert older["from_name"] == "Sami Ben Ali" and older["snippet"].startswith("Bonjour")
    assert ("connect", "imap.gmail.com") in FakeIMAP.calls          # server guessed from the address
    assert "password" not in box                                    # never sent back to the page


def test_mailbox_stays_read_only(client):
    connect(client)
    assert ("select", "INBOX", True) in FakeIMAP.calls
    fetches = [c for c in FakeIMAP.calls if c[:2] == ("uid", "fetch")]
    assert fetches and all("PEEK" in c[2][1] for c in fetches)       # nothing marked as read
    assert len(fetches) == 1                                          # all messages in one request
    assert not [c for c in FakeIMAP.calls if c[:2] == ("uid", "store")]


def test_wrong_password_explains_app_password(client):
    r = connect(client, password="mauvais")
    assert r.status_code == 400
    assert "mot de passe d'application" in r.json()["detail"]
    assert client.get("/mail").json()["configured"] is False         # nothing saved


def test_gmail_code_pasted_with_spaces(client):
    FakeIMAP.password = "abcdefghijklmnop"
    try:
        r = client.post("/mail/connect", json={"address": "moi@gmail.com", "password": " abcd efgh ijkl mnop "})
        assert r.status_code == 200 and r.json()["ok"]
    finally:
        FakeIMAP.password = "app-password"


def test_disconnect(client):
    connect(client)
    assert client.delete("/mail/connect").status_code == 204
    assert client.get("/mail").json()["configured"] is False


def test_assistant_reads_new_mail(client):
    connect(client)
    r = client.post("/assistant", json={"message": "est-ce que j'ai des nouveaux mails ?"}).json()
    assert "2 mails non lus" in r["reply"] and "Léa" in r["reply"] and "Facture de novembre" in r["reply"]
    assert not r["changed"]


def test_assistant_mail_when_not_connected(client):
    r = client.post("/assistant", json={"message": "j'ai des mails ?"}).json()
    assert "pas encore connectée" in r["reply"]


def test_send_mail_request_is_still_a_draft(client):
    r = client.post("/assistant", json={"message": "envoie un mail à Ali pour le devis"}).json()
    assert r["changed"] and client.get("/items").json()[0]["type"] == "email_draft"
