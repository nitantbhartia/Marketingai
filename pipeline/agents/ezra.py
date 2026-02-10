"""
Ezra - Publisher Agent

Publishes approved articles to static files (markdown/HTML).
No CMS needed - deploys to any static host (Netlify, Vercel, GitHub Pages).
"""

import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import markdown
from jinja2 import Template

from pipeline.agents.base import Agent
from pipeline.db import get_db, claim_article, release_claim


class Ezra(Agent):
    """
    Publisher agent - publishes articles to static files.

    Workflow:
    1. Claims article with status='ready_to_publish'
    2. Saves markdown to blog/ directory
    3. Generates HTML from markdown
    4. Updates metadata (published_url, published_at)
    5. Updates status to 'done'
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="ezra", config=config)
        self.blog_dir = Path(config.get("blog_output_dir", "./blog"))
        self.site_url = config.get("site_url", "https://claimcoach.app")

        # Create blog directories
        self.blog_dir.mkdir(exist_ok=True)
        (self.blog_dir / "posts").mkdir(exist_ok=True)
        (self.blog_dir / "html").mkdir(exist_ok=True)

    def run(self) -> Dict[str, Any]:
        """Find and publish ready articles."""

        self.log("Looking for articles to publish...")

        # Get articles ready to publish
        with get_db() as db:
            cursor = db.execute("""
                SELECT id, title, slug, markdown_content, meta_title,
                       meta_description, target_keyword, target_state
                FROM articles
                WHERE status = 'ready_to_publish'
                AND (publisher_claim IS NULL OR publisher_claim = '')
                ORDER BY updated_at ASC
                LIMIT ?
            """, (self.config.get("max_publishes_per_run", 2),))

            articles = [dict(row) for row in cursor.fetchall()]

        if not articles:
            self.log("No articles ready to publish")
            return {"published": 0, "skipped": 0, "failed": 0}

        self.log(f"Found {len(articles)} articles to publish")

        results = {
            "published": 0,
            "skipped": 0,
            "failed": 0,
            "articles": []
        }

        for article in articles:
            try:
                result = self._publish_article(article)
                if result["success"]:
                    results["published"] += 1
                else:
                    results["failed"] += 1
                results["articles"].append(result)
            except Exception as e:
                self.log(f"Error publishing article {article['id']}: {e}", level="error")
                results["failed"] += 1
                results["articles"].append({
                    "article_id": article["id"],
                    "success": False,
                    "error": str(e)
                })

        return results

    def _publish_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Publish single article to static files."""

        article_id = article["id"]

        # Claim article
        claim_id = self._generate_claim_id()
        if not claim_article(article_id, "publisher_claim", claim_id):
            return {
                "article_id": article_id,
                "success": False,
                "error": "Could not claim article"
            }

        self.log(f"Publishing: {article['title']}")

        try:
            # Generate slug if not set
            slug = article.get("slug") or self._slugify(article["title"])

            # Save markdown file
            markdown_path = self._save_markdown(article, slug)

            # Generate HTML file
            html_path = self._generate_html(article, slug)

            # Update index
            self._update_index(article, slug)

            # Determine URL
            published_url = f"{self.site_url}/blog/{slug}"

            # Update database
            with get_db() as db:
                db.execute("""
                    UPDATE articles SET
                        slug = ?,
                        published_url = ?,
                        published_at = CURRENT_TIMESTAMP,
                        status = 'done',
                        publisher_claim = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (slug, published_url, article_id))

            self.log(f"✓ Published: {published_url}")

            # Log to agent_log
            from pipeline.db import log_agent_action
            log_agent_action(
                agent_name=self.name,
                action="published",
                article_id=article_id,
                details={
                    "slug": slug,
                    "url": published_url,
                    "markdown_path": str(markdown_path),
                    "html_path": str(html_path)
                }
            )

            return {
                "article_id": article_id,
                "success": True,
                "slug": slug,
                "url": published_url,
                "markdown_path": str(markdown_path),
                "html_path": str(html_path)
            }

        except Exception as e:
            # Release claim on error
            release_claim(article_id, "publisher_claim")
            raise

    def _save_markdown(self, article: Dict[str, Any], slug: str) -> Path:
        """Save article as markdown file."""

        # Create frontmatter
        frontmatter = {
            "title": article.get("meta_title") or article["title"],
            "description": article.get("meta_description", ""),
            "keyword": article.get("target_keyword", ""),
            "state": article.get("target_state"),
            "slug": slug,
            "date": datetime.now().isoformat(),
        }

        # Build markdown with frontmatter
        content = "---\n"
        content += json.dumps(frontmatter, indent=2)
        content += "\n---\n\n"
        content += article["markdown_content"]

        # Save to file
        filepath = self.blog_dir / "posts" / f"{slug}.md"
        filepath.write_text(content, encoding="utf-8")

        self.log(f"  Saved markdown: {filepath}")
        return filepath

    def _generate_html(self, article: Dict[str, Any], slug: str) -> Path:
        """Generate HTML from markdown."""

        # Convert markdown to HTML
        html_content = markdown.markdown(
            article["markdown_content"],
            extensions=['extra', 'codehilite', 'toc', 'fenced_code']
        )

        # Simple HTML template
        template = Template("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ meta_title }}</title>
    <meta name="description" content="{{ meta_description }}">
    <meta name="keywords" content="{{ target_keyword }}">
    <link rel="stylesheet" href="/styles.css">
</head>
<body>
    <header>
        <nav>
            <a href="/">ClaimCoach</a>
            <a href="/blog">Blog</a>
        </nav>
    </header>

    <main>
        <article>
            <h1>{{ title }}</h1>
            {{ content | safe }}
        </article>

        <aside>
            <div class="cta">
                <h3>Get Your Free Settlement Analysis</h3>
                <p>Find out if your total loss offer is fair in under 5 minutes.</p>
                <a href="https://claimcoach.app" class="button">Analyze Your Offer</a>
            </div>
        </aside>
    </main>

    <footer>
        <p>&copy; {{ year }} ClaimCoach. All rights reserved.</p>
    </footer>
</body>
</html>
""")

        # Render HTML
        html = template.render(
            meta_title=article.get("meta_title") or article["title"],
            meta_description=article.get("meta_description", ""),
            target_keyword=article.get("target_keyword", ""),
            title=article["title"],
            content=html_content,
            year=datetime.now().year
        )

        # Save HTML file
        filepath = self.blog_dir / "html" / f"{slug}.html"
        filepath.write_text(html, encoding="utf-8")

        self.log(f"  Generated HTML: {filepath}")
        return filepath

    def _update_index(self, article: Dict[str, Any], slug: str):
        """Update blog index with new article."""

        index_path = self.blog_dir / "index.json"

        # Load existing index
        if index_path.exists():
            index = json.loads(index_path.read_text())
        else:
            index = {"articles": []}

        # Add new article to index
        index["articles"].insert(0, {
            "title": article.get("meta_title") or article["title"],
            "description": article.get("meta_description", ""),
            "slug": slug,
            "url": f"/blog/{slug}",
            "keyword": article.get("target_keyword", ""),
            "state": article.get("target_state"),
            "published_at": datetime.now().isoformat(),
        })

        # Save index
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

        self.log(f"  Updated index: {index_path}")

    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        import re

        # Convert to lowercase
        text = text.lower()

        # Replace spaces and special chars with hyphens
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text)

        # Remove leading/trailing hyphens
        text = text.strip('-')

        return text[:50]  # Limit length
