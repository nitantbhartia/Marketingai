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

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus
from pipeline.utils.readability import readability_report, word_count
from pipeline.utils.seo import detect_faq_section, extract_links

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — the core writing voice
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are writing content for ClaimCoach, an AI-powered tool that helps car owners
fight lowball insurance settlement offers.

VOICE & TONE:
- Authoritative but empathetic — you understand how stressful this is
- Adversarial toward insurance companies, NOT toward the reader
- Use "you" and "your" — speak directly to someone going through this
- Never condescending. These are smart people in an unfamiliar situation.
- Avoid jargon unless defining it. If you use a term like "diminished value," explain it.
- Write like a knowledgeable friend, not a textbook.

STRUCTURE:
- 1800–2200 words
- Keyword in first 100 words — naturally, not forced
- H2 headers every 200–300 words
- At least one actionable takeaway per section
- FAQ section with 3–5 questions (use ### for each question for schema markup)
- Include specific dollar amounts, ranges, and real data where possible
- End with clear CTA pointing to ClaimCoach

SEO REQUIREMENTS:
- Target keyword in: title, first paragraph, at least 2 H2s, meta description
- 2–3 external links to authoritative sources (state DOI websites, NAIC, etc.)
- Meta description: 150–160 chars, includes keyword and emotional hook

READABILITY:
- Flesch-Kincaid: 60+ (8th grade level)
- Sentences: max 25 words average
- Paragraphs: max 4 sentences
- Use short words. "Get" not "obtain." "Show" not "demonstrate."
- Mix sentence lengths — some punchy, some explanatory.

FORMAT:
- Output the article in Markdown format
- Use ## for H2 headers, ### for H3
- Use [link text](URL) for links
- At the very end, output a line: META_DESCRIPTION: <your 150-160 char meta description>

CRITICAL RULES:
- Never claim ClaimCoach can negotiate on your behalf (it can't — legal risk)
- Never promise specific dollar amounts ClaimCoach will recover
- Never give legal advice. Say "consider consulting an attorney" for complex situations.
- Never claim features ClaimCoach doesn't have
"""

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
        """Three-phase writing pipeline: Outline → Draft → Self-Review."""
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
            logger.info("No articles available to write")
            return {"status": "idle", "reason": "no_articles"}

        article = self.db.get_article(article_id)
        if article is None:
            return {"status": "error", "reason": "article_not_found"}

        logger.info(f"Writing article: {article.target_keyword} (revision={is_revision})")

        # Load context documents
        product_context = self.config.load_product_context()
        state_rules = self.config.load_state_rules()
        published = self.db.get_published_articles()
        internal_links_context = self._format_internal_links(published)
        lessons = self._extract_lessons()

        # ── Revision path: targeted fix instead of full rewrite ──
        if is_revision and article.markdown_content:
            result = self._targeted_revision(
                article, product_context, state_rules, internal_links_context, lessons,
            )
            if result:
                return result
            # Fall through to full rewrite if targeted revision fails

        # ── Phase 1: Generate outline ──
        outline = self._generate_outline(
            article, product_context, state_rules, internal_links_context,
        )

        # ── Phase 2: Write full article from outline ──
        prompt = self._build_prompt(
            article, product_context, state_rules, internal_links_context,
            is_revision=is_revision, lessons=lessons, outline=outline,
        )

        try:
            result_text = self.call_claude(
                prompt=prompt,
                system=self._build_system_prompt(article.content_category),
                model=self.default_model,
                max_tokens=8192,
            )
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            self.db.update_article(
                article_id, status=ArticleStatus.TODO.value, writer_claim=""
            )
            return {"status": "error", "reason": str(e)}

        content, meta_description = self._parse_result(result_text)
        title = article.suggested_title or article.title or article.target_keyword.title()

        # ── Phase 3: Self-review & auto-fix ──
        content, meta_description, fixes = self._self_review_and_fix(
            content, meta_description, article,
        )

        slug = self._generate_slug(title)
        wc = word_count(content)

        # Save and submit to Sage
        self.db.update_article(
            article_id,
            title=title,
            markdown_content=content,
            meta_description=meta_description,
            slug=slug,
            word_count=wc,
            status=ArticleStatus.REVIEW.value,
        )

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
    # Phase 1: Outline generation
    # ------------------------------------------------------------------
    def _generate_outline(
        self, article, product_context: str, state_rules: str,
        internal_links: str,
    ) -> str:
        """Generate a structured outline before writing.

        Uses a shorter LLM call to plan the article structure, key points,
        data to cite, and internal links to use. This ensures the draft
        phase has a clear roadmap and doesn't miss critical sections.
        """
        category_hint = CATEGORY_GUIDANCE.get(
            article.content_category or "", ""
        ).strip()

        prompt = f"""Create a detailed OUTLINE for an article targeting: "{article.target_keyword}"

Content category: {article.content_category or 'general'}
{f'Target state: {article.target_state}' if article.target_state else ''}

{f'Content brief: {article.content_brief}' if article.content_brief else ''}

{f'Category strategy: {category_hint}' if category_hint else ''}

{f'Internal links available: {internal_links}' if internal_links else ''}

Create an outline with:
1. **Hook** (first 100 words) — how to open with the keyword naturally
2. **5-7 H2 sections** — each with:
   - The H2 header text (include keyword in at least 2)
   - 3-4 key points to cover
   - Specific data/examples/dollar amounts to include
   - Which internal articles to link to (if relevant)
3. **FAQ section** — 3-5 questions with brief answer notes
4. **CTA section** — how to close with ClaimCoach
5. **External sources** — 2-3 authoritative sites to reference

Be specific about dollar amounts, timelines, and examples to include.
Format as a clean outline with ## headers and bullet points."""

        try:
            outline = self.call_claude(
                prompt=prompt,
                system="You are a content strategist creating detailed article outlines.",
                model=self.default_model,
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
    # Phase 3: Self-review & auto-fix
    # ------------------------------------------------------------------
    def _self_review_and_fix(
        self, content: str, meta_description: str, article,
    ) -> tuple[str, str, list[str]]:
        """Run deterministic checks and fix what we can before Sage sees it.

        Returns (fixed_content, fixed_meta, list_of_fixes_applied).
        """
        fixes: list[str] = []
        keyword = article.target_keyword or ""
        keyword_lower = keyword.lower()

        # Check 1: Keyword in first 100 words
        first_500 = content[:500].lower()
        if keyword_lower and keyword_lower not in first_500:
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

        # Check 2: CTA with ClaimCoach mention
        content_lower = content.lower()
        if "claimcoach" not in content_lower:
            content += (
                "\n\n## Take the Next Step\n\n"
                "Don't leave money on the table. "
                "[ClaimCoach](https://claimcoach.app) analyzes your total loss "
                "settlement and shows you exactly where the insurance company "
                "is shortchanging you — so you can fight back with real data."
            )
            fixes.append("added_claimcoach_cta")
        elif "claimcoach.app" not in content_lower:
            # ClaimCoach mentioned but no link — add link to the last mention
            content = re.sub(
                r"(?i)(ClaimCoach)(?![\w.])",
                r"[\1](https://claimcoach.app)",
                content,
                count=1,
            )
            fixes.append("added_claimcoach_link")

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
            if keyword_lower and keyword_lower not in meta_description.lower():
                meta_description += f" Learn about {keyword}."
            fixes.append("extended_meta_description")

        # Check 5: Keyword in meta description
        if keyword_lower and keyword_lower not in meta_description.lower():
            # Prepend keyword context
            meta_description = (
                f"{keyword.title()}: {meta_description}"
            )[:160]
            fixes.append("added_keyword_to_meta")

        if fixes:
            logger.info(f"Self-review applied {len(fixes)} fixes: {fixes}")

        return content, meta_description, fixes

    def _generate_faq_block(self, article) -> str:
        """Generate a quick FAQ section using the LLM."""
        try:
            result = self.call_claude(
                prompt=(
                    f'Generate an FAQ section for an article about "{article.target_keyword}". '
                    f"Write 3-4 questions and concise answers (2-3 sentences each). "
                    f"Format as:\n## Frequently Asked Questions\n\n"
                    f"### Question here?\n\nAnswer here.\n\n"
                    f"Make questions real things people search for."
                ),
                model=self.default_model,
                max_tokens=800,
            )
            return result.strip()
        except Exception as e:
            logger.warning(f"FAQ generation failed: {e}")
            return ""

    def _generate_meta(self, article) -> str:
        """Generate a meta description."""
        keyword = article.target_keyword or ""
        # Simple template fallback — no LLM needed
        return (
            f"Learn how to handle {keyword.lower()}. "
            f"Step-by-step guide with specific strategies to fight back "
            f"and get the settlement you deserve."
        )[:160]

    # ------------------------------------------------------------------
    # Targeted revision (instead of full rewrite)
    # ------------------------------------------------------------------
    def _targeted_revision(
        self, article, product_context: str, state_rules: str,
        internal_links: str, lessons: str,
    ) -> dict[str, Any] | None:
        """Fix specific issues from Sage's feedback without rewriting everything.

        Parses the revision_notes to identify specific, fixable issues and
        makes targeted edits. Returns None if the issues are too broad for
        targeted fixes (falls through to full rewrite).
        """
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

        content = article.markdown_content
        meta = article.meta_description or ""

        # Apply targeted fixes
        content, meta, fixes = self._self_review_and_fix(content, meta, article)

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
            except Exception as e:
                logger.warning(f"Targeted LLM fix failed: {e}")
                return None  # Fall through to full rewrite

        slug = self._generate_slug(
            article.suggested_title or article.title or article.target_keyword.title()
        )
        wc = word_count(content)

        self.db.update_article(
            article.id,
            markdown_content=content,
            meta_description=meta,
            slug=slug,
            word_count=wc,
            status=ArticleStatus.REVIEW.value,
        )

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
        """Extract individual issues from Sage's revision notes."""
        issues = []
        for line in notes.split("\n"):
            line = line.strip()
            if line.startswith("- ") and not line.startswith("- **"):
                issue = line[2:].strip()
                if issue and len(issue) > 5:
                    issues.append(issue)
        return issues

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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

    @staticmethod
    def _parse_result(result: str) -> tuple[str, str]:
        """Parse Claude's output into content and meta description."""
        meta_description = ""
        content_lines = []
        for line in result.split("\n"):
            if line.strip().startswith("META_DESCRIPTION:"):
                meta_description = line.split("META_DESCRIPTION:", 1)[1].strip()
            else:
                content_lines.append(line)
        content = "\n".join(content_lines).strip()
        return content, meta_description

    @staticmethod
    def _generate_slug(title: str) -> str:
        """Generate a URL slug from title."""
        slug = title.lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[\s]+", "-", slug.strip())
        slug = re.sub(r"-+", "-", slug)
        return slug[:80]
