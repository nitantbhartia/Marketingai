"""
Regression tests for rate-limit handling across the pipeline.

Ensures that 429 errors:
  1. Retry with exponential backoff in both Anthropic and Gemini paths
  2. Raise RateLimitError (not generic Exception) after exhausting retries
  3. Do NOT silently bounce articles to revision with unchanged content
  4. Do NOT degrade Sage scores (plagiarism, fact check) on rate limits
  5. Are recorded as metrics for dashboard visibility

Run with: python -m pytest tests/test_rate_limit.py -v
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.agents.base import BaseAgent, RateLimitError
from pipeline.config import Config
from pipeline.db import Article, ArticleStatus, Database


# ---------------------------------------------------------------------------
# Helpers — minimal concrete agent for testing base class behaviour
# ---------------------------------------------------------------------------

class _StubAgent(BaseAgent):
    """Concrete BaseAgent subclass for unit-testing call_claude / retry logic."""
    name = "stub"

    def run(self):
        return {}


def _make_config(**overrides) -> Config:
    """Create a Config with sensible test defaults."""
    cfg = Config()
    cfg.anthropic.api_key = "test-key"
    cfg.gemini.api_key = ""  # Anthropic provider by default
    cfg.llm_provider = "anthropic"
    cfg.pipeline.max_revision_rounds = 5
    cfg.pipeline.approval_score_threshold = 90
    cfg.copyscape.api_key = ""  # No Copyscape
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(tmp.name)


# =========================================================================
# 1. Base agent — _call_anthropic retry + error classification
# =========================================================================

class TestAnthropicRetryLogic:
    """Verify _call_anthropic retries on 429 and raises RateLimitError."""

    def setup_method(self):
        self.db = _make_db()
        self.agent = _StubAgent(_make_config(), self.db)

    @patch("pipeline.agents.base.time.sleep")
    def test_raises_rate_limit_error_on_429(self, mock_sleep):
        """After exhausting retries on 429, should raise RateLimitError."""
        import anthropic as _anthropic

        fake_429 = _anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_429

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RateLimitError, match="rate limited"):
                self.agent._call_anthropic("hello", "", None, 100)

        # Should have retried 3 times
        assert mock_client.messages.create.call_count == 3
        # Should have slept between retries (exponential backoff)
        assert mock_sleep.call_count == 3

    @patch("pipeline.agents.base.time.sleep")
    def test_server_error_raises_original_exception(self, mock_sleep):
        """500/503/529 errors should NOT raise RateLimitError."""
        import anthropic as _anthropic

        fake_response = MagicMock(status_code=500, headers={})
        fake_500 = _anthropic.APIStatusError(
            message="server error",
            response=fake_response,
            body=None,
        )
        fake_500.status_code = 500

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_500

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(_anthropic.APIStatusError):
                self.agent._call_anthropic("hello", "", None, 100)

        # Should have retried 3 times for server error too
        assert mock_client.messages.create.call_count == 3
        assert mock_sleep.call_count == 3

    @patch("pipeline.agents.base.time.sleep")
    def test_non_retryable_error_raises_immediately(self, mock_sleep):
        """400 (bad request) should raise immediately, no retries."""
        import anthropic as _anthropic

        fake_response = MagicMock(status_code=400, headers={})
        fake_400 = _anthropic.APIStatusError(
            message="bad request",
            response=fake_response,
            body=None,
        )
        fake_400.status_code = 400

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_400

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(_anthropic.APIStatusError):
                self.agent._call_anthropic("hello", "", None, 100)

        # Only 1 attempt — no retries for 400
        assert mock_client.messages.create.call_count == 1
        assert mock_sleep.call_count == 0

    @patch("pipeline.agents.base.time.sleep")
    def test_succeeds_after_transient_429(self, mock_sleep):
        """Should return successfully if 429 clears on retry."""
        import anthropic as _anthropic

        fake_429 = _anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        success_response = MagicMock()
        success_response.content = [MagicMock(text="Hello back!")]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [fake_429, success_response]

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = self.agent._call_anthropic("hello", "", None, 100)

        assert result == "Hello back!"
        assert mock_client.messages.create.call_count == 2
        assert mock_sleep.call_count == 1


# =========================================================================
# 2. Base agent — _call_gemini retry + RateLimitError wrapping
# =========================================================================

class TestGeminiRetryLogic:
    """Verify _call_gemini raises RateLimitError on 429 after retries."""

    def setup_method(self):
        self.db = _make_db()
        cfg = _make_config()
        cfg.gemini.api_key = "test-gemini-key"
        cfg.llm_provider = "gemini"
        self.agent = _StubAgent(cfg, self.db)

    @patch("pipeline.agents.base.time.sleep")
    def test_raises_rate_limit_error_on_429(self, mock_sleep):
        """After exhausting retries on Gemini 429, should raise RateLimitError."""
        import urllib.error

        fake_429 = urllib.error.HTTPError(
            url="https://example.com", code=429,
            msg="Too Many Requests", hdrs={}, fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=fake_429):
            with pytest.raises(RateLimitError, match="rate limited"):
                self.agent._call_gemini("hello", "", None, 100)

    @patch("pipeline.agents.base.time.sleep")
    def test_500_raises_original_error(self, mock_sleep):
        """Gemini 500 should raise the original HTTPError, not RateLimitError."""
        import urllib.error

        fake_500 = urllib.error.HTTPError(
            url="https://example.com", code=500,
            msg="Server Error", hdrs={}, fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=fake_500):
            with pytest.raises(urllib.error.HTTPError):
                self.agent._call_gemini("hello", "", None, 100)


# =========================================================================
# 3. Quill — rate limit does NOT bounce article to REVISION
# =========================================================================

class TestQuillRateLimitHandling:
    """Ensure Quill releases claim but does NOT change article status on 429."""

    def setup_method(self):
        self.db = _make_db()
        cfg = _make_config()
        cfg.gemini.api_key = "test-key"
        cfg.llm_provider = "gemini"
        self.cfg = cfg

    def _create_revision_article(self) -> Article:
        """Create an article in REVISION status with existing content."""
        article = self.db.create_article(
            title="Test Article",
            target_keyword="total loss settlement",
            status=ArticleStatus.IN_PROGRESS.value,
            markdown_content="# Existing Content\n\nSome words here.",
            meta_description="Existing meta",
            revision_notes="### Issues:\n- Keyword density too low",
            content_category="problem_aware",
        )
        self.db.update_article(article.id, revision_count=1)
        return self.db.get_article(article.id)

    @patch("pipeline.agents.base.time.sleep")
    def test_rate_limit_during_draft_does_not_change_status(self, mock_sleep):
        """If call_claude raises RateLimitError during drafting, article
        should keep its status (IN_PROGRESS) — NOT bounce to REVISION."""
        from pipeline.agents.quill import QuillAgent

        article = self.db.create_article(
            title="Fresh Article",
            target_keyword="test keyword",
            status=ArticleStatus.TODO.value,
        )

        quill = QuillAgent(self.cfg, self.db)

        # Mock pick_and_claim to return our article
        with patch.object(quill, "pick_and_claim", return_value=article.id), \
             patch.object(quill, "_try_revision", return_value=None), \
             patch.object(quill, "_extract_entities", return_value=""), \
             patch.object(quill, "_generate_outline", return_value=""), \
             patch.object(quill, "call_claude", side_effect=RateLimitError("429")):

            result = quill._write_one()

        assert result["status"] == "rate_limited"

        # Check article was NOT bounced to REVISION
        updated = self.db.get_article(article.id)
        assert updated.status != ArticleStatus.REVISION.value
        # Writer claim should be released
        assert updated.writer_claim == ""

    @patch("pipeline.agents.base.time.sleep")
    def test_rate_limit_during_targeted_revision(self, mock_sleep):
        """RateLimitError during targeted revision should NOT bounce article."""
        from pipeline.agents.quill import QuillAgent

        article = self._create_revision_article()
        quill = QuillAgent(self.cfg, self.db)

        with patch.object(quill, "_try_revision", return_value=article.id), \
             patch.object(quill, "_targeted_revision", side_effect=RateLimitError("429")):

            result = quill._write_one()

        assert result["status"] == "rate_limited"

        updated = self.db.get_article(article.id)
        assert updated.status != ArticleStatus.REVISION.value
        assert updated.writer_claim == ""

    def test_rate_limit_records_metric(self):
        """Rate limit events should be recorded for dashboard visibility."""
        from pipeline.agents.quill import QuillAgent

        article = self.db.create_article(
            title="Test",
            target_keyword="test",
            status=ArticleStatus.TODO.value,
        )

        quill = QuillAgent(self.cfg, self.db)

        with patch.object(quill, "pick_and_claim", return_value=article.id), \
             patch.object(quill, "_try_revision", return_value=None), \
             patch.object(quill, "_extract_entities", return_value=""), \
             patch.object(quill, "_generate_outline", return_value=""), \
             patch.object(quill, "call_claude", side_effect=RateLimitError("429")):

            quill._write_one()

        # Verify metric was recorded
        with self.db._connect() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM pipeline_metrics WHERE metric_name = 'rate_limit'"
            )
            count = cursor.fetchone()[0]
        assert count == 1

    def test_rate_limit_stops_batch(self):
        """After a rate_limited result, Quill should stop trying more articles."""
        from pipeline.agents.quill import QuillAgent

        quill = QuillAgent(self.cfg, self.db)

        rate_limited_result = {"status": "rate_limited", "article_id": 1, "reason": "429"}
        with patch.object(quill, "_write_one", return_value=rate_limited_result):
            result = quill.run()

        # Should have called _write_one only once (stopped on rate_limited)
        assert result["status"] == "rate_limited"


# =========================================================================
# 4. Sage — rate limit does NOT degrade scores or bounce article
# =========================================================================

class TestSageRateLimitHandling:
    """Ensure Sage propagates RateLimitError instead of degrading scores."""

    def setup_method(self):
        self.db = _make_db()
        cfg = _make_config()
        cfg.gemini.api_key = "test-key"
        cfg.llm_provider = "gemini"
        self.cfg = cfg

    def _create_review_article(self) -> Article:
        article = self.db.create_article(
            title="Review Article",
            target_keyword="total loss settlement",
            status=ArticleStatus.EDITOR_REVIEW.value,
            markdown_content="# Content\n\nSome content about total loss settlement.",
            meta_description="Test meta",
            content_category="problem_aware",
        )
        return self.db.get_article(article.id)

    def test_originality_check_propagates_rate_limit(self):
        """_llm_originality_check must NOT swallow RateLimitError."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        article = self._create_review_article()

        with patch.object(sage, "call_claude", side_effect=RateLimitError("429")):
            with pytest.raises(RateLimitError):
                sage._llm_originality_check("content", article)

    def test_fact_check_propagates_rate_limit(self):
        """_check_facts must NOT swallow RateLimitError from _ai_fact_check."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        article = self._create_review_article()

        with patch.object(sage, "call_claude", side_effect=RateLimitError("429")), \
             patch.object(sage.config, "load_product_context", return_value="context"):
            with pytest.raises(RateLimitError):
                sage._check_facts("some content", article)

    def test_review_article_propagates_rate_limit(self):
        """Full _review_article should propagate RateLimitError."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        sage._calibration = {"word_count_target": (1800, 2200), "word_count_ok": (1500, 2500)}
        article = self._create_review_article()

        # RateLimitError during plagiarism check should propagate
        with patch.object(sage, "_check_plagiarism", side_effect=RateLimitError("429")):
            with pytest.raises(RateLimitError):
                sage._review_article(article)

    def test_run_catches_rate_limit_and_releases_claim(self):
        """Sage.run() should catch RateLimitError, release claim, keep status."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        article = self._create_review_article()

        # Article starts unclaimed — Sage will claim it in run(), then hit the
        # rate limit during review, which should release the claim.
        with patch.object(sage, "_review_article", side_effect=RateLimitError("429")), \
             patch.object(sage, "_load_calibration", return_value={}):
            sage.run()

        # Article should still be in EDITOR_REVIEW (not bounced to REVISION)
        updated = self.db.get_article(article.id)
        assert updated.status == ArticleStatus.EDITOR_REVIEW.value
        # Claim should be released so Sage can pick it up next run
        assert updated.editor_claim == ""

    def test_run_rate_limit_does_not_count_as_revision(self):
        """Rate limited articles should NOT have their revision_count incremented."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        article = self._create_review_article()
        original_rev_count = article.revision_count

        with patch.object(sage, "_review_article", side_effect=RateLimitError("429")), \
             patch.object(sage, "_load_calibration", return_value={}):
            sage.run()

        updated = self.db.get_article(article.id)
        assert updated.revision_count == original_rev_count

    def test_run_records_rate_limit_metric(self):
        """Rate limit events in Sage should be recorded as metrics."""
        from pipeline.agents.sage import SageAgent

        sage = SageAgent(self.cfg, self.db)
        article = self._create_review_article()

        with patch.object(sage, "_review_article", side_effect=RateLimitError("429")), \
             patch.object(sage, "_load_calibration", return_value={}):
            sage.run()

        with self.db._connect() as conn:
            cursor = conn.execute(
                "SELECT details FROM pipeline_metrics WHERE metric_name = 'rate_limit'"
            )
            row = cursor.fetchone()

        assert row is not None
        meta = json.loads(row[0])
        assert meta["agent"] == "sage"


# =========================================================================
# 5. RateLimitError is a proper exception class
# =========================================================================

class TestRateLimitErrorClass:
    """Verify RateLimitError has correct inheritance and semantics."""

    def test_is_exception(self):
        assert issubclass(RateLimitError, Exception)

    def test_not_caught_by_value_error(self):
        """RateLimitError should NOT be caught by ValueError handlers."""
        with pytest.raises(RateLimitError):
            try:
                raise RateLimitError("test")
            except ValueError:
                pass  # Should not catch

    def test_caught_by_exception(self):
        """RateLimitError should be caught by generic Exception handler
        (for backwards compat where we haven't added specific handling yet)."""
        caught = False
        try:
            raise RateLimitError("test")
        except Exception:
            caught = True
        assert caught

    def test_message_preserved(self):
        err = RateLimitError("Gemini API rate limited after 3 retries")
        assert "3 retries" in str(err)
