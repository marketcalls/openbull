import { useTradingMode } from "@/contexts/TradingModeContext";

/**
 * Persistent banner that reminds the user they're in sandbox mode.
 *
 * Rationale: amber theme alone is easy to miss if you've been staring at the
 * page for an hour. An explicit textual banner is unambiguous — it matches
 * openalgo's pattern of making "not live" states impossible to forget about.
 */
export function SandboxBanner() {
  const { isSandbox } = useTradingMode();
  if (!isSandbox) return null;
  return (
    <div
      className="flex items-center justify-center gap-2 border-b border-indigo-500/40 bg-indigo-500/10 px-4 py-1.5 text-xs font-medium text-indigo-900 dark:text-indigo-200"
      role="status"
      aria-live="polite"
    >
      <span
        className="inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-500"
        aria-hidden
      />
      <span>
        SANDBOX MODE — orders are simulated, no real money or broker calls.
      </span>
    </div>
  );
}
