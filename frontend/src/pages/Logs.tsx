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
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  buildExportUrl,
  getApiLogStats,
  listApiLogs,
  type ApiLogRow,
  type ListApiLogsParams,
} from "@/api/logs";
import { cn } from "@/lib/utils";

const METHODS = ["", "GET", "POST", "PUT", "PATCH", "DELETE"] as const;
const STATUS_CLASSES = ["", "2xx", "3xx", "4xx", "5xx"] as const;

type Filters = {
  method: string;
  status_class: string;
  path_contains: string;
  start: string;
  end: string;
};

function statusBadge(code: number): "default" | "secondary" | "destructive" | "outline" {
  if (code >= 500) return "destructive";
  if (code >= 400) return "destructive";
  if (code >= 300) return "secondary";
  if (code >= 200) return "default";
  return "outline";
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
  const d = new Date(iso);
  return d.toLocaleString();
}

export default function Logs() {
  const [filters, setFilters] = useState<Filters>({
    method: "",
    status_class: "",
    path_contains: "",
    start: "",
    end: "",
  });
  const [cursor, setCursor] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const queryParams: ListApiLogsParams = useMemo(
    () => ({
      limit: 100,
      before_id: cursor ?? undefined,
      method: filters.method || undefined,
      status_class: (filters.status_class || undefined) as ListApiLogsParams["status_class"],
      path_contains: filters.path_contains || undefined,
      start: filters.start ? new Date(filters.start).toISOString() : undefined,
      end: filters.end ? new Date(filters.end).toISOString() : undefined,
    }),
    [filters, cursor]
  );

  const logsQuery = useQuery({
    queryKey: ["api-logs", queryParams],
    queryFn: () => listApiLogs(queryParams),
    refetchInterval: autoRefresh ? 5000 : false,
    refetchIntervalInBackground: false,
  });

  const statsQuery = useQuery({
    queryKey: ["api-logs-stats", filters.start, filters.end],
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
      setCursor(null); // reset pagination when filters change
      setExpandedId(null);
    },
    []
  );

  const resetFilters = useCallback(() => {
    setFilters({
      method: "",
      status_class: "",
      path_contains: "",
      start: "",
      end: "",
    });
    setCursor(null);
  }, []);

  const exportUrl = useMemo(() => {
    const { limit: _l, before_id: _b, ...rest } = queryParams;
    return buildExportUrl(rest);
  }, [queryParams]);

  const items = logsQuery.data?.items ?? [];
  const nextCursor = logsQuery.data?.next_cursor ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">API Logs</h1>
        <p className="text-sm text-muted-foreground">
          One row per <em>authenticated</em> HTTP request. Unauthenticated traffic
          (attacker floods, expired cookies, invalid API keys) is discarded at the
          middleware and never reaches the DB. Oldest rows are trimmed once the
          table exceeds the configured cap.
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Total" value={statsQuery.data?.total ?? "—"} />
        <StatCard
          label="2xx"
          value={statsQuery.data?.ok_2xx ?? "—"}
          tone="good"
        />
        <StatCard
          label="4xx"
          value={statsQuery.data?.client_errors_4xx ?? "—"}
          tone="warn"
        />
        <StatCard
          label="5xx"
          value={statsQuery.data?.server_errors_5xx ?? "—"}
          tone="bad"
        />
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>
            Cursor-paginated by id. Changing any filter resets the cursor.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
            <div className="space-y-1">
              <Label htmlFor="log-method">Method</Label>
              <select
                id="log-method"
                value={filters.method}
                onChange={(e) => setFilter("method", e.target.value)}
                className={cn(
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {METHODS.map((m) => (
                  <option key={m || "any"} value={m}>
                    {m || "Any"}
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
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {STATUS_CLASSES.map((s) => (
                  <option key={s || "any"} value={s}>
                    {s || "Any"}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1 lg:col-span-2">
              <Label htmlFor="log-path">Path contains</Label>
              <Input
                id="log-path"
                value={filters.path_contains}
                onChange={(e) => setFilter("path_contains", e.target.value)}
                placeholder="/api/v1/placeorder"
                autoComplete="off"
              />
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
            <Button
              variant={autoRefresh ? "default" : "outline"}
              size="sm"
              onClick={() => setAutoRefresh((v) => !v)}
            >
              {autoRefresh ? "Auto-refresh: on" : "Auto-refresh: off"}
            </Button>
            <a
              href={exportUrl}
              className={cn(
                "inline-flex h-9 items-center rounded-md border border-input bg-background px-3 text-sm font-medium",
                "hover:bg-accent hover:text-accent-foreground"
              )}
            >
              Export CSV
            </a>
            {logsQuery.isFetching && (
              <span className="text-xs text-muted-foreground">Loading…</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Requests</CardTitle>
            <CardDescription>
              Click any row to inspect the full request + response bodies.
            </CardDescription>
          </div>
          <div className="text-xs text-muted-foreground">
            {items.length} row{items.length === 1 ? "" : "s"}
            {cursor !== null && " · paginated"}
          </div>
        </CardHeader>
        <CardContent>
          {logsQuery.error ? (
            <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              Failed to load logs.
            </p>
          ) : items.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {logsQuery.isLoading
                ? "Loading…"
                : "No logs match these filters yet."}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[170px]">Time</TableHead>
                    <TableHead className="w-[90px]">Method</TableHead>
                    <TableHead>Path</TableHead>
                    <TableHead className="w-[90px] text-right">Status</TableHead>
                    <TableHead className="w-[90px] text-right">Latency</TableHead>
                    <TableHead className="w-[80px] text-right">User</TableHead>
                    <TableHead className="w-[90px]">Auth</TableHead>
                    <TableHead className="w-[110px]">IP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((r) => (
                    <LogRowBlock
                      key={r.id}
                      row={r}
                      expanded={expandedId === r.id}
                      onToggle={() => setExpandedId((cur) => (cur === r.id ? null : r.id))}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
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
        </CardContent>
      </Card>
    </div>
  );
}

function LogRowBlock({
  row,
  expanded,
  onToggle,
}: {
  row: ApiLogRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <TableRow className="cursor-pointer hover:bg-muted/50" onClick={onToggle}>
        <TableCell className="whitespace-nowrap text-xs">
          {fmtTime(row.created_at)}
        </TableCell>
        <TableCell>
          <Badge variant="outline">{row.method}</Badge>
        </TableCell>
        <TableCell className="max-w-[400px] truncate font-mono text-xs" title={row.path}>
          {row.path}
        </TableCell>
        <TableCell className="text-right">
          <Badge variant={statusBadge(row.status_code)}>{row.status_code}</Badge>
        </TableCell>
        <TableCell className="text-right font-mono text-xs">
          {fmtMs(row.duration_ms)}
        </TableCell>
        <TableCell className="text-right font-mono text-xs">
          {row.user_id ?? "—"}
        </TableCell>
        <TableCell className="text-xs">
          <Badge variant="secondary">{row.auth_method ?? "—"}</Badge>
        </TableCell>
        <TableCell className="font-mono text-xs">{row.client_ip ?? "—"}</TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={8} className="bg-muted/30 p-0">
            <div className="grid grid-cols-1 gap-3 p-4 lg:grid-cols-2">
              <DetailBlock title="Request" content={prettyJson(row.request_body)} />
              <DetailBlock title="Response" content={prettyJson(row.response_body)} />
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 border-t border-border/50 bg-muted/20 p-4 text-xs md:grid-cols-4">
              <KV label="Request ID" value={row.request_id ?? "—"} />
              <KV label="Log ID" value={String(row.id)} />
              <KV label="User-Agent" value={row.user_agent ?? "—"} />
              <KV label="Error" value={row.error ?? "—"} tone={row.error ? "bad" : undefined} />
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function DetailBlock({ title, content }: { title: string; content: string }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
        {content && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => navigator.clipboard?.writeText(content)}
          >
            Copy
          </button>
        )}
      </div>
      <pre className="max-h-80 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-xs">
        {content || <span className="text-muted-foreground">(empty)</span>}
      </pre>
    </div>
  );
}

function KV({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "bad";
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "break-all font-mono",
          tone === "bad" && "text-red-600"
        )}
      >
        {value}
      </p>
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
    <div className="rounded-md border border-border bg-card p-3">
      <p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-2xl font-semibold",
          tone === "good" && "text-green-600",
          tone === "warn" && "text-amber-600",
          tone === "bad" && "text-red-600"
        )}
      >
        {value}
      </p>
    </div>
  );
}
