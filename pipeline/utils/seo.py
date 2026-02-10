"""SEO scoring utilities."""

from __future__ import annotations

import re


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
    keyword_lower = keyword.lower()

    # Keyword in title (3 pts)
    if keyword_lower in title.lower():
        score += 3
    else:
        issues.append(f"Keyword '{keyword}' not in title")

    # Keyword in first paragraph / first 100 words (3 pts)
    first_500_chars = content[:500].lower()
    if keyword_lower in first_500_chars:
        score += 3
    else:
        issues.append("Keyword not in first 100 words")

    # Keyword in H2 headers (3 pts - need at least 2)
    h2_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    h2s = h2_pattern.findall(content)
    h2_keyword_count = sum(1 for h in h2s if keyword_lower in h.lower())
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

        if keyword_lower in meta_description.lower():
            score += 1
        else:
            issues.append("Keyword not in meta description")
    else:
        issues.append("No meta description")

    # Internal links (3 pts)
    if len(internal_links) >= 5:
        score += 3
    elif len(internal_links) >= 3:
        score += 2
        issues.append(f"Only {len(internal_links)} internal links (need 5+)")
    elif len(internal_links) >= 1:
        score += 1
        issues.append(f"Only {len(internal_links)} internal links (need 5+)")
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
