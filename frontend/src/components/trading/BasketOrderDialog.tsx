/**
 * Multi-leg basket execute dialog for the Strategy Builder.
 *
 * Per-leg controls (matching openalgo's ExecuteBasketDialog):
 *   - Use checkbox to include/exclude a leg without deleting it
 *   - Editable Lots (defaults from the page, user can override at fire time)
 *   - Editable Price (tick-snapped on blur; disabled when Pricetype=MARKET)
 *   - Inline result (orderid on success, message on error) once the basket
 *     fires — no separate Result phase, just status badges per row
 *
 * Global controls applied to every included leg:
 *   - Pricetype (Market / Limit / SL-L / SL-M)
 *   - Product (NRML carry-forward / MIS intraday)
 *
 * Order semantics:
 *   - Server fires BUY legs first, then SELL legs (margin efficiency).
 *     The dialog mirrors that ordering in the row list.
 *   - Strategy + exchange surface as header badges so the user sees
 *     exactly what's about to fire and where.
 *
 * Sandbox-aware: when trading mode is "sandbox" the dialog still calls
 * /api/v1/basketorder — the backend trading-mode dispatcher routes to
 * the sandbox engine. The dialog displays a "Sandbox" badge.
 */

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Send, XCircle } from "lucide-react";
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
import { Input } from "@/components/ui/input";
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
  /** Used to seed the Price input default. */
  entryPrice?: number;
  /** Per-leg tick size (default 0.05 for NSE F&O). */
  tickSize?: number;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  legs: BasketDialogLeg[];
  /** Strategy tag shown in the orderbook + the dialog header badge. */
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

/** Per-row editable state inside the dialog. */
interface RowState {
  legId: string;
  include: boolean;
  symbol: string;
  exchange: string;
  action: Action;
  optionType: OptionType;
  strike: number;
  lots: number;
  lotSize: number;
  price: number;
  tickSize: number;
}

/** Decimals implied by tick size (0.05 → 2, 0.1 → 1, 1 → 0). */
function tickDecimals(tick: number): number {
  if (!Number.isFinite(tick) || tick <= 0) return 2;
  if (tick >= 1) return 0;
  const s = tick.toString();
  const dot = s.indexOf(".");
  return dot === -1 ? 0 : s.length - dot - 1;
}

/** Snap to nearest tick multiple, strip floating-point drift. */
function roundToTick(value: number, tick = 0.05): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (!Number.isFinite(tick) || tick <= 0) return value;
  const decimals = tickDecimals(tick);
  return Number((Math.round(value / tick) * tick).toFixed(decimals));
}

export function BasketOrderDialog({
  open,
  onOpenChange,
  legs,
  strategy = "Strategy Builder",
  mode = "live",
  onComplete,
}: Props) {
  const [pricetype, setPricetype] = useState<PriceType>("MARKET");
  const [product, setProduct] = useState<Product>("NRML");
  const [rows, setRows] = useState<RowState[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<BasketLegResult[] | null>(null);
  const [topError, setTopError] = useState<string | null>(null);

  // BUY legs fire first for margin efficiency — mirror that order in the row
  // list so the user sees execution order, not just leg-creation order.
  const orderedLegs = useMemo(() => {
    const buys = legs.filter((l) => l.action === "BUY");
    const sells = legs.filter((l) => l.action === "SELL");
    return [...buys, ...sells];
  }, [legs]);
  const buyCount = useMemo(
    () => legs.filter((l) => l.action === "BUY").length,
    [legs],
  );
  const sellCount = useMemo(
    () => legs.filter((l) => l.action === "SELL").length,
    [legs],
  );

  const exchange = legs[0]?.exchange ?? "NFO";

  // Seed (or re-seed) rows whenever the dialog opens or the leg set changes.
  useEffect(() => {
    if (!open) return;
    setResults(null);
    setTopError(null);
    setSubmitting(false);
    setRows(
      orderedLegs.map((leg) => {
        const tick = leg.tickSize ?? 0.05;
        return {
          legId: leg.id,
          include: true,
          symbol: leg.symbol,
          exchange: leg.exchange,
          action: leg.action,
          optionType: leg.optionType,
          strike: leg.strike,
          lots: Math.max(1, Math.floor(leg.lots || 1)),
          lotSize: Math.max(1, Math.floor(leg.lotSize || 1)),
          price: roundToTick(leg.entryPrice || 0, tick),
          tickSize: tick,
        };
      }),
    );
  }, [open, orderedLegs]);

  const updateRow = (legId: string, patch: Partial<RowState>) =>
    setRows((prev) =>
      prev.map((r) => (r.legId === legId ? { ...r, ...patch } : r)),
    );

  const includedRows = rows.filter((r) => r.include);
  const malformedRows = includedRows.filter(
    (r) => !r.symbol || r.lots <= 0 || r.lotSize <= 0,
  );

  // Reset when re-opened on a fresh basket.
  const handleOpenChange = (next: boolean) => {
    onOpenChange(next);
  };

  const handleExecute = async () => {
    if (includedRows.length === 0 || submitting || results) return;

    if (malformedRows.length > 0) {
      toast.error(
        `${malformedRows.length} leg${malformedRows.length === 1 ? "" : "s"} missing symbol or qty`,
      );
      return;
    }

    if (pricetype === "LIMIT") {
      const bad = includedRows.find((r) => !r.price || r.price <= 0);
      if (bad) {
        toast.error(`${bad.symbol}: LIMIT needs a valid price`);
        return;
      }
    }

    setSubmitting(true);
    setTopError(null);

    const orders: BasketOrderLeg[] = includedRows.map((r) => ({
      symbol: r.symbol,
      exchange: r.exchange,
      action: r.action,
      quantity: Math.max(1, Math.floor(r.lots) * Math.max(1, r.lotSize)),
      pricetype,
      product,
      // Tick-snap once more before send in case the user hit Execute before
      // blurring a manually-edited price input.
      price:
        pricetype === "LIMIT" || pricetype === "SL"
          ? roundToTick(r.price, r.tickSize)
          : 0,
    }));

    try {
      const resp = await placeBasketOrder({ strategy, orders });
      if (resp.status === "error") {
        const msg = resp.message ?? "Basket failed";
        setTopError(msg);
        setSubmitting(false);
        toast.error(msg);
        return;
      }
      const r = resp.results ?? [];
      setResults(r);
      const successes = r.filter((x) => x.status === "success").length;
      const failures = r.length - successes;
      if (failures === 0) {
        toast.success(
          `Basket fired — ${successes} order${successes === 1 ? "" : "s"}.`,
        );
      } else {
        toast.warning(`Basket: ${successes} placed, ${failures} failed.`);
      }
      onComplete?.(r);
    } catch (e) {
      const msg =
        (e as {
          response?: { data?: { detail?: string; message?: string } };
          message?: string;
        })?.response?.data?.detail ??
        (e as { response?: { data?: { message?: string } } })?.response?.data
          ?.message ??
        (e as { message?: string })?.message ??
        "Basket failed";
      setTopError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Send className="h-4 w-4" />
            Execute basket
            <Badge
              variant={mode === "sandbox" ? "secondary" : "outline"}
              className="capitalize"
            >
              {mode}
            </Badge>
          </DialogTitle>
          {/* Strategy + exchange badges in the header, matching openalgo. */}
          <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
            <span className="text-muted-foreground">Strategy</span>
            <span
              className="rounded-md border bg-muted/40 px-2 py-0.5 font-mono text-[11px] font-semibold text-foreground"
              title={strategy}
            >
              {strategy}
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">Exchange</span>
            <span className="rounded-md border bg-muted/40 px-2 py-0.5 font-mono text-[11px] font-semibold text-foreground">
              {exchange}
            </span>
          </div>
        </DialogHeader>

        {topError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
            {topError}
          </div>
        )}
        {malformedRows.length > 0 && !results && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-700 dark:text-amber-300">
            {malformedRows.length} included leg
            {malformedRows.length === 1 ? "" : "s"} ha
            {malformedRows.length === 1 ? "s" : "ve"} no resolved symbol or
            zero qty — fix or untick before firing.
          </div>
        )}

        {/* Global controls — Pricetype + Product applied to every included leg. */}
        <div className="grid grid-cols-1 gap-3 rounded-lg border bg-muted/20 p-3 sm:grid-cols-2">
          <div className="space-y-1">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Pricetype (applied to every leg)
            </label>
            <select
              value={pricetype}
              onChange={(e) => setPricetype(e.target.value as PriceType)}
              disabled={submitting || !!results}
              className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            >
              {PRICE_TYPES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Product
            </label>
            <select
              value={product}
              onChange={(e) => setProduct(e.target.value as Product)}
              disabled={submitting || !!results}
              className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
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
          BUY legs fire first ({buyCount}), then SELL legs ({sellCount}) —
          server-side, for margin efficiency.
        </p>

        {/* Per-leg rows */}
        <div className="overflow-hidden rounded-lg border">
          {/* Header */}
          <div className="grid grid-cols-[32px_minmax(0,1fr)_72px_88px_104px] items-center gap-2 border-b bg-muted/30 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            <span className="text-center">Use</span>
            <span>Leg</span>
            <span className="text-right">Strike</span>
            <span className="text-right">Lots</span>
            <span className="text-right">Price</span>
          </div>
          {/* Body */}
          <div className="max-h-[40vh] overflow-y-auto">
            {rows.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                No active legs in the strategy.
              </div>
            ) : (
              rows.map((r, idx) => {
                const ce = r.optionType === "CE";
                const result = results?.find((x) => x.symbol === r.symbol);
                return (
                  <div
                    key={r.legId}
                    className={cn(
                      "grid grid-cols-[32px_minmax(0,1fr)_72px_88px_104px] items-start gap-2 px-3 py-2 text-sm",
                      idx !== rows.length - 1 && "border-b",
                      !r.include && "opacity-50",
                      result?.status === "success" && "bg-emerald-500/5",
                      result?.status === "error" && "bg-rose-500/5",
                    )}
                  >
                    {/* Use checkbox */}
                    <div className="flex h-8 items-center justify-center">
                      <input
                        type="checkbox"
                        checked={r.include}
                        onChange={(e) =>
                          updateRow(r.legId, { include: e.target.checked })
                        }
                        disabled={submitting || !!results}
                        className="h-4 w-4 cursor-pointer accent-primary"
                      />
                    </div>

                    {/* Leg cell — side + type badges + symbol */}
                    <div className="min-w-0 space-y-0.5">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={cn(
                            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-white",
                            r.action === "BUY" ? "bg-emerald-500" : "bg-rose-500",
                          )}
                        >
                          {r.action === "BUY" ? "B" : "S"}
                        </span>
                        <span
                          className={cn(
                            "shrink-0 rounded px-1 py-0.5 text-[10px] font-bold text-white",
                            ce ? "bg-red-600" : "bg-emerald-600",
                          )}
                        >
                          {r.optionType}
                        </span>
                        <span
                          className="break-all font-mono text-xs font-semibold leading-tight"
                          title={r.symbol}
                        >
                          {r.symbol || "(unresolved)"}
                        </span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Lot size: {r.lotSize} · Qty: {r.lots * r.lotSize}
                      </div>
                      {result && (
                        <div className="flex items-center gap-1 text-[10px]">
                          {result.status === "success" ? (
                            <>
                              <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                              <span className="truncate text-emerald-600 dark:text-emerald-400">
                                #{result.orderid}
                              </span>
                            </>
                          ) : (
                            <>
                              <XCircle className="h-3 w-3 text-rose-500" />
                              <span
                                className="truncate text-rose-600 dark:text-rose-400"
                                title={result.message}
                              >
                                {result.message || "Failed"}
                              </span>
                            </>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Strike (read-only) */}
                    <div className="flex h-8 items-center justify-end font-mono text-xs tabular-nums">
                      {r.strike}
                    </div>

                    {/* Lots */}
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      value={r.lots}
                      onChange={(e) =>
                        updateRow(r.legId, {
                          lots: Math.max(
                            1,
                            Math.floor(Number(e.target.value) || 1),
                          ),
                        })
                      }
                      disabled={submitting || !!results || !r.include}
                      className="h-8 text-right font-mono text-xs"
                    />

                    {/* Price (disabled for MARKET / SL-M) */}
                    <Input
                      type="number"
                      min={0}
                      step={r.tickSize}
                      value={r.price}
                      onChange={(e) =>
                        updateRow(r.legId, {
                          price: Number(e.target.value) || 0,
                        })
                      }
                      onBlur={(e) => {
                        const snapped = roundToTick(
                          Number(e.target.value) || 0,
                          r.tickSize,
                        );
                        updateRow(r.legId, { price: snapped });
                      }}
                      disabled={
                        submitting ||
                        !!results ||
                        !r.include ||
                        pricetype === "MARKET" ||
                        pricetype === "SL-M"
                      }
                      placeholder={
                        pricetype === "MARKET" || pricetype === "SL-M"
                          ? "MKT"
                          : "0.00"
                      }
                      className="h-8 text-right font-mono text-xs"
                    />
                  </div>
                );
              })
            )}
          </div>
        </div>

        <DialogFooter className="flex-row items-center justify-between sm:justify-between">
          <div className="text-xs text-muted-foreground">
            {includedRows.length} of {rows.length} leg
            {rows.length === 1 ? "" : "s"} selected
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={submitting}
            >
              {results ? "Close" : "Cancel"}
            </Button>
            <Button
              onClick={handleExecute}
              disabled={
                submitting ||
                !!results ||
                includedRows.length === 0 ||
                malformedRows.length > 0
              }
              className="gap-1.5"
            >
              <Send className="h-3.5 w-3.5" />
              {submitting
                ? "Firing…"
                : `Fire ${includedRows.length} order${includedRows.length === 1 ? "" : "s"}`}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
