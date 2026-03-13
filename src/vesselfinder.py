"""
VesselFinder vessel particulars scraper.

Fetches public vessel detail pages by IMO number and extracts key
particulars (tonnage, length, beam, DWT, year built, ship type).

Usage:
    from src.vesselfinder import fetch_vessel_particulars
    info = fetch_vessel_particulars("9920772")
"""

from __future__ import annotations

import re
import time
from typing import Optional

import httpx

VF_BASE = "https://www.vesselfinder.com/vessels/details"

# Browser-like headers to avoid being blocked
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _extract_field(html: str, label: str) -> Optional[str]:
    """
    Extract a value from VesselFinder's "VESSEL PARTICULARS" table.
    The HTML uses <td> pairs: <td>Label</td><td>Value</td>.
    """
    # Try pattern: label immediately followed by value in adjacent td/span
    pattern = re.compile(
        rf'{re.escape(label)}\s*</td>\s*<td[^>]*>\s*(.*?)\s*</td>',
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(html)
    if m:
        val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return val if val and val != '-' else None

    # Fallback: look in plain text after the label
    pattern2 = re.compile(
        rf'{re.escape(label)}\s*[:\s]+([0-9][0-9.,]*)',
        re.IGNORECASE,
    )
    m2 = pattern2.search(html)
    if m2:
        return m2.group(1).strip()

    return None


def _to_float(val: Optional[str]) -> Optional[float]:
    """Convert a scraped string to float, handling commas."""
    if not val:
        return None
    try:
        return float(val.replace(',', ''))
    except (ValueError, TypeError):
        return None


def _to_int(val: Optional[str]) -> Optional[int]:
    """Convert a scraped string to int."""
    f = _to_float(val)
    return int(f) if f is not None else None


def fetch_vessel_particulars(imo: str, timeout: float = 15.0) -> dict:
    """
    Fetch vessel particulars from VesselFinder by IMO number.

    Returns a dict with keys:
        imo, vessel_name, ship_type, flag, year_built,
        gross_tonnage, deadweight_t, length_m, beam_m, draught_m
    """
    url = f"{VF_BASE}/{imo}"
    result: dict = {"imo": str(imo)}

    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return result
        html = resp.text
    except Exception:
        return result

    # Vessel name — from <h1> or <title>
    name_match = re.search(r'<h1[^>]*>\s*(.*?)\s*</h1>', html, re.DOTALL)
    if name_match:
        result["vessel_name"] = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()

    # Ship type — from subtitle or meta
    type_match = re.search(r'<h2[^>]*>\s*(.*?),\s*IMO', html, re.DOTALL)
    if type_match:
        result["ship_type"] = re.sub(r'<[^>]+>', '', type_match.group(1)).strip()

    # Extract from VESSEL PARTICULARS section
    result["gross_tonnage"] = _to_int(_extract_field(html, "Gross Tonnage"))
    result["deadweight_t"] = _to_int(_extract_field(html, "Deadweight"))
    result["length_m"] = _to_float(_extract_field(html, "Length Overall"))
    result["beam_m"] = _to_float(_extract_field(html, "Beam"))
    result["draught_m"] = _to_float(_extract_field(html, "Draught"))
    result["year_built"] = _to_int(_extract_field(html, "Year of Build"))
    result["teu"] = _to_int(_extract_field(html, "TEU"))

    # Flag
    flag_raw = _extract_field(html, "Flag")
    if flag_raw:
        result["flag"] = flag_raw

    # Length/Beam from the summary line "Length / Beam  330 / 60 m"
    lb_match = re.search(r'Length\s*/\s*Beam\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*/\s*([\d.]+)', html)
    if lb_match:
        if result.get("length_m") is None:
            result["length_m"] = _to_float(lb_match.group(1))
        if result.get("beam_m") is None:
            result["beam_m"] = _to_float(lb_match.group(2))

    return result


def fetch_vessel_particulars_batch(
    imos: list[str],
    delay: float = 1.0,
    timeout: float = 15.0,
    progress_callback=None,
) -> dict[str, dict]:
    """
    Fetch vessel particulars for a list of IMO numbers.
    Returns {imo: particulars_dict}.

    Uses a polite delay between requests to avoid rate limiting.
    progress_callback(i, total) is called after each fetch.
    """
    results: dict[str, dict] = {}
    total = len(imos)
    for i, imo in enumerate(imos):
        results[str(imo)] = fetch_vessel_particulars(str(imo), timeout=timeout)
        if progress_callback:
            progress_callback(i + 1, total)
        if i < total - 1:
            time.sleep(delay)
    return results
