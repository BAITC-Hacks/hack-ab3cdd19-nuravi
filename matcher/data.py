import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import unicodedata

START = date(2026, 9, 23)
END = date(2026, 12, 31)
DAYS = (END - START).days + 1
DATA = Path(__file__).resolve().parents[1] / "data" / "contractors.csv"
FIELDS = {"id", "anon_name", "categories", "city", "price_from_kzt", "event_formats",
          "languages", "max_hours", "busy_dates", "description", "synthetic", "city_imputed", "price_imputed"}


def clean(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def key(value):
    return clean(value).casefold().replace("ё", "е")


def parts(value, required=True):
    values = tuple(sorted({clean(v) for v in value.split("|") if clean(v)}, key=key))
    if required and not values:
        raise ValueError("пустой обязательный список")
    return values


def number(value, label, allow_zero=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: требуется число") from None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"{label}: требуется конечное положительное число")
    return result


def flag(value):
    if key(value) not in {"true", "false"}:
        raise ValueError("повреждённый или отсутствующий флаг качества")
    return key(value) == "true"


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    categories: tuple
    city: str
    price: Decimal
    formats: tuple
    languages: tuple
    max_hours: object
    # None means that the source contains no availability data. An empty
    # frozenset would incorrectly look like confirmed availability every day.
    busy_dates: object
    description: str
    synthetic: bool = False
    city_imputed: bool = False
    price_imputed: bool = False


@dataclass(frozen=True)
class Catalog:
    profiles: tuple
    issues: tuple
    source_rows: int


def load_catalog(path=DATA):
    profiles, issues, seen = [], [], set()
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = FIELDS - set(reader.fieldnames or [])
        if missing:
            raise ValueError("В CSV отсутствуют поля: " + ", ".join(sorted(missing)))
        rows = list(reader)
    for line, raw in enumerate(rows, 2):
        try:
            if None in raw or any(raw[f] is None for f in FIELDS):
                raise ValueError("повреждённая структура строки")
            r = {f: clean(raw[f]) for f in FIELDS}
            if not all(r[f] for f in ("id", "anon_name", "city")):
                raise ValueError("нет id, имени или города")
            if r["id"] in seen:
                raise ValueError("повторяющийся id")
            busy = None if key(r["busy_dates"]) in {"", "[]", "null", "none"} else frozenset(
                date.fromisoformat(d) for d in parts(r["busy_dates"], False)
            )
            if busy is not None and any(d < START or d > END for d in busy):
                raise ValueError("занятая дата вне окна календаря")
            hours = None if key(r["max_hours"]) in {"", "null", "none"} else number(r["max_hours"], "max_hours")
            profiles.append(Profile(r["id"], r["anon_name"], parts(r["categories"]), r["city"],
                number(r["price_from_kzt"], "цена", True), parts(r["event_formats"]),
                parts(r["languages"]), hours, busy, r["description"],
                flag(r["synthetic"]), flag(r["city_imputed"]), flag(r["price_imputed"])))
            seen.add(r["id"])
        except (ValueError, TypeError) as exc:
            issues.append(f"Строка {line} ({raw.get('id') or 'без id'}): {exc}")
    return Catalog(tuple(profiles), tuple(issues), len(rows))
