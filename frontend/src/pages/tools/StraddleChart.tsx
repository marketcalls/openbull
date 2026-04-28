import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { fetchStraddleChart, type StraddleData } from "@/api/straddle";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";

type StraddleExchange = "NFO" | "BFO";

const STRADDLE_EXCHANGES: ReadonlyArray<{ value: StraddleExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

const INTERVALS: ReadonlyArray<string> = ["1m", "3m", "5m", "10m", "15m", "30m", "1h"];

const DAY_RANGES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: "1 Day" },
  { value: 3, label: "3 Days" },
  { value: 5, label: "5 Days" },
  { value: 10, label: "10 Days" },
  { value: 15, label: "15 Days" },
];

const CHART_HEIGHT = 500;

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

export default function StraddleChart() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<StraddleExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [interval, setInterval] = useState<string>("5m");
  const [days, setDays] = useState<number>(5);

  // Defaults match openalgo: Straddle on, Spot off, Synthetic off.
  const [showStraddle, setShowStraddle] = useState<boolean>(true);
  const [showSpot, setShowSpot] = useState<boolean>(false);
  const [showSyntheticFuture, setShowSyntheticFuture] = useState<boolean>(false);

  const [chartData, setChartData] = useState<StraddleData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const requestIdRef = useRef(0);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<{
    spot?: ISeriesApi<"Line">;
    straddle?: ISeriesApi<"Line">;
    synth?: ISeriesApi<"Line">;
  }>({});
  const dataRef = useRef<StraddleData | null>(null);

  // ── Underlyings + expiries (shared pattern) ──────────────────────────

  const underlyingsQuery = useQuery({
    queryKey: ["option-underlyings", exchange],
    queryFn: () => fetchUnderlyings(exchange),
    retry: 0,
    staleTime: 5 * 60_000,
  });
  const underlyings = useMemo<UnderlyingOption[]>(() => {
    if (underlyingsQuery.data?.status === "success" && underlyingsQuery.data.data.length > 0) {
      return underlyingsQuery.data.data;
    }
    return FALLBACK_UNDERLYINGS[exchange];
  }, [underlyingsQuery.data, exchange]);

  useEffect(() => {
    if (underlyings.length === 0) return;
    if (!underlyings.some((u) => u.symbol === underlying)) {
      setUnderlying(underlyings[0].symbol);
      setExpiry("");
    }
  }, [underlyings, underlying]);

  const expiriesQuery = useQuery({
    queryKey: ["expiries", underlying, exchange],
    queryFn: () => fetchExpiries({ symbol: underlying, exchange, instrumenttype: "options" }),
    enabled: !!underlying && !!exchange,
    retry: 0,
  });
  useEffect(() => {
    if (expiriesQuery.data?.status === "success" && expiriesQuery.data.data.length > 0) {
      setExpiry((prev) =>
        prev && expiriesQuery.data!.data.includes(prev) ? prev : expiriesQuery.data!.data[0]
      );
    }
  }, [expiriesQuery.data]);

  // ── Chart setup ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;
    const w = containerRef.current.offsetWidth || 800;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      seriesRef.current = {};
    }

    const chart = createChart(containerRef.current, {
      width: w,
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: isDark ? "#a6adbb" : "#333",
      },
      grid: {
        vertLines: { color: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.08)" },
        horzLines: { color: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.08)" },
      },
      rightPriceScale: {
        borderColor: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
        visible: true,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      leftPriceScale: {
        borderColor: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
        visible: true,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000);
          const hh = d.getHours().toString().padStart(2, "0");
          const mm = d.getMinutes().toString().padStart(2, "0");
          if (days > 1) {
            const dd = d.getDate().toString().padStart(2, "0");
            const mo = (d.getMonth() + 1).toString().padStart(2, "0");
            return `${dd}/${mo} ${hh}:${mm}`;
          }
          return `${hh}:${mm}`;
        },
      },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const spot = chart.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
      priceScaleId: "left",
      title: "Spot",
      visible: showSpot,
    });
    const straddle = chart.addSeries(LineSeries, {
      color: "#a855f7",
      lineWidth: 2,
      priceScaleId: "right",
      title: "Straddle (CE+PE)",
      visible: showStraddle,
    });
    const synth = chart.addSeries(LineSeries, {
      color: isDark ? "#fbbf24" : "#d97706",
      lineWidth: 2,
      priceScaleId: "left",
      lineStyle: LineStyle.Dashed,
      title: "Synthetic Future",
      visible: showSyntheticFuture,
    });

    chartRef.current = chart;
    seriesRef.current = { spot, straddle, synth };

    if (dataRef.current) applyData(dataRef.current);

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, days]);

  useEffect(() => {
    const onResize = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.offsetWidth });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Always bind full data — visibility is toggled separately via applyOptions.
  const applyData = useCallback((data: StraddleData) => {
    const { spot, straddle, synth } = seriesRef.current;
    if (!spot || !straddle || !synth) return;
    const sorted = [...data.series].sort((a, b) => a.time - b.time);
    spot.setData(sorted.map((p) => ({ time: p.time as UTCTimestamp, value: p.spot })));
    straddle.setData(sorted.map((p) => ({ time: p.time as UTCTimestamp, value: p.straddle })));
    synth.setData(
      sorted.map((p) => ({ time: p.time as UTCTimestamp, value: p.synthetic_future }))
    );
    chartRef.current?.timeScale().fitContent();
  }, []);

  // Toggle visibility on the existing series — no data re-bind.
  useEffect(() => {
    seriesRef.current.spot?.applyOptions({ visible: showSpot });
  }, [showSpot]);
  useEffect(() => {
    seriesRef.current.straddle?.applyOptions({ visible: showStraddle });
  }, [showStraddle]);
  useEffect(() => {
    seriesRef.current.synth?.applyOptions({ visible: showSyntheticFuture });
  }, [showSyntheticFuture]);

  // ── Data loading ────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchStraddleChart({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
        interval,
        days,
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success" && resp.data) {
        dataRef.current = resp.data;
        setChartData(resp.data);
        applyData(resp.data);
      } else {
        toast.error(resp.message ?? "Failed to load straddle data");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to load straddle data";
      toast.error(msg);
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [expiry, interval, days, underlying, exchange, applyData]);

  useEffect(() => {
    if (expiry) loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiry, interval, days]);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-4">
          <CardTitle className="text-xl font-semibold">Straddle Chart</CardTitle>
          <p className="text-sm text-muted-foreground">
            Dynamic ATM Straddle (CE+PE at the live ATM strike per candle) plus the synthetic
            future (K + CE − PE) overlay. NSE / BSE derivatives.
          </p>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">Exchange</label>
              <select
                value={exchange}
                onChange={(e) => setExchange(e.target.value as StraddleExchange)}
                className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {STRADDLE_EXCHANGES.map((e) => (
                  <option key={e.value} value={e.value}>
                    {e.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">
                Underlying ({underlyings.length})
              </label>
              <UnderlyingCombobox
                value={underlying}
                options={underlyings}
                onChange={(sym) => {
                  setUnderlying(sym);
                  setExpiry("");
                }}
                loading={underlyingsQuery.isLoading}
                className="w-44"
              />
            </div>
            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">Expiry</label>
              <select
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                disabled={
                  expiriesQuery.isLoading ||
                  !expiriesQuery.data ||
                  expiriesQuery.data.status !== "success"
                }
                className="h-8 w-36 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
              >
                {expiriesQuery.data?.status === "success" && expiriesQuery.data.data.length > 0 ? (
                  expiriesQuery.data.data.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))
                ) : (
                  <option value="">{expiriesQuery.isLoading ? "Loading…" : "No expiries"}</option>
                )}
              </select>
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
            <Button
              variant="outline"
              className="h-8"
              onClick={loadData}
              disabled={!expiry || isLoading}
            >
              {isLoading ? "Loading…" : "Refresh"}
            </Button>
          </div>

          {chartData && (
            <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
              <Badge variant="secondary">Spot LTP: {chartData.underlying_ltp?.toFixed(2)}</Badge>
              <Badge variant="secondary">Expiry: {chartData.expiry_date}</Badge>
              <Badge variant="secondary">DTE: {chartData.days_to_expiry}</Badge>
              <Badge variant="secondary">Interval: {chartData.interval}</Badge>
              <Badge variant="secondary">{chartData.series.length} candles</Badge>
            </div>
          )}

          <div
            ref={containerRef}
            className="w-full rounded-lg border border-border/50"
            style={{ height: CHART_HEIGHT }}
          />

          {/* Toggleable legend below the chart — clickable chips. */}
          <div className="mt-3 flex items-center justify-center gap-4">
            <button
              type="button"
              onClick={() => setShowStraddle((v) => !v)}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${
                showStraddle ? "bg-muted font-medium" : "opacity-50 hover:opacity-75"
              }`}
            >
              <span className="inline-block h-0.5 w-5 rounded bg-purple-500" />
              Straddle
            </button>
            <button
              type="button"
              onClick={() => setShowSpot((v) => !v)}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${
                showSpot ? "bg-muted font-medium" : "opacity-50 hover:opacity-75"
              }`}
            >
              <span className="inline-block h-0.5 w-5 rounded bg-blue-500" />
              Spot
            </button>
            <button
              type="button"
              onClick={() => setShowSyntheticFuture((v) => !v)}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${
                showSyntheticFuture ? "bg-muted font-medium" : "opacity-50 hover:opacity-75"
              }`}
            >
              <span
                className="inline-block h-0 w-5 border-t-2 border-dashed"
                style={{ borderColor: isDark ? "#fbbf24" : "#d97706" }}
              />
              Synthetic Fut
            </button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
