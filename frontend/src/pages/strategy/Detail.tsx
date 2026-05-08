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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { toast } from "sonner";
import {
  deleteStrategy,
  getStrategy,
  rotateWebhookToken,
} from "@/api/strategy_module";
import {
  UNIVERSE_TAB_LABELS,
  type Strategy,
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

function formatIst(iso: string): string {
  try {
    const d = new Date(iso);
    return (
      d.toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: "Asia/Kolkata",
      }) + " IST"
    );
  } catch {
    return iso;
  }
}

function PlaceholderTab({ phase, what }: { phase: string; what: string }) {
  return (
    <Card>
      <CardContent className="py-12 text-center">
        <p className="text-sm text-muted-foreground">
          {what} arrives in <span className="font-medium">{phase}</span>.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          The data layer is in place — only the UI wiring remains.
        </p>
      </CardContent>
    </Card>
  );
}

function LiveTab({ strategy }: { strategy: Strategy }) {
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
                <p className="mt-1 font-mono text-2xl font-semibold">
                  {m.value}
                </p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Legs</CardTitle>
          <CardDescription>
            Live LTP, MTM, effective SL, target, trail status — populated by
            the engine on the active run. Currently {strategy.status}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="py-6 text-center text-sm text-muted-foreground">
            No active run. The runtime engine ships in Phase 4; live state in
            Phase 6.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

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
            on create or rotate; the URL stored in TradingView's alert config
            is the credential.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label>Webhook URL (token redacted)</Label>
            <Input readOnly value={strategy.webhook_url} className="font-mono text-xs" />
            <p className="text-xs text-muted-foreground">
              Lost the token? Click <span className="font-medium">Rotate</span> to
              issue a new one. The old URL stops working immediately.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>TradingView alert message body</Label>
            <pre className="rounded-md bg-muted p-3 text-xs">
{`{"action":"start","mode":"sandbox"}`}
            </pre>
            <p className="text-xs text-muted-foreground">
              Phase 9 wires the receiver. For now, the URL exists and the
              token resolves — the engine just isn't running yet.
            </p>
          </div>

          <div className="flex justify-end">
            <Button variant="outline" onClick={onRotate} disabled={rotating}>
              {rotating ? "Rotating…" : "Rotate token"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent webhook events</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-6 text-center text-sm text-muted-foreground">
            Webhook receiver ships in Phase 9. Every accepted or rejected hit
            will appear here from `sm_webhook_event`.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export default function StrategyDetail() {
  const { id } = useParams<{ id: string }>();
  const numId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rotateReveal, setRotateReveal] = useState<{
    token: string;
    url: string;
  } | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["strategy", numId],
    queryFn: () => getStrategy(numId),
    enabled: Number.isFinite(numId) && numId > 0,
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
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Delete failed";
      toast.error(detail);
    },
  });

  if (!Number.isFinite(numId) || numId <= 0) {
    return <p className="text-sm text-destructive">Invalid strategy id.</p>;
  }
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error || !data) {
    return (
      <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
        Failed to load strategy.
      </p>
    );
  }

  const strategy: Strategy = data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted-foreground">
            {UNIVERSE_TAB_LABELS[strategy.universe_tab]}
          </div>
          <h1 className="text-2xl font-bold tracking-tight">{strategy.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={statusBadgeVariant(strategy.status)}>
              {strategy.status}
            </Badge>
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
          <Button variant="outline" onClick={() => navigate("/strategy")}>
            Back to list
          </Button>
          <Button
            variant="destructive"
            disabled={strategy.status !== "stopped"}
            onClick={() => setConfirmDelete(true)}
            title={
              strategy.status !== "stopped"
                ? `Cannot delete while ${strategy.status}`
                : undefined
            }
          >
            Delete
          </Button>
        </div>
      </div>

      <div className="text-xs text-muted-foreground">
        Created {formatIst(strategy.created_at)} · Updated {formatIst(strategy.updated_at)}
      </div>

      <Tabs defaultValue="live">
        <TabsList className={cn("flex flex-wrap gap-1 bg-transparent")} variant="line">
          <TabsTrigger value="live">Live</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="positions">Positions</TabsTrigger>
          <TabsTrigger value="events">Events</TabsTrigger>
          <TabsTrigger value="risk">Risk</TabsTrigger>
          <TabsTrigger value="webhook">Webhook</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="live" className="mt-4">
          <LiveTab strategy={strategy} />
        </TabsContent>
        <TabsContent value="orders" className="mt-4">
          <PlaceholderTab phase="Phase 4" what="Strategy-scoped orderbook" />
        </TabsContent>
        <TabsContent value="trades" className="mt-4">
          <PlaceholderTab phase="Phase 4" what="Strategy-scoped tradebook" />
        </TabsContent>
        <TabsContent value="positions" className="mt-4">
          <PlaceholderTab phase="Phase 4" what="Strategy-scoped positions" />
        </TabsContent>
        <TabsContent value="events" className="mt-4">
          <PlaceholderTab
            phase="Phase 6"
            what="Risk-event audit trail (sm_strategy_event)"
          />
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
          <PlaceholderTab phase="Phase 4" what="Run history (sm_strategy_run)" />
        </TabsContent>
      </Tabs>

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

      {/* One-time post-rotate token reveal */}
      <Dialog
        open={rotateReveal !== null}
        onOpenChange={(open) => !open && setRotateReveal(null)}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>New webhook URL — copy now</DialogTitle>
            <p className="text-sm text-muted-foreground">
              The previous URL stops working immediately. This new one is shown
              once.
            </p>
          </DialogHeader>
          {rotateReveal && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>New webhook URL</Label>
                <div className="flex items-center gap-2">
                  <Input
                    readOnly
                    value={rotateReveal.url}
                    className="font-mono text-xs"
                  />
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
