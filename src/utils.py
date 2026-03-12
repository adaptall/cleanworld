"""
Utility functions — haversine distance, coordinate helpers.
"""

from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    R = 6371.0  # Earth radius km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    return haversine_km(lat1, lon1, lat2, lon2) / 1.852


def bbox_pad(min_lat, max_lat, min_lon, max_lon, pad_deg=0.05):
    """Pad a bounding box by `pad_deg` degrees on each side."""
    return {
        "min_lat": min_lat - pad_deg,
        "max_lat": max_lat + pad_deg,
        "min_lon": min_lon - pad_deg,
        "max_lon": max_lon + pad_deg,
    }
