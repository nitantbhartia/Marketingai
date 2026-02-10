"""
Math Validator

Catches arithmetic errors in articles.
AI frequently botches math when calculating sales tax, settlement examples, or line item totals.
"""

import re
from typing import Dict, List, Optional
from content_quality.config import PRODUCT_CONTEXT_PATH


class MathValidator:
    """Validates mathematical claims in articles."""

    # Patterns for mathematical expressions
    PERCENTAGE_CALC_PATTERN = r"(\d+\.?\d*)\s*%\s*(?:of|on|×|times)\s*\$?\s*([\d,]+\.?\d*)\s*(?:is|equals?|=|comes?\s+to|would\s+be|adds?|totals?)\s*\$?\s*([\d,]+\.?\d*)"
    ADDITION_PATTERN = r"\$?\s*([\d,]+\.?\d*)\s*\+\s*\$?\s*([\d,]+\.?\d*)\s*(?:=|equals?|is|totals?)\s*\$?\s*([\d,]+\.?\d*)"
    SUBTRACTION_PATTERN = r"\$?\s*([\d,]+\.?\d*)\s*(?:-|minus|less)\s*\$?\s*([\d,]+\.?\d*)\s*(?:=|equals?|is|leaves?)\s*\$?\s*([\d,]+\.?\d*)"
    RANGE_PATTERN = r"(sales tax|title|registration|loss of use|dealer fee|aftermarket|towing|storage).*?\$?\s*([\d,]+)\s*[-–—to]+\s*\$?\s*([\d,]+)"

    def __init__(self):
        """Initialize validator."""
        self.canonical_ranges = self._load_canonical_ranges()

    def _load_canonical_ranges(self) -> Dict[str, tuple]:
        """Load canonical ranges from PRODUCT_CONTEXT.md."""
        ranges = {}
        try:
            with open(PRODUCT_CONTEXT_PATH, 'r') as f:
                content = f.read()

                # Extract line item ranges from table
                # Format: | Line Item | Typical Range |
                table_pattern = r"\|\s*([^|]+)\s*\|\s*\$?\s*([\d,]+)\s*[-–]\s*\$?\s*([\d,]+)"
                for match in re.finditer(table_pattern, content):
                    item = match.group(1).strip().lower()
                    min_val = self._parse_number(match.group(2))
                    max_val = self._parse_number(match.group(3))
                    ranges[item] = (min_val, max_val)

        except FileNotFoundError:
            pass

        return ranges

    def _parse_number(self, num_str: str) -> float:
        """Parse number string to float, handling commas and formatting."""
        cleaned = num_str.replace(',', '').replace('$', '').strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def _validate_percentage_calculation(self, text: str) -> List[Dict]:
        """Validate percentage calculations."""
        issues = []

        for match in re.finditer(self.PERCENTAGE_CALC_PATTERN, text, re.IGNORECASE):
            percentage = float(match.group(1))
            base = self._parse_number(match.group(2))
            claimed_result = self._parse_number(match.group(3))

            if base == 0:
                continue

            actual_result = round(percentage / 100 * base, 2)

            # Allow 1% tolerance for rounding
            tolerance = actual_result * 0.01
            if abs(actual_result - claimed_result) > tolerance:
                issues.append({
                    "type": "percentage_calculation",
                    "text": match.group(0),
                    "claimed": f"${claimed_result:,.2f}",
                    "actual": f"${actual_result:,.2f}",
                    "message": f"{percentage}% of ${base:,.0f} is ${actual_result:,.2f}, not ${claimed_result:,.2f}"
                })

        return issues

    def _validate_addition(self, text: str) -> List[Dict]:
        """Validate addition operations."""
        issues = []

        for match in re.finditer(self.ADDITION_PATTERN, text, re.IGNORECASE):
            num1 = self._parse_number(match.group(1))
            num2 = self._parse_number(match.group(2))
            claimed_result = self._parse_number(match.group(3))

            actual_result = round(num1 + num2, 2)

            # Allow small rounding tolerance
            if abs(actual_result - claimed_result) > 0.02:
                issues.append({
                    "type": "addition",
                    "text": match.group(0),
                    "claimed": f"${claimed_result:,.2f}",
                    "actual": f"${actual_result:,.2f}",
                    "message": f"${num1:,.2f} + ${num2:,.2f} = ${actual_result:,.2f}, not ${claimed_result:,.2f}"
                })

        return issues

    def _validate_subtraction(self, text: str) -> List[Dict]:
        """Validate subtraction operations."""
        issues = []

        for match in re.finditer(self.SUBTRACTION_PATTERN, text, re.IGNORECASE):
            num1 = self._parse_number(match.group(1))
            num2 = self._parse_number(match.group(2))
            claimed_result = self._parse_number(match.group(3))

            actual_result = round(num1 - num2, 2)

            # Allow small rounding tolerance
            if abs(actual_result - claimed_result) > 0.02:
                issues.append({
                    "type": "subtraction",
                    "text": match.group(0),
                    "claimed": f"${claimed_result:,.2f}",
                    "actual": f"${actual_result:,.2f}",
                    "message": f"${num1:,.2f} - ${num2:,.2f} = ${actual_result:,.2f}, not ${claimed_result:,.2f}"
                })

        return issues

    def _extract_line_item_ranges(self, text: str) -> Dict[str, List[tuple]]:
        """Extract all line item ranges mentioned in text."""
        ranges = {}

        for match in re.finditer(self.RANGE_PATTERN, text, re.IGNORECASE):
            item = match.group(1).strip().lower()
            min_val = self._parse_number(match.group(2))
            max_val = self._parse_number(match.group(3))

            if item not in ranges:
                ranges[item] = []
            ranges[item].append((min_val, max_val))

        return ranges

    def _validate_range_consistency(self, text: str) -> List[Dict]:
        """Validate that line item ranges are consistent throughout article."""
        issues = []
        ranges = self._extract_line_item_ranges(text)

        # Check for inconsistencies within article
        for item, occurrences in ranges.items():
            if len(occurrences) > 1:
                # Check all occurrences match
                for i, r1 in enumerate(occurrences):
                    for r2 in occurrences[i + 1:]:
                        if r1 != r2:
                            issues.append({
                                "type": "range_inconsistency",
                                "item": item,
                                "ranges": [f"${r1[0]:,.0f}-${r1[1]:,.0f}", f"${r2[0]:,.0f}-${r2[1]:,.0f}"],
                                "message": f"'{item}' cited as ${r1[0]:,.0f}-${r1[1]:,.0f} in one place and ${r2[0]:,.0f}-${r2[1]:,.0f} in another"
                            })

        # Check against canonical ranges from PRODUCT_CONTEXT.md
        for item, article_ranges in ranges.items():
            if item in self.canonical_ranges:
                canonical = self.canonical_ranges[item]
                for article_range in article_ranges:
                    # Check if ranges overlap
                    if not self._ranges_overlap(article_range, canonical):
                        issues.append({
                            "type": "range_mismatch",
                            "item": item,
                            "article_range": f"${article_range[0]:,.0f}-${article_range[1]:,.0f}",
                            "canonical_range": f"${canonical[0]:,.0f}-${canonical[1]:,.0f}",
                            "message": f"'{item}' range ${article_range[0]:,.0f}-${article_range[1]:,.0f} doesn't match PRODUCT_CONTEXT.md range ${canonical[0]:,.0f}-${canonical[1]:,.0f}"
                        })

        return issues

    def _ranges_overlap(self, r1: tuple, r2: tuple) -> bool:
        """Check if two ranges overlap."""
        return not (r1[1] < r2[0] or r2[1] < r1[0])

    def validate(self, article_text: str) -> Dict:
        """
        Validate all mathematical claims in article.

        Args:
            article_text: Article content

        Returns:
            Validation result dict
        """
        issues = []

        # Check percentage calculations
        issues.extend(self._validate_percentage_calculation(article_text))

        # Check addition
        issues.extend(self._validate_addition(article_text))

        # Check subtraction
        issues.extend(self._validate_subtraction(article_text))

        # Check range consistency
        issues.extend(self._validate_range_consistency(article_text))

        status = "PASS" if len(issues) == 0 else "FAIL"

        return {
            "status": status,
            "issues": issues,
            "error_count": len(issues),
        }


def validate_math(article_text: str) -> Dict:
    """
    Convenience function to validate math.

    Args:
        article_text: Article content

    Returns:
        Math validation result
    """
    validator = MathValidator()
    return validator.validate(article_text)
