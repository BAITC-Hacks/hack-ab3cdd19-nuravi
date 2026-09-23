"""Validate AI-interpreted event requests before they reach the matching engine."""

from datetime import date
from decimal import Decimal, InvalidOperation

from .data import END, START, clean, key
from .engine import Request


FIELDS = ("city", "date", "event_format", "category", "budget", "hours", "languages", "preference")
REQUIRED = ("city", "date", "event_format", "category", "budget")
LABELS = {"city": "город", "date": "дату", "event_format": "формат события",
          "category": "категорию подрядчика", "budget": "бюджет"}


def choices(catalog):
    return {
        "cities": sorted({p.city for p in catalog.profiles}, key=key),
        "categories": sorted({item for p in catalog.profiles for item in p.categories}, key=key),
        "formats": sorted({item for p in catalog.profiles for item in p.formats}, key=key),
        "languages": sorted({item for p in catalog.profiles for item in p.languages}, key=key),
    }


def _canonical(value, options):
    if not isinstance(value, str):
        return None
    return {key(option): option for option in options}.get(key(value))


def normalize(raw, catalog):
    """Accept only explicit, well-formed fields. Invalid required fields stay missing."""
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        raise ValueError("ИИ вернул неполный ответ")
    options = choices(catalog)
    values = {}
    # A city/category outside the catalog is still a meaningful no-category query.
    for field, option_name in (("city", "cities"), ("category", "categories")):
        value = raw[field]
        if value is None:
            values[field] = None
        elif isinstance(value, str) and 0 < len(clean(value)) <= 80:
            values[field] = _canonical(value, options[option_name]) or clean(value)
        else:
            values[field] = None
    values["event_format"] = _canonical(raw["event_format"], options["formats"])
    try:
        day = date.fromisoformat(raw["date"]) if isinstance(raw["date"], str) else None
        values["date"] = day if day is not None and START <= day <= END else None
    except ValueError:
        values["date"] = None
    for field in ("budget", "hours"):
        value = raw[field]
        if value is None:
            values[field] = None
            continue
        try:
            number = Decimal(str(value))
            valid = number.is_finite() and number > 0
            if field == "budget" and valid:
                valid = number == number.to_integral_value()
            values[field] = number if valid else None
        except (InvalidOperation, ValueError):
            values[field] = None
    language_values = raw["languages"]
    if not isinstance(language_values, list) or not all(isinstance(v, str) for v in language_values):
        raise ValueError("ИИ вернул неверный список языков")
    values["languages"] = tuple(sorted({_canonical(v, options["languages"]) or clean(v)
                                         for v in language_values if 0 < len(clean(v)) <= 40}, key=key))
    preference = raw["preference"]
    values["preference"] = clean(preference)[:240] if isinstance(preference, str) else ""
    return values


def missing(values):
    return tuple(field for field in REQUIRED if values.get(field) is None)


def question(values):
    absent = missing(values)
    if not absent:
        return ""
    fields = ", ".join(LABELS[field] for field in absent)
    if "date" in absent:
        return f"Уточните, пожалуйста: {fields}. Доступные даты: {START:%d.%m.%Y}–{END:%d.%m.%Y}."
    return f"Уточните, пожалуйста: {fields}."


def to_request(values):
    if missing(values):
        raise ValueError("Для подбора нужны город, дата, формат, категория и бюджет")
    return Request(city=values["city"], date=values["date"],
                   event_format=values["event_format"], category=values["category"],
                   budget=values["budget"], hours=values["hours"],
                   languages=values["languages"], preference=values["preference"])
