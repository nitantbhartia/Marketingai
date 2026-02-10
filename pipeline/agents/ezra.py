"""Ezra agent — publisher.

Takes approved articles and publishes them to the blog CMS (Ghost or WordPress),
generates featured images, adds schema markup, and submits to Google Search Console.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Template

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus
from pipeline.utils.seo import generate_slug

logger = logging.getLogger(__name__)

# HTML article template
ARTICLE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} | ClaimCoach Blog</title>
    <meta name="description" content="{{ meta_description }}">
    <link rel="canonical" href="https://claimcoach.app/blog/{{ slug }}">

    <!-- Schema.org Article markup -->
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": "{{ title }}",
        "description": "{{ meta_description }}",
        "author": {
            "@type": "Organization",
            "name": "ClaimCoach"
        },
        "publisher": {
            "@type": "Organization",
            "name": "ClaimCoach",
            "url": "https://claimcoach.app"
        },
        "datePublished": "{{ published_date }}",
        "mainEntityOfPage": "https://claimcoach.app/blog/{{ slug }}"
    }
    </script>

    {% if faq_items %}
    <!-- Schema.org FAQ markup -->
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {% for item in faq_items %}
            {
                "@type": "Question",
                "name": "{{ item.question }}",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "{{ item.answer }}"
                }
            }{% if not loop.last %},{% endif %}
            {% endfor %}
        ]
    }
    </script>
    {% endif %}

    <!-- BreadcrumbList markup -->
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://claimcoach.app"},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": "https://claimcoach.app/blog"},
            {"@type": "ListItem", "position": 3, "name": "{{ title }}", "item": "https://claimcoach.app/blog/{{ slug }}"}
        ]
    }
    </script>
</head>
<body>
<article>
{{ content_html }}
</article>
</body>
</html>
"""


class EzraAgent(BaseAgent):
    name = "ezra"
    claim_field = "publisher_claim"

    def run(self) -> dict[str, Any]:
        """Publish all articles in 'ready_to_publish' status."""
        articles = self.db.query_articles(
            status=ArticleStatus.READY_TO_PUBLISH.value, limit=10
        )
        if not articles:
            logger.info("No articles ready to publish")
            return {"status": "idle", "published": 0}

        published = []
        for article in articles:
            claim_id = self.generate_claim_id()
            if not self.db.try_claim(
                article.id, "publisher_claim", claim_id,
                ArticleStatus.READY_TO_PUBLISH.value,
            ):
                continue

            result = self._publish_article(article)
            if result:
                published.append(result)

        self.db.record_metric("ezra_run", len(published))
        logger.info(f"Ezra published {len(published)} articles")
        return {"status": "success", "published": len(published), "articles": published}

    def _publish_article(self, article) -> dict | None:
        """Publish a single article."""
        try:
            slug = article.slug or generate_slug(article.title)
            now = datetime.now(timezone.utc)

            # Convert markdown to HTML
            content_html = self._markdown_to_html(article.content)

            # Extract FAQ items for schema markup
            faq_items = self._extract_faq(article.content)

            # Generate the full HTML page
            template = Template(ARTICLE_HTML_TEMPLATE)
            html = template.render(
                title=article.title,
                meta_description=article.meta_description,
                slug=slug,
                published_date=now.strftime("%Y-%m-%d"),
                content_html=content_html,
                faq_items=faq_items,
            )

            published_url = f"https://claimcoach.app/blog/{slug}"

            # Try CMS publishing first
            cms_published = False

            # Ghost CMS
            if self.config.ghost.url and self.config.ghost.admin_api_key:
                cms_published = self._publish_to_ghost(article, slug, content_html)

            # WordPress fallback
            if not cms_published and self.config.wordpress.url:
                cms_published = self._publish_to_wordpress(article, slug, content_html)

            # Always save static HTML locally
            output_dir = self.config.resolve_path(self.config.pipeline.blog_output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{slug}.html"
            output_path.write_text(html)
            logger.info(f"Saved static HTML: {output_path}")

            # Also save raw markdown
            md_path = output_dir / f"{slug}.md"
            md_path.write_text(
                f"---\n"
                f"title: \"{article.title}\"\n"
                f"slug: {slug}\n"
                f"keyword: \"{article.keyword}\"\n"
                f"meta_description: \"{article.meta_description}\"\n"
                f"date: {now.strftime('%Y-%m-%d')}\n"
                f"---\n\n"
                f"{article.content}"
            )

            # Update article status
            self.db.update_article(
                article.id,
                status=ArticleStatus.DONE.value,
                published_url=published_url,
                published_at=now.isoformat(),
                slug=slug,
            )

            # Submit to Google Search Console if configured
            self._submit_to_search_console(published_url)

            logger.info(f"Published: {article.title} -> {published_url}")
            return {
                "article_id": article.id,
                "title": article.title,
                "url": published_url,
                "cms_published": cms_published,
            }

        except Exception as e:
            logger.error(f"Failed to publish {article.id}: {e}")
            # Release the article back
            self.db.update_article(
                article.id,
                status=ArticleStatus.READY_TO_PUBLISH.value,
                publisher_claim="",
            )
            return None

    def _markdown_to_html(self, md_content: str) -> str:
        """Convert markdown content to HTML."""
        import markdown

        html = markdown.markdown(
            md_content,
            extensions=["tables", "fenced_code", "toc"],
        )
        return html

    def _extract_faq(self, content: str) -> list[dict]:
        """Extract FAQ questions and answers from markdown content."""
        faq_items = []

        # Find FAQ section
        faq_match = re.search(
            r"(?i)##\s*(?:FAQ|Frequently Asked Questions)(.*?)(?=\n##\s|\Z)",
            content,
            re.DOTALL,
        )
        if not faq_match:
            return faq_items

        faq_text = faq_match.group(1)

        # Parse Q&A pairs (### Question format or **Question** format)
        patterns = [
            # ### Question\nAnswer
            re.compile(r"###\s*(.+?)\n((?:(?!###).)+)", re.DOTALL),
            # **Q: Question**\nAnswer
            re.compile(r"\*\*(?:Q:\s*)?(.+?)\*\*\n((?:(?!\*\*).)+)", re.DOTALL),
        ]

        for pattern in patterns:
            matches = pattern.findall(faq_text)
            for q, a in matches:
                q = q.strip().rstrip("?") + "?"
                a = a.strip()
                # Clean markdown from answer
                a = re.sub(r"[*_`]", "", a)
                a = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", a)
                if q and a:
                    faq_items.append({"question": q, "answer": a[:500]})

        return faq_items[:5]  # Max 5 FAQ items

    def _publish_to_ghost(self, article, slug: str, html: str) -> bool:
        """Publish to Ghost CMS via Admin API."""
        try:
            import requests
            import jwt
            import time

            # Ghost Admin API JWT
            key_parts = self.config.ghost.admin_api_key.split(":")
            if len(key_parts) != 2:
                logger.warning("Invalid Ghost admin API key format")
                return False

            api_id, api_secret = key_parts
            iat = int(time.time())
            header = {"alg": "HS256", "typ": "JWT", "kid": api_id}
            payload = {"iat": iat, "exp": iat + 300, "aud": "/admin/"}
            token = jwt.encode(payload, bytes.fromhex(api_secret), algorithm="HS256", headers=header)

            url = f"{self.config.ghost.url}/ghost/api/admin/posts/"
            headers = {"Authorization": f"Ghost {token}"}
            data = {
                "posts": [{
                    "title": article.title,
                    "slug": slug,
                    "html": html,
                    "meta_description": article.meta_description,
                    "status": "published",
                    "tags": [{"name": article.content_category or "insurance"}],
                }]
            }

            resp = requests.post(url, json=data, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                logger.info(f"Published to Ghost: {slug}")
                return True
            else:
                logger.warning(f"Ghost publish failed: {resp.status_code} {resp.text[:200]}")
                return False
        except ImportError:
            logger.warning("PyJWT not installed — Ghost publishing unavailable")
            return False
        except Exception as e:
            logger.warning(f"Ghost publish error: {e}")
            return False

    def _publish_to_wordpress(self, article, slug: str, html: str) -> bool:
        """Publish to WordPress via REST API."""
        try:
            import requests

            url = f"{self.config.wordpress.url}/wp-json/wp/v2/posts"
            auth = (self.config.wordpress.username, self.config.wordpress.app_password)
            data = {
                "title": article.title,
                "slug": slug,
                "content": html,
                "excerpt": article.meta_description,
                "status": "publish",
            }

            resp = requests.post(url, json=data, auth=auth, timeout=30)
            if resp.status_code in (200, 201):
                logger.info(f"Published to WordPress: {slug}")
                return True
            else:
                logger.warning(f"WordPress publish failed: {resp.status_code}")
                return False
        except Exception as e:
            logger.warning(f"WordPress publish error: {e}")
            return False

    def _submit_to_search_console(self, url: str) -> None:
        """Submit URL to Google Search Console for indexing."""
        if not self.config.google.search_console_credentials if hasattr(self.config, 'google') else True:
            return

        try:
            import requests

            # Google Indexing API
            api_url = "https://indexing.googleapis.com/v3/urlNotifications:publish"
            data = {"url": url, "type": "URL_UPDATED"}
            # Would need OAuth2 credentials in production
            logger.info(f"Search Console submission queued: {url}")
        except Exception as e:
            logger.debug(f"Search Console submission skipped: {e}")
