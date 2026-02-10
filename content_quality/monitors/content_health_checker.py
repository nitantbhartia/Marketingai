"""
Content Health Checker

Weekly crawl of the entire blog to catch broken links, content cannibalization,
and stale articles needing refresh.
"""

import re
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from content_quality.config import (
    STALE_CONTENT_DAYS,
    VERY_STALE_CONTENT_DAYS,
    CANNIBALIZATION_THRESHOLD,
    LINK_CHECK_TIMEOUT
)
from content_quality.db import get_db, log_agent_action
from content_quality.utils.text_utils import extract_links, extract_first_n_words


class ContentHealthChecker:
    """Checks health of published content."""

    def __init__(self, ghost_client=None):
        """
        Initialize content health checker.

        Args:
            ghost_client: Optional Ghost API client
        """
        self.ghost_client = ghost_client

    def _get_all_published_posts(self) -> List[Dict]:
        """Get all published posts from Ghost or database."""
        posts = []

        if self.ghost_client:
            # Fetch from Ghost API
            posts = self.ghost_client.get_all_published_posts()
        else:
            # Fetch from database
            with get_db() as db:
                cursor = db.execute("""
                    SELECT id, title, slug, markdown_content, meta_description,
                           published_url, published_at
                    FROM articles
                    WHERE status = 'done' AND published_at IS NOT NULL
                """)
                rows = cursor.fetchall()

                for row in rows:
                    posts.append({
                        "id": row["id"],
                        "title": row["title"],
                        "slug": row["slug"],
                        "html": row["markdown_content"],
                        "meta_description": row["meta_description"] or "",
                        "url": row["published_url"] or f"https://claimcoach.app/blog/{row['slug']}",
                        "published_at": row["published_at"],
                    })

        return posts

    def _check_link_status(self, url: str) -> tuple:
        """Check HTTP status of a link."""
        try:
            response = requests.head(
                url,
                timeout=LINK_CHECK_TIMEOUT,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 ClaimCoach-HealthChecker/1.0'}
            )

            if response.status_code >= 400:
                response = requests.get(url, timeout=LINK_CHECK_TIMEOUT, allow_redirects=True,
                                       headers={'User-Agent': 'Mozilla/5.0'})

            return (response.status_code, response.url)

        except requests.exceptions.Timeout:
            return ("TIMEOUT", url)
        except requests.exceptions.RequestException as e:
            return (f"ERROR", url)

    def crawl_broken_links(self) -> Dict:
        """
        Crawl all published posts and check links.

        Returns:
            Report dict with broken links
        """
        posts = self._get_all_published_posts()
        broken_links = []
        total_links_checked = 0

        for post in posts:
            links = extract_links(post.get("html", ""))
            total_links_checked += len(links)

            for url, link_text in links:
                status, final_url = self._check_link_status(url)

                if isinstance(status, int) and status >= 400:
                    broken_links.append({
                        "post_title": post["title"],
                        "post_slug": post["slug"],
                        "post_id": post.get("id"),
                        "broken_url": url,
                        "status": status,
                        "action": "FIX_OR_REMOVE_LINK",
                    })

                    # Save to database
                    self._save_broken_link(post.get("id"), url, str(status))

                elif status in ["TIMEOUT", "ERROR"]:
                    broken_links.append({
                        "post_title": post["title"],
                        "post_slug": post["slug"],
                        "post_id": post.get("id"),
                        "broken_url": url,
                        "status": status,
                        "action": "FIX_OR_REMOVE_LINK",
                    })

                    self._save_broken_link(post.get("id"), url, status)

        return {
            "total_posts_checked": len(posts),
            "total_links_checked": total_links_checked,
            "broken_links": broken_links,
            "broken_count": len(broken_links),
        }

    def _save_broken_link(self, article_id: Optional[int], url: str, status: str):
        """Save broken link to database."""
        if not article_id:
            return

        with get_db() as db:
            # Check if already exists
            cursor = db.execute("""
                SELECT id FROM broken_links
                WHERE article_id = ? AND broken_url = ? AND resolved = 0
            """, (article_id, url))

            if cursor.fetchone() is None:
                # Insert new broken link
                db.execute("""
                    INSERT INTO broken_links (article_id, broken_url, http_status, first_detected)
                    VALUES (?, ?, ?, DATE('now'))
                """, (article_id, url, status))

                # Update article broken_links_count
                db.execute("""
                    UPDATE articles SET
                        broken_links_count = broken_links_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (article_id,))

    def detect_cannibalization(self) -> Dict:
        """
        Find articles competing for the same keywords using TF-IDF similarity.

        Returns:
            Report dict with cannibalization pairs
        """
        posts = self._get_all_published_posts()

        if len(posts) < 2:
            return {
                "total_posts_analyzed": len(posts),
                "cannibalization_pairs": [],
            }

        # Build document corpus: title + meta_description + first 200 words
        documents = []
        post_info = []

        for post in posts:
            text = f"{post['title']} {post.get('meta_description', '')} {extract_first_n_words(post.get('html', ''), 200)}"
            documents.append(text)
            post_info.append({
                "id": post.get("id"),
                "title": post["title"],
                "slug": post["slug"],
                "url": post["url"],
                "published_at": post.get("published_at"),
            })

        # Calculate TF-IDF similarity
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
        tfidf_matrix = vectorizer.fit_transform(documents)
        similarity_matrix = cosine_similarity(tfidf_matrix)

        # Find pairs with high similarity
        cannibalization_pairs = []

        for i in range(len(posts)):
            for j in range(i + 1, len(posts)):
                similarity = similarity_matrix[i][j]

                if similarity > CANNIBALIZATION_THRESHOLD:
                    pair = {
                        "article_1": post_info[i],
                        "article_2": post_info[j],
                        "similarity_score": round(float(similarity), 3),
                        "action": "MERGE_OR_DIFFERENTIATE",
                        "suggestion": f"These articles are {round(float(similarity)*100)}% similar. Consider merging into one comprehensive article or clearly differentiating their target keywords."
                    }
                    cannibalization_pairs.append(pair)

                    # Save to database
                    self._save_cannibalization_pair(
                        post_info[i].get("id"),
                        post_info[j].get("id"),
                        float(similarity)
                    )

        return {
            "total_posts_analyzed": len(posts),
            "cannibalization_pairs": sorted(cannibalization_pairs, key=lambda x: x["similarity_score"], reverse=True),
        }

    def _save_cannibalization_pair(self, article_1_id: Optional[int], article_2_id: Optional[int], similarity: float):
        """Save cannibalization pair to database."""
        if not article_1_id or not article_2_id:
            return

        with get_db() as db:
            # Check if pair already exists
            cursor = db.execute("""
                SELECT id FROM cannibalization_pairs
                WHERE ((article_1_id = ? AND article_2_id = ?) OR (article_1_id = ? AND article_2_id = ?))
                AND resolved = 0
            """, (article_1_id, article_2_id, article_2_id, article_1_id))

            if cursor.fetchone() is None:
                # Insert new pair
                db.execute("""
                    INSERT INTO cannibalization_pairs (article_1_id, article_2_id, similarity_score)
                    VALUES (?, ?, ?)
                """, (article_1_id, article_2_id, similarity))

                # Flag both articles
                db.execute("""
                    UPDATE articles SET
                        cannibalization_flag = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN (?, ?)
                """, (article_1_id, article_2_id))

    def identify_stale_articles(self, gsc_data: Optional[Dict] = None) -> Dict:
        """
        Find articles that should be refreshed based on age and performance.

        Args:
            gsc_data: Optional GSC data from search console monitor

        Returns:
            Report dict with refresh candidates
        """
        posts = self._get_all_published_posts()
        refresh_candidates = []

        for post in posts:
            # Calculate age
            try:
                published_date = datetime.fromisoformat(post["published_at"].replace("Z", "+00:00"))
                age_days = (datetime.now().replace(tzinfo=published_date.tzinfo) - published_date).days
            except:
                age_days = 0

            # Get current ranking from database or GSC data
            avg_position = 999
            with get_db() as db:
                if post.get("id"):
                    cursor = db.execute(
                        "SELECT last_gsc_position FROM articles WHERE id = ?",
                        (post["id"],)
                    )
                    row = cursor.fetchone()
                    if row and row["last_gsc_position"]:
                        avg_position = row["last_gsc_position"]

            reasons = []
            priority = "LOW"

            # Almost page 1 + stale = high priority refresh
            if age_days > STALE_CONTENT_DAYS and 5 <= avg_position <= 15:
                reasons.append(f"Ranking #{round(avg_position)} and {age_days} days old — refresh could push to page 1")
                priority = "HIGH"

            # Very old content
            elif age_days > VERY_STALE_CONTENT_DAYS:
                reasons.append(f"{age_days} days old — freshness signal declining")
                priority = "MEDIUM"

            # State-specific content may have outdated regulations
            if self._contains_state_reference(post.get("html", "")):
                reasons.append("Contains state-specific content that may need updates")
                if priority == "LOW":
                    priority = "MEDIUM"

            if reasons:
                refresh_candidates.append({
                    "post_title": post["title"],
                    "post_slug": post["slug"],
                    "post_id": post.get("id"),
                    "age_days": age_days,
                    "avg_position": round(avg_position, 1) if avg_position < 999 else "unranked",
                    "priority": priority,
                    "reasons": reasons,
                    "action": "QUEUE_FOR_REFRESH",
                })

                # Update database
                if post.get("id"):
                    with get_db() as db:
                        db.execute("""
                            UPDATE articles SET
                                refresh_priority = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (priority, post["id"]))

        # Sort by priority
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        refresh_candidates.sort(key=lambda x: priority_order[x["priority"]])

        return {
            "total_posts_analyzed": len(posts),
            "refresh_candidates": refresh_candidates,
        }

    def _contains_state_reference(self, text: str) -> bool:
        """Check if text contains state references."""
        states = ["California", "Texas", "Florida", "New York", "Georgia",
                  "North Carolina", "Pennsylvania", "Illinois", "Ohio", "Michigan"]

        text_lower = text.lower()
        return any(state.lower() in text_lower for state in states)

    def run_weekly_health_check(self) -> Dict:
        """
        Run all content health checks.

        Returns:
            Combined report
        """
        print("Running content health checks...")

        # 1. Check broken links
        print("  1. Checking for broken links...")
        broken_links_report = self.crawl_broken_links()

        # 2. Detect cannibalization
        print("  2. Detecting content cannibalization...")
        cannibalization_report = self.detect_cannibalization()

        # 3. Identify stale articles
        print("  3. Identifying stale articles...")
        stale_articles_report = self.identify_stale_articles()

        # Log agent action
        log_agent_action(
            agent_name="content_health_checker",
            action="weekly_health_check_completed",
            details={
                "broken_links": len(broken_links_report["broken_links"]),
                "cannibalization_pairs": len(cannibalization_report["cannibalization_pairs"]),
                "refresh_candidates": len(stale_articles_report["refresh_candidates"]),
            }
        )

        return {
            "date": datetime.now().isoformat(),
            "broken_links": broken_links_report,
            "cannibalization": cannibalization_report,
            "stale_articles": stale_articles_report,
        }


def run_weekly_content_health_check():
    """Run weekly content health check (called by cron)."""
    checker = ContentHealthChecker()
    report = checker.run_weekly_health_check()

    print("\nContent Health Check Complete:")
    print(f"  - Broken Links: {report['broken_links']['broken_count']} found")
    print(f"  - Cannibalization Pairs: {len(report['cannibalization']['cannibalization_pairs'])} found")
    print(f"  - Refresh Candidates: {len(report['stale_articles']['refresh_candidates'])} articles")

    return report


if __name__ == "__main__":
    run_weekly_content_health_check()
