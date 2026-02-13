"""
Ezra - Publisher Agent

Publishes approved articles to static files (markdown/HTML).
No CMS needed - deploys to any static host (Netlify, Vercel, GitHub Pages).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown
from jinja2 import Template

from pipeline.agents.base import BaseAgent
from pipeline.db import ArticleStatus


class EzraAgent(BaseAgent):
    """
    Publisher agent - publishes articles to static files.

    Workflow:
    1. Claims article with status='ready_to_publish'
    2. Saves markdown to blog/ directory
    3. Generates HTML from markdown
    4. Updates metadata (published_url, published_at)
    5. Updates status to 'done'
    """

    name = "ezra"
    claim_field = "publisher_claim"

    def __init__(self, config, db):
        super().__init__(config=config, db=db)
        self.blog_dir = Path(
            getattr(config.blog, "output_dir", "./blog")
        )
        self.site_url = getattr(config.blog, "site_url", "https://claimcoach.app")

        # Create blog directories
        self.blog_dir.mkdir(parents=True, exist_ok=True)
        (self.blog_dir / "posts").mkdir(exist_ok=True)
        (self.blog_dir / "html").mkdir(exist_ok=True)

    def run(self) -> dict[str, Any]:
        """Find and publish ready articles."""

        self.logger.info("Looking for articles to publish...")

        articles = self.db.query_articles(
            status=ArticleStatus.READY_TO_PUBLISH.value,
            limit=2,
        )

        if not articles:
            self.logger.info("No articles ready to publish")
            return {"published": 0, "skipped": 0, "failed": 0}

        self.logger.info(f"Found {len(articles)} articles to publish")

        results: dict[str, Any] = {
            "published": 0,
            "skipped": 0,
            "failed": 0,
            "articles": [],
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
                self.logger.error(f"Error publishing article {article.id}: {e}")
                results["failed"] += 1
                results["articles"].append({
                    "article_id": article.id,
                    "success": False,
                    "error": str(e),
                })

        return results

    def _publish_article(self, article) -> dict[str, Any]:
        """Publish single article to static files."""

        article_id = article.id

        # Claim article
        claim_id = self.generate_claim_id()
        if not self.db.try_claim(
            article_id, "publisher_claim", claim_id,
            ArticleStatus.READY_TO_PUBLISH.value,
        ):
            return {
                "article_id": article_id,
                "success": False,
                "error": "Could not claim article",
            }

        # Validate article has publishable content
        if not article.markdown_content or len(article.markdown_content) < 200:
            self.logger.error(
                f"Article {article_id} missing or too short content "
                f"({len(article.markdown_content or '')} chars), cannot publish"
            )
            self.db.update_article(
                article_id,
                publisher_claim="",
                status=ArticleStatus.REVISION.value,
                revision_notes=(article.revision_notes or "")
                + "\n\n[EZRA] Cannot publish: article content missing or too short",
            )
            return {
                "article_id": article_id,
                "success": False,
                "error": "Content missing or too short to publish",
            }

        if not article.title:
            self.logger.error(f"Article {article_id} has no title, cannot publish")
            self.db.update_article(article_id, publisher_claim="")
            return {
                "article_id": article_id,
                "success": False,
                "error": "Article has no title",
            }

        self.logger.info(f"Publishing: {article.title}")

        try:
            # Generate slug if not set
            slug = article.slug or self._slugify(article.title)

            # Save markdown file
            markdown_path = self._save_markdown(article, slug)

            # Generate CTA variants
            cta_variants = self._generate_cta_variants(article)

            # Generate HTML file with CTAs
            html_path = self._generate_html(article, slug, cta_variants)

            # Update index
            self._update_index(article, slug)

            # Determine URL
            published_url = f"{self.site_url}/blog/{slug}"

            # Update database
            self.db.update_article(
                article_id,
                slug=slug,
                published_url=published_url,
                published_at=datetime.now().isoformat(),
                status=ArticleStatus.DONE.value,
                publisher_claim="",
            )

            self.logger.info(f"Published: {published_url}")

            self.db.record_metric("ezra_publish", 1, json.dumps({
                "article_id": article_id,
                "slug": slug,
                "url": published_url,
                "markdown_path": str(markdown_path),
                "html_path": str(html_path),
            }))

            return {
                "article_id": article_id,
                "success": True,
                "slug": slug,
                "url": published_url,
                "markdown_path": str(markdown_path),
                "html_path": str(html_path),
            }

        except Exception as e:
            # Release claim on error
            self.db.update_article(article_id, publisher_claim="")
            raise

    def _save_markdown(self, article, slug: str) -> Path:
        """Save article as markdown file."""

        frontmatter = {
            "title": article.meta_title or article.title,
            "description": article.meta_description or "",
            "keyword": article.target_keyword or "",
            "state": article.target_state or "",
            "slug": slug,
            "date": datetime.now().isoformat(),
        }

        content = "---\n"
        content += json.dumps(frontmatter, indent=2)
        content += "\n---\n\n"
        content += article.markdown_content

        filepath = self.blog_dir / "posts" / f"{slug}.md"
        filepath.write_text(content, encoding="utf-8")

        self.logger.info(f"  Saved markdown: {filepath}")
        return filepath

    def _generate_cta_variants(self, article) -> list[dict[str, str]]:
        """Generate personalized CTA variants based on article content."""
        state = article.target_state or ""
        keyword = article.target_keyword or ""

        variants = []

        # Primary CTA - State-specific if available
        if state:
            variants.append({
                "type": "primary",
                "position": "sidebar",
                "heading": f"Get Your Free {state} Settlement Analysis",
                "text": f"See if your {state} total loss offer is fair in under 5 minutes.",
                "button_text": "Analyze Your Offer",
                "button_url": "https://claimcoach.app",
            })
        else:
            variants.append({
                "type": "primary",
                "position": "sidebar",
                "heading": "Get Your Free Settlement Analysis",
                "text": "Find out if your total loss offer is fair in under 5 minutes.",
                "button_text": "Analyze Your Offer",
                "button_url": "https://claimcoach.app",
            })

        # Secondary CTA - Content-aware
        if "settlement" in keyword.lower():
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": "Not Sure If Your Offer Is Fair?",
                "text": "Our free calculator compares your offer to actual market values.",
                "button_text": "Check Your Settlement",
                "button_url": "https://claimcoach.app/calculator",
            })
        elif "total loss" in keyword.lower():
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": "Declared a Total Loss?",
                "text": "Get a detailed breakdown of what your vehicle is actually worth.",
                "button_text": "Get Your Valuation",
                "button_url": "https://claimcoach.app",
            })
        else:
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": "Questions About Your Claim?",
                "text": "Our free tool analyzes your settlement and shows what you may be missing.",
                "button_text": "Check Your Settlement",
                "button_url": "https://claimcoach.app",
            })

        # Bottom CTA - Newsletter signup
        variants.append({
            "type": "newsletter",
            "position": "bottom",
            "heading": "Insurance Tips in Your Inbox",
            "text": "Get weekly tips on navigating total loss claims and maximizing settlements.",
            "button_text": "Subscribe Free",
            "button_url": "https://claimcoach.app/newsletter",
        })

        # Context-aware CTA — identify the high-intent moment and inject
        # a CTA that matches the reader's peak frustration point
        context_cta = self._generate_context_aware_cta(article)
        if context_cta:
            variants.append(context_cta)

        return variants

    def _generate_context_aware_cta(self, article) -> dict[str, str] | None:
        """Use Flash-Lite to find the high-intent moment and create a matching CTA.

        Identifies where the reader feels most frustrated with their insurance
        company and generates a CTA that speaks directly to that emotion,
        linking to the most relevant ClaimCoach feature.

        Returns a CTA dict or None if generation fails.
        """
        if not self.has_llm:
            return None

        content = article.markdown_content or ""
        if len(content) < 500:
            return None

        keyword = article.target_keyword or ""

        prompt = f"""You are a conversion rate optimization expert for ClaimCoach (claimcoach.app),
an AI tool that analyzes total loss insurance settlements.

Read this article about "{keyword}" and identify the HIGH-INTENT MOMENT — the paragraph
where the reader feels MOST frustrated with their insurance company and most likely to take action.

Then create a context-aware CTA that:
1. Mirrors the specific frustration in that paragraph
2. Offers ClaimCoach as the immediate next step
3. Is 1 sentence for the heading, 1-2 sentences for the body text

ClaimCoach features you can link to:
- https://claimcoach.app — main settlement analyzer
- https://claimcoach.app/calculator — free settlement calculator

Respond in EXACTLY this format:
INSERT_AFTER: [quote the H2 heading this CTA should appear after]
HEADING: [CTA heading, max 10 words]
TEXT: [CTA body, 1-2 sentences, max 40 words]
BUTTON: [button text, 2-4 words]
URL: [one of the URLs above]

Article:
---
{content[:3000]}
---"""

        try:
            result = self.call_claude(
                prompt=prompt,
                system="You are a CRO expert. Return only the formatted CTA.",
                model=self.utility_model,
                max_tokens=300,
            )

            # Parse the structured response
            cta: dict[str, str] = {"type": "context_aware", "position": "inline"}
            for line in result.strip().split("\n"):
                line = line.strip()
                if line.startswith("INSERT_AFTER:"):
                    cta["insert_after"] = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("HEADING:"):
                    cta["heading"] = line.split(":", 1)[1].strip()
                elif line.startswith("TEXT:"):
                    cta["text"] = line.split(":", 1)[1].strip()
                elif line.startswith("BUTTON:"):
                    cta["button_text"] = line.split(":", 1)[1].strip()
                elif line.startswith("URL:"):
                    url = line.split(":", 1)[1].strip()
                    # Only allow claimcoach.app URLs
                    if "claimcoach.app" in url:
                        cta["button_url"] = url
                    else:
                        cta["button_url"] = "https://claimcoach.app"

            # Validate all required fields are present
            required = {"heading", "text", "button_text", "button_url"}
            if required.issubset(cta.keys()):
                self.logger.info(
                    f"Generated context-aware CTA: '{cta['heading']}' "
                    f"after '{cta.get('insert_after', 'unknown')}'"
                )
                return cta

            self.logger.debug("Context-aware CTA missing required fields, skipping")
            return None

        except Exception as e:
            self.logger.warning(f"Context-aware CTA generation failed: {e}")
            return None

    def _generate_html(self, article, slug: str, cta_variants: list[dict[str, str]]) -> Path:
        """Generate HTML from markdown."""

        html_content = markdown.markdown(
            article.markdown_content,
            extensions=['extra', 'codehilite', 'toc', 'fenced_code'],
        )

        primary_cta = next((c for c in cta_variants if c["type"] == "primary"), None)
        secondary_cta = next((c for c in cta_variants if c["type"] == "secondary"), None)
        newsletter_cta = next((c for c in cta_variants if c["type"] == "newsletter"), None)
        context_cta = next((c for c in cta_variants if c["type"] == "context_aware"), None)

        # Inject context-aware CTA into the HTML content at the right position
        if context_cta and context_cta.get("insert_after"):
            insert_heading = context_cta["insert_after"]
            cta_html_block = (
                f'<div class="cta cta-context-aware">'
                f'<h3>{context_cta["heading"]}</h3>'
                f'<p>{context_cta["text"]}</p>'
                f'<a href="{context_cta["button_url"]}" class="button button-context">'
                f'{context_cta["button_text"]}</a></div>'
            )
            # Try to insert after the matching H2 section
            # Look for the heading in the rendered HTML
            import re as _re
            pattern = _re.compile(
                rf'(<h2[^>]*>.*?{_re.escape(insert_heading[:30])}.*?</h2>)',
                _re.IGNORECASE | _re.DOTALL,
            )
            match = pattern.search(html_content)
            if match:
                # Find the next H2 or end of content to insert before
                next_h2 = _re.search(r'<h2', html_content[match.end():])
                if next_h2:
                    insert_pos = match.end() + next_h2.start()
                    html_content = (
                        html_content[:insert_pos]
                        + cta_html_block
                        + html_content[insert_pos:]
                    )
                else:
                    html_content += cta_html_block

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

            {% if secondary_cta %}
            <div class="cta cta-inline">
                <h3>{{ secondary_cta.heading }}</h3>
                <p>{{ secondary_cta.text }}</p>
                <a href="{{ secondary_cta.button_url }}" class="button">{{ secondary_cta.button_text }}</a>
            </div>
            {% endif %}
        </article>

        <aside>
            {% if primary_cta %}
            <div class="cta cta-sidebar">
                <h3>{{ primary_cta.heading }}</h3>
                <p>{{ primary_cta.text }}</p>
                <a href="{{ primary_cta.button_url }}" class="button button-primary">{{ primary_cta.button_text }}</a>
            </div>
            {% endif %}
        </aside>
    </main>

    {% if newsletter_cta %}
    <section class="cta cta-newsletter">
        <div class="container">
            <h2>{{ newsletter_cta.heading }}</h2>
            <p>{{ newsletter_cta.text }}</p>
            <a href="{{ newsletter_cta.button_url }}" class="button">{{ newsletter_cta.button_text }}</a>
        </div>
    </section>
    {% endif %}

    <footer>
        <p>&copy; {{ year }} ClaimCoach. All rights reserved.</p>
    </footer>
</body>
</html>
""")

        html = template.render(
            meta_title=article.meta_title or article.title,
            meta_description=article.meta_description or "",
            target_keyword=article.target_keyword or "",
            title=article.title,
            content=html_content,
            primary_cta=primary_cta,
            secondary_cta=secondary_cta,
            newsletter_cta=newsletter_cta,
            year=datetime.now().year,
        )

        filepath = self.blog_dir / "html" / f"{slug}.html"
        filepath.write_text(html, encoding="utf-8")

        self.logger.info(f"  Generated HTML: {filepath}")
        return filepath

    def _update_index(self, article, slug: str):
        """Update blog index with new article."""

        index_path = self.blog_dir / "index.json"

        if index_path.exists():
            index = json.loads(index_path.read_text())
        else:
            index = {"articles": []}

        index["articles"].insert(0, {
            "title": article.meta_title or article.title,
            "description": article.meta_description or "",
            "slug": slug,
            "url": f"/blog/{slug}",
            "keyword": article.target_keyword or "",
            "state": article.target_state or "",
            "published_at": datetime.now().isoformat(),
        })

        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

        self.logger.info(f"  Updated index: {index_path}")

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to URL-friendly slug.

        Truncates to 80 chars — aligned with Quill's _generate_slug to
        prevent slug mismatches that break internal links.
        """
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text)
        text = text.strip('-')
        return text[:80]
