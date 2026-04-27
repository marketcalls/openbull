import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { fetchOITracker, type OITrackerResponse } from "@/api/oitracker";
import {
  FALLBACK_UNDERLYINGS,
  FNO_EXCHANGES,
  type UnderlyingOption,
} from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";
import Plot from "@/components/charts/Plot";

type FnoExchange = "NFO" | "BFO";

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
  oi: OITrackerResponse | null,
  isDark: boolean,
  expiryLabel: string
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!oi?.chain) return { data: [], layout: {} };

  const lotSize = oi.lot_size || 1;
  const chain = oi.chain;
  const xIndices = chain.map((_, i) => i);
  const tickLabels = chain.map((s) => s.strike.toString());
  const ceLots = chain.map((s) => Math.round(s.ce_oi / lotSize));
  const peLots = chain.map((s) => Math.round(s.pe_oi / lotSize));

  const tickStep = Math.max(1, Math.floor(chain.length / 15));
  const tickVals = xIndices.filter((_, i) => i % tickStep === 0);
  const tickText = tickLabels.filter((_, i) => i % tickStep === 0);

  const colors = {
    text: isDark ? "#e0e0e0" : "#333333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
    ce: "#ef4444",
    pe: "#22c55e",
    atm: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.5)",
    hoverBg: isDark ? "#1e293b" : "#ffffff",
    hoverText: isDark ? "#e0e0e0" : "#333333",
    hoverBorder: isDark ? "#475569" : "#e2e8f0",
  };

  const data: unknown[] = [
    {
      x: xIndices,
      y: ceLots,
      type: "bar",
      name: "Call OI (lots)",
      marker: { color: colors.ce },
      hovertemplate: "Strike %{text}<br>CE OI: %{y:,} lots<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
    {
      x: xIndices,
      y: peLots,
      type: "bar",
      name: "Put OI (lots)",
      marker: { color: colors.pe },
      hovertemplate: "Strike %{text}<br>PE OI: %{y:,} lots<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
  ];

  const atmIndex = oi.atm_strike
    ? chain.findIndex((s) => s.strike === oi.atm_strike)
    : -1;

  const annotations =
    atmIndex >= 0
      ? [
          {
            x: atmIndex,
            y: 1,
            yref: "paper",
            text: `${oi.underlying} ATM ${oi.atm_strike}`,
            showarrow: false,
            font: { color: colors.text, size: 12 },
            yanchor: "bottom",
          },
        ]
      : [];

  const shapes =
    atmIndex >= 0
      ? [
          {
            type: "line",
            x0: atmIndex,
            x1: atmIndex,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: colors.atm, width: 1.5, dash: "dash" },
          },
        ]
      : [];

  const layout: Record<string, unknown> = {
    title: {
      text: `${oi.underlying} ${expiryLabel} — current OI`,
      font: { color: colors.text, size: 14 },
    },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: colors.text, family: "system-ui, sans-serif" },
    barmode: "group",
    bargap: 0.15,
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
      y: -0.15,
      font: { color: colors.text, size: 11 },
    },
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
      title: { text: "Open Interest (lots)", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
    },
    annotations,
    shapes,
  };

  return { data, layout };
}

export default function OITracker() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<FnoExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
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

  // Snap underlying to first available when the list changes.
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

  // OI tracker payload
  const [oiData, setOiData] = useState<OITrackerResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchOITracker({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success") {
        setOiData(resp);
      } else {
        toast.error(resp.message ?? "Failed to fetch OI data");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to fetch OI data";
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
  const plot = useMemo(() => buildPlot(oiData, isDark, expiryLabel), [oiData, isDark, expiryLabel]);

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
          <h1 className="text-2xl font-bold tracking-tight">OI Tracker</h1>
          <p className="text-sm text-muted-foreground">
            Snapshot of CE vs PE Open Interest per strike around ATM, with PCR and futures
            price for the same expiry.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as FnoExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {FNO_EXCHANGES.map((e) => (
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

      {oiData && oiData.status === "success" && (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Spot: {oiData.spot_price?.toFixed(1)}</Badge>
          {oiData.futures_price !== null && oiData.futures_price !== undefined && (
            <Badge variant="secondary">Futures: {oiData.futures_price.toFixed(1)}</Badge>
          )}
          <Badge variant="secondary">Lot: {oiData.lot_size}</Badge>
          <Badge variant="secondary">PCR (OI): {oiData.pcr_oi?.toFixed(2)}</Badge>
          <Badge variant="secondary">PCR (Vol): {oiData.pcr_volume?.toFixed(2)}</Badge>
          <Badge variant="secondary">ATM: {oiData.atm_strike}</Badge>
          <Badge variant="secondary">Total CE OI: {formatNumber(oiData.total_ce_oi)}</Badge>
          <Badge variant="secondary">Total PE OI: {formatNumber(oiData.total_pe_oi)}</Badge>
        </div>
      )}

      <Card>
        <CardContent className="p-2 sm:p-4">
          {isLoading && !oiData ? (
            <div className="flex h-[500px] items-center justify-center text-muted-foreground">
              Loading OI data…
            </div>
          ) : oiData?.chain && oiData.chain.length > 0 ? (
            <Plot
              data={plot.data}
              layout={plot.layout}
              config={plotConfig}
              useResizeHandler
              style={{ width: "100%", height: "500px" }}
            />
          ) : (
            <div className="flex h-[500px] items-center justify-center text-muted-foreground">
              {expiry ? "No OI data available." : "Select an underlying and expiry to view OI data."}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
