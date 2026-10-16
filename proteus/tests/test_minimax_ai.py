"""API integration tests use invented content and never call the network."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proteus import minimax_ai  # noqa: E402


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return json.dumps(self.data).encode()


def test_draft_sends_evidence_guardrails_and_uses_environment_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-test-key")
    seen = {}

    def fake_open(request, timeout):
        seen["request"] = request
        return FakeResponse({"choices": [{"message": {"content": "# Criterion A\n[Evidence needed]"}}]})

    monkeypatch.setattr(minimax_ai.urllib.request, "urlopen", fake_open)
    result = minimax_ai.draft_selection("Criterion A — example.com project")
    payload = json.loads(seen["request"].data)
    assert payload["model"] == "MiniMax-M2.7"
    assert "Never invent" in payload["messages"][0]["content"]
    assert "example.com" in payload["messages"][1]["content"]
    assert result.startswith("# Criterion A")


def test_search_exposes_source_urls_and_requires_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        minimax_ai.search_jobs("synthetic role")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-test-key")

    def fake_open(request, timeout):
        assert request.full_url.endswith("/v1/responses")
        assert json.loads(request.data)["tools"] == [{"type": "web_search"}]
        return FakeResponse({"status": "completed", "output_text": "One synthetic role",
                             "output": [{"content": [{"annotations": [
                                 {"url": "https://example.com/job"}]}]}]})

    monkeypatch.setattr(minimax_ai.urllib.request, "urlopen", fake_open)
    result = minimax_ai.search_jobs("synthetic role")
    assert "https://example.com/job" in result
