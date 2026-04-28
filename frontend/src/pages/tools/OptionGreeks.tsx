import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import {
  fetchIVChart,
  type IVChartData,
  type IVChartPoint,
} from "@/api/ivchart";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";

type GreeksExchange = "NFO" | "BFO";

const GREEKS_EXCHANGES: ReadonlyArray<{ value: GreeksExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

const INTERVALS: ReadonlyArray<string> = ["1m", "3m", "5m", "10m", "15m", "30m", "1h"];

const DAY_RANGES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: "1 Day" },
  { value: 5, label: "5 Days" },
  { value: 10, label: "10 Days" },
  { value: 15, label: "15 Days" },
];

const METRICS = ["iv", "delta", "theta", "vega", "gamma"] as const;
type MetricKey = (typeof METRICS)[number];

const METRIC_CONFIG: Record<
  MetricKey,
  { label: string; ceTitle: string; peTitle: string; formatter: (v: number) => string }
> = {
  iv: {
    label: "IV",
    ceTitle: "CE IV",
    peTitle: "PE IV",
    formatter: (v) => `${v.toFixed(2)}%`,
  },
  delta: {
    label: "Delta",
    ceTitle: "CE Delta",
    peTitle: "PE Delta",
    formatter: (v) => v.toFixed(4),
  },
  theta: {
    label: "Theta",
    ceTitle: "CE Theta",
    peTitle: "PE Theta",
    formatter: (v) => v.toFixed(4),
  },
  vega: {
    label: "Vega",
    ceTitle: "CE Vega",
    peTitle: "PE Vega",
    formatter: (v) => v.toFixed(4),
  },
  gamma: {
    label: "Gamma",
    ceTitle: "CE Gamma",
    peTitle: "PE Gamma",
    formatter: (v) => v.toFixed(6),
  },
};

const CHART_HEIGHT = 350;

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

interface ChartInstance {
  chart: IChartApi;
  series: ISeriesApi<"Line">;
}

export default function OptionGreeks() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  // Control state
  const [exchange, setExchange] = useState<GreeksExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [interval, setInterval] = useState<string>("5m");
  const [days, setDays] = useState<number>(1);
  const [activeTab, setActiveTab] = useState<MetricKey>("iv");
  const [chartData, setChartData] = useState<IVChartData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const requestIdRef = useRef(0);

  // Underlyings (DB-backed, with fallback)
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

  // Expiries
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

  // Chart refs — one DOM container + one chart instance per (metric, type) pair.
  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const chartsRef = useRef<Map<string, ChartInstance>>(new Map());
  const chartDataRef = useRef<IVChartData | null>(null);

  // Stable ref callbacks per chart slot.
  const refCallbacks = useMemo(() => {
    const cbs: Record<string, (el: HTMLDivElement | null) => void> = {};
    for (const metric of METRICS) {
      for (const type of ["ce", "pe"]) {
        const key = `${metric}-${type}`;
        cbs[key] = (el: HTMLDivElement | null) => {
          if (el) containerRefs.current.set(key, el);
          else containerRefs.current.delete(key);
        };
      }
    }
    return cbs;
  }, []);

  // ── Chart setup ────────────────────────────────────────────────

  const makeChartOptions = useCallback(
    (width: number) => ({
      width,
      height: CHART_HEIGHT,
      layout: {
        background: { type: ColorType.Solid as const, color: "transparent" },
        textColor: isDark ? "#a6adbb" : "#333",
      },
      grid: {
        vertLines: {
          color: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.1)",
          style: 1 as const,
          visible: true,
        },
        horzLines: {
          color: isDark ? "rgba(166,173,187,0.1)" : "rgba(0,0,0,0.1)",
          style: 1 as const,
          visible: true,
        },
      },
      rightPriceScale: {
        borderColor: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: isDark ? "rgba(166,173,187,0.2)" : "rgba(0,0,0,0.2)",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          // Force IST display regardless of user machine TZ.
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
        vertLine: {
          width: 1 as const,
          color: isDark ? "rgba(166,173,187,0.5)" : "rgba(0,0,0,0.3)",
          style: 2 as const,
          labelVisible: false,
        },
        horzLine: {
          width: 1 as const,
          color: isDark ? "rgba(166,173,187,0.5)" : "rgba(0,0,0,0.3)",
          style: 2 as const,
          labelBackgroundColor: isDark ? "#1f2937" : "#2563eb",
        },
      },
    }),
    [isDark, days]
  );

  const addWatermark = useCallback(
    (container: HTMLDivElement) => {
      const el = document.createElement("div");
      el.style.cssText = `position:absolute;z-index:2;font-family:Arial,sans-serif;font-size:28px;font-weight:bold;user-select:none;pointer-events:none;color:${isDark ? "rgba(166,173,187,0.12)" : "rgba(0,0,0,0.06)"}`;
      el.textContent = "OpenBull";
      container.appendChild(el);
      setTimeout(() => {
        el.style.left = `${container.offsetWidth / 2 - el.offsetWidth / 2}px`;
        el.style.top = `${container.offsetHeight / 2 - el.offsetHeight / 2}px`;
      }, 0);
    },
    [isDark]
  );

  // Update all 10 charts from a fresh response.
  const updateAllCharts = useCallback((data: IVChartData) => {
    for (const metric of METRICS) {
      for (const optType of ["CE", "PE"] as const) {
        const key = `${metric}-${optType.toLowerCase()}`;
        const inst = chartsRef.current.get(key);
        if (!inst) continue;

        const seriesData = data.series.find((s) => s.option_type === optType);
        if (!seriesData) continue;

        const points = seriesData.iv_data
          .filter((p: IVChartPoint) => p[metric] !== null && p[metric] !== undefined)
          .map((p: IVChartPoint) => ({
            time: p.time as UTCTimestamp,
            value: p[metric] as number,
          }))
          .sort((a, b) => (a.time as number) - (b.time as number));

        inst.series.setData(points);
        inst.chart.timeScale().fitContent();
      }
    }
  }, []);

  // Init / re-init all 10 charts when theme or days change (which affect options).
  useEffect(() => {
    // Tear down previous charts
    for (const [, inst] of chartsRef.current) inst.chart.remove();
    chartsRef.current.clear();
    for (const [, el] of containerRefs.current) el.innerHTML = "";

    const refContainer = containerRefs.current.get("iv-ce");
    const fallbackW = refContainer?.offsetWidth || 500;

    for (const metric of METRICS) {
      for (const type of ["ce", "pe"] as const) {
        const key = `${metric}-${type}`;
        const container = containerRefs.current.get(key);
        if (!container) continue;

        const w = container.offsetWidth > 0 ? container.offsetWidth : fallbackW;
        const color = type === "ce" ? "#22c55e" : "#ef4444";
        const cfg = METRIC_CONFIG[metric];
        const title = type === "ce" ? cfg.ceTitle : cfg.peTitle;

        const chart = createChart(container, makeChartOptions(w));
        const series = chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          priceScaleId: "right",
          title,
          priceFormat: { type: "custom" as const, formatter: cfg.formatter, minMove: 0.000001 },
        });

        addWatermark(container);
        chartsRef.current.set(key, { chart, series });
      }
    }

    if (chartDataRef.current) updateAllCharts(chartDataRef.current);

    return () => {
      for (const [, inst] of chartsRef.current) inst.chart.remove();
      chartsRef.current.clear();
    };
  }, [makeChartOptions, addWatermark, updateAllCharts]);

  // Window resize
  useEffect(() => {
    const onResize = () => {
      for (const [key, inst] of chartsRef.current) {
        const c = containerRefs.current.get(key);
        if (c && c.offsetWidth > 0) inst.chart.applyOptions({ width: c.offsetWidth });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── Data loading ───────────────────────────────────────────────

  const loadData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchIVChart({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
        interval,
        days,
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success" && resp.data) {
        chartDataRef.current = resp.data;
        setChartData(resp.data);
        updateAllCharts(resp.data);
      } else {
        toast.error(resp.message ?? "Failed to load IV chart");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to load IV chart";
      toast.error(msg);
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [expiry, interval, days, underlying, exchange, updateAllCharts]);

  useEffect(() => {
    if (expiry) loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiry, interval, days]);

  // ── Tab change with resize ─────────────────────────────────────

  const handleTabChange = (value: string) => {
    setActiveTab(value as MetricKey);
    requestAnimationFrame(() => {
      for (const type of ["ce", "pe"]) {
        const key = `${value}-${type}`;
        const inst = chartsRef.current.get(key);
        const c = containerRefs.current.get(key);
        if (inst && c && c.offsetWidth > 0) {
          inst.chart.applyOptions({ width: c.offsetWidth });
          inst.chart.timeScale().fitContent();
        }
      }
    });
  };

  // ── Display helpers ────────────────────────────────────────────

  const getLatestValue = (type: "CE" | "PE", metric: MetricKey): string => {
    if (!chartData) return "—";
    const s = chartData.series.find((x) => x.option_type === type);
    if (!s) return "—";
    const valid = s.iv_data.filter((p) => p[metric] !== null && p[metric] !== undefined);
    if (valid.length === 0) return "—";
    const v = valid[valid.length - 1][metric];
    if (v === null || v === undefined) return "—";
    return METRIC_CONFIG[metric].formatter(v);
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-4">
          <CardTitle className="text-xl font-semibold">Option Greeks</CardTitle>
          <p className="text-sm text-muted-foreground">
            Black-76 IV and Δ/Γ/Θ/V at every candle close for the ATM CE & PE — pulled from
            OHLCV history and computed in-process. NSE / BSE derivatives only.
          </p>
        </CardHeader>
        <CardContent>
          {/* Controls */}
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="block text-xs text-muted-foreground">Exchange</label>
              <select
                value={exchange}
                onChange={(e) => setExchange(e.target.value as GreeksExchange)}
                className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {GREEKS_EXCHANGES.map((e) => (
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

          {/* Info bar */}
          {chartData && (
            <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
              <div>
                <span className="text-muted-foreground">ATM Strike: </span>
                <span className="font-medium">{chartData.atm_strike}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Spot: </span>
                <span className="font-medium">{chartData.underlying_ltp?.toFixed(2)}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-full bg-green-500" />
                <span className="text-muted-foreground">CE:</span>
                <span className="font-medium">{chartData.ce_symbol}</span>
                <span className="ml-1 font-medium text-green-600 dark:text-green-400">
                  {getLatestValue("CE", "iv")}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
                <span className="text-muted-foreground">PE:</span>
                <span className="font-medium">{chartData.pe_symbol}</span>
                <span className="ml-1 font-medium text-red-600 dark:text-red-400">
                  {getLatestValue("PE", "iv")}
                </span>
              </div>
            </div>
          )}

          {/* Metric tabs */}
          <Tabs value={activeTab} onValueChange={(v) => handleTabChange(v as MetricKey)}>
            <TabsList className="grid w-full max-w-md grid-cols-5">
              {METRICS.map((m) => (
                <TabsTrigger key={m} value={m}>
                  {METRIC_CONFIG[m].label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          {/* Chart panels — all 10 always rendered, only active tab visible */}
          <div className="mt-4">
            {METRICS.map((metric) => (
              <div
                key={metric}
                className={
                  activeTab !== metric ? "pointer-events-none h-0 overflow-hidden" : ""
                }
              >
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  {/* CE chart */}
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-sm font-medium text-green-600 dark:text-green-400">
                        {chartData?.ce_symbol || "CE"} {METRIC_CONFIG[metric].label}
                      </span>
                      <span className="font-mono text-sm tabular-nums text-muted-foreground">
                        {getLatestValue("CE", metric)}
                      </span>
                    </div>
                    <div
                      ref={refCallbacks[`${metric}-ce`]}
                      className="relative w-full rounded-lg border border-border/50"
                      style={{ height: CHART_HEIGHT }}
                    />
                  </div>
                  {/* PE chart */}
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-sm font-medium text-red-600 dark:text-red-400">
                        {chartData?.pe_symbol || "PE"} {METRIC_CONFIG[metric].label}
                      </span>
                      <span className="font-mono text-sm tabular-nums text-muted-foreground">
                        {getLatestValue("PE", metric)}
                      </span>
                    </div>
                    <div
                      ref={refCallbacks[`${metric}-pe`]}
                      className="relative w-full rounded-lg border border-border/50"
                      style={{ height: CHART_HEIGHT }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
