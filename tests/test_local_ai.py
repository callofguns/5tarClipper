"""Local AI picker, tested against a fake Ollama (no real Ollama or model needed)."""
import json

import httpx
import pytest

from clipper.errors import UserFixable
from clipper.video import local_ai, rank

VCFG = {"local_model": "granite4.2:8b"}
ANSWER = {"moments": [{"start": 12, "end": 40, "title": "Bridge jump", "reason": "Huge payoff.", "score": 8}]}


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture
def fake_ollama(monkeypatch):
    sent = []

    def fake_get(url, timeout=None):
        if url.endswith("/api/version"):
            return Resp(200, {"version": "0.13"})
        if url.endswith("/api/tags"):
            return Resp(200, {"models": [{"name": "granite4.2:8b"}]})
        raise AssertionError(url)

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        if json.get("think") is False and fake_post.reject_think:
            return Resp(400, text='{"error":"model does not support think"}')
        return Resp(200, {"message": {"content": __import__("json").dumps(ANSWER)}, "eval_count": 42})
    fake_post.reject_think = False

    monkeypatch.setattr(local_ai.httpx, "get", fake_get)
    monkeypatch.setattr(local_ai.httpx, "post", fake_post)
    monkeypatch.setattr(local_ai, "free_whisper_memory", lambda: None)
    return sent, fake_post


def test_pick_sends_schema_and_reads_answer(fake_ollama):
    sent, _ = fake_ollama
    out = local_ai.pick("PROMPT", "SYSTEM", rank.SCHEMA, VCFG, log=lambda m: None)
    assert out == ANSWER["moments"]
    body = sent[0]
    assert body["model"] == "granite4.2:8b"
    assert body["format"] == rank.SCHEMA            # answer is forced into our JSON shape
    assert body["stream"] is False and body["think"] is False
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}


def test_retries_without_think_for_models_that_dont_support_it(fake_ollama):
    sent, fake_post = fake_ollama
    fake_post.reject_think = True
    out = local_ai.pick("PROMPT", "SYSTEM", rank.SCHEMA, VCFG, log=lambda m: None)
    assert out == ANSWER["moments"]
    assert len(sent) == 2 and "think" not in sent[1]


def test_model_name_matching(fake_ollama):
    assert local_ai.model_ready("granite4.2:8b")
    assert not local_ai.model_ready("qwen3.5:9b")


def test_clear_message_when_ollama_missing(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(local_ai.httpx, "get", down)
    with pytest.raises(UserFixable, match="Ollama"):
        local_ai.pick("PROMPT", "SYSTEM", rank.SCHEMA, VCFG, log=lambda m: None)
