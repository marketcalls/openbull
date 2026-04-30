/**
 * Saved-strategy picker — header dropdown listing the user's saved
 * strategies for the current trading mode. Clicking a row hands the
 * strategy id back to the parent, which loads its legs into the builder
 * (same code path the ?load=<id> URL param uses).
 *
 * Filtered by trading mode + active status so the picker doesn't
 * flood with closed/expired baskets. The Portfolio page is the place to
 * dig through historical strategies — this one is for "open something I
 * was just working on" loops.
 */

import { useState } from "react";
import { ChevronDown, FolderOpen, RefreshCw } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listStrategies } from "@/api/strategies";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Strategy, StrategyMode } from "@/types/strategy";

interface Props {
  mode: StrategyMode;
  /** Currently-loaded strategy id, when any — drives the highlight. */
  loadedId: number | null;
  onPick: (id: number) => void;
  /** Disable while the builder is busy (e.g. mid-save). */
  disabled?: boolean;
}

export function LoadStrategyPicker({ mode, loadedId, onPick, disabled }: Props) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  // Active strategies in the current trading mode. The shared cache key matches
  // the Strategy Portfolio page so a save on one page lands on the other
  // without an extra round trip.
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["strategies", mode, "active"],
    queryFn: () => listStrategies({ mode, status: "active" }),
    staleTime: 15_000,
    retry: 0,
  });

  const list: Strategy[] = data ?? [];

  const handlePick = (id: number) => {
    onPick(id);
    setOpen(false);
  };

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ["strategies"] });
    refetch();
  };

  return (
    <div className="relative">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="h-8 gap-1.5"
        title="Load a saved strategy"
      >
        <FolderOpen className="h-3.5 w-3.5" />
        <span>Load</span>
        {list.length > 0 && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold tabular-nums">
            {list.length}
          </span>
        )}
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 transition-transform",
            open && "rotate-180",
          )}
        />
      </Button>

      {open && (
        <>
          {/* Click-outside scrim */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />

          <div className="absolute right-0 z-50 mt-1 w-80 overflow-hidden rounded-lg border border-border bg-popover shadow-lg">
            <div className="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-2">
              <div>
                <p className="text-xs font-semibold capitalize">
                  {mode} · saved strategies
                </p>
                <p className="text-[10px] text-muted-foreground">
                  Active only · click to load
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={handleRefresh}
                className="h-6 w-6"
                title="Refresh list"
              >
                <RefreshCw
                  className={cn(
                    "h-3 w-3",
                    isLoading && "animate-spin",
                  )}
                />
              </Button>
            </div>

            <div className="max-h-[60vh] overflow-y-auto">
              {isLoading && list.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  Loading…
                </div>
              ) : isError ? (
                <div className="px-3 py-6 text-center text-xs text-destructive">
                  Failed to load saved strategies.
                </div>
              ) : list.length === 0 ? (
                <div className="space-y-1 px-3 py-6 text-center">
                  <p className="text-xs font-medium">No saved strategies yet.</p>
                  <p className="text-[10px] text-muted-foreground">
                    Build legs above and click Save — they'll show up here.
                  </p>
                </div>
              ) : (
                <ul className="py-1">
                  {list.map((s) => {
                    const isLoaded = loadedId === s.id;
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          onClick={() => handlePick(s.id)}
                          className={cn(
                            "block w-full px-3 py-2 text-left transition-colors hover:bg-muted/60",
                            isLoaded && "bg-muted/40",
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <p
                                className={cn(
                                  "truncate text-xs font-semibold",
                                  isLoaded && "text-primary",
                                )}
                              >
                                {s.name}
                              </p>
                              <p className="mt-0.5 text-[10px] text-muted-foreground">
                                {s.underlying} · {s.exchange}{" "}
                                {s.expiry_date ? `· ${s.expiry_date}` : ""}
                              </p>
                            </div>
                            <div className="text-right">
                              <p className="text-[10px] text-muted-foreground">
                                {s.legs.length} leg
                                {s.legs.length === 1 ? "" : "s"}
                              </p>
                              {isLoaded && (
                                <span className="text-[10px] font-medium text-primary">
                                  loaded
                                </span>
                              )}
                            </div>
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
