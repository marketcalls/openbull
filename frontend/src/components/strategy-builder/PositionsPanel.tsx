/**
 * Strategy Positions panel — left side of the Payoff tab. Mirrors openalgo's
 * PositionsPanel: per-leg checkbox row (toggle inclusion in the payoff
 * computation), Reset button, and a stats block (POP, Max Profit, Max Loss,
 * RR ratio, Breakevens, Net Credit/Debit, Total P&L).
 *
 * Math anchor: every stat is recomputed from the SAME helpers PayoffChart
 * uses (lib/black76, lib/probabilityOfProfit). The shared inputs ensure
 * the panel and the chart agree to the cent — no two-source-of-truth bug.
 *
 * What it does NOT compute: estimated margin. That needs a broker round
 * trip via /api/v1/margin. Wired separately or left blank.
 */

import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  asymptoticSlopes,
  findBreakevens,
  payoffAtExpiry,
  type PayoffLeg,
} from "@/lib/black76";
import { probabilityOfProfit } from "@/lib/probabilityOfProfit";
import { cn } from "@/lib/utils";
import type { SnapshotLegOutput } from "@/types/strategy";

interface Props {
  snapshotLegs: SnapshotLegOutput[];
  entryPriceBySymbol: Record<string, number>;
  spot: number;
  /** Symbols of legs the user wants INCLUDED in the payoff calc. */
  enabledSymbols: Set<string>;
  onToggleSymbol: (symbol: string) => void;
  onToggleAll: (enable: boolean) => void;
  onReset: () => void;
}

interface EnrichedLeg {
  symbol: string;
  action: "BUY" | "SELL";
  optionType: "CE" | "PE";
  strike: number;
  lots: number;
  lotSize: number;
  entryPrice: number;
  ivDecimal: number;
  dteYears: number;
  ltp: number;
  unrealizedPnl: number;
  expiry: string;
}

function buildEnriched(
  snapshotLegs: SnapshotLegOutput[],
  entryPriceBySymbol: Record<string, number>,
): EnrichedLeg[] {
  const out: EnrichedLeg[] = [];
  for (const l of snapshotLegs) {
    if (
      !l.strike ||
      !l.option_type ||
      l.ltp == null ||
      l.days_to_expiry == null
    ) {
      continue;
    }
    const entry = entryPriceBySymbol[l.symbol] ?? l.ltp;
    const sign = l.action === "BUY" ? 1 : -1;
    const upnl = (l.ltp - entry) * sign * l.lots * l.lot_size;
    out.push({
      symbol: l.symbol,
      action: l.action,
      optionType: l.option_type,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lot_size,
      entryPrice: entry > 0 ? entry : l.ltp,
      ivDecimal: (l.implied_volatility ?? 0) / 100,
      dteYears: Math.max((l.days_to_expiry ?? 0) / 365, 0.0001),
      ltp: l.ltp,
      unrealizedPnl: upnl,
      expiry: l.expiry_date ?? "",
    });
  }
  return out;
}

function fmtIN(n: number, opts?: { sign?: boolean; decimals?: number }): string {
  const abs = Math.abs(n);
  const fmt = abs.toLocaleString("en-IN", {
    minimumFractionDigits: opts?.decimals ?? 2,
    maximumFractionDigits: opts?.decimals ?? 2,
  });
  if (opts?.sign && n > 0) return `+${fmt}`;
  if (n < 0) return `−${fmt}`;
  return fmt;
}

function fmtInfinity(n: number, sign: "+" | "-"): string {
  if (!Number.isFinite(n)) return sign === "+" ? "Unlimited" : "Unlimited loss";
  return `${sign === "+" && n > 0 ? "+" : ""}${fmtIN(n)}`;
}

export function PositionsPanel({
  snapshotLegs,
  entryPriceBySymbol,
  spot,
  enabledSymbols,
  onToggleSymbol,
  onToggleAll,
  onReset,
}: Props) {
  const allEnriched = useMemo(
    () => buildEnriched(snapshotLegs, entryPriceBySymbol),
    [snapshotLegs, entryPriceBySymbol],
  );

  const enabledEnriched = useMemo(
    () => allEnriched.filter((l) => enabledSymbols.has(l.symbol)),
    [allEnriched, enabledSymbols],
  );

  const allSelected =
    allEnriched.length > 0 && enabledEnriched.length === allEnriched.length;
  const someSelected =
    enabledEnriched.length > 0 && enabledEnriched.length < allEnriched.length;

  // ── Stats over the *enabled* leg set ────────────────────────────────
  const stats = useMemo(() => {
    if (enabledEnriched.length === 0 || spot <= 0) {
      return null;
    }
    const payoffLegs: PayoffLeg[] = enabledEnriched.map((l) => ({
      action: l.action,
      optionType: l.optionType,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lotSize,
      entryPrice: l.entryPrice,
    }));

    // Sample range — same heuristic the chart uses, so values agree.
    const strikeMin = Math.min(...enabledEnriched.map((l) => l.strike));
    const strikeMax = Math.max(...enabledEnriched.map((l) => l.strike));
    const lo = Math.min(strikeMin * 0.95, spot * 0.85);
    const hi = Math.max(strikeMax * 1.05, spot * 1.15);
    const steps = 600;
    const dx = (hi - lo) / Math.max(steps - 1, 1);
    const curve: { spot: number; pnl: number }[] = new Array(steps);
    let yMin = Infinity;
    let yMax = -Infinity;
    for (let i = 0; i < steps; i++) {
      const x = lo + i * dx;
      const y = payoffAtExpiry(payoffLegs, x);
      curve[i] = { spot: x, pnl: y };
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
    const breakevens = findBreakevens(curve);
    const slopes = asymptoticSlopes(payoffLegs);
    const maxProfit = slopes.right > 0 || slopes.left < 0 ? Infinity : yMax;
    const maxLoss = slopes.right < 0 || slopes.left > 0 ? -Infinity : yMin;

    // Net credit / debit (signed rupee outlay):
    //   action=BUY  → leg pays entryPrice (debit, positive)
    //   action=SELL → leg receives entryPrice (credit, negative)
    let netDebit = 0;
    for (const l of enabledEnriched) {
      const sign = l.action === "BUY" ? 1 : -1;
      netDebit += sign * l.entryPrice * l.lots * l.lotSize;
    }
    const netCreditValue = -netDebit;

    // Total live unrealised P&L
    const totalUpnl = enabledEnriched.reduce((s, l) => s + l.unrealizedPnl, 0);

    // Probability of Profit. Returns null when σ or T can't be derived
    // (e.g. legs missing IV before the first snapshot lands) — show "—".
    const pop = probabilityOfProfit({
      legs: payoffLegs,
      spot,
      legIvDecimals: enabledEnriched.map((l) => l.ivDecimal),
      legDteYears: enabledEnriched.map((l) => l.dteYears),
    });
    const popPct = pop ? pop.probability : null;

    // Risk:reward ratio. Capped: if either side is unlimited, RR is "—".
    let rrLabel = "—";
    if (Number.isFinite(maxProfit) && Number.isFinite(maxLoss) && maxLoss < 0) {
      const r = Math.abs(maxProfit / maxLoss);
      rrLabel = `1 : ${r.toFixed(2)}`;
    }

    return {
      maxProfit,
      maxLoss,
      breakevens,
      netDebit,
      netCreditValue,
      totalUpnl,
      pop: popPct,
      rrLabel,
    };
  }, [enabledEnriched, spot]);

  if (allEnriched.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 rounded-md border border-dashed bg-muted/10 p-6 text-center text-muted-foreground">
        <p className="text-sm">No positions to show.</p>
        <p className="text-[11px]">
          Add legs and pick strikes to see them here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header — title + Reset */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold tracking-tight">
          Strategy Positions
        </h3>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onReset}
          className="h-7 text-[11px]"
        >
          Reset
        </Button>
      </div>

      {/* Master toggle */}
      <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-xs hover:bg-muted/30">
        <input
          type="checkbox"
          checked={allSelected}
          ref={(el) => {
            if (el) el.indeterminate = someSelected;
          }}
          onChange={(e) => onToggleAll(e.target.checked)}
          className="h-3.5 w-3.5 cursor-pointer"
        />
        <span className="font-medium">Select all ({enabledEnriched.length}/{allEnriched.length})</span>
      </label>

      {/* Per-leg rows */}
      <div className="space-y-1.5">
        {allEnriched.map((leg) => {
          const enabled = enabledSymbols.has(leg.symbol);
          const isBuy = leg.action === "BUY";
          return (
            <label
              key={leg.symbol}
              className={cn(
                "flex cursor-pointer items-start gap-2 rounded-md border p-2 transition-colors",
                enabled
                  ? "border-border bg-card hover:bg-muted/30"
                  : "border-border/50 bg-muted/10 opacity-60 hover:opacity-90",
              )}
            >
              <input
                type="checkbox"
                checked={enabled}
                onChange={() => onToggleSymbol(leg.symbol)}
                className="mt-0.5 h-3.5 w-3.5 cursor-pointer"
              />
              <span
                className={cn(
                  "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-bold uppercase",
                  isBuy
                    ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                    : "bg-rose-500/15 text-rose-700 dark:text-rose-400",
                )}
              >
                {isBuy ? "B" : "S"}
              </span>
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <span>
                    {leg.lots}×{leg.lotSize}
                  </span>
                  <span className="text-muted-foreground">·</span>
                  <span className="truncate">
                    {leg.expiry} {leg.strike}
                    {leg.optionType}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[11px] tabular-nums">
                  <span className="text-muted-foreground">
                    Entry ₹{leg.entryPrice.toFixed(2)}
                  </span>
                  <span className="text-muted-foreground">·</span>
                  <span className="text-muted-foreground">
                    LTP ₹{leg.ltp.toFixed(2)}
                  </span>
                </div>
                <div
                  className={cn(
                    "text-[11px] font-semibold tabular-nums",
                    leg.unrealizedPnl > 0
                      ? "text-emerald-600 dark:text-emerald-400"
                      : leg.unrealizedPnl < 0
                      ? "text-rose-600 dark:text-rose-400"
                      : "text-muted-foreground",
                  )}
                >
                  P&L {fmtIN(leg.unrealizedPnl, { sign: true })}
                </div>
              </div>
            </label>
          );
        })}
      </div>

      {/* Stats */}
      {stats && (
        <div className="space-y-1.5 rounded-md border border-border bg-muted/20 p-3 text-[11px]">
          <Stat
            label="Prob. of Profit"
            value={
              stats.pop !== null ? `${(stats.pop * 100).toFixed(2)}%` : "—"
            }
            tone="muted"
          />
          <Stat
            label="Max. Profit"
            value={fmtInfinity(stats.maxProfit, "+")}
            tone={stats.maxProfit > 0 ? "profit" : "muted"}
          />
          <Stat
            label="Max. Loss"
            value={fmtInfinity(stats.maxLoss, "-")}
            tone={stats.maxLoss < 0 ? "loss" : "muted"}
          />
          <Stat label="Max. RR Ratio" value={stats.rrLabel} tone="muted" />
          <Stat
            label="Breakevens"
            value={
              stats.breakevens.length > 0
                ? stats.breakevens.map((b) => b.toFixed(0)).join(" · ")
                : "—"
            }
            tone="muted"
          />
          <Stat
            label="Total P&L"
            value={fmtIN(stats.totalUpnl, { sign: true })}
            tone={
              stats.totalUpnl > 0
                ? "profit"
                : stats.totalUpnl < 0
                ? "loss"
                : "muted"
            }
          />
          <Stat
            label={stats.netCreditValue > 0 ? "Net Credit" : "Net Debit"}
            value={fmtIN(Math.abs(stats.netCreditValue))}
            tone={stats.netCreditValue > 0 ? "profit" : "muted"}
          />
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: string;
  tone?: "profit" | "loss" | "muted";
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="font-medium text-muted-foreground">{label}</span>
      <span
        className={cn(
          "font-semibold tabular-nums",
          tone === "profit" && "text-emerald-600 dark:text-emerald-400",
          tone === "loss" && "text-rose-600 dark:text-rose-400",
        )}
      >
        {value}
      </span>
    </div>
  );
}
