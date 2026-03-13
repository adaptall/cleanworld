"""
Vessel travel-history dashboard component.

Displays the port-visit itinerary for vessels that visited the selected port,
plus aggregate trade-route analytics.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.express as px
import streamlit as st


def render_vessel_history(
    history: dict[str, list[dict]],
    vessel_names: dict[str, str],
    selected_port: str,
) -> None:
    """
    Render a travel-history section.

    Parameters
    ----------
    history : {vessel_id: [visit_dicts]}
        Each visit dict has: start, end, port_name, port_flag, duration_hours,
        at_dock, lat, lon.
    vessel_names : {vessel_id: display_name}
    selected_port : current port label (used for highlighting)
    """
    st.subheader("📍 Vessel travel history")

    if not history:
        st.info("No travel history loaded yet.")
        return

    # ── Aggregate stats ────────────────────────────────────────────────
    all_visits: list[dict] = []
    for vid, visits in history.items():
        for v in visits:
            all_visits.append({**v, "vessel_id": vid})

    if not all_visits:
        st.info("No port-visit events found for these vessels.")
        return

    adf = pd.DataFrame(all_visits)

    # KPI row
    total_vessels = len(history)
    total_visits = len(adf)
    unique_ports = adf["port_name"].nunique()
    unique_countries = adf["port_flag"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vessels tracked", total_vessels)
    c2.metric("Total port calls", total_visits)
    c3.metric("Unique ports visited", unique_ports)
    c4.metric("Countries", unique_countries)

    # ── Top ports chart ────────────────────────────────────────────────
    port_counts = (
        adf.groupby("port_name")
        .agg(visits=("port_name", "size"), unique_vessels=("vessel_id", "nunique"))
        .reset_index()
        .sort_values("visits", ascending=False)
        .head(20)
    )

    col_left, col_right = st.columns(2)
    with col_left:
        fig = px.bar(
            port_counts,
            x="visits",
            y="port_name",
            orientation="h",
            title="Top ports visited by these vessels",
            labels={"port_name": "Port", "visits": "Visit count"},
            color="unique_vessels",
            color_continuous_scale="Viridis",
        )
        fig.update_layout(
            height=420,
            margin=dict(t=40, b=30),
            yaxis=dict(autorange="reversed"),
            coloraxis_colorbar_title="Vessels",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Top countries chart ────────────────────────────────────────────
    with col_right:
        country_counts = (
            adf[adf["port_flag"].str.len() > 0]
            .groupby("port_flag")
            .agg(visits=("port_flag", "size"))
            .reset_index()
            .sort_values("visits", ascending=False)
            .head(15)
        )
        if not country_counts.empty:
            fig2 = px.bar(
                country_counts,
                x="visits",
                y="port_flag",
                orientation="h",
                title="Top countries visited",
                labels={"port_flag": "Country (ISO3)", "visits": "Visit count"},
            )
            fig2.update_layout(
                height=420,
                margin=dict(t=40, b=30),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ── Trade routes (port → port connections) ─────────────────────────
    _render_trade_routes(adf, selected_port)

    # ── Per-vessel itinerary ───────────────────────────────────────────
    st.markdown("#### Per-vessel itinerary")
    port_upper = selected_port.upper()

    for vid, visits in sorted(
        history.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    ):
        name = vessel_names.get(vid, vid[:12])
        with st.expander(f"{name}  ({len(visits)} port calls)"):
            rows = []
            for v in visits:
                dur = v.get("duration_hours")
                pname = v.get("port_name") or "Unknown"
                highlight = "⭐ " if pname.upper() == port_upper else ""
                rows.append({
                    "Date": (v.get("start") or "?")[:10],
                    "Port": f"{highlight}{pname}",
                    "Country": v.get("port_flag") or "",
                    "Duration (h)": round(dur, 1) if dur else None,
                    "At dock": "✓" if v.get("at_dock") else "",
                })
            idf = pd.DataFrame(rows)
            st.dataframe(idf, hide_index=True, use_container_width=True, height=min(350, 35 * len(idf) + 38))


def _render_trade_routes(adf: pd.DataFrame, selected_port: str) -> None:
    """Show a summary of port-to-port connections (trade routes)."""

    # Build consecutive pairs per vessel
    pairs: list[tuple[str, str]] = []
    for vid, grp in adf.sort_values("start").groupby("vessel_id"):
        ports = grp["port_name"].tolist()
        for a, b in zip(ports, ports[1:]):
            if a and b and a != b:
                # Normalise pair order for undirected routes
                pairs.append((min(a, b), max(a, b)))

    if not pairs:
        return

    route_counts = Counter(pairs)
    top_routes = route_counts.most_common(15)

    with st.expander("🔗 Top trade routes (port-to-port connections)"):
        rows = []
        for (a, b), cnt in top_routes:
            rows.append({"Port A": a, "Port B": b, "Connections": cnt})
        rdf = pd.DataFrame(rows)
        st.dataframe(rdf, hide_index=True, use_container_width=True)
