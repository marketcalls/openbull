import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { fetchVolSurface, type VolSurfaceData } from "@/api/volsurface";
import { FALLBACK_UNDERLYINGS, type UnderlyingOption } from "@/types/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTheme } from "@/contexts/ThemeContext";
import Plot3D from "@/components/charts/Plot3D";

type SurfaceExchange = "NFO" | "BFO";

const SURFACE_EXCHANGES: ReadonlyArray<{ value: SurfaceExchange; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

const STRIKE_COUNTS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 5, label: "5" },
  { value: 10, label: "10" },
  { value: 15, label: "15" },
  { value: 20, label: "20" },
];

const MAX_EXPIRIES = 8;
const DEFAULT_AUTO_PICK = 4;

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

function hasAnyValue(surface: (number | null)[][]): boolean {
  for (const row of surface) {
    for (const v of row) {
      if (v !== null && v !== undefined && !Number.isNaN(v)) return true;
    }
  }
  return false;
}

function buildPlot(
  data: VolSurfaceData | null,
  isDark: boolean,
): { data: unknown[]; layout: Record<string, unknown> } {
  if (!data?.surface || data.surface.length === 0) return { data: [], layout: {} };
  if (!hasAnyValue(data.surface)) return { data: [], layout: {} };

  const colors = {
    text: isDark ? "#e0e0e0" : "#333",
    grid: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.1)",
    axisBg: isDark ? "rgba(20,20,28,0.5)" : "rgba(255,255,255,0.5)",
  };

  const yIndices = data.expiries.map((_, i) => i);
  const yLabels = data.expiries.map((e) => `${e.date} (${e.dte}d)`);

  // Customdata mirrors openalgo: a 2D array matching surface shape so each
  // cell's hover shows its row's expiry date.
  const customdata = data.surface.map((_, i) =>
    Array(data.strikes.length).fill(data.expiries[i]?.date ?? ""),
  );

  const traces: unknown[] = [
    {
      type: "surface",
      x: data.strikes,
      y: yIndices,
      z: data.surface,
      customdata,
      colorscale: isDark ? "Viridis" : "YlOrRd",
      showscale: true,
      opacity: 0.95,
      colorbar: {
        title: { text: "IV %", font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text },
        outlinewidth: 0,
        len: 0.6,
      },
      hovertemplate:
        "Strike: %{x}<br>Expiry: %{customdata}<br>IV: %{z:.2f}%<extra></extra>",
    },
  ];

  const layout: Record<string, unknown> = {
    title: {
      text: `${data.underlying} — Volatility Surface (Spot ${data.underlying_ltp?.toFixed(1)})`,
      font: { color: colors.text, size: 14 },
    },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: colors.text, family: "system-ui, sans-serif" },
    margin: { l: 0, r: 0, t: 50, b: 0 },
    scene: {
      // Manual aspect ratio is critical — without it Plotly autoscales each
      // axis to fit, which collapses the Z (IV%) range to a paper-thin sheet
      // because strike values are ~1000x larger than IV values numerically.
      aspectmode: "manual",
      aspectratio: { x: 2, y: 1.2, z: 0.8 },
      camera: { eye: { x: 1.6, y: -1.6, z: 0.7 } },
      bgcolor: "rgba(0,0,0,0)",
      xaxis: {
        title: { text: "Strike", font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text, size: 10 },
        backgroundcolor: colors.axisBg,
        gridcolor: colors.grid,
        color: colors.text,
      },
      yaxis: {
        title: { text: "Expiry", font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text, size: 10 },
        backgroundcolor: colors.axisBg,
        gridcolor: colors.grid,
        color: colors.text,
        tickmode: "array",
        tickvals: yIndices,
        ticktext: yLabels,
      },
      zaxis: {
        title: { text: "IV (%)", font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text, size: 10 },
        backgroundcolor: colors.axisBg,
        gridcolor: colors.grid,
        color: colors.text,
      },
    },
  };

  return { data: traces, layout };
}

export default function VolSurface() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [exchange, setExchange] = useState<SurfaceExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [strikeCount, setStrikeCount] = useState<number>(10);
  const [selectedExpiries, setSelectedExpiries] = useState<string[]>([]);
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
      setSelectedExpiries([]);
    }
  }, [underlyings, underlying]);

  const expiriesQuery = useQuery({
    queryKey: ["expiries", underlying, exchange],
    queryFn: () => fetchExpiries({ symbol: underlying, exchange, instrumenttype: "options" }),
    enabled: !!underlying && !!exchange,
    retry: 0,
  });
  const availableExpiries = useMemo<string[]>(() => {
    if (expiriesQuery.data?.status === "success") return expiriesQuery.data.data;
    return [];
  }, [expiriesQuery.data]);

  // Auto-pick first N expiries when the list refreshes.
  useEffect(() => {
    if (availableExpiries.length === 0) {
      setSelectedExpiries([]);
      return;
    }
    setSelectedExpiries((prev) => {
      const stillValid = prev.filter((e) => availableExpiries.includes(e));
      if (stillValid.length > 0) return stillValid;
      return availableExpiries.slice(0, DEFAULT_AUTO_PICK);
    });
  }, [availableExpiries]);

  const toggleExpiry = useCallback((expiry: string) => {
    setSelectedExpiries((prev) => {
      if (prev.includes(expiry)) return prev.filter((e) => e !== expiry);
      if (prev.length >= MAX_EXPIRIES) return prev;
      return [...prev, expiry];
    });
  }, []);

  const [data, setData] = useState<VolSurfaceData | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (selectedExpiries.length === 0) return;
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    try {
      const resp = await fetchVolSurface({
        underlying,
        exchange,
        expiry_dates: selectedExpiries.map(convertExpiryForApi),
        strike_count: strikeCount,
      });
      if (requestIdRef.current !== requestId) return;
      if (resp.status === "success" && resp.data) {
        setData(resp.data);
      } else {
        toast.error(resp.message ?? "Failed to fetch vol surface");
      }
    } catch (e) {
      if (requestIdRef.current !== requestId) return;
      const msg =
        (e as { response?: { data?: { message?: string } }; message?: string })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Failed to fetch vol surface";
      toast.error(msg);
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [underlying, exchange, selectedExpiries, strikeCount]);

  const plot = useMemo(() => buildPlot(data, isDark), [data, isDark]);

  const plotConfig = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      responsive: true,
    }),
    []
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Volatility Surface</h1>
          <p className="text-sm text-muted-foreground">
            3D IV surface across strikes × expiries — OTM convention (CE IV for K≥ATM, PE IV
            for K&lt;ATM). Pick up to {MAX_EXPIRIES} expiries; first {DEFAULT_AUTO_PICK} auto-selected.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as SurfaceExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {SURFACE_EXCHANGES.map((e) => (
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
                setSelectedExpiries([]);
              }}
              loading={underlyingsQuery.isLoading}
              className="w-44"
            />
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Strikes ± ATM</label>
            <select
              value={String(strikeCount)}
              onChange={(e) => setStrikeCount(Number(e.target.value))}
              className="h-8 w-20 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {STRIKE_COUNTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="outline"
            className="h-8"
            onClick={fetchData}
            disabled={selectedExpiries.length === 0 || isLoading}
          >
            {isLoading ? "Loading…" : "Load Surface"}
          </Button>
        </div>

        {availableExpiries.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <span className="text-xs text-muted-foreground">
              Expiries ({selectedExpiries.length}/{MAX_EXPIRIES}):
            </span>
            {availableExpiries.map((e) => {
              const active = selectedExpiries.includes(e);
              return (
                <button
                  key={e}
                  type="button"
                  onClick={() => toggleExpiry(e)}
                  className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-input bg-background text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {e}
                </button>
              );
            })}
          </div>
        )}

        {data && (
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">Spot: {data.underlying_ltp?.toFixed(1)}</Badge>
            <Badge variant="secondary">ATM: {data.atm_strike}</Badge>
            <Badge variant="secondary">{data.expiries.length} expiries</Badge>
            <Badge variant="secondary">{data.strikes.length} strikes</Badge>
          </div>
        )}
      </div>

      <Card>
        <CardContent className="p-2 sm:p-4">
          {isLoading && !data ? (
            <div className="flex h-[700px] items-center justify-center text-muted-foreground">
              Computing surface…
            </div>
          ) : data?.surface && data.surface.length > 0 && plot.data.length > 0 ? (
            <Plot3D
              data={plot.data}
              layout={plot.layout}
              config={plotConfig}
              useResizeHandler
              style={{ width: "100%", height: "700px" }}
            />
          ) : (
            <div className="flex h-[700px] flex-col items-center justify-center gap-2 text-muted-foreground">
              {selectedExpiries.length === 0 ? (
                <span>Select expiries and click Load Surface.</span>
              ) : data?.surface && data.surface.length > 0 ? (
                <>
                  <span>Surface has no IV values for the selected expiries.</span>
                  <span className="text-xs">
                    The option chain has no live quotes — likely outside market
                    hours. Try during NSE/BSE session.
                  </span>
                </>
              ) : (
                <span>No surface data.</span>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
