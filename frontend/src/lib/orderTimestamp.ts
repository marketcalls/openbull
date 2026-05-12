/**
 * Order/trade timestamp helpers — every broker returns the timestamp in a
 * slightly different shape, so we normalise here once instead of in every
 * page that wants to render a time column.
 *
 * Known shapes (sourced from openalgo's broker plugins):
 *  - ISO 8601:                "2026-05-12T14:35:22+05:30"
 *  - Norentm (Flattrade/etc): "14:35:22 12-05-2026"
 *  - Common Indian format:    "12-05-2026 14:35:22"
 *  - Time-only fallback:      "14:35:22"
 *
 * Ported from openalgo/frontend/src/pages/OrderBook.tsx so live + sandbox
 * orderbooks render the same time format regardless of which broker plugin
 * filled the row.
 */

/** Convert any supported broker timestamp into a millisecond epoch.
 *  Returns 0 if nothing parses (so callers can sort safely). */
export function parseOrderTimestamp(timestamp: string | null | undefined): number {
  if (!timestamp) return 0;
  // Native Date first — handles ISO, RFC 2822, and locale strings.
  let date = new Date(timestamp);
  if (!Number.isNaN(date.getTime())) return date.getTime();

  // Norentm: "HH:MM:SS DD-MM-YYYY"
  const norentm = timestamp.match(
    /^(\d{2}:\d{2}:\d{2})\s+(\d{2})-(\d{2})-(\d{4})$/,
  );
  if (norentm) {
    date = new Date(`${norentm[4]}-${norentm[3]}-${norentm[2]}T${norentm[1]}`);
    if (!Number.isNaN(date.getTime())) return date.getTime();
  }

  // Common: "DD-MM-YYYY HH:MM:SS"
  const ddmmyyyy = timestamp.match(
    /^(\d{2})-(\d{2})-(\d{4})\s+(\d{2}:\d{2}:\d{2})$/,
  );
  if (ddmmyyyy) {
    date = new Date(
      `${ddmmyyyy[3]}-${ddmmyyyy[2]}-${ddmmyyyy[1]}T${ddmmyyyy[4]}`,
    );
    if (!Number.isNaN(date.getTime())) return date.getTime();
  }

  return 0;
}

/** Compact time string for table rows: HH:MM:SS in Asia/Kolkata. */
export function formatOrderTime(timestamp: string | null | undefined): string {
  if (!timestamp) return "—";
  const epoch = parseOrderTimestamp(timestamp);
  if (epoch === 0) {
    // Last resort: extract any embedded HH:MM:SS so the user sees something.
    const m = timestamp.match(/(\d{2}:\d{2}:\d{2})/);
    return m ? m[1] : timestamp;
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(epoch));
}

/** Full date-time, useful for tooltips: "12 May 2026, 14:35:22 IST". */
export function formatOrderDateTime(
  timestamp: string | null | undefined,
): string {
  if (!timestamp) return "—";
  const epoch = parseOrderTimestamp(timestamp);
  if (epoch === 0) return timestamp;
  return (
    new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(epoch)) + " IST"
  );
}
