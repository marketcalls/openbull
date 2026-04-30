/**
 * Historical Strategy Chart tab — combined-premium time series for the
 * current leg set, with optional underlying overlay on a secondary y-axis
 * and per-leg toggleable lines.
 *
 * Drives off `POST /web/strategybuilder/chart` (Phase 3 backend service).
 * The backend already does the heavy lifting:
 *   - per-leg history fetches with intersected timestamps (no phantom dips
 *     when one broker candle is late),
 *   - combined-premium series signed by BUY/SELL × lots × lot_size,
 *   - PnL series stamped when every leg has an entry_price,
 *   - underlying-available fallback when intraday history is missing.
 *
 * This component renders that data via Plotly Scattergl (high-perf for
 * many points). Two y-axes — combined premium / P&L on the left, optional
 * underlying close on the right — because the scales are wildly
 * different (a 200-rupee combined premium next to a 24,000-rupee NIFTY
 * spot would crush the premium curve to a flat line on a single axis).
 *
 * IST timestamps: Plotly defaults to UTC. We pre-shift unix-seconds by
 * +05:30 and let Plotly render the shifted timestamps as "UTC" — what
 * the user sees is IST. Standard trick for Indian-market Plotly charts;
 * matches the OptionGreeks / StraddleChart inline usage but adapted to
 * Plotly's date axis (those two use lightweight-charts).
 *
 * Tab-scoped fetch: parent gates with `enabled={activeTab === "chart"}`
 * so we don't fan out N broker history calls just because the page is
 * mounted. The hook also debounces-on-change so rapid interval/days
 * toggles coalesce.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { getStrategyChart } from "@/api/strategybuilder";
import Plot from "@/components/charts/Plot";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";
import type { Action, ChartResponseData } from "@/types/strategy";
import type { FnoExchange } from "@/types/optionchain";

// Match the existing tools' INTERVALS list verbatim — keeping the dropdown
// consistent across all chart tools matters more than supporting every
// broker-specific interval.
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

/** Shift unix-seconds by +05:30 then convert to ms — Plotly sees these as
 * UTC, the user sees IST. */
function tsToIstMs(unixSec: number): number {
  return unixSec * 1000 + 5.5 * 60 * 60 * 1000;
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

  // Stable serialisation of the leg list — avoids refiring on every render
  // that produces a new array reference with identical content.
  const legsKey = JSON.stringify(legs);

  // Auto-fetch when controls change, only while the tab is active.
  useEffect(() => {
    if (!enabled) return;
    if (legs.length === 0) {
      setData(null);
      setError(null);
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
        setData(resp);
        // If PnL view is selected but the backend has no entry_premium
        // (a leg lacked entry_price), fall back to value view so the
        // chart isn't blank.
        if (view === "pnl" && resp.entry_premium == null) {
          setView("value");
        }
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

  // Manual refresh — same pipeline as the auto-fetch above. Forces a
  // refetch even when the deps haven't changed (useful after market opens).
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
        setData(resp);
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

  const colors = useMemo(
    () => ({
      text: isDark ? "#e0e0e0" : "#333333",
      grid: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      zero: isDark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.35)",
      combined: "#3b82f6", // blue-500
      pnlPositive: "#22c55e",
      pnlNegative: "#ef4444",
      underlying: "#f59e0b", // amber-500
      legPalette: [
        "#a855f7", // purple-500
        "#06b6d4", // cyan-500
        "#ec4899", // pink-500
        "#84cc16", // lime-500
        "#0ea5e9", // sky-500
        "#f97316", // orange-500
        "#14b8a6", // teal-500
        "#dc2626", // red-600
      ],
      hoverBg: isDark ? "#1e293b" : "#ffffff",
      hoverText: isDark ? "#e0e0e0" : "#333333",
      hoverBorder: isDark ? "#475569" : "#e2e8f0",
    }),
    [isDark],
  );

  const traces = useMemo<unknown[]>(() => {
    if (!data) return [];
    const out: unknown[] = [];

    // Combined series (value or pnl) on left axis
    const combinedField = view === "pnl" ? "pnl" : "value";
    const combinedX: number[] = [];
    const combinedY: number[] = [];
    for (const p of data.combined_series) {
      const v = p[combinedField as "value" | "pnl"];
      if (v == null || !Number.isFinite(v)) continue;
      combinedX.push(tsToIstMs(p.time));
      combinedY.push(v);
    }

    out.push({
      x: combinedX,
      y: combinedY,
      type: "scattergl",
      mode: "lines",
      name: view === "pnl" ? "Strategy P&L" : "Strategy Premium",
      line: { color: colors.combined, width: 2 },
      yaxis: "y",
      hovertemplate:
        view === "pnl"
          ? "%{x|%d/%m %H:%M IST}<br>P&L %{y:.2f}<extra></extra>"
          : "%{x|%d/%m %H:%M IST}<br>Premium %{y:.2f}<extra></extra>",
    });

    // Per-leg lines (left axis) — toggled
    if (showLegs) {
      data.leg_series.forEach((leg, i) => {
        const xs = leg.series.map((p) => tsToIstMs(p.time));
        const ys = leg.series.map((p) => p.close);
        out.push({
          x: xs,
          y: ys,
          type: "scattergl",
          mode: "lines",
          name: `${leg.action} ${leg.symbol}`,
          line: {
            color: colors.legPalette[i % colors.legPalette.length],
            width: 1,
            dash: "dot",
          },
          yaxis: "y",
          opacity: 0.7,
          hovertemplate:
            "%{x|%d/%m %H:%M IST}<br>" +
            leg.symbol +
            " %{y:.2f}<extra></extra>",
        });
      });
    }

    // Underlying overlay on right axis
    if (
      includeUnderlying &&
      data.underlying_available &&
      data.underlying_series.length > 0
    ) {
      out.push({
        x: data.underlying_series.map((p) => tsToIstMs(p.time)),
        y: data.underlying_series.map((p) => p.close),
        type: "scattergl",
        mode: "lines",
        name: data.underlying,
        line: { color: colors.underlying, width: 1.5 },
        yaxis: "y2",
        hovertemplate:
          "%{x|%d/%m %H:%M IST}<br>" +
          data.underlying +
          " %{y:.2f}<extra></extra>",
      });
    }

    return out;
  }, [data, view, showLegs, includeUnderlying, colors]);

  const layout = useMemo<Record<string, unknown>>(() => {
    const showY2 =
      includeUnderlying &&
      data?.underlying_available === true &&
      (data?.underlying_series.length ?? 0) > 0;

    const shapes: unknown[] = [];
    // Zero line on the P&L view
    if (view === "pnl" && data?.combined_series.length) {
      shapes.push({
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 0,
        line: { color: colors.zero, width: 1, dash: "dot" },
      });
    }

    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: colors.text, family: "system-ui, sans-serif" },
      hovermode: "x unified",
      hoverlabel: {
        bgcolor: colors.hoverBg,
        font: { color: colors.hoverText, size: 12 },
        bordercolor: colors.hoverBorder,
      },
      showlegend: true,
      legend: {
        orientation: "h",
        x: 0.5,
        xanchor: "center",
        y: -0.18,
        font: { color: colors.text, size: 11 },
      },
      margin: { l: 70, r: showY2 ? 70 : 30, t: 30, b: 70 },
      xaxis: {
        type: "date",
        tickformat: days > 1 ? "%d/%m %H:%M" : "%H:%M",
        tickfont: { color: colors.text, size: 10 },
        gridcolor: colors.grid,
        zeroline: false,
      },
      yaxis: {
        title: {
          text: view === "pnl" ? "Strategy P&L (₹)" : "Strategy Premium (₹)",
          font: { color: colors.text, size: 12 },
        },
        tickfont: { color: colors.text, size: 10 },
        gridcolor: colors.grid,
        zeroline: false,
      },
      yaxis2: showY2
        ? {
            title: {
              text: `${data?.underlying ?? "Underlying"} Close`,
              font: { color: colors.underlying, size: 12 },
            },
            tickfont: { color: colors.underlying, size: 10 },
            overlaying: "y",
            side: "right",
            showgrid: false,
            zeroline: false,
          }
        : undefined,
      shapes,
    };
  }, [data, view, includeUnderlying, colors, days]);

  const config = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "toggleSpikelines",
      ],
      responsive: true,
    }),
    [],
  );

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

      {/* Chart status badges */}
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

      {/* Empty / loading / chart */}
      {!hasUsableLegs ? (
        <div className="flex h-[480px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
          <p className="text-sm">No legs to chart.</p>
          <p className="text-xs">
            Add legs with resolved symbols — the historical curve will load
            automatically.
          </p>
        </div>
      ) : loading && !data ? (
        <div className="flex h-[480px] items-center justify-center text-sm text-muted-foreground">
          Fetching {legs.length} leg histor{legs.length === 1 ? "y" : "ies"}…
        </div>
      ) : data && data.combined_series.length > 0 ? (
        <Plot
          data={traces}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: "100%", height: "480px" }}
        />
      ) : data ? (
        <div className="flex h-[480px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
          <p className="text-sm">No overlapping candles.</p>
          <p className="text-xs">
            The legs' histories don't intersect for {interval} / {days}d. Try a
            larger interval or longer window.
          </p>
        </div>
      ) : null}
    </div>
  );
}
