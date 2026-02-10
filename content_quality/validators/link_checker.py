"""
Link Checker

Validates all links in an article before publishing.
Catches broken internal links, draft-article references, and dead external URLs.
"""

import re
import requests
from typing import Dict, List
from urllib.parse import urlparse

from content_quality.config import LINK_CHECK_TIMEOUT, MAX_RETRY_ATTEMPTS
from content_quality.utils.text_utils import extract_links, is_internal_link, extract_slug_from_url


class LinkChecker:
    """Validates links in articles."""

    def __init__(self, ghost_api_client=None):
        """
        Initialize link checker.

        Args:
            ghost_api_client: Optional Ghost API client for checking internal links
        """
        self.ghost_client = ghost_api_client

    def _check_link_status(self, url: str, retry_count: int = 0) -> tuple:
        """
        Check HTTP status of a link.

        Returns:
            (status_code, final_url) tuple
        """
        try:
            # Use HEAD request first (faster)
            response = requests.head(
                url,
                timeout=LINK_CHECK_TIMEOUT,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 ClaimCoach-LinkChecker/1.0'}
            )

            # Some servers don't support HEAD, try GET if HEAD fails
            if response.status_code >= 400:
                response = requests.get(
                    url,
                    timeout=LINK_CHECK_TIMEOUT,
                    allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 ClaimCoach-LinkChecker/1.0'}
                )

            return (response.status_code, response.url)

        except requests.exceptions.Timeout:
            if retry_count < MAX_RETRY_ATTEMPTS:
                return self._check_link_status(url, retry_count + 1)
            return ("TIMEOUT", url)

        except requests.exceptions.RequestException as e:
            if retry_count < MAX_RETRY_ATTEMPTS:
                return self._check_link_status(url, retry_count + 1)
            return (f"ERROR: {str(e)}", url)

    def _check_internal_link_with_ghost(self, url: str) -> Dict:
        """
        Check internal link against Ghost API.

        Args:
            url: Internal link URL

        Returns:
            Result dict with status and details
        """
        if not self.ghost_client:
            # If no Ghost client, fall back to HTTP check
            status, final_url = self._check_link_status(url)
            return {
                "url": url,
                "status": "OK" if status == 200 else status,
                "severity": "NONE" if status == 200 else "ERROR",
                "message": f"HTTP {status}" if isinstance(status, int) else status
            }

        # Extract slug from URL
        slug = extract_slug_from_url(url)

        try:
            # Check if post exists in Ghost
            post = self.ghost_client.get_post_by_slug(slug)

            if post is None:
                return {
                    "url": url,
                    "status": "NOT_FOUND",
                    "severity": "ERROR",
                    "message": f"No post found with slug '{slug}'"
                }

            if post.get("status") != "published":
                return {
                    "url": url,
                    "status": "DRAFT",
                    "severity": "ERROR",
                    "message": f"Post '{slug}' exists but is not published (status: {post.get('status')})"
                }

            return {
                "url": url,
                "status": "OK",
                "severity": "NONE",
                "message": "Post exists and is published"
            }

        except Exception as e:
            return {
                "url": url,
                "status": "ERROR",
                "severity": "ERROR",
                "message": f"Error checking Ghost API: {str(e)}"
            }

    def check(self, article_markdown: str) -> Dict:
        """
        Check all links in article.

        Args:
            article_markdown: Article content in markdown

        Returns:
            Result dict with internal and external link results
        """
        links = extract_links(article_markdown)
        results = {"internal": [], "external": [], "status": "PASS"}

        for url, link_text in links:
            if is_internal_link(url, "claimcoach.app"):
                # Internal link
                result = self._check_internal_link_with_ghost(url)
                results["internal"].append(result)

                if result["severity"] == "ERROR":
                    results["status"] = "FAIL"

            else:
                # External link
                status, final_url = self._check_link_status(url)

                if isinstance(status, int):
                    if status >= 400:
                        results["external"].append({
                            "url": url,
                            "status": status,
                            "severity": "ERROR",
                            "message": f"External link returned HTTP {status}"
                        })
                        results["status"] = "FAIL"
                    elif status in [301, 302]:
                        results["external"].append({
                            "url": url,
                            "status": status,
                            "severity": "WARNING",
                            "message": f"Redirect detected: {url} → {final_url}"
                        })
                    else:
                        results["external"].append({
                            "url": url,
                            "status": status,
                            "severity": "NONE",
                            "message": f"OK (HTTP {status})"
                        })
                else:
                    # Timeout or error
                    results["external"].append({
                        "url": url,
                        "status": status,
                        "severity": "WARNING" if status == "TIMEOUT" else "ERROR",
                        "message": status
                    })

                    if status != "TIMEOUT":
                        results["status"] = "FAIL"

        return results


def check_links(article_markdown: str, ghost_api_client=None) -> Dict:
    """
    Convenience function to check links.

    Args:
        article_markdown: Article content
        ghost_api_client: Optional Ghost API client

    Returns:
        Link check result
    """
    checker = LinkChecker(ghost_api_client)
    return checker.check(article_markdown)
