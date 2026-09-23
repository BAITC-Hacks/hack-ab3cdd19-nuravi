from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from .data import START, END, DAYS, clean, key, number

REASONS = {"busy": "Заняты на дату", "budget": "Выше бюджета", "format": "Не работают с форматом",
           "language": "Не подходят по языку", "duration": "Не подходят по длительности"}
WEIGHTS = {"format": 25, "language": 20, "budget": 20, "duration": 15, "semantic": 20}
# Only literal word stems in the description count. No inferred quality or experience.
TERMS = {"свадьба": ("свад", "бракосочет"), "той": ("той",),
         "корпоратив": ("корпоратив",), "конференция": ("конференц", "форум"),
         "юбилей": ("юбиле",), "день рождения": ("день рождения", "дня рождения", "дни рождения")}
LANG_TERMS = {"русский": ("русск",), "казахский": ("казах",), "английский": ("англий",)}


@dataclass(frozen=True)
class Request:
    city: str
    date: date
    event_format: str
    category: str
    budget: Decimal
    hours: object = None
    languages: tuple = ()

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


def failures(p, q):
    return tuple(k for k, fail in (
        ("busy", q.date in p.busy_dates), ("budget", p.price > q.budget),
        ("format", key(q.event_format) not in {key(v) for v in p.formats}),
        ("language", not set(q.languages).issubset({key(v) for v in p.languages})),
        ("duration", q.hours is not None and p.max_hours is not None and q.hours > p.max_hours)
    ) if fail)


def semantic_evidence(p, q):
    text = key(p.description)
    groups = [("Формат в описании", TERMS.get(key(q.event_format), (key(q.event_format),)))]
    groups += [(f"Язык в описании: {lang}", LANG_TERMS.get(lang, (lang,))) for lang in q.languages]
    hits = []
    for label, terms in groups:
        for term in terms:
            match = re.search(r"(?<!\w)" + re.escape(term) + r"\w*", text)
            if match:
                hits.append({"factor": label, "match": match.group(0), "field": "description"})
                break
    return Decimal(len(hits)) / Decimal(len(groups)), hits


def score(p, q):
    semantic, hits = semantic_evidence(p, q)
    active = {k: v for k, v in WEIGHTS.items() if not (k == "language" and not q.languages)
              and not (k == "duration" and q.hours is None)}
    raw = {"format": Decimal(1), "language": Decimal(1), "budget": p.price / q.budget,
           "duration": Decimal(p.max_hours is not None), "semantic": semantic}
    denominator = Decimal(sum(active.values()))
    breakdown = {k: {"weight": w, "value": float(raw[k]),
                     "points": float(raw[k] * w * 100 / denominator)} for k, w in active.items()}
    exact = sum(raw[k] * w for k, w in active.items()) * 100 / denominator
    matches = 1 + len(q.languages) + int(q.hours is not None and p.max_hours is not None) + len(hits)
    return exact, breakdown, hits, matches


def select(catalog, q):
    category = [p for p in catalog.profiles if key(q.category) in {key(c) for c in p.categories}]
    city = [p for p in category if key(p.city) == key(q.city)]
    rejected = {p.id: failures(p, q) for p in city}
    counts = Counter(reason for reasons in rejected.values() for reason in reasons)
    funnel = [("В категории", len(category)), ("В городе", len(city))]
    remaining = city
    for reason, label in REASONS.items():
        remaining = [p for p in remaining if reason not in rejected[p.id]]
        funnel.append((label.replace("Заняты на дату", "Доступны на дату") if reason == "busy" else
                       {"budget": "Прошли бюджет", "format": "Прошли формат", "language": "Прошли язык", "duration": "Прошли длительность"}[reason], len(remaining)))
    ranked = []
    for p in remaining:
        total, breakdown, hits, matches = score(p, q)
        ranked.append({"profile": p, "score": float(total), "breakdown": breakdown, "evidence": hits,
                       "sort_key": (-total, -matches, p.price, Decimal(len(p.busy_dates)) / DAYS, p.id)})
    ranked.sort(key=lambda c: c["sort_key"])
    return {"status": "no_category" if not city else "no_matches" if not ranked else "found",
            "candidates": ranked[:3], "eligible": len(ranked), "city_count": len(city),
            "category_count": len(category), "funnel": funnel,
            "reasons": {r: counts[r] for r in REASONS},
            "rejected": {pid: rs for pid, rs in rejected.items() if rs},
            "not_shown": max(0, len(ranked) - 3)}
