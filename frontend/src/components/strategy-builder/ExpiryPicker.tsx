/**
 * Strategy Builder expiry picker.
 *
 * Fetches the available option expiries for the chosen underlying via
 * `/api/v1/expiry`, snaps to the nearest expiry on first load, and lets
 * the user override. The display format is the API's
 * "DD-MMM-YYYY" — `convertExpiryForApi` converts to the
 * backend's expected "DDMMMYY" before any snapshot/chart call.
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchExpiries } from "@/api/optionchain";
import type { FnoExchange } from "@/types/optionchain";

interface Props {
  underlying: string;
  exchange: FnoExchange;
  expiry: string;
  onExpiryChange: (expiry: string) => void;
  disabled?: boolean;
}

export function ExpiryPicker({
  underlying,
  exchange,
  expiry,
  onExpiryChange,
  disabled,
}: Props) {
  const expiriesQuery = useQuery({
    queryKey: ["expiries", underlying, exchange],
    queryFn: () =>
      fetchExpiries({ symbol: underlying, exchange, instrumenttype: "options" }),
    enabled: !!underlying && !!exchange,
    retry: 0,
  });

  // Auto-snap to the first (nearest) expiry once the list loads, but only
  // when the current selection isn't in the new list. Lets the user keep
  // their expiry choice across underlying-bound refreshes when possible.
  useEffect(() => {
    if (
      expiriesQuery.data?.status === "success" &&
      expiriesQuery.data.data.length > 0
    ) {
      const list = expiriesQuery.data.data;
      if (!expiry || !list.includes(expiry)) {
        onExpiryChange(list[0]);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiriesQuery.data]);

  const list =
    expiriesQuery.data?.status === "success" ? expiriesQuery.data.data : [];

  return (
    <div className="space-y-1">
      <label className="block text-xs text-muted-foreground">Expiry</label>
      <select
        value={expiry}
        onChange={(e) => onExpiryChange(e.target.value)}
        disabled={disabled || expiriesQuery.isLoading || list.length === 0}
        className="h-8 w-40 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
      >
        {list.length > 0 ? (
          list.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))
        ) : (
          <option value="">
            {expiriesQuery.isLoading ? "Loading…" : "No expiries"}
          </option>
        )}
      </select>
    </div>
  );
}

/** "02-MAY-2026" → "02MAY26" — same converter the existing tools use. */
export function convertExpiryForApi(expiry: string): string {
  if (!expiry) return "";
  // "02-MAY-2026" -> ["02","MAY","2026"]
  const parts = expiry.split("-");
  if (parts.length !== 3) return expiry.replace(/-/g, "").toUpperCase();
  const [dd, mmm, yyyy] = parts;
  const yy = yyyy.length === 4 ? yyyy.slice(-2) : yyyy;
  return `${dd}${mmm}${yy}`.toUpperCase();
}
