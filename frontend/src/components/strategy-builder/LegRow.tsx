/**
 * Editable row for one strategy leg.
 *
 * The page owns the leg array and passes a single ``leg`` plus an
 * ``onChange`` callback so React state stays simple. Strike is a free
 * numeric input in this Phase 5 skeleton — Phase 6 will swap in a
 * dropdown sourced from the option chain.
 *
 * Color convention follows the rest of OpenBull: CE is RED (#ef4444),
 * PE is GREEN (#22c55e). This is opposite the typical "calls = bull"
 * mental model — high CE OI is bearish in our palette because the
 * convention reflects OI semantics, not direction. Same as OptionChain
 * and OITracker.
 */

import { useId } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
}

export function LegRow({ leg, onChange, onRemove, disabled }: Props) {
  const ceTone = leg.option_type === "CE";
  const ids = {
    action: useId(),
    type: useId(),
    strike: useId(),
    lots: useId(),
    entry: useId(),
  };

  const set = <K extends keyof BuilderLeg>(key: K, value: BuilderLeg[K]) => {
    onChange({ ...leg, [key]: value });
  };

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
          onChange={(e) => set("option_type", e.target.value as OptionType)}
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

      {/* Strike */}
      <div className="col-span-3 space-y-1">
        <label htmlFor={ids.strike} className="block text-[11px] text-muted-foreground">
          Strike
        </label>
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
