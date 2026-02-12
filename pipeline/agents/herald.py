"""Herald agent — social amplifier.

Promotes published articles across social channels (Reddit, Twitter/X, Facebook)
with anti-spam safeguards.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus

logger = logging.getLogger(__name__)

# Subreddits relevant to ClaimCoach
TARGET_SUBREDDITS = [
    "insurance",
    "personalfinance",
    "legaladvice",
    "cars",
    "askcarsales",
    "MechanicAdvice",
]

# Anti-spam limits
MAX_POSTS_PER_SUBREDDIT_PER_WEEK = 1
MIN_COMMENT_TO_LINK_RATIO = 5  # 5 helpful comments per 1 link share


class HeraldAgent(BaseAgent):
    name = "herald"
    claim_field = "herald_claim"

    def run(self) -> dict[str, Any]:
        """Promote published articles that haven't been amplified yet."""
        # Learn from past social engagement before generating new content
        self._learn_from_engagement()

        # Find articles that are done but not yet amplified
        done_articles = self.db.query_articles(
            status=ArticleStatus.DONE.value, limit=20
        )
        unpromoted = [a for a in done_articles if not a.social_status]

        if not unpromoted:
            logger.info("No articles to promote")
            return {"status": "idle", "promoted": 0}

        promoted = []
        for article in unpromoted:
            claim_id = self.generate_claim_id()
            if not self.db.try_claim(
                article.id, "herald_claim", claim_id, ArticleStatus.DONE.value
            ):
                continue

            result = self._promote_article(article)
            promoted.append(result)

        self.db.record_metric("herald_run", len(promoted))
        logger.info(f"Herald promoted {len(promoted)} articles")
        return {"status": "success", "promoted": len(promoted), "details": promoted}

    def _learn_from_engagement(self) -> None:
        """Analyze past social posts to learn what gets engagement.

        Checks the social_posts table for posts with engagement data and
        records lessons about which platforms and content categories work.
        """
        # Get all amplified articles to correlate content category with platform engagement
        amplified = self.db.query_articles(status=ArticleStatus.AMPLIFIED.value, limit=100)

        platform_engagement: dict[str, list[int]] = {}
        category_engagement: dict[str, list[int]] = {}

        for article in amplified:
            posts = self.db.get_social_posts_for_article(str(article.id))
            for post in posts:
                if post.engagement_count > 0:
                    platform_engagement.setdefault(post.platform, []).append(
                        post.engagement_count
                    )
                    cat = article.content_category or "general"
                    category_engagement.setdefault(
                        f"{post.platform}:{cat}", []
                    ).append(post.engagement_count)

        # Record platform lessons
        if platform_engagement:
            best_platform = max(
                platform_engagement,
                key=lambda p: sum(platform_engagement[p]) / len(platform_engagement[p]),
            )
            avg_eng = sum(platform_engagement[best_platform]) / len(
                platform_engagement[best_platform]
            )
            self.record_lesson(
                "herald", "best_platform",
                f"'{best_platform}' gets highest engagement (avg {avg_eng:.0f})",
            )

        # Record category+platform combos
        if category_engagement:
            best_combo = max(
                category_engagement,
                key=lambda c: sum(category_engagement[c]) / len(category_engagement[c]),
            )
            self.record_lesson(
                "herald", "best_combo",
                f"'{best_combo}' combo gets best social engagement",
            )

    def _promote_article(self, article) -> dict:
        """Generate social content for an article and post/draft it."""
        results = {"article_id": article.id, "title": article.title, "posts": []}

        # Generate social content using Claude
        if self.config.anthropic.api_key:
            social_content = self._generate_social_content(article)
        else:
            social_content = self._template_social_content(article)

        # Reddit promotion
        reddit_result = self._promote_reddit(article, social_content.get("reddit", ""))
        results["posts"].append(reddit_result)

        # Twitter/X promotion
        twitter_result = self._promote_twitter(article, social_content.get("twitter", ""))
        results["posts"].append(twitter_result)

        # Update article social status
        self.db.update_article(
            article.id,
            social_status="amplified",
            status=ArticleStatus.AMPLIFIED.value,
        )

        return results

    def _generate_social_content(self, article) -> dict:
        """Use Claude to generate platform-specific social content."""
        # Load engagement lessons to guide generation
        lessons = self.get_lessons_for_me()
        lesson_section = ""
        if lessons:
            lesson_section = "\n\nInsights from past social performance:\n"
            for lesson in lessons[:5]:
                lesson_section += f"- {lesson.lesson}\n"
            lesson_section += "Use these insights to optimize your posts.\n"

        prompt = f"""Generate social media posts to promote this article. The tone should be
genuinely helpful, never spammy. We're sharing a useful resource, not selling.

Article Title: {article.title}
Article URL: {article.published_url}
Keyword: {article.target_keyword}
Summary (first 500 chars): {article.markdown_content[:500]}
{lesson_section}
Generate posts for:

1. REDDIT: Write a helpful comment that could be posted on r/insurance or r/personalfinance.
It should be genuinely useful standalone advice that naturally mentions the article link as
a resource. Tone: "I found this guide useful when I went through the same thing."
Max 300 words.

2. TWITTER: Write a Twitter thread (3-4 tweets). Format: Start with a hook about the key
insight, share 2-3 specific facts/tips from the article, end with the link.
Tone: Sharp, informative, slightly adversarial toward insurers.
Each tweet max 280 chars.

3. FACEBOOK: Write a personal-feeling Facebook post for insurance/car groups.
Tone: "My friend just went through this and I found this breakdown really helpful..."
Max 200 words.

Format your response as:
REDDIT:
<reddit post>

TWITTER:
<tweet 1>
---
<tweet 2>
---
<tweet 3>

FACEBOOK:
<facebook post>"""

        try:
            result = self.call_claude(
                prompt,
                model=self.default_model,
                max_tokens=1500,
            )
            return self._parse_social_content(result)
        except Exception as e:
            logger.warning(f"Failed to generate social content: {e}")
            return self._template_social_content(article)

    def _parse_social_content(self, text: str) -> dict:
        """Parse Claude's social content output into platform-specific posts."""
        content = {"reddit": "", "twitter": "", "facebook": ""}

        sections = {
            "REDDIT:": "reddit",
            "TWITTER:": "twitter",
            "FACEBOOK:": "facebook",
        }

        current_section = None
        current_lines = []

        for line in text.split("\n"):
            stripped = line.strip()
            for marker, key in sections.items():
                if stripped.startswith(marker):
                    if current_section:
                        content[current_section] = "\n".join(current_lines).strip()
                    current_section = key
                    current_lines = []
                    remaining = stripped[len(marker):].strip()
                    if remaining:
                        current_lines.append(remaining)
                    break
            else:
                if current_section:
                    current_lines.append(line)

        if current_section:
            content[current_section] = "\n".join(current_lines).strip()

        return content

    def _template_social_content(self, article) -> dict:
        """Generate template-based social content (no AI needed)."""
        url = article.published_url or f"https://claimcoach.app/blog/{article.slug}"
        title = article.title
        keyword = article.target_keyword

        return {
            "reddit": (
                f"I came across this guide on {keyword} and found it really helpful. "
                f"Covers the specific steps and what to watch out for. Thought it might "
                f"help others in a similar situation: {url}"
            ),
            "twitter": (
                f"Just published: {title}\n\n"
                f"Most people don't realize what they're leaving on the table with "
                f"insurance settlements.\n\n"
                f"Full guide: {url}"
            ),
            "facebook": (
                f"Sharing this because a friend recently went through a tough insurance "
                f"claim situation. This guide on {keyword} breaks down what to look for "
                f"and how to fight back: {url}"
            ),
        }

    def _promote_reddit(self, article, content: str) -> dict:
        """Post to Reddit or save as draft for human review."""
        result = {
            "platform": "reddit",
            "status": "draft",
            "content": content,
        }

        # Save draft to database (Lurker's model — human approval required)
        self.db.create_social_post(
            article_id=article.id,
            platform="reddit",
            post_content=content,
            status="draft",
        )

        # Only auto-post if Reddit API is configured AND within anti-spam limits
        if self.config.reddit.client_id and self.config.reddit.client_secret:
            if self._check_reddit_spam_limits():
                posted = self._post_to_reddit(article, content)
                if posted:
                    result["status"] = "posted"
                    result["url"] = posted

        return result

    def _promote_twitter(self, article, content: str) -> dict:
        """Post to Twitter/X or save as draft."""
        result = {
            "platform": "twitter",
            "status": "draft",
            "content": content,
        }

        self.db.create_social_post(
            article_id=article.id,
            platform="twitter",
            post_content=content,
            status="draft",
        )

        if self.config.twitter.api_key:
            posted = self._post_to_twitter(content)
            if posted:
                result["status"] = "posted"

        return result

    def _check_reddit_spam_limits(self) -> bool:
        """Check if we're within anti-spam posting limits."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        posts = self.db.get_metrics(name="reddit_post", since=week_ago)
        # Simple check: max N posts per week total
        return len(posts) < len(TARGET_SUBREDDITS) * MAX_POSTS_PER_SUBREDDIT_PER_WEEK

    def _post_to_reddit(self, article, content: str) -> str | None:
        """Post to Reddit using the API."""
        try:
            import requests

            # OAuth2 authentication
            auth = requests.auth.HTTPBasicAuth(
                self.config.reddit.client_id,
                self.config.reddit.client_secret,
            )
            data = {
                "grant_type": "password",
                "username": self.config.reddit.username,
                "password": self.config.reddit.password,
            }
            headers = {"User-Agent": "ClaimCoach-Herald/0.1"}
            token_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=auth,
                data=data,
                headers=headers,
                timeout=30,
            )
            token = token_resp.json().get("access_token")
            if not token:
                logger.warning("Failed to get Reddit OAuth token")
                return None

            # Post as a comment in a relevant thread (not a new post — safer)
            # This is a placeholder; real implementation would find threads via Lurker
            self.db.record_metric("reddit_post", 1, article.id)
            logger.info("Reddit post drafted (auto-posting skipped — needs human review)")
            return None

        except Exception as e:
            logger.warning(f"Reddit post failed: {e}")
            return None

    def _post_to_twitter(self, content: str) -> bool:
        """Post to Twitter/X."""
        try:
            import requests
            from requests_oauthlib import OAuth1

            auth = OAuth1(
                self.config.twitter.api_key,
                self.config.twitter.api_secret,
                self.config.twitter.access_token,
                self.config.twitter.access_token_secret,
            )

            # Post first tweet
            first_tweet = content.split("\n")[0][:280]
            resp = requests.post(
                "https://api.twitter.com/2/tweets",
                json={"text": first_tweet},
                auth=auth,
                timeout=30,
            )
            return resp.status_code in (200, 201)
        except ImportError:
            logger.warning("requests-oauthlib not installed — Twitter posting unavailable")
            return False
        except Exception as e:
            logger.warning(f"Twitter post failed: {e}")
            return False
