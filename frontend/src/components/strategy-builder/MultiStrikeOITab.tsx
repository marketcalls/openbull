/**
 * Multi-Strike OI tab — overlays each option leg's historical Open Interest
 * on a single chart, with the underlying close on a secondary y-axis. Ported
 * from openalgo's MultiStrikeOITab; rendered via Plotly to stay consistent
 * with openbull's other chart tools (StrategyChartTab, OptionGreeks, etc).
 *
 * Data source: POST /web/strategybuilder/multi-strike-oi (see
 * backend/services/multi_strike_oi_service.py). The backend already does
 * the per-leg history fan-out, OI extraction, and trading-window cap, so
 * this component is presentation-only.
 *
 * No math here — OI is broker-reported, not computed. A leg whose broker
 * doesn't ship historical OI surfaces as ``has_oi=false`` in the response;
 * we badge it in the UI and skip drawing its (zeroed) line.
 *
 * Tab-scoped fetch: parent gates with ``enabled={activeTab === "oi"}`` so
 * the broker isn't polled on every page mount.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { getMultiStrikeOI } from "@/api/strategybuilder";
import Plot from "@/components/charts/Plot";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";
import type {
  Action,
  MultiStrikeOIData,
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

function tsToIstMs(unixSec: number): number {
  return unixSec * 1000 + 5.5 * 60 * 60 * 1000;
}

/** "NIFTY 30APR 24000 CALL" — OI legend label. */
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

/** Indian short form for OI counts: 12,34,567 → 12.35L, 1,23,45,678 → 1.23Cr. */
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
  const [data, setData] = useState<MultiStrikeOIData | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

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

  useEffect(() => {
    if (!enabled) return;
    if (apiLegs.length === 0) {
      setData(null);
      setError(null);
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
        setData(resp);
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
        setData(resp);
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

  const colors = useMemo(
    () => ({
      text: isDark ? "#e0e0e0" : "#333333",
      grid: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      underlying: "#f59e0b",
      hoverBg: isDark ? "#1e293b" : "#ffffff",
      hoverText: isDark ? "#e0e0e0" : "#333333",
      hoverBorder: isDark ? "#475569" : "#e2e8f0",
    }),
    [isDark],
  );

  const traces = useMemo<unknown[]>(() => {
    if (!data) return [];
    const out: unknown[] = [];

    // One line per leg with OI data.
    data.legs.forEach((leg, i) => {
      if (!leg.has_oi || leg.series.length === 0) return;
      const color = LEG_PALETTE[i % LEG_PALETTE.length];
      out.push({
        x: leg.series.map((p) => tsToIstMs(p.time)),
        y: leg.series.map((p) => p.value),
        type: "scattergl",
        mode: "lines",
        name: legLabel(data.underlying, leg.option_type, leg.strike, leg.expiry),
        line: { color, width: 2 },
        yaxis: "y",
        hovertemplate:
          "%{x|%d/%m %H:%M IST}<br>OI %{y:,.0f}<extra></extra>",
      });
    });

    // Underlying overlay (right axis).
    if (
      includeUnderlying &&
      data.underlying_available &&
      data.underlying_series.length > 0
    ) {
      out.push({
        x: data.underlying_series.map((p) => tsToIstMs(p.time)),
        y: data.underlying_series.map((p) => p.value),
        type: "scattergl",
        mode: "lines",
        name: data.underlying,
        line: { color: colors.underlying, width: 1.5, dash: "dot" },
        yaxis: "y2",
        hovertemplate:
          "%{x|%d/%m %H:%M IST}<br>" +
          data.underlying +
          " %{y:.2f}<extra></extra>",
      });
    }

    return out;
  }, [data, includeUnderlying, colors]);

  const layout = useMemo<Record<string, unknown>>(() => {
    const showY2 =
      includeUnderlying &&
      data?.underlying_available === true &&
      (data?.underlying_series.length ?? 0) > 0;

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
        title: { text: "Open Interest", font: { color: colors.text, size: 12 } },
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
    };
  }, [data, includeUnderlying, colors, days]);

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

  const hasUsableLegs = apiLegs.length > 0;
  const missingOI = useMemo(
    () => (data?.legs ?? []).filter((l) => !l.has_oi).length,
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
            {data.legs.filter((l) => l.has_oi).length} of {data.legs.length}{" "}
            legs · {data.interval} · {data.days}d
          </span>
          {data.underlying_ltp > 0 && (
            <span className="rounded-md border border-border bg-muted/40 px-2 py-1">
              {data.underlying} spot {data.underlying_ltp.toFixed(2)}
            </span>
          )}
          {/* Latest OI per leg, if available */}
          {data.legs
            .filter((l) => l.has_oi && l.series.length > 0)
            .slice(0, 4)
            .map((l) => {
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

      {!hasUsableLegs ? (
        <div className="flex h-[480px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
          <p className="text-sm">No legs to track.</p>
          <p className="text-xs">
            Add legs with resolved symbols — per-leg OI series will load
            automatically.
          </p>
        </div>
      ) : loading && !data ? (
        <div className="flex h-[480px] items-center justify-center text-sm text-muted-foreground">
          Fetching {apiLegs.length} leg OI histor
          {apiLegs.length === 1 ? "y" : "ies"}…
        </div>
      ) : data &&
        data.legs.some((l) => l.has_oi && l.series.length > 0) ? (
        <Plot
          data={traces}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: "100%", height: "480px" }}
        />
      ) : data ? (
        <div className="flex h-[480px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
          <p className="text-sm">No OI data.</p>
          <p className="text-xs">
            Your broker didn't return historical OI for any of these legs.
          </p>
        </div>
      ) : null}

      {/* Inline note matching openalgo's tooltip / status */}
      {data && data.legs.some((l) => l.has_oi) && (
        <p
          className={cn(
            "text-[11px] text-muted-foreground",
            data.legs.length > 1 && "italic",
          )}
        >
          Each line shows historical Open Interest for one option leg. Rising
          OI on a short leg = new short positioning building; falling OI =
          unwinding. Overlay the underlying to spot OI shifts at price levels.
        </p>
      )}
    </div>
  );
}
