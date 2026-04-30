import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, X, Check } from "lucide-react";
import type { UnderlyingOption } from "@/types/optionchain";
import { cn } from "@/lib/utils";

interface UnderlyingComboboxProps {
  value: string;
  options: UnderlyingOption[];
  onChange: (symbol: string) => void;
  loading?: boolean;
  placeholder?: string;
  className?: string;
}

const MAX_RESULTS = 200;
const PANEL_MAX_HEIGHT = 360;

interface PanelPosition {
  top: number;
  left: number;
  width: number;
  flipped: boolean;
}

export function UnderlyingCombobox({
  value,
  options,
  onChange,
  loading,
  placeholder = "Select underlying…",
  className,
}: UnderlyingComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [pos, setPos] = useState<PanelPosition | null>(null);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Display the bare ticker only (NIFTY, BANKNIFTY, RELIANCE) — not the
  // long company / contract name. Some brokers (Fyers) populate `name`
  // with the per-contract description which is noisy and useless to a
  // trader who's already on a F&O screen.
  const selectedLabel = useMemo(() => {
    const opt = options.find((o) => o.symbol === value);
    return opt ? opt.symbol : value;
  }, [options, value]);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (!q) return options.slice(0, MAX_RESULTS);
    const out: UnderlyingOption[] = [];
    // Symbol-only search — the name column isn't displayed any more, so
    // matching against it would produce confusing "ghost" results.
    for (const o of options) {
      if (o.symbol.includes(q)) {
        out.push(o);
        if (out.length >= MAX_RESULTS) break;
      }
    }
    return out;
  }, [options, query]);

  // Compute panel position from the trigger's viewport rect. Flip above when
  // there's not enough room below.
  const computePosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const flipped = spaceBelow < 220 && spaceAbove > spaceBelow;
    setPos({
      top: flipped ? Math.max(8, rect.top - 4) : rect.bottom + 4,
      left: rect.left,
      width: Math.max(rect.width, 280),
      flipped,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    computePosition();
  }, [open, computePosition]);

  // Reposition on scroll/resize. Use capture so we catch scrolls in nested
  // overflow containers (AppLayout's <main>).
  useEffect(() => {
    if (!open) return;
    const reposition = () => computePosition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, computePosition]);

  // Click outside the panel and trigger → close.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Keep highlight in range when filtered list shrinks.
  useEffect(() => {
    if (highlight >= filtered.length) setHighlight(0);
  }, [filtered.length, highlight]);

  // Focus input + jump highlight to currently selected option when opened.
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const idx = filtered.findIndex((o) => o.symbol === value);
    setHighlight(idx >= 0 ? idx : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const commit = (sym: string) => {
    onChange(sym);
    setOpen(false);
    setQuery("");
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = filtered[highlight];
      if (pick) commit(pick.symbol);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  // Scroll highlighted item into view inside the list.
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.children[highlight] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  const panel =
    open && pos
      ? createPortal(
          <div
            ref={panelRef}
            style={{
              position: "fixed",
              top: pos.flipped ? undefined : pos.top,
              bottom: pos.flipped ? window.innerHeight - pos.top : undefined,
              left: pos.left,
              width: pos.width,
              maxHeight: PANEL_MAX_HEIGHT,
              zIndex: 9999,
            }}
            className="flex flex-col rounded-lg border border-border bg-popover text-popover-foreground shadow-xl ring-1 ring-foreground/10"
          >
            <div className="flex items-center gap-2 border-b border-border px-2 py-2">
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onKey}
                placeholder={loading ? "Loading underlyings…" : "Search symbol…"}
                className="h-7 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                autoComplete="off"
                spellCheck={false}
              />
              {query && (
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    inputRef.current?.focus();
                  }}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="Clear search"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            {filtered.length === 0 ? (
              <p className="px-3 py-3 text-center text-xs text-muted-foreground">
                {loading ? "Loading…" : "No matches."}
              </p>
            ) : (
              <ul ref={listRef} className="flex-1 overflow-y-auto py-1">
                {filtered.map((o, i) => {
                  const active = i === highlight;
                  const selected = o.symbol === value;
                  return (
                    <li
                      key={o.symbol}
                      onMouseEnter={() => setHighlight(i)}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        commit(o.symbol);
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-sm",
                        active
                          ? "bg-primary text-primary-foreground"
                          : selected
                            ? "bg-muted"
                            : "hover:bg-muted/60"
                      )}
                    >
                      <Check
                        className={cn(
                          "h-3.5 w-3.5 shrink-0",
                          selected ? "opacity-100" : "opacity-0"
                        )}
                      />
                      <span
                        className={cn(
                          "font-mono font-semibold",
                          active && "text-primary-foreground"
                        )}
                      >
                        {o.symbol}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
            {options.length > filtered.length && (
              <div className="border-t border-border px-2 py-1 text-[10px] text-muted-foreground">
                Showing {filtered.length} of {options.length} — refine your search for more.
              </div>
            )}
          </div>,
          document.body
        )
      : null;

  return (
    <div className={cn("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-background px-2 text-left text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="truncate">
          {value ? selectedLabel : <span className="text-muted-foreground">{placeholder}</span>}
        </span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 opacity-60 transition-transform", open && "rotate-180")}
        />
      </button>
      {panel}
    </div>
  );
}
