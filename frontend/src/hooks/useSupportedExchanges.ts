import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { listBrokers } from "@/api/broker";

/**
 * Broker-aware exchange filtering — the single source of truth for which
 * exchanges a picker should show, derived from the connected broker's
 * plugin.json `supported_exchanges` (via /web/broker/list).
 *
 * Design goals (the app relies heavily on /tools, so this must be safe):
 *  - NEVER returns an empty list: if capabilities aren't loaded, or no broker is
 *    active, or the filter would empty the list, it falls back to the caller's
 *    full candidate list (i.e. previous hardcoded behaviour).
 *  - Index/feed exchanges (`*_INDEX`, `*INDICATOR`) are data-only and broker
 *    agnostic in OpenBull, so they are ALWAYS kept even though no plugin lists
 *    them — otherwise Search would lose GLOBAL_INDICATOR / NSE_INDEX etc.
 */

const KEEP_ALWAYS = (value: string): boolean =>
  value.endsWith("_INDEX") || value.includes("INDICATOR");

export interface ExchangeOption {
  value: string;
  label: string;
}

export function useSupportedExchanges() {
  const { data, isSuccess } = useQuery({
    queryKey: ["broker-list"],
    queryFn: listBrokers,
    staleTime: 5 * 60 * 1000,
  });

  // The connected broker's supported set, or null when unknown (→ fallback).
  const supported = useMemo<Set<string> | null>(() => {
    if (!isSuccess || !data) return null;
    const active = data.find((b) => b.is_active);
    const list = active?.supported_exchanges ?? [];
    return list.length ? new Set(list) : null;
  }, [isSuccess, data]);

  /**
   * Filter a candidate {value,label} list to the connected broker's supported
   * exchanges (always keeping index/feed exchanges). Returns the full candidate
   * list unchanged when capabilities are unknown or the result would be empty.
   */
  const filterExchanges = useCallback(
    <T extends ExchangeOption>(candidates: ReadonlyArray<T>): T[] => {
      if (!supported) return [...candidates];
      const filtered = candidates.filter(
        (c) => supported.has(c.value) || KEEP_ALWAYS(c.value),
      );
      return filtered.length ? filtered : [...candidates];
    },
    [supported],
  );

  return { supported, isLoaded: supported !== null, filterExchanges };
}
