"""
Atlas - Performance Analyst Agent

Analyzes published content performance and generates insights to improve future content.
Creates a feedback loop where the system learns what works and optimizes automatically.
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
from collections import defaultdict
import statistics

from pipeline.agents.base import Agent
from pipeline.db import get_db, log_performance_insight, log_agent_action


class Atlas(Agent):
    """
    Performance analyst agent - learns what works, optimizes content strategy.

    Workflow:
    1. Analyzes top-performing articles (GSC data)
    2. Identifies patterns and success factors
    3. Generates actionable insights
    4. Updates prompts/templates based on learnings
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="atlas", config=config)
        self.min_articles_for_insights = config.get("min_articles_for_analysis", 10)
        self.min_confidence_score = config.get("min_confidence_score", 0.7)

    def run(self) -> Dict[str, Any]:
        """Analyze performance and generate insights."""

        self.log("Starting performance analysis...")

        results = {
            "insights_generated": 0,
            "patterns_discovered": 0,
            "recommendations": []
        }

        # Get published articles with GSC data
        with get_db() as db:
            cursor = db.execute("""
                SELECT id, title, slug, target_keyword, target_state, word_count,
                       seo_score, readability_score, last_gsc_position,
                       last_gsc_impressions, last_gsc_clicks, last_gsc_ctr,
                       published_at, markdown_content
                FROM articles
                WHERE status = 'done'
                AND published_at IS NOT NULL
                AND last_gsc_position IS NOT NULL
                ORDER BY last_gsc_clicks DESC
            """)
            articles = [dict(row) for row in cursor.fetchall()]

        if len(articles) < self.min_articles_for_insights:
            self.log(f"Not enough articles for analysis ({len(articles)} < {self.min_articles_for_insights})")
            return results

        self.log(f"Analyzing {len(articles)} published articles...")

        # 1. Identify top performers
        top_performers = self._identify_top_performers(articles)
        results["patterns_discovered"] += len(top_performers)

        # 2. Analyze content patterns
        insights = []

        # Pattern: Word count correlation
        word_count_insight = self._analyze_word_count_pattern(articles)
        if word_count_insight:
            insights.append(word_count_insight)

        # Pattern: SEO score correlation
        seo_insight = self._analyze_seo_pattern(articles)
        if seo_insight:
            insights.append(seo_insight)

        # Pattern: State-specific performance
        state_insight = self._analyze_state_performance(articles)
        if state_insight:
            insights.append(state_insight)

        # Pattern: Keyword types that work
        keyword_insight = self._analyze_keyword_patterns(articles)
        if keyword_insight:
            insights.append(keyword_insight)

        # Pattern: Content structure analysis
        structure_insight = self._analyze_content_structure(articles)
        if structure_insight:
            insights.append(structure_insight)

        # 3. Save insights to database
        for insight in insights:
            if insight["confidence"] >= self.min_confidence_score:
                log_performance_insight(
                    insight_type=insight["type"],
                    insight_text=insight["text"],
                    confidence_score=insight["confidence"],
                    supporting_data=insight["data"]
                )
                results["insights_generated"] += 1
                results["recommendations"].append(insight["text"])

        # 4. Log activity
        log_agent_action(
            agent_name=self.name,
            action="analysis_complete",
            details=results
        )

        self.log(f"✓ Analysis complete: {results['insights_generated']} insights generated")
        return results

    def _identify_top_performers(self, articles: List[Dict]) -> List[Dict]:
        """Identify articles in top 3 positions or with high engagement."""
        top_performers = []

        for article in articles:
            position = article.get("last_gsc_position", 100)
            clicks = article.get("last_gsc_clicks", 0)
            ctr = article.get("last_gsc_ctr", 0)

            # Top 3 position OR high engagement
            if position <= 3 or (clicks > 50 and ctr > 0.05):
                top_performers.append(article)

        return top_performers

    def _analyze_word_count_pattern(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze correlation between word count and performance."""
        if len(articles) < 10:
            return None

        # Split into high/low performers by position
        high_performers = [a for a in articles if a.get("last_gsc_position", 100) <= 5]
        low_performers = [a for a in articles if a.get("last_gsc_position", 100) > 10]

        if not high_performers or not low_performers:
            return None

        # Calculate average word counts (estimate from markdown)
        def estimate_word_count(markdown: str) -> int:
            if not markdown:
                return 0
            return len(markdown.split())

        high_wc = [estimate_word_count(a.get("markdown_content", "")) for a in high_performers]
        low_wc = [estimate_word_count(a.get("markdown_content", "")) for a in low_performers]

        avg_high = statistics.mean(high_wc) if high_wc else 0
        avg_low = statistics.mean(low_wc) if low_wc else 0

        if avg_high > avg_low * 1.2:  # 20% longer
            difference_pct = ((avg_high - avg_low) / avg_low) * 100
            return {
                "type": "word_count",
                "text": f"Top-ranking articles average {int(avg_high)} words, {int(difference_pct)}% longer than lower-ranked articles ({int(avg_low)} words). Consider targeting {int(avg_high)}-word articles.",
                "confidence": min(0.9, 0.6 + (difference_pct / 100)),
                "data": {
                    "avg_high": int(avg_high),
                    "avg_low": int(avg_low),
                    "sample_size_high": len(high_wc),
                    "sample_size_low": len(low_wc)
                }
            }

        return None

    def _analyze_seo_pattern(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze SEO score impact on rankings."""
        scored_articles = [(a.get("seo_score", 0), a.get("last_gsc_position", 100))
                          for a in articles if a.get("seo_score")]

        if len(scored_articles) < 10:
            return None

        # Top 5 positions
        top_articles = [a for a in articles if a.get("last_gsc_position", 100) <= 5]
        avg_seo_top = statistics.mean([a.get("seo_score", 0) for a in top_articles]) if top_articles else 0

        # Position 6-20
        mid_articles = [a for a in articles if 5 < a.get("last_gsc_position", 100) <= 20]
        avg_seo_mid = statistics.mean([a.get("seo_score", 0) for a in mid_articles]) if mid_articles else 0

        if avg_seo_top > avg_seo_mid + 5:  # 5+ point difference
            return {
                "type": "seo_score",
                "text": f"Articles in top 5 positions average {int(avg_seo_top)}/100 SEO score vs {int(avg_seo_mid)}/100 for positions 6-20. Higher SEO scores correlate with better rankings.",
                "confidence": 0.85,
                "data": {
                    "avg_seo_top": int(avg_seo_top),
                    "avg_seo_mid": int(avg_seo_mid),
                    "top_count": len(top_articles),
                    "mid_count": len(mid_articles)
                }
            }

        return None

    def _analyze_state_performance(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze which states perform best."""
        state_performance = defaultdict(lambda: {"clicks": 0, "count": 0, "positions": []})

        for article in articles:
            state = article.get("target_state")
            if not state:
                continue

            clicks = article.get("last_gsc_clicks", 0)
            position = article.get("last_gsc_position", 100)

            state_performance[state]["clicks"] += clicks
            state_performance[state]["count"] += 1
            state_performance[state]["positions"].append(position)

        # Find best performing state
        best_state = None
        best_avg_clicks = 0

        for state, data in state_performance.items():
            if data["count"] >= 3:  # Need at least 3 articles
                avg_clicks = data["clicks"] / data["count"]
                if avg_clicks > best_avg_clicks:
                    best_avg_clicks = avg_clicks
                    best_state = state

        if best_state:
            data = state_performance[best_state]
            avg_position = statistics.mean(data["positions"])
            return {
                "type": "state_performance",
                "text": f"{best_state} content performs best with {int(best_avg_clicks)} avg clicks/article and avg position {avg_position:.1f}. Prioritize {best_state} content.",
                "confidence": 0.75,
                "data": {
                    "state": best_state,
                    "avg_clicks": int(best_avg_clicks),
                    "avg_position": round(avg_position, 1),
                    "article_count": data["count"]
                }
            }

        return None

    def _analyze_keyword_patterns(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze which keyword types perform best."""
        # Categorize keywords
        keyword_types = {
            "total_loss": [],
            "settlement": [],
            "insurance": [],
            "claim": [],
            "other": []
        }

        for article in articles:
            keyword = (article.get("target_keyword") or "").lower()
            position = article.get("last_gsc_position", 100)
            clicks = article.get("last_gsc_clicks", 0)

            if not keyword or position > 50:
                continue

            if "total loss" in keyword:
                keyword_types["total_loss"].append((position, clicks))
            elif "settlement" in keyword:
                keyword_types["settlement"].append((position, clicks))
            elif "insurance" in keyword:
                keyword_types["insurance"].append((position, clicks))
            elif "claim" in keyword:
                keyword_types["claim"].append((position, clicks))
            else:
                keyword_types["other"].append((position, clicks))

        # Find best performing type
        best_type = None
        best_avg_position = 100

        for kw_type, data in keyword_types.items():
            if len(data) >= 3:
                avg_pos = statistics.mean([d[0] for d in data])
                if avg_pos < best_avg_position:
                    best_avg_position = avg_pos
                    best_type = kw_type

        if best_type and best_avg_position < 10:
            data = keyword_types[best_type]
            avg_clicks = statistics.mean([d[1] for d in data])
            return {
                "type": "keyword_pattern",
                "text": f"'{best_type.replace('_', ' ')}' keywords rank best (avg position {best_avg_position:.1f}, {int(avg_clicks)} clicks). Focus on these keyword types.",
                "confidence": 0.70,
                "data": {
                    "keyword_type": best_type,
                    "avg_position": round(best_avg_position, 1),
                    "avg_clicks": int(avg_clicks),
                    "sample_size": len(data)
                }
            }

        return None

    def _analyze_content_structure(self, articles: List[Dict]) -> Dict[str, Any]:
        """Analyze content structure patterns (FAQs, lists, etc)."""
        top_articles = [a for a in articles if a.get("last_gsc_position", 100) <= 5]

        if len(top_articles) < 5:
            return None

        # Count structure elements
        has_faq_count = 0
        has_numbered_list_count = 0
        has_bullets_count = 0

        for article in top_articles:
            content = article.get("markdown_content", "")
            if not content:
                continue

            content_lower = content.lower()
            if "faq" in content_lower or "frequently asked" in content_lower:
                has_faq_count += 1
            if "\n1." in content or "\n2." in content:
                has_numbered_list_count += 1
            if "\n- " in content or "\n* " in content:
                has_bullets_count += 1

        total = len(top_articles)
        faq_pct = (has_faq_count / total) * 100
        list_pct = (has_numbered_list_count / total) * 100

        insights = []
        if faq_pct >= 60:
            insights.append(f"{int(faq_pct)}% of top articles include FAQ sections")
        if list_pct >= 60:
            insights.append(f"{int(list_pct)}% of top articles use numbered lists")

        if insights:
            return {
                "type": "content_structure",
                "text": "Top-performing articles share these patterns: " + "; ".join(insights) + ". Include these elements in future content.",
                "confidence": 0.80,
                "data": {
                    "faq_percentage": int(faq_pct),
                    "numbered_list_percentage": int(list_pct),
                    "sample_size": total
                }
            }

        return None
