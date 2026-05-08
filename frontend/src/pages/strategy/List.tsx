import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { toast } from "sonner";
import { deleteStrategy, listStrategies } from "@/api/strategy_module";
import { UNIVERSE_TAB_LABELS, type StrategyStatus } from "@/types/strategy_module";
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

function formatPnl(value: number): string {
  if (value === 0) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function formatIst(iso: string): string {
  // Backend already sends IST ISO 8601 with +05:30 offset. We just format
  // for display; never convert into the browser's local timezone.
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Kolkata",
    }) + " IST";
  } catch {
    return iso;
  }
}

export default function StrategyList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["strategies"],
    queryFn: () => listStrategies({}),
    refetchInterval: 30_000,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteStrategy(id),
    onSuccess: () => {
      toast.success("Strategy deleted");
      queryClient.invalidateQueries({ queryKey: ["strategies"] });
      setDeleteTargetId(null);
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Delete failed";
      toast.error(detail);
    },
  });

  const rows = data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Strategies</h1>
          <p className="text-sm text-muted-foreground">
            Multi-leg options strategies with risk management. Phase 1 is
            config-only — engine, scheduler, and webhook receiver come in
            later phases.
          </p>
        </div>
        <Button onClick={() => navigate("/strategy/new")}>+ New strategy</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Saved strategies</CardTitle>
          <CardDescription>
            Live P&L columns are zero placeholders until Phase 6 wires the
            engine. Status reflects whether a run is active.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              Failed to load strategies. Check the backend logs.
            </p>
          ) : isLoading ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Loading…
            </p>
          ) : rows.length === 0 ? (
            <div className="space-y-3 py-8 text-center">
              <p className="text-sm text-muted-foreground">
                No strategies yet.
              </p>
              <Button
                variant="secondary"
                onClick={() => navigate("/strategy/new")}
              >
                Create your first strategy
              </Button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Underlying</TableHead>
                    <TableHead>Tab</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Realized</TableHead>
                    <TableHead className="text-right">Unrealized</TableHead>
                    <TableHead className="text-right">Total P&L</TableHead>
                    <TableHead>Updated</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => (
                    <TableRow
                      key={r.id}
                      className={i % 2 === 0 ? "bg-muted/30" : ""}
                    >
                      <TableCell>
                        <Link
                          to={`/strategy/${r.id}`}
                          className="font-medium hover:underline"
                        >
                          {r.name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusBadgeVariant(r.status)}>
                          {r.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={r.live_enabled ? "destructive" : "secondary"}>
                          {r.live_enabled ? "LIVE-enabled" : "SANDBOX-only"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-medium">
                        {r.underlying}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {UNIVERSE_TAB_LABELS[r.universe_tab]}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{r.strategy_type}</Badge>
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          r.pnl_realized > 0 && "text-green-600",
                          r.pnl_realized < 0 && "text-red-600",
                        )}
                      >
                        {formatPnl(r.pnl_realized)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          r.pnl_unrealized > 0 && "text-green-600",
                          r.pnl_unrealized < 0 && "text-red-600",
                        )}
                      >
                        {formatPnl(r.pnl_unrealized)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono font-medium",
                          r.pnl_total > 0 && "text-green-600",
                          r.pnl_total < 0 && "text-red-600",
                        )}
                      >
                        {formatPnl(r.pnl_total)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatIst(r.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => navigate(`/strategy/${r.id}`)}
                          >
                            Open
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            disabled={r.status !== "stopped"}
                            title={
                              r.status !== "stopped"
                                ? `Cannot delete while ${r.status}`
                                : undefined
                            }
                            onClick={() => setDeleteTargetId(r.id)}
                          >
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={deleteTargetId !== null}
        onOpenChange={(open) => !open && setDeleteTargetId(null)}
        title="Delete this strategy?"
        description="This permanently removes the strategy and its audit trail. This cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={() => {
          if (deleteTargetId !== null) deleteMutation.mutate(deleteTargetId);
        }}
      />
    </div>
  );
}
