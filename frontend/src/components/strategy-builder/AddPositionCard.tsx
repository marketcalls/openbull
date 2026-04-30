/**
 * "Add a Position" card — quick-build a single leg with explicit
 * Segment / Expiry / Strike / Type / Side / Lot Qty controls and an
 * ADD BUY / ADD SELL action button. Replaces the old "+ Add Leg" button
 * that appended a blank row the user then had to fill in.
 *
 * Mirrors openalgo's manual leg builder. Reads from the chain context
 * for strike + LTP, surfaces the resolved option symbol below the
 * controls so the user sees exactly what they're about to add.
 *
 * Math anchor: this is a leg-construction component only. The eventual
 * snapshot endpoint computes Greeks / payoff / margin from the resulting
 * leg list — same pipeline the template applier feeds.
 */

import { useEffect, useMemo, useState } from "react";
import { Minus, Plus, PlusCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ChainContext } from "@/hooks/useChainContext";
import { cn } from "@/lib/utils";
import type { Action, OptionType } from "@/types/strategy";

interface Props {
  /** Read-only — chain provides strikes, ATM, LTP map. */
  chain: ChainContext | null;
  /** Available expiries (display format) the parent fetched. */
  expiries: string[];
  /** Currently-selected primary expiry (display format). */
  primaryExpiry: string;
  underlying: string;
  /** Default lot size used as fallback when chain hasn't loaded. */
  defaultLotSize: number;
  /** Build the OpenAlgo symbol from underlying + expiry + strike + type. */
  buildSymbol: (
    underlying: string,
    expiryDisplay: string,
    strike: number,
    type: OptionType,
  ) => string;
  /** Callback fires once for each click of ADD BUY / ADD SELL. */
  onAddPosition: (leg: {
    action: Action;
    option_type: OptionType;
    strike: number;
    lots: number;
    lot_size: number;
    expiry_display: string;
    entry_price: number;
    symbol: string;
  }) => void;
}

/** Classify strike's moneyness against ATM (CE: lower=ITM; PE: higher=ITM). */
function moneyness(
  strike: number,
  atm: number | null,
  strikes: number[] | undefined,
  type: OptionType,
): { label: string; kind: "ATM" | "ITM" | "OTM" } | null {
  if (!Number.isFinite(strike) || atm === null || !strikes || strikes.length < 2) {
    return null;
  }
  const sorted = [...strikes].sort((a, b) => a - b);
  const diffs: number[] = [];
  for (let i = 1; i < sorted.length; i++) diffs.push(sorted[i] - sorted[i - 1]);
  diffs.sort((a, b) => a - b);
  const step = diffs[Math.floor(diffs.length / 2)] || 0;
  if (step <= 0) return null;
  const steps = Math.round((strike - atm) / step);
  if (steps === 0) return { label: "ATM", kind: "ATM" };
  const isCallITM = type === "CE" && steps < 0;
  const isPutITM = type === "PE" && steps > 0;
  const kind: "ITM" | "OTM" = isCallITM || isPutITM ? "ITM" : "OTM";
  return { label: `${kind}${Math.abs(steps)}`, kind };
}

export function AddPositionCard({
  chain,
  expiries,
  primaryExpiry,
  underlying,
  defaultLotSize,
  buildSymbol,
  onAddPosition,
}: Props) {
  const [expiry, setExpiry] = useState<string>(primaryExpiry);
  const [strike, setStrike] = useState<number | null>(chain?.atm ?? null);
  const [optionType, setOptionType] = useState<OptionType>("CE");
  const [lots, setLots] = useState<number>(1);

  // Keep the local expiry/strike in sync with parent changes — but only
  // when the user hasn't picked something custom. The trick: when chain.atm
  // updates (underlying or expiry change), we re-snap the strike to ATM
  // unless the user has explicitly moved away.
  useEffect(() => {
    if (!primaryExpiry) return;
    setExpiry((prev) => prev || primaryExpiry);
  }, [primaryExpiry]);

  useEffect(() => {
    if (chain && (strike === null || !chain.strikes.includes(strike))) {
      setStrike(chain.atm);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chain?.atm, chain?.strikes.length]);

  const lotSize = chain?.lotSize ?? defaultLotSize;
  const ltp = useMemo<number | null>(() => {
    if (!chain || strike === null) return null;
    const v =
      optionType === "CE"
        ? chain.ceLtpByStrike.get(strike)
        : chain.peLtpByStrike.get(strike);
    return typeof v === "number" && v > 0 ? v : null;
  }, [chain, strike, optionType]);

  const moneyKind = useMemo(
    () =>
      strike !== null
        ? moneyness(strike, chain?.atm ?? null, chain?.strikes, optionType)
        : null,
    [strike, chain, optionType],
  );

  const symbol = useMemo(() => {
    if (!expiry || strike === null) return "";
    return buildSymbol(underlying, expiry, strike, optionType);
  }, [buildSymbol, underlying, expiry, strike, optionType]);

  const canAdd = strike !== null && expiry !== "" && lots > 0;

  const handleAdd = (action: Action) => {
    if (!canAdd || strike === null) return;
    onAddPosition({
      action,
      option_type: optionType,
      strike,
      lots,
      lot_size: lotSize,
      expiry_display: expiry,
      entry_price: ltp ?? 0,
      symbol,
    });
  };

  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      {/* Top strip — title + LTP indicator */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/20 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <PlusCircle className="h-4 w-4 text-muted-foreground" />
          <div>
            <h3 className="text-sm font-semibold leading-none">Add a Position</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Build legs manually with custom strike, expiry and side
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          {ltp !== null ? (
            <>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              <span className="text-muted-foreground">LTP</span>
              <span className="font-semibold tabular-nums">
                ₹{ltp.toFixed(2)}
              </span>
            </>
          ) : (
            <>
              <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
              <span className="text-muted-foreground">LTP —</span>
            </>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-3 px-4 py-3">
        {/* Segment — currently fixed to Options. Futures support is a
            backend story (separate symbol shape, no strike) — leave the
            select here as a placeholder so the layout matches openalgo. */}
        <div className="space-y-1">
          <label className="block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Segment
          </label>
          <select
            value="OPTION"
            disabled
            className="h-9 w-28 rounded-lg border border-input bg-background px-2 text-xs font-medium outline-none disabled:opacity-70"
          >
            <option value="OPTION">Options</option>
          </select>
        </div>

        {/* Expiry */}
        <div className="space-y-1">
          <label className="block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Expiry
          </label>
          <select
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
            disabled={expiries.length === 0}
            className="h-9 w-32 rounded-lg border border-input bg-background px-2 text-xs font-medium outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            {expiries.length === 0 ? (
              <option value="">No expiries</option>
            ) : (
              expiries.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))
            )}
          </select>
        </div>

        {/* Strike — dropdown when chain loaded, with moneyness chip */}
        <div className="space-y-1">
          <label className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Strike
            {moneyKind && (
              <span
                className={cn(
                  "rounded px-1 py-px text-[9px] font-bold uppercase tracking-wider",
                  moneyKind.kind === "ATM" &&
                    "bg-amber-500/15 text-amber-700 dark:text-amber-400",
                  moneyKind.kind === "ITM" &&
                    "bg-sky-500/15 text-sky-700 dark:text-sky-400",
                  moneyKind.kind === "OTM" &&
                    "bg-muted text-muted-foreground",
                )}
              >
                {moneyKind.label}
              </span>
            )}
          </label>
          <select
            value={strike !== null ? String(strike) : ""}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "") return;
              setStrike(Number(v));
            }}
            disabled={!chain}
            className="h-9 w-32 rounded-lg border border-input bg-background px-2 text-xs font-medium tabular-nums outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            {!chain && <option value="">Load chain…</option>}
            {chain?.strikes.map((s) => (
              <option key={s} value={String(s)}>
                {s}
                {chain.atm === s ? " · ATM" : ""}
              </option>
            ))}
          </select>
        </div>

        {/* Type — segmented CE/PE */}
        <div className="space-y-1">
          <label className="block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Type
          </label>
          <div className="inline-flex h-9 overflow-hidden rounded-lg border border-input bg-background">
            <button
              type="button"
              onClick={() => setOptionType("CE")}
              className={cn(
                "px-3 text-xs font-bold transition-colors",
                optionType === "CE"
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              CE
            </button>
            <button
              type="button"
              onClick={() => setOptionType("PE")}
              className={cn(
                "border-l border-border px-3 text-xs font-bold transition-colors",
                optionType === "PE"
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              PE
            </button>
          </div>
        </div>

        {/* Lot Qty — stepper */}
        <div className="space-y-1">
          <label className="block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Lot Qty
          </label>
          <div className="inline-flex h-9 items-center overflow-hidden rounded-lg border border-input">
            <button
              type="button"
              onClick={() => setLots((v) => Math.max(1, v - 1))}
              className="flex h-full w-8 items-center justify-center text-muted-foreground hover:bg-muted"
              aria-label="Decrease lots"
            >
              <Minus className="h-3 w-3" />
            </button>
            <input
              type="number"
              min={1}
              value={lots}
              onChange={(e) =>
                setLots(Math.max(1, parseInt(e.target.value || "1", 10)))
              }
              className="h-full w-12 border-x border-border bg-background text-center text-xs font-semibold tabular-nums outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            />
            <button
              type="button"
              onClick={() => setLots((v) => v + 1)}
              className="flex h-full w-8 items-center justify-center text-muted-foreground hover:bg-muted"
              aria-label="Increase lots"
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>
        </div>

        {/* Spacer + action buttons pinned right */}
        <div className="ml-auto flex items-end gap-2">
          <Button
            type="button"
            onClick={() => handleAdd("BUY")}
            disabled={!canAdd}
            className="h-9 gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            ADD BUY
            <span className="rounded bg-white/20 px-1 py-0.5 text-[9px] font-bold">
              +{lots}x
            </span>
          </Button>
          <Button
            type="button"
            onClick={() => handleAdd("SELL")}
            disabled={!canAdd}
            variant="outline"
            className="h-9 gap-1.5 border-rose-500/50 text-rose-600 hover:bg-rose-500/10 disabled:opacity-50 dark:text-rose-400"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            ADD SELL
            <span className="rounded bg-rose-500/15 px-1 py-0.5 text-[9px] font-bold">
              −{lots}x
            </span>
          </Button>
        </div>
      </div>

      {/* Resolved symbol footer */}
      <div className="border-t border-border bg-muted/20 px-4 py-1.5">
        <p className="font-mono text-[10px] text-muted-foreground">
          {symbol || "(pick strike + expiry to resolve a symbol)"}
        </p>
      </div>
    </div>
  );
}
