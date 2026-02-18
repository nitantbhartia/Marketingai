"""Scout agent — keyword research and topic discovery.

Discovers high-value topics for ClaimCoach content and populates
the backlog with scored, briefed entries ready for writing.
"""

from __future__ import annotations

import json
import logging
import re
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

    _STOPWORDS = {
        "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at",
        "with", "from", "by", "how", "what", "when", "why", "your", "you",
        "is", "are", "can", "do", "does", "my", "vs", "guide",
    }
    _US_STATES = {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    }

    @staticmethod
    def _product_info(product: str) -> dict[str, str]:
        p = (product or "claimcoach").strip().lower()
        if p == "medbill":
            return {
                "product": "medbill",
                "brand": "BillScan",
                "site_url": "https://billkarma.app",
            }
        return {
            "product": "claimcoach",
            "brand": "ClaimCoach",
            "site_url": "https://claimcoach.app",
        }

    def run(self) -> dict[str, Any]:
        """Research topics and populate backlog."""
        existing_count = self.db.count_articles(ArticleStatus.BACKLOG.value)
        logger.info(f"Current backlog: {existing_count} topics")

        # Build intent index to avoid duplicates/cannibalization.
        all_articles = self.db.query_articles(limit=5000)
        existing_keywords = {a.target_keyword.lower() for a in all_articles if a.target_keyword}
        intent_index = []
        cluster_primary: dict[str, str] = {}
        for a in all_articles:
            kw = (a.target_keyword or "").strip()
            if not kw:
                continue
            tokens = self._intent_tokens(kw)
            cluster = self._cluster_key(kw)
            product = (getattr(a, "product", "") or "claimcoach").strip().lower()
            primary = self._primary_url_for_article(a)
            intent_index.append(
                {
                    "keyword": kw.lower(),
                    "tokens": tokens,
                    "cluster": cluster,
                    "state": (a.target_state or self._state_hint(kw)),
                    "product": product,
                    "primary_url": primary,
                }
            )
            cluster_id = f"{product}:{cluster}" if cluster else ""
            if cluster_id and primary and cluster_id not in cluster_primary:
                cluster_primary[cluster_id] = primary

        products = list(getattr(self.config.pipeline, "products", ["claimcoach"]))
        if not products:
            products = ["claimcoach"]

        # Phase 1: Seed topics (always available, no API needed)
        topics = []
        for product in products:
            topics.extend(get_all_seed_topics(product=product))
        new_count = 0
        skipped_duplicates = 0
        skipped_cannibal = 0

        for topic in topics:
            kw = topic["keyword"]
            product = (topic.get("product", "claimcoach") or "claimcoach").strip().lower()
            if kw.lower() in existing_keywords:
                skipped_duplicates += 1
                continue
            conflict = self._find_cannibalization_conflict(
                kw, topic.get("state", ""), intent_index, target_product=product
            )
            if conflict:
                logger.info(
                    f"Skipping '{kw}' — cannibalizes cluster '{conflict['cluster']}' "
                    f"(primary URL: {conflict['primary_url']})"
                )
                skipped_cannibal += 1
                continue

            cluster = self._cluster_key(kw)
            cluster_id = f"{product}:{cluster}" if cluster else ""
            primary_url = cluster_primary.get(cluster_id)
            if not primary_url:
                primary_url = self._default_primary_url(kw, product=product)
                if cluster_id:
                    cluster_primary[cluster_id] = primary_url

            intent_template = self._infer_intent_template(
                keyword=kw,
                category=topic.get("category", ""),
                product=product,
            )
            fact_pack = self._build_fact_pack(
                keyword=kw,
                category=topic.get("category", ""),
                state=topic.get("state", ""),
                product=product,
            )

            # Generate content brief
            brief = self._generate_brief(
                topic,
                cluster,
                primary_url,
                intent_template=intent_template,
                fact_pack=fact_pack,
                product=product,
            )

            self.db.create_article(
                product=product,
                title=topic.get("suggested_title", ""),
                status=ArticleStatus.BACKLOG.value,
                target_keyword=kw,
                search_volume=topic.get("volume", 0),
                keyword_difficulty=topic.get("difficulty", 0.0),
                commercial_intent=topic.get("intent", 0.0),
                content_brief=brief,
                fact_pack=fact_pack,
                intent_template=intent_template,
                cluster_key=cluster,
                canonical_url=primary_url,
                target_state=topic.get("state", ""),
                content_category=topic.get("category", ""),
                suggested_title=self._suggest_title(kw, topic.get("category", "")),
            )
            existing_keywords.add(kw.lower())
            intent_index.append(
                {
                    "keyword": kw.lower(),
                    "tokens": self._intent_tokens(kw),
                    "cluster": cluster,
                    "state": topic.get("state", "") or self._state_hint(kw),
                    "product": product,
                    "primary_url": primary_url,
                }
            )
            new_count += 1

        # Phase 2: Discover related keywords via autocomplete
        discovery_bases = []
        if "claimcoach" in products:
            discovery_bases.extend([
                "total loss settlement",
                "insurance lowball offer",
                "car totaled what to do",
                "diminished value claim",
                "dispute insurance claim",
            ])
        if "medbill" in products:
            discovery_bases.extend([
                "medical bill negotiation",
                "surprise medical bill dispute",
                "hospital bill too high",
                "itemized bill errors",
                "out of network lab bill",
            ])

        discovered = 0
        for base in discovery_bases:
            related = discover_related_keywords(base)
            for kw in related:
                if kw.lower() in existing_keywords:
                    skipped_duplicates += 1
                    continue
                if not self._is_relevant(kw):
                    continue
                inferred_product = "medbill" if any(
                    t in kw.lower()
                    for t in (
                        "medical bill", "hospital bill", "anesthesia",
                        "lab bill", "no surprises", "er bill",
                    )
                ) else "claimcoach"
                conflict = self._find_cannibalization_conflict(
                    kw, "", intent_index, target_product=inferred_product
                )
                if conflict:
                    skipped_cannibal += 1
                    continue

                cluster = self._cluster_key(kw)
                cluster_id = f"{inferred_product}:{cluster}" if cluster else ""
                primary_url = cluster_primary.get(cluster_id)
                if not primary_url:
                    primary_url = self._default_primary_url(kw, product=inferred_product)
                    if cluster_id:
                        cluster_primary[cluster_id] = primary_url

                intent_template = self._infer_intent_template(
                    keyword=kw, category="discovered", product=inferred_product
                )
                fact_pack = self._build_fact_pack(
                    keyword=kw, category="discovered", state="", product=inferred_product
                )

                brief = (
                    f"Write a comprehensive guide about: {kw}\n\n"
                    f"Intent template: {intent_template}\n"
                    f"Cluster key: {cluster}\n"
                    f"Primary URL for this cluster: {primary_url}\n"
                    "Do not cannibalize the primary URL intent."
                )
                self.db.create_article(
                    product=inferred_product,
                    status=ArticleStatus.BACKLOG.value,
                    target_keyword=kw,
                    search_volume=100,  # Estimated
                    keyword_difficulty=0.25,
                    commercial_intent=0.7,
                    content_brief=brief,
                    fact_pack=fact_pack,
                    intent_template=intent_template,
                    cluster_key=cluster,
                    canonical_url=primary_url,
                    content_category="discovered",
                    suggested_title=self._suggest_title(kw, "discovered"),
                )
                existing_keywords.add(kw.lower())
                intent_index.append(
                    {
                        "keyword": kw.lower(),
                        "tokens": self._intent_tokens(kw),
                        "cluster": cluster,
                        "state": self._state_hint(kw),
                        "product": inferred_product,
                        "primary_url": primary_url,
                    }
                )
                discovered += 1

        # Load lessons about which categories perform well
        performance_hints = self._load_performance_hints()

        # Phase 3: If LLM is available, generate briefs for top unbriefed topics.
        # Capped at 5 per run (each brief = 2 API calls, well within free-tier
        # 5 RPM when spaced by the global rate_limit_delay).
        unbriefed = self.db.query_articles(status=ArticleStatus.BACKLOG.value, limit=50)
        unbriefed = [a for a in unbriefed if (a.product or "claimcoach") in products][:15]
        if performance_hints:
            # Prioritize high-pass categories and push known low-pass
            # categories to the back of the briefing queue.
            unbriefed = sorted(
                unbriefed,
                key=lambda a: self._brief_priority(a, performance_hints),
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
                    cluster = self._cluster_key(article.target_keyword or "")
                    product = (article.product or "claimcoach").strip().lower()
                    cluster_id = f"{product}:{cluster}" if cluster else ""
                    primary_url = cluster_primary.get(cluster_id) or self._default_primary_url(
                        article.target_keyword or "", product=product
                    )
                    if cluster_id:
                        cluster_primary[cluster_id] = primary_url
                    intent_template = self._infer_intent_template(
                        keyword=article.target_keyword or "",
                        category=article.content_category or "",
                        product=product,
                    )
                    fact_pack = self._build_fact_pack(
                        keyword=article.target_keyword or "",
                        category=article.content_category or "",
                        state=article.target_state or "",
                        product=product,
                    )
                    brief = self._ai_generate_brief(
                        article.target_keyword,
                        article.content_category,
                        cluster_key=cluster,
                        primary_url=primary_url,
                        intent_template=intent_template,
                        fact_pack=fact_pack,
                        product=product,
                    )
                    title = self._ai_suggest_title(article.target_keyword)
                    self.db.update_article(
                        article.id,
                        content_brief=brief,
                        fact_pack=fact_pack,
                        intent_template=intent_template,
                        cluster_key=cluster,
                        canonical_url=primary_url,
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
        backlog = [a for a in backlog if (a.product or "claimcoach") in products]
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
            "skipped_duplicates": skipped_duplicates,
            "skipped_cannibalization": skipped_cannibal,
            "ai_briefed": briefed,
            "promoted_to_todo": promoted,
            "final_backlog": final_backlog,
        }))

        summary = {
            "seed_topics_added": new_count,
            "discovered_topics": discovered,
            "skipped_duplicates": skipped_duplicates,
            "skipped_cannibalization": skipped_cannibal,
            "ai_briefed": briefed,
            "promoted_to_todo": promoted,
            "final_backlog": final_backlog,
        }
        logger.info(f"Scout complete: {summary}")
        return summary

    @staticmethod
    def _brief_priority(article, hints: dict) -> tuple[float, int, float, int]:
        """Priority key for AI briefing queue.

        Higher is better:
        1. Categories with repeated high-pass lessons
        2. Higher commercial intent and search volume
        3. Newer rows as tie-breaker
        Categories repeatedly marked low-pass are deprioritized but not dropped.
        """
        preferred = set(hints.get("preferred", []))
        avoid = set(hints.get("avoid", []))
        category = article.content_category or ""

        bucket = 0.0
        if category in preferred:
            bucket += 2.0
        if category in avoid:
            bucket -= 2.0

        return (
            bucket,
            int(getattr(article, "search_volume", 0) or 0),
            float(getattr(article, "commercial_intent", 0.0) or 0.0),
            int(getattr(article, "id", 0) or 0),
        )

    def _generate_brief(
        self,
        topic: dict,
        cluster_key: str,
        primary_url: str,
        intent_template: str = "",
        fact_pack: str = "",
        product: str = "claimcoach",
    ) -> str:
        """Generate a content brief from topic data."""
        category = topic.get("category", "")
        kw = topic["keyword"]
        info = self._product_info(product)

        briefs = {
            "problem_aware": (
                f"Write an empathetic, educational article targeting people who just "
                f"discovered their insurance settlement is too low. Focus on '{kw}'. "
                f"Validate their frustration, explain why this happens, and introduce "
                f"actionable steps they can take. End with a {info['brand']} CTA."
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
        base = briefs.get(category, f"Write a comprehensive guide about: {kw}")
        cluster_block = (
            "\n\nCluster canonicalization:\n"
            f"- Cluster key: {cluster_key}\n"
            f"- Primary URL for this intent cluster: {primary_url}\n"
            "- This article must target a distinct intent and avoid cannibalizing the primary URL.\n"
        )
        intent_block = (
            "\nIntent template:\n"
            f"- {intent_template}\n"
            "- Match this query intent exactly; do not drift into generic explainer mode.\n"
        ) if intent_template else ""
        fact_block = (
            "\nPre-write fact pack:\n"
            f"{fact_pack}\n"
            "- Use these sources for legal/numeric/timeline claims.\n"
        ) if fact_pack else ""
        brand_block = (
            "\nBrand + product context:\n"
            f"- Product: {info['brand']}\n"
            f"- Primary domain: {info['site_url']}\n"
        )
        return base + brand_block + intent_block + cluster_block + fact_block

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
            "medical bill", "hospital bill", "anesthesia", "lab bill",
            "charity care", "no surprises", "er bill", "itemized bill",
        ]
        kw_lower = keyword.lower()
        return any(term in kw_lower for term in relevant_terms)

    @classmethod
    def _intent_tokens(cls, keyword: str) -> set[str]:
        raw = re.sub(r"[^a-z0-9\s]", " ", (keyword or "").lower())
        tokens = [t for t in raw.split() if len(t) > 2 and t not in cls._STOPWORDS]
        return set(tokens)

    @classmethod
    def _state_hint(cls, keyword: str) -> str:
        text = (keyword or "").lower()
        for state in cls._US_STATES:
            if state in text:
                return state
        return ""

    def _cluster_key(self, keyword: str) -> str:
        tokens = sorted(self._intent_tokens(keyword))
        if not tokens:
            return ""
        return "-".join(tokens[:4])

    def _default_primary_url(self, keyword: str, product: str = "claimcoach") -> str:
        slug = re.sub(r"[^a-z0-9\s-]", "", keyword.lower())
        slug = re.sub(r"[\s]+", "-", slug).strip("-")
        info = self._product_info(product)
        return f"{info['site_url'].rstrip('/')}/blog/{slug[:80]}"

    def _primary_url_for_article(self, article) -> str:
        if getattr(article, "canonical_url", ""):
            return article.canonical_url
        if article.published_url:
            return article.published_url
        if article.slug:
            info = self._product_info(getattr(article, "product", "claimcoach"))
            return f"{info['site_url'].rstrip('/')}/blog/{article.slug}"
        kw = article.target_keyword or article.title or ""
        product = getattr(article, "product", "claimcoach")
        return self._default_primary_url(kw, product=product) if kw else ""

    def _find_cannibalization_conflict(
        self,
        keyword: str,
        target_state: str,
        intent_index: list[dict],
        target_product: str = "claimcoach",
    ) -> dict | None:
        """Return conflicting intent-cluster entry if keyword overlaps existing intent."""
        kw = (keyword or "").strip().lower()
        if not kw:
            return None

        tokens = self._intent_tokens(kw)
        if len(tokens) < 2:
            return None
        cluster = self._cluster_key(kw)
        state = (target_state or self._state_hint(kw) or "").lower()
        target_product = (target_product or "claimcoach").lower()

        for ex in intent_index:
            ex_kw = ex.get("keyword", "")
            ex_tokens = ex.get("tokens", set())
            ex_cluster = ex.get("cluster", "")
            ex_state = (ex.get("state") or "").lower()
            ex_product = (ex.get("product") or "claimcoach").lower()

            if ex_product != target_product:
                continue

            if kw == ex_kw:
                return ex

            # Treat state-specific and non-state intents as distinct clusters.
            if (state or ex_state) and state != ex_state:
                continue

            if cluster and ex_cluster and cluster == ex_cluster:
                return ex

            if not ex_tokens:
                continue
            inter = len(tokens & ex_tokens)
            union = len(tokens | ex_tokens)
            similarity = (inter / union) if union else 0.0
            if similarity >= 0.7:
                return ex

        return None

    def _infer_intent_template(self, keyword: str, category: str, product: str) -> str:
        """Classify keyword into a practical SERP-intent template."""
        kw = (keyword or "").lower()
        category = (category or "").lower()
        if any(t in kw for t in ("how to", "step", "dispute", "challenge", "appeal")):
            return "dispute_how_to"
        if any(t in kw for t in ("calculator", "estimate", "worth", "value", "threshold")):
            return "calculator_intent"
        if any(t in kw for t in ("law", "statute", "rule", "regulation", "rights")):
            return "law_threshold"
        if any(t in kw for t in ("negotiate", "negotiation", "counteroffer", "script")):
            return "negotiation_script"
        if category in {"state_specific", "line_item"}:
            return "law_threshold"
        if product == "medbill":
            return "negotiation_script"
        return "general_guide"

    def _build_fact_pack(
        self,
        keyword: str,
        category: str,
        state: str,
        product: str,
    ) -> str:
        """Build a deterministic pre-write factual source pack."""
        info = self._product_info(product)
        state_hint = (state or self._state_hint(keyword) or "").strip()
        lines = [
            f"- Product: {info['brand']} ({info['site_url']})",
            f"- Intent category: {category or 'general'}",
        ]
        if product == "medbill":
            lines.extend(
                [
                    "- Core sources:",
                    "  - CMS: https://www.cms.gov/",
                    "  - CMS No Surprises: https://www.cms.gov/nosurprises",
                    "  - HHS consumer guidance: https://www.hhs.gov/",
                    "  - IRS 501(r): https://www.irs.gov/charities-non-profits/charitable-organizations/requirements-for-501c3-hospitals-under-the-affordable-care-act-section-501r",
                    "- Numeric/legal/timeline claims must cite one of these or equivalent primary source.",
                ]
            )
        else:
            lines.extend(
                [
                    "- Core sources:",
                    "  - NAIC consumer portal: https://content.naic.org/consumer",
                    "  - State DOI directory: https://content.naic.org/state-insurance-departments",
                    "  - NHTSA: https://www.nhtsa.gov/",
                    "- Numeric/legal/timeline claims must cite state statutes or primary regulator pages.",
                ]
            )
        if state_hint:
            lines.append(f"- State focus: {state_hint} (add direct state regulator/statute links in draft)")
        return "\n".join(lines)

    def _load_performance_hints(self) -> dict:
        """Load lessons from Sage/Morgan about which categories and topics perform well."""
        lessons = self.get_lessons_for_me()
        if not lessons:
            return {}

        hints: dict[str, list[str]] = {
            "preferred": [], "avoid": [], "gsc_insights": [],
            "competitor_gaps": [], "community_demand": [],
        }
        for lesson in lessons:
            if lesson.category == "high_pass_category" and lesson.occurrences >= 2:
                hints["preferred"].append(lesson.lesson)
            elif lesson.category == "low_pass_category" and lesson.occurrences >= 3:
                hints["avoid"].append(lesson.lesson)
            elif lesson.category.startswith("gsc_"):
                hints["gsc_insights"].append(lesson.lesson)
            elif lesson.category == "competitor_gap":
                hints["competitor_gaps"].append(lesson.lesson)
            elif lesson.category == "community_demand":
                hints["community_demand"].append(lesson.lesson)
            elif lesson.category == "performance":
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

    def _ai_generate_brief(
        self,
        keyword: str,
        category: str,
        cluster_key: str = "",
        primary_url: str = "",
        intent_template: str = "",
        fact_pack: str = "",
        product: str = "claimcoach",
    ) -> str:
        """Use Claude to generate a detailed content brief."""
        info = self._product_info(product)
        # Include performance insights if available
        perf_section = ""
        hints = self._load_performance_hints()
        if hints.get("gsc_insights"):
            perf_section = "\n\nPerformance data from published articles:\n"
            for insight in hints["gsc_insights"][:3]:
                perf_section += f"- {insight}\n"
            perf_section += "Use these insights to shape the brief.\n"
        if hints.get("competitor_gaps"):
            perf_section += "\n\nCompetitor gaps identified by Rival agent:\n"
            for gap in hints["competitor_gaps"][:3]:
                perf_section += f"- {gap}\n"
        if hints.get("community_demand"):
            perf_section += "\n\nHigh-engagement community topics (from Lurker):\n"
            for topic in hints["community_demand"][:3]:
                perf_section += f"- {topic}\n"
        if category and category in set(hints.get("avoid", [])):
            perf_section += (
                "\n\nQuality caution:\n"
                "- This category has low first-pass review performance.\n"
                "- Use tighter structure, explicit citations, and avoid generic filler.\n"
            )

        # Run gap analysis to find what competitors miss
        gap_analysis = self._analyze_competitor_gaps(keyword) if product == "claimcoach" else ""
        gap_section = ""
        if gap_analysis:
            gap_section = f"\n\nCompetitor Gap Analysis (cover these gaps that existing articles miss):\n{gap_analysis}\n"

        cluster_section = ""
        if cluster_key and primary_url:
            cluster_section = (
                "\n\nCluster canonicalization requirements:\n"
                f"- Cluster key: {cluster_key}\n"
                f"- Primary URL for this cluster: {primary_url}\n"
                "- Ensure this new article targets a distinct intent and does not cannibalize the primary URL.\n"
            )
        intent_section = (
            "\n\nIntent template requirements:\n"
            f"- Intent template: {intent_template}\n"
            "- The final article outline must match this search intent exactly.\n"
        ) if intent_template else ""
        fact_section = (
            "\n\nPre-write fact pack:\n"
            f"{fact_pack}\n"
            "- Use these as default source anchors for legal/numeric/timeline claims.\n"
        ) if fact_pack else ""

        prompt = f"""Generate a content brief for an SEO article targeting the keyword: "{keyword}"

Category: {category}

The article is for {info['brand']} ({info['site_url']}).
{perf_section}{gap_section}{cluster_section}{intent_section}{fact_section}
Provide:
1. Suggested angle/hook (2 sentences)
2. Key points to cover (5-7 bullets)
3. Target audience pain point
4. Suggested internal topics to link to
5. Authoritative external sources to reference
6. Specific gaps to fill that competitors miss

Keep it concise — this is a brief, not the article."""

        brief = self.call_claude(
            prompt,
            model=self.strategy_model,
            max_tokens=500,
        )
        if cluster_key and primary_url:
            brief += (
                "\n\nCluster canonicalization:\n"
                f"- Cluster key: {cluster_key}\n"
                f"- Primary URL for this intent cluster: {primary_url}\n"
                "- This article must target distinct intent and avoid cannibalization."
            )
        if intent_template:
            brief += (
                "\n\nIntent template:\n"
                f"- {intent_template}\n"
                "- Match this query intent in intro, H2 flow, and CTA design."
            )
        return brief

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
