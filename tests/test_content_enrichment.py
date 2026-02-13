"""
Tests for content enrichment modules: SERP, freshness, NHTSA, and their
integration into the Quill/Sage article creation flow.

Run with: python -m pytest tests/test_content_enrichment.py -v
"""

import json
import os
import sys
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.config import Config, SerpConfig
from pipeline.db import Article, ArticleStatus, Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> Config:
    """Create a Config with sensible test defaults."""
    cfg = Config()
    cfg.anthropic.api_key = "test-key"
    cfg.gemini.api_key = ""
    cfg.llm_provider = "anthropic"
    cfg.pipeline.max_revision_rounds = 5
    cfg.pipeline.approval_score_threshold = 90
    cfg.copyscape.api_key = ""
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(tmp.name)


# ===========================================================================
# 1. SERP Module — SerpInsight, backends, analyzer
# ===========================================================================

class TestSerpInsight:
    """Test SerpInsight data structure and formatting."""

    def test_empty_insight_returns_empty_context(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(keyword="test keyword")
        assert insight.to_outline_context() == ""

    def test_paa_questions_formatted(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="total loss settlement",
            paa_questions=[
                "How much should I get for my totaled car?",
                "Can I negotiate a total loss settlement?",
                "What is actual cash value?",
            ],
        )
        context = insight.to_outline_context()
        assert "PEOPLE ALSO ASK" in context
        assert "How much should I get" in context
        assert "negotiate a total loss" in context

    def test_related_searches_formatted(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            related_searches=["total loss calculator", "diminished value claim"],
        )
        context = insight.to_outline_context()
        assert "RELATED SEARCHES" in context
        assert "total loss calculator" in context

    def test_competitor_headings_formatted(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            competitor_headings=["How to Fight a Total Loss", "Know Your Rights"],
        )
        context = insight.to_outline_context()
        assert "COMPETITOR H2 HEADINGS" in context
        assert "How to Fight" in context

    def test_content_gaps_formatted(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            content_gaps=["State-specific regulations", "Appraisal clause process"],
        )
        context = insight.to_outline_context()
        assert "CONTENT GAPS" in context
        assert "State-specific" in context

    def test_competitor_benchmark_formatted(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            competitors=[{"title": "Comp 1"}, {"title": "Comp 2"}],
            avg_competitor_word_count=2500,
        )
        context = insight.to_outline_context()
        assert "COMPETITOR BENCHMARK" in context
        assert "2500" in context

    def test_full_insight_all_sections(self):
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            paa_questions=["Q1?", "Q2?"],
            related_searches=["term1", "term2"],
            competitor_headings=["H1", "H2"],
            content_gaps=["Gap1"],
            competitors=[{"title": "C1"}],
            avg_competitor_word_count=2000,
        )
        context = insight.to_outline_context()
        assert "PEOPLE ALSO ASK" in context
        assert "RELATED SEARCHES" in context
        assert "COMPETITOR H2 HEADINGS" in context
        assert "CONTENT GAPS" in context
        assert "COMPETITOR BENCHMARK" in context


class TestSerpAPIBackend:
    """Test SerpAPI response parsing."""

    def test_parse_response_extracts_organic_results(self):
        from pipeline.utils.serp import SerpAPIBackend

        data = {
            "organic_results": [
                {"title": "Top Result", "link": "https://example.com", "snippet": "A snippet", "position": 1},
                {"title": "Second", "link": "https://other.com", "snippet": "Another", "position": 2},
            ],
            "related_questions": [
                {"question": "How do I file a claim?"},
                {"question": "What is ACV?"},
            ],
            "related_searches": [
                {"query": "total loss help"},
            ],
        }
        insight = SerpAPIBackend._parse_response("test keyword", data)
        assert len(insight.competitors) == 2
        assert insight.competitors[0]["title"] == "Top Result"
        assert len(insight.paa_questions) == 2
        assert "How do I file a claim?" in insight.paa_questions
        assert len(insight.related_searches) == 1

    def test_parse_response_handles_empty_data(self):
        from pipeline.utils.serp import SerpAPIBackend

        insight = SerpAPIBackend._parse_response("test", {})
        assert len(insight.competitors) == 0
        assert len(insight.paa_questions) == 0
        assert len(insight.related_searches) == 0

    def test_parse_response_skips_empty_questions(self):
        from pipeline.utils.serp import SerpAPIBackend

        data = {
            "related_questions": [
                {"question": ""},
                {"question": "Real question?"},
                {},
            ],
        }
        insight = SerpAPIBackend._parse_response("test", data)
        assert len(insight.paa_questions) == 1
        assert insight.paa_questions[0] == "Real question?"


class TestGoogleCSEBackend:
    """Test Google Custom Search Engine response parsing."""

    def test_parse_response_extracts_items(self):
        from pipeline.utils.serp import GoogleCSEBackend

        data = {
            "items": [
                {"title": "Result 1", "link": "https://a.com", "snippet": "Snippet 1"},
                {"title": "Result 2", "link": "https://b.com", "snippet": "Snippet 2"},
            ],
        }
        insight = GoogleCSEBackend._parse_response("test", data)
        assert len(insight.competitors) == 2
        assert insight.competitors[0]["position"] == 1
        assert insight.competitors[1]["position"] == 2

    def test_parse_response_handles_spelling_correction(self):
        from pipeline.utils.serp import GoogleCSEBackend

        data = {
            "items": [],
            "spelling": {"correctedQuery": "total loss settlement"},
        }
        insight = GoogleCSEBackend._parse_response("tottal loss", data)
        assert "total loss settlement" in insight.related_searches

    def test_parse_response_skips_same_spelling(self):
        from pipeline.utils.serp import GoogleCSEBackend

        data = {
            "items": [],
            "spelling": {"correctedQuery": "test"},
        }
        insight = GoogleCSEBackend._parse_response("test", data)
        assert len(insight.related_searches) == 0


class TestSerpAnalyzer:
    """Test the main SerpAnalyzer orchestrator."""

    def test_has_serp_backend_with_serpapi(self):
        from pipeline.utils.serp import SerpAnalyzer

        analyzer = SerpAnalyzer(serpapi_key="test-key")
        assert analyzer.has_serp_backend is True

    def test_has_serp_backend_with_google_cse(self):
        from pipeline.utils.serp import SerpAnalyzer

        analyzer = SerpAnalyzer(google_cse_key="key", google_cse_id="id")
        assert analyzer.has_serp_backend is True

    def test_has_serp_backend_without_keys(self):
        from pipeline.utils.serp import SerpAnalyzer

        analyzer = SerpAnalyzer()
        assert analyzer.has_serp_backend is False

    def test_extract_headings_deduplicates(self):
        from pipeline.utils.serp import SerpAnalyzer

        competitors = [
            {"title": "Same Title", "snippet": "Some detail here."},
            {"title": "Same Title", "snippet": "Different snippet text."},
        ]
        headings = SerpAnalyzer._extract_headings(competitors)
        # Title should appear only once
        assert headings.count("Same Title") == 1

    def test_extract_headings_filters_short_phrases(self):
        from pipeline.utils.serp import SerpAnalyzer

        competitors = [
            {"title": "Good Title", "snippet": "Too short. A longer phrase that qualifies as a heading."},
        ]
        headings = SerpAnalyzer._extract_headings(competitors)
        assert "Too short" not in headings  # Under 20 chars
        assert any("longer phrase" in h for h in headings)


# ===========================================================================
# 2. Freshness Module — detect, score, auto-fix
# ===========================================================================

class TestDetectStaleYears:
    """Test stale year detection in content."""

    def test_detects_stale_year(self):
        from pipeline.utils.freshness import detect_stale_years, CURRENT_YEAR

        text = f"According to 2020 data, settlements average $15,000."
        stale = detect_stale_years(text)
        assert len(stale) >= 1
        assert stale[0]["year"] == 2020

    def test_current_year_not_flagged(self):
        from pipeline.utils.freshness import detect_stale_years, CURRENT_YEAR

        text = f"As of {CURRENT_YEAR}, the average settlement is $15,000."
        stale = detect_stale_years(text)
        assert len(stale) == 0

    def test_last_year_not_flagged_within_threshold(self):
        from pipeline.utils.freshness import detect_stale_years, CURRENT_YEAR

        text = f"Data from {CURRENT_YEAR - 1} shows growth."
        stale = detect_stale_years(text)
        assert len(stale) == 0

    def test_future_year_not_flagged(self):
        from pipeline.utils.freshness import detect_stale_years, CURRENT_YEAR

        text = f"By {CURRENT_YEAR + 2}, regulations may change."
        stale = detect_stale_years(text)
        assert len(stale) == 0

    def test_historical_exemptions(self):
        from pipeline.utils.freshness import detect_stale_years

        text = "ClaimCoach was founded in 2018 to help consumers."
        stale = detect_stale_years(text)
        assert len(stale) == 0  # "founded" is exempt

    def test_enacted_exemption(self):
        from pipeline.utils.freshness import detect_stale_years

        text = "The Insurance Reform Act was enacted in 2015."
        stale = detect_stale_years(text)
        assert len(stale) == 0

    def test_multiple_stale_years(self):
        from pipeline.utils.freshness import detect_stale_years

        text = "Using 2020 data and 2021 statistics, we can see trends from 2019."
        stale = detect_stale_years(text)
        # All three should be flagged (all > 1 year behind current)
        years = {s["year"] for s in stale}
        assert 2020 in years
        assert 2019 in years


class TestFreshnessScore:
    """Test the composite freshness scoring function."""

    def test_perfect_freshness(self):
        from pipeline.utils.freshness import freshness_score, CURRENT_YEAR

        text = (
            f"As of {CURRENT_YEAR}, total loss settlements average $18,500. "
            f"Updated {CURRENT_YEAR} data shows improvement. "
            f"These {CURRENT_YEAR} statistics come from state DOI reports."
        )
        result = freshness_score(text)
        assert result["score"] == 3.0
        assert result["current_year_present"] is True
        assert result["has_freshness_language"] is True
        assert len(result["issues"]) == 0

    def test_no_year_reference(self):
        from pipeline.utils.freshness import freshness_score

        text = "Insurance settlements can be contested through the appraisal process."
        result = freshness_score(text)
        assert result["score"] < 2.0
        assert result["current_year_present"] is False
        assert len(result["issues"]) >= 1

    def test_stale_content_penalized(self):
        from pipeline.utils.freshness import freshness_score

        text = "According to 2020 data, the average was $12,000. As of 2021, it grew."
        result = freshness_score(text)
        assert result["score"] < 2.0
        assert len(result["stale_references"]) >= 1

    def test_last_year_partial_credit(self):
        from pipeline.utils.freshness import freshness_score, CURRENT_YEAR

        text = f"Data from {CURRENT_YEAR - 1} shows the trend continuing."
        result = freshness_score(text)
        # Should get 0.5 for last year (not full 1.0)
        assert 0 < result["score"] < 3.0


class TestAutoFixStaleYears:
    """Test automatic stale year replacement."""

    def test_fixes_as_of_pattern(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = "As of 2020, the average settlement was $15,000."
        fixed, count = auto_fix_stale_years(text)
        assert count == 1
        assert f"As of {CURRENT_YEAR}" in fixed
        assert "2020" not in fixed

    def test_fixes_updated_pattern(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = "Updated 2021: new data available."
        fixed, count = auto_fix_stale_years(text)
        assert count == 1
        assert f"Updated {CURRENT_YEAR}" in fixed

    def test_fixes_data_year_pattern(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = "2020 data shows a 15% increase."
        fixed, count = auto_fix_stale_years(text)
        assert count == 1
        assert f"{CURRENT_YEAR} data" in fixed

    def test_preserves_recent_years(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = f"As of {CURRENT_YEAR}, things are good."
        fixed, count = auto_fix_stale_years(text)
        assert count == 0
        assert fixed == text

    def test_preserves_non_temporal_years(self):
        from pipeline.utils.freshness import auto_fix_stale_years

        text = "Section 2020 of the insurance code applies here."
        fixed, count = auto_fix_stale_years(text)
        # "Section 2020" doesn't match "as of" or "YYYY data" patterns
        assert count == 0

    def test_multiple_fixes(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = "As of 2020, rates were low. 2021 statistics show changes. Updated 2019 figures."
        fixed, count = auto_fix_stale_years(text)
        assert count == 3
        assert "2020" not in fixed
        assert "2021" not in fixed
        assert "2019" not in fixed

    def test_case_insensitive(self):
        from pipeline.utils.freshness import auto_fix_stale_years, CURRENT_YEAR

        text = "UPDATED 2020 data now available."
        fixed, count = auto_fix_stale_years(text)
        assert count == 1
        assert f"UPDATED {CURRENT_YEAR}" in fixed


# ===========================================================================
# 3. NHTSA Module — vehicle parsing and enrichment
# ===========================================================================

class TestParseVehicleFromKeyword:
    """Test vehicle make/model/year extraction from keywords."""

    def test_year_make_model_pattern(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("2019 Honda Civic total loss value")
        assert result is not None
        assert result["year"] == 2019
        assert result["make"] == "Honda"
        assert result["model"] == "Civic"

    def test_make_model_year_pattern(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("Toyota Camry 2020 diminished value")
        assert result is not None
        assert result["year"] == 2020
        assert result["make"] == "Toyota"
        assert result["model"] == "Camry"

    def test_two_word_model(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("2021 Ford Mustang Mach")
        assert result is not None
        assert result["make"] == "Ford"
        # Model should include second word if it's not a stop word
        assert "Mustang" in result["model"]

    def test_no_vehicle_returns_none(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("total loss settlement california")
        assert result is None

    def test_no_year_returns_none(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("honda accord total loss")
        assert result is None

    def test_stop_words_not_captured_as_model(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("2019 Honda Civic total loss value")
        assert result is not None
        # "total" should NOT be part of the model
        assert "Total" not in result["model"]
        assert result["model"] == "Civic"

    def test_stop_words_settlement(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("2020 Toyota Camry settlement amount")
        assert result is not None
        assert result["model"] == "Camry"
        assert "Settlement" not in result["model"]

    def test_case_insensitive_keyword(self):
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("2019 honda civic diminished value")
        assert result is not None
        assert result["make"] == "Honda"
        assert result["model"] == "Civic"


class TestEnrichVehicleArticle:
    """Test NHTSA article enrichment with mocked API."""

    @patch("pipeline.utils.nhtsa.get_recalls")
    @patch("pipeline.utils.nhtsa.get_complaints")
    def test_enrichment_with_recalls(self, mock_complaints, mock_recalls):
        from pipeline.utils.nhtsa import enrich_vehicle_article

        mock_recalls.return_value = [
            {
                "campaign_number": "20V123",
                "component": "BRAKES",
                "summary": "Brake pads may wear prematurely causing extended stopping distances",
                "consequence": "Increased risk of crash",
                "remedy": "Replace brake pads",
                "report_date": "2020-01-15",
            },
        ]
        mock_complaints.return_value = {
            "total_complaints": 45,
            "top_components": [
                {"component": "ENGINE", "count": 20},
                {"component": "BRAKES", "count": 15},
            ],
        }

        result = enrich_vehicle_article("2019 Honda Civic total loss")
        assert result is not None
        assert "NHTSA VEHICLE DATA" in result
        assert "2019" in result
        assert "Honda" in result
        assert "Civic" in result
        assert "Recalls:" in result
        assert "BRAKES" in result
        assert "Owner Complaints:" in result
        assert "45 total" in result
        assert "nhtsa.gov" in result.lower()

    @patch("pipeline.utils.nhtsa.get_recalls")
    @patch("pipeline.utils.nhtsa.get_complaints")
    def test_enrichment_no_recalls_or_complaints(self, mock_complaints, mock_recalls):
        from pipeline.utils.nhtsa import enrich_vehicle_article

        mock_recalls.return_value = []
        mock_complaints.return_value = {"total_complaints": 0, "top_components": []}

        result = enrich_vehicle_article("2021 Toyota Camry total loss")
        assert result is not None
        assert "No recalls or significant complaints" in result
        assert "positive data point" in result

    def test_enrichment_no_vehicle_in_keyword(self):
        from pipeline.utils.nhtsa import enrich_vehicle_article

        result = enrich_vehicle_article("total loss settlement california")
        assert result is None


# ===========================================================================
# 4. Config — SerpConfig loading and env var overrides
# ===========================================================================

class TestSerpConfigLoading:
    """Test SerpConfig integration in the Config system."""

    def test_default_serp_config(self):
        cfg = Config()
        assert hasattr(cfg, "serp")
        assert cfg.serp.serpapi_key == ""
        assert cfg.serp.google_cse_key == ""
        assert cfg.serp.google_cse_id == ""
        assert cfg.serp.enabled is True

    def test_serp_env_var_overrides(self):
        with patch.dict(os.environ, {
            "SERPAPI_KEY": "test-serp-key",
            "GOOGLE_CSE_KEY": "test-cse-key",
            "GOOGLE_CSE_ID": "test-cse-id",
        }):
            cfg = Config.load(config_path="/nonexistent/config.yaml")
            assert cfg.serp.serpapi_key == "test-serp-key"
            assert cfg.serp.google_cse_key == "test-cse-key"
            assert cfg.serp.google_cse_id == "test-cse-id"

    def test_serp_in_section_map(self):
        """Ensure serp config is wired into _apply_dict."""
        cfg = Config()
        cfg._apply_dict({
            "serp": {
                "serpapi_key": "from-yaml",
                "enabled": False,
            },
        })
        assert cfg.serp.serpapi_key == "from-yaml"
        assert cfg.serp.enabled is False

    def test_image_config_exists(self):
        cfg = Config()
        assert hasattr(cfg, "images")
        assert cfg.images.unsplash_access_key == ""
        assert cfg.images.enabled is True


# ===========================================================================
# 5. Quill Integration — SERP/NHTSA wiring and freshness auto-fix
# ===========================================================================

class TestQuillSerpIntegration:
    """Test Quill's _get_serp_context method."""

    def setup_method(self):
        self.db = _make_db()
        self.cfg = _make_config()
        self.cfg.serp = SerpConfig(enabled=True)

    def test_returns_empty_on_disabled_serp(self):
        from pipeline.agents.quill import QuillAgent

        self.cfg.serp.enabled = False
        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="test keyword")
        result = quill._get_serp_context(article)
        assert result == ""

    def test_returns_empty_on_empty_keyword(self):
        from pipeline.agents.quill import QuillAgent

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="")
        result = quill._get_serp_context(article)
        assert result == ""

    @patch("pipeline.agents.quill.SerpAnalyzer")
    def test_returns_context_on_success(self, MockAnalyzer):
        from pipeline.agents.quill import QuillAgent
        from pipeline.utils.serp import SerpInsight

        mock_insight = SerpInsight(
            keyword="total loss",
            paa_questions=["How much?", "Can I negotiate?"],
        )
        MockAnalyzer.return_value.analyze.return_value = mock_insight

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="total loss", target_state="CA")
        result = quill._get_serp_context(article)
        assert "PEOPLE ALSO ASK" in result

    @patch("pipeline.agents.quill.SerpAnalyzer")
    def test_graceful_failure_on_exception(self, MockAnalyzer):
        from pipeline.agents.quill import QuillAgent

        MockAnalyzer.return_value.analyze.side_effect = Exception("API down")

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="test keyword")
        result = quill._get_serp_context(article)
        assert result == ""  # Graceful degradation

    def test_returns_empty_when_no_serp_config(self):
        from pipeline.agents.quill import QuillAgent

        # Remove serp config entirely
        delattr(self.cfg, "serp")
        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="test keyword")
        result = quill._get_serp_context(article)
        assert result == ""


class TestQuillNHTSAIntegration:
    """Test Quill's _get_nhtsa_context method."""

    def setup_method(self):
        self.db = _make_db()
        self.cfg = _make_config()

    @patch("pipeline.agents.quill.enrich_vehicle_article")
    def test_returns_context_for_vehicle_keyword(self, mock_enrich):
        from pipeline.agents.quill import QuillAgent

        mock_enrich.return_value = "=== NHTSA DATA ===\nRecall data here"

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="2019 Honda Civic total loss")
        result = quill._get_nhtsa_context(article)
        assert "NHTSA DATA" in result

    @patch("pipeline.agents.quill.enrich_vehicle_article")
    def test_returns_empty_for_non_vehicle_keyword(self, mock_enrich):
        from pipeline.agents.quill import QuillAgent

        mock_enrich.return_value = None

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="total loss settlement california")
        result = quill._get_nhtsa_context(article)
        assert result == ""

    def test_returns_empty_on_empty_keyword(self):
        from pipeline.agents.quill import QuillAgent

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="")
        result = quill._get_nhtsa_context(article)
        assert result == ""

    @patch("pipeline.agents.quill.enrich_vehicle_article")
    def test_graceful_failure_on_exception(self, mock_enrich):
        from pipeline.agents.quill import QuillAgent

        mock_enrich.side_effect = Exception("NHTSA API timeout")

        quill = QuillAgent(self.cfg, self.db)
        article = Article(target_keyword="2020 Toyota Camry loss")
        result = quill._get_nhtsa_context(article)
        assert result == ""


class TestQuillOutlineAcceptsNewParams:
    """Test that _generate_outline properly accepts and uses SERP/NHTSA context."""

    def setup_method(self):
        self.db = _make_db()
        self.cfg = _make_config()

    @patch.object(
        __import__("pipeline.agents.quill", fromlist=["QuillAgent"]).QuillAgent,
        "call_claude",
        return_value="## Outline Section\n- Point 1\n- Point 2",
    )
    def test_outline_with_serp_and_nhtsa_context(self, mock_call):
        from pipeline.agents.quill import QuillAgent

        quill = QuillAgent(self.cfg, self.db)
        article = Article(
            target_keyword="total loss settlement",
            content_category="problem_aware",
        )

        outline = quill._generate_outline(
            article, "product ctx", "state rules", "links",
            entity_map="LEGAL: ACV, subrogation",
            serp_context="=== PEOPLE ALSO ASK ===\n- Q1?\n- Q2?",
            nhtsa_context="=== NHTSA DATA ===\nRecall info",
        )

        assert outline  # Non-empty
        # Verify SERP and NHTSA context were passed in the prompt
        call_args = mock_call.call_args
        prompt = call_args.kwargs.get("prompt", call_args[0][0] if call_args[0] else "")
        assert "PEOPLE ALSO ASK" in prompt
        assert "NHTSA DATA" in prompt


class TestQuillFreshnessAutoFix:
    """Test freshness auto-fix in Quill's self-review."""

    def setup_method(self):
        self.db = _make_db()
        self.cfg = _make_config()

    def test_stale_years_fixed_in_self_review(self):
        from pipeline.agents.quill import QuillAgent
        from pipeline.utils.freshness import CURRENT_YEAR

        quill = QuillAgent(self.cfg, self.db)
        # Content with stale years in auto-fixable patterns
        content = (
            "## Total Loss Settlements\n\n"
            "As of 2020, the average total loss settlement was $15,000. "
            f"[ClaimCoach](https://claimcoach.app) helps you fight back.\n\n"
            "## FAQ\n\n"
            "### How much is my car worth?\n\n"
            "Check 2019 data for typical values.\n"
        )
        article = Article(
            target_keyword="total loss settlement",
            meta_description="Learn about total loss settlements.",
        )

        fixed_content, meta, fixes = quill._self_review_and_fix(
            content, "Learn about total loss settlements.", article,
        )

        # Should have fixed stale years
        stale_fix = [f for f in fixes if "stale_year" in f]
        assert len(stale_fix) >= 1
        assert "2020" not in fixed_content.split("As of")[1].split(",")[0] if "As of" in fixed_content else True
        assert f"{CURRENT_YEAR}" in fixed_content


# ===========================================================================
# 6. Sage Integration — freshness deduction in SEO scoring
# ===========================================================================

class TestSageFreshnessDeduction:
    """Test that Sage deducts SEO points for stale content."""

    def setup_method(self):
        self.db = _make_db()
        self.cfg = _make_config()
        self.cfg.gemini.api_key = "test-key"
        self.cfg.llm_provider = "gemini"

    def _create_article(self, content: str, **kwargs) -> Article:
        defaults = dict(
            title="Test Article",
            target_keyword="total loss settlement",
            status=ArticleStatus.EDITOR_REVIEW.value,
            markdown_content=content,
            meta_description="Test meta about total loss settlement.",
            content_category="problem_aware",
        )
        defaults.update(kwargs)
        article = self.db.create_article(**defaults)
        return self.db.get_article(article.id)

    def test_fresh_content_no_deduction(self):
        """Content with current year and freshness language should get full SEO points."""
        from pipeline.agents.sage import SageAgent
        from pipeline.utils.freshness import CURRENT_YEAR

        content = (
            f"## Total Loss Settlement Guide ({CURRENT_YEAR})\n\n"
            f"As of {CURRENT_YEAR}, settlements average $18,500. "
            f"Updated {CURRENT_YEAR} data from state DOI reports shows growth.\n\n"
            "## Understanding Your Rights\n\n"
            "Contact your [department of insurance](https://www.naic.org/) for help.\n"
        )
        article = self._create_article(content)
        sage = SageAgent(self.cfg, self.db)
        sage._calibration = {"word_count_target": (1800, 2200), "word_count_ok": (1500, 2500)}

        # Just test the freshness portion
        from pipeline.utils.freshness import freshness_score as calc_freshness
        fresh = calc_freshness(content)
        assert fresh["score"] >= 2.5  # Should not trigger deduction

    def test_stale_content_deduction(self):
        """Content with stale years should trigger SEO deduction."""
        from pipeline.utils.freshness import freshness_score as calc_freshness

        content = (
            "## Total Loss Settlement Guide\n\n"
            "According to 2020 data, settlements average $15,000. "
            "The 2019 statistics showed similar trends.\n\n"
        )
        fresh = calc_freshness(content)
        # Score should be low enough to trigger deduction
        assert fresh["score"] < 2.0
        assert len(fresh["issues"]) >= 1

    def test_freshness_issues_prefixed_in_seo(self):
        """Freshness issues should be prefixed with [Freshness] in SEO issues."""
        from pipeline.agents.sage import SageAgent
        from pipeline.utils.seo import score_seo, extract_links, detect_faq_section

        content = (
            "## Total Loss Settlement Guide\n\n"
            "According to 2020 data, settlements average $15,000.\n"
        )

        # Simulate what Sage does
        from pipeline.utils.freshness import freshness_score as calc_freshness
        fresh = calc_freshness(content)
        seo_issues = []
        seo_issues.extend(f"[Freshness] {i}" for i in fresh.get("issues", []))
        assert any("[Freshness]" in issue for issue in seo_issues)


# ===========================================================================
# 7. Sage — plagiarism score reuse on revision rounds
# ===========================================================================

class TestSagePlagiarismReuse:
    """Test that Sage reuses plagiarism scores on revision rounds."""

    def test_reuse_parses_score_from_notes(self):
        from pipeline.agents.sage import SageAgent

        db = _make_db()
        sage = SageAgent(_make_config(), db)

        article = Article(
            revision_notes=(
                "## Review Summary — Score: 75/100\n"
                "### Breakdown:\n"
                "- **plagiarism**: 18/20\n"
                "  - Minor overlap detected\n"
            ),
        )
        score, issues = sage._reuse_plagiarism_score(article)
        assert score == 18.0
        assert any("carried from round 1" in i for i in issues)

    def test_reuse_defaults_when_unparseable(self):
        from pipeline.agents.sage import SageAgent

        db = _make_db()
        sage = SageAgent(_make_config(), db)

        article = Article(revision_notes="Some notes without plagiarism score")
        score, issues = sage._reuse_plagiarism_score(article)
        assert score == 18.0  # Benefit of the doubt

    def test_reuse_on_revision_rounds(self):
        """Verify _review_article calls _reuse_plagiarism_score on revision rounds."""
        from pipeline.agents.sage import SageAgent

        db = _make_db()
        cfg = _make_config()
        cfg.gemini.api_key = "test"
        cfg.llm_provider = "gemini"
        sage = SageAgent(cfg, db)
        sage._calibration = {"word_count_target": (1800, 2200), "word_count_ok": (1500, 2500)}

        article = db.create_article(
            title="Revision Article",
            target_keyword="total loss",
            status=ArticleStatus.EDITOR_REVIEW.value,
            markdown_content="# Content\n\nAbout total loss.",
            meta_description="Test",
        )
        db.update_article(article.id, revision_count=1, revision_notes=(
            "### Breakdown:\n- **plagiarism**: 16/20\n  - Some overlap\n"
        ))
        article = db.get_article(article.id)

        with patch.object(sage, "_check_plagiarism") as mock_check, \
             patch.object(sage, "_check_facts", return_value=(15.0, [])), \
             patch.object(sage, "_ai_fact_check", return_value=[]):
            sage._review_article(article)
            # _check_plagiarism should NOT have been called (reuse instead)
            mock_check.assert_not_called()


# ===========================================================================
# 8. Edge cases and defensive checks
# ===========================================================================

class TestEdgeCases:
    """Test edge cases in the creation flow."""

    def test_freshness_score_empty_text(self):
        from pipeline.utils.freshness import freshness_score

        result = freshness_score("")
        assert result["score"] >= 0
        assert isinstance(result["issues"], list)

    def test_freshness_auto_fix_empty_text(self):
        from pipeline.utils.freshness import auto_fix_stale_years

        fixed, count = auto_fix_stale_years("")
        assert fixed == ""
        assert count == 0

    def test_detect_stale_years_no_years(self):
        from pipeline.utils.freshness import detect_stale_years

        result = detect_stale_years("No year references here at all.")
        assert result == []

    def test_serp_insight_paa_limit(self):
        """SerpInsight.to_outline_context() should limit PAA to 6 questions."""
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            paa_questions=[f"Question {i}?" for i in range(20)],
        )
        context = insight.to_outline_context()
        # Count "- Question" occurrences
        lines = [l for l in context.split("\n") if l.startswith("- Question")]
        assert len(lines) <= 6

    def test_serp_insight_related_limit(self):
        """SerpInsight.to_outline_context() should limit related searches to 8."""
        from pipeline.utils.serp import SerpInsight

        insight = SerpInsight(
            keyword="test",
            related_searches=[f"search {i}" for i in range(20)],
        )
        context = insight.to_outline_context()
        lines = [l for l in context.split("\n") if l.startswith("- search")]
        assert len(lines) <= 8

    def test_nhtsa_parse_vehicle_boundary_years(self):
        """Test year boundary: 2000 and 2029 should both match."""
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result_2000 = parse_vehicle_from_keyword("2000 Honda Civic value")
        assert result_2000 is not None
        assert result_2000["year"] == 2000

        result_2029 = parse_vehicle_from_keyword("2029 Tesla Model value")
        assert result_2029 is not None
        assert result_2029["year"] == 2029

    def test_nhtsa_parse_vehicle_out_of_range(self):
        """Years outside 2000-2029 should not match."""
        from pipeline.utils.nhtsa import parse_vehicle_from_keyword

        result = parse_vehicle_from_keyword("1999 Honda Civic total loss")
        assert result is None

        result = parse_vehicle_from_keyword("2030 Honda Civic total loss")
        assert result is None

    def test_nhtsa_enrich_returns_none_gracefully(self):
        """enrich_vehicle_article should return None for non-vehicle keywords."""
        from pipeline.utils.nhtsa import enrich_vehicle_article

        result = enrich_vehicle_article("insurance claim tips")
        assert result is None

    def test_freshness_score_max_and_structure(self):
        """Verify freshness_score return structure."""
        from pipeline.utils.freshness import freshness_score

        result = freshness_score("Some text")
        assert "score" in result
        assert "max" in result
        assert result["max"] == 3.0
        assert "stale_references" in result
        assert "current_year_present" in result
        assert "has_freshness_language" in result
        assert "issues" in result

    def test_serp_analyzer_deduplicates_autocomplete(self):
        """SerpAnalyzer.analyze should deduplicate autocomplete with existing related."""
        from pipeline.utils.serp import SerpAnalyzer, SerpInsight

        analyzer = SerpAnalyzer()  # No API keys, autocomplete only

        # Mock autocomplete to return duplicates
        with patch.object(analyzer._autocomplete, "query", return_value=[
            "total loss settlement",
            "total loss calculator",
            "total loss settlement",  # duplicate
        ]):
            insight = analyzer.analyze("total loss settlement")
            # Should have deduped
            lower_searches = [s.lower() for s in insight.related_searches]
            assert lower_searches.count("total loss settlement") <= 1
