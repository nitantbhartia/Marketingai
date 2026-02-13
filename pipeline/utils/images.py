"""Image resolution pipeline.

Converts Quill's image placeholders (``![alt](image:slug)``) into real URLs
before Ezra publishes.  Supports multiple backends with graceful fallback:

1. **Unsplash** — Free stock photos searched by alt text keywords
2. **Placeholder** — Branded placeholder images via placehold.co (zero-dependency fallback)

Adding new backends (DALL-E, Stable Diffusion, Midjourney) requires only
implementing ``resolve(alt_text, slug, keyword) -> str`` and registering
in ``ImageResolver.__init__``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend: Unsplash Source API (free, no key needed for low volume)
# ---------------------------------------------------------------------------
class UnsplashBackend:
    """Resolve images via the Unsplash Source API.

    The Source API redirects to a random photo matching the query.
    No API key is needed for the redirect-based endpoint.

    For higher volume (50+ req/hour), set an ``access_key`` and use
    the JSON search API instead.
    """

    BASE_URL = "https://api.unsplash.com"
    SOURCE_URL = "https://source.unsplash.com"

    def __init__(self, access_key: str = "", width: int = 1200, height: int = 630):
        self.access_key = access_key
        self.width = width
        self.height = height

    def resolve(self, alt_text: str, slug: str, keyword: str) -> str | None:
        """Return an Unsplash image URL for the given alt text.

        Uses the JSON API if an access_key is configured (returns a
        stable direct link), otherwise falls back to the Source redirect
        URL which works without authentication.
        """
        # Build search query from alt text — take meaningful words
        query_words = self._extract_search_terms(alt_text, keyword)
        query = " ".join(query_words)

        if self.access_key:
            return self._api_search(query)

        # Source URL (no key needed, returns redirect)
        encoded = urllib.parse.quote(query)
        return (
            f"{self.SOURCE_URL}/{self.width}x{self.height}"
            f"/?{encoded}"
        )

    def _api_search(self, query: str) -> str | None:
        """Use the Unsplash JSON API to find a photo."""
        try:
            params = urllib.parse.urlencode({
                "query": query,
                "per_page": 1,
                "orientation": "landscape",
            })
            url = f"{self.BASE_URL}/search/photos?{params}"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Client-ID {self.access_key}",
                "Accept-Version": "v1",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                results = data.get("results", [])
                if results:
                    # Return the regular-size URL with width parameter
                    raw_url = results[0]["urls"]["regular"]
                    return raw_url
        except Exception as e:
            logger.warning(f"Unsplash API search failed for '{query}': {e}")
        return None

    @staticmethod
    def _extract_search_terms(alt_text: str, keyword: str) -> list[str]:
        """Extract meaningful search terms from alt text."""
        # Common filler words to skip
        stop_words = {
            "a", "an", "the", "of", "for", "in", "on", "to", "and", "or",
            "is", "are", "was", "were", "with", "by", "from", "at", "this",
            "that", "it", "its", "image", "showing", "diagram", "chart",
            "infographic", "explaining", "about", "key", "factors",
        }
        words = re.findall(r"\b[a-zA-Z]{3,}\b", alt_text.lower())
        terms = [w for w in words if w not in stop_words]

        # If we got very few terms, add the keyword
        if len(terms) < 2:
            kw_words = re.findall(r"\b[a-zA-Z]{3,}\b", keyword.lower())
            terms.extend(w for w in kw_words if w not in stop_words)

        # Cap at 5 terms for better search relevance
        return terms[:5]


# ---------------------------------------------------------------------------
# Backend: Branded placeholder images (zero-dependency fallback)
# ---------------------------------------------------------------------------
class PlaceholderBackend:
    """Generate branded placeholder images via placehold.co.

    These aren't "real" images but they render as properly sized boxes
    with descriptive text — far better than broken image icons.
    Useful as a fallback when Unsplash is unavailable.
    """

    def __init__(self, width: int = 1200, height: int = 630):
        self.width = width
        self.height = height
        # ClaimCoach brand colors
        self.bg_color = "1a365d"  # Dark blue
        self.text_color = "ffffff"  # White

    def resolve(self, alt_text: str, slug: str, keyword: str) -> str | None:
        """Return a placehold.co URL with descriptive text overlay."""
        # Truncate text to fit in the image
        label = alt_text[:60] if alt_text else keyword[:40]
        encoded = urllib.parse.quote(label)
        return (
            f"https://placehold.co/{self.width}x{self.height}"
            f"/{self.bg_color}/{self.text_color}"
            f"?text={encoded}"
        )


# ---------------------------------------------------------------------------
# Main resolver — chains backends with fallback
# ---------------------------------------------------------------------------
class ImageResolver:
    """Resolve ``image:slug`` placeholders in article content to real URLs.

    Chains multiple backends in priority order:
    1. Unsplash (high-quality stock photos)
    2. Placeholder (branded fallback — always succeeds)

    Usage::

        resolver = ImageResolver(unsplash_key="optional")
        resolved_content = resolver.resolve_all(
            content=article.markdown_content,
            keyword=article.target_keyword,
        )
    """

    # Pattern matching Quill's image placeholder format
    PLACEHOLDER_PATTERN = re.compile(
        r"!\[([^\]]*)\]\((image:[^)]+)\)"
    )

    def __init__(
        self,
        unsplash_key: str = "",
        image_width: int = 1200,
        image_height: int = 630,
        cache_dir: str | Path | None = None,
    ):
        self.backends: list = []

        # Primary: Unsplash
        self.backends.append(
            UnsplashBackend(
                access_key=unsplash_key,
                width=image_width,
                height=image_height,
            )
        )

        # Fallback: Branded placeholder (always succeeds)
        self.backends.append(
            PlaceholderBackend(width=image_width, height=image_height)
        )

        # Simple file-based URL cache to avoid re-resolving the same placeholder
        self._cache: dict[str, str] = {}
        self._cache_path: Path | None = None
        if cache_dir:
            self._cache_path = Path(cache_dir) / "image_cache.json"
            self._load_cache()

    def resolve_all(self, content: str, keyword: str) -> str:
        """Replace all ``image:slug`` placeholders with real URLs.

        Returns the content with placeholders replaced.  Unresolvable
        placeholders are left as-is (shouldn't happen with the
        PlaceholderBackend as final fallback).
        """
        def _replacer(match: re.Match) -> str:
            alt_text = match.group(1)
            placeholder_url = match.group(2)  # e.g. "image:settlement-chart"
            slug = placeholder_url.replace("image:", "")

            # Check cache first
            cache_key = f"{slug}:{keyword}"
            if cache_key in self._cache:
                real_url = self._cache[cache_key]
                return f"![{alt_text}]({real_url})"

            # Try each backend in priority order
            real_url = self._resolve_with_backends(alt_text, slug, keyword)
            if real_url:
                self._cache[cache_key] = real_url
                return f"![{alt_text}]({real_url})"

            # No backend succeeded — leave placeholder as-is
            logger.warning(f"Could not resolve image placeholder: {placeholder_url}")
            return match.group(0)

        resolved = self.PLACEHOLDER_PATTERN.sub(_replacer, content)

        # Save cache if we resolved anything new
        if self._cache_path:
            self._save_cache()

        return resolved

    def _resolve_with_backends(
        self, alt_text: str, slug: str, keyword: str,
    ) -> str | None:
        """Try each backend until one returns a URL."""
        for backend in self.backends:
            try:
                url = backend.resolve(alt_text, slug, keyword)
                if url:
                    backend_name = type(backend).__name__
                    logger.debug(
                        f"Resolved '{slug}' via {backend_name}: {url[:80]}"
                    )
                    return url
            except Exception as e:
                backend_name = type(backend).__name__
                logger.warning(f"{backend_name} failed for '{slug}': {e}")
        return None

    def _load_cache(self) -> None:
        """Load URL cache from disk."""
        if self._cache_path and self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        """Persist URL cache to disk."""
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, indent=2))
