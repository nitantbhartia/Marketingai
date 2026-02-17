"""Factual-claim extraction and citation confidence scoring.

Used by Sage (review gate) and Ezra (pre-publish safety gate).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


AUTHORITATIVE_DOMAINS = (
    ".gov",
    ".edu",
    "naic.org",
    "nhtsa.gov",
    "floir.com",
    "insurance.ca.gov",
    "tdi.texas.gov",
    "dfs.ny.gov",
    "insurance.illinois.gov",
    "insurance.ohio.gov",
    "oci.georgia.gov",
    "law.cornell.edu",
)


@dataclass
class ClaimCheck:
    claim: str
    claim_type: str
    confidence: str  # verified | weak | unsupported
    reason: str


@dataclass
class ClaimGateResult:
    checks: list[ClaimCheck]
    verified: int
    weak: int
    unsupported: int
    blocking: bool


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_NUMERIC_RE = re.compile(r"(?:\$\d[\d,]*(?:\.\d+)?|\b\d+(?:\.\d+)?%|\b\d{1,4}(?:\s*-\s*\d{1,4})?%)")
_TIMELINE_RE = re.compile(
    r"\b\d+\s*(?:day|days|week|weeks|month|months|year|years|hour|hours)\b",
    re.IGNORECASE,
)
_LEGAL_RE = re.compile(
    r"(?:\bstatute\b|\bcode\b|\badmin\.?\s*code\b|\bregulation\b|§|\bunder\s+[a-z][a-z\s]{1,20}\s+law\b)",
    re.IGNORECASE,
)
_SOURCE_HINT_RE = re.compile(
    r"(?:\bsource\b|\baccording to\b|\bunder\b|\bcitation\b|\bstatute\b)",
    re.IGNORECASE,
)


def _iter_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    chunks = re.split(r"(?<=[.!?])\s+", normalized)
    return [c.strip() for c in chunks if c.strip()]


def _is_authoritative(url: str) -> bool:
    try:
        domain = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    return any(d in domain for d in AUTHORITATIVE_DOMAINS)


def _classify_claim(sentence: str) -> tuple[str, str]:
    """Return (confidence, reason) for a claim sentence."""
    links = _LINK_RE.findall(sentence)
    if links:
        urls = [url for _anchor, url in links]
        if any(_is_authoritative(u) for u in urls):
            return "verified", "has authoritative external citation"
        return "weak", "has citation link but source is not clearly authoritative"
    if _SOURCE_HINT_RE.search(sentence):
        return "unsupported", "mentions a source but no explicit citation link"
    return "unsupported", "no source citation link found"


def evaluate_factual_claims(text: str) -> ClaimGateResult:
    """Extract factual claims and score citation confidence.

    Claims required to be cited:
    - numeric claims ($ amounts, percentages)
    - timeline claims (days/weeks/months/years)
    - legal/statutory claims
    """
    checks: list[ClaimCheck] = []

    for sentence in _iter_sentences(text):
        claim_type = None
        if _LEGAL_RE.search(sentence):
            claim_type = "legal"
        elif _NUMERIC_RE.search(sentence):
            claim_type = "numeric"
        elif _TIMELINE_RE.search(sentence):
            claim_type = "timeline"

        if not claim_type:
            continue

        confidence, reason = _classify_claim(sentence)
        checks.append(
            ClaimCheck(
                claim=sentence[:280],
                claim_type=claim_type,
                confidence=confidence,
                reason=reason,
            )
        )

    verified = sum(1 for c in checks if c.confidence == "verified")
    weak = sum(1 for c in checks if c.confidence == "weak")
    unsupported = sum(1 for c in checks if c.confidence == "unsupported")

    return ClaimGateResult(
        checks=checks,
        verified=verified,
        weak=weak,
        unsupported=unsupported,
        blocking=unsupported > 0,
    )
