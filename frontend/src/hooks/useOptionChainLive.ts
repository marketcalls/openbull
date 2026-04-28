import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchOptionChain } from "@/api/optionchain";
import {
  type FnoExchange,
  type OptionChainResponse,
  type OptionStrike,
  getUnderlyingExchange,
} from "@/types/optionchain";
import { useMarketData } from "./useMarketData";
import { usePageVisibility } from "./usePageVisibility";

interface UseOptionChainLiveOptions {
  /** Polling interval for OI/Volume refresh, in ms. Default 30s. */
  oiRefreshInterval?: number;
  enabled?: boolean;
  /**
   * When true (default), the REST poll and the WebSocket subscription are
   * paused while the browser tab is hidden. They resume — with an immediate
   * refetch — as soon as the tab regains visibility.
   */
  pauseWhenHidden?: boolean;
}

function roundToTick(price: number | undefined, tickSize: number | undefined): number | undefined {
  if (price === undefined || price === null) return undefined;
  if (!tickSize || tickSize <= 0) return price;
  return Number((Math.round(price / tickSize) * tickSize).toFixed(2));
}

/**
 * Hybrid REST + WebSocket data source for the option chain — mirrors the
 * OpenAlgo `useOptionChainLive` pattern.
 *
 *  - REST `/api/v1/optionchain` is polled at `oiRefreshInterval` (slow) for
 *    the chain skeleton, OI and Volume.
 *  - As soon as a chain lands, WS subscriptions are opened in Depth mode for
 *    every CE/PE symbol plus the underlying spot.
 *  - Each tick merges into the in-memory chain so LTP/Bid/Ask/Bid_qty/Ask_qty
 *    update in real time without waiting for the next REST refresh.
 */
export function useOptionChainLive(params: {
  underlying: string;
  exchange: FnoExchange;
  expiryDate: string;
  strikeCount: number;
  options?: UseOptionChainLiveOptions;
}) {
  const { underlying, exchange, expiryDate, strikeCount } = params;
  const enabled = params.options?.enabled ?? true;
  const oiRefreshInterval = params.options?.oiRefreshInterval ?? 30000;
  const pauseWhenHidden = params.options?.pauseWhenHidden ?? true;

  const { isVisible, wasHidden } = usePageVisibility();
  const isPaused = pauseWhenHidden && !isVisible;
  const liveEnabled = enabled && !isPaused;

  const chainQuery = useQuery({
    queryKey: ["optionchain", underlying, exchange, expiryDate, strikeCount],
    queryFn: () =>
      fetchOptionChain({
        underlying,
        exchange,
        expiry_date: expiryDate,
        strike_count: strikeCount,
      }),
    enabled: liveEnabled && !!underlying && !!exchange && !!expiryDate,
    refetchInterval: oiRefreshInterval,
    refetchIntervalInBackground: false,
    retry: 0,
  });

  // When the tab returns from hidden, kick an immediate refetch so the user
  // never sees a stale chain on focus.
  useEffect(() => {
    if (wasHidden && liveEnabled) {
      chainQuery.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wasHidden, liveEnabled]);

  const polled = chainQuery.data?.status === "success" ? chainQuery.data : null;

  // Build the symbol set to stream — underlying (spot or FUT) + every CE/PE leg.
  // Prefer the response's resolved quote_symbol/quote_exchange (set by the
  // backend for MCX/CDS where the underlying is auto-mapped to the near-month
  // FUT) — fall back to the existing index/equity heuristic for older
  // responses that don't carry those fields.
  const wsSymbols = useMemo<Array<{ symbol: string; exchange: string }>>(() => {
    if (!polled) return [];
    const out: Array<{ symbol: string; exchange: string }> = [];
    const underlyingSym = polled.quote_symbol ?? underlying;
    const underlyingExch =
      polled.quote_exchange ?? getUnderlyingExchange(underlying, exchange);
    out.push({ symbol: underlyingSym, exchange: underlyingExch });
    for (const strike of polled.chain) {
      if (strike.ce?.symbol) out.push({ symbol: strike.ce.symbol, exchange });
      if (strike.pe?.symbol) out.push({ symbol: strike.pe.symbol, exchange });
    }
    return out;
  }, [polled, exchange, underlying]);

  const { data: wsData, isAuthenticated, state, error: wsError } = useMarketData({
    symbols: wsSymbols,
    mode: "Depth",
    enabled: liveEnabled && wsSymbols.length > 0,
  });

  const [merged, setMerged] = useState<OptionChainResponse | null>(null);
  const lastTickRef = useRef<number>(0);
  const [lastTickAt, setLastTickAt] = useState<Date | null>(null);

  // Merge live ticks into the polled chain whenever either side changes.
  useEffect(() => {
    if (!polled) {
      setMerged(null);
      return;
    }
    if (wsData.size === 0) {
      setMerged(polled);
      return;
    }

    const mergedChain: OptionStrike[] = polled.chain.map((strike) => {
      const next: OptionStrike = { ...strike };

      const mergeLeg = (legKey: "ce" | "pe") => {
        const leg = strike[legKey];
        if (!leg?.symbol) return;
        const ws = wsData.get(`${exchange}:${leg.symbol}`);
        if (!ws?.data) return;
        const buy = ws.data.depth?.buy?.[0];
        const sell = ws.data.depth?.sell?.[0];
        const ts = leg.tick_size;
        next[legKey] = {
          ...leg,
          ltp: roundToTick(ws.data.ltp, ts) ?? leg.ltp,
          bid: roundToTick(buy?.price ?? ws.data.bid_price, ts) ?? leg.bid,
          ask: roundToTick(sell?.price ?? ws.data.ask_price, ts) ?? leg.ask,
          bid_qty: buy?.quantity ?? ws.data.bid_size ?? leg.bid_qty,
          ask_qty: sell?.quantity ?? ws.data.ask_size ?? leg.ask_qty,
          volume: ws.data.volume ?? leg.volume,
          oi: ws.data.oi ?? leg.oi,
        };
      };
      mergeLeg("ce");
      mergeLeg("pe");
      return next;
    });

    // Bump the streaming timestamp if any tick arrived since last render.
    let newest = lastTickRef.current;
    for (const [, sd] of wsData) {
      if (sd.lastUpdate > newest) newest = sd.lastUpdate;
    }
    if (newest > lastTickRef.current) {
      lastTickRef.current = newest;
      setLastTickAt(new Date(newest));
    }

    // Live underlying ticks from WS (if present). Mirrors the wsSymbols
    // resolution above — uses the resolved quote_symbol/quote_exchange so
    // MCX/CDS chains get FUT ticks instead of looking up a non-existent spot.
    const underlyingSym = polled.quote_symbol ?? underlying;
    const underlyingExch =
      polled.quote_exchange ?? getUnderlyingExchange(underlying, exchange);
    const u = wsData.get(`${underlyingExch}:${underlyingSym}`);
    const underlyingLtp = u?.data?.ltp ?? polled.underlying_ltp;

    setMerged({
      ...polled,
      underlying_ltp: underlyingLtp,
      chain: mergedChain,
    });
  }, [polled, wsData, exchange, underlying]);

  return {
    data: merged,
    isLoading: chainQuery.isLoading,
    isStreaming: isAuthenticated && wsSymbols.length > 0 && !isPaused,
    isPaused,
    wsState: state,
    wsError,
    streamingSymbols: wsSymbols.length,
    error:
      chainQuery.data?.status === "error"
        ? chainQuery.data.message ?? "Failed to load chain"
        : (chainQuery.error as Error | null)?.message ?? null,
    lastUpdate: lastTickAt ?? (chainQuery.dataUpdatedAt ? new Date(chainQuery.dataUpdatedAt) : null),
    refetch: chainQuery.refetch,
  };
}
