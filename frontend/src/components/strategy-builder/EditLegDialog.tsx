/**
 * EditLegDialog — modal form for editing a single leg.
 *
 * Replaces the inline LegRow grid for tactile leg edits. Triggered from:
 *   - the pencil icon in PositionsPanel (Payoff tab)
 *   - the pencil icon in LegRow (Legs tab) — also added here as a consistent
 *     entry point so the two tabs offer the same experience
 *
 * Fields: Action (BUY/SELL pills), Type (CE/PE), Expiry (dropdown sourced
 * from the page's expiry list), Strike (dropdown from chain context with
 * a moneyness chip), Lot Qty stepper, Entry Price (auto-filled from chain
 * LTP when the user changes strike or type, manually overridable).
 *
 * Mirrors openalgo's EditLegDialog pattern. Strike is a strict pick from
 * the chain's strike grid — keeps illegal strikes from creeping in. The
 * expiry dropdown shows display format ("DD-MMM-YYYY") and converts to
 * backend "DDMMMYY" on save via the parent's converter.
 *
 * Math anchor: this is a leg-edit component only. The snapshot pipeline
 * downstream computes Greeks / payoff / margin — same as before.
 */

import { useEffect, useMemo, useState } from "react";
import { Minus, Plus, Save, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ChainContext } from "@/hooks/useChainContext";
import { cn } from "@/lib/utils";
import type { Action, OptionType } from "@/types/strategy";

import type { BuilderLeg } from "./LegRow";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The leg currently being edited. null when no leg is selected (dialog should be closed). */
  leg: BuilderLeg | null;
  /** Available expiries in DISPLAY format ("DD-MMM-YYYY"). */
  expiries: string[];
  /** Display format of the leg's current expiry (passed in by the parent
   *  so the dialog doesn't have to convert API ↔ display itself). */
  legExpiryDisplay: string;
  /** Live chain context — drives the strike dropdown + LTP auto-fill. */
  chain: ChainContext | null;
  /** Underlying base symbol (NIFTY, BANKNIFTY, …). Used in the title. */
  underlying: string;
  /** Convert display expiry → backend "DDMMMYY". Same converter the page uses. */
  convertExpiryForApi: (displayExpiry: string) => string;
  /** Build the OpenAlgo symbol from underlying + API expiry + strike + type. */
  buildOptionSymbol: (
    underlying: string,
    expiryApi: string,
    strike: number,
    optType: OptionType,
  ) => string;
  /** Save callback — parent merges into its leg array. */
  onSave: (updated: BuilderLeg) => void;
  /** Delete callback — parent removes from its leg array. */
  onDelete: (legId: string) => void;
}

interface Moneyness {
  label: string;
  kind: "ATM" | "ITM" | "OTM";
}

function classifyMoneyness(
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

export function EditLegDialog({
  open,
  onOpenChange,
  leg,
  expiries,
  legExpiryDisplay,
  chain,
  underlying,
  convertExpiryForApi,
  buildOptionSymbol,
  onSave,
  onDelete,
}: Props) {
  const [action, setAction] = useState<Action>("BUY");
  const [optionType, setOptionType] = useState<OptionType>("CE");
  const [expiryDisplay, setExpiryDisplay] = useState<string>("");
  const [strike, setStrike] = useState<number>(0);
  const [lots, setLots] = useState<number>(1);
  const [entryPrice, setEntryPrice] = useState<string>("0");

  // Hydrate local state from the leg when the dialog opens.
  useEffect(() => {
    if (!leg) return;
    setAction(leg.action);
    setOptionType(leg.option_type);
    setExpiryDisplay(legExpiryDisplay);
    setStrike(Number.isFinite(leg.strike) ? leg.strike : (chain?.atm ?? 0));
    setLots(Math.max(1, leg.lots));
    setEntryPrice(leg.entry_price.toString());
  }, [leg, legExpiryDisplay, chain?.atm]);

  const moneyness = useMemo(
    () =>
      classifyMoneyness(strike, chain?.atm, chain?.strikes, optionType),
    [strike, chain, optionType],
  );

  /** Auto-fill entry price from chain LTP when (strike, type) change. The
   *  helper is invoked from the field handlers themselves so the user's
   *  manual edit isn't clobbered when they explicitly typed a price. */
  const refillFromLtp = (
    nextStrike: number,
    nextType: OptionType,
  ) => {
    if (!chain) return;
    const ltp =
      nextType === "CE"
        ? chain.ceLtpByStrike.get(nextStrike)
        : chain.peLtpByStrike.get(nextStrike);
    if (typeof ltp === "number" && ltp > 0) {
      setEntryPrice(ltp.toFixed(2));
    }
  };

  const resolvedSymbol = useMemo(() => {
    if (!leg || !expiryDisplay || !Number.isFinite(strike)) return "";
    return buildOptionSymbol(
      underlying,
      convertExpiryForApi(expiryDisplay),
      strike,
      optionType,
    );
  }, [
    buildOptionSymbol,
    convertExpiryForApi,
    expiryDisplay,
    leg,
    optionType,
    strike,
    underlying,
  ]);

  if (!leg) return null;

  const handleSave = () => {
    const expiryApi = convertExpiryForApi(expiryDisplay);
    const symbol = buildOptionSymbol(underlying, expiryApi, strike, optionType);
    const updated: BuilderLeg = {
      ...leg,
      action,
      option_type: optionType,
      strike,
      lots: Math.max(1, lots),
      expiry_date: expiryApi,
      entry_price: Number(entryPrice) || 0,
      symbol,
    };
    onSave(updated);
    onOpenChange(false);
  };

  const handleDelete = () => {
    onDelete(leg.id);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Position</DialogTitle>
          <DialogDescription className="text-xs">
            Adjust strike, expiry, side, lot qty, or entry price. Changes
            apply on Save.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Action — BUY / SELL pills */}
          <div className="space-y-1.5">
            <Label className="text-xs">Action</Label>
            <div className="inline-flex h-9 w-full overflow-hidden rounded-lg border border-input bg-background">
              <button
                type="button"
                onClick={() => setAction("BUY")}
                className={cn(
                  "flex-1 text-xs font-bold transition-colors",
                  action === "BUY"
                    ? "bg-emerald-600 text-white"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                BUY
              </button>
              <button
                type="button"
                onClick={() => setAction("SELL")}
                className={cn(
                  "flex-1 border-l border-border text-xs font-bold transition-colors",
                  action === "SELL"
                    ? "bg-rose-600 text-white"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                SELL
              </button>
            </div>
          </div>

          {/* Type + Expiry side by side */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Type</Label>
              <div className="inline-flex h-9 w-full overflow-hidden rounded-lg border border-input bg-background">
                <button
                  type="button"
                  onClick={() => {
                    setOptionType("CE");
                    refillFromLtp(strike, "CE");
                  }}
                  className={cn(
                    "flex-1 text-xs font-bold transition-colors",
                    optionType === "CE"
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  CE
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOptionType("PE");
                    refillFromLtp(strike, "PE");
                  }}
                  className={cn(
                    "flex-1 border-l border-border text-xs font-bold transition-colors",
                    optionType === "PE"
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  PE
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Expiry</Label>
              <select
                value={expiryDisplay}
                onChange={(e) => setExpiryDisplay(e.target.value)}
                disabled={expiries.length === 0}
                className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm font-medium outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
              >
                {expiries.length === 0 && <option value="">No expiries</option>}
                {!expiries.includes(expiryDisplay) && expiryDisplay !== "" && (
                  <option value={expiryDisplay}>{expiryDisplay}</option>
                )}
                {expiries.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Strike with moneyness chip */}
          <div className="space-y-1.5">
            <Label className="flex items-center gap-1.5 text-xs">
              Strike
              {moneyness && (
                <span
                  className={cn(
                    "rounded px-1 py-px text-[9px] font-bold uppercase tracking-wider",
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
            </Label>
            {chain && chain.strikes.length > 0 ? (
              <select
                value={String(strike)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) {
                    setStrike(v);
                    refillFromLtp(v, optionType);
                  }
                }}
                className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm font-medium tabular-nums outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {/* Allow the leg's existing strike even if it's outside the
                    fetched chain window (e.g. a deep ITM saved strategy). */}
                {!chain.strikes.includes(strike) && (
                  <option value={String(strike)}>{strike}</option>
                )}
                {chain.strikes.map((s) => (
                  <option key={s} value={String(s)}>
                    {s}
                    {chain.atm === s ? " · ATM" : ""}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                type="number"
                value={Number.isFinite(strike) ? strike : ""}
                step="0.5"
                onChange={(e) => setStrike(Number(e.target.value) || 0)}
                className="h-9 text-sm tabular-nums"
              />
            )}
          </div>

          {/* Lot Qty + Entry Price */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">
                Lot Qty
                <span className="ml-1.5 text-[10px] text-muted-foreground">
                  × {leg.lot_size || "?"}
                </span>
              </Label>
              <div className="inline-flex h-9 w-full overflow-hidden rounded-lg border border-input">
                <button
                  type="button"
                  onClick={() => setLots((v) => Math.max(1, v - 1))}
                  className="flex h-full w-9 items-center justify-center text-muted-foreground hover:bg-muted"
                  aria-label="Decrease lots"
                >
                  <Minus className="h-3.5 w-3.5" />
                </button>
                <input
                  type="number"
                  min={1}
                  value={lots}
                  onChange={(e) =>
                    setLots(Math.max(1, parseInt(e.target.value || "1", 10)))
                  }
                  className="h-full w-full border-x border-border bg-background text-center text-sm font-semibold tabular-nums outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                />
                <button
                  type="button"
                  onClick={() => setLots((v) => v + 1)}
                  className="flex h-full w-9 items-center justify-center text-muted-foreground hover:bg-muted"
                  aria-label="Increase lots"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Entry Price ₹</Label>
              <Input
                type="number"
                step="0.05"
                min={0}
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
                className="h-9 text-sm tabular-nums"
              />
            </div>
          </div>

          {/* Resolved symbol preview */}
          <div className="rounded-md border border-dashed bg-muted/30 px-3 py-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Resolved Symbol
            </p>
            <p className="font-mono text-xs font-semibold tabular-nums">
              {resolvedSymbol || "(pick strike + expiry)"}
            </p>
          </div>
        </div>

        <DialogFooter className="flex-row items-center justify-between sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleDelete}
            className="gap-1.5 text-rose-600 hover:bg-rose-500/10 hover:text-rose-600 dark:text-rose-400"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete leg
          </Button>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="button" onClick={handleSave} className="gap-1.5">
              <Save className="h-3.5 w-3.5" />
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
