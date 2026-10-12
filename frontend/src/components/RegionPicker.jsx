import { useEffect, useState } from "react";
import { MapContainer, Marker, Rectangle, TileLayer, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { searchPlaces } from "../api.js";

const DEFAULT_CENTRE = [23.6, 58.5]; // Muscat, Oman, as a neutral starting view
const BOX_SIZES = [0.25, 0.5, 1, 2];

// Lets the user pick a region by city name, a lat/lon point, or a box (centre + half-width),
// and also by clicking on the map.
export default function RegionPicker({ region, onChange }) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [searchError, setSearchError] = useState("");

  const mode = region.mode;
  const hasPoint = typeof region.lat === "number" && typeof region.lon === "number";

  useEffect(() => {
    if (mode !== "city" || query.trim().length < 2) {
      setSuggestions([]);
      return undefined;
    }
    const timer = setTimeout(async () => {
      try {
        setSearchError("");
        setSuggestions(await searchPlaces(query.trim()));
      } catch (err) {
        setSuggestions([]);
        setSearchError(err.message);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [query, mode]);

  function pickPlace(place) {
    const parts = [place.name, place.admin1, place.country].filter(Boolean);
    onChange({ ...region, mode: "city", label: parts.join(", "), lat: place.latitude, lon: place.longitude });
    setSuggestions([]);
    setQuery(parts.join(", "));
  }

  function setField(field, value) {
    const number = parseFloat(value);
    onChange({ ...region, [field]: Number.isNaN(number) ? "" : number });
  }

  // Map clicks set the centre (point or box) and clear the typed city label.
  function setFromMap(lat, lon) {
    const label = `${lat.toFixed(2)}, ${lon.toFixed(2)}`;
    onChange({ ...region, mode: mode === "city" ? "point" : mode, lat: +lat.toFixed(4), lon: +lon.toFixed(4), label });
    setQuery("");
  }

  function switchMode(next) {
    setSuggestions([]);
    onChange({ ...region, mode: next, label: next === "city" ? region.label : "" });
  }

  const half = region.boxSize ?? 0.5;
  const bounds = hasPoint && mode === "box"
    ? [[region.lat - half, region.lon - half], [region.lat + half, region.lon + half]]
    : null;

  return (
    <fieldset className="region-picker">
      <legend>Region</legend>

      <div className="segmented" role="tablist">
        {[
          ["city", "City"],
          ["point", "Lat / lon"],
          ["box", "Lat-lon box"],
        ].map(([value, text]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            className={mode === value ? "active" : ""}
            onClick={() => switchMode(value)}
          >
            {text}
          </button>
        ))}
      </div>

      {mode === "city" && (
        <div className="city-search">
          <input
            type="search"
            placeholder="Type a city, e.g. Muscat"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="City search"
          />
          {searchError && <p className="field-error">{searchError}</p>}
          {suggestions.length > 0 && (
            <ul className="suggestions">
              {suggestions.map((place) => (
                <li key={`${place.latitude},${place.longitude},${place.name}`}>
                  <button type="button" onClick={() => pickPlace(place)}>
                    {[place.name, place.admin1, place.country].filter(Boolean).join(", ")}
                    <span className="muted">
                      {" "}({place.latitude.toFixed(2)}, {place.longitude.toFixed(2)})
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {(mode === "point" || mode === "box") && (
        <div className="coords">
          <label>
            Latitude
            <input type="number" step="0.01" min="-90" max="90" value={region.lat ?? ""}
              onChange={(e) => setField("lat", e.target.value)} />
          </label>
          <label>
            Longitude
            <input type="number" step="0.01" min="-180" max="180" value={region.lon ?? ""}
              onChange={(e) => setField("lon", e.target.value)} />
          </label>
          {mode === "box" && (
            <label className="span-2">
              Box half-width (degrees): the box is 3×3 grid points across ±{half}°
              <select value={half} onChange={(e) => onChange({ ...region, boxSize: Number(e.target.value) })}>
                {BOX_SIZES.map((size) => (
                  <option key={size} value={size}>±{size}° (about {Math.round(size * 222)} km)</option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      <div className="map-wrap" aria-label="Map: click to set the location">
        <MapContainer center={hasPoint ? [region.lat, region.lon] : DEFAULT_CENTRE} zoom={hasPoint ? 6 : 3}
          scrollWheelZoom={false} className="map">
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <ClickToSet onPick={setFromMap} />
          {hasPoint && mode !== "box" && <Marker position={[region.lat, region.lon]} />}
          {bounds && <Rectangle bounds={bounds} pathOptions={{ color: "#c2410c" }} />}
        </MapContainer>
        <p className="muted small">Click the map to set {mode === "box" ? "the box centre" : "the location"}.</p>
      </div>
    </fieldset>
  );
}

function ClickToSet({ onPick }) {
  useMapEvents({
    click(event) {
      onPick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}
