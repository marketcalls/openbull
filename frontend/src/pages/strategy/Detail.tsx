import { useState } from "react";
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
  listRuns,
  rotateWebhookToken,
  startRun,
  stopRun,
  unlockWebhook,
  type StrategyEvent,
  type StrategyOrder,
  type StrategyRun,
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
}: {
  strategy: Strategy;
  orders: StrategyOrder[];
  onCloseLeg: (legId: number) => void;
  closingLegId: number | null;
  liveState: StrategySnapshot | null;
  wsStatus: WsStatus;
}) {
  // Pair each leg config with its current run's entry + (latest) exit order
  // for the fallback rendering when the WS hasn't sent state yet.
  const currentRunOrders = strategy.current_run_id
    ? orders.filter((o) => o.kind === "entry" || o.kind.startsWith("exit"))
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

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Live P&L</CardTitle>
            <CardDescription>
              Realized + Unrealized = Total. Streamed via WebSocket while the
              run is active.
            </CardDescription>
          </div>
          <Badge variant={ws.variant} className="text-[10px]">
            {`WS ${ws.label}`}
          </Badge>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: "Realized", value: liveState?.mtm_realized ?? null },
              { label: "Unrealized", value: liveState?.mtm_unrealized ?? null },
              { label: "Total P&L", value: liveState?.mtm_total ?? null },
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
          {liveState && (
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
              <span>Peak: <span className="font-mono">{fmtPnl(liveState.peak)}</span></span>
              <span>Trough: <span className="font-mono">{fmtPnl(liveState.trough)}</span></span>
              <span>Updated: <span className="font-mono">{liveState.ts_ist}</span></span>
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

function HistoryTab({ runs }: { runs: StrategyRun[] }) {
  if (runs.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No runs yet. Each Start spawns a run row.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run history</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run #</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Broker</TableHead>
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
                    <Badge variant={r.mode === "live" ? "destructive" : "secondary"}>
                      {r.mode}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs">{r.broker}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatIst(r.started_at)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatIst(r.stopped_at)}
                  </TableCell>
                  <TableCell>
                    {r.stop_reason ? (
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {r.stop_reason}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{r.trigger_source}</TableCell>
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
  const [startDialogOpen, setStartDialogOpen] = useState(false);
  const [startMode, setStartMode] = useState<StrategyMode>("sandbox");
  const [closingLegId, setClosingLegId] = useState<number | null>(null);
  const [rotateReveal, setRotateReveal] = useState<{
    token: string;
    url: string;
  } | null>(null);
  const [enableLiveDialogOpen, setEnableLiveDialogOpen] = useState(false);
  const [liveModePassword, setLiveModePassword] = useState("");

  const strategyQuery = useQuery({
    queryKey: ["strategy", numId],
    queryFn: () => getStrategy(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) => {
      // Light REST poll for status/current_run_id transitions; live legs/MTM
      // arrive via the WebSocket (Phase 6).
      const d = q.state.data;
      return d && d.status === "running" ? 10_000 : false;
    },
  });

  // Phase 6 WebSocket connection — only while running, to keep idle pages quiet.
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
      strategyQuery.data?.status === "running" ? 5_000 : false,
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
      strategyQuery.data?.status === "running" ? 5_000 : false,
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["strategy", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-orders", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-runs", numId] });
    queryClient.invalidateQueries({ queryKey: ["strategy-events", numId] });
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
    onSuccess: () => {
      toast.success("Leg closed");
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
                onClick={() => stopMutation.mutate()}
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
          <TabsTrigger value="orders">Orders</TabsTrigger>
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
          />
        </TabsContent>
        <TabsContent value="setup" className="mt-4">
          <SetupTab strategy={strategy} />
        </TabsContent>
        <TabsContent value="orders" className="mt-4">
          <OrdersTab orders={orders} />
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
          <HistoryTab runs={runs} />
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
