"""Readability scoring utilities."""

from __future__ import annotations

import re
import math


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting so readability scores reflect prose only.

    Strips headers, link syntax, image syntax, bold/italic/code markers,
    and list markers. Keeps the actual words.
    """
    # Remove full header lines (# Header -> "")
    clean = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    # Remove link syntax but keep link text
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    # Remove images
    clean = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", clean)
    # Remove bold/italic/code markers
    clean = re.sub(r"[*_~`]", "", clean)
    # Remove list markers (- or * at start of line)
    clean = re.sub(r"^\s*[-*+]\s+", "", clean, flags=re.MULTILINE)
    # Remove numbered list markers
    clean = re.sub(r"^\s*\d+\.\s+", "", clean, flags=re.MULTILINE)
    # Collapse multiple blank lines
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def count_syllables(word: str) -> int:
    """Estimate syllable count for an English word."""
    word = word.lower().strip()
    if not word:
        return 0
    if len(word) <= 3:
        return 1

    # Remove trailing 'e'
    word = re.sub(r"e$", "", word)

    # Count vowel groups
    vowel_groups = re.findall(r"[aeiouy]+", word)
    count = len(vowel_groups)
    return max(1, count)


def flesch_kincaid_score(text: str) -> float:
    """Calculate Flesch-Kincaid Reading Ease score.

    Higher = easier to read. Target: 60+ (8th grade level).
    Strips markdown formatting so headers don't skew the score.
    """
    text = _strip_markdown(text)
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0.0

    words = re.findall(r"\b[a-zA-Z]+\b", text)
    if not words:
        return 0.0

    total_sentences = len(sentences)
    total_words = len(words)
    total_syllables = sum(count_syllables(w) for w in words)

    if total_sentences == 0 or total_words == 0:
        return 0.0

    score = (
        206.835
        - 1.015 * (total_words / total_sentences)
        - 84.6 * (total_syllables / total_words)
    )
    return round(max(0, min(100, score)), 1)


def avg_sentence_length(text: str) -> float:
    """Calculate average sentence length in words."""
    text = _strip_markdown(text)
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0.0

    words_per_sentence = [len(re.findall(r"\b\w+\b", s)) for s in sentences]
    return round(sum(words_per_sentence) / len(words_per_sentence), 1)


def avg_paragraph_length(text: str) -> float:
    """Calculate average paragraph length in sentences."""
    text = _strip_markdown(text)
    paragraphs = re.split(r"\n\n+", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if not paragraphs:
        return 0.0

    sentences_per_para = []
    for para in paragraphs:
        sents = re.split(r"[.!?]+", para)
        sents = [s.strip() for s in sents if s.strip()]
        sentences_per_para.append(len(sents))

    return round(sum(sentences_per_para) / len(sentences_per_para), 1)


def word_count(text: str) -> int:
    """Count words in text, excluding markdown formatting."""
    # Strip markdown headers, links, images
    clean = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    clean = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", clean)
    clean = re.sub(r"[*_~`]", "", clean)

    words = re.findall(r"\b\w+\b", clean)
    return len(words)


def passive_voice_ratio(text: str) -> float:
    """Estimate fraction of sentences that use passive voice.

    Looks for common passive constructions: "was/were/is/are/been/being + past participle".
    Returns a ratio between 0.0 and 1.0.
    """
    clean = _strip_markdown(text)
    sentences = re.split(r"[.!?]+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0.0

    # Pattern: be-verb + optional adverb + past participle (-ed, -en, or irregular)
    passive_pattern = re.compile(
        r"\b(?:is|are|was|were|been|being|be)\b"
        r"\s+(?:\w+ly\s+)?"
        r"(?:\w+ed|written|broken|chosen|driven|eaten|fallen|forgotten|frozen|given|"
        r"hidden|known|paid|proven|seen|shown|spoken|stolen|taken|told|worn)\b",
        re.IGNORECASE,
    )

    passive_count = sum(1 for s in sentences if passive_pattern.search(s))
    return round(passive_count / len(sentences), 3)


# Common transition words/phrases grouped by function
_TRANSITION_PHRASES = [
    # Addition
    "in addition", "furthermore", "moreover", "also", "additionally",
    # Contrast
    "however", "on the other hand", "nevertheless", "in contrast",
    "conversely", "although", "even though", "while", "whereas",
    # Cause / effect
    "therefore", "as a result", "consequently", "because of this",
    "for this reason", "thus", "so",
    # Example
    "for example", "for instance", "such as", "specifically",
    # Sequence
    "first", "second", "third", "next", "then", "finally",
    # Summary
    "overall", "in short", "in summary",
]


def transition_word_score(text: str) -> float:
    """Percentage of sentences that start with or contain a transition word.

    Good writing typically has 30-40%+ sentences with transitions.
    Returns a ratio between 0.0 and 1.0.
    """
    clean = _strip_markdown(text)
    sentences = re.split(r"[.!?]+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0.0

    count = 0
    for sent in sentences:
        sent_lower = sent.lower()
        if any(t in sent_lower for t in _TRANSITION_PHRASES):
            count += 1

    return round(count / len(sentences), 3)


def sentence_length_variety(text: str) -> float:
    """Coefficient of variation (std / mean) of sentence lengths.

    A higher value indicates more variety: short punchy sentences mixed with
    longer explanatory ones.  Good writing targets CV >= 0.40.
    Returns 0.0 if fewer than 2 sentences.
    """
    clean = _strip_markdown(text)
    sentences = re.split(r"[.!?]+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) < 2:
        return 0.0

    lengths = [len(re.findall(r"\b\w+\b", s)) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    std = math.sqrt(variance)
    return round(std / mean, 3)


def readability_report(text: str) -> dict:
    """Generate a full readability report."""
    fk = flesch_kincaid_score(text)
    avg_sent = avg_sentence_length(text)
    avg_para = avg_paragraph_length(text)
    wc = word_count(text)
    passive = passive_voice_ratio(text)
    transitions = transition_word_score(text)
    variety = sentence_length_variety(text)

    issues = []
    if fk < 60:
        issues.append(f"Flesch-Kincaid score {fk} (target: 60+)")
    if avg_sent > 25:
        issues.append(f"Average sentence length {avg_sent} words (target: ≤25)")
    if avg_para > 4:
        issues.append(f"Average paragraph length {avg_para} sentences (target: ≤4)")
    if passive > 0.15:
        pct = round(passive * 100)
        issues.append(f"Passive voice in {pct}% of sentences (target: ≤15%)")
    if transitions < 0.25:
        pct = round(transitions * 100)
        issues.append(f"Transition words in only {pct}% of sentences (target: ≥25%)")
    if variety < 0.40:
        issues.append(f"Low sentence length variety (CV={variety}, target: ≥0.40) — mix short and long sentences")

    return {
        "flesch_kincaid": fk,
        "avg_sentence_length": avg_sent,
        "avg_paragraph_length": avg_para,
        "passive_voice_ratio": passive,
        "transition_word_score": transitions,
        "sentence_length_variety": variety,
        "word_count": wc,
        "issues": issues,
        "passes": fk >= 60 and avg_sent <= 25,
    }
