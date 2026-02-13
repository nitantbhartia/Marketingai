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
from typing import Any

from pipeline.agents.base import BaseAgent, RateLimitError
from pipeline.db import ArticleStatus
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

**Strict word limit: 1200-1500 words. Never exceed 1500.**

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

6. **FAQ** (~250 words, 3-4 questions for schema markup, use ### for each question)
   → **FINAL CTA**: End the last FAQ answer with a natural ClaimCoach link.

**Total: ~1200-1500 words, 4 CTAs. Reader hits the first one before they've scrolled twice.**

### CTA Rules
- 4 CTAs per article (1 early, 2 contextual, 1 closing)
- NEVER use salesy banner language. CTAs must feel like helpful suggestions.
- Always link to https://claimcoach.app — never make up feature-specific URLs
- Vary the CTA copy. Don't repeat the same line 4 times.
- Good: "Want to see which ones apply to your offer? [ClaimCoach checks this automatically.](https://claimcoach.app)"
- Bad: "Click here to try ClaimCoach!" (too pushy)

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


class QuillAgent(BaseAgent):
    name = "quill"
    claim_field = "writer_claim"

    # ------------------------------------------------------------------
    # Main entry point — three-phase pipeline
    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        """Three-phase writing pipeline: Outline → Draft → Self-Review.

        Processes up to ``max_articles_per_run`` articles per invocation so a
        single scheduled run can produce multiple pieces of content.
        """
        max_per_run = getattr(self.config.pipeline, "max_articles_per_run", 3)
        results: list[dict[str, Any]] = []

        for _ in range(max_per_run):
            result = self._write_one()
            if result["status"] == "idle":
                break  # nothing left to write
            results.append(result)
            if result["status"] in ("error", "rate_limited"):
                break  # stop on error or rate limit to avoid burning quota

        if not results:
            logger.info("No articles available to write")
            return {"status": "idle", "reason": "no_articles"}

        if len(results) == 1:
            return results[0]

        return {
            "status": "batch_complete",
            "articles_written": len([r for r in results if r["status"] == "success"]),
            "results": results,
        }

    # ------------------------------------------------------------------
    # Single-article pipeline (called in a loop by run())
    # ------------------------------------------------------------------
    def _write_one(self) -> dict[str, Any]:
        """Write or revise a single article through the 3-phase pipeline."""
        # Try "revision" first (re-writes take priority)
        article_id = self._try_revision()
        is_revision = article_id is not None

        # Then try fresh "todo" articles
        if article_id is None:
            article_id = self.pick_and_claim(
                from_status=ArticleStatus.TODO.value,
                to_status=ArticleStatus.IN_PROGRESS.value,
            )

        if article_id is None:
            return {"status": "idle", "reason": "no_articles"}

        article = self.db.get_article(article_id)
        if article is None:
            self.db.update_article(article_id, writer_claim="")
            return {"status": "error", "reason": "article_not_found"}

        logger.info(f"Writing article: {article.target_keyword} (revision={is_revision})")

        try:
            return self._write_one_inner(article, article_id, is_revision)
        except RateLimitError as e:
            logger.warning(f"Rate limited during pre-draft of article {article_id}: {e}")
            self.db.update_article(article_id, writer_claim="")
            self.db.record_metric("rate_limit", 0, json.dumps({
                "agent": "quill", "article_id": article_id, "phase": "pre_draft",
            }))
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error writing article {article_id}: {e}", exc_info=True)
            rollback_status = (
                ArticleStatus.REVISION.value if is_revision
                else ArticleStatus.TODO.value
            )
            self.db.update_article(
                article_id, status=rollback_status, writer_claim=""
            )
            return {"status": "error", "article_id": article_id, "reason": str(e)}

    def _write_one_inner(self, article, article_id: int, is_revision: bool) -> dict[str, Any]:
        """Inner write logic — caller guarantees claim release on exception."""
        # Load context documents
        product_context = self.config.load_product_context()
        state_rules = self.config.load_state_rules()
        published = self.db.get_published_articles()
        internal_links_context = self._format_internal_links(published)
        lessons = self._extract_lessons()

        # ── Revision path: targeted fix instead of full rewrite ──
        if is_revision and article.markdown_content:
            try:
                result = self._targeted_revision(
                    article, product_context, state_rules, internal_links_context, lessons,
                    published_articles=published,
                )
            except RateLimitError as e:
                logger.warning(f"Rate limited during targeted revision of article {article_id}: {e}")
                self.db.update_article(article_id, writer_claim="")
                self.db.record_metric("rate_limit", 0, json.dumps({
                    "agent": "quill", "article_id": article_id, "phase": "targeted_revision",
                }))
                return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
            if result:
                return result
            # Fall through to full rewrite if targeted revision fails

        # ── Phase 0: Entity mapping (E-E-A-T knowledge graph) ──
        entity_map = self._extract_entities(article)

        # ── Phase 0b: SERP analysis (real-time search intelligence) ──
        serp_context = self._get_serp_context(article)

        # ── Phase 0c: Vehicle data enrichment (NHTSA, if applicable) ──
        nhtsa_context = self._get_nhtsa_context(article)

        # ── Phase 1: Generate outline ──
        outline = self._generate_outline(
            article, product_context, state_rules, internal_links_context,
            entity_map=entity_map,
            revision_feedback=article.revision_notes if is_revision else "",
            serp_context=serp_context,
            nhtsa_context=nhtsa_context,
        )

        # ── Phase 2: Write article (section-by-section from outline) ──
        try:
            result_text = self._draft_from_outline(
                article, outline, product_context, state_rules,
                internal_links_context, is_revision, lessons,
            )
        except RateLimitError as e:
            # Rate limit — release claim but DON'T change article status.
            # The article stays in its current status (IN_PROGRESS) and will
            # be picked up again on the next scheduler run once the rate
            # limit window resets.
            logger.warning(f"Rate limited during draft of article {article_id}: {e}")
            self.db.update_article(article_id, writer_claim="")
            self.db.record_metric("rate_limit", 0, json.dumps({
                "agent": "quill", "article_id": article_id, "phase": "draft",
            }))
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
        except Exception as e:
            logger.error(f"Draft error: {e}")
            rollback_status = (
                ArticleStatus.REVISION.value if is_revision
                else ArticleStatus.TODO.value
            )
            self.db.update_article(
                article_id, status=rollback_status, writer_claim=""
            )
            return {"status": "error", "reason": str(e)}

        try:
            content, meta_description = self._parse_result(result_text)
            title = article.suggested_title or article.title or article.target_keyword.title()

            # ── Phase 2.5: Contrastive critique (anti-AI-laziness) ──
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
                rollback_status = (
                    ArticleStatus.REVISION.value if is_revision
                    else ArticleStatus.TODO.value
                )
                self.db.update_article(
                    article_id,
                    title=title,
                    markdown_content=content,
                    meta_description=meta_description,
                    slug=slug,
                    word_count=wc,
                    status=rollback_status,
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
            self.db.update_article(article_id, writer_claim="")
            self.db.record_metric("rate_limit", 0, json.dumps({
                "agent": "quill", "article_id": article_id, "phase": "post_draft",
            }))
            return {"status": "rate_limited", "article_id": article_id, "reason": str(e)}
        except Exception as e:
            logger.error(f"Post-draft error for article {article_id}: {e}", exc_info=True)
            rollback_status = (
                ArticleStatus.REVISION.value if is_revision
                else ArticleStatus.TODO.value
            )
            self.db.update_article(
                article_id, status=rollback_status, writer_claim=""
            )
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

    # ------------------------------------------------------------------
    # Phase 1: Outline generation
    # ------------------------------------------------------------------
    def _generate_outline(
        self, article, product_context: str, state_rules: str,
        internal_links: str, entity_map: str = "",
        revision_feedback: str = "",
        serp_context: str = "",
        nhtsa_context: str = "",
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

        prompt = f"""Create a detailed OUTLINE for an article targeting: "{article.target_keyword}"

Content category: {article.content_category or 'general'}
{f'Target state: {article.target_state}' if article.target_state else ''}

{f'Content brief: {article.content_brief}' if article.content_brief else ''}

{f'Category strategy: {category_hint}' if category_hint else ''}

{f'Internal links available: {internal_links}' if internal_links else ''}
{entity_section}{serp_section}{nhtsa_section}{revision_section}
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

Be specific about dollar amounts, timelines, and examples to include.
Use the entity map terms naturally throughout — these signal expertise to search engines.
Plan at least 2-3 image placements and ensure every section has a visual break.
Format as a clean outline with ## headers and bullet points."""

        try:
            outline = self.call_claude(
                prompt=prompt,
                system="You are a content strategist creating detailed article outlines.",
                model=self.strategy_model,
                max_tokens=1500,
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
            )
            return self.call_claude(
                prompt=prompt,
                system=self._build_system_prompt(article.content_category),
                model=self.fast_model,
                max_tokens=8192,
                temperature=0.85,  # Human-feel temperature for creative writing
            )

        # ── Section-by-section drafting ──
        system = self._build_system_prompt(article.content_category)
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
        if is_revision and article.revision_notes:
            context_block += (
                f"=== REVISION FEEDBACK (address these issues) ===\n"
                f"{article.revision_notes[-2000:]}\n\n"
            )

        # Extract friction points from the content brief for variable injection
        friction_points = self._extract_friction_points(article)

        drafted_sections: list[str] = []
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
                section_prompt += (
                    f"\n=== PREVIOUSLY WRITTEN (for continuity) ===\n"
                    f"{drafted_sections[-1][-500:]}\n\n"
                )

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
            )
            # Alternate sections get image placeholders (target 2-3 total)
            if i % 2 == 1 and not is_last:
                section_prompt += (
                    f"- Include ONE image placeholder: ![Descriptive alt text about "
                    f"{keyword}](image:relevant-slug). Alt text must be 10+ words.\n"
                )

            section_text = self.call_claude(
                prompt=section_prompt,
                system=system,
                model=self.fast_model,
                max_tokens=1500,
                temperature=0.85,  # Human-feel temperature for creative writing
            )
            drafted_sections.append(section_text.strip())
            logger.debug(
                f"Drafted section {i + 1}/{len(sections)}: {heading} "
                f"({len(section_text)} chars)"
            )

        return "\n\n".join(drafted_sections)

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
    def _build_system_prompt(self, content_category: str) -> str:
        """Build category-aware system prompt."""
        base = SYSTEM_PROMPT
        guidance = CATEGORY_GUIDANCE.get(content_category or "", "")
        if guidance:
            base += "\n" + guidance
        return base

    def _build_prompt(
        self,
        article,
        product_context: str,
        state_rules: str,
        internal_links: str,
        is_revision: bool,
        lessons: str = "",
        outline: str = "",
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

        parts.append(
            "\nRemember: End the article with a clear CTA pointing to ClaimCoach "
            "(claimcoach.app). After the article, output:\n"
            "META_DESCRIPTION: <150-160 character meta description>"
        )

        return "\n\n".join(parts)

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
                system=self._build_system_prompt(article.content_category),
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

        # Check 1: Keyword in first 100 words (fuzzy match — allows stop words)
        if keyword_lower and not _keyword_match(keyword, content[:500]):
            # Insert keyword into the first paragraph naturally
            paragraphs = content.split("\n\n", 1)
            if paragraphs:
                first_para = paragraphs[0]
                # Find the first sentence end to insert after
                sent_end = re.search(r"[.!?]\s", first_para)
                if sent_end:
                    insert_pos = sent_end.end()
                    insertion = f"When it comes to {keyword}, knowledge is your best weapon. "
                    first_para = (
                        first_para[:insert_pos] + insertion + first_para[insert_pos:]
                    )
                    content = first_para + (
                        "\n\n" + paragraphs[1] if len(paragraphs) > 1 else ""
                    )
                    fixes.append("inserted_keyword_first_100_words")

        # Check 2: CTA placement — 4 CTAs (1 early, 2 contextual, 1 closing)
        content_lower = content.lower()
        cta_count = len(re.findall(r"claimcoach\.app", content_lower))

        if cta_count < 4:
            content, cta_fixes = self._ensure_four_ctas(content, keyword)
            fixes.extend(cta_fixes)

        # Check 3: FAQ section present
        if not detect_faq_section(content):
            faq_block = self._generate_faq_block(article)
            if faq_block:
                # Insert before the last section (which should be the CTA)
                cta_marker = re.search(
                    r"\n##\s.*(?:Next Step|Get Started|Take Action|ClaimCoach)",
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

        # Check 10: Freshness — replace stale year references with current year.
        # Sage SEO deducts points for stale content; this auto-fixes the
        # unambiguous cases ("as of 2023" → "as of {current_year}").
        content, freshness_fixes = auto_fix_stale_years(content)
        if freshness_fixes > 0:
            fixes.append(f"fixed_{freshness_fixes}_stale_year_references")

        if fixes:
            logger.info(f"Self-review applied {len(fixes)} fixes: {fixes}")

        return content, meta_description, fixes

    @staticmethod
    def _ensure_four_ctas(content: str, keyword: str) -> tuple[str, list[str]]:
        """Ensure the article has 4 strategically placed CTAs.

        Strategy:
        1. Early CTA — within first 300 words (after emotional hook/problem)
        2. Contextual CTA — after line-items/mid-body section
        3. Contextual CTA — after state-rules/second-body section
        4. Closing CTA — in or after FAQ section

        Returns (modified_content, list_of_fixes).
        """
        fixes: list[str] = []
        content_lower = content.lower()

        # Count existing CTA links
        cta_positions = [
            m.start() for m in re.finditer(r"claimcoach\.app", content_lower)
        ]

        # Split content into words for position tracking
        words = content.split()
        total_words = len(words)

        # CTA copy variants (varied, not repetitive)
        cta_variants = [
            (
                "\n\n> See what's missing from your offer in 5 minutes — "
                "[check your settlement free](https://claimcoach.app).\n"
            ),
            (
                "\n\n[ClaimCoach checks all of these line items automatically]"
                "(https://claimcoach.app) — upload your offer and see what "
                "they left out.\n"
            ),
            (
                "\n\nGet your state-specific settlement analysis at "
                "[ClaimCoach](https://claimcoach.app) — it takes 5 minutes.\n"
            ),
            (
                "\n\nDon't leave money on the table. "
                "[ClaimCoach](https://claimcoach.app) analyzes your settlement "
                "and shows you exactly where the insurer shortchanged you.\n"
            ),
        ]

        # If no ClaimCoach mention at all, treat all zones as missing
        if "claimcoach" not in content_lower:
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
        late_start = int(total_chars * 0.55)
        late_end = int(total_chars * 0.80)
        closing_start = int(total_chars * 0.80)

        has_early = any(p < early_end for p in cta_positions)
        has_mid = any(mid_start <= p <= mid_end for p in cta_positions)
        has_late = any(late_start <= p <= late_end for p in cta_positions)
        has_closing = any(p >= closing_start for p in cta_positions)

        needed: list[tuple[int, str]] = []  # (variant_idx, zone)
        if not has_early:
            needed.append((0, "early"))
        if not has_mid:
            needed.append((1, "mid"))
        if not has_late:
            needed.append((2, "late"))
        if not has_closing:
            needed.append((3, "closing"))

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

            elif zone == "late":
                # After the state-rules section (around 60-75%)
                if len(h2_positions) >= 5:
                    insert_pos = h2_positions[4]
                else:
                    insert_pos = int(total_chars * 0.65)

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

    @staticmethod
    def _inject_internal_links(
        content: str, published: list, existing_count: int = 0,
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
            url = article.published_url or f"https://claimcoach.app/blog/{article.slug}"
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
                url = article.published_url or f"https://claimcoach.app/blog/{article.slug}"
                if url:
                    related.append(f"- [{article.title}]({url})")
            if related:
                # Insert before the last ## section (CTA)
                cta_match = re.search(
                    r"\n##\s.*(?:Next Step|Get Started|Take Action|ClaimCoach)",
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

    @staticmethod
    def _generate_article_json_ld(content: str, article) -> str:
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
                "name": "ClaimCoach",
                "url": "https://claimcoach.app",
            },
            "publisher": {
                "@type": "Organization",
                "name": "ClaimCoach",
                "url": "https://claimcoach.app",
                "logo": {
                    "@type": "ImageObject",
                    "url": "https://claimcoach.app/logo.png",
                },
            },
        }

        # Add article image if placeholders exist
        images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
        if images:
            article_schema["image"] = [
                f"https://claimcoach.app/images/{url.replace('image:', '')}.webp"
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
        default_alt = f"Infographic explaining key factors in {keyword}"

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
        try:
            result = self.call_claude(
                prompt=(
                    f'Write a meta description (150-160 chars) for an article about '
                    f'"{keyword}" for ClaimCoach. Include the keyword, an emotional '
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
    MAX_WORD_COUNT = 1500
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
        issues = self._parse_revision_issues(notes)
        if not issues:
            return None

        # Classify issues as targeted-fixable or needs-full-rewrite
        targeted = []
        broad = []
        for issue in issues:
            issue_lower = issue.lower()
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
                "first 300 words", "need 4 cta", "target: 4",
            )):
                targeted.append(issue)
            else:
                broad.append(issue)

        # If most issues are broad, do a full rewrite
        if len(broad) > len(targeted):
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

        # Apply targeted fixes
        content, meta, fixes = self._self_review_and_fix(
            content, meta, article, published_articles=published_articles,
        )

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
                    system=self._build_system_prompt(article.content_category),
                    model=self.default_model,
                    max_tokens=8192,
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
    def _parse_revision_issues(notes: str) -> list[str]:
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
        for line in notes.split("\n"):
            stripped = line.strip()
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
            url = a.published_url or f"https://claimcoach.app/blog/{a.slug}"
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
