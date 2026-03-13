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


def fetch_vessel_detail(vessel_id: str, timeout: float = 20.0) -> dict:
    """
    Fetch detailed vessel info (tonnage, length, IMO, etc.) from GFW.
    Returns a flat dict with key fields.
    """
    url = f"{GFW_BASE}/vessels/{vessel_id}"
    params = {"dataset": "public-global-vessel-identity:latest"}
    try:
        resp = httpx.get(url, headers=_headers(), params=params, timeout=timeout)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(retry_after)
            resp = httpx.get(url, headers=_headers(), params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {"vessel_id": vessel_id}

    # Extract from registryInfo — take the latest record
    registry = data.get("registryInfo", [])
    best = {}
    for entry in registry:
        if entry.get("latestVesselInfo"):
            best = entry
            break
    if not best and registry:
        best = registry[-1]

    return {
        "vessel_id": vessel_id,
        "imo": best.get("imo"),
        "shipname": best.get("shipname"),
        "callsign": best.get("callsign"),
        "tonnage_gt": best.get("tonnageGt"),
        "length_m": best.get("lengthM"),
    }


def fetch_vessel_details_batch(
    vessel_ids: list[str],
    max_concurrent: int = 5,
    timeout: float = 15.0,
    progress_callback=None,
) -> dict[str, dict]:
    """
    Fetch vessel details for a list of IDs (sequential with rate-limit
    awareness).  Returns {vessel_id: detail_dict}.

    progress_callback(i, total) is called after each fetch if provided.
    """
    results: dict[str, dict] = {}
    total = len(vessel_ids)
    for i, vid in enumerate(vessel_ids):
        results[vid] = fetch_vessel_detail(vid, timeout=timeout)
        if progress_callback:
            progress_callback(i + 1, total)
        # Brief pause to respect rate limits
        if (i + 1) % max_concurrent == 0:
            time.sleep(0.5)
    return results


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


# ---------------------------------------------------------------------------
# Vessel port-visit history (global, per-vessel)
# ---------------------------------------------------------------------------

# Global bbox covering the whole world — used to anchor the spatial query
# so the GFW server can use its spatial index efficiently.
_GLOBAL_BBOX: dict = {
    "type": "Polygon",
    "coordinates": [[
        [-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90],
    ]],
}


def fetch_vessel_history(
    vessel_ids: list[str],
    start_date: str,
    end_date: str,
    batch_size: int = 10,
    timeout: float = 60.0,
    progress_callback=None,
) -> list[dict]:
    """
    Fetch the global port-visit history for a list of vessels.

    Uses the GFW events endpoint with a global bounding box and
    ``vessels`` filter — this forces the server to use its spatial index
    and returns results in ~6–25 s per batch.

    Parameters
    ----------
    vessel_ids : list of GFW vessel ID strings
    start_date, end_date : "YYYY-MM-DD"
    batch_size : how many vessels per API call (default 10)
    timeout : per-request timeout in seconds
    progress_callback : optional (done, total) callable

    Returns
    -------
    list of raw event dicts (same schema as ``fetch_port_visits``).
    """
    url = f"{GFW_BASE}/events"
    all_events: list[dict] = []
    total_batches = (len(vessel_ids) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        chunk = vessel_ids[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        body = {
            "datasets": [PORT_VISIT_DATASET],
            "startDate": start_date,
            "endDate": end_date,
            "vessels": chunk,
            "geometry": _GLOBAL_BBOX,
        }
        params = {"offset": 0, "limit": SINGLE_REQUEST_LIMIT}

        try:
            resp = httpx.post(
                url, headers=_headers(), json=body,
                params=params, timeout=timeout,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "10"))
                time.sleep(retry_after)
                resp = httpx.post(
                    url, headers=_headers(), json=body,
                    params=params, timeout=timeout,
                )
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("entries", [])
            if isinstance(entries, dict):
                entries = entries.get("entries", [])
            all_events.extend(entries or [])
        except Exception:
            pass  # skip failed batches, continue

        if progress_callback:
            progress_callback(batch_idx + 1, total_batches)

    return all_events


def parse_vessel_history(events: list[dict]) -> dict[str, list[dict]]:
    """
    Parse raw history events into a ``{vessel_id: [visit_records]}`` dict.

    Each visit record is a compact dict with: start, port_name, port_flag,
    duration_hours, at_dock, lat, lon.  Sorted chronologically.
    """
    from collections import defaultdict

    by_vessel: dict[str, list[dict]] = defaultdict(list)

    for ev in events:
        vessel = ev.get("vessel", {}) or {}
        pv = ev.get("port_visit", {}) or {}
        sa = pv.get("startAnchorage", {}) or {}
        pos = ev.get("position", {}) or {}

        vid = vessel.get("id")
        if not vid:
            continue

        duration_h = pv.get("durationHrs")
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

        by_vessel[vid].append({
            "start": start,
            "end": end,
            "port_name": sa.get("name") or sa.get("topDestination") or "Unknown",
            "port_flag": sa.get("flag") or "",
            "duration_hours": duration_h,
            "at_dock": sa.get("atDock"),
            "lat": pos.get("lat"),
            "lon": pos.get("lon"),
        })

    # Sort each vessel's visits chronologically
    for vid in by_vessel:
        by_vessel[vid].sort(key=lambda r: r.get("start") or "")

    return dict(by_vessel)
