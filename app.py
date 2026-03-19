"""
C-LeanWorld — Hull-Cleaning Robot Deployment Planner
=====================================================
Main Streamlit application.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is on the path so src/ and components/ resolve
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.port_data import (
    load_raw_cells,
    build_port_groups,
    build_sublabel_groups,
    get_cells_for_port,
    get_cells_for_sublabel,
    port_bounding_box,
    port_bbox_coords,
)
from src.gfw_client import fetch_port_visits, parse_port_visits, fetch_vessel_details_batch, fetch_vessel_history, parse_vessel_history
from src.vesselfinder import fetch_vessel_particulars
from src.vessel_cache import (
    get_many as cache_get_many,
    set_vessel as cache_set_vessel,
    cache_stats,
    get_many_histories,
    set_vessel_history,
)
from src.copernicus_client import fetch_currents, add_speed_direction
from src.analytics import site_score
from src.utils import haversine_nm

from components.map_view import render_port_map, render_map_legend
from components.sidebar import render_sidebar
from components.visit_dashboard import render_visit_dashboard
from components.current_dashboard import render_current_dashboard
from components.history_dashboard import render_vessel_history

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="C-LeanWorld",
    page_icon="🧹",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Cache expensive data loads ───────────────────────────────────────────────

@st.cache_data(show_spinner="Loading port & anchorage reference data …")
def _load_data():
    raw = load_raw_cells()
    ports = build_port_groups(raw)
    subs = build_sublabel_groups(raw)
    return raw, ports, subs


raw_cells, port_groups, sublabel_groups = _load_data()

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["🗺️ Simple view", "🔬 Advanced analysis"])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Simple view (placeholder)
# ═══════════════════════════════════════════════════════════════════════════
with tab1:
    st.info("🚧 Simple view coming soon.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Advanced analysis (full existing app)
# ═══════════════════════════════════════════════════════════════════════════
with tab2:
    # ── Sidebar ───────────────────────────────────────────────────────────
    selection = render_sidebar(port_groups, sublabel_groups)

    selected_port = selection["selected_port"]
    selected_sub = selection["selected_sub"]
    start_date = selection["start_date"]
    end_date = selection["end_date"]

    # ── Map ───────────────────────────────────────────────────────────────
    cell_df = None
    if selected_port:
        if selected_sub:
            cell_df = get_cells_for_sublabel(raw_cells, selected_port, selected_sub)
        else:
            cell_df = get_cells_for_port(raw_cells, selected_port)

    render_port_map(port_groups, selected_label=selected_port, cell_df=cell_df)
    render_map_legend()

    # ── Selected port info ────────────────────────────────────────────────
    if selected_port:
        port_row = port_groups[port_groups["label"] == selected_port].iloc[0]

        st.markdown("---")
        info_cols = st.columns([2, 1, 1, 1])
        info_cols[0].markdown(f"### {selected_port}")
        info_cols[1].metric("Country", port_row["iso3"] or "—")
        info_cols[2].metric("S2 cells", int(port_row["cell_count"]))
        info_cols[3].metric("Sub-locations", int(port_row["sublabel_count"]))

        subs = sublabel_groups[sublabel_groups["label"] == selected_port].sort_values("cell_count", ascending=False)
        with st.expander(f"Sub-locations of {selected_port} ({len(subs)})"):
            st.dataframe(
                subs[["sublabel", "iso3", "centroid_lat", "centroid_lon", "cell_count", "has_dock", "has_anchorage", "mean_distance_from_shore_m"]],
                width="stretch",
                hide_index=True,
            )

        # ── Fetch buttons ─────────────────────────────────────────────────
        st.markdown("---")
        col_fetch1, col_fetch2 = st.columns(2)

        with col_fetch1:
            if st.button("🚢 Fetch port visits", type="primary"):
                with st.spinner("Querying Global Fishing Watch …"):
                    try:
                        geometry = port_bounding_box(raw_cells, selected_port)
                        events = fetch_port_visits(
                            geometry=geometry,
                            start_date=str(start_date),
                            end_date=str(end_date),
                            port_name=selected_port,
                            duration=selection["min_stay_hours"] * 60,
                        )
                        records = parse_port_visits(events)
                        visits_df = pd.DataFrame(records)
                        if visits_df.empty:
                            st.warning("No port visits found for this selection.")
                        else:
                            st.success(f"Fetched {len(visits_df)} port-visit events.")
                        st.session_state["visits_df"] = visits_df
                        st.session_state["visits_port"] = selected_port
                    except Exception as e:
                        st.error(f"GFW API error: {e}")

        with col_fetch2:
            if st.button("🌊 Fetch ocean currents", type="secondary"):
                with st.spinner("Querying Copernicus Marine …"):
                    try:
                        bbox = port_bbox_coords(raw_cells, selected_port, pad_deg=0.02)
                        ds = fetch_currents(
                            min_lon=bbox["minimum_longitude"],
                            max_lon=bbox["maximum_longitude"],
                            min_lat=bbox["minimum_latitude"],
                            max_lat=bbox["maximum_latitude"],
                            start_date=str(start_date),
                            end_date=str(end_date),
                        )
                        ds = add_speed_direction(ds)
                        st.session_state["current_ds"] = ds
                        st.session_state["current_port"] = selected_port
                    except Exception as e:
                        st.error(f"Copernicus error: {e}")

        # ── Visit dashboard ───────────────────────────────────────────────
        if st.session_state.get("visits_port") == selected_port and "visits_df" in st.session_state:
            st.markdown("---")
            vdf = st.session_state["visits_df"]
            selected_vtypes = selection.get("vessel_types", [])
            if not vdf.empty and selected_vtypes and "vessel_type" in vdf.columns:
                mask = vdf["vessel_type"].str.lower().isin([v.lower() for v in selected_vtypes])
                vdf_filtered = vdf[mask]
                n_excluded = len(vdf) - len(vdf_filtered)
                if n_excluded:
                    st.caption(f"ℹ️ Showing {len(vdf_filtered)} of {len(vdf)} visits (filtered to: {', '.join(selected_vtypes)})")
            else:
                vdf_filtered = vdf
            render_visit_dashboard(vdf_filtered, selected_port)

            # ── Enrich vessel details ─────────────────────────────────────
            if not vdf_filtered.empty and "gross_tonnage" not in vdf_filtered.columns:
                if st.button("🔍 Enrich vessel details (type, tonnage, length)"):
                    unique_ids = vdf.loc[vdf["vessel_id"].notna(), "vessel_id"].unique().tolist()
                    if not unique_ids:
                        st.warning("No vessel IDs to enrich.")
                    else:
                        status = st.empty()
                        progress = st.progress(0, text="Phase 1: Fetching IMO numbers from GFW…")

                        status.info(f"Phase 1/2: Looking up IMO numbers for {len(unique_ids)} vessels…")
                        def _gfw_progress(i, total):
                            progress.progress(i / total * 0.4, text=f"GFW vessel {i}/{total}")
                        gfw_details = fetch_vessel_details_batch(unique_ids, progress_callback=_gfw_progress)

                        imo_map: dict[str, str] = {}
                        for vid, det in gfw_details.items():
                            imo = det.get("imo")
                            if imo:
                                imo_map[str(imo)] = vid

                        all_imos = list(imo_map.keys())
                        cached, missing_imos = cache_get_many(all_imos)
                        status.info(
                            f"Phase 2/2: {len(cached)} vessels cached, "
                            f"fetching {len(missing_imos)} from VesselFinder…"
                        )

                        vf_results: dict[str, dict] = dict(cached)
                        for i, imo in enumerate(missing_imos):
                            pct = 0.4 + (i + 1) / max(len(missing_imos), 1) * 0.6
                            progress.progress(pct, text=f"VesselFinder {i+1}/{len(missing_imos)}")
                            info = fetch_vessel_particulars(imo)
                            vf_results[imo] = info
                            cache_set_vessel(imo, info)
                            import time; time.sleep(0.8)

                        progress.progress(1.0, text="Done!")

                        enrich_rows = []
                        for imo, vf_data in vf_results.items():
                            vid = imo_map.get(imo)
                            if vid:
                                enrich_rows.append({
                                    "vessel_id": vid,
                                    "imo": imo,
                                    "gross_tonnage": vf_data.get("gross_tonnage"),
                                    "deadweight_t": vf_data.get("deadweight_t"),
                                    "length_m": vf_data.get("length_m"),
                                    "beam_m": vf_data.get("beam_m"),
                                    "year_built": vf_data.get("year_built"),
                                    "ship_type": vf_data.get("ship_type"),
                                    "teu": vf_data.get("teu"),
                                    "vf_name": vf_data.get("vessel_name"),
                                })

                        if enrich_rows:
                            edf = pd.DataFrame(enrich_rows)
                            enriched = vdf.merge(edf, on="vessel_id", how="left")
                            st.session_state["visits_df"] = enriched

                        n_found = sum(1 for r in enrich_rows if r.get("gross_tonnage"))
                        stats = cache_stats()
                        status.success(
                            f"Enriched {n_found}/{len(unique_ids)} vessels · "
                            f"Cache: {stats['total_vessels']} vessels ({stats['size_mb']:.1f} MB)"
                        )
                        progress.empty()
                        st.rerun()

            # ── Vessel travel history ─────────────────────────────────────
            if not vdf_filtered.empty and "vessel_id" in vdf_filtered.columns:
                unique_vids = vdf_filtered.loc[
                    vdf_filtered["vessel_id"].notna(), "vessel_id"
                ].unique().tolist()

                from datetime import timedelta
                history_end = str(end_date)
                history_start = str(start_date - timedelta(days=90))

                if st.button("📍 Fetch vessel travel history"):
                    with st.spinner("Querying GFW for vessel travel history …"):
                        status = st.empty()
                        progress = st.progress(0, text="Checking cache…")

                        cached_hist, missing_vids = get_many_histories(unique_vids)
                        status.info(
                            f"{len(cached_hist)} vessels cached, "
                            f"fetching history for {len(missing_vids)}…"
                        )

                        if missing_vids:
                            def _hist_progress(done, total):
                                pct = (len(cached_hist) + done) / max(len(unique_vids), 1)
                                progress.progress(min(pct, 1.0), text=f"GFW batch {done}/{total}")

                            raw_events = fetch_vessel_history(
                                vessel_ids=missing_vids,
                                start_date=history_start,
                                end_date=history_end,
                                batch_size=10,
                                timeout=90.0,
                                progress_callback=_hist_progress,
                            )
                            new_hist = parse_vessel_history(raw_events)

                            for vid, visits in new_hist.items():
                                set_vessel_history(vid, visits)
                            cached_hist.update(new_hist)

                        for vid in unique_vids:
                            if vid not in cached_hist:
                                cached_hist[vid] = []
                                set_vessel_history(vid, [])

                        st.session_state["vessel_history"] = cached_hist
                        st.session_state["history_port"] = selected_port

                        total_visits = sum(len(v) for v in cached_hist.values())
                        progress.progress(1.0, text="Done!")
                        status.success(
                            f"Loaded travel history: {total_visits} port calls "
                            f"across {len(cached_hist)} vessels"
                        )
                        progress.empty()

            if (
                st.session_state.get("history_port") == selected_port
                and "vessel_history" in st.session_state
            ):
                st.markdown("---")
                vname_map = {}
                for _, row in vdf_filtered.drop_duplicates("vessel_id").iterrows():
                    vid = row.get("vessel_id")
                    name = row.get("vf_name") or row.get("vessel_name") or vid
                    if vid:
                        vname_map[vid] = name or vid
                render_vessel_history(
                    st.session_state["vessel_history"],
                    vname_map,
                    selected_port,
                )

        # ── Current dashboard ─────────────────────────────────────────────
        if st.session_state.get("current_port") == selected_port and "current_ds" in st.session_state:
            st.markdown("---")
            render_current_dashboard(st.session_state["current_ds"], selected_port)

    else:
        st.markdown("---")
        st.info("👈 Select a port or anchorage from the sidebar to begin analysis.")
        st.markdown(
            """
            **How to use:**
            1. Filter by country and/or search for a port name in the sidebar.
            2. Select a port — the map will zoom in and show individual S2 cells.
            3. Choose a date range, then click **Fetch port visits** or **Fetch ocean currents**.
            4. Explore the analytics dashboards that appear below the map.
            """
        )

