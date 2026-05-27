import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { toast } from "sonner";
import {
  closeAll,
  closeLeg,
  deleteStrategy,
  disableLiveMode,
  enableLiveMode,
  getStrategy,
  killSwitch,
  listEvents,
  listOrders,
  listPositions,
  listRuns,
  listTrades,
  rotateWebhookToken,
  startRun,
  stopRun,
  unlockWebhook,
  type StrategyEvent,
  type StrategyOrder,
  type StrategyPosition,
  type StrategyRun,
  type StrategyTrade,
} from "@/api/strategy_module";
import {
  UNIVERSE_TAB_LABELS,
  type Strategy,
  type StrategyMode,
  type StrategyStatus,
} from "@/types/strategy_module";
import { cn } from "@/lib/utils";
import {
  useStrategyWebSocket,
  type StrategySnapshot,
  type StrategyWsEvent,
  type WsStatus,
} from "@/hooks/useStrategyWebSocket";

function statusBadgeVariant(
  status: StrategyStatus,
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "running":
      return "default";
    case "paused":
      return "secondary";
    case "errored":
      return "destructive";
    default:
      return "outline";
  }
}

function formatIst(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return (
      d.toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZone: "Asia/Kolkata",
      }) + " IST"
    );
  } catch {
    return iso;
  }
}

function formatPnl(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "0.00";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function severityClass(severity: string): string {
  if (severity === "critical") return "text-red-600";
  if (severity === "warn") return "text-amber-600";
  return "text-muted-foreground";
}

function orderStatusVariant(
  status: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (status === "complete") return "default";
  if (status === "rejected") return "destructive";
  if (status === "cancelled") return "outline";
  return "secondary"; // pending / open
}

// ---------------------------------------------------------------------------
// Live tab
// ---------------------------------------------------------------------------

function fmtPnl(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value === 0) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function fmtPrice(value: unknown): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function wsStatusBadge(status: WsStatus): {
  label: string;
  variant: "default" | "secondary" | "destructive" | "outline";
} {
  switch (status) {
    case "open":
      return { label: "live", variant: "default" };
    case "connecting":
    case "reconnecting":
      return { label: status, variant: "secondary" };
    case "error":
      return { label: "error", variant: "destructive" };
    default:
      return { label: status, variant: "outline" };
  }
}

function LiveTab({
  strategy,
  orders,
  onCloseLeg,
  closingLegId,
  liveState,
  wsStatus,
  lastRun,
}: {
  strategy: Strategy;
  orders: StrategyOrder[];
  onCloseLeg: (legId: number) => void;
  closingLegId: number | null;
  liveState: StrategySnapshot | null;
  wsStatus: WsStatus;
  /** Most recent run (running or stopped) — used to render the just-
   * completed run's realized P&L on the Live tab after the strategy
   * stops, since the WS disconnects on status flip and clears liveState. */
  lastRun: StrategyRun | null;
}) {
  // Pair each leg config with its current run's entry + (latest) exit order
  // for the fallback rendering when the WS hasn't sent state yet. Scope to
  // the active run — without the run_id filter, exits from prior runs of
  // the same strategy slot into exitByLeg and the "Close leg" button stays
  // disabled because the leg looks already-exited.
  const currentRunOrders = strategy.current_run_id
    ? orders.filter(
        (o) =>
          o.run_id === strategy.current_run_id &&
          (o.kind === "entry" || o.kind.startsWith("exit")),
      )
    : [];
  const entryByLeg = new Map<number, StrategyOrder>();
  const exitByLeg = new Map<number, StrategyOrder>();
  for (const o of currentRunOrders) {
    if (o.kind === "entry") {
      if (!entryByLeg.has(o.leg_id)) entryByLeg.set(o.leg_id, o);
    } else if (o.status !== "rejected") {
      exitByLeg.set(o.leg_id, o);
    }
  }

  // Live state from WS overrides the REST fallback whenever present.
  const liveLegByLegId = new Map<number, Record<string, unknown>>();
  if (liveState) {
    for (const l of liveState.legs) {
      liveLegByLegId.set(Number(l.leg_id), l);
    }
  }

  const ws = wsStatusBadge(wsStatus);
  const haveLive = liveState !== null && strategy.status === "running";

  // When the run is stopped (WS closed, liveState cleared), surface the
  // last run's finalized P&L so the operator can see the just-completed
  // run's result until they press Start again. While running, prefer
  // liveState — it ticks in real time.
  const showLast =
    liveState == null && lastRun != null && strategy.status !== "running";
  const pnlRealized = showLast
    ? lastRun.pnl_realized
    : (liveState?.mtm_realized ?? null);
  const pnlUnrealized = showLast ? 0 : (liveState?.mtm_unrealized ?? null);
  const pnlTotal = showLast
    ? lastRun.pnl_realized
    : (liveState?.mtm_total ?? null);
  const pnlPeak = showLast ? lastRun.pnl_peak : (liveState?.peak ?? null);
  const pnlTrough = showLast ? lastRun.pnl_trough : (liveState?.trough ?? null);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Live P&L</CardTitle>
            <CardDescription>
              {showLast
                ? "Last run — realized P&L from the most recent run; resets on the next Start."
                : "Realized + Unrealized = Total. Streamed via WebSocket while the run is active."}
            </CardDescription>
          </div>
          <Badge variant={ws.variant} className="text-[10px]">
            {`WS ${ws.label}`}
          </Badge>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: "Realized", value: pnlRealized },
              { label: "Unrealized", value: pnlUnrealized },
              { label: "Total P&L", value: pnlTotal },
            ].map((m) => (
              <div
                key={m.label}
                className="rounded-md border bg-muted/30 p-4 text-center"
              >
                <p className="text-xs uppercase tracking-wider text-muted-foreground">
                  {m.label}
                </p>
                <p
                  className={cn(
                    "mt-1 font-mono text-2xl font-semibold",
                    m.value != null && m.value > 0 && "text-green-600",
                    m.value != null && m.value < 0 && "text-red-600",
                  )}
                >
                  {fmtPnl(m.value)}
                </p>
              </div>
            ))}
          </div>
          {(liveState || showLast) && (
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
              <span>Peak: <span className="font-mono">{fmtPnl(pnlPeak)}</span></span>
              <span>Trough: <span className="font-mono">{fmtPnl(pnlTrough)}</span></span>
              {liveState && (
                <span>Updated: <span className="font-mono">{liveState.ts_ist}</span></span>
              )}
              {showLast && lastRun?.stopped_at && (
                <span>Stopped: <span className="font-mono">{lastRun.stopped_at}</span></span>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Legs</CardTitle>
          <CardDescription>
            {strategy.status === "running"
              ? "Active run — live LTP / MTM / effective SL stream from the engine."
              : "Run inactive — start the strategy to see live state here."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Pos</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Entry</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">MTM</TableHead>
                  <TableHead className="text-right">Eff. SL</TableHead>
                  <TableHead className="text-right">Eff. Tgt</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategy.legs.map((leg) => {
                  const live = haveLive ? liveLegByLegId.get(leg.id) : null;
                  const entry = entryByLeg.get(leg.id);
                  const exit = exitByLeg.get(leg.id);
                  const isOpen =
                    !!entry && entry.status !== "rejected" && !exit;
                  const stateLabel =
                    (live?.status as string) ??
                    (!entry
                      ? "configured"
                      : entry.status === "rejected"
                        ? "rejected"
                        : exit
                          ? "closed"
                          : "open");
                  const symbol =
                    (live?.symbol as string) ?? entry?.symbol ?? "—";
                  const qty =
                    (live?.qty as number) ?? entry?.qty ?? leg.lots;
                  const liveMtm = live?.mtm as number | undefined;
                  return (
                    <TableRow key={leg.id}>
                      <TableCell className="font-mono">{leg.id}</TableCell>
                      <TableCell className="font-mono text-xs">{symbol}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{leg.position}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono">{qty}</TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtPrice(live?.entry_avg)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtPrice(live?.ltp)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          liveMtm != null && liveMtm > 0 && "text-green-600",
                          liveMtm != null && liveMtm < 0 && "text-red-600",
                        )}
                      >
                        {fmtPnl(liveMtm)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtPrice(live?.effective_sl)}
                        {Boolean(live?.trail_active) && (
                          <span className="ml-1 text-[10px] text-amber-600">
                            (trail)
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtPrice(live?.effective_target)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            stateLabel === "open"
                              ? "default"
                              : stateLabel === "rejected"
                                ? "destructive"
                                : "outline"
                          }
                        >
                          {stateLabel}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={
                            !isOpen ||
                            closingLegId === leg.id ||
                            strategy.status !== "running"
                          }
                          onClick={() => onCloseLeg(leg.id)}
                          title={
                            !isOpen
                              ? "Leg is not open"
                              : strategy.status !== "running"
                                ? "Strategy not running"
                                : undefined
                          }
                        >
                          {closingLegId === leg.id ? "Closing…" : "Close leg"}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Orders tab
// ---------------------------------------------------------------------------

function OrdersTab({ orders }: { orders: StrategyOrder[] }) {
  if (orders.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No orders yet. Start a run to see entries appear here.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Strategy orderbook</CardTitle>
        <CardDescription>
          Every order placed by this strategy across all runs. Audit-grade.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Placed</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Leg</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Broker order id</TableHead>
                <TableHead>Reject reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orders.map((o) => (
                <TableRow key={o.id}>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatIst(o.placed_at)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {o.kind}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono">{o.leg_id}</TableCell>
                  <TableCell className="font-mono">{o.symbol}</TableCell>
                  <TableCell>{o.action}</TableCell>
                  <TableCell className="text-right font-mono">{o.qty}</TableCell>
                  <TableCell>
                    <Badge variant={orderStatusVariant(o.status)}>{o.status}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {o.broker_order_id ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-destructive">
                    {o.reject_reason ?? ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History tab — strategy runs
// ---------------------------------------------------------------------------

// One completed round-trip on a leg — entry + matching exit.
interface RoundTrip {
  run_id: number;
  leg_id: number;
  symbol: string;
  exchange: string;
  side: "long" | "short";
  qty: number;
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  exit_kind: string;
  pnl: number;
}

function buildRoundTrips(orders: StrategyOrder[]): RoundTrip[] {
  // Group filled orders by (run_id, leg_id). Pair each leg's first
  // entry order with its latest exit order. Each run currently produces
  // one round-trip per leg (batch start_run + stop_run / SL / target);
  // this also handles signal-mode legs that toggle entry+exit multiple
  // times within one run by FIFO-matching entries to subsequent exits.
  const byLeg = new Map<string, StrategyOrder[]>();
  for (const o of orders) {
    if ((o.status || "").toLowerCase() !== "complete") continue;
    const fq = Number(o.filled_qty ?? o.qty ?? 0);
    const price = Number(o.avg_fill_price ?? 0);
    if (fq <= 0 || price <= 0) continue;
    const key = `${o.run_id}:${o.leg_id}`;
    const list = byLeg.get(key);
    if (list) list.push(o);
    else byLeg.set(key, [o]);
  }

  const trips: RoundTrip[] = [];
  for (const list of byLeg.values()) {
    // Sort by filled_at if available, else placed_at.
    list.sort((a, b) => {
      const ta = a.filled_at ?? a.placed_at;
      const tb = b.filled_at ?? b.placed_at;
      return ta.localeCompare(tb);
    });
    // FIFO queue of open lots — each entry pushes a lot; each exit
    // pops the oldest matching lot and emits a round-trip.
    type OpenLot = {
      side: "long" | "short";
      qty: number;
      entry: StrategyOrder;
    };
    const open: OpenLot[] = [];
    for (const o of list) {
      const isEntry = o.kind === "entry";
      const isExit = o.kind.startsWith("exit");
      const action = (o.action || "").toUpperCase();
      const fq = Number(o.filled_qty ?? o.qty ?? 0);
      if (isEntry) {
        open.push({
          side: action === "BUY" ? "long" : "short",
          qty: fq,
          entry: o,
        });
      } else if (isExit && open.length > 0) {
        // Match against oldest open lot first.
        let remaining = fq;
        while (remaining > 0 && open.length > 0) {
          const lot = open[0];
          const matched = Math.min(remaining, lot.qty);
          const entryPx = Number(lot.entry.avg_fill_price ?? 0);
          const exitPx = Number(o.avg_fill_price ?? 0);
          const sign = lot.side === "long" ? 1 : -1;
          trips.push({
            run_id: o.run_id,
            leg_id: o.leg_id,
            symbol: o.symbol,
            exchange: o.exchange,
            side: lot.side,
            qty: matched,
            entry_time: lot.entry.filled_at ?? lot.entry.placed_at,
            entry_price: entryPx,
            exit_time: o.filled_at ?? o.placed_at,
            exit_price: exitPx,
            exit_kind: o.kind,
            pnl: (exitPx - entryPx) * matched * sign,
          });
          lot.qty -= matched;
          remaining -= matched;
          if (lot.qty <= 0) open.shift();
        }
      }
    }
  }
  // Newest first.
  trips.sort((a, b) => b.exit_time.localeCompare(a.exit_time));
  return trips;
}

type ModeFilter = "all" | "live" | "sandbox";

function HistoryTab({
  runs,
  orders,
}: {
  runs: StrategyRun[];
  orders: StrategyOrder[];
}) {
  // Mode filter — drives both the Performance card and the trade ledger.
  const [modeFilter, setModeFilter] = useState<ModeFilter>("all");

  // Compute closed round-trips from the full order history. One row per
  // completed (entry → exit) pair on a leg, spanning every run of the
  // strategy. Newest first.
  const allTrips = useMemo(() => buildRoundTrips(orders), [orders]);

  // Run lookup so each trade can be tagged with its originating mode
  // (sandbox / live).
  const runModeById = useMemo(() => {
    const m = new Map<number, "sandbox" | "live" | string>();
    for (const r of runs) m.set(r.id, r.mode);
    return m;
  }, [runs]);

  // Per-mode partition for the side-by-side comparison card (stays
  // independent of the filter — that card is meant to be a quick
  // sandbox-vs-live read regardless of what's selected above).
  const tripsByMode = useMemo(() => {
    const live: RoundTrip[] = [];
    const sandbox: RoundTrip[] = [];
    for (const t of allTrips) {
      const mode = runModeById.get(t.run_id);
      if (mode === "live") live.push(t);
      else sandbox.push(t);
    }
    return { live, sandbox };
  }, [allTrips, runModeById]);

  // Per-leg round-trips scoped to the active filter — drives the leg
  // detail ledger below the per-run summary.
  const trips = useMemo(() => {
    if (modeFilter === "all") return allTrips;
    return allTrips.filter((t) => runModeById.get(t.run_id) === modeFilter);
  }, [allTrips, modeFilter, runModeById]);

  // Per-RUN aggregation. A multi-leg basket (e.g. a 2-leg short strangle)
  // closes as ONE trade attempt — win/loss is decided by the SUM of all
  // its legs, not by counting each leg separately. Without this, a
  // strangle that exits at +400/-300 = +100 net would count as
  // 1 win + 1 loss instead of one winning trade.
  interface RunTrade {
    run_id: number;
    mode: "sandbox" | "live" | string;
    entry_time: string;   // earliest leg entry in the run
    exit_time: string;    // latest leg exit in the run
    num_legs: number;
    pnl: number;          // sum across all leg round-trips in the run
    exit_kinds: string[]; // distinct exit kinds the legs went out on
  }
  const runTrades = useMemo<RunTrade[]>(() => {
    const byRun = new Map<number, RunTrade>();
    for (const t of trips) {
      const existing = byRun.get(t.run_id);
      if (existing) {
        existing.pnl += t.pnl;
        existing.num_legs += 1;
        if (t.entry_time < existing.entry_time) existing.entry_time = t.entry_time;
        if (t.exit_time > existing.exit_time) existing.exit_time = t.exit_time;
        if (!existing.exit_kinds.includes(t.exit_kind))
          existing.exit_kinds.push(t.exit_kind);
      } else {
        byRun.set(t.run_id, {
          run_id: t.run_id,
          mode: runModeById.get(t.run_id) ?? "sandbox",
          entry_time: t.entry_time,
          exit_time: t.exit_time,
          num_legs: 1,
          pnl: t.pnl,
          exit_kinds: [t.exit_kind],
        });
      }
    }
    // Newest first.
    return Array.from(byRun.values()).sort((a, b) =>
      b.exit_time.localeCompare(a.exit_time),
    );
  }, [trips, runModeById]);

  // ---------------------------------------------------------------------
  // Stats compute on RUN-level P&L, not leg-level. For multi-leg baskets
  // (e.g. short strangle = 2 legs) the basket is one trade attempt; its
  // win/loss is decided by the sum of all legs. Counting each leg
  // separately would double-count a strangle and skew the win rate.
  // For exit-kind breakdown we still aggregate per leg-trip (a basket
  // can have legs exit via different rules — e.g. one leg target,
  // other leg SL — and the operator wants to see both reasons).
  // ---------------------------------------------------------------------
  const stats = useMemo(() => {
    const total = runTrades.length;
    const winners = runTrades.filter((t) => t.pnl > 0);
    const losers = runTrades.filter((t) => t.pnl < 0);
    const wins = winners.length;
    const losses = losers.length;
    const scratches = total - wins - losses;
    const winRate = total > 0 ? (wins / total) * 100 : 0;
    const totalPnl = runTrades.reduce((s, t) => s + t.pnl, 0);
    const avgPnl = total > 0 ? totalPnl / total : 0;
    const grossWin = winners.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = losers.reduce((s, t) => s + t.pnl, 0);
    const avgWin = wins > 0 ? grossWin / wins : 0;
    const avgLoss = losses > 0 ? grossLoss / losses : 0;
    const rrRatio =
      avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : avgWin > 0 ? Infinity : 0;
    const profitFactor =
      grossLoss !== 0
        ? Math.abs(grossWin / grossLoss)
        : grossWin > 0
          ? Infinity
          : 0;
    const bestTrade =
      total > 0 ? Math.max(...runTrades.map((t) => t.pnl)) : 0;
    const worstTrade =
      total > 0 ? Math.min(...runTrades.map((t) => t.pnl)) : 0;

    // Max drawdown on the cumulative net P&L curve walked chronologically
    // by run. Multi-leg baskets count as a single P&L point per run.
    let maxDrawdown = 0;
    let peak = 0;
    let cum = 0;
    const chronological = [...runTrades].sort((a, b) =>
      a.exit_time.localeCompare(b.exit_time),
    );
    for (const t of chronological) {
      cum += t.pnl;
      if (cum > peak) peak = cum;
      const dd = cum - peak;
      if (dd < maxDrawdown) maxDrawdown = dd;
    }

    // Longest losing / winning streak — also at the run level.
    let maxLoseStreak = 0;
    let curLose = 0;
    let maxWinStreak = 0;
    let curWin = 0;
    for (const t of chronological) {
      if (t.pnl < 0) {
        curLose += 1;
        curWin = 0;
        if (curLose > maxLoseStreak) maxLoseStreak = curLose;
      } else if (t.pnl > 0) {
        curWin += 1;
        curLose = 0;
        if (curWin > maxWinStreak) maxWinStreak = curWin;
      } else {
        curWin = 0;
        curLose = 0;
      }
    }

    // Average run duration (earliest leg entry → latest leg exit).
    let totalDurMs = 0;
    let countedDur = 0;
    for (const t of runTrades) {
      const a = new Date(t.entry_time).getTime();
      const b = new Date(t.exit_time).getTime();
      if (!Number.isNaN(a) && !Number.isNaN(b) && b > a) {
        totalDurMs += b - a;
        countedDur += 1;
      }
    }
    const avgDurMin = countedDur > 0 ? totalDurMs / countedDur / 60000 : 0;

    // Exit-kind breakdown stays leg-level — a basket can exit through
    // multiple rules and seeing every one is useful for tuning. P&L
    // bucketing here is per-leg P&L so the chips reconcile with the
    // ledger detail; total of the chips equals totalPnl above.
    const exitKindCounts: Record<string, number> = {};
    const exitKindPnl: Record<string, number> = {};
    for (const t of trips) {
      exitKindCounts[t.exit_kind] = (exitKindCounts[t.exit_kind] || 0) + 1;
      exitKindPnl[t.exit_kind] = (exitKindPnl[t.exit_kind] || 0) + t.pnl;
    }

    return {
      total,
      wins,
      losses,
      scratches,
      winRate,
      totalPnl,
      avgPnl,
      grossWin,
      grossLoss,
      avgWin,
      avgLoss,
      rrRatio,
      profitFactor,
      bestTrade,
      worstTrade,
      maxDrawdown,
      maxLoseStreak,
      maxWinStreak,
      avgDurMin,
      exitKindCounts,
      exitKindPnl,
      legCount: trips.length,
    };
  }, [runTrades, trips]);

  // Headline number is now run-level. Renamed for clarity in the JSX.
  const totalTrades = stats.total;

  if (runs.length === 0 && totalTrades === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No history yet. Each completed entry+exit will appear here as one
            trade row.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary metrics + mode filter */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle>Strategy performance</CardTitle>
            <CardDescription>
              {modeFilter === "all"
                ? "Aggregated across every closed trade — all runs, all days, both modes."
                : modeFilter === "live"
                  ? "Live trades only — real money, real broker."
                  : "Sandbox trades only — paper P&L, no real money."}
            </CardDescription>
          </div>
          {/* Mode filter — segmented pills */}
          <div className="flex h-9 overflow-hidden rounded-md border border-input">
            {(
              [
                { value: "all", label: "All", count: allTrips.length },
                {
                  value: "live",
                  label: "Live",
                  count: tripsByMode.live.length,
                },
                {
                  value: "sandbox",
                  label: "Sandbox",
                  count: tripsByMode.sandbox.length,
                },
              ] as const
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setModeFilter(opt.value)}
                className={cn(
                  "px-3 text-xs font-medium transition-colors",
                  modeFilter === opt.value
                    ? "bg-primary text-primary-foreground"
                    : "bg-background hover:bg-muted",
                )}
              >
                {opt.label}
                <span className="ml-1 text-[10px] opacity-70">
                  ({opt.count})
                </span>
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {totalTrades === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No {modeFilter === "all" ? "" : `${modeFilter} `}trades yet.
            </p>
          ) : (
            <>
              {/* Row 1 — headline + win/loss profile (run-level) */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
                <Stat label="Trades" value={String(totalTrades)} />
                <Stat
                  label="Win rate"
                  value={`${stats.winRate.toFixed(1)}%`}
                  tone={
                    stats.winRate >= 50 ? "good" : stats.winRate > 0 ? "warn" : "bad"
                  }
                />
                <Stat
                  label="Wins / Losses"
                  value={`${stats.wins} / ${stats.losses}${
                    stats.scratches ? ` (+${stats.scratches})` : ""
                  }`}
                />
                <Stat
                  label="Total P&L"
                  value={
                    stats.totalPnl === 0 ? "0.00" : formatPnl(stats.totalPnl)
                  }
                  tone={
                    stats.totalPnl > 0
                      ? "good"
                      : stats.totalPnl < 0
                        ? "bad"
                        : "neutral"
                  }
                  bold
                />
                <Stat
                  label="Avg P&L / run"
                  value={stats.avgPnl === 0 ? "0.00" : formatPnl(stats.avgPnl)}
                  tone={
                    stats.avgPnl > 0
                      ? "good"
                      : stats.avgPnl < 0
                        ? "bad"
                        : "neutral"
                  }
                />
              </div>

              {/* Row 2 — winner/loser size + RR */}
              <p className="mt-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Winner / loser profile
              </p>
              <div className="mt-1 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
                <Stat
                  label="Avg win"
                  value={stats.avgWin === 0 ? "—" : formatPnl(stats.avgWin)}
                  tone="good"
                />
                <Stat
                  label="Avg loss"
                  value={stats.avgLoss === 0 ? "—" : formatPnl(stats.avgLoss)}
                  tone="bad"
                />
                <Stat
                  label="Reward / Risk"
                  value={
                    stats.rrRatio === Infinity
                      ? "∞"
                      : stats.rrRatio === 0
                        ? "—"
                        : stats.rrRatio.toFixed(2)
                  }
                  tone={
                    stats.rrRatio >= 2
                      ? "good"
                      : stats.rrRatio > 1
                        ? "warn"
                        : "bad"
                  }
                />
                <Stat
                  label="Profit factor"
                  value={
                    stats.profitFactor === Infinity
                      ? "∞"
                      : stats.profitFactor === 0
                        ? "—"
                        : stats.profitFactor.toFixed(2)
                  }
                  tone={
                    stats.profitFactor >= 1.5
                      ? "good"
                      : stats.profitFactor > 1
                        ? "warn"
                        : "bad"
                  }
                />
                <Stat
                  label="Best / Worst"
                  value={`${formatPnl(stats.bestTrade)} / ${formatPnl(stats.worstTrade)}`}
                />
              </div>

              {/* Row 3 — risk + duration */}
              <p className="mt-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Risk &amp; behaviour
              </p>
              <div className="mt-1 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
                <Stat
                  label="Max drawdown"
                  value={
                    stats.maxDrawdown === 0
                      ? "—"
                      : formatPnl(stats.maxDrawdown)
                  }
                  tone="bad"
                />
                <Stat
                  label="Worst losing streak"
                  value={`${stats.maxLoseStreak} ${
                    stats.maxLoseStreak === 1 ? "loss" : "losses"
                  }`}
                  tone={stats.maxLoseStreak >= 5 ? "bad" : undefined}
                />
                <Stat
                  label="Best winning streak"
                  value={`${stats.maxWinStreak} ${
                    stats.maxWinStreak === 1 ? "win" : "wins"
                  }`}
                  tone={stats.maxWinStreak >= 3 ? "good" : undefined}
                />
                <Stat
                  label="Avg trade duration"
                  value={
                    stats.avgDurMin === 0
                      ? "—"
                      : stats.avgDurMin < 60
                        ? `${stats.avgDurMin.toFixed(1)}m`
                        : stats.avgDurMin < 24 * 60
                          ? `${(stats.avgDurMin / 60).toFixed(1)}h`
                          : `${(stats.avgDurMin / (24 * 60)).toFixed(1)}d`
                  }
                />
                <Stat
                  label="Gross win / loss"
                  value={`${formatPnl(stats.grossWin)} / ${formatPnl(stats.grossLoss)}`}
                />
              </div>

              {/* Exit-kind breakdown — what rule actually closed the trades. */}
              {Object.keys(stats.exitKindCounts).length > 0 && (
                <>
                  <p className="mt-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                    Exit reasons
                  </p>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {Object.entries(stats.exitKindCounts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([kind, count]) => {
                        const pct = (count / totalTrades) * 100;
                        const pnl = stats.exitKindPnl[kind] ?? 0;
                        return (
                          <div
                            key={kind}
                            className="rounded-md border bg-muted/30 px-3 py-1.5"
                          >
                            <div className="flex items-center gap-2">
                              <Badge
                                variant="outline"
                                className="font-mono text-[10px]"
                              >
                                {kind}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {count} ({pct.toFixed(0)}%)
                              </span>
                              <span
                                className={cn(
                                  "font-mono text-xs",
                                  pnl > 0 && "text-green-600",
                                  pnl < 0 && "text-red-600",
                                )}
                              >
                                {formatPnl(pnl)}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                  </div>
                </>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Per-mode split so sandbox paper P&L doesn't contaminate the
          live track record (and vice versa). Also computed run-level. */}
      {(tripsByMode.live.length > 0 || tripsByMode.sandbox.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>By mode</CardTitle>
            <CardDescription>
              Sandbox trades are paper; live trades touched real money.
              Win rate counts a multi-leg basket as one trade attempt.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {(["live", "sandbox"] as const).map((mode) => {
                // Aggregate legs into per-run trades inside the mode.
                const list = tripsByMode[mode];
                const byRun = new Map<number, number>();
                for (const t of list)
                  byRun.set(t.run_id, (byRun.get(t.run_id) ?? 0) + t.pnl);
                const runPnls = Array.from(byRun.values());
                const total = runPnls.length;
                const w = runPnls.filter((p) => p > 0).length;
                const wr = total > 0 ? (w / total) * 100 : 0;
                const pnl = runPnls.reduce((s, p) => s + p, 0);
                return (
                  <div
                    key={mode}
                    className={cn(
                      "rounded-md border p-3",
                      mode === "live"
                        ? "border-red-500/40 bg-red-500/5"
                        : "border-blue-500/40 bg-blue-500/5",
                    )}
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <Badge
                        variant={mode === "live" ? "destructive" : "secondary"}
                      >
                        {mode.toUpperCase()}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {total} {total === 1 ? "trade" : "trades"} ·{" "}
                        {list.length} {list.length === 1 ? "leg" : "legs"}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-sm">
                      <div>
                        <p className="text-[10px] uppercase text-muted-foreground">
                          Net P&L
                        </p>
                        <p
                          className={cn(
                            "font-mono font-semibold",
                            pnl > 0 && "text-green-600",
                            pnl < 0 && "text-red-600",
                          )}
                        >
                          {total === 0 ? "—" : formatPnl(pnl)}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-muted-foreground">
                          Win rate
                        </p>
                        <p className="font-mono font-semibold">
                          {total === 0 ? "—" : `${wr.toFixed(1)}%`}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-muted-foreground">
                          Wins / Total
                        </p>
                        <p className="font-mono font-semibold">
                          {w} / {total}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Per-run summary — one row per Start/Stop, P&L = sum across all
          legs in that run. This is what win-rate and other stats use. */}
      <Card>
        <CardHeader>
          <CardTitle>Trade summary (per run)</CardTitle>
          <CardDescription>
            One row per run (= one trade attempt). Net P&L is the sum of
            every leg's round-trip in that run. Multi-leg baskets count
            as a single trade for win-rate purposes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {runTrades.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No closed runs yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Run</TableHead>
                    <TableHead className="text-xs">Mode</TableHead>
                    <TableHead className="text-right text-xs">Legs</TableHead>
                    <TableHead className="text-xs">Entry (first leg)</TableHead>
                    <TableHead className="text-xs">Exit (last leg)</TableHead>
                    <TableHead className="text-xs">Duration</TableHead>
                    <TableHead className="text-xs">Exit kinds</TableHead>
                    <TableHead className="text-right text-xs">Net P&L</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runTrades.map((r) => {
                    const durMin =
                      (new Date(r.exit_time).getTime() -
                        new Date(r.entry_time).getTime()) /
                      60000;
                    const durStr = Number.isFinite(durMin)
                      ? durMin < 60
                        ? `${durMin.toFixed(1)}m`
                        : durMin < 24 * 60
                          ? `${(durMin / 60).toFixed(1)}h`
                          : `${(durMin / (24 * 60)).toFixed(1)}d`
                      : "—";
                    return (
                      <TableRow key={r.run_id}>
                        <TableCell className="font-mono text-xs">
                          #{r.run_id}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              r.mode === "live" ? "destructive" : "secondary"
                            }
                            className="text-[10px]"
                          >
                            {r.mode}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {r.num_legs}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {formatIst(r.entry_time)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {formatIst(r.exit_time)}
                        </TableCell>
                        <TableCell className="text-xs">{durStr}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {r.exit_kinds.map((k) => (
                              <Badge
                                key={k}
                                variant="outline"
                                className="font-mono text-[10px]"
                              >
                                {k}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono text-xs font-semibold",
                            r.pnl > 0 && "text-green-600",
                            r.pnl < 0 && "text-red-600",
                          )}
                        >
                          {formatPnl(r.pnl)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Leg-level ledger — kept for drill-down. */}
      <Card>
        <CardHeader>
          <CardTitle>Leg detail</CardTitle>
          <CardDescription>
            One row per leg round-trip. FIFO-matched within each leg.
            Useful for diagnosing which leg of a multi-leg basket carried
            the run's P&L.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {totalTrades === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No closed trades yet — open positions will appear here once
              they exit.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Run</TableHead>
                    <TableHead className="text-xs">Mode</TableHead>
                    <TableHead className="text-xs">Symbol</TableHead>
                    <TableHead className="text-xs">Side</TableHead>
                    <TableHead className="text-right text-xs">Qty</TableHead>
                    <TableHead className="text-xs">Entry time</TableHead>
                    <TableHead className="text-right text-xs">Entry</TableHead>
                    <TableHead className="text-xs">Exit time</TableHead>
                    <TableHead className="text-right text-xs">Exit</TableHead>
                    <TableHead className="text-xs">Exit kind</TableHead>
                    <TableHead className="text-right text-xs">P&L</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {trips.map((t, i) => {
                    const mode = runModeById.get(t.run_id);
                    return (
                      <TableRow key={`${t.run_id}-${t.leg_id}-${i}`}>
                        <TableCell className="font-mono text-xs">
                          #{t.run_id}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              mode === "live" ? "destructive" : "secondary"
                            }
                            className="text-[10px]"
                          >
                            {mode ?? "—"}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {t.symbol}
                          <span className="ml-1 text-muted-foreground">
                            {t.exchange}
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              t.side === "long" ? "default" : "destructive"
                            }
                            className="text-[10px]"
                          >
                            {t.side}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {t.qty}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {formatIst(t.entry_time)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {t.entry_price.toFixed(2)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {formatIst(t.exit_time)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {t.exit_price.toFixed(2)}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className="font-mono text-[10px]"
                          >
                            {t.exit_kind}
                          </Badge>
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono text-xs font-semibold",
                            t.pnl > 0 && "text-green-600",
                            t.pnl < 0 && "text-red-600",
                          )}
                        >
                          {formatPnl(t.pnl)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Run-level breakdown — kept for context (one row per Start/Stop) */}
      {runs.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Run history</CardTitle>
            <CardDescription>
              Every Start spawns a run row; each row aggregates the leg
              trades above into a single finalised P&L.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run #</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Stopped</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Trigger</TableHead>
                    <TableHead className="text-right">P&L</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="font-mono">{r.id}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            r.mode === "live" ? "destructive" : "secondary"
                          }
                        >
                          {r.mode}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {formatIst(r.started_at)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {formatIst(r.stopped_at)}
                      </TableCell>
                      <TableCell>
                        {r.stop_reason ? (
                          <Badge
                            variant="outline"
                            className="font-mono text-[10px]"
                          >
                            {r.stop_reason}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {r.trigger_source}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          r.pnl_realized > 0 && "text-green-600",
                          r.pnl_realized < 0 && "text-red-600",
                        )}
                      >
                        {r.pnl_realized.toFixed(2)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  bold,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad" | "warn" | "neutral";
  bold?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md p-3",
        bold ? "border-2 bg-muted/40" : "border bg-muted/30",
      )}
    >
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 font-mono",
          bold ? "text-xl font-bold" : "text-lg font-semibold",
          tone === "good" && "text-green-600",
          tone === "bad" && "text-red-600",
          tone === "warn" && "text-amber-600",
        )}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Events tab — audit trail
// ---------------------------------------------------------------------------

function EventsTab({
  events,
  wsEvents,
}: {
  events: StrategyEvent[];
  wsEvents: StrategyWsEvent[];
}) {
  // Merge: WS events are prepended (they're newer and not yet in the REST
  // page until next refetch). De-dup by (kind, ts) approximate key.
  type Row = {
    key: string;
    ts: string;
    kind: string;
    severity: string;
    message: string;
    live: boolean;
  };
  const seenKeys = new Set<string>();
  const merged: Row[] = [];
  for (const e of wsEvents) {
    const k = `ws:${e.kind}:${e.ts_ms_utc}`;
    if (seenKeys.has(k)) continue;
    seenKeys.add(k);
    merged.push({
      key: k,
      ts: e.ts_ist,
      kind: e.kind,
      severity: e.severity,
      message: e.message,
      live: true,
    });
  }
  for (const e of events) {
    merged.push({
      key: `db:${e.id}`,
      ts: e.ts,
      kind: e.kind,
      severity: e.severity,
      message: e.message,
      live: false,
    });
  }

  if (merged.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No events yet. Every state change writes one row.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit trail</CardTitle>
        <CardDescription>
          Every event the strategy module publishes lands here. Live events
          appear instantly via WebSocket; persisted rows poll every 10s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-1.5">
          {merged.map((e) => (
            <div
              key={e.key}
              className="grid grid-cols-[170px_140px_60px_1fr] items-start gap-2 border-b border-border/40 py-1.5 text-sm last:border-0"
            >
              <span className="font-mono text-xs text-muted-foreground">
                {formatIst(e.ts)}
                {e.live && (
                  <Badge variant="secondary" className="ml-1 text-[9px]">
                    live
                  </Badge>
                )}
              </span>
              <Badge variant="outline" className="w-fit font-mono text-[10px]">
                {e.kind}
              </Badge>
              <span
                className={cn("font-mono text-[10px]", severityClass(e.severity))}
              >
                {e.severity}
              </span>
              <span className="whitespace-pre-wrap">{e.message}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Setup tab — read-only summary of the strategy's full configuration
// ---------------------------------------------------------------------------

function SetupTab({ strategy }: { strategy: Strategy }) {
  const navigate = useNavigate();
  const isStopped = strategy.status === "stopped";
  const sched = strategy.scheduler;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <CardTitle>Strategy setup</CardTitle>
            <CardDescription>
              Full configuration as last saved. Edit when stopped.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!isStopped}
            title={!isStopped ? `Cannot edit while ${strategy.status}` : undefined}
            onClick={() => navigate(`/strategy/${strategy.id}/edit`)}
          >
            Edit
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <RiskRow label="Name" value={strategy.name} />
          <RiskRow
            label="Universe"
            value={UNIVERSE_TAB_LABELS[strategy.universe_tab]}
          />
          <RiskRow
            label="Underlying"
            value={`${strategy.underlying} (${strategy.underlying_exchange})`}
          />
          <RiskRow label="Type" value={strategy.strategy_type} />
          {strategy.strategy_type === "intraday" && (
            <>
              <RiskRow label="Entry time" value={strategy.entry_time ?? "—"} />
              <RiskRow label="Exit time" value={strategy.exit_time ?? "—"} />
            </>
          )}
          <RiskRow label="Product" value={strategy.product} />
          <RiskRow label="Pricetype" value={strategy.pricetype} />
          <RiskRow
            label="Daily loss limit"
            value={
              strategy.daily_loss_limit_inr != null
                ? `₹${strategy.daily_loss_limit_inr.toFixed(2)}`
                : "off"
            }
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Legs</CardTitle>
          <CardDescription>
            {strategy.legs.length} leg{strategy.legs.length === 1 ? "" : "s"} configured.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            {strategy.strategy_kind === "signal" ? (
              // Signal-mode legs - each row carries its own symbol+exchange,
              // a side (which signal actions it accepts), and an absolute
              // quantity. Distinct table from the batch-mode option-spread
              // view because the schema is shaped differently per kind.
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="px-2 py-1 text-left">#</th>
                    <th className="px-2 py-1 text-left">Symbol</th>
                    <th className="px-2 py-1 text-left">Exchange</th>
                    <th className="px-2 py-1 text-left">Segment</th>
                    <th className="px-2 py-1 text-left">Side</th>
                    <th className="px-2 py-1 text-right">Qty</th>
                    <th className="px-2 py-1 text-left">Expiry</th>
                  </tr>
                </thead>
                <tbody>
                  {strategy.legs.map((leg) => (
                    <tr key={leg.id} className="border-t">
                      <td className="px-2 py-1.5 font-mono">{leg.id}</td>
                      <td className="px-2 py-1.5 font-mono">{leg.symbol ?? "—"}</td>
                      <td className="px-2 py-1.5 font-mono">{leg.exchange ?? "—"}</td>
                      <td className="px-2 py-1.5">{leg.segment}</td>
                      <td className="px-2 py-1.5">
                        <Badge variant="outline" className="text-xs">
                          {leg.side ?? "both"}
                        </Badge>
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">
                        {leg.qty ?? "—"}
                      </td>
                      <td className="px-2 py-1.5">
                        {leg.segment === "futures" ? (leg.expiry ?? "—") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="px-2 py-1 text-left">#</th>
                    <th className="px-2 py-1 text-left">Segment</th>
                    <th className="px-2 py-1 text-left">Pos</th>
                    <th className="px-2 py-1 text-right">Lots</th>
                    <th className="px-2 py-1 text-left">Expiry</th>
                    <th className="px-2 py-1 text-left">Type</th>
                    <th className="px-2 py-1 text-left">Strike</th>
                  </tr>
                </thead>
                <tbody>
                  {strategy.legs.map((leg) => {
                    const strikeText =
                      leg.segment !== "options"
                        ? "—"
                        : leg.strike_mode === "strike"
                          ? leg.strike_value != null
                            ? `${leg.strike_value}`
                            : "—"
                          : `ATM (${leg.atm_offset ?? "ATM"})`;
                    return (
                      <tr key={leg.id} className="border-t">
                        <td className="px-2 py-1.5 font-mono">{leg.id}</td>
                        <td className="px-2 py-1.5">{leg.segment}</td>
                        <td className="px-2 py-1.5">
                          <Badge variant="outline" className="text-xs">
                            {leg.position}
                          </Badge>
                        </td>
                        <td className="px-2 py-1.5 text-right font-mono">
                          {leg.lots}
                        </td>
                        <td className="px-2 py-1.5">{leg.expiry ?? "—"}</td>
                        <td className="px-2 py-1.5">
                          {leg.option_type ?? "—"}
                        </td>
                        <td className="px-2 py-1.5 font-mono">{strikeText}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Scheduler</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {sched?.enabled ? (
            <>
              <RiskRow label="Enabled" value="yes" />
              <RiskRow label="Days" value={sched.days.join(", ")} />
              <RiskRow label="Start time (IST)" value={sched.start_time} />
              <RiskRow
                label="Auto-stop time (IST)"
                value={sched.auto_stop_time ?? "—"}
              />
              <RiskRow label="Default mode" value={sched.default_mode} />
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Scheduler is off. Strategy can still be started manually or via
              the webhook.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Positions tab - strategy-scoped position book derived from filled orders
// ---------------------------------------------------------------------------

function PositionsTab({
  positions,
  summary,
  runId,
  loading,
  liveLegs,
}: {
  positions: StrategyPosition[];
  summary?: {
    realized: number;
    unrealized: number;
    total: number;
    historical_realized?: number;
    cumulative_realized?: number;
  };
  runId: number | null;
  loading: boolean;
  /** Live leg state from the WS delta stream. When present, the per-position
   *  LTP / unrealized cells are overlaid with tick-rate values; the REST
   *  snapshot stays as the floor when ticks haven't yet arrived for a
   *  symbol (e.g. broker WS hasn't subscribed it yet). */
  liveLegs?: Array<Record<string, unknown>>;
}) {
  // Build a (SYMBOL, EXCHANGE) -> live leg map so the per-row render can
  // overlay live LTP/MTM without an O(N*M) lookup. Symbol+exchange match
  // is the natural key because a strategy can have multiple legs on the
  // same contract (e.g. two CE strikes at different offsets).
  const liveBySymbol = new Map<string, Record<string, unknown>>();
  for (const leg of liveLegs ?? []) {
    const sym = String(leg.symbol ?? "").toUpperCase();
    const exch = String(leg.exchange ?? "").toUpperCase();
    if (sym && exch) liveBySymbol.set(`${sym}|${exch}`, leg);
  }

  // Overlay live ltp / unrealized onto each REST position row. Falls back
  // to whatever the REST endpoint returned when no tick has arrived yet.
  const liveTotals = { realized: 0, unrealized: 0 };
  const merged = positions.map((p) => {
    const live = liveBySymbol.get(
      `${p.symbol.toUpperCase()}|${p.exchange.toUpperCase()}`,
    );
    const liveLtp =
      live && typeof live.ltp === "number" && Number.isFinite(live.ltp)
        ? (live.ltp as number)
        : null;
    const ltp = liveLtp ?? p.ltp;
    // Recompute unrealized off the live LTP rather than trust live.mtm —
    // mtm is a per-leg figure; positions can aggregate multiple legs into
    // one row, so we derive from the position's own qty + avg_entry.
    let unrealized = p.unrealized_pnl;
    if (ltp != null && p.net_qty !== 0) {
      const sign = p.net_qty > 0 ? 1 : -1;
      unrealized = (ltp - p.avg_entry_price) * Math.abs(p.net_qty) * sign;
    }
    liveTotals.realized += p.realized_pnl;
    liveTotals.unrealized += unrealized;
    return { ...p, ltp, unrealized_pnl: unrealized };
  });

  // Prefer live totals when WS deltas have started flowing; else fall
  // back to REST summary so the user sees something on first paint.
  const effectiveSummary = summary
    ? liveLegs && liveLegs.length > 0
      ? {
          ...summary,
          realized: liveTotals.realized,
          unrealized: liveTotals.unrealized,
          total: liveTotals.realized + liveTotals.unrealized,
        }
      : summary
    : undefined;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Strategy positions</CardTitle>
          <CardDescription>
            Net positions derived from this strategy's filled orders.
            {runId !== null && (
              <>
                {" "}Run <span className="font-mono">#{runId}</span>.
              </>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {effectiveSummary && (
            <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-md border p-3">
                <div className="text-xs uppercase text-muted-foreground">
                  Realized (this run)
                </div>
                <div
                  className={cn(
                    "font-mono text-xl",
                    effectiveSummary.realized > 0 && "text-green-600",
                    effectiveSummary.realized < 0 && "text-red-600",
                  )}
                >
                  {formatPnl(effectiveSummary.realized)}
                </div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs uppercase text-muted-foreground">
                  Unrealized
                </div>
                <div
                  className={cn(
                    "font-mono text-xl",
                    effectiveSummary.unrealized > 0 && "text-green-600",
                    effectiveSummary.unrealized < 0 && "text-red-600",
                  )}
                >
                  {formatPnl(effectiveSummary.unrealized)}
                </div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs uppercase text-muted-foreground">
                  Run total
                </div>
                <div
                  className={cn(
                    "font-mono text-xl",
                    effectiveSummary.total > 0 && "text-green-600",
                    effectiveSummary.total < 0 && "text-red-600",
                  )}
                >
                  {formatPnl(effectiveSummary.total)}
                </div>
              </div>
              <div className="rounded-md border-2 p-3">
                <div className="text-xs uppercase text-muted-foreground">
                  Cumulative realized
                </div>
                <div
                  className={cn(
                    "font-mono text-xl font-bold",
                    (effectiveSummary.cumulative_realized ?? 0) > 0 && "text-green-600",
                    (effectiveSummary.cumulative_realized ?? 0) < 0 && "text-red-600",
                  )}
                >
                  {formatPnl(effectiveSummary.cumulative_realized ?? effectiveSummary.realized)}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Lifetime across all runs
                </div>
              </div>
            </div>
          )}
          {loading ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              Loading…
            </p>
          ) : merged.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No positions for the current run.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Exchange</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">Net Qty</TableHead>
                    <TableHead className="text-right">Avg Entry</TableHead>
                    <TableHead className="text-right">LTP</TableHead>
                    <TableHead className="text-right">Unrealized</TableHead>
                    <TableHead className="text-right">Realized (lifetime)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {merged.map((p) => {
                    const rowRealized = p.realized_pnl_lifetime ?? p.realized_pnl;
                    return (
                    <TableRow key={`${p.symbol}-${p.exchange}-${p.product}`}>
                      <TableCell className="font-mono font-medium">
                        {p.symbol}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {p.exchange}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {p.product}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            p.side === "long"
                              ? "default"
                              : p.side === "short"
                                ? "destructive"
                                : "secondary"
                          }
                          className="text-xs"
                        >
                          {p.side}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {p.net_qty}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {p.avg_entry_price.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {p.ltp != null ? p.ltp.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          p.unrealized_pnl > 0 && "text-green-600",
                          p.unrealized_pnl < 0 && "text-red-600",
                        )}
                      >
                        {formatPnl(p.unrealized_pnl)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          rowRealized > 0 && "text-green-600",
                          rowRealized < 0 && "text-red-600",
                        )}
                        title="Lifetime realized on this contract across all runs"
                      >
                        {formatPnl(rowRealized)}
                      </TableCell>
                    </TableRow>
                  );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trades tab - strategy-scoped fills (= sm_strategy_order rows with status=complete)
// ---------------------------------------------------------------------------

function TradesTab({
  trades,
  loading,
}: {
  trades: StrategyTrade[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Strategy tradebook</CardTitle>
        <CardDescription>
          Every filled order placed by this strategy. Executed price is
          the broker/sandbox average fill price.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Loading…
          </p>
        ) : trades.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No trades yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Filled</TableHead>
                  <TableHead>Run</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Executed Price</TableHead>
                  <TableHead className="text-right">Trade Value</TableHead>
                  <TableHead>Order ID</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((t) => (
                  <TableRow key={t.order_id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatIst(t.filled_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      #{t.run_id}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {t.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono">{t.symbol}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {t.exchange}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={t.action === "BUY" ? "default" : "destructive"}
                        className="text-xs"
                      >
                        {t.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {t.filled_qty}
                    </TableCell>
                    <TableCell className="text-right font-mono font-medium">
                      {t.avg_fill_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {t.trade_value.toFixed(2)}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {t.broker_order_id ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Risk tab (unchanged from Phase 2)
// ---------------------------------------------------------------------------

function RiskTab({ strategy }: { strategy: Strategy }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Strategy-level risk</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <RiskRow
            label="Overall SL"
            value={
              strategy.overall_sl_mtm != null
                ? `₹${strategy.overall_sl_mtm.toFixed(2)} MTM`
                : "off"
            }
          />
          <RiskRow
            label="Overall Target"
            value={
              strategy.overall_target_mtm != null
                ? `₹${strategy.overall_target_mtm.toFixed(2)} MTM`
                : "off"
            }
          />
          <RiskRow
            label="Trail-SL-to-entry"
            value={strategy.trail_sl_to_entry ? "enabled" : "off"}
          />
          {strategy.lock_profit ? (
            <>
              <Separator />
              <RiskRow
                label="Lock-profit mode"
                value={
                  strategy.lock_profit.mode === "lock"
                    ? "Lock (static floor)"
                    : "Lock + Trail (rising floor)"
                }
              />
              <RiskRow
                label="If profit reaches"
                value={`₹${strategy.lock_profit.if_profit_reaches.toFixed(2)}`}
              />
              <RiskRow
                label="Lock floor"
                value={`₹${strategy.lock_profit.lock_profit.toFixed(2)}`}
              />
              {strategy.lock_profit.mode === "lock_and_trail" &&
                strategy.lock_profit.trail_step != null && (
                  <RiskRow
                    label="Trail step"
                    value={`₹${strategy.lock_profit.trail_step.toFixed(2)}`}
                  />
                )}
            </>
          ) : (
            <RiskRow label="Lock-profit" value="off" />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Per-leg risk</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="px-2 py-1 text-left">#</th>
                  <th className="px-2 py-1 text-left">Type</th>
                  <th className="px-2 py-1 text-right">SL pts</th>
                  <th className="px-2 py-1 text-right">Target pts</th>
                  <th className="px-2 py-1 text-right">Trail X / Y</th>
                </tr>
              </thead>
              <tbody>
                {strategy.legs.map((leg) => (
                  <tr key={leg.id} className="border-t">
                    <td className="px-2 py-1.5">{leg.id}</td>
                    <td className="px-2 py-1.5">
                      <Badge variant="outline" className="text-xs">
                        {leg.position} · {leg.segment}
                        {leg.option_type ? ` · ${leg.option_type}` : ""}
                      </Badge>
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {leg.sl_pts ?? "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {leg.target_pts ?? "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">
                      {leg.trail.x} / {leg.trail.y}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function RiskRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Webhook tab (unchanged from Phase 2)
// ---------------------------------------------------------------------------

function WebhookTab({
  strategy,
  onRotate,
  rotating,
}: {
  strategy: Strategy;
  onRotate: () => void;
  rotating: boolean;
}) {
  const isSignal = strategy.strategy_kind === "signal";
  const sampleLegId = strategy.legs[0]?.id ?? 1;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>TradingView webhook</CardTitle>
          <CardDescription>
            URL contains a per-strategy secret token. The token is shown once
            on create or rotate.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label>Webhook URL (token redacted)</Label>
            <Input readOnly value={strategy.webhook_url} className="font-mono text-xs" />
          </div>

          {isSignal ? (
            <>
              <div className="space-y-1.5">
                <Label>TradingView alert payloads (one per signal action)</Label>
                <p className="text-xs text-muted-foreground">
                  Each TradingView alert sends one of the four payloads below.
                  Use <span className="font-mono">leg_id</span> when you know
                  it; otherwise the engine falls back to{" "}
                  <span className="font-mono">(symbol, exchange)</span> lookup.
                  Mismatched signals (e.g. <span className="font-mono">long_exit</span>{" "}
                  on a flat leg) are silent no-ops - safe for repeat alerts.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-xs uppercase text-green-700 dark:text-green-400">
                    Long entry
                  </Label>
                  <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"long_entry","leg_id":${sampleLegId}}`}
                  </pre>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs uppercase text-amber-700 dark:text-amber-400">
                    Long exit
                  </Label>
                  <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"long_exit","leg_id":${sampleLegId}}`}
                  </pre>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs uppercase text-red-700 dark:text-red-400">
                    Short entry
                  </Label>
                  <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"short_entry","leg_id":${sampleLegId}}`}
                  </pre>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs uppercase text-amber-700 dark:text-amber-400">
                    Short exit
                  </Label>
                  <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"short_exit","leg_id":${sampleLegId}}`}
                  </pre>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs uppercase">
                  Symbol-keyed fallback (when leg_id unknown)
                </Label>
                <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"long_entry","symbol":"${strategy.legs[0]?.symbol ?? "RELIANCE"}","exchange":"${strategy.legs[0]?.exchange ?? "NSE"}"}`}
                </pre>
              </div>
            </>
          ) : (
            <div className="space-y-1.5">
              <Label>TradingView alert message body</Label>
              <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"start","mode":"sandbox"}`}
              </pre>
              <p className="text-xs text-muted-foreground">
                Send <span className="font-mono">{`{"action":"stop"}`}</span> to
                square off and finalize the run.
              </p>
            </div>
          )}

          <div className="flex justify-end">
            <Button variant="outline" onClick={onRotate} disabled={rotating}>
              {rotating ? "Rotating…" : "Rotate token"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main detail component
// ---------------------------------------------------------------------------

export default function StrategyDetail() {
  const { id } = useParams<{ id: string }>();
  const numId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmCloseAll, setConfirmCloseAll] = useState(false);
  const [confirmKill, setConfirmKill] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const [startDialogOpen, setStartDialogOpen] = useState(false);
  const [startMode, setStartMode] = useState<StrategyMode>("sandbox");
  const [closingLegId, setClosingLegId] = useState<number | null>(null);
  const [rotateReveal, setRotateReveal] = useState<{
    token: string;
    url: string;
  } | null>(null);
  const [enableLiveDialogOpen, setEnableLiveDialogOpen] = useState(false);
  const [liveModePassword, setLiveModePassword] = useState("");

  // ---- Data refresh strategy ----
  // All five queries below used to poll every 5–10s while the strategy
  // was running. They now refresh via the WS hook: the backend pushes
  // order_update / strategy_update / run_update frames at each write
  // point (engine.record_order, reconcile_order_fill, repo.start_run /
  // finalize_run), and useStrategyWebSocket hydrates the React Query
  // cache directly. We keep a slow 60s "safety" refetch as a backstop
  // for missed frames (network blip, queue overflow, server restart).
  const SAFETY_REFETCH_MS = 60_000;

  const strategyQuery = useQuery({
    queryKey: ["strategy", numId],
    queryFn: () => getStrategy(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && d.status === "running" ? SAFETY_REFETCH_MS : false;
    },
  });

  // WebSocket connection — only while running, to keep idle pages quiet.
  const wsEnabled =
    Number.isFinite(numId) && numId > 0 &&
    strategyQuery.data?.status === "running";
  const { status: wsStatus, liveState, events: wsEvents } =
    useStrategyWebSocket(
      wsEnabled && strategyQuery.data ? numId : null,
      wsEnabled,
    );

  const ordersQuery = useQuery({
    queryKey: ["strategy-orders", numId],
    queryFn: () => listOrders(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) =>
      strategyQuery.data?.status === "running" ? SAFETY_REFETCH_MS : false,
  });

  const runsQuery = useQuery({
    queryKey: ["strategy-runs", numId],
    queryFn: () => listRuns(numId),
    enabled: Number.isFinite(numId) && numId > 0,
  });

  const eventsQuery = useQuery({
    queryKey: ["strategy-events", numId],
    queryFn: () => listEvents(numId, undefined, 200),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) =>
      strategyQuery.data?.status === "running" ? SAFETY_REFETCH_MS : false,
  });

  const positionsQuery = useQuery({
    queryKey: ["strategy-positions", numId],
    queryFn: () => listPositions(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) =>
      strategyQuery.data?.status === "running" ? SAFETY_REFETCH_MS : false,
  });

  const tradesQuery = useQuery({
    queryKey: ["strategy-trades", numId],
    queryFn: () => listTrades(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) =>
      strategyQuery.data?.status === "running" ? SAFETY_REFETCH_MS : false,
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-orders", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-runs", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-events", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-positions", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-trades", numId] });
  };

  const startMutation = useMutation({
    mutationFn: (mode: StrategyMode) => startRun(numId, mode),
    onSuccess: (resp) => {
      const rejected = resp.legs.filter((l) => l.status === "rejected");
      if (rejected.length > 0) {
        toast.warning(
          `Run started, but ${rejected.length} leg(s) were rejected. See Orders tab.`,
        );
      } else {
        toast.success(`Run started — ${resp.legs.length} legs placed`);
      }
      setStartDialogOpen(false);
      invalidateAll();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        "Start failed";
      toast.error(detail);
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => stopRun(numId),
    onSuccess: () => {
      toast.success("Run stopped");
      invalidateAll();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        "Stop failed";
      toast.error(detail);
    },
  });

  const closeAllMutation = useMutation({
    mutationFn: () => closeAll(numId),
    onSuccess: () => {
      toast.success("All open legs closed");
      setConfirmCloseAll(false);
      invalidateAll();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        "Close-all failed";
      toast.error(detail);
    },
  });

  const closeLegMutation = useMutation({
    mutationFn: (legId: number) => closeLeg(numId, legId),
    onSuccess: (resp) => {
      toast.success(
        resp.auto_stopped
          ? "Leg closed — last open leg, run stopped"
          : "Leg closed",
      );
      setClosingLegId(null);
      invalidateAll();
    },
    onError: (err: unknown) => {
      setClosingLegId(null);
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        "Close-leg failed";
      toast.error(detail);
    },
  });

  const rotateMutation = useMutation({
    mutationFn: () => rotateWebhookToken(numId),
    onSuccess: (resp) => {
      setRotateReveal({ token: resp.webhook_token, url: resp.strategy.webhook_url });
      queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    },
    onError: () => toast.error("Failed to rotate token"),
  });

  const enableLiveMutation = useMutation({
    mutationFn: (password: string) => enableLiveMode(numId, password),
    onSuccess: () => {
      toast.success("Live mode enabled");
      setEnableLiveDialogOpen(false);
      setLiveModePassword("");
      queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Live-enable failed";
      toast.error(detail);
    },
  });

  const disableLiveMutation = useMutation({
    mutationFn: () => disableLiveMode(numId),
    onSuccess: () => {
      toast.success("Live mode disabled");
      queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Live-disable failed";
      toast.error(detail);
    },
  });

  const killSwitchMutation = useMutation({
    mutationFn: () => killSwitch(numId),
    onSuccess: (resp) => {
      const cancelledCount = resp.cancelled_orders?.length ?? 0;
      toast.warning(
        `Kill switch fired. ${cancelledCount} pending order(s) cancelled; webhook locked.`,
      );
      invalidateAll();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Kill switch failed";
      toast.error(detail);
    },
  });

  const unlockMutation = useMutation({
    mutationFn: () => unlockWebhook(numId),
    onSuccess: (resp) => {
      if (resp.noop) {
        toast.info("Webhook was already unlocked");
      } else {
        toast.success("Webhook unlocked - signals will be accepted again");
      }
      queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Unlock failed";
      toast.error(detail);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteStrategy(numId),
    onSuccess: () => {
      toast.success("Strategy deleted");
      navigate("/strategy");
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
        "Delete failed";
      toast.error(detail);
    },
  });

  if (!Number.isFinite(numId) || numId <= 0) {
    return <p className="text-sm text-destructive">Invalid strategy id.</p>;
  }
  if (strategyQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (strategyQuery.error || !strategyQuery.data) {
    return (
      <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
        Failed to load strategy.
      </p>
    );
  }

  const strategy: Strategy = strategyQuery.data;
  const orders = ordersQuery.data ?? [];
  const runs = runsQuery.data ?? [];
  const events = eventsQuery.data ?? [];

  const isRunning = strategy.status === "running";
  const isStopped = strategy.status === "stopped";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted-foreground">
            {UNIVERSE_TAB_LABELS[strategy.universe_tab]}
          </div>
          <h1 className="text-2xl font-bold tracking-tight">{strategy.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={statusBadgeVariant(strategy.status)}>{strategy.status}</Badge>
            <Badge variant={strategy.live_enabled ? "destructive" : "secondary"}>
              {strategy.live_enabled ? "LIVE-enabled" : "SANDBOX-only"}
            </Badge>
            {strategy.webhook_locked && (
              <Badge variant="destructive" className="font-semibold">
                WEBHOOK LOCKED
              </Badge>
            )}
            {strategy.strategy_kind === "signal" && (
              <Badge variant="default" className="bg-blue-600 hover:bg-blue-600">
                Signal mode
              </Badge>
            )}
            <Badge variant="outline">{strategy.strategy_type}</Badge>
            {strategy.strategy_kind === "signal" ? (
              <Badge variant="outline">
                {strategy.direction === "both"
                  ? "Long+Short"
                  : strategy.direction === "long_only"
                    ? "Long only"
                    : "Short only"}
              </Badge>
            ) : (
              <Badge variant="outline">
                {strategy.underlying} · {strategy.underlying_exchange}
              </Badge>
            )}
            <Badge variant="outline">{strategy.product}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Kill switch is always available (running OR stopped) - the
              point is to lock the webhook even when nothing is running.
              Disabled briefly while in flight. */}
          <Button
            variant="destructive"
            className="border-2 border-red-700 bg-red-600 font-bold hover:bg-red-700"
            disabled={killSwitchMutation.isPending}
            onClick={() => setConfirmKill(true)}
            title="Cancel pending orders, flatten positions, and block all webhook signals"
          >
            {killSwitchMutation.isPending ? "Killing…" : "KILL SWITCH"}
          </Button>
          {strategy.webhook_locked && (
            <Button
              variant="outline"
              disabled={unlockMutation.isPending}
              onClick={() => unlockMutation.mutate()}
              title="Resume accepting webhook signals"
            >
              {unlockMutation.isPending ? "Unlocking…" : "Unlock webhook"}
            </Button>
          )}
          {isStopped && !strategy.webhook_locked && (
            <Button onClick={() => setStartDialogOpen(true)}>Start run</Button>
          )}
          {isRunning && (
            <>
              <Button
                variant="destructive"
                disabled={closeAllMutation.isPending}
                onClick={() => setConfirmCloseAll(true)}
              >
                {closeAllMutation.isPending ? "Closing…" : "Close All"}
              </Button>
              <Button
                variant="outline"
                disabled={stopMutation.isPending}
                onClick={() => setConfirmStop(true)}
              >
                {stopMutation.isPending ? "Stopping…" : "Stop"}
              </Button>
            </>
          )}
          {isStopped && !strategy.live_enabled && (
            <Button
              variant="destructive"
              onClick={() => setEnableLiveDialogOpen(true)}
              title="Enable live mode (password re-auth required)"
            >
              Enable LIVE
            </Button>
          )}
          {isStopped && strategy.live_enabled && (
            <Button
              variant="outline"
              onClick={() => disableLiveMutation.mutate()}
              disabled={disableLiveMutation.isPending}
              title="Disable live mode — strategy reverts to sandbox-only"
            >
              {disableLiveMutation.isPending ? "Disabling…" : "Disable LIVE"}
            </Button>
          )}
          <Button
            variant="outline"
            disabled={!isStopped}
            title={!isStopped ? `Cannot edit while ${strategy.status}` : undefined}
            onClick={() => navigate(`/strategy/${strategy.id}/edit`)}
          >
            Edit
          </Button>
          <Button variant="outline" onClick={() => navigate("/strategy")}>
            Back
          </Button>
          <Button
            variant="destructive"
            disabled={!isStopped}
            onClick={() => setConfirmDelete(true)}
            title={!isStopped ? `Cannot delete while ${strategy.status}` : undefined}
          >
            Delete
          </Button>
        </div>
      </div>

      <div className="text-xs text-muted-foreground">
        Created {formatIst(strategy.created_at)} · Updated{" "}
        {formatIst(strategy.updated_at)}
        {strategy.current_run_id ? (
          <span className="ml-3">· Current run: <span className="font-mono">#{strategy.current_run_id}</span></span>
        ) : null}
      </div>

      <Tabs defaultValue="live">
        <TabsList className="flex flex-wrap gap-1 bg-transparent" variant="line">
          <TabsTrigger value="live">Live</TabsTrigger>
          <TabsTrigger value="setup">Setup</TabsTrigger>
          <TabsTrigger value="positions">Positions</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="events">Events</TabsTrigger>
          <TabsTrigger value="risk">Risk</TabsTrigger>
          <TabsTrigger value="webhook">Webhook</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="live" className="mt-4">
          <LiveTab
            strategy={strategy}
            orders={orders}
            closingLegId={closingLegId}
            onCloseLeg={(legId) => {
              setClosingLegId(legId);
              closeLegMutation.mutate(legId);
            }}
            liveState={liveState}
            wsStatus={wsStatus}
            lastRun={runs[0] ?? null}
          />
        </TabsContent>
        <TabsContent value="setup" className="mt-4">
          <SetupTab strategy={strategy} />
        </TabsContent>
        <TabsContent value="positions" className="mt-4">
          <PositionsTab
            positions={positionsQuery.data?.positions ?? []}
            summary={positionsQuery.data?.summary}
            runId={positionsQuery.data?.run_id ?? null}
            loading={positionsQuery.isLoading}
            liveLegs={liveState?.legs}
          />
        </TabsContent>
        <TabsContent value="orders" className="mt-4">
          <OrdersTab orders={orders} />
        </TabsContent>
        <TabsContent value="trades" className="mt-4">
          <TradesTab
            trades={tradesQuery.data ?? []}
            loading={tradesQuery.isLoading}
          />
        </TabsContent>
        <TabsContent value="events" className="mt-4">
          <EventsTab events={events} wsEvents={wsEvents} />
        </TabsContent>
        <TabsContent value="risk" className="mt-4">
          <RiskTab strategy={strategy} />
        </TabsContent>
        <TabsContent value="webhook" className="mt-4">
          <WebhookTab
            strategy={strategy}
            onRotate={() => rotateMutation.mutate()}
            rotating={rotateMutation.isPending}
          />
        </TabsContent>
        <TabsContent value="history" className="mt-4">
          <HistoryTab runs={runs} orders={orders} />
        </TabsContent>
      </Tabs>

      {/* Enable LIVE mode — password re-auth (plan Section 14.3) */}
      <Dialog open={enableLiveDialogOpen} onOpenChange={setEnableLiveDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Enable live mode — confirm with password</DialogTitle>
            <p className="text-sm text-muted-foreground">
              Live mode places real broker orders. Once enabled, any
              scheduled / webhook / manual start with{" "}
              <span className="font-mono">mode=live</span> will hit your
              broker. Disable again from the Detail header.
            </p>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="livepw">Account password</Label>
              <Input
                id="livepw"
                type="password"
                value={liveModePassword}
                onChange={(e) => setLiveModePassword(e.target.value)}
                autoFocus
                autoComplete="current-password"
                placeholder="re-enter your account password"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && liveModePassword) {
                    enableLiveMutation.mutate(liveModePassword);
                  }
                }}
              />
            </div>
            <p className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
              You are about to enable LIVE mode. Real orders, real margin.
              This action is recorded in the audit trail.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setEnableLiveDialogOpen(false);
                setLiveModePassword("");
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={!liveModePassword || enableLiveMutation.isPending}
              onClick={() => enableLiveMutation.mutate(liveModePassword)}
            >
              {enableLiveMutation.isPending ? "Verifying…" : "Enable LIVE"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Start-run mode picker */}
      <Dialog open={startDialogOpen} onOpenChange={setStartDialogOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Start run — pick mode</DialogTitle>
            <p className="text-sm text-muted-foreground">
              Live mode places real broker orders. Sandbox mode is paper-only.
            </p>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex h-10 overflow-hidden rounded-md border border-input">
              {(["sandbox", "live"] as StrategyMode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setStartMode(m)}
                  disabled={m === "live" && !strategy.live_enabled}
                  className={cn(
                    "flex-1 text-sm font-medium transition-colors disabled:opacity-40",
                    startMode === m
                      ? "bg-primary text-primary-foreground"
                      : "bg-background hover:bg-muted",
                  )}
                  title={
                    m === "live" && !strategy.live_enabled
                      ? "Enable live mode on the strategy first"
                      : undefined
                  }
                >
                  {m.toUpperCase()}
                </button>
              ))}
            </div>
            {startMode === "live" && !strategy.live_enabled && (
              <p className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
                Strategy isn't live-enabled. Use "Enable LIVE" on the detail
                page to re-authenticate and unlock live mode.
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStartDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={
                startMutation.isPending ||
                (startMode === "live" && !strategy.live_enabled)
              }
              onClick={() => startMutation.mutate(startMode)}
            >
              {startMutation.isPending ? "Starting…" : `Start ${startMode}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmCloseAll}
        onOpenChange={setConfirmCloseAll}
        title="Close all open legs?"
        description="Exits every open leg at MARKET and stops the run."
        confirmLabel="Close all"
        variant="destructive"
        loading={closeAllMutation.isPending}
        onConfirm={() => closeAllMutation.mutate()}
      />

      <ConfirmDialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        title="Stop the run?"
        description={
          "Every open leg will be exited at MARKET and the run finalised. " +
          "Realized P&L gets locked in; the strategy returns to a stopped state " +
          "and stops accepting webhook signals until you start it again."
        }
        confirmLabel="Stop run"
        variant="destructive"
        loading={stopMutation.isPending}
        onConfirm={() => {
          stopMutation.mutate();
          setConfirmStop(false);
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this strategy?"
        description="Permanently removes the strategy and its audit trail."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />

      <ConfirmDialog
        open={confirmKill}
        onOpenChange={setConfirmKill}
        title="Activate kill switch?"
        description={
          "This cancels every pending order, flattens every open " +
          "position at MARKET, and locks the webhook so external " +
          "TradingView signals are refused. The strategy stays stopped " +
          "until you explicitly unlock and start it."
        }
        confirmLabel="KILL"
        variant="destructive"
        loading={killSwitchMutation.isPending}
        onConfirm={() => {
          killSwitchMutation.mutate();
          setConfirmKill(false);
        }}
      />

      <Dialog
        open={rotateReveal !== null}
        onOpenChange={(open) => !open && setRotateReveal(null)}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>New webhook URL — copy now</DialogTitle>
            <p className="text-sm text-muted-foreground">
              The previous URL stops working immediately. This one is shown once.
            </p>
          </DialogHeader>
          {rotateReveal && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>New webhook URL</Label>
                <div className="flex items-center gap-2">
                  <Input readOnly value={rotateReveal.url} className="font-mono text-xs" />
                  <Button
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(rotateReveal.url);
                      toast.success("Copied URL");
                    }}
                  >
                    Copy
                  </Button>
                </div>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setRotateReveal(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
