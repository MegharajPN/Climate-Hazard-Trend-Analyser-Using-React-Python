import { useState } from "react";
import { ApiError, analysisQueryString, fetchAnalysis } from "./api.js";
import RegionPicker from "./components/RegionPicker.jsx";
import { EventCountChart, IntensityChart } from "./components/HazardCharts.jsx";

const CURRENT_YEAR = new Date().getFullYear();

const HAZARDS = [
  { value: "heatwave", label: "Heatwave", noun: "Heatwave events", minDays: 3, percentile: true },
  { value: "heavy_rain", label: "Heavy rainfall", noun: "Heavy-rain days", minDays: 1, percentile: true },
  { value: "drought", label: "Drought (dry spells)", noun: "Drought spells", minDays: 10, percentile: false },
];

const DEFAULTS = {
  region: { mode: "city", label: "", lat: "", lon: "", boxSize: 0.5 },
  hazard: "heatwave",
  startYear: 1990,
  endYear: 2020,
  percentile: 95,
  minDays: 3,
  baselineStart: 1981,
  baselineEnd: 2010,
};

function validate(form) {
  const errors = [];
  const { region } = form;
  if (typeof region.lat !== "number" || typeof region.lon !== "number") {
    errors.push("Choose a region: search a city, click the map, or enter coordinates.");
  }
  if (form.startYear >= form.endYear) errors.push("Start year must be before end year.");
  if (form.endYear > CURRENT_YEAR) errors.push(`End year cannot be after ${CURRENT_YEAR}.`);
  if (form.endYear - form.startYear + 1 > 80) errors.push("The time range is limited to 80 years.");
  if (form.baselineStart >= form.baselineEnd) errors.push("Baseline start must be before baseline end.");
  if (form.baselineEnd - form.baselineStart + 1 < 10) errors.push("The baseline must span at least 10 years.");
  return errors;
}

// Build the API parameters from the form. Boxes send lat/lon plus box_size.
function buildParams(form) {
  const { region } = form;
  const params = {
    lat: region.lat.toFixed(4),
    lon: region.lon.toFixed(4),
    hazard: form.hazard,
    start_year: form.startYear,
    end_year: form.endYear,
    label: region.label || `${region.lat.toFixed(2)}, ${region.lon.toFixed(2)}`,
  };
  if (region.mode === "box") params.box_size = region.boxSize ?? 0.5;
  const hazard = HAZARDS.find((h) => h.value === form.hazard);
  if (hazard.percentile) {
    params.percentile = form.percentile;
    params.baseline_start = form.baselineStart;
    params.baseline_end = form.baselineEnd;
  }
  params.min_days = form.minDays;
  return params;
}

export default function App() {
  const [form, setForm] = useState(DEFAULTS);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [status, setStatus] = useState("idle"); // idle | loading | done | error
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState([]);
  const [apiError, setApiError] = useState("");

  const update = (patch) => setForm((prev) => ({ ...prev, ...patch }));
  const hazardMeta = HAZARDS.find((h) => h.value === form.hazard);

  function changeHazard(value) {
    const meta = HAZARDS.find((h) => h.value === value);
    update({ hazard: value, minDays: meta.minDays });
  }

  async function runAnalysis(event) {
    event.preventDefault();
    const problems = validate(form);
    setErrors(problems);
    setApiError("");
    if (problems.length) return;

    setResult(null); // never leave the previous region's numbers on screen
    setStatus("loading");
    try {
      const data = await fetchAnalysis(buildParams(form));
      setResult(data);
      setStatus("done");
    } catch (err) {
      setApiError(err instanceof ApiError && err.status === 429
        ? "Too many analyses in a short time. Please wait and try again."
        : err.message);
      setStatus("error");
    }
  }

  const trend = result?.trend;
  const mk = trend?.mann_kendall;
  const quality = result?.data_quality;
  const pct = result?.percent_change_first_last_5yr;

  return (
    <div className="app">
      <header className="app-header">
        <h1>Climate Hazard Trend Analyzer</h1>
        <p className="muted">
          Historical daily weather (Open-Meteo ERA5 archive) → hazard detection → trend analysis.
        </p>
      </header>

      <form className="controls card" onSubmit={runAnalysis} noValidate>
        <RegionPicker region={form.region} onChange={(region) => update({ region })} />

        <div className="grid-2">
          <label>
            Hazard
            <select value={form.hazard} onChange={(e) => changeHazard(e.target.value)}>
              {HAZARDS.map((h) => (
                <option key={h.value} value={h.value}>{h.label}</option>
              ))}
            </select>
          </label>
          <div className="range">
            <label>
              From year
              <input type="number" min="1940" max={CURRENT_YEAR} value={form.startYear}
                onChange={(e) => update({ startYear: Number(e.target.value) })} />
            </label>
            <label>
              To year
              <input type="number" min="1941" max={CURRENT_YEAR} value={form.endYear}
                onChange={(e) => update({ endYear: Number(e.target.value) })} />
            </label>
          </div>
        </div>

        <button type="button" className="link" onClick={() => setShowAdvanced((s) => !s)}>
          {showAdvanced ? "Hide" : "Show"} hazard definition
        </button>

        {showAdvanced && (
          <div className="grid-2 advanced">
            {hazardMeta.percentile && (
              <label>
                Percentile threshold
                <input type="number" step="0.5" min="50" max="99.9" value={form.percentile}
                  onChange={(e) => update({ percentile: Number(e.target.value) })} />
              </label>
            )}
            <label>
              Minimum consecutive days
              <input type="number" min="1" max="30" value={form.minDays}
                onChange={(e) => update({ minDays: Number(e.target.value) })} />
            </label>
            {hazardMeta.percentile && (
              <>
                <label>
                  Baseline start year
                  <input type="number" min="1940" max={CURRENT_YEAR} value={form.baselineStart}
                    onChange={(e) => update({ baselineStart: Number(e.target.value) })} />
                </label>
                <label>
                  Baseline end year
                  <input type="number" min="1940" max={CURRENT_YEAR} value={form.baselineEnd}
                    onChange={(e) => update({ baselineEnd: Number(e.target.value) })} />
                </label>
              </>
            )}
          </div>
        )}

        {errors.length > 0 && (
          <ul className="error-list">
            {errors.map((msg) => <li key={msg}>{msg}</li>)}
          </ul>
        )}
        {apiError && <p className="error-list">{apiError}</p>}

        <button type="submit" className="primary" disabled={status === "loading"}>
          {status === "loading" ? "Analysing… (first run for a region can take 30 s or more)" : `Analyse ${hazardMeta.label.toLowerCase()}`}
        </button>
      </form>

      {result && (
        <section className="results">
          <div className="result-actions no-print">
            <a className="button-link" href={`/api/analysis/export/?${analysisQueryString(buildParams(form))}`}>
              Download CSV
            </a>
            <button type="button" className="button-link" onClick={() => window.print()}>
              Print / save as PDF
            </button>
          </div>

          <div className="kpis">
            <Kpi label={result.hazard_label + " events"} value={result.events_total} />
            <Kpi
              label="Change, first vs last 5 yrs"
              value={pct == null ? "n/a" : `${pct > 0 ? "+" : ""}${pct.toFixed(0)}%`}
            />
            <Kpi
              label="Trend per decade"
              value={trend?.slope_per_decade != null
                ? `${trend.slope_per_decade > 0 ? "+" : ""}${trend.slope_per_decade.toFixed(2)}`
                : "n/a"}
            />
            <Kpi
              label="Mann–Kendall p-value"
              value={mk?.p_value != null ? mk.p_value.toFixed(3) : "n/a"}
              hint={mk?.trend}
            />
          </div>

          <div className="card">
            <h2>Key insights · {result.location.label}</h2>
            <ul className="insights">
              {result.insights.map((line) => <li key={line}>{line}</li>)}
            </ul>
            <p className="muted small">
              Definition: {result.definition.description}
              {result.definition.window ? ` Threshold ${result.definition.window}.` : ""}
              {result.cached ? " Served from stored results." : ""}
            </p>
          </div>

          <div className="card">
            <h2>{result.hazard_label}: count per year</h2>
            <EventCountChart plot={result.plot} noun={hazardMeta.noun} />
          </div>

          <div className="card">
            <h2>Duration and intensity</h2>
            <IntensityChart plot={result.plot} units={result.units} />
          </div>

          <div className="card">
            <h2>Data quality</h2>
            <ul className="quality">
              <li>Daily coverage: {quality.coverage_pct}% ({quality.days_available.toLocaleString()} of {quality.days_expected.toLocaleString()} days)</li>
              <li>
                Years excluded (under {quality.min_coverage_pct}% coverage):{" "}
                {quality.years_excluded.length ? quality.years_excluded.join(", ") : "none"}
              </li>
              {result.hazard === "heatwave" && (
                <li>Calendar days with a valid threshold: {quality.valid_days} of 366</li>
              )}
              {result.hazard === "heavy_rain" && (
                <li>Heavy-rain threshold: {quality.value_mm != null ? `${quality.value_mm} mm/day` : "not available"}</li>
              )}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}

function Kpi({ label, value, hint }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  );
}
