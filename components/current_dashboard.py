"""
Ocean-current analytics dashboard — speed histogram, direction rose, hourly profile.
"""

from __future__ import annotations

import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import xarray as xr

from src.copernicus_client import add_speed_direction, current_statistics, hourly_speed_profile


def render_current_dashboard(ds: xr.Dataset, location_name: str) -> None:
    """Render ocean-current analytics panel for a selected anchorage."""

    st.subheader(f"🌊 Ocean currents — {location_name}")

    # Ensure derived variables exist
    if "current_speed_kn" not in ds:
        ds = add_speed_direction(ds)

    stats = current_statistics(ds)

    if stats["n_obs"] == 0:
        st.info("No current observations available for this selection.")
        return

    # --- KPI row ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mean (kn)", f"{stats['mean_kn']:.2f}")
    c2.metric("Median (kn)", f"{stats['median_kn']:.2f}")
    c3.metric("P90 (kn)", f"{stats['p90_kn']:.2f}")
    c4.metric("Max (kn)", f"{stats['max_kn']:.2f}")

    c5, c6, c7, _ = st.columns(4)
    c5.metric("% > 1.0 kn", f"{stats['pct_above_1kn']:.1f}%")
    c6.metric("% > 1.5 kn", f"{stats['pct_above_1_5kn']:.1f}%")
    c7.metric("Std-dev (kn)", f"{stats['std_kn']:.2f}")

    # --- Charts ---
    col_left, col_right = st.columns(2)

    # Speed histogram
    speed_vals = ds["current_speed_kn"].values.flatten()
    speed_vals = speed_vals[~np.isnan(speed_vals)]

    with col_left:
        fig_hist = px.histogram(
            x=speed_vals,
            nbins=40,
            title="Current speed distribution",
            labels={"x": "Speed (knots)"},
        )
        fig_hist.update_layout(height=320, margin=dict(t=40, b=30))
        st.plotly_chart(fig_hist, width="stretch")

    # Direction rose
    with col_right:
        dir_vals = ds["current_dir_deg"].values.flatten()
        dir_vals = dir_vals[~np.isnan(dir_vals)]
        if len(dir_vals) > 0:
            # Bin into 16 compass sectors
            bins = np.arange(0, 360 + 22.5, 22.5)
            labels_compass = [
                "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
            ]
            sector_idx = np.digitize(dir_vals, bins) - 1
            sector_idx = sector_idx % 16
            counts = np.bincount(sector_idx, minlength=16)
            fig_rose = go.Figure(go.Barpolar(
                r=counts,
                theta=labels_compass,
                marker_color="steelblue",
                opacity=0.8,
            ))
            fig_rose.update_layout(
                title="Current direction (towards)",
                polar=dict(angularaxis=dict(direction="clockwise", rotation=90)),
                height=350,
                margin=dict(t=50, b=20),
            )
            st.plotly_chart(fig_rose, width="stretch")

    # Hourly speed profile
    profile = hourly_speed_profile(ds)
    if profile:
        hours = list(profile.keys())
        speeds = list(profile.values())
        fig_hour = px.line(
            x=hours, y=speeds,
            title="Mean current speed by hour of day",
            labels={"x": "Hour (UTC)", "y": "Speed (knots)"},
            markers=True,
        )
        fig_hour.update_layout(height=280, margin=dict(t=40, b=30))
        st.plotly_chart(fig_hour, width="stretch")
