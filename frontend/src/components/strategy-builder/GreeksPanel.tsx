/**
 * Greeks tab — per-leg row plus a sticky aggregate row.
 *
 * Per-leg numbers come straight from the snapshot (Black-76, solved IV,
 * units: theta/day and vega/1%). Aggregate is the snapshot's pre-computed
 * `totals` so we don't double up on the math the backend already did.
 *
 * Color convention: BUY in emerald, SELL in red, premium positive in
 * neutral / negative in emerald (it's a credit), theta uses sign tone,
 * unrealized PnL uses sign tone.
 */

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { SnapshotResponse } from "@/types/strategy";

interface Props {
  snapshot: SnapshotResponse | null;
  loading: boolean;
  error: string | null;
}

function fmt(n: number | undefined, digits: number): string {
  if (n === undefined || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function signTone(n: number | undefined): string {
  if (n === undefined || !Number.isFinite(n) || n === 0) return "";
  return n > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

export function GreeksPanel({ snapshot, loading, error }: Props) {
  if (loading && !snapshot) {
    return (
      <div className="flex h-[280px] items-center justify-center text-sm text-muted-foreground">
        Solving Greeks…
      </div>
    );
  }
  if (error && !snapshot) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!snapshot || snapshot.legs.length === 0) {
    return (
      <div className="flex h-[280px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
        <p className="text-sm">No Greeks yet.</p>
        <p className="text-xs">
          Add legs and pick strikes — Greeks update automatically.
        </p>
      </div>
    );
  }

  const totals = snapshot.totals;
  const showPnl = totals.unrealized_pnl !== undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Spot</span>
        <span className="font-mono font-semibold tabular-nums">
          {snapshot.spot_price.toFixed(2)}
        </span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">As of</span>
        <span className="font-mono text-xs tabular-nums">
          {new Date(snapshot.as_of).toLocaleTimeString()}
        </span>
      </div>

      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Leg</TableHead>
              <TableHead className="text-right">Strike</TableHead>
              <TableHead className="text-right">Lots</TableHead>
              <TableHead className="text-right">LTP</TableHead>
              <TableHead className="text-right">IV %</TableHead>
              <TableHead className="text-right">Delta</TableHead>
              <TableHead className="text-right">Gamma</TableHead>
              <TableHead className="text-right">Theta /day</TableHead>
              <TableHead className="text-right">Vega /1%</TableHead>
              <TableHead className="text-right">DTE</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {snapshot.legs.map((leg) => {
              const ce = leg.option_type === "CE";
              return (
                <TableRow key={`${leg.index}-${leg.symbol}`}>
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
                        {leg.option_type ?? "—"}
                      </span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {leg.symbol}
                      </span>
                      {leg.note && (
                        <span
                          className="text-[10px] text-amber-600 dark:text-amber-400"
                          title={leg.note}
                        >
                          *deep ITM
                        </span>
                      )}
                      {leg.error && (
                        <span
                          className="text-[10px] text-red-600 dark:text-red-400"
                          title={leg.error}
                        >
                          *err
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.strike, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {leg.lots} × {leg.lot_size}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.ltp ?? undefined, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.implied_volatility, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.greeks?.delta, 4)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.greeks?.gamma, 6)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right font-mono tabular-nums",
                      signTone(leg.greeks?.theta),
                    )}
                  >
                    {fmt(leg.greeks?.theta, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmt(leg.greeks?.vega, 2)}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                    {fmt(leg.days_to_expiry, 2)}
                  </TableCell>
                </TableRow>
              );
            })}

            {/* Aggregate row */}
            <TableRow className="border-t-2 border-border bg-muted/40 font-semibold">
              <TableCell colSpan={3} className="font-medium">
                Position
              </TableCell>
              <TableCell
                className={cn(
                  "text-right font-mono tabular-nums",
                  totals.premium_paid > 0
                    ? ""
                    : "text-emerald-600 dark:text-emerald-400",
                )}
                title={totals.premium_paid >= 0 ? "Net debit" : "Net credit"}
              >
                {totals.premium_paid >= 0 ? "" : ""}
                {fmt(totals.premium_paid, 2)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                —
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {fmt(totals.delta, 2)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {fmt(totals.gamma, 4)}
              </TableCell>
              <TableCell
                className={cn(
                  "text-right font-mono tabular-nums",
                  signTone(totals.theta),
                )}
              >
                {fmt(totals.theta, 2)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {fmt(totals.vega, 2)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                —
              </TableCell>
            </TableRow>

            {showPnl && (
              <TableRow className="bg-muted/40">
                <TableCell colSpan={3} className="text-xs text-muted-foreground">
                  Unrealized P&L (vs entry prices)
                </TableCell>
                <TableCell
                  colSpan={7}
                  className={cn(
                    "text-right font-mono text-base font-semibold tabular-nums",
                    signTone(totals.unrealized_pnl),
                  )}
                >
                  ₹ {fmt(totals.unrealized_pnl, 2)}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
