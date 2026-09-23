from dataclasses import asdict, replace
from datetime import timedelta
import json

import streamlit as st

from matcher.data import DATA, START, END, load_catalog
from matcher.demo import DEMOS, demo_request
from matcher.engine import Request, select, REASONS
from matcher.explain import (comparison_rows, comparison_summary, explain,
                             limitations, money, recommendation_facts)

st.set_page_config(page_title="Подбор · HackAlem", page_icon="◈", layout="wide")


@st.cache_data
def read_catalog(modified):
    return load_catalog()


try:
    catalog = read_catalog(DATA.stat().st_mtime_ns)
except (OSError, ValueError) as exc:
    st.error(f"Не удалось загрузить каталог: {exc}. Проверьте data/contractors.csv.")
    st.stop()

if not catalog.profiles:
    st.error("В каталоге нет корректных профилей. Исправьте CSV перед подбором.")
    st.write(list(catalog.issues))
    st.stop()


def fill_demo():
    data = DEMOS[st.session_state.demo]
    for k, v in data.items():
        st.session_state[k] = list(v) if k == "languages" else float(v) if k == "hours" and v else v
    st.session_state.hours = float(data["hours"] or 0)
    st.session_state.request = demo_request(st.session_state.demo)
    st.session_state.pop("validation_error", None)


if "request" not in st.session_state:
    st.session_state.demo = next(iter(DEMOS))
    fill_demo()

demo_labels = {
    "Плотная категория · 14 ноября": "Плотная категория",
    "Та же заявка · 15 ноября": "Другая дата",
    "Редкая категория · флорист": "Редкая категория",
    "Никто не проходит · бюджет": "Нет результата",
    "Категории нет · зарубежье": "Нет категории в городе",
}

st.caption("HACKALEM / УМНЫЙ ПОДБОР ПОДРЯДЧИКОВ")
st.title("Подбор подрядчиков")
st.caption("До трёх вариантов из каталога с проверяемыми причинами выбора.")

with st.container(border=True):
    st.segmented_control("Быстрые примеры", list(DEMOS), key="demo", format_func=demo_labels.get,
                         on_change=fill_demo, width="stretch")
    row = st.columns([1, 1.35, 1, 1, 1.15])
    row[0].selectbox("Город", sorted({p.city for p in catalog.profiles}), key="city")
    row[1].selectbox("Категория", sorted({c for p in catalog.profiles for c in p.categories}), key="category")
    row[2].date_input("Дата", min_value=START, max_value=END, format="DD.MM.YYYY", key="date")
    row[3].selectbox("Формат", sorted({f for p in catalog.profiles for f in p.formats}), key="event_format")
    row[4].number_input("Бюджет, ₸", min_value=0, step=50000, key="budget")
    with st.expander("Дополнительные параметры · язык и длительность"):
        optional = st.columns(2)
        optional[0].multiselect("Языки", sorted({l for p in catalog.profiles for l in p.languages}), key="languages", help="Подрядчик должен поддерживать все выбранные языки.")
        optional[1].number_input("Длительность, ч · 0 = не указана", min_value=0.0, step=0.5, key="hours")
    submitted = st.button("Подобрать подрядчиков", type="primary")
    if submitted:
        try:
            st.session_state.request = Request(
                **{k: st.session_state[k] for k in ("city", "date", "event_format", "category", "budget", "languages")},
                hours=st.session_state.hours or None)
            st.session_state.pop("validation_error", None)
        except ValueError as exc:
            st.session_state.validation_error = str(exc)
    st.caption("Календарь: 23.09–31.12.2026 · подбор и объяснения работают локально.")

if catalog.issues:
    st.warning(f"Исключено повреждённых строк: {len(catalog.issues)}. Итоги относятся только к корректной части каталога.")
    with st.expander("Ошибки исходных данных"):
        st.write(list(catalog.issues))
if "validation_error" in st.session_state:
    st.error(st.session_state.validation_error)
    st.info("Исправьте параметры и повторите подбор.")
    st.stop()

q = st.session_state.request
r = select(catalog, q)
st.subheader(f"{q.category} · {q.city} · {q.date:%d.%m.%Y}")
st.caption(f"{q.event_format.capitalize()} · {money(q.budget)} · {', '.join(q.languages) or 'язык не задан'} · {f'{q.hours:g} ч' if q.hours else 'длительность не задана'}")

metrics = st.columns(4)
metrics[0].metric("В городе", r["city_count"])
metrics[1].metric("Подходят", r["eligible"])
metrics[2].metric("Показано", len(r["candidates"]))
metrics[3].metric("Исключено", len(r["rejected"]))

if r["status"] == "no_category":
    st.warning(f"В городе «{q.city}» нет подрядчиков категории «{q.category}» в доступном каталоге.")
    st.write("Выберите другой город или категорию. Ослабление бюджета и даты здесь не добавит профилей.")
elif r["status"] == "no_matches":
    st.warning(f"В городе есть {r['city_count']} профилей этой категории, но никто не проходит все условия.")
    st.write("Причины и количество исключённых профилей приведены ниже. Попробуйте другую дату или скорректируйте ограничения.")
else:
    if r["eligible"] < 3:
        st.info(f"Найдено {r['eligible']} из 3 вариантов. Остальные профили не прошли условия — причины ниже.")
    else:
        st.success(f"Подобраны 3 варианта из {r['eligible']} подходящих.")
    cards = st.columns(len(r["candidates"]))
    for i, (column, c) in enumerate(zip(cards, r["candidates"]), 1):
        p = c["profile"]
        with column.container(border=True):
            st.markdown(f"### {i}. {p.name}")
            st.caption(f"{p.id} · {' / '.join(p.categories)} · {p.city}")
            st.metric("Соответствие", f"{c['score']:.1f} / 100")
            st.markdown(f"**От {money(p.price)}**")
            st.caption("✓ Доступен на дату · ✓ Формат подходит")
            st.write(explain(c, q))
            with st.expander("Почему рекомендован"):
                for label, fact in recommendation_facts(c, q):
                    st.markdown(f"**{label}.** {fact}")
                st.markdown("**Состав оценки**")
                labels = {"format": "Формат", "language": "Язык", "budget": "Бюджет", "duration": "Длительность", "semantic": "Описание"}
                for key, value in c["breakdown"].items():
                    st.caption(f"{labels[key]}: {value['points']:.1f} из {value['weight']} баллов · подтверждено {value['value']:.0%}")
                notes = limitations(p)
                if notes:
                    st.markdown("**Ограничения**")
                    for note in notes:
                        st.caption(note)

    if len(r["candidates"]) >= 2:
        first, second = r["candidates"][:2]
        st.subheader("Почему первый кандидат выше второго")
        st.write(comparison_summary(first, second))
        with st.expander("Показать сравнение всех компонентов"):
            st.table(comparison_rows(first, second, q))
            st.caption("Использованы те же компоненты и баллы, что и в итоговой оценке; категория, город и доступность уже проверены обязательными условиями.")

st.subheader("Почему другие не попали")
reason_parts = [f"{label.lower()} — {r['reasons'][code]}" for code, label in REASONS.items() if r["reasons"][code]]
st.write(" · ".join(reason_parts) if reason_parts else "Дополнительных исключений по условиям нет.")
st.caption(f"Другой город: {r['category_count'] - r['city_count']} · прошли условия, но ниже топ-3: {r['not_shown']}.")
with st.expander("Подробности исключения"):
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Последовательная воронка")
        st.table([{"Этап": stage, "Осталось": count} for stage, count in r["funnel"]])
    with col2:
        st.caption("Все причины")
        st.table([{"Причина": label, "Профилей": r["reasons"][code]} for code, label in REASONS.items()])
        st.caption("У профиля может быть несколько причин, поэтому сумма может быть больше числа исключённых.")
    names = {p.id: p.name for p in catalog.profiles}
    if r["rejected"]:
        st.markdown("**Исключённые профили**")
        st.table([{"ID": pid, "Имя": names[pid], "Причины": "; ".join(REASONS[x] for x in reasons)}
                  for pid, reasons in sorted(r["rejected"].items())])
    else:
        st.caption("Нет исключённых профилей.")

with st.expander("Что меняется на другой дате", expanded=False):
    other_date = st.date_input("Сравнить с датой", value=q.date + timedelta(days=1) if q.date < END else q.date - timedelta(days=1), min_value=START, max_value=END, format="DD.MM.YYYY")
    other = select(catalog, replace(q, date=other_date))
    st.write(f"На {q.date:%d.%m}: подходят {r['eligible']}, заняты {r['reasons']['busy']}. На {other_date:%d.%m}: подходят {other['eligible']}, заняты {other['reasons']['busy']}.")
    st.write("Топ на второй дате: " + (", ".join(f"{c['profile'].name} ({c['profile'].id})" for c in other["candidates"]) or "никто не проходит"))
    changes = [{"ID": p.id, "Имя": p.name, f"{q.date:%d.%m.%Y}": "занят" if q.date in p.busy_dates else "свободен", f"{other_date:%d.%m.%Y}": "занят" if other_date in p.busy_dates else "свободен"}
               for p in catalog.profiles if p.city == q.city and q.category in p.categories
               and p.busy_dates
               and (q.date in p.busy_dates) != (other_date in p.busy_dates)]
    if changes:
        st.table(changes)
    else:
        st.caption("Занятость этой категории не изменилась; выдача может остаться той же.")

with st.expander("Как принято решение · аудит подбора"):
    st.write("Обязательные условия: категория → город → наличие календаря → дата → бюджет → формат → все выбранные языки → длительность. Отсутствие максимума часов не исключает профиль, но длительность считается неподтверждённой.")
    st.write("Веса: формат 25%, язык 20%, бюджет 20%, длительность 15%, описание 20%. Неуказанные язык и длительность не влияют на оценку; оставшиеся веса приводятся к шкале 100. Если максимум часов не указан, фактор длительности даёт 0 баллов.")
    st.write("Бюджет: цена / бюджет. Среди допустимых цен выше балл у цены ближе к бюджету; это не оценка качества и не рекомендация потратить больше.")
    st.write("Описание: учитывается доля явных совпадений с форматом и выбранными языками. Стиль, масштаб, аудитория и опыт без соответствующих параметров запроса не дают бонусов. Рекламные заявления не считаются рейтингом.")
    st.write("При равных баллах: больше подтверждений → меньшая цена → меньшая доля занятых дней из 100 → идентификатор по алфавиту. Доступность не входит в балл; доля занятых дней используется только для устойчивого порядка при равенстве.")
    st.write("Отметки о синтетическом профиле, восстановленном городе или цене не повышают оценку и показываются в ограничениях карточки. Оценка — показатель соответствия запросу, а не вероятность успеха или рейтинг надёжности.")
    st.markdown("**Параметры этого подбора**")
    st.table([
        {"Параметр": "Город", "Значение": q.city},
        {"Параметр": "Категория", "Значение": q.category},
        {"Параметр": "Дата", "Значение": f"{q.date:%d.%m.%Y}"},
        {"Параметр": "Формат", "Значение": q.event_format},
        {"Параметр": "Бюджет", "Значение": money(q.budget)},
        {"Параметр": "Языки", "Значение": ", ".join(q.languages) or "не указаны"},
        {"Параметр": "Длительность", "Значение": f"{q.hours:g} ч" if q.hours is not None else "не указана"},
    ])
    payload = {"request": asdict(q), "status": r["status"], "funnel": r["funnel"], "reasons": r["reasons"],
               "rejected": r["rejected"], "eligible": r["eligible"], "data_issues": catalog.issues,
               "candidates": [{"profile": asdict(c["profile"]), "score": c["score"], "breakdown": c["breakdown"], "evidence": c["evidence"], "explanation": explain(c, q), "recommendation_facts": recommendation_facts(c, q), "limitations": limitations(c["profile"])} for c in r["candidates"]]}
    if len(r["candidates"]) >= 2:
        payload["top_comparison"] = {"summary": comparison_summary(*r["candidates"][:2]),
                                     "components": comparison_rows(*r["candidates"][:2], q)}
    def serialize(value):
        if isinstance(value, (set, frozenset)):
            return sorted(str(x) for x in value)
        return str(value)
    st.download_button("Скачать аудит JSON", json.dumps(payload, ensure_ascii=False, indent=2, default=serialize), "audit.json", "application/json")
