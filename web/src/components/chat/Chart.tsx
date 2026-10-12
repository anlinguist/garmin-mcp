// Ported from CPM (sidepanel-ui/src/Chart.tsx). Pure renderer for a server-normalized ChartSpec:
// Rabbit's render_chart tool (agent.py, normalize_chart) already validated/coerced the
// model-authored spec, so this file only draws — no parsing, no trust decisions. Design follows the dataviz skill: one axis, fixed-order categorical palette
// (theme tokens), recessive grid, thin marks with rounded data-ends, legend for ≥2 series, hover
// tooltips, text in ink tokens never series color. Single-series charts use the CPM accent.

import { useEffect, useMemo, useRef, useState } from "react";
export type ChartType = "bar" | "stackedBar" | "line" | "area" | "donut" | "stat";
export type ChartUnit =
  | "none" | "count" | "seconds" | "minutes" | "hours" | "miles" | "km" | "yards" | "meters"
  | "bpm" | "watts" | "percent";
export interface ChartSeries { readonly name?: string; readonly data: readonly number[] }
export interface ChartSpec {
  readonly type: ChartType
  readonly title?: string
  readonly x?: readonly string[]
  readonly series: readonly ChartSeries[]
  readonly unit?: ChartUnit
}

/** Live reduced-motion preference — charts render fully, just without the entrance animation. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = (): void => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Counts a hero number up from 0 on mount (skipped when reduced-motion). */
function useCountUp(target: number, reduced: boolean, ms = 650): number {
  const [value, setValue] = useState(reduced ? target : 0);
  const ref = useRef(target);
  ref.current = target;
  useEffect(() => {
    if (reduced) {
      setValue(target);
      return;
    }
    let raf = 0;
    let start = 0;
    const tick = (t: number): void => {
      if (!start) start = t;
      const p = Math.min(1, (t - start) / ms);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(ref.current * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, reduced, ms]);
  return value;
}

const SERIES_VARS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-6",
  "--chart-7",
  "--chart-8",
] as const;

const num = (v: number, d = 1) => new Intl.NumberFormat("en", { maximumFractionDigits: d }).format(v);

function hms(totalSeconds: number): string {
  const t = Math.round(Math.abs(totalSeconds));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function fmt(v: number, unit: ChartUnit | undefined): string {
  switch (unit) {
    case "count": return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
    case "seconds": return hms(v);
    case "minutes": return `${num(v, 0)} min`;
    case "hours": return `${num(v, 1)} h`;
    case "miles": return `${num(v, 1)} mi`;
    case "km": return `${num(v, 1)} km`;
    case "yards": return `${num(v, 0)} yd`;
    case "meters": return `${num(v, 0)} m`;
    case "bpm": return `${num(v, 0)} bpm`;
    case "watts": return `${num(v, 0)} W`;
    case "percent": return `${num(v, 0)}%`;
    default: return num(v, 2);
  }
}

/** Axis ticks: compact, without repeating the unit on every tick. */
function fmtTick(v: number, unit: ChartUnit | undefined): string {
  if (unit === "seconds") return hms(v)
  if (unit === "percent") return `${num(v, 0)}%`
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

function niceMax(m: number): number {
  if (m <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(m)));
  const n = m / pow;
  // Finer steps than 1/2/5/10 so a 220 peak gets a 250 axis, not 500.
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((x) => n <= x) ?? 10;
  return step * pow;
}

function color(i: number, single: boolean): string {
  if (single) return "var(--accent)";
  return `var(${SERIES_VARS[Math.min(i, SERIES_VARS.length - 1)]})`;
}

function DataTable({ spec, labels }: { spec: ChartSpec; labels: string[] }) {
  const donut = spec.type === "donut" || spec.type === "stat";
  return (
    <div className="mt-2 max-h-56 overflow-auto rounded-md border border-(--border)">
      <table className="w-full text-left text-[0.6471rem] tabular-nums">
        <thead className="sticky top-0 bg-(--bg-surface) text-(--text-muted)">
          <tr>
            <th className="px-2 py-1 font-medium">{donut ? "Item" : ""}</th>
            {donut ? <th className="px-2 py-1 font-medium">Value</th>
              : spec.series.map((s, i) => <th key={i} className="px-2 py-1 font-medium">{s.name ?? spec.title ?? `Series ${i + 1}`}</th>)}
          </tr>
        </thead>
        <tbody>
          {donut
            ? spec.series.map((s, i) => (
              <tr key={i} className="border-t border-(--border)">
                <td className="px-2 py-1">{s.name ?? `Item ${i + 1}`}</td>
                <td className="px-2 py-1">{fmt(s.data[s.data.length - 1] ?? 0, spec.unit)}</td>
              </tr>))
            : labels.map((l, r) => (
              <tr key={r} className="border-t border-(--border)">
                <td className="px-2 py-1 text-(--text-muted)">{l}</td>
                {spec.series.map((s, i) => <td key={i} className="px-2 py-1">{fmt(s.data[r] ?? 0, spec.unit)}</td>)}
              </tr>))}
        </tbody>
      </table>
    </div>
  );
}

export function Chart({ spec }: { spec: ChartSpec }) {
  const [showData, setShowData] = useState(false);
  const single = spec.series.length === 1;
  const labels = useMemo(() => {
    const len = Math.max(...spec.series.map((s) => s.data.length));
    return spec.x && spec.x.length >= len
      ? spec.x.slice()
      : Array.from({ length: len }, (_, i) => String(i + 1));
  }, [spec]);

  if (spec.type === "stat") return <StatTiles spec={spec} />;

  return (
    <figure className="my-3 rounded-lg border border-(--border) bg-(--bg-surface) p-3">
      {spec.title && <figcaption className="mb-2 text-xs font-semibold">{spec.title}</figcaption>}
      {spec.type === "donut" ? (
        <Donut spec={spec} labels={labels} />
      ) : (
        <XYChart spec={spec} labels={labels} single={single} />
      )}
      {!single && spec.type !== "donut" && (
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
          {spec.series.map((s, i) => (
            <li key={i} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-[2px]"
                style={{ background: color(i, false) }}
              />
              <span className="font-mono text-[0.5882rem] text-(--text-muted)">{s.name ?? `Series ${i + 1}`}</span>
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={() => setShowData((v) => !v)}
        className="mt-2 text-[0.5882rem] font-medium uppercase tracking-widest text-(--text-muted) hover:text-(--text)">
        {showData ? "Hide data" : "Show data"}
      </button>
      {showData && <DataTable spec={spec} labels={labels} />}
    </figure>
  );
}

function StatTiles({ spec }: { spec: ChartSpec }) {
  const reduced = usePrefersReducedMotion();
  const tiles = spec.series.map((s, i) => ({
    label: s.name ?? spec.title ?? `Value ${i + 1}`,
    value: s.data[s.data.length - 1] ?? 0,
  }));
  return (
    <figure className="my-3">
      {spec.title && spec.series.length > 1 && (
        <figcaption className="mb-2 text-xs font-semibold">{spec.title}</figcaption>
      )}
      <div className={`grid gap-2 ${tiles.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
        {tiles.map((t, i) => (
          <div
            key={i}
            className={`rounded-lg border border-(--border) bg-(--bg-surface) px-3 py-2.5 ${reduced ? "" : "cpm-rise"}`}
            style={reduced ? undefined : { animationDelay: `${i * 0.08}s` }}
          >
            <div className="truncate font-mono text-[0.5882rem] uppercase tracking-wider text-(--text-muted)">
              {t.label}
            </div>
            <StatValue value={t.value} unit={spec.unit} reduced={reduced} />
          </div>
        ))}
      </div>
    </figure>
  );
}

function StatValue({ value, unit, reduced }: { value: number; unit: ChartUnit | undefined; reduced: boolean }) {
  const shown = useCountUp(value, reduced);
  return (
    <div className="mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">
      {fmt(shown, unit)}
    </div>
  );
}

function XYChart({
  spec,
  labels,
  single,
}: {
  spec: ChartSpec;
  labels: string[];
  single: boolean;
}) {
  const W = 320;
  const H = 180;
  const mL = 34;
  const mR = 10;
  const mT = 8;
  const mB = 20;
  const iW = W - mL - mR;
  const iH = H - mT - mB;
  const n = labels.length;
  const series = spec.series;
  const stacked = spec.type === "stackedBar";
  const isBar = spec.type === "bar" || spec.type === "stackedBar";

  const [active, setActive] = useState<number | null>(null);
  const reduced = usePrefersReducedMotion();
  const barAnim = (i: number) =>
    reduced ? {} : { className: "cpm-bar", style: { animationDelay: `${i * 0.05}s` } };

  const max = useMemo(() => {
    let m = 0;
    for (let i = 0; i < n; i++) {
      if (stacked) {
        m = Math.max(m, series.reduce((sum, s) => sum + Math.max(0, s.data[i] ?? 0), 0));
      } else {
        for (const s of series) m = Math.max(m, s.data[i] ?? 0);
      }
    }
    return niceMax(m);
  }, [series, n, stacked]);

  const y = (v: number) => mT + iH - (v / max) * iH;
  const bandW = iW / n;
  const ticks = [0, max / 2, max];

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={`${spec.type} chart${spec.title ? `: ${spec.title}` : ""}`}
        className="block"
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={mL} x2={W - mR} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" strokeWidth={1} />
            <text x={mL - 4} y={y(t) + 3} textAnchor="end" className="fill-(--text-muted) font-mono text-[0.5294rem]">
              {fmtTick(t, spec.unit)}
            </text>
          </g>
        ))}

        {isBar
          ? labels.map((_, i) => {
              const groupW = bandW * 0.7;
              const x0 = mL + i * bandW + (bandW - groupW) / 2;
              if (stacked) {
                let acc = 0;
                return (
                  <g key={i}>
                    {series.map((s, si) => {
                      const v = Math.max(0, s.data[i] ?? 0);
                      const yTop = y(acc + v);
                      const h = y(acc) - yTop;
                      acc += v;
                      return (
                        <rect
                          key={si}
                          x={x0}
                          y={yTop}
                          width={groupW}
                          height={Math.max(0, h - 1)}
                          rx={2}
                          className={barAnim(i).className}
                          style={{ fill: color(si, single), ...barAnim(i).style }}
                        />
                      );
                    })}
                  </g>
                );
              }
              const bw = groupW / series.length;
              return (
                <g key={i}>
                  {series.map((s, si) => {
                    const v = s.data[i] ?? 0;
                    return (
                      <rect
                        key={si}
                        x={x0 + si * bw}
                        y={y(v)}
                        width={Math.max(1, bw - 1.5)}
                        height={Math.max(0, y(0) - y(v))}
                        rx={2}
                        className={barAnim(i).className}
                        style={{ fill: color(si, single), ...barAnim(i).style }}
                      />
                    );
                  })}
                  {single && n <= 8 && (
                    <text
                      x={x0 + groupW / 2}
                      y={y(series[0]!.data[i] ?? 0) - 3}
                      textAnchor="middle"
                      className="fill-(--text-muted) font-mono text-[0.5294rem]"
                    >
                      {fmt(series[0]!.data[i] ?? 0, spec.unit)}
                    </text>
                  )}
                </g>
              );
            })
          : series.map((s, si) => {
              const cx = (i: number) => mL + bandW * i + bandW / 2;
              const pts = s.data.map((v, i) => `${cx(i)},${y(v)}`).join(" ");
              const areaPath =
                `M ${cx(0)},${y(0)} ` +
                s.data.map((v, i) => `L ${cx(i)},${y(v)}`).join(" ") +
                ` L ${cx(s.data.length - 1)},${y(0)} Z`;
              const drawDelay = si * 0.15;
              return (
                <g key={si}>
                  {spec.type === "area" && (
                    <path
                      d={areaPath}
                      className={reduced ? undefined : "cpm-fade"}
                      style={
                        reduced
                          ? { fill: color(si, single), opacity: 0.14 }
                          : ({
                              fill: color(si, single),
                              opacity: 0.14,
                              "--cpm-fade-to": 0.14,
                              animationDelay: `${drawDelay + 0.25}s`,
                            } as React.CSSProperties)
                      }
                    />
                  )}
                  <polyline
                    points={pts}
                    fill="none"
                    pathLength={reduced ? undefined : 1}
                    className={reduced ? undefined : "cpm-line"}
                    style={reduced ? { stroke: color(si, single) } : { stroke: color(si, single), animationDelay: `${drawDelay}s` }}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                  {(n <= 12 || active !== null) &&
                    s.data.map((v, i) => (
                      <circle
                        key={i}
                        cx={cx(i)}
                        cy={y(v)}
                        r={active === i ? 3.5 : 2.5}
                        className={reduced ? undefined : "cpm-fade"}
                        style={reduced ? { fill: color(si, single) } : { fill: color(si, single), animationDelay: `${drawDelay + 0.5}s` }}
                        stroke="var(--bg-surface)"
                        strokeWidth={1}
                      />
                    ))}
                </g>
              );
            })}

        {labels.map((lab, i) => {
          // Thin labels by how many of the longest one fit (9px mono ≈ 5.6px/char), so
          // "Sep 29 Run"-style labels never collide. No forced last label.
          const longest = Math.max(...labels.map((l) => l.length), 1);
          const fit = Math.max(1, Math.floor(iW / (longest * 5.6 + 8)));
          const every = Math.max(1, Math.ceil(n / fit));
          if (i % every !== 0) return null;
          return (
            <text
              key={i}
              x={mL + bandW * i + bandW / 2}
              y={H - 6}
              textAnchor="middle"
              className="fill-(--text-muted) font-mono text-[0.5294rem]"
            >
              {lab}
            </text>
          );
        })}

        {labels.map((_, i) => (
          <rect
            key={i}
            x={mL + bandW * i}
            y={mT}
            width={bandW}
            height={iH}
            fill="transparent"
            onMouseEnter={() => setActive(i)}
            onMouseLeave={() => setActive((a) => (a === i ? null : a))}
          />
        ))}
        {active !== null && (
          <line
            x1={mL + bandW * active + bandW / 2}
            x2={mL + bandW * active + bandW / 2}
            y1={mT}
            y2={mT + iH}
            stroke="var(--border-strong)"
            strokeWidth={1}
          />
        )}
      </svg>

      {active !== null && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-md border border-(--border) bg-(--bg-surface) px-2 py-1 shadow-sm"
          style={{ left: `${((mL + bandW * active + bandW / 2) / W) * 100}%`, top: 0 }}
        >
          <div className="font-mono text-[0.5882rem] text-(--text-muted)">{labels[active]}</div>
          {series.map((s, si) => (
            <div key={si} className="flex items-center gap-1.5 whitespace-nowrap">
              <span aria-hidden className="h-2 w-2 rounded-[2px]" style={{ background: color(si, single) }} />
              <span className="text-[0.6471rem]">
                {s.name ? `${s.name}: ` : ""}
                {fmt(s.data[active] ?? 0, spec.unit)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Donut({ spec, labels }: { spec: ChartSpec; labels: string[] }) {
  const slices = useMemo(() => {
    const s = spec.series;
    if (s.length > 1) {
      return s.map((e, i) => ({ name: e.name ?? labels[i] ?? `#${i + 1}`, value: Math.max(0, e.data[0] ?? 0) }));
    }
    return (s[0]?.data ?? []).map((v, i) => ({ name: labels[i] ?? `#${i + 1}`, value: Math.max(0, v) }));
  }, [spec, labels]);

  const total = slices.reduce((a, b) => a + b.value, 0) || 1;
  const R = 60;
  const r = 38;
  const cx = 70;
  const cy = 70;
  const [active, setActive] = useState<number | null>(null);
  const reduced = usePrefersReducedMotion();

  let angle = -Math.PI / 2;
  const arcs = slices.map((sl, i) => {
    const frac = sl.value / total;
    const a0 = angle;
    const a1 = angle + frac * Math.PI * 2;
    angle = a1;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (ang: number, rad: number) => `${cx + rad * Math.cos(ang)},${cy + rad * Math.sin(ang)}`;
    const gap = 0.015;
    const d = `M ${p(a0 + gap, R)} A ${R} ${R} 0 ${large} 1 ${p(a1 - gap, R)} L ${p(a1 - gap, r)} A ${r} ${r} 0 ${large} 0 ${p(a0 + gap, r)} Z`;
    return { d, color: color(i, false), name: sl.name, value: sl.value, frac };
  });

  return (
    <div className="flex items-center gap-3">
      <svg viewBox="0 0 140 140" width="128" height="128" role="img" aria-label={spec.title ?? "donut chart"}>
        <g className={reduced ? undefined : "cpm-fade"}>
          {arcs.map((a, i) => (
            <path
              key={i}
              d={a.d}
              style={{ fill: a.color, opacity: active === null || active === i ? 1 : 0.4 }}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive((x) => (x === i ? null : x))}
            />
          ))}
        </g>
        <text x={cx} y={cy - 2} textAnchor="middle" className="fill-(--text) text-[0.7647rem] font-semibold">
          {fmt(total, spec.unit)}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" className="fill-(--text-muted) font-mono text-[0.4706rem] uppercase tracking-wider">
          total
        </text>
      </svg>
      <ul className="flex min-w-0 flex-col gap-1">
        {arcs.map((a, i) => (
          <li
            key={i}
            className="flex items-center gap-1.5"
            onMouseEnter={() => setActive(i)}
            onMouseLeave={() => setActive((x) => (x === i ? null : x))}
          >
            <span aria-hidden className="h-2 w-2 shrink-0 rounded-[2px]" style={{ background: a.color }} />
            <span className="truncate font-mono text-[0.5882rem] text-(--text-muted)">{a.name}</span>
            <span className="ml-auto whitespace-nowrap text-[0.6471rem]">
              {fmt(a.value, spec.unit)} · {Math.round(a.frac * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
