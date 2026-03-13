"""
Persistent vessel cache backed by diskcache.

Stores vessel particulars (from VesselFinder or GFW) keyed by IMO number.
Once a vessel is cached, it is never re-fetched — this keeps the app fast
and avoids hitting rate limits on repeated queries.

Cache location: data/vessel_cache (auto-created, git-ignored).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import diskcache

_CACHE_DIR = Path(__file__).parent.parent / "data" / "vessel_cache"
_cache: Optional[diskcache.Cache] = None


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
