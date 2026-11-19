import pytest

from app import assistant


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    """Tests never talk to a real Ollama or Claude: point Ollama to a closed port."""
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assistant._local_state.update(t=1e12, ok=False)   # "checked recently: not available" -> no network wait
    yield
    assistant._local_state.update(t=0.0, ok=False)
