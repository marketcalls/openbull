/**
 * Live P&L tab — WebSocket-streamed leg LTPs with flashing cells and a
 * tick-by-tick aggregated P&L total.
 *
 * Subscriptions are *tab-scoped*: useMarketData runs only when this tab
 * is active (the parent gates via the `enabled` prop). That keeps the
 * builder from holding open a WS just because the page is mounted, and
 * matches OpenAlgo's design call to keep WS scope narrow so unrelated
 * tabs don't see streaming-induced re-renders.
 *
 * Symbols come from the builder legs (not the snapshot), so the table
 * keeps responding even if the snapshot is mid-fetch. Entry prices come
 * from the legs too — the snapshot's entry-price is just a server-side
 * derivation of what the user typed in.
 *
 * Signed P&L per leg:
 *   pnl = (ltp - entry) * lots * lot_size * sign(action)
 * Total = Σ leg pnl. Same signing convention as the snapshot endpoint
 * and useStrategySnapshot, so numbers agree across tabs.
 */

import { useMemo } from "react";

import { LivePriceCell } from "@/components/strategy-builder/LivePriceCell";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMarketData } from "@/hooks/useMarketData";
import { cn } from "@/lib/utils";
import type { Action, OptionType } from "@/types/strategy";

export interface PnlLeg {
  /** Stable React key. */
  id: string;
  symbol: string;
  exchange: string;
  action: Action;
  optionType: OptionType;
  strike: number;
  lots: number;
  lotSize: number;
  /** Entry price the user committed to (typed or auto-prefilled). */
  entryPrice: number;
}

interface Props {
  legs: PnlLeg[];
  /** Drive subscription on/off — parent passes `activeTab === "pnl"`. */
  enabled: boolean;
}

function actionSign(action: Action): 1 | -1 {
  return action === "BUY" ? 1 : -1;
}

function signTone(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

/** Map key matches useMarketData's internal `${exchange}:${symbol}` key. */
function ltpKey(symbol: string, exchange: string) {
  return `${exchange}:${symbol}`;
}

export function PnLTab({ legs, enabled }: Props) {
  // Build the subscription set from the legs. Dedup happens inside
  // useMarketData via Set, but we still pre-dedup here so a 4-leg
  // strategy with a repeated symbol shows up as a single row.
  const subscriptionSymbols = useMemo(() => {
    const seen = new Set<string>();
    const out: Array<{ symbol: string; exchange: string }> = [];
    for (const leg of legs) {
      if (!leg.symbol) continue;
      const k = ltpKey(leg.symbol, leg.exchange);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ symbol: leg.symbol, exchange: leg.exchange });
    }
    return out;
  }, [legs]);

  const { data, state, isAuthenticated, error } = useMarketData({
    symbols: subscriptionSymbols,
    mode: "Quote",
    enabled: enabled && subscriptionSymbols.length > 0,
  });

  // Per-leg P&L, computed on every tick.
  const legPnls = useMemo(() => {
    return legs.map((leg) => {
      const tick = leg.symbol
        ? data.get(ltpKey(leg.symbol, leg.exchange))
        : undefined;
      const ltp = tick?.data.ltp ?? null;
      const lastUpdate = tick?.lastUpdate ?? 0;
      const sign = actionSign(leg.action);
      const multiplier = sign * leg.lots * leg.lotSize;
      const pnl =
        ltp != null && Number.isFinite(ltp)
          ? (ltp - leg.entryPrice) * multiplier
          : null;
      // Signed entry premium contribution: BUY adds debit, SELL adds credit
      const entryContribution = multiplier * leg.entryPrice;
      return { leg, ltp, pnl, lastUpdate, entryContribution };
    });
  }, [legs, data]);

  const total = useMemo(() => {
    let pnl = 0;
    let pnlValid = false;
    let entry = 0;
    for (const r of legPnls) {
      if (r.pnl != null) {
        pnl += r.pnl;
        pnlValid = true;
      }
      entry += r.entryContribution;
    }
    return { pnl: pnlValid ? pnl : null, entryPremium: entry };
  }, [legPnls]);

  // Stale-tick detection: if the most recent tick across all symbols is
  // older than 30s, the market is likely closed / between sessions —
  // surface that to the user instead of showing eerily-frozen numbers.
  const newestTickAgeSec = useMemo(() => {
    let newest = 0;
    for (const r of legPnls) {
      if (r.lastUpdate > newest) newest = r.lastUpdate;
    }
    if (newest === 0) return null;
    return Math.floor((Date.now() - newest) / 1000);
  }, [legPnls]);

  if (legs.length === 0) {
    return (
      <div className="flex h-[280px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
        <p className="text-sm">No legs to stream.</p>
        <p className="text-xs">
          Add legs and pick strikes — the P&L tab subscribes automatically.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Connection state */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <ConnectionBadge state={state} authenticated={isAuthenticated} />
        {error && (
          <span className="rounded-md bg-destructive/10 px-2 py-1 text-destructive">
            {error}
          </span>
        )}
        {newestTickAgeSec !== null && newestTickAgeSec > 30 && (
          <span className="rounded-md bg-amber-500/10 px-2 py-1 text-amber-600 dark:text-amber-400">
            Last tick {newestTickAgeSec}s ago — market may be closed
          </span>
        )}
        <span className="ml-auto text-muted-foreground">
          {subscriptionSymbols.length} unique symbol
          {subscriptionSymbols.length === 1 ? "" : "s"} subscribed (Quote mode)
        </span>
      </div>

      {/* Per-leg table */}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Leg</TableHead>
              <TableHead className="text-right">Strike</TableHead>
              <TableHead className="text-right">Lots</TableHead>
              <TableHead className="text-right">Entry ₹</TableHead>
              <TableHead className="text-right">LTP</TableHead>
              <TableHead className="text-right">Change from entry</TableHead>
              <TableHead className="text-right">P&L (₹)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {legPnls.map(({ leg, ltp, pnl }) => {
              const ce = leg.optionType === "CE";
              const change = ltp != null ? ltp - leg.entryPrice : null;
              return (
                <TableRow key={leg.id}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                          leg.action === "BUY"
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                            : "bg-red-500/15 text-red-600 dark:text-red-400",
                        )}
                      >
                        {leg.action}
                      </span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                          ce
                            ? "bg-red-500/10 text-red-600 dark:text-red-400"
                            : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                        )}
                      >
                        {leg.optionType}
                      </span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {leg.symbol}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {leg.strike}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {leg.lots} × {leg.lotSize}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {leg.entryPrice.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right">
                    <LivePriceCell value={ltp} />
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right font-mono tabular-nums",
                      change !== null && signTone(change),
                    )}
                  >
                    {change === null
                      ? "—"
                      : `${change >= 0 ? "+" : ""}${change.toFixed(2)}`}
                  </TableCell>
                  <TableCell className="text-right">
                    <LivePriceCell
                      value={pnl}
                      format={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`}
                      className={cn(
                        pnl !== null && signTone(pnl),
                        "font-semibold",
                      )}
                    />
                  </TableCell>
                </TableRow>
              );
            })}

            {/* Aggregate row */}
            <TableRow className="border-t-2 border-border bg-muted/40">
              <TableCell colSpan={3} className="font-medium">
                Total
              </TableCell>
              <TableCell
                className={cn(
                  "text-right font-mono tabular-nums",
                  total.entryPremium > 0
                    ? ""
                    : "text-emerald-600 dark:text-emerald-400",
                )}
                title={
                  total.entryPremium >= 0
                    ? "Net debit at entry"
                    : "Net credit at entry"
                }
              >
                {total.entryPremium.toFixed(2)}
              </TableCell>
              <TableCell />
              <TableCell />
              <TableCell className="text-right">
                <LivePriceCell
                  value={total.pnl}
                  format={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`}
                  className={cn(
                    total.pnl !== null && signTone(total.pnl),
                    "text-base font-bold",
                  )}
                />
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function ConnectionBadge({
  state,
  authenticated,
}: {
  state: string;
  authenticated: boolean;
}) {
  const label =
    state === "idle"
      ? "Idle"
      : state === "connecting"
        ? "Connecting…"
        : state === "authenticating"
          ? "Authenticating…"
          : authenticated
            ? "Live"
            : state === "error"
              ? "Error"
              : state === "closed"
                ? "Closed"
                : state;
  const tone =
    authenticated
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : state === "error"
        ? "bg-red-500/15 text-red-700 dark:text-red-300"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium",
        tone,
      )}
    >
      {authenticated && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
      )}
      WS: {label}
    </span>
  );
}
