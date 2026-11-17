"""Retry rule for the three AI providers: a refused request (any 4xx except
408, 409 and 429) fails at once with the provider's own message. Rate limits,
server errors, timeouts, connection errors and unparseable answers are retried."""
from __future__ import annotations

import json
import sys
import types

import pytest
import requests

import backend  # noqa: E402 — conftest stubs SDKs before this import

BOTH = pytest.mark.parametrize("call,good,expected", [
    (backend._call_ai_json, '{"ok": true}', {"ok": True}),
    (backend._call_ai_text, "a summary", "a summary"),
])


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(backend.time, "sleep", waited.append)
    return waited


def _answering(answers, calls):
    def answer(*_args, **_kwargs):
        calls.append(1)
        nxt = answers[len(calls) - 1]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    return answer


class _GenaiError(Exception):
    """Shape of google.genai.errors.APIError: the HTTP status is on .code."""
    def __init__(self, code: int, message: str):
        super().__init__(f"{code} {message}")
        self.code = code


def _gemini(monkeypatch, answers) -> list:
    calls: list = []
    replies = [a if isinstance(a, Exception) else types.SimpleNamespace(text=a, usage_metadata=None)
               for a in answers]
    client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=_answering(replies, calls)))
    monkeypatch.setattr(backend, "_get_gemini", lambda: client)
    monkeypatch.setattr(backend, "AI_PROVIDER", "gemini")
    return calls


def _http(status: int, body: dict) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    resp.url = "http://ollama/api/chat"
    return resp


def _ollama(monkeypatch, answers) -> list:
    calls: list = []
    monkeypatch.setattr(backend.requests, "post", _answering(answers, calls))
    monkeypatch.setattr(backend, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(backend, "OLLAMA_URL", "http://ollama")
    return calls


@BOTH
@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_gemini_refusal_is_not_retried(monkeypatch, sleeps, call, good, expected, status):
    calls = _gemini(monkeypatch, [_GenaiError(status, "models/retired is not found"), good])
    with pytest.raises(ValueError, match=f"Gemini refused the request .HTTP {status}: .*models/retired is not found"):
        call("hi")
    assert len(calls) == 1 and sleeps == []


@BOTH
@pytest.mark.parametrize("first", [
    _GenaiError(408, "slow"), _GenaiError(409, "busy"), _GenaiError(429, "RESOURCE_EXHAUSTED"),
    _GenaiError(500, "INTERNAL"), _GenaiError(503, "UNAVAILABLE"), TimeoutError("timed out"),
])
def test_gemini_passing_failure_is_retried(monkeypatch, sleeps, call, good, expected, first):
    calls = _gemini(monkeypatch, [first, good])
    assert call("hi") == expected
    assert len(calls) == 2 and len(sleeps) == 1


def test_gemini_unparseable_json_is_retried(monkeypatch, sleeps):
    calls = _gemini(monkeypatch, ["not json", '{"ok": true}'])
    assert backend._call_ai_json("hi") == {"ok": True}
    assert len(calls) == 2


@BOTH
def test_ollama_refusal_is_not_retried(monkeypatch, sleeps, call, good, expected):
    calls = _ollama(monkeypatch, [_http(404, {"error": "model 'retired' not found"}),
                                  _http(200, {"message": {"content": good}})])
    with pytest.raises(ValueError, match="Ollama refused the request .HTTP 404: .*model 'retired' not found"):
        call("hi")
    assert len(calls) == 1 and sleeps == []


@BOTH
@pytest.mark.parametrize("first", [
    _http(429, {}), _http(500, {}), requests.ConnectionError("refused"), requests.Timeout("too slow"),
])
def test_ollama_passing_failure_is_retried(monkeypatch, sleeps, call, good, expected, first):
    calls = _ollama(monkeypatch, [first, _http(200, {"message": {"content": good}})])
    assert call("hi") == expected
    assert len(calls) == 2 and len(sleeps) == 1


@BOTH
def test_claude_refusal_is_not_retried_and_the_sdk_does_not_retry_either(
        monkeypatch, sleeps, call, good, expected):
    class NotFound(Exception):
        status_code = 404

    seen = {"calls": 0}

    class FakeAnthropic:
        def __init__(self, **kw):
            seen["client_kwargs"] = kw
            self.messages = self

        def create(self, **_kw):
            seen["calls"] += 1
            raise NotFound("model: retired-claude")

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setattr(backend, "AI_PROVIDER", "claude")
    monkeypatch.setattr(backend, "CLAUDE_API_KEY", "SECRET-KEY")

    with pytest.raises(ValueError, match="Claude refused the request .HTTP 404: model: retired-claude") as err:
        call("hi")
    assert seen["calls"] == 1 and sleeps == []
    assert seen["client_kwargs"]["max_retries"] == 0   # one retry loop, ours
    assert "SECRET-KEY" not in str(err.value)
