"""Sage agent — quality gate.

Reviews articles against a 100-point rubric covering plagiarism,
SEO, readability, factual accuracy, and compliance.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus
from pipeline.utils.readability import readability_report, word_count
from pipeline.utils.seo import detect_faq_section, extract_links, score_seo

logger = logging.getLogger(__name__)

# Phrases that indicate unauthorized legal/medical/promissory claims
# Derived from PRODUCT_CONTEXT.md "Language to NEVER Use" section
FLAGGED_PHRASES = [
    # Legal advice
    "you are legally entitled",
    "the law requires",
    "this constitutes legal advice",
    "sue your insurance company",
    "take legal action",
    # Guarantees / promissory language
    "claimcoach will get you more money",
    "guaranteed results",
    "100% success rate",
    "guaranteed to get",
    "you will receive",
    "we guarantee",
    "get thousands more",
    "recover thousands",
    "you are owed at least",
    # False product claims
    "claimcoach negotiates for you",
    "claimcoach will negotiate",
    "negotiate on your behalf",
    "claimcoach files",
    "claimcoach will file",
    "legally binding analysis",
    # Future features (must not reference)
    "upload your settlement letter",
    "upload your policy",
    "our ai reviews your policy",
    "generates your dispute letter",
    "generate a dispute letter",
    "chat assistant",
    "status tracking",
    # Unverified data claims
    "average user recovers",
]


class SageAgent(BaseAgent):
    name = "sage"
    claim_field = "editor_claim"

    def run(self) -> dict[str, Any]:
        """Review all articles in 'review' status."""
        # Load performance lessons to calibrate scoring thresholds
        self._calibration = self._load_calibration()

        articles = self.db.query_articles(status=ArticleStatus.REVIEW.value, limit=20)
        if not articles:
            logger.info("No articles to review")
            return {"status": "idle", "reviewed": 0}

        results = []
        for article in articles:
            # Claim the article
            claim_id = self.generate_claim_id()
            if not self.db.try_claim(
                article.id, "editor_claim", claim_id, ArticleStatus.REVIEW.value
            ):
                continue

            try:
                result = self._review_article(article)
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Error reviewing article {article.id}: {e}", exc_info=True
                )
                # Release the claim so the article can be retried on the next run
                self.db.update_article(article.id, editor_claim="")

        approved = sum(1 for r in results if r["decision"] == "approved")
        revision = sum(1 for r in results if r["decision"] == "revision")
        rejected = sum(1 for r in results if r["decision"] == "rejected")

        self.db.record_metric(
            "sage_run",
            len(results),
            json.dumps({"approved": approved, "revision": revision, "rejected": rejected}),
        )

        logger.info(
            f"Sage reviewed {len(results)} articles: "
            f"{approved} approved, {revision} revision, {rejected} rejected"
        )
        return {
            "status": "success",
            "reviewed": len(results),
            "approved": approved,
            "revision": revision,
            "rejected": rejected,
        }

    def _review_article(self, article) -> dict:
        """Run the full review rubric on an article."""
        scores: dict[str, dict] = {}
        total_score = 0.0
        all_issues: list[str] = []

        content = article.markdown_content or ""

        # 1. Plagiarism check (20 pts) — uses Copyscape if available, else skip
        plag_score, plag_issues = self._check_plagiarism(content)
        scores["plagiarism"] = {"score": plag_score, "max": 20, "issues": plag_issues}
        total_score += plag_score
        all_issues.extend(plag_issues)

        # 2. SEO score (20 pts)
        internal_links, external_links = extract_links(content)
        has_faq = detect_faq_section(content)
        seo_raw, seo_issues = score_seo(
            content=content,
            title=article.title,
            keyword=article.target_keyword,
            meta_description=article.meta_description,
            internal_links=internal_links,
            external_links=external_links,
            has_faq=has_faq,
        )
        scores["seo"] = {"score": seo_raw, "max": 20, "issues": seo_issues}
        total_score += seo_raw
        all_issues.extend(seo_issues)

        # 3. Readability (15 pts)
        read_report = readability_report(content)
        read_score = 0.0
        read_issues = read_report["issues"]
        if read_report["flesch_kincaid"] >= 60:
            read_score += 10
        elif read_report["flesch_kincaid"] >= 50:
            read_score += 5
        if read_report["avg_sentence_length"] <= 25:
            read_score += 5
        elif read_report["avg_sentence_length"] <= 30:
            read_score += 2.5
        scores["readability"] = {"score": read_score, "max": 15, "issues": read_issues}
        total_score += read_score
        all_issues.extend(read_issues)

        # 4. Factual accuracy (20 pts) — AI check if available, rule-based otherwise
        fact_score, fact_issues = self._check_facts(content, article)
        scores["factual_accuracy"] = {"score": fact_score, "max": 20, "issues": fact_issues}
        total_score += fact_score
        all_issues.extend(fact_issues)

        # 5. Internal links valid (10 pts)
        link_score = 0.0
        link_issues = []
        published = self.db.get_published_articles()
        published_slugs = {a.slug for a in published if a.slug}
        published_urls = {a.published_url for a in published if a.published_url}
        if internal_links:
            valid = 0
            for link in internal_links:
                # Check if the link matches a published article
                if link in published_urls or any(s in link for s in published_slugs):
                    valid += 1
            if len(internal_links) > 0 and valid == len(internal_links):
                link_score = 10
            elif valid > 0:
                link_score = 5
                link_issues.append(f"{len(internal_links) - valid} internal links point to unpublished articles")
            elif len(published) <= 3:
                # Grace period: links exist but nothing is published yet
                link_score = 8
                link_issues.append("Internal links present but no published articles to validate against (grace period)")
            else:
                link_issues.append("Internal links don't match published articles")
        else:
            # No internal links at all
            if len(published) > 3:
                link_issues.append("No internal links (published articles available)")
            else:
                link_score = 8  # Grace period when few articles published
                link_issues.append("No internal links (few published articles — grace period)")
        scores["internal_links"] = {"score": link_score, "max": 10, "issues": link_issues}
        total_score += link_score
        all_issues.extend(link_issues)

        # 6. Word count (5 pts) — calibrated from GSC performance data
        wc = word_count(content)
        wc_score = 0.0
        wc_issues = []
        target_lo, target_hi = self._calibration.get("word_count_target", (1800, 2200))
        ok_lo, ok_hi = self._calibration.get("word_count_ok", (1500, 2500))
        if target_lo <= wc <= target_hi:
            wc_score = 5
        elif ok_lo <= wc <= ok_hi:
            wc_score = 3
            wc_issues.append(f"Word count {wc} (target: {target_lo}-{target_hi})")
        else:
            wc_issues.append(f"Word count {wc} far from target ({target_lo}-{target_hi})")
        scores["word_count"] = {"score": wc_score, "max": 5, "issues": wc_issues}
        total_score += wc_score
        all_issues.extend(wc_issues)

        # 7. CTA present (5 pts)
        cta_score, cta_issues = self._check_cta(content)
        scores["cta"] = {"score": cta_score, "max": 5, "issues": cta_issues}
        total_score += cta_score
        all_issues.extend(cta_issues)

        # 8. No legal advice (5 pts)
        legal_score, legal_issues = self._check_legal_compliance(content)
        scores["legal_compliance"] = {"score": legal_score, "max": 5, "issues": legal_issues}
        total_score += legal_score
        all_issues.extend(legal_issues)

        # Decision — all non-passing articles enter the revision loop so
        # Quill can automatically improve them using Sage's feedback.
        # Articles are only rejected when max revision rounds are exhausted.
        total_score = round(total_score, 1)
        threshold = self.config.pipeline.approval_score_threshold
        if total_score >= threshold:
            decision = "approved"
            new_status = ArticleStatus.READY_TO_PUBLISH.value
        elif article.revision_count >= self.config.pipeline.max_revision_rounds:
            decision = "rejected"
            new_status = ArticleStatus.REJECTED.value
        else:
            decision = "revision"
            new_status = ArticleStatus.REVISION.value

        # Format revision notes
        revision_notes = self._format_review(scores, total_score, decision, all_issues)

        if decision == "rejected":
            revision_notes += "\n\n[REJECTED: Maximum revision rounds exceeded]"

        # Update article
        update_kwargs = {
            "status": new_status,
            "seo_score": seo_raw,
            "readability_score": read_report["flesch_kincaid"],
            "word_count": wc,
            "editor_claim": "",  # Always release Sage's claim after decision
            "validation_status": "pass" if decision == "approved" else "fail",
            "validation_notes": revision_notes,
        }
        if decision == "revision":
            existing_notes = article.revision_notes or ""
            separator = "\n\n---\n\n" if existing_notes else ""
            update_kwargs["revision_notes"] = existing_notes + separator + revision_notes
            update_kwargs["revision_count"] = article.revision_count + 1
            update_kwargs["writer_claim"] = ""  # Release for Quill to pick up
        elif decision == "rejected":
            update_kwargs["revision_notes"] = revision_notes

        self.db.update_article(article.id, **update_kwargs)

        # Record generalizable lessons for other agents
        self._record_lessons(scores, all_issues, decision, article)

        # Notify dashboard
        self._notify_dashboard(article.id, decision, total_score)

        logger.info(
            f"Article {article.id} ({article.title}): "
            f"score={total_score}/100 -> {decision}"
        )

        return {
            "article_id": article.id,
            "title": article.title,
            "score": total_score,
            "decision": decision,
            "scores": scores,
        }

    def _check_plagiarism(self, content: str) -> tuple[float, list[str]]:
        """Check for plagiarism. Uses Copyscape if configured, else gives full marks."""
        if not self.config.copyscape.api_key:
            # No Copyscape configured — give benefit of the doubt but flag
            return 18.0, ["Plagiarism check skipped (Copyscape not configured)"]

        try:
            import requests

            params = {
                "u": self.config.copyscape.username,
                "o": self.config.copyscape.api_key,
                "t": content[:5000],  # Copyscape text limit
                "f": "json",
            }
            resp = requests.post(
                "https://www.copyscape.com/api/", data=params, timeout=30
            )
            data = resp.json()

            if data.get("count", 0) == 0:
                return 20.0, []
            else:
                matches = data.get("result", [])
                return 0.0, [f"Plagiarism detected: {len(matches)} matches found"]
        except Exception as e:
            logger.warning(f"Copyscape check failed: {e}")
            return 15.0, [f"Plagiarism check error: {e}"]

    def _check_facts(self, content: str, article) -> tuple[float, list[str]]:
        """Check factual accuracy against product context and state rules."""
        issues = []
        score = 20.0

        content_lower = content.lower()

        # Check for false product claims (aligned with PRODUCT_CONTEXT.md)
        false_claims = [
            ("negotiate on your behalf", "Claims ClaimCoach negotiates with insurers"),
            ("negotiate with insurance", "Implies ClaimCoach negotiates directly"),
            ("communicates with.*adjuster", "Claims ClaimCoach communicates with adjusters"),
            ("file a claim for you", "Claims ClaimCoach files claims"),
            ("file your dispute", "Claims ClaimCoach files disputes"),
            ("files? your appeal", "Claims ClaimCoach files appeals"),
            ("upload your policy", "References future feature (PDF uploads)"),
            ("upload your settlement", "References future feature (document uploads)"),
            ("generates? (?:a |your )?dispute letter", "References future feature (dispute letters)"),
            ("chat assistant", "References future feature (chat assistant)"),
            ("status tracking", "References future feature (status tracking)"),
            ("pull(?:s)? comparable.*listings", "References future feature (auto comp pulls)"),
            ("analyze your health insurance", "References non-auto insurance"),
            ("analyze your homeowner", "References non-auto insurance"),
            ("analyze your renter", "References non-auto insurance"),
            ("access insurance.*database", "Claims database access"),
            ("access.*ccc one", "Claims access to CCC ONE"),
            ("binding valuation", "Claims legally binding valuations"),
            ("legally enforceable appraisal", "Claims legally enforceable appraisals"),
        ]

        for pattern, issue in false_claims:
            if re.search(pattern, content_lower):
                issues.append(f"FACTUAL ERROR: {issue}")
                score -= 5

        # If AI is available, do a deeper fact check
        if self.has_llm:
            try:
                ai_issues = self._ai_fact_check(content, article)
                issues.extend(ai_issues)
                score -= len(ai_issues) * 2
            except Exception as e:
                logger.warning(f"AI fact check failed: {e}")
        else:
            # Without AI verification, cap at 10/20 to reflect the uncertainty.
            # Regex patterns only catch known-bad claims; real factual errors
            # (wrong thresholds, incorrect dollar amounts) require AI.
            score = min(score, 10.0)
            issues.append("Factual accuracy capped at 10/20 (no LLM configured for deep check)")

        return max(0, score), issues

    def _ai_fact_check(self, content: str, article) -> list[str]:
        """Use Claude to check facts against reference docs."""
        product_context = self.config.load_product_context()

        prompt = f"""Review this article for factual accuracy. Check it against the product context below.

=== PRODUCT CONTEXT ===
{product_context}

=== ARTICLE ===
Title: {article.title}
Target State: {article.target_state}

{content[:3000]}

List ONLY factual errors or unauthorized claims. If none found, respond with "NO ISSUES".
Format each issue on its own line starting with "- "."""

        result = self.call_claude(
            prompt,
            model=self.strategy_model,
            max_tokens=500,
        )

        if "NO ISSUES" in result.upper():
            return []

        issues = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                issues.append(f"AI fact check: {line[2:]}")
        return issues

    def _check_cta(self, content: str) -> tuple[float, list[str]]:
        """Check for ClaimCoach CTA."""
        issues = []
        score = 0.0

        content_lower = content.lower()
        if "claimcoach" in content_lower:
            score += 2.5
        else:
            issues.append("No mention of ClaimCoach")

        if "claimcoach.app" in content_lower:
            score += 2.5
        else:
            issues.append("No link to claimcoach.app")

        return score, issues

    def _check_legal_compliance(self, content: str) -> tuple[float, list[str]]:
        """Check for unauthorized legal advice."""
        issues = []
        content_lower = content.lower()

        for phrase in FLAGGED_PHRASES:
            if phrase in content_lower:
                issues.append(f"Flagged phrase: '{phrase}'")

        if issues:
            return 0.0, issues
        return 5.0, []

    def _load_calibration(self) -> dict:
        """Load performance lessons from Morgan to calibrate scoring.

        If GSC data shows that articles at 2400 words outperform 1800-word
        articles, the word count target shifts. If high readability correlates
        strongly with clicks, readability weight stays high.
        """
        lessons = self.db.get_lessons("sage")
        # Also read Morgan's lessons for Quill about performance — Sage uses
        # these to calibrate its own scoring rather than drift from reality.
        quill_perf = self.db.get_lessons("quill", category="performance")

        calibration: dict[str, Any] = {
            "word_count_target": (1800, 2200),  # default
            "word_count_ok": (1500, 2500),       # default acceptable range
        }

        for lesson in quill_perf:
            text = lesson.lesson.lower()
            # Parse "Top-performing articles average 2400 words"
            if "average" in text and "words" in text:
                import re as _re
                match = _re.search(r"average\s+(\d+)\s+words", text)
                if match and lesson.confidence >= 0.4:
                    avg = int(match.group(1))
                    # Shift target toward what actually performs
                    calibration["word_count_target"] = (avg - 200, avg + 200)
                    calibration["word_count_ok"] = (avg - 500, avg + 500)
                    logger.info(
                        f"Calibrated word count target to {avg-200}-{avg+200} "
                        f"(GSC data, confidence={lesson.confidence:.1f})"
                    )

        return calibration

    def _record_lessons(
        self, scores: dict, all_issues: list[str], decision: str, article
    ) -> None:
        """Extract generalizable lessons from this review and store for other agents."""
        # Failure lessons → Quill: what to avoid
        if decision in ("revision", "rejected"):
            for category, data in scores.items():
                if data["score"] < data["max"]:
                    for issue in data.get("issues", []):
                        normalized = self._normalize_issue(issue)
                        if normalized:
                            self.record_lesson("quill", category, normalized)

        # Success lessons → Quill: what works
        if decision == "approved" and article.revision_count == 0:
            self.record_lesson(
                "quill", "success_pattern",
                f"first_draft_pass category={article.content_category or 'general'}",
            )

        # Category pass/fail → Scout: which topics are feasible
        if article.content_category:
            if decision == "approved":
                self.record_lesson("scout", "high_pass_category", article.content_category)
            elif decision in ("revision", "rejected"):
                self.record_lesson("scout", "low_pass_category", article.content_category)

    @staticmethod
    def _normalize_issue(issue: str) -> str:
        """Normalize issue text for consistent matching across articles.

        Strips article-specific numbers so "Word count 1543" and "Word count 1678"
        both become "Word count N" and count as the same lesson.
        """
        if not issue or len(issue) < 5:
            return ""
        # Strip 3+ digit numbers (word counts, scores)
        normalized = re.sub(r"\b\d{3,}\b", "N", issue)
        # Strip floats in parentheses: "(55.2)" → "(N)"
        normalized = re.sub(r"\([\d.]+\)", "(N)", normalized)
        return normalized.strip()

    def _format_review(
        self,
        scores: dict,
        total: float,
        decision: str,
        issues: list[str],
    ) -> str:
        """Format the review as human-readable notes."""
        lines = [
            f"## Review Summary — Score: {total}/100 — Decision: {decision.upper()}",
            "",
            "### Breakdown:",
        ]
        for category, data in scores.items():
            lines.append(f"- **{category}**: {data['score']}/{data['max']}")
            for issue in data.get("issues", []):
                lines.append(f"  - {issue}")

        if issues:
            lines.append("")
            lines.append("### Issues to Fix:")
            for issue in issues:
                lines.append(f"- {issue}")

        return "\n".join(lines)

    def _notify_dashboard(self, article_id: int, decision: str, score: float) -> None:
        """Send webhook notification to dashboard when article is ready for review."""
        try:
            dashboard_url = self.config.pipeline.dashboard_url
            if not dashboard_url:
                # Dashboard not configured, skip notification
                return

            webhook_url = f"{dashboard_url}/api/notifications/article-ready"

            payload = {
                "article_id": article_id,
                "status": decision,
                "score": score,
                "agent": "sage",
                "message": f"Article {article_id} is ready for review (score: {score}/100)"
            }

            response = requests.post(
                webhook_url,
                json=payload,
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"Dashboard notified about article {article_id}")
            else:
                logger.warning(f"Dashboard notification failed: {response.status_code}")

        except Exception as e:
            # Don't fail the review if notification fails
            logger.warning(f"Failed to notify dashboard: {e}")
