# Climate Hazard Trend Analyzer

A web app that pulls historical daily temperatures for a region, detects heatwaves using a
percentile-based definition, and shows how their frequency and duration have changed over time.

- **Backend:** Python 3.11, Django 5.1 + Django REST Framework, MySQL (MariaDB 11.8 tested)
- **Frontend:** React 18 (Vite), Chart.js via react-chartjs-2
- **Data source:** [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) (ERA5 reanalysis, no API key)

---

## 1. Setup

### Prerequisites
- Python 3.11+, Node.js 18+
- A running MySQL 8 / MariaDB server

### Database
Create an empty database (the name is up to you; the default in `.env.example` is `climate_hazards`):

```sql
CREATE DATABASE climate_hazards CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # then edit DB_* values and DJANGO_SECRET_KEY
python manage.py migrate
python manage.py test hazards   # 21 tests, no network needed
python manage.py runserver 8000
```

Environment variables (see `backend/.env.example`):

| Variable | Purpose |
|---|---|
| `DB_CONNECTION` | must be `mysql` |
| `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | MySQL connection |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | Django settings |
| `CORS_ALLOWED_ORIGINS` | frontend origins allowed to call the API directly |
| `RATE_LIMIT_ANON`, `RATE_LIMIT_ANALYSIS` | DRF rate limits (default `60/hour`, `20/hour`) |
| `API_CACHE_SECONDS` | in-process cache lifetime for geocoding (default 3600) |

### Frontend
```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```
The Vite dev server proxies `/api` to `http://127.0.0.1:8000`. If the backend runs elsewhere,
set `VITE_API_PROXY=http://host:port` before `npm run dev`.

### Production build
```bash
cd frontend && npm run build    # static files in frontend/dist
```
Serve `frontend/dist` from any static host and point the API at the Django server (set
`CORS_ALLOWED_ORIGINS` to the static host's origin, or serve both behind one domain).

---

## 2. Architecture

```
React (Vite)  ──/api──▶  Django REST API ──▶ Open-Meteo archive (only uncached days)
  RegionPicker              │
  App (form, KPIs)          ├─ ingest.py   clean + bulk upsert daily rows → MySQL
  Chart.js charts           ├─ heatwave.py calendar-day percentile thresholds, run detection
                            ├─ trends.py   OLS, Mann–Kendall, Sen's slope, rolling mean
                            └─ analysis.py orchestration; stores result document → MySQL
```

### Data model (MySQL)
| Table | Role |
|---|---|
| `hazards_dailyweather` | Cleaned daily Tmax/Tmin/precip per grid location. Unique on `(location_key, date)`. This is the raw-data cache. |
| `hazards_fetchedrange` | Which date ranges were already pulled from Open-Meteo, so repeat requests skip the network. |
| `hazards_analysisresult` | JSON document per analysis, keyed by a SHA-256 of the parameters. Returned directly for 24 h. |

### Endpoints
| Method | Path | Notes |
|---|---|---|
| GET | `/api/health/` | liveness check |
| GET | `/api/geocode/?q=Muscat` | city search (Open-Meteo geocoding), cached 24 h |
| GET | `/api/analysis/?lat&lon[&box_size]&hazard&start_year&end_year&percentile&min_days&baseline_start&baseline_end&label` | hazard analysis JSON; rate-limited. `box_size` (degrees, half-width) turns the point into a 3×3 grid box |
| GET | `/api/analysis/export/?…same params` | yearly results as a CSV download |

Example response fields: `events_total`, `percent_change_first_last_5yr`, `trend` (OLS and
Mann–Kendall), `plot` (arrays ready for charting), `yearly`, `data_quality`, `insights`
(plain-language summary strings), `cached`.

---

## 3. Data source

- **Open-Meteo archive** (`archive-api.open-meteo.com/v1/archive`): daily `temperature_2m_max`,
  `temperature_2m_min`, `precipitation_sum`, in °C and mm, timezone GMT.
- Chosen because it needs no key, covers 1940 to a few days ago, and returns one request per
  location for the whole period.
- Open-Meteo serves ERA5 reanalysis (about 0.25° grid). Values are model-based, not station
  observations, so local extremes can be smoothed. This is noted in the insights text.

---

## 4. Hazard logic

Three hazards share one event detector (`hazards/detection.py`): a run of consecutive flagged days of at least `min_days` is one event.

| Hazard | Flag per day | Default min days | Intensity |
|---|---|---|---|
| `heatwave` | Tmax above the calendar-day 95th percentile (baseline) | 3 | mean °C above threshold |
| `heavy_rain` | precipitation above the 95th percentile of baseline wet days (≥ 1 mm) | 1 | mean mm above threshold |
| `drought` | precipitation below 1 mm (dry day) | 10 | not defined (duration is the metric) |

### Heatwave details

1. **Threshold (climate definition).** For each calendar day (1–366), the 95th percentile of
   daily Tmax is computed from the baseline period (default **1981–2010**), pooling every baseline
   year's values within **±7 days** of that calendar day. Using a calendar-day threshold avoids
   flagging all of June as a heatwave because June is warmer than March.
2. **Hot day.** `Tmax > threshold` for that calendar day.
3. **Heatwave.** A run of **at least 3 consecutive hot days** (configurable 2–10).
4. **Event attribution.** An event belongs to the year it starts in.
5. **Metrics per year.** Event count, event days, longest event, mean intensity (mean °C above
   threshold across event days), and data coverage.

## 5. Cleaning and data handling

- Temperatures outside **[-60, 60] °C** are set to NULL (treated as API or sensor error).
- Negative precipitation is set to NULL.
- Missing days are **never filled**. A missing day counts as not hot, so it breaks a run and
  cannot join two hot spells into one.
- Any year with **under 80% daily coverage** is shown in the data-quality panel and excluded
  from counts and trend statistics.
- Gaps are not interpolated, so the reported numbers are never inflated by filled data.

## 6. Trend analysis

- **Percent change:** mean event count of the first 5 valid years vs the last 5 valid years of the
  selected range. This is what the headline "Heatwaves have increased by X% since <start>" uses.
- **Linear regression (OLS):** slope in events per year, reported per decade, plus R².
- **Mann–Kendall test:** two-sided, normal approximation, with tie correction. Reports τ, p-value,
  and a trend label at the 5% level.
- **Sen's slope:** median pairwise slope, reported per decade as a robust magnitude.
- **5-year rolling mean** of event counts, plotted alongside the bars.

## 7. Caching, rate limiting, input safety

- **Raw data:** each date range is fetched once per location. Later requests only fetch what is
  missing (the `FetchedRange` check).
- **Results:** analysis documents are stored in MySQL and reused for 24 h. Response has
  `Cache-Control: public, max-age=3600`.
- **Geocoding:** cached in process for 24 h.
- **Rate limits:** anonymous default `60/hour`; analysis endpoint `20/hour` (both configurable).
- **Input validation (DRF serializer):** latitude/longitude ranges, years not in the future,
  start before end, span ≤ 80 years, baseline ≥ 10 years, percentile 50–99.9, min days 2–10,
  hazard from an allow-list.
- **External API failures** become HTTP 502 with a message, not a stack trace.
- **Queries** use the ORM only. No user input is interpolated into SQL.

---

## 8. Assumptions

1. **Database:** the case brief suggests a NoSQL store. This build uses **MySQL** as requested. The
   `AnalysisResult` table is a JSON document store inside MySQL, so the read path matches the
   brief's "read from the database and return JSON" requirement.
2. **Lat-lon box:** the box is sampled on a 3×3 grid of points. The daily series is the mean across the
   nine points, so the analysis describes the area rather than one spot. Box averaging smooths extremes,
   so the rainfall thresholds are lower than for a single point.
3. **Baseline:** the threshold baseline defaults to **1981–2010**, a common WMO normal. Baseline
   years are part of the analysed series, so their exceedance rate is close to but not exactly
   5% by construction. The insights text says so.
4. **Hazard scope:** only **Heatwave** is implemented. The hazard field is a list so droughts or
   heavy-rain events can be added as new modules.
5. **Recent data:** Open-Meteo archive lags real time by a few days. End dates are clamped to
   today minus 2 days, and the current partial year is excluded by the 80% coverage rule.
6. **Percent change sensitivity:** comparing 5-year averages of small counts is noisy. A few years
   can swing the percentage a lot. The regression and Mann–Kendall results are shown next to it
   for context.
7. **Leap years:** day-of-year is used for the calendar lookup, so Feb 29 shares a slot with Mar 1
   in the threshold calendar. The effect on the 95th percentile is negligible.

---

## 9. What I would improve with more time

- Add droughts (SPI or consecutive dry days) and heavy-rain hazards using the same pipeline.
- Use a finer grid for boxes, or a gridded ERA5 file, instead of 9 API points.
- Show event locations on the map (the map currently only picks the region).
- Server-side PDF export (the UI uses the browser's print-to-PDF).
- Scheduled refresh of recent data (e.g. nightly Celery task) instead of fetch-on-demand.
- Move the in-process cache and rate limits to Redis so they work across multiple workers.
- Bootstrap confidence intervals for the percent-change figure, so noisy comparisons show a range.
- Return server-rendered PNG trend plots from matplotlib for embedding in reports.
- Add frontend tests (Vitest + React Testing Library) and a CI pipeline.

---

## 10. AI tools used

- **Claude Code** (Claude, Anthropic) was used to scaffold the Django project, the hazard logic,
  the tests, and the React UI from a written specification. The author reviewed the generated
  code, ran the backend test suite and a live API call against Open-Meteo, and built the frontend.
- The heatwave definition, data-cleaning rules, and assumptions above were chosen by the author
  and should be checked against the code during review.

---

## 11. Deployment (free tier)

Stack: **Render** (free web service for the API, free static site for the frontend) and **TiDB Cloud
Serverless** (free MySQL-compatible database). Both are configured by `render.yaml` at the repo root.

### Step 1: Database (TiDB Cloud)
1. Create a free Serverless cluster at tidbcloud.com.
2. Open *Connect*, and note the **host**, **port** (4000), **user**, and **password**.
3. Create a database named `climate_hazards` (or use the default).

### Step 2: API on Render
1. In Render: **New > Blueprint**, select this GitHub repository.
2. When prompted for the `sync: false` values, enter the TiDB details:
   `DB_HOST`, `DB_PORT=4000`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`.
   Leave `CORS_ALLOWED_ORIGINS` and `VITE_API_BASE` blank for now (set them in steps 3 and 4).
3. Wait for the deploy. Check `https://<api>.onrender.com/api/health/` returns `{"status":"ok"}`.

### Step 3: Frontend on Render
1. Open the `climate-hazard-web` service and set **VITE_API_BASE** to the API URL from step 2
   (for example `https://climate-hazard-api.onrender.com`). Trigger a redeploy so the build uses it.
2. Note the frontend URL (for example `https://climate-hazard-web.onrender.com`).

### Step 4: Connect them
In the API service, set **CORS_ALLOWED_ORIGINS** to the frontend URL and let it redeploy.

### Notes for the free tier
- The free web service sleeps after about 15 minutes idle. The first request after that can take
  30–60 seconds. Open `/api/health/` once before a demo to wake it.
- The first analysis for a region fetches data from Open-Meteo and can take 30 seconds or more.
  Later requests for that region are served from MySQL.
- Secrets (`DJANGO_SECRET_KEY`, DB password) are set in the Render dashboard, not in the repo.
  `DJANGO_DEBUG` is `0` in production.
