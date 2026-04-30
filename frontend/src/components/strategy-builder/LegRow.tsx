/**
 * Editable row for one strategy leg.
 *
 * The page owns the leg array and passes a single ``leg`` plus an
 * ``onChange`` callback so React state stays simple.
 *
 * Phase 2 (April 2026, openalgo UI port): strike is now a dropdown sourced
 * from the chain context when available, with a moneyness chip beside it
 * (ATM / ITMn / OTMn). Editing the strike OR the option type auto-fills
 * ``entry_price`` from the chain's CE/PE LTP map — same UX shortcut openalgo
 * uses in its EditLegDialog. Falls back to a free-text input when the chain
 * hasn't loaded yet.
 *
 * Color convention follows the rest of OpenBull: CE is RED, PE is GREEN.
 * High CE OI = bearish in our palette because the convention reflects OI
 * semantics, not direction.
 */

import { useId, useMemo } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ChainContext } from "@/hooks/useChainContext";
import { cn } from "@/lib/utils";

import type { Action, OptionType } from "@/types/strategy";

export interface BuilderLeg {
  /** Local React key. Use crypto.randomUUID() when building new legs. */
  id: string;
  action: Action;
  option_type: OptionType;
  strike: number;
  lots: number;
  lot_size: number;
  /** Backend format "DDMMMYY". Per-leg so calendar/diagonal works without a schema change. */
  expiry_date: string;
  entry_price: number;
  /** Resolved option symbol — derived elsewhere; LegRow only displays it. */
  symbol: string;
}

interface Props {
  leg: BuilderLeg;
  onChange: (leg: BuilderLeg) => void;
  onRemove: () => void;
  disabled?: boolean;
  /** Live chain — when present, strike becomes a dropdown and entry_price
   *  auto-fills from LTP. Optional so unit tests / callers without chain
   *  data can still render. */
  chainContext?: ChainContext | null;
}

interface Moneyness {
  label: string;
  kind: "ATM" | "ITM" | "OTM";
}

/**
 * Classify a strike's moneyness relative to ATM, returning a short label
 * like "ATM", "ITM2", "OTM3". Returns null when inputs aren't enough to
 * compute (no ATM, missing strike step).
 *
 *   CE: strike < ATM → ITM, strike > ATM → OTM
 *   PE: strike > ATM → ITM, strike < ATM → OTM
 */
function strikeMoneyness(
  strike: number,
  atm: number | undefined,
  strikes: number[] | undefined,
  optType: OptionType,
): Moneyness | null {
  if (
    !Number.isFinite(strike) ||
    atm === undefined ||
    !strikes ||
    strikes.length < 2
  ) {
    return null;
  }
  const sorted = [...strikes].sort((a, b) => a - b);
  // Use the median spacing as the "step" — robust to one-off gaps.
  const diffs: number[] = [];
  for (let i = 1; i < sorted.length; i++) diffs.push(sorted[i] - sorted[i - 1]);
  diffs.sort((a, b) => a - b);
  const step = diffs[Math.floor(diffs.length / 2)] || 0;
  if (step <= 0) return null;
  const steps = Math.round((strike - atm) / step);
  if (steps === 0) return { label: "ATM", kind: "ATM" };
  const isCallITM = optType === "CE" && steps < 0;
  const isPutITM = optType === "PE" && steps > 0;
  const kind: "ITM" | "OTM" = isCallITM || isPutITM ? "ITM" : "OTM";
  return { label: `${kind}${Math.abs(steps)}`, kind };
}

export function LegRow({
  leg,
  onChange,
  onRemove,
  disabled,
  chainContext,
}: Props) {
  const ceTone = leg.option_type === "CE";
  const ids = {
    action: useId(),
    type: useId(),
    strike: useId(),
    lots: useId(),
    entry: useId(),
  };

  const moneyness = useMemo(
    () =>
      strikeMoneyness(
        leg.strike,
        chainContext?.atm,
        chainContext?.strikes,
        leg.option_type,
      ),
    [leg.strike, leg.option_type, chainContext],
  );

  const set = <K extends keyof BuilderLeg>(key: K, value: BuilderLeg[K]) => {
    onChange({ ...leg, [key]: value });
  };

  /** Apply (strike, option_type) and auto-fill entry_price from the chain's
   *  LTP at that combo. Triggered on either field's onChange so the user
   *  doesn't have to retype premium after switching to a different strike. */
  const applyStrikeAndType = (
    nextStrike: number,
    nextType: OptionType,
  ) => {
    const ltp =
      chainContext &&
      Number.isFinite(nextStrike) &&
      (nextType === "CE"
        ? chainContext.ceLtpByStrike.get(nextStrike)
        : chainContext.peLtpByStrike.get(nextStrike));
    onChange({
      ...leg,
      strike: nextStrike,
      option_type: nextType,
      entry_price:
        typeof ltp === "number" && ltp > 0
          ? Number(ltp.toFixed(2))
          : leg.entry_price,
    });
  };

  const useStrikeDropdown =
    chainContext !== null &&
    chainContext !== undefined &&
    chainContext.strikes.length > 0;

  return (
    <div className="grid grid-cols-12 items-end gap-2 rounded-md border border-border bg-card/50 p-2">
      {/* Action */}
      <div className="col-span-2 space-y-1">
        <label htmlFor={ids.action} className="block text-[11px] text-muted-foreground">
          Action
        </label>
        <select
          id={ids.action}
          value={leg.action}
          onChange={(e) => set("action", e.target.value as Action)}
          disabled={disabled}
          className={cn(
            "h-8 w-full rounded-lg border bg-background px-2 text-sm font-medium outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
            leg.action === "BUY"
              ? "border-emerald-500/50 text-emerald-600 dark:text-emerald-400"
              : "border-red-500/50 text-red-600 dark:text-red-400",
          )}
        >
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </div>

      {/* Option type */}
      <div className="col-span-2 space-y-1">
        <label htmlFor={ids.type} className="block text-[11px] text-muted-foreground">
          Type
        </label>
        <select
          id={ids.type}
          value={leg.option_type}
          onChange={(e) =>
            applyStrikeAndType(leg.strike, e.target.value as OptionType)
          }
          disabled={disabled}
          className={cn(
            "h-8 w-full rounded-lg border bg-background px-2 text-sm font-medium outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
            ceTone ? "border-red-500/50 text-red-600 dark:text-red-400" : "border-emerald-500/50 text-emerald-600 dark:text-emerald-400",
          )}
        >
          <option value="CE">CE</option>
          <option value="PE">PE</option>
        </select>
      </div>

      {/* Strike — dropdown when chain is loaded, free input otherwise */}
      <div className="col-span-3 space-y-1">
        <label
          htmlFor={ids.strike}
          className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
        >
          Strike
          {moneyness && (
            <span
              className={cn(
                "rounded px-1 py-px text-[9px] font-semibold uppercase tracking-wider",
                moneyness.kind === "ATM" &&
                  "bg-amber-500/15 text-amber-700 dark:text-amber-400",
                moneyness.kind === "ITM" &&
                  "bg-sky-500/15 text-sky-700 dark:text-sky-400",
                moneyness.kind === "OTM" &&
                  "bg-muted text-muted-foreground",
              )}
            >
              {moneyness.label}
            </span>
          )}
        </label>
        {useStrikeDropdown ? (
          <select
            id={ids.strike}
            value={Number.isFinite(leg.strike) ? String(leg.strike) : ""}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "") return;
              applyStrikeAndType(Number(v), leg.option_type);
            }}
            disabled={disabled}
            className="h-8 w-full rounded-lg border border-input bg-background px-2 text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
          >
            {!Number.isFinite(leg.strike) && (
              <option value="">— pick —</option>
            )}
            {chainContext!.strikes.map((s) => (
              <option key={s} value={String(s)}>
                {s} {chainContext!.atm === s ? "·ATM" : ""}
              </option>
            ))}
          </select>
        ) : (
          <Input
            id={ids.strike}
            type="number"
            inputMode="decimal"
            step="0.5"
            min="0"
            value={Number.isFinite(leg.strike) ? leg.strike : ""}
            onChange={(e) => {
              const v = e.target.value;
              set("strike", v === "" ? Number.NaN : Number(v));
            }}
            disabled={disabled}
            className="h-8"
          />
        )}
      </div>

      {/* Lots */}
      <div className="col-span-2 space-y-1">
        <label htmlFor={ids.lots} className="block text-[11px] text-muted-foreground">
          Lots × {leg.lot_size || "?"}
        </label>
        <Input
          id={ids.lots}
          type="number"
          inputMode="numeric"
          min="1"
          step="1"
          value={leg.lots}
          onChange={(e) => set("lots", Math.max(1, parseInt(e.target.value || "1", 10)))}
          disabled={disabled}
          className="h-8"
        />
      </div>

      {/* Entry price */}
      <div className="col-span-2 space-y-1">
        <label htmlFor={ids.entry} className="block text-[11px] text-muted-foreground">
          Entry ₹
        </label>
        <Input
          id={ids.entry}
          type="number"
          inputMode="decimal"
          step="0.05"
          min="0"
          value={leg.entry_price}
          onChange={(e) => set("entry_price", Number(e.target.value || 0))}
          disabled={disabled}
          className="h-8"
        />
      </div>

      {/* Remove */}
      <div className="col-span-1 flex items-end justify-end">
        <Button
          variant="ghost"
          size="sm"
          onClick={onRemove}
          disabled={disabled}
          aria-label="Remove leg"
          className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Resolved symbol — full row, read-only, monospace */}
      <div className="col-span-12 -mt-1">
        <p className="font-mono text-[10px] text-muted-foreground">
          {leg.symbol || "(strike + expiry will resolve to a symbol)"}
        </p>
      </div>
    </div>
  );
}
