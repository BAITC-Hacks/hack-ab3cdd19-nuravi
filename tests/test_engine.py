from dataclasses import replace
from datetime import date
from decimal import Decimal
import random

import pytest

from matcher.data import Catalog, Profile, load_catalog, START, END
from matcher.demo import DEMOS, demo_request
from matcher.engine import Request, select, score, semantic_evidence
from matcher.explain import explain, limitations


@pytest.fixture
def q():
    return Request("Алматы", date(2026, 11, 14), "корпоратив", "Ведущий", 500000, 6, ("русский",))


@pytest.fixture
def p():
    # Test-only fixture, never loaded into the demo catalog.
    return Profile("TEST-1", "Тестовый профиль", ("Ведущий",), "Алматы", Decimal(400000),
                   ("корпоратив",), ("русский", "казахский"), Decimal(8),
                   frozenset({date(2026, 10, 1)}),
                   "Веду корпоративы на русском языке.")


def catalog(*profiles):
    return Catalog(tuple(profiles), (), len(profiles))


@pytest.mark.parametrize("change,reason", [
    ({"busy_dates": frozenset({date(2026, 11, 14)})}, "busy"),
    ({"price": Decimal(500001)}, "budget"),
    ({"formats": ("свадьба",)}, "format"),
    ({"languages": ("английский",)}, "language"),
    ({"max_hours": Decimal(5)}, "duration"),
])
def test_each_hard_filter(p, q, change, reason):
    result = select(catalog(replace(p, **change)), q)
    assert result["status"] == "no_matches"
    assert result["candidates"] == []
    assert result["reasons"][reason] == 1
    assert result["rejected"] == {p.id: (reason,)}


@pytest.mark.parametrize("change", [{"city": "Астана"}, {"categories": ("Фотограф",)}, {"categories": ("Ведущий церемонии",)}])
def test_no_category_is_distinct(p, q, change):
    result = select(catalog(replace(p, **change)), q)
    assert result["status"] == "no_category"
    assert not any(result["reasons"].values())


def test_all_busy_and_overlapping_reasons(p, q):
    ps = [replace(p, id=str(i), busy_dates=frozenset({q.date}), price=Decimal(600000)) for i in range(3)]
    result = select(catalog(*ps), q)
    assert result["status"] == "no_matches"
    assert result["reasons"]["busy"] == result["reasons"]["budget"] == 3
    assert len(result["rejected"]) == 3
    assert [n for _, n in result["funnel"]] == [3, 3, 3, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("empty_calendar", [None, frozenset()])
def test_unknown_availability_is_not_treated_as_free(p, q, empty_calendar):
    unknown = replace(p, busy_dates=empty_calendar)
    result = select(catalog(unknown), q)
    assert result["status"] == "no_matches"
    assert result["reasons"]["availability_unknown"] == 1
    assert result["rejected"] == {p.id: ("availability_unknown",)}
    assert "данных о занятости нет" in " ".join(limitations(unknown))


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_result_counts(p, q, count):
    result = select(catalog(*(replace(p, id=str(i)) for i in range(count))), q)
    assert len(result["candidates"]) == min(3, count)
    assert result["eligible"] == count
    assert result["not_shown"] == max(0, count - 3)


def test_null_hours_not_a_confirmed_match(p, q):
    p = replace(p, max_hours=None)
    r = select(catalog(p), replace(q, hours=100))
    assert r["eligible"] == 1
    assert r["candidates"][0]["breakdown"]["duration"]["points"] == 0
    assert "длительность не подтверждена" in " ".join(limitations(p))
    assert "при максимуме" not in explain(r["candidates"][0], q)


def test_optional_weights_are_renormalized(p, q):
    q = replace(q, languages=(), hours=None)
    total, breakdown, *_ = score(p, q)
    assert set(breakdown) == {"format", "budget", "semantic"}
    assert sum(v["points"] for v in breakdown.values()) == pytest.approx(float(total))
    assert breakdown["format"]["points"] == pytest.approx(25 / 65 * 100)


def test_budget_prefers_proximity_not_cheapest(p, q):
    r = select(catalog(p, replace(p, id="closer", price=Decimal(490000))), q)
    assert r["candidates"][0]["profile"].id == "closer"


def test_price_and_duration_boundaries_are_inclusive(p, q):
    assert select(catalog(replace(p, price=q.budget, max_hours=q.hours)), q)["eligible"] == 1


def test_all_selected_languages_required(p, q):
    assert select(catalog(p), replace(q, languages=("русский", "казахский")))["eligible"] == 1
    assert select(catalog(p), replace(q, languages=("русский", "английский")))["eligible"] == 0


def test_normalization_and_multicategory(p, q):
    p = replace(p, categories=("Видеограф", "Ведущий"))
    assert select(catalog(p), replace(q, city="  АЛМАТЫ ", category="ведущий", event_format=" Корпоратив ", languages=("РУССКИЙ",)))["eligible"] == 1


@pytest.mark.parametrize("budget", [0, -1, "abc", "NaN", "Infinity", None])
def test_invalid_budget(q, budget):
    with pytest.raises(ValueError, match="Бюджет"):
        replace(q, budget=budget)


@pytest.mark.parametrize("hours", [0, -1, "nan", "inf", "six"])
def test_invalid_hours(q, hours):
    with pytest.raises(ValueError, match="Длительность"):
        replace(q, hours=hours)


@pytest.mark.parametrize("d", [date(2026, 9, 22), date(2027, 1, 1), "2026-11-14", None])
def test_outside_calendar(q, d):
    with pytest.raises(ValueError, match="Дата вне календаря"):
        replace(q, date=d)


@pytest.mark.parametrize("d", [START, END])
def test_calendar_edges(p, q, d):
    assert select(catalog(p), replace(q, date=d))["eligible"] == 1


def test_marketing_does_not_raise_score(p, q):
    original = score(p, q)[0]
    assert score(replace(p, description=p.description + " Лучший, надёжный, топ-1, опыт 100 лет!"), q)[0] == original
    ratio, hits = semantic_evidence(replace(p, description="Профессиональный подрядчик"), q)
    assert ratio == 0 and hits == []


def test_unknown_description(p, q):
    r = select(catalog(replace(p, description="")), q)
    assert r["eligible"] == 1
    assert r["candidates"][0]["breakdown"]["semantic"]["points"] == 0
    assert "описание отсутствует" in explain(r["candidates"][0], q)


def test_stable_ties_use_busy_share_then_id(p, q):
    profiles = [replace(p, id="B"), replace(p, id="A"),
                replace(p, id="0", busy_dates=frozenset({date(2026, 10, 1), END}))]
    r = select(catalog(*profiles), q)
    assert [c["profile"].id for c in r["candidates"]] == ["A", "B", "0"]


def test_tied_score_prefers_more_confirmations(p, q):
    a = replace(p, id="A", price=Decimal(100000))
    b = replace(p, id="B", description="Веду корпоративы.", price=Decimal(350000))
    assert score(a, q)[0] == score(b, q)[0]
    assert score(a, q)[3] > score(b, q)[3]
    assert select(catalog(b, a), q)["candidates"][0]["profile"].id == "A"


def test_tied_score_and_confirmations_prefer_lower_price(p, q):
    a = replace(p, id="A", price=Decimal(500000), max_hours=None)
    b = replace(p, id="B", price=Decimal(375000), description="Веду корпоративы.")
    assert score(a, q)[0] == score(b, q)[0]
    assert score(a, q)[3] == score(b, q)[3]
    assert select(catalog(a, b), q)["candidates"][0]["profile"].id == "B"


def test_real_data_deterministic_and_shuffle_invariant():
    cat = load_catalog()
    q = demo_request(next(iter(DEMOS)))
    expected = select(cat, q)["candidates"]
    shuffled = list(cat.profiles)
    random.Random(42).shuffle(shuffled)
    assert select(catalog(*shuffled), q)["candidates"] == expected
    assert select(cat, q)["candidates"] == expected
    explanations = [explain(c, q) for c in expected]
    # Even removing ids and names, the actual factual texts must differ.
    stripped = [text.replace(c["profile"].id, "").replace(c["profile"].name, "") for c, text in zip(expected, explanations)]
    assert len(set(stripped)) == 3


def test_real_demo_date_change_is_due_to_calendar():
    cat = load_catalog()
    q = demo_request(next(iter(DEMOS)))
    a, b = select(cat, q), select(cat, replace(q, date=date(2026, 11, 15)))
    assert [c["profile"].id for c in a["candidates"]] != [c["profile"].id for c in b["candidates"]]
    assert a["reasons"]["busy"] != b["reasons"]["busy"]
    eligible_a = {p.id for p in cat.profiles if p.city == q.city and q.category in p.categories and p.id not in a["rejected"]}
    eligible_b = {p.id for p in cat.profiles if p.city == q.city and q.category in p.categories and p.id not in b["rejected"]}
    for pid in eligible_a ^ eligible_b:
        p = next(p for p in cat.profiles if p.id == pid)
        assert (q.date in p.busy_dates) != (date(2026, 11, 15) in p.busy_dates)


def test_demo_outcomes():
    results = [select(load_catalog(), demo_request(name)) for name in DEMOS]
    assert [r["status"] for r in results] == ["found", "found", "found", "no_matches", "no_category"]
    assert [r["eligible"] for r in results] == [4, 6, 1, 0, 0]
