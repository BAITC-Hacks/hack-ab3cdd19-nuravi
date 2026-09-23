"""Template-only factual explanations; never influences eligibility or ranking."""

from .text import best_evidence, sentences


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
    return f"Базовый вклад: {component['base_points']:.1f} из {component['weight']} баллов."


def availability_fact(profile, event_date):
    if not profile.busy_dates:
        return "В профиле нет данных о занятых датах — доступность требует подтверждения."
    if event_date not in profile.busy_dates:
        return f"На {event_date:%d.%m.%Y} дата отсутствует в списке занятых дат — подрядчик считается доступным."
    return f"На {event_date:%d.%m.%Y} дата присутствует в списке занятых дат — подрядчик недоступен."


def recommendation_facts(candidate, q):
    """Facts displayed on a result card. They only use request/profile/score data."""
    p = candidate["profile"]
    supported_languages = {language.casefold() for language in p.languages}
    matched = candidate["evidence"][0]["match"] if candidate["evidence"] else ""
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
    if not q.preference:
        relevance = "Пожелание в запросе не указано; описание не влияло на порядок."
    elif candidate.get("semantic_source") == "AI embeddings":
        quote = candidate.get("ai_quote")
        relevance = (f"Сходство описания с пожеланием: {candidate['breakdown']['semantic']['value']:.2f} из 1. "
                     + (f"Проверенная фраза профиля: «{quote}». " if quote else
                        "Дословная фраза через AI-аудит не подтверждена. ")
                     + _score_suffix(candidate, 'semantic'))
    elif matched and candidate["evidence"][0].get("field") == "languages":
        relevance = f"Пожелание подтверждено языками профиля: {matched}. {_score_suffix(candidate, 'semantic')}"
    elif matched:
        relevance = f"Часть слов пожелания найдена в описании: «{matched}». {_score_suffix(candidate, 'semantic')}"
    elif p.description:
        relevance = f"В описании нет словесного подтверждения пожеланию «{q.preference}». {_score_suffix(candidate, 'semantic')}"
    else:
        relevance = f"Описание отсутствует; смысловая релевантность не подтверждена. {_score_suffix(candidate, 'semantic')}"
    facts.append(("Релевантность описания", relevance))
    return facts


def explain(candidate, q):
    p = candidate["profile"]
    hit = candidate["evidence"]
    if hit and hit[0].get("field") == "languages":
        excerpt = ""
        reason = f"Пожелание подтверждено данными профиля: {hit[0]['match']}"
    elif hit:
        excerpt = hit[0]["match"]
    else:
        _, format_hit = best_evidence(p.description, q.event_format)
        passages = sentences(p.description)
        informative = next((sentence for sentence in passages
                            if len(sentence) >= 20 and not sentence.casefold().startswith(
                                ("приветствую", "добрый день", "здравствуйте"))), "")
        excerpt = format_hit[0]["match"] if format_hit else informative or (passages or [""])[0]
    if len(excerpt) > 210:
        matched_terms = hit[0]["terms"] if hit else []
        positions = [excerpt.casefold().find(term) for term in matched_terms]
        positions = [position for position in positions if position >= 0]
        start = max(0, min(positions) - 60) if positions else 0
        shortened = excerpt[start:start + 210].rsplit(" ", 1)[0]
        excerpt = ("…" if start else "") + shortened + ("…" if start + 210 < len(excerpt) else "")
    excerpt = excerpt.rstrip(" .!?")
    if not (hit and hit[0].get("field") == "languages"):
        reason = f"В описании {p.id}: «{excerpt}»" if excerpt else "Описание профиля отсутствует"
    if q.preference and not hit:
        reason += "; подтверждения пожеланию не найдено"
    return (f"На {q.date:%d.%m.%Y} доступен по календарю; формат «{q.event_format}» подходит, цена от {money(p.price)} укладывается в бюджет. "
            f"{reason}.")


def comparison_rows(first, second, q):
    """Five score components for a factual top-1 versus top-2 comparison."""
    a, b = first["profile"], second["profile"]

    def component(candidate, factor, detail):
        value = candidate["breakdown"].get(factor)
        if value is None:
            return f"{detail}; не влиял на оценку"
        return f"{detail}; {value['base_points']:.1f}/{value['weight']} базовых баллов"

    requested_languages = ", ".join(q.languages) or "не заданы"
    if q.hours is None:
        duration_a = duration_b = "не задана"
    else:
        duration_a = f"{q.hours:g} ч ≤ {a.max_hours:g} ч" if a.max_hours is not None else f"{q.hours:g} ч; максимум часов не указан"
        duration_b = f"{q.hours:g} ч ≤ {b.max_hours:g} ч" if b.max_hours is not None else f"{q.hours:g} ч; максимум часов не указан"
    evidence_a = first["evidence"][0]["match"] if first["evidence"] else "совпадений нет"
    evidence_b = second["evidence"][0]["match"] if second["evidence"] else "совпадений нет"
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
        changes = []
        for factor in FACTOR_LABELS:
            delta = (_points(first, factor) or 0) - (_points(second, factor) or 0)
            if abs(delta) > 1e-9:
                changes.append(f"{FACTOR_LABELS[factor]} {delta:+.1f}")
        detail = ", ".join(changes) or "сумма компонентов оценки"
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
