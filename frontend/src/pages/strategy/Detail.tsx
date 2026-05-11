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
  getStrategy,
  listEvents,
  listOrders,
  listRuns,
  rotateWebhookToken,
  startRun,
  stopRun,
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

function LiveTab({
  strategy,
  orders,
  onCloseLeg,
  closingLegId,
}: {
  strategy: Strategy;
  orders: StrategyOrder[];
  onCloseLeg: (legId: number) => void;
  closingLegId: number | null;
}) {
  // Pair each leg config with its current run's entry + (latest) exit order
  // to derive the leg's open/closed state. Real LTPs and MTM come in Phase 6.
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

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Live P&L</CardTitle>
          <CardDescription>
            Realized · Unrealized · Total — streamed over WebSocket once the
            engine ships in Phase 6.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: "Realized", value: "—" },
              { label: "Unrealized", value: "—" },
              { label: "Total P&L", value: "—" },
            ].map((m) => (
              <div
                key={m.label}
                className="rounded-md border bg-muted/30 p-4 text-center"
              >
                <p className="text-xs uppercase tracking-wider text-muted-foreground">
                  {m.label}
                </p>
                <p className="mt-1 font-mono text-2xl font-semibold">{m.value}</p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Legs</CardTitle>
          <CardDescription>
            {strategy.status === "running"
              ? "Active run — manual leg-close available."
              : "Run inactive — start the strategy to see live state here."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Symbol / segment</TableHead>
                  <TableHead>Position</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Entry order</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategy.legs.map((leg) => {
                  const entry = entryByLeg.get(leg.id);
                  const exit = exitByLeg.get(leg.id);
                  const isOpen =
                    !!entry &&
                    entry.status !== "rejected" &&
                    !exit;
                  const stateLabel = !entry
                    ? "configured"
                    : entry.status === "rejected"
                      ? "rejected"
                      : exit
                        ? "closed"
                        : "open";
                  return (
                    <TableRow key={leg.id}>
                      <TableCell className="font-mono">{leg.id}</TableCell>
                      <TableCell>
                        {entry ? (
                          <span className="font-mono">{entry.symbol}</span>
                        ) : (
                          <Badge variant="outline">
                            {leg.segment}
                            {leg.option_type ? ` · ${leg.option_type}` : ""}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{leg.position}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {entry?.qty ?? leg.lots}
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
                      <TableCell className="text-xs text-muted-foreground">
                        {entry?.broker_order_id ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!isOpen || closingLegId === leg.id || strategy.status !== "running"}
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

function EventsTab({ events }: { events: StrategyEvent[] }) {
  if (events.length === 0) {
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
          Every event the strategy module publishes is persisted here. Newest
          first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-1.5">
          {events.map((e) => (
            <div
              key={e.id}
              className="grid grid-cols-[170px_140px_60px_1fr] items-start gap-2 border-b border-border/40 py-1.5 text-sm last:border-0"
            >
              <span className="font-mono text-xs text-muted-foreground">
                {formatIst(e.ts)}
              </span>
              <Badge variant="outline" className="w-fit font-mono text-[10px]">
                {e.kind}
              </Badge>
              <span className={cn("font-mono text-[10px]", severityClass(e.severity))}>
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
          <div className="space-y-1.5">
            <Label>TradingView alert message body</Label>
            <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"start","mode":"sandbox"}`}
            </pre>
          </div>
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
  const [startDialogOpen, setStartDialogOpen] = useState(false);
  const [startMode, setStartMode] = useState<StrategyMode>("sandbox");
  const [closingLegId, setClosingLegId] = useState<number | null>(null);
  const [rotateReveal, setRotateReveal] = useState<{
    token: string;
    url: string;
  } | null>(null);

  const strategyQuery = useQuery({
    queryKey: ["strategy", numId],
    queryFn: () => getStrategy(numId),
    enabled: Number.isFinite(numId) && numId > 0,
    refetchInterval: (q) => {
      // Poll while a run is active so the legs table reflects exits.
      // Phase 6 replaces this with a WebSocket push.
      const d = q.state.data;
      return d && d.status === "running" ? 5_000 : false;
    },
  });

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
            <Badge variant="outline">{strategy.strategy_type}</Badge>
            <Badge variant="outline">
              {strategy.underlying} · {strategy.underlying_exchange}
            </Badge>
            <Badge variant="outline">{strategy.product}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isStopped && (
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
          />
        </TabsContent>
        <TabsContent value="orders" className="mt-4">
          <OrdersTab orders={orders} />
        </TabsContent>
        <TabsContent value="events" className="mt-4">
          <EventsTab events={events} />
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
                Strategy isn't live-enabled. Re-auth flow ships in Phase 7+.
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
