import React from "react";
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
 * The panel fills the frame it is placed in (header top, buttons bottom,
 * rows filling between), so it matches a neighbouring SCADA panel of the
 * same size with no scrollbar.
 *
 * Values are hard-coded to the reference screenshot for now; they are
 * kept in a single data array so wiring them to the backend later is a
 * one-line change.
 * ------------------------------------------------------------------ */

const ROWS = [
  { label: "GHI Avg", value: "-0.167", unit: "W/m²" },
  { label: "T° Enviromental Avg", value: "24.600", unit: "°C" },
  { label: "POA Avg", value: "0.000", unit: "W/m²" },
  { label: "T° Module Avg", value: "24.713", unit: "°C" },
  { label: "Daily PR", value: "85.35", unit: "%" },
  { label: "Plant Availability", value: "0.00", unit: "%" },
  { label: "Max. Generation Capacity", value: "0.00", unit: "%" },
];

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
          {ROWS.map((r) => (
            <div className="kpi-row" key={r.label}>
              <span className="kpi-row__label">{r.label}</span>
              <span className="kpi-row__value">
                {r.value} {r.unit}
              </span>
            </div>
          ))}
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
