"""
SEO Scorer

Programmatically scores every article's SEO optimization.
Returns a score out of 100 based on multiple checks.
"""

import re
from typing import Dict, List
from urllib.parse import urlparse

from content_quality.config import (
    MIN_SEO_SCORE, MIN_WORD_COUNT, MAX_WORD_COUNT,
    MIN_FAQ_QUESTIONS, META_DESC_MIN_LENGTH, META_DESC_MAX_LENGTH
)
from content_quality.utils.text_utils import (
    extract_headings, extract_links, extract_images,
    word_count, extract_first_n_words, find_keyword_in_text,
    extract_faq_section, check_heading_hierarchy, is_internal_link
)


# Authoritative domains for external link quality check
AUTHORITATIVE_DOMAINS = [
    # Government
    "insurance.ca.gov", "tdi.texas.gov", "floir.com", "dfs.ny.gov",
    "insurance.pa.gov", "insurance.illinois.gov", "insurance.ohio.gov",
    "oci.georgia.gov", "ncdoi.gov", "michigan.gov",
    "naic.org", "usa.gov", ".gov",

    # Legal
    "law.cornell.edu", "justia.com", "findlaw.com",

    # Vehicle valuation
    "kbb.com", "edmunds.com", "nada.com", "carfax.com",

    # Consumer advocacy
    "consumerfinance.gov", "ftc.gov", "bbb.org",

    # Insurance reference
    "insure.com", "nerdwallet.com", "policygenius.com",

    # Education
    ".edu",
]


class SEOScorer:
    """Scores article SEO optimization."""

    def __init__(self):
        """Initialize scorer."""
        self.checks = {}
        self.total_score = 0

    def _slug_contains_keyword(self, slug: str, keyword: str) -> bool:
        """Check if slug contains keyword (hyphenated)."""
        slug_lower = slug.lower().replace('-', ' ').replace('_', ' ')
        keyword_lower = keyword.lower()
        return keyword_lower in slug_lower

    @staticmethod
    def _keyword_match(keyword: str, text: str) -> bool:
        """Fuzzy keyword match to avoid brittle exact-phrase failures."""
        kw = (keyword or "").lower().strip()
        txt = (text or "").lower()
        if not kw:
            return False
        if kw in txt:
            return True
        parts = kw.split()
        if len(parts) <= 1:
            return False
        pattern = r"\b" + r"\b.{0,30}\b".join(re.escape(w) for w in parts) + r"\b"
        if re.search(pattern, txt):
            return True
        return all(re.search(r"\b" + re.escape(w) + r"\b", txt) for w in parts)

    def _check_keyword_in_title(self, meta_title: str, keyword: str) -> Dict:
        """Check if keyword appears in title (8 points)."""
        passed = self._keyword_match(keyword, meta_title)
        return {
            "passed": passed,
            "points_earned": 8 if passed else 0,
            "points_possible": 8,
            "message": "✓ Keyword in title" if passed else f"✗ Target keyword '{keyword}' not found in title: '{meta_title}'"
        }

    def _check_title_length(self, meta_title: str) -> Dict:
        """Check title length 50-60 characters (5 points)."""
        length = len(meta_title)
        passed = 50 <= length <= 60
        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": f"✓ Title length {length} chars (target: 50-60)" if passed else f"✗ Title is {length} chars (target: 50-60)"
        }

    def _check_keyword_in_meta_description(self, meta_description: str, keyword: str) -> Dict:
        """Check if keyword appears in meta description (5 points)."""
        passed = self._keyword_match(keyword, meta_description)
        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": "✓ Keyword in meta description" if passed else "✗ Target keyword not in meta description"
        }

    def _check_meta_description_length(self, meta_description: str) -> Dict:
        """Check meta description length 140-160 characters (4 points)."""
        length = len(meta_description)
        passed = META_DESC_MIN_LENGTH <= length <= META_DESC_MAX_LENGTH
        return {
            "passed": passed,
            "points_earned": 4 if passed else 0,
            "points_possible": 4,
            "message": f"✓ Meta description {length} chars (target: {META_DESC_MIN_LENGTH}-{META_DESC_MAX_LENGTH})" if passed else f"✗ Meta description is {length} chars (target: {META_DESC_MIN_LENGTH}-{META_DESC_MAX_LENGTH})"
        }

    def _check_keyword_in_slug(self, slug: str, keyword: str) -> Dict:
        """Check if keyword appears in slug (3 points)."""
        passed = self._slug_contains_keyword(slug, keyword)
        return {
            "passed": passed,
            "points_earned": 3 if passed else 0,
            "points_possible": 3,
            "message": "✓ Keyword reflected in URL slug" if passed else "✗ Keyword not reflected in URL slug"
        }

    def _check_keyword_in_first_100_words(self, article_markdown: str, keyword: str) -> Dict:
        """Check if keyword appears in first 100 words (8 points)."""
        first_100 = extract_first_n_words(article_markdown, 100)
        passed = self._keyword_match(keyword, first_100)
        return {
            "passed": passed,
            "points_earned": 8 if passed else 0,
            "points_possible": 8,
            "message": "✓ Keyword in first 100 words" if passed else "✗ Keyword not found in first 100 words"
        }

    def _check_keyword_in_h2s(self, article_markdown: str, keyword: str) -> Dict:
        """Check if keyword appears in at least 2 H2 headings (7 points)."""
        headings = extract_headings(article_markdown)
        h2_headings = [text for level, text in headings if level == 2]

        count = sum(1 for h2 in h2_headings if self._keyword_match(keyword, h2))
        passed = count >= 1

        return {
            "passed": passed,
            "points_earned": 7 if count >= 2 else (4 if count == 1 else 0),
            "points_possible": 7,
            "message": (
                f"✓ Keyword in {count} H2 headings (target: 1+, ideal: 2+)"
                if passed
                else "✗ Keyword not found in H2 headings (target: 1+)"
            )
        }

    def _check_h2_frequency(self, article_markdown: str) -> Dict:
        """Check H2 heading cadence (about every 140-360 words)."""
        headings = extract_headings(article_markdown)
        h2_count = sum(1 for level, _ in headings if level == 2)
        total_words = word_count(article_markdown)

        if h2_count == 0:
            return {
                "passed": False,
                "points_earned": 0,
                "points_possible": 5,
                "message": "✗ No H2 headings found"
            }

        avg_words = total_words / h2_count
        passed = 140 <= avg_words <= 360

        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": (
                f"✓ Average {int(avg_words)} words between H2s (target: 140-360)"
                if passed
                else f"✗ Average {int(avg_words)} words between H2s (target: 140-360)"
            )
        }

    def _check_h2_count(self, article_markdown: str) -> Dict:
        """Check at least 5 H2 headings (5 points)."""
        headings = extract_headings(article_markdown)
        h2_count = sum(1 for level, _ in headings if level == 2)
        passed = h2_count >= 5

        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": f"✓ {h2_count} H2 headings (minimum: 5)" if passed else f"✗ Only {h2_count} H2 headings (minimum: 5)"
        }

    def _check_heading_hierarchy(self, article_markdown: str) -> Dict:
        """Check no skipped heading levels (5 points)."""
        headings = extract_headings(article_markdown)
        errors = check_heading_hierarchy(headings)
        passed = len(errors) == 0

        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": "✓ Heading hierarchy is valid" if passed else f"✗ Heading levels skipped: {'; '.join(errors)}"
        }

    def _check_internal_links(self, article_markdown: str) -> Dict:
        """Check at least 3 internal links (10 points)."""
        links = extract_links(article_markdown)
        internal_links = [url for url, _ in links if is_internal_link(url, "claimcoach.app")]
        count = len(internal_links)
        passed = count >= 3

        return {
            "passed": passed,
            "points_earned": 10 if passed else 0,
            "points_possible": 10,
            "message": f"✓ {count} internal links (minimum: 3)" if passed else f"✗ Only {count} internal links (minimum: 3)"
        }

    def _check_external_links_count(self, article_markdown: str) -> Dict:
        """Check at least 2 external links (5 points)."""
        links = extract_links(article_markdown)
        external_links = [url for url, _ in links if not is_internal_link(url, "claimcoach.app")]
        count = len(external_links)
        passed = count >= 2

        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": f"✓ {count} external links (minimum: 2)" if passed else f"✗ Only {count} external links (minimum: 2)"
        }

    def _check_external_links_quality(self, article_markdown: str) -> Dict:
        """Check external links point to authoritative domains (5 points)."""
        links = extract_links(article_markdown)
        external_links = [url for url, _ in links if not is_internal_link(url, "claimcoach.app")]
        if len(external_links) < 2:
            return {
                "passed": False,
                "points_earned": 0,
                "points_possible": 5,
                "message": "✗ Not enough external links to assess quality (minimum: 2)"
            }

        low_authority = []
        for url in external_links:
            try:
                domain = urlparse(url).netloc.lower()
                is_authoritative = any(auth_domain in domain for auth_domain in AUTHORITATIVE_DOMAINS)
                if not is_authoritative:
                    low_authority.append(domain)
            except:
                continue

        passed = len(low_authority) == 0

        return {
            "passed": passed,
            "points_earned": 5 if passed else 0,
            "points_possible": 5,
            "message": "✓ All external links are authoritative" if passed else f"✗ External links include low-authority domains: {', '.join(set(low_authority))}"
        }

    def _check_faq_section(self, article_markdown: str) -> Dict:
        """Check FAQ section with 3+ Q&A pairs (8 points)."""
        faqs = extract_faq_section(article_markdown)
        count = len(faqs)
        passed = count >= MIN_FAQ_QUESTIONS

        return {
            "passed": passed,
            "points_earned": 8 if passed else 0,
            "points_possible": 8,
            "message": f"✓ FAQ section has {count} questions (minimum: {MIN_FAQ_QUESTIONS})" if passed else f"✗ FAQ section has {count} questions (minimum: {MIN_FAQ_QUESTIONS})"
        }

    def _check_cta_present(self, article_markdown: str) -> Dict:
        """Check article has 3 CTAs with one early placement (4 points)."""
        links = extract_links(article_markdown)
        cta_links = [
            url for url, _ in links
            if "claimcoach.app" in url.lower() and "/blog" not in url.lower()
        ]
        cta_count = len(cta_links)

        words = article_markdown.split()
        first_300 = " ".join(words[:300]).lower()
        early_cta = "claimcoach.app" in first_300

        points = 0
        if cta_count >= 1:
            points += 1
        if cta_count >= 2:
            points += 1
        if cta_count >= 3:
            points += 1
        if early_cta:
            points += 1

        passed = cta_count >= 3 and early_cta

        return {
            "passed": passed,
            "points_earned": points,
            "points_possible": 4,
            "message": (
                f"✓ CTA coverage: {cta_count} link(s), early CTA {'present' if early_cta else 'missing'}"
                if passed
                else f"✗ CTA coverage weak: {cta_count} link(s), early CTA {'present' if early_cta else 'missing'} (target: 3 + early)"
            )
        }

    def _check_image_alt_text(self, article_markdown: str, keyword: str) -> Dict:
        """Check images have alt text; at least one contains keyword (3 points)."""
        images = extract_images(article_markdown)

        if not images:
            return {
                "passed": True,
                "points_earned": 3,
                "points_possible": 3,
                "message": "✓ No images (alt text check N/A)"
            }

        missing_alt = [url for url, alt in images if not alt.strip()]
        has_keyword_in_alt = any(keyword.lower() in alt.lower() for _, alt in images)

        passed = len(missing_alt) == 0 and has_keyword_in_alt

        if len(missing_alt) > 0:
            message = f"✗ {len(missing_alt)} images missing alt text"
        elif not has_keyword_in_alt:
            message = "✗ No image alt text contains target keyword"
        else:
            message = "✓ All images have alt text; keyword found in alt text"

        return {
            "passed": passed,
            "points_earned": 3 if passed else 0,
            "points_possible": 3,
            "message": message
        }

    def _check_word_count(self, article_markdown: str) -> Dict:
        """Check word count is 1800-2200 (10 points)."""
        count = word_count(article_markdown)
        passed = MIN_WORD_COUNT <= count <= MAX_WORD_COUNT

        return {
            "passed": passed,
            "points_earned": 10 if passed else 0,
            "points_possible": 10,
            "message": f"✓ Article is {count} words (target: {MIN_WORD_COUNT}-{MAX_WORD_COUNT})" if passed else f"✗ Article is {count} words (target: {MIN_WORD_COUNT}-{MAX_WORD_COUNT})"
        }

    def score(self, article_markdown: str, target_keyword: str, meta_title: str,
              meta_description: str, slug: str) -> Dict:
        """
        Score article SEO optimization.

        Args:
            article_markdown: Full article content
            target_keyword: Target SEO keyword
            meta_title: Meta title
            meta_description: Meta description
            slug: URL slug

        Returns:
            Score dict with total score, status, checks, and suggestions
        """
        checks = {}

        # Title & Meta (25 points)
        checks["keyword_in_title"] = self._check_keyword_in_title(meta_title, target_keyword)
        checks["title_length"] = self._check_title_length(meta_title)
        checks["keyword_in_meta_description"] = self._check_keyword_in_meta_description(meta_description, target_keyword)
        checks["meta_description_length"] = self._check_meta_description_length(meta_description)
        checks["keyword_in_slug"] = self._check_keyword_in_slug(slug, target_keyword)

        # Content Structure (30 points)
        checks["keyword_in_first_100_words"] = self._check_keyword_in_first_100_words(article_markdown, target_keyword)
        checks["keyword_in_h2s"] = self._check_keyword_in_h2s(article_markdown, target_keyword)
        checks["h2_frequency"] = self._check_h2_frequency(article_markdown)
        checks["h2_count"] = self._check_h2_count(article_markdown)
        checks["no_skipped_headings"] = self._check_heading_hierarchy(article_markdown)

        # Links (20 points)
        checks["internal_links_count"] = self._check_internal_links(article_markdown)
        checks["external_links_count"] = self._check_external_links_count(article_markdown)
        checks["external_links_quality"] = self._check_external_links_quality(article_markdown)

        # Content Quality Signals (15 points)
        checks["faq_section"] = self._check_faq_section(article_markdown)
        checks["cta_present"] = self._check_cta_present(article_markdown)
        checks["image_alt_text"] = self._check_image_alt_text(article_markdown, target_keyword)

        # Word Count (10 points)
        checks["word_count_range"] = self._check_word_count(article_markdown)

        # Calculate total score
        total_score = sum(check["points_earned"] for check in checks.values())

        # Generate prioritized suggestions
        suggestions = []
        failed_checks = sorted(
            [(name, check) for name, check in checks.items() if not check["passed"]],
            key=lambda x: x[1]["points_possible"],
            reverse=True
        )

        for name, check in failed_checks[:5]:  # Top 5 suggestions
            suggestions.append(check["message"])

        status = "PASS" if total_score >= MIN_SEO_SCORE else "FAIL"

        return {
            "total_score": total_score,
            "status": status,
            "checks": checks,
            "suggestions": suggestions,
        }


def score_seo(article_data: Dict) -> Dict:
    """
    Convenience function to score SEO.

    Args:
        article_data: Dict with keys: article_markdown, target_keyword, meta_title, meta_description, slug

    Returns:
        SEO score result
    """
    scorer = SEOScorer()
    return scorer.score(
        article_data.get("article_markdown", ""),
        article_data.get("target_keyword", ""),
        article_data.get("meta_title", ""),
        article_data.get("meta_description", ""),
        article_data.get("slug", "")
    )
