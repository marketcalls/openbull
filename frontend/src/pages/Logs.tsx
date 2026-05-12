import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { JsonEditor } from "@/components/ui/json-editor";
import {
  buildExportUrl,
  getApiLogStats,
  listApiLogs,
  type ApiLogRow,
  type ListApiLogsParams,
} from "@/api/logs";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants / helpers
// ---------------------------------------------------------------------------

const API_TYPES = [
  "",
  "placeorder",
  "placesmartorder",
  "modifyorder",
  "cancelorder",
  "cancelallorder",
  "closeposition",
  "basketorder",
  "splitorder",
  "optionsorder",
  "optionsmultiorder",
] as const;

const STATUS_CLASSES = ["", "2xx", "4xx", "5xx"] as const;
const MODES = ["", "live", "sandbox"] as const;

type Filters = {
  api_type: string;
  mode: string;
  status_class: string;
  search: string;
  start: string;
  end: string;
};

function apiTypeFromPath(path: string): string {
  const tail = path.split("/").filter(Boolean).pop();
  return tail || path;
}

function apiTypeTone(apiType: string): string {
  switch (apiType) {
    case "placeorder":
      return "bg-blue-500/15 text-blue-700 ring-blue-500/30 dark:text-blue-300";
    case "placesmartorder":
      return "bg-purple-500/15 text-purple-700 ring-purple-500/30 dark:text-purple-300";
    case "modifyorder":
      return "bg-amber-500/15 text-amber-700 ring-amber-500/30 dark:text-amber-300";
    case "cancelorder":
    case "cancelallorder":
      return "bg-rose-500/15 text-rose-700 ring-rose-500/30 dark:text-rose-300";
    case "closeposition":
      return "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300";
    case "basketorder":
    case "splitorder":
      return "bg-indigo-500/15 text-indigo-700 ring-indigo-500/30 dark:text-indigo-300";
    case "optionsorder":
    case "optionsmultiorder":
      return "bg-cyan-500/15 text-cyan-700 ring-cyan-500/30 dark:text-cyan-300";
    default:
      return "bg-muted text-muted-foreground ring-border";
  }
}

function statusTone(code: number): string {
  if (code >= 500) return "bg-rose-500/15 text-rose-700 ring-rose-500/30 dark:text-rose-300";
  if (code >= 400) return "bg-amber-500/15 text-amber-700 ring-amber-500/30 dark:text-amber-300";
  if (code >= 200 && code < 300)
    return "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300";
  return "bg-muted text-muted-foreground ring-border";
}

function actionTone(action: string): string {
  return action.toUpperCase() === "BUY"
    ? "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300"
    : "bg-rose-500/15 text-rose-700 ring-rose-500/30 dark:text-rose-300";
}

function modeTone(mode: string | null): string {
  if (mode === "sandbox")
    return "bg-indigo-500/15 text-indigo-700 ring-indigo-500/30 dark:text-indigo-300";
  if (mode === "live")
    return "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300";
  return "bg-muted text-muted-foreground ring-border";
}

function parseJson(s: string | null): Record<string, unknown> {
  if (!s) return {};
  try {
    const v = JSON.parse(s);
    return typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function prettyJson(s: string | null): string {
  if (!s) return "";
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Logs() {
  const [filters, setFilters] = useState<Filters>({
    api_type: "",
    mode: "",
    status_class: "",
    search: "",
    start: "",
    end: "",
  });
  const [cursor, setCursor] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const queryParams: ListApiLogsParams = useMemo(
    () => ({
      limit: 50,
      before_id: cursor ?? undefined,
      mode: (filters.mode || undefined) as ListApiLogsParams["mode"],
      status_class: (filters.status_class || undefined) as ListApiLogsParams["status_class"],
      // api_type filter is enforced server-side by path_contains since the
      // backend already restricts to trade endpoints — passing the api_type
      // value as path_contains narrows further to a single endpoint.
      path_contains: filters.api_type || filters.search || undefined,
      start: filters.start ? new Date(filters.start).toISOString() : undefined,
      end: filters.end ? new Date(filters.end).toISOString() : undefined,
    }),
    [filters, cursor]
  );

  const logsQuery = useQuery({
    queryKey: ["trade-logs", queryParams],
    queryFn: () => listApiLogs(queryParams),
    refetchInterval: autoRefresh ? 5000 : false,
    refetchIntervalInBackground: false,
  });

  const statsQuery = useQuery({
    queryKey: ["trade-logs-stats", filters.start, filters.end],
    queryFn: () =>
      getApiLogStats(
        filters.start ? new Date(filters.start).toISOString() : undefined,
        filters.end ? new Date(filters.end).toISOString() : undefined
      ),
    refetchInterval: autoRefresh ? 10000 : false,
  });

  const setFilter = useCallback(
    <K extends keyof Filters>(key: K, value: Filters[K]) => {
      setFilters((prev) => ({ ...prev, [key]: value }));
      setCursor(null);
      setExpandedId(null);
    },
    []
  );

  const resetFilters = useCallback(() => {
    setFilters({
      api_type: "",
      mode: "",
      status_class: "",
      search: "",
      start: "",
      end: "",
    });
    setCursor(null);
  }, []);

  // Client-side search filter — narrows down by symbol / orderid in the
  // request body, on top of server-side filters. Lets the user grep without
  // a round-trip when the page is already loaded.
  const items = useMemo(() => {
    const rows = logsQuery.data?.items ?? [];
    const term = filters.search.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((r) => {
      const blob = `${r.path} ${r.request_body ?? ""} ${r.response_body ?? ""}`.toLowerCase();
      return blob.includes(term);
    });
  }, [logsQuery.data, filters.search]);

  const nextCursor = logsQuery.data?.next_cursor ?? null;

  const exportUrl = useMemo(() => {
    const { limit: _l, before_id: _b, ...rest } = queryParams;
    return buildExportUrl(rest);
  }, [queryParams]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Trade Logs
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Event-driven log of every authenticated order action — live and
            sandbox. API keys, passwords and tokens are redacted at the
            middleware before the row is written.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => logsQuery.refetch()}
            disabled={logsQuery.isFetching}
          >
            {logsQuery.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
          <Button
            variant={autoRefresh ? "default" : "outline"}
            size="sm"
            onClick={() => setAutoRefresh((v) => !v)}
            title="Toggle 5-second auto-refresh"
          >
            {autoRefresh ? "Live · on" : "Live · off"}
          </Button>
          <a
            href={exportUrl}
            className={cn(
              "inline-flex h-7 items-center rounded-md border border-input bg-background px-2.5 text-[0.8rem] font-medium",
              "hover:bg-muted hover:text-foreground"
            )}
          >
            Export CSV
          </a>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Total orders" value={statsQuery.data?.total ?? "—"} />
        <StatCard
          label="Accepted (2xx)"
          value={statsQuery.data?.ok_2xx ?? "—"}
          tone="good"
        />
        <StatCard
          label="Rejected (4xx)"
          value={statsQuery.data?.client_errors_4xx ?? "—"}
          tone="warn"
        />
        <StatCard
          label="Server errors (5xx)"
          value={statsQuery.data?.server_errors_5xx ?? "—"}
          tone="bad"
        />
      </div>

      {/* Filters */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Filters</CardTitle>
          <CardDescription className="text-[12px]">
            Filters apply server-side. Cursor resets when any field changes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
            <div className="space-y-1 lg:col-span-2">
              <Label htmlFor="log-search">Search</Label>
              <Input
                id="log-search"
                value={filters.search}
                onChange={(e) => setFilter("search", e.target.value)}
                placeholder="Symbol, order id, strategy…"
                autoComplete="off"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="log-apitype">API type</Label>
              <select
                id="log-apitype"
                value={filters.api_type}
                onChange={(e) => setFilter("api_type", e.target.value)}
                className={cn(
                  "flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {API_TYPES.map((a) => (
                  <option key={a || "any"} value={a}>
                    {a || "Any"}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <Label htmlFor="log-mode">Mode</Label>
              <select
                id="log-mode"
                value={filters.mode}
                onChange={(e) => setFilter("mode", e.target.value)}
                className={cn(
                  "flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {MODES.map((m) => (
                  <option key={m || "any"} value={m}>
                    {m === "" ? "Any" : m === "live" ? "Live" : "Sandbox"}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <Label htmlFor="log-status">Status</Label>
              <select
                id="log-status"
                value={filters.status_class}
                onChange={(e) => setFilter("status_class", e.target.value)}
                className={cn(
                  "flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {STATUS_CLASSES.map((s) => (
                  <option key={s || "any"} value={s}>
                    {s === "" ? "Any" : s === "2xx" ? "Accepted (2xx)" : s === "4xx" ? "Rejected (4xx)" : "Server (5xx)"}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <Label htmlFor="log-start">From</Label>
              <Input
                id="log-start"
                type="datetime-local"
                value={filters.start}
                onChange={(e) => setFilter("start", e.target.value)}
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="log-end">To</Label>
              <Input
                id="log-end"
                type="datetime-local"
                value={filters.end}
                onChange={(e) => setFilter("end", e.target.value)}
              />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={resetFilters}>
              Reset
            </Button>
            {logsQuery.isFetching && (
              <span className="text-xs text-muted-foreground">Loading…</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Logs */}
      <div className="space-y-3">
        {logsQuery.error ? (
          <Card>
            <CardContent className="p-6 text-sm text-destructive">
              Failed to load trade logs.
            </CardContent>
          </Card>
        ) : items.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <p className="text-sm font-semibold tracking-tight">
                No trade activity yet
              </p>
              <p className="mt-1 text-[12px] text-muted-foreground">
                Place an order to see it logged here. Adjust filters if you
                expected results.
              </p>
            </CardContent>
          </Card>
        ) : (
          items.map((r) => (
            <TradeLogRow
              key={r.id}
              row={r}
              expanded={expandedId === r.id}
              onToggle={() =>
                setExpandedId((cur) => (cur === r.id ? null : r.id))
              }
            />
          ))
        )}

        <div className="flex items-center justify-between pt-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCursor(null)}
            disabled={cursor === null}
          >
            Back to latest
          </Button>
          <Button
            size="sm"
            onClick={() => nextCursor && setCursor(nextCursor)}
            disabled={!nextCursor}
          >
            Load older
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function TradeLogRow({
  row,
  expanded,
  onToggle,
}: {
  row: ApiLogRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const req = parseJson(row.request_body);
  const apiType = apiTypeFromPath(row.path);
  const action = typeof req.action === "string" ? (req.action as string) : "";
  const symbol = typeof req.symbol === "string" ? (req.symbol as string) : "";
  const exchange = typeof req.exchange === "string" ? (req.exchange as string) : "";
  const strategy = typeof req.strategy === "string" ? (req.strategy as string) : "";
  const product = typeof req.product === "string" ? (req.product as string) : "";
  const pricetype = typeof req.pricetype === "string" ? (req.pricetype as string) : "";
  const quantity = req.quantity ?? "";
  const price = req.price ?? "";
  const triggerPrice = req.trigger_price ?? "";
  const orderId = typeof req.orderid === "string" ? (req.orderid as string) : "";

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-4 sm:p-5">
        {/* Top row: badges */}
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={apiTypeTone(apiType)} bold>
            {apiType}
          </Chip>
          {action && <Chip tone={actionTone(action)}>{action}</Chip>}
          {exchange && <Chip tone="bg-muted text-foreground ring-border">{exchange}</Chip>}
          {strategy && (
            <Chip tone="bg-muted text-foreground ring-border" subtle>
              <span className="text-muted-foreground">strategy</span> {strategy}
            </Chip>
          )}
          <Chip tone={statusTone(row.status_code)} bold>
            {row.status_code}
          </Chip>
          {row.mode && <Chip tone={modeTone(row.mode)}>{row.mode}</Chip>}
          <span className="ml-auto text-[11px] text-muted-foreground tabular-nums">
            {fmtTime(row.created_at)} · {fmtMs(row.duration_ms)}
          </span>
        </div>

        {/* Order detail tiles (only show what we have) */}
        {(symbol || quantity || price || product || pricetype || orderId) && (
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {symbol && <DetailTile label="Symbol" value={symbol} mono />}
            {quantity !== "" && (
              <DetailTile label="Quantity" value={String(quantity)} mono />
            )}
            {price !== "" && String(price) !== "0" && (
              <DetailTile label="Price" value={String(price)} mono />
            )}
            {triggerPrice !== "" && String(triggerPrice) !== "0" && (
              <DetailTile label="Trigger" value={String(triggerPrice)} mono />
            )}
            {product && <DetailTile label="Product" value={product} />}
            {pricetype && <DetailTile label="Type" value={pricetype} />}
            {orderId && <DetailTile label="Order ID" value={orderId} mono truncate />}
          </div>
        )}

        {/* Error line */}
        {row.error && (
          <div className="mt-3 rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-[12px] text-rose-700 dark:text-rose-300">
            <span className="font-semibold uppercase tracking-[0.1em]">
              Error
            </span>
            {" — "}
            {row.error}
          </div>
        )}

        {/* Toggle */}
        <button
          type="button"
          onClick={onToggle}
          className={cn(
            "mt-4 inline-flex w-full items-center justify-between rounded-md bg-muted/40 px-3 py-2 text-[12px] font-medium text-muted-foreground transition-colors",
            "hover:bg-muted hover:text-foreground"
          )}
          aria-expanded={expanded}
        >
          <span>Request &amp; response payload</span>
          <span className="text-[10px] tracking-[0.12em]">
            {expanded ? "HIDE" : "VIEW"}
          </span>
        </button>

        {expanded && (
          <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <PayloadBlock title="Request" content={prettyJson(row.request_body)} />
            <PayloadBlock title="Response" content={prettyJson(row.response_body)} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Small bits
// ---------------------------------------------------------------------------

function Chip({
  children,
  tone,
  bold,
  subtle,
}: {
  children: React.ReactNode;
  tone: string;
  bold?: boolean;
  subtle?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] uppercase tracking-[0.1em] ring-1 ring-inset",
        bold ? "font-semibold" : "font-medium",
        subtle && "normal-case tracking-normal",
        tone
      )}
    >
      {children}
    </span>
  );
}

function DetailTile({
  label,
  value,
  mono,
  truncate,
}: {
  label: string;
  value: string;
  mono?: boolean;
  truncate?: boolean;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
      <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 text-[13px] font-semibold tracking-tight",
          mono && "font-mono tabular-nums",
          truncate && "truncate"
        )}
        title={truncate ? value : undefined}
      >
        {value}
      </p>
    </div>
  );
}

function PayloadBlock({ title, content }: { title: string; content: string }) {
  // Auto-size by line count, cap at ~70vh
  const lines = content ? content.split("\n").length : 0;
  const maxH =
    typeof window !== "undefined" ? Math.floor(window.innerHeight * 0.7) : 600;
  const height = Math.min(Math.max(lines * 20 + 24, 120), maxH);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {title}
        </p>
        {content && (
          <button
            type="button"
            className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground hover:text-foreground"
            onClick={() => navigator.clipboard?.writeText(content)}
          >
            Copy
          </button>
        )}
      </div>
      <div
        className="rounded-md border border-border bg-card/50"
        style={{ height }}
      >
        <JsonEditor value={content || "{}"} readOnly lineWrapping={false} />
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-card px-3 py-3 sm:px-4 sm:py-3.5",
        tone === "good" && "border-emerald-500/30 bg-emerald-500/5",
        tone === "warn" && "border-amber-500/30 bg-amber-500/5",
        tone === "bad" && "border-rose-500/30 bg-rose-500/5"
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 text-xl font-bold tracking-tight tabular-nums sm:text-2xl",
          tone === "good" && "text-emerald-600 dark:text-emerald-400",
          tone === "warn" && "text-amber-600 dark:text-amber-400",
          tone === "bad" && "text-rose-600 dark:text-rose-400"
        )}
      >
        {value}
      </p>
    </div>
  );
}
