import { useMemo } from "react";
import { useMarketData } from "@/hooks/useMarketData";
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
}

export interface UseLivePriceOptions {
  /** Whether the hook is enabled (default: true). */
  enabled?: boolean;
  /** ms after which a WebSocket tick is considered stale (default: 5000). */
  staleThreshold?: number;
  /** Pause the WebSocket when the tab is hidden (default: true). */
  pauseWhenHidden?: boolean;
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
}

/**
 * Real-time price overlay for tabular data. Subscribes to each row's symbol via
 * the OpenBull WebSocket proxy (LTP mode, see {@link useMarketData}) and, on
 * every tick, recomputes LTP / P&L / P&L% client-side. Mirrors openalgo's
 * useLivePrice, adapted to OpenBull's hook surface (no built-in market-status
 * or REST fallback — the WS proxy is the live source).
 *
 * Open rows (qty != 0) get live unrealized P&L; closed rows (qty == 0) keep
 * their REST values so realized P&L stays stable.
 *
 * @example
 * const { data: livePositions, isLive } = useLivePrice(openPositions, {
 *   enabled: openPositions.length > 0,
 * });
 */
export function useLivePrice<T extends PriceableItem>(
  items: T[],
  options: UseLivePriceOptions = {},
): UseLivePriceResult<T> {
  const { enabled = true, staleThreshold = 5000, pauseWhenHidden = true } = options;

  const { isVisible } = usePageVisibility();
  const isPaused = pauseWhenHidden && !isVisible;

  const symbols = useMemo(
    () => items.map((i) => ({ symbol: i.symbol, exchange: i.exchange })),
    [items],
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

  // Recompute whenever a tick lands (marketData is a fresh Map each tick) or the
  // underlying REST rows change.
  const enhancedData = useMemo(() => {
    return items.map((item) => {
      const key = `${item.exchange}:${item.symbol}`;
      const wsData = marketData.get(key);
      const qty = item.quantity ?? 0;
      const avgPrice = item.average_price ?? 0;

      const fresh =
        wsData?.data?.ltp != null &&
        wsData.lastUpdate != null &&
        Date.now() - wsData.lastUpdate < staleThreshold;

      const currentLtp =
        fresh && wsData?.data?.ltp != null ? wsData.data.ltp : item.ltp;

      // Closed rows: keep REST values unchanged (realized P&L is fixed).
      if (qty === 0) {
        return { ...item } as T;
      }

      let pnl = item.pnl ?? 0;
      let pnlpercent = item.pnlpercent ?? 0;

      if (currentLtp != null && avgPrice > 0) {
        // Long: profit when ltp > avg. Short: profit when ltp < avg.
        const unrealized =
          qty > 0
            ? (currentLtp - avgPrice) * qty
            : (avgPrice - currentLtp) * Math.abs(qty);
        pnl = unrealized;
        const investment = Math.abs(avgPrice * qty);
        pnlpercent = investment > 0 ? (pnl / investment) * 100 : 0;
      }

      return { ...item, ltp: currentLtp, pnl, pnlpercent } as T;
    });
  }, [items, marketData, staleThreshold]);

  return { data: enhancedData, isLive, isConnected, isPaused };
}

/**
 * Aggregate portfolio stats from rows already enhanced by {@link useLivePrice}.
 * Used by the Holdings page to keep totals in sync with live LTP.
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
