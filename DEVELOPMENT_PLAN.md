# C-LeanWorld — Hull-Cleaning Robot Deployment Planner

## 1. Vision & Purpose

A map-based web application that helps inform business decisions on **where in the world to deploy underwater hull-cleaning robots** for large container and tanker vessels.

For any selected port or anchorage the user can assess:

| Dimension | Question answered |
|---|---|
| **Market size** | How many vessel visits occur? What is the vessel mix (type, size, flag)? |
| **Business fit** | How long do vessels stay (median, P90, distribution)? How far is the anchorage from the port? |
| **Operational feasibility** | What are local ocean current conditions (speed distribution, peak, direction variance)? Are conditions safe for robot deployment? |

---

## 2. Data Sources

### 2.1 Global Fishing Watch (GFW) — V3 API
- **Base URL:** `https://gateway.api.globalfishingwatch.org/v3`
- **Auth:** Bearer token (application name: AVK)
- **Key endpoints used:**

| Endpoint | Purpose | Method |
|---|---|---|
| `POST /v3/events` | Port-visit events filtered by geometry, date range, vessel, flag, min duration | POST |
| `GET /v3/vessels/search` | Search vessels by name, MMSI, IMO, flag, type | GET |
| `GET /v3/vessels/{id}` | Get vessel details (type, tonnage, flag, etc.) | GET |

- **Port-visit event dataset:** `public-global-port-visits-events:latest`

- **Port / Anchorage reference file:** GFW provides a CSV mapping of ports and anchorages with S2 cell geometry. Bundled locally at `Base Data/named_anchorages_v2_pipe_v3_202601.csv`.

  **Data structure (166,497 rows × 10 columns):**

  | Column | Description |
  |---|---|
  | `s2id` | S2 cell identifier (hex) — each row is one cell |
  | `lat`, `lon` | Centre-point of the S2 cell |
  | `label` | Top-level port name (14,627 unique, e.g. "LONDON", "SINGAPORE") |
  | `sublabel` | Sub-location within the port (39,876 unique, e.g. "CANARY WHARF", "TILBURY") |
  | `label_source` | How the label was assigned (`top_destination`, `WPI_ports`, `anchorage_overrides`, `geonames_1000`, `tmt`, `indonesia`, `china_s2id_override`, `peru`) |
  | `iso3` | ISO-3 country code (209 countries) |
  | `distance_from_shore_m` | Distance from shore in metres |
  | `drift_radius` | Drift radius of the anchorage area |
  | `dock` | `true` = berth/dock, `false` = anchorage, blank = unclassified |

  **Grouping strategy:**
  - **Port level** (`label`): centroid of all cells, total cell count, sub-location count — used for map markers and sidebar search.
  - **Sub-location level** (`sublabel`): centroid, cell count, dock/anchorage flag — used for detailed selection.
  - **Cell level** (raw): individual lat/lon points plotted on the map to show the physical size and shape of each anchorage/port area.

#### Key request example — port visits in a polygon
```bash
curl -X POST 'https://gateway.api.globalfishingwatch.org/v3/events?offset=0&limit=50' \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{
    "datasets": ["public-global-port-visits-events:latest"],
    "startDate": "2024-01-01",
    "endDate": "2024-12-31",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[lng1,lat1],[lng2,lat2],...]]
    }
  }'
```

### 2.2 Copernicus Marine Service — Ocean current data
- **Product:** `GLOBAL_ANALYSISFORECAST_PHY_001_024`
- **Dataset (daily currents):** `cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m`
- **Dataset (hourly, for intra-day analysis):** `cmems_mod_glo_phy_anfc_0.083deg_PT1H-m`
- **Variables of interest:**
  - `uo` — eastward sea water velocity (m/s)
  - `vo` — northward sea water velocity (m/s)
  - Derived: current speed = √(uo² + vo²), direction = atan2(vo, uo)
- **Access method:** `copernicusmarine` Python toolbox (`subset` function)
- **Auth:** Free Copernicus Marine account (username/password stored in `~/.copernicusmarine/.copernicusmarine-credentials` or env vars)

#### Key request example — subset currents for a location
```python
import copernicusmarine

copernicusmarine.subset(
    dataset_id="cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
    variables=["uo", "vo"],
    minimum_longitude=103.5,
    maximum_longitude=104.2,
    minimum_latitude=1.0,
    maximum_latitude=1.5,
    start_datetime="2025-01-01",
    end_datetime="2025-01-31",
    minimum_depth=0,
    maximum_depth=5,
    output_filename="currents_singapore.nc"
)
```

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit Frontend                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Map View    │  │ Filters &    │  │  Analytics     │  │
│  │  (deck.gl /  │  │ Controls     │  │  Dashboard     │  │
│  │  pydeck /    │  │ (port select,│  │  (plots,       │  │
│  │  folium)     │  │  date range) │  │   tables, KPIs)│  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                 │                  │           │
│  ───────┴─────────────────┴──────────────────┴────────── │
│                   Streamlit session state                 │
└──────────────────────────┬──────────────────────────────┘
                           │
              ┌────────────┴─────────────┐
              │     Python Backend       │
              │  ┌────────────────────┐  │
              │  │  gfw_client.py     │  │  ← GFW API wrapper
              │  │  copernicus.py     │  │  ← Copernicus data access
              │  │  analytics.py     │  │  ← Statistics & scoring
              │  │  cache.py         │  │  ← Disk/Redis caching
              │  └────────────────────┘  │
              └──────┬──────────┬────────┘
                     │          │
          ┌──────────┘          └──────────┐
          ▼                               ▼
 ┌─────────────────┐           ┌────────────────────┐
 │  GFW V3 API     │           │  Copernicus Marine  │
 │  (REST / JSON)  │           │  Toolbox (NetCDF)   │
 └─────────────────┘           └────────────────────┘
```

### Why Streamlit?
- Fast to build; pure Python — no JS/HTML boilerplate.
- `pydeck` / `streamlit-folium` provide rich interactive maps.
- Built-in caching (`@st.cache_data`) keeps API responses snappy on repeat queries.
- Easy deployment via Streamlit Community Cloud, Docker, or any cloud VM.
- **Risk:** very large date ranges or many ports could be slow → mitigate with caching, pagination, and pre-aggregation.

---

## 4. Feature Breakdown & Milestones

### Phase 1 — Core Data Layer & Proof of Concept (Week 1–2)

| # | Task | Details |
|---|---|---|
| 1.1 | **Project scaffolding** | Set up repo, `pyproject.toml` / `requirements.txt`, folder structure, `.env` for secrets. |
| 1.2 | **GFW API client** | Python module wrapping port-visit event queries: auth, pagination (offset/limit), error handling, rate-limit back-off. |
| 1.3 | **Port & anchorage reference data** | Parse GFW port/anchorage mapping file. Build a lookup: port name/ID → polygon geometry, lat/lon centroid, country. |
| 1.4 | **Copernicus client** | Python module using `copernicusmarine.subset()` (or `open_dataset()` for lazy-loading) to fetch `uo`, `vo` for a bounding box + time range. Compute speed & direction from NetCDF/xarray. |
| 1.5 | **Basic Streamlit page** | Map showing all ports/anchorages as dots. Click a dot → display name, country. Date-range picker. |

### Phase 2 — Analytics Engine (Week 3–4)

| # | Task | Details |
|---|---|---|
| 2.1 | **Port-visit analytics** | For a selected port+date range: total visits, unique vessels, visits by vessel type (container, tanker, bulk), visits by flag, weekly/monthly time series. |
| 2.2 | **Duration analytics** | Histogram & box-plot of port-call durations (hours). Median, P25, P75, P90. Separate anchorage vs. berth durations if data allows. |
| 2.3 | **Ocean current analytics** | For selected anchorage area: current speed histogram, time-of-day heatmap, direction rose plot, peak speed, mean speed, std-dev, percentiles. Flag periods above operational threshold (e.g., >1.5 kn). |
| 2.4 | **Distance calculation** | If anchorage, compute distance from associated port (haversine on centroids). Display on map with a line. |
| 2.5 | **Caching layer** | Cache GFW responses and Copernicus NetCDF subsets to disk (`diskcache` or `st.cache_data` with TTL). Avoid redundant API calls. |

### Phase 3 — Scoring & Business-Case View (Week 5)

| # | Task | Details |
|---|---|---|
| 3.1 | **Site-suitability score** | Composite score per port/anchorage combining: market size (visit count), dwell time (median duration ≥ threshold), current feasibility (% time currents < operational limit). Weights configurable in sidebar. |
| 3.2 | **Comparison view** | Select 2–5 ports side-by-side; display radar/spider chart of normalised scores. |
| 3.3 | **Export** | Download results as CSV/Excel; export charts as PNG; generate a one-page PDF summary per port. |

### Phase 4 — Polish & Deployment (Week 6)

| # | Task | Details |
|---|---|---|
| 4.1 | **UI/UX refinement** | Responsive layout, loading spinners, error toasts, help tooltips, colour-coded map markers by score. |
| 4.2 | **Deployment** | Dockerise (`Dockerfile` + `docker-compose.yml`). Deploy to Streamlit Community Cloud *or* a cloud VM (AWS/GCP/Azure). Set secrets via env vars / Streamlit secrets. |
| 4.3 | **User guide & README** | Usage instructions, screenshots, architecture diagram, API key setup, Copernicus account setup. |
| 4.4 | **Testing** | Unit tests for analytics functions; integration tests against GFW & Copernicus with mocked responses. |

---

## 5. Proposed Project Structure

```
C-LeanWorld/
├── app.py                     # Streamlit entry point
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example               # Template for secrets
├── .streamlit/
│   └── config.toml            # Streamlit theme & settings
│   └── secrets.toml           # (gitignored) API keys at runtime
├── data/
│   └── ports_anchorages.csv   # GFW port/anchorage reference file
├── src/
│   ├── __init__.py
│   ├── gfw_client.py          # GFW API wrapper (auth, events, vessels)
│   ├── copernicus_client.py   # Copernicus Marine data access
│   ├── analytics.py           # Statistics, scoring, aggregations
│   ├── models.py              # Pydantic models for API responses
│   ├── cache.py               # Caching helpers
│   └── utils.py               # Haversine, coordinate helpers, etc.
├── components/
│   ├── map_view.py            # Map rendering (pydeck / folium)
│   ├── sidebar.py             # Filters, port selector, date picker
│   ├── visit_dashboard.py     # Port-visit charts & tables
│   ├── current_dashboard.py   # Ocean-current charts
│   └── comparison.py          # Side-by-side port comparison
├── tests/
│   ├── test_gfw_client.py
│   ├── test_copernicus.py
│   ├── test_analytics.py
│   └── conftest.py
└── DEVELOPMENT_PLAN.md        # ← this file
```

---

## 6. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| **Language** | Python 3.11+ | Native support for all data/API libs |
| **Web framework** | Streamlit ≥ 1.32 | Rapid prototyping, built-in caching, easy deploy |
| **Map** | `pydeck` (deck.gl) or `streamlit-folium` | Interactive, supports GeoJSON polygons, tooltips, colour scales |
| **Data wrangling** | `pandas`, `xarray`, `numpy` | Industry-standard for tabular + NetCDF data |
| **Plotting** | `plotly` (via `st.plotly_chart`) | Interactive histograms, box plots, rose diagrams, heatmaps |
| **HTTP** | `httpx` (async-capable) or `requests` | GFW API calls |
| **Ocean data** | `copernicusmarine` Python toolbox | Official Copernicus access; handles auth, subsetting, lazy-load via OPeNDAP |
| **Caching** | `st.cache_data` + `diskcache` | Avoid redundant API calls; TTL-based freshness |
| **Validation** | `pydantic` | Type-safe API response parsing |
| **Testing** | `pytest` + `responses` / `respx` | Mock HTTP for deterministic tests |
| **Containerisation** | Docker + docker-compose | Reproducible deployment |
| **CI/CD** | GitHub Actions | Lint, test, build & push Docker image |

---

## 7. Key API Integration Details

### 7.1 GFW Port-Visit Query Flow

```
User selects port → look up polygon from ports_anchorages.csv
       │
       ▼
POST /v3/events
  body: {
    datasets: ["public-global-port-visits-events:latest"],
    startDate, endDate,
    geometry: { type: "Polygon", coordinates: [...] }
  }
       │
       ▼
Paginate (offset += limit) until all events retrieved
       │
       ▼
Parse each event:
  - event.vessel  →  MMSI, name, type, flag, id
  - event.start   →  arrival timestamp
  - event.end     →  departure timestamp
  - duration_h    =  (end - start) in hours
  - event.port    →  port name / anchorage flag
       │
       ▼
Aggregate into analytics DataFrames
```

### 7.2 Copernicus Current Query Flow

```
User selects anchorage → compute bounding box (±0.1° around centroid)
       │
       ▼
copernicusmarine.open_dataset(
  dataset_id = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
  variables  = ["uo","vo"],
  minimum_longitude, maximum_longitude,
  minimum_latitude, maximum_latitude,
  start_datetime, end_datetime,
  minimum_depth=0, maximum_depth=5
)
       │
       ▼
xarray.Dataset with dims (time, depth, lat, lon)
       │
       ▼
Compute:
  speed = sqrt(uo² + vo²)       →  m/s → knots (* 1.94384)
  direction = arctan2(vo, uo)    →  degrees (oceanographic convention)
       │
       ▼
Statistics:
  - Histogram of speed (bins 0–3 kn)
  - Mean, median, P90, P99, max
  - Std-dev and variance
  - Time-of-day breakdown (hourly mean speed)
  - Wind-rose style direction plot
  - % time above operational threshold
```

### 7.3 Data Freshness & Limits

| Source | Temporal coverage | Update frequency | Rate limits |
|---|---|---|---|
| GFW port visits | 2012 – present (≈3-month lag) | ~Monthly | 100 req/min (token-based) |
| Copernicus hourly currents | Jun 2022 – present +10-day forecast | Daily | No hard quota; large subsets can be slow |

---

## 8. UI Wireframe (Conceptual)

```
┌────────────────────────────────────────────────────────────────┐
│  C-LeanWorld  🧹🚢        [Date range: ______ to ______]      │
├──────────────┬─────────────────────────────────────────────────┤
│  SIDEBAR     │                   MAP                          │
│              │                                                │
│ 🔍 Search    │     ● Port A (green = high score)              │
│  port name   │          ○ Anchorage A1                        │
│              │     ● Port B (yellow = medium)                 │
│ Vessel type  │          ○ Anchorage B1                        │
│ ☑ Container  │          ○ Anchorage B2                        │
│ ☑ Tanker     │     ● Port C (red = low score)                 │
│ ☐ Bulk       │                                                │
│              │   [click anchorage to select]                   │
│ Min stay (h) │                                                │
│ [___24___]   │                                                │
│              ├────────────────────────────────────────────────┤
│ Score weights│           ANALYTICS PANEL                       │
│ Market  [##] │  ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│ Dwell   [##] │  │ Visits/  │ │ Duration │ │ Current      │   │
│ Current [##] │  │ month    │ │ distrib. │ │ speed dist.  │   │
│              │  │ (bar)    │ │ (box)    │ │ (histogram)  │   │
│ [Compare]    │  └──────────┘ └──────────┘ └──────────────┘   │
│ [Export CSV] │  ┌──────────┐ ┌─────────────────────────────┐  │
│              │  │ Vessel   │ │ Current direction rose      │  │
│              │  │ type pie │ │                             │  │
│              │  └──────────┘ └─────────────────────────────┘  │
└──────────────┴────────────────────────────────────────────────┘
```

---

## 9. Deployment Options

### Option A — Streamlit Community Cloud (simplest)
1. Push repo to GitHub (public or with access grant).
2. Connect to [share.streamlit.io](https://share.streamlit.io).
3. Add secrets (GFW token, Copernicus credentials) in Streamlit Cloud UI.
4. App is live with a public URL.
- **Pros:** Free, zero-ops, HTTPS out of the box.
- **Cons:** Limited compute (1 GB RAM), cold starts, public unless on Teams plan.

### Option B — Docker on a Cloud VM
```bash
docker build -t cleanworld .
docker run -p 8501:8501 --env-file .env cleanworld
```
- Use a $5–10/month VM (Hetzner, DigitalOcean, AWS Lightsail).
- Put behind Caddy or nginx for HTTPS + custom domain.
- **Pros:** Full control, more RAM/CPU, persistent disk cache.
- **Cons:** More ops overhead.

### Option C — Cloud Run / App Runner (serverless container)
- Build image, push to registry, deploy to GCP Cloud Run or AWS App Runner.
- **Pros:** Scales to zero, pay-per-use, managed HTTPS.
- **Cons:** Cold starts; stateless (need external cache or DB for persistence).

---

## 10. Security & Secrets Management

| Secret | Storage |
|---|---|
| GFW Bearer token | `.env` / `st.secrets["GFW_TOKEN"]` — **never commit** |
| Copernicus username & password | `~/.copernicusmarine/` config or env vars `COPERNICUSMARINE_SERVICE_USERNAME` / `COPERNICUSMARINE_SERVICE_PASSWORD` |

- `.env` and `.streamlit/secrets.toml` are in `.gitignore`.
- In CI/CD, inject via GitHub Secrets → Docker build args or runtime env.

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| GFW API rate limits or downtime | No fresh data | Aggressive caching (24h TTL); fallback to cached data with staleness banner |
| Copernicus large subset requests are slow | Poor UX on first load | Pre-fetch and cache popular anchorages; use `open_dataset` for lazy OPeNDAP streaming; limit default date range to 30 days |
| GFW port-visit data has 3-month lag | Cannot show very recent visits | Clearly display data-freshness timestamp in UI |
| Current speed data resolution (0.083° ≈ 9 km) may not capture local harbour effects | Over/under-estimate feasibility | Note resolution limits in UI; consider higher-res regional Copernicus products for key regions |
| Streamlit performance with many concurrent users | Slow/crash | Move to Docker with more RAM; add Redis caching layer; consider FastAPI + React if >10 concurrent users |

---

## 12. Future Enhancements (Post-MVP)

- **Vessel AIS tracks:** Show individual vessel trajectories entering/leaving port.
- **Wave height overlay:** Add significant wave height from Copernicus wave product.
- **Tidal windows:** Integrate tidal data to identify optimal deployment windows.
- **Cost model:** Input robot deployment cost, cleaning price per m² hull → estimated revenue per port/year.
- **Alerts / monitoring:** Email or Slack notification when a new high-value vessel arrives at a watched port.
- **Multi-user auth:** Login system with user roles (viewer, analyst, admin).
- **Database backend:** PostgreSQL + PostGIS for persistent storage of historical queries and results.
- **Higher-res current models:** Use regional Copernicus products (IBI, Mediterranean, etc.) where available.

---

## 13. Getting Started (Quick-start for developers)

```bash
# 1. Clone & enter project
git clone <repo-url> && cd C-LeanWorld

# 2. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up secrets
cp .env.example .env
# Edit .env with your GFW token and Copernicus credentials

# 5. (First time) Configure Copernicus Marine credentials
copernicusmarine login  # interactive prompt for username/password

# 6. Place the GFW ports/anchorages file
# Copy the CSV/GeoJSON into data/ports_anchorages.csv

# 7. Run the app
streamlit run app.py

# 8. Open http://localhost:8501 in your browser
```

---

## 14. Estimated Timeline

| Week | Focus | Deliverable |
|---|---|---|
| 1 | Scaffolding + GFW client + port reference data | Port dots on map, click to see name |
| 2 | Copernicus client + basic current data | Current data fetching works end-to-end |
| 3 | Port-visit analytics + duration charts | Visit dashboard with histograms |
| 4 | Current analytics + direction rose | Current dashboard complete |
| 5 | Scoring model + comparison view + export | Business-case comparison feature |
| 6 | UI polish + Docker + deploy + docs | Live app accessible to stakeholders |

---

*Document created: 12 March 2026*
*Project: C-LeanWorld — Cleaning Robot Deployment Planner*
