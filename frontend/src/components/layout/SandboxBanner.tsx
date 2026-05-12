import { useTradingMode } from "@/contexts/TradingModeContext";

/**
 * Persistent banner that reminds the user they're in sandbox mode.
 *
 * Rationale: the slate-indigo palette alone is easy to miss after staring at
 * the page for an hour. A solid indigo strip with white text — Stripe-style
 * test-mode treatment — makes "not live" unmistakable even on a tinted
 * page background.
 */
export function SandboxBanner() {
  const { isSandbox } = useTradingMode();
  if (!isSandbox) return null;
  return (
    <div
      className="flex items-center justify-center gap-2 border-b border-indigo-700 bg-indigo-600 px-4 py-2 text-[12px] font-semibold tracking-wide text-white shadow-sm"
      role="status"
      aria-live="polite"
    >
      <span
        className="inline-block h-2 w-2 animate-pulse rounded-full bg-white ring-2 ring-white/40"
        aria-hidden
      />
      <span className="uppercase tracking-[0.14em]">Sandbox mode</span>
      <span className="text-white/70" aria-hidden>
        ·
      </span>
      <span className="font-medium normal-case tracking-normal text-white/90">
        orders are simulated, no real money or broker calls
      </span>
    </div>
  );
}
