"""
Readability Analyzer

Ensures articles are written at an 8th-grade reading level.
Target audience: stressed car owners, not insurance professionals.
"""

import re
from typing import Dict, List
import textstat

from content_quality.config import MIN_READABILITY_SCORE
from content_quality.utils.text_utils import strip_markdown


# Word simplification suggestions
SIMPLIFY_MAP = {
    "automobile": "car",
    "vehicle": "car",
    "compensation": "payment",
    "documentation": "paperwork",
    "determination": "decision",
    "approximately": "about",
    "demonstrate": "show",
    "subsequently": "then",
    "utilize": "use",
    "obtain": "get",
    "sufficient": "enough",
    "reimburse": "pay back",
    "navigate": "deal with",
    "comprehensive": "full",
    "modification": "change",
    "considerable": "big",
    "substantial": "large",
    "methodology": "method",
    "significantly": "a lot",
    "accumulate": "add up",
    "initiate": "start",
    "terminate": "end",
    "facilitate": "help",
    "correspond": "match",
    "communicate": "talk to",
    "circumstances": "situation",
    "requirements": "rules",
    "obligation": "duty",
    "assessment": "review",
    "deficiency": "gap",
    "discrepancy": "difference",
    "jurisdiction": "state",
    "regulation": "rule",
}


class ReadabilityAnalyzer:
    """Analyzes article readability."""

    def __init__(self):
        """Initialize analyzer."""
        pass

    def _find_long_sentences(self, text: str, max_words: int = 30) -> List[str]:
        """Find sentences exceeding max_words."""
        sentences = re.split(r'[.!?]+', text)
        long_sentences = []

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            word_count = len(sentence.split())
            if word_count > max_words:
                long_sentences.append(sentence[:200] + "..." if len(sentence) > 200 else sentence)

        return long_sentences

    def _find_complex_words(self, text: str, min_syllables: int = 3) -> List[str]:
        """
        Find complex words (3+ syllables).
        Excludes proper nouns and known technical terms.
        """
        # Technical terms that are OK to use
        whitelist = {
            "insurance", "adjuster", "settlement", "claimcoach", "california",
            "diminished", "depreciation", "total loss", "actual cash value",
            "statute", "regulation", "property", "liability", "coverage",
            "policy", "premium", "deductible"
        }

        words = re.findall(r'\b[a-z]+\b', text.lower())
        complex_words = []

        for word in words:
            if len(word) <= 4:  # Short words are usually fine
                continue

            if word in whitelist:
                continue

            # Use textstat to count syllables
            syllables = textstat.syllable_count(word)
            if syllables >= min_syllables:
                # Suggest simpler alternative if available
                if word in SIMPLIFY_MAP:
                    complex_words.append(f"{word} → {SIMPLIFY_MAP[word]}")
                else:
                    complex_words.append(word)

        # Remove duplicates
        return list(set(complex_words))

    def analyze(self, article_markdown: str) -> Dict:
        """
        Analyze article readability.

        Args:
            article_markdown: Full article content

        Returns:
            Analysis dict with scores and issues
        """
        # Strip markdown formatting
        clean_text = strip_markdown(article_markdown)

        if not clean_text.strip():
            return {
                "status": "FAIL",
                "flesch_reading_ease": 0,
                "issues": [{"metric": "empty_text", "fix": "Article has no text content"}]
            }

        # Calculate readability metrics
        results = {
            "flesch_kincaid_grade": textstat.flesch_kincaid_grade(clean_text),
            "flesch_reading_ease": textstat.flesch_reading_ease(clean_text),
            "gunning_fog": textstat.gunning_fog(clean_text),
            "avg_sentence_length": textstat.avg_sentence_length(clean_text),
            "avg_syllables_per_word": textstat.avg_syllables_per_word(clean_text),
            "word_count": textstat.lexicon_count(clean_text),
            "sentence_count": textstat.sentence_count(clean_text),
        }

        issues = []

        # Flesch Reading Ease: target 60+ (8th grade level)
        if results["flesch_reading_ease"] < MIN_READABILITY_SCORE:
            issues.append({
                "metric": "flesch_reading_ease",
                "value": round(results["flesch_reading_ease"], 1),
                "target": f"{MIN_READABILITY_SCORE}+",
                "fix": "Simplify vocabulary and shorten sentences. Target 8th grade level."
            })

        # Average sentence length: target < 20 words
        if results["avg_sentence_length"] > 20:
            issues.append({
                "metric": "avg_sentence_length",
                "value": round(results["avg_sentence_length"], 1),
                "target": "< 20 words",
                "fix": "Break up long sentences. Each sentence should express one idea."
            })

        # Find specific long sentences (> 30 words)
        long_sentences = self._find_long_sentences(clean_text, max_words=30)
        if long_sentences:
            issues.append({
                "metric": "long_sentences",
                "count": len(long_sentences),
                "examples": long_sentences[:3],  # Show first 3
                "fix": "These sentences exceed 30 words and should be split."
            })

        # Find complex words (3+ syllables)
        complex_words = self._find_complex_words(clean_text, min_syllables=3)
        complex_percentage = len(complex_words) / results["word_count"] * 100 if results["word_count"] > 0 else 0

        if complex_percentage > 8:  # > 8% complex words
            issues.append({
                "metric": "complex_vocabulary",
                "percentage": round(complex_percentage, 1),
                "target": "< 8%",
                "examples": complex_words[:10],
                "fix": "Replace multi-syllable words with simpler alternatives where possible."
            })

        # Determine status
        status = "PASS" if results["flesch_reading_ease"] >= MIN_READABILITY_SCORE else "FAIL"

        results["status"] = status
        results["issues"] = issues

        return results


def analyze_readability(article_markdown: str) -> Dict:
    """
    Convenience function to analyze readability.

    Args:
        article_markdown: Full article content

    Returns:
        Readability analysis result
    """
    analyzer = ReadabilityAnalyzer()
    return analyzer.analyze(article_markdown)
