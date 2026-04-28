import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { fetchMaxPain, type MaxPainResponse } from "@/api/maxpain";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";
import Plot from "@/components/charts/Plot";

type MaxPainExchange = "NFO" | "BFO";

const MAX_PAIN_EXCHANGES: ReadonlyArray<{ value: MaxPainExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

function formatNumber(num: number): string {
  if (!num) return "0";
  if (num >= 10_000_000) return `${(num / 10_000_000).toFixed(1)}Cr`;
  if (num >= 100_000) return `${(num / 100_000).toFixed(1)}L`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return Math.round(num).toString();
}

function buildPlot(
  data: MaxPainResponse | null,
  isDark: boolean,
  expiryLabel: string
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!data?.chain) return { data: [], layout: {} };

  const chain = data.chain;
  const xIndices = chain.map((_, i) => i);
  const tickLabels = chain.map((s) => s.strike.toString());
  const painValues = chain.map((s) => s.total_pain);

  const tickStep = Math.max(1, Math.floor(chain.length / 15));
  const tickVals = xIndices.filter((_, i) => i % tickStep === 0);
  const tickText = tickLabels.filter((_, i) => i % tickStep === 0);

  const colors = {
    text: isDark ? "#e0e0e0" : "#333333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
    bar: isDark ? "#60a5fa" : "#3b82f6",
    barMin: isDark ? "#f59e0b" : "#d97706",
    atm: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.5)",
    maxPain: isDark ? "#f59e0b" : "#d97706",
    hoverBg: isDark ? "#1e293b" : "#ffffff",
    hoverText: isDark ? "#e0e0e0" : "#333333",
    hoverBorder: isDark ? "#475569" : "#e2e8f0",
  };

  const maxPainIndex = chain.findIndex((s) => s.strike === data.max_pain_strike);
  const atmIndex = chain.findIndex((s) => s.strike === data.atm_strike);

  // Highlight the max-pain bar in a different colour
  const barColors = chain.map((_, i) => (i === maxPainIndex ? colors.barMin : colors.bar));

  const traces: unknown[] = [
    {
      x: xIndices,
      y: painValues,
      type: "bar",
      name: "Total Pain",
      marker: { color: barColors },
      hovertemplate: "Strike %{text}<br>Pain: %{y:,.0f}<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
  ];

  const annotations: unknown[] = [];
  const shapes: unknown[] = [];

  if (atmIndex >= 0) {
    annotations.push({
      x: atmIndex,
      y: 1,
      yref: "paper",
      text: `ATM ${data.atm_strike}`,
      showarrow: false,
      font: { color: colors.text, size: 11 },
      yanchor: "bottom",
    });
    shapes.push({
      type: "line",
      x0: atmIndex,
      x1: atmIndex,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.atm, width: 1.2, dash: "dash" },
    });
  }

  if (maxPainIndex >= 0 && maxPainIndex !== atmIndex) {
    annotations.push({
      x: maxPainIndex,
      y: 0.92,
      yref: "paper",
      text: `Max Pain ${data.max_pain_strike}`,
      showarrow: false,
      font: { color: colors.maxPain, size: 12, weight: "bold" },
      yanchor: "bottom",
    });
    shapes.push({
      type: "line",
      x0: maxPainIndex,
      x1: maxPainIndex,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.maxPain, width: 1.8, dash: "dot" },
    });
  }

  const layout: Record<string, unknown> = {
    title: {
      text: `${data.underlying} ${expiryLabel} — Max Pain ${data.max_pain_strike}`,
      font: { color: colors.text, size: 14 },
    },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: colors.text, family: "system-ui, sans-serif" },
    bargap: 0.15,
    hovermode: "x unified",
    hoverlabel: {
      bgcolor: colors.hoverBg,
      font: { color: colors.hoverText, size: 12 },
      bordercolor: colors.hoverBorder,
    },
    showlegend: false,
    margin: { l: 70, r: 30, t: 50, b: 80 },
    xaxis: {
      tickmode: "array",
      tickvals: tickVals,
      ticktext: tickText,
      title: { text: "Strike Price", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
      tickangle: -45,
    },
    yaxis: {
      title: { text: "Total Pain", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
    },
    annotations,
    shapes,
  };

  return { data: traces, layout };
}

export default function MaxPain() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<MaxPainExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const requestIdRef = useRef(0);

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

  const [data, setData] = useState<MaxPainResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchMaxPain({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success") {
        setData(resp);
      } else {
        toast.error(resp.message ?? "Failed to fetch max pain");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to fetch max pain";
      toast.error(msg);
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [underlying, exchange, expiry]);

  useEffect(() => {
    if (expiry) fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiry]);

  const expiryLabel = useMemo(() => convertExpiryForApi(expiry), [expiry]);
  const plot = useMemo(() => buildPlot(data, isDark, expiryLabel), [data, isDark, expiryLabel]);

  const plotConfig = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: [
        "pan2d",
        "select2d",
        "lasso2d",
        "autoScale2d",
        "toggleSpikelines",
      ],
      responsive: true,
    }),
    []
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Max Pain</h1>
          <p className="text-sm text-muted-foreground">
            Strike where total option-writer payout to buyers is minimized — the settle level
            most adverse to net option buyers, derived from current OI per strike.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as MaxPainExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {MAX_PAIN_EXCHANGES.map((e) => (
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
              className="w-56"
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
              className="h-8 w-40 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
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
          <Button variant="outline" onClick={fetchData} disabled={!expiry || isLoading}>
            {isLoading ? "Loading…" : "Refresh"}
          </Button>
        </div>
      </div>

      {data && data.status === "success" && (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Spot: {data.spot_price?.toFixed(1)}</Badge>
          <Badge variant="secondary">ATM: {data.atm_strike}</Badge>
          <Badge>Max Pain: {data.max_pain_strike}</Badge>
          <Badge variant="secondary">PCR (OI): {data.pcr_oi?.toFixed(2)}</Badge>
          <Badge variant="secondary">Total CE OI: {formatNumber(data.total_ce_oi)}</Badge>
          <Badge variant="secondary">Total PE OI: {formatNumber(data.total_pe_oi)}</Badge>
        </div>
      )}

      <Card>
        <CardContent className="p-2 sm:p-4">
          {isLoading && !data ? (
            <div className="flex h-[500px] items-center justify-center text-muted-foreground">
              Loading max pain…
            </div>
          ) : data?.chain && data.chain.length > 0 ? (
            <Plot
              data={plot.data}
              layout={plot.layout}
              config={plotConfig}
              useResizeHandler
              style={{ width: "100%", height: "500px" }}
            />
          ) : (
            <div className="flex h-[500px] items-center justify-center text-muted-foreground">
              {expiry
                ? "No max pain data available."
                : "Select an underlying and expiry to view max pain."}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
