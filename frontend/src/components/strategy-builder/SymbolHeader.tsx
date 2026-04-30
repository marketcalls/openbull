/**
 * SymbolHeader — horizontal strip of metric chips (Spot / Lot / DTE / ATM IV)
 * for the Strategy Builder. Drives off the chain context + snapshot the page
 * already fetches; no new network calls of its own.
 *
 * Why these four metrics:
 *   - Spot      : what the underlying is doing right now (chain.spot or
 *                 snapshot.spot_price — they're the same broker quote).
 *   - Lot Size  : drives margin/qty math; users want to confirm before
 *                 firing a basket.
 *   - DTE       : "days to expiry" — colours decisions for short-vol vs
 *                 long-vol templates. Computed from the picked expiry.
 *   - ATM IV    : averaged over the user's current legs at the ATM strike
 *                 (if any). Falls back to averaging across all legs, or
 *                 dashes when no snapshot has landed yet. This is a
 *                 deliberate simplification vs openalgo's dedicated ATM
 *                 IV fetch — openbull doesn't yet expose a per-strike IV
 *                 endpoint outside the snapshot.
 *
 * Futures Price + IV Percentile are intentionally NOT shown — adding them
 * means new endpoints (FUT quotes + 30-day IV history). Out of scope for
 * this visual port; SymbolHeader keeps a flexible MetricCell so they can
 * be added later without restructure.
 *
 * No math drift: every value is read from the existing chain/snapshot
 * pipeline. The Greeks / payoff / margin pipelines are untouched.
 */

import { useMemo } from "react";

import { cn } from "@/lib/utils";
import type { ChainContext } from "@/hooks/useChainContext";
import type { SnapshotLegOutput, SnapshotResponse } from "@/types/strategy";

interface Props {
  underlying: string;
  exchange: string;
  /** Human-readable expiry, e.g. "26-MAY-2026". Empty until picker resolves. */
  expiryDisplay: string;
  chain: ChainContext | null;
  snapshot: SnapshotResponse | null;
  loading?: boolean;
}

interface MetricCellProps {
  label: string;
  value: string;
  sub?: string;
  tone?: "primary" | "profit" | "warn" | "muted";
  accent?: boolean;
}

function MetricCell({
  label,
  value,
  sub,
  tone = "muted",
  accent = false,
}: MetricCellProps) {
  return (
    <div
      className={cn(
        "relative flex flex-col justify-center gap-1 px-4 py-3 transition",
        accent && "bg-gradient-to-b from-background to-muted/20",
      )}
    >
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-semibold tabular-nums leading-none",
          accent ? "text-2xl tracking-tight" : "text-base",
          tone === "primary" && "text-foreground",
          tone === "profit" && "text-emerald-600 dark:text-emerald-400",
          tone === "warn" && "text-amber-600 dark:text-amber-400",
          tone === "muted" && "text-foreground",
        )}
      >
        {value}
      </span>
      {sub && (
        <span className="text-[10px] font-medium text-muted-foreground">
          {sub}
        </span>
      )}
    </div>
  );
}

/**
 * Compute days-to-expiry from a "DD-MMM-YYYY" display string, floored at 0.
 * Returns null if the date can't be parsed (e.g. picker hasn't loaded yet).
 *
 * IST-aware: market expiry is at 15:30 IST = 10:00 UTC. We use that as the
 * cutoff so DTE rolls over at the right local moment.
 */
function calcDte(expiryDisplay: string, now: Date = new Date()): number | null {
  if (!expiryDisplay) return null;
  const parts = expiryDisplay.split("-");
  if (parts.length !== 3) return null;
  const [ddStr, mmm, yyyyStr] = parts;
  const dd = parseInt(ddStr, 10);
  const yyyy = parseInt(yyyyStr.length === 2 ? "20" + yyyyStr : yyyyStr, 10);
  const months: Record<string, number> = {
    JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
    JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
  };
  const mIdx = months[mmm.toUpperCase()];
  if (mIdx === undefined || !Number.isFinite(dd) || !Number.isFinite(yyyy)) {
    return null;
  }
  const expiryUtc = Date.UTC(yyyy, mIdx, dd, 10, 0, 0); // 15:30 IST
  const ms = expiryUtc - now.getTime();
  return Math.max(0, ms / (1000 * 60 * 60 * 24));
}

/**
 * Average IV across snapshot legs that sit at the ATM strike (if any).
 * Falls back to the all-leg average if no leg is at ATM. Returns null when
 * no snapshot is available yet.
 */
function deriveAtmIv(
  snapshotLegs: SnapshotLegOutput[] | undefined,
  atm: number | undefined,
): number | null {
  if (!snapshotLegs || snapshotLegs.length === 0) return null;
  const ivs = snapshotLegs
    .map((l) => l.implied_volatility)
    .filter((iv): iv is number => typeof iv === "number" && iv > 0);
  if (ivs.length === 0) return null;

  if (atm !== undefined) {
    const atmIvs = snapshotLegs
      .filter((l) => l.strike === atm && typeof l.implied_volatility === "number")
      .map((l) => l.implied_volatility as number)
      .filter((iv) => iv > 0);
    if (atmIvs.length > 0) {
      return atmIvs.reduce((a, b) => a + b, 0) / atmIvs.length;
    }
  }

  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}

export function SymbolHeader({
  underlying,
  exchange,
  expiryDisplay,
  chain,
  snapshot,
  loading = false,
}: Props) {
  const spot = snapshot?.spot_price ?? chain?.spot ?? null;
  const lotSize = chain?.lotSize ?? null;
  const dte = useMemo(() => calcDte(expiryDisplay), [expiryDisplay]);
  const atmIv = useMemo(
    () => deriveAtmIv(snapshot?.legs, chain?.atm),
    [snapshot?.legs, chain?.atm],
  );

  const hasData = spot !== null;
  const dteTone: MetricCellProps["tone"] =
    dte === null ? "muted" : dte <= 2 ? "warn" : "muted";

  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      {/* Top strip — symbol + status pill */}
      <div className="flex flex-wrap items-center gap-3 border-b bg-gradient-to-r from-muted/40 via-background to-background px-4 py-2.5">
        <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          Analyzing
        </span>
        <div className="inline-flex items-baseline gap-2">
          <span className="text-sm font-bold tracking-wide text-foreground">
            {underlying || "—"}
          </span>
          <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {exchange}
          </span>
          {expiryDisplay && (
            <>
              <span className="text-muted-foreground">·</span>
              <span className="text-xs font-semibold text-foreground">
                {expiryDisplay}
              </span>
            </>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <span
              className={cn(
                "absolute inline-flex h-full w-full rounded-full opacity-75",
                hasData && !loading
                  ? "animate-ping bg-emerald-400"
                  : loading
                  ? "animate-pulse bg-amber-400"
                  : "bg-muted-foreground/40",
              )}
            />
            <span
              className={cn(
                "relative inline-flex h-2 w-2 rounded-full",
                hasData && !loading
                  ? "bg-emerald-500"
                  : loading
                  ? "bg-amber-500"
                  : "bg-muted-foreground/60",
              )}
            />
          </span>
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {loading ? "Loading" : hasData ? "Live" : "Idle"}
          </span>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 divide-x divide-y sm:grid-cols-4 sm:divide-y-0">
        <MetricCell
          label="Spot"
          value={spot !== null ? spot.toFixed(2) : "—"}
          tone="primary"
          accent
        />
        <MetricCell
          label="Lot Size"
          value={lotSize !== null ? String(lotSize) : "—"}
          sub={
            lotSize !== null && spot !== null
              ? `notional ≈ ₹${(lotSize * spot).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`
              : undefined
          }
        />
        <MetricCell
          label="DTE"
          value={dte !== null ? `${dte.toFixed(0)}` : "—"}
          sub={dte !== null ? "days to expiry" : undefined}
          tone={dteTone}
        />
        <MetricCell
          label="ATM IV"
          value={atmIv !== null ? `${atmIv.toFixed(2)}%` : "—"}
          sub={atmIv === null && hasData ? "add legs to read" : undefined}
          tone="warn"
        />
      </div>
    </div>
  );
}
