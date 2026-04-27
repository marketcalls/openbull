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
import { fetchOptionChain, fetchExpiries } from "@/api/optionchain";
import {
  DEFAULT_UNDERLYINGS,
  FNO_EXCHANGES,
  STRIKE_COUNTS,
  type OptionStrike,
} from "@/types/optionchain";
import { PlaceOrderDialog } from "@/components/trading/PlaceOrderDialog";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;

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

function convertExpiryForApi(expiry: string): string {
  // Backend stores expiry as "DD-MMM-YY"; option chain expects "DDMMMYY"
  if (!expiry) return "";
  return expiry.replace(/-/g, "").toUpperCase();
}

interface OrderTarget {
  symbol: string;
  exchange: string;
  action: "BUY" | "SELL";
  ltp: number;
  lotSize: number;
  tickSize: number;
}

interface OptionChainRowProps {
  strike: OptionStrike;
  prev: OptionStrike | undefined;
  maxOi: number;
  optionExchange: string;
  onPlaceOrder: (target: OrderTarget) => void;
}

function OptionChainRow({ strike, prev, maxOi, optionExchange, onPlaceOrder }: OptionChainRowProps) {
  const ce = strike.ce;
  const pe = strike.pe;
  const label = ce?.label ?? pe?.label ?? "";
  const isATM = label === "ATM";
  const isCeOTM = label.startsWith("OTM");
  const isPeOTM = label.startsWith("ITM");

  const cePrev = prev?.ce?.ltp;
  const pePrev = prev?.pe?.ltp;
  const ceFlash =
    cePrev !== undefined && ce && cePrev !== ce.ltp
      ? ce.ltp > cePrev
        ? "bg-green-500/25"
        : "bg-red-500/25"
      : "";
  const peFlash =
    pePrev !== undefined && pe && pePrev !== pe.ltp
      ? pe.ltp > pePrev
        ? "bg-green-500/25"
        : "bg-red-500/25"
      : "";

  const ceBarPct = ce?.oi ? Math.min((ce.oi / maxOi) * 100, 100) : 0;
  const peBarPct = pe?.oi ? Math.min((pe.oi / maxOi) * 100, 100) : 0;

  const num = "font-mono tabular-nums text-xs";

  return (
    <TableRow className="group relative">
      {/* CE side */}
      <TableCell
        className={cn(
          "relative p-0",
          isCeOTM && !isATM && "bg-amber-500/5"
        )}
      >
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
        <div className="relative z-10 grid grid-cols-3 gap-2 px-3 py-1.5">
          <span className={cn(num, "text-right text-muted-foreground")}>
            {formatInLakhs(ce?.oi)}
          </span>
          <span className={cn(num, "text-right text-muted-foreground")}>
            {formatInLakhs(ce?.volume)}
          </span>
          <span
            className={cn(
              num,
              "text-right font-semibold transition-colors",
              ceFlash
            )}
          >
            {ce ? formatPrice(ce.ltp) : "—"}
          </span>
        </div>
      </TableCell>

      {/* Strike */}
      <TableCell
        className={cn(
          "w-20 min-w-20 text-center text-sm font-bold",
          isATM ? "bg-primary/15" : "bg-muted/30"
        )}
      >
        {strike.strike}
      </TableCell>

      {/* PE side */}
      <TableCell
        className={cn(
          "relative p-0",
          isPeOTM && !isATM && "bg-amber-500/5"
        )}
      >
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
        <div className="relative z-10 grid grid-cols-3 gap-2 px-3 py-1.5">
          <span
            className={cn(
              num,
              "text-left font-semibold transition-colors",
              peFlash
            )}
          >
            {pe ? formatPrice(pe.ltp) : "—"}
          </span>
          <span className={cn(num, "text-left text-muted-foreground")}>
            {formatInLakhs(pe?.volume)}
          </span>
          <span className={cn(num, "text-left text-muted-foreground")}>
            {formatInLakhs(pe?.oi)}
          </span>
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
  let ceVol = 0;
  let peVol = 0;
  for (const s of chain) {
    if (s.ce) {
      ceOi += s.ce.oi ?? 0;
      ceVol += s.ce.volume ?? 0;
    }
    if (s.pe) {
      peOi += s.pe.oi ?? 0;
      peVol += s.pe.volume ?? 0;
    }
  }
  return { ceOi, peOi, ceVol, peVol };
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
  const [exchange, setExchange] = useState<string>("NFO");
  const defaultsForExchange = DEFAULT_UNDERLYINGS[exchange] ?? [];
  const [underlying, setUnderlying] = useState<string>(defaultsForExchange[0] ?? "NIFTY");
  const [strikeCount, setStrikeCount] = useState<number>(10);
  const [expiry, setExpiry] = useState<string>("");
  const [orderTarget, setOrderTarget] = useState<OrderTarget | null>(null);

  const prevChainRef = useRef<Map<number, OptionStrike>>(new Map());

  // Reset underlying & expiry when exchange changes
  useEffect(() => {
    const list = DEFAULT_UNDERLYINGS[exchange] ?? [];
    setUnderlying(list[0] ?? "");
    setExpiry("");
  }, [exchange]);

  // Fetch expiries when underlying changes
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

  // Fetch chain (polling)
  const chainQuery = useQuery({
    queryKey: ["optionchain", underlying, exchange, expiry, strikeCount],
    queryFn: () =>
      fetchOptionChain({
        underlying,
        exchange,
        expiry_date: convertExpiryForApi(expiry),
        strike_count: strikeCount,
      }),
    enabled: !!underlying && !!exchange && !!expiry,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    retry: 0,
  });

  const data = chainQuery.data?.status === "success" ? chainQuery.data : null;
  const errorMsg =
    chainQuery.error
      ? (chainQuery.error as Error).message
      : chainQuery.data?.status === "error"
        ? chainQuery.data.message
        : null;

  // After each successful render, snapshot the chain so the next render can flash diffs.
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
    () => (data?.chain ? calcTotals(data.chain) : { ceOi: 0, peOi: 0, ceVol: 0, peVol: 0 }),
    [data?.chain]
  );
  const maxOi = useMemo(() => (data?.chain ? calcMaxOi(data.chain) : 1), [data?.chain]);

  const spotChange =
    data && data.underlying_prev_close > 0
      ? data.underlying_ltp - data.underlying_prev_close
      : 0;
  const spotChangePct =
    data && data.underlying_prev_close > 0
      ? (spotChange / data.underlying_prev_close) * 100
      : 0;

  const handlePlaceOrder = useCallback((t: OrderTarget) => {
    setOrderTarget(t);
  }, []);
  const handleDialogChange = useCallback((open: boolean) => {
    if (!open) setOrderTarget(null);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Option Chain</h1>
        <p className="text-sm text-muted-foreground">
          Strikes around ATM with live LTP, OI and Volume — hover a row and click B/S to place an order.
        </p>
      </div>

      {/* Selectors */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Exchange</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className="h-8 w-28 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {FNO_EXCHANGES.map((e) => (
                <option key={e.value} value={e.value}>
                  {e.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="block text-xs text-muted-foreground">Underlying</label>
            <select
              value={underlying}
              onChange={(e) => setUnderlying(e.target.value)}
              className="h-8 w-40 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {defaultsForExchange.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
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
          <Button
            variant="outline"
            onClick={() => chainQuery.refetch()}
            disabled={!expiry || chainQuery.isFetching}
          >
            {chainQuery.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant={chainQuery.isFetching ? "default" : "secondary"}>
              {chainQuery.isFetching ? "Polling" : `Auto-refresh ${POLL_MS / 1000}s`}
            </Badge>
            {chainQuery.dataUpdatedAt > 0 && (
              <span>Updated {new Date(chainQuery.dataUpdatedAt).toLocaleTimeString()}</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Summary cards */}
      {data && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">{data.underlying} Spot</div>
              <div className="text-2xl font-bold text-primary">{formatPrice(data.underlying_ltp)}</div>
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
                  pcr > 1 ? "text-green-600 dark:text-green-400" : "text-yellow-600 dark:text-yellow-400"
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

      {/* Chain table */}
      <Card>
        <CardContent className="p-0">
          {errorMsg ? (
            <div className="p-6 text-center text-sm text-destructive">{errorMsg}</div>
          ) : !data && chainQuery.isLoading ? (
            <div className="flex items-center justify-center py-16">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
            </div>
          ) : !data ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Pick an exchange, underlying and expiry to load the chain.
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
                    <div className="grid grid-cols-3 gap-2 px-3 py-2 text-xs font-medium">
                      <span className="text-right">OI</span>
                      <span className="text-right">Volume</span>
                      <span className="text-right">LTP</span>
                    </div>
                  </TableHead>
                  <TableHead className="w-20 min-w-20 bg-muted/30 text-center text-xs">
                    Strike
                  </TableHead>
                  <TableHead className="border-l border-border p-0">
                    <div className="grid grid-cols-3 gap-2 px-3 py-2 text-xs font-medium">
                      <span className="text-left">LTP</span>
                      <span className="text-left">Volume</span>
                      <span className="text-left">OI</span>
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
          onSuccess={() => chainQuery.refetch()}
        />
      )}
    </div>
  );
}
