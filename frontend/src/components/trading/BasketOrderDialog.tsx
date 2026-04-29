/**
 * Multi-leg basket execute dialog for the Strategy Builder.
 *
 * Two phases inside one dialog:
 *
 *   1. Confirm — shows the leg list grouped BUY-first / SELL-second so
 *      the user can verify exactly what will fire (the backend orders
 *      this way for margin efficiency, and we mirror the order in the
 *      UI). Lets the user pick Pricetype + Product once, applied to
 *      every leg. A pre-flight check disables the fire button when any
 *      leg is missing a symbol or has zero qty.
 *
 *   2. Result — after the basket fires, shows per-leg outcome (orderid
 *      on success, error message on failure) and a summary count.
 *
 * Sandbox-aware: when trading mode is "sandbox" the dialog still calls
 * /api/v1/basketorder — the backend trading-mode dispatcher routes to
 * the sandbox engine. The dialog displays a "Sandbox" badge so the
 * user knows real money isn't moving.
 */

import { useState } from "react";
import { toast } from "sonner";

import {
  placeBasketOrder,
  type BasketLegResult,
  type BasketOrderLeg,
  type PriceType,
  type Product,
} from "@/api/basketorder";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { Action, OptionType } from "@/types/strategy";

/** What the page hands us — the dialog adapts this to BasketOrderLeg shape. */
export interface BasketDialogLeg {
  id: string;
  symbol: string;
  exchange: string;
  action: Action;
  optionType: OptionType;
  strike: number;
  lots: number;
  lotSize: number;
  /** Used in the result table only. */
  entryPrice?: number;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  legs: BasketDialogLeg[];
  /** Strategy tag shown in the orderbook. */
  strategy?: string;
  /** "live" or "sandbox" — dialog only reflects this in a badge. */
  mode?: "live" | "sandbox";
  /** Fired after a successful basket — useful for refreshing positions. */
  onComplete?: (results: BasketLegResult[]) => void;
}

const PRICE_TYPES: ReadonlyArray<{ value: PriceType; label: string }> = [
  { value: "MARKET", label: "Market" },
  { value: "LIMIT", label: "Limit" },
  { value: "SL", label: "SL-L" },
  { value: "SL-M", label: "SL-M" },
];

const PRODUCTS: ReadonlyArray<{ value: Product; label: string }> = [
  { value: "NRML", label: "NRML (carry forward)" },
  { value: "MIS", label: "MIS (intraday)" },
];

type Phase = "confirm" | "firing" | "result";

export function BasketOrderDialog({
  open,
  onOpenChange,
  legs,
  strategy = "Strategy Builder",
  mode = "live",
  onComplete,
}: Props) {
  const [phase, setPhase] = useState<Phase>("confirm");
  const [pricetype, setPricetype] = useState<PriceType>("MARKET");
  const [product, setProduct] = useState<Product>("NRML");
  const [results, setResults] = useState<BasketLegResult[]>([]);
  const [topError, setTopError] = useState<string | null>(null);

  // Reset when re-opened on a fresh basket.
  const handleOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      // Reset for the next session after the close animation
      window.setTimeout(() => {
        setPhase("confirm");
        setResults([]);
        setTopError(null);
      }, 200);
    }
  };

  const buyLegs = legs.filter((l) => l.action === "BUY");
  const sellLegs = legs.filter((l) => l.action === "SELL");
  const orderedLegs = [...buyLegs, ...sellLegs];
  const malformed = legs.filter(
    (l) => !l.symbol || l.lots <= 0 || l.lotSize <= 0,
  );

  const fire = async () => {
    setPhase("firing");
    setTopError(null);

    const orders: BasketOrderLeg[] = orderedLegs.map((l) => ({
      symbol: l.symbol,
      exchange: l.exchange,
      action: l.action,
      quantity: l.lots * l.lotSize,
      pricetype,
      product,
    }));

    try {
      const resp = await placeBasketOrder({ strategy, orders });
      if (resp.status === "error") {
        setTopError(resp.message ?? "Basket failed");
        setPhase("confirm");
        toast.error(resp.message ?? "Basket failed");
        return;
      }
      const r = resp.results ?? [];
      setResults(r);
      setPhase("result");
      const successes = r.filter((x) => x.status === "success").length;
      const failures = r.length - successes;
      if (failures === 0) {
        toast.success(`Basket fired — ${successes} order${successes === 1 ? "" : "s"}.`);
      } else {
        toast.warning(`Basket: ${successes} placed, ${failures} failed.`);
      }
      onComplete?.(r);
    } catch (e) {
      const msg =
        (e as { response?: { data?: { detail?: string; message?: string } }; message?: string })
          ?.response?.data?.detail ??
        (e as { response?: { data?: { message?: string } } })?.response?.data?.message ??
        (e as { message?: string })?.message ??
        "Basket failed";
      setTopError(msg);
      setPhase("confirm");
      toast.error(msg);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {phase === "result" ? "Basket result" : "Execute basket"}
            <Badge variant={mode === "sandbox" ? "secondary" : "outline"} className="capitalize">
              {mode}
            </Badge>
          </DialogTitle>
        </DialogHeader>

        {/* ── Confirm / firing phase ─────────────────────────────────── */}
        {phase !== "result" && (
          <>
            {topError && (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                {topError}
              </div>
            )}
            {malformed.length > 0 && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
                {malformed.length} leg{malformed.length === 1 ? "" : "s"} ha
                {malformed.length === 1 ? "s" : "ve"} no resolved symbol or zero
                qty — fix before firing.
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="block text-xs text-muted-foreground">
                  Pricetype (applied to every leg)
                </label>
                <select
                  value={pricetype}
                  onChange={(e) => setPricetype(e.target.value as PriceType)}
                  disabled={phase === "firing"}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
                >
                  {PRICE_TYPES.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label className="block text-xs text-muted-foreground">Product</label>
                <select
                  value={product}
                  onChange={(e) => setProduct(e.target.value as Product)}
                  disabled={phase === "firing"}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
                >
                  {PRODUCTS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <p className="text-[11px] text-muted-foreground">
              BUY legs fire first ({buyLegs.length}), then SELL legs (
              {sellLegs.length}) — server-side, for margin efficiency.
            </p>

            <div className="max-h-[40vh] overflow-y-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Order</TableHead>
                    <TableHead>Leg</TableHead>
                    <TableHead className="text-right">Strike</TableHead>
                    <TableHead className="text-right">Qty</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {orderedLegs.map((l, i) => {
                    const ce = l.optionType === "CE";
                    return (
                      <TableRow key={l.id}>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          #{i + 1}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span
                              className={cn(
                                "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                                l.action === "BUY"
                                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                  : "bg-red-500/15 text-red-600 dark:text-red-400",
                              )}
                            >
                              {l.action}
                            </span>
                            <span
                              className={cn(
                                "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                                ce
                                  ? "bg-red-500/10 text-red-600 dark:text-red-400"
                                  : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                              )}
                            >
                              {l.optionType}
                            </span>
                            <span className="font-mono text-[11px] text-muted-foreground">
                              {l.symbol || "(unresolved)"}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {l.strike}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {l.lots} × {l.lotSize} ={" "}
                          <span className="font-semibold">
                            {l.lots * l.lotSize}
                          </span>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={phase === "firing"}
              >
                Cancel
              </Button>
              <Button
                onClick={fire}
                disabled={
                  phase === "firing" ||
                  legs.length === 0 ||
                  malformed.length > 0
                }
              >
                {phase === "firing"
                  ? "Firing…"
                  : `Fire ${legs.length} order${legs.length === 1 ? "" : "s"}`}
              </Button>
            </DialogFooter>
          </>
        )}

        {/* ── Result phase ──────────────────────────────────────────── */}
        {phase === "result" && (
          <>
            <ResultSummary results={results} />
            <div className="max-h-[40vh] overflow-y-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Order ID / Message</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.map((r, i) => (
                    <TableRow key={`${r.symbol}-${i}`}>
                      <TableCell className="font-mono text-xs">
                        {r.symbol}
                      </TableCell>
                      <TableCell>
                        <span
                          className={cn(
                            "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                            r.status === "success"
                              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                              : "bg-red-500/15 text-red-600 dark:text-red-400",
                          )}
                        >
                          {r.status === "success" ? "Placed" : "Error"}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {r.orderid || r.message || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <DialogFooter>
              <Button onClick={() => handleOpenChange(false)}>Close</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ResultSummary({ results }: { results: BasketLegResult[] }) {
  const successes = results.filter((r) => r.status === "success").length;
  const failures = results.length - successes;
  return (
    <div className="rounded-md border border-border bg-muted/40 p-2 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="secondary">
          {successes} placed of {results.length}
        </Badge>
        {failures > 0 && (
          <Badge
            variant="outline"
            className="border-red-500/50 text-red-600 dark:text-red-400"
          >
            {failures} failed
          </Badge>
        )}
        {failures === 0 && successes > 0 && (
          <Badge
            variant="outline"
            className="border-emerald-500/50 text-emerald-600 dark:text-emerald-400"
          >
            All placed
          </Badge>
        )}
      </div>
    </div>
  );
}
