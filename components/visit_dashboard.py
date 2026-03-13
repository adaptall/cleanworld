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


def _vessel_summary_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a summary table: one row per unique vessel with visit count,
    total hours, mean stay, and vessel details.
    """
    if df.empty:
        return pd.DataFrame()

    agg = df.groupby("vessel_id", dropna=False).agg(
        vessel_name=("vessel_name", "first"),
        vessel_type=("vessel_type", "first"),
        vessel_flag=("vessel_flag", "first"),
        vessel_mmsi=("vessel_mmsi", "first"),
        visits=("event_id", "count"),
        total_hours=("duration_hours", "sum"),
        mean_stay_h=("duration_hours", "mean"),
    ).reset_index()

    # Add enrichment columns if available
    enrich_cols = [
        "imo", "ship_type", "gross_tonnage", "deadweight_t",
        "length_m", "beam_m", "year_built", "teu", "vf_name",
    ]
    for col in enrich_cols:
        if col in df.columns:
            first = df.dropna(subset=[col]).groupby("vessel_id")[col].first()
            agg = agg.merge(first, on="vessel_id", how="left")

    # Use VesselFinder name if available (more reliable)
    if "vf_name" in agg.columns:
        agg["vessel_name"] = agg["vf_name"].fillna(agg["vessel_name"])

    # Ship category for filtering: Container / Tanker / Bulk / Other
    if "ship_type" in agg.columns:
        agg["category"] = agg["ship_type"].apply(_classify_ship_type)

    agg["mean_stay_h"] = agg["mean_stay_h"].round(1)
    agg["total_hours"] = agg["total_hours"].round(1)
    return agg.sort_values("visits", ascending=False)


def _classify_ship_type(ship_type: str | None) -> str:
    """Map detailed VesselFinder ship type to broad category."""
    if not ship_type:
        return "Other"
    st = ship_type.lower()
    if "container" in st:
        return "Container"
    if "tanker" in st or "lng" in st or "lpg" in st or "chemical" in st:
        return "Tanker"
    if "bulk" in st:
        return "Bulk Carrier"
    if "cargo" in st or "general" in st:
        return "Cargo"
    if "passenger" in st or "cruise" in st or "ferry" in st or "ro-pax" in st:
        return "Passenger"
    if "tug" in st or "pilot" in st or "dredg" in st or "supply" in st or "offshore" in st:
        return "Workboat"
    return "Other"


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

    # --- Vessel summary table ---
    st.subheader("🚢 Vessel summary")
    vessel_tbl = _vessel_summary_table(df)
    if not vessel_tbl.empty:
        # Build display columns dynamically
        base_cols = ["vessel_name"]
        # If enriched, show detailed columns
        if "ship_type" in vessel_tbl.columns:
            base_cols += ["category", "ship_type"]
        else:
            base_cols += ["vessel_type"]
        base_cols += ["vessel_flag"]
        for extra in ("gross_tonnage", "deadweight_t", "length_m", "beam_m", "year_built", "teu"):
            if extra in vessel_tbl.columns and vessel_tbl[extra].notna().any():
                base_cols.append(extra)
        base_cols += ["visits", "total_hours", "mean_stay_h"]
        if "imo" in vessel_tbl.columns and vessel_tbl["imo"].notna().any():
            # Add VesselFinder link
            vessel_tbl["details"] = vessel_tbl["imo"].apply(
                lambda x: f"https://www.vesselfinder.com/vessels/details/{x}" if pd.notna(x) else ""
            )
            base_cols.append("details")
        display_cols = [c for c in base_cols if c in vessel_tbl.columns]

        # Category filter (if enriched)
        if "category" in vessel_tbl.columns:
            categories = sorted(vessel_tbl["category"].dropna().unique())
            selected_cats = st.multiselect(
                "Filter by ship category",
                options=categories,
                default=categories,
                key="vessel_cat_filter",
            )
            if selected_cats:
                vessel_tbl = vessel_tbl[vessel_tbl["category"].isin(selected_cats)]

        # Size filter (if enriched)
        if "gross_tonnage" in vessel_tbl.columns and vessel_tbl["gross_tonnage"].notna().any():
            gt_col = vessel_tbl["gross_tonnage"].dropna()
            min_gt, max_gt = int(gt_col.min()), int(gt_col.max())
            if min_gt < max_gt:
                gt_range = st.slider(
                    "Gross tonnage range",
                    min_value=min_gt,
                    max_value=max_gt,
                    value=(min_gt, max_gt),
                    key="gt_filter",
                )
                vessel_tbl = vessel_tbl[
                    vessel_tbl["gross_tonnage"].isna() |
                    vessel_tbl["gross_tonnage"].between(gt_range[0], gt_range[1])
                ]

        st.dataframe(
            vessel_tbl[display_cols],
            width="stretch",
            hide_index=True,
            height=min(500, 35 * len(vessel_tbl) + 38),
            column_config={
                "details": st.column_config.LinkColumn("VesselFinder", display_text="🔗 View"),
                "gross_tonnage": st.column_config.NumberColumn("GT", format="%d"),
                "deadweight_t": st.column_config.NumberColumn("DWT (t)", format="%d"),
                "length_m": st.column_config.NumberColumn("Length (m)", format="%.1f"),
                "beam_m": st.column_config.NumberColumn("Beam (m)", format="%.1f"),
                "year_built": st.column_config.NumberColumn("Built", format="%d"),
                "teu": st.column_config.NumberColumn("TEU", format="%d"),
                "visits": "Visits",
                "total_hours": st.column_config.NumberColumn("Total hrs", format="%.0f"),
                "mean_stay_h": st.column_config.NumberColumn("Avg stay (h)", format="%.1f"),
            },
        )
        st.caption(f"{len(vessel_tbl)} unique vessels · {int(vessel_tbl['visits'].sum())} total visits")
    else:
        st.info("No vessel data to summarise.")
