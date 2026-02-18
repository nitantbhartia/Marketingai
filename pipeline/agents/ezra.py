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
from pipeline.utils.factual_claims import evaluate_factual_claims
from pipeline.utils.images import ImageResolver


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

        # Image resolver — converts image:slug placeholders to real URLs
        img_cfg = getattr(config, "images", None)
        if img_cfg and getattr(img_cfg, "enabled", True):
            self._image_resolver = ImageResolver(
                unsplash_key=getattr(img_cfg, "unsplash_access_key", ""),
                image_width=getattr(img_cfg, "width", 1200),
                image_height=getattr(img_cfg, "height", 630),
                cache_dir=getattr(img_cfg, "cache_dir", ".image_cache"),
            )
        else:
            self._image_resolver = None

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

    @staticmethod
    def _product_info(article) -> dict[str, str]:
        product = (getattr(article, "product", "") or "claimcoach").strip().lower()
        if product == "medbill":
            return {
                "product": "medbill",
                "brand": "BillScan",
                "site_url": "https://billkarma.app",
                "tools_url": "https://billkarma.app/tools",
            }
        return {
            "product": "claimcoach",
            "brand": "ClaimCoach",
            "site_url": "https://claimcoach.app",
            "tools_url": "https://claimcoach.app/tools",
        }

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

        # Hard factual gate: do not publish when unsupported factual claims
        # (numeric/legal/timeline) are missing source citations.
        claim_gate = evaluate_factual_claims(article.markdown_content or "")
        if claim_gate.blocking:
            unsupported_examples = [
                f"- [{c.claim_type}] {c.claim[:180]}"
                for c in claim_gate.checks
                if c.confidence == "unsupported"
            ][:8]
            note = (
                "[EZRA FACTUAL GATE] Publish blocked: "
                f"{claim_gate.unsupported} unsupported factual claim(s). "
                "Add source citations for numeric, legal, and timeline claims.\n"
                + "\n".join(unsupported_examples)
            )
            self.db.update_article(
                article_id,
                publisher_claim="",
                status=ArticleStatus.REVISION.value,
                revision_notes=(article.revision_notes or "") + "\n\n" + note,
                validation_status="FAIL",
            )
            return {
                "article_id": article_id,
                "success": False,
                "error": f"Factual gate failed ({claim_gate.unsupported} unsupported claims)",
            }

        self.logger.info(f"Publishing: {article.title}")

        try:
            # Generate slug if not set — ensure uniqueness
            slug = article.slug or self._slugify(article.title)
            slug = self._ensure_unique_slug(slug)

            # Resolve image placeholders to real URLs before publishing
            article = self._resolve_images(article)

            # Reject if unresolved image placeholders remain
            remaining_placeholders = len(
                re.findall(r"!\[[^\]]*\]\(image:[^)]+\)", article.markdown_content or "")
            )
            if remaining_placeholders > 0:
                self.logger.warning(
                    f"  {remaining_placeholders} unresolved image placeholder(s) — "
                    f"sending back to revision"
                )
                self.db.update_article(
                    article_id,
                    status=ArticleStatus.REVISION.value,
                    publisher_claim="",
                    revision_notes=(
                        f"[EZRA] {remaining_placeholders} image placeholder(s) "
                        f"could not be resolved. Remove or replace them."
                    ),
                )
                return {
                    "article_id": article_id,
                    "success": False,
                    "error": f"{remaining_placeholders} unresolved image placeholders",
                }

            if getattr(self.config.pipeline, "ezra_strict_publish_gate", True):
                gate_errors = self._strict_publish_gate(article)
                if gate_errors:
                    self.db.update_article(
                        article_id,
                        status=ArticleStatus.REVISION.value,
                        publisher_claim="",
                        revision_notes=(
                            (article.revision_notes or "")
                            + "\n\n[EZRA STRICT GATE] Publish blocked:\n- "
                            + "\n- ".join(gate_errors[:12])
                        ),
                        validation_status="FAIL",
                    )
                    return {
                        "article_id": article_id,
                        "success": False,
                        "error": "Strict publish gate failed",
                        "gate_errors": gate_errors[:12],
                    }

            # Save markdown file
            markdown_path = self._save_markdown(article, slug)

            # Generate CTA variants
            cta_variants = self._generate_cta_variants(article)
            self._persist_cta_variants(article_id, cta_variants)

            # Generate HTML file with CTAs
            html_path = self._generate_html(article, slug, cta_variants)

            # Update index
            self._update_index(article, slug)

            # Determine URL (normalize trailing slash)
            product_info = self._product_info(article)
            published_url = f"{product_info['site_url'].rstrip('/')}/blog/{slug}"

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

    def _resolve_images(self, article):
        """Replace image:slug placeholders with real image URLs.

        Runs before publishing so the final markdown/HTML contains real
        <img> tags.  Falls back gracefully — if resolution fails for any
        image, the placeholder stays and renders as alt text.
        """
        if not self._image_resolver:
            return article

        content = article.markdown_content or ""
        keyword = article.target_keyword or ""

        # Count placeholders before resolution
        placeholder_count = len(re.findall(r"!\[[^\]]*\]\(image:[^)]+\)", content))
        if placeholder_count == 0:
            return article

        self.logger.info(
            f"  Resolving {placeholder_count} image placeholder(s)..."
        )

        resolved_content = self._image_resolver.resolve_all(content, keyword)

        # Count how many were actually resolved
        remaining = len(re.findall(r"!\[[^\]]*\]\(image:[^)]+\)", resolved_content))
        resolved_count = placeholder_count - remaining

        if resolved_count > 0:
            self.logger.info(
                f"  Resolved {resolved_count}/{placeholder_count} images"
            )
            # Update the article's content (create a copy to avoid mutating the DB object)
            from dataclasses import replace
            article = replace(article, markdown_content=resolved_content)

        return article

    def _strict_publish_gate(self, article) -> list[str]:
        """Hard quality gate before publishing."""
        errors: list[str] = []
        text = article.markdown_content or ""
        lower = text.lower()
        word_count = len(text.split())

        # 1) Minimum depth
        if word_count < 900:
            errors.append(f"Article too short for publish gate ({word_count} words, min 900)")

        # 2) FAQ + schema requirements
        has_faq = ("## faq" in lower) or ("frequently asked questions" in lower)
        if not has_faq:
            errors.append("Missing FAQ section")
        if '"@type": "Article"' not in text and '"@type":"Article"' not in text:
            errors.append("Missing Article JSON-LD schema")
        if has_faq and "FAQPage" not in text:
            errors.append("FAQ exists but FAQPage schema missing")

        # 3) Citation density for factual claims
        citation_links = re.findall(r"\[[^\]]+\]\(https?://[^)]+\)", text)
        if len(citation_links) < 3:
            errors.append("Insufficient inline citations (<3 source links)")

        numeric_claims = len(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))
        if numeric_claims >= 8 and len(citation_links) < 5:
            errors.append("Numeric claim density high but citation count too low")

        # 4) Product/domain consistency
        info = self._product_info(article)
        wrong_domain = "billkarma.app" if info["product"] == "claimcoach" else "claimcoach.app"
        if wrong_domain in lower:
            errors.append(f"Cross-product domain leak detected ({wrong_domain})")

        # 5) Require tools for calculator/threshold intent
        tool_required_terms = (
            "calculator", "threshold", "estimate", "fair", "value", "settlement",
            "anesthesia", "itemized", "dispute",
        )
        kw = (article.target_keyword or "").lower()
        tool_needed = any(t in kw for t in tool_required_terms)
        has_tool_embed = ("<!-- TOOL:" in text) or ('data-cc-tool="' in text)
        if tool_needed and not has_tool_embed:
            errors.append("Tool embed required for this keyword intent")

        return errors

    def _persist_cta_variants(self, article_id: int, variants: list[dict[str, str]]) -> None:
        """Persist generated CTA variants for experimentation tracking."""
        if not variants:
            return
        try:
            with self.db._connect() as conn:
                for cta in variants:
                    text = f"{cta.get('heading', '').strip()} | {cta.get('text', '').strip()}".strip(" |")
                    if not text:
                        continue
                    conn.execute(
                        """
                        INSERT INTO cta_variants (article_id, cta_text, cta_type, position, is_active)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (
                            article_id,
                            text[:700],
                            cta.get("type", "unknown"),
                            cta.get("position", "unknown"),
                        ),
                    )
        except Exception:
            # Non-blocking: CTA tracking should not fail publishing.
            self.logger.debug("Skipping CTA variant persistence", exc_info=True)

    def _best_historical_cta(self, product: str, cta_type: str) -> dict[str, str] | None:
        """Fetch best historical CTA copy for a product/type to bias A/B generation."""
        try:
            with self.db._connect() as conn:
                row = conn.execute(
                    """
                    SELECT cta_text, impressions, clicks, conversions
                    FROM cta_variants v
                    JOIN articles a ON a.id = v.article_id
                    WHERE COALESCE(a.product, 'claimcoach') = ?
                      AND v.cta_type = ?
                      AND v.impressions >= 20
                    ORDER BY
                      (CASE WHEN v.clicks > 0 THEN CAST(v.conversions AS REAL) / v.clicks ELSE 0 END) DESC,
                      (CASE WHEN v.impressions > 0 THEN CAST(v.clicks AS REAL) / v.impressions ELSE 0 END) DESC,
                      v.clicks DESC
                    LIMIT 1
                    """,
                    (product, cta_type),
                ).fetchone()
            if not row:
                return None
            parts = (row["cta_text"] or "").split("|", 1)
            heading = parts[0].strip() if parts else ""
            body = parts[1].strip() if len(parts) > 1 else ""
            return {"heading": heading, "text": body}
        except Exception:
            return None

    def _save_markdown(self, article, slug: str) -> Path:
        """Save article as markdown file."""

        frontmatter = {
            "title": article.meta_title or article.title,
            "description": article.meta_description or "",
            "keyword": article.target_keyword or "",
            "state": article.target_state or "",
            "product": (article.product or "claimcoach"),
            "intent_template": getattr(article, "intent_template", "") or "",
            "cluster_key": getattr(article, "cluster_key", "") or "",
            "canonical_target": getattr(article, "canonical_url", "") or "",
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
        info = self._product_info(article)
        product = info["product"]
        brand = info["brand"]
        site_url = info["site_url"]
        tools_url = info["tools_url"]

        variants = []

        # Primary CTA - State-specific if available
        winner_primary = self._best_historical_cta(product, "primary")
        if state:
            variants.append({
                "type": "primary",
                "position": "sidebar",
                "heading": winner_primary.get("heading") if winner_primary else f"Get Your Free {state} Analysis",
                "text": winner_primary.get("text") if winner_primary else f"See if your {state} {keyword} offer is fair in under 5 minutes.",
                "button_text": "Analyze Now",
                "button_url": site_url,
            })
        else:
            variants.append({
                "type": "primary",
                "position": "sidebar",
                "heading": winner_primary.get("heading") if winner_primary else f"Get Your Free {brand} Analysis",
                "text": winner_primary.get("text") if winner_primary else "Find missed line items and next-best dispute actions in minutes.",
                "button_text": "Analyze Now",
                "button_url": site_url,
            })

        # Secondary CTA - Content-aware
        winner_secondary = self._best_historical_cta(product, "secondary")
        if "settlement" in keyword.lower():
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": winner_secondary.get("heading") if winner_secondary else "Not Sure If Your Offer Is Fair?",
                "text": winner_secondary.get("text") if winner_secondary else "Use a fast benchmark check before accepting anything.",
                "button_text": "Check Your Settlement",
                "button_url": tools_url,
            })
        elif "total loss" in keyword.lower():
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": winner_secondary.get("heading") if winner_secondary else "Declared a Total Loss?",
                "text": winner_secondary.get("text") if winner_secondary else "Get a structured valuation review before you negotiate.",
                "button_text": "Check Valuation",
                "button_url": site_url,
            })
        else:
            variants.append({
                "type": "secondary",
                "position": "inline",
                "heading": winner_secondary.get("heading") if winner_secondary else "Need a Faster Answer?",
                "text": winner_secondary.get("text") if winner_secondary else "Run a guided check to find errors and best next steps.",
                "button_text": "Run Free Check",
                "button_url": site_url,
            })

        # Bottom CTA - Newsletter signup
        variants.append({
            "type": "newsletter",
            "position": "bottom",
            "heading": f"{brand} Tips in Your Inbox",
            "text": "Get practical weekly guidance and dispute playbooks.",
            "button_text": "Subscribe Free",
            "button_url": f"{site_url}/newsletter",
        })

        # Context-aware CTA — identify the high-intent moment and inject
        # a CTA that matches the reader's peak frustration point
        context_cta = self._generate_context_aware_cta(article)
        if context_cta:
            variants.append(context_cta)

        return variants

    def _generate_context_aware_cta(self, article) -> dict[str, str] | None:
        """Use Flash-Lite to find the high-intent moment and create a matching CTA.

        Identifies where the reader feels most frustrated and generates a CTA
        that speaks directly to that emotion, linking to the most relevant
        product feature.

        Returns a CTA dict or None if generation fails.
        """
        if not self.has_llm:
            return None

        content = article.markdown_content or ""
        if len(content) < 500:
            return None

        keyword = article.target_keyword or ""
        info = self._product_info(article)
        brand = info["brand"]
        site_url = info["site_url"]
        tools_url = info["tools_url"]

        prompt = f"""You are a conversion rate optimization expert for {brand} ({site_url}),
an AI tool that helps users analyze and challenge unfair billing/settlement outcomes.

Read this article about "{keyword}" and identify the HIGH-INTENT MOMENT — the paragraph
where the reader feels MOST frustrated with their insurance company and most likely to take action.

Then create a context-aware CTA that:
1. Mirrors the specific frustration in that paragraph
2. Offers the product as the immediate next step
3. Is 1 sentence for the heading, 1-2 sentences for the body text

Available URLs:
- {site_url} — main analyzer
- {tools_url} — tools and calculators

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
                    # Only allow same-product URLs
                    if info["site_url"].replace("https://", "") in url:
                        cta["button_url"] = url
                    else:
                        cta["button_url"] = info["site_url"]

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
        info = self._product_info(article)

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

        # Render interactive tool placeholders into widget HTML
        html_content = self._render_tool_placeholders(html_content)

        template = Template("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ meta_title }}</title>
    <meta name="description" content="{{ meta_description }}">
    <meta name="keywords" content="{{ target_keyword }}">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{{ canonical_url }}">
    <!-- Open Graph -->
    <meta property="og:type" content="article">
    <meta property="og:title" content="{{ meta_title }}">
    <meta property="og:description" content="{{ meta_description }}">
    <meta property="og:url" content="{{ canonical_url }}">
    {% if og_image %}<meta property="og:image" content="{{ og_image }}">{% endif %}
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{{ meta_title }}">
    <meta name="twitter:description" content="{{ meta_description }}">
    {% if og_image %}<meta name="twitter:image" content="{{ og_image }}">{% endif %}
    <link rel="stylesheet" href="/styles.css">
    {% if has_tools %}<link rel="stylesheet" href="/static/tools/tools.css">{% endif %}
</head>
<body>
    <header>
        <nav>
            <a href="/">{{ brand_name }}</a>
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
        <p>&copy; {{ year }} {{ brand_name }}. All rights reserved.</p>
    </footer>
    {% if has_tools %}
    <script id="cc-tool-data" type="application/json">{{ tool_data_json | safe }}</script>
    <script src="/static/tools/tools.js"></script>
    {% endif %}
</body>
</html>
""")

        # Extract first image URL from content for og:image
        og_image = ""
        img_match = re.search(r'<img[^>]+src="([^"]+)"', html_content)
        if not img_match:
            img_match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", article.markdown_content or "")
        if img_match:
            og_image = img_match.group(1)

        canonical_url = f"{info['site_url'].rstrip('/')}/blog/{slug}"

        # Check if article has embedded interactive tools
        has_tools = "data-cc-tool" in html_content
        tool_data_json = ""
        if has_tools:
            try:
                from tools.data import export_tool_data_json
                tool_data_json = export_tool_data_json()
            except ImportError:
                self.logger.warning("tools.data not available — skipping tool rendering")
                has_tools = False

        html = template.render(
            meta_title=article.meta_title or article.title,
            meta_description=article.meta_description or "",
            target_keyword=article.target_keyword or "",
            title=article.title,
            content=html_content,
            canonical_url=canonical_url,
            og_image=og_image,
            primary_cta=primary_cta,
            secondary_cta=secondary_cta,
            newsletter_cta=newsletter_cta,
            year=datetime.now().year,
            brand_name=info["brand"],
            has_tools=has_tools,
            tool_data_json=tool_data_json,
        )

        filepath = self.blog_dir / "html" / f"{slug}.html"
        filepath.write_text(html, encoding="utf-8")

        self.logger.info(f"  Generated HTML: {filepath}")
        return filepath

    @staticmethod
    def _render_tool_placeholders(html_content: str) -> str:
        """Replace <!-- TOOL:tool_id:mini --> placeholders with widget divs.

        Quill embeds these comment-style placeholders during self-review.
        Ezra converts them to real ``<div data-cc-tool="...">`` elements
        that the client-side JS (tools.js) mounts on page load.
        """
        import re as _re

        def _replace(m: _re.Match) -> str:
            tool_id = m.group(1)
            mode = m.group(2) or "mini"
            rest = m.group(3) or ""
            # Extract data-state if present
            state_match = _re.search(r'data-state="([^"]*)"', rest)
            state_attr = f' data-state="{state_match.group(1)}"' if state_match else ""
            return (
                f'<div data-cc-tool="{tool_id}" data-mode="{mode}"'
                f'{state_attr}></div>'
            )

        return _re.sub(
            r'<!--\s*TOOL:([\w]+):([\w]+)((?:\s+[^>]*)?)\s*-->',
            _replace,
            html_content,
        )

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

        # Atomic write: write to temp file first, then rename
        tmp_path = index_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
        tmp_path.replace(index_path)

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

    def _ensure_unique_slug(self, slug: str) -> str:
        """Append a numeric suffix if slug already exists among published articles."""
        published = self.db.get_published_articles()
        existing_slugs = {a.slug for a in published if a.slug}
        if slug not in existing_slugs:
            return slug
        for i in range(2, 100):
            candidate = f"{slug[:76]}-{i}"
            if candidate not in existing_slugs:
                self.logger.info(f"  Slug '{slug}' taken, using '{candidate}'")
                return candidate
        return f"{slug[:70]}-{hash(slug) % 10000}"
