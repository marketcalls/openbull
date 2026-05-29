import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
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
import { useSupportedExchanges } from "@/hooks/useSupportedExchanges";
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

function instrumentBadgeVariant(
  type: string | null,
): "default" | "secondary" | "outline" {
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

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Search() {
  const [params, setParams] = useSearchParams();
  const tabFromUrl = params.get("tab");
  const initialTab = tabFromUrl === "guide" ? "guide" : "search";

  const [tab, setTab] = useState<"search" | "guide">(
    initialTab as "search" | "guide",
  );
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [exchange, setExchange] = useState<string>(params.get("ex") ?? "NSE");

  // Keep the URL ↔ state loosely in sync — lets users share a link to a
  // particular search or to the format guide. Only writes when the values
  // change to avoid replacing history on every keystroke.
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (tab === "guide") next.set("tab", "guide");
    else next.delete("tab");
    if (query) next.set("q", query);
    else next.delete("q");
    next.set("ex", exchange);
    if (next.toString() !== params.toString()) {
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, query, exchange]);

  /** Called from the Format Guide's example tables — pre-fills + switches
   *  to the Search tab so the user can see what comes back. */
  const handleTryExample = (sym: string, exch: string) => {
    setQuery(sym);
    setExchange(exch);
    setTab("search");
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          Symbol Search
        </h1>
        <p className="text-sm text-muted-foreground">
          Look up instruments from the master contract using OpenAlgo-style
          symbology, or read the format guide below.
        </p>
      </div>

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as "search" | "guide")}
        className="gap-4"
      >
        <TabsList>
          <TabsTrigger value="search">Search</TabsTrigger>
          <TabsTrigger value="guide">Format Guide</TabsTrigger>
        </TabsList>

        <TabsContent value="search" className="space-y-4">
          <SearchPanel
            query={query}
            setQuery={setQuery}
            exchange={exchange}
            setExchange={setExchange}
          />
        </TabsContent>

        <TabsContent value="guide" className="space-y-4">
          <FormatGuide onTryExample={handleTryExample} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search panel
// ---------------------------------------------------------------------------

function SearchPanel({
  query,
  setQuery,
  exchange,
  setExchange,
}: {
  query: string;
  setQuery: (v: string) => void;
  exchange: string;
  setExchange: (v: string) => void;
}) {
  const debouncedQuery = useDebounced(query.trim(), 300);
  const enabled = debouncedQuery.length >= 1 && exchange.length > 0;

  // Broker-aware exchange options: filter the tradable exchanges to the
  // connected broker's supported set (index/feed exchanges are always kept,
  // and it falls back to the full list when capabilities aren't loaded).
  const { filterExchanges } = useSupportedExchanges();

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
    if (exchange === "GLOBAL_INDEX") {
      return "Try DOWJONES, NIKKEI225, FTSE100, HANGSENG, SP500, GIFTNIFTY.";
    }
    if (exchange === "GLOBAL_INDICATOR") {
      return "Try USDINR, BZUSD (Brent), CLUSD (WTI).";
    }
    return "Try INFY, TCS, or part of a company name.";
  }, [exchange]);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
          <CardDescription>
            Searches the master contract for your active broker. Pick an exchange,
            then type a symbol or part of one. Need help with the format? Switch
            to the Format Guide tab above.
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
                  "focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                {filterExchanges(EXCHANGES).map((e) => (
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
              Failed to search symbols. Make sure the master contract has been
              downloaded for your broker.
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
                    <TableRow
                      key={`${row.token ?? row.symbol}-${i}`}
                      className={i % 2 === 0 ? "bg-muted/30" : ""}
                    >
                      <TableCell className="font-medium">{row.symbol}</TableCell>
                      <TableCell
                        className="max-w-[240px] truncate"
                        title={row.name ?? undefined}
                      >
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
                      <TableCell className="whitespace-nowrap">
                        {row.expiry ?? "—"}
                      </TableCell>
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
              {enabled
                ? "No symbols matched your query."
                : "Enter a symbol to begin searching."}
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Format Guide — tutorial content + interactive builder
// ---------------------------------------------------------------------------

const MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

function isoToExpiryToken(iso: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  if (!y || !m || !d) return "";
  const month = MONTHS[parseInt(m, 10) - 1];
  if (!month) return "";
  return `${d.padStart(2, "0")}${month}${y.slice(-2)}`;
}

function normaliseStrike(s: string): string {
  if (!s) return "";
  const n = Number(s);
  if (!Number.isFinite(n)) return s;
  // Drop trailing .0 but keep meaningful decimals
  return Number.isInteger(n) ? String(n) : String(n).replace(/0+$/, "").replace(/\.$/, "");
}

interface ExampleRow {
  symbol: string;
  exchange: string;
  label: string;
}

const EQUITY_EXAMPLES: ExampleRow[] = [
  { symbol: "INFY", exchange: "NSE", label: "Infosys" },
  { symbol: "TATAMOTORS", exchange: "BSE", label: "Tata Motors (BSE)" },
  { symbol: "SBIN", exchange: "NSE", label: "State Bank of India" },
  { symbol: "RELIANCE", exchange: "NSE", label: "Reliance Industries" },
];

const FUTURES_EXAMPLES: ExampleRow[] = [
  { symbol: "BANKNIFTY30APR26FUT", exchange: "NFO", label: "Bank Nifty Apr 2026 future" },
  { symbol: "SENSEX30APR26FUT", exchange: "BFO", label: "SENSEX Apr 2026 future" },
  { symbol: "USDINR28MAY26FUT", exchange: "CDS", label: "USDINR May 2026 future" },
  { symbol: "CRUDEOIL19MAY26FUT", exchange: "MCX", label: "Crude Oil May 2026 future" },
];

const OPTIONS_EXAMPLES: ExampleRow[] = [
  { symbol: "NIFTY28APR2624250CE", exchange: "NFO", label: "Nifty 24,250 Call · 28-Apr-2026" },
  { symbol: "VEDL30APR26292.5CE", exchange: "NFO", label: "Vedanta 292.5 Call · 30-Apr-2026" },
  { symbol: "USDINR28MAY2684CE", exchange: "CDS", label: "USDINR 84 Call · 28-May-2026" },
  { symbol: "CRUDEOIL19MAY266750CE", exchange: "MCX", label: "Crude Oil 6,750 Call · 19-May-2026" },
];

const NSE_INDICES = [
  "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX",
  "NIFTYNXT50", "NIFTY100", "NIFTY200", "NIFTY500",
  "NIFTYIT", "NIFTYAUTO", "NIFTYBANK", "NIFTYFMCG", "NIFTYPHARMA",
  "NIFTYMETAL", "NIFTYENERGY", "NIFTYREALTY",
];

const BSE_INDICES = [
  "SENSEX", "BANKEX", "SENSEX50", "BSE100", "BSE200", "BSE500",
  "BSEAUTO", "BSEPSU", "BSEMETAL", "BSEPOWER", "BSEMIDCAP", "BSESMALLCAP",
];

const EXCHANGE_CODES: Array<{ code: string; use: string; tradable: boolean }> = [
  { code: "NSE", use: "Cash equities (NSE)", tradable: true },
  { code: "BSE", use: "Cash equities (BSE)", tradable: true },
  { code: "NFO", use: "F&O on NSE", tradable: true },
  { code: "BFO", use: "F&O on BSE", tradable: true },
  { code: "CDS", use: "Currency derivatives (NSE)", tradable: true },
  { code: "BCD", use: "Currency derivatives (BSE)", tradable: true },
  { code: "MCX", use: "Commodities (multi-commodity exchange)", tradable: true },
  { code: "NCDEX", use: "Agri commodities (NCDEX)", tradable: true },
  { code: "NSE_INDEX", use: "NSE index quote/history feeds", tradable: false },
  { code: "BSE_INDEX", use: "BSE index quote/history feeds", tradable: false },
  { code: "MCX_INDEX", use: "MCX index quote/history feeds", tradable: false },
];

// ---------------------------------------------------------------------------
// Visual language — one consistent color per symbol segment, reused across
// the anatomy hero, the format cards, the builder, and the examples so a
// trader's eye learns the format once and reads everything else faster.
// ---------------------------------------------------------------------------

type Role = "under" | "expiry" | "strike" | "ce" | "pe" | "fut" | "exch";

const ROLE_CLASS: Record<Role, string> = {
  under:
    "bg-foreground text-background ring-foreground/30 dark:bg-foreground dark:text-background",
  expiry:
    "bg-amber-500/15 text-amber-700 ring-amber-500/40 dark:text-amber-300",
  strike:
    "bg-emerald-500/15 text-emerald-700 ring-emerald-500/40 dark:text-emerald-300",
  ce:
    "bg-emerald-500 text-white ring-emerald-500/60",
  pe:
    "bg-rose-500 text-white ring-rose-500/60",
  fut:
    "bg-indigo-500 text-white ring-indigo-500/60",
  exch:
    "bg-sky-500/15 text-sky-700 ring-sky-500/40 dark:text-sky-300",
};

const ROLE_DOT: Record<Role, string> = {
  under: "bg-foreground",
  expiry: "bg-amber-500",
  strike: "bg-emerald-500",
  ce: "bg-emerald-500",
  pe: "bg-rose-500",
  fut: "bg-indigo-500",
  exch: "bg-sky-500",
};

function RoleChip({
  role,
  children,
  size = "md",
}: {
  role: Role;
  children: React.ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md font-mono font-semibold tracking-tight ring-1 ring-inset",
        size === "lg" && "px-3 py-1.5 text-base sm:text-lg",
        size === "md" && "px-2 py-0.5 text-[13px]",
        size === "sm" && "px-1.5 py-0.5 text-[11px]",
        ROLE_CLASS[role],
      )}
    >
      {children}
    </span>
  );
}

function RoleLabel({
  role,
  label,
}: {
  role: Role;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={cn("inline-block h-1.5 w-1.5 rounded-full", ROLE_DOT[role])}
        aria-hidden
      />
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Anatomy — exploded view of an options symbol with labels below each segment.
// This is the "aha" moment for new traders — one image teaches the format.
// ---------------------------------------------------------------------------

function Anatomy() {
  const segments: Array<{ role: Role; text: string; label: string; hint: string }> = [
    { role: "under", text: "NIFTY", label: "Underlying", hint: "Index or stock symbol" },
    { role: "expiry", text: "28APR26", label: "Expiry", hint: "DDMMMYY uppercase" },
    { role: "strike", text: "24250", label: "Strike", hint: "Actual price · decimals OK" },
    { role: "ce", text: "CE", label: "Call / Put", hint: "CE for call · PE for put" },
  ];

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle>The anatomy of an OpenBull symbol</CardTitle>
        <CardDescription>
          One canonical string per instrument — identical across every broker.
          Read it left-to-right: who, when, where, which.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {/* The whole symbol, large */}
        <div className="rounded-xl border border-border bg-muted/20 p-5 sm:p-7">
          {/* Stacked chips with labels below */}
          <div className="flex flex-wrap items-end gap-x-1.5 gap-y-4 sm:gap-x-2">
            {segments.map((s) => (
              <div key={s.role} className="flex min-w-0 flex-col items-center gap-2">
                <RoleChip role={s.role} size="lg">
                  {s.text}
                </RoleChip>
                <div className="flex flex-col items-center">
                  <RoleLabel role={s.role} label={s.label} />
                  <span className="mt-0.5 text-[11px] text-muted-foreground">
                    {s.hint}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Joined readout */}
          <div className="mt-6 border-t border-border/60 pt-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              Concatenated symbol
            </p>
            <p className="mt-1 font-mono text-xl font-bold tracking-tight sm:text-2xl">
              NIFTY28APR2624250CE
            </p>
            <p className="mt-1 text-[12px] text-muted-foreground">
              Trade on exchange{" "}
              <RoleChip role="exch" size="sm">
                NFO
              </RoleChip>
              {" "}— that's where Nifty options live.
            </p>
          </div>
        </div>

        {/* Mini-legend */}
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-[12px]">
          <RoleLabel role="under" label="Underlying" />
          <RoleLabel role="expiry" label="Expiry" />
          <RoleLabel role="strike" label="Strike" />
          <RoleLabel role="ce" label="CE (call)" />
          <RoleLabel role="pe" label="PE (put)" />
          <RoleLabel role="fut" label="FUT" />
          <RoleLabel role="exch" label="Exchange" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Pattern cards — three side-by-side templates using the same color tokens.
// ---------------------------------------------------------------------------

function PatternCards() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Three patterns cover everything</CardTitle>
        <CardDescription>
          Equity, futures, options. Whichever you're trading, the structure is
          fixed — same parts in the same order.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <PatternCard
            label="Equity"
            chips={[{ role: "under", text: "UNDERLYING" }]}
            example={[{ role: "under", text: "INFY" }]}
            joined="INFY"
            note="Just the base ticker. Use the exchange field (NSE / BSE) to pick the venue."
          />
          <PatternCard
            label="Futures"
            chips={[
              { role: "under", text: "UNDERLYING" },
              { role: "expiry", text: "DDMMMYY" },
              { role: "fut", text: "FUT" },
            ]}
            example={[
              { role: "under", text: "BANKNIFTY" },
              { role: "expiry", text: "30APR26" },
              { role: "fut", text: "FUT" },
            ]}
            joined="BANKNIFTY30APR26FUT"
            note="Expiry is DD-MMM-YY uppercase. No spaces, no hyphens, no slashes."
          />
          <PatternCard
            label="Options"
            chips={[
              { role: "under", text: "UNDERLYING" },
              { role: "expiry", text: "DDMMMYY" },
              { role: "strike", text: "STRIKE" },
              { role: "ce", text: "CE / PE" },
            ]}
            example={[
              { role: "under", text: "NIFTY" },
              { role: "expiry", text: "28APR26" },
              { role: "strike", text: "24250" },
              { role: "ce", text: "CE" },
            ]}
            joined="NIFTY28APR2624250CE"
            note="Strike is the actual price (292.5 is fine). CE = call, PE = put."
          />
        </div>
      </CardContent>
    </Card>
  );
}

function PatternCard({
  label,
  chips,
  example,
  joined,
  note,
}: {
  label: string;
  chips: Array<{ role: Role; text: string }>;
  example: Array<{ role: Role; text: string }>;
  joined: string;
  note: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/20 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>

      {/* Pattern row */}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {chips.map((c, i) => (
          <span key={i} className="inline-flex items-center">
            <RoleChip role={c.role} size="sm">
              {c.text}
            </RoleChip>
            {i < chips.length - 1 && (
              <span className="mx-0.5 text-muted-foreground/50">+</span>
            )}
          </span>
        ))}
      </div>

      {/* Worked example */}
      <p className="mt-3 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground/70">
        Example
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {example.map((c, i) => (
          <RoleChip key={i} role={c.role} size="sm">
            {c.text}
          </RoleChip>
        ))}
      </div>
      <p className="mt-2 font-mono text-[13px] font-semibold tracking-tight text-foreground">
        {joined}
      </p>
      <p className="mt-2 text-[11px] leading-snug text-muted-foreground">{note}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FormatGuide — top-level layout
// ---------------------------------------------------------------------------

function FormatGuide({
  onTryExample,
}: {
  onTryExample: (symbol: string, exchange: string) => void;
}) {
  return (
    <>
      <Anatomy />
      <PatternCards />
      <SymbolBuilder onTryExample={onTryExample} />

      <ExampleTable
        title="Equity examples"
        description="Cash equities. The base ticker is the OpenBull symbol — pick NSE or BSE in the exchange field."
        rows={EQUITY_EXAMPLES}
        kind="equity"
        onTry={onTryExample}
      />
      <ExampleTable
        title="Futures examples"
        description="Underlying + DDMMMYY (uppercase) + FUT. Trade on NFO / BFO / CDS / MCX."
        rows={FUTURES_EXAMPLES}
        kind="futures"
        onTry={onTryExample}
      />
      <ExampleTable
        title="Options examples"
        description="Underlying + DDMMMYY + strike + CE/PE. Decimal strikes are valid (e.g. 292.5)."
        rows={OPTIONS_EXAMPLES}
        kind="options"
        onTry={onTryExample}
      />

      <Card>
        <CardHeader>
          <CardTitle>Indices — non-tradable feeds</CardTitle>
          <CardDescription>
            Spot-only. Use these as the <code>underlying</code> for option
            services, or to stream live index quotes. Click any chip to verify
            it's in your broker's master contract.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <IndexChipGroup
            label="NSE_INDEX"
            items={NSE_INDICES}
            onTry={(sym) => onTryExample(sym, "NSE_INDEX")}
          />
          <IndexChipGroup
            label="BSE_INDEX"
            items={BSE_INDICES}
            onTry={(sym) => onTryExample(sym, "BSE_INDEX")}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Exchange codes</CardTitle>
          <CardDescription>
            The <code>exchange</code> field on every API call. Feed-only venues
            can be streamed but not traded.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[140px]">Code</TableHead>
                  <TableHead>Use</TableHead>
                  <TableHead className="w-[110px]">Tradable</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {EXCHANGE_CODES.map((c) => (
                  <TableRow key={c.code}>
                    <TableCell>
                      <RoleChip role="exch" size="sm">
                        {c.code}
                      </RoleChip>
                    </TableCell>
                    <TableCell className="text-sm">{c.use}</TableCell>
                    <TableCell>
                      {c.tradable ? (
                        <Badge
                          variant="outline"
                          className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                        >
                          Yes
                        </Badge>
                      ) : (
                        <Badge
                          variant="outline"
                          className="border-muted-foreground/30 text-muted-foreground"
                        >
                          Feed only
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <DosAndDonts />
    </>
  );
}

// ---------------------------------------------------------------------------
// Do / Don't — side-by-side pitfalls
// ---------------------------------------------------------------------------

function DosAndDonts() {
  const items: Array<{ wrong: string; right: string; why: string }> = [
    {
      wrong: "NIFTY 28-Apr-26 24250 CE",
      right: "NIFTY28APR2624250CE",
      why: "No spaces, no hyphens, expiry in uppercase DDMMMYY.",
    },
    {
      wrong: "VEDL30APR26292.50CE",
      right: "VEDL30APR26292.5CE",
      why: "Drop trailing zeros on decimal strikes.",
    },
    {
      wrong: "NIFTY-FUT-APR26",
      right: "NIFTY30APR26FUT",
      why: "Futures keep the same DDMMMYY token, with literal FUT at the end.",
    },
    {
      wrong: "Searching INFY on NFO",
      right: "Searching INFY on NSE",
      why: "Cash equities live on NSE / BSE — F&O lives on NFO / BFO.",
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Do / Don't</CardTitle>
        <CardDescription>
          Things that bite people the first time. Two side-by-side examples; the
          difference is exactly what you need to remember.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3">
          {items.map((it, i) => (
            <div
              key={i}
              className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-muted/20 p-3 md:grid-cols-[1fr_1fr_2fr]"
            >
              <div className="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-rose-700 dark:text-rose-300">
                  Don't
                </p>
                <p className="mt-0.5 break-all font-mono text-[12px] text-rose-700 line-through decoration-rose-500/60 dark:text-rose-300">
                  {it.wrong}
                </p>
              </div>
              <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-300">
                  Do
                </p>
                <p className="mt-0.5 break-all font-mono text-[12px] font-semibold text-emerald-700 dark:text-emerald-300">
                  {it.right}
                </p>
              </div>
              <p className="self-center px-1 text-[12px] text-muted-foreground">
                {it.why}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function Token({
  children,
  literal,
}: {
  children: React.ReactNode;
  literal?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-1.5 py-0.5 font-mono text-[11px] ring-1 ring-inset",
        literal
          ? "bg-foreground/10 text-foreground ring-foreground/20"
          : "bg-muted text-muted-foreground ring-border",
      )}
    >
      {children}
    </span>
  );
}

// Split an OpenBull symbol into role-tagged segments so the example tables
// can render the same colour-coded breakdown used in the anatomy hero.
// Falls back to a single neutral chip if the symbol doesn't match the
// expected shape — useful for indices, non-standard tickers, etc.
const FUT_RE = /^([A-Z0-9.&]+?)(\d{2}[A-Z]{3}\d{2})FUT$/;
const OPT_RE = /^([A-Z0-9.&]+?)(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$/;

function splitSymbol(
  symbol: string,
  kind: "equity" | "futures" | "options",
): Array<{ role: Role; text: string }> {
  if (kind === "equity") return [{ role: "under", text: symbol }];
  if (kind === "futures") {
    const m = symbol.match(FUT_RE);
    if (!m) return [{ role: "under", text: symbol }];
    return [
      { role: "under", text: m[1] },
      { role: "expiry", text: m[2] },
      { role: "fut", text: "FUT" },
    ];
  }
  const m = symbol.match(OPT_RE);
  if (!m) return [{ role: "under", text: symbol }];
  return [
    { role: "under", text: m[1] },
    { role: "expiry", text: m[2] },
    { role: "strike", text: m[3] },
    { role: m[4] === "CE" ? "ce" : "pe", text: m[4] },
  ];
}

function ExampleTable({
  title,
  description,
  rows,
  kind,
  onTry,
}: {
  title: string;
  description: string;
  rows: ExampleRow[];
  kind: "equity" | "futures" | "options";
  onTry: (symbol: string, exchange: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Instrument</TableHead>
                <TableHead>OpenBull symbol</TableHead>
                <TableHead className="w-[100px]">Exchange</TableHead>
                <TableHead className="w-[180px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const parts = splitSymbol(r.symbol, kind);
                return (
                  <TableRow key={r.symbol}>
                    <TableCell className="text-sm">{r.label}</TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1.5">
                        <code className="font-mono text-[12px] font-semibold tracking-tight">
                          {r.symbol}
                        </code>
                        <div className="flex flex-wrap items-center gap-1">
                          {parts.map((p, i) => (
                            <RoleChip key={i} role={p.role} size="sm">
                              {p.text}
                            </RoleChip>
                          ))}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <RoleChip role="exch" size="sm">
                        {r.exchange}
                      </RoleChip>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="inline-flex gap-2">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => {
                            navigator.clipboard
                              ?.writeText(r.symbol)
                              .then(() => toast.success("Copied symbol"))
                              .catch(() => toast.error("Copy failed"));
                          }}
                        >
                          Copy
                        </Button>
                        <Button
                          size="xs"
                          onClick={() => onTry(r.symbol, r.exchange)}
                        >
                          Search this
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

function IndexChipGroup({
  label,
  items,
  onTry,
}: {
  label: string;
  items: string[];
  onTry: (sym: string) => void;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          Exchange
        </span>
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
          {label}
        </code>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((sym) => (
          <button
            key={sym}
            type="button"
            onClick={() => onTry(sym)}
            className={cn(
              "inline-flex items-center rounded-md border border-border bg-muted/30 px-2 py-1 font-mono text-[11px] transition-colors",
              "hover:border-foreground/30 hover:bg-muted",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
            title={`Search ${sym} on ${label}`}
          >
            {sym}
          </button>
        ))}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Symbol builder (interactive)
// ---------------------------------------------------------------------------

type Kind = "equity" | "futures" | "options";

function SymbolBuilder({
  onTryExample,
}: {
  onTryExample: (symbol: string, exchange: string) => void;
}) {
  const [kind, setKind] = useState<Kind>("options");
  const [base, setBase] = useState("NIFTY");
  const [expiry, setExpiry] = useState(""); // YYYY-MM-DD
  const [strike, setStrike] = useState("24250");
  const [optionType, setOptionType] = useState<"CE" | "PE">("CE");
  const [exchange, setExchange] = useState("NFO");

  // When the user flips between kinds, pick a sane default exchange so the
  // "Search this" handoff lands on the right master-contract slice.
  useEffect(() => {
    if (kind === "equity") setExchange("NSE");
    else setExchange("NFO");
  }, [kind]);

  const expiryToken = isoToExpiryToken(expiry);
  const strikeToken = normaliseStrike(strike);
  const baseUpper = base.trim().toUpperCase();

  const built = useMemo(() => {
    if (!baseUpper) return "";
    if (kind === "equity") return baseUpper;
    if (kind === "futures") {
      if (!expiryToken) return "";
      return `${baseUpper}${expiryToken}FUT`;
    }
    // options
    if (!expiryToken || !strikeToken) return "";
    return `${baseUpper}${expiryToken}${strikeToken}${optionType}`;
  }, [baseUpper, kind, expiryToken, strikeToken, optionType]);

  const handleCopy = () => {
    if (!built) return;
    navigator.clipboard
      ?.writeText(built)
      .then(() => toast.success("Copied symbol"))
      .catch(() => toast.error("Copy failed"));
  };

  const handleTry = () => {
    if (!built) return;
    onTryExample(built, exchange);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Symbol builder</CardTitle>
        <CardDescription>
          Pick the parts, see the symbol assemble live. Hit "Search this" to
          confirm it exists in the master contract.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Kind picker */}
        <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
          {(["equity", "futures", "options"] as const).map((k) => {
            const active = kind === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={cn(
                  "inline-flex h-7 items-center rounded-md px-3 text-[12px] font-medium capitalize transition-colors",
                  active
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {k}
              </button>
            );
          })}
        </div>

        {/* Inputs */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="space-y-1 lg:col-span-1">
            <Label htmlFor="b-base">Base</Label>
            <Input
              id="b-base"
              value={base}
              onChange={(e) => setBase(e.target.value)}
              placeholder={kind === "equity" ? "INFY" : "NIFTY"}
              className="uppercase"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          {kind !== "equity" && (
            <div className="space-y-1 lg:col-span-1">
              <Label htmlFor="b-expiry">Expiry</Label>
              <Input
                id="b-expiry"
                type="date"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </div>
          )}

          {kind === "options" && (
            <>
              <div className="space-y-1 lg:col-span-1">
                <Label htmlFor="b-strike">Strike</Label>
                <Input
                  id="b-strike"
                  type="text"
                  inputMode="decimal"
                  value={strike}
                  onChange={(e) => setStrike(e.target.value)}
                  placeholder="24250"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1 lg:col-span-1">
                <Label>Option type</Label>
                <div className="inline-flex h-9 rounded-md border border-input bg-background p-0.5">
                  {(["CE", "PE"] as const).map((t) => {
                    const active = optionType === t;
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setOptionType(t)}
                        className={cn(
                          "inline-flex h-8 w-12 items-center justify-center rounded text-[12px] font-semibold transition-colors",
                          active
                            ? t === "CE"
                              ? "bg-emerald-500 text-white shadow-sm"
                              : "bg-rose-500 text-white shadow-sm"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {t}
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          )}

          <div className="space-y-1 lg:col-span-1">
            <Label htmlFor="b-exch">Exchange</Label>
            <select
              id="b-exch"
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              {EXCHANGES.map((e) => (
                <option key={e.value} value={e.value}>
                  {e.value}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Live preview */}
        <div className="rounded-lg border border-border bg-background p-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Symbol preview
          </p>
          <div className="mt-2 flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p
              className={cn(
                "font-mono text-lg font-bold tracking-tight sm:text-xl",
                built ? "text-foreground" : "text-muted-foreground/60",
              )}
            >
              {built || "Fill in the fields above…"}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleCopy}
                disabled={!built}
              >
                Copy
              </Button>
              <Button size="sm" onClick={handleTry} disabled={!built}>
                Search this →
              </Button>
            </div>
          </div>
          {built && kind !== "equity" && (
            <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
              <Token>{baseUpper}</Token>
              <Token>{expiryToken}</Token>
              {kind === "options" && <Token>{strikeToken}</Token>}
              {kind === "options" && <Token literal>{optionType}</Token>}
              {kind === "futures" && <Token literal>FUT</Token>}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
