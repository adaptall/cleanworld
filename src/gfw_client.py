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
PAGE_SIZE = 200  # events per page (GFW allows up to 99999 but large pages are slow)
MAX_EVENTS = 2000  # safety cap on total events to avoid endless fetching

# Standard region grid size for fast queries (degrees).
# GFW indexes data spatially; large round regions hit the index better
# than tight arbitrary polygons.  We query a broad region and filter
# results client-side by port name / topDestination.
STANDARD_REGION_DEG = 4.0


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

def _standard_region_bbox(centre_lat: float, centre_lon: float) -> dict:
    """
    Return a large GeoJSON Polygon snapped to a standard grid.
    This hits GFW spatial indices efficiently and returns fast.
    Results are filtered client-side by port name afterwards.
    """
    half = STANDARD_REGION_DEG / 2
    # Snap to grid
    min_lat = max(-90, (centre_lat // half) * half - half)
    max_lat = min(90, min_lat + STANDARD_REGION_DEG)
    min_lon = max(-180, (centre_lon // half) * half - half)
    max_lon = min(180, min_lon + STANDARD_REGION_DEG)
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def fetch_port_visits(
    geometry: dict,
    start_date: str,
    end_date: str,
    centre_lat: Optional[float] = None,
    centre_lon: Optional[float] = None,
    port_name: Optional[str] = None,
    vessels: Optional[list[str]] = None,
    flags: Optional[list[str]] = None,
    duration: Optional[int] = None,
    limit: int = PAGE_SIZE,
    max_pages: int = 20,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """
    Fetch port-visit events for a date range.

    Strategy: if centre_lat/lon are provided, queries a broad standard
    region (fast because it hits spatial indices), then filters results
    client-side by port_name.  Falls back to the exact geometry if no
    centre is provided.

    Parameters
    ----------
    geometry : dict   GeoJSON Polygon (tight bbox — used as fallback)
    start_date, end_date : str  "YYYY-MM-DD"
    centre_lat, centre_lon : optional port centroid for broad-region query
    port_name : optional — filter results to this port name (topDestination)
    vessels : optional list of GFW vessel IDs
    flags : optional list of ISO-3 flag codes
    duration : optional minimum duration in minutes
    limit : page size
    max_pages : safety cap on pagination
    timeout : per-request timeout in seconds

    Returns
    -------
    list[dict]  — flat list of event dicts from all pages
    """
    url = f"{GFW_BASE}/events"
    all_events: list[dict] = []

    # Use broad standard region if we have a centroid (much faster)
    if centre_lat is not None and centre_lon is not None:
        query_geom = _standard_region_bbox(centre_lat, centre_lon)
    else:
        query_geom = geometry

    body: dict[str, Any] = {
        "datasets": [PORT_VISIT_DATASET],
        "startDate": start_date,
        "endDate": end_date,
        "geometry": query_geom,
    }
    if vessels:
        body["vessels"] = vessels
    if flags:
        body["flags"] = flags
    if duration is not None:
        body["duration"] = duration

    offset = 0
    for _page in range(max_pages):
        params = {"offset": offset, "limit": limit}
        resp = httpx.post(url, headers=_headers(), json=body, params=params, timeout=timeout)

        if resp.status_code == 429:
            # Rate-limited — back off and retry once
            retry_after = int(resp.headers.get("Retry-After", "10"))
            time.sleep(retry_after)
            resp = httpx.post(url, headers=_headers(), json=body, params=params, timeout=timeout)

        resp.raise_for_status()
        data = resp.json()

        entries = data.get("entries", data) if isinstance(data, dict) else data
        if isinstance(entries, dict):
            entries = entries.get("entries", [])
        if not entries:
            break

        all_events.extend(entries)
        if len(entries) < limit:
            break  # last page
        if len(all_events) >= MAX_EVENTS:
            break  # safety cap
        offset += limit

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
