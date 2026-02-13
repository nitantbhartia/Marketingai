"""Content freshness detection and enforcement.

Detects stale year references, outdated statistics, and missing freshness
signals in articles.  Google rewards "recently updated" content — articles
citing 2023 data in 2026 lose ranking to competitors who update.

Used by:
- **Sage** — deducts points for stale references
- **Quill** — self-review replaces stale years with current year markers
- **Atlas** — identifies published articles needing refresh
"""

from __future__ import annotations

import re
from datetime import date

# Current year — used for freshness thresholds
CURRENT_YEAR = date.today().year

# How old a year reference can be before it's flagged as stale
STALENESS_THRESHOLD = 1  # Flag years more than 1 year behind current

# Year references that are OK even if old (historical context)
_HISTORICAL_EXEMPTIONS = {
    "founded", "established", "since", "created",
    "enacted", "signed into law", "passed in",
}


def detect_stale_years(text: str) -> list[dict]:
    """Find year references in text that are stale (> threshold years old).

    Returns a list of dicts with the stale year, surrounding context,
    and a suggested replacement.
    """
    stale_refs: list[dict] = []

    # Match 4-digit years (2000-2099 range)
    for match in re.finditer(r"\b(20[0-9]{2})\b", text):
        year = int(match.group(1))
        if year > CURRENT_YEAR:
            continue  # Future year — fine
        if CURRENT_YEAR - year <= STALENESS_THRESHOLD:
            continue  # Recent enough

        # Check surrounding context for historical exemptions
        start = max(0, match.start() - 50)
        end = min(len(text), match.end() + 50)
        context = text[start:end].lower()

        if any(exempt in context for exempt in _HISTORICAL_EXEMPTIONS):
            continue  # Historical reference — exempt

        stale_refs.append({
            "year": year,
            "position": match.start(),
            "context": text[start:end].strip(),
            "age": CURRENT_YEAR - year,
            "suggestion": f"Update to {CURRENT_YEAR} data or mark as historical",
        })

    return stale_refs


def freshness_score(text: str) -> dict:
    """Score content freshness on a 0-3 scale.

    Checks:
    1. Current year mentioned (signals fresh content)
    2. No stale year references (nothing more than 1 year old)
    3. Freshness language present ("updated", "latest", "as of 2026")

    Returns dict with score and issues.
    """
    issues: list[str] = []
    score = 0.0

    stale = detect_stale_years(text)
    current_year_present = bool(re.search(rf"\b{CURRENT_YEAR}\b", text))
    last_year_present = bool(re.search(rf"\b{CURRENT_YEAR - 1}\b", text))

    # Freshness language markers
    freshness_markers = [
        rf"(?:as of|updated|current as of)\s+{CURRENT_YEAR}",
        r"(?:latest|most recent|updated)\s+(?:data|statistics|numbers|figures)",
        rf"{CURRENT_YEAR}\s+(?:update|guide|rates|data|statistics)",
    ]
    has_freshness_language = any(
        re.search(p, text, re.IGNORECASE) for p in freshness_markers
    )

    # Scoring
    # 1 pt: current year mentioned
    if current_year_present:
        score += 1.0
    elif last_year_present:
        score += 0.5
        issues.append(
            f"Article references {CURRENT_YEAR - 1} but not {CURRENT_YEAR} — "
            f"add current year data for freshness signal"
        )
    else:
        issues.append(
            f"No {CURRENT_YEAR} reference in article — add current year data "
            f"or statistics for freshness signal"
        )

    # 1 pt: no stale references
    if len(stale) == 0:
        score += 1.0
    elif len(stale) <= 2:
        score += 0.5
        years = sorted(set(r["year"] for r in stale))
        issues.append(
            f"Stale year references: {', '.join(str(y) for y in years)} "
            f"(more than {STALENESS_THRESHOLD} year(s) old — update or mark as historical)"
        )
    else:
        years = sorted(set(r["year"] for r in stale))
        issues.append(
            f"{len(stale)} stale year references found ({', '.join(str(y) for y in years)}) "
            f"— update to {CURRENT_YEAR} data or add context"
        )

    # 1 pt: freshness language present
    if has_freshness_language:
        score += 1.0
    else:
        issues.append(
            f"No freshness language — add phrases like 'As of {CURRENT_YEAR}' "
            f"or 'Updated {CURRENT_YEAR} data' to signal recency to Google"
        )

    return {
        "score": round(score, 1),
        "max": 3.0,
        "stale_references": stale,
        "current_year_present": current_year_present,
        "has_freshness_language": has_freshness_language,
        "issues": issues,
    }


def auto_fix_stale_years(text: str) -> tuple[str, int]:
    """Replace simple stale year patterns with current year markers.

    Only fixes patterns where the replacement is unambiguous:
    - "as of 2023" → "as of 2026"
    - "2023 data" → "2026 data"
    - "in 2024" → "in 2026" (when not historical context)

    Returns (fixed_text, count_of_fixes).
    """
    fixes = 0

    # Pattern: "as of YYYY" / "updated YYYY" / "current as of YYYY"
    def _replace_temporal(match: re.Match) -> str:
        nonlocal fixes
        prefix = match.group(1)
        year = int(match.group(2))
        if CURRENT_YEAR - year > STALENESS_THRESHOLD:
            fixes += 1
            return f"{prefix}{CURRENT_YEAR}"
        return match.group(0)

    text = re.sub(
        r"((?:as of|updated|current as of|through)\s+)(20[0-9]{2})",
        _replace_temporal,
        text,
        flags=re.IGNORECASE,
    )

    # Pattern: "YYYY data/statistics/figures/rates"
    def _replace_data_year(match: re.Match) -> str:
        nonlocal fixes
        year = int(match.group(1))
        suffix = match.group(2)
        if CURRENT_YEAR - year > STALENESS_THRESHOLD:
            fixes += 1
            return f"{CURRENT_YEAR} {suffix}"
        return match.group(0)

    text = re.sub(
        r"\b(20[0-9]{2})\s+(data|statistics|figures|rates|numbers|averages|estimates)",
        _replace_data_year,
        text,
        flags=re.IGNORECASE,
    )

    return text, fixes
