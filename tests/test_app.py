from dataclasses import asdict
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from matcher.ai import AIService, AIUnavailable
from matcher.demo import demo_request


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def app():
    return AppTest.from_file(APP, default_timeout=20).run()


def submit_prompt(at, text):
    at.text_area(key="prompt_message").set_value(text)
    at.button(key="submit_prompt").click().run()
    return at


def mock_demo(monkeypatch, name="Плотная категория · 14 ноября"):
    values = asdict(demo_request(name))
    monkeypatch.setattr(AIService, "interpret", lambda self, *args: values)


def test_first_screen_is_prompt_first_without_demo_cards():
    at = app()
    assert not at.exception and not at.error and not at.warning
    assert any("Кого вы ищете?" in title.value for title in at.title)
    assert at.button(key="submit_prompt").label == "Найти подрядчика"
    assert not at.success and not at.info
    assert not any(button.key.startswith("demo_") for button in at.button)


def test_complete_prompt_runs_existing_matching_and_shows_editable_summary(monkeypatch):
    mock_demo(monkeypatch)
    at = submit_prompt(app(), "Нужен ведущий в Алматы на корпоратив 14 ноября до 1,5 млн тенге")
    assert not at.exception and not at.error
    assert at.session_state.request.category == "Ведущий"
    assert at.session_state.request.budget == 1500000
    assert "Подобраны 3" in at.success[0].value
    assert any("Я понял" in item.value for item in at.markdown)
    assert at.selectbox(key="category").value == "Ведущий"
    assert at.number_input(key="budget").value == 1500000
    assert [item.label for item in at.expander].count("Почему рекомендован") == 3


def test_incomplete_prompt_asks_only_missing_fields_and_accepts_followup(monkeypatch):
    full = asdict(demo_request("Плотная категория · 14 ноября"))
    first = dict(full, city=None, date=None, budget=None, event_format=None)
    calls = []

    def interpret(self, message, previous, catalog, today):
        calls.append((message, previous))
        return first if len(calls) == 1 else full

    monkeypatch.setattr(AIService, "interpret", interpret)
    at = submit_prompt(app(), "Ищу ведущего")
    assert not at.exception and not at.success
    assert at.session_state.request is None
    assert "городе" in at.info[0].value and "дату" in at.info[0].value
    assert "кого ищете" not in at.info[0].value
    assert any("Предварительно" in item.value for item in at.caption)
    assert sum("Дата не указана — доступность не проверена" in item.value
               for item in at.markdown) == 3
    at = submit_prompt(at, "Алматы, 14 ноября, корпоратив, до 1,5 млн ₸")
    assert not at.exception and at.success
    assert calls[1][1]["category"] == "Ведущий"


def test_partial_request_does_not_show_a_score_or_false_availability(monkeypatch):
    first = {"city": None, "category": "Ведущий", "date": None,
             "event_format": None, "budget": None, "hours": None,
             "languages": (), "preference": ""}
    monkeypatch.setattr(AIService, "interpret", lambda self, *args: first)
    at = submit_prompt(app(), "Мне нужен ведущий")
    assert not at.exception and not at.error
    assert at.session_state.request is None
    assert sum("Ведущий» есть в профиле" in item.value for item in at.markdown) == 3
    assert not any("Доступен" in item.value or "Совпадение с запросом" in item.value
                   for item in at.markdown)


def test_three_turn_dialogue_updates_previews_before_final_matching(monkeypatch):
    full = asdict(demo_request("Плотная категория · 14 ноября"))
    first = dict(full, city=None, date=None, event_format=None, budget=None)
    second = dict(full, event_format=None, budget=None)
    replies = iter((first, second, full))
    monkeypatch.setattr(AIService, "interpret", lambda self, *args: next(replies))
    at = submit_prompt(app(), "Мне нужен ведущий")
    assert at.session_state.request is None
    assert any("Предварительно" in item.value for item in at.caption)
    at = submit_prompt(at, "Алматы, 14 ноября")
    assert at.session_state.request is None
    assert "формат" in at.info[0].value and "бюджет" in at.info[0].value
    assert any("Дата отсутствует в списке занятых дат" in item.value
               for item in at.markdown)
    at = submit_prompt(at, "Корпоратив, до 1,5 млн тенге")
    assert not at.exception and at.session_state.request is not None
    assert "Подобраны 3" in at.success[0].value


def test_ai_outage_keeps_text_and_manual_path(monkeypatch):
    def unavailable(self, *args):
        raise AIUnavailable("private provider detail")

    monkeypatch.setattr(AIService, "interpret", unavailable)
    at = submit_prompt(app(), "Ищу флориста")
    assert not at.exception and not at.error
    assert "ИИ сейчас недоступен" in at.warning[0].value
    assert any("Ищу флориста" in item.value for item in at.caption)
    assert "private provider detail" not in at.warning[0].value
    at.selectbox(key="category").set_value("Флорист")
    at.date_input(key="date").set_value(date(2026, 10, 10))
    at.number_input(key="budget").set_value(300000)
    at.button(key="submit_query").click().run()
    assert not at.exception and not at.warning
    assert at.session_state.request.category == "Флорист"
    assert "Найдено 1 из 3" in at.info[0].value


def test_manual_invalid_budget_and_recovery():
    at = app()
    at.button(key="submit_query").click().run()
    assert not at.exception
    assert "Бюджет" in at.error[0].value
    assert not at.success
    at.number_input(key="budget").set_value(1500000)
    at.button(key="submit_query").click().run()
    assert not at.exception and not at.error
    assert at.session_state.request.hours is None
    assert at.session_state.request.languages == ()


def test_no_category_and_no_matches_stay_distinct():
    at = app()
    at.selectbox(key="city").set_value("Зарубежье")
    at.selectbox(key="category").set_value("Флорист")
    at.date_input(key="date").set_value(date(2026, 11, 14))
    at.number_input(key="budget").set_value(300000)
    at.button(key="submit_query").click().run()
    assert not at.exception
    assert "нет подрядчиков" in at.warning[0].value

    at.selectbox(key="city").set_value("Алматы")
    at.selectbox(key="category").set_value("Ведущий")
    at.number_input(key="budget").set_value(10000)
    at.button(key="submit_query").click().run()
    assert not at.exception
    assert "никто не проходит" in at.warning[0].value
    assert any("У всех профилей цена" in item.value for item in at.markdown)


def test_result_cards_keep_facts_comparison_and_no_raw_json(monkeypatch):
    mock_demo(monkeypatch)
    at = submit_prompt(app(), "Ведущий для корпоратива в Алматы 14 ноября до 1,5 млн")
    assert not at.exception
    markdown = [item.value for item in at.markdown]
    assert sum("**Категория.**" in item for item in markdown) == 3
    assert sum("**Доступность.** На 14.11.2026 дата отсутствует в списке занятых дат" in item
               for item in markdown) == 3
    assert any("Почему первый кандидат выше второго" in item.value for item in at.subheader)
    assert not at.json and not at.text


def test_new_request_clears_previous_result(monkeypatch):
    mock_demo(monkeypatch)
    at = submit_prompt(app(), "Ведущий для корпоратива в Алматы 14 ноября до 1,5 млн")
    at.button(key="new_query").click().run()
    assert not at.exception and not at.success
    assert at.session_state.request is None
