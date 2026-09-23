from dataclasses import replace
from datetime import date
from decimal import Decimal
import random

import pytest

from matcher.data import Catalog, Profile, load_catalog, START, END
from matcher.demo import DEMOS, demo_request
from matcher.engine import Request, select, score, semantic_evidence
from matcher.explain import (availability_fact, comparison_rows,
                             comparison_summary, explain, limitations,
                             recommendation_facts)


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
    assert "В профиле нет данных о занятых датах — доступность требует подтверждения." in limitations(unknown)
    assert availability_fact(unknown, q.date) == "В профиле нет данных о занятых датах — доступность требует подтверждения."


def test_confirmed_availability_uses_required_wording(p, q):
    assert availability_fact(p, q.date) == "На 14.11.2026 дата отсутствует в списке занятых дат — подрядчик считается доступным."


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
    assert set(breakdown) == {"format", "budget"}
    assert sum(v["points"] for v in breakdown.values()) == pytest.approx(float(total))
    assert breakdown["format"]["max_points"] == pytest.approx(25 / 45 * 100)
    assert all(v["points"] <= v["max_points"] for v in breakdown.values())


def test_budget_prefers_lower_price_after_hard_filter(p, q):
    r = select(catalog(p, replace(p, id="closer", price=Decimal(490000))), q)
    assert r["candidates"][0]["profile"].id == p.id


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
    q = replace(q, preference="деловой форум")
    original = score(p, q)[0]
    assert score(replace(p, description=p.description + " Лучший, надёжный, топ-1, опыт 100 лет!"), q)[0] == original
    ratio, hits = semantic_evidence(replace(p, description="Профессиональный подрядчик"), q)
    assert ratio == 0 and hits == []


def test_unknown_description(p, q):
    q = replace(q, preference="деловой форум")
    r = select(catalog(replace(p, description="")), q)
    assert r["eligible"] == 1
    assert r["candidates"][0]["breakdown"]["semantic"]["points"] == 0
    assert "описание профиля отсутствует" in explain(r["candidates"][0], q).lower()


def test_stable_ties_use_busy_share_then_id(p, q):
    profiles = [replace(p, id="B"), replace(p, id="A"),
                replace(p, id="0", busy_dates=frozenset({date(2026, 10, 1), END}))]
    r = select(catalog(*profiles), q)
    assert [c["profile"].id for c in r["candidates"]] == ["A", "B", "0"]


def test_preference_changes_order_with_grounded_evidence(p, q):
    q = replace(q, preference="деловой форум")
    matching = replace(p, id="MATCH", description="Провёл деловой форум для международных гостей.")
    other = replace(p, id="OTHER", description="Веду камерные свадьбы.")
    result = select(catalog(other, matching), q)
    assert result["candidates"][0]["profile"].id == "MATCH"
    assert "деловой форум" in explain(result["candidates"][0], q).lower()
    assert result["candidates"][0]["breakdown"]["semantic"]["points"] > 0
    assert result["candidates"][1]["breakdown"]["semantic"]["points"] == 0


def test_negative_preference_needs_negative_source_phrase(p, q):
    q = replace(q, preference="без конкурсов")
    with_contests = replace(p, id="CONTESTS", price=Decimal(100000),
                            description="Провожу конкурсы на каждом празднике.")
    without_contests = replace(p, id="NO-CONTESTS", price=Decimal(400000),
                               description="Веду мероприятие без конкурсов.")
    result = select(catalog(with_contests, without_contests), q)
    assert result["candidates"][0]["profile"].id == "NO-CONTESTS"
    assert result["candidates"][0]["evidence"][0]["match"] == without_contests.description
    assert result["candidates"][1]["breakdown"]["semantic"]["points"] == 0


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


def test_three_cards_have_complete_distinct_recommendation_facts():
    q = demo_request(next(iter(DEMOS)))
    candidates = select(load_catalog(), q)["candidates"]
    explanations = [recommendation_facts(candidate, q) for candidate in candidates]
    expected_labels = ["Категория", "Город", "Доступность", "Формат", "Язык", "Бюджет", "Длительность", "Релевантность описания"]
    assert len(explanations) == 3
    assert all([label for label, _ in facts] == expected_labels for facts in explanations)
    assert len({tuple(facts) for facts in explanations}) == 3
    assert all("На 14.11.2026 дата отсутствует в списке занятых дат" in dict(facts)["Доступность"] for facts in explanations)


def test_top_comparison_uses_real_score_components():
    q = demo_request(next(iter(DEMOS)))
    first, second = select(load_catalog(), q)["candidates"][:2]
    rows = comparison_rows(first, second, q)
    assert [row["Фактор"] for row in rows] == ["Формат", "Язык", "Бюджет", "Длительность", "Смысловая релевантность"]
    for factor, row in zip(("format", "language", "budget", "duration", "semantic"), rows):
        if factor in first["breakdown"]:
            assert f"{first['breakdown'][factor]['base_points']:.1f}/{first['breakdown'][factor]['weight']}" in row[first["profile"].id]
            assert f"{second['breakdown'][factor]['base_points']:.1f}/{second['breakdown'][factor]['weight']}" in row[second["profile"].id]
    assert first["profile"].id in comparison_summary(first, second)
    assert second["profile"].id in comparison_summary(first, second)


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
    assert [r["status"] for r in results] == ["found", "found", "found", "found", "no_matches", "no_category"]
    assert [r["eligible"] for r in results] == [4, 6, 6, 1, 0, 0]
