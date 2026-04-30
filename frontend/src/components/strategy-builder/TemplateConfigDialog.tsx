/**
 * TemplateConfigDialog — preview / tweak modal opened when the user
 * clicks a template card in the Strategy Library. Shows the resolved
 * legs (strike, type, side, lot qty) so the user can adjust before
 * applying. Mirrors openalgo's TemplateDialog flow:
 *
 *   Template card click → TemplateConfigDialog opens with N legs pre-filled
 *   ↳ user tweaks per-leg strike (dropdown) + lot qty (stepper)
 *   ↳ user clicks Apply → all legs added to the builder
 *
 * Why a confirmation modal instead of direct apply:
 *   - User can sanity-check ATM and adjacent strikes before committing.
 *   - Multi-leg presets like Iron Condor have 4 legs that span 4 strikes;
 *     getting one wrong because the chain's ATM differed from the user's
 *     mental model is annoying. The dialog makes the strike grid explicit.
 *   - Adjusting lot qty per leg up-front saves a follow-up edit per leg.
 *
 * Math: same strikeOffset → strike resolution as the direct-apply path
 * (lib/strategyTemplates.resolveStrikeOffset). Entry prices are pulled
 * from the chain LTP at the resolved strike. Calendar / diagonal templates
 * also resolve their `expiryOffset` against the parent's expiries list.
 */

import { useEffect, useMemo, useState } from "react";
import { Sparkles, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { ChainContext } from "@/hooks/useChainContext";
import {
  resolveStrikeOffset,
  type StrategyTemplate,
} from "@/lib/strategyTemplates";
import { cn } from "@/lib/utils";
import type { Action, OptionType } from "@/types/strategy";

/** Per-leg row state inside the dialog — editable strike + lots. */
interface RowState {
  rowKey: string;
  action: Action;
  optionType: OptionType;
  /** Resolved absolute strike. May be null if chain didn't resolve. */
  strike: number | null;
  /** Entry price prefilled from chain LTP. User can override. */
  entryPrice: number;
  /** API-format expiry the leg will land on (after expiryOffset resolution). */
  expiryApi: string;
  /** Display-format equivalent of expiryApi. */
  expiryDisplay: string;
  /** Lot multiplier — defaults to template's value. */
  lots: number;
}

/** Output payload sent up to the parent on Apply. */
export interface AppliedTemplateLeg {
  action: Action;
  option_type: OptionType;
  strike: number;
  lots: number;
  expiry_api: string;
  entry_price: number;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  template: StrategyTemplate | null;
  /** Live chain context — gives strikes + LTPs at the *primary* expiry. */
  chain: ChainContext | null;
  /** Available expiries in DISPLAY format ("DD-MMM-YYYY"). */
  expiries: string[];
  /** Currently-selected primary expiry (display format). */
  primaryExpiry: string;
  underlying: string;
  /** Default lot size — used when chain is loading. */
  defaultLotSize: number;
  /** Convert display expiry → backend "DDMMMYY". */
  convertExpiryForApi: (displayExpiry: string) => string;
  /** Apply the (possibly-edited) legs. The parent rebuilds the leg list. */
  onApply: (legs: AppliedTemplateLeg[], template: StrategyTemplate) => void;
}

export function TemplateConfigDialog({
  open,
  onOpenChange,
  template,
  chain,
  expiries,
  primaryExpiry,
  underlying,
  defaultLotSize,
  convertExpiryForApi,
  onApply,
}: Props) {
  const [rows, setRows] = useState<RowState[]>([]);

  // Re-seed rows whenever the dialog opens against a different template,
  // or the chain / primary expiry changes underneath.
  useEffect(() => {
    if (!template || !open) return;
    const baseExpiryIdx = expiries.indexOf(primaryExpiry);
    const seeded: RowState[] = template.legs.map((tl, idx) => {
      // Resolve expiry offset (calendar / diagonal templates use it).
      let expDisp = primaryExpiry;
      const off = tl.expiryOffset ?? 0;
      if (off !== 0 && baseExpiryIdx >= 0) {
        expDisp = expiries[baseExpiryIdx + off] ?? primaryExpiry;
      }
      const expApi = convertExpiryForApi(expDisp);

      let strike: number | null = null;
      let entry = 0;
      if (chain) {
        const resolved = resolveStrikeOffset(
          tl.strikeOffset,
          chain.atm,
          chain.strikes,
        );
        if (resolved !== null) {
          strike = resolved;
          // Only fill entry from chain LTP if leg is on the primary expiry —
          // far-leg calendar prices aren't in the chain context.
          if (expApi === convertExpiryForApi(primaryExpiry)) {
            const ltp =
              tl.option_type === "CE"
                ? chain.ceLtpByStrike.get(resolved)
                : chain.peLtpByStrike.get(resolved);
            if (typeof ltp === "number" && ltp > 0) {
              entry = Number(ltp.toFixed(2));
            }
          }
        }
      }

      return {
        rowKey: `${idx}-${tl.action}-${tl.option_type}-${tl.strikeOffset}`,
        action: tl.action,
        optionType: tl.option_type,
        strike,
        entryPrice: entry,
        expiryApi: expApi,
        expiryDisplay: expDisp,
        lots: Math.max(1, tl.lots),
      };
    });
    setRows(seeded);
  }, [template, open, chain, expiries, primaryExpiry, convertExpiryForApi]);

  const updateRow = (rowKey: string, patch: Partial<RowState>) =>
    setRows((prev) => prev.map((r) => (r.rowKey === rowKey ? { ...r, ...patch } : r)));

  // Net premium summary — purely informational. Sign convention matches
  // openbull's existing rupee P&L: BUY=+1 (debit), SELL=−1 (credit).
  const summary = useMemo(() => {
    if (rows.length === 0) return null;
    const lotSize = chain?.lotSize ?? defaultLotSize;
    let net = 0;
    for (const r of rows) {
      if (r.strike === null) continue;
      const sign = r.action === "BUY" ? 1 : -1;
      net += sign * r.entryPrice * r.lots * lotSize;
    }
    return { net, lotSize };
  }, [rows, chain?.lotSize, defaultLotSize]);

  const unresolvedCount = rows.filter((r) => r.strike === null).length;
  const canApply =
    rows.length > 0 &&
    rows.every((r) => r.strike !== null && r.lots > 0 && r.expiryApi);

  const handleApply = () => {
    if (!template || !canApply) return;
    const out: AppliedTemplateLeg[] = rows
      .filter((r) => r.strike !== null)
      .map((r) => ({
        action: r.action,
        option_type: r.optionType,
        strike: r.strike as number,
        lots: r.lots,
        expiry_api: r.expiryApi,
        entry_price: r.entryPrice,
      }));
    onApply(out, template);
    onOpenChange(false);
  };

  if (!template) return null;

  const directionTone =
    template.direction === "BULLISH"
      ? "text-emerald-700 dark:text-emerald-400"
      : template.direction === "BEARISH"
      ? "text-rose-700 dark:text-rose-400"
      : "text-amber-700 dark:text-amber-400";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            <span>{template.name}</span>
            <span
              className={cn(
                "rounded-full bg-muted px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider",
                directionTone,
              )}
            >
              {template.direction === "NON_DIRECTIONAL"
                ? "Neutral"
                : template.direction.toLowerCase()}
            </span>
          </DialogTitle>
          <p className="text-xs text-muted-foreground">
            {template.description}
            {underlying && (
              <>
                {" "}
                ·{" "}
                <span className="font-semibold text-foreground">
                  {underlying}
                </span>
              </>
            )}
          </p>
        </DialogHeader>

        {/* Resolution warning */}
        {unresolvedCount > 0 && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px] text-amber-700 dark:text-amber-300">
            {unresolvedCount} of {rows.length} leg{rows.length === 1 ? "" : "s"} couldn't
            resolve a strike — the chain may not extend that far. Pick a strike
            below or wait for the chain to load.
          </div>
        )}

        {/* Per-leg rows */}
        <div className="overflow-hidden rounded-lg border">
          <div className="grid grid-cols-[40px_92px_minmax(0,1fr)_92px_104px] items-center gap-2 border-b bg-muted/30 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            <span>Side</span>
            <span>Type</span>
            <span>Strike</span>
            <span className="text-right">Lots</span>
            <span className="text-right">Entry ₹</span>
          </div>
          <div className="max-h-[40vh] overflow-y-auto">
            {rows.map((r) => (
              <div
                key={r.rowKey}
                className="grid grid-cols-[40px_92px_minmax(0,1fr)_92px_104px] items-center gap-2 border-b border-border/60 px-3 py-2 text-sm last:border-b-0"
              >
                {/* Side badge — preset by template, not editable here */}
                <span
                  className={cn(
                    "inline-flex h-7 items-center justify-center rounded font-bold",
                    r.action === "BUY"
                      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                      : "bg-rose-500/15 text-rose-700 dark:text-rose-400",
                  )}
                >
                  {r.action}
                </span>

                {/* Type badge */}
                <span
                  className={cn(
                    "inline-flex h-7 items-center justify-center rounded font-bold",
                    r.optionType === "CE"
                      ? "bg-red-500/10 text-red-600 dark:text-red-400"
                      : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                  )}
                >
                  {r.optionType}
                </span>

                {/* Strike — chain-sourced dropdown when available */}
                {chain && chain.strikes.length > 0 ? (
                  <select
                    value={r.strike !== null ? String(r.strike) : ""}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (!Number.isFinite(v)) return;
                      // Refresh entry price from chain LTP at the new strike.
                      const ltp =
                        r.optionType === "CE"
                          ? chain.ceLtpByStrike.get(v)
                          : chain.peLtpByStrike.get(v);
                      updateRow(r.rowKey, {
                        strike: v,
                        entryPrice:
                          typeof ltp === "number" && ltp > 0
                            ? Number(ltp.toFixed(2))
                            : r.entryPrice,
                      });
                    }}
                    className="h-8 rounded-md border border-input bg-background px-2 text-xs tabular-nums outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
                  >
                    {r.strike === null && <option value="">— pick —</option>}
                    {r.strike !== null && !chain.strikes.includes(r.strike) && (
                      <option value={String(r.strike)}>{r.strike}</option>
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
                    value={r.strike !== null ? r.strike : ""}
                    onChange={(e) =>
                      updateRow(r.rowKey, {
                        strike: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    className="h-8 text-xs tabular-nums"
                  />
                )}

                {/* Lots */}
                <Input
                  type="number"
                  min={1}
                  step={1}
                  value={r.lots}
                  onChange={(e) =>
                    updateRow(r.rowKey, {
                      lots: Math.max(
                        1,
                        Math.floor(Number(e.target.value) || 1),
                      ),
                    })
                  }
                  className="h-8 text-right text-xs tabular-nums"
                />

                {/* Entry */}
                <Input
                  type="number"
                  step="0.05"
                  min={0}
                  value={r.entryPrice}
                  onChange={(e) =>
                    updateRow(r.rowKey, {
                      entryPrice: Number(e.target.value) || 0,
                    })
                  }
                  className="h-8 text-right text-xs tabular-nums"
                />
              </div>
            ))}
          </div>
        </div>

        {/* Net premium summary */}
        {summary && summary.net !== 0 && (
          <div className="flex items-center justify-between rounded-md border bg-muted/20 px-3 py-2 text-xs">
            <span className="text-muted-foreground">
              {summary.net > 0 ? "Net debit (rough)" : "Net credit (rough)"}
            </span>
            <span
              className={cn(
                "font-semibold tabular-nums",
                summary.net > 0
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-emerald-600 dark:text-emerald-400",
              )}
            >
              ₹{Math.abs(summary.net).toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })}
            </span>
          </div>
        )}

        <DialogFooter className="flex-row items-center justify-between sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            className="gap-1.5"
          >
            <X className="h-3.5 w-3.5" />
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleApply}
            disabled={!canApply}
            className="gap-1.5"
          >
            Apply {rows.length} leg{rows.length === 1 ? "" : "s"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
