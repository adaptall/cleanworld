"""
Persistent vessel cache backed by diskcache.

Stores vessel particulars (from VesselFinder or GFW) keyed by IMO number,
and vessel port-visit history keyed by GFW vessel ID.

Cache location: data/vessel_cache (auto-created, git-ignored).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import diskcache

_CACHE_DIR = Path(__file__).parent.parent / "data" / "vessel_cache"
_cache: Optional[diskcache.Cache] = None

# History entries are refreshed if older than this (seconds).
HISTORY_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _get_cache() -> diskcache.Cache:
    global _cache
    if _cache is None:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        _cache = diskcache.Cache(str(_CACHE_DIR), size_limit=500 * 1024 * 1024)  # 500 MB
    return _cache


def get_vessel(imo: str) -> Optional[dict]:
    """Return cached vessel particulars for an IMO, or None if not cached."""
    cache = _get_cache()
    return cache.get(str(imo))


def set_vessel(imo: str, data: dict) -> None:
    """Store vessel particulars in the cache."""
    cache = _get_cache()
    cache.set(str(imo), data)


def get_many(imos: list[str]) -> tuple[dict[str, dict], list[str]]:
    """
    Look up many IMOs at once.

    Returns (found, missing):
        found:   {imo: data_dict} for cached vessels
        missing: list of IMOs not in cache
    """
    cache = _get_cache()
    found: dict[str, dict] = {}
    missing: list[str] = []
    for imo in imos:
        key = str(imo)
        val = cache.get(key)
        if val is not None:
            found[key] = val
        else:
            missing.append(key)
    return found, missing


def cache_stats() -> dict:
    """Return basic cache statistics."""
    cache = _get_cache()
    return {
        "total_vessels": len(cache),
        "size_mb": cache.volume() / (1024 * 1024),
    }


# ---------------------------------------------------------------------------
# Vessel port-visit history cache
# ---------------------------------------------------------------------------


def _history_key(vessel_id: str) -> str:
    return f"history:{vessel_id}"


def get_vessel_history(vessel_id: str) -> Optional[list[dict]]:
    """
    Return cached port-visit history for a GFW vessel ID, or None if not
    cached or expired (older than ``HISTORY_TTL_SECONDS``).
    """
    cache = _get_cache()
    entry = cache.get(_history_key(vessel_id))
    if entry is None:
        return None
    # Entry format: {"ts": epoch, "visits": [...]}
    if time.time() - entry.get("ts", 0) > HISTORY_TTL_SECONDS:
        return None  # expired
    return entry.get("visits")


def set_vessel_history(vessel_id: str, visits: list[dict]) -> None:
    """Store port-visit history for a vessel with a timestamp."""
    cache = _get_cache()
    cache.set(_history_key(vessel_id), {"ts": time.time(), "visits": visits})


def get_many_histories(
    vessel_ids: list[str],
) -> tuple[dict[str, list[dict]], list[str]]:
    """
    Look up cached history for many vessel IDs.

    Returns (found, missing):
        found:   {vessel_id: [visit_dicts]}
        missing: list of vessel IDs not in cache or expired
    """
    found: dict[str, list[dict]] = {}
    missing: list[str] = []
    for vid in vessel_ids:
        visits = get_vessel_history(vid)
        if visits is not None:
            found[vid] = visits
        else:
            missing.append(vid)
    return found, missing
