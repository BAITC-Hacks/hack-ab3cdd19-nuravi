from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from matcher.demo import DEMOS

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.mark.parametrize("name", list(DEMOS))
def test_demo_ui(name):
    at = AppTest.from_file(APP, default_timeout=20).run()
    at.button_group(key="demo").set_value(name)
    at.run()
    assert not at.exception
    assert not at.error
    assert any("Почему другие" in s.value for s in at.subheader)
    assert len(at.table) >= 2
    if "Никто" in name:
        assert "никто не проходит" in at.warning[0].value
    elif "Категории нет" in name:
        assert "нет подрядчиков" in at.warning[0].value
    elif "Редкая" in name:
        assert "Найдено 1 из 3" in at.info[0].value
    else:
        assert "Подобраны 3" in at.success[0].value


def test_invalid_budget_and_recovery():
    at = AppTest.from_file(APP, default_timeout=20).run()
    at.number_input(key="budget").set_value(0)
    at.button[0].click().run()
    assert not at.exception
    assert "Бюджет" in at.error[0].value
    assert not at.success
    at.number_input(key="budget").set_value(1500000)
    at.button[0].click().run()
    assert not at.exception and not at.error and at.success


def test_submit_custom_query_without_optional_fields():
    at = AppTest.from_file(APP, default_timeout=20).run()
    at.multiselect(key="languages").set_value([])
    at.number_input(key="hours").set_value(0.0)
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state.request.hours is None
    assert at.session_state.request.languages == ()


def test_main_controls_have_submit_button_without_form_warning():
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    assert not at.error
    assert not at.warning
    assert any(button.label == "Подобрать подрядчиков" for button in at.button)


def test_cards_show_factual_explanations_and_top_comparison():
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    subheadings = [heading.value for heading in at.subheader]
    markdown = [item.value for item in at.markdown]
    assert [item.label for item in at.expander].count("Почему рекомендован") == 3
    assert sum("**Категория.**" in item for item in markdown) == 3
    assert sum("**Доступность.** Дата отсутствует в списке занятых дат — подрядчик считается доступным." in item for item in markdown) == 3
    assert "Почему первый кандидат выше второго" in subheadings
    assert any("HK-44733 выше HK-27222 на 10.0 балла" in item for item in markdown)
    assert not at.json
    assert not at.text
    assert not any("Словарные совпадения" in item or "description" in item for item in markdown)
