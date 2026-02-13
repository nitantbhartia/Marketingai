"""NHTSA Vehicle Data API integration.

Pulls real vehicle recall, complaint, and specification data from the
National Highway Traffic Safety Administration's free public API.

Used by Quill to enrich vehicle-specific articles with authoritative
government data — a strong E-E-A-T signal that competitors rarely include.

API docs: https://vpic.nhtsa.dot.gov/api/
Recall API: https://api.nhtsa.gov/recalls/recallsByVehicle
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# NHTSA API endpoints (all free, no key required)
VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles"
RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"


def decode_vin(vin: str) -> dict | None:
    """Decode a VIN to get vehicle make, model, year, and specs.

    Uses the NHTSA vPIC (Vehicle Product Information Catalog) API.
    Free, no authentication required.
    """
    url = f"{VPIC_URL}/DecodeVin/{vin}?format=json"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        results = data.get("Results", [])
        decoded: dict[str, str] = {}
        for item in results:
            variable = item.get("Variable", "")
            value = item.get("Value")
            if value and value.strip():
                decoded[variable] = value.strip()

        return {
            "make": decoded.get("Make", ""),
            "model": decoded.get("Model", ""),
            "year": decoded.get("Model Year", ""),
            "body_class": decoded.get("Body Class", ""),
            "vehicle_type": decoded.get("Vehicle Type", ""),
            "plant_city": decoded.get("Plant City", ""),
            "plant_state": decoded.get("Plant State", ""),
            "gvwr": decoded.get("Gross Vehicle Weight Rating From", ""),
        }
    except Exception as e:
        logger.warning(f"NHTSA VIN decode failed for '{vin}': {e}")
        return None


def get_recalls(make: str, model: str, year: int) -> list[dict]:
    """Get recall data for a specific vehicle.

    Returns list of recall records with campaign number, component,
    summary, and consequence.
    """
    params = urllib.parse.urlencode({
        "make": make,
        "model": model,
        "modelYear": year,
    })
    url = f"{RECALLS_URL}?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        recalls: list[dict] = []
        for result in data.get("results", []):
            recalls.append({
                "campaign_number": result.get("NHTSACampaignNumber", ""),
                "component": result.get("Component", ""),
                "summary": result.get("Summary", ""),
                "consequence": result.get("Consequence", ""),
                "remedy": result.get("Remedy", ""),
                "report_date": result.get("ReportReceivedDate", ""),
            })

        return recalls
    except Exception as e:
        logger.warning(f"NHTSA recalls query failed for {year} {make} {model}: {e}")
        return []


def get_complaints(make: str, model: str, year: int) -> dict:
    """Get complaint summary for a specific vehicle.

    Returns count and most common complaint components.
    """
    params = urllib.parse.urlencode({
        "make": make,
        "model": model,
        "modelYear": year,
    })
    url = f"{COMPLAINTS_URL}?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        results = data.get("results", [])
        components: dict[str, int] = {}
        for result in results:
            comp = result.get("components", "Unknown")
            components[comp] = components.get(comp, 0) + 1

        # Sort by frequency
        top_components = sorted(
            components.items(), key=lambda x: x[1], reverse=True
        )[:5]

        return {
            "total_complaints": len(results),
            "top_components": [
                {"component": comp, "count": count}
                for comp, count in top_components
            ],
        }
    except Exception as e:
        logger.warning(f"NHTSA complaints query failed for {year} {make} {model}: {e}")
        return {"total_complaints": 0, "top_components": []}


def parse_vehicle_from_keyword(keyword: str) -> dict | None:
    """Extract make, model, and year from a keyword string.

    Handles patterns like:
    - "2019 Honda Civic total loss value"
    - "Toyota Camry 2020 diminished value"
    - "honda accord total loss"
    """
    # Insurance terms that signal the end of the vehicle name
    _STOP_WORDS = (
        "total|loss|value|diminished|settlement|insurance|claim|worth|cost|"
        "accident|damage|repair|gap|coverage|appraisal|review|guide|calculator|"
        "salvage|threshold|payout|offer|lemon|recall|complaint|average"
    )

    # Pattern: year make model (make and model must not be stop words)
    match = re.search(
        rf"\b(20[0-2][0-9])\s+(?!(?:{_STOP_WORDS})\b)([A-Za-z]+)\s+([A-Za-z]+(?:\s+(?!{_STOP_WORDS}\b)[A-Za-z]+)?)\b",
        keyword,
        re.IGNORECASE,
    )
    if match:
        return {
            "year": int(match.group(1)),
            "make": match.group(2).title(),
            "model": match.group(3).title(),
        }

    # Pattern: make model year (make must not be a stop word)
    match = re.search(
        rf"\b(?!(?:{_STOP_WORDS})\b)([A-Za-z]+)\s+([A-Za-z]+(?:\s+(?!{_STOP_WORDS}\b)[A-Za-z]+)?)\s+(20[0-2][0-9])\b",
        keyword,
        re.IGNORECASE,
    )
    if match:
        return {
            "year": int(match.group(3)),
            "make": match.group(1).title(),
            "model": match.group(2).title(),
        }

    return None


def enrich_vehicle_article(keyword: str) -> str | None:
    """Generate a vehicle data enrichment block for article outlines.

    Pulls recalls and complaints for the vehicle in the keyword and
    formats them as context for Quill's outline generation.
    """
    vehicle = parse_vehicle_from_keyword(keyword)
    if not vehicle:
        return None

    make = vehicle["make"]
    model = vehicle["model"]
    year = vehicle["year"]

    logger.info(f"Enriching with NHTSA data for {year} {make} {model}")

    recalls = get_recalls(make, model, year)
    complaints = get_complaints(make, model, year)

    parts: list[str] = [
        f"=== NHTSA VEHICLE DATA: {year} {make} {model} (Authoritative Government Source) ===",
        f"Source: National Highway Traffic Safety Administration (nhtsa.gov)",
        "",
    ]

    if recalls:
        parts.append(f"**Recalls:** {len(recalls)} recall(s) on record")
        for r in recalls[:3]:
            parts.append(f"  - {r['component']}: {r['summary'][:100]}...")
        parts.append("")

    if complaints.get("total_complaints", 0) > 0:
        parts.append(
            f"**Owner Complaints:** {complaints['total_complaints']} total"
        )
        for comp in complaints.get("top_components", [])[:3]:
            parts.append(f"  - {comp['component']}: {comp['count']} complaints")
        parts.append("")

    if not recalls and complaints.get("total_complaints", 0) == 0:
        parts.append(f"No recalls or significant complaints on record for {year} {make} {model}.")
        parts.append("This is a positive data point — mention it as a vehicle reliability indicator.")
        parts.append("")

    parts.append(
        "IMPORTANT: Cite NHTSA as the source. Link to https://www.nhtsa.gov/ "
        "for E-E-A-T authority. This government data differentiates your article "
        "from competitors who don't cite authoritative sources."
    )

    return "\n".join(parts)
