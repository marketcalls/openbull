/**
 * Tabular-nums price cell with a brief green/red flash on every tick.
 *
 * Pattern lifted from `OptionChain.tsx:135-145` and adapted to be
 * self-contained: instead of asking the parent to track a `prev` snapshot
 * and pass `flash` as a prop, the cell keeps its own previous-value ref
 * and runs the highlight in an internal effect. Lets P&L rows stay
 * stateless from the parent's perspective.
 *
 * Highlight fades after ~600 ms via a transient class — long enough to be
 * noticed mid-tick stream, short enough not to look perpetually busy.
 */

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface Props {
  value: number | null | undefined;
  /** Number → display string. Defaults to two-decimal fixed. */
  format?: (v: number) => string;
  /** Extra classes (forwarded to the outer span). */
  className?: string;
  /** Override fallback when value is missing. */
  emptyLabel?: string;
}

export function LivePriceCell({
  value,
  format = (v) => v.toFixed(2),
  className,
  emptyLabel = "—",
}: Props) {
  const prevRef = useRef<number | null>(null);
  const [flash, setFlash] = useState<"" | "up" | "down">("");

  useEffect(() => {
    if (value == null || !Number.isFinite(value)) return;
    const prev = prevRef.current;
    if (prev !== null && prev !== value) {
      setFlash(value > prev ? "up" : "down");
      const t = window.setTimeout(() => setFlash(""), 600);
      prevRef.current = value;
      return () => window.clearTimeout(t);
    }
    prevRef.current = value;
  }, [value]);

  if (value == null || !Number.isFinite(value)) {
    return (
      <span
        className={cn(
          "inline-block font-mono tabular-nums text-muted-foreground",
          className,
        )}
      >
        {emptyLabel}
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-block rounded px-1 font-mono tabular-nums transition-colors duration-300",
        flash === "up" && "bg-emerald-500/25 text-emerald-700 dark:text-emerald-300",
        flash === "down" && "bg-red-500/25 text-red-700 dark:text-red-300",
        className,
      )}
    >
      {format(value)}
    </span>
  );
}
