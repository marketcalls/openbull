/**
 * Close-strategy dialog.
 *
 * Marks the whole strategy ``status="closed"`` and stamps every still-open
 * leg with ``exit_price`` + ``exit_time`` + ``status="closed"``. The
 * server-side router auto-stamps ``closed_at`` when status flips to
 * "closed", so we don't have to.
 *
 * The user can edit the exit price per leg before confirming — defaults
 * to the live LTP we already have streaming. Sets a server-recorded
 * realized P&L into the saved JSON, which the Portfolio's "Closed" tab
 * can display later without re-pricing anything.
 *
 * Important: this does NOT square off the broker positions. It only
 * updates the saved-strategy bookkeeping. To actually close out, the
 * user must place opposing orders manually (or use a Phase 11
 * "square-off basket" feature). The dialog footnote calls this out.
 */

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { updateStrategy } from "@/api/strategies";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategy: Strategy | null;
  /** ``${exchange}:${symbol}`` → live LTP. Used to default the exit-price column. */
  liveLtpMap: Map<string, number | undefined>;
  onClosed: (updated: Strategy) => void;
}

interface ExitPriceForm {
  /** Indexed by leg's local id (or fallback string for legs without one). */
  [legKey: string]: string;
}

function legKey(leg: StrategyLeg, idx: number): string {
  return leg.id ?? `idx-${idx}`;
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

export function CloseStrategyDialog({
  open,
  onOpenChange,
  strategy,
  liveLtpMap,
  onClosed,
}: Props) {
  const [exitPrices, setExitPrices] = useState<ExitPriceForm>({});
  const [submitting, setSubmitting] = useState(false);

  // Whenever the dialog opens against a (possibly different) strategy,
  // pre-fill exit prices from live LTPs. Closed/expired legs keep their
  // existing exit_price.
  useEffect(() => {
    if (!open || !strategy) return;
    const next: ExitPriceForm = {};
    const exchange = strategy.exchange.toUpperCase();
    strategy.legs.forEach((leg, idx) => {
      const k = legKey(leg, idx);
      if (leg.status === "open" || !leg.status) {
        const live = leg.symbol
          ? liveLtpMap.get(ltpKey(leg.symbol, exchange))
          : undefined;
        next[k] = live !== undefined ? live.toFixed(2) : "";
      } else {
        next[k] =
          leg.exit_price !== undefined && leg.exit_price !== null
            ? String(leg.exit_price)
            : "";
      }
    });
    setExitPrices(next);
  }, [open, strategy, liveLtpMap]);

  const totals = useMemo(() => {
    if (!strategy) return { realized: 0, valid: false };
    let realized = 0;
    let allValid = true;
    strategy.legs.forEach((leg, idx) => {
      const k = legKey(leg, idx);
      if (leg.status === "closed" || leg.status === "expired") {
        const exit = leg.exit_price ?? 0;
        realized +=
          actionSign(leg.action) *
          (exit - (leg.entry_price ?? 0)) *
          leg.lots *
          (leg.lot_size ?? 0);
        return;
      }
      const raw = exitPrices[k];
      const exit = raw === "" || raw === undefined ? NaN : Number(raw);
      if (!Number.isFinite(exit)) {
        allValid = false;
        return;
      }
      realized +=
        actionSign(leg.action) *
        (exit - (leg.entry_price ?? 0)) *
        leg.lots *
        (leg.lot_size ?? 0);
    });
    return { realized, valid: allValid };
  }, [strategy, exitPrices]);

  if (!strategy) return null;

  const exchange = strategy.exchange.toUpperCase();

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      const now = new Date().toISOString();
      const updatedLegs: StrategyLeg[] = strategy.legs.map((leg, idx) => {
        if (leg.status === "closed" || leg.status === "expired") return leg;
        const k = legKey(leg, idx);
        const raw = exitPrices[k];
        const exit = raw === "" || raw === undefined ? null : Number(raw);
        if (exit === null || !Number.isFinite(exit)) {
          throw new Error(
            `Leg ${idx + 1}: exit price is required to mark closed`,
          );
        }
        return {
          ...leg,
          status: "closed" as const,
          exit_price: exit,
          exit_time: leg.exit_time ?? now,
        };
      });

      const updated = await updateStrategy(strategy.id, {
        status: "closed",
        legs: updatedLegs,
      });
      toast.success(`Strategy '${updated.name}' closed`);
      onClosed(updated);
      onOpenChange(false);
    } catch (e) {
      const msg =
        (e as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (e as { message?: string })?.message ??
        "Failed to close strategy";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Close strategy: {strategy.name}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge variant="secondary">{strategy.underlying}</Badge>
            <Badge variant="outline">{strategy.exchange}</Badge>
            <Badge variant="outline" className="capitalize">
              {strategy.mode}
            </Badge>
            {strategy.expiry_date && (
              <Badge variant="outline">{strategy.expiry_date}</Badge>
            )}
          </div>

          <p className="text-xs text-muted-foreground">
            Set exit prices for every open leg. Defaults to the live LTP. The
            strategy's status becomes <code className="rounded bg-muted px-1">closed</code>{" "}
            and the realized P&L is locked into the saved record.
          </p>

          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Leg</TableHead>
                  <TableHead className="text-right">Strike</TableHead>
                  <TableHead className="text-right">Lots</TableHead>
                  <TableHead className="text-right">Entry ₹</TableHead>
                  <TableHead className="text-right">Live LTP</TableHead>
                  <TableHead className="text-right">Exit ₹</TableHead>
                  <TableHead className="text-right">Realized P&L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategy.legs.map((leg, idx) => {
                  const k = legKey(leg, idx);
                  const isClosed =
                    leg.status === "closed" || leg.status === "expired";
                  const live = leg.symbol
                    ? liveLtpMap.get(ltpKey(leg.symbol, exchange))
                    : undefined;
                  const raw = exitPrices[k];
                  const exit =
                    raw === "" || raw === undefined ? NaN : Number(raw);
                  const realized = Number.isFinite(exit)
                    ? actionSign(leg.action) *
                      (exit - (leg.entry_price ?? 0)) *
                      leg.lots *
                      (leg.lot_size ?? 0)
                    : NaN;
                  const ce = leg.option_type === "CE";

                  return (
                    <TableRow key={k}>
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
                          {isClosed && (
                            <Badge variant="outline" className="text-[9px]">
                              Already {leg.status}
                            </Badge>
                          )}
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
                      <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                        {live !== undefined ? live.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        {isClosed ? (
                          <span className="font-mono tabular-nums text-muted-foreground">
                            {leg.exit_price?.toFixed(2) ?? "—"}
                          </span>
                        ) : (
                          <Input
                            type="number"
                            inputMode="decimal"
                            step="0.05"
                            min="0"
                            value={raw ?? ""}
                            onChange={(e) =>
                              setExitPrices((p) => ({
                                ...p,
                                [k]: e.target.value,
                              }))
                            }
                            className="h-7 w-24 text-right tabular-nums"
                            placeholder="—"
                          />
                        )}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono tabular-nums",
                          Number.isFinite(realized) && signTone(realized),
                        )}
                      >
                        {Number.isFinite(realized)
                          ? `${realized >= 0 ? "+" : ""}${realized.toFixed(2)}`
                          : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}

                <TableRow className="border-t-2 border-border bg-muted/40 font-semibold">
                  <TableCell colSpan={6} className="text-right">
                    Realized total
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right font-mono text-base font-bold tabular-nums",
                      totals.valid && signTone(totals.realized),
                    )}
                  >
                    {totals.valid
                      ? `${totals.realized >= 0 ? "+" : ""}₹ ${totals.realized.toFixed(2)}`
                      : "—"}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>

          <p className="text-[11px] text-muted-foreground">
            Note: this only updates the saved-strategy record. Use the
            Strategy Builder's basket-execute, your broker's positions
            screen, or a square-off order to actually close out the
            broker-side positions.
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={submitting || !totals.valid}
            title={
              totals.valid
                ? "Mark every open leg closed at the supplied prices"
                : "Set an exit price for every open leg"
            }
          >
            {submitting ? "Closing…" : "Close strategy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
