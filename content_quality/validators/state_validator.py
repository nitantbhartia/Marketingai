"""
State Regulation Validator

Catches factually incorrect state-specific claims before they publish.
Cross-references against STATE_RULES.md.
"""

import re
from typing import Dict, List, Optional, Set
from pathlib import Path

from content_quality.config import STATE_RULES_PATH


# State facts database (loaded from STATE_RULES.md)
STATE_FACTS = {
    "California": {
        "threshold_method": "TLF",
        "threshold_value": None,
        "invalid_threshold_claims": ["75%", "80%", "70%"],
        "sales_tax_required": True,
        "reopener_days": 35,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 3,
        "fault_system": "at-fault",
        "key_statute": "Cal. Code Regs. § 2695.8",
    },
    "Texas": {
        "threshold_method": "TLT",
        "threshold_value": "100%",
        "sales_tax_required": "policy-dependent",
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 2,
        "fault_system": "at-fault",
        "key_statute": "Tex. Ins. Code § 1952.301",
    },
    "Florida": {
        "threshold_method": "TLT",
        "threshold_value": "80%",
        "invalid_threshold_claims": ["75%", "70%"],
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 4,
        "fault_system": "no-fault",
        "key_statute": "Fla. Stat. § 319.30",
        "notes": "no-fault for BI; at-fault for property damage"
    },
    "New York": {
        "threshold_method": "TLT",
        "threshold_value": "75%",
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 3,
        "fault_system": "no-fault",
        "key_statute": "NY Ins. Law § 2610",
    },
    "Georgia": {
        "threshold_method": "TLF",
        "threshold_value": None,
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": True,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 4,
        "fault_system": "at-fault",
        "key_statute": "O.C.G.A. § 33-34-6",
        "special_rules": ["2+ major component replacements = total loss", "17c formula for DV"],
    },
    "North Carolina": {
        "threshold_method": "TLT",
        "threshold_value": "75%",
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": True,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 3,
        "fault_system": "at-fault",
        "negligence_rule": "contributory",
        "key_statute": "N.C. Gen. Stat. § 20-109.1",
    },
    "Pennsylvania": {
        "threshold_method": "TLF",
        "threshold_value": None,
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 2,
        "fault_system": "choice",
        "key_statute": "31 Pa. Code § 146.3",
    },
    "Illinois": {
        "threshold_method": "TLF",
        "threshold_value": None,
        "sales_tax_required": True,
        "sales_tax_condition": "must purchase replacement within 30 days",
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 5,
        "fault_system": "at-fault",
        "key_statute": "215 ILCS 5/155.22a",
        "special_rules": ["Flood vehicles totaled at 50% ACV", "30-day purchase window for sales tax"],
    },
    "Ohio": {
        "threshold_method": "TLF",
        "threshold_value": None,
        "sales_tax_required": True,
        "sales_tax_condition": "must provide purchase documentation within 30 days",
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": True,
        "statute_of_limitations_years": 4,
        "fault_system": "at-fault",
        "key_statute": "Ohio Admin. Code § 3901-1-54",
        "special_rules": ["Sales tax capped at claim amount, not replacement vehicle price"],
    },
    "Michigan": {
        "threshold_method": "TLT",
        "threshold_value": "75%",
        "sales_tax_required": True,
        "reopener_days": None,
        "diminished_value_first_party": False,
        "diminished_value_third_party": False,
        "mini_tort_limit": 3000,
        "statute_of_limitations_years": 3,
        "fault_system": "no-fault",
        "key_statute": "Mich. Comp. Laws § 257.217c",
        "special_rules": ["Mini-tort $3,000 cap on property damage claims", "DV essentially unavailable"],
    },
}

# State name variations and abbreviations
STATE_ALIASES = {
    "CA": "California", "calif": "California", "cali": "California",
    "TX": "Texas", "tex": "Texas",
    "FL": "Florida", "fla": "Florida",
    "NY": "New York", "new york state": "New York",
    "GA": "Georgia", "ga state": "Georgia",
    "NC": "North Carolina", "n.c.": "North Carolina",
    "PA": "Pennsylvania", "penn": "Pennsylvania",
    "IL": "Illinois", "ill": "Illinois",
    "OH": "Ohio",
    "MI": "Michigan", "mich": "Michigan",
}


class StateRegulationValidator:
    """Validates state-specific insurance claims."""

    # Detection patterns for different claim types
    CLAIM_PATTERNS = {
        "threshold": [
            r"(\d+)%\s*(threshold|total loss|totaled)",
            r"total loss (threshold|formula|method).*(TLF|TLT|\d+%)",
            r"(repair cost|damage).*(exceed|reach|equal).*(\d+%)",
        ],
        "timeline": [
            r"(\d+)\s*(day|calendar day|business day).*(dispute|challenge|reopen|respond|file)",
            r"(within|have)\s*(\d+)\s*(day|month|year)",
        ],
        "sales_tax": [
            r"sales tax.*(required|must|included|mandatory|owed)",
            r"(must|required to).*(include|pay|cover).*sales tax",
            r"sales tax.*(not required|not included|excluded|optional)",
        ],
        "diminished_value": [
            r"diminished value.*(allowed|permitted|available|recognized|first.party|third.party)",
            r"(first.party|own insurer).*(diminished value|DV)",
            r"(cannot|can not|don.t|do not).*(file|claim|recover).*diminished value",
        ],
        "statute_of_limitations": [
            r"(\d+)\s*(year|month).*(statute of limitation|file|deadline|time limit)",
            r"(statute of limitation|filing deadline|time limit).*(\d+)\s*(year|month)",
        ],
        "fault_system": [
            r"(no.fault|at.fault|fault|tort|choice)\s*(state|system|law)",
        ],
    }

    def __init__(self):
        """Initialize validator."""
        pass

    def _extract_states_from_text(self, text: str) -> Set[str]:
        """Extract all state names mentioned in text."""
        states_found = set()
        text_lower = text.lower()

        # Check full state names
        for state in STATE_FACTS.keys():
            if state.lower() in text_lower:
                states_found.add(state)

        # Check aliases
        for alias, state in STATE_ALIASES.items():
            if re.search(r'\b' + re.escape(alias.lower()) + r'\b', text_lower):
                states_found.add(state)

        return states_found

    def _find_sentences_with_state(self, text: str, state: str) -> List[Tuple[str, int]]:
        """Find all sentences that mention a specific state."""
        sentences = re.split(r'[.!?]+', text)
        state_sentences = []

        for i, sentence in enumerate(sentences):
            if state.lower() in sentence.lower():
                state_sentences.append((sentence.strip(), i))
            else:
                # Check aliases
                for alias, full_name in STATE_ALIASES.items():
                    if full_name == state and re.search(r'\b' + re.escape(alias.lower()) + r'\b', sentence.lower()):
                        state_sentences.append((sentence.strip(), i))
                        break

        return state_sentences

    def _validate_threshold_claim(self, sentence: str, state: str) -> Optional[Dict]:
        """Validate total loss threshold claims."""
        state_info = STATE_FACTS.get(state)
        if not state_info:
            return None

        # Extract percentage claims
        threshold_pattern = r"(\d+)%"
        matches = re.findall(threshold_pattern, sentence)

        for match in matches:
            percentage = f"{match}%"

            # Check if this percentage is explicitly wrong for this state
            if "invalid_threshold_claims" in state_info:
                if percentage in state_info["invalid_threshold_claims"]:
                    correct_value = state_info["threshold_value"] or f"{state_info['threshold_method']} formula (no fixed %)"
                    return {
                        "severity": "ERROR",
                        "state": state,
                        "claim_type": "threshold",
                        "claimed_value": percentage,
                        "correct_value": correct_value,
                        "source": state_info["key_statute"],
                        "message": f"{state} uses {state_info['threshold_method']}, not {percentage}. Correct value: {correct_value}"
                    }

            # If state uses TLF (no percentage), any percentage claim is suspect
            if state_info["threshold_method"] == "TLF" and state_info["threshold_value"] is None:
                if percentage in ["70%", "75%", "80%", "85%", "90%", "100%"]:
                    return {
                        "severity": "ERROR",
                        "state": state,
                        "claim_type": "threshold",
                        "claimed_value": percentage,
                        "correct_value": "TLF formula (no fixed percentage)",
                        "source": state_info["key_statute"],
                        "message": f"{state} uses Total Loss Formula (TLF), not a fixed percentage like {percentage}"
                    }

            # If state has specific percentage, verify it matches
            if state_info["threshold_value"] and percentage != state_info["threshold_value"]:
                return {
                    "severity": "ERROR",
                    "state": state,
                    "claim_type": "threshold",
                    "claimed_value": percentage,
                    "correct_value": state_info["threshold_value"],
                    "source": state_info["key_statute"],
                    "message": f"{state} threshold is {state_info['threshold_value']}, not {percentage}"
                }

        return None

    def _validate_diminished_value_claim(self, sentence: str, state: str) -> Optional[Dict]:
        """Validate diminished value availability claims."""
        state_info = STATE_FACTS.get(state)
        if not state_info:
            return None

        sentence_lower = sentence.lower()

        # Check for first-party DV claims
        if re.search(r"(first.party|own insurer).*(diminished value|DV)", sentence_lower, re.IGNORECASE):
            if not state_info["diminished_value_first_party"]:
                available_states = [s for s, info in STATE_FACTS.items() if info.get("diminished_value_first_party")]
                return {
                    "severity": "ERROR",
                    "state": state,
                    "claim_type": "diminished_value",
                    "claimed_value": "first-party DV available",
                    "correct_value": "first-party DV NOT available",
                    "source": state_info["key_statute"],
                    "message": f"{state} does NOT allow first-party diminished value claims. Only {', '.join(available_states)} allow this."
                }

        # Check for third-party DV claims
        if re.search(r"(third.party|at.fault|other driver).*(diminished value|DV)", sentence_lower, re.IGNORECASE):
            if not state_info["diminished_value_third_party"]:
                return {
                    "severity": "ERROR",
                    "state": state,
                    "claim_type": "diminished_value",
                    "claimed_value": "third-party DV available",
                    "correct_value": "third-party DV NOT available (effectively blocked)",
                    "source": state_info["key_statute"],
                    "message": f"{state} effectively blocks third-party DV (mini-tort limit: ${state_info.get('mini_tort_limit', 0)})"
                }

        return None

    def _validate_statute_of_limitations(self, sentence: str, state: str) -> Optional[Dict]:
        """Validate statute of limitations claims."""
        state_info = STATE_FACTS.get(state)
        if not state_info:
            return None

        # Extract year claims
        sol_pattern = r"(\d+)\s*(year|yr)"
        matches = re.findall(sol_pattern, sentence.lower())

        for match in matches:
            years_claimed = int(match[0])
            correct_years = state_info["statute_of_limitations_years"]

            if years_claimed != correct_years:
                return {
                    "severity": "ERROR",
                    "state": state,
                    "claim_type": "statute_of_limitations",
                    "claimed_value": f"{years_claimed} years",
                    "correct_value": f"{correct_years} years",
                    "source": state_info["key_statute"],
                    "message": f"{state} statute of limitations is {correct_years} years, not {years_claimed} years"
                }

        return None

    def validate(self, article_markdown: str, target_state: Optional[str] = None) -> Dict:
        """
        Validate article for state regulation accuracy.

        Args:
            article_markdown: Full article content
            target_state: Primary state if state-specific article

        Returns:
            Validation result dict
        """
        states_referenced = self._extract_states_from_text(article_markdown)
        issues = []
        unverifiable_claims = []

        # If no states mentioned, auto-pass
        if not states_referenced:
            return {
                "status": "PASS",
                "score": 100,
                "issues": [],
                "states_referenced": [],
                "unverifiable_claims": [],
            }

        # Validate each state mention
        for state in states_referenced:
            if state not in STATE_FACTS:
                unverifiable_claims.append(f"State '{state}' mentioned but not in validation database")
                continue

            sentences = self._find_sentences_with_state(article_markdown, state)

            for sentence, line_num in sentences:
                # Check threshold claims
                threshold_issue = self._validate_threshold_claim(sentence, state)
                if threshold_issue:
                    threshold_issue["line"] = line_num
                    threshold_issue["sentence"] = sentence
                    issues.append(threshold_issue)

                # Check DV claims
                dv_issue = self._validate_diminished_value_claim(sentence, state)
                if dv_issue:
                    dv_issue["line"] = line_num
                    dv_issue["sentence"] = sentence
                    issues.append(dv_issue)

                # Check SOL claims
                sol_issue = self._validate_statute_of_limitations(sentence, state)
                if sol_issue:
                    sol_issue["line"] = line_num
                    sol_issue["sentence"] = sentence
                    issues.append(sol_issue)

        # Determine status
        error_count = len([i for i in issues if i["severity"] == "ERROR"])
        warning_count = len(unverifiable_claims)

        if error_count > 0:
            status = "FAIL"
            score = max(0, 100 - (error_count * 20))
        elif warning_count > 0:
            status = "WARN"
            score = 80
        else:
            status = "PASS"
            score = 100

        return {
            "status": status,
            "score": score,
            "issues": issues,
            "states_referenced": list(states_referenced),
            "unverifiable_claims": unverifiable_claims,
        }


def validate_state_rules(article_markdown: str, target_state: Optional[str] = None) -> Dict:
    """
    Convenience function to validate state regulations.

    Args:
        article_markdown: Full article content
        target_state: Primary state if state-specific article

    Returns:
        Validation result dict
    """
    validator = StateRegulationValidator()
    return validator.validate(article_markdown, target_state)
