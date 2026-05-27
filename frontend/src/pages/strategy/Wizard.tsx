import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  createStrategy,
  listStrikes,
  listUnderlyings,
  updateStrategy,
  type UnderlyingChoice,
} from "@/api/strategy_module";
import {
  ATM_OFFSETS,
  EXPIRY_RANK_LABELS,
  LEG_SIDE_LABELS,
  SIGNAL_MODE_ALLOWED_TABS,
  STRATEGY_DIRECTION_LABELS,
  STRATEGY_KIND_HINT,
  STRATEGY_KIND_LABELS,
  TAB_DEFAULT_UNDERLYINGS,
  TAB_EXPIRIES,
  TAB_INTRADAY_DEFAULTS,
  TAB_SEGMENTS,
  UNIVERSE_TAB_HINT,
  UNIVERSE_TAB_LABELS,
  allowedProductsForLegs,
  defaultProductForLegs,
  defaultProductForSignal,
  type ExpiryRank,
  type Leg,
  type LegSide,
  type Position,
  type Product,
  type Segment,
  type Strategy,
  type StrategyCreate,
  type StrategyDirection,
  type StrategyKind,
  type StrategyType,
  type StrategyUpdate,
  type UniverseTab,
} from "@/types/strategy_module";
import { cn } from "@/lib/utils";

const TABS: UniverseTab[] = [
  "weekly_monthly",
  "monthly_only",
  "stocks_fno",
  "mcx",
];

function freshLeg(id: number, tab: UniverseTab): Leg {
  const allowedExpiries = expiriesFor(tab, "options");
  return {
    id,
    segment: "options",
    expiry: allowedExpiries[0],
    lots: 1,
    position: "S",
    option_type: "CE",
    strike_mode: "atm",
    atm_offset: "ATM",
    strike_value: null,
    target_pts: null,
    sl_pts: null,
    trail: { x: 0, y: 0 },
    momentum: null,
  };
}

/** Signal-mode leg defaults: cash for stocks (NSE), futures for MCX,
 *  options for index tabs (weekly_monthly / monthly_only — no spot
 *  trading on indices). The user can switch segment per-leg afterwards.
 *  Options legs ride the same option_type / strike_mode / atm_offset /
 *  strike_value pipeline as batch-mode; the engine resolves the actual
 *  contract at signal time from (leg.symbol, expiry rank, option fields). */
function freshSignalLeg(id: number, tab: UniverseTab): Leg {
  const allowedSegs = TAB_SEGMENTS[tab];
  const segment: Segment =
    tab === "mcx"
      ? "futures"
      : tab === "weekly_monthly" || tab === "monthly_only"
        ? "options"
        : allowedSegs.includes("cash")
          ? "cash"
          : allowedSegs[0];
  const expiry: ExpiryRank | null =
    segment === "cash" ? null : expiriesFor(tab, segment)[0] ?? "current_month";
  return {
    id,
    segment,
    expiry,
    lots: 1,
    position: "B",
    option_type: segment === "options" ? "CE" : null,
    strike_mode: segment === "options" ? "atm" : null,
    atm_offset: segment === "options" ? "ATM" : null,
    strike_value: null,
    symbol: "",
    exchange: "",
    side: "both",
    qty: 1,
    target_pts: null,
    sl_pts: null,
    trail: { x: 0, y: 0 },
    momentum: null,
  };
}

interface LegCardProps {
  leg: Leg;
  tab: UniverseTab;
  index: number;
  underlying: string;
  underlyingExchange: string;
  onChange: (next: Leg) => void;
  onRemove: () => void;
  onOpenStrikePicker: () => void;
  removable: boolean;
}

/**
 * Expiry-rank choices for a leg given (tab, segment).
 *
 * - Futures contracts are monthly-only on every Indian exchange — even
 *   NIFTY/SENSEX have monthly FUT only (weekly contracts exist on the
 *   options side, not the futures side). So segment=futures always gets
 *   the two monthly ranks regardless of tab.
 * - Options on weekly_monthly tabs (NIFTY, SENSEX) get the full four
 *   ranks. Stock F&O and MCX are monthly-only.
 */
function expiriesFor(tab: UniverseTab, segment: Segment): ExpiryRank[] {
  if (segment === "cash") return [];
  if (segment === "futures") return ["current_month", "next_month"];
  return TAB_EXPIRIES[tab];
}

function LegCard({
  leg,
  tab,
  index,
  underlying: _underlying,
  underlyingExchange: _underlyingExchange,
  onChange,
  onRemove,
  onOpenStrikePicker,
  removable,
}: LegCardProps) {
  const segments = TAB_SEGMENTS[tab];
  const expiries = expiriesFor(tab, leg.segment);

  const update = <K extends keyof Leg>(key: K, value: Leg[K]) => {
    onChange({ ...leg, [key]: value });
  };

  return (
    <Card className="border-dashed bg-muted/30">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base">Leg {index + 1}</CardTitle>
        {removable && (
          <Button size="sm" variant="ghost" onClick={onRemove}>
            Remove
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Segment</Label>
            <select
              value={leg.segment}
              onChange={(e) => {
                const seg = e.target.value as Segment;
                // Resolve the NEW strike_mode first; the atm_offset /
                // strike_value defaults must be evaluated against that
                // (not against `leg.strike_mode`, which may be null after
                // an earlier round-trip through a non-options segment).
                // Without this, toggling off→on options leaves
                // atm_offset=null while strike_mode='atm' and the schema
                // rejects with "atm_offset required when strike_mode='atm'".
                const nextStrikeMode =
                  seg === "options" ? leg.strike_mode ?? "atm" : null;
                onChange({
                  ...leg,
                  segment: seg,
                  option_type: seg === "options" ? leg.option_type ?? "CE" : null,
                  strike_mode: nextStrikeMode,
                  atm_offset:
                    nextStrikeMode === "atm"
                      ? leg.atm_offset ?? "ATM"
                      : null,
                  strike_value:
                    nextStrikeMode === "strike"
                      ? leg.strike_value ?? null
                      : null,
                });
              }}
              className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {segments.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Expiry</Label>
            <select
              value={leg.expiry}
              onChange={(e) => update("expiry", e.target.value as ExpiryRank)}
              className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {expiries.map((e) => (
                <option key={e} value={e}>
                  {EXPIRY_RANK_LABELS[e]}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Lots</Label>
            <Input
              type="number"
              min={1}
              max={50}
              value={leg.lots}
              onChange={(e) => update("lots", Math.max(1, parseInt(e.target.value || "1", 10)))}
              className="h-9"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Position</Label>
            <div className="flex h-9 overflow-hidden rounded-md border border-input">
              {(["B", "S"] as Position[]).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => update("position", p)}
                  className={cn(
                    "flex-1 text-sm font-medium transition-colors",
                    leg.position === p
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-muted",
                  )}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        </div>

        {leg.segment === "options" && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="space-y-1.5">
              <Label className="text-xs uppercase">Option Type</Label>
              <div className="flex h-9 overflow-hidden rounded-md border border-input">
                {(["CE", "PE"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => update("option_type", t)}
                    className={cn(
                      "flex-1 text-sm font-medium transition-colors",
                      leg.option_type === t
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs uppercase">Strike mode</Label>
              <div className="flex h-9 overflow-hidden rounded-md border border-input">
                {(["atm", "strike"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      onChange({
                        ...leg,
                        strike_mode: m,
                        atm_offset: m === "atm" ? leg.atm_offset ?? "ATM" : null,
                        strike_value: m === "strike" ? leg.strike_value ?? null : null,
                      });
                    }}
                    className={cn(
                      "flex-1 text-sm font-medium transition-colors",
                      leg.strike_mode === m
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {m === "atm" ? "ATM-relative" : "Direct strike"}
                  </button>
                ))}
              </div>
            </div>

            {leg.strike_mode === "atm" ? (
              <div className="space-y-1.5 sm:col-span-2">
                <Label className="text-xs uppercase">Strike offset</Label>
                <select
                  value={leg.atm_offset ?? "ATM"}
                  onChange={(e) => update("atm_offset", e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  {ATM_OFFSETS.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="space-y-1.5 sm:col-span-2">
                <Label className="text-xs uppercase">Strike value</Label>
                <div className="flex gap-2">
                  <Input
                    type="number"
                    step={0.01}
                    value={leg.strike_value ?? ""}
                    placeholder="Pick from list →"
                    readOnly
                    className="h-9 font-mono"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={onOpenStrikePicker}
                  >
                    Pick strike
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Filtered by underlying + resolved expiry rank ({leg.expiry}).
                </p>
              </div>
            )}
          </div>
        )}

        <Separator />

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Stop Loss (pts)</Label>
            <Input
              type="number"
              step={0.01}
              min={0}
              value={leg.sl_pts ?? ""}
              placeholder="0 = off"
              onChange={(e) =>
                update("sl_pts", e.target.value === "" ? null : Number(e.target.value))
              }
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Target (pts)</Label>
            <Input
              type="number"
              step={0.01}
              min={0}
              value={leg.target_pts ?? ""}
              placeholder="0 = off"
              onChange={(e) =>
                update("target_pts", e.target.value === "" ? null : Number(e.target.value))
              }
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Trail SL — X (pts)</Label>
            <Input
              type="number"
              step={0.01}
              min={0}
              value={leg.trail.x}
              onChange={(e) =>
                update("trail", { ...leg.trail, x: Number(e.target.value || 0) })
              }
              className="h-9"
            />
            <p className="text-[10px] text-muted-foreground">
              With Y blank: initial SL at entry ± X, then trails the peak 1:1 by
              X pts. With Y set: stepped trail — arms at X, advances in Y-pt
              steps.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Trail SL — Y (step, optional)</Label>
            <Input
              type="number"
              step={0.01}
              min={0}
              value={leg.trail.y}
              onChange={(e) =>
                update("trail", { ...leg.trail, y: Number(e.target.value || 0) })
              }
              className="h-9"
            />
            <p className="text-[10px] text-muted-foreground">
              Leave blank (or 0) for a classic fixed-distance trail driven by X
              alone.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Signal-mode leg card (slice 8). Multi-symbol leg builder - each leg picks
// its own symbol+exchange, side (long/short/both), and absolute qty.
// ---------------------------------------------------------------------------

interface SignalLegCardProps {
  leg: Leg;
  tab: UniverseTab;
  index: number;
  underlyings: Array<{ symbol: string; name: string; exchange: string }>;
  strategyType: StrategyType;
  onChange: (next: Leg) => void;
  onRemove: () => void;
  removable: boolean;
}

function SignalLegCard({
  leg,
  tab,
  index,
  underlyings,
  strategyType,
  onChange,
  onRemove,
  removable,
}: SignalLegCardProps) {
  // Same set of segments per tab as batch mode — drives whether the
  // user can pick options/futures/cash. The engine resolves the actual
  // FUT/option contract from (leg.symbol, expiry rank, option fields)
  // at signal time, so all three segments work in signal mode.
  const segments: Segment[] = TAB_SEGMENTS[tab];
  const expiryChoices: ExpiryRank[] = expiriesFor(tab, leg.segment);

  const update = <K extends keyof Leg>(key: K, value: Leg[K]) => {
    onChange({ ...leg, [key]: value });
  };

  const onSymbolChange = (sym: string) => {
    // When picking from the dropdown, the matching entry's exchange is
    // also populated so the user doesn't have to type it. Free-text
    // edits (typing a symbol not in the list) leave exchange untouched.
    const match = underlyings.find((u) => u.symbol === sym);
    onChange({
      ...leg,
      symbol: sym,
      exchange: match ? match.exchange : leg.exchange ?? "",
    });
  };

  const qtyLabel =
    leg.segment === "cash" ? "Quantity (shares)" : "Quantity (units)";

  return (
    <Card className="border-dashed bg-muted/30">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="text-base">Leg {index + 1}</CardTitle>
        {removable && (
          <Button size="sm" variant="ghost" onClick={onRemove}>
            Remove
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Symbol</Label>
            <Input
              list={`signal-symbols-${leg.id}`}
              value={leg.symbol ?? ""}
              onChange={(e) => onSymbolChange(e.target.value.toUpperCase())}
              placeholder="e.g. RELIANCE"
              className="h-9 font-mono"
            />
            <datalist id={`signal-symbols-${leg.id}`}>
              {underlyings.map((u) => (
                <option key={u.symbol} value={u.symbol}>
                  {u.name}
                </option>
              ))}
            </datalist>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Exchange</Label>
            <Input
              value={leg.exchange ?? ""}
              onChange={(e) =>
                update("exchange", e.target.value.toUpperCase())
              }
              placeholder={tab === "mcx" ? "MCX" : "NSE"}
              className="h-9 font-mono"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Segment</Label>
            <select
              value={leg.segment}
              onChange={(e) => {
                const seg = e.target.value as Segment;
                // Resolve the new strike_mode first and use it as the
                // source of truth for the atm_offset / strike_value
                // defaults — same race fix as the batch LegCard.
                const nextStrikeMode =
                  seg === "options" ? leg.strike_mode ?? "atm" : null;
                onChange({
                  ...leg,
                  segment: seg,
                  expiry:
                    seg === "cash" ? null : expiryChoices[0] ?? "current",
                  option_type: seg === "options" ? leg.option_type ?? "CE" : null,
                  strike_mode: nextStrikeMode,
                  atm_offset:
                    nextStrikeMode === "atm"
                      ? leg.atm_offset ?? "ATM"
                      : null,
                  strike_value:
                    nextStrikeMode === "strike"
                      ? leg.strike_value ?? null
                      : null,
                });
              }}
              className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {segments.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>

          {leg.segment !== "cash" && (
            <div className="space-y-1.5">
              <Label className="text-xs uppercase">Expiry</Label>
              <select
                value={leg.expiry ?? expiryChoices[0] ?? "current_month"}
                onChange={(e) => update("expiry", e.target.value as ExpiryRank)}
                className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {expiryChoices.map((e) => (
                  <option key={e} value={e}>
                    {EXPIRY_RANK_LABELS[e]}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Side</Label>
            <select
              value={leg.side ?? "both"}
              onChange={(e) => update("side", e.target.value as LegSide)}
              className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {(["long", "short", "both"] as LegSide[]).map((s) => (
                <option key={s} value={s}>
                  {LEG_SIDE_LABELS[s]}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs uppercase">{qtyLabel}</Label>
            <Input
              type="number"
              min={1}
              max={1000000}
              value={leg.qty ?? 1}
              onChange={(e) =>
                update("qty", Math.max(1, parseInt(e.target.value || "1", 10)))
              }
              className="h-9"
            />
          </div>
        </div>

        {leg.segment === "options" && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="space-y-1.5">
              <Label className="text-xs uppercase">Option Type</Label>
              <div className="flex h-9 overflow-hidden rounded-md border border-input">
                {(["CE", "PE"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => update("option_type", t)}
                    className={cn(
                      "flex-1 text-sm font-medium transition-colors",
                      leg.option_type === t
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs uppercase">Strike mode</Label>
              <div className="flex h-9 overflow-hidden rounded-md border border-input">
                {(["atm", "strike"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      onChange({
                        ...leg,
                        strike_mode: m,
                        atm_offset: m === "atm" ? leg.atm_offset ?? "ATM" : null,
                        strike_value:
                          m === "strike" ? leg.strike_value ?? null : null,
                      });
                    }}
                    className={cn(
                      "flex-1 text-sm font-medium transition-colors",
                      leg.strike_mode === m
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {m === "atm" ? "ATM-relative" : "Direct strike"}
                  </button>
                ))}
              </div>
            </div>

            {leg.strike_mode === "atm" ? (
              <div className="space-y-1.5 sm:col-span-2">
                <Label className="text-xs uppercase">Strike offset</Label>
                <select
                  value={leg.atm_offset ?? "ATM"}
                  onChange={(e) => update("atm_offset", e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  {ATM_OFFSETS.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="space-y-1.5 sm:col-span-2">
                <Label className="text-xs uppercase">Strike value</Label>
                <Input
                  type="number"
                  step={0.01}
                  value={leg.strike_value ?? ""}
                  placeholder="e.g. 25000"
                  onChange={(e) =>
                    update(
                      "strike_value",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                  className="h-9 font-mono"
                />
                <p className="text-xs text-muted-foreground">
                  Engine looks up this strike on {leg.symbol || "<symbol>"}{" "}
                  {leg.expiry} {leg.option_type} at signal time.
                </p>
              </div>
            )}
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Product for this leg:{" "}
          <span className="font-mono">
            {defaultProductForSignal(strategyType, leg.segment)}
          </span>{" "}
          (auto-picked from strategy type and segment).
        </p>
      </CardContent>
    </Card>
  );
}

interface StrikePickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  underlying: string;
  underlyingExchange: string;
  expiryRank: ExpiryRank;
  optionType: "CE" | "PE";
  selectedStrike: number | null;
  onPick: (strike: number) => void;
}

function StrikePickerDialog({
  open,
  onOpenChange,
  underlying,
  underlyingExchange,
  expiryRank,
  optionType,
  selectedStrike,
  onPick,
}: StrikePickerProps) {
  const [filter, setFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["strikes", underlying, underlyingExchange, expiryRank, optionType],
    queryFn: () =>
      listStrikes({
        underlying,
        underlying_exchange: underlyingExchange,
        expiry_rank: expiryRank,
        option_type: optionType,
      }),
    enabled: open && !!underlying,
    staleTime: 60_000,
  });

  const strikes = data?.strikes ?? [];
  const filtered = filter
    ? strikes.filter((s) => String(s).includes(filter))
    : strikes;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            Pick strike — {underlying} {expiryRank} {optionType}
          </DialogTitle>
          {data && (
            <p className="text-xs text-muted-foreground">
              {strikes.length} strikes available · resolved expiry:{" "}
              <span className="font-mono">{data.expiry}</span> on {data.exchange}
            </p>
          )}
        </DialogHeader>
        <div className="space-y-3">
          <Input
            placeholder="Filter (e.g. 24000)…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            autoFocus
          />
          <div className="max-h-72 overflow-y-auto rounded-md border">
            {isLoading ? (
              <p className="p-3 text-center text-sm text-muted-foreground">Loading…</p>
            ) : error ? (
              <p className="p-3 text-center text-sm text-destructive">
                Failed to load strikes. Master contract may not be downloaded.
              </p>
            ) : filtered.length === 0 ? (
              <p className="p-3 text-center text-sm text-muted-foreground">No matches</p>
            ) : (
              <ul className="divide-y">
                {filtered.map((strike) => (
                  <li key={strike}>
                    <button
                      type="button"
                      onClick={() => {
                        onPick(strike);
                        onOpenChange(false);
                      }}
                      className={cn(
                        "flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-muted",
                        selectedStrike === strike && "bg-primary/10 font-semibold",
                      )}
                    >
                      <span className="font-mono">{strike}</span>
                      {selectedStrike === strike && (
                        <Badge variant="secondary" className="text-[10px]">
                          selected
                        </Badge>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface StrategyWizardProps {
  /** When provided, the form pre-fills from this strategy and saves via
   *  PATCH instead of POST. Used by the /strategy/:id/edit route. */
  editing?: Strategy;
}

export default function StrategyWizard({ editing }: StrategyWizardProps = {}) {
  const navigate = useNavigate();
  const isEdit = editing != null;

  // ---- Section 0: strategy kind + direction (slice 8) ----
  // Kind is locked once a strategy is created (backend forbids the
  // strategy_kind field on PATCH per slice 2). The UI mirrors that
  // by making the toggle read-only in edit mode.
  const initialKind: StrategyKind =
    (editing?.strategy_kind as StrategyKind) ?? "batch";
  const [kind, setKind] = useState<StrategyKind>(initialKind);
  const [direction, setDirection] = useState<StrategyDirection>(
    (editing?.direction as StrategyDirection) ?? "both",
  );
  const isSignal = kind === "signal";

  // ---- Section A: tab + index + timings ----
  const [tab, setTab] = useState<UniverseTab>(
    editing?.universe_tab ?? (isSignal ? "stocks_fno" : "weekly_monthly"),
  );
  const [name, setName] = useState(editing?.name ?? "");
  const [underlying, setUnderlying] = useState<string>(
    editing?.underlying ?? TAB_DEFAULT_UNDERLYINGS.weekly_monthly[0].symbol,
  );
  const [strategyType, setStrategyType] = useState<StrategyType>(
    editing?.strategy_type ?? "intraday",
  );
  const _initialTab: UniverseTab =
    editing?.universe_tab ?? (isSignal ? "stocks_fno" : "weekly_monthly");
  const [entryTime, setEntryTime] = useState(
    editing?.entry_time ?? TAB_INTRADAY_DEFAULTS[_initialTab].entry,
  );
  const [exitTime, setExitTime] = useState(
    editing?.exit_time ?? TAB_INTRADAY_DEFAULTS[_initialTab].exit,
  );
  const [product, setProduct] = useState<Product>(
    editing?.product ??
      defaultProductForLegs(
        editing?.legs ?? [
          isSignal ? freshSignalLeg(1, _initialTab) : freshLeg(1, _initialTab),
        ],
      ),
  );

  // Underlyings come from the API now (dynamic for stocks_fno and mcx).
  // The hardcoded TAB_DEFAULT_UNDERLYINGS is the seed shown until the
  // first fetch returns — keeps the wizard responsive on slow connections.
  const { data: underlyingsData } = useQuery({
    queryKey: ["strategy-underlyings", tab],
    queryFn: () => listUnderlyings(tab),
    staleTime: 60_000,
  });
  const underlyings: UnderlyingChoice[] =
    underlyingsData && underlyingsData.length > 0
      ? underlyingsData
      : TAB_DEFAULT_UNDERLYINGS[tab];

  const underlyingExchange = useMemo(
    () => underlyings.find((u) => u.symbol === underlying)?.exchange ?? underlyings[0]?.exchange ?? "NSE",
    [underlying, underlyings],
  );

  // ---- Section B: legs ----
  const [legs, setLegs] = useState<Leg[]>(() => {
    if (editing && editing.legs.length > 0) return editing.legs;
    const startTab = editing?.universe_tab ?? (isSignal ? "stocks_fno" : "weekly_monthly");
    return [isSignal ? freshSignalLeg(1, startTab) : freshLeg(1, startTab)];
  });

  // ---- Strike picker state (open one at a time, scoped to a leg index) ----
  const [strikePickerLegIndex, setStrikePickerLegIndex] = useState<number | null>(
    null,
  );

  // ---- Section C: overall risk ----
  const [overallSl, setOverallSl] = useState<string>(
    editing?.overall_sl_mtm != null ? String(editing.overall_sl_mtm) : "",
  );
  const [overallTarget, setOverallTarget] = useState<string>(
    editing?.overall_target_mtm != null ? String(editing.overall_target_mtm) : "",
  );
  const [trailToEntry, setTrailToEntry] = useState(
    editing?.trail_sl_to_entry ?? false,
  );
  const [lockEnabled, setLockEnabled] = useState(editing?.lock_profit != null);
  const [lockMode, setLockMode] = useState<"lock" | "lock_and_trail">(
    editing?.lock_profit?.mode ?? "lock",
  );
  const [lockProfitReaches, setLockProfitReaches] = useState<string>(
    editing?.lock_profit?.if_profit_reaches != null
      ? String(editing.lock_profit.if_profit_reaches)
      : "",
  );
  const [lockProfitFloor, setLockProfitFloor] = useState<string>(
    editing?.lock_profit?.lock_profit != null
      ? String(editing.lock_profit.lock_profit)
      : "",
  );
  const [lockTrailStep, setLockTrailStep] = useState<string>(
    editing?.lock_profit?.trail_step != null
      ? String(editing.lock_profit.trail_step)
      : "",
  );

  // ---- Section D: scheduler ----
  const [schedulerEnabled, setSchedulerEnabled] = useState(
    editing?.scheduler?.enabled ?? false,
  );
  const [schedulerStart, setSchedulerStart] = useState(
    editing?.scheduler?.start_time ?? "09:15",
  );
  const [schedulerStop, setSchedulerStop] = useState<string>(
    editing?.scheduler?.auto_stop_time ?? "",
  );

  const onTabChange = (next: UniverseTab) => {
    setTab(next);
    // Seed-pick a sensible default; the API fetch will overwrite shortly.
    const seed = TAB_DEFAULT_UNDERLYINGS[next];
    if (seed.length > 0) setUnderlying(seed[0].symbol);
    // Reset legs to one fresh leg with the right shape for the kind+tab.
    const seededLegs = [
      isSignal ? freshSignalLeg(1, next) : freshLeg(1, next),
    ];
    setLegs(seededLegs);
    // Tab-aware intraday window — MCX runs 09:00-23:25, NSE/BSE 09:35-15:15.
    setEntryTime(TAB_INTRADAY_DEFAULTS[next].entry);
    setExitTime(TAB_INTRADAY_DEFAULTS[next].exit);
    // Tab-aware default product — NSE/BSE cash = MIS, derivatives = NRML.
    setProduct(defaultProductForLegs(seededLegs));
  };

  const onKindChange = (next: StrategyKind) => {
    if (isEdit) return; // immutable post-create
    setKind(next);
    // Pick a tab valid for the new kind (signal-mode hides options tabs).
    const nextTab: UniverseTab =
      next === "signal" && !SIGNAL_MODE_ALLOWED_TABS.includes(tab)
        ? SIGNAL_MODE_ALLOWED_TABS[0]
        : tab;
    setTab(nextTab);
    setLegs([next === "signal" ? freshSignalLeg(1, nextTab) : freshLeg(1, nextTab)]);
  };

  const addLeg = () => {
    if (legs.length >= 10) {
      toast.error("Up to 10 legs per strategy");
      return;
    }
    const nextId = (legs.at(-1)?.id ?? 0) + 1;
    setLegs([
      ...legs,
      isSignal ? freshSignalLeg(nextId, tab) : freshLeg(nextId, tab),
    ]);
  };

  const updateLeg = (i: number, next: Leg) => {
    const copy = legs.slice();
    copy[i] = next;
    setLegs(copy);
  };

  const removeLeg = (i: number) => {
    if (legs.length <= 1) return;
    setLegs(legs.filter((_, idx) => idx !== i));
  };

  // Snap `product` back to a valid value whenever the leg composition
  // changes (e.g. user switched a leg from futures to cash and the old
  // NRML selection no longer applies).
  const allowedProducts = useMemo(() => allowedProductsForLegs(legs), [legs]);
  useEffect(() => {
    if (!allowedProducts.includes(product)) {
      setProduct(allowedProducts[0]);
    }
  }, [allowedProducts, product]);

  // ---- Webhook reveal modal (one-time-view of the token) ----
  const [revealedToken, setRevealedToken] = useState<{
    token: string;
    url: string;
    strategyId: number;
  } | null>(null);

  const createMutation = useMutation({
    mutationFn: (payload: StrategyCreate) => createStrategy(payload),
    onSuccess: (response) => {
      setRevealedToken({
        token: response.webhook_token,
        url: response.strategy.webhook_url,
        strategyId: response.strategy.id,
      });
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Failed to create strategy";
      toast.error(typeof detail === "string" ? detail : JSON.stringify(detail));
    },
  });

  const updateMutation = useMutation({
    mutationFn: (payload: StrategyUpdate) => updateStrategy(editing!.id, payload),
    onSuccess: (updated) => {
      toast.success("Strategy updated");
      navigate(`/strategy/${updated.id}`);
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Failed to update strategy";
      toast.error(typeof detail === "string" ? detail : JSON.stringify(detail));
    },
  });

  const submit = () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }

    // Per-kind preflight validation. Backend Pydantic does this too,
    // but catching here gives a clearer error in the toast.
    if (isSignal) {
      for (const leg of legs) {
        if (!leg.symbol?.trim()) {
          toast.error(`Leg ${leg.id}: symbol is required`);
          return;
        }
        if (!leg.exchange?.trim()) {
          toast.error(`Leg ${leg.id}: exchange is required`);
          return;
        }
        if (!leg.qty || leg.qty < 1) {
          toast.error(`Leg ${leg.id}: quantity must be at least 1`);
          return;
        }
      }
    }

    // For signal-mode strategies the strategy-row underlying / exchange
    // are nominal (each leg carries its own symbol). Pick the first
    // leg's symbol as a stand-in so the existing detail-page header
    // and list view have something meaningful to display.
    const firstSignalLeg = isSignal ? legs[0] : null;
    const submittedUnderlying = isSignal
      ? (firstSignalLeg?.symbol?.toUpperCase() || "MULTI")
      : underlying.toUpperCase();
    const submittedExchange = isSignal
      ? (firstSignalLeg?.exchange?.toUpperCase() || "NSE")
      : underlyingExchange;

    const payload: StrategyCreate = {
      name: name.trim(),
      strategy_kind: kind,
      direction,
      universe_tab: tab,
      underlying: submittedUnderlying,
      underlying_exchange: submittedExchange,
      strategy_type: strategyType,
      entry_time: strategyType === "intraday" ? entryTime : null,
      exit_time: strategyType === "intraday" ? exitTime : null,
      product,
      pricetype: "MARKET",
      legs,
      overall_sl_mtm: overallSl ? Number(overallSl) : null,
      overall_target_mtm: overallTarget ? Number(overallTarget) : null,
      trail_sl_to_entry: trailToEntry,
      lock_profit:
        lockEnabled && lockProfitReaches && lockProfitFloor
          ? {
              mode: lockMode,
              if_profit_reaches: Number(lockProfitReaches),
              lock_profit: Number(lockProfitFloor),
              trail_step:
                lockMode === "lock_and_trail" && lockTrailStep
                  ? Number(lockTrailStep)
                  : null,
            }
          : null,
      scheduler: schedulerEnabled
        ? {
            enabled: true,
            days: ["MON", "TUE", "WED", "THU", "FRI"],
            start_time: schedulerStart,
            auto_stop_time: schedulerStop || null,
            default_mode: "sandbox",
          }
        : null,
      webhook_ip_allowlist: null,
      daily_loss_limit_inr: null,
    };

    if (isEdit) {
      // strategy_kind is immutable post-create; the backend's
      // StrategyUpdate schema forbids it. Strip before sending.
      const { strategy_kind: _kind, ...updatePayload } = payload;
      void _kind;
      updateMutation.mutate(updatePayload as StrategyUpdate);
    } else {
      createMutation.mutate(payload);
    }
  };

  const submitting = isEdit ? updateMutation.isPending : createMutation.isPending;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          {isEdit ? `Edit "${editing!.name}"` : "New strategy"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {isEdit
            ? "Edits are committed immediately. The webhook token is preserved - rotate it from the Webhook tab if needed."
            : "Configure legs and risk. Sandbox-only until you explicitly enable live mode on the detail page."}
        </p>
      </div>

      {/* Strategy kind picker - slice 8. Immutable after create per
       * backend Pydantic; UI grays out the unchosen kind in edit mode. */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Strategy kind</CardTitle>
          <CardDescription>
            {isEdit
              ? "Kind is locked after the strategy is created."
              : "Pick how the strategy is driven. This cannot be changed later."}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {(["batch", "signal"] as StrategyKind[]).map((k) => (
              <button
                key={k}
                type="button"
                disabled={isEdit && k !== kind}
                onClick={() => onKindChange(k)}
                className={cn(
                  "rounded-md border p-3 text-left transition-colors",
                  kind === k
                    ? "border-primary bg-primary/10"
                    : "border-border hover:bg-muted/50",
                  isEdit && k !== kind && "cursor-not-allowed opacity-40",
                )}
              >
                <div className="text-sm font-medium">
                  {STRATEGY_KIND_LABELS[k]}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {STRATEGY_KIND_HINT[k]}
                </div>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Universe tabs */}
      <Card>
        <CardContent className="p-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {TABS.map((t) => {
              const disabledForKind =
                isSignal && !SIGNAL_MODE_ALLOWED_TABS.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  disabled={disabledForKind}
                  onClick={() => !disabledForKind && onTabChange(t)}
                  className={cn(
                    "rounded-md border p-3 text-left transition-colors",
                    tab === t
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted/50",
                    disabledForKind && "cursor-not-allowed opacity-40",
                  )}
                >
                  <div className="text-sm font-medium">{UNIVERSE_TAB_LABELS[t]}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {UNIVERSE_TAB_HINT[t]}
                  </div>
                  {disabledForKind && (
                    <div className="mt-1 text-[10px] uppercase text-muted-foreground">
                      not available in signal mode
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Direction picker (signal mode only) */}
      {isSignal && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Direction filter</CardTitle>
            <CardDescription>
              Restricts which signals the engine accepts. Long-only ignores
              short_entry / short_exit signals; short-only ignores the
              long ones. Both accepts all four.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="flex overflow-hidden rounded-md border border-input">
              {(["both", "long_only", "short_only"] as StrategyDirection[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDirection(d)}
                  className={cn(
                    "flex-1 px-3 py-2 text-sm font-medium transition-colors",
                    direction === d
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-muted",
                  )}
                >
                  {STRATEGY_DIRECTION_LABELS[d]}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Index and Timings */}
      <Card>
        <CardHeader>
          <CardTitle>Index and Timings</CardTitle>
          <CardDescription>
            Pick the underlying and (for intraday) entry/exit windows.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="name">Strategy name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Iron condor weekly"
                maxLength={200}
              />
            </div>

            {!isSignal && (
              <div className="space-y-1.5">
                <Label htmlFor="underlying">Underlying</Label>
                <select
                  id="underlying"
                  value={underlying}
                  onChange={(e) => setUnderlying(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                >
                  {underlyings.map((u) => (
                    <option key={u.symbol} value={u.symbol}>
                      {u.symbol} — {u.name}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  Exchange: <span className="font-mono">{underlyingExchange}</span>
                </p>
              </div>
            )}
            {isSignal && (
              <div className="space-y-1.5 rounded-md bg-muted/40 p-2 text-xs text-muted-foreground sm:col-span-1">
                Signal mode: each leg picks its own symbol. The strategy row
                shows the first leg's symbol as a label.
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>Strategy type</Label>
              <div className="flex h-10 overflow-hidden rounded-md border border-input">
                {(["intraday", "positional"] as StrategyType[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setStrategyType(t)}
                    className={cn(
                      "flex-1 text-sm font-medium transition-colors",
                      strategyType === t
                        ? "bg-primary text-primary-foreground"
                        : "bg-background hover:bg-muted",
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            {strategyType === "intraday" && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="entry">Entry time (IST)</Label>
                  <Input
                    id="entry"
                    type="time"
                    value={entryTime}
                    onChange={(e) => setEntryTime(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="exit">Exit time (IST)</Label>
                  <Input
                    id="exit"
                    type="time"
                    value={exitTime}
                    onChange={(e) => setExitTime(e.target.value)}
                  />
                </div>
              </>
            )}
          </div>

          {strategyType === "intraday" ? (
            <p className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
              Intraday signals only execute after your entry time and auto-exit at exit time.
            </p>
          ) : (
            <p className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
              Positional strategies activate on signal and exit automatically at contract expiry.
            </p>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="product">Product</Label>
              <select
                id="product"
                value={product}
                onChange={(e) => setProduct(e.target.value as Product)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              >
                {allowedProducts.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                {allowedProducts.length === 1
                  ? "Mixed cash + derivatives legs: only MIS works for both."
                  : allowedProducts.includes("CNC")
                    ? "Cash equity: CNC (delivery) or MIS (intraday)."
                    : "Derivatives: NRML (carry) or MIS (intraday)."}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Leg builder */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Leg Builder</CardTitle>
            <CardDescription>
              Up to 10 legs. Add as many as you need; remove the rest.
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={addLeg}>
            + Add leg
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {legs.map((leg, i) =>
            isSignal ? (
              <SignalLegCard
                key={leg.id}
                leg={leg}
                tab={tab}
                index={i}
                underlyings={underlyings}
                strategyType={strategyType}
                onChange={(next) => updateLeg(i, next)}
                onRemove={() => removeLeg(i)}
                removable={legs.length > 1}
              />
            ) : (
              <LegCard
                key={leg.id}
                leg={leg}
                tab={tab}
                index={i}
                underlying={underlying}
                underlyingExchange={underlyingExchange}
                onChange={(next) => updateLeg(i, next)}
                onRemove={() => removeLeg(i)}
                onOpenStrikePicker={() => setStrikePickerLegIndex(i)}
                removable={legs.length > 1}
              />
            ),
          )}
        </CardContent>
      </Card>

      {/* Overall settings */}
      <Card>
        <CardHeader>
          <CardTitle>Overall Strategy Settings</CardTitle>
          <CardDescription>
            Strategy-level risk applied across all legs. Evaluated against
            total MTM (realized + unrealized).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="overall-sl">Overall SL (₹ MTM)</Label>
              <Input
                id="overall-sl"
                type="number"
                min={0}
                step={1}
                value={overallSl}
                onChange={(e) => setOverallSl(e.target.value)}
                placeholder="empty = off"
              />
              <p className="text-xs text-muted-foreground">
                Enter as positive — applied as a negative threshold.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="overall-target">Overall Target (₹ MTM)</Label>
              <Input
                id="overall-target"
                type="number"
                min={0}
                step={1}
                value={overallTarget}
                onChange={(e) => setOverallTarget(e.target.value)}
                placeholder="empty = off"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={lockEnabled}
                onChange={(e) => setLockEnabled(e.target.checked)}
              />
              Enable Lock-Profit
            </label>
            {lockEnabled && (
              <div className="space-y-3 rounded-md border border-dashed p-3">
                <div className="flex h-10 overflow-hidden rounded-md border border-input">
                  {(["lock", "lock_and_trail"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setLockMode(m)}
                      className={cn(
                        "flex-1 text-sm font-medium transition-colors",
                        lockMode === m
                          ? "bg-primary text-primary-foreground"
                          : "bg-background hover:bg-muted",
                      )}
                    >
                      {m === "lock" ? "Lock (static floor)" : "Lock + Trail (rising floor)"}
                    </button>
                  ))}
                </div>
                <div
                  className={cn(
                    "grid gap-3 sm:grid-cols-2",
                    lockMode === "lock_and_trail" && "sm:grid-cols-3",
                  )}
                >
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase">If profit reaches (₹)</Label>
                    <Input
                      type="number"
                      min={0}
                      value={lockProfitReaches}
                      onChange={(e) => setLockProfitReaches(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase">Lock floor (₹)</Label>
                    <Input
                      type="number"
                      min={0}
                      value={lockProfitFloor}
                      onChange={(e) => setLockProfitFloor(e.target.value)}
                    />
                  </div>
                  {lockMode === "lock_and_trail" && (
                    <div className="space-y-1.5">
                      <Label className="text-xs uppercase">Trail step (₹)</Label>
                      <Input
                        type="number"
                        min={0}
                        value={lockTrailStep}
                        onChange={(e) => setLockTrailStep(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={trailToEntry}
              onChange={(e) => setTrailToEntry(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Trail SL to entry price</span>
              <span className="ml-2 text-xs text-muted-foreground">
                When ANY leg's SL fires, every other open leg's SL moves to its entry.
                Overall SL is bypassed in this mode.
              </span>
            </span>
          </label>
        </CardContent>
      </Card>

      {/* Scheduler */}
      <Card>
        <CardHeader>
          <CardTitle>Scheduler</CardTitle>
          <CardDescription>
            Optional cron-based start. Mon–Fri default. Times are interpreted
            in IST (Asia/Kolkata).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={schedulerEnabled}
              onChange={(e) => setSchedulerEnabled(e.target.checked)}
            />
            Enable scheduled start (Mon–Fri)
          </label>
          {schedulerEnabled && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Start time (IST)</Label>
                <Input
                  type="time"
                  value={schedulerStart}
                  onChange={(e) => setSchedulerStart(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Auto-stop time (optional)</Label>
                <Input
                  type="time"
                  value={schedulerStop}
                  onChange={(e) => setSchedulerStop(e.target.value)}
                />
              </div>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            Webhook URL is generated automatically on save and shown to you
            once. Copy it into TradingView.
          </p>
        </CardContent>
      </Card>

      <div className="flex items-center justify-end gap-3">
        <Button
          variant="outline"
          onClick={() =>
            navigate(isEdit ? `/strategy/${editing!.id}` : "/strategy")
          }
        >
          Cancel
        </Button>
        <Button onClick={submit} disabled={submitting}>
          {submitting
            ? "Saving…"
            : isEdit
              ? "Save changes"
              : "Save and Continue"}
        </Button>
      </div>

      {/* Searchable strike picker — opens for a single leg at a time */}
      {strikePickerLegIndex !== null && legs[strikePickerLegIndex] && (
        <StrikePickerDialog
          open={strikePickerLegIndex !== null}
          onOpenChange={(o) => !o && setStrikePickerLegIndex(null)}
          underlying={underlying}
          underlyingExchange={underlyingExchange}
          expiryRank={legs[strikePickerLegIndex].expiry}
          optionType={legs[strikePickerLegIndex].option_type ?? "CE"}
          selectedStrike={legs[strikePickerLegIndex].strike_value ?? null}
          onPick={(strike) => {
            const i = strikePickerLegIndex;
            if (i === null) return;
            updateLeg(i, { ...legs[i], strike_value: strike });
          }}
        />
      )}

      {/* One-time webhook-token reveal */}
      <Dialog
        open={revealedToken !== null}
        onOpenChange={(open) => {
          if (!open && revealedToken) {
            navigate(`/strategy/${revealedToken.strategyId}`);
          }
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Strategy created — copy your webhook URL</DialogTitle>
            <p className="text-sm text-muted-foreground">
              This URL contains your secret token. It is shown once and cannot
              be retrieved again. If you lose it, rotate the token from the
              Webhook tab.
            </p>
          </DialogHeader>

          {revealedToken && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>Webhook URL</Label>
                <div className="flex items-center gap-2">
                  <Input readOnly value={revealedToken.url} className="font-mono text-xs" />
                  <Button
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(revealedToken.url);
                      toast.success("Copied URL");
                    }}
                  >
                    Copy
                  </Button>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label>TradingView alert message body</Label>
                <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"start","mode":"sandbox"}`}
                </pre>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button
              onClick={() => {
                if (revealedToken) navigate(`/strategy/${revealedToken.strategyId}`);
              }}
            >
              I've copied it — continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
