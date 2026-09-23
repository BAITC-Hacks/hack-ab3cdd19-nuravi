from datetime import date
from decimal import Decimal

import pytest

from matcher.ai import AIService, AIUnavailable, INTAKE_SCHEMA
from matcher.data import load_catalog
from matcher.engine import select
from matcher.intake import missing, normalize, question, to_request


FULL = {"city": "Алматы", "date": "2026-11-14", "event_format": "корпоратив",
        "category": "Ведущий", "budget": 1500000, "hours": 6,
        "languages": ["русский"], "preference": None}


def test_normalized_ai_fields_make_a_valid_request_without_changing_hard_filters():
    catalog = load_catalog()
    values = normalize(FULL, catalog)
    assert not missing(values)
    q = to_request(values)
    assert q.date == date(2026, 11, 14)
    assert q.budget == Decimal(1500000)
    assert q.languages == ("русский",)
    result = select(catalog, q)
    assert result["status"] == "found" and result["eligible"] == 4
    assert all(q.date not in c["profile"].busy_dates for c in result["candidates"])


def test_missing_and_invalid_required_fields_trigger_one_clarification():
    catalog = load_catalog()
    values = normalize(dict(FULL, city=None, date="2027-01-01", budget=0), catalog)
    assert missing(values) == ("city", "date", "budget")
    text = question(values)
    assert "город" in text and "дату" in text and "бюджет" in text
    assert "категорию" not in text
    with pytest.raises(ValueError):
        to_request(values)


def test_unknown_city_is_an_honest_empty_result():
    catalog = load_catalog()
    values = normalize(dict(FULL, city="Шымкент"), catalog)
    assert to_request(values).city == "Шымкент"
    assert select(catalog, to_request(values))["status"] == "no_category"


def test_ai_interpretation_is_cached_and_schema_checked(tmp_path, monkeypatch):
    catalog = load_catalog()
    calls = []

    def fake_chat(self, provider, system, payload, schema=None):
        calls.append((provider, payload, schema))
        return FULL

    monkeypatch.setattr(AIService, "_chat", fake_chat)
    kwargs = {"config": {"OPENAI_API_KEY": "test"}, "cache_path": tmp_path / "emb.json",
              "evidence_cache_path": tmp_path / "evidence.json",
              "intake_cache_path": tmp_path / "intake.json"}
    service = AIService(**kwargs)
    first = service.interpret("Нужен ведущий в Алматы 14 ноября", {}, catalog, date(2026, 9, 23))
    assert service.interpret("Нужен ведущий в Алматы 14 ноября", {}, catalog,
                             date(2026, 9, 23)) == first
    assert AIService(**kwargs).interpret("Нужен ведущий в Алматы 14 ноября", {}, catalog,
                                        date(2026, 9, 23)) == first
    assert len(calls) == 1 and calls[0][0] == "OpenAI"
    assert calls[0][2] == INTAKE_SCHEMA


def test_followup_sends_prior_fields_and_nvidia_can_take_over(tmp_path, monkeypatch):
    catalog = load_catalog()
    prior = normalize(dict(FULL, city=None, date=None, budget=None, event_format=None), catalog)
    providers = []

    def fake_chat(self, provider, system, payload, schema=None):
        providers.append(provider)
        assert payload["previous"]["category"] == "Ведущий"
        assert payload["previous"]["hours"] == "6"  # Decimal is serialized safely.
        if provider == "OpenAI":
            raise AIUnavailable("offline")
        return FULL

    monkeypatch.setattr(AIService, "_chat", fake_chat)
    service = AIService({"OPENAI_API_KEY": "test", "NVIDIA_API_KEY": "test"},
                        tmp_path / "emb.json", tmp_path / "evidence.json", tmp_path / "intake.json")
    values = service.interpret("Алматы, 14 ноября, корпоратив, 1,5 млн ₸", prior,
                               catalog, date(2026, 9, 23))
    assert providers == ["OpenAI", "NVIDIA"]
    assert not missing(values)


def test_malformed_ai_response_never_creates_a_request(tmp_path, monkeypatch):
    catalog = load_catalog()
    monkeypatch.setattr(AIService, "_chat", lambda self, *args, **kwargs: {"city": "Алматы"})
    service = AIService({"OPENAI_API_KEY": "test"}, tmp_path / "emb.json",
                        tmp_path / "evidence.json", tmp_path / "intake.json")
    with pytest.raises(AIUnavailable, match="не удалось обработать запрос"):
        service.interpret("Ведущий", {}, catalog, date(2026, 9, 23))


def test_connection_failure_has_an_actionable_message(tmp_path, monkeypatch):
    catalog = load_catalog()

    def offline(self, *args, **kwargs):
        raise AIUnavailable("API временно недоступен")

    monkeypatch.setattr(AIService, "_chat", offline)
    service = AIService({"OPENAI_API_KEY": "test", "NVIDIA_API_KEY": "test"},
                        tmp_path / "emb.json", tmp_path / "evidence.json", tmp_path / "intake.json")
    with pytest.raises(AIUnavailable, match="нет соединения с AI-сервисом"):
        service.interpret("Ведущий", {}, catalog, date(2026, 9, 23))
