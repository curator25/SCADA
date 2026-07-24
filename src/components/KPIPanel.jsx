import React, { useEffect, useState } from "react";
import "./KPIPanel.css";

/* ------------------------------------------------------------------ *
 * KPI Panel  —  matches the UI / colour spec shared by the boss.
 *
 *   Frame ......... fills its container (SCADA frame / browser window)
 *   Background .... #EEEEEE
 *   Row colour .... alternating #F5F5F5 (grey) / #FFFFFF (white)
 *   Text colour ... #4A4A49
 *   Rows .......... grow evenly to fill the panel height
 *
 * Rows are driven entirely by the backend: whatever parameters are listed
 * in the backend's config.json come back from /api/kpi and are rendered
 * here. Adding a parameter there makes it appear here with no frontend
 * change.
 *
 * The URL is relative on purpose - Vite proxies /api in development and
 * nginx proxies it in Docker, so this code never needs to know the
 * backend's host or port.
 * ------------------------------------------------------------------ */

const API_URL = "/api/kpi";
const POLL_MS = 1000;               // Panel refreshes once per second

const ACTIONS = [
  "Trackers Availability Report",
  "Export 5Min PR Report",
  "Export Daily PR Report",
];

/* settings / tune icon shown at the top-left of the header */
function TuneIcon() {
  return (
    <svg
      className="kpi-tune"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    >
      <line x1="6" y1="3" x2="6" y2="21" />
      <line x1="12" y1="3" x2="12" y2="21" />
      <line x1="18" y1="3" x2="18" y2="21" />
      <circle cx="6" cy="9" r="2.1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="15" r="2.1" fill="currentColor" stroke="none" />
      <circle cx="18" cy="7" r="2.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export default function KPIPanel() {
  const [rows, setRows] = useState([]);       // Latest parameters from the backend
  const [online, setOnline] = useState(false); // Is the backend reachable?

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const response = await fetch(API_URL, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        if (cancelled) return;

        setRows(data.parameters ?? []);
        setOnline(true);
      } catch {
        // Keep the last known rows so the panel never goes blank - the values
        // are blanked to '--' below instead.
        if (!cancelled) setOnline(false);
      }
    }

    load();                                   // Fetch immediately on mount
    const timer = setInterval(load, POLL_MS); // ...then once a second

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="kpi-stage">
      <div className="kpi-frame">
        {/* -------------------------- header -------------------------- */}
        <header className="kpi-header">
          <span className="kpi-header__icon">
            <TuneIcon />
          </span>
          <span className="kpi-header__title">KPI</span>
        </header>

        {/* --------------------------- rows --------------------------- */}
        <div className="kpi-rows">
          {rows.length === 0 ? (
            <div className="kpi-empty">Connecting…</div>
          ) : (
            rows.map((row) => {
              // Show '--' when the backend is unreachable or the reading is bad,
              // so a dead sensor is never mistaken for a real 0.00
              const unavailable = !online || row.quality !== "good";
              return (
                <div className="kpi-row" key={row.name}>
                  <span className="kpi-row__label">{row.label}</span>
                  <span className="kpi-row__value">
                    {unavailable ? "--" : `${row.display} ${row.unit}`}
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* ------------------------- actions -------------------------- */}
        <div className="kpi-actions">
          <button className="kpi-btn kpi-btn--wide">{ACTIONS[0]}</button>
          <button className="kpi-btn">{ACTIONS[1]}</button>
          <button className="kpi-btn">{ACTIONS[2]}</button>
        </div>
      </div>
    </div>
  );
}
