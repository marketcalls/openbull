/**
 * Strategy Builder underlying + exchange picker.
 *
 * Emits two stacked controls (Exchange select + Underlying combobox) as
 * a fragment so the parent's flex-wrap row sits them inline with the
 * other controls. Same pattern as OITracker / OptionChain.
 */

import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchUnderlyings } from "@/api/optionchain";
import { UnderlyingCombobox } from "@/components/trading/UnderlyingCombobox";
import {
  FALLBACK_UNDERLYINGS,
  FNO_EXCHANGES,
  type FnoExchange,
  type UnderlyingOption,
} from "@/types/optionchain";

interface Props {
  exchange: FnoExchange;
  underlying: string;
  onExchangeChange: (e: FnoExchange) => void;
  onUnderlyingChange: (sym: string) => void;
  /** Disable controls while a parent operation is in flight. */
  disabled?: boolean;
}

export function UnderlyingPicker({
  exchange,
  underlying,
  onExchangeChange,
  onUnderlyingChange,
  disabled,
}: Props) {
  const underlyingsQuery = useQuery({
    queryKey: ["option-underlyings", exchange],
    queryFn: () => fetchUnderlyings(exchange),
    retry: 0,
    staleTime: 5 * 60_000,
  });

  const underlyings = useMemo<UnderlyingOption[]>(() => {
    if (
      underlyingsQuery.data?.status === "success" &&
      underlyingsQuery.data.data.length > 0
    ) {
      return underlyingsQuery.data.data;
    }
    return FALLBACK_UNDERLYINGS[exchange];
  }, [underlyingsQuery.data, exchange]);

  // Snap underlying to the first available when the list changes — same
  // safety as OITracker. Without this, switching from NFO→BFO with NIFTY
  // selected would leave a stale underlying that doesn't exist on BFO.
  useEffect(() => {
    if (underlyings.length === 0) return;
    if (!underlyings.some((u) => u.symbol === underlying)) {
      onUnderlyingChange(underlyings[0].symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlyings]);

  return (
    <>
      <div className="space-y-1">
        <label className="block text-xs text-muted-foreground">Exchange</label>
        <select
          value={exchange}
          onChange={(e) => onExchangeChange(e.target.value as FnoExchange)}
          disabled={disabled}
          className="h-8 w-24 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
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
          Underlying ({underlyings.length})
        </label>
        <UnderlyingCombobox
          value={underlying}
          options={underlyings}
          onChange={onUnderlyingChange}
          loading={underlyingsQuery.isLoading}
          className="w-56"
        />
      </div>
    </>
  );
}
