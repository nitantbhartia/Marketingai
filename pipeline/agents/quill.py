"""Quill agent — content writer.

Picks articles from the queue, writes SEO-optimized long-form content
using Claude, and submits for review.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus
from pipeline.utils.readability import word_count
from pipeline.utils.seo import generate_slug

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are writing content for ClaimCoach, an AI-powered tool that helps car owners
fight lowball insurance settlement offers.

VOICE & TONE:
- Authoritative but empathetic — you understand how stressful this is
- Adversarial toward insurance companies, NOT toward the reader
- Use "you" and "your" — speak directly to someone going through this
- Never condescending. These are smart people in an unfamiliar situation.
- Avoid jargon unless defining it. If you use a term like "diminished value," explain it.

STRUCTURE:
- 1800–2200 words
- Keyword in first 100 words
- H2 headers every 200–300 words
- At least one actionable takeaway per section
- FAQ section with 3–5 questions (schema markup compatible)
- Include specific dollar amounts and ranges where possible
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


class QuillAgent(BaseAgent):
    name = "quill"
    claim_field = "writer_claim"

    def run(self) -> dict[str, Any]:
        """Pick an article and write it."""
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

        logger.info(f"Writing article: {article.keyword} (revision={is_revision})")

        # Load context documents
        product_context = self.config.load_product_context()
        state_rules = self.config.load_state_rules()

        # Get published articles for internal linking
        published = self.db.get_published_articles()
        internal_links_context = self._format_internal_links(published)

        # Build the writing prompt
        prompt = self._build_prompt(
            article, product_context, state_rules, internal_links_context, is_revision
        )

        # Call Claude to write the article
        try:
            result = self.call_claude(
                prompt=prompt,
                system=SYSTEM_PROMPT,
                model=self.default_model,
                max_tokens=8192,
            )
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            self.db.update_article(article_id, status=ArticleStatus.TODO.value, writer_claim="")
            return {"status": "error", "reason": str(e)}

        # Parse the result
        content, meta_description = self._parse_result(result)
        title = article.suggested_title or article.title or article.keyword.title()
        slug = generate_slug(title)
        wc = word_count(content)

        # Update the article
        self.db.update_article(
            article_id,
            title=title,
            content=content,
            meta_description=meta_description,
            slug=slug,
            word_count=wc,
            status=ArticleStatus.REVIEW.value,
        )

        self.db.record_metric("quill_write", wc, json.dumps({
            "article_id": article_id,
            "keyword": article.keyword,
            "is_revision": is_revision,
            "word_count": wc,
        }))

        logger.info(f"Wrote article {article_id}: {title} ({wc} words)")
        return {
            "status": "success",
            "article_id": article_id,
            "title": title,
            "word_count": wc,
            "is_revision": is_revision,
        }

    def _try_revision(self) -> str | None:
        """Try to pick up an article in revision status."""
        articles = self.db.query_articles(status=ArticleStatus.REVISION.value, limit=10)
        for article in articles:
            if article.revision_count >= self.config.pipeline.max_revision_rounds:
                # Too many revisions — reject
                self.db.update_article(
                    article.id,
                    status=ArticleStatus.REJECTED.value,
                    revision_notes=(
                        article.revision_notes + "\n[REJECTED: Max revision rounds exceeded]"
                    ),
                )
                continue

            claim_id = self.generate_claim_id()
            if self.db.try_claim(article.id, "writer_claim", claim_id, ArticleStatus.IN_PROGRESS.value):
                return article.id
        return None

    def _build_prompt(
        self,
        article,
        product_context: str,
        state_rules: str,
        internal_links: str,
        is_revision: bool,
    ) -> str:
        parts = []

        # Reference documents
        parts.append(f"=== PRODUCT CONTEXT (follow strictly) ===\n{product_context}\n")

        if article.target_state and state_rules:
            parts.append(f"=== STATE RULES ===\n{state_rules}\n")

        if internal_links:
            parts.append(f"=== PUBLISHED ARTICLES (link to these) ===\n{internal_links}\n")

        # Writing task
        if is_revision:
            parts.append(f"=== REVISION TASK ===")
            parts.append(f"Revise the following article based on reviewer feedback.")
            parts.append(f"Keyword: {article.keyword}")
            parts.append(f"Revision notes:\n{article.revision_notes}")
            parts.append(f"\nOriginal article:\n{article.content}")
        else:
            parts.append(f"=== WRITING TASK ===")
            parts.append(f"Write a new article.")
            parts.append(f"Target keyword: {article.keyword}")
            if article.content_brief:
                parts.append(f"Content brief:\n{article.content_brief}")
            if article.target_state:
                parts.append(f"Target state: {article.target_state}")
            if article.content_category:
                parts.append(f"Content category: {article.content_category}")
            if article.suggested_title:
                parts.append(f"Suggested title (you can adjust): {article.suggested_title}")

        parts.append(
            "\nRemember: End the article with a clear CTA pointing to ClaimCoach "
            "(claimcoach.app). After the article, output:\n"
            "META_DESCRIPTION: <150-160 character meta description>"
        )

        return "\n\n".join(parts)

    def _format_internal_links(self, published: list) -> str:
        """Format published articles as internal linking options."""
        if not published:
            return ""
        lines = ["Available articles for internal linking:"]
        for a in published[:20]:  # Cap at 20
            url = a.published_url or f"https://claimcoach.app/blog/{a.slug}"
            lines.append(f"- [{a.title}]({url}) — keyword: {a.keyword}")
        return "\n".join(lines)

    def _parse_result(self, result: str) -> tuple[str, str]:
        """Parse Claude's output into content and meta description."""
        meta_description = ""
        content = result

        # Extract META_DESCRIPTION line
        lines = result.split("\n")
        content_lines = []
        for line in lines:
            if line.strip().startswith("META_DESCRIPTION:"):
                meta_description = line.split("META_DESCRIPTION:", 1)[1].strip()
            else:
                content_lines.append(line)

        content = "\n".join(content_lines).strip()
        return content, meta_description
