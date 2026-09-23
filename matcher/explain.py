"""Template-only factual explanations; never influences eligibility or ranking."""
import re


def money(value):
    return f"{value:,.0f}".replace(",", " ") + " ₸"


def explain(candidate, q):
    p = candidate["profile"]
    facts = [f"цена от {money(p.price)} при бюджете {money(q.budget)}",
             f"на {q.date:%d.%m.%Y} занятость не указана", f"формат «{q.event_format}» есть в профиле"]
    if q.languages:
        facts.append("языки: " + ", ".join(q.languages))
    if q.hours is not None and p.max_hours is not None:
        facts.append(f"запрос {q.hours:g} ч при максимуме {p.max_hours:g} ч")
    # Exact, attributed excerpt distinguishes descriptions without endorsing advertising claims.
    first_sentence = re.split(r"(?<=[.!?])\s+", p.description, maxsplit=1)[0]
    excerpt = first_sentence[:210]
    if len(first_sentence) > 210:
        excerpt = excerpt.rsplit(" ", 1)[0] + "…"
    evidence = f' В описании {p.id} указано: «{excerpt}»' if excerpt else f" У {p.id} описание отсутствует; смысловая релевантность не подтверждена."
    return "; ".join(facts).capitalize() + "." + evidence


def limitations(p):
    notes = ["Цена «от» — итоговую стоимость необходимо уточнить."]
    if p.max_hours is None:
        notes.append("max_hours=null: ограничение неприменимо; длительность не подтверждена и не даёт баллов.")
    else:
        notes.append(f"Максимум на площадке: {p.max_hours:g} ч.")
    if p.synthetic:
        notes.append("synthetic=true: полностью синтетический профиль из исходного датасета.")
    if p.city_imputed:
        notes.append("city_imputed=true: город проставлен при подготовке данных.")
    if p.price_imputed:
        notes.append("price_imputed=true: цена проставлена при подготовке данных.")
    return notes
