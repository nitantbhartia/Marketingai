"""
Text processing utilities for content analysis.
"""

import re
from typing import List, Tuple
from bs4 import BeautifulSoup
import markdown as md


def markdown_to_html(markdown_text: str) -> str:
    """Convert markdown to HTML."""
    return md.markdown(markdown_text, extensions=['extra', 'codehilite'])


def strip_markdown(markdown_text: str) -> str:
    """Strip markdown formatting to get plain text."""
    # Convert to HTML first
    html = markdown_to_html(markdown_text)
    # Then strip HTML tags
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text()


def extract_headings(markdown_text: str) -> List[Tuple[int, str]]:
    """
    Extract all headings from markdown with their levels.

    Returns:
        List of (level, text) tuples
    """
    headings = []
    for line in markdown_text.split('\n'):
        # ATX-style headings (# ## ###)
        match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if match:
            level = len(match.group(1))
            text = match.group(2)
            headings.append((level, text))

    return headings


def extract_links(text: str) -> List[Tuple[str, str]]:
    """
    Extract all links from markdown text.

    Returns:
        List of (url, text) tuples
    """
    # Markdown link pattern: [text](url)
    pattern = r'\[([^\]]+)\]\(([^\)]+)\)'
    matches = re.findall(pattern, text)
    return [(url, text) for text, url in matches]


def extract_images(markdown_text: str) -> List[Tuple[str, str]]:
    """
    Extract all images from markdown.

    Returns:
        List of (url, alt_text) tuples
    """
    # Markdown image pattern: ![alt](url)
    pattern = r'!\[([^\]]*)\]\(([^\)]+)\)'
    matches = re.findall(pattern, markdown_text)
    return [(url, alt_text) for alt_text, url in matches]


def word_count(text: str) -> int:
    """Count words in text."""
    clean_text = strip_markdown(text)
    words = re.findall(r'\b\w+\b', clean_text)
    return len(words)


def extract_first_n_words(text: str, n: int) -> str:
    """Extract first N words from text."""
    clean_text = strip_markdown(text)
    words = re.findall(r'\b\w+\b', clean_text)
    return ' '.join(words[:n])


def find_keyword_in_text(text: str, keyword: str, case_sensitive: bool = False) -> List[int]:
    """
    Find all positions where keyword appears in text.

    Returns:
        List of character positions
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    positions = []
    for match in re.finditer(re.escape(keyword), text, flags):
        positions.append(match.start())
    return positions


def extract_faq_section(markdown_text: str) -> List[Tuple[str, str]]:
    """
    Extract FAQ questions and answers from markdown.

    Looks for patterns like:
    - ## What is...?
    - **Q:** or **Question:**
    - Q: or Question:

    Returns:
        List of (question, answer) tuples
    """
    faqs = []

    # Pattern 1: Headings as questions
    lines = markdown_text.split('\n')
    for i, line in enumerate(lines):
        # Check if line is a heading that ends with ?
        if re.match(r'^#{2,4}\s+.+\?$', line.strip()):
            question = re.sub(r'^#{2,4}\s+', '', line.strip())
            # Get next non-empty lines as answer
            answer_lines = []
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j].strip() and not lines[j].strip().startswith('#'):
                    answer_lines.append(lines[j].strip())
                elif lines[j].strip().startswith('#'):
                    break
            answer = ' '.join(answer_lines)
            if answer:
                faqs.append((question, answer))

    # Pattern 2: **Q:** or **Question:** format
    q_pattern = r'\*\*Q(?:uestion)?:\*\*\s*(.+?)(?:\*\*A(?:nswer)?:\*\*\s*(.+?)(?=\*\*Q|$))'
    for match in re.finditer(q_pattern, markdown_text, re.IGNORECASE | re.DOTALL):
        question = match.group(1).strip()
        answer = match.group(2).strip() if match.group(2) else ""
        faqs.append((question, answer))

    return faqs


def is_internal_link(url: str, domain: str) -> bool:
    """Check if URL is internal to given domain."""
    url_lower = url.lower()
    domain_lower = domain.lower()

    # Relative links are internal
    if not url_lower.startswith('http'):
        return True

    # Check if domain is in URL
    return domain_lower in url_lower


def extract_slug_from_url(url: str) -> str:
    """Extract slug from URL."""
    # Remove protocol and domain
    path = re.sub(r'^https?://[^/]+/', '', url)
    # Remove trailing slash
    path = path.rstrip('/')
    # Get last segment
    segments = path.split('/')
    return segments[-1] if segments else ""


def check_heading_hierarchy(headings: List[Tuple[int, str]]) -> List[str]:
    """
    Check if heading hierarchy is valid (no skipped levels).

    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    prev_level = 0

    for level, text in headings:
        if prev_level > 0 and level > prev_level + 1:
            errors.append(f"Skipped from H{prev_level} to H{level}: '{text}'")
        prev_level = level

    return errors
