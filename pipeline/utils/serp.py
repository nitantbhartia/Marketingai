"""SERP analysis and People Also Ask extraction.

Pulls real search data before article generation so Quill's outlines
are informed by what's actually ranking and what readers are asking.

Supports two backends:
1. **SerpAPI** — Full SERP + PAA + Related Searches (paid, ~$50/mo)
2. **Google Custom Search API** — Top 10 results (free, 100 queries/day)

Both return a ``SerpInsight`` dict that feeds into Quill's outline prompt.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SerpInsight:
    """Structured SERP intelligence for a keyword."""

    keyword: str = ""
    # Top organic results
    competitors: list[dict] = field(default_factory=list)
    # People Also Ask questions (gold for FAQ sections)
    paa_questions: list[str] = field(default_factory=list)
    # Related searches (LSI / semantic variants)
    related_searches: list[str] = field(default_factory=list)
    # Competitor heading analysis
    competitor_headings: list[str] = field(default_factory=list)
    # Average word count of top results (if available)
    avg_competitor_word_count: int = 0
    # Content gaps — topics competitors cover that we should too
    content_gaps: list[str] = field(default_factory=list)
    # FAQ themes extracted from PAA / related searches
    faq_themes: list[str] = field(default_factory=list)

    def to_outline_context(self) -> str:
        """Format SERP data for injection into Quill's outline prompt."""
        parts: list[str] = []

        if self.competitors:
            parts.append("=== TOP RANKING PAGES (SERP Top 10 benchmark) ===")
            for c in self.competitors[:10]:
                title = c.get("title", "").strip()
                url = c.get("url", "").strip()
                if title and url:
                    parts.append(f"- {title} — {url}")
            parts.append("")

        if self.paa_questions:
            parts.append("=== PEOPLE ALSO ASK (your FAQ section MUST answer these) ===")
            for q in self.paa_questions[:6]:
                parts.append(f"- {q}")
            parts.append("")

        if self.faq_themes:
            parts.append("=== FAQ THEMES (cluster these in your FAQ and H2s) ===")
            for t in self.faq_themes[:8]:
                parts.append(f"- {t}")
            parts.append("")

        if self.related_searches:
            parts.append("=== RELATED SEARCHES (weave these terms naturally into content) ===")
            for s in self.related_searches[:8]:
                parts.append(f"- {s}")
            parts.append("")

        if self.competitor_headings:
            parts.append("=== COMPETITOR H2 HEADINGS (ensure you cover these topics) ===")
            for h in self.competitor_headings[:10]:
                parts.append(f"- {h}")
            parts.append("")

        if self.content_gaps:
            parts.append("=== CONTENT GAPS (topics competitors miss — your opportunity) ===")
            for g in self.content_gaps[:5]:
                parts.append(f"- {g}")
            parts.append("NON-NEGOTIABLE: every content gap above must be covered in the article.")
            parts.append("")

        if self.avg_competitor_word_count > 0:
            parts.append(
                f"=== COMPETITOR BENCHMARK ===\n"
                f"Average top-10 word count: {self.avg_competitor_word_count} words\n"
                f"Top {len(self.competitors)} results analyzed\n"
            )

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Backend: SerpAPI (full SERP + PAA + related searches)
# ---------------------------------------------------------------------------
class SerpAPIBackend:
    """Query SerpAPI for comprehensive SERP data.

    Free tier: 100 searches/month.  Paid: ~$50/mo for 5,000 searches.
    Returns organic results, PAA, related searches, and knowledge graph.
    """

    BASE_URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def query(self, keyword: str, location: str = "United States") -> SerpInsight:
        """Run a SERP query and extract structured insights."""
        params = urllib.parse.urlencode({
            "q": keyword,
            "location": location,
            "hl": "en",
            "gl": "us",
            "api_key": self.api_key,
        })
        url = f"{self.BASE_URL}?{params}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return self._parse_response(keyword, data)
        except Exception as e:
            logger.warning(f"SerpAPI query failed for '{keyword}': {e}")
            return SerpInsight(keyword=keyword)

    @staticmethod
    def _parse_response(keyword: str, data: dict) -> SerpInsight:
        """Parse SerpAPI JSON into structured insight."""
        insight = SerpInsight(keyword=keyword)

        # Organic results
        for result in data.get("organic_results", [])[:10]:
            insight.competitors.append({
                "title": result.get("title", ""),
                "url": result.get("link", ""),
                "snippet": result.get("snippet", ""),
                "position": result.get("position", 0),
            })

        # People Also Ask
        for paa in data.get("related_questions", []):
            question = paa.get("question", "")
            if question:
                insight.paa_questions.append(question)

        # Related searches
        for related in data.get("related_searches", []):
            query = related.get("query", "")
            if query:
                insight.related_searches.append(query)

        return insight


# ---------------------------------------------------------------------------
# Backend: Google Custom Search API (free, 100 queries/day)
# ---------------------------------------------------------------------------
class GoogleCSEBackend:
    """Query Google Custom Search Engine for top results.

    Free tier: 100 queries/day (no PAA, but gets titles + snippets).
    Requires a CSE ID and API key from Google Cloud Console.
    """

    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, cse_id: str):
        self.api_key = api_key
        self.cse_id = cse_id

    def query(self, keyword: str) -> SerpInsight:
        """Run a Custom Search query and extract insights."""
        params = urllib.parse.urlencode({
            "q": keyword,
            "key": self.api_key,
            "cx": self.cse_id,
            "num": 10,
        })
        url = f"{self.BASE_URL}?{params}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return self._parse_response(keyword, data)
        except Exception as e:
            logger.warning(f"Google CSE query failed for '{keyword}': {e}")
            return SerpInsight(keyword=keyword)

    @staticmethod
    def _parse_response(keyword: str, data: dict) -> SerpInsight:
        """Parse Google CSE JSON into structured insight."""
        insight = SerpInsight(keyword=keyword)

        for i, item in enumerate(data.get("items", [])[:10]):
            insight.competitors.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "position": i + 1,
            })

        # Extract related searches from spelling/correction suggestions
        if "spelling" in data:
            corrected = data["spelling"].get("correctedQuery", "")
            if corrected and corrected != keyword:
                insight.related_searches.append(corrected)

        return insight


# ---------------------------------------------------------------------------
# Backend: Google Autocomplete (free, no key needed, good for related terms)
# ---------------------------------------------------------------------------
class AutocompleteBackend:
    """Extract related searches via Google Autocomplete API (free).

    No API key required.  Good for discovering related terms and
    long-tail variations that feed into LSI keyword coverage.
    """

    BASE_URL = "https://suggestqueries.google.com/complete/search"

    def query(self, keyword: str) -> list[str]:
        """Get autocomplete suggestions for a keyword."""
        params = urllib.parse.urlencode({
            "client": "firefox",
            "q": keyword,
        })
        url = f"{self.BASE_URL}?{params}"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                # Response format: ["query", ["suggestion1", "suggestion2", ...]]
                if isinstance(data, list) and len(data) >= 2:
                    return [s for s in data[1] if s.lower() != keyword.lower()]
        except Exception as e:
            logger.debug(f"Autocomplete query failed for '{keyword}': {e}")
        return []


# ---------------------------------------------------------------------------
# Main SERP analyzer — chains backends
# ---------------------------------------------------------------------------
class SerpAnalyzer:
    """Analyze SERP for a keyword using available backends.

    Usage::

        analyzer = SerpAnalyzer(serpapi_key="...", google_cse_key="...", google_cse_id="...")
        insight = analyzer.analyze("total loss settlement california")
        outline_context = insight.to_outline_context()
    """

    def __init__(
        self,
        serpapi_key: str = "",
        google_cse_key: str = "",
        google_cse_id: str = "",
    ):
        self._serpapi = SerpAPIBackend(serpapi_key) if serpapi_key else None
        self._google_cse = (
            GoogleCSEBackend(google_cse_key, google_cse_id)
            if google_cse_key and google_cse_id
            else None
        )
        self._autocomplete = AutocompleteBackend()

    @property
    def has_serp_backend(self) -> bool:
        """Check if a real SERP backend is configured."""
        return bool(self._serpapi or self._google_cse)

    def analyze(self, keyword: str, state: str = "") -> SerpInsight:
        """Run full SERP analysis and return structured insight.

        Combines data from the best available backend + autocomplete
        for related term discovery.
        """
        # Use the best available backend
        if self._serpapi:
            insight = self._serpapi.query(keyword)
        elif self._google_cse:
            insight = self._google_cse.query(keyword)
        else:
            insight = SerpInsight(keyword=keyword)
            logger.info("No SERP API configured — using autocomplete only")

        # Always enrich with autocomplete suggestions for related terms
        autocomplete_terms = self._autocomplete.query(keyword)
        # Also query with state prefix for geo-specific suggestions
        if state:
            state_terms = self._autocomplete.query(f"{keyword} {state}")
            autocomplete_terms.extend(state_terms)

        # Deduplicate and merge with existing related searches
        existing = set(s.lower() for s in insight.related_searches)
        for term in autocomplete_terms:
            if term.lower() not in existing:
                insight.related_searches.append(term)
                existing.add(term.lower())

        # Extract competitor headings from titles and snippets
        if insight.competitors:
            insight.competitor_headings = self._extract_headings(
                insight.competitors
            )

        insight.faq_themes = self._extract_faq_themes(
            insight.paa_questions, insight.related_searches
        )
        insight.content_gaps = self._infer_content_gaps(
            keyword=keyword,
            state=state,
            competitor_headings=insight.competitor_headings,
            paa_questions=insight.paa_questions,
        )

        return insight

    @staticmethod
    def _extract_headings(competitors: list[dict]) -> list[str]:
        """Extract likely H2 topics from competitor titles and snippets.

        Competitor titles reveal the angles they chose; snippets reveal
        subtopics Google highlighted as relevant.
        """
        headings: list[str] = []
        seen = set()

        for comp in competitors[:7]:
            # Title often reveals the main angle
            title = comp.get("title", "")
            if title and title.lower() not in seen:
                headings.append(title)
                seen.add(title.lower())

            # Snippet reveals what Google considers relevant content
            snippet = comp.get("snippet", "")
            # Look for sentence fragments that suggest subtopics
            for phrase in re.split(r"[.!?…]", snippet):
                phrase = phrase.strip()
                if 20 < len(phrase) < 80 and phrase.lower() not in seen:
                    headings.append(phrase)
                    seen.add(phrase.lower())

        return headings[:12]

    @staticmethod
    def _extract_faq_themes(
        paa_questions: list[str], related_searches: list[str]
    ) -> list[str]:
        """Derive FAQ themes from PAA and related searches."""
        themes: list[str] = []
        seen: set[str] = set()
        source = list(paa_questions[:8]) + list(related_searches[:8])
        for raw in source:
            t = raw.strip().lower()
            if not t:
                continue
            t = re.sub(
                r"^(what|how|when|why|where|who|can|does|do|is|are)\s+",
                "",
                t,
            )
            t = re.sub(r"[?.,!]", "", t).strip()
            if len(t) < 8:
                continue
            if t not in seen:
                seen.add(t)
                themes.append(t)
        return themes[:10]

    @staticmethod
    def _infer_content_gaps(
        keyword: str,
        state: str,
        competitor_headings: list[str],
        paa_questions: list[str],
    ) -> list[str]:
        """Identify high-value subtopics likely missing from competitors.

        This is heuristic by design: it favors practical subtopics that
        improve usefulness and conversion for insurance settlement content.
        """
        corpus = " ".join(competitor_headings + paa_questions).lower()
        kw = (keyword or "").lower()

        candidates: list[tuple[str, tuple[str, ...]]] = []
        if "total loss" in kw or "totaled" in kw:
            candidates.extend([
                ("ACV formula with worked example", ("actual cash value", "acv", "formula")),
                ("Threshold math example (repair cost vs ACV)", ("threshold", "repair", "percent")),
                ("Dispute workflow with scripts/checklist", ("dispute", "appeal", "negotiate")),
                ("Required documents checklist", ("documents", "paperwork", "evidence")),
                ("Settlement line-item breakdown", ("sales tax", "fees", "registration")),
            ])
        if "diminished value" in kw:
            candidates.extend([
                ("How diminished value is calculated", ("formula", "multiplier", "17c")),
                ("Comparable vehicle evidence strategy", ("comparables", "comps", "listings")),
                ("Negotiation timeline and escalation path", ("timeline", "escalation", "complaint")),
            ])
        if not candidates:
            candidates.extend([
                ("Step-by-step action plan", ("step", "checklist", "what to do")),
                ("Common insurer tactics and counter-moves", ("mistakes", "lowball", "tactics")),
                ("FAQ for edge cases", ("faq", "questions", "common questions")),
            ])

        # Encourage state-specific authority when a state is targeted.
        if state:
            candidates.append((
                f"{state} DOI complaint and mediation path",
                ("department of insurance", "doi", "complaint", "mediation"),
            ))
            candidates.append((
                f"{state} statute citation section",
                ("statute", "code", "§", state.lower()),
            ))

        gaps: list[str] = []
        for label, hints in candidates:
            if not any(h in corpus for h in hints):
                gaps.append(label)
        # De-dup while preserving order
        out: list[str] = []
        seen: set[str] = set()
        for g in gaps:
            if g not in seen:
                seen.add(g)
                out.append(g)
        return out[:8]
