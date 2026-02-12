"""Scout agent — keyword research and topic discovery.

Discovers high-value topics for ClaimCoach content and populates
the backlog with scored, briefed entries ready for writing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus
from pipeline.utils.keywords import (
    discover_related_keywords,
    get_all_seed_topics,
    score_topic,
)

logger = logging.getLogger(__name__)


class ScoutAgent(BaseAgent):
    name = "scout"

    def run(self) -> dict[str, Any]:
        """Research topics and populate backlog."""
        existing_count = self.db.count_articles(ArticleStatus.BACKLOG.value)
        logger.info(f"Current backlog: {existing_count} topics")

        # Get existing keywords to avoid duplicates
        all_articles = self.db.query_articles(limit=5000)
        existing_keywords = {a.target_keyword.lower() for a in all_articles if a.target_keyword}

        # Phase 1: Seed topics (always available, no API needed)
        topics = get_all_seed_topics()
        new_count = 0
        skipped = 0

        for topic in topics:
            kw = topic["keyword"]
            if kw.lower() in existing_keywords:
                skipped += 1
                continue

            # Generate content brief
            brief = self._generate_brief(topic)

            self.db.create_article(
                title=topic.get("suggested_title", ""),
                status=ArticleStatus.BACKLOG.value,
                target_keyword=kw,
                search_volume=topic.get("volume", 0),
                keyword_difficulty=topic.get("difficulty", 0.0),
                commercial_intent=topic.get("intent", 0.0),
                content_brief=brief,
                target_state=topic.get("state", ""),
                content_category=topic.get("category", ""),
                suggested_title=self._suggest_title(kw, topic.get("category", "")),
            )
            existing_keywords.add(kw.lower())
            new_count += 1

        # Phase 2: Discover related keywords via autocomplete
        discovery_bases = [
            "total loss settlement",
            "insurance lowball offer",
            "car totaled what to do",
            "diminished value claim",
            "dispute insurance claim",
        ]

        discovered = 0
        for base in discovery_bases:
            related = discover_related_keywords(base)
            for kw in related:
                if kw.lower() in existing_keywords:
                    continue
                if not self._is_relevant(kw):
                    continue

                self.db.create_article(
                    status=ArticleStatus.BACKLOG.value,
                    target_keyword=kw,
                    search_volume=100,  # Estimated
                    keyword_difficulty=0.25,
                    commercial_intent=0.7,
                    content_brief=f"Write a comprehensive guide about: {kw}",
                    content_category="discovered",
                    suggested_title=self._suggest_title(kw, "discovered"),
                )
                existing_keywords.add(kw.lower())
                discovered += 1

        # Load lessons about which categories perform well
        performance_hints = self._load_performance_hints()

        # Phase 3: If LLM is available, generate briefs for top unbriefed topics.
        # Capped at 5 per run (each brief = 2 API calls, well within free-tier
        # 5 RPM when spaced by the global rate_limit_delay).
        unbriefed = self.db.query_articles(status=ArticleStatus.BACKLOG.value, limit=15)
        if performance_hints:
            # Prioritize articles in high-performing categories
            unbriefed = sorted(
                unbriefed,
                key=lambda a: a.content_category in performance_hints.get("preferred", []),
                reverse=True,
            )
        max_ai_briefs = 5
        briefed = 0
        if self.config.anthropic.api_key or self.config.gemini.api_key:
            for article in unbriefed:
                # Skip if already has a detailed AI-generated brief
                # (AI briefs are longer and don't start with generic phrases)
                if article.content_brief and len(article.content_brief) > 200 and not article.content_brief.startswith("Write a comprehensive"):
                    continue
                try:
                    brief = self._ai_generate_brief(article.target_keyword, article.content_category)
                    title = self._ai_suggest_title(article.target_keyword)
                    self.db.update_article(
                        article.id,
                        content_brief=brief,
                        title=title,
                    )
                    briefed += 1
                    if briefed >= max_ai_briefs:
                        break
                except Exception as e:
                    logger.warning(f"Failed to generate AI brief: {e}")

        # Phase 4: Promote backlog articles that have a real brief to "todo"
        # so Quill can pick them up.  Without this step articles sit in
        # backlog forever because Quill only queries the "todo" status.
        promoted = 0
        backlog = self.db.query_articles(
            status=ArticleStatus.BACKLOG.value, limit=50
        )
        for article in backlog:
            # Only promote articles with a substantive AI-generated brief
            brief = article.content_brief or ""
            if len(brief) > 100 and not brief.startswith("Write a comprehensive"):
                self.db.update_article(
                    article.id, status=ArticleStatus.TODO.value
                )
                promoted += 1

        # Record metrics
        final_backlog = self.db.count_articles(ArticleStatus.BACKLOG.value)
        self.db.record_metric("scout_run", new_count + discovered, json.dumps({
            "seed_added": new_count,
            "discovered": discovered,
            "skipped_duplicates": skipped,
            "ai_briefed": briefed,
            "promoted_to_todo": promoted,
            "final_backlog": final_backlog,
        }))

        summary = {
            "seed_topics_added": new_count,
            "discovered_topics": discovered,
            "skipped_duplicates": skipped,
            "ai_briefed": briefed,
            "promoted_to_todo": promoted,
            "final_backlog": final_backlog,
        }
        logger.info(f"Scout complete: {summary}")
        return summary

    def _generate_brief(self, topic: dict) -> str:
        """Generate a content brief from topic data."""
        category = topic.get("category", "")
        kw = topic["keyword"]

        briefs = {
            "problem_aware": (
                f"Write an empathetic, educational article targeting people who just "
                f"discovered their insurance settlement is too low. Focus on '{kw}'. "
                f"Validate their frustration, explain why this happens, and introduce "
                f"actionable steps they can take. End with ClaimCoach CTA."
            ),
            "solution_aware": (
                f"Write a detailed how-to guide for '{kw}'. Include step-by-step "
                f"instructions, specific examples, common mistakes to avoid, and "
                f"realistic timelines. Reference state-specific variations where relevant."
            ),
            "state_specific": (
                f"Write a comprehensive state guide about '{kw}'. Include the state's "
                f"total loss threshold, specific statutes, DOI contact info, and "
                f"step-by-step dispute process for that state."
            ),
            "vehicle_specific": (
                f"Write a guide about '{kw}'. Include typical value ranges, what "
                f"factors affect valuation, how to find comparable vehicles, and "
                f"common line items specific to this vehicle type."
            ),
            "line_item": (
                f"Write a deep-dive article on '{kw}'. Explain what this line item "
                f"is, why insurers often miss it, typical dollar amounts, and how "
                f"to ensure it's included in a settlement."
            ),
            "comparison": (
                f"Write a balanced comparison article about '{kw}'. Cover pros and "
                f"cons of each option, cost differences, when each is appropriate, "
                f"and help the reader make an informed decision."
            ),
            "emotional": (
                f"Write a story-driven article about '{kw}'. Use relatable scenarios, "
                f"validate the reader's frustration, share what others have experienced, "
                f"and provide practical next steps."
            ),
        }
        return briefs.get(category, f"Write a comprehensive guide about: {kw}")

    def _suggest_title(self, keyword: str, category: str) -> str:
        """Generate a suggested article title."""
        kw_title = keyword.title()
        templates = {
            "problem_aware": f"{kw_title}: What to Do When Your Settlement Is Too Low",
            "solution_aware": f"How to {kw_title}: A Step-by-Step Guide",
            "state_specific": f"{kw_title}: Your Complete Guide",
            "vehicle_specific": f"{kw_title}: What You Should Know",
            "line_item": f"{kw_title}: The Line Item Most People Miss",
            "comparison": f"{kw_title}: Which Option Is Right for You?",
            "emotional": f"{kw_title}: Real Stories and What You Can Do",
        }
        return templates.get(category, f"{kw_title}: What You Need to Know")

    def _is_relevant(self, keyword: str) -> bool:
        """Check if a discovered keyword is relevant to ClaimCoach."""
        relevant_terms = [
            "total loss", "settlement", "insurance", "claim", "adjuster",
            "lowball", "dispute", "totaled", "car value", "diminished",
            "payout", "offer", "vehicle", "accident",
        ]
        kw_lower = keyword.lower()
        return any(term in kw_lower for term in relevant_terms)

    def _load_performance_hints(self) -> dict:
        """Load lessons from Sage/Morgan about which categories and topics perform well."""
        lessons = self.get_lessons_for_me()
        if not lessons:
            return {}

        hints: dict[str, list[str]] = {"preferred": [], "avoid": [], "gsc_insights": []}
        for lesson in lessons:
            if lesson.category == "high_pass_category" and lesson.occurrences >= 2:
                hints["preferred"].append(lesson.lesson)
            elif lesson.category == "low_pass_category" and lesson.occurrences >= 3:
                hints["avoid"].append(lesson.lesson)
            elif lesson.category.startswith("gsc_"):
                hints["gsc_insights"].append(lesson.lesson)
        return hints

    def _analyze_competitor_gaps(self, keyword: str) -> str:
        """Gap Analyst — use search grounding to find what top articles lack.

        Queries Gemini Pro with search grounding enabled to discover what
        existing top-ranking content misses: state-level nuances, insider
        adjuster tips, specific dollar amounts, and actionable templates.

        Returns a gap analysis string to embed in the content brief, or
        empty string if the analysis fails or no LLM is available.
        """
        if not self.has_llm:
            return ""

        prompt = f"""You are an SEO gap analyst for insurance content.

Search for the top-ranking articles about: "{keyword}"

Analyze what the existing content LACKS. Insurance content typically misses:
1. **State-specific nuances** — most articles are generic; identify which state laws or thresholds are missing
2. **Insider adjuster tips** — what would a claims adjuster say that these articles don't cover?
3. **Specific dollar amounts and ranges** — vague articles say "you could get more" but don't give numbers
4. **Actionable templates** — exact phrases, scripts, or letter templates readers can use
5. **Common mistakes** — what do most articles fail to warn readers about?

Return a concise gap analysis (5-8 bullet points) of what a NEW article should include
that existing content does NOT cover. Be specific — cite actual gaps, not generic advice.

Format:
GAPS:
- [specific gap 1]
- [specific gap 2]
..."""

        try:
            # Uses strategy_model (Pro) which has search grounding enabled
            result = self.call_claude(
                prompt,
                model=self.strategy_model,
                max_tokens=600,
            )
            # Extract just the gaps section
            if "GAPS:" in result:
                return result[result.index("GAPS:"):]
            return result.strip()
        except Exception as e:
            logger.warning(f"Gap analysis failed for '{keyword}': {e}")
            return ""

    def _ai_generate_brief(self, keyword: str, category: str) -> str:
        """Use Claude to generate a detailed content brief."""
        # Include performance insights if available
        perf_section = ""
        hints = self._load_performance_hints()
        if hints.get("gsc_insights"):
            perf_section = "\n\nPerformance data from published articles:\n"
            for insight in hints["gsc_insights"][:3]:
                perf_section += f"- {insight}\n"
            perf_section += "Use these insights to shape the brief.\n"

        # Run gap analysis to find what competitors miss
        gap_analysis = self._analyze_competitor_gaps(keyword)
        gap_section = ""
        if gap_analysis:
            gap_section = f"\n\nCompetitor Gap Analysis (cover these gaps that existing articles miss):\n{gap_analysis}\n"

        prompt = f"""Generate a content brief for an SEO article targeting the keyword: "{keyword}"

Category: {category}

The article is for ClaimCoach (claimcoach.app), an AI tool that helps car owners fight
lowball insurance total loss settlement offers.
{perf_section}{gap_section}
Provide:
1. Suggested angle/hook (2 sentences)
2. Key points to cover (5-7 bullets)
3. Target audience pain point
4. Suggested internal topics to link to
5. Authoritative external sources to reference
6. Specific gaps to fill that competitors miss

Keep it concise — this is a brief, not the article."""

        return self.call_claude(
            prompt,
            model=self.strategy_model,
            max_tokens=500,
        )

    def _ai_suggest_title(self, keyword: str) -> str:
        """Use Claude to suggest an SEO-optimized title."""
        prompt = f"""Suggest one SEO-optimized blog post title for the keyword: "{keyword}"

Requirements:
- Include the keyword naturally
- Under 60 characters if possible
- Compelling for someone searching this keyword (they're stressed about their insurance settlement)
- No clickbait, but create urgency

Return ONLY the title, nothing else."""

        return self.call_claude(
            prompt,
            model=self.utility_model,
            max_tokens=60,
        ).strip().strip('"')
