"""
Copernicus Marine ocean-current data access.

Uses the `copernicusmarine` toolbox to fetch uo/vo (eastward/northward
sea-water velocity) and compute derived current speed & direction.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import xarray as xr


# ---------------------------------------------------------------------------
# Dataset identifiers
# ---------------------------------------------------------------------------
HOURLY_DATASET = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
DAILY_DATASET = "cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m"
VARIABLES = ["uo", "vo"]

MS_TO_KNOTS = 1.94384


def _get_credentials() -> tuple[str, str]:
    """Resolve Copernicus Marine credentials from env or Streamlit secrets."""
    username = os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME", "")
    password = os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD", "")
    # Also check our own env var names
    if not username:
        username = os.environ.get("COPERNICUS_USERNAME", "")
    if not password:
        password = os.environ.get("COPERNICUS_PASSWORD", "")
    # Try Streamlit secrets
    if not username or not password:
        try:
            import streamlit as st
            username = username or st.secrets.get("COPERNICUS_USERNAME", "")
            password = password or st.secrets.get("COPERNICUS_PASSWORD", "")
        except Exception:
            pass
    if not username or not password:
        raise RuntimeError(
            "Copernicus Marine credentials not set. "
            "Put COPERNICUS_USERNAME and COPERNICUS_PASSWORD in .env or Streamlit secrets."
        )
    return username, password


# ---------------------------------------------------------------------------
# Fetch current data via copernicusmarine
# ---------------------------------------------------------------------------

def fetch_currents(
    min_lon: float,
    max_lon: float,
    min_lat: float,
    max_lat: float,
    start_date: str,
    end_date: str,
    dataset_id: str = DAILY_DATASET,
    max_depth: float = 5.0,
    output_dir: str = "data/cache",
) -> xr.Dataset:
    """
    Fetch ocean-current data for a bounding box and time range.

    Uses daily data by default (sufficient for current assessment and
    much faster than hourly).  Passes credentials explicitly to avoid
    interactive prompts.

    Returns an xarray Dataset with variables uo, vo.
    """
    import copernicusmarine

    username, password = _get_credentials()

    try:
        ds = copernicusmarine.open_dataset(
            dataset_id=dataset_id,
            variables=VARIABLES,
            username=username,
            password=password,
            minimum_longitude=min_lon,
            maximum_longitude=max_lon,
            minimum_latitude=min_lat,
            maximum_latitude=max_lat,
            start_datetime=start_date,
            end_datetime=end_date,
            minimum_depth=0,
            maximum_depth=max_depth,
        )
        # Force-load into memory so we don't hold an open remote connection
        ds = ds.load()
        return ds
    except Exception:
        pass

    # Fallback: download subset to file
    os.makedirs(output_dir, exist_ok=True)
    fname = (
        f"currents_{min_lat:.2f}_{max_lat:.2f}_{min_lon:.2f}_{max_lon:.2f}"
        f"_{start_date}_{end_date}.nc"
    ).replace(" ", "_")
    out_path = os.path.join(output_dir, fname)

    if not os.path.exists(out_path):
        copernicusmarine.subset(
            dataset_id=dataset_id,
            variables=VARIABLES,
            username=username,
            password=password,
            minimum_longitude=min_lon,
            maximum_longitude=max_lon,
            minimum_latitude=min_lat,
            maximum_latitude=max_lat,
            start_datetime=start_date,
            end_datetime=end_date,
            minimum_depth=0,
            maximum_depth=max_depth,
            output_filename=fname,
            output_directory=output_dir,
        )
    return xr.open_dataset(out_path)


# ---------------------------------------------------------------------------
# Derived quantities
# ---------------------------------------------------------------------------

def add_speed_direction(ds: xr.Dataset) -> xr.Dataset:
    """Add `current_speed_kn` and `current_dir_deg` variables to the dataset."""
    uo = ds["uo"]
    vo = ds["vo"]
    speed_ms = np.sqrt(uo**2 + vo**2)
    ds["current_speed_kn"] = speed_ms * MS_TO_KNOTS
    ds["current_speed_kn"].attrs["units"] = "knots"
    ds["current_speed_kn"].attrs["long_name"] = "Current speed"

    direction = np.degrees(np.arctan2(vo, uo))  # math convention
    # Convert to oceanographic convention (direction current flows TOWARDS, 0=N clockwise)
    ds["current_dir_deg"] = (90.0 - direction) % 360.0
    ds["current_dir_deg"].attrs["units"] = "degrees_true"
    ds["current_dir_deg"].attrs["long_name"] = "Current direction (towards)"

    return ds


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def current_statistics(ds: xr.Dataset) -> dict:
    """
    Compute summary statistics for current speed at a location.

    Expects `current_speed_kn` to exist (call add_speed_direction first).
    Returns a dict of scalars.
    """
    speed = ds["current_speed_kn"].values.flatten()
    speed = speed[~np.isnan(speed)]

    if len(speed) == 0:
        return {"n_obs": 0}

    return {
        "n_obs": int(len(speed)),
        "mean_kn": float(np.mean(speed)),
        "median_kn": float(np.median(speed)),
        "std_kn": float(np.std(speed)),
        "p25_kn": float(np.percentile(speed, 25)),
        "p75_kn": float(np.percentile(speed, 75)),
        "p90_kn": float(np.percentile(speed, 90)),
        "p99_kn": float(np.percentile(speed, 99)),
        "max_kn": float(np.max(speed)),
        "pct_above_1kn": float(np.mean(speed > 1.0) * 100),
        "pct_above_1_5kn": float(np.mean(speed > 1.5) * 100),
        "pct_above_2kn": float(np.mean(speed > 2.0) * 100),
    }


def hourly_speed_profile(ds: xr.Dataset) -> dict:
    """
    Compute mean current speed by hour of day.

    Returns dict mapping hour (0-23) -> mean speed in knots.
    """
    if "current_speed_kn" not in ds:
        ds = add_speed_direction(ds)

    # Average over lat, lon, depth first to get a time series
    ts = ds["current_speed_kn"].mean(dim=[d for d in ds.dims if d != "time"])
    hours = ts.time.dt.hour.values
    speeds = ts.values

    profile: dict[int, list[float]] = {}
    for h, s in zip(hours, speeds):
        if not np.isnan(s):
            profile.setdefault(int(h), []).append(float(s))

    return {h: float(np.mean(v)) for h, v in sorted(profile.items())}
