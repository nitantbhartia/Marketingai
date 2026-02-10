"""
Internal Link Optimizer

Automatically suggests and creates contextual internal links between articles.
Builds topic authority and improves site structure.
"""

import re
from typing import List, Dict, Tuple
from collections import defaultdict
import sqlite3

from content_quality.db import get_db, add_internal_link


class InternalLinkOptimizer:
    """
    Suggests contextual internal links between related articles.

    Workflow:
    1. Analyze article content and keywords
    2. Find related published articles
    3. Identify natural linking opportunities
    4. Calculate link quality scores
    5. Return top recommendations
    """

    def __init__(self, min_quality_score: float = 0.6):
        self.min_quality_score = min_quality_score
        self.max_links_per_article = 5

    def suggest_links(self, article_id: int, article_content: str, article_keyword: str) -> List[Dict]:
        """
        Suggest internal links for an article.

        Returns list of suggestions with scores.
        """
        suggestions = []

        # Get all published articles except this one
        with get_db() as db:
            cursor = db.execute("""
                SELECT id, title, slug, target_keyword, target_state, markdown_content, published_url
                FROM articles
                WHERE status = 'done'
                AND published_url IS NOT NULL
                AND id != ?
                ORDER BY last_gsc_position ASC NULLS LAST
                LIMIT 100
            """, (article_id,))
            candidates = [dict(row) for row in cursor.fetchall()]

        if not candidates:
            return []

        # Extract key phrases from content
        key_phrases = self._extract_key_phrases(article_content)

        # For each candidate, check if it's a good link opportunity
        for candidate in candidates:
            # Calculate relevance score
            relevance = self._calculate_relevance(
                article_keyword,
                article_content,
                key_phrases,
                candidate
            )

            if relevance < self.min_quality_score:
                continue

            # Find best anchor text in content
            anchor_opportunities = self._find_anchor_opportunities(
                article_content,
                candidate["title"],
                candidate["target_keyword"]
            )

            if not anchor_opportunities:
                continue

            # Take best anchor
            best_anchor = anchor_opportunities[0]

            suggestions.append({
                "target_article_id": candidate["id"],
                "target_title": candidate["title"],
                "target_url": candidate["published_url"],
                "target_keyword": candidate["target_keyword"],
                "anchor_text": best_anchor["text"],
                "anchor_context": best_anchor["context"],
                "quality_score": relevance,
                "position_in_content": best_anchor["position"]
            })

        # Sort by quality score and limit
        suggestions.sort(key=lambda x: x["quality_score"], reverse=True)
        return suggestions[:self.max_links_per_article]

    def apply_suggestions(self, source_article_id: int, suggestions: List[Dict]) -> int:
        """
        Apply internal link suggestions by recording them in database.

        Returns number of links added.
        """
        count = 0
        for suggestion in suggestions:
            add_internal_link(
                source_article_id=source_article_id,
                target_article_id=suggestion["target_article_id"],
                anchor_text=suggestion["anchor_text"],
                quality_score=suggestion["quality_score"]
            )
            count += 1

        return count

    def inject_links_in_markdown(self, markdown_content: str, suggestions: List[Dict]) -> str:
        """
        Inject internal links into markdown content.

        Returns updated markdown.
        """
        updated_content = markdown_content

        # Sort by position (process from end to start to maintain positions)
        sorted_suggestions = sorted(suggestions, key=lambda x: x["position_in_content"], reverse=True)

        for suggestion in sorted_suggestions:
            anchor_text = suggestion["anchor_text"]
            target_url = suggestion["target_url"]

            # Find exact occurrence in context
            # Replace first occurrence with markdown link
            pattern = re.escape(anchor_text)
            replacement = f"[{anchor_text}]({target_url})"

            # Only replace if not already a link
            if f"[{anchor_text}]" not in updated_content:
                updated_content = re.sub(pattern, replacement, updated_content, count=1)

        return updated_content

    def _extract_key_phrases(self, content: str) -> List[str]:
        """Extract important phrases from content."""
        # Common insurance/total loss phrases
        phrases = [
            "total loss",
            "actual cash value",
            "ACV",
            "fair market value",
            "settlement offer",
            "insurance claim",
            "diminished value",
            "salvage value",
            "insurance adjuster",
            "claim settlement",
            "total loss threshold",
            "insurance payout",
            "third party claim",
            "first party claim",
            "insurance settlement",
            "vehicle valuation"
        ]

        content_lower = content.lower()
        found_phrases = [p for p in phrases if p in content_lower]

        return found_phrases

    def _calculate_relevance(
        self,
        source_keyword: str,
        source_content: str,
        source_phrases: List[str],
        candidate: Dict
    ) -> float:
        """
        Calculate how relevant a candidate article is for linking.

        Returns score 0.0-1.0
        """
        score = 0.0
        content_lower = source_content.lower()

        # 1. Keyword overlap (0.4 weight)
        if source_keyword and candidate["target_keyword"]:
            source_kw_words = set(source_keyword.lower().split())
            target_kw_words = set(candidate["target_keyword"].lower().split())
            overlap = len(source_kw_words & target_kw_words)
            if overlap > 0:
                score += 0.4 * min(1.0, overlap / len(source_kw_words))

        # 2. State matching (0.2 weight)
        if candidate.get("target_state"):
            state = candidate["target_state"].lower()
            if state in content_lower:
                score += 0.2

        # 3. Key phrase overlap (0.3 weight)
        candidate_content_lower = (candidate.get("markdown_content") or "").lower()
        phrase_overlap = sum(1 for phrase in source_phrases if phrase in candidate_content_lower)
        if phrase_overlap > 0:
            score += 0.3 * min(1.0, phrase_overlap / max(len(source_phrases), 1))

        # 4. Title mention (0.1 weight bonus)
        candidate_title_lower = candidate["title"].lower()
        # Check if title or partial title mentioned
        title_words = candidate_title_lower.split()
        if any(word in content_lower for word in title_words if len(word) > 4):
            score += 0.1

        return min(1.0, score)

    def _find_anchor_opportunities(
        self,
        content: str,
        target_title: str,
        target_keyword: str
    ) -> List[Dict]:
        """
        Find good anchor text opportunities in content.

        Returns list of {text, context, position}
        """
        opportunities = []
        content_lower = content.lower()

        # Candidate anchor texts (in priority order)
        candidates = []

        # 1. Target keyword
        if target_keyword:
            candidates.append(target_keyword.lower())

        # 2. Title or partial title
        title_lower = target_title.lower()
        candidates.append(title_lower)

        # Also try title without common prefixes
        title_cleaned = re.sub(r'^(how to|what is|understanding|guide to)\s+', '', title_lower)
        if title_cleaned != title_lower:
            candidates.append(title_cleaned)

        # 3. Extract key phrases from title
        title_words = title_lower.split()
        if len(title_words) >= 2:
            # Try last 2-4 words of title (often the core topic)
            for i in range(2, min(5, len(title_words) + 1)):
                phrase = " ".join(title_words[-i:])
                candidates.append(phrase)

        # Find occurrences
        for anchor_text in candidates:
            # Find all occurrences
            pattern = re.compile(re.escape(anchor_text), re.IGNORECASE)
            for match in pattern.finditer(content):
                position = match.start()

                # Get context (50 chars before/after)
                context_start = max(0, position - 50)
                context_end = min(len(content), position + len(anchor_text) + 50)
                context = content[context_start:context_end]

                # Skip if already a link
                if "[" in context[context.find(anchor_text)-1:context.find(anchor_text)+1]:
                    continue

                opportunities.append({
                    "text": anchor_text,
                    "context": context,
                    "position": position
                })

                # Only take first occurrence of each anchor
                break

        # Sort by position (earlier in content is better)
        opportunities.sort(key=lambda x: x["position"])

        return opportunities


def optimize_article_links(article_id: int, markdown_content: str, target_keyword: str) -> Dict:
    """
    Helper function to optimize internal links for an article.

    Returns:
        {
            "suggestions": [...],
            "updated_markdown": "...",
            "links_added": int
        }
    """
    optimizer = InternalLinkOptimizer()

    # Get suggestions
    suggestions = optimizer.suggest_links(article_id, markdown_content, target_keyword)

    if not suggestions:
        return {
            "suggestions": [],
            "updated_markdown": markdown_content,
            "links_added": 0
        }

    # Record in database
    links_added = optimizer.apply_suggestions(article_id, suggestions)

    # Inject into markdown
    updated_markdown = optimizer.inject_links_in_markdown(markdown_content, suggestions)

    return {
        "suggestions": suggestions,
        "updated_markdown": updated_markdown,
        "links_added": links_added
    }
