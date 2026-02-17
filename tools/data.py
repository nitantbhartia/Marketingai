"""Data and calculation logic for ClaimCoach interactive conversion tools.

Four tools:
  1. Sales Tax Calculator — specific dollar amount from settlement × state rate
  2. Settlement Checklist — visual gap analysis of missing line items
  3. Fairness Quiz — 0-100 score based on what's in/missing from offer
  4. Car Worth Estimator — link to KBB with pre-filled params (phase 1)
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# State sales tax rates
# Source: state revenue departments.  avg_combined includes local averages.
# ---------------------------------------------------------------------------
STATE_SALES_TAX: dict[str, dict[str, Any]] = {
    "Alabama": {"state_rate": 4.00, "avg_combined": 9.24},
    "Alaska": {"state_rate": 0, "avg_combined": 1.76, "note": "No state tax but local taxes may apply"},
    "Arizona": {"state_rate": 5.60, "avg_combined": 8.40},
    "Arkansas": {"state_rate": 6.50, "avg_combined": 9.47},
    "California": {"state_rate": 7.25, "avg_combined": 8.85},
    "Colorado": {"state_rate": 2.90, "avg_combined": 7.81},
    "Connecticut": {"state_rate": 6.35, "avg_combined": 6.35},
    "Delaware": {"state_rate": 0, "avg_combined": 0, "note": "No sales tax — but 4.25% document fee on vehicles"},
    "Florida": {"state_rate": 6.00, "avg_combined": 7.01},
    "Georgia": {"state_rate": 4.00, "avg_combined": 7.37, "note": "TAVT of ~6.6% applies instead for title transfers"},
    "Hawaii": {"state_rate": 4.00, "avg_combined": 4.44},
    "Idaho": {"state_rate": 6.00, "avg_combined": 6.02},
    "Illinois": {"state_rate": 6.25, "avg_combined": 8.82},
    "Indiana": {"state_rate": 7.00, "avg_combined": 7.00},
    "Iowa": {"state_rate": 6.00, "avg_combined": 6.94},
    "Kansas": {"state_rate": 6.50, "avg_combined": 8.71},
    "Kentucky": {"state_rate": 6.00, "avg_combined": 6.00},
    "Louisiana": {"state_rate": 4.45, "avg_combined": 9.56},
    "Maine": {"state_rate": 5.50, "avg_combined": 5.50},
    "Maryland": {"state_rate": 6.00, "avg_combined": 6.00},
    "Massachusetts": {"state_rate": 6.25, "avg_combined": 6.25},
    "Michigan": {"state_rate": 6.00, "avg_combined": 6.00},
    "Minnesota": {"state_rate": 6.875, "avg_combined": 7.49},
    "Mississippi": {"state_rate": 7.00, "avg_combined": 7.07},
    "Missouri": {"state_rate": 4.225, "avg_combined": 8.38},
    "Montana": {"state_rate": 0, "avg_combined": 0},
    "Nebraska": {"state_rate": 5.50, "avg_combined": 6.94},
    "Nevada": {"state_rate": 6.85, "avg_combined": 8.23},
    "New Hampshire": {"state_rate": 0, "avg_combined": 0},
    "New Jersey": {"state_rate": 6.625, "avg_combined": 6.60},
    "New Mexico": {"state_rate": 4.875, "avg_combined": 7.72},
    "New York": {"state_rate": 4.00, "avg_combined": 8.52},
    "North Carolina": {"state_rate": 4.75, "avg_combined": 6.99, "note": "3% highway use tax applies, capped"},
    "North Dakota": {"state_rate": 5.00, "avg_combined": 6.96},
    "Ohio": {"state_rate": 5.75, "avg_combined": 7.24},
    "Oklahoma": {"state_rate": 4.50, "avg_combined": 8.98},
    "Oregon": {"state_rate": 0, "avg_combined": 0, "note": "0.5% vehicle privilege/use tax may apply"},
    "Pennsylvania": {"state_rate": 6.00, "avg_combined": 6.34},
    "Rhode Island": {"state_rate": 7.00, "avg_combined": 7.00},
    "South Carolina": {"state_rate": 6.00, "avg_combined": 7.43, "note": "Capped at $500 for vehicles"},
    "South Dakota": {"state_rate": 4.20, "avg_combined": 6.40},
    "Tennessee": {"state_rate": 7.00, "avg_combined": 9.55},
    "Texas": {"state_rate": 6.25, "avg_combined": 8.20},
    "Utah": {"state_rate": 6.10, "avg_combined": 7.19},
    "Vermont": {"state_rate": 6.00, "avg_combined": 6.24},
    "Virginia": {"state_rate": 4.30, "avg_combined": 5.75},
    "Washington": {"state_rate": 6.50, "avg_combined": 10.25},
    "West Virginia": {"state_rate": 6.00, "avg_combined": 6.55},
    "Wisconsin": {"state_rate": 5.00, "avg_combined": 5.43},
    "Wyoming": {"state_rate": 4.00, "avg_combined": 5.36},
}

# ---------------------------------------------------------------------------
# Settlement checklist line items
# ---------------------------------------------------------------------------
CHECKLIST_STANDARD: list[dict[str, Any]] = [
    {
        "id": "sales_tax",
        "label": "Sales tax on replacement vehicle",
        "description": "Tax you'll pay when buying a replacement car.",
        "range_low": 800,
        "range_high": 3000,
        "calculate": True,
    },
    {
        "id": "title_registration",
        "label": "Title, registration, and transfer fees",
        "description": "One-time fees to legally register a replacement vehicle.",
        "range_low": 200,
        "range_high": 500,
    },
    {
        "id": "comparable_adjustments",
        "label": "Comparable vehicle adjustments",
        "description": "If the insurer's comps have higher mileage or worse condition than yours, the ACV should be adjusted up.",
        "range_low": 500,
        "range_high": 2000,
    },
    {
        "id": "dealer_fees",
        "label": "Dealer documentation fees",
        "description": "Doc fees charged by dealers when buying a replacement vehicle.",
        "range_low": 300,
        "range_high": 800,
    },
    {
        "id": "loss_of_use",
        "label": "Loss of use / rental car gap",
        "description": "Compensation for days without a vehicle beyond what rental coverage provides.",
        "range_low": 200,
        "range_high": 1500,
    },
    {
        "id": "aftermarket",
        "label": "Aftermarket modifications and upgrades",
        "description": "Custom wheels, audio systems, tint, performance parts.",
        "range_low": 0,
        "range_high": 5000,
    },
]

CHECKLIST_STATE_SPECIFIC: dict[str, list[dict[str, Any]]] = {
    "California": [
        {
            "id": "ca_license_fees",
            "label": "License fees (remaining registration term)",
            "description": "Pro-rated vehicle license fees for the remainder of your registration period.",
            "range_low": 50,
            "range_high": 200,
        },
        {
            "id": "ca_35_day_reopener",
            "label": "35-day reopener right",
            "description": "If you can't find a comparable vehicle within 35 days, the insurer must reopen your claim.",
            "is_right": True,
            "statute": "Cal. Code Regs. \u00a7 2695.8(c)",
        },
    ],
    "Georgia": [
        {
            "id": "ga_tavt",
            "label": "Title Ad Valorem Tax (TAVT)",
            "description": "Georgia charges ~6.6% TAVT on vehicle title transfers instead of traditional sales tax.",
            "range_low": 500,
            "range_high": 2000,
        },
        {
            "id": "ga_diminished_value",
            "label": "Diminished value (first-party)",
            "description": "Georgia is the ONLY state requiring insurers to pay diminished value on first-party claims.",
            "range_low": 500,
            "range_high": 5000,
            "statute": "State Farm v. Mabry, 274 Ga. 498 (2001)",
        },
    ],
    "North Carolina": [
        {
            "id": "nc_highway_use_tax",
            "label": "Highway use tax",
            "description": "NC charges a 3% highway use tax on vehicle purchases (capped).",
            "range_low": 200,
            "range_high": 500,
        },
        {
            "id": "nc_diminished_value",
            "label": "Diminished value (first-party)",
            "description": "NC allows first-party diminished value claims.",
            "range_low": 500,
            "range_high": 5000,
        },
    ],
    "Illinois": [
        {
            "id": "il_30_day_window",
            "label": "Sales tax (30-day purchase window)",
            "description": "You must purchase or lease a replacement within 30 days to recover sales tax.",
            "is_right": True,
            "statute": "215 ILCS 5/155.22a",
        },
    ],
    "Ohio": [
        {
            "id": "oh_30_day_window",
            "label": "Sales tax (30-day documentation required)",
            "description": "You must provide purchase documentation within 30 days to recover sales tax.",
            "is_right": True,
            "statute": "Ohio Admin. Code \u00a7 3901-1-54",
        },
    ],
    "Florida": [
        {
            "id": "fl_mediation",
            "label": "Free DOI mediation",
            "description": "Florida offers free mediation through the Department of Insurance for disputed claims.",
            "is_right": True,
            "statute": "FL DOI guidance",
            "hotline": "1-877-693-5236",
        },
    ],
    "Texas": [
        {
            "id": "tx_sales_tax_required",
            "label": "Sales tax (mandatory inclusion)",
            "description": "Texas requires insurers to include sales tax in total loss settlements.",
            "is_right": True,
            "statute": "28 TAC \u00a7 5.4903",
        },
    ],
}

# ---------------------------------------------------------------------------
# Tool-to-article-topic mapping
# ---------------------------------------------------------------------------
TOOL_TOPIC_MAP: dict[str, list[str]] = {
    "sales_tax_calculator": [
        "sales tax", "tax recovery", "replacement vehicle tax",
        "tax owed", "sales tax insurance",
    ],
    "settlement_checklist": [
        "total loss", "settlement", "line item", "what to expect",
        "how to dispute", "insurance offer", "claim checklist",
    ],
    "fairness_quiz": [
        "is my offer fair", "lowball", "am i getting", "offer too low",
        "fair settlement", "underpaid",
    ],
    "car_worth_estimator": [
        "car worth", "vehicle value", "actual cash value", "acv",
        "what is my car worth", "totaled car value",
    ],
}

TOOL_DISPLAY_NAMES: dict[str, str] = {
    "sales_tax_calculator": "Sales Tax Calculator",
    "settlement_checklist": "Settlement Checklist",
    "fairness_quiz": "Offer Fairness Quiz",
    "car_worth_estimator": "Car Worth Estimator",
}

TOOLS_LIBRARY_URL = "https://claimcoach.app/tools"


# ---------------------------------------------------------------------------
# Calculation functions
# ---------------------------------------------------------------------------

def calculate_sales_tax(
    settlement_amount: float,
    state: str,
    keeping_vehicle: bool = False,
    salvage_value: float = 0,
) -> dict[str, Any]:
    """Calculate sales tax owed on a total loss settlement."""
    state_data = STATE_SALES_TAX.get(state)
    if not state_data:
        return {"error": f"State '{state}' not found"}

    rate = state_data["avg_combined"] / 100

    if keeping_vehicle and salvage_value > 0:
        taxable_amount = settlement_amount - salvage_value
    else:
        taxable_amount = settlement_amount

    tax_owed = round(taxable_amount * rate, 2)

    return {
        "state": state,
        "settlement_amount": settlement_amount,
        "tax_rate_percent": state_data["avg_combined"],
        "state_rate_percent": state_data["state_rate"],
        "taxable_amount": taxable_amount,
        "tax_owed": tax_owed,
        "keeping_vehicle": keeping_vehicle,
        "note": state_data.get("note"),
    }


def calculate_checklist_results(
    checked_items: list[str],
    state: str,
    offer_amount: float,
) -> dict[str, Any]:
    """Calculate the estimated gap from unchecked settlement items."""
    all_items = list(CHECKLIST_STANDARD) + CHECKLIST_STATE_SPECIFIC.get(state, [])

    missing: list[dict[str, Any]] = []
    total_low = 0
    total_high = 0

    for item in all_items:
        if item["id"] in checked_items:
            continue
        if item.get("is_right"):
            missing.append({**item, "type": "right"})
            continue

        low = item.get("range_low", 0)
        high = item.get("range_high", 0)

        # Compute exact sales tax when possible
        if item["id"] == "sales_tax" and item.get("calculate"):
            tax_result = calculate_sales_tax(offer_amount, state, keeping_vehicle=False)
            if "error" not in tax_result:
                low = tax_result["tax_owed"]
                high = tax_result["tax_owed"]

        total_low += low
        total_high += high
        missing.append({
            **item,
            "type": "dollar",
            "estimated_low": low,
            "estimated_high": high,
        })

    return {
        "checked_count": len(checked_items),
        "missing_count": len(missing),
        "missing_items": missing,
        "estimated_gap_low": total_low,
        "estimated_gap_high": total_high,
        "adjusted_offer_low": offer_amount + total_low,
        "adjusted_offer_high": offer_amount + total_high,
    }


def calculate_fairness_score(
    state: str,
    offer_amount: float,
    includes_sales_tax: bool,
    included_items: list[str],
) -> dict[str, Any]:
    """Score an offer 0-100.  Lower = worse offer."""
    score = 50  # base — they at least made an offer
    missing_items: list[dict[str, Any]] = []
    gap_low = 0
    gap_high = 0

    # Sales tax (15 pts)
    if includes_sales_tax:
        score += 15
    else:
        score -= 15
        tax = calculate_sales_tax(offer_amount, state, False)
        if "error" not in tax:
            missing_items.append({"item": "Sales tax", "amount": tax["tax_owed"]})
            gap_low += tax["tax_owed"]
            gap_high += tax["tax_owed"]

    # Standard line items (7 pts each, 5 items = 35 pts)
    item_ids = [
        "title_registration", "comparable_adjustments",
        "dealer_fees", "loss_of_use", "aftermarket",
    ]
    included_count = 0
    for item_id in item_ids:
        if item_id in included_items:
            score += 7
            included_count += 1
        else:
            item_data = next(
                (i for i in CHECKLIST_STANDARD if i["id"] == item_id), None
            )
            if item_data:
                missing_items.append({
                    "item": item_data["label"],
                    "low": item_data.get("range_low", 0),
                    "high": item_data.get("range_high", 0),
                })
                gap_low += item_data.get("range_low", 0)
                gap_high += item_data.get("range_high", 0)

    # Penalty for many missing items
    missing_count = len(item_ids) - included_count
    if missing_count >= 3:
        score -= 10

    score = max(0, min(100, score))

    if score >= 80:
        severity = "Your offer looks reasonable, but there may still be room to negotiate."
    elif score >= 60:
        severity = "Your offer is below average. Several common line items appear to be missing."
    elif score >= 40:
        severity = "Your offer is significantly below what you're likely owed."
    else:
        severity = "Your offer is very low. You're likely leaving thousands on the table."

    return {
        "score": score,
        "severity": severity,
        "missing_items": missing_items,
        "estimated_gap_low": gap_low,
        "estimated_gap_high": gap_high,
        "adjusted_offer_low": offer_amount + gap_low,
        "adjusted_offer_high": offer_amount + gap_high,
    }


def get_tool_for_article(keyword: str, content: str = "") -> str | None:
    """Pick the best tool to embed based on article keyword/content.

    Returns tool id or None if no strong match.  Falls back to
    ``fairness_quiz`` as the universal default.
    """
    text = f"{keyword} {content[:500]}".lower()

    best_tool = None
    best_hits = 0
    for tool_id, triggers in TOOL_TOPIC_MAP.items():
        hits = sum(1 for t in triggers if t in text)
        if hits > best_hits:
            best_hits = hits
            best_tool = tool_id

    # Default: fairness quiz works for any reader
    return best_tool or "fairness_quiz"


def get_tool_display_name(tool_id: str) -> str:
    """Return a human-friendly tool name."""
    return TOOL_DISPLAY_NAMES.get(tool_id, "Calculator")


def get_tools_library_url() -> str:
    """Return the public tools hub URL."""
    return TOOLS_LIBRARY_URL


def export_tool_data_json() -> str:
    """Export all tool data as JSON for the client-side widgets."""
    return json.dumps({
        "state_sales_tax": STATE_SALES_TAX,
        "checklist_standard": CHECKLIST_STANDARD,
        "checklist_state_specific": CHECKLIST_STATE_SPECIFIC,
        "states": sorted(STATE_SALES_TAX.keys()),
    }, indent=2)
