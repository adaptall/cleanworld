"""
Port & anchorage reference data loader.

Reads the GFW named-anchorages CSV (166k S2 cells) and produces:
  1. A *grouped* DataFrame of unique port/sub-location entries with centroids —
     used for the sidebar selector, search, and map markers.
  2. The raw cell-level DataFrame — used for plotting individual S2 cells on
     the map so users can see the physical extent of each anchorage/port area.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Default path to the CSV shipped with the repo
# ---------------------------------------------------------------------------
_DEFAULT_CSV = Path(__file__).resolve().parent.parent / "Base Data" / "named_anchorages_v2_pipe_v3_202601.csv"


# ---------------------------------------------------------------------------
# 1.  Load raw cells
# ---------------------------------------------------------------------------

def load_raw_cells(csv_path: Optional[str | Path] = None) -> pd.DataFrame:
    """Load the full cell-level CSV into a DataFrame with proper types."""
    path = Path(csv_path) if csv_path else _DEFAULT_CSV
    if not path.exists():
        raise FileNotFoundError(f"Anchorage CSV not found at {path}")

    df = pd.read_csv(
        path,
        dtype={
            "s2id": str,
            "label": str,
            "sublabel": str,
            "label_source": str,
            "iso3": str,
            "dock": str,
        },
    )

    # Clean up
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["distance_from_shore_m"] = pd.to_numeric(df["distance_from_shore_m"], errors="coerce")
    df["drift_radius"] = pd.to_numeric(df["drift_radius"], errors="coerce")

    # Normalise dock flag to boolean
    df["is_dock"] = df["dock"].str.lower() == "true"

    # Fill blanks
    df["label"] = df["label"].fillna("UNKNOWN")
    df["sublabel"] = df["sublabel"].fillna(df["label"])
    df["iso3"] = df["iso3"].fillna("")

    return df


# ---------------------------------------------------------------------------
# 2.  Group by sublabel (sub-location level)
# ---------------------------------------------------------------------------

def build_sublabel_groups(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate S2 cells to the *sublabel* level.

    Each row = one sub-location (e.g. "CANARY WHARF" under "LONDON").
    Columns produced:
        label, sublabel, iso3, label_source,
        centroid_lat, centroid_lon,
        cell_count, has_dock, has_anchorage,
        mean_distance_from_shore_m, mean_drift_radius,
        min_lat, max_lat, min_lon, max_lon          (bounding box)
    """
    grouped = (
        raw.groupby(["label", "sublabel"], as_index=False)
        .agg(
            iso3=("iso3", "first"),
            label_source=("label_source", "first"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            cell_count=("s2id", "count"),
            has_dock=("is_dock", "any"),
            has_anchorage=("is_dock", lambda s: (~s).any()),
            mean_distance_from_shore_m=("distance_from_shore_m", "mean"),
            mean_drift_radius=("drift_radius", "mean"),
            min_lat=("lat", "min"),
            max_lat=("lat", "max"),
            min_lon=("lon", "min"),
            max_lon=("lon", "max"),
        )
    )
    return grouped


# ---------------------------------------------------------------------------
# 3.  Group by label (port level — top-level name)
# ---------------------------------------------------------------------------

def build_port_groups(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate S2 cells to the *label* (port) level.

    Each row = one port (e.g. "LONDON"), with its centroid, cell count,
    sub-location count, and a bounding box.
    """
    grouped = (
        raw.groupby("label", as_index=False)
        .agg(
            iso3=("iso3", "first"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            cell_count=("s2id", "count"),
            sublabel_count=("sublabel", "nunique"),
            has_dock=("is_dock", "any"),
            has_anchorage=("is_dock", lambda s: (~s).any()),
            mean_distance_from_shore_m=("distance_from_shore_m", "mean"),
            min_lat=("lat", "min"),
            max_lat=("lat", "max"),
            min_lon=("lon", "min"),
            max_lon=("lon", "max"),
        )
    )
    return grouped


# ---------------------------------------------------------------------------
# 4.  Filter helpers
# ---------------------------------------------------------------------------

def get_cells_for_port(raw: pd.DataFrame, label: str) -> pd.DataFrame:
    """Return all S2 cells belonging to a port (label)."""
    return raw[raw["label"] == label].copy()


def get_cells_for_sublabel(raw: pd.DataFrame, label: str, sublabel: str) -> pd.DataFrame:
    """Return all S2 cells belonging to a specific sub-location."""
    return raw[(raw["label"] == label) & (raw["sublabel"] == sublabel)].copy()


def search_ports(port_groups: pd.DataFrame, query: str, limit: int = 20) -> pd.DataFrame:
    """Case-insensitive search on port label. Returns top matches."""
    mask = port_groups["label"].str.contains(query.upper(), case=False, na=False)
    return port_groups[mask].head(limit)


def filter_by_country(port_groups: pd.DataFrame, iso3: str) -> pd.DataFrame:
    """Filter ports by ISO-3 country code."""
    return port_groups[port_groups["iso3"] == iso3.upper()].copy()


# ---------------------------------------------------------------------------
# 5.  Bounding-box / polygon helpers for API calls
# ---------------------------------------------------------------------------

def port_bounding_box(raw: pd.DataFrame, label: str, pad_deg: float = 0.05) -> dict:
    """
    Return a GeoJSON Polygon for the bounding box of a port's cells,
    padded by `pad_deg` degrees.  Suitable for the GFW events API geometry
    parameter and for Copernicus lat/lon subsetting.
    """
    cells = get_cells_for_port(raw, label)
    if cells.empty:
        raise ValueError(f"No cells found for label '{label}'")

    min_lat = float(cells["lat"].min() - pad_deg)
    max_lat = float(cells["lat"].max() + pad_deg)
    min_lon = float(cells["lon"].min() - pad_deg)
    max_lon = float(cells["lon"].max() + pad_deg)

    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],  # close ring
        ]],
    }


def port_bbox_coords(raw: pd.DataFrame, label: str, pad_deg: float = 0.05) -> dict:
    """
    Return a flat dict with min/max lat/lon — handy for Copernicus subset().
    """
    cells = get_cells_for_port(raw, label)
    if cells.empty:
        raise ValueError(f"No cells found for label '{label}'")

    return {
        "minimum_latitude": float(cells["lat"].min() - pad_deg),
        "maximum_latitude": float(cells["lat"].max() + pad_deg),
        "minimum_longitude": float(cells["lon"].min() - pad_deg),
        "maximum_longitude": float(cells["lon"].max() + pad_deg),
    }


# ---------------------------------------------------------------------------
# 6.  Convenience: load everything in one call
# ---------------------------------------------------------------------------

def load_all(csv_path: Optional[str | Path] = None):
    """
    Returns (raw_cells, port_groups, sublabel_groups).
    Intended to be called once at app startup and cached.
    """
    raw = load_raw_cells(csv_path)
    ports = build_port_groups(raw)
    sublabels = build_sublabel_groups(raw)
    return raw, ports, sublabels


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    raw, ports, subs = load_all()
    print(f"Raw cells:       {len(raw):,}")
    print(f"Port groups:     {len(ports):,}")
    print(f"Sublabel groups: {len(subs):,}")
    print(f"\nTop 10 ports by cell count:")
    print(ports.nlargest(10, "cell_count")[["label", "iso3", "cell_count", "sublabel_count", "centroid_lat", "centroid_lon"]].to_string(index=False))

    # Test bounding box
    bbox = port_bounding_box(raw, "SINGAPORE")
    print(f"\nSINGAPORE bbox: {bbox}")
