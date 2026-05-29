import { useEffect, useMemo, useState } from "react";

/**
 * Approximate market-open status, computed CLIENT-SIDE from IST wall-clock.
 *
 * OpenBull has no server-side timings/holidays endpoint (unlike openalgo's
 * /admin/api/timings), so this is a best-effort heuristic:
 *   - weekend (Sat/Sun) -> closed for all non-crypto exchanges
 *   - per-family session windows in IST (below)
 *   - NO holiday calendar — on an exchange holiday this will report "open"
 *
 * It is intended only as an informational signal (e.g. a "market closed" hint
 * and to drive UI affordances). It deliberately does NOT gate live WebSocket
 * data: ticks are always rendered when they arrive, regardless of this flag.
 */

// Session windows in IST minutes-since-midnight. [openMin, closeMin].
const SESSIONS: Record<string, [number, number]> = {
  NSE: [555, 930], // 09:15 - 15:30
  BSE: [555, 930],
  NFO: [555, 930],
  BFO: [555, 930],
  NSE_INDEX: [555, 930],
  BSE_INDEX: [555, 930],
  CDS: [540, 1020], // 09:00 - 17:00
  BCD: [540, 1020],
  NCDEX: [540, 1020],
  MCX: [540, 1415], // 09:00 - 23:35
  MCX_INDEX: [540, 1415],
  NCO: [540, 1415],
};

function istNow(): { weekday: number; minutes: number } {
  // en-GB gives 24h HH:MM; weekday via the 'short' name mapped to 0..6 (Sun=0).
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hour12: false,
  });
  const parts = fmt.formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const hh = parseInt(get("hour") || "0", 10);
  const mm = parseInt(get("minute") || "0", 10);
  const wd = get("weekday");
  const map: Record<string, number> = {
    Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
  };
  return { weekday: map[wd] ?? 1, minutes: hh * 60 + mm };
}

function isOpenFor(exchange: string, now: { weekday: number; minutes: number }): boolean {
  const ex = (exchange || "").toUpperCase();
  if (ex.startsWith("CRYPTO")) return true; // 24x7
  if (now.weekday === 0 || now.weekday === 6) return false; // weekend
  const win = SESSIONS[ex];
  if (!win) return true; // unknown exchange — lenient (don't suppress)
  return now.minutes >= win[0] && now.minutes <= win[1];
}

export interface UseMarketStatusReturn {
  /** True iff the given exchange's approximate session is currently open. */
  isMarketOpen: (exchange: string) => boolean;
  /** True iff any known exchange family is currently open. */
  isAnyMarketOpen: () => boolean;
}

export function useMarketStatus(): UseMarketStatusReturn {
  // Re-render once a minute so open/close transitions are reflected.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  return useMemo<UseMarketStatusReturn>(
    () => ({
      isMarketOpen: (exchange: string) => isOpenFor(exchange, istNow()),
      isAnyMarketOpen: () => {
        const now = istNow();
        return Object.keys(SESSIONS).some((ex) => isOpenFor(ex, now));
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
}
