"""
Remix - Content Repurposer Agent

Transforms published articles into multiple formats for different platforms.
One article becomes: Twitter thread, LinkedIn post, email newsletter, YouTube script, etc.
"""

import json
import re
from datetime import datetime
from typing import Dict, Any, List
import anthropic

from pipeline.agents.base import Agent
from content_quality.db import get_db, log_content_remix, log_agent_action


class Remix(Agent):
    """
    Content repurposer agent - creates variations for multiple platforms.

    Workflow:
    1. Finds published articles to repurpose
    2. Generates format-specific variations
    3. Saves remixes to database
    4. Returns ready-to-publish content for each platform
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="remix", config=config)
        self.anthropic_api_key = config.get("anthropic_api_key", "")
        self.remix_types = config.get("remix_types", ["twitter", "linkedin", "email", "youtube"])
        self.max_articles_per_run = config.get("max_remixes_per_run", 3)

    def run(self) -> Dict[str, Any]:
        """Generate content remixes."""

        self.log("Starting content remix generation...")

        results = {
            "articles_processed": 0,
            "remixes_created": 0,
            "by_type": {}
        }

        # Get recently published articles that haven't been remixed yet
        with get_db() as db:
            cursor = db.execute("""
                SELECT a.id, a.title, a.slug, a.markdown_content,
                       a.target_keyword, a.target_state, a.published_url
                FROM articles a
                WHERE a.status = 'done'
                AND a.published_at IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM content_remixes cr
                    WHERE cr.source_article_id = a.id
                )
                ORDER BY a.published_at DESC
                LIMIT ?
            """, (self.max_articles_per_run,))
            articles = [dict(row) for row in cursor.fetchall()]

        if not articles:
            self.log("No articles to remix")
            return results

        self.log(f"Remixing {len(articles)} articles...")

        for article in articles:
            self.log(f"  Processing: {article['title']}")

            for remix_type in self.remix_types:
                try:
                    remix = self._generate_remix(article, remix_type)

                    if remix:
                        # Save to database
                        remix_id = log_content_remix(
                            source_article_id=article["id"],
                            remix_type=remix_type,
                            remix_content=remix["content"],
                            published_url=None  # Would be filled when actually posted
                        )

                        results["remixes_created"] += 1
                        results["by_type"][remix_type] = results["by_type"].get(remix_type, 0) + 1

                        self.log(f"    ✓ {remix_type} remix created (#{remix_id})")

                except Exception as e:
                    self.log(f"    ✗ Error creating {remix_type} remix: {e}", level="error")
                    continue

            results["articles_processed"] += 1

        # Log activity
        log_agent_action(
            agent_name=self.name,
            action="remixes_created",
            details=results
        )

        self.log(f"✓ Remix complete: {results['remixes_created']} variations created")
        return results

    def _generate_remix(self, article: Dict[str, Any], remix_type: str) -> Dict[str, Any]:
        """
        Generate a platform-specific remix of the article.

        Returns dict with content and metadata.
        """
        if remix_type == "twitter":
            return self._create_twitter_thread(article)
        elif remix_type == "linkedin":
            return self._create_linkedin_post(article)
        elif remix_type == "email":
            return self._create_email_newsletter(article)
        elif remix_type == "youtube":
            return self._create_youtube_script(article)
        else:
            return None

    def _create_twitter_thread(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Create engaging Twitter thread from article."""

        # Extract key points from article
        content = article.get("markdown_content", "")
        title = article["title"]
        url = article.get("published_url", "")

        # Use Claude to generate thread
        if not self.anthropic_api_key:
            # Fallback: simple extraction
            return self._create_simple_twitter_thread(article)

        prompt = f"""Convert this article into an engaging Twitter thread (8-10 tweets, max 280 chars each).

Article: {title}

Content summary:
{content[:2000]}

Requirements:
- Tweet 1: Hook with surprising insight or question
- Tweets 2-8: Key takeaways, one per tweet
- Tweet 9: Call to action to read full article
- Use simple language, no jargon
- Include relevant emoji (but don't overuse)
- Make each tweet standalone but connected
- Last tweet includes: "Read more: {url}"

Format as JSON array of tweet objects:
[
  {{"tweet_number": 1, "text": "..."}},
  ...
]
"""

        try:
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            thread_json = response.content[0].text

            # Extract JSON from response
            json_match = re.search(r'\[.*\]', thread_json, re.DOTALL)
            if json_match:
                thread = json.loads(json_match.group())
                return {
                    "content": json.dumps(thread, indent=2),
                    "metadata": {
                        "tweet_count": len(thread),
                        "total_chars": sum(len(t["text"]) for t in thread)
                    }
                }

        except Exception as e:
            self.log(f"Claude API error for Twitter thread: {e}", level="warning")

        # Fallback
        return self._create_simple_twitter_thread(article)

    def _create_simple_twitter_thread(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Simple Twitter thread without AI."""
        title = article["title"]
        url = article.get("published_url", "")

        thread = [
            {"tweet_number": 1, "text": f"🚗 {title}"},
            {"tweet_number": 2, "text": f"Thread 🧵 Everything you need to know..."},
            {"tweet_number": 3, "text": f"Full guide: {url}"}
        ]

        return {
            "content": json.dumps(thread, indent=2),
            "metadata": {"tweet_count": len(thread)}
        }

    def _create_linkedin_post(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Create professional LinkedIn post."""

        content = article.get("markdown_content", "")
        title = article["title"]
        url = article.get("published_url", "")

        if not self.anthropic_api_key:
            return self._create_simple_linkedin_post(article)

        prompt = f"""Convert this article into a professional LinkedIn post (max 1300 characters).

Article: {title}

Content:
{content[:2000]}

Requirements:
- Start with attention-grabbing first line
- 3-5 key insights in short paragraphs
- Professional but conversational tone
- Include 3-5 relevant hashtags at end
- Call to action: "Read the full guide: {url}"
- Use line breaks for readability

Focus on value for professionals dealing with insurance claims.
"""

        try:
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )

            post_content = response.content[0].text

            return {
                "content": post_content,
                "metadata": {
                    "char_count": len(post_content),
                    "platform": "linkedin"
                }
            }

        except Exception as e:
            self.log(f"Claude API error for LinkedIn post: {e}", level="warning")

        return self._create_simple_linkedin_post(article)

    def _create_simple_linkedin_post(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Simple LinkedIn post without AI."""
        title = article["title"]
        url = article.get("published_url", "")

        post = f"""{title}

A comprehensive guide for anyone dealing with total loss insurance claims.

Key takeaways inside ⬇️

Read the full guide: {url}

#Insurance #TotalLoss #Claims #ConsumerRights
"""

        return {
            "content": post,
            "metadata": {"char_count": len(post)}
        }

    def _create_email_newsletter(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Create email newsletter version."""

        content = article.get("markdown_content", "")
        title = article["title"]
        url = article.get("published_url", "")

        if not self.anthropic_api_key:
            return self._create_simple_email(article)

        prompt = f"""Convert this article into an engaging email newsletter.

Article: {title}

Content:
{content[:3000]}

Requirements:
- Subject line (50 chars max)
- Preheader text (90 chars max)
- Opening paragraph with hook
- 3-4 key takeaways as bullet points
- Brief explanation of each takeaway (2-3 sentences)
- Call to action button: "Read Full Article"
- Closing with helpful tip
- Friendly, helpful tone

Format as JSON:
{{
  "subject": "...",
  "preheader": "...",
  "body_html": "...",
  "cta_url": "{url}"
}}
"""

        try:
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            email_json = response.content[0].text

            # Extract JSON
            json_match = re.search(r'\{.*\}', email_json, re.DOTALL)
            if json_match:
                email_data = json.loads(json_match.group())
                return {
                    "content": json.dumps(email_data, indent=2),
                    "metadata": {
                        "subject": email_data.get("subject", ""),
                        "platform": "email"
                    }
                }

        except Exception as e:
            self.log(f"Claude API error for email: {e}", level="warning")

        return self._create_simple_email(article)

    def _create_simple_email(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Simple email without AI."""
        title = article["title"]
        url = article.get("published_url", "")

        email = {
            "subject": title[:50],
            "preheader": "New guide from ClaimCoach",
            "body_html": f"<h2>{title}</h2><p>Read our latest guide on navigating total loss claims.</p><p><a href='{url}'>Read Full Article</a></p>",
            "cta_url": url
        }

        return {
            "content": json.dumps(email, indent=2),
            "metadata": {"subject": email["subject"]}
        }

    def _create_youtube_script(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Create YouTube video script."""

        content = article.get("markdown_content", "")
        title = article["title"]
        url = article.get("published_url", "")

        if not self.anthropic_api_key:
            return self._create_simple_youtube_script(article)

        prompt = f"""Convert this article into a YouTube video script (5-7 minutes).

Article: {title}

Content:
{content[:3000]}

Requirements:
- Video title (< 60 chars, attention-grabbing)
- Hook (first 15 seconds to retain viewers)
- Introduction (30 seconds)
- 3-5 main points with explanations
- Visual suggestions in [brackets]
- Call to action
- Outro
- Conversational, engaging tone
- Include timestamp markers

Format as sections with timestamps.
"""

        try:
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}]
            )

            script = response.content[0].text

            return {
                "content": script,
                "metadata": {
                    "platform": "youtube",
                    "estimated_duration": "5-7 minutes",
                    "word_count": len(script.split())
                }
            }

        except Exception as e:
            self.log(f"Claude API error for YouTube script: {e}", level="warning")

        return self._create_simple_youtube_script(article)

    def _create_simple_youtube_script(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Simple YouTube script without AI."""
        title = article["title"]
        url = article.get("published_url", "")

        script = f"""VIDEO TITLE: {title}

[0:00] HOOK:
"What if I told you that insurance companies often underpay total loss claims? Here's what you need to know..."

[0:15] INTRO:
"Hey everyone, welcome back. Today we're breaking down {title.lower()}."

[SHOW article link on screen]

[0:45] MAIN CONTENT:
[Cover key points from article]

[5:00] CALL TO ACTION:
"For the complete guide with all the details, check out the link in the description."

Link: {url}

[5:30] OUTRO:
"Thanks for watching! Subscribe for more insurance claim tips."
"""

        return {
            "content": script,
            "metadata": {"platform": "youtube"}
        }
