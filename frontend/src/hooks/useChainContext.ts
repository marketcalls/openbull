/**
 * Fetches the option chain for the current (underlying, exchange, expiry)
 * tuple and exposes a compact "context" object the Strategy Builder uses
 * to:
 *
 *   - resolve template offsets ("ATM", "OTM2", ...) to absolute strikes
 *   - prefill new legs' lot_size from the actual symtoken row
 *   - (optionally) prefill entry_price from the live LTP at the chosen
 *     strike + option type, so the user gets a realistic starting cost
 *     without having to type prices in by hand
 *
 * The chain is fetched once per (underlying, exchange, expiry) and
 * cached for 60 seconds — the data refreshes when the user clicks
 * Refresh on the page or switches expiry. We deliberately do NOT
 * subscribe to live ticks here: the chain is a *grid* used for setup;
 * live prices flow through the snapshot endpoint and (in Phase 7) the
 * P&L tab's WS subscription.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchOptionChain } from "@/api/optionchain";
import type { FnoExchange, OptionChainResponse } from "@/types/optionchain";

const CHAIN_STRIKE_COUNT = 25;

export interface ChainContext {
  /** ATM strike picked by the backend (nearest to spot). */
  atm: number;
  /** Sorted ascending strike grid for the expiry (CE+PE union). */
  strikes: number[];
  /** Lot size for the underlying — same across all strikes for a given expiry. */
  lotSize: number;
  /** Tick size (price granularity) — used by the order dialog later. */
  tickSize: number;
  /** Underlying spot LTP at the time of the fetch. */
  spot: number;
  /** Last fetched expiry — convenience for the consumer. */
  expiry: string;
  /** strike → CE LTP (for entry-price prefill on BUY/SELL CE). */
  ceLtpByStrike: Map<number, number>;
  /** strike → PE LTP. */
  peLtpByStrike: Map<number, number>;
}

interface Args {
  underlying: string;
  exchange: FnoExchange;
  /** Expiry in backend "DDMMMYY" format. Empty string disables the query. */
  expiryApi: string;
}

interface Result {
  context: ChainContext | null;
  loading: boolean;
  error: string | null;
  /** Force-refetch (no-op while stale-time hasn't elapsed). */
  refetch: () => Promise<unknown>;
}

function toContext(
  resp: OptionChainResponse | undefined,
  expiryApi: string,
): ChainContext | null {
  if (!resp || resp.status !== "success") return null;

  const strikes: number[] = [];
  const ceLtp = new Map<number, number>();
  const peLtp = new Map<number, number>();
  let lotSize = 0;
  let tickSize = 0;

  for (const row of resp.chain) {
    strikes.push(row.strike);
    if (row.ce) {
      ceLtp.set(row.strike, row.ce.ltp);
      if (!lotSize) lotSize = row.ce.lotsize;
      if (!tickSize) tickSize = row.ce.tick_size;
    }
    if (row.pe) {
      peLtp.set(row.strike, row.pe.ltp);
      if (!lotSize) lotSize = row.pe.lotsize;
      if (!tickSize) tickSize = row.pe.tick_size;
    }
  }

  strikes.sort((a, b) => a - b);

  return {
    atm: resp.atm_strike,
    strikes,
    lotSize: lotSize || 1,
    tickSize: tickSize || 0.05,
    spot: resp.underlying_ltp,
    expiry: expiryApi,
    ceLtpByStrike: ceLtp,
    peLtpByStrike: peLtp,
  };
}

export function useChainContext({
  underlying,
  exchange,
  expiryApi,
}: Args): Result {
  const query = useQuery({
    queryKey: ["chain-context", underlying, exchange, expiryApi],
    queryFn: () =>
      fetchOptionChain({
        underlying,
        exchange,
        expiry_date: expiryApi,
        strike_count: CHAIN_STRIKE_COUNT,
      }),
    enabled: Boolean(underlying && exchange && expiryApi),
    staleTime: 60_000,
    retry: 0,
  });

  const context = useMemo(
    () => toContext(query.data, expiryApi),
    [query.data, expiryApi],
  );

  const error = useMemo(() => {
    if (!query.isError && (!query.data || query.data.status === "success")) {
      return null;
    }
    if (query.data?.status === "error") return query.data.message ?? "Chain unavailable";
    const e = query.error as { message?: string } | null;
    return e?.message ?? "Chain unavailable";
  }, [query.isError, query.data, query.error]);

  return {
    context,
    loading: query.isLoading,
    error,
    refetch: query.refetch,
  };
}
