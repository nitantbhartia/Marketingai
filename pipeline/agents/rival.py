"""
Rival - Competitor Intelligence Agent

Monitors competitor content, identifies gaps, and creates "better version" briefs.
Keeps you ahead of the competition through systematic analysis.
"""

import json
import hashlib
import re
from datetime import datetime
from typing import Dict, Any, List
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

from pipeline.agents.base import Agent
from content_quality.db import get_db, log_competitor_article, log_agent_action


class Rival(Agent):
    """
    Competitor monitoring agent - tracks competitors and identifies opportunities.

    Workflow:
    1. Monitors competitor domains
    2. Detects new/updated content
    3. Analyzes their top-ranking articles
    4. Identifies content gaps
    5. Creates briefs for "better" articles
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="rival", config=config)
        self.competitor_domains = config.get("competitor_domains", [])
        self.target_keywords = config.get("target_keywords", [])
        self.max_articles_per_run = config.get("max_competitor_checks", 20)

    def run(self) -> Dict[str, Any]:
        """Monitor competitors and identify opportunities."""

        self.log("Starting competitor analysis...")

        results = {
            "competitors_checked": 0,
            "articles_tracked": 0,
            "gaps_identified": 0,
            "briefs_created": 0,
            "opportunities": []
        }

        if not self.competitor_domains:
            self.log("No competitor domains configured", level="warning")
            return results

        # Track existing keywords we cover
        our_keywords = self._get_our_keywords()

        for domain in self.competitor_domains:
            self.log(f"Analyzing {domain}...")

            try:
                # Check competitor's sitemap or blog
                articles = self._scrape_competitor_blog(domain)
                results["competitors_checked"] += 1

                for article in articles[:self.max_articles_per_run]:
                    # Log to database
                    self._track_competitor_article(article)
                    results["articles_tracked"] += 1

                    # Check if we have this keyword
                    if article.get("keyword") and article["keyword"] not in our_keywords:
                        gap = {
                            "keyword": article["keyword"],
                            "competitor_domain": domain,
                            "competitor_url": article["url"],
                            "competitor_position": article.get("position", "unknown"),
                            "opportunity_type": "keyword_gap"
                        }
                        results["gaps_identified"] += 1
                        results["opportunities"].append(gap)

                        # Create brief for Quill
                        brief_id = self._create_content_brief(article, gap)
                        if brief_id:
                            results["briefs_created"] += 1

            except Exception as e:
                self.log(f"Error analyzing {domain}: {e}", level="error")
                continue

        # Log activity
        log_agent_action(
            agent_name=self.name,
            action="competitor_analysis",
            details=results
        )

        self.log(f"✓ Competitor analysis complete: {results['gaps_identified']} gaps found")
        return results

    def _get_our_keywords(self) -> set:
        """Get keywords we already target."""
        keywords = set()
        with get_db() as db:
            cursor = db.execute("SELECT DISTINCT target_keyword FROM articles WHERE target_keyword IS NOT NULL")
            for row in cursor.fetchall():
                keywords.add(row[0].lower())
        return keywords

    def _scrape_competitor_blog(self, domain: str) -> List[Dict]:
        """
        Scrape competitor blog to find articles.

        Returns list of article dicts.
        """
        articles = []

        # Try common blog patterns
        blog_urls = [
            f"https://{domain}/blog",
            f"https://{domain}/articles",
            f"https://{domain}/resources",
            f"https://{domain}/learn"
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ClaimCoachBot/1.0; +https://claimcoach.app)"
        }

        for blog_url in blog_urls:
            try:
                response = requests.get(blog_url, headers=headers, timeout=10)
                if response.status_code != 200:
                    continue

                soup = BeautifulSoup(response.content, 'html.parser')

                # Find article links
                article_links = []

                # Try common patterns
                for link in soup.find_all('a', href=True):
                    href = link['href']

                    # Make absolute URL
                    if href.startswith('/'):
                        href = f"https://{domain}{href}"

                    # Filter for article URLs
                    if any(pattern in href for pattern in ['/blog/', '/article/', '/post/', '/learn/']):
                        article_links.append({
                            "url": href,
                            "title": link.get_text(strip=True)
                        })

                # Process articles
                for link in article_links[:20]:  # Limit per blog
                    article_data = self._analyze_article(link["url"], link["title"], domain)
                    if article_data:
                        articles.append(article_data)

                # If we found articles, stop trying other URLs
                if articles:
                    break

            except Exception as e:
                self.log(f"Error scraping {blog_url}: {e}", level="debug")
                continue

        return articles

    def _analyze_article(self, url: str, title: str, domain: str) -> Dict:
        """
        Analyze a single competitor article.

        Returns article data dict or None.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; ClaimCoachBot/1.0; +https://claimcoach.app)"
            }
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.content, 'html.parser')

            # Extract content
            content = ""
            article_body = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile('content|article|post'))

            if article_body:
                content = article_body.get_text(strip=True)

            # Calculate word count
            word_count = len(content.split())

            # Try to extract keyword (very basic - from title/URL)
            keyword = self._extract_keyword(title, url)

            # Create content hash for change detection
            content_hash = hashlib.md5(content.encode()).hexdigest()

            return {
                "url": url,
                "title": title,
                "domain": domain,
                "keyword": keyword,
                "word_count": word_count,
                "content_hash": content_hash,
                "content": content[:1000]  # First 1000 chars for analysis
            }

        except Exception as e:
            self.log(f"Error analyzing article {url}: {e}", level="debug")
            return None

    def _extract_keyword(self, title: str, url: str) -> str:
        """
        Extract likely target keyword from title/URL.

        Basic heuristic.
        """
        # Try URL first (often contains slug with keyword)
        url_parts = urlparse(url).path.split('/')
        url_parts = [p for p in url_parts if p and len(p) > 3]

        if url_parts:
            # Last path segment is often the slug
            slug = url_parts[-1]
            # Remove common suffixes
            slug = re.sub(r'\.(html|htm|php|aspx)$', '', slug)
            # Convert hyphens to spaces
            keyword = slug.replace('-', ' ').replace('_', ' ')
            return keyword

        # Fallback to title
        # Remove common prefixes
        title_clean = re.sub(r'^(how to|what is|guide to|understanding|the ultimate guide to|complete guide to)\s+', '', title.lower())
        return title_clean[:100]  # Limit length

    def _track_competitor_article(self, article: Dict):
        """Save competitor article to database."""
        log_competitor_article(
            domain=article["domain"],
            url=article["url"],
            title=article["title"],
            keyword=article.get("keyword", ""),
            word_count=article.get("word_count", 0),
            position=0,  # Would need SERP API to get actual position
            content_hash=article.get("content_hash", "")
        )

    def _create_content_brief(self, competitor_article: Dict, gap: Dict) -> int:
        """
        Create a content brief for Quill to write a better article.

        Returns article ID or None.
        """
        try:
            # Analyze competitor content
            competitor_content = competitor_article.get("content", "")

            # Generate brief using Claude
            brief = self._generate_brief(competitor_article, gap)

            # Create article in database
            with get_db() as db:
                cursor = db.execute("""
                    INSERT INTO articles
                    (title, slug, target_keyword, status, revision_notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    brief["title"],
                    brief["slug"],
                    gap["keyword"],
                    "todo",
                    json.dumps({
                        "brief": brief["content_brief"],
                        "competitor_url": competitor_article["url"],
                        "competitor_domain": competitor_article["domain"],
                        "word_count_target": brief["word_count_target"],
                        "opportunity_type": "outrank_competitor"
                    })
                ))

                article_id = cursor.lastrowid

            self.log(f"  Created brief for '{gap['keyword']}' (article #{article_id})")
            return article_id

        except Exception as e:
            self.log(f"Error creating brief: {e}", level="error")
            return None

    def _generate_brief(self, competitor_article: Dict, gap: Dict) -> Dict:
        """Generate content brief to outrank competitor."""

        # Simple brief generation (in production, could use Claude API)
        keyword = gap["keyword"]
        title = f"How to {keyword.title()}"  # Basic title generation

        # Generate slug
        slug = re.sub(r'[^\w\s-]', '', keyword.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        slug = slug.strip('-')[:50]

        # Content brief
        brief = f"""Write a comprehensive guide on '{keyword}' that outranks {gap['competitor_domain']}.

Competitor article: {competitor_article['url']}
Competitor word count: {competitor_article.get('word_count', 'unknown')}

Requirements:
- Target keyword: {keyword}
- Make it 20% longer and more comprehensive than competitor
- Include specific examples and step-by-step instructions
- Add FAQ section addressing common questions
- Use clear, helpful tone
- Include practical tips the competitor didn't cover
"""

        # Calculate target word count (20% more than competitor)
        competitor_wc = competitor_article.get("word_count", 2000)
        target_wc = int(competitor_wc * 1.2)

        return {
            "title": title,
            "slug": slug,
            "content_brief": brief,
            "word_count_target": target_wc
        }
