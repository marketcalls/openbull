/**
 * Straddles & Strangles Chain — scanner + position management on one page.
 *
 * Top half: scanner table that ranks (underlying × expiry) pairs for one of
 * the four strategy variants (Short/Long Straddle, Short/Long Strangle).
 * Per-row "Trade Now" opens the existing BasketOrderDialog with the two
 * legs prefilled in the correct B/S direction.
 *
 * Bottom half: management of open straddle/strangle pairs derived from the
 * user's live positions, plus a "Recent / Closed today" strip of baskets
 * fired from this page (persisted in localStorage so Reopen survives
 * reloads).
 *
 * Pricing / Greeks / IV / POP / breakevens all run client-side via
 * lib/black76 + lib/probabilityOfProfit. The chain endpoint is the only
 * data dependency — we fetch it per (underlying, expiry) and never call
 * a custom backend.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";

import {
  fetchExpiries,
  fetchOptionChain,
  fetchUnderlyings,
} from "@/api/optionchain";
import { getPositions } from "@/api/dashboard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  BasketOrderDialog,
  type BasketDialogLeg,
} from "@/components/trading/BasketOrderDialog";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { cn } from "@/lib/utils";
import {
  buildScannerRow,
  groupPositionsToStraddles,
  STRATEGIES,
  STRATEGY_KEYS,
  type ActiveGroup,
  type ScannerRow,
  type StrategyKey,
} from "@/lib/straddleScanner";
import type { FnoExchange, UnderlyingOption } from "@/types/optionchain";
import type { OptionType } from "@/types/strategy";

// ─── Constants ──────────────────────────────────────────────────────────

const STORAGE_KEY = "openbull_straddle_recent_baskets";
const STORAGE_MAX = 30;

/** Indices scope when "Indices = All" is selected. NFO majors + BFO indices. */
const INDEX_SCOPE: Array<{ exchange: FnoExchange; underlying: string }> = [
  { exchange: "NFO", underlying: "NIFTY" },
  { exchange: "NFO", underlying: "BANKNIFTY" },
  { exchange: "NFO", underlying: "FINNIFTY" },
  { exchange: "NFO", underlying: "MIDCPNIFTY" },
  { exchange: "BFO", underlying: "SENSEX" },
  { exchange: "BFO", underlying: "BANKEX" },
];

/** How many expiries to scan per underlying. 3 = nearest weekly + next + monthly. */
const EXPIRIES_PER_UNDERLYING = 3;
/** How many strikes to fetch per chain call. 7 covers ATM ± 3 — enough for
 *  strangle offsets up to 3 with headroom. */
const STRIKE_COUNT = 7;
/** When stock-mode "All" is selected, scan this many top stocks × nearest 1 expiry. */
const TOP_STOCKS_LIMIT = 20;
const EXPIRIES_FOR_STOCK_ALL = 1;

const INSTRUMENT_TABS: Array<{ value: "indices" | "stock"; label: string }> = [
  { value: "indices", label: "Indices" },
  { value: "stock", label: "Stock" },
];

// ─── localStorage: recently-fired baskets ────────────────────────────────

interface RecentBasketLeg {
  symbol: string;
  exchange: string;
  action: "BUY" | "SELL";
  optionType: OptionType;
  strike: number;
  lots: number;
  lotSize: number;
  entryPrice: number;
  tickSize: number;
}

interface RecentBasket {
  id: string;
  ts: number;
  strategyKey: StrategyKey;
  strategyLabel: string;
  underlying: string;
  exchange: string;
  expiry: string;
  legs: RecentBasketLeg[];
}

function loadRecentBaskets(): RecentBasket[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((b) => b && Array.isArray(b.legs));
  } catch {
    return [];
  }
}

function saveRecentBaskets(baskets: RecentBasket[]): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(baskets.slice(0, STORAGE_MAX)),
    );
  } catch {
    /* quota exceeded — drop silently */
  }
}

// ─── Formatting helpers ─────────────────────────────────────────────────

function fmtPrice(n: number | null | undefined, dp = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(dp);
}

function fmtSpot(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(2);
}

function fmtPct(p: number | null | undefined, dp = 0): string {
  if (p == null || !Number.isFinite(p)) return "—";
  return `${(p * 100).toFixed(dp)}%`;
}

function fmtIvPct(iv: number | null | undefined): string {
  if (iv == null || !Number.isFinite(iv) || iv <= 0) return "—";
  return (iv * 100).toFixed(1);
}

// ─── Page ────────────────────────────────────────────────────────────────

export default function StraddlesStrangleChain() {
  const queryClient = useQueryClient();
  const { mode } = useTradingMode();

  // ── UI state ─────────────────────────────────────────────────────────
  const [strategy, setStrategy] = useState<StrategyKey>("short_straddle");
  const [instrumentType, setInstrumentType] = useState<"indices" | "stock">(
    "indices",
  );
  /** "ALL" or a specific underlying symbol. */
  const [scrip, setScrip] = useState<string>("ALL");
  const [perLot, setPerLot] = useState<boolean>(false);
  const [strangleOffset, setStrangleOffset] = useState<number>(1);

  // ── Underlyings ──────────────────────────────────────────────────────
  /** NFO underlyings — used for the Stock combobox. */
  const nfoUnderlyings = useQuery({
    queryKey: ["option-underlyings", "NFO"],
    queryFn: () => fetchUnderlyings("NFO"),
    staleTime: 5 * 60_000,
    retry: 0,
    enabled: instrumentType === "stock",
  });

  const stockOptions = useMemo<UnderlyingOption[]>(() => {
    if (
      nfoUnderlyings.data?.status === "success" &&
      nfoUnderlyings.data.data.length > 0
    ) {
      // Strip indices from the stock combobox — those live under the
      // Indices tab. The exclusion list mirrors INDEX_SCOPE.
      const indices = new Set(INDEX_SCOPE.map((s) => s.underlying));
      return nfoUnderlyings.data.data.filter((o) => !indices.has(o.symbol));
    }
    // Fallback minimal list shown until the API responds.
    return [
      { symbol: "RELIANCE", name: "Reliance" },
      { symbol: "TCS", name: "TCS" },
      { symbol: "HDFCBANK", name: "HDFC Bank" },
      { symbol: "INFY", name: "Infosys" },
      { symbol: "ICICIBANK", name: "ICICI Bank" },
    ];
  }, [nfoUnderlyings.data]);

  // ── Resolve "scope" — the (exchange, underlying) pairs to scan ───────
  type ScopeEntry = { exchange: FnoExchange; underlying: string };
  const scope = useMemo<ScopeEntry[]>(() => {
    if (instrumentType === "indices") {
      if (scrip === "ALL") return INDEX_SCOPE;
      const found = INDEX_SCOPE.find((s) => s.underlying === scrip);
      return found ? [found] : [];
    }
    // stock
    if (scrip === "ALL") {
      return stockOptions
        .slice(0, TOP_STOCKS_LIMIT)
        .map((o) => ({ exchange: "NFO" as FnoExchange, underlying: o.symbol }));
    }
    if (scrip) return [{ exchange: "NFO", underlying: scrip }];
    return [];
  }, [instrumentType, scrip, stockOptions]);

  // ── Expiries per underlying (parallel fetch) ─────────────────────────
  const expiryQueries = useQueries({
    queries: scope.map((s) => ({
      queryKey: ["expiries", s.underlying, s.exchange],
      queryFn: () =>
        fetchExpiries({
          symbol: s.underlying,
          exchange: s.exchange,
          instrumenttype: "options",
        }),
      staleTime: 5 * 60_000,
      retry: 0,
    })),
  });

  // ── Build the (underlying, exchange, expiry) cartesian product ────────
  type ChainTarget = { exchange: FnoExchange; underlying: string; expiry: string };
  const chainTargets = useMemo<ChainTarget[]>(() => {
    const expiriesPer =
      instrumentType === "stock" && scrip === "ALL"
        ? EXPIRIES_FOR_STOCK_ALL
        : EXPIRIES_PER_UNDERLYING;

    const out: ChainTarget[] = [];
    scope.forEach((s, idx) => {
      const q = expiryQueries[idx];
      if (q?.data?.status !== "success") return;
      const list = q.data.data.slice(0, expiriesPer);
      for (const e of list) {
        out.push({ exchange: s.exchange, underlying: s.underlying, expiry: e });
      }
    });
    return out;
  }, [scope, expiryQueries, instrumentType, scrip]);

  // ── Chain queries (parallel, per target) ──────────────────────────────
  // The `/api/v1/optionchain` endpoint wants the expiry as DDMMMYYYY (no
  // dashes), not the picker's display "DD-MMM-YYYY".
  const chainQueries = useQueries({
    queries: chainTargets.map((t) => ({
      queryKey: [
        "scanner-chain",
        t.underlying,
        t.exchange,
        t.expiry,
        STRIKE_COUNT,
      ],
      queryFn: () =>
        fetchOptionChain({
          underlying: t.underlying,
          exchange: t.exchange,
          expiry_date: t.expiry.replace(/-/g, "").toUpperCase(),
          strike_count: STRIKE_COUNT,
        }),
      staleTime: 25_000,
      refetchInterval: 30_000,
      retry: 0,
    })),
  });

  // ── Derive scanner rows ──────────────────────────────────────────────
  const rows = useMemo<ScannerRow[]>(() => {
    const out: ScannerRow[] = [];
    chainTargets.forEach((t, idx) => {
      const q = chainQueries[idx];
      const data = q?.data;
      if (!data || data.status !== "success") return;
      const row = buildScannerRow({
        underlying: t.underlying,
        exchange: t.exchange,
        expiry: t.expiry,
        spot: data.underlying_ltp,
        atmStrike: data.atm_strike,
        chain: data.chain,
        strategy,
        strangleOffset,
      });
      if (row) out.push(row);
    });
    return out;
  }, [chainTargets, chainQueries, strategy, strangleOffset]);

  const expiriesLoading = expiryQueries.some((q) => q.isFetching);
  const chainsLoading = chainQueries.some((q) => q.isFetching);
  const isFetching = expiriesLoading || chainsLoading;

  // Surface chain errors so a misconfigured broker / unsupported underlying
  // doesn't silently produce an empty table.
  const chainErrors = useMemo(
    () =>
      chainQueries
        .map((q, i) => {
          const t = chainTargets[i];
          if (!t) return null;
          if (q.error) return `${t.underlying} ${t.expiry}: ${(q.error as Error).message}`;
          if (q.data?.status === "error") {
            return `${t.underlying} ${t.expiry}: ${q.data.message ?? "chain error"}`;
          }
          return null;
        })
        .filter((s): s is string => s !== null),
    [chainQueries, chainTargets],
  );

  const hasRows = rows.length > 0;

  const refreshAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["scanner-chain"] });
    queryClient.invalidateQueries({ queryKey: ["positions"] });
  }, [queryClient]);

  // ── Positions + Active groups ────────────────────────────────────────
  const positionsQuery = useQuery({
    queryKey: ["positions"],
    queryFn: getPositions,
    refetchInterval: 15_000,
  });
  const positions = positionsQuery.data ?? [];

  const activeGroups = useMemo(
    () => groupPositionsToStraddles(positions),
    [positions],
  );

  // Track positions LTPs by symbol for the "spot vs BE" UX in active groups.
  const spotByUnderlying = useMemo(() => {
    // Reuse chain LTPs (the scanner is already fetching them) — falls back
    // to the position's own LTP for stocks not in the scanner scope.
    const map = new Map<string, number>();
    chainTargets.forEach((t, idx) => {
      const data = chainQueries[idx]?.data;
      if (data?.status === "success" && data.underlying_ltp > 0) {
        map.set(t.underlying, data.underlying_ltp);
      }
    });
    return map;
  }, [chainTargets, chainQueries]);

  // ── Recent baskets (localStorage) ────────────────────────────────────
  const [recents, setRecents] = useState<RecentBasket[]>(() =>
    loadRecentBaskets(),
  );
  /** Persist any change to recents back to localStorage. */
  useEffect(() => {
    saveRecentBaskets(recents);
  }, [recents]);

  /** Append a basket on successful fire (called from BasketOrderDialog). */
  const appendRecent = useCallback((b: RecentBasket) => {
    setRecents((prev) => {
      const filtered = prev.filter((x) => x.id !== b.id);
      return [b, ...filtered].slice(0, STORAGE_MAX);
    });
  }, []);

  const removeRecent = useCallback((id: string) => {
    setRecents((prev) => prev.filter((x) => x.id !== id));
  }, []);

  // Derive "closed today" set: a recent basket is closed when every leg's
  // symbol has either zero qty in the current positions snapshot, or is
  // absent entirely. Active baskets (where any leg still has qty) are
  // already shown in the Active groups section.
  const positionQtyBySymbol = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of positions) m.set(p.symbol, Number(p.quantity) || 0);
    return m;
  }, [positions]);

  const recentClosed = useMemo(
    () =>
      recents.filter((b) =>
        b.legs.every((l) => (positionQtyBySymbol.get(l.symbol) ?? 0) === 0),
      ),
    [recents, positionQtyBySymbol],
  );

  // ── Trade dialog plumbing ────────────────────────────────────────────
  const [basketOpen, setBasketOpen] = useState(false);
  const [basketLegs, setBasketLegs] = useState<BasketDialogLeg[]>([]);
  const [basketStrategy, setBasketStrategy] = useState<string>("Straddle");
  /** Stash so we can persist the basket on successful fire. */
  const pendingBasketRef = useRef<RecentBasket | null>(null);

  const openTrade = useCallback(
    (row: ScannerRow) => {
      const cfg = STRATEGIES[strategy];
      const legs: BasketDialogLeg[] = [
        {
          id: `scan-${row.id}-CE`,
          symbol: row.callSymbol,
          exchange: row.exchange,
          action: cfg.callAction,
          optionType: "CE",
          strike: row.callStrike,
          lots: 1,
          lotSize: row.lotSize,
          entryPrice: row.callPrem,
          tickSize: row.callTickSize,
        },
        {
          id: `scan-${row.id}-PE`,
          symbol: row.putSymbol,
          exchange: row.exchange,
          action: cfg.putAction,
          optionType: "PE",
          strike: row.putStrike,
          lots: 1,
          lotSize: row.lotSize,
          entryPrice: row.putPrem,
          tickSize: row.putTickSize,
        },
      ];
      setBasketLegs(legs);
      setBasketStrategy(cfg.label);
      pendingBasketRef.current = {
        id: row.id,
        ts: Date.now(),
        strategyKey: strategy,
        strategyLabel: cfg.label,
        underlying: row.underlying,
        exchange: row.exchange,
        expiry: row.expiry,
        legs: legs.map((l) => ({
          symbol: l.symbol,
          exchange: l.exchange,
          action: l.action,
          optionType: l.optionType,
          strike: l.strike,
          lots: l.lots,
          lotSize: l.lotSize,
          entryPrice: l.entryPrice ?? 0,
          tickSize: l.tickSize ?? 0.05,
        })),
      };
      setBasketOpen(true);
    },
    [strategy],
  );

  /** Open the basket dialog with reverse-direction legs to close a group. */
  const openClose = useCallback((g: ActiveGroup) => {
    const legs: BasketDialogLeg[] = [];
    if (g.ce) {
      legs.push({
        id: `close-${g.id}-CE`,
        symbol: g.ce.position.symbol,
        exchange: g.ce.position.exchange,
        action: g.ce.sign === "L" ? "SELL" : "BUY",
        optionType: "CE",
        strike: g.ce.strike,
        lots: 1,
        lotSize: g.ce.qty,
        entryPrice: g.ce.position.ltp,
        tickSize: 0.05,
      });
    }
    if (g.pe) {
      legs.push({
        id: `close-${g.id}-PE`,
        symbol: g.pe.position.symbol,
        exchange: g.pe.position.exchange,
        action: g.pe.sign === "L" ? "SELL" : "BUY",
        optionType: "PE",
        strike: g.pe.strike,
        lots: 1,
        lotSize: g.pe.qty,
        entryPrice: g.pe.position.ltp,
        tickSize: 0.05,
      });
    }
    setBasketLegs(legs);
    setBasketStrategy(`Close ${g.type}`);
    pendingBasketRef.current = null; // closes don't go into "Recent"
    setBasketOpen(true);
  }, []);

  /** Open the basket dialog with same-direction legs to add lots. */
  const openAdd = useCallback((g: ActiveGroup) => {
    const legs: BasketDialogLeg[] = [];
    if (g.ce) {
      legs.push({
        id: `add-${g.id}-CE`,
        symbol: g.ce.position.symbol,
        exchange: g.ce.position.exchange,
        action: g.ce.sign === "L" ? "BUY" : "SELL",
        optionType: "CE",
        strike: g.ce.strike,
        lots: 1,
        lotSize: g.ce.qty,
        entryPrice: g.ce.position.ltp,
        tickSize: 0.05,
      });
    }
    if (g.pe) {
      legs.push({
        id: `add-${g.id}-PE`,
        symbol: g.pe.position.symbol,
        exchange: g.pe.position.exchange,
        action: g.pe.sign === "L" ? "BUY" : "SELL",
        optionType: "PE",
        strike: g.pe.strike,
        lots: 1,
        lotSize: g.pe.qty,
        entryPrice: g.pe.position.ltp,
        tickSize: 0.05,
      });
    }
    setBasketLegs(legs);
    setBasketStrategy(`Add ${g.type}`);
    pendingBasketRef.current = null;
    setBasketOpen(true);
  }, []);

  /** Re-fire a recent basket using its persisted legs. */
  const openReopen = useCallback((b: RecentBasket) => {
    const legs: BasketDialogLeg[] = b.legs.map((l, i) => ({
      id: `reopen-${b.id}-${i}`,
      symbol: l.symbol,
      exchange: l.exchange,
      action: l.action,
      optionType: l.optionType,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lotSize,
      entryPrice: l.entryPrice,
      tickSize: l.tickSize,
    }));
    setBasketLegs(legs);
    setBasketStrategy(`Reopen ${b.strategyLabel}`);
    pendingBasketRef.current = { ...b, ts: Date.now() };
    setBasketOpen(true);
  }, []);

  const handleBasketComplete = useCallback(
    (results: Array<{ status: "success" | "error" }>) => {
      const anyOk = results.some((r) => r.status === "success");
      if (anyOk && pendingBasketRef.current) {
        appendRecent(pendingBasketRef.current);
        pendingBasketRef.current = null;
      }
      // Refetch positions so the Active groups update immediately.
      queryClient.invalidateQueries({ queryKey: ["positions"] });
    },
    [appendRecent, queryClient],
  );

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Straddles &amp; Strangles Chain
          </h1>
          <p className="text-sm text-muted-foreground">
            Scan ATM straddles &amp; OTM strangles across expiries, fire baskets,
            and manage open pairs — all on one page.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={refreshAll}
          disabled={isFetching}
        >
          <RefreshCw
            className={cn("h-4 w-4 mr-2", isFetching && "animate-spin")}
          />
          Refresh
        </Button>
      </div>

      {/* Strategy tabs */}
      <Tabs
        value={strategy}
        onValueChange={(v) => setStrategy(v as StrategyKey)}
      >
        <TabsList variant="line" className="h-9">
          {STRATEGY_KEYS.map((k) => (
            <TabsTrigger key={k} value={k} className="px-3">
              {STRATEGIES[k].label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Filter bar */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-3">
          {/* Indices / Stock */}
          <div className="flex items-center gap-1 rounded-lg border bg-muted/30 p-0.5">
            {INSTRUMENT_TABS.map((t) => (
              <button
                key={t.value}
                type="button"
                onClick={() => {
                  setInstrumentType(t.value);
                  setScrip("ALL");
                }}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                  instrumentType === t.value
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Scrip */}
          <div className="space-y-1">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Scrip
            </label>
            {instrumentType === "indices" ? (
              <select
                value={scrip}
                onChange={(e) => setScrip(e.target.value)}
                className="h-8 w-44 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="ALL">All indices</option>
                {INDEX_SCOPE.map((s) => (
                  <option key={s.underlying} value={s.underlying}>
                    {s.underlying}
                  </option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2">
                <select
                  value={scrip === "ALL" ? "ALL" : "ONE"}
                  onChange={(e) => {
                    if (e.target.value === "ALL") setScrip("ALL");
                    else setScrip(stockOptions[0]?.symbol ?? "");
                  }}
                  className="h-8 w-28 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <option value="ALL">Top {TOP_STOCKS_LIMIT}</option>
                  <option value="ONE">Pick one</option>
                </select>
                {scrip !== "ALL" && (
                  <UnderlyingCombobox
                    value={scrip}
                    options={stockOptions}
                    onChange={setScrip}
                    loading={nfoUnderlyings.isLoading}
                    className="w-48"
                  />
                )}
              </div>
            )}
          </div>

          {/* Strangle offset (only relevant for strangle tabs) */}
          {STRATEGIES[strategy].isStrangle && (
            <div className="space-y-1">
              <label className="block text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                OTM offset (strikes)
              </label>
              <Input
                type="number"
                min={1}
                step={1}
                value={strangleOffset}
                onChange={(e) =>
                  setStrangleOffset(
                    Math.max(1, Math.floor(Number(e.target.value) || 1)),
                  )
                }
                className="h-8 w-20 text-right font-mono"
              />
            </div>
          )}

          {/* Per-Lot toggle */}
          <label className="flex h-8 cursor-pointer items-center gap-2 rounded-lg border bg-background px-3 text-sm">
            <input
              type="checkbox"
              checked={perLot}
              onChange={(e) => setPerLot(e.target.checked)}
              className="h-3.5 w-3.5 cursor-pointer accent-primary"
            />
            <span>Per Lot</span>
          </label>

          {/* Trading mode badge */}
          <div className="ml-auto">
            <Badge
              variant={mode === "sandbox" ? "secondary" : "outline"}
              className="capitalize"
            >
              {mode}
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Scanner table */}
      <Card>
        <CardContent className="p-0">
          {!hasRows ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              {isFetching ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" />
                  {expiriesLoading ? "Loading expiries…" : "Scanning chains…"}
                </>
              ) : scope.length === 0 ? (
                <p>Pick a scrip to scan.</p>
              ) : chainErrors.length > 0 ? (
                <div className="max-w-2xl space-y-1 px-6 text-center text-xs text-rose-600 dark:text-rose-400">
                  <p className="font-semibold">Chain queries failed:</p>
                  {chainErrors.slice(0, 5).map((m) => (
                    <p key={m} className="font-mono">
                      {m}
                    </p>
                  ))}
                  {chainErrors.length > 5 && (
                    <p>+ {chainErrors.length - 5} more</p>
                  )}
                </div>
              ) : (
                <p>No tradeable rows in the current scope.</p>
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead className="text-right">Spot</TableHead>
                    <TableHead>Expiry</TableHead>
                    <TableHead className="text-right">Call Strike</TableHead>
                    <TableHead className="text-right">Call Prem</TableHead>
                    <TableHead className="text-right">Call Δ</TableHead>
                    <TableHead className="text-right">Put Strike</TableHead>
                    <TableHead className="text-right">Put Prem</TableHead>
                    <TableHead className="text-right">Put Δ</TableHead>
                    <TableHead className="text-right">Avg IV</TableHead>
                    <TableHead className="text-right">Max Profit</TableHead>
                    <TableHead className="text-right">BE (−)</TableHead>
                    <TableHead className="text-right">BE (+)</TableHead>
                    <TableHead className="text-right">POP</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => {
                    const cfg = STRATEGIES[strategy];
                    const lotMul = perLot ? r.lotSize : 1;
                    const callPremView = r.callPrem * lotMul;
                    const putPremView = r.putPrem * lotMul;
                    const maxProfitView =
                      r.maxProfit == null ? null : r.maxProfit * lotMul;
                    const callIsRed = cfg.callAction === "SELL";
                    const putIsRed = cfg.putAction === "SELL";
                    return (
                      <TableRow
                        key={r.id}
                        className={i % 2 === 0 ? "bg-muted/30" : ""}
                      >
                        <TableCell className="font-medium">
                          {r.underlying}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtSpot(r.spot)}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {r.expiry}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {r.callStrike}{" "}
                          <span className="text-muted-foreground">CE</span>
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono tabular-nums",
                            callIsRed
                              ? "text-rose-600 dark:text-rose-400"
                              : "text-emerald-600 dark:text-emerald-400",
                          )}
                        >
                          {fmtPrice(callPremView)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtPrice(r.callDelta, 2)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {r.putStrike}{" "}
                          <span className="text-muted-foreground">PE</span>
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono tabular-nums",
                            putIsRed
                              ? "text-rose-600 dark:text-rose-400"
                              : "text-emerald-600 dark:text-emerald-400",
                          )}
                        >
                          {fmtPrice(putPremView)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtPrice(r.putDelta, 2)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtIvPct(r.avgIv)}
                        </TableCell>
                        <TableCell className="text-right">
                          {maxProfitView == null ? (
                            <span className="text-emerald-600 dark:text-emerald-400">
                              ∞
                            </span>
                          ) : (
                            <span className="rounded-md border border-emerald-500/40 bg-emerald-500/5 px-1.5 py-0.5 font-mono text-xs tabular-nums text-emerald-700 dark:text-emerald-300">
                              {fmtPrice(maxProfitView)}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtSpot(r.beMinus)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtSpot(r.bePlus)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtPct(r.pop)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            onClick={() => openTrade(r)}
                            className="h-7 px-3 text-xs"
                          >
                            Trade Now
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Active straddles & strangles */}
      <ActiveStraddlesSection
        groups={activeGroups}
        spotByUnderlying={spotByUnderlying}
        loading={positionsQuery.isLoading}
        onClose={openClose}
        onAdd={openAdd}
      />

      {/* Recent / Closed today */}
      <RecentBasketsSection
        recentClosed={recentClosed}
        allRecents={recents}
        onReopen={openReopen}
        onDiscard={removeRecent}
      />

      {/* Basket dialog */}
      <BasketOrderDialog
        open={basketOpen}
        onOpenChange={setBasketOpen}
        legs={basketLegs}
        strategy={basketStrategy}
        mode={mode === "sandbox" ? "sandbox" : "live"}
        onComplete={handleBasketComplete}
      />
    </div>
  );
}

// ─── Active straddles section ───────────────────────────────────────────

function ActiveStraddlesSection({
  groups,
  spotByUnderlying,
  loading,
  onClose,
  onAdd,
}: {
  groups: ActiveGroup[];
  spotByUnderlying: Map<string, number>;
  loading: boolean;
  onClose: (g: ActiveGroup) => void;
  onAdd: (g: ActiveGroup) => void;
}) {
  const [confirming, setConfirming] = useState<{
    kind: "close" | "add";
    group: ActiveGroup;
  } | null>(null);

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wider">
              Active Straddles &amp; Strangles
            </h2>
            <p className="text-xs text-muted-foreground">
              Auto-detected from your open positions. Pairs first, orphans
              last.
            </p>
          </div>
          <Badge variant="outline" className="text-xs">
            {groups.length} group{groups.length === 1 ? "" : "s"}
          </Badge>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading positions…
          </div>
        ) : groups.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No open straddle / strangle positions detected.
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {groups.map((g) => (
              <ActiveGroupCard
                key={g.id}
                group={g}
                spot={spotByUnderlying.get(g.underlying)}
                onClose={() => setConfirming({ kind: "close", group: g })}
                onAdd={() => setConfirming({ kind: "add", group: g })}
              />
            ))}
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(o) => {
          if (!o) setConfirming(null);
        }}
        title={
          confirming?.kind === "close"
            ? "Close pair at MARKET"
            : "Add lots to pair"
        }
        description={
          confirming?.group ? (
            <>
              {confirming.kind === "close"
                ? "Square off both legs at MARKET? "
                : "Open the basket dialog to add lots in the same direction. "}
              <span className="font-mono">
                {confirming.group.underlying} {confirming.group.expiryDisplay}
              </span>{" "}
              ({confirming.group.type}).
            </>
          ) : null
        }
        confirmLabel={confirming?.kind === "close" ? "Close" : "Continue"}
        cancelLabel="Cancel"
        variant={confirming?.kind === "close" ? "destructive" : "default"}
        onConfirm={() => {
          if (!confirming) return;
          if (confirming.kind === "close") onClose(confirming.group);
          else onAdd(confirming.group);
          setConfirming(null);
        }}
      />
    </Card>
  );
}

function ActiveGroupCard({
  group,
  spot,
  onClose,
  onAdd,
}: {
  group: ActiveGroup;
  spot: number | undefined;
  onClose: () => void;
  onAdd: () => void;
}) {
  const isLong = group.sign === "L";
  const isPair = group.ce && group.pe;

  // Net premium handling: net debit/credit math is sign-aware in the
  // grouping layer (`netEntryPremium`). Here we just label it.
  const entryAbs = Math.abs(group.netEntryPremium);
  const currentAbs = Math.abs(group.netCurrentPremium);
  const premiumLabel = isLong ? "Debit paid" : "Credit received";

  // BE distance — only show if we have a reasonable spot reference.
  let beHint: string | null = null;
  if (spot && spot > 0 && isPair) {
    // Approximate breakeven for a non-debit/credit-known pair: lower BE =
    // putStrike − totalPremium, upper BE = callStrike + totalPremium.
    const totalPrem =
      Number(group.ce!.position.average_price) +
      Number(group.pe!.position.average_price);
    const beLow = group.pe!.strike - totalPrem;
    const beHigh = group.ce!.strike + totalPrem;
    if (spot >= beLow && spot <= beHigh) {
      beHint = `Spot ${spot.toFixed(2)} between BE ${beLow.toFixed(0)} – ${beHigh.toFixed(0)}`;
    } else {
      beHint = `Spot ${spot.toFixed(2)} OUTSIDE BE ${beLow.toFixed(0)} – ${beHigh.toFixed(0)}`;
    }
  }

  return (
    <div className="space-y-2 rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                "text-xs",
                isLong
                  ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
                  : "border-rose-500/40 text-rose-700 dark:text-rose-300",
              )}
            >
              {group.type}
            </Badge>
            <span className="font-mono text-sm font-semibold">
              {group.underlying}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {group.expiryDisplay} · {group.product}
            </span>
          </div>
          {beHint && (
            <div
              className={cn(
                "text-[11px]",
                beHint.includes("OUTSIDE")
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-emerald-600 dark:text-emerald-400",
              )}
            >
              {beHint}
            </div>
          )}
        </div>
        <div
          className={cn(
            "font-mono text-sm font-semibold tabular-nums",
            group.mtm >= 0
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400",
          )}
        >
          {group.mtm >= 0 ? "+" : ""}
          {group.mtm.toFixed(2)}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <LegMini leg={group.ce} optionType="CE" />
        <LegMini leg={group.pe} optionType="PE" />
      </div>

      <div className="flex items-center justify-between border-t pt-2 text-[11px]">
        <span className="text-muted-foreground">
          {premiumLabel}: {entryAbs.toFixed(2)} → now {currentAbs.toFixed(2)}
        </span>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 px-2 text-xs"
            onClick={onAdd}
            disabled={!isPair}
            title={isPair ? "Add lots in same direction" : "Pair incomplete"}
          >
            <Plus className="h-3 w-3" />
            Add
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={onClose}
          >
            <X className="h-3 w-3" />
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}

function LegMini({
  leg,
  optionType,
}: {
  leg: ActiveGroup["ce"] | ActiveGroup["pe"];
  optionType: OptionType;
}) {
  if (!leg) {
    return (
      <div className="rounded-md border border-dashed bg-muted/20 px-2 py-1 text-center text-muted-foreground">
        no {optionType} leg
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-muted/20 px-2 py-1">
      <div className="flex items-center justify-between">
        <span className="font-mono font-semibold">
          {leg.strike} {optionType}
        </span>
        <span
          className={cn(
            "rounded px-1 text-[10px] font-bold text-white",
            leg.sign === "L" ? "bg-emerald-500" : "bg-rose-500",
          )}
        >
          {leg.sign === "L" ? "LONG" : "SHORT"} {leg.qty}
        </span>
      </div>
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>Avg {Number(leg.position.average_price).toFixed(2)}</span>
        <span>LTP {Number(leg.position.ltp).toFixed(2)}</span>
      </div>
    </div>
  );
}

// ─── Recent / Closed today section ───────────────────────────────────────

function RecentBasketsSection({
  recentClosed,
  allRecents,
  onReopen,
  onDiscard,
}: {
  recentClosed: RecentBasket[];
  allRecents: RecentBasket[];
  onReopen: (b: RecentBasket) => void;
  onDiscard: (id: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const list = showAll ? allRecents : recentClosed;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wider">
              {showAll ? "Recent baskets" : "Closed today"}
            </h2>
            <p className="text-xs text-muted-foreground">
              {showAll
                ? "Every basket fired from this page (newest first)."
                : "Baskets where every leg has zero qty in the current snapshot."}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAll((v) => !v)}
            className="text-xs"
          >
            {showAll ? "Closed only" : "Show all recents"}
          </Button>
        </div>

        {list.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            {showAll
              ? "No baskets fired from this page yet."
              : "No closed-today baskets to reopen."}
          </p>
        ) : (
          <div className="space-y-1.5">
            {list.map((b) => (
              <div
                key={b.id + "-" + b.ts}
                className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/20 px-3 py-2 text-xs"
              >
                <Badge variant="outline" className="text-[10px]">
                  {b.strategyLabel}
                </Badge>
                <span className="font-mono font-semibold">{b.underlying}</span>
                <span className="text-muted-foreground">{b.expiry}</span>
                <span className="text-muted-foreground">·</span>
                <span className="text-muted-foreground">
                  {b.legs
                    .map(
                      (l) =>
                        `${l.action[0]}${l.optionType} ${l.strike} × ${l.lots}`,
                    )
                    .join(", ")}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 gap-1 px-2 text-[11px]"
                    onClick={() => onReopen(b)}
                  >
                    <RotateCcw className="h-3 w-3" />
                    Reopen
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-1.5 text-muted-foreground hover:text-destructive"
                    onClick={() => onDiscard(b.id)}
                    aria-label="Discard recent basket"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
