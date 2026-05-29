import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMultiQuotes, type MultiQuoteRow } from "@/api/quotes";
import { useMarketData } from "@/hooks/useMarketData";
import { useMarketStatus } from "@/hooks/useMarketStatus";
import { usePageVisibility } from "@/hooks/usePageVisibility";

/**
 * Base shape for any row that can carry a live price (positions, holdings).
 */
export interface PriceableItem {
  symbol: string;
  exchange: string;
  ltp?: number;
  pnl?: number;
  pnlpercent?: number;
  quantity?: number;
  average_price?: number;
  /** Realized P&L already booked today (e.g. partial closes); added to unrealized. */
  today_realized_pnl?: number;
  /** Contract multiplier (default 1). Some instruments price per-lot. */
  lot_size?: number;
}

export interface UseLivePriceOptions {
  /** Whether the hook is enabled (default: true). */
  enabled?: boolean;
  /** ms after which a WebSocket tick is considered stale (default: 5000). */
  staleThreshold?: number;
  /** Pause the WebSocket + polling when the tab is hidden (default: true). */
  pauseWhenHidden?: boolean;
  /** Use the MultiQuotes REST API as a fallback when WS is down (default: true). */
  useMultiQuotesFallback?: boolean;
  /** ms between MultiQuotes fallback polls (default: 30000). */
  multiQuotesRefreshInterval?: number;
}

export interface UseLivePriceResult<T extends PriceableItem> {
  /** Rows enhanced with live LTP and recomputed P&L. */
  data: T[];
  /** WebSocket authenticated and not paused. */
  isLive: boolean;
  /** WebSocket connected (any state past connecting). */
  isConnected: boolean;
  /** WebSocket paused because the tab is hidden. */
  isPaused: boolean;
  /** Serving REST (MultiQuotes) prices because the WebSocket isn't live. */
  isFallbackMode: boolean;
  /** Approximate "any market open" flag (client-side heuristic). */
  isAnyMarketOpen: boolean;
  /** Force a MultiQuotes refresh now. */
  refreshMultiQuotes: () => Promise<void>;
}

/**
 * Real-time price overlay for tabular data. Subscribes to each row's symbol over
 * the OpenBull WebSocket proxy (LTP mode) and recomputes LTP / P&L / P&L% per
 * tick. When the socket isn't delivering data it falls back to polling the
 * MultiQuotes REST API, so prices/P&L stay populated when the WS is down or
 * outside market hours.
 *
 * Priority per row: fresh WS tick -> MultiQuotes -> REST baseline (item.ltp).
 * Open rows (qty != 0) get live unrealized P&L (+ today's realized, * lot_size);
 * closed rows (qty == 0) keep their REST values so realized P&L stays stable.
 *
 * The returned shape is a superset of the original hook (data/isLive/isConnected/
 * isPaused), so existing consumers are unaffected.
 */
export function useLivePrice<T extends PriceableItem>(
  items: T[],
  options: UseLivePriceOptions = {},
): UseLivePriceResult<T> {
  const {
    enabled = true,
    staleThreshold = 5000,
    pauseWhenHidden = true,
    useMultiQuotesFallback = true,
    multiQuotesRefreshInterval = 30000,
  } = options;

  const { isVisible, wasHidden } = usePageVisibility();
  const { isAnyMarketOpen } = useMarketStatus();
  const isPaused = pauseWhenHidden && !isVisible;

  const symbols = useMemo(
    () => items.map((i) => ({ symbol: i.symbol, exchange: i.exchange })),
    [items],
  );
  const symbolsKey = useMemo(
    () => symbols.map((s) => `${s.exchange}:${s.symbol}`).sort().join(","),
    [symbols],
  );

  const {
    data: marketData,
    isConnected,
    isAuthenticated,
  } = useMarketData({
    symbols,
    mode: "LTP",
    enabled: enabled && items.length > 0 && !isPaused,
  });

  const isLive = isAuthenticated && !isPaused;
  // Fallback mode: the WS isn't streaming, so displayed prices come from REST.
  const isFallbackMode = useMultiQuotesFallback && !isLive;

  // ── MultiQuotes REST fallback ───────────────────────────────────────
  const [multiQuotes, setMultiQuotes] = useState<Map<string, MultiQuoteRow>>(new Map());

  const fetchMultiQuotes = useCallback(async () => {
    if (!useMultiQuotesFallback || items.length === 0) return;
    const rows = await getMultiQuotes(
      items.map((i) => ({ symbol: i.symbol, exchange: i.exchange })),
    );
    if (!rows.length) return;
    const next = new Map<string, MultiQuoteRow>();
    for (const r of rows) {
      if (r.symbol && r.exchange) next.set(`${r.exchange}:${r.symbol}`, r);
    }
    setMultiQuotes(next);
  }, [useMultiQuotesFallback, items]);

  // Poll on an interval as a fallback. WS ticks take priority in enhancedData;
  // this keeps prices fresh when the socket is down or the market is closed.
  useEffect(() => {
    if (!enabled || !useMultiQuotesFallback || items.length === 0) return;
    if (pauseWhenHidden && !isVisible) return;

    let cancelled = false;
    const tick = () => {
      if (!cancelled) void fetchMultiQuotes();
    };
    tick(); // immediate on mount / symbol change
    const id = setInterval(tick, multiQuotesRefreshInterval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [
    enabled,
    useMultiQuotesFallback,
    items.length,
    symbolsKey,
    isVisible,
    pauseWhenHidden,
    multiQuotesRefreshInterval,
    fetchMultiQuotes,
  ]);

  // Refresh immediately when the tab regains focus after being hidden.
  const wasHiddenRef = useRef(wasHidden);
  useEffect(() => {
    if (wasHidden && !wasHiddenRef.current && isVisible) {
      void fetchMultiQuotes();
    }
    wasHiddenRef.current = wasHidden;
  }, [wasHidden, isVisible, fetchMultiQuotes]);

  // ── Enhance rows ────────────────────────────────────────────────────
  const enhancedData = useMemo(() => {
    return items.map((item) => {
      const key = `${item.exchange}:${item.symbol}`;
      const wsData = marketData.get(key);
      const mq = multiQuotes.get(key);
      const qty = item.quantity ?? 0;
      const avgPrice = item.average_price ?? 0;

      const wsFresh =
        wsData?.data?.ltp != null &&
        wsData.lastUpdate != null &&
        Date.now() - wsData.lastUpdate < staleThreshold;

      // Priority: fresh WS tick -> MultiQuotes -> REST baseline.
      let currentLtp = item.ltp;
      if (wsFresh && wsData?.data?.ltp != null) {
        currentLtp = wsData.data.ltp;
      } else if (typeof mq?.ltp === "number" && mq.ltp > 0) {
        currentLtp = mq.ltp;
      }

      // Closed rows: keep REST values unchanged (realized P&L is fixed).
      if (qty === 0) {
        return { ...item } as T;
      }

      let pnl = item.pnl ?? 0;
      let pnlpercent = item.pnlpercent ?? 0;

      if (currentLtp != null && avgPrice > 0) {
        const lotSize = item.lot_size ?? 1;
        const realized = item.today_realized_pnl ?? 0;
        // Long: profit when ltp > avg. Short: profit when ltp < avg.
        const unrealized =
          qty > 0
            ? (currentLtp - avgPrice) * qty * lotSize
            : (avgPrice - currentLtp) * Math.abs(qty) * lotSize;
        pnl = realized + unrealized;
        const investment = Math.abs(avgPrice * qty);
        pnlpercent = investment > 0 ? (pnl / investment) * 100 : 0;
      }

      return { ...item, ltp: currentLtp, pnl, pnlpercent } as T;
    });
  }, [items, marketData, multiQuotes, staleThreshold]);

  return {
    data: enhancedData,
    isLive,
    isConnected,
    isPaused,
    isFallbackMode,
    isAnyMarketOpen: isAnyMarketOpen(),
    refreshMultiQuotes: fetchMultiQuotes,
  };
}

/**
 * Aggregate portfolio stats from rows already enhanced by {@link useLivePrice}.
 */
export function calculateLiveStats<T extends PriceableItem>(items: T[]) {
  let totalPnl = 0;
  let totalInvestment = 0;
  let totalHoldingValue = 0;

  items.forEach((item) => {
    totalPnl += item.pnl ?? 0;
    const avgPrice = item.average_price ?? 0;
    const qty = item.quantity ?? 0;
    const ltp = item.ltp ?? avgPrice;
    totalInvestment += avgPrice * qty;
    totalHoldingValue += ltp * qty;
  });

  const totalPnlPercent =
    totalInvestment > 0 ? (totalPnl / totalInvestment) * 100 : 0;

  return {
    totalholdingvalue: totalHoldingValue,
    totalinvvalue: totalInvestment,
    totalprofitandloss: totalPnl,
    totalpnlpercentage: totalPnlPercent,
  };
}
