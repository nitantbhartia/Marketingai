"""
Search Console Monitor

Weekly analysis of Google Search Console data to identify optimization opportunities,
content gaps, and performance issues.
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from content_quality.config import (
    GOOGLE_SEARCH_CONSOLE_CREDENTIALS,
    SITE_URL
)
from content_quality.db import get_db, log_agent_action


class SearchConsoleMonitor:
    """Monitors Google Search Console data for SEO insights."""

    def __init__(self):
        """Initialize GSC monitor."""
        self.client = self._init_gsc_client()

    def _init_gsc_client(self):
        """Initialize Google Search Console API client."""
        try:
            credentials_info = json.loads(GOOGLE_SEARCH_CONSOLE_CREDENTIALS)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/webmasters.readonly']
            )
            return build('searchconsole', 'v1', credentials=credentials)
        except Exception as e:
            print(f"Warning: Could not initialize GSC client: {e}")
            return None

    def _fetch_gsc_data(self, start_date: str, end_date: str, dimensions: List[str]) -> List[Dict]:
        """Fetch data from Google Search Console."""
        if not self.client:
            return []

        try:
            response = self.client.searchanalytics().query(
                siteUrl=SITE_URL,
                body={
                    "startDate": start_date,
                    "endDate": end_date,
                    "dimensions": dimensions,
                    "rowLimit": 1000,
                }
            ).execute()

            return response.get("rows", [])
        except Exception as e:
            print(f"Error fetching GSC data: {e}")
            return []

    def _extract_slug_from_url(self, url: str) -> str:
        """Extract slug from URL."""
        import re
        path = re.sub(r'^https?://[^/]+/', '', url)
        path = path.rstrip('/')
        segments = path.split('/')
        return segments[-1] if segments else ""

    def _aggregate_by_page(self, data: List[Dict]) -> Dict[str, Dict]:
        """Aggregate GSC data by page."""
        pages = {}

        for row in data:
            page = row["keys"][1]  # Assuming dimensions are ["query", "page"]

            if page not in pages:
                pages[page] = {
                    "impressions": 0,
                    "clicks": 0,
                    "position_sum": 0,
                    "count": 0,
                }

            pages[page]["impressions"] += row.get("impressions", 0)
            pages[page]["clicks"] += row.get("clicks", 0)
            pages[page]["position_sum"] += row.get("position", 0)
            pages[page]["count"] += 1

        # Calculate averages
        for page, metrics in pages.items():
            metrics["avg_position"] = metrics["position_sum"] / metrics["count"] if metrics["count"] > 0 else 0
            metrics["ctr"] = metrics["clicks"] / metrics["impressions"] if metrics["impressions"] > 0 else 0

        return pages

    def weekly_report(self) -> Dict:
        """
        Generate weekly Search Console report.

        Returns:
            Report dict with actionable insights
        """
        report = {
            "date": datetime.now().isoformat(),
            "almost_page_one": [],
            "high_impressions_low_ctr": [],
            "new_ranking_keywords": [],
            "declining_articles": [],
            "top_performers": [],
        }

        # Fetch last 7 days of data
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        data = self._fetch_gsc_data(
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
            dimensions=["query", "page"]
        )

        if not data:
            return report

        # Analyze each query/page combination
        for row in data:
            query = row["keys"][0]
            page = row["keys"][1]
            position = row.get("position", 999)
            impressions = row.get("impressions", 0)
            clicks = row.get("clicks", 0)
            ctr = row.get("ctr", 0)

            # Almost page 1: positions 4-10 with decent impressions
            if 4 <= position <= 10 and impressions >= 50:
                report["almost_page_one"].append({
                    "query": query,
                    "page": page,
                    "position": round(position, 1),
                    "impressions": impressions,
                    "action": "UPDATE_ARTICLE",
                    "priority": "HIGH",
                    "suggestion": f"Ranking #{round(position)} for '{query}' with {impressions} impressions. Refresh content, add sections, update date to push to page 1."
                })

            # High impressions, low CTR: title/meta not compelling
            if impressions >= 100 and ctr < 0.02 and position <= 15:
                report["high_impressions_low_ctr"].append({
                    "query": query,
                    "page": page,
                    "impressions": impressions,
                    "ctr": round(ctr * 100, 2),
                    "action": "REWRITE_TITLE_META",
                    "priority": "MEDIUM",
                    "suggestion": f"Getting {impressions} impressions but only {round(ctr*100, 2)}% CTR. Rewrite title and meta description to be more compelling."
                })

            # New keywords (not explicitly targeted)
            # This would require checking against article's target keyword from database

        # Compare with previous week for declining articles
        previous_start = start_date - timedelta(days=7)
        previous_end = start_date - timedelta(days=1)

        previous_data = self._fetch_gsc_data(
            previous_start.strftime("%Y-%m-%d"),
            previous_end.strftime("%Y-%m-%d"),
            dimensions=["query", "page"]
        )

        if previous_data:
            current_pages = self._aggregate_by_page(data)
            previous_pages = self._aggregate_by_page(previous_data)

            for page, current_metrics in current_pages.items():
                if page in previous_pages:
                    prev_metrics = previous_pages[page]
                    position_change = current_metrics["avg_position"] - prev_metrics["avg_position"]

                    # Declining (position increased = ranking dropped)
                    if position_change > 3:
                        report["declining_articles"].append({
                            "page": page,
                            "position_change": round(position_change, 1),
                            "current_position": round(current_metrics["avg_position"], 1),
                            "action": "INVESTIGATE",
                            "priority": "MEDIUM",
                            "suggestion": f"Dropped {round(position_change, 1)} positions. Check for content quality issues or new competition."
                        })

                    # Top performers (improving)
                    elif position_change < -2:  # Negative = improved
                        report["top_performers"].append({
                            "page": page,
                            "position_change": round(position_change, 1),
                            "current_position": round(current_metrics["avg_position"], 1),
                            "impressions": current_metrics["impressions"],
                            "clicks": current_metrics["clicks"],
                        })

        # Save report to database
        self._save_report(report)

        # Log agent action
        log_agent_action(
            agent_name="search_console_monitor",
            action="weekly_report_generated",
            details={"total_opportunities": len(report["almost_page_one"]) + len(report["high_impressions_low_ctr"])}
        )

        return report

    def _save_report(self, report: Dict):
        """Save report to database for Morgan to process."""
        with get_db() as db:
            # Save GSC snapshots
            for item in report.get("almost_page_one", []) + report.get("high_impressions_low_ctr", []):
                page = item.get("page", "")
                slug = self._extract_slug_from_url(page)

                # Get article ID if exists
                cursor = db.execute("SELECT id FROM articles WHERE slug = ?", (slug,))
                article = cursor.fetchone()

                if article:
                    article_id = article["id"]

                    # Update article with GSC data
                    db.execute("""
                        UPDATE articles SET
                            last_gsc_position = ?,
                            last_gsc_impressions = ?,
                            last_gsc_clicks = ?,
                            last_gsc_ctr = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        item.get("position"),
                        item.get("impressions"),
                        item.get("clicks", 0),
                        item.get("ctr", 0),
                        article_id
                    ))

                    # Set refresh priority if almost page one
                    if item.get("action") == "UPDATE_ARTICLE":
                        db.execute("""
                            UPDATE articles SET
                                refresh_priority = 'HIGH',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (article_id,))


def run_weekly_search_console_report():
    """Run weekly Search Console report (called by cron)."""
    monitor = SearchConsoleMonitor()
    report = monitor.weekly_report()

    print(f"Search Console Report Generated:")
    print(f"  - Almost Page One: {len(report['almost_page_one'])} opportunities")
    print(f"  - High Impressions, Low CTR: {len(report['high_impressions_low_ctr'])} articles")
    print(f"  - Declining Articles: {len(report['declining_articles'])} articles")
    print(f"  - Top Performers: {len(report['top_performers'])} articles")

    return report


if __name__ == "__main__":
    run_weekly_search_console_report()
