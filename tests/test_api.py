from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import rag
from app.main import app
from app.tools import error_budget_minutes

HEADERS = {"X-API-Key": "test-key"}


def test_healthz():
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["chunks"] > 0


def test_ask_returns_answer_and_sources():
    with TestClient(app) as c:
        r = c.post("/ask", json={"question": "What is the checkout-api availability SLO?"}, headers=HEADERS)
        assert r.status_code == 200
        assert "slo-policy.md" in r.json()["sources"]


def test_ask_requires_api_key():
    with TestClient(app) as c:
        assert c.post("/ask", json={"question": "hello there"}).status_code == 401


def test_question_validation():
    with TestClient(app) as c:
        assert c.post("/ask", json={"question": "x"}, headers=HEADERS).status_code == 422


def test_chaos_injection(monkeypatch):
    monkeypatch.setenv("FAILURE_RATE", "1")
    with TestClient(app) as c:
        assert c.post("/ask", json={"question": "anything?"}, headers=HEADERS).status_code == 500


def test_error_budget_tool():
    assert error_budget_minutes(99.9, 30) == 43.2


def test_generate_limits_thinking(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen["config"] = config
            return SimpleNamespace(text="ok", usage_metadata=None)

    monkeypatch.setattr(rag, "client", lambda: SimpleNamespace(models=FakeModels()))
    assert rag._generate("hi") == ("ok", rag.CHAT_MODEL, (0, 0))
    assert seen["config"].thinking_config.thinking_level == "LOW"
    assert seen["config"].http_options.timeout == rag.LLM_TIMEOUT_MS


def test_generate_falls_back_after_primary_fails(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(model)
            if model == rag.CHAT_MODEL:
                raise TimeoutError("timed out")
            return SimpleNamespace(text="ok", usage_metadata=None)

    monkeypatch.setattr(rag, "client", lambda: SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(rag.time, "sleep", lambda s: None)
    assert rag._generate("hi")[1] == rag.FALLBACK_MODEL
    assert calls == [rag.CHAT_MODEL] * rag.ATTEMPTS_PER_MODEL + [rag.FALLBACK_MODEL]
