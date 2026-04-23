import { useState } from "react";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";
import type { TradingMode } from "@/api/trading_mode";

/**
 * Topbar 2-way segmented switch. Non-admins see a read-only badge reflecting
 * the current mode — the POST endpoint rejects their click regardless, but
 * disabling it client-side is a clearer UX than a surprise 403.
 */
export function TradingModeSwitch() {
  const { mode, isLoading, setMode } = useTradingMode();
  const { user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const canToggle = !!user?.is_admin;

  const options: { value: TradingMode; label: string }[] = [
    { value: "live", label: "Live" },
    { value: "sandbox", label: "Sandbox" },
  ];

  const handleClick = async (next: TradingMode) => {
    if (next === mode || !canToggle || pending) return;
    setError(null);
    setPending(true);
    try {
      await setMode(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to switch mode");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex flex-col items-end">
      <div
        className={cn(
          "inline-flex items-center rounded-md border p-0.5 text-xs font-medium",
          "border-border bg-muted/40"
        )}
        role="group"
        aria-label="Trading mode"
      >
        {options.map((opt) => {
          const selected = mode === opt.value;
          const sandbox = opt.value === "sandbox";
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => handleClick(opt.value)}
              disabled={!canToggle || pending || isLoading}
              className={cn(
                "inline-flex h-7 items-center rounded px-3 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                !canToggle && "cursor-not-allowed opacity-70",
                selected
                  ? sandbox
                    ? "bg-amber-500 text-white shadow-sm"
                    : "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              aria-pressed={selected}
              title={
                canToggle
                  ? `Switch to ${opt.label.toLowerCase()} mode`
                  : "Only admins can switch trading mode"
              }
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      {error && (
        <span className="mt-1 text-[10px] text-destructive">{error}</span>
      )}
    </div>
  );
}
