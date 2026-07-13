import React, { useState } from "react";
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

/* ----------------------------- mock data ------------------------------ */
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const r3 = (v) => Math.round(v * 1000) / 1000;

const DAYS = 31;
const cloudyDays = [4, 11, 12, 19, 26];

const chartData = Array.from({ length: DAYS }, (_, i) => {
  const d = i + 1;
  const seasonal = 0.84 + 0.03 * Math.sin((i / DAYS) * Math.PI * 2);
  const noise = 0.02 * Math.sin(i * 1.7) + 0.015 * Math.cos(i * 0.9);
  const cloudy = cloudyDays.includes(d) ? -0.07 : 0;
  return {
    day: d,
    actual: r3(clamp(seasonal + noise + cloudy, 0.55, 0.95)),
    simulated: r3(clamp(0.858 + 0.02 * Math.sin(i * 0.8), 0.7, 0.95)),
    forecast: r3(clamp(0.85 + 0.025 * Math.cos(i * 0.7 + 1), 0.7, 0.95)),
  };
});

const tableRows = chartData.slice(0, 12).map((r) => ({
  timestamp: `2026-07-${String(r.day).padStart(2, "0")} 12:00:00`,
  actual: r.actual,
  simulated: r.simulated,
  forecast: r.forecast,
}));

const actuals = chartData.map((r) => r.actual);
const avg = r3(actuals.reduce((a, b) => a + b, 0) / actuals.length);
const peak = Math.max(...actuals);
const low = Math.min(...actuals);
const peakDay = chartData.find((r) => r.actual === peak).day;
const lowDay = chartData.find((r) => r.actual === low).day;

const SERIES = {
  actual: "#4ADE80",
  simulated: "#2DD4BF",
  forecast: "#38BDF8",
};

/* ------------------------------- icons -------------------------------- */
const Ico = ({ d, ...p }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
    strokeLinejoin="round" {...p}>
    {d}
  </svg>
);
const ChevIcon = (p) => <Ico {...p} width="14" height="14" d={<path d="m6 9 6 6 6-6" />} />;
const ColsIcon = (p) => <Ico {...p} d={<><rect x="3" y="3" width="18" height="18" rx="1.5" /><path d="M9 3v18M15 3v18" /></>} />;
const ExportIcon = (p) => <Ico {...p} d={<><path d="M12 3v12M8 11l4 4 4-4" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></>} />;

/* ---------------------------- sub components --------------------------- */
function Field({ label, value, onChange, options }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <span className="field__box">
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
        <ChevIcon className="field__chev" />
      </span>
    </label>
  );
}

function Stat({ label, value, unit, accent }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value" style={accent ? { color: SERIES.actual } : undefined}>
        {value}
        {unit && <span className="stat__unit">{unit}</span>}
      </div>
    </div>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="tt">
      <div className="tt__day">July {label}</div>
      {payload.map((p) => (
        <div className="tt__row" key={p.dataKey}>
          <span className="tt__dot" style={{ background: p.color }} />
          <span className="tt__key">{p.name}</span>
          <span className="tt__val">{Number(p.value).toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------- app ---------------------------------- */
export default function KPIDashboard() {
  const [plant, setPlant] = useState("Home");
  const [metric, setMetric] = useState("Performance Ratio");
  const [source, setSource] = useState("Actual");
  const [month, setMonth] = useState("July");
  const [year, setYear] = useState("2026");

  return (
    <div className="app">
      <style>{CSS}</style>

      <main className="wrap">
        {/* --------------------------- filters --------------------------- */}
        <section className="filters">
          <div className="filters__row">
            <Field label="Plant" value={plant} onChange={setPlant}
              options={["Home", "North Array", "Rooftop A", "Substation 2"]} />
            <Field label="KPI Metric" value={metric} onChange={setMetric}
              options={["Performance Ratio", "Specific Yield", "Availability", "Capacity Factor"]} />
            <Field label="Data Source" value={source} onChange={setSource}
              options={["Actual", "Simulated", "Forecast", "All"]} />
            <Field label="Month" value={month} onChange={setMonth}
              options={["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]} />
            <Field label="Year" value={year} onChange={setYear}
              options={["2024", "2025", "2026"]} />
          </div>
          <button className="apply">Apply</button>
        </section>

        {/* ---------------------------- title ---------------------------- */}
        <div className="title-row">
          <h1 className="title">KPI Dashboard</h1>
          <span className="live"><span className="live__dot" />Live</span>
        </div>

        {/* -------------------------- stat strip ------------------------- */}
        <section className="stats">
          <Stat label="Average PR" value={avg.toFixed(3)} accent />
          <Stat label="Peak PR" value={peak.toFixed(3)} unit={`· Jul ${peakDay}`} />
          <Stat label="Lowest PR" value={low.toFixed(3)} unit={`· Jul ${lowDay}`} />
          <Stat label="Reporting Days" value={DAYS} unit="/ 31" />
        </section>

        {/* ---------------------------- chart ---------------------------- */}
        <section className="card chart-card">
          <div className="card__head">
            <div>
              <div className="card__title">Performance Ratio</div>
              <div className="card__sub">{plant} · {month} {year}</div>
            </div>
            <div className="legend">
              {[["Actual", SERIES.actual], ["Simulated", SERIES.simulated], ["Forecast", SERIES.forecast]].map(
                ([name, color]) => (
                  <span className="legend__item" key={name}>
                    <span className="legend__dot" style={{ background: color }} />
                    {name}
                  </span>
                )
              )}
            </div>
          </div>

          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: -8, bottom: 4 }}>
                <defs>
                  <linearGradient id="fillActual" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={SERIES.actual} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={SERIES.actual} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#1B2624" strokeDasharray="3 6" vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: "#5F726D", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#1B2624" }}
                  interval={1}
                  label={{ value: "Days", position: "insideBottom", offset: -2, fill: "#5F726D", fontSize: 11 }}
                />
                <YAxis
                  domain={[0, 1]}
                  ticks={[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]}
                  tick={{ fill: "#5F726D", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#1B2624" }}
                  width={52}
                  label={{ value: "Performance Ratio ( % )", angle: -90, position: "insideLeft", fill: "#5F726D", fontSize: 11, dy: 60 }}
                />
                <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#2C3B37", strokeWidth: 1 }} />
                <Area
                  type="monotone" dataKey="actual" name="Actual"
                  stroke={SERIES.actual} strokeWidth={2.2}
                  fill="url(#fillActual)"
                  dot={false} activeDot={{ r: 4, fill: SERIES.actual, stroke: "#070A09", strokeWidth: 2 }}
                />
                <Line
                  type="monotone" dataKey="simulated" name="Simulated"
                  stroke={SERIES.simulated} strokeWidth={1.6}
                  strokeDasharray="5 5" dot={false} activeDot={{ r: 3 }}
                />
                <Line
                  type="monotone" dataKey="forecast" name="Forecast"
                  stroke={SERIES.forecast} strokeWidth={1.6}
                  strokeDasharray="2 5" dot={false} activeDot={{ r: 3 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>

        {/* ---------------------------- table ---------------------------- */}
        <section className="card table-card">
          <div className="toolbar">
            <button className="toolbar__btn"><ColsIcon />Columns</button>
            <button className="toolbar__btn"><ExportIcon />Export</button>
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Actual</th>
                  <th>Simulated</th>
                  <th>Forecast</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={row.timestamp}>
                    <td className="mono muted">{row.timestamp}</td>
                    <td className="mono" style={{ color: SERIES.actual }}>{row.actual.toFixed(3)}</td>
                    <td className="mono" style={{ color: SERIES.simulated }}>{row.simulated.toFixed(3)}</td>
                    <td className="mono" style={{ color: SERIES.forecast }}>{row.forecast.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-foot">
            {tableRows.length} of {DAYS} rows · showing daily snapshots at 12:00
          </div>
        </section>
      </main>
    </div>
  );
}

/* ------------------------------- styles ------------------------------- */
const CSS = `
.app{
  --bg:#070A09; --surface:#0D1312; --surface-2:#111917;
  --border:#1B2624; --border-strong:#26332F;
  --accent:#4ADE80; --accent-2:#22C55E;
  --text:#E7EFEC; --muted:#869992; --dim:#5F726D;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono","Roboto Mono",Menlo,Consolas,monospace;
  min-height:100vh; background:
    radial-gradient(1200px 480px at 20% -8%, rgba(74,222,128,.06), transparent 60%),
    var(--bg);
  color:var(--text); font-family:var(--sans);
  font-size:14px; -webkit-font-smoothing:antialiased;
}
.app *{box-sizing:border-box;}
.app button{font-family:inherit; cursor:pointer;}
.app select{font-family:inherit;}

/* layout */
.wrap{max-width:1360px; margin:0 auto; padding:32px 24px 64px;}

/* filters */
.filters{
  display:flex; align-items:flex-end; gap:14px; flex-wrap:wrap;
  padding-bottom:6px;
}
.filters__row{display:flex; gap:14px; flex-wrap:wrap; flex:1;}
.field{display:flex; flex-direction:column; gap:5px; min-width:150px; flex:1;}
.field__label{
  font-size:11px; color:var(--dim); font-weight:600;
  letter-spacing:.6px; text-transform:uppercase; padding-left:2px;
}
.field__box{position:relative; display:block;}
.field__box select{
  width:100%; appearance:none; -webkit-appearance:none;
  background:var(--surface); color:var(--text);
  border:1px solid var(--border); border-radius:10px;
  padding:11px 34px 11px 13px; font-size:14px; outline:none;
  transition:border-color .15s, box-shadow .15s;
}
.field__box select:hover{border-color:var(--border-strong);}
.field__box select:focus{border-color:var(--accent); box-shadow:0 0 0 3px rgba(74,222,128,.14);}
.field__chev{
  position:absolute; right:11px; top:50%; transform:translateY(-50%);
  color:var(--dim); pointer-events:none;
}

.apply{
  height:44px; padding:0 30px; border:none; border-radius:10px;
  font-size:13px; font-weight:700; letter-spacing:1px; text-transform:uppercase;
  color:#06120C; background:linear-gradient(135deg,#4ADE80,#22C55E);
  box-shadow:0 4px 18px rgba(34,197,94,.35); transition:transform .12s, box-shadow .15s;
}
.apply:hover{box-shadow:0 6px 26px rgba(34,197,94,.5); transform:translateY(-1px);}
.apply:active{transform:translateY(0);}

/* title */
.title-row{display:flex; align-items:center; gap:14px; margin:26px 0 16px;}
.title{font-size:22px; font-weight:700; letter-spacing:-.4px; margin:0;}
.live{
  display:inline-flex; align-items:center; gap:7px;
  font-size:11px; font-weight:600; letter-spacing:1px; text-transform:uppercase;
  color:var(--accent); padding:5px 11px; border-radius:999px;
  background:rgba(74,222,128,.08); border:1px solid rgba(74,222,128,.22);
}
.live__dot{
  width:7px; height:7px; border-radius:50%; background:var(--accent);
  box-shadow:0 0 8px var(--accent); animation:pulse 2s infinite;
}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}

/* stat strip */
.stats{
  display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:16px;
}
.stat{
  background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:15px 17px;
}
.stat__label{
  font-size:10.5px; color:var(--dim); font-weight:600;
  letter-spacing:.7px; text-transform:uppercase; margin-bottom:9px;
}
.stat__value{
  font-family:var(--mono); font-size:26px; font-weight:600; letter-spacing:-.5px;
  color:var(--text); line-height:1;
}
.stat__unit{font-size:12px; color:var(--dim); margin-left:8px; font-weight:500;}

/* cards */
.card{
  background:var(--surface); border:1px solid var(--border);
  border-radius:14px; margin-bottom:18px; overflow:hidden;
}
.card__head{
  display:flex; align-items:center; justify-content:space-between;
  padding:18px 20px 6px;
}
.card__title{font-size:15px; font-weight:650;}
.card__sub{font-size:12px; color:var(--muted); margin-top:3px;}
.legend{display:flex; gap:18px;}
.legend__item{
  display:inline-flex; align-items:center; gap:7px;
  font-size:12px; color:var(--muted);
}
.legend__dot{width:9px; height:9px; border-radius:3px;}

.chart{height:380px; padding:6px 14px 14px;}

/* tooltip */
.tt{
  background:rgba(9,14,13,.95); border:1px solid var(--border-strong);
  border-radius:10px; padding:10px 12px; box-shadow:0 8px 30px rgba(0,0,0,.5);
}
.tt__day{font-size:11px; color:var(--dim); font-weight:600; margin-bottom:7px; letter-spacing:.4px;}
.tt__row{display:flex; align-items:center; gap:9px; font-size:12px; padding:2px 0;}
.tt__dot{width:8px; height:8px; border-radius:3px;}
.tt__key{color:var(--muted); min-width:70px;}
.tt__val{font-family:var(--mono); color:var(--text); margin-left:auto; font-weight:600;}

/* table */
.toolbar{
  display:flex; gap:6px; padding:12px 14px;
  border-bottom:1px solid var(--border);
}
.toolbar__btn{
  display:inline-flex; align-items:center; gap:7px;
  background:transparent; border:1px solid transparent; border-radius:8px;
  color:var(--accent); font-size:12px; font-weight:600;
  letter-spacing:.4px; padding:7px 11px; transition:.15s;
}
.toolbar__btn:hover{background:rgba(74,222,128,.08); border-color:rgba(74,222,128,.2);}
.table-scroll{overflow-x:auto;}
.table{width:100%; border-collapse:collapse; min-width:640px;}
.table th{
  text-align:left; font-size:11px; letter-spacing:.6px; text-transform:uppercase;
  color:var(--dim); font-weight:600; padding:14px 20px;
  border-bottom:1px solid var(--border); background:var(--surface-2);
}
.table td{
  padding:13px 20px; font-size:13px;
  border-bottom:1px solid rgba(27,38,36,.6);
}
.table tbody tr{transition:background .12s;}
.table tbody tr:hover{background:var(--surface-2);}
.table tbody tr:last-child td{border-bottom:none;}
.mono{font-family:var(--mono); font-weight:500;}
.muted{color:var(--muted);}
.table-foot{
  padding:12px 20px; font-size:12px; color:var(--dim);
  border-top:1px solid var(--border); background:var(--surface-2);
}

/* responsive */
@media (max-width:900px){
  .stats{grid-template-columns:repeat(2,1fr);}
  .legend{display:none;}
  .filters{flex-direction:column; align-items:stretch;}
  .apply{width:100%;}
}
@media (max-width:560px){
  .stats{grid-template-columns:1fr 1fr;}
  .stat__value{font-size:22px;}
}
`;