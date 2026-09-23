from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path

import streamlit as st

from matcher.data import DATA, START, END, key, load_catalog
from matcher.ai import AIService, AIUnavailable
from matcher.demo import DEMOS, demo_request
from matcher.engine import (Request, select, failures, language_count_preference,
                            REASONS, WEIGHTS)
from matcher.explain import (comparison_rows, comparison_summary, explain,
                             limitations, money, recommendation_facts)

st.set_page_config(page_title="Подбор · HackAlem", page_icon="◈", layout="wide")
st.html("<style>" + Path(__file__).with_name("ui.css").read_text(encoding="utf-8") + "</style>")


@st.cache_data
def read_catalog(modified):
    return load_catalog()


@st.cache_resource
def ai_service():
    return AIService()


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
    st.session_state.preference = ""
    for k, v in data.items():
        st.session_state[k] = list(v) if k == "languages" else float(v) if k == "hours" and v else v
    st.session_state.hours = float(data["hours"] or 0)
    st.session_state.request = demo_request(st.session_state.demo)
    st.session_state.pop("validation_error", None)


if "request" not in st.session_state:
    st.session_state.demo = next(iter(DEMOS))
    fill_demo()

DEMO_CARDS = {
    "Плотная категория · 14 ноября": ("01 / МНОГО ВАРИАНТОВ", "Корпоратив в Алматы", "Три рекомендации из плотной категории."),
    "Та же заявка · 15 ноября": ("04 / ДРУГАЯ ДАТА", "Смена даты", "Посмотрите, кто вошёл в выдачу после смены дня."),
    "Пожелание · деловой форум": ("05 / СМЫСЛ ЗАЯВКИ", "Деловой форум", "Проверьте совпадение пожелания с описанием."),
    "Редкая категория · флорист": ("02 / РЕДКАЯ КАТЕГОРИЯ", "Флорист", "Один подходящий профиль без искусственного топ-3."),
    "Никто не проходит · бюджет": ("03 / НЕТ СОВПАДЕНИЙ", "Строгий бюджет", "Честный пустой результат с причинами отказа."),
    "Категории нет · зарубежье": ("06 / НЕТ КАТЕГОРИИ", "Другой город", "Отдельное объяснение отсутствующей категории."),
}
PRIMARY_DEMOS = ("Плотная категория · 14 ноября", "Редкая категория · флорист",
                 "Никто не проходит · бюджет")
SECONDARY_DEMOS = ("Та же заявка · 15 ноября", "Пожелание · деловой форум",
                   "Категории нет · зарубежье")


def choose_demo(name):
    st.session_state.demo = name
    fill_demo()


def show_demo_card(name, index):
    eyebrow, title, description = DEMO_CARDS[name]
    with st.container(border=True, key=f"demo_card_{index}"):
        st.caption(eyebrow)
        st.markdown(f"### {title}")
        st.write(description)
        selected = st.session_state.demo == name
        st.button("Выбран" if selected else "Показать пример", key=f"demo_{index}",
                  on_click=choose_demo, args=(name,), type="primary" if selected else "secondary",
                  use_container_width=True)


with st.container(key="hero"):
    hero_text, hero_fact = st.columns([2.1, 1], vertical_alignment="center")
    with hero_text:
        st.markdown('<span class="hero-kicker">NURAVI / HACKALEM AI</span>', unsafe_allow_html=True)
        st.title("Подрядчик для вашего события — за минуту")
        st.write("Задайте условия. Сервис проверит занятость и бюджет, затем покажет до трёх вариантов с понятными причинами выбора.")
    with hero_fact:
        st.markdown('<div class="hero-note"><strong>До 3</strong><span>проверенных вариантов с объяснением для каждого</span></div>',
                    unsafe_allow_html=True)

st.subheader("Попробуйте готовый сценарий")
st.caption("Выберите пример — форма и результат обновятся сразу.")
demo_columns = st.columns(3, gap="medium")
for index, name in enumerate(PRIMARY_DEMOS):
    with demo_columns[index]:
        show_demo_card(name, list(DEMOS).index(name))
with st.expander("Ещё сценарии · дата, пожелание, город"):
    extra_columns = st.columns(3, gap="medium")
    for index, name in enumerate(SECONDARY_DEMOS):
        with extra_columns[index]:
            show_demo_card(name, list(DEMOS).index(name))

with st.container(border=True, key="event_form"):
    st.markdown("### Параметры события")
    st.caption("Обязательные поля определяют, кто может попасть в рекомендации.")
    row = st.columns([1, 1.2, 1])
    row[0].selectbox("Город", sorted({p.city for p in catalog.profiles}), key="city")
    row[1].selectbox("Категория", sorted({c for p in catalog.profiles for c in p.categories}), key="category")
    row[2].date_input("Дата", min_value=START, max_value=END, format="DD.MM.YYYY", key="date",
                      help="Подрядчики, занятые в выбранный день, исключаются из рекомендаций.")
    row = st.columns([1, 1])
    row[0].selectbox("Формат события", sorted({f for p in catalog.profiles for f in p.formats}), key="event_format")
    row[1].number_input("Бюджет, ₸", min_value=0, step=50000, key="budget")
    with st.expander("Уточнить подбор · язык, длительность и пожелание",
                     expanded=bool(st.session_state.request.preference)):
        optional = st.columns(2)
        optional[0].multiselect("Языки", sorted({l for p in catalog.profiles for l in p.languages}), key="languages", help="Подрядчик должен поддерживать все выбранные языки.")
        optional[1].number_input("Длительность, ч · 0 = не указана", min_value=0.0, step=0.5, key="hours")
        st.text_input("Что важно в подрядчике · необязательно", key="preference",
                      max_chars=240, placeholder="Например: спокойный стиль и опыт деловых мероприятий")
    st.toggle("Уточнить рекомендации с ИИ", key="ai_enabled",
              help="ИИ сравнивает описания с пожеланием. Условия события проверяются отдельно.")
    submitted = st.button("Подобрать подрядчиков", key="submit_query", type="primary", use_container_width=True)
    if submitted:
        try:
            st.session_state.request = Request(
                **{k: st.session_state[k] for k in ("city", "date", "event_format", "category", "budget", "languages", "preference")},
                hours=st.session_state.hours or None)
            st.session_state.pop("validation_error", None)
        except ValueError as exc:
            st.session_state.validation_error = str(exc)
    st.caption("Доступные даты: 23.09–31.12.2026. Язык, длительность и пожелание можно не указывать.")

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
ai_quotes = {}
ai_status = ""
ai_user_status = ""
if st.session_state.ai_enabled:
    service = ai_service()
    if not service.available:
        ai_status = "Ключи AI не найдены; показан локальный результат"
        ai_user_status = "ИИ недоступен: ключи не найдены. Показан обычный подбор."
    elif r["status"] == "found":
        if q.preference:
            eligible_profiles = [p for p in catalog.profiles if key(q.category) in
                                 {key(category) for category in p.categories}
                                 and key(p.city) == key(q.city) and not failures(p, q)]
            requested_languages = language_count_preference(q.preference)
            if requested_languages is not None:
                ai_status = f"Пожелание проверено по спискам языков профилей: требуется не менее {requested_languages}"
                ai_user_status = "Пожелание проверено по фактическому списку языков каждого профиля."
            else:
                ai_quotes, evidence_status = service.evidence([{"profile": p} for p in eligible_profiles], q)
                grounded = [p for p in eligible_profiles if p.id in ai_quotes]
                ai_scored = False
                if grounded:
                    try:
                        scores = service.semantic_scores(grounded, q.preference)
                        r = select(catalog, q, scores)
                        ai_scored = any(c["semantic_source"] == "AI embeddings" for c in r["candidates"])
                        ai_status = "Embeddings OpenAI рассчитаны для профилей с подтверждённой фразой"
                    except AIUnavailable as exc:
                        ai_status = f"Семантический API недоступен ({exc}); использован локальный поиск"
                ai_status = f"{ai_status}. {evidence_status}" if ai_status else evidence_status
                if ai_scored:
                    ai_user_status = "ИИ уточнил оценку по пожеланию; цитаты проверены по профилям."
                elif ai_quotes:
                    ai_user_status = "ИИ нашёл проверенные фразы; оценка рассчитана локально."
                else:
                    ai_user_status = "ИИ не нашёл подтверждения пожеланию. Показан локальный подбор."
        else:
            ai_status = "Укажите пожелание, чтобы ИИ сравнил смысл описаний"
            ai_user_status = "Чтобы ИИ уточнил подбор, добавьте пожелание к подрядчику."
        for candidate in r["candidates"]:
            candidate["ai_quote"] = ai_quotes.get(candidate["profile"].id)
    else:
        ai_status = "AI не вызывается: подходящих кандидатов нет"
st.subheader("Подходящие подрядчики")
st.markdown(f"**{q.category} · {q.city} · {q.date:%d.%m.%Y}**")
st.caption(f"{q.event_format.capitalize()} · {money(q.budget)} · {', '.join(q.languages) or 'язык не задан'} · {f'{q.hours:g} ч' if q.hours else 'длительность не задана'}")
if q.preference:
    st.caption(f"Пожелание: {q.preference}")
if ai_user_status:
    st.caption(ai_user_status)

with st.container(key="result_metrics"):
    metrics = st.columns(4)
    metrics[0].metric("В городе", r["city_count"])
    metrics[1].metric("Подходят", r["eligible"])
    metrics[2].metric("Показано", len(r["candidates"]))
    metrics[3].metric("Исключено", len(r["rejected"]))
st.caption("Оценка совпадения отражает условия этого запроса; это не рейтинг качества подрядчика.")

if r["status"] == "no_category":
    with st.container(border=True, key="empty_state"):
        st.markdown("### В этом городе нет нужной категории")
        st.warning(f"В городе «{q.city}» нет подрядчиков категории «{q.category}» в доступном каталоге.")
        st.write("Выберите другой город или категорию. Изменение бюджета и даты здесь не добавит профилей.")
elif r["status"] == "no_matches":
    with st.container(border=True, key="empty_state"):
        st.markdown("### Подходящих подрядчиков пока нет")
        st.warning(f"Профилей этой категории в городе: {r['city_count']}; никто не проходит все условия.")
        leading_reasons = sorted(((count, REASONS[code].lower()) for code, count in r["reasons"].items() if count), reverse=True)
        if leading_reasons:
            st.write("Основные причины: " + ", ".join(f"{label} — {count}" for count, label in leading_reasons[:3]) + ".")
        st.write("Попробуйте другую дату или скорректируйте ограничения.")
else:
    if r["eligible"] < 3:
        shortage = (f"Профилей этой категории в городе: {r['city_count']}."
                    if r["city_count"] == r["eligible"] else
                    f"Исключено: {len(r['rejected'])}; причины ниже.")
        st.info(f"Найдено {r['eligible']} из 3 вариантов. {shortage}")
    else:
        st.success(f"Подобраны 3 варианта из {r['eligible']} подходящих.")
    cards = st.columns(len(r["candidates"]), gap="medium")
    for i, (column, c) in enumerate(zip(cards, r["candidates"]), 1):
        p = c["profile"]
        with column.container(border=True, key=f"result_card_{i}"):
            rank = "ЛУЧШЕЕ СОВПАДЕНИЕ" if i == 1 else f"ВАРИАНТ {i}"
            st.markdown(f'<span class="result-rank">{rank}</span>', unsafe_allow_html=True)
            st.markdown(f"### {p.name}")
            st.caption(f"{p.id} · {' / '.join(p.categories)} · {p.city}")
            st.markdown(f'<div class="result-price">от {money(p.price)}</div>', unsafe_allow_html=True)
            st.markdown(f'<span class="result-badge">✓ Доступен {q.date:%d.%m.%Y}</span>'
                        '<span class="result-badge secondary">✓ Формат подходит</span>', unsafe_allow_html=True)
            st.progress(min(1.0, max(0.0, c["score"] / 100)), text=f"Совпадение с запросом · {c['score']:.1f} / 100")
            provenance = [label for condition, label in ((p.synthetic, "синтетический профиль"),
                          (p.city_imputed, "город восстановлен"), (p.price_imputed, "цена восстановлена")) if condition]
            if provenance:
                st.caption("Данные: " + " · ".join(provenance))
            st.markdown("**Почему подходит**")
            st.write(explain(c, q))
            if c.get("ai_quote"):
                st.caption(f"Проверенная фраза из описания: «{c['ai_quote']}»")
            with st.expander("Почему рекомендован"):
                for label, fact in recommendation_facts(c, q):
                    st.markdown(f"**{label}.** {fact}")
                st.markdown("**Состав оценки**")
                labels = {"format": "Формат", "language": "Язык", "budget": "Бюджет", "duration": "Длительность", "semantic": "Пожелание"}
                for key, base_weight in WEIGHTS.items():
                    value = c["breakdown"].get(key)
                    if value is None:
                        st.caption(f"{labels[key]}: не задано в запросе · не участвует в оценке")
                    else:
                        st.caption(f"{labels[key]}: {value['base_points']:.1f} из {base_weight} базовых баллов · подтверждено {value['value']:.0%}")
                st.caption("Итоговая оценка приводится к шкале 100 только по факторам, указанным в этом запросе.")
                notes = limitations(p)
                if notes:
                    st.markdown("**Ограничения**")
                    for note in notes:
                        st.caption(note)

    if len(r["candidates"]) >= 2:
        first, second = r["candidates"][:2]
        with st.container(border=True, key="comparison"):
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
    if st.session_state.ai_enabled and q.preference:
        st.caption("Для сравнения второй даты с ИИ выберите её в форме; здесь показан обычный подбор.")
    other_date = st.date_input("Сравнить с датой", value=q.date + timedelta(days=1) if q.date < END else q.date - timedelta(days=1), min_value=START, max_value=END, format="DD.MM.YYYY")
    other = select(catalog, replace(q, date=other_date))
    st.write(f"На {q.date:%d.%m}: подходят {r['eligible']}, заняты {r['reasons']['busy']}. На {other_date:%d.%m}: подходят {other['eligible']}, заняты {other['reasons']['busy']}.")
    first_ids = {c["profile"].id for c in r["candidates"]}
    second_ids = {c["profile"].id for c in other["candidates"]}
    profiles_by_id = {p.id: p for p in catalog.profiles}
    for pid in sorted(first_ids - second_ids):
        if other_date in profiles_by_id[pid].busy_dates:
            st.write(f"{profiles_by_id[pid].name} ({pid}) выбыл из выдачи на {other_date:%d.%m.%Y}: дата занята.")
        else:
            st.write(f"{profiles_by_id[pid].name} ({pid}) вышел из топ-3 на {other_date:%d.%m.%Y}: изменился состав доступных кандидатов.")
    for pid in sorted(second_ids - first_ids):
        if q.date in profiles_by_id[pid].busy_dates:
            st.write(f"{profiles_by_id[pid].name} ({pid}) вошёл в выдачу на {other_date:%d.%m.%Y}: на исходную дату он был занят.")
        else:
            st.write(f"{profiles_by_id[pid].name} ({pid}) вошёл в топ-3 на {other_date:%d.%m.%Y} после изменения состава доступных кандидатов.")
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
    st.write("Базовые веса: формат 25, язык 20, бюджет 20, длительность 15, пожелание 20. Неуказанные язык, длительность и пожелание показаны как неучаствующие; остальные веса приводятся к шкале 100 для итоговой оценки. Указанный максимум часов даёт баллы только при подтверждённой длительности.")
    st.write("Бюджет: после обязательной проверки предпочтение получает меньшая цена «от». Это не оценка качества подрядчика и не гарантия итоговой цены.")
    st.write("Пожелание: в локальном режиме сравниваются слова с фразами описаний. При включённом ИИ используется сходство embeddings; дословные фразы от LLM показываются только после проверки с исходным профилем и AI-аудита.")
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
        {"Параметр": "Пожелание", "Значение": q.preference or "не указано"},
    ])
    payload = {"request": asdict(q), "status": r["status"], "funnel": r["funnel"], "reasons": r["reasons"],
               "rejected": r["rejected"], "eligible": r["eligible"], "data_issues": catalog.issues,
               "ai_status": ai_status, "candidates": [{"profile": asdict(c["profile"]), "score": c["score"], "breakdown": c["breakdown"], "evidence": c["evidence"], "semantic_source": c["semantic_source"], "ai_quote": c.get("ai_quote"), "explanation": explain(c, q), "recommendation_facts": recommendation_facts(c, q), "limitations": limitations(c["profile"])} for c in r["candidates"]]}
    if len(r["candidates"]) >= 2:
        payload["top_comparison"] = {"summary": comparison_summary(*r["candidates"][:2]),
                                     "components": comparison_rows(*r["candidates"][:2], q)}
    def serialize(value):
        if isinstance(value, (set, frozenset)):
            return sorted(str(x) for x in value)
        return str(value)
    st.download_button("Скачать аудит JSON", json.dumps(payload, ensure_ascii=False, indent=2, default=serialize), "audit.json", "application/json")
