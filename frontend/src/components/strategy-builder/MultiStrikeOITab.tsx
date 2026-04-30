/**
 * Multi-Strike OI tab — overlays each option leg's historical Open Interest
 * on a single chart, with the underlying close on a secondary y-axis.
 *
 * Data source: POST /web/strategybuilder/multi-strike-oi (see
 * backend/services/multi_strike_oi_service.py). Backend handles the per-leg
 * history fan-out, OI extraction, and trading-window cap, so this component
 * is presentation-only.
 *
 * Chart engine: TradingView lightweight-charts (April 2026 — switched from
 * Plotly to match openalgo's MultiStrikeOITab and pick up the perf win on
 * dense intraday OI series). Same chart-init / theme-reactivity / IST-tick
 * pattern as StrategyChartTab and StraddleChart.
 *
 * No math here — OI is broker-reported, not computed. A leg whose broker
 * doesn't ship historical OI surfaces as `has_oi=false` in the response;
 * we badge it in the UI and skip drawing its (zeroed) line.
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

import { getMultiStrikeOI } from "@/api/strategybuilder";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";
import type {
  Action,
  MultiStrikeOIData,
  MultiStrikeOILeg,
  MultiStrikeOILegInput,
  OptionType,
} from "@/types/strategy";
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

// Stable per-leg palette — cycles past 10 legs (rare in practice).
const LEG_PALETTE: ReadonlyArray<string> = [
  "#a855f7", // violet
  "#3b82f6", // blue
  "#ec4899", // pink
  "#10b981", // emerald
  "#f97316", // orange
  "#06b6d4", // cyan
  "#eab308", // yellow
  "#ef4444", // red
  "#84cc16", // lime
  "#8b5cf6", // purple
];

const CHART_HEIGHT = 480;

export interface OILegInput {
  symbol: string;
  action: Action;
  strike?: number;
  optionType?: OptionType;
  expiryDate?: string;
}

interface Props {
  underlying: string;
  exchange?: string;
  optionsExchange: FnoExchange;
  legs: OILegInput[];
  enabled: boolean;
}

/** "NIFTY 30APR 24000 CALL" — legend / toggle label. */
function legLabel(
  underlying: string,
  optionType: OptionType | undefined,
  strike: number | undefined,
  expiry: string | undefined,
): string {
  const side = optionType === "CE" ? "CALL" : optionType === "PE" ? "PUT" : "";
  const expiryShort = expiry ? expiry.slice(0, 5) : ""; // "28APR26" → "28APR"
  return [underlying, expiryShort, strike ?? "", side]
    .filter((s) => s !== "" && s !== undefined && s !== null)
    .join(" ");
}

/** Indian short-form OI count: 12,34,567 → 12.35L; 1,23,45,678 → 1.23Cr. */
function formatOI(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

export function MultiStrikeOITab({
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
  /** Symbols (or "__underlying__") the user has hidden via legend toggles.
   *  Toggling visibility doesn't refetch — applyOptions on the existing series. */
  const [hiddenSeries, setHiddenSeries] = useState<Record<string, boolean>>({});
  const [data, setData] = useState<MultiStrikeOIData | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const underlyingSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  /** Keyed by leg.symbol — survives leg add/remove without leaking series. */
  const legSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const dataRef = useRef<MultiStrikeOIData | null>(null);

  const apiLegs = useMemo<MultiStrikeOILegInput[]>(
    () =>
      legs.map((l) => ({
        symbol: l.symbol,
        action: l.action,
        strike: l.strike,
        option_type: l.optionType,
        expiry_date: l.expiryDate,
      })),
    [legs],
  );

  const legsKey = JSON.stringify(apiLegs);

  const colors = useMemo(
    () => ({
      text: isDark ? "#a6adbb" : "#333",
      grid: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.08)",
      border: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
      crosshair: isDark ? "rgba(166,173,187,0.5)" : "rgba(0,0,0,0.3)",
      underlying: isDark ? "#fbbf24" : "#d97706", // amber
    }),
    [isDark],
  );

  // ── Chart init / re-init ────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    const w = containerRef.current.offsetWidth || 800;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
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

    // Underlying series — left axis if no leg series, right axis when legs
    // are present (OI on left, underlying on right). We always create on
    // RIGHT for stability; legs go LEFT (OI Open Interest scale).
    const u = chart.addSeries(LineSeries, {
      color: colors.underlying,
      lineWidth: 2,
      priceScaleId: "right",
      title: "Underlying",
      lastValueVisible: true,
      priceLineVisible: false,
      lineStyle: LineStyle.Dotted,
    });

    chartRef.current = chart;
    underlyingSeriesRef.current = u;

    if (dataRef.current) applyDataToChart(dataRef.current);

    return () => {
      chart.remove();
      chartRef.current = null;
      underlyingSeriesRef.current = null;
      legSeriesRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, days]);

  // Resize observer.
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
    (d: MultiStrikeOIData) => {
      const chart = chartRef.current;
      const u = underlyingSeriesRef.current;
      if (!chart || !u) return;

      // Underlying — only when toggle is on AND backend says it's available.
      if (
        includeUnderlying &&
        d.underlying_available &&
        d.underlying_series.length > 0
      ) {
        const sortedU = [...d.underlying_series].sort((a, b) => a.time - b.time);
        u.setData(sortedU.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
        u.applyOptions({ visible: !hiddenSeries["__underlying__"] });
      } else {
        u.setData([]);
        u.applyOptions({ visible: false });
      }

      // Reconcile leg series — drop those for legs that disappeared.
      const wantSymbols = new Set(d.legs.filter((l) => l.has_oi).map((l) => l.symbol));
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

      // Bind / update each leg series. has_oi=false legs are skipped — they'd
      // draw a flat zero line that adds noise.
      d.legs.forEach((leg, i) => {
        if (!leg.has_oi || leg.series.length === 0) return;
        const color = LEG_PALETTE[i % LEG_PALETTE.length];
        let series = legSeriesRef.current.get(leg.symbol);
        if (!series) {
          series = chart.addSeries(LineSeries, {
            color,
            lineWidth: 2,
            priceScaleId: "left",
            title: legLabel(d.underlying, leg.option_type, leg.strike, leg.expiry),
            lastValueVisible: true,
            priceLineVisible: false,
            visible: !hiddenSeries[leg.symbol],
          });
          legSeriesRef.current.set(leg.symbol, series);
        } else {
          series.applyOptions({
            color,
            title: legLabel(d.underlying, leg.option_type, leg.strike, leg.expiry),
            visible: !hiddenSeries[leg.symbol],
          });
        }
        const sortedLeg = [...leg.series].sort((a, b) => a.time - b.time);
        series.setData(
          sortedLeg.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })),
        );
      });

      chart.timeScale().fitContent();
    },
    [includeUnderlying, hiddenSeries],
  );

  // Re-bind when toggles change without refetching.
  useEffect(() => {
    if (dataRef.current) applyDataToChart(dataRef.current);
  }, [applyDataToChart]);

  // Toggle visibility on existing series — no rebind, no refetch.
  useEffect(() => {
    underlyingSeriesRef.current?.applyOptions({
      visible: !hiddenSeries["__underlying__"],
    });
    for (const [sym, series] of legSeriesRef.current.entries()) {
      series.applyOptions({ visible: !hiddenSeries[sym] });
    }
  }, [hiddenSeries]);

  // ── Auto-fetch on prop changes ──────────────────────────────────────
  useEffect(() => {
    if (!enabled) return;
    if (apiLegs.length === 0) {
      setData(null);
      dataRef.current = null;
      setError(null);
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
    getMultiStrikeOI({
      underlying,
      exchange,
      options_exchange: optionsExchange,
      interval,
      days,
      include_underlying: includeUnderlying,
      legs: apiLegs,
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
          "OI fetch failed";
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
    if (apiLegs.length === 0) {
      toast.error("Add legs with resolved symbols before refreshing");
      return;
    }
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    getMultiStrikeOI({
      underlying,
      exchange,
      options_exchange: optionsExchange,
      interval,
      days,
      include_underlying: includeUnderlying,
      legs: apiLegs,
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
          "OI fetch failed";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => {
        if (requestIdRef.current === reqId) setLoading(false);
      });
  };

  const toggleSeries = useCallback((key: string) => {
    setHiddenSeries((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const hasUsableLegs = apiLegs.length > 0;
  const missingOI = useMemo(
    () => (data?.legs ?? []).filter((l) => !l.has_oi).length,
    [data],
  );
  const visibleLegs = useMemo<MultiStrikeOILeg[]>(
    () => (data?.legs ?? []).filter((l) => l.has_oi && l.series.length > 0),
    [data],
  );

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap items-end gap-3">
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
            {visibleLegs.length} of {data.legs.length} legs · {data.interval} ·{" "}
            {data.days}d
          </span>
          {data.underlying_ltp > 0 && (
            <span className="rounded-md border border-border bg-muted/40 px-2 py-1">
              {data.underlying} spot {data.underlying_ltp.toFixed(2)}
            </span>
          )}
          {/* Latest OI per leg, max 4 to keep the strip readable */}
          {visibleLegs.slice(0, 4).map((l) => {
            const last = l.series[l.series.length - 1];
            return (
              <span
                key={l.symbol}
                className="rounded-md border border-border bg-muted/40 px-2 py-1"
                title={l.symbol}
              >
                {legLabel(data.underlying, l.option_type, l.strike, l.expiry)}{" "}
                {formatOI(last.value)}
              </span>
            );
          })}
          {missingOI > 0 && (
            <span className="rounded-md bg-amber-500/10 px-2 py-1 text-amber-600 dark:text-amber-400">
              {missingOI} leg{missingOI === 1 ? "" : "s"} missing OI history
            </span>
          )}
          {includeUnderlying && data.underlying_available === false && (
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
              Loading OI data…
            </div>
          </div>
        )}
        {!hasUsableLegs && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-lg bg-background/80 text-center text-muted-foreground">
            <p className="text-sm">No legs to track.</p>
            <p className="text-xs">
              Add legs with resolved symbols — per-leg OI series will load
              automatically.
            </p>
          </div>
        )}
        {hasUsableLegs && data && visibleLegs.length === 0 && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-lg bg-background/80 text-center text-muted-foreground">
            <p className="text-sm">No OI data.</p>
            <p className="text-xs">
              Your broker didn't return historical OI for any of these legs.
            </p>
          </div>
        )}
      </div>

      {/* Legend / per-series toggles below the chart */}
      {data && visibleLegs.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-2 text-xs">
          {data.underlying_available && (
            <button
              type="button"
              onClick={() => toggleSeries("__underlying__")}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors",
                !hiddenSeries["__underlying__"]
                  ? "bg-muted/40 font-medium"
                  : "opacity-50 hover:opacity-75",
              )}
            >
              <span
                className="inline-block h-0.5 w-4 rounded"
                style={{
                  backgroundColor: colors.underlying,
                }}
              />
              {data.underlying || underlying || "Underlying"}
            </button>
          )}
          {visibleLegs.map((leg, idx) => {
            const color = LEG_PALETTE[idx % LEG_PALETTE.length];
            const hidden = hiddenSeries[leg.symbol];
            return (
              <button
                key={leg.symbol}
                type="button"
                onClick={() => toggleSeries(leg.symbol)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors",
                  !hidden ? "bg-muted/40 font-medium" : "opacity-50 hover:opacity-75",
                )}
                title={leg.symbol}
              >
                <span
                  className="inline-block h-0.5 w-4 rounded"
                  style={{ backgroundColor: color }}
                />
                {legLabel(data.underlying, leg.option_type, leg.strike, leg.expiry)}
              </button>
            );
          })}
        </div>
      )}

      {data && visibleLegs.length > 0 && (
        <p className="text-[11px] italic text-muted-foreground">
          Each line shows historical Open Interest for one option leg. Rising
          OI on a short leg = new short positioning building; falling OI =
          unwinding. Overlay the underlying to spot OI shifts at price levels.
        </p>
      )}
    </div>
  );
}
