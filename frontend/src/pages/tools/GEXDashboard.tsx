import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { fetchGEX, type GEXResponse } from "@/api/gex";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";
import Plot from "@/components/charts/Plot";

type GEXExchange = "NFO" | "BFO";

const GEX_EXCHANGES: ReadonlyArray<{ value: GEXExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

function formatNumber(num: number): string {
  if (!num) return "0";
  const sign = num < 0 ? "-" : "";
  const abs = Math.abs(num);
  if (abs >= 10_000_000) return `${sign}${(abs / 10_000_000).toFixed(1)}Cr`;
  if (abs >= 100_000) return `${sign}${(abs / 100_000).toFixed(1)}L`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${Math.round(abs).toString()}`;
}

function buildOIWallsPlot(
  data: GEXResponse | null,
  isDark: boolean,
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!data?.chain) return { data: [], layout: {} };

  const colors = {
    text: isDark ? "#e0e0e0" : "#333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
    ce: "#ef4444",
    pe: "#22c55e",
    atm: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.55)",
  };

  const xIndices = data.chain.map((_, i) => i);
  const tickLabels = data.chain.map((s) => s.strike.toString());
  const tickStep = Math.max(1, Math.floor(data.chain.length / 15));
  const tickVals = xIndices.filter((_, i) => i % tickStep === 0);
  const tickText = tickLabels.filter((_, i) => i % tickStep === 0);

  const traces: unknown[] = [
    {
      x: xIndices,
      y: data.chain.map((s) => s.ce_oi),
      type: "bar",
      name: "CE OI",
      marker: { color: colors.ce },
      hovertemplate: "Strike %{text}<br>CE OI: %{y:,}<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
    {
      x: xIndices,
      y: data.chain.map((s) => s.pe_oi),
      type: "bar",
      name: "PE OI",
      marker: { color: colors.pe },
      hovertemplate: "Strike %{text}<br>PE OI: %{y:,}<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
  ];

  const atmIdx = data.chain.findIndex((s) => s.strike === data.atm_strike);
  const shapes: unknown[] = [];
  const annotations: unknown[] = [];
  if (atmIdx >= 0) {
    shapes.push({
      type: "line",
      x0: atmIdx,
      x1: atmIdx,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.atm, width: 1.5, dash: "dash" },
    });
    annotations.push({
      x: atmIdx,
      y: 1,
      yref: "paper",
      text: `ATM ${data.atm_strike}`,
      showarrow: false,
      font: { color: colors.text, size: 11 },
      yanchor: "bottom",
    });
  }

  const layout: Record<string, unknown> = {
    title: {
      text: `${data.underlying} ${data.expiry_date} — OI Walls`,
      font: { color: colors.text, size: 14 },
    },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: colors.text, family: "system-ui, sans-serif" },
    barmode: "group",
    bargap: 0.15,
    hovermode: "x unified",
    showlegend: true,
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: -0.15, font: { color: colors.text, size: 11 } },
    margin: { l: 70, r: 30, t: 50, b: 70 },
    xaxis: {
      tickmode: "array",
      tickvals: tickVals,
      ticktext: tickText,
      title: { text: "Strike", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
      tickangle: -45,
    },
    yaxis: {
      title: { text: "Open Interest", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
    },
    annotations,
    shapes,
  };

  return { data: traces, layout };
}

function buildNetGexPlot(
  data: GEXResponse | null,
  isDark: boolean,
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!data?.chain) return { data: [], layout: {} };

  const colors = {
    text: isDark ? "#e0e0e0" : "#333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
    pos: "#3b82f6",
    neg: "#f97316",
    atm: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.55)",
  };

  const xIndices = data.chain.map((_, i) => i);
  const tickLabels = data.chain.map((s) => s.strike.toString());
  const tickStep = Math.max(1, Math.floor(data.chain.length / 15));
  const tickVals = xIndices.filter((_, i) => i % tickStep === 0);
  const tickText = tickLabels.filter((_, i) => i % tickStep === 0);

  const traces: unknown[] = [
    {
      x: xIndices,
      y: data.chain.map((s) => s.net_gex),
      type: "bar",
      name: "Net GEX",
      marker: {
        color: data.chain.map((s) => (s.net_gex >= 0 ? colors.pos : colors.neg)),
      },
      hovertemplate: "Strike %{text}<br>Net GEX: %{y:,.0f}<extra></extra>",
      text: tickLabels,
      textposition: "none",
    },
  ];

  const atmIdx = data.chain.findIndex((s) => s.strike === data.atm_strike);
  const shapes: unknown[] = [];
  const annotations: unknown[] = [];
  if (atmIdx >= 0) {
    shapes.push({
      type: "line",
      x0: atmIdx,
      x1: atmIdx,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.atm, width: 1.5, dash: "dash" },
    });
    annotations.push({
      x: atmIdx,
      y: 1,
      yref: "paper",
      text: `ATM ${data.atm_strike}`,
      showarrow: false,
      font: { color: colors.text, size: 11 },
      yanchor: "bottom",
    });
  }

  const layout: Record<string, unknown> = {
    title: {
      text: `${data.underlying} — Net Gamma Exposure per Strike`,
      font: { color: colors.text, size: 14 },
    },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: colors.text, family: "system-ui, sans-serif" },
    bargap: 0.15,
    hovermode: "x unified",
    showlegend: false,
    margin: { l: 70, r: 30, t: 50, b: 70 },
    xaxis: {
      tickmode: "array",
      tickvals: tickVals,
      ticktext: tickText,
      title: { text: "Strike", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
      tickangle: -45,
    },
    yaxis: {
      title: { text: "Net GEX (CE − PE)", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
      zeroline: true,
      zerolinecolor: colors.atm,
    },
    annotations,
    shapes,
  };

  return { data: traces, layout };
}

export default function GEXDashboard() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<GEXExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [autoRefresh, setAutoRefresh] = useState<boolean>(false);
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

  const [data, setData] = useState<GEXResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchGEX({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success") {
        setData(resp);
      } else {
        toast.error(resp.message ?? "Failed to fetch GEX data");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to fetch GEX data";
      toast.error(msg);
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [underlying, exchange, expiry]);

  useEffect(() => {
    if (expiry) fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiry]);

  // Auto-refresh every 30s.
  useEffect(() => {
    if (!autoRefresh || !expiry) return;
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [autoRefresh, expiry, fetchData]);

  const oiPlot = useMemo(() => buildOIWallsPlot(data, isDark), [data, isDark]);
  const gexPlot = useMemo(() => buildNetGexPlot(data, isDark), [data, isDark]);

  const plotConfig = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["pan2d", "select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
      responsive: true,
    }),
    []
  );

  // Top 5 |gamma| strikes for the table.
  const topGammaStrikes = useMemo(() => {
    if (!data?.chain) return [];
    return [...data.chain]
      .map((s) => ({
        strike: s.strike,
        ce_oi: s.ce_oi,
        pe_oi: s.pe_oi,
        ce_gex: s.ce_gex,
        pe_gex: s.pe_gex,
        net_gex: s.net_gex,
        abs_total_gex: Math.abs(s.ce_gex) + Math.abs(s.pe_gex),
      }))
      .sort((a, b) => b.abs_total_gex - a.abs_total_gex)
      .slice(0, 5);
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">GEX Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Gamma Exposure per strike (γ × OI × lot size). OI walls show where dealer hedging
            concentrates; net GEX flips sign at the gamma-flip level.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as GEXExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {GEX_EXCHANGES.map((e) => (
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
          <label className="flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border border-input bg-background px-2 text-xs">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto 30s
          </label>
          <Button
            variant="outline"
            className="h-8"
            onClick={fetchData}
            disabled={!expiry || isLoading}
          >
            {isLoading ? "Loading…" : "Refresh"}
          </Button>
        </div>
      </div>

      {data && data.status === "success" && (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Spot: {data.spot_price?.toFixed(1)}</Badge>
          {data.futures_price !== null && data.futures_price !== undefined && (
            <Badge variant="secondary">Futures: {data.futures_price.toFixed(1)}</Badge>
          )}
          <Badge variant="secondary">ATM: {data.atm_strike}</Badge>
          <Badge variant="secondary">Lot: {data.lot_size}</Badge>
          <Badge variant="secondary">PCR (OI): {data.pcr_oi?.toFixed(2)}</Badge>
          <Badge variant="secondary">CE OI: {formatNumber(data.total_ce_oi)}</Badge>
          <Badge variant="secondary">PE OI: {formatNumber(data.total_pe_oi)}</Badge>
          <Badge>Net GEX: {formatNumber(data.total_net_gex)}</Badge>
        </div>
      )}

      <Card>
        <CardContent className="p-2 sm:p-4">
          {isLoading && !data ? (
            <div className="flex h-[450px] items-center justify-center text-muted-foreground">
              Loading GEX…
            </div>
          ) : data?.chain && data.chain.length > 0 ? (
            <Plot
              data={oiPlot.data}
              layout={oiPlot.layout}
              config={plotConfig}
              useResizeHandler
              style={{ width: "100%", height: "450px" }}
            />
          ) : (
            <div className="flex h-[450px] items-center justify-center text-muted-foreground">
              {expiry ? "No GEX data." : "Select an underlying and expiry."}
            </div>
          )}
        </CardContent>
      </Card>

      {data?.chain && data.chain.length > 0 && (
        <Card>
          <CardContent className="p-2 sm:p-4">
            <Plot
              data={gexPlot.data}
              layout={gexPlot.layout}
              config={plotConfig}
              useResizeHandler
              style={{ width: "100%", height: "400px" }}
            />
          </CardContent>
        </Card>
      )}

      {topGammaStrikes.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="border-b px-4 py-2 text-sm font-medium">Top |γ| strikes</div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Strike</TableHead>
                  <TableHead className="text-right">CE OI</TableHead>
                  <TableHead className="text-right">PE OI</TableHead>
                  <TableHead className="text-right">CE GEX</TableHead>
                  <TableHead className="text-right">PE GEX</TableHead>
                  <TableHead className="text-right">Net GEX</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {topGammaStrikes.map((r) => (
                  <TableRow key={r.strike}>
                    <TableCell className="font-mono">
                      {r.strike}
                      {r.strike === data?.atm_strike && (
                        <span className="ml-2 text-xs text-muted-foreground">ATM</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(r.ce_oi)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(r.pe_oi)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(r.ce_gex)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(r.pe_gex)}
                    </TableCell>
                    <TableCell
                      className={`text-right font-mono tabular-nums ${
                        r.net_gex > 0
                          ? "text-blue-600 dark:text-blue-400"
                          : r.net_gex < 0
                          ? "text-orange-600 dark:text-orange-400"
                          : ""
                      }`}
                    >
                      {formatNumber(r.net_gex)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
