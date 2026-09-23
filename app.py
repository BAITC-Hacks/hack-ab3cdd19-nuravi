from dataclasses import asdict, replace
from datetime import timedelta
import json

import streamlit as st

from matcher.data import DATA, START, END, FIELDS, load_catalog
from matcher.demo import DEMOS, demo_request
from matcher.engine import Request, select, REASONS
from matcher.explain import explain, limitations, money

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

with st.sidebar:
    st.markdown("### Параметры мероприятия")
    st.selectbox("Демо-сценарий", list(DEMOS), key="demo", on_change=fill_demo)
    with st.form("request_form"):
        st.selectbox("Город", sorted({p.city for p in catalog.profiles}), key="city")
        st.selectbox("Категория подрядчика", sorted({c for p in catalog.profiles for c in p.categories}), key="category")
        st.date_input("Дата мероприятия", min_value=START, max_value=END, format="DD.MM.YYYY", key="date")
        st.selectbox("Тип мероприятия", sorted({f for p in catalog.profiles for f in p.formats}), key="event_format")
        st.number_input("Бюджет, ₸", min_value=0, step=50000, key="budget")
        st.multiselect("Языки · необязательно", sorted({l for p in catalog.profiles for l in p.languages}), key="languages", help="Подрядчик должен поддерживать все выбранные языки.")
        st.number_input("Длительность, ч · 0 = не указана", min_value=0.0, step=0.5, key="hours")
        submitted = st.form_submit_button("Подобрать подрядчиков", type="primary", width="stretch")
    if submitted:
        try:
            st.session_state.request = Request(
                **{k: st.session_state[k] for k in ("city", "date", "event_format", "category", "budget", "languages")},
                hours=st.session_state.hours or None)
            st.session_state.pop("validation_error", None)
        except ValueError as exc:
            st.session_state.validation_error = str(exc)
    st.caption("Календарь: 23.09–31.12.2026. Даты вне этого окна не поддерживаются.")
    st.caption("Объяснения работают локально, без API и отправки данных.")

st.caption("HACKALEM / УМНЫЙ ПОДБОР ПОДРЯДЧИКОВ")
st.title("Подрядчики. С понятными причинами.")
st.write("До трёх вариантов из каталога — с фактами, ограничениями и проверяемым решением.")
st.caption(f"{len(catalog.profiles)} профилей · {sum(p.synthetic for p in catalog.profiles)} синтетических · имена вымышленные")
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
st.caption(f"{q.event_format.capitalize()} · бюджет {money(q.budget)} · языки: {', '.join(q.languages) or 'не заданы'} · длительность: {str(q.hours) + ' ч' if q.hours else 'не задана'}")
st.caption("Результаты относятся к параметрам выше. После изменения формы нажмите «Подобрать подрядчиков».")

metrics = st.columns(3)
metrics[0].metric("В категории и городе", r["city_count"])
metrics[1].metric("Прошли все условия", r["eligible"])
metrics[2].metric("Показано вариантов", len(r["candidates"]))

if r["status"] == "no_category":
    st.warning(f"В городе «{q.city}» нет подрядчиков категории «{q.category}» в доступном каталоге.")
    st.write("Выберите другой город или категорию. Ослабление бюджета и даты здесь не добавит профилей.")
elif r["status"] == "no_matches":
    st.warning(f"В городе есть {r['city_count']} профилей этой категории, но никто не проходит все условия.")
    st.write("Причины и количество исключённых профилей приведены ниже. Попробуйте другую дату или скорректируйте ограничения.")
else:
    if r["eligible"] < 3:
        st.info(f"Найдено только {r['eligible']} из 3 вариантов: в категории и городе {r['city_count']} профилей, исключено {len(r['rejected'])}. Причины — ниже.")
    else:
        st.success(f"Подобраны 3 варианта из {r['eligible']} подходящих.")
    for i, c in enumerate(r["candidates"], 1):
        p = c["profile"]
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.subheader(f"{i:02d} / {p.name}")
            left.caption(f"{p.id} · {' / '.join(p.categories)} · {p.city}")
            right.metric("Оценка соответствия", f"{c['score']:.1f} / 100")
            st.markdown(f"**От {money(p.price)}** · {q.date:%d.%m.%Y}: свободен по календарю датасета")
            st.write(explain(c, q))
            for note in limitations(p):
                st.caption(note)
            with st.expander(f"Проверить факты и баллы · {p.id}"):
                labels = {"format": "Формат", "language": "Язык", "budget": "Близость цены к бюджету", "duration": "Длительность", "semantic": "Совпадения в описании"}
                st.table([{"Фактор": labels[k], "Вес": v["weight"], "Подтверждение (0–1)": round(v["value"], 3), "Вклад в итог": round(v["points"], 2)} for k, v in c["breakdown"].items()])
                st.write("Словарные совпадения в description:", c["evidence"] or "Не найдены")
                st.text(p.description or "Описание отсутствует")
                st.json({"id": p.id, "event_formats": p.formats, "languages": p.languages,
                         "max_hours": str(p.max_hours) if p.max_hours is not None else None,
                         "busy_dates": sorted(d.isoformat() for d in p.busy_dates),
                         "synthetic": p.synthetic, "city_imputed": p.city_imputed, "price_imputed": p.price_imputed})

st.subheader("Почему другие не попали")
col1, col2 = st.columns(2)
with col1:
    st.caption("Последовательная воронка: каждый шаг применяется к предыдущему")
    st.table([{"Этап": stage, "Профилей осталось": count} for stage, count in r["funnel"]])
with col2:
    st.caption("Все причины среди профилей выбранной категории и города")
    st.table([{"Причина": label, "Профилей": r["reasons"][code]} for code, label in REASONS.items()])
    st.caption("У профиля может быть несколько причин; сумма причин может превышать число исключённых профилей.")
    st.write(f"Другой город: {r['category_count'] - r['city_count']}. Прошли условия, но ниже топ-3: {r['not_shown']}.")

with st.expander("Какие именно профили исключены"):
    names = {p.id: p.name for p in catalog.profiles}
    st.table([{"ID": pid, "Имя": names[pid], "Причины": "; ".join(REASONS[x] for x in reasons)} for pid, reasons in sorted(r["rejected"].items())]) if r["rejected"] else st.write("Нет исключений по дополнительным условиям.")

with st.expander("Что меняется на другой дате", expanded=False):
    other_date = st.date_input("Сравнить с датой", value=q.date + timedelta(days=1) if q.date < END else q.date - timedelta(days=1), min_value=START, max_value=END, format="DD.MM.YYYY")
    other = select(catalog, replace(q, date=other_date))
    st.write(f"На {q.date:%d.%m}: подходят {r['eligible']}, заняты {r['reasons']['busy']}. На {other_date:%d.%m}: подходят {other['eligible']}, заняты {other['reasons']['busy']}.")
    st.write("Топ на второй дате: " + (", ".join(f"{c['profile'].name} ({c['profile'].id})" for c in other["candidates"]) or "никто не проходит"))
    changes = [{"ID": p.id, "Имя": p.name, f"{q.date:%d.%m.%Y}": "занят" if q.date in p.busy_dates else "свободен", f"{other_date:%d.%m.%Y}": "занят" if other_date in p.busy_dates else "свободен"}
               for p in catalog.profiles if p.city == q.city and q.category in p.categories
               and (q.date in p.busy_dates) != (other_date in p.busy_dates)]
    if changes:
        st.table(changes)
    else:
        st.caption("Занятость этой категории не изменилась; выдача может остаться той же.")

with st.expander("Как принято решение · аудит подбора"):
    st.write("Жёсткие фильтры: категория → город → дата → бюджет → формат → все выбранные языки → длительность. max_hours=null не исключает профиль.")
    st.write("Веса: формат 25%, язык 20%, бюджет 20%, длительность 15%, текст 20%. Неуказанные язык и длительность исключаются из знаменателя; оставшиеся веса нормируются к 100. При указанной длительности max_hours=null даёт 0 баллов за этот фактор.")
    st.write("Бюджет: цена / бюджет. Среди допустимых цен выше балл у цены ближе к бюджету; это не оценка качества и не рекомендация потратить больше.")
    st.write("Текст: доля подтверждённых групп «формат» и выбранных языков по явным словам в description. Стиль, масштаб, аудитория и опыт без соответствующих параметров запроса не дают бонусов. Рекламные заявления не считаются рейтингом.")
    st.write("При равных баллах: больше подтверждений → меньшая цена → меньшая доля занятых дней из 100 → id по алфавиту. Доступность не входит в балл; доля занятых дней используется только как поздний tie-breaker.")
    st.write("Поля CSV:", ", ".join(sorted(FIELDS)))
    st.write("Флаги synthetic, city_imputed, price_imputed не повышают баллы и показаны в ограничениях каждой карточки. Оценка — эвристика соответствия, не вероятность успеха и не рейтинг надёжности.")
    st.json(asdict(q), expanded=False)
    payload = {"request": asdict(q), "status": r["status"], "funnel": r["funnel"], "reasons": r["reasons"],
               "rejected": r["rejected"], "eligible": r["eligible"], "data_issues": catalog.issues,
               "candidates": [{"profile": asdict(c["profile"]), "score": c["score"], "breakdown": c["breakdown"], "evidence": c["evidence"], "explanation": explain(c, q), "limitations": limitations(c["profile"])} for c in r["candidates"]]}
    def serialize(value):
        if isinstance(value, (set, frozenset)):
            return sorted(str(x) for x in value)
        return str(value)
    st.download_button("Скачать аудит JSON", json.dumps(payload, ensure_ascii=False, indent=2, default=serialize), "audit.json", "application/json")
