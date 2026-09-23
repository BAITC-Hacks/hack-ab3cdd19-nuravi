"""Template-only factual explanations; never influences eligibility or ranking."""
import re


FACTOR_LABELS = {
    "format": "Формат",
    "language": "Язык",
    "budget": "Бюджет",
    "duration": "Длительность",
    "semantic": "Релевантность описания",
}


def money(value):
    return f"{value:,.0f}".replace(",", " ") + " ₸"


def _points(candidate, factor):
    component = candidate["breakdown"].get(factor)
    return None if component is None else component["points"]


def _score_suffix(candidate, factor):
    component = candidate["breakdown"].get(factor)
    if component is None:
        return "Фактор не влиял на итоговую оценку."
    return f"Вклад в итоговую оценку: {component['points']:.1f} из {component['weight']} баллов."


def availability_fact(profile, event_date):
    if not profile.busy_dates:
        return "В профиле нет данных о занятых датах — доступность требует подтверждения."
    if event_date not in profile.busy_dates:
        return "Дата отсутствует в списке занятых дат — подрядчик считается доступным."
    return "Дата присутствует в списке занятых дат — подрядчик недоступен."


def recommendation_facts(candidate, q):
    """Facts displayed on a result card. They only use request/profile/score data."""
    p = candidate["profile"]
    supported_languages = {language.casefold() for language in p.languages}
    matched = ", ".join(hit["match"] for hit in candidate["evidence"])
    facts = [
        ("Категория", f"«{q.category}» присутствует в категориях профиля: {', '.join(p.categories)}."),
        ("Город", f"Город профиля — {p.city}; он совпадает с городом запроса {q.city}."),
        ("Доступность", availability_fact(p, q.date)),
        ("Формат", f"Формат «{q.event_format}» есть в списке поддерживаемых форматов. {_score_suffix(candidate, 'format')}"),
    ]
    if q.languages:
        requested = ", ".join(q.languages)
        profile_languages = ", ".join(p.languages)
        all_supported = all(language in supported_languages for language in q.languages)
        status = "поддерживаются" if all_supported else "поддерживаются не полностью"
        facts.append(("Язык", f"Запрошенные языки ({requested}) {status}; языки профиля: {profile_languages}. {_score_suffix(candidate, 'language')}"))
    else:
        facts.append(("Язык", f"Язык в запросе не указан; языки профиля: {', '.join(p.languages)}. Фактор не влиял на итоговую оценку."))
    facts.append(("Бюджет", f"Цена от {money(p.price)} укладывается в бюджет {money(q.budget)}; запас — {money(q.budget - p.price)}. {_score_suffix(candidate, 'budget')}"))
    if q.hours is None:
        duration = "Длительность в запросе не указана. Фактор не влиял на итоговую оценку."
    elif p.max_hours is None:
        duration = f"Запрошено {q.hours:g} ч, но в профиле не указан максимум часов: соответствие длительности не подтверждено. {_score_suffix(candidate, 'duration')}"
    else:
        duration = f"Запрошено {q.hours:g} ч при максимуме профиля {p.max_hours:g} ч. {_score_suffix(candidate, 'duration')}"
    facts.append(("Длительность", duration))
    if matched:
        relevance = f"В описании профиля найдены явные совпадения: {matched}. {_score_suffix(candidate, 'semantic')}"
    elif p.description:
        relevance = f"В описании профиля не найдены явные совпадения с форматом и выбранными языками. {_score_suffix(candidate, 'semantic')}"
    else:
        relevance = f"Описание отсутствует; смысловая релевантность не подтверждена. {_score_suffix(candidate, 'semantic')}"
    facts.append(("Релевантность описания", relevance))
    return facts


def explain(candidate, q):
    p = candidate["profile"]
    # Exact, attributed excerpt distinguishes descriptions without endorsing advertising claims.
    first_sentence = re.split(r"(?<=[.!?])\s+", p.description, maxsplit=1)[0]
    excerpt = first_sentence[:210]
    if len(first_sentence) > 210:
        excerpt = excerpt.rsplit(" ", 1)[0] + "…"
    evidence = f'В описании {p.id} указано: «{excerpt}»' if excerpt else f"У {p.id} описание отсутствует; смысловая релевантность не подтверждена."
    return (f"Цена от {money(p.price)} укладывается в бюджет {money(q.budget)}; формат «{q.event_format}» указан среди поддерживаемых. "
            f"{evidence}")


def comparison_rows(first, second, q):
    """Five score components for a factual top-1 versus top-2 comparison."""
    a, b = first["profile"], second["profile"]

    def component(candidate, factor, detail):
        value = candidate["breakdown"].get(factor)
        if value is None:
            return f"{detail}; не влиял на оценку"
        return f"{detail}; {value['points']:.1f}/{value['weight']} баллов"

    requested_languages = ", ".join(q.languages) or "не заданы"
    if q.hours is None:
        duration_a = duration_b = "не задана"
    else:
        duration_a = f"{q.hours:g} ч ≤ {a.max_hours:g} ч" if a.max_hours is not None else f"{q.hours:g} ч; максимум часов не указан"
        duration_b = f"{q.hours:g} ч ≤ {b.max_hours:g} ч" if b.max_hours is not None else f"{q.hours:g} ч; максимум часов не указан"
    evidence_a = ", ".join(hit["match"] for hit in first["evidence"]) or "совпадений нет"
    evidence_b = ", ".join(hit["match"] for hit in second["evidence"]) or "совпадений нет"
    return [
        {"Фактор": "Формат", a.id: component(first, "format", f"«{q.event_format}» поддерживается"), b.id: component(second, "format", f"«{q.event_format}» поддерживается")},
        {"Фактор": "Язык", a.id: component(first, "language", f"{requested_languages}; профиль: {', '.join(a.languages)}"), b.id: component(second, "language", f"{requested_languages}; профиль: {', '.join(b.languages)}")},
        {"Фактор": "Бюджет", a.id: component(first, "budget", f"{money(a.price)} из {money(q.budget)}"), b.id: component(second, "budget", f"{money(b.price)} из {money(q.budget)}")},
        {"Фактор": "Длительность", a.id: component(first, "duration", duration_a), b.id: component(second, "duration", duration_b)},
        {"Фактор": "Смысловая релевантность", a.id: component(first, "semantic", evidence_a), b.id: component(second, "semantic", evidence_b)},
    ]


def comparison_summary(first, second):
    a, b = first["profile"], second["profile"]
    difference = first["score"] - second["score"]
    if difference > 1e-9:
        advantages = []
        for factor in FACTOR_LABELS:
            delta = (_points(first, factor) or 0) - (_points(second, factor) or 0)
            if delta > 1e-9:
                advantages.append(f"{FACTOR_LABELS[factor]} +{delta:.1f}")
        detail = ", ".join(advantages) or "сумма компонентов оценки"
        return f"{a.id} выше {b.id} на {difference:.1f} балла: {detail}."
    first_matches, second_matches = -first["sort_key"][1], -second["sort_key"][1]
    if first_matches != second_matches:
        return f"Итоговая оценка одинаковая ({first['score']:.1f}), но у {a.id} больше подтверждений: {first_matches} против {second_matches}."
    if a.price != b.price:
        return f"Оценка и число подтверждений одинаковы; {a.id} выше по дополнительному правилу меньшей цены: {money(a.price)} против {money(b.price)}."
    first_busy, second_busy = first["sort_key"][3], second["sort_key"][3]
    if first_busy != second_busy:
        return f"Оценка, подтверждения и цена одинаковы; {a.id} выше из-за меньшей доли занятых дат: {float(first_busy):.0%} против {float(second_busy):.0%}."
    return f"Все числовые критерии равны; {a.id} выше {b.id} по последнему правилу стабильной сортировки — порядку идентификаторов."


def limitations(p):
    notes = ["Цена «от» — итоговую стоимость необходимо уточнить."]
    if not p.busy_dates:
        notes.append("В профиле нет данных о занятых датах — доступность требует подтверждения.")
    if p.max_hours is None:
        notes.append("Максимум часов в профиле не указан; длительность не подтверждена и не даёт баллов.")
    else:
        notes.append(f"Максимум на площадке: {p.max_hours:g} ч.")
    if p.synthetic:
        notes.append("Профиль помечен в исходном каталоге как полностью синтетический.")
    if p.city_imputed:
        notes.append("Город был добавлен при подготовке исходного каталога.")
    if p.price_imputed:
        notes.append("Цена была добавлена при подготовке исходного каталога.")
    return notes
