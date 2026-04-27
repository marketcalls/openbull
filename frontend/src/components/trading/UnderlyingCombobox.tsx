import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";
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

const MAX_RESULTS = 100;

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
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Lookup the friendly label for the currently selected symbol.
  const selectedLabel = useMemo(() => {
    const opt = options.find((o) => o.symbol === value);
    if (opt) return opt.name === opt.symbol ? opt.symbol : `${opt.symbol} — ${opt.name}`;
    return value;
  }, [options, value]);

  // Filter options by query — match symbol or name, case-insensitive.
  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    if (!q) return options.slice(0, MAX_RESULTS);
    const out: UnderlyingOption[] = [];
    for (const o of options) {
      if (o.symbol.includes(q) || o.name.toUpperCase().includes(q)) {
        out.push(o);
        if (out.length >= MAX_RESULTS) break;
      }
    }
    return out;
  }, [options, query]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Keep highlight in range when filtered list shrinks.
  useEffect(() => {
    if (highlight >= filtered.length) setHighlight(0);
  }, [filtered.length, highlight]);

  // Focus input + scroll active option into view when opened.
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    setHighlight(() => {
      const idx = filtered.findIndex((o) => o.symbol === value);
      return idx >= 0 ? idx : 0;
    });
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

  // Scroll highlighted item into view.
  useEffect(() => {
    if (!open || !listRef.current) return;
    const el = listRef.current.children[highlight] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  return (
    <div ref={wrapRef} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-background px-2 text-left text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="truncate">
          {value ? selectedLabel : <span className="text-muted-foreground">{placeholder}</span>}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-60" />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full min-w-[260px] rounded-lg border border-border bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10">
          <div className="flex items-center gap-2 border-b border-border px-2 py-1.5">
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKey}
              placeholder={loading ? "Loading underlyings…" : "Search underlying…"}
              className="h-7 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              autoComplete="off"
              spellCheck={false}
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
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
            <ul ref={listRef} className="max-h-72 overflow-y-auto py-1">
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
                      "flex cursor-pointer items-center justify-between gap-2 px-2.5 py-1.5 text-sm",
                      active && "bg-accent text-accent-foreground",
                      selected && !active && "bg-muted"
                    )}
                  >
                    <span className="font-mono font-medium">{o.symbol}</span>
                    {o.name && o.name !== o.symbol && (
                      <span className="truncate text-xs text-muted-foreground">{o.name}</span>
                    )}
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
        </div>
      )}
    </div>
  );
}
