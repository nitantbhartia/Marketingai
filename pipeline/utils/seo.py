"""SEO scoring utilities."""

from __future__ import annotations

import re


def _keyword_match(keyword: str, text: str) -> bool:
    """Check if all words in *keyword* appear in *text*.

    Tries three strategies in order:
      1. Exact substring (fastest, strictest)
      2. All words in order with up to 30 chars between each
         ("total loss settlement california" matches
          "Total Loss Settlement in California")
      3. All words present anywhere in the text
         ("California total loss settlement" also matches)
    """
    kw = keyword.lower().strip()
    txt = text.lower()
    if not kw:
        return False
    # Fast path: exact substring
    if kw in txt:
        return True
    parts = kw.split()
    if len(parts) <= 1:
        return False
    # Words in order with gaps
    pattern = r"\b" + r"\b.{0,30}\b".join(re.escape(w) for w in parts) + r"\b"
    if re.search(pattern, txt):
        return True
    # All words present (any order)
    return all(re.search(r"\b" + re.escape(w) + r"\b", txt) for w in parts)


def score_seo(
    content: str,
    title: str,
    keyword: str,
    meta_description: str,
    internal_links: list[str],
    external_links: list[str],
    has_faq: bool = False,
) -> tuple[float, list[str]]:
    """Score an article's SEO quality on a 0-20 scale. Returns (score, issues)."""
    score = 0.0
    issues: list[str] = []

    # Keyword in title (3 pts)
    if _keyword_match(keyword, title):
        score += 3
    else:
        issues.append(f"Keyword '{keyword}' not in title")

    # Keyword in first paragraph / first 100 words (3 pts)
    first_500_chars = content[:500]
    if _keyword_match(keyword, first_500_chars):
        score += 3
    else:
        issues.append("Keyword not in first 100 words")

    # Keyword in H2 headers (3 pts - need at least 2)
    h2_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    h2s = h2_pattern.findall(content)
    h2_keyword_count = sum(1 for h in h2s if _keyword_match(keyword, h))
    if h2_keyword_count >= 2:
        score += 3
    elif h2_keyword_count == 1:
        score += 1.5
        issues.append("Keyword in only 1 H2 header (need 2+)")
    else:
        issues.append("Keyword not in any H2 headers")

    # Meta description (3 pts)
    if meta_description:
        if 150 <= len(meta_description) <= 160:
            score += 2
        elif 130 <= len(meta_description) <= 170:
            score += 1
            issues.append(f"Meta description length {len(meta_description)} (target 150-160)")
        else:
            issues.append(f"Meta description length {len(meta_description)} (target 150-160)")

        if _keyword_match(keyword, meta_description):
            score += 1
        else:
            issues.append("Keyword not in meta description")
    else:
        issues.append("No meta description")

    # Internal links (3 pts) — target 3-5 contextual links
    if len(internal_links) >= 3:
        score += 3
    elif len(internal_links) >= 2:
        score += 2
        issues.append(f"Only {len(internal_links)} internal links (target 3+)")
    elif len(internal_links) >= 1:
        score += 1
        issues.append(f"Only {len(internal_links)} internal links (target 3+)")
    else:
        issues.append("No internal links")

    # External links (2 pts)
    if len(external_links) >= 2:
        score += 2
    elif len(external_links) >= 1:
        score += 1
        issues.append(f"Only {len(external_links)} external links (need 2-3)")
    else:
        issues.append("No external links")

    # FAQ section (3 pts)
    if has_faq:
        score += 3
    else:
        issues.append("No FAQ section")

    # Keyword density check (2 pts) — target 0.5%-3% of total words
    if keyword:
        kw_lower = keyword.lower()
        words = content.lower().split()
        total_words = len(words) if words else 1
        # Count keyword occurrences (all words present in a window)
        kw_parts = kw_lower.split()
        kw_count = content.lower().count(kw_lower) if len(kw_parts) == 1 else sum(
            1 for i in range(len(words) - len(kw_parts) + 1)
            if all(kw_parts[j] in words[i + j] for j in range(len(kw_parts)))
        )
        density = (kw_count * len(kw_parts)) / total_words * 100 if total_words else 0
        if 0.5 <= density <= 3.0:
            score += 2
        elif 0.3 <= density <= 4.0:
            score += 1
            issues.append(f"Keyword density {density:.1f}% (target 0.5-3%)")
        elif density < 0.3:
            issues.append(f"Keyword density too low: {density:.1f}% (target 0.5-3%)")
        else:
            issues.append(f"Keyword stuffing: density {density:.1f}% (target 0.5-3%)")

    return score, issues


def extract_links(content: str) -> tuple[list[str], list[str]]:
    """Extract internal and external links from markdown content."""
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    internal = []
    external = []
    for _text, url in link_pattern.findall(content):
        if "claimcoach" in url.lower():
            internal.append(url)
        elif url.startswith("http"):
            external.append(url)
    return internal, external


def detect_faq_section(content: str) -> bool:
    """Check if content has an FAQ section."""
    faq_patterns = [
        r"(?i)##\s*(?:FAQ|Frequently Asked Questions)",
        r"(?i)##\s*Common Questions",
    ]
    return any(re.search(p, content) for p in faq_patterns)


def generate_slug(title: str) -> str:
    """Generate a URL slug from an article title."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:80]
