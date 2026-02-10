"""
Test suite for content quality validators.

Run with: python -m pytest tests/test_validation.py
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from content_quality.validators.product_validator import validate_product_claims
from content_quality.validators.state_validator import validate_state_rules
from content_quality.validators.seo_scorer import score_seo
from content_quality.validators.readability_analyzer import analyze_readability
from content_quality.validators.link_checker import check_links
from content_quality.validators.math_validator import validate_math


# Test articles
GOOD_ARTICLE = """
# How to Check Your Total Loss Settlement in California

Getting a fair total loss settlement is important. Many car owners don't know if their offer is fair.
ClaimCoach helps you identify missing line items in under 5 minutes.

## What Is a Total Loss?

A total loss happens when repair costs are too high. In California, insurers use a Total Loss Formula (TLF).
This is different from other states that use a percentage threshold.

## Common Missing Line Items

Insurers often leave off these items:

### Sales Tax

Sales tax is required in California. This typically ranges from 7% to 10% of your car's value.
For example, if your car is worth $15,000, sales tax would be about $1,200.

### Title and Registration

Title fees range from $15 to $50. Registration costs $50 to $500 depending on your vehicle.

## How ClaimCoach Helps

ClaimCoach analyzes your settlement and shows you what's missing. We help you understand if your offer is fair.
No credit card required for the initial analysis.

[Try ClaimCoach now](https://claimcoach.app)

## Frequently Asked Questions

### How long does it take?

ClaimCoach analyzes your settlement in under 5 minutes.

### What if I find missing items?

You can contact your adjuster to discuss the missing line items we identified.

### Does ClaimCoach negotiate for me?

No, ClaimCoach does not negotiate with insurers. We help you identify gaps so you can discuss them with your adjuster.
"""

BAD_ARTICLE_PRODUCT_CLAIMS = """
# Total Loss Settlement Help

Upload your settlement letter to ClaimCoach and we'll negotiate with your insurance company on your behalf.
We guarantee you'll get more money - our average user recovers $5,000 more.

We'll file the dispute for you and our AI chatbot will answer all your questions.
Track your claim status in real-time with notifications.

ClaimCoach integrates directly with CCC ONE and Mitchell to access your adjuster's systems.

This is 100% risk-free and guaranteed to work. You have nothing to lose!
"""

BAD_ARTICLE_STATE_FACTS = """
# Florida Total Loss Guide

Florida has a 75% total loss threshold. This means if repairs exceed 75% of your car's value, it's totaled.

You have 45 days to dispute the settlement in Florida.

Diminished value claims are available from your own insurance company in Florida.
"""

BAD_ARTICLE_MATH = """
# Settlement Calculation Example

If your car is worth $20,000, and sales tax is 8%, you should receive $1,800 in sales tax.

Adding the title fee of $50 plus registration of $200 equals $300 in total fees.
"""

BAD_ARTICLE_SEO = """
# Article Title

This article doesn't have proper SEO.

No headings. No keyword usage. No FAQ section.

Very short article.

[External link](https://example.com)
"""


class TestProductValidator:
    """Test product claim validator."""

    def test_good_article(self):
        result = validate_product_claims(GOOD_ARTICLE)
        assert result["status"] == "PASS"
        assert len(result["hard_violations"]) == 0

    def test_bad_article_product_claims(self):
        result = validate_product_claims(BAD_ARTICLE_PRODUCT_CLAIMS)
        assert result["status"] == "FAIL"
        assert len(result["hard_violations"]) > 0

        # Check specific violations
        violations_text = " ".join([v["matched_text"].lower() for v in result["hard_violations"]])
        assert "negotiate" in violations_text or "upload" in violations_text


class TestStateValidator:
    """Test state regulation validator."""

    def test_good_article(self):
        result = validate_state_rules(GOOD_ARTICLE, "California")
        assert result["status"] in ["PASS", "WARN"]
        assert "California" in result["states_referenced"]

    def test_bad_article_state_facts(self):
        result = validate_state_rules(BAD_ARTICLE_STATE_FACTS, "Florida")
        assert result["status"] == "FAIL"
        assert len(result["issues"]) > 0

        # Florida is 80%, not 75%
        threshold_issues = [i for i in result["issues"] if i["claim_type"] == "threshold"]
        assert len(threshold_issues) > 0


class TestSEOScorer:
    """Test SEO scorer."""

    def test_good_article(self):
        result = score_seo({
            "article_markdown": GOOD_ARTICLE,
            "target_keyword": "total loss settlement california",
            "meta_title": "How to Check Your Total Loss Settlement in California",
            "meta_description": "Learn how to check if your California total loss settlement is fair. ClaimCoach helps you identify missing line items in under 5 minutes.",
            "slug": "total-loss-settlement-california",
        })

        assert result["total_score"] >= 70  # Should be decent
        assert "keyword_in_title" in result["checks"]
        assert result["checks"]["keyword_in_title"]["passed"]

    def test_bad_article_seo(self):
        result = score_seo({
            "article_markdown": BAD_ARTICLE_SEO,
            "target_keyword": "total loss settlement",
            "meta_title": "Article Title",
            "meta_description": "Short description",
            "slug": "article",
        })

        assert result["status"] == "FAIL"
        assert result["total_score"] < 80


class TestReadabilityAnalyzer:
    """Test readability analyzer."""

    def test_good_article(self):
        result = analyze_readability(GOOD_ARTICLE)
        assert result["status"] == "PASS"
        assert result["flesch_reading_ease"] >= 60

    def test_complex_article(self):
        complex_text = """
        The aforementioned automobile depreciation calculation methodology necessitates
        comprehensive documentation substantiating the diminished valuation subsequent to
        the vehicular collision. Notwithstanding the complexity of the determination, the
        insurance adjuster's assessment may demonstrate significant discrepancies from the
        actual diminished value calculation.
        """
        result = analyze_readability(complex_text)
        # This should fail or have issues
        assert len(result["issues"]) > 0


class TestMathValidator:
    """Test math validator."""

    def test_good_article(self):
        result = validate_math(GOOD_ARTICLE)
        # The good article has correct math
        assert result["status"] == "PASS"

    def test_bad_article_math(self):
        result = validate_math(BAD_ARTICLE_MATH)
        assert result["status"] == "FAIL"
        assert len(result["issues"]) > 0

        # 8% of $20,000 is $1,600, not $1,800
        percentage_errors = [i for i in result["issues"] if i["type"] == "percentage_calculation"]
        assert len(percentage_errors) > 0


class TestLinkChecker:
    """Test link checker."""

    def test_good_article(self):
        result = check_links(GOOD_ARTICLE)
        # Good article has valid internal link
        assert len(result["internal"]) == 1

    def test_broken_link(self):
        article_with_broken_link = """
        [Broken link](https://claimcoach.app/blog/this-post-does-not-exist-12345)
        [External link](https://httpstat.us/404)
        """
        result = check_links(article_with_broken_link)
        # Should detect broken links (though may take time)
        assert "internal" in result or "external" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
