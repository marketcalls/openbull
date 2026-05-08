import { useMemo, useState } from "react";
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
  type UnderlyingChoice,
} from "@/api/strategy_module";
import {
  ATM_OFFSETS,
  TAB_DEFAULT_UNDERLYINGS,
  TAB_EXPIRIES,
  TAB_SEGMENTS,
  UNIVERSE_TAB_HINT,
  UNIVERSE_TAB_LABELS,
  type ExpiryRank,
  type Leg,
  type Position,
  type Product,
  type Segment,
  type StrategyCreate,
  type StrategyType,
  type UniverseTab,
} from "@/types/strategy_module";
import { cn } from "@/lib/utils";

const TABS: UniverseTab[] = [
  "weekly_monthly",
  "monthly_only",
  "stocks_fno",
  "mcx",
  "delta",
];

function freshLeg(id: number, tab: UniverseTab): Leg {
  const allowedExpiries = TAB_EXPIRIES[tab];
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
  const expiries = TAB_EXPIRIES[tab];

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
                onChange({
                  ...leg,
                  segment: seg,
                  // Drop option-only fields when switching away from options
                  option_type: seg === "options" ? leg.option_type ?? "CE" : null,
                  strike_mode: seg === "options" ? leg.strike_mode ?? "atm" : null,
                  atm_offset:
                    seg === "options" && leg.strike_mode === "atm"
                      ? leg.atm_offset ?? "ATM"
                      : null,
                  strike_value:
                    seg === "options" && leg.strike_mode === "strike"
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
                  {e}
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
            <Label className="text-xs uppercase">Trail SL — X (trigger pts)</Label>
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
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase">Trail SL — Y (step pts)</Label>
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
          </div>
        </div>
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

export default function StrategyWizard() {
  const navigate = useNavigate();

  // ---- Section A: tab + index + timings ----
  const [tab, setTab] = useState<UniverseTab>("weekly_monthly");
  const [name, setName] = useState("");
  const [underlying, setUnderlying] = useState<string>(
    TAB_DEFAULT_UNDERLYINGS.weekly_monthly[0].symbol,
  );
  const [strategyType, setStrategyType] = useState<StrategyType>("intraday");
  const [entryTime, setEntryTime] = useState("09:35");
  const [exitTime, setExitTime] = useState("15:15");
  const [product, setProduct] = useState<Product>("NRML");

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
  const [legs, setLegs] = useState<Leg[]>([freshLeg(1, "weekly_monthly")]);

  // ---- Strike picker state (open one at a time, scoped to a leg index) ----
  const [strikePickerLegIndex, setStrikePickerLegIndex] = useState<number | null>(
    null,
  );

  // ---- Section C: overall risk ----
  const [overallSl, setOverallSl] = useState<string>("");
  const [overallTarget, setOverallTarget] = useState<string>("");
  const [trailToEntry, setTrailToEntry] = useState(false);
  const [lockEnabled, setLockEnabled] = useState(false);
  const [lockMode, setLockMode] = useState<"lock" | "lock_and_trail">("lock");
  const [lockProfitReaches, setLockProfitReaches] = useState<string>("");
  const [lockProfitFloor, setLockProfitFloor] = useState<string>("");
  const [lockTrailStep, setLockTrailStep] = useState<string>("");

  // ---- Section D: scheduler ----
  const [schedulerEnabled, setSchedulerEnabled] = useState(false);
  const [schedulerStart, setSchedulerStart] = useState("09:15");
  const [schedulerStop, setSchedulerStop] = useState<string>("");

  const onTabChange = (next: UniverseTab) => {
    setTab(next);
    // Seed-pick a sensible default; the API fetch will overwrite shortly.
    const seed = TAB_DEFAULT_UNDERLYINGS[next];
    if (seed.length > 0) setUnderlying(seed[0].symbol);
    // Reset legs to one fresh leg with valid expiry/segment for the new tab
    setLegs([freshLeg(1, next)]);
  };

  const addLeg = () => {
    if (legs.length >= 10) {
      toast.error("Up to 10 legs per strategy");
      return;
    }
    const nextId = (legs.at(-1)?.id ?? 0) + 1;
    setLegs([...legs, freshLeg(nextId, tab)]);
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

  const submit = () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }

    const payload: StrategyCreate = {
      name: name.trim(),
      universe_tab: tab,
      underlying: underlying.toUpperCase(),
      underlying_exchange: underlyingExchange,
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

    createMutation.mutate(payload);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">New strategy</h1>
        <p className="text-sm text-muted-foreground">
          Configure legs and risk. Sandbox-only until you explicitly enable
          live mode on the detail page.
        </p>
      </div>

      {/* Universe tabs */}
      <Card>
        <CardContent className="p-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                disabled={t === "delta"}
                onClick={() => onTabChange(t)}
                className={cn(
                  "rounded-md border p-3 text-left transition-colors",
                  tab === t
                    ? "border-primary bg-primary/10"
                    : "border-border hover:bg-muted/50",
                  t === "delta" && "cursor-not-allowed opacity-40",
                )}
              >
                <div className="text-sm font-medium">{UNIVERSE_TAB_LABELS[t]}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {UNIVERSE_TAB_HINT[t]}
                </div>
                {t === "delta" && (
                  <div className="mt-1">
                    <Badge variant="outline" className="text-[10px]">
                      coming soon
                    </Badge>
                  </div>
                )}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

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
                <option value="NRML">NRML</option>
                <option value="MIS">MIS</option>
                <option value="CNC">CNC</option>
              </select>
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
          {legs.map((leg, i) => (
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
          ))}
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
            Optional cron-based start. Mon–Fri default. Phase 8 will wire
            APScheduler — for now this is config-only.
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
        <Button variant="outline" onClick={() => navigate("/strategy")}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={createMutation.isPending}>
          {createMutation.isPending ? "Saving…" : "Save and Continue"}
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
