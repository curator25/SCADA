import React from "react";

/* ------------------------------------------------------------------ *
 * KPI Panel  —  matches the UI / colour spec shared by the boss.
 *
 *   Frame ......... 536 (W) x 440 (H), fixed small panel
 *   Background .... #EEEEEE
 *   Row colour .... alternating #E7E7E7 (grey) / #FFFFFF (white)
 *   Text colour ... #4A4A49
 *   Row height .... 42 px
 *
 * The panel is a fixed 536x440 block centered in the window. Opening the
 * link shows only this panel and nothing else.
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
      <style>{CSS}</style>

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

/* ------------------------------- styles ------------------------------- */
const CSS = `
.kpi-stage{
  --bg:#EEEEEE;
  --row:#E7E7E7;
  --text:#4A4A49;
  min-height:100vh;
  background:#EEEEEE;
  display:flex;
  align-items:center;
  justify-content:center;
  font-family:"Segoe UI",Tahoma,Geneva,Verdana,Arial,sans-serif;
  color:var(--text);
}
.kpi-stage *{box-sizing:border-box;}

/* fixed small panel — 536 x 440, centered in the window */
.kpi-frame{
  width:536px;
  height:440px;
  background:var(--bg);
  border:1px solid #C4C4C4;
  display:flex;
  flex-direction:column;
  overflow:hidden;
}

/* header */
.kpi-header{
  position:relative;
  height:46px;
  flex:0 0 46px;
  display:flex;
  align-items:center;
  justify-content:center;
  background:#F5F5F5;
  border-bottom:1px solid #DADADA;
}
.kpi-header__icon{
  position:absolute;
  left:14px;
  display:inline-flex;
  color:var(--text);
}
.kpi-header__title{
  font-size:22px;
  font-weight:600;
  letter-spacing:.5px;
  color:var(--text);
}

/* rows — grow to fill the window, never shorter than 42px */
.kpi-rows{
  flex:1 1 auto;
  display:flex;
  flex-direction:column;
}
.kpi-row{
  flex:0 0 42px;
  height:42px;
  display:grid;
  grid-template-columns:1fr 1fr;
  align-items:center;
  padding:0 4px;
}
/* alternate row colours — 1st grey, 2nd white, and so on */
.kpi-row:nth-child(odd){background:#E7E7E7;}
.kpi-row:nth-child(even){background:#FFFFFF;}
.kpi-row__label{
  padding-left:14px;
  font-size:14px;
  color:var(--text);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.kpi-row__value{
  text-align:center;
  font-size:14px;
  color:var(--text);
}

/* action buttons */
.kpi-actions{
  flex:0 0 auto;
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:8px 10px;
  padding:10px 14px 14px;
}
.kpi-btn{
  height:30px;
  padding:0 10px;
  font-size:12.5px;
  color:var(--text);
  background:linear-gradient(180deg,#FCFCFC 0%,#E4E4E4 100%);
  border:1px solid #ADADAD;
  border-radius:2px;
  box-shadow:inset 0 1px 0 #FFFFFF;
  cursor:pointer;
  white-space:nowrap;
  transition:background .12s,border-color .12s;
}
.kpi-btn:hover{
  background:linear-gradient(180deg,#FFFFFF 0%,#DCDCDC 100%);
  border-color:#8C8C8C;
}
.kpi-btn:active{
  background:linear-gradient(180deg,#DCDCDC 0%,#E8E8E8 100%);
  box-shadow:inset 0 1px 2px rgba(0,0,0,.2);
}
/* Trackers button sits in column 1, row 1; column 1 / row 2 stays empty
   so the two Export buttons stack on the right — matching the mock */
.kpi-btn--wide{grid-column:1; grid-row:1;}
`;
