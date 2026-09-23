from datetime import date
from .engine import Request

DEMOS = {
    "Плотная категория · 14 ноября": dict(city="Алматы", date=date(2026, 11, 14), event_format="корпоратив", category="Ведущий", budget=1500000, hours=6, languages=("русский",)),
    "Та же заявка · 15 ноября": dict(city="Алматы", date=date(2026, 11, 15), event_format="корпоратив", category="Ведущий", budget=1500000, hours=6, languages=("русский",)),
    "Редкая категория · флорист": dict(city="Алматы", date=date(2026, 10, 10), event_format="корпоратив", category="Флорист", budget=300000, hours=6, languages=("русский",)),
    "Никто не проходит · бюджет": dict(city="Алматы", date=date(2026, 11, 14), event_format="корпоратив", category="Ведущий", budget=10000, hours=6, languages=("русский",)),
    "Категории нет · зарубежье": dict(city="Зарубежье", date=date(2026, 11, 14), event_format="корпоратив", category="Флорист", budget=300000, hours=None, languages=()),
}


def demo_request(name):
    return Request(**DEMOS[name])


if __name__ == "__main__":
    from time import perf_counter
    from .data import load_catalog
    from .engine import select
    from .explain import explain

    catalog = load_catalog()
    for name in DEMOS:
        request = demo_request(name)
        started = perf_counter()
        result = select(catalog, request)
        elapsed_ms = (perf_counter() - started) * 1000
        print(f"\n{name}: {result['status']}, подходят {result['eligible']}, {elapsed_ms:.2f} мс")
        for candidate in result["candidates"]:
            print(f"  {candidate['profile'].id}: {candidate['score']:.2f} — {explain(candidate, request)}")
        print("  Причины:", result["reasons"])
