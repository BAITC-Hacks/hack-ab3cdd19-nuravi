from dataclasses import replace
from types import SimpleNamespace

import pytest

from matcher.ai import AIService, AIUnavailable
from matcher.data import load_catalog
from matcher.demo import demo_request
from matcher.engine import failures, select
from matcher.explain import comparison_summary, explain


def test_ai_score_changes_only_semantics_after_hard_filters():
    catalog = load_catalog()
    q = demo_request("Пожелание · деловой форум")
    local = select(catalog, q)
    eligible = [p for p in catalog.profiles if p.city == q.city and q.category in p.categories
                and not failures(p, q)]
    override = {eligible[0].id: 0.7}
    ai = select(catalog, q, override)
    assert (ai["eligible"], ai["reasons"], ai["rejected"]) == \
           (local["eligible"], local["reasons"], local["rejected"])
    assert all(c["profile"].id not in ai["rejected"] for c in ai["candidates"])


def test_embedding_cache_avoids_repeat_api_calls(tmp_path, monkeypatch):
    calls = []

    def fake_post(base, path, key, body, timeout=6):
        calls.append(body["input"])
        return {"data": [{"index": i, "embedding": vector} for i, vector in
                         enumerate(([1.0, 0.0], [0.6, 0.8]))]}

    monkeypatch.setattr("matcher.ai._post", fake_post)
    service = AIService({"OPENAI_API_KEY": "test"}, tmp_path / "embeddings.json")
    p = SimpleNamespace(id="A", description="Провожу деловые форумы")
    assert service.semantic_scores([p], "деловой форум") == {"A": pytest.approx(0.6)}
    assert service.semantic_scores([p], "деловой форум") == {"A": pytest.approx(0.6)}
    assert len(calls) == 1
    assert AIService({"OPENAI_API_KEY": "test"}, tmp_path / "embeddings.json").semantic_scores(
        [p], "деловой форум") == {"A": pytest.approx(0.6)}
    assert len(calls) == 1


def test_quote_requires_exact_source_and_auditor(tmp_path, monkeypatch):
    q = replace(demo_request("Пожелание · деловой форум"), preference="спокойный стиль")
    candidate = {"profile": SimpleNamespace(id="A", description="Веду спокойно и без спешки.")}
    service = AIService({"OPENAI_API_KEY": "test", "NVIDIA_API_KEY": "test"},
                        tmp_path / "cache.json", tmp_path / "evidence.json")

    def fake_chat(provider, system, payload, schema=None):
        if "Выбери" in system:
            return {"items": [{"id": "A", "quote": "Веду спокойно и без спешки."}]}
        if provider == "NVIDIA":
            raise AIUnavailable("HTTP 401")
        return {"approved_ids": ["A"]}

    monkeypatch.setattr(service, "_chat", fake_chat)
    quotes, status = service.evidence([candidate], q)
    assert quotes == {"A": "Веду спокойно и без спешки."}
    assert "OpenAI проверил" in status

    monkeypatch.setattr(service, "_chat", lambda *args, **kwargs:
                        pytest.fail("cached evidence should not call an API"))
    assert service.evidence([candidate], q)[0] == quotes

    service = AIService({"OPENAI_API_KEY": "test", "NVIDIA_API_KEY": "test"},
                        tmp_path / "cache.json", tmp_path / "other-evidence.json")
    monkeypatch.setattr(service, "_chat", lambda *args, **kwargs:
                        {"items": [{"id": "A", "quote": "Выдающийся опыт в любой отрасли."}]})
    quotes, _ = service.evidence([candidate], q)
    assert quotes == {}


def test_nvidia_quote_is_hidden_when_auditor_rejects_it(tmp_path, monkeypatch):
    q = replace(demo_request("Пожелание · деловой форум"), preference="спокойный стиль")
    candidate = {"profile": SimpleNamespace(id="A", description="Веду спокойно и без спешки.")}
    service = AIService({"NVIDIA_API_KEY": "test"}, tmp_path / "cache.json",
                        tmp_path / "evidence.json")

    def fake_chat(provider, system, payload, schema=None):
        if provider == "OpenAI":
            raise AIUnavailable("API-ключ не задан")
        if "Выбери" in system:
            return {"items": [{"id": "A", "quote": "Веду спокойно и без спешки."}]}
        return {"approved_ids": []}

    monkeypatch.setattr(service, "_chat", fake_chat)
    quotes, status = service.evidence([candidate], q)
    assert quotes == {}
    assert "NVIDIA проверил смысл" in status


def test_ai_quote_is_used_in_card_explanation():
    catalog = load_catalog()
    q = replace(demo_request("Пожелание · деловой форум"), preference="спокойный стиль")
    candidate = select(catalog, q)["candidates"][0].copy()
    quote = candidate["profile"].description.split(".", 1)[0]
    candidate["ai_quote"] = quote
    text = explain(candidate, q)
    assert f"«{quote}" in text
    assert "подтверждения пожеланию не найдено" not in text


def test_reasoning_model_override_uses_supported_request_parameters(monkeypatch):
    sent = {}

    def fake_post(base, path, key, body, timeout=6):
        sent.update(body)
        return {"choices": [{"message": {"content": '{"approved_ids": []}'}}]}

    monkeypatch.setattr("matcher.ai._post", fake_post)
    service = AIService({"OPENAI_API_KEY": "test", "OPENAI_CHAT_MODEL": "gpt-5.6-sol"})
    assert service._chat("OpenAI", "Return JSON", {}, {"type": "object"}) == {"approved_ids": []}
    assert sent["model"] == "gpt-5.6-sol"
    assert sent["reasoning_effort"] == "low"
    assert "temperature" not in sent and "max_tokens" not in sent


def test_unknown_ai_score_rejected():
    catalog = load_catalog()
    q = demo_request("Пожелание · деловой форум")
    eligible = next(p for p in catalog.profiles if p.city == q.city and q.category in p.categories
                    and not failures(p, q))
    with pytest.raises(ValueError, match="Семантическая оценка"):
        select(catalog, q, {eligible.id: 1.1})


def test_comparison_mentions_tradeoff():
    first = {"profile": SimpleNamespace(id="A"), "score": 82.0,
             "breakdown": {"semantic": {"points": 12.0}, "budget": {"points": 8.0}}}
    second = {"profile": SimpleNamespace(id="B"), "score": 78.0,
              "breakdown": {"semantic": {"points": 6.0}, "budget": {"points": 10.0}}}
    summary = comparison_summary(first, second)
    assert "Релевантность описания +6.0" in summary
    assert "Бюджет -2.0" in summary
