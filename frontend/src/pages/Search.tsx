import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { searchSymbols } from "@/api/symbols";
import { EXCHANGES } from "@/types/symbol";
import { cn } from "@/lib/utils";

const MAX_ROWS = 50; // backend LIMIT 50

function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

function instrumentBadgeVariant(type: string | null): "default" | "secondary" | "outline" {
  if (!type) return "outline";
  const t = type.toUpperCase();
  if (t === "CE" || t === "PE" || t === "OPTIDX" || t === "OPTSTK" || t === "OPTFUT") {
    return "default";
  }
  if (t === "FUT" || t === "FUTIDX" || t === "FUTSTK" || t === "FUTCOM") {
    return "secondary";
  }
  return "outline";
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

export default function Search() {
  const [query, setQuery] = useState("");
  const [exchange, setExchange] = useState<string>("NSE");
  const debouncedQuery = useDebounced(query.trim(), 300);

  const enabled = debouncedQuery.length >= 1 && exchange.length > 0;

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["symbol-search", exchange, debouncedQuery],
    queryFn: () => searchSymbols(debouncedQuery, exchange),
    enabled,
    staleTime: 30_000,
  });

  const resultCount = data?.length ?? 0;
  const hitCap = resultCount === MAX_ROWS;

  const hint = useMemo(() => {
    if (exchange === "NFO" || exchange === "BFO") {
      return "Try NIFTY, BANKNIFTY, or a specific options symbol like NIFTY28MAR2420800CE.";
    }
    if (exchange === "MCX") return "Try CRUDEOIL, GOLD, SILVER.";
    if (exchange === "NSE_INDEX" || exchange === "BSE_INDEX") {
      return "Try NIFTY, BANKNIFTY, SENSEX, BANKEX.";
    }
    return "Try INFY, TCS, or part of a company name.";
  }, [exchange]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Symbol Search</h1>
        <p className="text-sm text-muted-foreground">
          Look up instruments from the master contract using OpenAlgo-style symbology.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
          <CardDescription>
            Searches the master contract for your active broker. Pick an exchange, then type
            a symbol or part of one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[200px_1fr]">
            <div className="space-y-2">
              <Label htmlFor="exchange">Exchange</Label>
              <select
                id="exchange"
                value={exchange}
                onChange={(e) => setExchange(e.target.value)}
                className={cn(
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "ring-offset-background focus-visible:outline-none focus-visible:ring-2",
                  "focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                )}
              >
                {EXCHANGES.map((e) => (
                  <option key={e.value} value={e.value}>
                    {e.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="query">Symbol</Label>
              <Input
                id="query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. NIFTY, INFY, CRUDEOIL"
                autoComplete="off"
                spellCheck={false}
              />
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Results</CardTitle>
          <CardDescription>
            {!enabled
              ? "Enter a search term to see matches."
              : isLoading || isFetching
              ? "Searching…"
              : error
              ? "Search failed."
              : resultCount === 0
              ? "No symbols found."
              : hitCap
              ? `${resultCount}+ matches (showing first ${MAX_ROWS} — refine your query for more specific results)`
              : `${resultCount} match${resultCount === 1 ? "" : "es"}`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              Failed to search symbols. Make sure the master contract has been downloaded for
              your broker.
            </div>
          ) : enabled && data && data.length > 0 ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Exchange</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Expiry</TableHead>
                    <TableHead className="text-right">Strike</TableHead>
                    <TableHead className="text-right">Lot</TableHead>
                    <TableHead className="text-right">Tick</TableHead>
                    <TableHead>Broker Symbol</TableHead>
                    <TableHead>Broker Exch</TableHead>
                    <TableHead>Token</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((row, i) => (
                    <TableRow key={`${row.token ?? row.symbol}-${i}`} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                      <TableCell className="font-medium">{row.symbol}</TableCell>
                      <TableCell className="max-w-[240px] truncate" title={row.name ?? undefined}>
                        {row.name ?? "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{row.exchange}</Badge>
                      </TableCell>
                      <TableCell>
                        {row.instrumenttype ? (
                          <Badge variant={instrumentBadgeVariant(row.instrumenttype)}>
                            {row.instrumenttype}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{row.expiry ?? "—"}</TableCell>
                      <TableCell className="text-right">{fmt(row.strike)}</TableCell>
                      <TableCell className="text-right">{fmtInt(row.lotsize)}</TableCell>
                      <TableCell className="text-right">{fmt(row.tick_size, 2)}</TableCell>
                      <TableCell className="font-mono text-xs">{row.brsymbol}</TableCell>
                      <TableCell>{row.brexchange ?? "—"}</TableCell>
                      <TableCell className="font-mono text-xs">{row.token ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {enabled ? "No symbols matched your query." : "Enter a symbol to begin searching."}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
