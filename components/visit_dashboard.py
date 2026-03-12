"""
Port-visit analytics dashboard — charts and KPIs.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics import (
    visit_summary,
    visits_by_vessel_type,
    visits_by_flag,
    monthly_visit_counts,
)


def render_visit_dashboard(visits_df: pd.DataFrame, port_name: str) -> None:
    """Render the full visit analytics panel for a selected port."""

    st.subheader(f"📊 Port visits — {port_name}")

    if visits_df.empty:
        st.info("No port-visit data returned for this selection.")
        return

    # Filter out likely permanent moorings (>180 days)
    MAX_DURATION_H = 180 * 24  # 180 days
    df = visits_df.copy()
    permanent = df["duration_hours"].fillna(0) > MAX_DURATION_H
    n_filtered = permanent.sum()
    df = df[~permanent]

    if n_filtered > 0:
        st.caption(f"ℹ️ {n_filtered} visits with duration > 180 days excluded (likely permanent moorings).")

    if df.empty:
        st.info("All visits were permanent moorings — no transient visits to analyse.")
        return

    # --- KPI row ---
    summary = visit_summary(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total visits", summary["total_visits"])
    c2.metric("Unique vessels", summary["unique_vessels"])
    c3.metric("Median stay (h)", f"{summary['median_duration_h']:.1f}" if summary["median_duration_h"] else "—")
    c4.metric("P90 stay (h)", f"{summary['p90_duration_h']:.1f}" if summary["p90_duration_h"] else "—")

    # --- Dock vs Anchorage split ---
    if "at_dock" in df.columns:
        c5, c6, _ , _ = st.columns(4)
        dock_count = df["at_dock"].sum() if df["at_dock"].notna().any() else 0
        anch_count = len(df) - dock_count
        c5.metric("At dock", int(dock_count))
        c6.metric("At anchorage", int(anch_count))

    # --- Charts row ---
    col_left, col_right = st.columns(2)

    with col_left:
        # Duration histogram
        if "duration_hours" in df and df["duration_hours"].notna().any():
            fig_dur = px.histogram(
                df.dropna(subset=["duration_hours"]),
                x="duration_hours",
                nbins=30,
                title="Stay duration distribution",
                labels={"duration_hours": "Duration (hours)"},
            )
            fig_dur.update_layout(height=320, margin=dict(t=40, b=30))
            st.plotly_chart(fig_dur, width="stretch")

    with col_right:
        # Vessel type pie
        vt = visits_by_vessel_type(df)
        if not vt.empty:
            fig_vt = px.pie(vt, names="vessel_type", values="count", title="Visits by vessel type")
            fig_vt.update_layout(height=320, margin=dict(t=40, b=30))
            st.plotly_chart(fig_vt, width="stretch")

    # --- Monthly time series ---
    monthly = monthly_visit_counts(df)
    if not monthly.empty:
        fig_ts = px.bar(monthly, x="month", y="count", title="Monthly visit count")
        fig_ts.update_layout(height=280, margin=dict(t=40, b=30))
        st.plotly_chart(fig_ts, width="stretch")

    # --- Flag breakdown (top 10) ---
    vf = visits_by_flag(df)
    if not vf.empty:
        with st.expander("Flag-state breakdown (top 15)"):
            st.dataframe(vf.head(15), width="stretch", hide_index=True)
