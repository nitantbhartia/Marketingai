"""Keyword research utilities.

Provides keyword research via free/scraping methods when paid APIs
(Ahrefs, SEMrush) are not configured. When API keys are available,
uses those instead.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class KeywordData:
    keyword: str
    search_volume: int = 0
    difficulty: float = 0.0
    commercial_intent: float = 0.0
    source: str = "estimated"


# ── Pre-defined seed keywords for ClaimCoach ─────────────────

SEED_KEYWORDS: list[dict] = [
    # Problem-aware
    {"keyword": "insurance lowball offer", "volume": 2400, "difficulty": 0.35, "intent": 0.8, "category": "problem_aware"},
    {"keyword": "total loss settlement too low", "volume": 1800, "difficulty": 0.30, "intent": 0.85, "category": "problem_aware"},
    {"keyword": "insurance company undervalued my car", "volume": 1200, "difficulty": 0.25, "intent": 0.8, "category": "problem_aware"},
    {"keyword": "unfair total loss settlement", "volume": 900, "difficulty": 0.28, "intent": 0.85, "category": "problem_aware"},
    {"keyword": "insurance offer too low totaled car", "volume": 800, "difficulty": 0.22, "intent": 0.9, "category": "problem_aware"},

    # Solution-aware
    {"keyword": "how to dispute total loss offer", "volume": 3200, "difficulty": 0.32, "intent": 0.9, "category": "solution_aware"},
    {"keyword": "how to negotiate total loss settlement", "volume": 2800, "difficulty": 0.35, "intent": 0.9, "category": "solution_aware"},
    {"keyword": "dispute insurance settlement", "volume": 2200, "difficulty": 0.38, "intent": 0.85, "category": "solution_aware"},
    {"keyword": "how to get more money from insurance claim", "volume": 2000, "difficulty": 0.30, "intent": 0.85, "category": "solution_aware"},
    {"keyword": "negotiate with insurance adjuster", "volume": 1600, "difficulty": 0.33, "intent": 0.8, "category": "solution_aware"},

    # Line-item specific
    {"keyword": "sales tax on replacement vehicle insurance", "volume": 1400, "difficulty": 0.20, "intent": 0.75, "category": "line_item"},
    {"keyword": "loss of use claim total loss", "volume": 1100, "difficulty": 0.22, "intent": 0.8, "category": "line_item"},
    {"keyword": "diminished value claim", "volume": 4500, "difficulty": 0.45, "intent": 0.7, "category": "line_item"},
    {"keyword": "title and registration fees insurance settlement", "volume": 600, "difficulty": 0.15, "intent": 0.75, "category": "line_item"},
    {"keyword": "dealer fees total loss settlement", "volume": 500, "difficulty": 0.12, "intent": 0.8, "category": "line_item"},

    # Comparison
    {"keyword": "public adjuster vs doing it yourself", "volume": 800, "difficulty": 0.25, "intent": 0.7, "category": "comparison"},
    {"keyword": "hire attorney for total loss claim", "volume": 700, "difficulty": 0.30, "intent": 0.65, "category": "comparison"},
    {"keyword": "CCC ONE car valuation accuracy", "volume": 600, "difficulty": 0.18, "intent": 0.75, "category": "comparison"},

    # Emotional / story
    {"keyword": "lowballed by state farm total loss", "volume": 500, "difficulty": 0.15, "intent": 0.8, "category": "emotional"},
    {"keyword": "lowballed by geico total loss", "volume": 450, "difficulty": 0.15, "intent": 0.8, "category": "emotional"},
    {"keyword": "insurance company totaled my car unfairly", "volume": 700, "difficulty": 0.20, "intent": 0.85, "category": "emotional"},
]

# State-specific templates
STATE_KEYWORDS_TEMPLATE = [
    "{state} total loss threshold",
    "{state} diminished value claim",
    "{state} total loss settlement guide",
    "{state} insurance settlement dispute",
    "how to dispute total loss {state}",
    "{state} car insurance claim rights",
]

STATES = [
    "California", "Texas", "Florida", "New York", "Pennsylvania",
    "Illinois", "Ohio", "Georgia", "North Carolina", "Michigan",
    "New Jersey", "Virginia", "Washington", "Arizona", "Tennessee",
    "Massachusetts", "Indiana", "Missouri", "Maryland", "Colorado",
]

# Vehicle-specific templates
VEHICLE_KEYWORDS_TEMPLATE = [
    "{year} {make} {model} total loss value",
    "what is my {year} {make} {model} worth totaled",
    "{make} {model} total loss settlement",
]

POPULAR_VEHICLES = [
    {"year": "2019", "make": "Honda", "model": "Civic"},
    {"year": "2020", "make": "Toyota", "model": "Camry"},
    {"year": "2018", "make": "Honda", "model": "Accord"},
    {"year": "2019", "make": "Toyota", "model": "RAV4"},
    {"year": "2020", "make": "Ford", "model": "F-150"},
    {"year": "2018", "make": "Chevrolet", "model": "Malibu"},
    {"year": "2019", "make": "Nissan", "model": "Altima"},
    {"year": "2020", "make": "Hyundai", "model": "Elantra"},
    {"year": "2017", "make": "Honda", "model": "CR-V"},
    {"year": "2019", "make": "Toyota", "model": "Corolla"},
]


def generate_state_keywords() -> list[dict]:
    """Generate state-specific keyword variations."""
    results = []
    for state in STATES:
        for template in STATE_KEYWORDS_TEMPLATE:
            kw = template.format(state=state)
            results.append({
                "keyword": kw,
                "volume": 300,  # Estimated
                "difficulty": 0.20,
                "intent": 0.75,
                "category": "state_specific",
                "state": state,
            })
    return results


def generate_vehicle_keywords() -> list[dict]:
    """Generate vehicle-specific keyword variations."""
    results = []
    for vehicle in POPULAR_VEHICLES:
        for template in VEHICLE_KEYWORDS_TEMPLATE:
            kw = template.format(**vehicle)
            results.append({
                "keyword": kw,
                "volume": 200,  # Estimated
                "difficulty": 0.15,
                "intent": 0.8,
                "category": "vehicle_specific",
            })
    return results


def score_topic(
    volume: int,
    difficulty: float,
    intent: float,
    category: str = "",
    freshness_bonus: float = 0.0,
) -> float:
    """Calculate priority score for a topic.

    Score = (volume × 0.3) + (inverse_difficulty × 0.3) +
            (intent × 0.25) + (freshness × 0.15)

    State-specific and vehicle-specific get 1.5x multiplier.
    """
    # Normalize volume to 0-1 range (assuming max ~5000)
    norm_volume = min(volume / 5000.0, 1.0)
    inverse_diff = 1.0 - difficulty

    score = (
        norm_volume * 0.3
        + inverse_diff * 0.3
        + intent * 0.25
        + freshness_bonus * 0.15
    )

    # Multiplier for high-converting categories
    if category in ("state_specific", "vehicle_specific"):
        score *= 1.5

    return round(score, 3)


def get_all_seed_topics() -> list[dict]:
    """Get all seed topics with scores."""
    topics = []

    for kw in SEED_KEYWORDS:
        kw["score"] = score_topic(
            kw["volume"], kw["difficulty"], kw["intent"], kw.get("category", "")
        )
        topics.append(kw)

    for kw in generate_state_keywords():
        kw["score"] = score_topic(
            kw["volume"], kw["difficulty"], kw["intent"], kw.get("category", "")
        )
        topics.append(kw)

    for kw in generate_vehicle_keywords():
        kw["score"] = score_topic(
            kw["volume"], kw["difficulty"], kw["intent"], kw.get("category", "")
        )
        topics.append(kw)

    # Sort by score descending
    topics.sort(key=lambda x: x["score"], reverse=True)
    return topics


def google_autocomplete(query: str) -> list[str]:
    """Get Google autocomplete suggestions (free, no API key needed)."""
    try:
        url = "https://suggestqueries.google.com/complete/search"
        params = {"client": "firefox", "q": query}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) > 1:
                return data[1]
    except Exception as e:
        logger.warning(f"Google autocomplete failed: {e}")
    return []


def discover_related_keywords(base_keyword: str) -> list[str]:
    """Discover related keywords via autocomplete and variations."""
    suggestions = []

    # Google autocomplete for the base keyword
    suggestions.extend(google_autocomplete(base_keyword))

    # Add common modifiers
    modifiers = ["how to", "what is", "best way to", "can I", "should I"]
    for mod in modifiers:
        suggestions.extend(google_autocomplete(f"{mod} {base_keyword}"))

    # Deduplicate
    seen = set()
    unique = []
    for s in suggestions:
        s_lower = s.lower()
        if s_lower not in seen:
            seen.add(s_lower)
            unique.append(s)

    return unique
