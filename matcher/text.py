"""Small, deterministic text matcher that returns the source sentence as evidence."""

from collections import Counter
from decimal import Decimal
import re

from .data import key

STOP_WORDS = frozenset({
    "для", "это", "как", "при", "про", "без", "или", "мне", "нам", "нужен",
    "нужна", "нужно", "хочу", "ищу", "чтобы", "который", "которые", "очень",
    "мероприятие", "мероприятия", "подрядчик", "подрядчика", "работа", "работы",
})
COMMON_STEMS = frozenset({"ведущ", "гости", "госте", "гость", "мероп", "подря"})


def terms(value):
    """Use short stems to match common Russian inflections without a model."""
    words = re.findall(r"[а-яa-z0-9]+", key(value))
    return frozenset(stem for word in words if len(word) >= 3 and word not in STOP_WORDS
                     if (stem := word[:5] if len(word) > 5 else word) not in COMMON_STEMS)


def sentences(description):
    return [part.strip(" \t\r\n•-–") for part in re.split(r"(?<=[.!?])\s+|\n+", description)
            if part.strip(" \t\r\n•-–")]


def best_evidence(description, preference, document_frequency=None):
    """Return query-term coverage and the best matching original sentence.

    A zero score has no evidence. We never infer a claim absent from the source.
    """
    requested = terms(preference)
    if not requested or not description:
        return Decimal(0), []
    frequency = document_frequency or Counter()
    weights = {word: Decimal(1) / Decimal(1 + frequency[word]) for word in requested}
    total = sum(weights.values())
    ranked = []
    requires_without = bool(re.search(r"\bбез\b", key(preference)))
    for index, sentence in enumerate(sentences(description)):
        if requires_without and not re.search(r"\bбез\b", key(sentence)):
            continue
        overlap = requested & terms(sentence)
        if overlap:
            coverage = sum(weights[word] for word in overlap) / total
            ranked.append((coverage, -index, sentence, overlap))
    if not ranked:
        return Decimal(0), []
    coverage, _, sentence, overlap = max(ranked, key=lambda row: (row[0], row[1]))
    return coverage, [{"factor": "Пожелание", "match": sentence, "terms": sorted(overlap),
                       "field": "description"}]


def document_frequency(profiles):
    count = Counter()
    for profile in profiles:
        count.update(terms(profile.description))
    return count
