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
import { fetchIVSmile, type IVSmileResponse } from "@/api/ivsmile";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";
import Plot from "@/components/charts/Plot";

type SmileExchange = "NFO" | "BFO";

const SMILE_EXCHANGES: ReadonlyArray<{ value: SmileExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

function buildPlot(
  data: IVSmileResponse | null,
  isDark: boolean,
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!data?.chain) return { data: [], layout: {} };

  const colors = {
    text: isDark ? "#e0e0e0" : "#333333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.08)",
    ce: "#3b82f6",
    pe: "#ef4444",
    spot: isDark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.55)",
    atm: isDark ? "#22c55e" : "#16a34a",
    hoverBg: isDark ? "#1e293b" : "#ffffff",
    hoverText: isDark ? "#e0e0e0" : "#333333",
    hoverBorder: isDark ? "#475569" : "#e2e8f0",
  };

  const strikes = data.chain.map((s) => s.strike);
  const ceIv = data.chain.map((s) => s.ce_iv);
  const peIv = data.chain.map((s) => s.pe_iv);

  const traces: unknown[] = [
    {
      x: strikes,
      y: ceIv,
      type: "scatter",
      mode: "lines+markers",
      name: "Call IV",
      line: { color: colors.ce, width: 2 },
      marker: { color: colors.ce, size: 6 },
      connectgaps: true,
      hovertemplate: "Strike %{x}<br>CE IV: %{y:.2f}%<extra></extra>",
    },
    {
      x: strikes,
      y: peIv,
      type: "scatter",
      mode: "lines+markers",
      name: "Put IV",
      line: { color: colors.pe, width: 2 },
      marker: { color: colors.pe, size: 6 },
      connectgaps: true,
      hovertemplate: "Strike %{x}<br>PE IV: %{y:.2f}%<extra></extra>",
    },
  ];

  const shapes: unknown[] = [];
  const annotations: unknown[] = [];

  if (data.spot_price) {
    shapes.push({
      type: "line",
      x0: data.spot_price,
      x1: data.spot_price,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.spot, width: 1.2, dash: "dash" },
    });
    annotations.push({
      x: data.spot_price,
      y: 1,
      yref: "paper",
      text: `Spot ${data.spot_price.toFixed(1)}`,
      showarrow: false,
      font: { color: colors.text, size: 11 },
      yanchor: "bottom",
    });
  }

  if (data.atm_strike && data.atm_strike !== data.spot_price) {
    shapes.push({
      type: "line",
      x0: data.atm_strike,
      x1: data.atm_strike,
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: colors.atm, width: 1.2, dash: "dot" },
    });
    annotations.push({
      x: data.atm_strike,
      y: 0.92,
      yref: "paper",
      text: `ATM ${data.atm_strike}`,
      showarrow: false,
      font: { color: colors.atm, size: 11 },
      yanchor: "bottom",
    });
  }

  const layout: Record<string, unknown> = {
    title: {
      text: `${data.underlying} ${data.expiry_date} — IV Smile`,
      font: { color: colors.text, size: 14 },
    },
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
    margin: { l: 60, r: 30, t: 50, b: 60 },
    xaxis: {
      title: { text: "Strike Price", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
    },
    yaxis: {
      title: { text: "Implied Volatility (%)", font: { color: colors.text, size: 12 } },
      tickfont: { color: colors.text, size: 10 },
      gridcolor: colors.grid,
    },
    shapes,
    annotations,
  };

  return { data: traces, layout };
}

export default function IVSmile() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<SmileExchange>("NFO");
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

  const [data, setData] = useState<IVSmileResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!expiry) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchIVSmile({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success") {
        setData(resp);
      } else {
        toast.error(resp.message ?? "Failed to fetch IV Smile");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to fetch IV Smile";
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

  const plot = useMemo(() => buildPlot(data, isDark), [data, isDark]);

  const plotConfig = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["pan2d", "select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
      responsive: true,
    }),
    []
  );

  // ATM ±5% strikes for the table.
  const tableRows = useMemo(() => {
    if (!data?.chain || !data.atm_strike) return [];
    const lo = data.atm_strike * 0.95;
    const hi = data.atm_strike * 1.05;
    return data.chain
      .filter((r) => r.strike >= lo && r.strike <= hi)
      .map((r) => ({
        ...r,
        diff:
          r.ce_iv !== null && r.pe_iv !== null ? +(r.pe_iv - r.ce_iv).toFixed(2) : null,
      }));
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">IV Smile</h1>
          <p className="text-sm text-muted-foreground">
            Call vs Put implied volatility across strikes for a single expiry — Black-76 IV from
            live option chain. Skew = Put IV at ATM-5% minus Call IV at ATM+5%.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as SmileExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {SMILE_EXCHANGES.map((e) => (
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
          <Badge variant="secondary">ATM: {data.atm_strike}</Badge>
          <Badge variant="secondary">
            ATM IV: {data.atm_iv !== null ? `${data.atm_iv.toFixed(2)}%` : "—"}
          </Badge>
          <Badge variant="secondary">
            Skew: {data.skew !== null ? `${data.skew.toFixed(2)}%` : "—"}
          </Badge>
        </div>
      )}

      <Card>
        <CardContent className="p-2 sm:p-4">
          {isLoading && !data ? (
            <div className="flex h-[500px] items-center justify-center text-muted-foreground">
              Loading IV smile…
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
              {expiry ? "No IV data available." : "Select an underlying and expiry."}
            </div>
          )}
        </CardContent>
      </Card>

      {tableRows.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Strike</TableHead>
                  <TableHead className="text-right">Call IV (%)</TableHead>
                  <TableHead className="text-right">Put IV (%)</TableHead>
                  <TableHead className="text-right">PE − CE</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tableRows.map((r) => (
                  <TableRow key={r.strike}>
                    <TableCell className="font-mono">
                      {r.strike}
                      {r.strike === data?.atm_strike && (
                        <span className="ml-2 text-xs text-muted-foreground">ATM</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {r.ce_iv !== null ? r.ce_iv.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {r.pe_iv !== null ? r.pe_iv.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell
                      className={`text-right font-mono tabular-nums ${
                        r.diff !== null && r.diff > 0
                          ? "text-red-600 dark:text-red-400"
                          : r.diff !== null && r.diff < 0
                          ? "text-green-600 dark:text-green-400"
                          : ""
                      }`}
                    >
                      {r.diff !== null ? r.diff.toFixed(2) : "—"}
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
