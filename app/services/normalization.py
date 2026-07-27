"""Pure normalization helpers.

These functions normalize identifiers for comparison. They never rewrite
technical prose intended for the generated document.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

_SPACE_RE = re.compile(r"[\s\u00a0\u2007\u202f]+")
_GHE_RE = re.compile(
    r"(?i)\bGHE\s*(?:N[º°o.]?\s*)?[-_:./]?\s*0*(?P<number>\d{1,4})\b"
)
_GHE_WITH_NAME_RE = re.compile(
    r"(?i)\bGHE\s*(?:N[º°o.]?\s*)?[-_:./]?\s*0*(?P<number>\d{1,4})\b"
    r"(?:\s*[-–—:|]\s*(?P<name>[^\r\n]+))?"
)
_PERCENTAGE_RE = re.compile(
    r"(?<!\d)(?P<value>\d{1,3}(?:[.,]\d{1,2})?)\s*%"
)


def clean_text(value: Any) -> str:
    """Return display text with only transport whitespace normalized."""

    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\x00", "")).strip()


def normalize_key(value: Any) -> str:
    """Create an accent- and punctuation-insensitive comparison key."""

    text = clean_text(value).casefold()
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def canonical_ghe_code(value: Any) -> str | None:
    """Return ``GHE NN`` without changing the underlying official record."""

    match = _GHE_RE.search(clean_text(value))
    if not match:
        return None
    number = int(match.group("number"))
    width = max(2, len(str(number)))
    return f"GHE {number:0{width}d}"


def parse_ghe_reference(value: Any) -> tuple[str | None, str | None]:
    """Extract a canonical GHE code and an optional adjacent name."""

    text = clean_text(value)
    match = _GHE_WITH_NAME_RE.search(text)
    if not match:
        return None, None
    code = canonical_ghe_code(match.group(0))
    name = clean_text(match.group("name")) or None
    if name:
        # Remove common suffixes that are clearly document headings, while
        # retaining the source wording used as the primary hint.
        name = re.split(r"\s{2,}|\s+[|]\s+", name, maxsplit=1)[0].strip(" -–—:;")
    return code, name or None


def normalize_ghe_name(value: Any) -> str:
    text = normalize_key(value)
    text = re.sub(r"^ghe\s+\d+\s*", "", text)
    stop_prefixes = (
        "analise do ",
        "analise ",
        "resultados do ",
        "resultados ",
        "grupo ",
    )
    for prefix in stop_prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip()


def ghe_name_similarity(left: Any, right: Any) -> float:
    """Conservative similarity score used only to suggest mappings."""

    left_key = normalize_ghe_name(left)
    right_key = normalize_ghe_name(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    token_score = len(left_tokens & right_tokens) / max(
        len(left_tokens | right_tokens), 1
    )
    sequence_score = SequenceMatcher(None, left_key, right_key).ratio()
    return round(max(token_score, sequence_score * 0.9), 4)


def parse_population(value: Any) -> int | None:
    """Parse an explicit non-negative headcount.

    Empty cells and labels such as ``TOTAL`` return ``None``. Fractional and
    negative values are rejected instead of silently rounded.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            return None
        return int(value)
    text = clean_text(value)
    if not text or normalize_key(text) in {"total", "subtotal", "n a", "na"}:
        return None
    match = re.search(r"(?<![\d.,])-?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    numeric = match.group(0).replace(",", ".")
    try:
        parsed = Decimal(numeric)
    except InvalidOperation:
        return None
    if parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def stable_unique(values: Iterable[Any]) -> list[str]:
    """De-duplicate display values without changing their first spelling."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        display = clean_text(value)
        key = normalize_key(display)
        if display and key not in seen:
            result.append(display)
            seen.add(key)
    return result


def extract_percentage(value: Any) -> str | None:
    """Extract a percentage exactly as written, including decimal separator."""

    match = _PERCENTAGE_RE.search(clean_text(value))
    return f"{match.group('value')}%" if match else None


def classify_technical_heading(value: Any) -> str:
    """Map a heading to a normalized category without altering its content."""

    key = normalize_key(value)
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("conclusion", ("conclusao", "consideracoes finais")),
        ("action_plan", ("plano de acao", "acoes integradas")),
        ("prioritization", ("priorizacao", "prioridades")),
        ("positive_points", ("pontos positivos", "aspectos positivos")),
        ("critical_points", ("pontos criticos", "aspectos criticos")),
        (
            "improvements",
            ("indicacoes de melhoria", "oportunidades de melhoria", "melhorias"),
        ),
        (
            "relevant_questions",
            ("perguntas de maior relevancia", "questoes relevantes"),
        ),
        ("favorability", ("favorabilidade", "percentual favoravel")),
        ("classification", ("classificacao", "classe")),
        ("technical_reading", ("leitura tecnica", "analise tecnica")),
        ("overview", ("visao geral", "resumo geral", "panorama geral")),
    )
    for category, phrases in rules:
        if any(phrase in key for phrase in phrases):
            return category
    return "other"


def looks_like_heading(text: Any, style_name: str | None = None) -> bool:
    """Identify structural headings conservatively."""

    cleaned = clean_text(text)
    if not cleaned:
        return False
    style_key = normalize_key(style_name)
    if style_key.startswith(("heading", "titulo", "title", "cabecalho")):
        return True
    if parse_ghe_reference(cleaned)[0] and len(cleaned) <= 160:
        return True
    technical = classify_technical_heading(cleaned)
    if technical != "other" and len(cleaned) <= 160:
        return True
    return (
        len(cleaned) <= 100
        and cleaned[-1:] not in ".;,"
        and (cleaned.isupper() or cleaned.endswith(":"))
    )
