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

from content_quality.validators.math_validator import MathValidator
from content_quality.validators.product_validator import ProductClaimValidator
from content_quality.validators.state_validator import StateRegulationValidator
from pipeline.agents.base import BaseAgent, RateLimitError
from pipeline.db import ArticleStatus
from pipeline.utils.freshness import freshness_score as calc_freshness
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
        """Auto-score articles in 'editor_review' status."""
        # Load performance lessons to calibrate scoring thresholds
        self._calibration = self._load_calibration()

        articles = self.db.query_articles(status=ArticleStatus.EDITOR_REVIEW.value, limit=20)
        if not articles:
            logger.info("No articles to review")
            return {"status": "idle", "reviewed": 0}

        results = []
        for article in articles:
            # Claim the article
            claim_id = self.generate_claim_id()
            if not self.db.try_claim(
                article.id, "editor_claim", claim_id, ArticleStatus.EDITOR_REVIEW.value
            ):
                continue

            try:
                result = self._review_article(article)
                results.append(result)
            except RateLimitError as e:
                # Rate limit — release the claim but keep status as
                # EDITOR_REVIEW so Sage picks it up again on the next run.
                logger.warning(
                    f"Rate limited reviewing article {article.id}: {e}"
                )
                self.db.update_article(article.id, editor_claim="")
                self.db.record_metric("rate_limit", 0, json.dumps({
                    "agent": "sage", "article_id": article.id,
                }))
            except Exception as e:
                logger.error(
                    f"Error reviewing article {article.id}: {e}", exc_info=True
                )
                # Release the claim and send back to REVISION so Quill can retry.
                existing_notes = article.revision_notes or ""
                separator = "\n\n---\n\n" if existing_notes else ""
                error_note = f"[SAGE ERROR] Review failed: {e}. Sent back for revision."
                self.db.update_article(
                    article.id,
                    editor_claim="",
                    status=ArticleStatus.REVISION.value,
                    revision_notes=existing_notes + separator + error_note,
                    writer_claim="",
                )

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

        # ── 100-POINT RUBRIC (10 categories) ──
        #
        # Plagiarism:          17 pts
        # SEO:                 18 pts
        # Readability:         15 pts  (8 sub-metrics)
        # Factual + Math:      18 pts  (AI/regex + math validator)
        # Internal links:       8 pts
        # Word count:           4 pts
        # CTA:                  5 pts  (placement-aware)
        # Legal compliance:     5 pts
        # Media & Formatting:  10 pts  (NEW: images, scanability, schema)
        # ────────────────────────────
        # Total:              100 pts

        # 1. Plagiarism check (17 pts)
        # On revision rounds, reuse the score from round 1 — content
        # originality is inherent and doesn't change between edits.
        if article.revision_count > 0:
            plag_score, plag_issues = self._reuse_plagiarism_score(article)
        else:
            plag_score, plag_issues = self._check_plagiarism(content, article)
        # Scale: plagiarism checks return 0-20, normalize to 0-17
        plag_score = round(plag_score * 17 / 20, 1)
        scores["plagiarism"] = {"score": plag_score, "max": 17, "issues": plag_issues}
        total_score += plag_score
        all_issues.extend(plag_issues)

        # 2. SEO score (18 pts)
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
        # Scale: score_seo returns 0-20, normalize to 0-18
        seo_scaled = round(seo_raw * 18 / 20, 1)

        # Freshness check — stale year references hurt rankings.
        # Deduct up to 2 pts from SEO for poor freshness signals.
        fresh = calc_freshness(content)
        fresh_score = fresh["score"]  # 0-3 scale
        freshness_deduction = 0.0
        if fresh_score < 1.0:
            freshness_deduction = 2.0
        elif fresh_score < 2.0:
            freshness_deduction = 1.0
        elif fresh_score < 2.5:
            freshness_deduction = 0.5
        if freshness_deduction > 0:
            seo_scaled = max(0, round(seo_scaled - freshness_deduction, 1))
        seo_issues.extend(f"[Freshness] {i}" for i in fresh.get("issues", []))

        scores["seo"] = {"score": seo_scaled, "max": 18, "issues": seo_issues}
        total_score += seo_scaled
        all_issues.extend(seo_issues)

        # 3. Readability (15 pts — 8 sub-metrics)
        read_report = readability_report(content)
        read_score = 0.0
        read_issues = read_report["issues"]
        # 3a. Flesch-Kincaid (3 pts)
        if read_report["flesch_kincaid"] >= 60:
            read_score += 3
        elif read_report["flesch_kincaid"] >= 50:
            read_score += 1.5
        # 3b. Sentence length (2 pts)
        if read_report["avg_sentence_length"] <= 25:
            read_score += 2
        elif read_report["avg_sentence_length"] <= 30:
            read_score += 1
        # 3c. Paragraph length (2 pts)
        if read_report["avg_paragraph_length"] <= 4:
            read_score += 2
        elif read_report["avg_paragraph_length"] <= 5:
            read_score += 1
        # 3d. Passive voice (2 pts) — target ≤15%
        if read_report["passive_voice_ratio"] <= 0.10:
            read_score += 2
        elif read_report["passive_voice_ratio"] <= 0.15:
            read_score += 1.5
        elif read_report["passive_voice_ratio"] <= 0.25:
            read_score += 0.5
        # 3e. Transition words (2 pts) — target ≥25%
        if read_report["transition_word_score"] >= 0.30:
            read_score += 2
        elif read_report["transition_word_score"] >= 0.25:
            read_score += 1.5
        elif read_report["transition_word_score"] >= 0.15:
            read_score += 0.5
        # 3f. Sentence variety (1.5 pts) — target CV ≥0.40
        if read_report["sentence_length_variety"] >= 0.50:
            read_score += 1.5
        elif read_report["sentence_length_variety"] >= 0.40:
            read_score += 1
        elif read_report["sentence_length_variety"] >= 0.30:
            read_score += 0.5
        # 3g. Complex word density (1.5 pts) — target ≤8%
        if read_report["complex_word_density"] <= 0.05:
            read_score += 1.5
        elif read_report["complex_word_density"] <= 0.08:
            read_score += 1
        elif read_report["complex_word_density"] <= 0.12:
            read_score += 0.5
        # 3h. Heading structure (1 pt)
        headings = read_report["heading_structure"]
        if headings["h2_count"] >= 3 and not headings["issues"]:
            read_score += 1
        elif headings["h2_count"] >= 2:
            read_score += 0.5
        scores["readability"] = {"score": read_score, "max": 15, "issues": read_issues}
        total_score += read_score
        all_issues.extend(read_issues)

        # 4. Factual + Math accuracy (18 pts)
        # 4a. Factual claims (15 pts from AI/regex)
        fact_score, fact_issues = self._check_facts(content, article)
        # Scale: _check_facts returns 0-20, normalize to 0-15
        fact_score = round(min(fact_score, 20.0) * 15 / 20, 1)
        # 4b. Math accuracy (3 pts from MathValidator)
        math_score, math_issues = self._check_math(content)
        fact_total = fact_score + math_score
        all_fact_issues = fact_issues + math_issues
        scores["factual_accuracy"] = {"score": fact_total, "max": 18, "issues": all_fact_issues}
        total_score += fact_total
        all_issues.extend(all_fact_issues)

        # 5. Internal links (8 pts: 6 validity + 2 anchor text quality)
        link_score = 0.0
        link_issues = []
        published = self.db.get_published_articles()
        published_slugs = {a.slug for a in published if a.slug}
        published_urls = {a.published_url for a in published if a.published_url}
        if internal_links:
            valid = 0
            for link in internal_links:
                if link in published_urls or any(s in link for s in published_slugs):
                    valid += 1
            if len(internal_links) > 0 and valid == len(internal_links):
                link_score = 6
            elif valid > 0:
                link_score = 3
                link_issues.append(f"{len(internal_links) - valid} internal links point to unpublished articles")
            elif len(published) <= 3:
                link_score = 5
                link_issues.append("Internal links present but no published articles to validate against (grace period)")
            else:
                link_issues.append("Internal links don't match published articles")

            # Anchor text quality (2 pts) — check anchor text is descriptive,
            # not "click here" or raw URLs
            anchor_score, anchor_issues = self._check_anchor_text_quality(content)
            link_score += anchor_score
            link_issues.extend(anchor_issues)
        else:
            if len(published) > 3:
                link_issues.append("No internal links (published articles available)")
            else:
                link_score = 5
                link_issues.append("No internal links (few published articles — grace period)")
        scores["internal_links"] = {"score": link_score, "max": 8, "issues": link_issues}
        total_score += link_score
        all_issues.extend(link_issues)

        # 6. Word count (4 pts) — calibrated from GSC performance data
        wc = word_count(content)
        wc_score = 0.0
        wc_issues = []
        target_lo, target_hi = self._calibration.get("word_count_target", (1800, 2200))
        ok_lo, ok_hi = self._calibration.get("word_count_ok", (1500, 2500))
        if target_lo <= wc <= target_hi:
            wc_score = 4
        elif ok_lo <= wc <= ok_hi:
            wc_score = 2
            wc_issues.append(f"Word count {wc} (target: {target_lo}-{target_hi})")
        else:
            wc_issues.append(f"Word count {wc} far from target ({target_lo}-{target_hi})")
        scores["word_count"] = {"score": wc_score, "max": 4, "issues": wc_issues}
        total_score += wc_score
        all_issues.extend(wc_issues)

        # 7. CTA quality (5 pts — placement-aware)
        cta_score, cta_issues = self._check_cta(content)
        scores["cta"] = {"score": cta_score, "max": 5, "issues": cta_issues}
        total_score += cta_score
        all_issues.extend(cta_issues)

        # 8. No legal advice (5 pts)
        legal_score, legal_issues = self._check_legal_compliance(content)
        scores["legal_compliance"] = {"score": legal_score, "max": 5, "issues": legal_issues}
        total_score += legal_score
        all_issues.extend(legal_issues)

        # 9. Media & Formatting (10 pts — NEW)
        media_score, media_issues = self._check_media_and_formatting(read_report)
        scores["media_formatting"] = {"score": media_score, "max": 10, "issues": media_issues}
        total_score += media_score
        all_issues.extend(media_issues)

        # 9. State regulation accuracy
        state_validator = StateRegulationValidator()
        state_result = state_validator.validate(content, article.target_state)
        state_accuracy = state_result["status"]  # PASS / WARN / FAIL
        for issue in state_result.get("issues", []):
            msg = issue if isinstance(issue, str) else issue.get("message", str(issue))
            all_issues.append(f"[State Accuracy] {msg}")

        # 10. Product compliance
        product_validator = ProductClaimValidator()
        product_result = product_validator.validate(content)
        product_compliance = product_result["status"]  # PASS / FAIL
        for v in product_result.get("hard_violations", []):
            all_issues.append(f"[Product Compliance] {v.get('suggestion', v.get('matched_text', ''))}")

        # ── Critical violation detection ──
        # Legal compliance = 0, product FAIL, or state FAIL are critical
        # issues that can't be trusted through 5 revision rounds. Cap at 2
        # rounds to save tokens and prevent publishing risk.
        has_critical = (
            legal_score == 0
            or product_compliance == "FAIL"
            or state_accuracy == "FAIL"
        )
        critical_reasons: list[str] = []
        if legal_score == 0:
            critical_reasons.append("legal compliance violation")
        if product_compliance == "FAIL":
            critical_reasons.append("unauthorized product claims")
        if state_accuracy == "FAIL":
            critical_reasons.append("incorrect state regulation facts")

        max_rounds = 2 if has_critical else self.config.pipeline.max_revision_rounds

        # Decision — all non-passing articles enter the revision loop so
        # Quill can automatically improve them using Sage's feedback.
        # Critical violations get max 2 rounds; standard issues get 5.
        total_score = round(total_score, 1)
        threshold = self.config.pipeline.approval_score_threshold
        if total_score >= threshold and not has_critical:
            decision = "approved"
            new_status = ArticleStatus.REVIEW.value  # Human review before publish
        elif total_score >= threshold and has_critical:
            # Score is high enough BUT critical violations remain — cannot
            # approve content with legal/product/state issues regardless
            # of score. Send back for targeted fix.
            decision = "revision"
            new_status = ArticleStatus.REVISION.value
        elif article.revision_count >= max_rounds:
            decision = "rejected"
            new_status = ArticleStatus.REJECTED.value
        else:
            decision = "revision"
            new_status = ArticleStatus.REVISION.value

        # Format revision notes
        revision_notes = self._format_review(scores, total_score, decision, all_issues)

        if decision == "rejected" and has_critical:
            revision_notes += (
                f"\n\n[REJECTED: Critical violation(s) not resolved after "
                f"{article.revision_count} revision(s): {', '.join(critical_reasons)}]"
            )
        elif decision == "rejected":
            revision_notes += "\n\n[REJECTED: Maximum revision rounds exceeded]"

        if decision == "revision" and has_critical:
            revision_notes += (
                f"\n\n[CRITICAL: {', '.join(critical_reasons).upper()} — "
                f"fix immediately or article will be rejected after "
                f"{max_rounds - article.revision_count} more round(s)]"
            )

        # Update article
        update_kwargs = {
            "status": new_status,
            "sage_score": total_score,
            "seo_score": seo_scaled,
            "readability_score": read_report["flesch_kincaid"],
            "word_count": wc,
            "editor_claim": "",  # Always release Sage's claim after decision
            "validation_status": "pass" if decision == "approved" else "fail",
            "validation_notes": revision_notes,
            "state_accuracy": state_accuracy,
            "product_compliance": product_compliance,
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

    def _check_plagiarism(self, content: str, article=None) -> tuple[float, list[str]]:
        """Check for plagiarism / originality.

        Priority:
          1. Copyscape API (paid, most reliable) — if configured
          2. LLM originality review (free, uses Gemini/Claude) — if LLM available
          3. Skip with 14/20 benefit-of-the-doubt — no check possible
        """
        # ── Tier 1: Copyscape (gold standard) ──
        if self.config.copyscape.api_key:
            return self._copyscape_check(content)

        # ── Tier 2: LLM-based originality review (free) ──
        if self.has_llm:
            return self._llm_originality_check(content, article)

        # ── Tier 3: No check available ──
        return 14.0, ["Plagiarism check skipped (no Copyscape or LLM configured)"]

    def _copyscape_check(self, content: str) -> tuple[float, list[str]]:
        """Check plagiarism via Copyscape API."""
        try:
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

    def _llm_originality_check(self, content: str, article=None) -> tuple[float, list[str]]:
        """Use LLM to assess content originality (free alternative to Copyscape).

        Checks for:
        - Generic boilerplate that appears on many insurance sites
        - Lack of specific, original analysis
        - Templated filler paragraphs
        - Whether content is meaningfully tailored to the target keyword/state
        """
        keyword = getattr(article, "target_keyword", "unknown") if article else "unknown"
        title = getattr(article, "title", "") if article else ""

        # Send first ~3000 chars to keep token cost low (utility model)
        excerpt = content[:3000]

        prompt = f"""You are a plagiarism and originality reviewer for insurance content.

Article title: {title}
Target keyword: {keyword}

Evaluate the following article excerpt for originality. Score it 0-20 based on:
- 20: Highly original with specific, unique analysis and examples
- 15-19: Mostly original but has some generic passages
- 10-14: Contains significant boilerplate or templated content
- 5-9: Mostly generic content found on many insurance websites
- 0-4: Appears to be copied or entirely templated

Look for:
1. Generic boilerplate (e.g. "insurance companies are not on your side" without specifics)
2. Templated filler paragraphs that could apply to any state/topic
3. Lack of specific data points, examples, or unique analysis
4. Whether content is genuinely tailored to "{keyword}" or just superficially mentions it

Respond in EXACTLY this JSON format:
{{"score": <int 0-20>, "issues": ["issue1", "issue2"]}}

If no issues, use an empty list: {{"score": 20, "issues": []}}

Article excerpt:
---
{excerpt}
---"""

        try:
            raw = self.call_claude(
                prompt=prompt,
                system="You are an originality checker. Respond only with valid JSON.",
                model=self.utility_model,
                max_tokens=300,
            )

            # Parse JSON from response
            raw = raw.strip()
            # Handle markdown code blocks
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            result = json.loads(raw)
            score = max(0.0, min(20.0, float(result.get("score", 14))))
            issues = result.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)]

            # Prefix issues so reviewers know the source
            issues = [f"[LLM originality] {i}" for i in issues if i]

            return score, issues

        except RateLimitError:
            raise  # Let rate limits propagate — don't degrade the score
        except Exception as e:
            logger.warning(f"LLM originality check failed: {e}")
            return 14.0, [f"Originality check error (LLM): {e}"]

    def _reuse_plagiarism_score(self, article) -> tuple[float, list[str]]:
        """Reuse plagiarism score from a previous review round.

        Plagiarism doesn't change between revision rounds (content originality
        is inherent to the first draft), so we parse the previous score from
        revision_notes instead of re-running the check.
        """
        notes = article.revision_notes or ""
        match = re.search(r"\*\*plagiarism\*\*:\s*([\d.]+)/20", notes)
        if match:
            prev_score = min(20.0, max(0.0, float(match.group(1))))
            return prev_score, [f"Plagiarism score carried from round 1: {prev_score}/20"]
        # Couldn't parse — give benefit of the doubt
        return 18.0, ["Plagiarism score reused (prior result not found, defaulting to 18/20)"]

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
            except RateLimitError:
                raise  # Let rate limits propagate — don't skip fact check
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
        """Check for ClaimCoach CTA — placement-aware scoring.

        Scores for presence, link, and strategic placement:
        - Mention exists (1 pt)
        - Link to claimcoach.app (1 pt)
        - CTA in closing section (1 pt)
        - Mid-article mention or CTA (1 pt)
        - Benefit-driven CTA copy, not just brand mention (1 pt)
        """
        issues: list[str] = []
        score = 0.0

        content_lower = content.lower()
        lines = content.split("\n")

        # 1 pt: ClaimCoach mentioned at all
        if "claimcoach" in content_lower:
            score += 1
        else:
            issues.append("No mention of ClaimCoach")
            return score, issues  # No CTA at all — remaining checks moot

        # 1 pt: Link to claimcoach.app
        if "claimcoach.app" in content_lower:
            score += 1
        else:
            issues.append("No link to claimcoach.app")

        # 1 pt: CTA in closing section (last 20% of article)
        total_chars = len(content)
        closing_start = int(total_chars * 0.8)
        closing_section = content_lower[closing_start:]
        if "claimcoach" in closing_section:
            score += 1
        else:
            issues.append("No ClaimCoach CTA in closing section (last 20% of article)")

        # 1 pt: Mid-article mention (between 25%-75% of article)
        mid_start = int(total_chars * 0.25)
        mid_end = int(total_chars * 0.75)
        mid_section = content_lower[mid_start:mid_end]
        if "claimcoach" in mid_section:
            score += 1
        else:
            issues.append("No mid-article ClaimCoach mention (add subtle reference in body)")

        # 1 pt: Benefit-driven CTA copy (not just brand name)
        benefit_patterns = [
            r"claimcoach\s+(?:analyzes?|shows?|helps?|identifies?|checks?)",
            r"(?:try|use|check out|visit|get started with)\s+claimcoach",
            r"claimcoach\.app\)?\s*(?:to|and|—|–|-)\s+\w+",
        ]
        has_benefit = any(
            re.search(p, content_lower) for p in benefit_patterns
        )
        if has_benefit:
            score += 1
        else:
            issues.append("CTA mentions ClaimCoach but lacks benefit copy (explain what it does for the reader)")

        return score, issues

    @staticmethod
    def _check_anchor_text_quality(content: str) -> tuple[float, list[str]]:
        """Score internal link anchor text quality (2 pts).

        Good anchor text is descriptive and keyword-relevant:
        - [total loss settlement guide](/blog/total-loss) — GOOD
        - [click here](/blog/total-loss) — BAD
        - [https://claimcoach.app/blog/total-loss](/blog/total-loss) — BAD

        Returns (score, issues).
        """
        issues: list[str] = []
        # Find all markdown links
        links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
        if not links:
            return 0.0, []

        # Filter to internal links only
        internal = [
            (anchor, url) for anchor, url in links
            if "claimcoach" in url.lower() or url.startswith("/")
        ]
        if not internal:
            return 1.0, []  # No internal links to check — give partial credit

        bad_anchors = {
            "click here", "here", "this article", "read more", "learn more",
            "this link", "this page", "link", "article",
        }

        bad_count = 0
        for anchor, _url in internal:
            anchor_stripped = anchor.strip().lower()
            # Check for generic anchors
            if anchor_stripped in bad_anchors:
                bad_count += 1
            # Check for URL-as-anchor
            elif anchor_stripped.startswith("http"):
                bad_count += 1
            # Check for very short anchors (1-2 words, under 8 chars)
            elif len(anchor_stripped) < 8 and len(anchor_stripped.split()) <= 2:
                bad_count += 1

        if bad_count == 0:
            return 2.0, []
        elif bad_count <= len(internal) // 2:
            issues.append(
                f"{bad_count}/{len(internal)} internal links have generic anchor text "
                f"(use descriptive, keyword-rich anchors instead of 'click here')"
            )
            return 1.0, issues
        else:
            issues.append(
                f"Most internal links ({bad_count}/{len(internal)}) have generic anchor text — "
                f"use descriptive anchors like 'total loss settlement guide' instead of 'click here'"
            )
            return 0.0, issues

    @staticmethod
    def _check_math(content: str) -> tuple[float, list[str]]:
        """Validate math claims using MathValidator (3 pts).

        AI frequently botches arithmetic when calculating sales tax,
        settlement examples, or line item totals.
        """
        try:
            validator = MathValidator()
            result = validator.validate(content)
            issues = [
                f"[Math Error] {i['message']}" for i in result.get("issues", [])
            ]
            error_count = result.get("error_count", 0)
            if error_count == 0:
                return 3.0, []
            elif error_count == 1:
                return 1.5, issues
            else:
                return 0.0, issues
        except Exception as e:
            logger.warning(f"Math validation failed: {e}")
            return 2.0, [f"Math validation error: {e}"]

    @staticmethod
    def _check_media_and_formatting(read_report: dict) -> tuple[float, list[str]]:
        """Score media presence and content formatting (10 pts).

        Sub-scores:
        - Image placeholders present (3 pts)
        - Image alt text quality (1 pt)
        - Scanability: lists & bold terms (3 pts)
        - FAQPage JSON-LD schema (2 pts)
        - Blockquote callouts (1 pt)
        """
        score = 0.0
        issues: list[str] = []

        # ── Images (3 pts for presence + 1 pt for alt text quality) ──
        images = read_report.get("image_coverage", {})
        img_count = images.get("image_count", 0)
        if img_count >= 3:
            score += 3
        elif img_count >= 2:
            score += 2
        elif img_count >= 1:
            score += 1
        else:
            issues.append("No image placeholders (add 2-4 images with descriptive alt text)")

        # Alt text quality
        empty_alts = images.get("empty_alt_count", 0)
        short_alts = images.get("short_alt_count", 0)
        if img_count > 0 and empty_alts == 0 and short_alts == 0:
            score += 1
        elif img_count > 0 and empty_alts > 0:
            issues.append(f"{empty_alts} image(s) missing alt text — add descriptive, keyword-rich alt")

        # ── Scanability (3 pts) ──
        scan = read_report.get("scanability", {})
        scan_raw = scan.get("score", 0)
        # scan_raw is 0.0-2.0 scale, map to 0-3 pts
        if scan_raw >= 1.5:
            score += 3
        elif scan_raw >= 1.0:
            score += 2
        elif scan_raw >= 0.5:
            score += 1
        else:
            issues.append("Low scanability — add more bullet lists, bold key terms, and visual breaks")

        # ── Structured data / JSON-LD schema (2 pts) ──
        # 1 pt for Article schema (E-E-A-T: datePublished, author, publisher)
        # 1 pt for FAQPage schema (rich snippet eligibility)
        faq = read_report.get("faq_schema", {})
        if faq.get("has_article_schema"):
            score += 1
        else:
            issues.append("No Article JSON-LD schema (missing E-E-A-T signals: datePublished, author)")

        if faq.get("has_json_ld"):
            if not faq.get("issues"):
                score += 1
            else:
                score += 0.5
                issues.extend(faq["issues"])
        elif faq.get("has_faq_section"):
            issues.append("FAQ section exists but no FAQPage JSON-LD schema — missing rich snippet opportunity")
        else:
            issues.append("No FAQ section or FAQPage schema")

        # ── Blockquote callouts (1 pt) ──
        blockquotes = scan.get("blockquotes", 0)
        if blockquotes >= 2:
            score += 1
        elif blockquotes >= 1:
            score += 0.5
        else:
            issues.append("No blockquote callouts — add Adjuster Insider tips for engagement")

        return round(score, 1), issues

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
