"""
Sidebar component — port search, selection, filters, date range.
"""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd
import streamlit as st


def render_sidebar(port_groups: pd.DataFrame, sublabel_groups: pd.DataFrame) -> dict:
    """
    Render the sidebar controls and return the current selection state.

    Returns dict with keys:
        selected_port   : str | None
        selected_sub    : str | None (sublabel, or None = whole port)
        start_date      : datetime.date
        end_date        : datetime.date
        vessel_types    : list[str]
        min_stay_hours  : int
    """
    st.sidebar.title("🧹 C-LeanWorld")
    st.sidebar.caption("Hull-cleaning robot deployment planner")
    st.sidebar.markdown("---")

    # --- Country filter ---
    countries = sorted(port_groups["iso3"].dropna().unique())
    country = st.sidebar.selectbox(
        "Country (ISO-3)", options=["ALL"] + countries, index=0
    )
    filtered = port_groups if country == "ALL" else port_groups[port_groups["iso3"] == country]

    # --- Port search / select ---
    search = st.sidebar.text_input("🔍 Search port name", "")
    if search:
        filtered = filtered[filtered["label"].str.contains(search.upper(), case=False, na=False)]

    port_options = filtered.sort_values("label")["label"].tolist()
    selected_port = st.sidebar.selectbox(
        "Select port",
        options=["— none —"] + port_options,
        index=0,
    )
    if selected_port == "— none —":
        selected_port = None

    # --- Sub-location ---
    selected_sub: Optional[str] = None
    if selected_port:
        subs = sublabel_groups[sublabel_groups["label"] == selected_port].sort_values("sublabel")
        sub_options = subs["sublabel"].tolist()
        if len(sub_options) > 1:
            selected_sub = st.sidebar.selectbox(
                "Sub-location",
                options=["ALL (whole port)"] + sub_options,
            )
            if selected_sub == "ALL (whole port)":
                selected_sub = None
        elif len(sub_options) == 1:
            selected_sub = sub_options[0]
            st.sidebar.text(f"Sub-location: {selected_sub}")

    st.sidebar.markdown("---")

    # --- Date range ---
    today = datetime.date.today()
    default_start = today - datetime.timedelta(days=365)
    col1, col2 = st.sidebar.columns(2)
    start_date = col1.date_input("From", value=default_start)
    end_date = col2.date_input("To", value=today)

    st.sidebar.markdown("---")

    # --- Vessel type filter ---
    vessel_types = st.sidebar.multiselect(
        "Vessel types",
        options=["cargo", "tanker", "fishing", "passenger", "other"],
        default=["cargo", "tanker"],
    )

    # --- Min stay ---
    min_stay = st.sidebar.slider("Min stay (hours)", 0, 168, 6, step=1)

    return {
        "selected_port": selected_port,
        "selected_sub": selected_sub,
        "start_date": start_date,
        "end_date": end_date,
        "vessel_types": vessel_types,
        "min_stay_hours": min_stay,
    }
