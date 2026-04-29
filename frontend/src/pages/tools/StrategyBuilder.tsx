/**
 * Strategy Builder — page shell.
 *
 * Phase 5 shipped the foundation; Phase 6 wires in:
 *   - useChainContext: pulls the strike grid + ATM + lot_size + per-strike
 *     LTPs from /api/v1/optionchain so templates can resolve offsets to
 *     real strikes and new legs prefill entry_price from the live LTP.
 *   - useStrategySnapshot: debounced auto-fetch of the live snapshot on
 *     every leg change (manual Refresh remains for explicit reload).
 *   - GreeksPanel: per-leg + aggregate Greeks table (Greeks tab).
 *   - PayoffChart: At-Expiry + T+0 curves with breakevens, ±σ bands,
 *     spot vertical, and "Unlimited" annotations (Payoff tab).
 *
 * State ownership: this page owns the entire builder state (legs, picker
 * values, snapshot result). Components are presentation-only — they take
 * a value + onChange. Keeps the data flow obvious and makes the URL
 * `?load=<id>` round-trip trivial.
 *
 * No WebSocket subscription on the underlying spot — the snapshot
 * endpoint delivers it on demand. Per-leg LTP streaming is wired
 * separately in the Phase 7 P&L tab so spot ticks don't cascade
 * re-renders into the payoff chart.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { getStrategy } from "@/api/strategies";
import { ExpiryPicker, convertExpiryForApi } from "@/components/strategy-builder/ExpiryPicker";
import { GreeksPanel } from "@/components/strategy-builder/GreeksPanel";
import { LegRow, type BuilderLeg } from "@/components/strategy-builder/LegRow";
import { PayoffChart } from "@/components/strategy-builder/PayoffChart";
import { PnLTab, type PnlLeg } from "@/components/strategy-builder/PnLTab";
import { SaveStrategyDialog } from "@/components/strategy-builder/SaveStrategyDialog";
import { StrategyTemplatePicker } from "@/components/strategy-builder/StrategyTemplatePicker";
import { UnderlyingPicker } from "@/components/strategy-builder/UnderlyingPicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { useChainContext } from "@/hooks/useChainContext";
import { useStrategySnapshot } from "@/hooks/useStrategySnapshot";
import { resolveOffset, type StrategyTemplate } from "@/lib/strategyTemplates";
import { cn } from "@/lib/utils";
import type { FnoExchange } from "@/types/optionchain";
import type {
  SnapshotLegInput,
  StrategyLeg,
} from "@/types/strategy";

// Default lot sizes by underlying. Used as a starting point for new legs;
// user can override per-leg or per-page. Phase 6 will replace this with a
// `useLotSize(underlying)` hook backed by the symtoken table.
const DEFAULT_LOT_SIZES: Record<string, number> = {
  NIFTY: 75,
  BANKNIFTY: 15,
  FINNIFTY: 65,
  MIDCPNIFTY: 120,
  NIFTYNXT50: 25,
  SENSEX: 20,
  BANKEX: 30,
  USDINR: 1000,
  EURINR: 1000,
  GBPINR: 1000,
  JPYINR: 1000,
};

function defaultLotSize(underlying: string): number {
  return DEFAULT_LOT_SIZES[underlying.toUpperCase()] ?? 1;
}

function newLeg(
  expiry: string,
  defaults: Partial<BuilderLeg> = {},
  lotSize = 1,
): BuilderLeg {
  return {
    id: crypto.randomUUID(),
    action: "BUY",
    option_type: "CE",
    strike: Number.NaN,
    lots: 1,
    lot_size: lotSize,
    expiry_date: expiry,
    entry_price: 0,
    symbol: "",
    ...defaults,
  };
}

/** "NIFTY" + "02MAY26" + 25000 + "CE" → "NIFTY02MAY2625000CE". */
function buildOptionSymbol(
  underlying: string,
  expiryApi: string,
  strike: number,
  optType: "CE" | "PE",
): string {
  if (!underlying || !expiryApi || !Number.isFinite(strike) || strike <= 0) {
    return "";
  }
  // Strip trailing zero on whole-number strikes; keep decimals for currency
  // strikes like USDINR 82.50.
  const strikeStr =
    strike % 1 === 0
      ? strike.toString()
      : strike.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return `${underlying}${expiryApi}${strikeStr}${optType}`;
}

/** Resolve every leg's `symbol` from the current underlying + expiry. */
function resolveAllSymbols(
  legs: BuilderLeg[],
  underlying: string,
): BuilderLeg[] {
  return legs.map((leg) => ({
    ...leg,
    symbol: buildOptionSymbol(
      underlying,
      leg.expiry_date,
      leg.strike,
      leg.option_type,
    ),
  }));
}

/** Convert builder legs to the persistence schema's StrategyLeg[]. */
function toPersistedLegs(legs: BuilderLeg[]): StrategyLeg[] {
  return legs.map((l) => ({
    id: l.id,
    action: l.action,
    option_type: l.option_type,
    strike: l.strike,
    lots: l.lots,
    lot_size: l.lot_size,
    expiry_date: l.expiry_date,
    symbol: l.symbol,
    entry_price: l.entry_price,
    status: "open" as const,
  }));
}

export default function StrategyBuilder() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { mode: tradingMode } = useTradingMode();

  // ── Builder state ────────────────────────────────────────────────────
  const [exchange, setExchange] = useState<FnoExchange>("NFO");
  const [underlying, setUnderlying] = useState("NIFTY");
  /** Display format "DD-MMM-YYYY"; backend wants "DDMMMYY" via convertExpiryForApi. */
  const [expiry, setExpiry] = useState<string>("");
  const [legs, setLegs] = useState<BuilderLeg[]>([]);
  const [activeTab, setActiveTab] = useState<string>("legs");

  // Persistence
  const [strategyId, setStrategyId] = useState<number | null>(null);
  const [strategyName, setStrategyName] = useState("");
  const [strategyNotes, setStrategyNotes] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [templateValue] = useState("");

  const expiryApi = useMemo(() => convertExpiryForApi(expiry), [expiry]);

  // ── Chain context (ATM, strike grid, lot_size, per-strike LTPs) ──────
  const chain = useChainContext({ underlying, exchange, expiryApi });

  // ── Live snapshot (auto-debounced on leg changes) ────────────────────
  const snapshotLegs = useMemo<SnapshotLegInput[]>(
    () =>
      legs
        .filter((l) => l.symbol)
        .map((l) => ({
          symbol: l.symbol,
          action: l.action,
          lots: l.lots,
          lot_size: l.lot_size,
          entry_price: l.entry_price > 0 ? l.entry_price : undefined,
        })),
    [legs],
  );

  const {
    snapshot,
    loading: snapshotLoading,
    error: snapshotError,
    refetch: refetchSnapshot,
  } = useStrategySnapshot({
    underlying,
    options_exchange: exchange,
    legs: snapshotLegs,
  });

  // Surface snapshot errors as toasts when they're new — keeps the user
  // informed without duplicating the error text in two places.
  useEffect(() => {
    if (snapshotError) toast.error(snapshotError);
  }, [snapshotError]);

  // Map of symbol → user-entered entry price (for PayoffChart fallback).
  const entryPriceBySymbol = useMemo(() => {
    const out: Record<string, number> = {};
    for (const l of legs) {
      if (l.symbol) out[l.symbol] = l.entry_price;
    }
    return out;
  }, [legs]);

  // Adapter for the P&L tab — maps builder legs to the streaming-table shape.
  // Skips legs without a resolved symbol (the WS subscriber would reject them
  // anyway). Uses the page's options exchange as the leg's WS exchange so a
  // BFO basket subscribes on BFO, not NFO.
  const pnlLegs = useMemo<PnlLeg[]>(
    () =>
      legs
        .filter((l) => l.symbol && l.entry_price > 0)
        .map((l) => ({
          id: l.id,
          symbol: l.symbol,
          exchange: exchange,
          action: l.action,
          optionType: l.option_type,
          strike: l.strike,
          lots: l.lots,
          lotSize: l.lot_size,
          entryPrice: l.entry_price,
        })),
    [legs, exchange],
  );

  // ── Load saved strategy from `?load=<id>` ────────────────────────────
  useEffect(() => {
    const loadParam = searchParams.get("load");
    if (!loadParam) return;
    const id = parseInt(loadParam, 10);
    if (!Number.isFinite(id) || id <= 0) return;

    let cancelled = false;
    getStrategy(id)
      .then((s) => {
        if (cancelled) return;
        setExchange((s.exchange as FnoExchange) || "NFO");
        setUnderlying(s.underlying);
        // The saved expiry is in "DDMMMYY" backend format. Display picker
        // uses "DD-MMM-YYYY" — we'll resync after expiriesQuery loads;
        // until then set legs anyway with the saved per-leg expiry.
        setStrategyId(s.id);
        setStrategyName(s.name);
        setStrategyNotes(s.notes);
        const lotSize =
          s.legs[0]?.lot_size ?? defaultLotSize(s.underlying);
        setLegs(
          s.legs.map((l) => ({
            id: l.id ?? crypto.randomUUID(),
            action: l.action,
            option_type: l.option_type,
            strike: l.strike,
            lots: l.lots,
            lot_size: l.lot_size ?? lotSize,
            expiry_date:
              l.expiry_date ?? (s.expiry_date ? s.expiry_date : ""),
            entry_price: l.entry_price ?? 0,
            symbol:
              l.symbol ??
              buildOptionSymbol(
                s.underlying,
                l.expiry_date ?? s.expiry_date ?? "",
                l.strike,
                l.option_type,
              ),
          })),
        );
        toast.success(`Loaded '${s.name}'`);
      })
      .catch((e) => {
        if (cancelled) return;
        const msg =
          (e as { response?: { data?: { detail?: string } }; message?: string })
            ?.response?.data?.detail ??
          (e as { message?: string })?.message ??
          "Failed to load strategy";
        toast.error(msg);
      })
      .finally(() => {
        // Drop the URL param so a refresh doesn't re-trigger the load.
        const next = new URLSearchParams(searchParams);
        next.delete("load");
        setSearchParams(next, { replace: true });
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Re-resolve symbols when underlying / expiry / per-leg fields change ─
  // Keeps the displayed symbol in each LegRow consistent without needing
  // every onChange to re-derive it.
  useEffect(() => {
    if (!expiryApi) return;
    setLegs((prev) => {
      let mutated = false;
      const next = prev.map((leg) => {
        const wantExpiry = leg.expiry_date || expiryApi;
        const wantSymbol = buildOptionSymbol(
          underlying,
          wantExpiry,
          leg.strike,
          leg.option_type,
        );
        if (
          leg.symbol === wantSymbol &&
          leg.expiry_date === wantExpiry
        ) {
          return leg;
        }
        mutated = true;
        return { ...leg, symbol: wantSymbol, expiry_date: wantExpiry };
      });
      return mutated ? next : prev;
    });
  }, [underlying, expiryApi]);

  // ── Leg manipulation ────────────────────────────────────────────────
  // Prefer the chain-derived lot size when we have it; fall back to the
  // built-in defaults so the row label shows "Lots × N" before the chain
  // fetch settles.
  const lotSizeForUnderlying = useMemo(
    () => chain.context?.lotSize ?? defaultLotSize(underlying),
    [chain.context, underlying],
  );

  const handleAddLeg = useCallback(() => {
    if (!expiryApi) {
      toast.error("Pick an expiry first");
      return;
    }
    setLegs((prev) => [
      ...prev,
      newLeg(expiryApi, {}, lotSizeForUnderlying),
    ]);
  }, [expiryApi, lotSizeForUnderlying]);

  const handleLegChange = useCallback((updated: BuilderLeg) => {
    setLegs((prev) => {
      return prev.map((l) =>
        l.id === updated.id
          ? {
              ...updated,
              symbol: buildOptionSymbol(
                underlying,
                updated.expiry_date,
                updated.strike,
                updated.option_type,
              ),
            }
          : l,
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying]);

  const handleLegRemove = useCallback((id: string) => {
    setLegs((prev) => prev.filter((l) => l.id !== id));
  }, []);

  const handleClearAll = useCallback(() => {
    setLegs([]);
    setStrategyId(null);
    setStrategyName("");
    setStrategyNotes(null);
    // Snapshot is owned by useStrategySnapshot — emptying `legs` makes the
    // hook clear it on the next tick. No direct setter call needed.
  }, []);

  // ── Apply template ──────────────────────────────────────────────────
  // Resolves each template leg's relative offset (ATM / OTMn / ITMn) to
  // an absolute strike via the chain, picks the live LTP at that strike
  // as the default entry price, and stamps the resolved option symbol.
  // Falls back to NaN strike + empty symbol when the chain isn't ready
  // yet — the user can refresh once it loads, or set strikes manually.
  const handleApplyTemplate = useCallback(
    (template: StrategyTemplate) => {
      if (!expiryApi) {
        toast.error("Pick an expiry first");
        return;
      }
      const lotSize = lotSizeForUnderlying;
      const ctx = chain.context;
      const newLegs: BuilderLeg[] = template.legs.map((tl) => {
        let strike = Number.NaN;
        let entryPrice = 0;
        let symbol = "";
        if (ctx) {
          const resolved = resolveOffset(
            tl.offset,
            ctx.atm,
            ctx.strikes,
            tl.option_type,
          );
          if (resolved !== null) {
            strike = resolved;
            const ltp =
              tl.option_type === "CE"
                ? ctx.ceLtpByStrike.get(resolved)
                : ctx.peLtpByStrike.get(resolved);
            if (ltp && ltp > 0) entryPrice = Number(ltp.toFixed(2));
            symbol = buildOptionSymbol(
              underlying,
              expiryApi,
              resolved,
              tl.option_type,
            );
          }
        }
        return {
          id: crypto.randomUUID(),
          action: tl.action,
          option_type: tl.option_type,
          strike,
          lots: tl.lots,
          lot_size: lotSize,
          expiry_date: expiryApi,
          entry_price: entryPrice,
          symbol,
        };
      });
      setLegs(newLegs);
      setStrategyName((prev) => prev || template.name);

      const unresolved = newLegs.filter((l) => !l.symbol).length;
      if (unresolved > 0) {
        toast.message(
          `Applied '${template.name}'. ${unresolved} leg${unresolved === 1 ? "" : "s"} need a strike — chain not ready yet.`,
        );
      } else {
        toast.success(`Applied '${template.name}' (${newLegs.length} legs).`);
      }
    },
    [expiryApi, lotSizeForUnderlying, chain.context, underlying],
  );

  // ── Refresh button — manual refetch on top of the auto-debounce ─────
  const handleRefreshSnapshot = useCallback(async () => {
    if (snapshotLegs.length === 0) {
      toast.error("Add at least one leg with a strike before refreshing");
      return;
    }
    await refetchSnapshot();
  }, [snapshotLegs.length, refetchSnapshot]);

  const handleSaved = useCallback((saved: { id: number; name: string; notes: string | null }) => {
    setStrategyId(saved.id);
    setStrategyName(saved.name);
    setStrategyNotes(saved.notes);
  }, []);

  // ── Pre-resolve legs (with current-underlying symbol) for save dialog ─
  const persistedLegs = useMemo(
    () => toPersistedLegs(resolveAllSymbols(legs, underlying)),
    [legs, underlying],
  );

  const totals = snapshot?.totals;
  const hasUnsolvedStrikes = legs.some(
    (l) => !Number.isFinite(l.strike) || l.strike <= 0,
  );

  return (
    <div className="space-y-4">
      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Strategy Builder</h1>
          <p className="text-sm text-muted-foreground">
            Multi-leg option strategy designer. Build a basket from scratch or
            from a template, save it, and reload from your portfolio.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <UnderlyingPicker
            exchange={exchange}
            underlying={underlying}
            onExchangeChange={setExchange}
            onUnderlyingChange={(u) => {
              setUnderlying(u);
              setExpiry("");
            }}
          />
          <ExpiryPicker
            underlying={underlying}
            exchange={exchange}
            expiry={expiry}
            onExpiryChange={setExpiry}
          />
          <StrategyTemplatePicker
            value={templateValue}
            onApply={handleApplyTemplate}
          />
          <Button
            variant="outline"
            onClick={() => setSaveOpen(true)}
            disabled={legs.length === 0}
          >
            {strategyId ? "Update" : "Save"}
          </Button>
          <Button
            variant="ghost"
            onClick={handleClearAll}
            disabled={legs.length === 0 && !strategyId}
          >
            Clear
          </Button>
        </div>
      </div>

      {/* ── Status badges ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2">
        {strategyId !== null && (
          <Badge variant="secondary">
            Loaded: {strategyName} (id {strategyId})
          </Badge>
        )}
        <Badge variant="outline" className="capitalize">
          Mode: {tradingMode}
        </Badge>
        {chain.context && (
          <Badge variant="secondary">
            ATM: {chain.context.atm} · {chain.context.strikes.length} strikes
          </Badge>
        )}
        {chain.loading && !chain.context && (
          <Badge variant="outline" className="animate-pulse">
            Loading chain…
          </Badge>
        )}
        {snapshot && (
          <>
            <Badge variant="secondary">Spot: {snapshot.spot_price.toFixed(2)}</Badge>
            <Badge variant="secondary">As of: {new Date(snapshot.as_of).toLocaleTimeString()}</Badge>
          </>
        )}
      </div>

      {/* ── Tabs ──────────────────────────────────────────────────────── */}
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as string)}>
        <TabsList>
          <TabsTrigger value="legs">Legs</TabsTrigger>
          <TabsTrigger value="greeks">Greeks</TabsTrigger>
          <TabsTrigger value="payoff">Payoff</TabsTrigger>
          <TabsTrigger value="chart">Chart</TabsTrigger>
          <TabsTrigger value="pnl">P&amp;L</TabsTrigger>
        </TabsList>

        {/* Legs tab */}
        <TabsContent value="legs">
          <Card>
            <CardContent className="space-y-3 p-4">
              {legs.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 py-8 text-center text-muted-foreground">
                  <p className="text-sm">No legs yet.</p>
                  <p className="text-xs">
                    Pick a template above, or add legs manually below.
                  </p>
                </div>
              ) : (
                legs.map((leg) => (
                  <LegRow
                    key={leg.id}
                    leg={leg}
                    onChange={handleLegChange}
                    onRemove={() => handleLegRemove(leg.id)}
                  />
                ))
              )}
              <div className="flex items-center justify-between">
                <Button variant="outline" onClick={handleAddLeg} disabled={!expiryApi}>
                  + Add Leg
                </Button>
                <Button
                  onClick={handleRefreshSnapshot}
                  disabled={
                    snapshotLoading || legs.length === 0 || hasUnsolvedStrikes
                  }
                >
                  {snapshotLoading ? "Loading…" : "Refresh Snapshot"}
                </Button>
              </div>
              {hasUnsolvedStrikes && legs.length > 0 && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Set a strike on every leg before refreshing the snapshot.
                </p>
              )}

              {/* Skeleton snapshot summary — Phase 6 will replace this with
                  the full GreeksPanel component. */}
              {totals && (
                <div className="rounded-md border border-border bg-muted/30 p-3">
                  <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3 lg:grid-cols-6">
                    <SummaryStat
                      label="Net Premium"
                      value={totals.premium_paid.toFixed(2)}
                      tone={totals.premium_paid >= 0 ? "neutral" : "positive"}
                      hint={totals.premium_paid >= 0 ? "Debit" : "Credit"}
                    />
                    <SummaryStat label="Δ" value={totals.delta.toFixed(2)} />
                    <SummaryStat label="Γ" value={totals.gamma.toFixed(4)} />
                    <SummaryStat
                      label="Θ /day"
                      value={totals.theta.toFixed(2)}
                      tone={totals.theta < 0 ? "negative" : "positive"}
                    />
                    <SummaryStat label="V /1%" value={totals.vega.toFixed(2)} />
                    {totals.unrealized_pnl !== undefined && (
                      <SummaryStat
                        label="Unrealized P&L"
                        value={totals.unrealized_pnl.toFixed(2)}
                        tone={
                          totals.unrealized_pnl > 0
                            ? "positive"
                            : totals.unrealized_pnl < 0
                              ? "negative"
                              : "neutral"
                        }
                      />
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Greeks tab — per-leg + aggregate */}
        <TabsContent value="greeks">
          <Card>
            <CardContent className="p-3 sm:p-4">
              <GreeksPanel
                snapshot={snapshot}
                loading={snapshotLoading}
                error={snapshotError}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* Payoff tab — At-Expiry + T+0 curves */}
        <TabsContent value="payoff">
          <Card>
            <CardContent className="p-2 sm:p-4">
              {snapshot ? (
                <PayoffChart
                  snapshotLegs={snapshot.legs}
                  entryPriceBySymbol={entryPriceBySymbol}
                  spot={snapshot.spot_price}
                />
              ) : (
                <div className="flex h-[420px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
                  <p className="text-sm">
                    {snapshotLoading
                      ? "Pricing legs…"
                      : "No snapshot yet."}
                  </p>
                  <p className="text-xs">
                    {snapshotLoading
                      ? "First snapshot is fetching."
                      : "Add legs and pick strikes to draw the payoff."}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Phase 8 — historical strategy chart */}
        <TabsContent value="chart">
          <PlaceholderTab
            title="Strategy chart"
            phase="Phase 8"
            note="Historical combined-premium time series with optional underlying overlay. Drives off /web/strategybuilder/chart with intersection-correct timestamps."
          />
        </TabsContent>

        {/* P&L tab — WebSocket-streamed leg LTPs */}
        <TabsContent value="pnl">
          <Card>
            <CardContent className="p-3 sm:p-4">
              <PnLTab legs={pnlLegs} enabled={activeTab === "pnl"} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── Save dialog ───────────────────────────────────────────────── */}
      <SaveStrategyDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        existingId={strategyId}
        initialName={strategyName}
        initialNotes={strategyNotes}
        underlying={underlying}
        exchange={exchange}
        expiryDate={expiryApi || null}
        legs={persistedLegs}
        mode={tradingMode === "sandbox" ? "sandbox" : "live"}
        onSaved={(s) => handleSaved(s)}
      />
    </div>
  );
}

// ─── Tiny helpers ───────────────────────────────────────────────────────

function SummaryStat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative" | "neutral";
  hint?: string;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "font-mono text-sm font-semibold tabular-nums",
          tone === "positive" && "text-emerald-600 dark:text-emerald-400",
          tone === "negative" && "text-red-600 dark:text-red-400",
        )}
      >
        {value}
      </p>
      {hint && <p className="text-[10px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function PlaceholderTab({
  title,
  phase,
  note,
}: {
  title: string;
  phase: string;
  note: string;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-2 py-12 text-center">
        <p className="text-base font-medium">{title}</p>
        <Badge variant="outline">{phase}</Badge>
        <p className="max-w-md text-xs text-muted-foreground">{note}</p>
      </CardContent>
    </Card>
  );
}
