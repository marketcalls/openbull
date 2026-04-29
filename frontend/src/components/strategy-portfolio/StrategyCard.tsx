/**
 * Single strategy card in the Portfolio list.
 *
 * Two states: collapsed (header only — name, meta, aggregate P&L,
 * actions) and expanded (per-leg table). Collapsed by default; click
 * the chevron to expand.
 *
 * The card consumes the shared liveLtpMap from the parent page rather
 * than holding its own WebSocket — one ref-counted subscription serves
 * the whole portfolio so a symbol used in three strategies streams
 * once, not three times.
 *
 * For ACTIVE strategies, P&L is unrealized (current LTP vs entry). For
 * CLOSED / EXPIRED, realized (exit_price vs entry, no live data).
 *
 * "View" navigates back to the builder via /tools/strategybuilder?load=<id>.
 * "Close" opens the close-dialog (page-owned). "Delete" hard-deletes
 * after a confirm() prompt.
 */

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { deleteStrategy } from "@/api/strategies";
import { LivePriceCell } from "@/components/strategy-builder/LivePriceCell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { Strategy, StrategyLeg } from "@/types/strategy";

interface Props {
  strategy: Strategy;
  /** ``${exchange}:${symbol}`` → live LTP. Shared across all cards. */
  liveLtpMap: Map<string, number | undefined>;
  /** Called when the user clicks "Close strategy" — page handles the dialog. */
  onCloseClick: (strategy: Strategy) => void;
  /** Called after a successful delete so the parent can drop the row. */
  onDeleted: (id: number) => void;
}

function ltpKey(symbol: string | null | undefined, exchange: string): string {
  return `${exchange}:${symbol ?? ""}`;
}

function actionSign(action: "BUY" | "SELL"): 1 | -1 {
  return action === "BUY" ? 1 : -1;
}

function signTone(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

/** Compute leg's contribution to net premium and (un)realized P&L.
 *
 *   contribPremium = sign × lots × lot_size × entry_price
 *   liveValue      = sign × lots × lot_size × current   (current = exit_price for closed legs, ltp for open)
 *   pnl            = liveValue − contribPremium
 */
function computeLegPnl(
  leg: StrategyLeg,
  ltp: number | undefined,
): { current: number | null; pnl: number | null; entry: number } {
  const sign = actionSign(leg.action);
  const lotSize = leg.lot_size ?? 0;
  const multiplier = sign * leg.lots * lotSize;
  const entry = multiplier * (leg.entry_price ?? 0);

  // Closed/expired legs use exit_price as the source-of-truth current
  let currentPrice: number | null = null;
  if (leg.status === "closed" || leg.status === "expired") {
    currentPrice =
      leg.exit_price !== undefined && leg.exit_price !== null
        ? leg.exit_price
        : null;
  } else {
    currentPrice =
      ltp !== undefined && Number.isFinite(ltp) ? ltp : null;
  }

  if (currentPrice === null) {
    return { current: null, pnl: null, entry };
  }
  const current = multiplier * currentPrice;
  return { current, pnl: current - entry, entry };
}

export function StrategyCard({
  strategy,
  liveLtpMap,
  onCloseClick,
  onDeleted,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const navigate = useNavigate();

  const exchange = strategy.exchange.toUpperCase();
  const isActive = strategy.status === "active";

  const aggregate = useMemo(() => {
    let entryPremium = 0;
    let currentValue = 0;
    let pnl = 0;
    let allCurrent = true;
    for (const leg of strategy.legs) {
      const ltp = leg.symbol
        ? liveLtpMap.get(ltpKey(leg.symbol, exchange))
        : undefined;
      const r = computeLegPnl(leg, ltp);
      entryPremium += r.entry;
      if (r.current === null) allCurrent = false;
      else {
        currentValue += r.current;
      }
      if (r.pnl === null) allCurrent = false;
      else {
        pnl += r.pnl;
      }
    }
    return {
      entryPremium,
      currentValue: allCurrent ? currentValue : null,
      pnl: allCurrent ? pnl : null,
    };
  }, [strategy.legs, liveLtpMap, exchange]);

  const handleView = () => {
    navigate(`/tools/strategybuilder?load=${strategy.id}`);
  };

  const handleDelete = async () => {
    if (
      !window.confirm(
        `Delete '${strategy.name}'? This only removes the saved record — it does not affect any open broker positions.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      await deleteStrategy(strategy.id);
      toast.success(`Deleted '${strategy.name}'`);
      onDeleted(strategy.id);
    } catch (e) {
      const msg =
        (e as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (e as { message?: string })?.message ??
        "Delete failed";
      toast.error(msg);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-3 sm:p-4">
        {/* ── Header row ──────────────────────────────────────────────── */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <button
              type="button"
              onClick={() => setExpanded((x) => !x)}
              className="mt-0.5 rounded p-0.5 text-muted-foreground hover:bg-muted"
              aria-label={expanded ? "Collapse" : "Expand"}
            >
              {expanded ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
            <div>
              <p className="text-sm font-semibold leading-tight">
                {strategy.name}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                <Badge variant="secondary">#{strategy.id}</Badge>
                <Badge variant="outline">{strategy.underlying}</Badge>
                <Badge variant="outline">{strategy.exchange}</Badge>
                {strategy.expiry_date && (
                  <Badge variant="outline">{strategy.expiry_date}</Badge>
                )}
                <Badge variant="outline" className="capitalize">
                  {strategy.mode}
                </Badge>
                <Badge
                  variant="outline"
                  className={cn(
                    "capitalize",
                    strategy.status === "active" &&
                      "border-emerald-500/40 text-emerald-600 dark:text-emerald-400",
                    strategy.status === "closed" &&
                      "border-zinc-500/40 text-muted-foreground",
                    strategy.status === "expired" &&
                      "border-amber-500/40 text-amber-600 dark:text-amber-400",
                  )}
                >
                  {strategy.status}
                </Badge>
                <span className="text-muted-foreground">
                  {strategy.legs.length} leg
                  {strategy.legs.length === 1 ? "" : "s"}
                </span>
              </div>
            </div>
          </div>

          {/* Aggregate P&L */}
          <div className="flex flex-col items-end gap-0.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {isActive ? "Unrealized P&L" : "Realized P&L"}
            </span>
            <LivePriceCell
              value={aggregate.pnl}
              format={(v) => `${v >= 0 ? "+" : ""}₹ ${v.toFixed(2)}`}
              className={cn(
                aggregate.pnl !== null && signTone(aggregate.pnl),
                "text-base font-bold",
              )}
            />
            <span className="text-[10px] tabular-nums text-muted-foreground">
              Entry {aggregate.entryPremium.toFixed(2)}
              {aggregate.currentValue !== null && (
                <> · Now {aggregate.currentValue.toFixed(2)}</>
              )}
            </span>
          </div>
        </div>

        {/* ── Action buttons ─────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleView}>
            View in builder
          </Button>
          {isActive && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onCloseClick(strategy)}
            >
              Close strategy
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDelete}
            disabled={deleting}
            className="text-muted-foreground hover:text-destructive"
          >
            {deleting ? "Deleting…" : "Delete"}
          </Button>
          {strategy.notes && (
            <span
              className="ml-auto max-w-[40ch] truncate text-[11px] text-muted-foreground"
              title={strategy.notes}
            >
              {strategy.notes}
            </span>
          )}
        </div>

        {/* ── Expanded leg detail ────────────────────────────────────── */}
        {expanded && (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Leg</TableHead>
                  <TableHead className="text-right">Strike</TableHead>
                  <TableHead className="text-right">Lots</TableHead>
                  <TableHead className="text-right">Entry ₹</TableHead>
                  <TableHead className="text-right">Current</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategy.legs.map((leg, idx) => {
                  const ltp = leg.symbol
                    ? liveLtpMap.get(ltpKey(leg.symbol, exchange))
                    : undefined;
                  const r = computeLegPnl(leg, ltp);
                  const ce = leg.option_type === "CE";
                  return (
                    <TableRow key={leg.id ?? idx}>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1.5">
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
                            {leg.option_type}
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
                        {leg.lots} × {leg.lot_size ?? "?"}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {leg.entry_price?.toFixed(2) ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        {leg.status === "closed" || leg.status === "expired" ? (
                          <span className="font-mono tabular-nums">
                            {leg.exit_price?.toFixed(2) ?? "—"}
                          </span>
                        ) : (
                          <LivePriceCell value={ltp} />
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <LivePriceCell
                          value={r.pnl}
                          format={(v) =>
                            `${v >= 0 ? "+" : ""}${v.toFixed(2)}`
                          }
                          className={cn(
                            r.pnl !== null && signTone(r.pnl),
                            "font-semibold",
                          )}
                        />
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] capitalize",
                            leg.status === "closed" &&
                              "border-zinc-500/40 text-muted-foreground",
                            leg.status === "expired" &&
                              "border-amber-500/40 text-amber-600 dark:text-amber-400",
                          )}
                        >
                          {leg.status ?? "open"}
                        </Badge>
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
  );
}
