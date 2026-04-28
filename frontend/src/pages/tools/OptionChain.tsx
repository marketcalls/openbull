import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { fetchExpiries, fetchUnderlyings } from "@/api/optionchain";
import { useOptionChainLive } from "@/hooks/useOptionChainLive";
import {
  COLUMNS,
  FALLBACK_UNDERLYINGS,
  FNO_EXCHANGES,
  STRIKE_COUNTS,
  VISIBLE_COLUMNS_STORAGE_KEY,
  type ColumnKey,
  type FnoExchange,
  type OptionLeg,
  type OptionStrike,
  type UnderlyingOption,
} from "@/types/optionchain";
import { PlaceOrderDialog } from "@/components/trading/PlaceOrderDialog";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import { cn } from "@/lib/utils";

function formatInLakhs(num: number | null | undefined): string {
  if (!num) return "0";
  const lakhs = num / 100000;
  if (lakhs >= 100) return lakhs.toFixed(0) + "L";
  if (lakhs >= 10) return lakhs.toFixed(1) + "L";
  if (lakhs >= 1) return lakhs.toFixed(2) + "L";
  const k = num / 1000;
  if (k >= 1) return k.toFixed(1) + "K";
  return num.toLocaleString();
}

function formatPrice(num: number | null | undefined): string {
  if (num === null || num === undefined) return "0.00";
  return num.toFixed(2);
}

function formatInt(num: number | null | undefined): string {
  if (num === null || num === undefined || num === 0) return "0";
  return Math.round(num).toLocaleString();
}

function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

function loadVisibleColumns(): ColumnKey[] {
  try {
    const raw = localStorage.getItem(VISIBLE_COLUMNS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as string[];
      if (Array.isArray(parsed)) {
        return parsed.filter((k) => COLUMNS.some((c) => c.key === k)) as ColumnKey[];
      }
    }
  } catch {
    /* ignore */
  }
  return COLUMNS.filter((c) => c.defaultVisible).map((c) => c.key);
}

function saveVisibleColumns(keys: ColumnKey[]) {
  try {
    localStorage.setItem(VISIBLE_COLUMNS_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    /* ignore */
  }
}

interface OrderTarget {
  symbol: string;
  exchange: string;
  action: "BUY" | "SELL";
  ltp: number;
  lotSize: number;
  tickSize: number;
}

interface CellRendererProps {
  leg: OptionLeg | null;
  flash: "" | "up" | "down";
}

function getColumnValue(leg: OptionLeg | null, key: ColumnKey): React.ReactNode {
  if (!leg) return "—";
  switch (key) {
    case "oi":
      return formatInLakhs(leg.oi);
    case "volume":
      return formatInLakhs(leg.volume);
    case "bid_qty":
      return formatInt(leg.bid_qty);
    case "bid":
      return formatPrice(leg.bid);
    case "ltp":
      return formatPrice(leg.ltp);
    case "ask":
      return formatPrice(leg.ask);
    case "ask_qty":
      return formatInt(leg.ask_qty);
    case "spread":
      if (!leg.bid || !leg.ask) return "—";
      return formatPrice(leg.ask - leg.bid);
  }
}

function colorForKey(key: ColumnKey): string {
  if (key === "bid") return "text-red-500";
  if (key === "ask") return "text-green-500";
  if (key === "ltp") return "font-semibold";
  return "text-muted-foreground";
}

function CellValue({ leg, columnKey, flash }: { leg: OptionLeg | null; columnKey: ColumnKey } & Pick<CellRendererProps, "flash">) {
  return (
    <span
      className={cn(
        "font-mono tabular-nums text-xs transition-colors",
        colorForKey(columnKey),
        columnKey === "ltp" && flash === "up" && "bg-green-500/25",
        columnKey === "ltp" && flash === "down" && "bg-red-500/25"
      )}
    >
      {getColumnValue(leg, columnKey)}
    </span>
  );
}

interface OptionChainRowProps {
  strike: OptionStrike;
  prev: OptionStrike | undefined;
  maxOi: number;
  optionExchange: string;
  visibleColumns: ColumnKey[];
  onPlaceOrder: (target: OrderTarget) => void;
}

function OptionChainRow({
  strike,
  prev,
  maxOi,
  optionExchange,
  visibleColumns,
  onPlaceOrder,
}: OptionChainRowProps) {
  const ce = strike.ce;
  const pe = strike.pe;
  const label = ce?.label ?? pe?.label ?? "";
  const isATM = label === "ATM";
  const isCeOTM = label.startsWith("OTM");
  const isPeOTM = label.startsWith("ITM");

  const ceFlash: "" | "up" | "down" =
    prev?.ce?.ltp !== undefined && ce && prev.ce.ltp !== ce.ltp
      ? ce.ltp > prev.ce.ltp
        ? "up"
        : "down"
      : "";
  const peFlash: "" | "up" | "down" =
    prev?.pe?.ltp !== undefined && pe && prev.pe.ltp !== pe.ltp
      ? pe.ltp > prev.pe.ltp
        ? "up"
        : "down"
      : "";

  const ceBarPct = ce?.oi ? Math.min((ce.oi / maxOi) * 100, 100) : 0;
  const peBarPct = pe?.oi ? Math.min((pe.oi / maxOi) * 100, 100) : 0;

  const colCount = visibleColumns.length;

  return (
    <TableRow className="group relative">
      <TableCell className={cn("relative p-0", isCeOTM && !isATM && "bg-amber-500/5")}>
        <div
          className="absolute inset-y-0 left-0 z-0 bg-gradient-to-r from-green-500/25 to-transparent transition-all duration-300 pointer-events-none"
          style={{ width: `${ceBarPct}%` }}
        />
        {ce && (
          <div className="absolute right-1 top-1/2 -translate-y-1/2 z-20 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPlaceOrder({
                  symbol: ce.symbol,
                  exchange: optionExchange,
                  action: "BUY",
                  ltp: ce.ltp,
                  lotSize: ce.lotsize ?? 1,
                  tickSize: ce.tick_size ?? 0.05,
                });
              }}
              className="rounded bg-green-600 px-1.5 py-0.5 text-[10px] font-bold text-white hover:bg-green-700"
            >
              B
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPlaceOrder({
                  symbol: ce.symbol,
                  exchange: optionExchange,
                  action: "SELL",
                  ltp: ce.ltp,
                  lotSize: ce.lotsize ?? 1,
                  tickSize: ce.tick_size ?? 0.05,
                });
              }}
              className="rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white hover:bg-red-700"
            >
              S
            </button>
          </div>
        )}
        <div
          className="relative z-10 grid gap-2 px-3 py-1.5"
          style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
        >
          {visibleColumns.map((key) => (
            <span key={key} className="text-right">
              <CellValue leg={ce} columnKey={key} flash={ceFlash} />
            </span>
          ))}
        </div>
      </TableCell>

      <TableCell
        className={cn(
          "w-20 min-w-20 text-center text-sm font-bold",
          isATM ? "bg-primary/15" : "bg-muted/30"
        )}
      >
        {strike.strike}
      </TableCell>

      <TableCell className={cn("relative p-0", isPeOTM && !isATM && "bg-amber-500/5")}>
        <div
          className="absolute inset-y-0 right-0 z-0 bg-gradient-to-l from-red-500/25 to-transparent transition-all duration-300 pointer-events-none"
          style={{ width: `${peBarPct}%` }}
        />
        {pe && (
          <div className="absolute left-1 top-1/2 -translate-y-1/2 z-20 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPlaceOrder({
                  symbol: pe.symbol,
                  exchange: optionExchange,
                  action: "BUY",
                  ltp: pe.ltp,
                  lotSize: pe.lotsize ?? 1,
                  tickSize: pe.tick_size ?? 0.05,
                });
              }}
              className="rounded bg-green-600 px-1.5 py-0.5 text-[10px] font-bold text-white hover:bg-green-700"
            >
              B
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPlaceOrder({
                  symbol: pe.symbol,
                  exchange: optionExchange,
                  action: "SELL",
                  ltp: pe.ltp,
                  lotSize: pe.lotsize ?? 1,
                  tickSize: pe.tick_size ?? 0.05,
                });
              }}
              className="rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white hover:bg-red-700"
            >
              S
            </button>
          </div>
        )}
        <div
          className="relative z-10 grid gap-2 px-3 py-1.5"
          style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
        >
          {visibleColumns.map((key) => (
            <span key={key} className="text-left">
              <CellValue leg={pe} columnKey={key} flash={peFlash} />
            </span>
          ))}
        </div>
      </TableCell>
    </TableRow>
  );
}

function calcPCR(chain: OptionStrike[]): number {
  let ceOi = 0;
  let peOi = 0;
  for (const s of chain) {
    if (s.ce?.oi) ceOi += s.ce.oi;
    if (s.pe?.oi) peOi += s.pe.oi;
  }
  if (ceOi === 0) return 0;
  return peOi / ceOi;
}

function calcTotals(chain: OptionStrike[]) {
  let ceOi = 0;
  let peOi = 0;
  for (const s of chain) {
    if (s.ce) ceOi += s.ce.oi ?? 0;
    if (s.pe) peOi += s.pe.oi ?? 0;
  }
  return { ceOi, peOi };
}

function calcMaxOi(chain: OptionStrike[]): number {
  let max = 0;
  for (const s of chain) {
    if (s.ce?.oi && s.ce.oi > max) max = s.ce.oi;
    if (s.pe?.oi && s.pe.oi > max) max = s.pe.oi;
  }
  return max || 1;
}

export default function OptionChain() {
  const [exchange, setExchange] = useState<FnoExchange>("NFO");
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [strikeCount, setStrikeCount] = useState<number>(10);
  const [expiry, setExpiry] = useState<string>("");
  const [orderTarget, setOrderTarget] = useState<OrderTarget | null>(null);
  const [visibleColumns, setVisibleColumns] = useState<ColumnKey[]>(() => loadVisibleColumns());

  const prevChainRef = useRef<Map<number, OptionStrike>>(new Map());

  // Underlyings list — DB-backed (option-ticker prefix + name), falls back to
  // a small hardcoded list while the API loads.
  const underlyingsQuery = useQuery({
    queryKey: ["option-underlyings", exchange],
    queryFn: () => fetchUnderlyings(exchange),
    retry: 0,
    staleTime: 5 * 60_000,
  });
  const underlyings = useMemo<UnderlyingOption[]>(() => {
    if (underlyingsQuery.data?.status === "success" && underlyingsQuery.data.data.length > 0) {
      return underlyingsQuery.data.data;
    }
    return FALLBACK_UNDERLYINGS[exchange];
  }, [underlyingsQuery.data, exchange]);

  // When the available underlyings change, snap to the first one if the
  // current selection is no longer in the list (e.g. after switching exchange).
  useEffect(() => {
    if (underlyings.length === 0) return;
    if (!underlyings.some((u) => u.symbol === underlying)) {
      setUnderlying(underlyings[0].symbol);
      setExpiry("");
    }
  }, [underlyings, underlying]);

  // Expiries
  const expiriesQuery = useQuery({
    queryKey: ["expiries", underlying, exchange],
    queryFn: () => fetchExpiries({ symbol: underlying, exchange, instrumenttype: "options" }),
    enabled: !!underlying && !!exchange,
    retry: 0,
  });
  useEffect(() => {
    if (expiriesQuery.data?.status === "success" && expiriesQuery.data.data.length > 0) {
      setExpiry((prev) =>
        prev && expiriesQuery.data!.data.includes(prev) ? prev : expiriesQuery.data!.data[0]
      );
    } else if (expiriesQuery.data?.status === "error") {
      toast.error(expiriesQuery.data.message ?? "Failed to load expiries");
    }
  }, [expiriesQuery.data]);

  // Live chain (REST + WS)
  const {
    data,
    isLoading,
    isStreaming,
    isPaused,
    wsState,
    wsError,
    streamingSymbols,
    error: chainError,
    lastUpdate,
    refetch,
  } = useOptionChainLive({
    underlying,
    exchange,
    expiryDate: convertExpiryForApi(expiry),
    strikeCount,
    options: {
      oiRefreshInterval: 30000,
      enabled: !!underlying && !!expiry,
      pauseWhenHidden: true,
    },
  });

  useEffect(() => {
    if (!data?.chain) return;
    const t = setTimeout(() => {
      const m = new Map<number, OptionStrike>();
      data.chain.forEach((s) => m.set(s.strike, s));
      prevChainRef.current = m;
    }, 200);
    return () => clearTimeout(t);
  }, [data?.chain]);

  const pcr = useMemo(() => (data?.chain ? calcPCR(data.chain) : 0), [data?.chain]);
  const totals = useMemo(
    () => (data?.chain ? calcTotals(data.chain) : { ceOi: 0, peOi: 0 }),
    [data?.chain]
  );
  const maxOi = useMemo(() => (data?.chain ? calcMaxOi(data.chain) : 1), [data?.chain]);

  const spotChange =
    data && data.underlying_prev_close > 0 ? data.underlying_ltp - data.underlying_prev_close : 0;
  const spotChangePct =
    data && data.underlying_prev_close > 0 ? (spotChange / data.underlying_prev_close) * 100 : 0;

  const toggleColumn = useCallback((key: ColumnKey) => {
    setVisibleColumns((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key];
      saveVisibleColumns(next);
      return next;
    });
  }, []);
  const resetColumns = useCallback(() => {
    const def = COLUMNS.filter((c) => c.defaultVisible).map((c) => c.key);
    setVisibleColumns(def);
    saveVisibleColumns(def);
  }, []);

  const orderedVisibleColumns = useMemo(
    () => COLUMNS.filter((c) => visibleColumns.includes(c.key)).map((c) => c.key),
    [visibleColumns]
  );

  const handlePlaceOrder = useCallback((t: OrderTarget) => setOrderTarget(t), []);
  const handleDialogChange = useCallback((open: boolean) => {
    if (!open) setOrderTarget(null);
  }, []);

  const colCount = orderedVisibleColumns.length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Option Chain</h1>
        <p className="text-sm text-muted-foreground">
          Strikes around ATM with live LTP, Bid/Ask, OI and Volume — REST every 30s for OI/Volume,
          WebSocket Depth stream for everything else. Hover a row and click B/S to place an order.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value as FnoExchange)}
              className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {FNO_EXCHANGES.map((e) => (
                <option key={e.value} value={e.value}>
                  {e.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">
              Underlying {underlyingsQuery.isLoading ? "(loading…)" : `(${underlyings.length})`}
            </label>
            <UnderlyingCombobox
              value={underlying}
              options={underlyings}
              onChange={(sym) => {
                setUnderlying(sym);
                setExpiry("");
              }}
              loading={underlyingsQuery.isLoading}
              className="w-56"
            />
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Expiry</label>
            <select
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              disabled={
                expiriesQuery.isLoading ||
                !expiriesQuery.data ||
                expiriesQuery.data.status !== "success"
              }
              className="h-8 w-40 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            >
              {expiriesQuery.data?.status === "success" && expiriesQuery.data.data.length > 0 ? (
                expiriesQuery.data.data.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))
              ) : (
                <option value="">{expiriesQuery.isLoading ? "Loading…" : "No expiries"}</option>
              )}
            </select>
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Strikes</label>
            <select
              value={String(strikeCount)}
              onChange={(e) => setStrikeCount(Number(e.target.value))}
              className="h-8 w-32 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {STRIKE_COUNTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="outline">Columns ({orderedVisibleColumns.length})</Button>}
            />
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuLabel>Show columns</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {COLUMNS.map((c) => (
                <DropdownMenuCheckboxItem
                  key={c.key}
                  checked={visibleColumns.includes(c.key)}
                  onCheckedChange={() => toggleColumn(c.key)}
                >
                  {c.label}
                </DropdownMenuCheckboxItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={resetColumns}>Reset to defaults</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button variant="outline" onClick={() => refetch()} disabled={!expiry || isLoading}>
            Refresh
          </Button>

          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Badge
              variant={
                isPaused
                  ? "outline"
                  : isStreaming
                    ? "default"
                    : wsState === "error"
                      ? "destructive"
                      : "secondary"
              }
            >
              {isPaused
                ? "Paused (tab hidden)"
                : isStreaming
                  ? `Streaming ${streamingSymbols} symbols`
                  : wsState === "authenticating"
                    ? "Authenticating…"
                    : wsState === "connecting"
                      ? "Connecting…"
                      : wsState === "error"
                        ? "WS error"
                        : "Polling"}
            </Badge>
            {lastUpdate && <span>Updated {lastUpdate.toLocaleTimeString()}</span>}
          </div>
        </CardContent>
        {wsError && (
          <CardContent className="pt-0">
            <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              WebSocket: {wsError}. Falling back to REST polling.
            </p>
          </CardContent>
        )}
      </Card>

      {data && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">
                {data.quote_symbol && data.quote_symbol !== data.underlying
                  ? `${data.underlying} Futures (${data.quote_symbol})`
                  : `${data.underlying} Spot`}
              </div>
              <div className="text-2xl font-bold text-primary">
                {formatPrice(data.underlying_ltp)}
              </div>
              <div
                className={cn(
                  "text-xs",
                  spotChange > 0
                    ? "text-green-600 dark:text-green-400"
                    : spotChange < 0
                      ? "text-red-600 dark:text-red-400"
                      : "text-muted-foreground"
                )}
              >
                {spotChange >= 0 ? "+" : ""}
                {formatPrice(spotChange)} ({spotChangePct.toFixed(2)}%) · Prev{" "}
                {formatPrice(data.underlying_prev_close)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">ATM Strike</div>
              <div className="text-2xl font-bold">{data.atm_strike}</div>
              <div className="text-xs text-muted-foreground">Expiry: {data.expiry_date}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">PCR</div>
              <div
                className={cn(
                  "text-2xl font-bold",
                  pcr > 1
                    ? "text-green-600 dark:text-green-400"
                    : "text-yellow-600 dark:text-yellow-400"
                )}
              >
                {pcr.toFixed(2)}
              </div>
              <div className="text-xs text-muted-foreground">Put / Call OI ratio</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">Total OI (CE | PE)</div>
              <div className="mt-1 text-sm">
                <span className="font-mono tabular-nums text-green-600 dark:text-green-400">
                  {formatInLakhs(totals.ceOi)}
                </span>
                <span className="mx-2 text-muted-foreground">|</span>
                <span className="font-mono tabular-nums text-red-600 dark:text-red-400">
                  {formatInLakhs(totals.peOi)}
                </span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-gradient-to-r from-green-500 to-primary transition-all duration-500"
                  style={{
                    width:
                      totals.ceOi + totals.peOi > 0
                        ? `${(totals.ceOi / (totals.ceOi + totals.peOi)) * 100}%`
                        : "0%",
                  }}
                />
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {chainError ? (
            <div className="p-6 text-center text-sm text-destructive">{chainError}</div>
          ) : !data && isLoading ? (
            <div className="flex items-center justify-center py-16">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
            </div>
          ) : !data ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Pick an exchange, underlying and expiry to load the chain.
            </p>
          ) : colCount === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No columns selected. Toggle some on via the Columns dropdown.
            </p>
          ) : (
            <Table className="w-full table-fixed">
              <TableHeader>
                <TableRow className="bg-muted/30">
                  <TableHead className="border-r border-border text-center text-sm font-bold text-green-600 dark:text-green-400">
                    CALLS
                  </TableHead>
                  <TableHead className="w-20 min-w-20" />
                  <TableHead className="border-l border-border text-center text-sm font-bold text-red-600 dark:text-red-400">
                    PUTS
                  </TableHead>
                </TableRow>
                <TableRow className="bg-muted/50">
                  <TableHead className="border-r border-border p-0">
                    <div
                      className="grid gap-2 px-3 py-2 text-xs font-medium"
                      style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
                    >
                      {orderedVisibleColumns.map((key) => (
                        <span key={key} className="text-right">
                          {COLUMNS.find((c) => c.key === key)?.label}
                        </span>
                      ))}
                    </div>
                  </TableHead>
                  <TableHead className="w-20 min-w-20 bg-muted/30 text-center text-xs">
                    Strike
                  </TableHead>
                  <TableHead className="border-l border-border p-0">
                    <div
                      className="grid gap-2 px-3 py-2 text-xs font-medium"
                      style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
                    >
                      {orderedVisibleColumns.map((key) => (
                        <span key={key} className="text-left">
                          {COLUMNS.find((c) => c.key === key)?.label}
                        </span>
                      ))}
                    </div>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.chain.map((s) => (
                  <OptionChainRow
                    key={s.strike}
                    strike={s}
                    prev={prevChainRef.current.get(s.strike)}
                    maxOi={maxOi}
                    optionExchange={exchange}
                    visibleColumns={orderedVisibleColumns}
                    onPlaceOrder={handlePlaceOrder}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {orderTarget && (
        <PlaceOrderDialog
          open={!!orderTarget}
          onOpenChange={handleDialogChange}
          symbol={orderTarget.symbol}
          exchange={orderTarget.exchange}
          action={orderTarget.action}
          ltp={orderTarget.ltp}
          lotSize={orderTarget.lotSize}
          tickSize={orderTarget.tickSize}
          strategy="OptionChain"
          onSuccess={() => refetch()}
        />
      )}
    </div>
  );
}
