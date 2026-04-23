import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
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
import {
  getWebSocketApiKey,
  getWebSocketConfig,
  getWebSocketHealth,
} from "@/api/websocket";
import { EXCHANGES, type SymbolSearchResult } from "@/types/symbol";
import { cn } from "@/lib/utils";

type WsStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "authenticating"
  | "authenticated"
  | "error"
  | "closed";

type Mode = "LTP" | "QUOTE" | "DEPTH";

interface Subscription {
  symbol: string;
  exchange: string;
  mode: Mode;
}

interface DepthLevel {
  price?: number;
  quantity?: number;
  orders?: number;
}

interface Depth {
  buy?: DepthLevel[];
  sell?: DepthLevel[];
}

interface TickRow {
  symbol: string;
  exchange: string;
  mode: string;
  ltp?: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
  change?: number;
  change_percent?: number;
  depth?: Depth;
  last_update: number;
}

interface LogEntry {
  id: number;
  at: number;
  direction: "in" | "out" | "sys";
  text: string;
}

const MAX_LOG = 200;

function useDebounced<T>(value: T, delayMs = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return v;
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return String(Math.round(Number(value)));
}

function statusVariant(status: WsStatus): {
  label: string;
  dot: string;
  badge: "default" | "secondary" | "destructive" | "outline";
} {
  switch (status) {
    case "authenticated":
      return { label: "Authenticated", dot: "bg-green-500", badge: "default" };
    case "connected":
    case "authenticating":
      return {
        label: status === "connected" ? "Connected" : "Authenticating…",
        dot: "bg-amber-500",
        badge: "secondary",
      };
    case "connecting":
      return { label: "Connecting…", dot: "bg-amber-500", badge: "secondary" };
    case "error":
      return { label: "Error", dot: "bg-red-500", badge: "destructive" };
    case "closed":
      return { label: "Closed", dot: "bg-muted-foreground", badge: "outline" };
    default:
      return { label: "Idle", dot: "bg-muted-foreground", badge: "outline" };
  }
}

export default function WebSocketTest() {
  const [status, setStatus] = useState<WsStatus>("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [broker, setBroker] = useState<string>("");

  const [query, setQuery] = useState("");
  const [exchange, setExchange] = useState<string>("NSE");
  const [mode, setMode] = useState<Mode>("LTP");
  const debouncedQuery = useDebounced(query.trim(), 300);

  const [subs, setSubs] = useState<Subscription[]>([]);
  const [ticks, setTicks] = useState<Record<string, TickRow>>({});
  const [log, setLog] = useState<LogEntry[]>([]);
  const logIdRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);

  const symKey = (s: string, e: string) => `${e}:${s}`;

  const appendLog = useCallback(
    (direction: LogEntry["direction"], text: string) => {
      setLog((prev) => {
        const next: LogEntry = {
          id: ++logIdRef.current,
          at: Date.now(),
          direction,
          text,
        };
        const updated = [next, ...prev];
        return updated.slice(0, MAX_LOG);
      });
    },
    []
  );

  const send = useCallback(
    (payload: object) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const text = JSON.stringify(payload);
      ws.send(text);
      appendLog("out", text);
    },
    [appendLog]
  );

  const disconnect = useCallback(() => {
    const ws = wsRef.current;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    wsRef.current = null;
    setStatus("closed");
  }, []);

  const connect = useCallback(async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    setErrorMsg("");
    setStatus("connecting");
    appendLog("sys", "Fetching WS config & API key…");

    let url: string;
    let apiKey: string;
    try {
      const [cfg, key] = await Promise.all([
        getWebSocketConfig(),
        getWebSocketApiKey(),
      ]);
      url = cfg.websocket_url;
      apiKey = key;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to fetch config";
      setErrorMsg(msg);
      setStatus("error");
      appendLog("sys", `Config fetch failed: ${msg}`);
      return;
    }

    appendLog("sys", `Connecting to ${url}`);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("authenticating");
      appendLog("sys", "Socket open — sending authenticate");
      send({ action: "authenticate", api_key: apiKey });
    };

    ws.onmessage = (evt) => {
      appendLog("in", typeof evt.data === "string" ? evt.data : "[binary]");
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(evt.data as string);
      } catch {
        return;
      }

      if (msg.type === "auth") {
        if (msg.status === "success") {
          setStatus("authenticated");
          setBroker(typeof msg.broker === "string" ? msg.broker : "");
          reconnectAttempts.current = 0;
        } else {
          setStatus("error");
          setErrorMsg(String(msg.message ?? "Authentication failed"));
        }
        return;
      }

      if (msg.type === "market_data") {
        const symbol = String(msg.symbol ?? "");
        const exch = String(msg.exchange ?? "");
        const dmode = String(msg.mode ?? "").toLowerCase();
        const data = (msg.data as Record<string, unknown>) ?? {};
        const key = symKey(symbol, exch);

        setTicks((prev) => {
          const existing = prev[key] ?? {
            symbol,
            exchange: exch,
            mode: dmode,
            last_update: Date.now(),
          };
          const next: TickRow = {
            ...existing,
            mode: dmode,
            last_update: Date.now(),
          };

          if ("ltp" in data) next.ltp = Number(data.ltp);
          if ("open" in data) next.open = Number(data.open);
          if ("high" in data) next.high = Number(data.high);
          if ("low" in data) next.low = Number(data.low);
          if ("close" in data) next.close = Number(data.close);
          if ("volume" in data) next.volume = Number(data.volume);
          if ("change" in data) next.change = Number(data.change);
          if ("change_percent" in data) next.change_percent = Number(data.change_percent);

          const depth = data.depth as Depth | undefined;
          if (depth && (depth.buy || depth.sell)) {
            next.depth = depth;
          } else if (dmode === "depth") {
            const bids = data.bids as DepthLevel[] | undefined;
            const asks = data.asks as DepthLevel[] | undefined;
            if (bids || asks) next.depth = { buy: bids, sell: asks };
          }

          return { ...prev, [key]: next };
        });
      }
    };

    ws.onerror = () => {
      setStatus("error");
      setErrorMsg("WebSocket error — check console");
      appendLog("sys", "WebSocket error");
    };

    ws.onclose = (ev) => {
      setStatus("closed");
      appendLog("sys", `Socket closed (code=${ev.code})`);
      wsRef.current = null;
    };
  }, [appendLog, send]);

  // Auto-disconnect on unmount.
  useEffect(() => {
    return () => {
      const ws = wsRef.current;
      if (ws) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  // --- Symbol search dropdown ----
  const searchEnabled = debouncedQuery.length >= 1;
  const searchQuery = useQuery({
    queryKey: ["ws-test-symbol-search", exchange, debouncedQuery],
    queryFn: () => searchSymbols(debouncedQuery, exchange),
    enabled: searchEnabled,
    staleTime: 30_000,
  });

  const pickSymbol = useCallback((row: SymbolSearchResult) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setErrorMsg("Connect to the WebSocket before subscribing");
      return;
    }
    const sub: Subscription = { symbol: row.symbol, exchange: row.exchange, mode };
    setSubs((prev) =>
      prev.some((s) => s.symbol === sub.symbol && s.exchange === sub.exchange && s.mode === sub.mode)
        ? prev
        : [...prev, sub]
    );
    send({
      action: "subscribe",
      symbols: [{ symbol: row.symbol, exchange: row.exchange }],
      mode,
    });
    setQuery("");
  }, [mode, send]);

  const unsubscribeOne = useCallback((sub: Subscription) => {
    send({
      action: "unsubscribe",
      symbols: [{ symbol: sub.symbol, exchange: sub.exchange }],
      mode: sub.mode,
    });
    setSubs((prev) =>
      prev.filter(
        (s) => !(s.symbol === sub.symbol && s.exchange === sub.exchange && s.mode === sub.mode)
      )
    );
    setTicks((prev) => {
      const next = { ...prev };
      delete next[symKey(sub.symbol, sub.exchange)];
      return next;
    });
  }, [send]);

  const clearLog = useCallback(() => setLog([]), []);

  const tickRows = useMemo(() => Object.values(ticks), [ticks]);

  // --- Health polling (every 5s while page is open) ---
  const { data: health } = useQuery({
    queryKey: ["ws-test-health"],
    queryFn: getWebSocketHealth,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

  const s = statusVariant(status);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">WebSocket Test</h1>
        <p className="text-sm text-muted-foreground">
          Dev tool for exercising the broker streaming adapter end-to-end.
          Connects to the internal WebSocket proxy using your API key, subscribes
          to symbols, and displays raw frames plus MarketDataCache health.
        </p>
      </div>

      {/* Connection */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Connection</CardTitle>
            <CardDescription>
              One shared broker session per process. Connect once, then subscribe.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("h-2.5 w-2.5 rounded-full", s.dot)} aria-hidden />
            <Badge variant={s.badge}>{s.label}</Badge>
            {broker && status === "authenticated" && (
              <Badge variant="outline">{broker}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={connect}
              disabled={status === "connecting" || status === "authenticated" || status === "connected" || status === "authenticating"}
            >
              Connect
            </Button>
            <Button variant="outline" onClick={disconnect} disabled={status === "idle" || status === "closed"}>
              Disconnect
            </Button>
          </div>
          {errorMsg && (
            <p className="mt-3 rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {errorMsg}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Subscribe */}
      <Card>
        <CardHeader>
          <CardTitle>Subscribe</CardTitle>
          <CardDescription>
            Pick an exchange and mode, then select a symbol from search results
            to subscribe. DEPTH implies QUOTE and LTP on the server side.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[200px_160px_1fr]">
            <div className="space-y-2">
              <Label htmlFor="ws-exchange">Exchange</Label>
              <select
                id="ws-exchange"
                value={exchange}
                onChange={(e) => setExchange(e.target.value)}
                className={cn(
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                {EXCHANGES.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="ws-mode">Mode</Label>
              <select
                id="ws-mode"
                value={mode}
                onChange={(e) => setMode(e.target.value as Mode)}
                className={cn(
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                <option value="LTP">LTP</option>
                <option value="QUOTE">QUOTE</option>
                <option value="DEPTH">DEPTH</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="ws-query">Symbol</Label>
              <Input
                id="ws-query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Type to search — click a result to subscribe"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          </div>

          {searchEnabled && (
            <div className="mt-4 max-h-60 overflow-y-auto rounded-md border border-border">
              {searchQuery.isLoading ? (
                <p className="p-3 text-sm text-muted-foreground">Searching…</p>
              ) : searchQuery.error ? (
                <p className="p-3 text-sm text-destructive">Search failed.</p>
              ) : !searchQuery.data || searchQuery.data.length === 0 ? (
                <p className="p-3 text-sm text-muted-foreground">No matches.</p>
              ) : (
                <ul className="divide-y divide-border">
                  {searchQuery.data.slice(0, 20).map((row, i) => (
                    <li
                      key={`${row.token ?? row.symbol}-${i}`}
                      className="flex items-center justify-between gap-2 px-3 py-2 hover:bg-muted/40"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{row.symbol}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {row.name ?? "—"} · {row.exchange}
                          {row.instrumenttype ? ` · ${row.instrumenttype}` : ""}
                        </p>
                      </div>
                      <Button size="sm" variant="outline" onClick={() => pickSymbol(row)}>
                        Subscribe
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {subs.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                Active subscriptions ({subs.length})
              </p>
              <div className="flex flex-wrap gap-2">
                {subs.map((sub) => (
                  <Badge
                    key={`${sub.exchange}:${sub.symbol}:${sub.mode}`}
                    variant="secondary"
                    className="flex items-center gap-1"
                  >
                    <span>
                      {sub.exchange}:{sub.symbol} · {sub.mode}
                    </span>
                    <button
                      type="button"
                      className="ml-1 text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => unsubscribeOne(sub)}
                      aria-label={`Unsubscribe ${sub.symbol}`}
                    >
                      ×
                    </button>
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Live ticks */}
      <Card>
        <CardHeader>
          <CardTitle>Live ticks</CardTitle>
          <CardDescription>
            Last seen values per subscribed symbol. Updates push-only — no polling.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {tickRows.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No ticks yet. Subscribe to a symbol to see updates stream in.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Exchange</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead className="text-right">LTP</TableHead>
                    <TableHead className="text-right">Open</TableHead>
                    <TableHead className="text-right">High</TableHead>
                    <TableHead className="text-right">Low</TableHead>
                    <TableHead className="text-right">Close</TableHead>
                    <TableHead className="text-right">Chg %</TableHead>
                    <TableHead className="text-right">Volume</TableHead>
                    <TableHead className="text-right">Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tickRows.map((r) => (
                    <TableRow key={symKey(r.symbol, r.exchange)}>
                      <TableCell className="font-medium">{r.symbol}</TableCell>
                      <TableCell>{r.exchange}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{r.mode}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono">{fmt(r.ltp)}</TableCell>
                      <TableCell className="text-right font-mono">{fmt(r.open)}</TableCell>
                      <TableCell className="text-right font-mono">{fmt(r.high)}</TableCell>
                      <TableCell className="text-right font-mono">{fmt(r.low)}</TableCell>
                      <TableCell className="text-right font-mono">{fmt(r.close)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          r.change_percent && r.change_percent > 0 && "text-green-600",
                          r.change_percent && r.change_percent < 0 && "text-red-600"
                        )}
                      >
                        {fmt(r.change_percent)}
                      </TableCell>
                      <TableCell className="text-right font-mono">{fmtInt(r.volume)}</TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground">
                        {new Date(r.last_update).toLocaleTimeString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Depth panel */}
      {tickRows.some((r) => r.depth) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {tickRows
            .filter((r) => r.depth)
            .map((r) => (
              <Card key={`depth-${symKey(r.symbol, r.exchange)}`}>
                <CardHeader>
                  <CardTitle className="text-base">
                    Depth · {r.exchange}:{r.symbol}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <p className="mb-1 font-medium text-green-600">Buy</p>
                      <table className="w-full">
                        <thead>
                          <tr className="text-muted-foreground">
                            <th className="text-left">Qty</th>
                            <th className="text-right">Price</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(r.depth?.buy ?? []).slice(0, 10).map((lvl, i) => (
                            <tr key={`b-${i}`}>
                              <td>{fmtInt(lvl.quantity)}</td>
                              <td className="text-right font-mono">{fmt(lvl.price)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div>
                      <p className="mb-1 font-medium text-red-600">Sell</p>
                      <table className="w-full">
                        <thead>
                          <tr className="text-muted-foreground">
                            <th className="text-left">Qty</th>
                            <th className="text-right">Price</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(r.depth?.sell ?? []).slice(0, 10).map((lvl, i) => (
                            <tr key={`s-${i}`}>
                              <td>{fmtInt(lvl.quantity)}</td>
                              <td className="text-right font-mono">{fmt(lvl.price)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
        </div>
      )}

      {/* Health */}
      <Card>
        <CardHeader>
          <CardTitle>MarketDataCache health</CardTitle>
          <CardDescription>
            Server-side view of the centralized feed. Polled every 5 s via HTTP.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!health ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3 lg:grid-cols-4">
              <HealthStat label="Status" value={health.healthy ? "healthy" : "unhealthy"} tone={health.healthy ? "good" : "bad"} />
              <HealthStat
                label="Connected"
                value={health.authenticated ? "authenticated" : health.connected ? "connected" : "no"}
                tone={health.authenticated ? "good" : "bad"}
              />
              <HealthStat
                label="Data flow"
                value={health.data_flow_healthy ? "active" : "inactive"}
                tone={health.data_flow_healthy ? "good" : "bad"}
              />
              <HealthStat
                label="Trade-safe"
                value={health.trade_management_safe ? "yes" : "no"}
                tone={health.trade_management_safe ? "good" : "bad"}
              />
              <HealthStat label="Last data age" value={`${fmt(health.last_data_age_seconds, 1)} s`} />
              <HealthStat label="Ticks processed" value={fmtInt(health.total_updates_processed)} />
              <HealthStat label="Symbols cached" value={fmtInt(health.cache_size)} />
              <HealthStat label="Subscribers" value={`${health.total_subscribers} (${health.critical_subscribers} critical)`} />
              <HealthStat label="Validation errors" value={fmtInt(health.validation_errors)} />
              <HealthStat label="Stale events" value={fmtInt(health.stale_data_events)} />
              <HealthStat label="Reconnects" value={fmtInt(health.reconnect_count)} />
              <HealthStat label="Uptime" value={`${fmt(health.uptime_seconds, 0)} s`} />
            </div>
          )}
          {health && !health.trade_management_safe && health.trade_management_reason && (
            <p className="mt-3 rounded-md bg-amber-500/10 p-2 text-sm text-amber-700 dark:text-amber-400">
              RMS paused: {health.trade_management_reason}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Raw frame log */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Frames</CardTitle>
            <CardDescription>
              Last {MAX_LOG} messages. Newest at top. Includes client commands (↑) and server frames (↓).
            </CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={clearLog} disabled={log.length === 0}>
            Clear
          </Button>
        </CardHeader>
        <CardContent>
          {log.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No frames yet.</p>
          ) : (
            <div className="max-h-80 overflow-y-auto rounded-md border border-border bg-muted/20 p-2 font-mono text-xs">
              {log.map((e) => (
                <div key={e.id} className="border-b border-border/40 py-1 last:border-0">
                  <span className="mr-2 text-muted-foreground">
                    {new Date(e.at).toLocaleTimeString()}
                  </span>
                  <span
                    className={cn(
                      "mr-2 font-bold",
                      e.direction === "in" && "text-green-600",
                      e.direction === "out" && "text-blue-600",
                      e.direction === "sys" && "text-muted-foreground"
                    )}
                  >
                    {e.direction === "in" ? "↓" : e.direction === "out" ? "↑" : "·"}
                  </span>
                  <span className="whitespace-pre-wrap break-all">{e.text}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function HealthStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "font-mono",
          tone === "good" && "text-green-600",
          tone === "bad" && "text-red-600"
        )}
      >
        {value}
      </p>
    </div>
  );
}
