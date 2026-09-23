from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from .data import START, END, DAYS, clean, key, number
from .text import best_evidence, document_frequency

REASONS = {"availability_unknown": "Нет данных о занятости", "busy": "Заняты на дату", "budget": "Выше бюджета", "format": "Не работают с форматом",
           "language": "Не подходят по языку", "duration": "Не подходят по длительности"}
# Stable, human-readable base weights. Active weights are normalized only for
# the final 0–100 score when a user leaves optional fields blank.
WEIGHTS = {"format": 25, "language": 20, "budget": 20, "duration": 15, "semantic": 20}


@dataclass(frozen=True)
class Request:
    city: str
    date: date
    event_format: str
    category: str
    budget: Decimal
    hours: object = None
    languages: tuple = ()
    preference: str = ""

    def __post_init__(self):
        for f in ("city", "event_format", "category"):
            if not clean(getattr(self, f)):
                raise ValueError("Заполните город, категорию и формат")
            object.__setattr__(self, f, clean(getattr(self, f)))
        if type(self.date) is not date or not START <= self.date <= END:
            raise ValueError("Дата вне календаря: выберите 23.09.2026–31.12.2026. За пределами окна доступность неизвестна.")
        object.__setattr__(self, "budget", number(self.budget, "Бюджет"))
        if self.hours is not None:
            object.__setattr__(self, "hours", number(self.hours, "Длительность"))
        langs = (self.languages,) if isinstance(self.languages, str) else self.languages
        object.__setattr__(self, "languages", tuple(sorted({key(x) for x in langs if clean(x)})))
        preference = clean(self.preference)
        if len(preference) > 240:
            raise ValueError("Пожелание: не более 240 символов")
        object.__setattr__(self, "preference", preference)


def failures(p, q):
    return tuple(k for k, fail in (
        ("availability_unknown", not p.busy_dates),
        ("busy", bool(p.busy_dates) and q.date in p.busy_dates),
        ("budget", p.price > q.budget),
        ("format", key(q.event_format) not in {key(v) for v in p.formats}),
        ("language", not set(q.languages).issubset({key(v) for v in p.languages})),
        ("duration", q.hours is not None and p.max_hours is not None and q.hours > p.max_hours)
    ) if fail)


def language_count_preference(value):
    """Return an explicit requested number of languages, if present."""
    return language_preference(value)[0]


def language_preference(value):
    """Split a language-count fact from any other requested qualities."""
    normalized = key(value)
    match = re.search(
        r"\b(?:(?:знани\w*|владени\w*|владе\w*|работа\w*\s+с)\s+)?"
        r"(\d+|двух|двумя|трех|тремя|четырех|четырьмя|пяти)\s+язык\w*\b",
        normalized,
    )
    if not match:
        return None, normalized
    words = {"двух": 2, "двумя": 2, "трех": 3, "тремя": 3,
             "четырех": 4, "четырьмя": 4, "пяти": 5}
    count = words.get(match.group(1), int(match.group(1)) if match.group(1).isdigit() else None)
    remainder = (normalized[:match.start()] + " " + normalized[match.end():]).strip(" ,.;:")
    remainder = re.sub(r"^(?:и|а также)\s+", "", remainder).strip()
    return count, remainder


def semantic_evidence(p, q, frequency=None):
    requested_count, remainder = language_preference(q.preference)
    if requested_count is not None:
        if len(p.languages) < requested_count:
            return Decimal(0), []
        fact = f"Профиль поддерживает {len(p.languages)} языка: {', '.join(p.languages)}"
        language_hit = {"factor": "Пожелание", "match": fact,
                        "terms": [str(requested_count)], "field": "languages"}
        if not remainder:
            return Decimal(1), [language_hit]
        relevance, text_hits = best_evidence(p.description, remainder, frequency)
        return relevance, [language_hit, *text_hits] if relevance else []
    return best_evidence(p.description, q.preference, frequency)


def score(p, q, frequency=None, semantic_override=None):
    semantic, hits = semantic_evidence(p, q, frequency)
    requested_count, remainder = language_preference(q.preference)
    if q.preference and semantic_override is not None and remainder and \
            (requested_count is None or len(p.languages) >= requested_count):
        ai_semantic = Decimal(str(semantic_override))
        if not ai_semantic.is_finite() or not 0 <= ai_semantic <= 1:
            raise ValueError("Семантическая оценка должна быть от 0 до 1")
        semantic = max(semantic, ai_semantic)
    active = {k: v for k, v in WEIGHTS.items() if not (k == "language" and not q.languages)
              and not (k == "duration" and q.hours is None) and not (k == "semantic" and not q.preference)}
    raw = {"format": Decimal(1), "language": Decimal(1), "budget": 1 - p.price / q.budget,
           "duration": Decimal(p.max_hours is not None), "semantic": semantic}
    denominator = Decimal(sum(active.values()))
    breakdown = {k: {"weight": w, "max_points": float(Decimal(w) * 100 / denominator),
                     "base_points": float(raw[k] * w), "value": float(raw[k]),
                     "points": float(raw[k] * w * 100 / denominator)} for k, w in active.items()}
    exact = sum(raw[k] * w for k, w in active.items()) * 100 / denominator
    matches = 1 + len(q.languages) + int(q.hours is not None and p.max_hours is not None) + len(hits)
    return exact, breakdown, hits, matches


def select(catalog, q, semantic_scores=None):
    category = [p for p in catalog.profiles if key(q.category) in {key(c) for c in p.categories}]
    city = [p for p in category if key(p.city) == key(q.city)]
    rejected = {p.id: failures(p, q) for p in city}
    counts = Counter(reason for reasons in rejected.values() for reason in reasons)
    funnel = [("В категории", len(category)), ("В городе", len(city))]
    remaining = city
    for reason, label in REASONS.items():
        remaining = [p for p in remaining if reason not in rejected[p.id]]
        funnel.append(("Есть данные о занятости" if reason == "availability_unknown" else
                       label.replace("Заняты на дату", "Доступны на дату") if reason == "busy" else
                       {"budget": "Прошли бюджет", "format": "Прошли формат", "language": "Прошли язык", "duration": "Прошли длительность"}[reason], len(remaining)))
    ranked = []
    # Keep text weights stable when only the date, budget, or availability changes.
    frequency = document_frequency(city) if q.preference else None
    for p in remaining:
        override = semantic_scores.get(p.id) if semantic_scores is not None else None
        total, breakdown, hits, matches = score(p, q, frequency, override)
        local_semantic = semantic_evidence(p, q, frequency)[0] if override is not None else None
        requested_count, remainder = language_preference(q.preference)
        ai_used = (override is not None and bool(remainder)
                   and (requested_count is None or len(p.languages) >= requested_count)
                   and Decimal(str(override)) > local_semantic)
        ranked.append({"profile": p, "score": float(total), "breakdown": breakdown, "evidence": hits,
                       "semantic_source": "AI embeddings" if ai_used else "локальный поиск",
                       "sort_key": (-total, -matches, p.price, Decimal(len(p.busy_dates)) / DAYS, p.id)})
    ranked.sort(key=lambda c: c["sort_key"])
    return {"status": "no_category" if not city else "no_matches" if not ranked else "found",
            "candidates": ranked[:3], "eligible": len(ranked), "city_count": len(city),
            "category_count": len(category), "funnel": funnel,
            "reasons": {r: counts[r] for r in REASONS},
            "rejected": {pid: rs for pid, rs in rejected.items() if rs},
            "not_shown": max(0, len(ranked) - 3)}
