/**
 * Strategy Portfolio — list view of saved strategies with live aggregate P&L.
 *
 * Architecture:
 *   - Single useQuery to /web/strategies (filtered by mode + status).
 *   - Client-side filter for `underlying` (small enough not to need a
 *     DB filter in v1; the backend already supports it but mixing two
 *     filter mechanisms makes the URL state harder to reason about).
 *   - One shared useMarketData call subscribes to the union of all
 *     visible strategies' open-leg symbols. Each StrategyCard reads
 *     LTPs from this shared map — a symbol used in three strategies
 *     streams once, not three times.
 *   - "View in builder" navigates with ?load=<id>; the builder reads
 *     the param on mount (already implemented in Phase 5).
 *   - "Close strategy" opens a single page-owned CloseStrategyDialog,
 *     pre-filled with live LTPs as exit-price defaults.
 *   - "Delete" hard-deletes after a confirm() prompt.
 *
 * Active vs closed:
 *   - Active strategies: open legs use streaming LTP for the "Current"
 *     and P&L cells. Closed legs in the same strategy use exit_price.
 *     P&L surfaces as "Unrealized" in the card header.
 *   - Closed/Expired strategies: every leg uses exit_price. P&L
 *     surfaces as "Realized" — fixed at the time of close.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listStrategies } from "@/api/strategies";
import { CloseStrategyDialog } from "@/components/strategy-portfolio/CloseStrategyDialog";
import { StrategyCard } from "@/components/strategy-portfolio/StrategyCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { useMarketData } from "@/hooks/useMarketData";
import { cn } from "@/lib/utils";
import type { Strategy, StrategyMode, StrategyStatus } from "@/types/strategy";

const MODE_OPTIONS: ReadonlyArray<{ value: StrategyMode | "all"; label: string }> = [
  { value: "all", label: "All modes" },
  { value: "live", label: "Live" },
  { value: "sandbox", label: "Sandbox" },
];

const STATUS_OPTIONS: ReadonlyArray<{
  value: StrategyStatus | "all";
  label: string;
}> = [
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
  { value: "expired", label: "Expired" },
  { value: "all", label: "All" },
];

function ltpKey(symbol: string, exchange: string): string {
  return `${exchange}:${symbol}`;
}

export default function StrategyPortfolio() {
  const queryClient = useQueryClient();
  const { mode: currentMode } = useTradingMode();

  // Filters — default to "current trading mode" + active so the user
  // sees strategies relevant to their current session first.
  const [modeFilter, setModeFilter] = useState<StrategyMode | "all">(
    currentMode === "sandbox" ? "sandbox" : "live",
  );
  const [statusFilter, setStatusFilter] = useState<StrategyStatus | "all">(
    "active",
  );
  const [underlyingFilter, setUnderlyingFilter] = useState<string>("");

  const [closeOpen, setCloseOpen] = useState(false);
  const [closingStrategy, setClosingStrategy] = useState<Strategy | null>(null);

  // ── Strategies query ────────────────────────────────────────────────
  const strategiesQuery = useQuery({
    queryKey: [
      "strategies",
      modeFilter === "all" ? null : modeFilter,
      statusFilter === "all" ? null : statusFilter,
    ],
    queryFn: () =>
      listStrategies({
        mode: modeFilter === "all" ? undefined : modeFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
      }),
    staleTime: 30_000,
    retry: 0,
  });

  // Apply client-side underlying filter on top of the server response.
  const visibleStrategies = useMemo<Strategy[]>(() => {
    const raw = strategiesQuery.data ?? [];
    if (!underlyingFilter.trim()) return raw;
    const u = underlyingFilter.trim().toUpperCase();
    return raw.filter((s) => s.underlying.toUpperCase().includes(u));
  }, [strategiesQuery.data, underlyingFilter]);

  // ── Shared subscription set ─────────────────────────────────────────
  // Union of all open-leg symbols across visible *active* strategies.
  // Closed/expired strategies have no streaming LTPs to source — their
  // P&L is realized and read off exit_price already.
  const subscriptionSymbols = useMemo(() => {
    const seen = new Set<string>();
    const out: Array<{ symbol: string; exchange: string }> = [];
    for (const s of visibleStrategies) {
      if (s.status !== "active") continue;
      const exch = s.exchange.toUpperCase();
      for (const leg of s.legs) {
        if (!leg.symbol) continue;
        if (leg.status === "closed" || leg.status === "expired") continue;
        const k = ltpKey(leg.symbol, exch);
        if (seen.has(k)) continue;
        seen.add(k);
        out.push({ symbol: leg.symbol, exchange: exch });
      }
    }
    return out;
  }, [visibleStrategies]);

  const { data: tickMap, isAuthenticated, state, error: wsError } = useMarketData({
    symbols: subscriptionSymbols,
    mode: "Quote",
    enabled: subscriptionSymbols.length > 0,
  });

  // Project the tick map into a lighter Map<key, ltp> so children don't
  // have to peer into MarketTickData themselves.
  const liveLtpMap = useMemo<Map<string, number | undefined>>(() => {
    const out = new Map<string, number | undefined>();
    for (const [key, val] of tickMap.entries()) {
      out.set(key, val.data.ltp);
    }
    return out;
  }, [tickMap]);

  // ── Mutations and refetch handlers ──────────────────────────────────
  const refetch = () => {
    queryClient.invalidateQueries({ queryKey: ["strategies"] });
  };

  const handleCloseClick = (s: Strategy) => {
    setClosingStrategy(s);
    setCloseOpen(true);
  };

  const handleClosed = (updated: Strategy) => {
    // Optimistically update the cached list so the row re-renders with
    // its new "closed" badge instantly. The next refetch will reconcile
    // with the server.
    queryClient.setQueriesData<Strategy[]>(
      { queryKey: ["strategies"] },
      (prev) => prev?.map((s) => (s.id === updated.id ? updated : s)) ?? prev,
    );
    refetch();
  };

  const handleDeleted = (id: number) => {
    queryClient.setQueriesData<Strategy[]>(
      { queryKey: ["strategies"] },
      (prev) => prev?.filter((s) => s.id !== id) ?? prev,
    );
  };

  // Re-pick the mode filter when the global trading mode flips so a
  // sandbox session naturally surfaces sandbox strategies first.
  useEffect(() => {
    if (modeFilter === "all") return;
    if (currentMode === "sandbox" && modeFilter !== "sandbox") {
      setModeFilter("sandbox");
    } else if (currentMode === "live" && modeFilter !== "live") {
      setModeFilter("live");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentMode]);

  return (
    <div className="space-y-4">
      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Strategy Portfolio</h1>
          <p className="text-sm text-muted-foreground">
            Saved strategies with live aggregated P&L. Click a card to expand
            its legs.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Mode</label>
            <select
              value={modeFilter}
              onChange={(e) =>
                setModeFilter(e.target.value as StrategyMode | "all")
              }
              className="h-8 w-32 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {MODE_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Status</label>
            <div className="inline-flex h-8 rounded-lg border border-input bg-background p-0.5">
              {STATUS_OPTIONS.map((s) => {
                const active = statusFilter === s.value;
                return (
                  <button
                    key={s.value}
                    type="button"
                    onClick={() => setStatusFilter(s.value)}
                    className={cn(
                      "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                      active
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {s.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">
              Underlying (filter)
            </label>
            <input
              type="text"
              value={underlyingFilter}
              onChange={(e) => setUnderlyingFilter(e.target.value)}
              placeholder="NIFTY, BANKNIFTY…"
              className="h-8 w-40 rounded-lg border border-input bg-background px-2 text-sm uppercase outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </div>
          <Button variant="outline" onClick={refetch}>
            Refresh
          </Button>
          <Link to="/tools/strategybuilder">
            <Button>+ New strategy</Button>
          </Link>
        </div>
      </div>

      {/* ── Status strip ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {subscriptionSymbols.length > 0 ? (
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-medium",
              isAuthenticated
                ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                : state === "error"
                  ? "bg-red-500/15 text-red-700 dark:text-red-300"
                  : "bg-muted text-muted-foreground",
            )}
          >
            {isAuthenticated && (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            )}
            WS:{" "}
            {isAuthenticated
              ? "Live"
              : state === "connecting"
                ? "Connecting…"
                : state === "authenticating"
                  ? "Authenticating…"
                  : state === "error"
                    ? "Error"
                    : state}
          </span>
        ) : (
          <Badge variant="outline">No active legs streaming</Badge>
        )}
        <span className="text-muted-foreground">
          {subscriptionSymbols.length} unique symbol
          {subscriptionSymbols.length === 1 ? "" : "s"} subscribed
        </span>
        {wsError && (
          <span className="rounded-md bg-destructive/10 px-2 py-1 text-destructive">
            {wsError}
          </span>
        )}
        <span className="ml-auto text-muted-foreground">
          {visibleStrategies.length} strateg
          {visibleStrategies.length === 1 ? "y" : "ies"} shown
        </span>
      </div>

      {/* ── Cards / empty / loading / error ──────────────────────────── */}
      {strategiesQuery.isLoading ? (
        <div className="flex h-[280px] items-center justify-center text-sm text-muted-foreground">
          Loading saved strategies…
        </div>
      ) : strategiesQuery.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <p className="text-sm text-destructive">
              Failed to load strategies.
            </p>
            <p className="text-xs text-muted-foreground">
              {(strategiesQuery.error as { message?: string } | null)
                ?.message ?? "Try refreshing."}
            </p>
            <Button variant="outline" onClick={refetch}>
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : visibleStrategies.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted/40 text-muted-foreground">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-6 w-6"
              >
                <path d="M3 3h7v7H3z" />
                <path d="M14 3h7v7h-7z" />
                <path d="M3 14h7v7H3z" />
                <path d="M14 14h7v7h-7z" />
              </svg>
            </div>
            <div className="space-y-1">
              <p className="text-base font-medium">No strategies match</p>
              <p className="max-w-md text-xs text-muted-foreground">
                {(strategiesQuery.data?.length ?? 0) === 0
                  ? "Build your first strategy in the Strategy Builder, save it, and it'll show up here with live P&L."
                  : "Adjust the filters above to widen the search."}
              </p>
            </div>
            <Link to="/tools/strategybuilder">
              <Button variant="outline">+ Open Strategy Builder</Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        // 2-column grid on lg+ so power users can scan a portfolio at a
        // glance. Single-col on smaller viewports keeps the expanded leg
        // table from compressing.
        <div className="grid gap-3 lg:grid-cols-2">
          {visibleStrategies.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              liveLtpMap={liveLtpMap}
              onCloseClick={handleCloseClick}
              onDeleted={handleDeleted}
            />
          ))}
        </div>
      )}

      {/* ── Close dialog ─────────────────────────────────────────────── */}
      <CloseStrategyDialog
        open={closeOpen}
        onOpenChange={setCloseOpen}
        strategy={closingStrategy}
        liveLtpMap={liveLtpMap}
        onClosed={handleClosed}
      />
    </div>
  );
}
