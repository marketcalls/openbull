/**
 * Historical Strategy Chart tab — combined P&L / premium time series for the
 * current leg set, with optional underlying overlay on a secondary y-axis
 * and per-leg toggleable lines.
 *
 * Drives off `POST /web/strategybuilder/chart` (Phase 3 backend service).
 * The backend returns:
 *   - combined_series[*].value  — signed rupee position premium
 *   - combined_series[*].pnl    — value(t) − entry_premium (when all legs
 *     have entry_price)
 *   - combined_series[*].combined_premium / .net_premium — openalgo-shape
 *     per-share parity columns (kept here as a status badge)
 *   - leg_series[*].series      — per-leg historical close
 *   - underlying_series         — index close on the second y-axis
 *
 * Chart engine: TradingView lightweight-charts. Switched from Plotly
 * (April 2026) to match openalgo's visual language and pick up the perf
 * win on dense intraday series. The other openbull tools that already use
 * lightweight-charts (StraddleChart, OptionGreeks) follow the same chart
 * init / theme-reactive / IST-formatted timestamp patterns.
 *
 * IST timestamps: backend stamps Unix seconds; we shift +05:30 inside the
 * tickMarkFormatter and the localization timeFormatter so users (and
 * non-IST machines) see Indian-market times.
 *
 * Tab-scoped fetch: parent gates with `enabled={activeTab === "chart"}`
 * so the broker isn't polled on every page mount.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { toast } from "sonner";

import { getStrategyChart } from "@/api/strategybuilder";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";
import type { Action, ChartResponseData } from "@/types/strategy";
import type { FnoExchange } from "@/types/optionchain";

const INTERVALS: ReadonlyArray<string> = [
  "1m",
  "3m",
  "5m",
  "10m",
  "15m",
  "30m",
  "1h",
];

const DAY_RANGES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: "1 Day" },
  { value: 3, label: "3 Days" },
  { value: 5, label: "5 Days" },
  { value: 10, label: "10 Days" },
  { value: 15, label: "15 Days" },
  { value: 30, label: "30 Days" },
];

// Per-leg palette — cycles past 8 legs (rare in practice).
const LEG_PALETTE: ReadonlyArray<string> = [
  "#a855f7", // violet
  "#06b6d4", // cyan
  "#ec4899", // pink
  "#84cc16", // lime
  "#0ea5e9", // sky
  "#f97316", // orange
  "#14b8a6", // teal
  "#dc2626", // red-600
];

const CHART_HEIGHT = 480;

type ViewMode = "value" | "pnl";

export interface ChartLegInput {
  symbol: string;
  action: Action;
  lots: number;
  lot_size: number;
  entry_price?: number;
}

interface Props {
  underlying: string;
  /** Spot exchange — auto-resolved server-side when omitted. */
  exchange?: string;
  /** Options exchange (NFO/BFO/MCX) — used as the default leg exchange. */
  optionsExchange: FnoExchange;
  legs: ChartLegInput[];
  /** Set false to suppress fetches when the tab isn't active. */
  enabled: boolean;
}

export function StrategyChartTab({
  underlying,
  exchange,
  optionsExchange,
  legs,
  enabled,
}: Props) {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [interval, setInterval] = useState<string>("5m");
  const [days, setDays] = useState<number>(5);
  const [includeUnderlying, setIncludeUnderlying] = useState<boolean>(true);
  const [showLegs, setShowLegs] = useState<boolean>(false);
  const [view, setView] = useState<ViewMode>("pnl");

  const [data, setData] = useState<ChartResponseData | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  // Refs into the chart so re-renders don't tear it down. The init effect
  // builds the chart + series; the data effect binds rows; visibility
  // effects toggle without rebinding.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const combinedSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const underlyingSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  /** Keyed by leg.symbol — survives leg add/remove without leaking series. */
  const legSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const dataRef = useRef<ChartResponseData | null>(null);

  // Stable serialisation of the leg list — avoids refiring on every render
  // that produces a new array reference with identical content.
  const legsKey = JSON.stringify(legs);

  // ── Theme-reactive colour palette (read from refs in event handlers
  //    that the closure-captures from the init effect would otherwise miss). ──
  const colors = useMemo(
    () => ({
      text: isDark ? "#a6adbb" : "#333",
      grid: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.08)",
      border: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
      crosshair: isDark ? "rgba(166,173,187,0.5)" : "rgba(0,0,0,0.3)",
      combined: "#3b82f6", // blue-500
      underlying: isDark ? "#fbbf24" : "#d97706", // amber
      zero: isDark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.35)",
    }),
    [isDark],
  );

  // ── Chart init / re-init ────────────────────────────────────────────
  // Re-init when theme flips (lightweight-charts colours bake in at init)
  // or when the days knob changes (tickFormatter depends on it).
  useEffect(() => {
    if (!containerRef.current) return;
    const w = containerRef.current.offsetWidth || 800;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      combinedSeriesRef.current = null;
      underlyingSeriesRef.current = null;
      legSeriesRef.current.clear();
    }

    const chart = createChart(containerRef.current, {
      width: w,
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: colors.text,
      },
      grid: {
        vertLines: { color: colors.grid, style: 1 as const, visible: true },
        horzLines: { color: colors.grid, style: 1 as const, visible: true },
      },
      leftPriceScale: {
        visible: true,
        borderColor: colors.border,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      rightPriceScale: {
        visible: true,
        borderColor: colors.border,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: colors.border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          // Force IST regardless of user machine TZ.
          const ist = new Date(time * 1000 + 5.5 * 60 * 60 * 1000);
          const hh = ist.getUTCHours().toString().padStart(2, "0");
          const mm = ist.getUTCMinutes().toString().padStart(2, "0");
          if (days > 1) {
            const dd = ist.getUTCDate().toString().padStart(2, "0");
            const mo = (ist.getUTCMonth() + 1).toString().padStart(2, "0");
            return `${dd}/${mo} ${hh}:${mm}`;
          }
          return `${hh}:${mm}`;
        },
      },
      localization: {
        timeFormatter: (time: number) => {
          const ist = new Date(time * 1000 + 5.5 * 60 * 60 * 1000);
          const dd = ist.getUTCDate().toString().padStart(2, "0");
          const mo = (ist.getUTCMonth() + 1).toString().padStart(2, "0");
          const yy = ist.getUTCFullYear().toString().slice(-2);
          const hh = ist.getUTCHours().toString().padStart(2, "0");
          const mm = ist.getUTCMinutes().toString().padStart(2, "0");
          return `${dd}/${mo}/${yy} ${hh}:${mm} IST`;
        },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { width: 1 as const, color: colors.crosshair, style: 2 as const },
        horzLine: { width: 1 as const, color: colors.crosshair, style: 2 as const },
      },
    });

    // Combined series (left axis). The view toggle (P&L vs Premium) flips
    // which column we bind into this single series — no need for two series.
    const combined = chart.addSeries(LineSeries, {
      color: colors.combined,
      lineWidth: 2,
      priceScaleId: "left",
      title: view === "pnl" ? "Strategy P&L" : "Strategy Premium",
      lastValueVisible: true,
      priceLineVisible: false,
    });

    // Underlying overlay (right axis). Present always so toggle is cheap;
    // `setData([])` blanks it when the user disables the overlay.
    const u = chart.addSeries(LineSeries, {
      color: colors.underlying,
      lineWidth: 2,
      priceScaleId: "right",
      title: underlying || "Underlying",
      lastValueVisible: true,
      priceLineVisible: false,
      lineStyle: LineStyle.Dotted,
    });

    chartRef.current = chart;
    combinedSeriesRef.current = combined;
    underlyingSeriesRef.current = u;

    if (dataRef.current) applyDataToChart(dataRef.current);

    return () => {
      chart.remove();
      chartRef.current = null;
      combinedSeriesRef.current = null;
      underlyingSeriesRef.current = null;
      legSeriesRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, days, underlying]);

  // Resize observer — keep the chart width in sync with its container.
  useEffect(() => {
    const onResize = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.offsetWidth });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── Data binding ────────────────────────────────────────────────────
  const applyDataToChart = useCallback(
    (d: ChartResponseData) => {
      const chart = chartRef.current;
      const combined = combinedSeriesRef.current;
      const u = underlyingSeriesRef.current;
      if (!chart || !combined || !u) return;

      // Combined series — bind the column matching the current view.
      const sortedCombined = [...d.combined_series].sort((a, b) => a.time - b.time);
      const combinedField = view === "pnl" ? "pnl" : "value";
      const combinedPoints = sortedCombined
        .map((p) => {
          const v = p[combinedField as "value" | "pnl"];
          if (v == null || !Number.isFinite(v)) return null;
          return { time: p.time as UTCTimestamp, value: v };
        })
        .filter((p): p is { time: UTCTimestamp; value: number } => p !== null);
      combined.setData(combinedPoints);
      combined.applyOptions({
        title: view === "pnl" ? "Strategy P&L" : "Strategy Premium",
      });

      // Underlying — only if the toggle is on AND backend says it's available.
      if (
        includeUnderlying &&
        d.underlying_available &&
        d.underlying_series.length > 0
      ) {
        const sortedU = [...d.underlying_series].sort((a, b) => a.time - b.time);
        u.setData(sortedU.map((p) => ({ time: p.time as UTCTimestamp, value: p.close })));
        u.applyOptions({ visible: true });
      } else {
        u.setData([]);
        u.applyOptions({ visible: false });
      }

      // Per-leg lines — reconcile against existing refs so toggling legs
      // doesn't leak chart resources. Remove series for legs that disappeared.
      const wantSymbols = showLegs
        ? new Set(d.leg_series.map((l) => l.symbol))
        : new Set<string>();
      for (const [sym, series] of legSeriesRef.current.entries()) {
        if (!wantSymbols.has(sym)) {
          try {
            chart.removeSeries(series);
          } catch {
            /* already removed */
          }
          legSeriesRef.current.delete(sym);
        }
      }

      if (showLegs) {
        d.leg_series.forEach((leg, i) => {
          const color = LEG_PALETTE[i % LEG_PALETTE.length];
          let series = legSeriesRef.current.get(leg.symbol);
          if (!series) {
            series = chart.addSeries(LineSeries, {
              color,
              lineWidth: 1,
              priceScaleId: "left",
              title: `${leg.action} ${leg.symbol}`,
              lastValueVisible: false,
              priceLineVisible: false,
              lineStyle: LineStyle.Dashed,
            });
            legSeriesRef.current.set(leg.symbol, series);
          } else {
            series.applyOptions({
              color,
              title: `${leg.action} ${leg.symbol}`,
            });
          }
          const sortedLeg = [...leg.series].sort((a, b) => a.time - b.time);
          series.setData(
            sortedLeg.map((p) => ({ time: p.time as UTCTimestamp, value: p.close })),
          );
        });
      }

      chart.timeScale().fitContent();
    },
    [view, includeUnderlying, showLegs],
  );

  // Re-bind data when the view or visibility toggles change without
  // refetching — applyDataToChart reads from dataRef.
  useEffect(() => {
    if (dataRef.current) applyDataToChart(dataRef.current);
  }, [applyDataToChart]);

  // ── Auto-fetch on prop changes ──────────────────────────────────────
  useEffect(() => {
    if (!enabled) return;
    if (legs.length === 0) {
      setData(null);
      dataRef.current = null;
      setError(null);
      combinedSeriesRef.current?.setData([]);
      underlyingSeriesRef.current?.setData([]);
      for (const series of legSeriesRef.current.values()) {
        try {
          chartRef.current?.removeSeries(series);
        } catch {
          /* already removed */
        }
      }
      legSeriesRef.current.clear();
      return;
    }

    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    getStrategyChart({
      underlying,
      exchange,
      options_exchange: optionsExchange,
      interval,
      days,
      include_underlying: includeUnderlying,
      legs,
    })
      .then((resp) => {
        if (requestIdRef.current !== reqId) return;
        dataRef.current = resp;
        setData(resp);
        // Fall back to value view if the user picked P&L but no entry premia
        // exist (one or more legs lack entry_price).
        if (view === "pnl" && resp.entry_premium == null) {
          setView("value");
          return; // applyDataToChart will re-run from the view-change effect
        }
        applyDataToChart(resp);
      })
      .catch((e) => {
        if (requestIdRef.current !== reqId) return;
        const msg =
          (e as { response?: { data?: { detail?: string } }; message?: string })
            ?.response?.data?.detail ??
          (e as { message?: string })?.message ??
          "Chart fetch failed";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => {
        if (requestIdRef.current === reqId) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    enabled,
    legsKey,
    underlying,
    exchange,
    optionsExchange,
    interval,
    days,
    includeUnderlying,
  ]);

  const handleRefresh = () => {
    if (legs.length === 0) {
      toast.error("Add legs with resolved symbols before refreshing");
      return;
    }
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    getStrategyChart({
      underlying,
      exchange,
      options_exchange: optionsExchange,
      interval,
      days,
      include_underlying: includeUnderlying,
      legs,
    })
      .then((resp) => {
        if (requestIdRef.current !== reqId) return;
        dataRef.current = resp;
        setData(resp);
        applyDataToChart(resp);
      })
      .catch((e) => {
        if (requestIdRef.current !== reqId) return;
        const msg =
          (e as { response?: { data?: { detail?: string } }; message?: string })
            ?.response?.data?.detail ??
          (e as { message?: string })?.message ??
          "Chart fetch failed";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => {
        if (requestIdRef.current === reqId) setLoading(false);
      });
  };

  const hasUsableLegs = legs.length > 0;
  const hasPnlData =
    data?.entry_premium !== null && data?.entry_premium !== undefined;

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="block text-xs text-muted-foreground">View</label>
          <div className="inline-flex rounded-lg border border-input p-0.5">
            <button
              type="button"
              onClick={() => setView("pnl")}
              disabled={!hasPnlData}
              className={cn(
                "h-7 rounded-md px-2 text-xs font-medium transition-colors",
                view === "pnl"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
                !hasPnlData && "cursor-not-allowed opacity-40",
              )}
              title={
                hasPnlData
                  ? "Mark-to-market P&L vs entry"
                  : "Set entry prices on every leg to see P&L"
              }
            >
              P&L
            </button>
            <button
              type="button"
              onClick={() => setView("value")}
              className={cn(
                "h-7 rounded-md px-2 text-xs font-medium transition-colors",
                view === "value"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Premium
            </button>
          </div>
        </div>

        <div className="space-y-1">
          <label className="block text-xs text-muted-foreground">Interval</label>
          <select
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
            className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {INTERVALS.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label className="block text-xs text-muted-foreground">Days</label>
          <select
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
            className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {DAY_RANGES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>

        <label className="flex h-8 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={includeUnderlying}
            onChange={(e) => setIncludeUnderlying(e.target.checked)}
            className="h-3.5 w-3.5 cursor-pointer"
          />
          Underlying overlay
        </label>

        <label className="flex h-8 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={showLegs}
            onChange={(e) => setShowLegs(e.target.checked)}
            className="h-3.5 w-3.5 cursor-pointer"
          />
          Show per-leg
        </label>

        <Button
          variant="outline"
          onClick={handleRefresh}
          disabled={!hasUsableLegs || loading}
          className="h-8"
        >
          {loading ? "Loading…" : "Refresh"}
        </Button>
      </div>

      {/* Status badges */}
      {data && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-md border border-border bg-muted/40 px-2 py-1">
            {data.combined_series.length} candles · {data.interval} ·{" "}
            {data.days}d
          </span>
          {data.underlying_ltp > 0 && (
            <span className="rounded-md border border-border bg-muted/40 px-2 py-1">
              {data.underlying} spot {data.underlying_ltp.toFixed(2)}
            </span>
          )}
          {data.entry_premium !== null && (
            <span className="rounded-md border border-border bg-muted/40 px-2 py-1">
              Entry premium ₹{data.entry_premium.toFixed(2)}
            </span>
          )}
          {/* Credit / Debit / Flat — backend-classified per openalgo's
              convention (SELL=+1 per-share). */}
          {data.tag === "credit" ? (
            <span
              className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 font-medium text-emerald-700 dark:text-emerald-400"
              title={`Net credit ₹${data.entry_abs_premium.toFixed(2)} per share (openalgo formula)`}
            >
              Net credit · ₹{data.entry_abs_premium.toFixed(2)}/sh
            </span>
          ) : data.tag === "debit" ? (
            <span
              className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-1 font-medium text-rose-700 dark:text-rose-400"
              title={`Net debit ₹${data.entry_abs_premium.toFixed(2)} per share (openalgo formula)`}
            >
              Net debit · ₹{data.entry_abs_premium.toFixed(2)}/sh
            </span>
          ) : (
            <span className="rounded-md border border-border bg-muted/40 px-2 py-1 font-medium">
              Flat
            </span>
          )}
          {includeUnderlying && !data.underlying_available && (
            <span className="rounded-md bg-amber-500/10 px-2 py-1 text-amber-600 dark:text-amber-400">
              Underlying intraday history not available — overlay hidden
            </span>
          )}
        </div>
      )}

      {error && !data && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Chart */}
      <div className="relative">
        <div
          ref={containerRef}
          className="relative w-full rounded-lg border border-border/50"
          style={{ height: CHART_HEIGHT }}
        />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-background/60">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Loading strategy chart…
            </div>
          </div>
        )}
        {!hasUsableLegs && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-lg bg-background/80 text-center text-muted-foreground">
            <p className="text-sm">No legs to chart.</p>
            <p className="text-xs">
              Add legs with resolved symbols — the historical curve will load
              automatically.
            </p>
          </div>
        )}
        {hasUsableLegs && data && data.combined_series.length === 0 && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-lg bg-background/80 text-center text-muted-foreground">
            <p className="text-sm">No overlapping candles.</p>
            <p className="text-xs">
              The legs' histories don't intersect for {interval} / {days}d. Try
              a larger interval or longer window.
            </p>
          </div>
        )}
      </div>

      {/* Series legend / toggles below the chart */}
      {data && data.combined_series.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-3 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/40 px-2.5 py-1 font-medium">
            <span
              className="inline-block h-0.5 w-4 rounded"
              style={{ backgroundColor: colors.combined }}
            />
            {view === "pnl" ? "Strategy P&L" : "Strategy Premium"}
          </span>
          {data.underlying_available && (
            <button
              type="button"
              onClick={() => setIncludeUnderlying((v) => !v)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors",
                includeUnderlying
                  ? "bg-muted/40 font-medium"
                  : "opacity-50 hover:opacity-75",
              )}
            >
              <span
                className="inline-block h-0.5 w-4 rounded"
                style={{
                  backgroundColor: colors.underlying,
                  borderTop: "1px dotted",
                }}
              />
              {data.underlying || underlying || "Underlying"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
