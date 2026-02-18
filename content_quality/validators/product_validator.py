"""
Product Claim Validator

Prevents articles from claiming features ClaimCoach doesn't have,
making promises it can't keep, or using legally risky language.
"""

import re
from typing import Dict, List, Tuple

from content_quality.config import PRODUCT_CONTEXT_PATH, REFERENCE_DIR


class ProductClaimValidator:
    """Validates product claims against approved capabilities."""

    COMMON_HARD_FAIL_PATTERNS = [
        # Guarantees and promises
        (
            r"guarantee[ds]?\s+(you|a|more|higher|additional|extra)",
            "guarantee",
            "Never guarantee outcomes. Rewrite without guarantees."
        ),
        (
            r"(100%|always)\s*(success|work|effective|guaranteed)",
            "guarantee",
            "Avoid absolute claims. Use 'commonly', 'often', or 'typically'."
        ),
        (
            r"(will|going to)\s*(get|receive|recover|win)\s*(you\s+)?(more|extra|additional|higher|thousands|hundreds)",
            "overpromise",
            "Don't promise specific monetary outcomes. Rewrite to focus on identification."
        ),
        (
            r"average\s+(user|customer|person|claimant)\s+(recover|get|receive)s?\s+\$[\d,]+",
            "overpromise",
            "Don't cite average recovery amounts without data. Remove claim."
        ),

        # Legal advice
        (
            r"you\s+(are|are not)\s+legally\s+(entitled|required|obligated)",
            "legal_advice",
            "Don't make legal entitlement claims. Suggest consulting a lawyer."
        ),
        (
            r"the\s+law\s+(requires|mandates|says)\s+(your\s+)?(insurer|insurance company|adjuster|provider|hospital)\s+(must|to)",
            "legal_advice",
            "Don't interpret law. Cite statute and suggest legal consultation."
        ),
        (
            r"(sue|lawsuit|take legal action|go to court)",
            "legal_advice",
            "Don't advise litigation. Suggest consulting an attorney."
        ),
        (
            r"(this constitutes|this is not|we provide|we offer)\s+legal\s+advice",
            "legal_advice",
            "Don't define legal advice. Let disclaimer handle this."
        ),

        # Risk claims
        (
            r"(risk.?free|no.?risk|nothing to lose)",
            "overpromise",
            "Remove risk-free claims. Focus on concrete value."
        ),
        (
            r"(we|claimcoach|billkarma|billscan)\s+(ensure|ensure that|make sure)\s+(you get|you receive|your settlement|your reduction|your savings)",
            "overpromise",
            "Don't claim to ensure outcomes. Say 'helps you identify' instead."
        ),
    ]

    # Feature hallucinations (things ClaimCoach does NOT do)
    CLAIMCOACH_HARD_FAIL_PATTERNS = [
        # Negotiation claims
        (
            r"(claimcoach|claim coach|we|our tool).*(negotiate|negotiates|negotiating).*(for you|on your behalf|with.*insurer|with.*adjuster)",
            "feature_hallucination",
            "ClaimCoach does NOT negotiate with insurers. Remove this claim."
        ),
        (
            r"(claimcoach|claim coach|we|our tool).*(file|files|filing).*(claim|dispute|appeal).*(for you|on your behalf)",
            "feature_hallucination",
            "ClaimCoach does NOT file claims or disputes. Remove this claim."
        ),

        # Document upload claims
        (
            r"(upload|submit).*(policy|settlement letter|document|pdf|report).*(to claimcoach|to our|for analysis)",
            "feature_hallucination",
            "ClaimCoach does NOT have document upload. Remove this claim."
        ),

        # Document generation claims
        (
            r"(claimcoach|claim coach|we|our tool).*(generate|creates?|produce).*(dispute letter|demand letter|counter.?offer letter)",
            "feature_hallucination",
            "ClaimCoach does NOT generate letters. Remove this claim."
        ),

        # Chat/assistant claims
        (
            r"(claimcoach|claim coach|we|our tool).*(chat|assistant|chatbot|ask.*question)",
            "feature_hallucination",
            "ClaimCoach does NOT have a chat feature. Remove this claim."
        ),

        # Tracking claims
        (
            r"(claimcoach|claim coach|we|our tool).*(track|tracking|status|notification|notify|alert)",
            "feature_hallucination",
            "ClaimCoach does NOT track claims. Remove this claim."
        ),

        # Communication claims
        (
            r"(claimcoach|claim coach|we|our tool).*(contact|communicate|reach out to|call|email).*(insurer|adjuster|insurance company)",
            "feature_hallucination",
            "ClaimCoach does NOT contact insurers. Remove this claim."
        ),

        # Integration claims
        (
            r"(claimcoach|claim coach|we|our tool).*(access|connect|integrate).*(CCC ONE|Mitchell|insurance.*system|database)",
            "feature_hallucination",
            "ClaimCoach does NOT integrate with insurer systems. Remove this claim."
        ),
    ]

    # Feature hallucinations (things BillKarma does NOT do)
    MEDBILL_HARD_FAIL_PATTERNS = [
        (
            r"(billkarma|billscan|bill karma|bill scan|we|our tool).*(file|files|filing).*(insurance claim|insurance appeal)",
            "feature_hallucination",
            "BillKarma does NOT file insurance claims or appeals for users."
        ),
        (
            r"(billkarma|billscan|bill karma|bill scan|we|our tool).*(provide|gives?|offer).*(diagnosis|medical advice)",
            "feature_hallucination",
            "BillKarma does NOT provide medical advice."
        ),
        (
            r"(billkarma|billscan|bill karma|bill scan|we|our tool).*(verify|determine|decide).*(medical necessity|medically necessary)",
            "feature_hallucination",
            "BillKarma does NOT determine medical necessity."
        ),
        (
            r"(billkarma|billscan|bill karma|bill scan|we|our tool).*(replace|instead of).*(attorney|lawyer|legal counsel)",
            "feature_hallucination",
            "BillKarma does NOT replace legal representation."
        ),
        (
            r"(billkarma|billscan|bill karma|bill scan|we|our tool).*(access|store|pull).*(medical records|full patient records|ehr|emr)",
            "feature_hallucination",
            "BillKarma does not claim access to full medical records beyond bill data."
        ),
    ]

    # Vague claims that should be more specific
    SOFT_WARN_PATTERNS = [
        (
            r"(most|many|typically|usually)\s+(people|owners|drivers|consumers)\s+(are owed|can get|receive|recover)",
            "vague_claim",
            "Be more specific. Cite a state or provide context."
        ),
        (
            r"(significant|substantial|considerable)\s+(amount|money|increase|difference)",
            "vague_claim",
            "Quantify if possible. Give ranges or examples."
        ),
        (
            r"(may|might|could)\s+be\s+entitled",
            "vague_claim",
            "OK, but cite specific state if possible."
        ),
        (
            r"\d+%\s+of\s+(offers|settlements|claims)\s+(are|is)",
            "missing_source",
            "Stats need sources. Add citation or remove."
        ),
    ]

    def __init__(self, product: str = "claimcoach"):
        """Initialize validator."""
        self.product = (product or "claimcoach").strip().lower()
        self.product_context = self._load_product_context()

    def _load_product_context(self) -> str:
        """Load product context reference file."""
        try:
            if self.product == "medbill":
                medbill_path = REFERENCE_DIR / "MEDBILL_PRODUCT_CONTEXT.md"
                if medbill_path.exists():
                    return medbill_path.read_text()
            with open(PRODUCT_CONTEXT_PATH, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _hard_fail_patterns(self) -> List[Tuple[str, str, str]]:
        patterns = list(self.COMMON_HARD_FAIL_PATTERNS)
        if self.product == "medbill":
            patterns.extend(self.MEDBILL_HARD_FAIL_PATTERNS)
        else:
            patterns.extend(self.CLAIMCOACH_HARD_FAIL_PATTERNS)
        return patterns

    def validate(self, article_markdown: str) -> Dict:
        """
        Validate article for product claim violations.

        Args:
            article_markdown: Full article content in markdown

        Returns:
            Dict with status, violations, and warnings
        """
        # Convert to lowercase for case-insensitive matching
        text_lower = article_markdown.lower()

        hard_violations = []
        soft_warnings = []

        # Check hard fail patterns
        for pattern, category, suggestion in self._hard_fail_patterns():
            for match in re.finditer(pattern, text_lower, re.IGNORECASE):
                # Find line number
                line_num = text_lower[:match.start()].count('\n') + 1
                matched_text = article_markdown[match.start():match.end()]

                hard_violations.append({
                    "pattern_category": category,
                    "matched_text": matched_text,
                    "line_number": line_num,
                    "suggestion": suggestion,
                })

        # Check soft warn patterns
        for pattern, category, suggestion in self.SOFT_WARN_PATTERNS:
            for match in re.finditer(pattern, text_lower, re.IGNORECASE):
                line_num = text_lower[:match.start()].count('\n') + 1
                matched_text = article_markdown[match.start():match.end()]

                soft_warnings.append({
                    "pattern_category": category,
                    "matched_text": matched_text,
                    "line_number": line_num,
                    "suggestion": suggestion,
                })

        status = "FAIL" if hard_violations else "PASS"

        return {
            "status": status,
            "hard_violations": hard_violations,
            "soft_warnings": soft_warnings,
            "violation_count": len(hard_violations),
            "warning_count": len(soft_warnings),
        }


def validate_product_claims(article_markdown: str, product: str = "claimcoach") -> Dict:
    """
    Convenience function to validate product claims.

    Args:
        article_markdown: Full article content

    Returns:
        Validation result dict
    """
    validator = ProductClaimValidator(product=product)
    return validator.validate(article_markdown)
