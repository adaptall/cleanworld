"""
Analytics helpers — visit statistics, duration distributions, scoring.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Port-visit analytics
# ---------------------------------------------------------------------------

def visit_summary(visits_df: pd.DataFrame) -> dict:
    """High-level KPIs from a DataFrame of parsed port-visit records."""
    if visits_df.empty:
        return {"total_visits": 0}

    return {
        "total_visits": len(visits_df),
        "unique_vessels": visits_df["vessel_id"].nunique(),
        "unique_flags": visits_df["vessel_flag"].nunique(),
        "median_duration_h": float(visits_df["duration_hours"].median()) if "duration_hours" in visits_df else None,
        "mean_duration_h": float(visits_df["duration_hours"].mean()) if "duration_hours" in visits_df else None,
        "p90_duration_h": float(visits_df["duration_hours"].quantile(0.9)) if "duration_hours" in visits_df else None,
    }


def visits_by_vessel_type(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Group visits by vessel type and count."""
    if visits_df.empty or "vessel_type" not in visits_df:
        return pd.DataFrame(columns=["vessel_type", "count"])
    return (
        visits_df.groupby("vessel_type", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )


def visits_by_flag(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Group visits by flag state and count."""
    if visits_df.empty or "vessel_flag" not in visits_df:
        return pd.DataFrame(columns=["vessel_flag", "count"])
    return (
        visits_df.groupby("vessel_flag", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )


def monthly_visit_counts(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Time series of visit counts per month."""
    if visits_df.empty or "start" not in visits_df:
        return pd.DataFrame(columns=["month", "count"])
    df = visits_df.copy()
    df["month"] = pd.to_datetime(df["start"]).dt.to_period("M").astype(str)
    return df.groupby("month").size().reset_index(name="count")


# ---------------------------------------------------------------------------
# Duration distribution
# ---------------------------------------------------------------------------

def duration_histogram_data(visits_df: pd.DataFrame, bins: int = 30) -> dict:
    """Return histogram bin edges and counts for duration_hours."""
    dur = visits_df["duration_hours"].dropna()
    if dur.empty:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(dur, bins=bins)
    return {"edges": edges.tolist(), "counts": counts.tolist()}


# ---------------------------------------------------------------------------
# Site-suitability scoring
# ---------------------------------------------------------------------------

def site_score(
    visit_count: int,
    median_duration_h: float,
    pct_current_below_threshold: float,
    weights: dict[str, float] | None = None,
) -> float:
    """
    Compute a normalised 0-100 site-suitability score.

    Components (each normalised 0-1 before weighting):
      market   — based on visit count  (log-scaled, 500+ visits => 1.0)
      dwell    — based on median duration (12h+ => 1.0)
      current  — pct of time currents < threshold (100% => 1.0)
    """
    w = weights or {"market": 0.4, "dwell": 0.3, "current": 0.3}

    # Market: log-scale, cap at 500 visits
    market = min(1.0, np.log1p(visit_count) / np.log1p(500))

    # Dwell: linear ramp 0-12h
    dwell = min(1.0, max(0.0, median_duration_h / 12.0)) if median_duration_h else 0.0

    # Current feasibility: already a pct (0-100)
    current = min(1.0, pct_current_below_threshold / 100.0)

    raw = w["market"] * market + w["dwell"] * dwell + w["current"] * current
    return round(raw * 100, 1)
