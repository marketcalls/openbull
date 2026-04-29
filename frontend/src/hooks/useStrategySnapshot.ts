/**
 * Debounced snapshot hook for the Strategy Builder.
 *
 * Wraps POST /web/strategybuilder/snapshot with:
 *
 *   - debounced auto-fetch on leg changes (default 600 ms) so rapid edits
 *     don't fan out N redundant snapshots while the user is still typing,
 *   - a `requestIdRef` ratchet so out-of-order responses can never overwrite
 *     a newer one (the OptionChain pattern, copied directly because it's
 *     proven to handle the "user changes controls fast" case),
 *   - a `refetch()` function for the manual Refresh button,
 *   - a stable error-message string instead of an axios error object so
 *     the UI doesn't have to repeat the unwrap logic.
 *
 * We use plain useState + useEffect rather than React Query because:
 *
 *   1. The snapshot's queryKey would have to include the legs array,
 *      which serialises poorly (object identity changes on every render
 *      until the user actually settles), and
 *   2. The debounce is the whole point — React Query's `enabled` flag
 *      doesn't combine with debouncing as cleanly as a setTimeout does.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getStrategySnapshot } from "@/api/strategybuilder";
import type {
  SnapshotLegInput,
  SnapshotResponse,
} from "@/types/strategy";

interface Args {
  underlying: string;
  options_exchange: string;
  legs: SnapshotLegInput[];
  /** Auto-debounce window in ms; set to 0 to disable auto-fetch. Default 600. */
  debounceMs?: number;
  /** Skip auto-fetch entirely (e.g. while the chain context is still loading). */
  enabled?: boolean;
}

interface Result {
  snapshot: SnapshotResponse | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

function unwrapErr(e: unknown): string {
  return (
    (e as { response?: { data?: { detail?: string } }; message?: string })?.response?.data
      ?.detail ??
    (e as { message?: string })?.message ??
    "Snapshot failed"
  );
}

export function useStrategySnapshot({
  underlying,
  options_exchange,
  legs,
  debounceMs = 600,
  enabled = true,
}: Args): Result {
  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const doFetch = useCallback(async (): Promise<void> => {
    if (legs.length === 0) {
      setSnapshot(null);
      setError(null);
      return;
    }
    const reqId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await getStrategySnapshot({
        underlying,
        options_exchange,
        legs,
      });
      if (requestIdRef.current !== reqId) return;
      setSnapshot(resp);
    } catch (e) {
      if (requestIdRef.current !== reqId) return;
      setError(unwrapErr(e));
    } finally {
      if (requestIdRef.current === reqId) setLoading(false);
    }
  }, [underlying, options_exchange, legs]);

  // Auto-fetch on leg / underlying changes, debounced.
  // We serialise the legs into a stable string for the dep array so React
  // doesn't re-fire on every parent render that happens to produce a new
  // array reference with identical content.
  const legsKey = JSON.stringify(legs);

  useEffect(() => {
    if (!enabled || debounceMs === 0 || legs.length === 0) return;
    const handle = setTimeout(() => {
      doFetch();
    }, debounceMs);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legsKey, underlying, options_exchange, enabled, debounceMs]);

  return {
    snapshot,
    loading,
    error,
    refetch: doFetch,
  };
}
