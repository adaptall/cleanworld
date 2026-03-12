"""
GFW API client — wraps the Global Fishing Watch V3 Events API for port visits.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"
PORT_VISIT_DATASET = "public-global-port-visits-events:latest"
# We request ALL results in a single API call (no pagination).
# GFW allows limit up to 99999.  A single large request is much faster
# than multiple paginated requests because the server only runs the
# spatial query once.
SINGLE_REQUEST_LIMIT = 99999

# Standard region grid size for fast queries (degrees).
# A 2° box is ~220 km across — large enough to cover any port area,
# small enough to keep results manageable.
STANDARD_REGION_DEG = 2.0


def _get_token() -> str:
    """Resolve GFW bearer token from env or Streamlit secrets."""
    token = os.environ.get("GFW_TOKEN", "")
    if not token:
        try:
            import streamlit as st
            token = st.secrets.get("GFW_TOKEN", "")
        except Exception:
            pass
    if not token:
        raise RuntimeError("GFW_TOKEN not set.  Put it in .env or Streamlit secrets.")
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Port-visit events
# ---------------------------------------------------------------------------


def fetch_port_visits(
    geometry: dict,
    start_date: str,
    end_date: str,
    port_name: Optional[str] = None,
    vessels: Optional[list[str]] = None,
    flags: Optional[list[str]] = None,
    duration: Optional[int] = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """
    Fetch port-visit events in a **single** API call.

    Uses the tight bounding box derived from the anchorage/berth cells
    (typically only a few km across).  All results are returned in one
    request (limit=99999), no pagination needed.

    Parameters
    ----------
    geometry : dict   GeoJSON Polygon (tight bbox from port cells + pad)
    start_date, end_date : str  "YYYY-MM-DD"
    port_name : optional — further filter results by anchorage name
    timeout : per-request timeout in seconds
    """
    url = f"{GFW_BASE}/events"

    body: dict[str, Any] = {
        "datasets": [PORT_VISIT_DATASET],
        "startDate": start_date,
        "endDate": end_date,
        "geometry": geometry,
    }
    if vessels:
        body["vessels"] = vessels
    if flags:
        body["flags"] = flags
    if duration is not None:
        body["duration"] = duration

    # Single request — no pagination loop
    params = {"offset": 0, "limit": SINGLE_REQUEST_LIMIT}
    resp = httpx.post(url, headers=_headers(), json=body, params=params, timeout=timeout)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "10"))
        time.sleep(retry_after)
        resp = httpx.post(url, headers=_headers(), json=body, params=params, timeout=timeout)

    resp.raise_for_status()
    data = resp.json()

    entries = data.get("entries", data) if isinstance(data, dict) else data
    if isinstance(entries, dict):
        entries = entries.get("entries", [])
    all_events = entries if entries else []

    # --- Client-side filter by port name ---
    if port_name and all_events:
        port_upper = port_name.upper()
        filtered = []
        for ev in all_events:
            pv = ev.get("port_visit", {}) or {}
            for anch_key in ("startAnchorage", "intermediateAnchorage", "endAnchorage"):
                anch = pv.get(anch_key, {}) or {}
                top_dest = (anch.get("topDestination") or "").upper()
                anch_name = (anch.get("name") or "").upper()
                if port_upper in (top_dest, anch_name):
                    filtered.append(ev)
                    break
        return filtered

    return all_events


# ---------------------------------------------------------------------------
# Vessel search (lightweight helper)
# ---------------------------------------------------------------------------

def search_vessels(
    query: str,
    limit: int = 10,
    datasets: list[str] | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Search vessels by name, MMSI, or IMO."""
    url = f"{GFW_BASE}/vessels/search"
    params: dict[str, Any] = {"query": query, "limit": limit}
    if datasets:
        params["datasets"] = datasets
    resp = httpx.get(url, headers=_headers(), params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("entries", [])


# ---------------------------------------------------------------------------
# Parse port-visit events into a flat table
# ---------------------------------------------------------------------------

def parse_port_visits(events: list[dict]) -> list[dict]:
    """
    Flatten raw GFW port-visit event dicts into records suitable for a DataFrame.

    Uses the rich `port_visit` sub-object when available (GFW V3) which
    contains durationHrs, anchorage details, confidence, etc.

    Returns list of dicts.
    """
    records = []
    for ev in events:
        vessel = ev.get("vessel", {}) or {}
        pos = ev.get("position", {}) or {}
        pv = ev.get("port_visit", {}) or {}

        start_anch = pv.get("startAnchorage", {}) or {}
        end_anch = pv.get("endAnchorage", {}) or {}
        int_anch = pv.get("intermediateAnchorage", {}) or {}

        # Duration: prefer the API-provided value
        duration_h = pv.get("durationHrs")

        # Fallback: compute from start/end timestamps
        start = ev.get("start")
        end = ev.get("end")
        if duration_h is None and start and end:
            from datetime import datetime
            try:
                t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
                duration_h = (t1 - t0).total_seconds() / 3600
            except Exception:
                pass

        records.append({
            "event_id": ev.get("id"),
            "visit_id": pv.get("visitId"),
            "confidence": pv.get("confidence"),
            # Vessel info
            "vessel_id": vessel.get("id"),
            "vessel_name": vessel.get("name"),
            "vessel_mmsi": vessel.get("ssvid"),
            "vessel_flag": vessel.get("flag"),
            "vessel_type": vessel.get("type"),
            # Timing
            "start": start,
            "end": end,
            "duration_hours": duration_h,
            # Anchorage info
            "port_name": start_anch.get("name") or start_anch.get("topDestination"),
            "port_id": start_anch.get("id"),
            "port_flag": start_anch.get("flag"),
            "at_dock": start_anch.get("atDock"),
            "anchorage_id": start_anch.get("anchorageId"),
            "end_port_name": end_anch.get("name"),
            "end_port_id": end_anch.get("id"),
            # Position
            "lat": pos.get("lat"),
            "lon": pos.get("lon"),
        })
    return records
