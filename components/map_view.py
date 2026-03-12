"""
Map view component — renders ports and anchorage cells on an interactive map.

Uses pydeck for performance with 166k+ points:
  - Port-level markers (centroids, coloured dots)
  - Cell-level scatter when a port is selected (shows physical extent)
"""

from __future__ import annotations

import pydeck as pdk
import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _dock_colour(is_dock: bool) -> list[int]:
    """Blue for dock/berth, orange for anchorage."""
    return [30, 100, 220, 160] if is_dock else [230, 140, 30, 160]


# ---------------------------------------------------------------------------
# Full-world port overview
# ---------------------------------------------------------------------------

def render_port_map(
    port_groups: pd.DataFrame,
    selected_label: str | None = None,
    cell_df: pd.DataFrame | None = None,
    height: int = 550,
) -> None:
    """
    Render the main map.

    - All ports shown as small dots (ScatterplotLayer).
    - If a port is selected, its individual S2 cells are shown as a second
      layer so the user can see the physical extent / shape.
    """

    # --- Port dots layer ---
    port_layer_data = port_groups.copy()
    port_layer_data["radius"] = (port_layer_data["cell_count"].clip(upper=200) * 15 + 500).astype(int)

    # Colour: green if both dock+anchorage, blue if dock only, orange if anchorage only
    def _port_colour(row):
        if row["has_dock"] and row["has_anchorage"]:
            return [50, 180, 80, 180]
        elif row["has_dock"]:
            return [30, 100, 220, 180]
        else:
            return [230, 140, 30, 180]

    port_layer_data["color"] = port_layer_data.apply(_port_colour, axis=1)

    port_layer = pdk.Layer(
        "ScatterplotLayer",
        data=port_layer_data,
        get_position=["centroid_lon", "centroid_lat"],
        get_radius="radius",
        get_fill_color="color",
        pickable=True,
        auto_highlight=True,
        radius_min_pixels=2,
        radius_max_pixels=15,
    )

    layers = [port_layer]

    # --- Initial view ---
    if selected_label and cell_df is not None and not cell_df.empty:
        centre_lat = cell_df["lat"].mean()
        centre_lon = cell_df["lon"].mean()
        # Zoom to fit the port extent
        lat_range = cell_df["lat"].max() - cell_df["lat"].min()
        lon_range = cell_df["lon"].max() - cell_df["lon"].min()
        extent = max(lat_range, lon_range, 0.01)
        zoom = max(5, min(15, 10 - extent * 8))

        # Cell-level layer
        cell_layer_data = cell_df.copy()
        cell_layer_data["color"] = cell_layer_data["is_dock"].apply(
            lambda d: [30, 100, 220, 140] if d else [230, 140, 30, 140]
        )
        cell_layer = pdk.Layer(
            "ScatterplotLayer",
            data=cell_layer_data,
            get_position=["lon", "lat"],
            get_radius=200,
            get_fill_color="color",
            pickable=True,
            auto_highlight=True,
            radius_min_pixels=3,
            radius_max_pixels=10,
        )
        layers.append(cell_layer)
    else:
        centre_lat = 20.0
        centre_lon = 0.0
        zoom = 2

    view_state = pdk.ViewState(
        latitude=centre_lat,
        longitude=centre_lon,
        zoom=zoom,
        pitch=0,
    )

    tooltip = {
        "html": "<b>{label}</b><br/>Country: {iso3}<br/>Cells: {cell_count}",
        "style": {"backgroundColor": "#333", "color": "white", "fontSize": "12px"},
    }

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style="mapbox://styles/mapbox/light-v11",
    )

    st.pydeck_chart(deck, height=height, width="stretch")


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------

def render_map_legend():
    """Show a simple colour legend below the map."""
    st.markdown(
        """
        <div style="display:flex; gap:18px; font-size:13px; margin-top:-8px;">
            <span>🟢 Port + Anchorage</span>
            <span>🔵 Dock / Berth cells</span>
            <span>🟠 Anchorage cells</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
