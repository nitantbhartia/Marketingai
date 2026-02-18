"""Quill agent — content writer.

Three-phase writing pipeline:
  Phase 1 — Outline: structured plan with H2s, key points, data to cite
  Phase 2 — Draft: full article written from the outline (Sonnet quality)
  Phase 3 — Self-Review: deterministic rubric checks + targeted auto-fix
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.agents.base import BaseAgent, RateLimitError
from pipeline.db import ArticleStatus
from pipeline.utils.factual_claims import evaluate_factual_claims
from pipeline.utils.freshness import auto_fix_stale_years
from pipeline.utils.nhtsa import enrich_vehicle_article
from pipeline.utils.readability import readability_report, word_count
from pipeline.utils.seo import _keyword_match, detect_faq_section, extract_links
from pipeline.utils.serp import SerpAnalyzer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Golden System Prompt — Role-Based Constraint Prompting
# Suppresses AI-isms and forces "High-Agency, Low-Fluff" tone for insurance
# content that reads like it was written by a senior claims adjuster.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """### Role
You are the Lead Content Strategist and Senior Insurance Adjuster for ClaimCoach (claimcoach.app).
Your goal is to write SEO-optimized, authoritative, and deeply empathetic guides that help
car owners navigate the nightmare of lowball total loss insurance settlements.

### Tone & Style Guidelines (The "Human" Filter)
1. NO AI-ISMS: Never use these words: delve, tapestry, pivotal, unlock, landscape, comprehensive,
   utilize, leverage, embark, foster, streamline, robust, cutting-edge, paradigm, synergy,
   game-changer, deep dive, at the end of the day, it's important to note, in today's world.
2. VARY SENTENCE RHYTHM: Mix short, punchy sentences (3-7 words) with longer, explanatory ones.
   If a sentence is over 20 words, the next must be under 10. This creates a natural cadence.
3. EMPATHETIC COACHING: Use "you" and "we." Acknowledge the stress. Don't lecture; coach.
   Write like a knowledgeable friend who happens to be a former adjuster.
4. JARGON HANDLING: Use technical terms (e.g., "Actual Cash Value," "Subrogation," "Bad Faith")
   but explain them instantly in plain English as if talking to a friend over coffee.
5. NO WRAPPED SUMMARIES: Do not start sections with "In conclusion" or "To summarize."
   End with a clear, actionable "Next Step."
6. ACTIVE VOICE ONLY: "The adjuster denied the claim" not "The claim was denied by the adjuster."
7. SHORT WORDS: "Get" not "obtain." "Show" not "demonstrate." "Use" not "utilize." "Help" not "facilitate."

### Article Structure (Conversion-Focused — FOLLOW THIS EXACTLY)
Your reader just got a lowball offer. They're stressed, maybe angry, probably on their phone
at 11pm. They want to know "am I getting screwed and what do I do right now." Every extra
paragraph is a chance for them to bounce. Keep it tight.

**Target word count: 1200-1800 words.**

Follow this structure:

1. **Hook** (~50 words): Lead with a specific dollar amount they're losing.
   Example: "Your insurer left $1,700 off your offer. Here's how."

2. **The problem + emotional validation** (~200 words): Acknowledge their frustration.
   Explain WHY insurers lowball. Make them feel seen, not lectured.
   → **FIRST CTA HERE** (within 300 words): The reader just learned their offer is probably
     missing $1,500-$3,000 in line items. They're activated. Hit them now.
     Example: "See what's missing from your offer in 5 minutes — [check your settlement free](https://claimcoach.app)"

3. **The specific line items they miss** (~400 words): Sales tax, registration, dealer fees,
   aftermarket parts, condition adjustments. Use bullets. Bold the dollar amounts.
   → **CONTEXTUAL CTA**: After listing items, naturally link to ClaimCoach.
     Example: "[ClaimCoach checks all of these automatically](https://claimcoach.app)"

4. **State-specific rules** (~300 words, if applicable): What their state requires.
   → **CONTEXTUAL CTA**: "Get your [state]-specific analysis [here](https://claimcoach.app)"

5. **What to do next** (~200 words): 3-5 concrete action steps. Numbered list.

6. **FAQ** (~250 words, 3-5 questions for schema markup, use ### for each question)
   → **FINAL CTA**: End the last FAQ answer with a natural ClaimCoach link.

**Total: ~1200-1800 words, 3 CTAs. Reader hits the first one before they've scrolled twice.**

### CTA Rules
- Exactly 3 CTAs per article (1 early, 1 contextual, 1 closing)
- NEVER use salesy banner language. CTAs must feel like helpful suggestions.
- Always link to https://claimcoach.app — never make up feature-specific URLs
- Vary the CTA copy. Don't repeat the same line.
- Good: "Want to see which ones apply to your offer? [ClaimCoach checks this automatically.](https://claimcoach.app)"
- Bad: "Click here to try ClaimCoach!" (too pushy)

### Interactive Tools
- If the article includes calculations, valuation, thresholds, taxes, or fairness checks,
  embed one mini tool placeholder in body:
  `<!-- TOOL:tool_id:mini -->`
- Also add one plain link to the tools hub:
  [Browse all calculators and quizzes](https://claimcoach.app/tools)
- Do not add tools when content is purely narrative and non-numeric.

### Callouts
- Use "Adjuster Insider" Callouts: Use Markdown blockquotes (> ) for tips that a standard
  insurance company wouldn't want a policyholder to know. Prefix with **Adjuster Insider:**
  Example: > **Adjuster Insider:** Most adjusters have authority to increase offers by 10-15%
  without supervisor approval. They just won't tell you that.
- Entity-First SEO: Naturally weave in primary entities (Policy Limits, Declarations Page,
  Replacement Cost, Actual Cash Value) within the first 100 words.
- "Why This Matters to Your Wallet": For every technical fact, add one sentence explaining
  the dollar impact. Don't just say what something is — say what it costs the reader.
- Include specific dollar amounts, ranges, and real data where possible

### Image Placeholders (REQUIRED — 1-2 per article)
Include 1-2 image placeholders throughout the article using this format:
  ![Descriptive alt text with keyword](image:short-slug-description)
Rules:
- Alt text MUST be descriptive (10+ words) and include the target keyword or a close variant
- Place images where visual context would help the reader (diagrams, examples, comparisons)
- Distribute images evenly — don't cluster them all at the top
- Good: ![Chart showing average total loss settlement amounts by state for 2024](image:settlement-amounts-by-state)
- Bad: ![image](image:img1) — this is useless for SEO and accessibility

### Scanability (REQUIRED)
- Use bullet or numbered lists in at least 2-3 sections (readers scan, not read)
- Bold **key terms** and **important numbers** so scanners catch them
- Use tables for comparisons or structured data when appropriate
- Every H2 section should have at least one visual break (list, callout, bold term, or image)

### SEO Requirements
- Target keyword in: title, first paragraph, at least 2 H2s, meta description
- 2-3 external links to authoritative sources (state DOI websites, NAIC, etc.)
- Meta description: 150-160 chars, includes keyword and emotional hook

### Citation Requirements (HARD GATE)
- Every legal claim, percentage, dollar amount, and timeline must include an inline source citation.
- Citation format: sentence + `[Source Name](https://...)` in the same sentence or immediately after.
- Prefer authoritative domains (.gov, NAIC, Cornell Law, state DOI websites).
- Uncited factual claims will fail Sage review and be blocked from publishing.

### Readability
- Flesch-Kincaid: 60+ (8th grade level)
- Sentences: max 25 words average
- Paragraphs: max 4 sentences
- Mix sentence lengths — some punchy, some explanatory

### Format
- Output the article in Markdown format
- Use ## for H2 headers, ### for H3
- Use [link text](URL) for links
- Use > for Adjuster Insider callouts
- At the very end, output a line: META_DESCRIPTION: <your 150-160 char meta description>

### Critical Rules
- Never claim ClaimCoach can negotiate on your behalf (it can't — legal risk)
- Never promise specific dollar amounts ClaimCoach will recover
- Never give legal advice. Say "consider consulting an attorney" for complex situations.
- Never claim features ClaimCoach doesn't have
"""

# ---------------------------------------------------------------------------
# Banned AI-ism words — deterministic filter applied in self-review
# ---------------------------------------------------------------------------
AI_ISMS = [
    "delve", "tapestry", "pivotal", "unlock", "landscape", "utilize",
    "leverage", "embark", "foster", "streamline", "robust", "cutting-edge",
    "paradigm", "synergy", "game-changer", "deep dive",
    "at the end of the day", "it's important to note", "in today's world",
    "it is worth noting", "in this article we will", "without further ado",
    "in the realm of", "navigating the complexities",
]

# Simple replacements for common AI-isms
AI_ISM_REPLACEMENTS: dict[str, str] = {
    "utilize": "use",
    "leverage": "use",
    "comprehensive": "full",
    "robust": "strong",
    "streamline": "simplify",
    "facilitate": "help",
    "implement": "set up",
    "subsequently": "then",
    "furthermore": "also",
    "additionally": "also",
    "demonstrate": "show",
    "obtain": "get",
    "commence": "start",
    "endeavor": "try",
    "ascertain": "find out",
    "in order to": "to",
    "due to the fact that": "because",
    "at this point in time": "now",
    "prior to": "before",
}

# ---------------------------------------------------------------------------
# Category-specific writing strategies
# ---------------------------------------------------------------------------
CATEGORY_GUIDANCE = {
    "problem_aware": """
CATEGORY STRATEGY — Problem-Aware Reader:
This reader just discovered their settlement is too low and is frustrated/confused.
- Open with EMPATHY. Validate their shock and frustration in the first paragraph.
- Explain WHY this happens (insurance company incentives, automated valuation tools).
- Give them a clear "what to do right now" framework — numbered steps.
- Use language like "You're not alone" and "This is fixable."
- Include a specific example: "If your car is worth $15,000 but they offered $11,500..."
- Emotional arc: Frustration → Understanding → Empowerment → Action.
""",
    "solution_aware": """
CATEGORY STRATEGY — Solution-Aware Reader:
This reader already knows they need to fight back and is looking for HOW.
- Skip the emotional setup. Go straight to actionable steps.
- Number every step. Be extremely specific about what to do and say.
- Include templates or exact phrases they can use with adjusters.
- Give realistic timelines: "This process typically takes 2-4 weeks."
- Cover common mistakes and what NOT to do.
- Include dollar-amount examples at every step.
""",
    "state_specific": """
CATEGORY STRATEGY — State-Specific Guide:
This reader needs information specific to their state's laws and processes.
- Lead with the state's total loss threshold and how it's calculated.
- Cite specific statutes by number (e.g., "Under California Insurance Code §2695.8...").
- Include the state Department of Insurance contact info and complaint process.
- Cover state-specific quirks (owner-retain, salvage title rules, tax recovery).
- Include a "Your Rights in [State]" section with bullet points.
- Link to the actual state DOI website as an authoritative external source.
""",
    "vehicle_specific": """
CATEGORY STRATEGY — Vehicle-Specific Guide:
This reader has a specific type of vehicle and wants targeted valuation advice.
- Include typical value ranges for this vehicle type.
- Explain what factors most affect this vehicle's valuation (mileage, trim, condition).
- Show how to find comparable vehicles (specific instructions for KBB, Edmunds, AutoTrader).
- List common line items insurers miss for this vehicle type.
- Include a sample comparable vehicle analysis with numbers.
""",
    "line_item": """
CATEGORY STRATEGY — Line-Item Deep Dive:
This reader wants to understand a specific settlement line item.
- Define the line item clearly in the first paragraph.
- Explain why insurers often undercount or omit it.
- Give typical dollar amounts or percentage ranges.
- Show exactly how to document and claim this item.
- Include the specific language to use when requesting it from the adjuster.
""",
    "comparison": """
CATEGORY STRATEGY — Comparison Guide:
This reader is weighing two or more options and needs help deciding.
- Present both sides fairly — don't be preachy.
- Use a clear structure: Option A vs. Option B with pros/cons.
- Include cost comparisons with specific dollar amounts.
- Give a clear recommendation at the end based on the reader's situation.
- Use "If you're in situation X, choose Y" decision framework.
""",
    "emotional": """
CATEGORY STRATEGY — Emotional/Story-Driven:
This reader is stressed and needs to feel understood before they'll act.
- Open with a relatable scenario: "You just got off the phone with your adjuster..."
- Use second person throughout — put the reader IN the story.
- Share what others have experienced (composite scenarios, not fake testimonials).
- Build from helplessness to empowerment throughout the article.
- Every section should end with something actionable they can do TODAY.
- Tone: warm, understanding, then gradually more assertive.
""",
}

INTENT_TEMPLATE_GUIDANCE = {
    "dispute_how_to": (
        "Intent Pattern — Dispute How-To:\n"
        "- Lead with immediate action.\n"
        "- Include scripts/templates and timeline.\n"
        "- Prioritize evidence checklist over background theory."
    ),
    "calculator_intent": (
        "Intent Pattern — Calculator/Estimator:\n"
        "- Define the formula early.\n"
        "- Include worked numeric examples and ranges.\n"
        "- Embed tool placeholder and worksheet-style steps."
    ),
    "law_threshold": (
        "Intent Pattern — Law/Threshold:\n"
        "- Lead with statute/regulation context.\n"
        "- Translate legal text into practical implications.\n"
        "- Add rights + escalation section."
    ),
    "negotiation_script": (
        "Intent Pattern — Negotiation Script:\n"
        "- Use call/email scripts.\n"
        "- Include red flags and escalation ladder.\n"
        "- Keep sections action-oriented."
    ),
}


class QuillAgent(BaseAgent):
    name = "quill"
    claim_field = "writer_claim"
    DRAFT_TEMPERATURE = 0.85
    REVISION_TEMPERATURE = 0.35

    # Structured issue codes that can be resolved with deterministic routines.
    ROUTINE_ISSUE_CODES = {
        "SEO_KEYWORD_OPENING",
        "SEO_META_MISSING",
        "SEO_META_LENGTH",
        "SEO_META_NO_KEYWORD",
        "SEO_FAQ_MISSING",
        "LINKS_INTERNAL_MISSING",
        "LINKS_EXTERNAL_MISSING",
        "CTA_MISSING",
        "CTA_LINK_MISSING",
        "CTA_EARLY_MISSING",
        "CTA_COUNT_LOW",
        "MEDIA_IMAGE_MISSING",
        "MEDIA_ALT_TEXT_WEAK",
        "SCHEMA_ARTICLE_MISSING",
        "SCHEMA_FAQ_MISSING",
        "FACT_UNSUPPORTED_CLAIM",
        "FACT_WEAK_CLAIM",
        "FACT_CITATION_MISSING",
        "INTERACTIVE_TOOL_MISSING",
    }
    # Structured issue codes that generally require a narrative rewrite pass.
    BROAD_REWRITE_CODES = {
        "SEO_H2_KEYWORD_COVERAGE",
        "SEO_KEYWORD_DENSITY",
        "READABILITY_LOW",
        "READABILITY_SENTENCE_LENGTH",
        "READABILITY_PARAGRAPH_LENGTH",
        "READABILITY_PASSIVE_VOICE",
        "READABILITY_TRANSITIONS_LOW",
        "READABILITY_VARIETY_LOW",
        "READABILITY_COMPLEX_WORDS",
        "FACTUAL_ERROR",
        "FACT_MATH_ERROR",
        "FACT_TRUNCATED_SENTENCE",
        "PLAGIARISM_RISK",
        "LEGAL_COMPLIANCE_FLAGGED",
        "PRODUCT_CLAIM_VIOLATION",
        "STATE_ACCURACY_FAIL",
    }

    # ------------------------------------------------------------------
    # Main entry point — three-phase pipeline
    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        """Three-phase writing pipeline: Outline → Draft → Self-Review.

        Processes up to ``max_articles_per_run`` articles per invocation so a
        single scheduled run can produce multiple pieces of content.

        Slot allocation: revisions get at most (max_per_run - 1) slots so
        at least 1 slot is always reserved for new TODO articles.  This
        prevents revision loops from starving the pipeline of fresh content.
        """
        recovered = self._recover_stale_in_progress()

        max_per_run = getattr(self.config.pipeline, "max_articles_per_run", 3)
        daily_cap = max(1, int(getattr(self.config.pipeline, "daily_article_cap", 8)))
        written_today = self._count_written_today()
        if written_today >= daily_cap:
            logger.info(
                f"Daily article cap reached: {written_today}/{daily_cap}. "
                "Skipping Quill run."
            )
            return {
                "status": "idle",
                "reason": "daily_cap_reached",
                "written_today": written_today,
                "recovered_stale_in_progress": recovered,
            }

        remaining_quota = max(0, daily_cap - written_today)
        max_per_run = min(max_per_run, remaining_quota)
        max_revisions = max(1, max_per_run - 1)  # reserve 1 slot for new work
        results: list[dict[str, Any]] = []
        revision_count = 0

        for _ in range(max_per_run):
            # Alternate: revisions first, but cap them
            prefer_revision = revision_count < max_revisions
            result = self._write_one(prefer_revision=prefer_revision)
            if result["status"] == "idle":
                break  # nothing left to write
            results.append(result)
            if result.get("is_revision"):
                revision_count += 1
            if result["status"] in ("error", "rate_limited"):
                break  # stop on error or rate limit to avoid burning quota

        if not results:
            logger.info("No articles available to write")
            return {
                "status": "idle",
                "reason": "no_articles",
                "recovered_stale_in_progress": recovered,
            }

        if len(results) == 1:
            results[0]["recovered_stale_in_progress"] = recovered
            return results[0]

        return {
            "status": "batch_complete",
            "articles_written": len([r for r in results if r["status"] == "success"]),
            "recovered_stale_in_progress": recovered,
            "results": results,
        }

    def _recover_stale_in_progress(self) -> int:
        """Move stale in_progress rows back to todo so work can resume.

        This catches crashed workers or lost claims when Quill is invoked
        outside dashboard pre-hooks.
        """
        hours = max(
            1, int(getattr(self.config.pipeline, "quill_stale_recovery_hours", 4))
        )
        stale = self.db.get_stuck_articles(hours=hours)
        stale_in_progress = [
            a for a in stale if a.status == ArticleStatus.IN_PROGRESS.value
        ]
        for article in stale_in_progress:
            self.db.update_article(
                article.id,
                status=ArticleStatus.TODO.value,
                writer_claim="",
            )
        if stale_in_progress:
            self.db.record_metric(
                "quill_recovered_stale_in_progress",
                len(stale_in_progress),
                json.dumps({"hours": hours}),
            )
            logger.warning(
                "Recovered %s stale in_progress article(s) to todo (>%sh old)",
                len(stale_in_progress),
                hours,
            )
        return len(stale_in_progress)

    def _count_written_today(self) -> int:
        """Count successful Quill writes since UTC midnight."""
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        metrics = self.db.get_metrics(name="quill_write", since=start_of_day, limit=2000)
        return len(metrics)

    # ------------------------------------------------------------------
    # Single-article pipeline (called in a loop by run())
    # ------------------------------------------------------------------
    def _write_one(self, prefer_revision: bool = True) -> dict[str, Any]:
        """Write or revise a single article through the 3-phase pipeline.

        When *prefer_revision* is True, revision articles are tried first.
        When False, TODO articles are tried first — this ensures fresh
        content is always produced even when revisions are queued.
        """
        article_id = None
        is_revision = False

        if prefer_revision:
            # Try revision first, then TODO
            article_id = self._try_revision()
            is_revision = article_id is not None
            if article_id is None:
                article_id = self.pick_and_claim(
                    from_status=ArticleStatus.TODO.value,
                    to_status=ArticleStatus.IN_PROGRESS.value,
                )
        else:
            # Try TODO first, then revision
            article_id = self.pick_and_claim(
                from_status=ArticleStatus.TODO.value,
                to_status=ArticleStatus.IN_PROGRESS.value,
            )
            if article_id is None:
                article_id = self._try_revision()
                is_revision = article_id is not None

        if article_id is None:
            return {"status": "idle", "reason": "no_articles", "is_revision": False}

        article = self.db.get_article(article_id)
        if article is None:
            self.db.update_article(article_id, writer_claim="")
            return {"status": "error", "reason": "article_not_found", "is_revision": is_revision}

        logger.info(f"Writing article: {article.target_keyword} (revision={is_revision})")

        try:
            with self.metric_context(
                article_id=article_id,
                keyword=article.target_keyword,
                phase="write",
            ):
                return self._write_one_inner(article, article_id, is_revision)
        except RateLimitError as e:
            logger.warning(f"Rate limited during pre-draft of article {article_id}: {e}")
            self._rollback_claim(article_id, is_revision, phase="pre_draft")
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e), "is_revision": is_revision}
        except Exception as e:
            logger.error(f"Unexpected error writing article {article_id}: {e}", exc_info=True)
            self._rollback_claim(article_id, is_revision)
            return {"status": "error", "article_id": article_id, "reason": str(e)}

    def _rollback_claim(self, article_id: int, is_revision: bool, phase: str = "") -> None:
        """Release writer claim and reset the article to its appropriate queued status.

        Call this in any except block inside the write pipeline so the article
        can be re-claimed on the next scheduler tick rather than staying stuck.
        """
        rollback_status = (
            ArticleStatus.REVISION.value if is_revision
            else ArticleStatus.TODO.value
        )
        self.db.update_article(article_id, status=rollback_status, writer_claim="")
        if phase:
            self.db.record_metric("rate_limit", 0, json.dumps({
                "agent": "quill", "article_id": article_id, "phase": phase,
            }))

    def _write_one_inner(self, article, article_id: int, is_revision: bool) -> dict[str, Any]:
        """Inner write logic — caller guarantees claim release on exception."""
        # Load context documents
        product_context = self.config.load_product_context()
        state_rules = self.config.load_state_rules()
        seo_template = self.config.load_seo_template()
        published = self.db.get_published_articles()
        internal_links_context = self._format_internal_links(published)
        lessons = self._extract_lessons()
        exemplar_context = self._build_exemplar_context(article, published)
        fact_pack = self._compose_fact_pack(article)
        winner_memory = self._build_winner_memory(article, published)

        # ── Revision path: targeted fix instead of full rewrite ──
        if is_revision and article.markdown_content:
            try:
                result = self._targeted_revision(
                    article, product_context, state_rules, internal_links_context, lessons,
                    published_articles=published,
                )
            except RateLimitError as e:
                logger.warning(f"Rate limited during targeted revision of article {article_id}: {e}")
                self._rollback_claim(article_id, is_revision, phase="targeted_revision")
                return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
            if result:
                return result
            # Fall through to full rewrite if targeted revision fails

        # Always use the strategy model for outline generation — the outline is
        # the structural blueprint for the entire article and determines section
        # coverage, citation plan, and FAQ seeds. The marginal cost difference
        # versus fast_model is small compared to the quality improvement.
        outline_model = self.strategy_model

        # ── Phase 0: Entity mapping (E-E-A-T knowledge graph) ──
        entity_map = self._extract_entities(article)

        # ── Phase 0b: SERP analysis (real-time search intelligence) ──
        serp_context = self._get_serp_context(article)
        if serp_context and "TOP RANKING PAGES" not in (article.content_brief or ""):
            try:
                merged_brief = (article.content_brief or "").strip()
                merged_brief += (
                    "\n\n=== SERP BRIEF (auto-generated before writing) ===\n"
                    + serp_context[:2500]
                )
                self.db.update_article(article_id, content_brief=merged_brief)
                article.content_brief = merged_brief
            except Exception:
                logger.debug("Unable to persist SERP brief into content_brief", exc_info=True)

        # ── Phase 0c: Vehicle data enrichment (NHTSA, if applicable) ──
        nhtsa_context = self._get_nhtsa_context(article)

        # ── Phase 1: Generate outline ──
        outline = self._generate_outline(
            article, product_context, state_rules, internal_links_context,
            entity_map=entity_map,
            revision_feedback=article.revision_notes if is_revision else "",
            serp_context=serp_context,
            nhtsa_context=nhtsa_context,
            fact_pack=fact_pack,
            winner_memory=winner_memory,
            model_name=outline_model,
            seo_template=seo_template,
            exemplar_context=exemplar_context,
        )

        # ── Phase 2: Write article (section-by-section from outline) ──
        try:
            result_text = self._draft_from_outline(
                article, outline, product_context, state_rules,
                internal_links_context, is_revision, lessons,
                fact_pack=fact_pack,
                winner_memory=winner_memory,
                seo_template=seo_template,
                exemplar_context=exemplar_context,
            )
        except RateLimitError as e:
            logger.warning(f"Rate limited during draft of article {article_id}: {e}")
            self._rollback_claim(article_id, is_revision, phase="draft")
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
        except Exception as e:
            logger.error(f"Draft error: {e}")
            self._rollback_claim(article_id, is_revision)
            return {"status": "error", "reason": str(e)}

        try:
            content, meta_description = self._parse_result(result_text)
            title = article.suggested_title or article.title or article.target_keyword.title()

            # ── Phase 2.5: Contrastive critique (anti-AI-laziness) ──
            if getattr(self.config.pipeline, "quill_enable_contrastive_critique", False):
                content = self._contrastive_critique(content, article)

            # ── Phase 3: Self-review & auto-fix ──
            content, meta_description, fixes = self._self_review_and_fix(
                content, meta_description, article,
                published_articles=published,
            )

            slug = self._generate_slug(title)
            wc = word_count(content)

            # ── Quality gate: don't waste a Sage review on content that
            # clearly can't pass the approval threshold. ──
            gate_pass, gate_reason = self._passes_minimum_bar(content, wc)
            if not gate_pass:
                logger.warning(
                    f"Article {article_id} failed quality gate: {gate_reason}"
                )
                # Save partial content so humans can inspect; rollback claim/status.
                self.db.update_article(
                    article_id,
                    title=title,
                    markdown_content=content,
                    meta_description=meta_description,
                    slug=slug,
                    word_count=wc,
                    status=ArticleStatus.REVISION.value if is_revision else ArticleStatus.TODO.value,
                    writer_claim="",
                    revision_notes=(article.revision_notes or "")
                    + f"\n\n[QUILL SELF-CHECK FAIL] {gate_reason}",
                )
                return {
                    "status": "quality_gate_fail",
                    "article_id": article_id,
                    "reason": gate_reason,
                    "word_count": wc,
                }

            # Save and submit to Sage — clear stale revision_notes so Sage
            # scores the new content fresh rather than being confused by
            # feedback from a prior round that this rewrite already addressed.
            update_kwargs = dict(
                title=title,
                markdown_content=content,
                meta_description=meta_description,
                slug=slug,
                word_count=wc,
                status=ArticleStatus.EDITOR_REVIEW.value,
                writer_claim="",
            )
            if is_revision:
                update_kwargs["revision_notes"] = (
                    f"[Full rewrite in revision round {article.revision_count + 1} — "
                    f"previous feedback incorporated into new outline and draft]"
                )
            self.db.update_article(article_id, **update_kwargs)
        except RateLimitError as e:
            logger.warning(f"Rate limited during post-draft of article {article_id}: {e}")
            self._rollback_claim(article_id, is_revision, phase="post_draft")
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
        except Exception as e:
            logger.error(f"Post-draft error for article {article_id}: {e}", exc_info=True)
            self._rollback_claim(article_id, is_revision)
            return {"status": "error", "reason": str(e)}

        self.db.record_metric("quill_write", wc, json.dumps({
            "article_id": article_id,
            "keyword": article.target_keyword,
            "is_revision": is_revision,
            "word_count": wc,
            "self_review_fixes": fixes,
            "had_outline": bool(outline),
        }))

        logger.info(
            f"Wrote article {article_id}: {title} ({wc} words, "
            f"{len(fixes)} self-review fixes)"
        )
        return {
            "status": "success",
            "article_id": article_id,
            "title": title,
            "word_count": wc,
            "is_revision": is_revision,
            "self_review_fixes": fixes,
        }

    def _is_high_trust_article(self, article, is_revision: bool = False) -> bool:
        """Identify content that warrants higher-cost reasoning models."""
        if is_revision or getattr(article, "revision_count", 0) > 0:
            return True
        if getattr(article, "target_state", ""):
            return True
        if (getattr(article, "content_category", "") or "") in {
            "state_specific",
            "comparison",
        }:
            return True
        return False

    def _use_strategy_model_for_article(
        self, article, is_revision: bool = False
    ) -> bool:
        """Gate expensive strategy model calls for cost control."""
        if not getattr(
            self.config.pipeline, "quill_use_strategy_on_high_trust_only", True
        ):
            return True
        return self._is_high_trust_article(article, is_revision=is_revision)

    # ------------------------------------------------------------------
    # Phase 0: Entity mapping (E-E-A-T knowledge graph)
    # ------------------------------------------------------------------
    def _extract_entities(self, article) -> str:
        """Entity Mapper — extract key insurance/legal entities for E-E-A-T.

        Uses Flash to identify the precise legal and insurance terminology
        that should appear in the article. This ensures the writer uses
        authoritative language that signals expertise to Google.

        Returns a structured entity map string to inject into the outline
        prompt, or empty string if extraction fails.
        """
        if not self.has_llm:
            return ""

        keyword = article.target_keyword or ""
        state = article.target_state or ""
        category = article.content_category or ""

        prompt = f"""You are an insurance domain expert building a knowledge graph for SEO content.

For an article about "{keyword}"{f' in {state}' if state else ''} (category: {category or 'general'}):

Extract the key ENTITIES that must appear in this article for E-E-A-T authority.
Group them into categories:

1. **Legal Terms** — statutes, regulations, legal concepts (e.g., "Actual Cash Value", "diminished value", "bad faith")
2. **Insurance Concepts** — industry terms the reader needs to understand (e.g., "subrogation", "total loss threshold", "comparable vehicles")
3. **Processes** — specific procedures to reference (e.g., "appraisal clause", "DOI complaint", "demand letter")
4. **Data Points** — specific numbers, ranges, or benchmarks to include (e.g., "typical 10-20% undervaluation", "75% threshold in CA")
5. **Authoritative Sources** — organizations or references to cite (e.g., "NAIC", "state DOI", "CCC ONE")

Return 3-5 entities per category. Be specific to "{keyword}" — generic terms that apply to any article are not useful.

Format as a clean list:
LEGAL: term1, term2, term3
INSURANCE: term1, term2, term3
PROCESSES: term1, term2, term3
DATA_POINTS: point1, point2, point3
SOURCES: source1, source2, source3"""

        try:
            result = self.call_claude(
                prompt=prompt,
                system="You are an insurance domain expert. Return only the entity map.",
                model=self.fast_model,
                max_tokens=500,
            )
            logger.debug(f"Entity map for '{keyword}': {len(result)} chars")
            return result.strip()
        except Exception as e:
            logger.warning(f"Entity extraction failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 0b: SERP analysis (real-time search intelligence)
    # ------------------------------------------------------------------
    def _get_serp_context(self, article) -> str:
        """Pull real-time SERP data for the target keyword.

        Uses SerpAPI, Google CSE, or Autocomplete (free fallback) to gather:
        - People Also Ask questions → feed into FAQ section
        - Related searches → LSI keyword coverage
        - Competitor headings → content gap analysis

        Returns formatted context string for the outline prompt, or empty
        string if SERP analysis is disabled or no backend is configured.
        """
        keyword = article.target_keyword or ""
        if not keyword:
            return ""

        if not getattr(self.config, "serp", None) or not self.config.serp.enabled:
            return ""

        try:
            analyzer = SerpAnalyzer(
                serpapi_key=self.config.serp.serpapi_key,
                google_cse_key=self.config.serp.google_cse_key,
                google_cse_id=self.config.serp.google_cse_id,
            )
            state = article.target_state or ""
            insight = analyzer.analyze(keyword, state=state)
            context = insight.to_outline_context()
            if context:
                logger.info(
                    f"SERP context for '{keyword}': {len(insight.paa_questions)} PAA, "
                    f"{len(insight.related_searches)} related, "
                    f"{len(insight.competitor_headings)} headings"
                )
            return context
        except Exception as e:
            logger.warning(f"SERP analysis failed for '{keyword}': {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 0c: Vehicle data enrichment (NHTSA)
    # ------------------------------------------------------------------
    def _get_nhtsa_context(self, article) -> str:
        """Pull NHTSA vehicle data if the keyword references a specific vehicle.

        Uses the free NHTSA API to get recalls and complaints data for
        vehicle-specific articles. This government data is a strong E-E-A-T
        signal that competitors rarely include.

        Returns formatted context string for the outline prompt, or empty
        string if the keyword doesn't reference a specific vehicle.
        """
        keyword = article.target_keyword or ""
        if not keyword:
            return ""

        try:
            context = enrich_vehicle_article(keyword)
            if context:
                logger.info(f"NHTSA enrichment found for '{keyword}'")
            return context or ""
        except Exception as e:
            logger.warning(f"NHTSA enrichment failed for '{keyword}': {e}")
            return ""

    @staticmethod
    def _extract_outline_from_markdown(markdown: str, limit: int = 6) -> str:
        """Extract up to ``limit`` H2 headings as a compact outline seed."""
        if not markdown:
            return ""
        headings: list[str] = []
        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped.startswith("## "):
                continue
            heading = re.sub(r"\s+", " ", stripped[3:]).strip()
            if not heading:
                continue
            headings.append(heading)
            if len(headings) >= limit:
                break
        if not headings:
            return ""
        return "\n".join(f"- {h}" for h in headings)

    def _product_info(self, article=None) -> dict[str, str]:
        product = (getattr(article, "product", "") or "claimcoach").strip().lower()
        if product == "medbill":
            return {
                "product": "medbill",
                "brand": "BillScan",
                "domain": "billkarma.app",
                "site_url": "https://billkarma.app",
                "tools_url": "https://billkarma.app/tools",
            }
        return {
            "product": "claimcoach",
            "brand": "ClaimCoach",
            "domain": "claimcoach.app",
            "site_url": "https://claimcoach.app",
            "tools_url": "https://claimcoach.app/tools",
        }

    def _apply_branding(self, text: str, article=None) -> str:
        info = self._product_info(article)
        branded = text
        branded = branded.replace("ClaimCoach", info["brand"])
        branded = branded.replace("claimcoach.app/tools", info["domain"] + "/tools")
        branded = branded.replace("claimcoach.app", info["domain"])
        branded = branded.replace("https://claimcoach.app", info["site_url"])
        return branded

    def _build_exemplar_context(self, article, published_articles: list) -> str:
        """Provide high-pass article patterns as prompt seeds (structure only)."""
        target_category = (article.content_category or "").strip().lower()
        target_state = (article.target_state or "").strip().lower()
        target_product = (getattr(article, "product", "") or "claimcoach").strip().lower()
        threshold = float(getattr(self.config.pipeline, "approval_score_threshold", 75))

        ranked: list[tuple[float, Any]] = []
        for candidate in published_articles:
            cand_product = (getattr(candidate, "product", "") or "claimcoach").strip().lower()
            if cand_product != target_product:
                continue
            if not candidate.markdown_content:
                continue
            if (candidate.word_count or 0) < 900:
                continue

            score = 0.0
            cand_category = (candidate.content_category or "").strip().lower()
            cand_state = (candidate.target_state or "").strip().lower()
            if target_category and cand_category == target_category:
                score += 4.0
            if target_state and cand_state == target_state:
                score += 2.0
            if candidate.revision_count == 0:
                score += 3.0
            if (candidate.validation_status or "").upper() == "PASS":
                score += 2.0
            if (candidate.sage_score or 0.0) >= threshold:
                score += 2.0
            score += min((candidate.seo_score or 0.0) / 10.0, 2.0)
            score += min((candidate.last_gsc_clicks or 0.0) / 100.0, 2.0)
            ranked.append((score, candidate))

        if not ranked:
            return ""

        ranked.sort(
            key=lambda x: (
                x[0],
                x[1].sage_score or 0.0,
                x[1].seo_score or 0.0,
                x[1].last_gsc_clicks or 0,
                x[1].id or 0,
            ),
            reverse=True,
        )

        lines = [
            "Use these approved article patterns as structure seeds. "
            "Copy the flow, never the wording.",
        ]
        for idx, (_score, sample) in enumerate(ranked[:2], start=1):
            outline = self._extract_outline_from_markdown(sample.markdown_content)
            if not outline:
                continue
            cta_line = ""
            for line in sample.markdown_content.splitlines():
                if self._product_info(article)["domain"] in line.lower():
                    cta_line = re.sub(r"\s+", " ", line).strip()
                    break

            lines.append(
                f"Exemplar {idx}: title='{sample.title}', keyword='{sample.target_keyword}', "
                f"category='{sample.content_category or 'general'}', score={sample.sage_score:.1f}"
            )
            lines.append("H2 flow:")
            lines.append(outline)
            if cta_line:
                lines.append(f"CTA style: {cta_line[:220]}")

        seed_root = Path("reference") / "gold_articles" / target_product
        if seed_root.exists():
            for idx, seed_path in enumerate(sorted(seed_root.glob("*.md"))[:10], start=1):
                try:
                    seed_text = seed_path.read_text(encoding="utf-8")
                except Exception:
                    continue
                outline = self._extract_outline_from_markdown(seed_text)
                if not outline:
                    continue
                lines.append(f"Seed Exemplar {idx}: file='{seed_path.name}'")
                lines.append("H2 flow:")
                lines.append(outline)

        return "\n".join(lines) if len(lines) > 1 else ""

    def _compose_fact_pack(self, article) -> str:
        """Return stored fact pack with deterministic fallback anchors."""
        stored = (getattr(article, "fact_pack", "") or "").strip()
        if stored:
            return stored
        info = self._product_info(article)
        state = (getattr(article, "target_state", "") or "").strip()
        lines = [f"- Product: {info['brand']} ({info['site_url']})"]
        if info["product"] == "medbill":
            lines.extend(
                [
                    "- Source anchors:",
                    "  - https://www.cms.gov/",
                    "  - https://www.cms.gov/nosurprises",
                    "  - https://www.hhs.gov/",
                    "  - https://www.irs.gov/charities-non-profits/charitable-organizations/requirements-for-501c3-hospitals-under-the-affordable-care-act-section-501r",
                ]
            )
        else:
            lines.extend(
                [
                    "- Source anchors:",
                    "  - https://content.naic.org/consumer",
                    "  - https://content.naic.org/state-insurance-departments",
                    "  - https://www.nhtsa.gov/",
                ]
            )
        if state:
            lines.append(f"- State focus: {state}")
        return "\n".join(lines)

    def _build_winner_memory(self, article, published_articles: list) -> str:
        """Retrieve lightweight pattern memory from winning published articles."""
        target_product = (getattr(article, "product", "") or "claimcoach").strip().lower()
        candidates = []
        for p in published_articles:
            if (getattr(p, "product", "") or "claimcoach").strip().lower() != target_product:
                continue
            if not p.markdown_content:
                continue
            if (p.last_gsc_clicks or 0) <= 0 and (p.sage_score or 0) <= 0:
                continue
            candidates.append(p)
        if not candidates:
            return ""
        candidates.sort(
            key=lambda p: (
                p.last_gsc_clicks or 0,
                -(p.last_gsc_position or 100),
                p.sage_score or 0,
            ),
            reverse=True,
        )
        lines = ["Top-performing patterns to imitate structurally (never copy wording):"]
        for idx, p in enumerate(candidates[:3], start=1):
            intro = ""
            for para in p.markdown_content.split("\n\n"):
                cleaned = para.strip()
                if cleaned and not cleaned.startswith("#"):
                    intro = re.sub(r"\s+", " ", cleaned)[:180]
                    break
            h2_count = len(re.findall(r"^##\s+", p.markdown_content, flags=re.MULTILINE))
            faq_count = len(re.findall(r"^###\s+", p.markdown_content, flags=re.MULTILINE))
            lines.append(
                f"- Winner {idx}: '{p.target_keyword}' | clicks={p.last_gsc_clicks or 0}, "
                f"pos={p.last_gsc_position or 'NA'}, H2={h2_count}, FAQ={faq_count}"
            )
            if intro:
                lines.append(f"  Intro cadence: {intro}")
        return "\n".join(lines)

    def _draft_temperature(self, is_revision: bool) -> float:
        """Use lower variance for revision rounds to reduce repeat failures."""
        return self.REVISION_TEMPERATURE if is_revision else self.DRAFT_TEMPERATURE

    # ------------------------------------------------------------------
    # Phase 1: Outline generation
    # ------------------------------------------------------------------
    def _generate_outline(
        self, article, product_context: str, state_rules: str,
        internal_links: str, entity_map: str = "",
        revision_feedback: str = "",
        serp_context: str = "",
        nhtsa_context: str = "",
        fact_pack: str = "",
        winner_memory: str = "",
        model_name: str | None = None,
        seo_template: str = "",
        exemplar_context: str = "",
    ) -> str:
        """Generate a structured outline before writing.

        Uses a shorter LLM call to plan the article structure, key points,
        data to cite, and internal links to use. This ensures the draft
        phase has a clear roadmap and doesn't miss critical sections.

        When revision_feedback is provided, the outline explicitly accounts
        for the issues identified in the previous review round.
        """
        category_hint = CATEGORY_GUIDANCE.get(
            article.content_category or "", ""
        ).strip()

        entity_section = ""
        if entity_map:
            entity_section = (
                f"\n=== ENTITY MAP (use these terms for E-E-A-T authority) ===\n"
                f"{entity_map}\n"
            )

        revision_section = ""
        if revision_feedback:
            revision_section = (
                f"\n=== REVISION FEEDBACK (the previous draft had these issues — "
                f"your outline MUST address every one) ===\n"
                f"{revision_feedback[-1500:]}\n"
            )

        serp_section = ""
        if serp_context:
            serp_section = f"\n{serp_context}\n"

        nhtsa_section = ""
        if nhtsa_context:
            nhtsa_section = f"\n{nhtsa_context}\n"

        fact_pack_section = ""
        if getattr(self.config.pipeline, "quill_use_fact_pack", True):
            raw_fact_pack = fact_pack or (getattr(article, "fact_pack", "") or "")
            if raw_fact_pack:
                fact_pack_section = (
                    "\n=== PRE-WRITE FACT PACK (primary-source anchors) ===\n"
                    f"{raw_fact_pack[:2200]}\n"
                )

        winner_memory_section = ""
        if getattr(self.config.pipeline, "quill_use_winner_memory", True) and winner_memory:
            winner_memory_section = (
                "\n=== WINNER PATTERN MEMORY (from top-performing published posts) ===\n"
                f"{winner_memory[:1800]}\n"
            )

        intent_template_section = ""
        if getattr(article, "intent_template", ""):
            intent_template_section = (
                "\n=== INTENT TEMPLATE ===\n"
                f"- Template: {article.intent_template}\n"
                "- Keep intro/H2/CTA aligned with this intent. Do not drift.\n"
            )

        exemplar_section = ""
        if exemplar_context:
            exemplar_section = (
                "\n=== HIGH-PASS EXEMPLARS (structure seed, do not copy text) ===\n"
                f"{exemplar_context[:2200]}\n"
            )

        template_section = ""
        if seo_template:
            template_section = (
                "\n=== SEO ARTICLE TEMPLATE (follow exactly) ===\n"
                f"{seo_template[:2500]}\n"
            )

        prompt = f"""Create a detailed OUTLINE for an article targeting: "{article.target_keyword}"

Content category: {article.content_category or 'general'}
{f'Target state: {article.target_state}' if article.target_state else ''}

{f'Content brief: {article.content_brief}' if article.content_brief else ''}

{f'Category strategy: {category_hint}' if category_hint else ''}

{f'Internal links available: {internal_links}' if internal_links else ''}
{entity_section}{serp_section}{nhtsa_section}{fact_pack_section}{winner_memory_section}{intent_template_section}{exemplar_section}{revision_section}{template_section}
Create an outline with:
1. **Hook** (first 100 words) — how to open with the keyword naturally
2. **5-7 H2 sections** — each with:
   - The H2 header text (include keyword in at least 2)
   - 3-4 key points to cover
   - Specific data/examples/dollar amounts to include
   - Which internal articles to link to (if relevant)
   - Which entities from the entity map to incorporate
   - **Visual break plan**: what type of visual element fits this section —
     image placeholder, bullet list, numbered steps, comparison table, or
     Adjuster Insider callout. Every section must have at least one.
   - **Image suggestion** (for 2-3 sections): brief description of what image
     would help the reader (chart, diagram, infographic, map). Format:
     IMAGE: [description of what the image should show]
3. **FAQ section** — 3-5 questions with brief answer notes
4. **CTA section** — how to close with ClaimCoach (mid-article subtle mention + closing CTA)
5. **External sources** — 2-3 authoritative sites to reference
6. **Citation plan** — for each section, list the source URL(s) that support legal, numeric, and timeline claims

Be specific about dollar amounts, timelines, and examples to include.
Use the entity map terms naturally throughout — these signal expertise to search engines.
Plan at least 2-3 image placements and ensure every section has a visual break.
Every SERP content gap and FAQ theme provided above is NON-NEGOTIABLE coverage.
Format as a clean outline with ## headers and bullet points."""
        prompt = self._apply_branding(prompt, article=article)

        try:
            outline = self.call_claude(
                prompt=prompt,
                system="You are a content strategist creating detailed article outlines.",
                model=model_name or self.strategy_model,
                max_tokens=2500,
            )
            logger.info(
                f"Generated outline for '{article.target_keyword}' "
                f"({len(outline)} chars)"
            )
            return outline
        except Exception as e:
            logger.warning(f"Outline generation failed, writing without: {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 2: Build the writing prompt
    # ------------------------------------------------------------------
    # Phase 2: Section-by-section drafting (Flash muscle)
    # ------------------------------------------------------------------
    def _draft_from_outline(
        self, article, outline: str, product_context: str,
        state_rules: str, internal_links: str,
        is_revision: bool, lessons: str,
        fact_pack: str = "",
        winner_memory: str = "",
        seo_template: str = "",
        exemplar_context: str = "",
    ) -> str:
        """Draft the article section-by-section using Flash.

        If the Pro-generated outline has clear H2 sections, each section
        is drafted individually — Flash excels at following strict structure.
        Falls back to single-shot drafting when the outline is empty or
        doesn't parse into sections.
        """
        sections = self._parse_outline_sections(outline) if outline else []

        # Fall back to monolithic draft if outline didn't yield sections
        if len(sections) < 3:
            prompt = self._build_prompt(
                article, product_context, state_rules, internal_links,
                is_revision=is_revision, lessons=lessons, outline=outline,
                fact_pack=fact_pack, winner_memory=winner_memory,
                seo_template=seo_template,
                exemplar_context=exemplar_context,
            )
            return self.call_claude(
                prompt=prompt,
                system=self._build_system_prompt(article.content_category, article=article),
                model=self.fast_model,
                max_tokens=8192,
                temperature=self._draft_temperature(is_revision),
            )

        # ── Section-by-section drafting ──
        system = self._build_system_prompt(article.content_category, article=article)
        context_block = (
            f"=== PRODUCT CONTEXT ===\n{product_context[:2000]}\n\n"
            f"=== FULL ARTICLE OUTLINE ===\n{outline}\n\n"
        )
        if article.target_state and state_rules:
            context_block += f"=== STATE RULES ===\n{state_rules[:1500]}\n\n"
        if internal_links:
            context_block += f"=== INTERNAL LINKS ===\n{internal_links}\n\n"
        if lessons:
            context_block += f"=== PAST LESSONS ===\n{lessons}\n\n"
        if getattr(self.config.pipeline, "quill_use_fact_pack", True):
            raw_fact_pack = fact_pack or (getattr(article, "fact_pack", "") or "")
            if raw_fact_pack:
                context_block += (
                    "=== PRE-WRITE FACT PACK ===\n"
                    f"{raw_fact_pack[:1600]}\n\n"
                )
        if getattr(self.config.pipeline, "quill_use_winner_memory", True) and winner_memory:
            context_block += (
                "=== WINNER PATTERN MEMORY ===\n"
                f"{winner_memory[:1200]}\n\n"
            )
        if getattr(article, "intent_template", ""):
            context_block += (
                "=== INTENT TEMPLATE ===\n"
                f"{article.intent_template}\n"
                "Keep this intent intact throughout.\n\n"
            )
        if exemplar_context:
            context_block += (
                "=== HIGH-PASS EXEMPLARS (structure seed; never copy wording) ===\n"
                f"{exemplar_context[:1800]}\n\n"
            )
        if seo_template:
            context_block += (
                f"=== SEO ARTICLE TEMPLATE ===\n{seo_template[:2000]}\n\n"
            )
        if is_revision and article.revision_notes:
            context_block += (
                f"=== REVISION FEEDBACK (address these issues) ===\n"
                f"{article.revision_notes[-2000:]}\n\n"
            )

        # Extract friction points from the content brief for variable injection
        friction_points = self._extract_friction_points(article)

        drafted_sections: list[str] = []
        section_summaries: list[str] = []
        keyword = article.target_keyword

        for i, (heading, bullets) in enumerate(sections):
            is_first = i == 0
            is_last = i == len(sections) - 1
            section_prompt = (
                f"{context_block}"
                f"Target keyword: {keyword}\n\n"
                f"You are writing SECTION {i + 1} of {len(sections)} for this article.\n"
                f"Section heading: {heading}\n"
                f"Key points for this section:\n{bullets}\n\n"
            )
            if is_first:
                section_prompt += (
                    "This is the OPENING section. Include the keyword naturally in "
                    "the first 100 words. Weave in primary entities (Actual Cash Value, "
                    "Policy Limits, Replacement Cost) in the opening. "
                    "Open with empathy and a strong hook.\n"
                )
            if is_last:
                section_prompt += (
                    "This is the CLOSING section. Do NOT start with 'In conclusion' or "
                    "'To summarize.' End with a clear actionable Next Step and CTA "
                    "pointing to ClaimCoach (claimcoach.app). After the section, output:\n"
                    "META_DESCRIPTION: <150-160 character meta description>\n"
                )
            if not is_first:
                # Pass the tail of the previous section (1500 chars) plus a
                # one-line summary of earlier sections so the model maintains
                # narrative coherence without needing the full text.
                prev_tail = drafted_sections[-1][-1500:]
                prior_summary = (
                    " | ".join(section_summaries[:-1]) if len(section_summaries) > 1 else ""
                )
                continuity = (
                    f"\n=== PREVIOUSLY WRITTEN (for continuity) ===\n{prev_tail}\n\n"
                )
                if prior_summary:
                    continuity += (
                        f"=== EARLIER SECTIONS COVERED ===\n{prior_summary}\n"
                        "Do NOT repeat these points. Build on them.\n\n"
                    )
                section_prompt += continuity

            # Inject friction point for the middle sections (where frustration lives)
            if friction_points and 1 <= i <= len(sections) - 2:
                fp_index = (i - 1) % len(friction_points)
                section_prompt += (
                    f"\n=== FRICTION POINT (weave this insider knowledge into the section) ===\n"
                    f"{friction_points[fp_index]}\n\n"
                )

            section_prompt += (
                f"Write ONLY this section (heading + 200-350 words). "
                f"Use ## for the H2 heading. Output Markdown only.\n"
                f"REQUIRED for every section:\n"
                f"- At least one > **Adjuster Insider:** blockquote callout\n"
                f"- At least one visual break: bullet list, numbered steps, bold key terms, or table\n"
                f"- For every technical fact, add a 'Why this matters to your wallet' sentence\n"
                f"- Cite legal/numeric/timeline claims with inline sources: [Source](https://...)\n"
            )
            # Alternate sections get image placeholders (target 2-3 total)
            if i % 2 == 1 and not is_last:
                section_prompt += (
                    f"- Include ONE image placeholder: ![Descriptive alt text about "
                    f"{keyword}](image:relevant-slug). Alt text must be 10+ words.\n"
                )
            section_prompt = self._apply_branding(section_prompt, article=article)

            section_text = self.call_claude(
                prompt=section_prompt,
                system=system,
                model=self.fast_model,
                max_tokens=1500,
                temperature=self._draft_temperature(is_revision),
            )
            drafted_sections.append(section_text.strip())
            # Build a one-line summary of this section for subsequent sections
            # to reference — prevents circular repetition across sections.
            heading_text = heading.lstrip("#").strip()
            section_summaries.append(heading_text)
            logger.debug(
                f"Drafted section {i + 1}/{len(sections)}: {heading} "
                f"({len(section_text)} chars)"
            )

        assembled = "\n\n".join(drafted_sections)
        return self._smooth_section_transitions(assembled, keyword)

    def _smooth_section_transitions(self, content: str, keyword: str) -> str:
        """Lightweight coherence pass over assembled section-by-section content.

        Each section is drafted independently, which can leave abrupt topic
        jumps and repetitive opening patterns ("In this section...", "Now
        let's look at..."). This pass rewrites only the opening sentence of
        each H2 section to create a natural narrative flow.

        Uses the utility model — very cheap (~300 tokens per article) and
        does not touch the body of any section.

        Returns the smoothed content, or the original on failure.
        """
        if not self.has_llm:
            return content

        # Extract the opening lines of each H2 section boundary
        h2_positions = [m.start() for m in re.finditer(r"\n## ", content)]
        if len(h2_positions) < 2:
            return content  # Not enough sections to smooth

        # Build a list of (heading, first_body_sentence) for the prompt
        transitions: list[tuple[int, str, str]] = []
        for pos in h2_positions:
            # Get the heading text
            end_of_heading = content.find("\n", pos + 1)
            if end_of_heading == -1:
                continue
            heading = content[pos + 1:end_of_heading].strip()
            # Get the first non-empty line of body after the heading
            after_heading = content[end_of_heading:].lstrip("\n")
            first_line_end = after_heading.find("\n")
            first_line = after_heading[:first_line_end].strip() if first_line_end > 0 else after_heading[:200].strip()
            if first_line and not first_line.startswith(("![", ">", "-", "*", "1.", "#")):
                transitions.append((pos, heading, first_line))

        if not transitions:
            return content

        prompt = (
            f'An article about "{keyword}" was drafted section-by-section. '
            f"The section openings below may be abrupt, repetitive, or start with "
            f'"In this section" / "Now let\'s" style filler. Rewrite ONLY the opening '
            f"sentence of each section to flow naturally from the previous topic. "
            f"Keep each rewrite under 25 words. Return ONLY the rewrites, one per line, "
            f'in the format: HEADING_TEXT|||NEW_OPENING_SENTENCE\n\n'
        )
        for _, heading, first_line in transitions:
            prompt += f"HEADING: {heading}\nCURRENT OPENING: {first_line}\n\n"

        try:
            response = self.call_claude(
                prompt=prompt,
                system="You are an editor fixing section transitions. Output only the format requested.",
                model=self.utility_model,
                max_tokens=400,
            )
        except Exception as e:
            logger.debug(f"Transition smoothing failed: {e}")
            return content

        # Apply the rewrites
        rewrites: dict[str, str] = {}
        for line in response.splitlines():
            if "|||" in line:
                parts = line.split("|||", 1)
                if len(parts) == 2:
                    rewrites[parts[0].strip()] = parts[1].strip()

        if not rewrites:
            return content

        smoothed = content
        applied = 0
        for _, heading, old_opening in transitions:
            heading_text = heading.lstrip("#").strip()
            new_opening = rewrites.get(heading_text, "")
            if new_opening and old_opening and old_opening in smoothed:
                smoothed = smoothed.replace(old_opening, new_opening, 1)
                applied += 1

        if applied:
            logger.info(f"Smoothed {applied} section transitions for '{keyword}'")
        return smoothed

    @staticmethod
    def _parse_outline_sections(outline: str) -> list[tuple[str, str]]:
        """Parse a Pro-generated outline into (heading, bullet_points) tuples.

        Splits on ## headings and captures everything between them as the
        bullet-point context for that section.
        """
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_lines: list[str] = []

        for line in outline.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                if current_heading:
                    sections.append((current_heading, "\n".join(current_lines)))
                current_heading = stripped
                current_lines = []
            elif current_heading:
                current_lines.append(line)

        if current_heading:
            sections.append((current_heading, "\n".join(current_lines)))

        return sections

    # ------------------------------------------------------------------
    def _build_system_prompt(self, content_category: str, article=None) -> str:
        """Build category-aware system prompt."""
        base = SYSTEM_PROMPT
        guidance = CATEGORY_GUIDANCE.get(content_category or "", "")
        if guidance:
            base += "\n" + guidance
        intent = (getattr(article, "intent_template", "") or "").strip().lower() if article else ""
        intent_guidance = INTENT_TEMPLATE_GUIDANCE.get(intent, "")
        if intent_guidance:
            base += "\n\n" + intent_guidance
        return self._apply_branding(base, article=article)

    def _build_prompt(
        self,
        article,
        product_context: str,
        state_rules: str,
        internal_links: str,
        is_revision: bool,
        lessons: str = "",
        outline: str = "",
        fact_pack: str = "",
        winner_memory: str = "",
        seo_template: str = "",
        exemplar_context: str = "",
    ) -> str:
        parts = []

        # Reference documents
        parts.append(f"=== PRODUCT CONTEXT (follow strictly) ===\n{product_context}\n")

        if article.target_state and state_rules:
            parts.append(f"=== STATE RULES ===\n{state_rules}\n")

        if internal_links:
            parts.append(
                f"=== PUBLISHED ARTICLES (link to these) ===\n{internal_links}\n"
            )

        if lessons:
            parts.append(
                f"=== LESSONS FROM PAST REVIEWS (avoid these mistakes) ===\n{lessons}\n"
            )

        if getattr(self.config.pipeline, "quill_use_fact_pack", True):
            raw_fact_pack = fact_pack or (getattr(article, "fact_pack", "") or "")
            if raw_fact_pack:
                parts.append(
                    f"=== PRE-WRITE FACT PACK (primary sources) ===\n{raw_fact_pack}\n"
                )

        if getattr(self.config.pipeline, "quill_use_winner_memory", True) and winner_memory:
            parts.append(
                "=== WINNER PATTERN MEMORY (retrieval from top performers) ===\n"
                f"{winner_memory}\n"
            )

        if getattr(article, "intent_template", ""):
            parts.append(
                "=== INTENT TEMPLATE ===\n"
                f"{article.intent_template}\n"
                "Match this intent pattern in intro, structure, and CTA placement.\n"
            )

        if exemplar_context:
            parts.append(
                "=== HIGH-PASS EXEMPLARS (structure seed; never copy wording) ===\n"
                f"{exemplar_context}\n"
            )

        if seo_template:
            parts.append(
                f"=== SEO ARTICLE TEMPLATE (follow exactly) ===\n{seo_template}\n"
            )

        if outline:
            parts.append(
                f"=== ARTICLE OUTLINE (follow this structure) ===\n{outline}\n"
            )

        # Writing task
        if is_revision:
            parts.append("=== REVISION TASK ===")
            parts.append("Revise the following article based on reviewer feedback.")
            parts.append(f"Keyword: {article.target_keyword}")
            parts.append(f"Revision notes:\n{article.revision_notes}")
            parts.append(f"\nOriginal article:\n{article.markdown_content}")
        else:
            parts.append("=== WRITING TASK ===")
            parts.append("Write the full article following the outline above.")
            parts.append(f"Target keyword: {article.target_keyword}")
            if article.content_brief and not outline:
                parts.append(f"Content brief:\n{article.content_brief}")
            if article.target_state:
                parts.append(f"Target state: {article.target_state}")
            if article.content_category:
                parts.append(f"Content category: {article.content_category}")
            if article.suggested_title:
                parts.append(
                    f"Suggested title (you can adjust): {article.suggested_title}"
                )

        info = self._product_info(article)
        parts.append(
            f"\nRemember: End the article with a clear CTA pointing to {info['brand']} "
            f"({info['domain']}). After the article, output:\n"
            "META_DESCRIPTION: <150-160 character meta description>\n"
            "Also: cite every legal/numeric/timeline claim with inline sources."
        )

        return self._apply_branding("\n\n".join(parts), article=article)

    # ------------------------------------------------------------------
    # Phase 2.5: Contrastive Critique Loop (anti-AI-laziness)
    # ------------------------------------------------------------------
    def _contrastive_critique(self, content: str, article) -> str:
        """Contrastive Critique Loop — eliminates generic, robotic content.

        A Flash-Lite agent acts as a cynical insurance adjuster, identifying
        passages that are too generic or sound AI-written. The original
        drafting model (Flash) then rewrites those sections.

        This costs nearly nothing (~400 Flash-Lite tokens + ~1500 Flash tokens)
        but dramatically increases the 'human' feel of the content.

        Returns the refined content, or original content on failure.
        """
        if not self.has_llm:
            return content

        keyword = article.target_keyword or ""

        # ── Step 1: Critique (Flash-Lite as cynical adjuster) ──
        critique_prompt = f"""You are a cynical, experienced insurance adjuster reviewing a blog post about "{keyword}".

You've read thousands of generic insurance articles and you can spot AI-written fluff instantly.

Read this article and identify EXACTLY 3 problems:
1. The most GENERIC passage that could appear on any insurance website (quote it)
2. The most ROBOTIC-sounding sentence that no real person would say (quote it)
3. One place where the article makes a VAGUE claim instead of giving specific numbers or actionable steps (quote it)

For each problem, explain in one sentence WHY it's bad and WHAT would make it better.

Format:
GENERIC: "[quoted passage]" — [why it's bad and what to replace it with]
ROBOTIC: "[quoted sentence]" — [why it sounds fake and how a real person would say it]
VAGUE: "[quoted claim]" — [what specific data or action should replace it]

Article:
---
{content[:4000]}
---"""

        try:
            critique = self.call_claude(
                prompt=critique_prompt,
                system="You are a cynical insurance adjuster. Be brutally honest. Quote exactly from the text.",
                model=self.utility_model,
                max_tokens=600,
            )
            logger.debug(f"Critique for '{keyword}': {len(critique)} chars")

            if not critique or len(critique) < 50:
                return content

            # ── Step 2: Refine (Flash rewrites flagged sections) ──
            refine_prompt = f"""You wrote an article about "{keyword}". A reviewer found these problems:

{critique}

Rewrite the COMPLETE article below, fixing ONLY the 3 flagged issues. Do NOT change anything else.
For each fix:
- Replace generic passages with specific, original analysis
- Replace robotic sentences with natural, conversational language
- Replace vague claims with specific numbers, dollar amounts, or actionable steps

Output the full article in Markdown. Keep everything that wasn't flagged exactly the same.

Article:
---
{content}
---"""

            refined = self.call_claude(
                prompt=refine_prompt,
                system=self._build_system_prompt(article.content_category, article=article),
                model=self.fast_model,
                max_tokens=8192,
            )

            # Validate the refinement didn't destroy the article
            refined_clean = refined.strip()
            if len(refined_clean) < len(content) * 0.7:
                logger.warning("Critique refinement shortened article too much, keeping original")
                return content

            logger.info(f"Contrastive critique applied for '{keyword}'")
            return refined_clean

        except Exception as e:
            logger.warning(f"Contrastive critique failed: {e}")
            return content

    def _natural_keyword_insertion(self, keyword: str) -> str:
        """Return a one-sentence keyword mention that reads like a human wrote it.

        Uses the utility model to generate a natural, contextually appropriate
        sentence rather than a static template. Falls back to a category-aware
        template if the LLM call fails.
        """
        if self.has_llm:
            try:
                result = self.call_claude(
                    prompt=(
                        f'Write ONE short sentence (under 20 words) that naturally '
                        f'mentions "{keyword}" from the perspective of a car owner '
                        f'dealing with an insurance settlement. It must sound like '
                        f'a real person, not a blog intro. No AI-isms. No "In today\'s '
                        f'world." Just a direct, grounded sentence. Output only the '
                        f'sentence, no explanation.'
                    ),
                    system="You write plain, human-sounding insurance content.",
                    model=self.utility_model,
                    max_tokens=60,
                )
                sentence = result.strip().strip('"').strip("'")
                if sentence and len(sentence) < 150:
                    if not sentence.endswith((".","!","?")):
                        sentence += "."
                    return sentence + " "
            except Exception:
                pass
        # Fallback: topic-aware alternatives, none of which are generic
        fallbacks = [
            f"Most adjusters underpay on {keyword} — and they're counting on you not to notice.",
            f"Your offer may already be missing value on {keyword}.",
            f"The fight over {keyword} is one you can win with the right numbers.",
        ]
        import hashlib
        idx = int(hashlib.md5(keyword.encode()).hexdigest(), 16) % len(fallbacks)
        return fallbacks[idx] + " "

    # ------------------------------------------------------------------
    # Phase 3: Self-review & auto-fix
    # ------------------------------------------------------------------
    def _self_review_and_fix(
        self, content: str, meta_description: str, article,
        published_articles: list | None = None,
    ) -> tuple[str, str, list[str]]:
        """Run deterministic checks and fix what we can before Sage sees it.

        Returns (fixed_content, fixed_meta, list_of_fixes_applied).
        """
        fixes: list[str] = []
        keyword = article.target_keyword or ""
        keyword_lower = keyword.lower()

        # Pre-check: strip leaked placeholders and metadata artifacts.
        artifact_patterns = [
            r"(?im)^\s*infographic explaining[^\n]*$",
            r"(?im)^\s*meta_description\s*:[^\n]*$",
            r"(?im)^\s*meta_title\s*:[^\n]*$",
            r"(?im)^\s*slug\s*:[^\n]*$",
        ]
        artifact_hits = 0
        for pattern in artifact_patterns:
            cleaned, replaced = re.subn(pattern, "", content)
            if replaced:
                content = cleaned
                artifact_hits += replaced
        if artifact_hits:
            content = re.sub(r"\n{3,}", "\n\n", content).strip()
            fixes.append(f"removed_{artifact_hits}_template_artifacts")

        # Check 1: Keyword in first 100 words (fuzzy match — allows stop words)
        if keyword_lower and not _keyword_match(keyword, content[:500]):
            # Insert keyword into the first paragraph naturally.
            # Use a small LLM call to produce a natural-sounding sentence
            # rather than a static template that triggers AI-ism detectors.
            paragraphs = content.split("\n\n", 1)
            if paragraphs:
                first_para = paragraphs[0]
                sent_end = re.search(r"[.!?]\s", first_para)
                if sent_end:
                    insert_pos = sent_end.end()
                    insertion = self._natural_keyword_insertion(keyword)
                    first_para = (
                        first_para[:insert_pos] + insertion + first_para[insert_pos:]
                    )
                    content = first_para + (
                        "\n\n" + paragraphs[1] if len(paragraphs) > 1 else ""
                    )
                    fixes.append("inserted_keyword_first_100_words")

        # Check 2: CTA placement — 3 CTAs (1 early, 1 contextual, 1 closing)
        content_lower = content.lower()
        domain_pat = re.escape(self._product_info(article)["domain"])
        cta_count = len(re.findall(domain_pat, content_lower))

        if cta_count < 3:
            content, cta_fixes = self._ensure_three_ctas(content, keyword, article)
            fixes.extend(cta_fixes)

        # Check 3: FAQ section present
        if not detect_faq_section(content):
            faq_block = self._generate_faq_block(article)
            if faq_block:
                # Insert before the last section (which should be the CTA)
                cta_marker = re.search(
                    r"\n##\s.*(?:Next Step|Get Started|Take Action)",
                    content, re.IGNORECASE,
                )
                if cta_marker:
                    content = (
                        content[:cta_marker.start()]
                        + "\n\n" + faq_block
                        + content[cta_marker.start():]
                    )
                else:
                    content += "\n\n" + faq_block
                fixes.append("added_faq_section")

        # Check 4: Meta description
        if not meta_description:
            meta_description = self._generate_meta(article)
            fixes.append("generated_meta_description")
        elif len(meta_description) > 165:
            meta_description = meta_description[:157] + "..."
            fixes.append("trimmed_meta_description")
        elif len(meta_description) < 130:
            # Too short — try to extend
            if keyword_lower and not _keyword_match(keyword, meta_description):
                meta_description += f" Learn about {keyword}."
            fixes.append("extended_meta_description")

        # Check 5: Keyword in meta description (fuzzy match)
        if keyword_lower and not _keyword_match(keyword, meta_description):
            # Prepend keyword context
            meta_description = (
                f"{keyword.title()}: {meta_description}"
            )[:160]
            fixes.append("added_keyword_to_meta")

        # Check 6: AI-isms filter — deterministic find-and-replace for
        # banned words that make content sound robotic/AI-generated
        ai_ism_count = 0
        for old_word, new_word in AI_ISM_REPLACEMENTS.items():
            pattern = re.compile(re.escape(old_word), re.IGNORECASE)
            new_content = pattern.sub(new_word, content)
            if new_content != content:
                ai_ism_count += 1
                content = new_content
        # Also strip multi-word AI-isms that have no simple replacement
        for phrase in AI_ISMS:
            if phrase.lower() in content.lower():
                # Remove the phrase while keeping surrounding sentence structure
                pattern = re.compile(
                    r",?\s*" + re.escape(phrase) + r"\s*,?\s*",
                    re.IGNORECASE,
                )
                cleaned = pattern.sub(" ", content)
                if cleaned != content:
                    ai_ism_count += 1
                    content = cleaned
        if ai_ism_count > 0:
            # Clean up any double spaces left by removals
            content = re.sub(r"  +", " ", content)
            fixes.append(f"removed_{ai_ism_count}_ai_isms")

        # Check 7: FAQPage + Article JSON-LD — structured data for rich snippets
        # and E-E-A-T signals (datePublished, author, publisher).
        if '"@type"' not in content:
            json_ld = self._generate_article_json_ld(content, article)
            if json_ld:
                content += "\n\n" + json_ld
                fixes.append("added_article_json_ld")
        elif detect_faq_section(content) and "FAQPage" not in content:
            faq_json_ld = self._generate_faq_json_ld(content)
            if faq_json_ld:
                content += "\n\n" + faq_json_ld
                fixes.append("added_faqpage_json_ld")

        # Check 8: Image placeholders — inject if Quill didn't generate enough
        image_matches = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
        if len(image_matches) < 2:
            content = self._inject_image_placeholders(content, article)
            new_count = len(re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content))
            if new_count > len(image_matches):
                fixes.append(f"injected_{new_count - len(image_matches)}_image_placeholders")

        # Check 9: Internal links — inject links to published articles if missing.
        # This directly impacts Sage scoring: SEO internal_links (3pts) +
        # Sage internal_links category (10pts) = 13pts at stake.
        internal, external = extract_links(content)
        if len(internal) < 3 and published_articles:
            injected = self._inject_internal_links(
                content, published_articles, existing_count=len(internal),
            )
            if injected != content:
                content = injected
                new_internal, _ = extract_links(content)
                fixes.append(f"injected_{len(new_internal) - len(internal)}_internal_links")

        # Check 8: External links — inject authoritative sources if missing.
        # Sage SEO deducts 2pts for missing external links.
        _, external = extract_links(content)
        if len(external) < 2:
            injected = self._inject_external_links(
                content, article, existing_count=len(external),
            )
            if injected != content:
                content = injected
                _, new_external = extract_links(content)
                fixes.append(f"injected_{len(new_external) - len(external)}_external_links")

        # Check 9b: Hard factual-citation gate prep — ensure every legal,
        # numeric, and timeline claim has at least one source citation.
        content, citation_fixes = self._inject_claim_citations(content, article)
        fixes.extend(citation_fixes)

        # Check 10: Freshness — replace stale year references with current year.
        # Sage SEO deducts points for stale content; this auto-fixes the
        # unambiguous cases ("as of 2023" → "as of {current_year}").
        content, freshness_fixes = auto_fix_stale_years(content)
        if freshness_fixes > 0:
            fixes.append(f"fixed_{freshness_fixes}_stale_year_references")

        # Check 11: Interactive conversion tool — embed a mini widget
        # placeholder based on article topic.  Ezra renders these into
        # real HTML during publishing.
        if "<!-- TOOL:" not in content:
            from tools.data import (
                get_tool_display_name,
                get_tool_for_article,
                get_tools_library_url,
            )
            tool_id = get_tool_for_article(keyword, content)
            if tool_id:
                state_attr = ""
                if article.target_state:
                    state_attr = f' data-state="{article.target_state}"'
                tool_name = get_tool_display_name(tool_id)
                tools_url = get_tools_library_url()
                placeholder = (
                    f"\n\n<!-- TOOL:{tool_id}:mini{state_attr} -->\n"
                    f"\nTry the **{tool_name}** and browse more calculators here: "
                    f"[{self._product_info(article)['brand']} Tools]({tools_url}).\n"
                )
                # Insert after the second H2 (roughly after problem section)
                h2_matches = list(re.finditer(r"\n##\s", content))
                if len(h2_matches) >= 2:
                    pos = h2_matches[1].start()
                    content = content[:pos] + placeholder + content[pos:]
                else:
                    # Fallback: insert at ~25% mark
                    pos = len(content) // 4
                    # Find next paragraph break
                    nl = content.find("\n\n", pos)
                    if nl > 0:
                        content = content[:nl] + placeholder + content[nl:]
                    else:
                        content += placeholder
                fixes.append(f"embedded_tool_{tool_id}")

        if fixes:
            logger.info(f"Self-review applied {len(fixes)} fixes: {fixes}")

        return content, meta_description, fixes

    @staticmethod
    def _citation_url_for_claim(article, claim_type: str) -> str:
        """Pick an authoritative fallback citation URL per claim type."""
        state = (getattr(article, "target_state", "") or "").strip().lower()
        if claim_type == "legal":
            if state:
                return "https://www.naic.org/state_web_map.htm"
            return "https://www.law.cornell.edu/"
        if claim_type == "timeline":
            return "https://content.naic.org/consumer/auto-insurance.htm"
        return "https://www.naic.org/"

    def _inject_claim_citations(self, content: str, article) -> tuple[str, list[str]]:
        """Inject inline citations for unsupported factual claims.

        This is a deterministic fallback to help drafts satisfy Sage's
        factual-claim gate even when the model misses a citation.
        """
        result = evaluate_factual_claims(content)
        if not result.blocking:
            return content, []

        inserted = 0
        for check in result.checks:
            if check.confidence != "unsupported":
                continue

            citation_url = self._citation_url_for_claim(article, check.claim_type)
            source_name = "State DOI/NAIC" if check.claim_type == "legal" else "NAIC"
            target = check.claim.strip()
            if not target:
                continue
            if citation_url in target:
                continue

            replacement = (
                f"{target} [Source: {source_name}]({citation_url})"
            )
            if target in content:
                content = content.replace(target, replacement, 1)
                inserted += 1
                continue

            # Fallback: match by prefix if sentence normalization differs.
            prefix = re.escape(target[:80]).replace(r"\ ", r"\s+")
            m = re.search(prefix, content, re.IGNORECASE)
            if m:
                end = m.end()
                content = (
                    content[:end]
                    + f" [Source: {source_name}]({citation_url})"
                    + content[end:]
                )
                inserted += 1

        if inserted == 0:
            return content, []
        return content, [f"injected_{inserted}_claim_citations"]

    def _ensure_three_ctas(self, content: str, keyword: str, article) -> tuple[str, list[str]]:
        """Ensure the article has 3 strategically placed CTAs.

        Strategy:
        1. Early CTA — within first 300 words (after emotional hook/problem)
        2. Contextual CTA — after line-items/mid-body section
        3. Closing CTA — in or after FAQ section

        Returns (modified_content, list_of_fixes).
        """
        fixes: list[str] = []
        content_lower = content.lower()

        # Count existing CTA links
        info = self._product_info(article)
        cta_positions = [
            m.start() for m in re.finditer(re.escape(info["domain"]), content_lower)
        ]

        # Split content into words for position tracking
        words = content.split()
        # CTA copy variants (varied, not repetitive)
        cta_variants = [
            (
                "\n\n> See what's missing in 5 minutes — "
                f"[check it free]({info['site_url']}).\n"
            ),
            (
                f"\n\n[{info['brand']} checks these line items automatically]"
                f"({info['site_url']}) — upload your offer and see what "
                "they left out.\n"
            ),
            (
                f"\n\nGet your analysis at "
                f"[{info['brand']}]({info['site_url']}) — it takes 5 minutes.\n"
            ),
            (
                f"\n\nDon't leave money on the table. "
                f"[{info['brand']}]({info['site_url']}) analyzes your case "
                "and shows where value may be missing.\n"
            ),
        ]

        # If no brand mention at all, treat all zones as missing
        if info["brand"].lower() not in content_lower:
            cta_positions = []

        # Check which zones already have CTAs
        total_chars = len(content)
        # Zone boundaries (character positions)
        early_end = 0
        # Find the char position of the 300th word
        word_idx = 0
        for i, ch in enumerate(content):
            if ch in (" ", "\n"):
                word_idx += 1
                if word_idx >= 300:
                    early_end = i
                    break
        if early_end == 0:
            early_end = int(total_chars * 0.2)

        mid_start = int(total_chars * 0.25)
        mid_end = int(total_chars * 0.55)
        closing_start = int(total_chars * 0.80)

        has_early = any(p < early_end for p in cta_positions)
        has_mid = any(mid_start <= p <= mid_end for p in cta_positions)
        has_closing = any(p >= closing_start for p in cta_positions)

        needed: list[tuple[int, str]] = []  # (variant_idx, zone)
        if not has_early:
            needed.append((0, "early"))
        if not has_mid:
            needed.append((1, "mid"))
        if not has_closing:
            needed.append((2, "closing"))

        if not needed:
            return content, fixes

        # Inject missing CTAs at appropriate positions
        h2_positions = [m.start() for m in re.finditer(r"\n##\s", content)]

        # Build insertion points per zone
        for variant_idx, zone in needed:
            cta_text = cta_variants[variant_idx]
            insert_pos = None

            if zone == "early":
                # After the first H2 section (end of "problem" section)
                if len(h2_positions) >= 2:
                    insert_pos = h2_positions[1]
                else:
                    # Fallback: after first ~300 words
                    insert_pos = early_end

            elif zone == "mid":
                # After the second or third H2 section
                if len(h2_positions) >= 4:
                    insert_pos = h2_positions[3]
                elif len(h2_positions) >= 3:
                    insert_pos = h2_positions[2]
                else:
                    insert_pos = int(total_chars * 0.4)

            elif zone == "closing":
                # At the very end of the article
                insert_pos = len(content)

            if insert_pos is not None:
                if insert_pos >= len(content):
                    content += cta_text
                else:
                    # Insert before the next section heading
                    content = content[:insert_pos] + cta_text + content[insert_pos:]
                    # Adjust h2_positions for subsequent insertions
                    offset = len(cta_text)
                    h2_positions = [
                        p + offset if p >= insert_pos else p
                        for p in h2_positions
                    ]
                fixes.append(f"injected_{zone}_cta")

        return content, fixes

    def _inject_internal_links(
        self, content: str, published: list, target_article, existing_count: int = 0,
    ) -> str:
        """Deterministically inject internal links to published articles.

        Finds mentions of published article keywords/titles in the content
        and wraps the first occurrence with a markdown link.
        """
        target_count = max(0, 3 - existing_count)
        if target_count == 0 or not published:
            return content

        injected = 0
        for article in published[:15]:
            if injected >= target_count:
                break
            if article.published_url:
                url = article.published_url
            else:
                info = self._product_info(article)
                url = f"{info['site_url'].rstrip('/')}/blog/{article.slug}"
            if not url or url in content:
                continue

            # Try to find the article's keyword or title in the content
            keyword = article.target_keyword or ""
            title = article.title or ""
            anchor = None
            for term in [keyword, title]:
                if not term or len(term) < 5:
                    continue
                # Look for the term in prose (not inside existing links or headers)
                escaped = re.escape(term)
                pattern = re.compile(
                    r"(?<!\[)(?<!\()" + escaped + r"(?!\])" + r"(?!\))",
                    re.IGNORECASE,
                )
                match = pattern.search(content)
                if match:
                    anchor = match.group(0)
                    content = content[:match.start()] + f"[{anchor}]({url})" + content[match.end():]
                    injected += 1
                    break

        # Fallback: if no keyword matches found, append a "Related Reading" section
        if injected == 0 and target_count > 0:
            related = []
            for article in published[:3]:
                if article.published_url:
                    url = article.published_url
                else:
                    info = self._product_info(article)
                    url = f"{info['site_url'].rstrip('/')}/blog/{article.slug}"
                if url:
                    related.append(f"- [{article.title}]({url})")
            if related:
                # Insert before the last ## section (CTA)
                cta_match = re.search(
                    r"\n##\s.*(?:Next Step|Get Started|Take Action)",
                    content, re.IGNORECASE,
                )
                block = "\n\n## Related Reading\n\n" + "\n".join(related) + "\n"
                if cta_match:
                    content = content[:cta_match.start()] + block + content[cta_match.start():]
                else:
                    content += block
                injected = len(related)

        return content

    @staticmethod
    def _inject_external_links(
        content: str, article, existing_count: int = 0,
    ) -> str:
        """Inject authoritative external links if the article has fewer than 2.

        Uses state-specific DOI links when a target_state is set, otherwise
        falls back to general authoritative insurance sources.
        """
        target_count = max(0, 2 - existing_count)
        if target_count == 0:
            return content

        state = (article.target_state or "").strip()

        # Authoritative sources with anchor text patterns to look for
        sources = []
        if state:
            sources.append({
                "url": f"https://www.naic.org/state_web_map.htm",
                "anchors": ["department of insurance", "doi", "state insurance", "naic",
                            "insurance commissioner", "insurance regulator"],
                "fallback_text": f"National Association of Insurance Commissioners (NAIC)",
            })
        sources.extend([
            {
                "url": "https://www.naic.org/",
                "anchors": ["naic", "national association of insurance",
                            "insurance commissioner", "insurance regulation"],
                "fallback_text": "National Association of Insurance Commissioners",
            },
            {
                "url": "https://consumer.ftc.gov/",
                "anchors": ["federal trade commission", "ftc", "consumer protection",
                            "consumer rights"],
                "fallback_text": "Federal Trade Commission consumer resources",
            },
        ])

        injected = 0
        for source in sources:
            if injected >= target_count:
                break
            if source["url"] in content:
                continue

            linked = False
            for anchor_text in source["anchors"]:
                escaped = re.escape(anchor_text)
                pattern = re.compile(
                    r"(?<!\[)(?<!\()" + escaped + r"(?!\])" + r"(?!\))",
                    re.IGNORECASE,
                )
                match = pattern.search(content)
                if match:
                    original = match.group(0)
                    content = (
                        content[:match.start()]
                        + f"[{original}]({source['url']})"
                        + content[match.end():]
                    )
                    injected += 1
                    linked = True
                    break

            # If no anchor found in text, skip (don't force-insert)
        return content

    def _generate_article_json_ld(self, content: str, article) -> str:
        """Generate combined Article + FAQPage JSON-LD for E-E-A-T signals.

        Produces a single script block with:
        - Article schema (datePublished, author, publisher, headline)
        - FAQPage schema (if FAQ section exists)

        This provides search engines with structured metadata that boosts
        credibility and enables rich snippet display.
        """
        import json as _json
        from datetime import date

        title = article.suggested_title or article.title or article.target_keyword.title()
        keyword = article.target_keyword or ""
        meta_desc = article.meta_description or ""
        today = date.today().isoformat()
        info = self._product_info(article)

        schemas: list[dict] = []

        # Article schema — E-E-A-T signals
        article_schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title[:110],
            "description": meta_desc[:160] if meta_desc else f"Guide to {keyword}",
            "datePublished": today,
            "dateModified": today,
            "author": {
                "@type": "Organization",
                "name": info["brand"],
                "url": info["site_url"],
            },
            "publisher": {
                "@type": "Organization",
                "name": info["brand"],
                "url": info["site_url"],
                "logo": {
                    "@type": "ImageObject",
                    "url": info["site_url"].rstrip("/") + "/logo.png",
                },
            },
        }

        # Add article image if placeholders exist
        images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
        if images:
            article_schema["image"] = [
                f"{info['site_url'].rstrip('/')}/images/{url.replace('image:', '')}.webp"
                for _alt, url in images[:3]
                if url.startswith("image:")
            ]

        schemas.append(article_schema)

        # FAQPage schema — extract from FAQ H3s
        faq_match = re.search(
            r"^##\s+.*(?:FAQ|Frequently Asked|Common Questions).*$",
            content, re.MULTILINE | re.IGNORECASE,
        )
        if faq_match:
            faq_text = content[faq_match.end():]
            next_h2 = re.search(r"^##\s+", faq_text, re.MULTILINE)
            if next_h2:
                faq_text = faq_text[:next_h2.start()]

            pairs: list[dict] = []
            questions = re.split(r"^###\s+", faq_text, flags=re.MULTILINE)
            for q_block in questions:
                q_block = q_block.strip()
                if not q_block:
                    continue
                lines = q_block.split("\n", 1)
                question = lines[0].strip().rstrip("?") + "?"
                answer = lines[1].strip() if len(lines) > 1 else ""
                answer = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", answer)
                answer = re.sub(r"[*_`]", "", answer).strip()
                if question and answer and len(answer) > 20:
                    pairs.append({
                        "@type": "Question",
                        "name": question,
                        "acceptedAnswer": {"@type": "Answer", "text": answer},
                    })

            if len(pairs) >= 2:
                schemas.append({
                    "@context": "https://schema.org",
                    "@type": "FAQPage",
                    "mainEntity": pairs,
                })

        if not schemas:
            return ""

        # Combine into a single script block with @graph if multiple schemas
        if len(schemas) == 1:
            json_str = _json.dumps(schemas[0], indent=2)
        else:
            graph = {
                "@context": "https://schema.org",
                "@graph": schemas,
            }
            json_str = _json.dumps(graph, indent=2)

        return f'<script type="application/ld+json">\n{json_str}\n</script>'

    @staticmethod
    def _generate_faq_json_ld(content: str) -> str:
        """Extract FAQ H3 questions/answers and produce FAQPage JSON-LD.

        Parses the markdown FAQ section and converts Q&A pairs into the
        schema.org FAQPage format that Google uses for rich snippets.
        """
        import json as _json

        # Find FAQ section and extract Q&A pairs
        faq_match = re.search(
            r"^##\s+.*(?:FAQ|Frequently Asked|Common Questions).*$",
            content, re.MULTILINE | re.IGNORECASE,
        )
        if not faq_match:
            return ""

        faq_text = content[faq_match.end():]
        # Stop at next H2
        next_h2 = re.search(r"^##\s+", faq_text, re.MULTILINE)
        if next_h2:
            faq_text = faq_text[:next_h2.start()]

        # Parse ### Question? / Answer pairs
        pairs: list[dict] = []
        questions = re.split(r"^###\s+", faq_text, flags=re.MULTILINE)
        for q_block in questions:
            q_block = q_block.strip()
            if not q_block:
                continue
            lines = q_block.split("\n", 1)
            question = lines[0].strip().rstrip("?") + "?"
            answer = lines[1].strip() if len(lines) > 1 else ""
            # Clean markdown from answer
            answer = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", answer)
            answer = re.sub(r"[*_`]", "", answer)
            answer = answer.strip()
            if question and answer and len(answer) > 20:
                pairs.append({
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": answer,
                    },
                })

        if len(pairs) < 2:
            return ""

        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": pairs,
        }

        json_str = _json.dumps(schema, indent=2)
        return f'<script type="application/ld+json">\n{json_str}\n</script>'

    @staticmethod
    def _inject_image_placeholders(content: str, article) -> str:
        """Inject image placeholders into sections that lack visual breaks.

        Places descriptive image placeholders after the first paragraph of
        H2 sections that don't already contain images, lists, or tables.
        """
        keyword = article.target_keyword or "insurance claim"
        slug_keyword = re.sub(r"\s+", "-", keyword.lower())[:30]

        sections = re.split(r"(^##\s+.+$)", content, flags=re.MULTILINE)
        injected = 0
        max_inject = 3
        result_parts: list[str] = []

        # Image descriptions keyed to common section themes
        image_hints = [
            (r"step|how to|process|guide", f"Step-by-step diagram showing the {keyword} process"),
            (r"value|worth|amount|cost|money", f"Chart comparing typical {keyword} amounts and ranges"),
            (r"example|case|scenario|story", f"Example breakdown of a real {keyword} case with numbers"),
            (r"state|law|regulation|statute", f"Map showing {keyword} regulations by state"),
            (r"compare|vs|versus|difference", f"Side-by-side comparison table for {keyword} options"),
        ]
        default_alt = f"Breakdown of key factors that affect {keyword}"

        for i, part in enumerate(sections):
            result_parts.append(part)
            # Only inject after H2 headings
            if not re.match(r"^##\s+", part, re.MULTILINE):
                continue
            if injected >= max_inject:
                continue
            # Check if the next section already has an image
            next_section = sections[i + 1] if i + 1 < len(sections) else ""
            if re.search(r"!\[", next_section):
                continue

            # Pick alt text based on section heading
            heading_lower = part.lower()
            alt_text = default_alt
            for pattern, hint in image_hints:
                if re.search(pattern, heading_lower):
                    alt_text = hint
                    break

            slug = f"image:{slug_keyword}-{injected + 1}"

            # Find first paragraph end in the next section to insert after
            if i + 1 < len(sections):
                section_text = sections[i + 1]
                para_end = re.search(r"\n\n", section_text)
                if para_end:
                    insert_pos = para_end.end()
                    sections[i + 1] = (
                        section_text[:insert_pos]
                        + f"\n![{alt_text}]({slug})\n\n"
                        + section_text[insert_pos:]
                    )
                    injected += 1

        return "".join(result_parts)

    def _generate_faq_block(self, article) -> str:
        """Generate a quick FAQ section using Flash-Lite (utility tier)."""
        try:
            result = self.call_claude(
                prompt=(
                    f'Generate an FAQ section for an article about "{article.target_keyword}". '
                    f"Write 3-4 questions and concise answers (2-3 sentences each). "
                    f"Format as:\n## Frequently Asked Questions\n\n"
                    f"### Question here?\n\nAnswer here.\n\n"
                    f"Make questions real things people search for."
                ),
                model=self.utility_model,
                max_tokens=800,
            )
            faq = result.strip()
            # Validate FAQ structure: need H3 questions with answers
            if not self._validate_faq_structure(faq):
                logger.warning("Generated FAQ failed structure validation, discarding")
                return ""
            return faq
        except Exception as e:
            logger.warning(f"FAQ generation failed: {e}")
            return ""

    @staticmethod
    def _validate_faq_structure(faq_text: str) -> bool:
        """Validate FAQ has proper Q&A structure.

        Requirements:
        - At least 2 H3 headers (### Question?)
        - Each question should end with '?'
        - Each answer should be at least 30 chars
        """
        if not faq_text:
            return False
        h3_pattern = re.compile(r"^###\s+(.+)$", re.MULTILINE)
        questions = h3_pattern.findall(faq_text)
        if len(questions) < 2:
            return False
        # Check questions end with ?
        valid_qs = sum(1 for q in questions if q.strip().endswith("?"))
        if valid_qs < 2:
            return False
        # Check answers exist between questions
        sections = re.split(r"^###\s+", faq_text, flags=re.MULTILINE)
        for section in sections[1:]:  # skip text before first ###
            # After the question line, the answer follows
            lines = section.split("\n", 1)
            answer = lines[1].strip() if len(lines) > 1 else ""
            if len(answer) < 30:
                return False
        return True

    def _generate_meta(self, article) -> str:
        """Generate a meta description using Flash-Lite (utility tier)."""
        keyword = article.target_keyword or ""
        info = self._product_info(article)
        try:
            result = self.call_claude(
                prompt=(
                    f'Write a meta description (150-160 chars) for an article about '
                    f'"{keyword}" for {info["brand"]}. Include the keyword, an emotional '
                    f'hook, and a reason to click. Return ONLY the meta description.'
                ),
                model=self.utility_model,
                max_tokens=80,
            )
            meta = result.strip().strip('"')
            if 100 < len(meta) <= 165:
                return meta
        except Exception as e:
            logger.debug(f"LLM meta generation failed, using template: {e}")
        # Template fallback
        return (
            f"Learn how to handle {keyword.lower()}. "
            f"Step-by-step guide with specific strategies to fight back "
            f"and get the settlement you deserve."
        )[:160]

    # ------------------------------------------------------------------
    # Quality gate — prevent submission to Sage when content clearly
    # cannot meet the approval threshold.
    # ------------------------------------------------------------------

    MIN_WORD_COUNT = 1000
    MAX_WORD_COUNT = 1800
    MIN_READABILITY = 40

    def _passes_minimum_bar(self, content: str, wc: int) -> tuple[bool, str]:
        """Check if content meets the minimum bar for Sage review.

        Returns (passes, reason). Articles that fail are held back so
        Quill can retry rather than wasting a Sage review cycle.
        """
        reasons = []
        if wc < self.MIN_WORD_COUNT:
            reasons.append(f"Word count {wc} below minimum {self.MIN_WORD_COUNT}")
        if wc > self.MAX_WORD_COUNT:
            reasons.append(f"Word count {wc} exceeds maximum {self.MAX_WORD_COUNT}")

        report = readability_report(content)
        if report["flesch_kincaid"] < self.MIN_READABILITY:
            reasons.append(
                f"Readability FK {report['flesch_kincaid']} below minimum {self.MIN_READABILITY}"
            )

        # Hard structural blockers — don't send corrupted drafts to Sage.
        lower = (content or "").lower()
        if not re.search(r"^##\s+", content, flags=re.MULTILINE):
            reasons.append("Missing H2 structure")
        if "frequently asked questions" not in lower and "## faq" not in lower:
            reasons.append("Missing FAQ section")
        if len(re.findall(r"^###\s+", content, flags=re.MULTILINE)) < 2:
            reasons.append("FAQ/questions too thin (<2 H3 questions)")
        if re.search(
            r"(infographic explaining|step-by-step diagram showing|chart comparing typical)",
            lower,
        ):
            reasons.append("Contains placeholder artifact text")
        if re.search(
            r"(?:meta_description|meta_title|slug|target_state|keyword)\s*:",
            content,
            flags=re.IGNORECASE,
        ):
            reasons.append("Contains leaked metadata markers in body")
        if re.search(r"[A-Za-z]\s+\*\s+[A-Za-z]", content):
            reasons.append("Contains malformed inline bullet injection")
        if re.search(r"(?:\b[A-Za-z]{2,}\s+){4,}\.\s*$", content):
            # Whole article ending with an unfinished run-on sentence.
            reasons.append("Article appears truncated at ending")

        if reasons:
            return False, "; ".join(reasons)
        return True, ""

    # ------------------------------------------------------------------
    # Targeted revision (instead of full rewrite)
    # ------------------------------------------------------------------
    def _targeted_revision(
        self, article, product_context: str, state_rules: str,
        internal_links: str, lessons: str,
        published_articles: list | None = None,
    ) -> dict[str, Any] | None:
        """Fix specific issues from Sage's feedback without rewriting everything.

        Parses the revision_notes to identify specific, fixable issues and
        makes targeted edits. Returns None if the issues are too broad for
        targeted fixes (falls through to full rewrite).

        Skips targeted revision on round 2+ because if the first targeted
        attempt didn't raise the score, a full rewrite is needed.
        """
        # On 2nd+ revision, targeted fixes already ran once — go straight
        # to full rewrite which produces substantially different content.
        if article.revision_count >= 2:
            logger.info(
                f"Revision round {article.revision_count} — skipping targeted "
                f"revision, doing full rewrite"
            )
            return None

        notes = article.revision_notes or ""
        structured = self._parse_structured_issue_payload(notes)
        structured_codes = {
            (item.get("code") or "").strip().upper()
            for item in structured
            if (item.get("code") or "").strip()
        }
        issues = self._parse_revision_issues(notes)
        if not issues:
            return None

        # Classify issues as targeted-fixable or needs-full-rewrite
        targeted = []
        broad = []
        for issue in issues:
            code = ""
            match = re.match(r"^\[([A-Z0-9_]+)\]\s*", issue)
            if match:
                code = match.group(1).upper()
            issue_lower = issue.lower()
            if code in self.BROAD_REWRITE_CODES:
                broad.append(issue)
                continue
            if code in self.ROUTINE_ISSUE_CODES:
                targeted.append(issue)
                continue
            if any(kw in issue_lower for kw in (
                "keyword not in", "no mention of claimcoach", "no link to claimcoach",
                "no faq section", "no internal links", "no external links",
                "word count", "meta description", "flagged phrase",
                "no mention", "no link",
                # Media & formatting issues fixable by self-review
                "no image", "image placeholder", "missing alt text",
                "no faqpage", "json-ld", "schema",
                "list items", "bold phrase", "scanability",
                "blockquote", "callout",
                # CTA placement issues
                "no mid-article", "closing section",
                "no cta in first", "only 1 cta", "only 2 cta", "only 3 cta",
                "first 300 words", "need 3 cta", "target: 3",
            )):
                targeted.append(issue)
            else:
                broad.append(issue)

        if structured_codes:
            for code in sorted(structured_codes):
                tagged = f"[{code}]"
                if code in self.BROAD_REWRITE_CODES and not any(tagged in b for b in broad):
                    broad.append(f"{tagged} structured review flagged this issue")
                elif code in self.ROUTINE_ISSUE_CODES and not any(tagged in t for t in targeted):
                    targeted.append(f"{tagged} structured review flagged this issue")

        # If most issues are broad, do a full rewrite
        if len(broad) > len(targeted) and not structured_codes:
            logger.info(
                f"Revision needs full rewrite ({len(broad)} broad vs "
                f"{len(targeted)} targeted issues)"
            )
            return None

        logger.info(
            f"Attempting targeted revision: {len(targeted)} fixable, "
            f"{len(broad)} broad issues"
        )

        original_content = article.markdown_content
        content = original_content
        meta = article.meta_description or ""
        fixes: list[str] = []

        if structured_codes:
            content, meta, routine_fixes = self._apply_structured_fix_routines(
                content, meta, article, structured_codes, published_articles=published_articles,
            )
            fixes.extend(routine_fixes)

        # Apply targeted fixes
        content, meta, auto_fixes = self._self_review_and_fix(
            content, meta, article, published_articles=published_articles,
        )
        fixes.extend(auto_fixes)

        # For remaining broad issues, ask LLM for a focused edit
        if broad:
            try:
                edit_prompt = (
                    f"You have an existing article about \"{article.target_keyword}\". "
                    f"Make ONLY the following changes — do NOT rewrite sections that are fine.\n\n"
                    f"Issues to fix:\n"
                    + "\n".join(f"- {issue}" for issue in broad) +
                    f"\n\n=== PRODUCT CONTEXT ===\n{product_context[:1000]}\n"
                    f"\n=== CURRENT ARTICLE ===\n{content}\n"
                    f"\nOutput the COMPLETE article with fixes applied. "
                    f"Keep everything else exactly the same."
                )
                result = self.call_claude(
                    prompt=edit_prompt,
                    system=self._build_system_prompt(article.content_category, article=article),
                    model=(
                        self.strategy_model
                        if self._is_high_trust_article(article, is_revision=True)
                        else self.fast_model
                    ),
                    max_tokens=8192,
                    temperature=self._draft_temperature(True),
                )
                content, new_meta = self._parse_result(result)
                if new_meta:
                    meta = new_meta
                fixes.append(f"llm_targeted_fix:{len(broad)}_issues")
            except RateLimitError:
                raise  # Let rate limits propagate — don't bounce to revision
            except Exception as e:
                logger.warning(f"Targeted LLM fix failed: {e}")
                return None  # Fall through to full rewrite

        try:
            # Verify the revision actually changed the content
            if original_content and content.strip() == original_content.strip():
                logger.warning(
                    f"Targeted revision of {article.id} produced identical content — "
                    f"falling through to full rewrite"
                )
                return None

            # Check minimum change threshold (at least 5% different)
            if original_content:
                overlap = sum(
                    1 for a, b in zip(content, original_content) if a == b
                )
                max_len = max(len(content), len(original_content), 1)
                similarity = overlap / max_len
                if similarity > 0.95:
                    logger.warning(
                        f"Targeted revision of {article.id} changed <5% of content — "
                        f"falling through to full rewrite"
                    )
                    return None

            slug = self._generate_slug(
                article.suggested_title or article.title or article.target_keyword.title()
            )
            wc = word_count(content)

            # Quality gate — same check as _write_one
            gate_pass, gate_reason = self._passes_minimum_bar(content, wc)
            if not gate_pass:
                logger.warning(
                    f"Targeted revision of {article.id} failed quality gate: {gate_reason}"
                )
                self.db.update_article(
                    article.id,
                    markdown_content=content,
                    meta_description=meta,
                    slug=slug,
                    word_count=wc,
                    status=ArticleStatus.REVISION.value,
                    writer_claim="",
                    revision_notes=(article.revision_notes or "")
                    + f"\n\n[QUILL SELF-CHECK FAIL] {gate_reason}",
                )
                return None  # Fall through to full rewrite

            self.db.update_article(
                article.id,
                markdown_content=content,
                meta_description=meta,
                slug=slug,
                word_count=wc,
                status=ArticleStatus.EDITOR_REVIEW.value,
                writer_claim="",
                revision_notes=(
                    f"[Targeted revision in round {article.revision_count + 1} — "
                    f"applied fixes: {', '.join(fixes) or 'none'}]"
                ),
            )
        except Exception as e:
            logger.error(
                f"Post-revision error for article {article.id}: {e}", exc_info=True
            )
            self.db.update_article(
                article.id, status=ArticleStatus.REVISION.value, writer_claim=""
            )
            return None  # Fall through to full rewrite

        self.db.record_metric("quill_write", wc, json.dumps({
            "article_id": article.id,
            "keyword": article.target_keyword,
            "is_revision": True,
            "revision_type": "targeted",
            "word_count": wc,
            "self_review_fixes": fixes,
        }))

        logger.info(
            f"Targeted revision of article {article.id} ({wc} words, "
            f"{len(fixes)} fixes)"
        )
        return {
            "status": "success",
            "article_id": article.id,
            "title": article.title,
            "word_count": wc,
            "is_revision": True,
            "revision_type": "targeted",
            "self_review_fixes": fixes,
        }

    @staticmethod
    def _parse_structured_issue_payload(notes: str) -> list[dict[str, str]]:
        """Parse Sage's machine-readable issue block from revision notes."""
        if not notes:
            return []
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", notes, flags=re.DOTALL)
        for raw in reversed(blocks):
            try:
                data = json.loads(raw)
            except Exception:
                continue
            issues = data.get("issues", [])
            if not isinstance(issues, list):
                continue
            parsed: list[dict[str, str]] = []
            for item in issues:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip().upper()
                issue = str(item.get("issue", "")).strip()
                category = str(item.get("category", "")).strip().lower()
                if not code and not issue:
                    continue
                parsed.append({
                    "code": code,
                    "issue": issue,
                    "category": category,
                })
            if parsed:
                return parsed
        return []

    def _parse_revision_issues(self, notes: str) -> list[str]:
        """Extract individual issues from Sage's revision notes.

        Sage formats notes as:
            ### Breakdown:
            - **seo**: 15/20
              - Keyword density too low       <-- indented sub-issue
              - No FAQ section                <-- indented sub-issue
            ### Issues to Fix:
            - Keyword density too low         <-- top-level duplicate

        We parse BOTH indented sub-issues (under category headers) and
        the top-level "Issues to Fix" list, then deduplicate.
        """
        issues = []
        seen = set()
        for item in self._parse_structured_issue_payload(notes):
            code = item.get("code", "").strip()
            issue = item.get("issue", "").strip()
            rendered = f"[{code}] {issue}" if code and issue else (issue or f"[{code}]")
            norm = rendered.lower()
            if norm and norm not in seen:
                seen.add(norm)
                issues.append(rendered)

        in_code_block = False
        for line in notes.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # Skip category header lines like "- **seo**: 15/20"
            if stripped.startswith("- **") and ":" in stripped:
                continue
            # Skip section headers and empty lines
            if stripped.startswith("#") or not stripped:
                continue
            # Match both top-level "- issue" and indented "  - issue"
            if stripped.startswith("- ") and len(stripped) > 7:
                issue = stripped[2:].strip()
                # Skip score-carried lines and meta lines
                if issue.startswith("[") or issue.startswith("Plagiarism score carried"):
                    continue
                norm = issue.lower()
                if norm not in seen:
                    seen.add(norm)
                    issues.append(issue)
        return issues

    @staticmethod
    def _inject_keyword_in_opening(content: str, keyword: str) -> str:
        """Insert the target keyword naturally into the opening paragraph."""
        if not keyword:
            return content
        paragraphs = content.split("\n\n", 1)
        if not paragraphs:
            return content
        first_para = paragraphs[0]
        sent_end = re.search(r"[.!?]\s", first_para)
        if not sent_end:
            return content
        insert_pos = sent_end.end()
        insertion = f"When it comes to {keyword}, knowledge is your best weapon. "
        updated_first = first_para[:insert_pos] + insertion + first_para[insert_pos:]
        return updated_first + ("\n\n" + paragraphs[1] if len(paragraphs) > 1 else "")

    def _apply_structured_fix_routines(
        self,
        content: str,
        meta_description: str,
        article,
        issue_codes: set[str],
        published_articles: list | None = None,
    ) -> tuple[str, str, list[str]]:
        """Apply deterministic fix routines keyed by Sage structured issue codes."""
        fixes: list[str] = []
        codes = {c.strip().upper() for c in issue_codes if c}
        keyword = article.target_keyword or ""

        if "SEO_KEYWORD_OPENING" in codes and keyword and not _keyword_match(keyword, content[:500]):
            updated = self._inject_keyword_in_opening(content, keyword)
            if updated != content:
                content = updated
                fixes.append("routine_seo_keyword_opening")

        if codes & {"CTA_MISSING", "CTA_LINK_MISSING", "CTA_EARLY_MISSING", "CTA_COUNT_LOW"}:
            updated, cta_fixes = self._ensure_three_ctas(content, keyword, article)
            if updated != content:
                content = updated
            if cta_fixes:
                fixes.append("routine_cta_layout")

        if "SEO_FAQ_MISSING" in codes and not detect_faq_section(content):
            faq_block = self._generate_faq_block(article)
            if faq_block:
                content += "\n\n" + faq_block
                fixes.append("routine_add_faq")

        if codes & {"SEO_META_MISSING", "SEO_META_LENGTH", "SEO_META_NO_KEYWORD"}:
            if not meta_description:
                meta_description = self._generate_meta(article)
            if len(meta_description) > 165:
                meta_description = meta_description[:157] + "..."
            elif len(meta_description) < 130 and keyword and not _keyword_match(keyword, meta_description):
                meta_description = (meta_description + f" Learn about {keyword}.")[:160]
            if keyword and not _keyword_match(keyword, meta_description):
                meta_description = f"{keyword.title()}: {meta_description}"[:160]
            fixes.append("routine_meta")

        if "LINKS_INTERNAL_MISSING" in codes and published_articles:
            internal_links, _ = extract_links(content)
            if len(internal_links) < 3:
                injected = self._inject_internal_links(
                    content,
                    published_articles,
                    article,
                    existing_count=len(internal_links),
                )
                if injected != content:
                    content = injected
                    fixes.append("routine_internal_links")

        if codes & {"LINKS_EXTERNAL_MISSING", "SEO_EXTERNAL_LINKS_MISSING"}:
            _, external_links = extract_links(content)
            if len(external_links) < 2:
                injected = self._inject_external_links(
                    content, article, existing_count=len(external_links),
                )
                if injected != content:
                    content = injected
                    fixes.append("routine_external_links")

        if codes & {"FACT_UNSUPPORTED_CLAIM", "FACT_WEAK_CLAIM", "FACT_CITATION_MISSING"}:
            updated, citation_fixes = self._inject_claim_citations(content, article)
            if updated != content:
                content = updated
            if citation_fixes:
                fixes.append("routine_claim_citations")

        if codes & {"MEDIA_IMAGE_MISSING", "MEDIA_ALT_TEXT_WEAK"}:
            before = content
            content = self._inject_image_placeholders(content, article)
            if content != before:
                fixes.append("routine_image_placeholders")

        if "SCHEMA_ARTICLE_MISSING" in codes and '"@type"' not in content:
            json_ld = self._generate_article_json_ld(content, article)
            if json_ld:
                content += "\n\n" + json_ld
                fixes.append("routine_article_schema")

        if "SCHEMA_FAQ_MISSING" in codes and detect_faq_section(content) and "FAQPage" not in content:
            faq_json_ld = self._generate_faq_json_ld(content)
            if faq_json_ld:
                content += "\n\n" + faq_json_ld
                fixes.append("routine_faq_schema")

        return content, meta_description, fixes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_friction_points(article) -> list[str]:
        """Extract friction points from the content brief for variable injection.

        Friction points are insider insights that adjusters use against
        policyholders. Injecting these into section prompts forces the
        model to include specific, non-generic knowledge.

        Parses bullet points from the brief, or generates generic friction
        points based on the article's category and keyword.
        """
        brief = article.content_brief or ""
        keyword = article.target_keyword or ""
        category = article.content_category or ""

        # Try to extract bullet points from the AI-generated brief
        points = []
        for line in brief.split("\n"):
            line = line.strip()
            if line.startswith("- ") and len(line) > 20:
                points.append(line[2:])
            elif line.startswith("* ") and len(line) > 20:
                points.append(line[2:])

        if points:
            return points[:5]

        # Fallback: category-specific friction points
        category_friction: dict[str, list[str]] = {
            "problem_aware": [
                f"Adjusters often use automated valuation tools that systematically undervalue vehicles by 10-20% for '{keyword}' cases.",
                "Insurance companies know most people accept the first offer. Only 5-10% of policyholders ever push back.",
            ],
            "solution_aware": [
                f"When dealing with '{keyword}', adjusters have internal authority to increase offers by 10-15% without supervisor approval — they just won't volunteer this.",
                "Most policyholders don't know they can invoke the appraisal clause — a binding process that takes the decision out of the adjuster's hands entirely.",
            ],
            "state_specific": [
                f"State regulations on '{keyword}' often have specific deadlines that insurance companies hope you'll miss.",
                "Filing a DOI complaint triggers an automatic review — adjusters know this and often settle quickly once they see the complaint number.",
            ],
            "vehicle_specific": [
                f"For '{keyword}', insurers often exclude aftermarket modifications, low-mileage bonuses, and regional price variations from their valuations.",
                "CCC ONE valuations (used by most insurers) are known to pull comparables from different markets to lower your vehicle's value.",
            ],
        }

        return category_friction.get(category, [
            f"Adjusters processing '{keyword}' claims often cite 'company policy' to deny legitimate line items — but company policy is not law.",
            "Most total loss settlements are initially 15-25% below fair market value. The insurance company is counting on you not knowing this.",
        ])

    def _try_revision(self) -> int | None:
        """Try to pick up an article in revision status."""
        articles = self.db.query_articles(
            status=ArticleStatus.REVISION.value, limit=10
        )
        for article in articles:
            if article.revision_count >= self.config.pipeline.max_revision_rounds:
                self.db.update_article(
                    article.id,
                    status=ArticleStatus.REJECTED.value,
                    revision_notes=(
                        article.revision_notes
                        + "\n[REJECTED: Max revision rounds exceeded]"
                    ),
                )
                continue

            claim_id = self.generate_claim_id()
            if self.db.try_claim(
                article.id, "writer_claim", claim_id,
                ArticleStatus.IN_PROGRESS.value,
            ):
                return article.id
        return None

    def _format_internal_links(self, published: list) -> str:
        """Format published articles as internal linking options."""
        if not published:
            return ""
        lines = ["Available articles for internal linking:"]
        for a in published[:20]:
            if a.published_url:
                url = a.published_url
            else:
                info = self._product_info(a)
                url = f"{info['site_url'].rstrip('/')}/blog/{a.slug}"
            lines.append(f"- [{a.title}]({url}) — keyword: {a.target_keyword}")
        return "\n".join(lines)

    def _extract_lessons(self) -> str:
        """Load lessons from the feedback_lessons DB table."""
        lessons = self.get_lessons_for_me()
        if not lessons:
            return ""

        by_category: dict[str, list] = defaultdict(list)
        for lesson in lessons:
            by_category[lesson.category].append(lesson)

        lines = []

        rubric_cats = [
            "seo", "readability", "factual_accuracy", "cta",
            "legal_compliance", "word_count", "internal_links", "plagiarism",
        ]
        for cat in rubric_cats:
            cat_lessons = by_category.get(cat, [])
            if not cat_lessons:
                continue
            lines.append(f"  {cat.upper()} — common issues:")
            for lesson in sorted(cat_lessons, key=lambda x: -x.occurrences)[:3]:
                lines.append(f"    - ({lesson.occurrences}x) {lesson.lesson}")

        perf = by_category.get("performance", [])
        if perf:
            lines.append("")
            lines.append("  PERFORMANCE DATA (from real search traffic):")
            for lesson in perf[:3]:
                lines.append(f"    - {lesson.lesson}")

        successes = by_category.get("success_pattern", [])
        if successes:
            lines.append("")
            lines.append("  PATTERNS FROM FIRST-DRAFT APPROVALS:")
            for lesson in successes[:3]:
                lines.append(f"    - ({lesson.occurrences}x) {lesson.lesson}")

        return "\n".join(lines) if lines else ""

    # Metadata markers the LLM might emit.  Used by _parse_result to strip
    # leaked tags from the article body.
    _META_MARKERS = re.compile(
        r"(?:^|\s+)(?:META_DESCRIPTION|META_TITLE|TITLE|SLUG|KEYWORD|CATEGORY|TARGET_STATE)\s*:\s*[^\n]*",
        re.IGNORECASE,
    )

    @staticmethod
    def _parse_result(result: str) -> tuple[str, str]:
        """Parse Claude's output into content and meta description.

        Also strips any metadata markers that leaked into the article body
        (e.g. ``META_DESCRIPTION:`` appearing mid-sentence instead of on
        its own line).
        """
        meta_description = ""
        content_lines: list[str] = []
        for line in result.split("\n"):
            stripped = line.strip()
            if stripped.upper().startswith("META_DESCRIPTION:"):
                meta_description = stripped.split(":", 1)[1].strip()
            elif stripped.upper().startswith("META_TITLE:"):
                # Discard — we generate meta_title separately
                pass
            else:
                content_lines.append(line)
        content = "\n".join(content_lines).strip()

        # Second pass: catch metadata markers that appeared mid-sentence
        content = QuillAgent._META_MARKERS.sub("", content)
        # Clean up leftover whitespace artifacts
        content = re.sub(r"[ \t]+\n", "\n", content)   # trailing spaces
        content = re.sub(r"\n{3,}", "\n\n", content)    # excessive blank lines

        return content, meta_description

    @staticmethod
    def _generate_slug(title: str) -> str:
        """Generate a URL slug from title."""
        slug = title.lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[\s]+", "-", slug.strip())
        slug = re.sub(r"-+", "-", slug)
        return slug[:80]
