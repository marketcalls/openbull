/**
 * Clickable table-head cell that toggles sort direction on the current
 * column or switches to a new column (with a default direction depending
 * on whether the column is numeric / time-like).
 *
 * Pair with `useSortedRows` from `@/lib/sort` to get the sorted list.
 */

import * as React from "react";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface SortState<K extends string> {
  key: K;
  direction: "asc" | "desc";
}

interface Props<K extends string> {
  sortKey: K;
  current: SortState<K> | null;
  onSort: (key: K) => void;
  align?: "left" | "right";
  className?: string;
  children: React.ReactNode;
}

export function SortableHead<K extends string>({
  sortKey,
  current,
  onSort,
  align = "left",
  className,
  children,
}: Props<K>) {
  const active = current?.key === sortKey;
  const arrow = active ? (current!.direction === "asc" ? "▲" : "▼") : "▾";

  return (
    <TableHead className={cn(align === "right" && "text-right", className)}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "group inline-flex items-center gap-1 select-none outline-none",
          "rounded-sm text-[12px] font-medium uppercase tracking-[0.04em] transition-colors",
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
          "focus-visible:ring-2 focus-visible:ring-ring",
          align === "right" && "ml-auto",
        )}
        aria-label={`Sort by ${typeof children === "string" ? children : sortKey}`}
        aria-sort={
          active ? (current!.direction === "asc" ? "ascending" : "descending") : "none"
        }
      >
        <span>{children}</span>
        <span
          className={cn(
            "text-[9px] leading-none",
            active ? "opacity-100" : "opacity-30 group-hover:opacity-60",
          )}
          aria-hidden
        >
          {arrow}
        </span>
      </button>
    </TableHead>
  );
}
