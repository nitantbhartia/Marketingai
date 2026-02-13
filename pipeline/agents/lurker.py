"""Lurker agent — community scout.

Finds relevant Reddit/forum threads for engagement opportunities.
Drafts responses but does NOT auto-post (human approval required).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from pipeline.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# Search queries for finding relevant threads
SEARCH_QUERIES = [
    "total loss lowball insurance",
    "insurance settlement too low",
    "insurance company undervalued my car",
    "dispute total loss offer",
    "totaled car unfair settlement",
    "state farm total loss lowball",
    "geico total loss settlement",
    "progressive total loss offer",
    "allstate total loss lowball",
    "insurance adjuster settlement unfair",
    "diminished value claim help",
    "loss of use insurance claim",
    "sales tax total loss settlement",
]

# Reddit subreddits to search
TARGET_SUBREDDITS = [
    "insurance",
    "personalfinance",
    "legaladvice",
    "cars",
    "askcarsales",
    "MechanicAdvice",
    "povertyfinance",
]


class LurkerAgent(BaseAgent):
    name = "lurker"

    def run(self) -> dict[str, Any]:
        """Search for engagement opportunities across platforms."""
        # Learn from past opportunity outcomes before searching
        self._learn_from_outcomes()

        opportunities_found = 0

        # Search Reddit
        reddit_opps = self._search_reddit()
        opportunities_found += len(reddit_opps)

        # Draft responses for high-scoring opportunities
        drafted = 0
        if self.has_llm:
            for opp in reddit_opps:
                if opp.get("score", 0) >= 0.6:
                    draft = self._draft_response(opp)
                    if draft:
                        self.db.create_opportunity(
                            platform="reddit",
                            url=opp.get("url", ""),
                            thread_title=opp.get("title", ""),
                            subreddit=opp.get("subreddit", ""),
                            relevance_score=opp.get("score", 0),
                            engagement_count=opp.get("comments", 0),
                            posted_at=opp.get("created", ""),
                            draft_response=draft,
                            status="pending",
                        )
                        drafted += 1

        self.db.record_metric(
            "lurker_run",
            opportunities_found,
            json.dumps({"reddit": len(reddit_opps), "drafted": drafted}),
        )

        logger.info(
            f"Lurker found {opportunities_found} opportunities, "
            f"drafted {drafted} responses"
        )
        return {
            "status": "success",
            "opportunities": opportunities_found,
            "drafted": drafted,
        }

    def _learn_from_outcomes(self) -> None:
        """Analyze which past opportunities got approved vs. skipped.

        Records lessons about which subreddits and thread characteristics
        yield responses that humans actually approve and post.
        """
        all_opps = self.db.query_opportunities(limit=200)
        if len(all_opps) < 10:
            return

        # Approval rate by subreddit
        sub_stats: dict[str, dict[str, int]] = {}
        for opp in all_opps:
            sub = opp.subreddit or "unknown"
            sub_stats.setdefault(sub, {"approved": 0, "total": 0})
            sub_stats[sub]["total"] += 1
            if opp.status in ("approved", "posted"):
                sub_stats[sub]["approved"] += 1

        for sub, stats in sub_stats.items():
            if stats["total"] >= 3:
                rate = stats["approved"] / stats["total"]
                if rate >= 0.5:
                    self.record_lesson(
                        "lurker", "high_approval_subreddit",
                        f"r/{sub} ({rate:.0%} approval rate, n={stats['total']})",
                    )
                elif rate <= 0.2 and stats["total"] >= 5:
                    self.record_lesson(
                        "lurker", "low_approval_subreddit",
                        f"r/{sub} ({rate:.0%} approval rate — consider deprioritizing)",
                    )

        # Feed high-engagement community topics back to Scout so it can
        # discover topics people are actively asking about.
        high_engagement = [
            opp for opp in all_opps
            if opp.engagement_count >= 20
            and opp.relevance_score >= 0.7
            and opp.status in ("approved", "posted")
        ]
        for opp in high_engagement[:5]:
            self.record_lesson(
                "scout", "community_demand",
                f"High engagement ({opp.engagement_count} comments) in "
                f"r/{opp.subreddit}: {opp.thread_title}",
            )

    def _search_reddit(self) -> list[dict]:
        """Search Reddit for relevant threads."""
        opportunities = []

        # Use Reddit's JSON API (no authentication needed for search)
        for subreddit in TARGET_SUBREDDITS:
            for query in SEARCH_QUERIES[:5]:  # Limit queries per run
                try:
                    results = self._reddit_search(subreddit, query)
                    for post in results:
                        scored = self._score_opportunity(post, subreddit)
                        if scored["score"] >= 0.4:
                            opportunities.append(scored)
                except Exception as e:
                    logger.debug(f"Reddit search failed for r/{subreddit} '{query}': {e}")

        # Deduplicate by URL
        seen = set()
        unique = []
        for opp in opportunities:
            url = opp.get("url", "")
            if url not in seen:
                seen.add(url)
                unique.append(opp)

        # Sort by score
        unique.sort(key=lambda x: x.get("score", 0), reverse=True)
        return unique[:20]  # Top 20 opportunities

    def _reddit_search(self, subreddit: str, query: str) -> list[dict]:
        """Search a subreddit using Reddit's public JSON API."""
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {
            "q": query,
            "restrict_sr": "on",
            "sort": "new",
            "limit": 10,
            "t": "week",  # Last week only
        }
        headers = {"User-Agent": "ClaimCoach-Lurker/0.1 (content research)"}

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        data = resp.json()
        posts = []
        for child in data.get("data", {}).get("children", []):
            post_data = child.get("data", {})
            posts.append({
                "title": post_data.get("title", ""),
                "url": f"https://reddit.com{post_data.get('permalink', '')}",
                "subreddit": subreddit,
                "score": 0,
                "comments": post_data.get("num_comments", 0),
                "upvotes": post_data.get("ups", 0),
                "created": datetime.fromtimestamp(
                    post_data.get("created_utc", 0), tz=timezone.utc
                ).isoformat(),
                "selftext": post_data.get("selftext", "")[:500],
            })

        return posts

    def _score_opportunity(self, post: dict, subreddit: str) -> dict:
        """Score an opportunity for relevance and engagement potential.

        Uses learned subreddit approval rates to boost/penalize scores.
        """
        score = 0.0
        title_lower = (post.get("title", "") + " " + post.get("selftext", "")).lower()

        # Relevance scoring
        high_relevance = [
            "total loss", "lowball", "settlement", "insurance offer",
            "undervalued", "totaled car",
        ]
        medium_relevance = [
            "insurance claim", "dispute", "adjuster", "car value",
            "diminished value", "payout",
        ]
        low_relevance = [
            "accident", "damage", "repair", "collision",
        ]

        for term in high_relevance:
            if term in title_lower:
                score += 0.2

        for term in medium_relevance:
            if term in title_lower:
                score += 0.1

        for term in low_relevance:
            if term in title_lower:
                score += 0.05

        # Engagement signals
        comments = post.get("comments", 0)
        if comments >= 10:
            score += 0.15
        elif comments >= 5:
            score += 0.1
        elif comments >= 1:
            score += 0.05

        # Recency bonus
        if post.get("created"):
            try:
                created = datetime.fromisoformat(post["created"])
                hours_old = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                if hours_old < 24:
                    score += 0.15
                elif hours_old < 72:
                    score += 0.1
            except (ValueError, TypeError):
                pass

        # Learned subreddit bonus/penalty
        high_subs = self.get_lessons_for_me(category="high_approval_subreddit")
        low_subs = self.get_lessons_for_me(category="low_approval_subreddit")
        high_sub_names = {l.lesson.split("(")[0].strip() for l in high_subs}
        low_sub_names = {l.lesson.split("(")[0].strip() for l in low_subs}

        if f"r/{subreddit}" in high_sub_names:
            score += 0.1  # Boost subreddits with high approval history
        elif f"r/{subreddit}" in low_sub_names:
            score -= 0.1  # Penalize subreddits that rarely get approved

        # Cap at 1.0
        score = min(1.0, max(0.0, round(score, 2)))

        post["score"] = score
        return post

    def _draft_response(self, opportunity: dict) -> str | None:
        """Use Claude to draft a helpful response."""
        # Find relevant published articles to link
        published = self.db.get_published_articles()
        relevant_articles = self._find_relevant_articles(
            opportunity, published
        )

        articles_context = ""
        if relevant_articles:
            articles_context = "\nRelevant articles you can reference:\n"
            for a in relevant_articles[:3]:
                url = a.published_url or f"https://claimcoach.app/blog/{a.slug}"
                articles_context += f"- [{a.title}]({url})\n"

        # Load engagement lessons to guide response style
        lessons = self.get_lessons_for_me()
        lesson_section = ""
        if lessons:
            lesson_section = "\n\nLessons from past responses:\n"
            for lesson in lessons[:4]:
                lesson_section += f"- {lesson.lesson}\n"

        prompt = f"""Draft a helpful Reddit response to this thread. Be genuinely useful —
not promotional. Share real advice and only mention the article if it's truly relevant.

Thread title: {opportunity.get('title', '')}
Subreddit: r/{opportunity.get('subreddit', '')}
Thread content: {opportunity.get('selftext', '')[:500]}
{articles_context}{lesson_section}

Rules:
- Sound like a real person who's been through a similar situation
- Give specific, actionable advice first
- Only link to an article if it genuinely adds value
- Never mention "ClaimCoach" by name — let the article speak for itself
- Keep it under 200 words
- Match the subreddit's tone (r/insurance is professional, r/personalfinance is casual)

Draft the response:"""

        try:
            response = self.call_claude(
                prompt,
                model=self.default_model,
                max_tokens=400,
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"Failed to draft response: {e}")
            return None

    def _find_relevant_articles(self, opportunity: dict, published: list) -> list:
        """Find published articles relevant to an opportunity."""
        text = (
            opportunity.get("title", "") + " " + opportunity.get("selftext", "")
        ).lower()

        scored = []
        for article in published:
            keyword = article.target_keyword.lower() if article.target_keyword else ""
            title = article.title.lower() if article.title else ""

            relevance = 0
            if keyword and keyword in text:
                relevance += 2
            for word in keyword.split():
                if len(word) > 3 and word in text:
                    relevance += 0.5
            for word in title.split():
                if len(word) > 3 and word in text:
                    relevance += 0.3

            if relevance > 0:
                scored.append((relevance, article))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[:3]]
