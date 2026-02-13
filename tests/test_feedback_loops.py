"""
Regression tests for agent feedback loops and data flow.

Ensures agents properly pass data to downstream agents and that
silent failures (like the Quill/Sage revision loop bug) don't recur.

Run with: python -m pytest tests/test_feedback_loops.py -v
"""

import pytest
import sys
import os
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pipeline.db import Database, Article, ArticleStatus, _get_article_fields


# ---------------------------------------------------------------------------
# update_article() field validation
# ---------------------------------------------------------------------------

class TestUpdateArticleFieldValidation:
    """Prevent silent data loss from typos in update_article() kwargs."""

    def setup_method(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self._tmp.name)
        self.article = self.db.create_article(
            title="Test Article",
            target_keyword="test keyword",
            status=ArticleStatus.REVIEW.value,
            markdown_content="Some content",
        )

    def test_valid_fields_accepted(self):
        result = self.db.update_article(
            self.article.id,
            title="Updated Title",
            seo_score=85.0,
            revision_count=1,
        )
        assert result.title == "Updated Title"
        assert result.seo_score == 85.0

    def test_invalid_field_raises(self):
        with pytest.raises(ValueError, match="unknown fields"):
            self.db.update_article(
                self.article.id,
                recission_count=2,  # typo — should be revision_count
            )

    def test_multiple_invalid_fields_raises(self):
        with pytest.raises(ValueError, match="unknown fields"):
            self.db.update_article(
                self.article.id,
                title="OK",
                nonexistent_field="bad",
                another_typo="worse",
            )


# ---------------------------------------------------------------------------
# Quill revision issue parser
# ---------------------------------------------------------------------------

class TestParseRevisionIssues:
    """Ensure Sage's formatted notes are fully parsed by Quill."""

    def setup_method(self):
        # Import lazily to avoid requiring full LLM config
        from pipeline.agents.quill import QuillAgent
        self.parse = QuillAgent._parse_revision_issues

    def test_parses_top_level_issues(self):
        notes = (
            "### Issues to Fix:\n"
            "- Keyword density too low\n"
            "- No FAQ section found\n"
        )
        issues = self.parse(notes)
        assert len(issues) == 2
        assert "Keyword density too low" in issues

    def test_parses_indented_sub_issues(self):
        notes = (
            "### Breakdown:\n"
            "- **seo**: 15/20\n"
            "  - Keyword not in H1\n"
            "  - No internal links to published articles\n"
            "- **readability**: 10/15\n"
            "  - Avg sentence length 32 words (target: <=25)\n"
        )
        issues = self.parse(notes)
        assert len(issues) == 3
        assert "Keyword not in H1" in issues
        assert "No internal links to published articles" in issues
        assert any("sentence length" in i for i in issues)

    def test_skips_category_headers(self):
        notes = (
            "- **plagiarism**: 16/20\n"
            "  - Minor overlap with competitor\n"
        )
        issues = self.parse(notes)
        # Should get the sub-issue but NOT the header
        assert len(issues) == 1
        assert "Minor overlap" in issues[0]
        assert "**plagiarism**" not in str(issues)

    def test_deduplicates_issues(self):
        notes = (
            "### Breakdown:\n"
            "- **seo**: 15/20\n"
            "  - Keyword density too low\n"
            "### Issues to Fix:\n"
            "- Keyword density too low\n"
        )
        issues = self.parse(notes)
        assert len(issues) == 1

    def test_skips_score_carried_lines(self):
        notes = "- Plagiarism score carried from round 1: 16/20\n"
        issues = self.parse(notes)
        assert len(issues) == 0

    def test_full_sage_output(self):
        notes = (
            "## Review Summary — Score: 55/100 — Decision: REVISION\n"
            "\n"
            "### Breakdown:\n"
            "- **plagiarism**: 16/20\n"
            "  - [LLM originality] Some passages are generic\n"
            "- **seo**: 12/20\n"
            "  - Keyword not in first 100 words\n"
            "  - No FAQ section\n"
            "- **readability**: 10/15\n"
            "  - Sentences too long (avg 28 words)\n"
            "- **factual_accuracy**: 14/20\n"
            "- **internal_links**: 0/10\n"
            "  - No internal links (published articles available)\n"
            "- **word_count**: 3/5\n"
            "  - Word count 1543 (target: 1800-2200)\n"
            "- **cta**: 0/5\n"
            "  - No mention of ClaimCoach or claimcoach.app\n"
            "- **legal_compliance**: 5/5\n"
            "\n"
            "### Issues to Fix:\n"
            "- Keyword not in first 100 words\n"
            "- No FAQ section\n"
            "- Sentences too long (avg 28 words)\n"
            "- No internal links (published articles available)\n"
            "- Word count 1543 (target: 1800-2200)\n"
            "- No mention of ClaimCoach or claimcoach.app\n"
        )
        issues = self.parse(notes)
        # Should have at least 6 unique issues from both sections
        assert len(issues) >= 6
        assert any("FAQ" in i for i in issues)
        assert any("ClaimCoach" in i for i in issues)
        assert any("internal links" in i for i in issues)


# ---------------------------------------------------------------------------
# Article field consistency
# ---------------------------------------------------------------------------

class TestArticleFieldConsistency:
    """Ensure the Article dataclass fields match what agents expect."""

    def test_critical_fields_exist(self):
        """Fields that agents depend on must be in the Article dataclass."""
        required_fields = {
            "revision_count", "revision_notes", "markdown_content",
            "seo_score", "readability_score", "validation_notes",
            "published_url", "social_status", "publisher_claim",
            "herald_claim", "writer_claim", "editor_claim",
            "word_count", "title", "slug", "status",
        }
        actual = _get_article_fields()
        missing = required_fields - actual
        assert not missing, f"Article dataclass missing fields: {missing}"

    def test_no_sql_injection_in_field_names(self):
        """Field names must be safe for SQL interpolation."""
        for field in _get_article_fields():
            assert field.isidentifier(), f"Unsafe field name: {field}"
            assert " " not in field
            assert ";" not in field
