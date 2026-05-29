import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getPositions } from "@/api/dashboard";
import { closeAllPositions } from "@/api/orders";
import { placeOrder } from "@/api/optionchain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useLivePrice } from "@/hooks/useLivePrice";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { PositionItem } from "@/types/order";

function getPnlColor(value: number): string {
  if (value > 0) return "text-green-600 dark:text-green-400";
  if (value < 0) return "text-red-600 dark:text-red-400";
  return "";
}

/** Open positions are anything with a non-zero net qty. Zero-qty rows are
 *  closed-today fills that openalgo / sandbox keep visible until the next
 *  session boundary so the user can see the round trip — they're not
 *  re-closeable. */
function isCloseable(p: PositionItem): boolean {
  return (p.quantity ?? 0) !== 0;
}

/** Pending confirm state — `null` = no dialog open. */
type PendingConfirm =
  | { kind: "closeOne"; position: PositionItem }
  | { kind: "closeAll"; count: number };

export default function Positions() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<PendingConfirm | null>(null);

  const { data: positions, isLoading, error } = useQuery({
    queryKey: ["positions"],
    queryFn: getPositions,
    refetchInterval: 15000,
  });

  /** Only show positions with non-zero quantity (matches openalgo /positions
   *  behavior). Zero-qty rows are closed-today fills that brokers keep
   *  visible until the next session boundary — we hide them from the UI. */
  const openPositions = useMemo(
    () => (positions ?? []).filter(isCloseable),
    [positions],
  );

  const openCount = openPositions.length;

  // Live overlay: subscribe to each open position's LTP over the WS proxy and
  // recompute P&L on every tick. Falls back to the REST snapshot when the
  // socket is down or the tab is hidden.
  const { data: livePositions, isLive, isPaused } = useLivePrice(openPositions, {
    enabled: openPositions.length > 0,
  });

  /** Per-row close — reverses the position's direction at MARKET for
   *  abs(qty). Long → SELL, short → BUY. The backend's placeorder
   *  endpoint takes care of sandbox vs live dispatch. */
  const closeOneMutation = useMutation({
    mutationFn: async (pos: PositionItem) => {
      if (pos.quantity === 0) {
        throw new Error("Position is already flat");
      }
      const action: "BUY" | "SELL" = pos.quantity > 0 ? "SELL" : "BUY";
      const product = (pos.product || "MIS") as "MIS" | "NRML" | "CNC";
      return placeOrder({
        symbol: pos.symbol,
        exchange: pos.exchange,
        action,
        quantity: Math.abs(pos.quantity),
        pricetype: "MARKET",
        product,
        strategy: "Close Position",
      });
    },
    onSuccess: (resp, pos) => {
      if (resp.status === "success" && resp.orderid) {
        toast.success(`Closing ${pos.symbol}: ${resp.orderid}`);
        queryClient.invalidateQueries({ queryKey: ["positions"] });
        queryClient.invalidateQueries({ queryKey: ["orderbook"] });
      } else {
        toast.error(resp.message ?? `Close failed for ${pos.symbol}`);
      }
    },
    onError: (err: unknown, pos) => {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        `Close failed for ${pos.symbol}`;
      toast.error(msg);
    },
    onSettled: () => setConfirming(null),
  });

  const closeAllMutation = useMutation({
    mutationFn: () => closeAllPositions("Close All"),
    onSuccess: (resp) => {
      if (resp.status === "success") {
        toast.success(resp.message ?? "Close-all submitted");
        queryClient.invalidateQueries({ queryKey: ["positions"] });
        queryClient.invalidateQueries({ queryKey: ["orderbook"] });
      } else {
        toast.error(resp.message ?? "Close-all failed");
      }
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        "Close-all failed";
      toast.error(msg);
    },
    onSettled: () => setConfirming(null),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading positions...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load positions.
        </div>
      </div>
    );
  }

  const handleCloseAll = () => {
    if (openCount === 0) return;
    setConfirming({ kind: "closeAll", count: openCount });
  };

  const handleExportCsv = () => {
    // Export the unfiltered list (including zero-qty closed-today rows) so
    // the CSV mirrors what the broker returned — useful for end-of-day P&L
    // reconciliation. The UI hides zero-qty rows but the audit trail should
    // not.
    const rows = positions ?? [];
    if (rows.length === 0) return;
    const columns: CsvColumn<PositionItem>[] = [
      { header: "Symbol", value: (r) => r.symbol },
      { header: "Exchange", value: (r) => r.exchange },
      { header: "Product", value: (r) => r.product },
      { header: "Quantity", value: (r) => r.quantity },
      {
        header: "Side",
        value: (r) =>
          r.quantity > 0 ? "LONG" : r.quantity < 0 ? "SHORT" : "FLAT",
      },
      { header: "Average Price", value: (r) => r.average_price.toFixed(2) },
      { header: "LTP", value: (r) => r.ltp.toFixed(2) },
      { header: "P&L", value: (r) => r.pnl.toFixed(2) },
    ];
    downloadCsv({ filename: "positions", columns, rows });
    toast.success(`Exported ${rows.length} position${rows.length === 1 ? "" : "s"} to CSV`);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Positions</h1>
          <p className="text-sm text-muted-foreground">View your open positions</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            onClick={handleExportCsv}
            disabled={(positions?.length ?? 0) === 0}
            title={
              (positions?.length ?? 0) === 0
                ? "No positions to export"
                : `Export ${positions?.length} position${positions?.length === 1 ? "" : "s"} as CSV`
            }
          >
            Export CSV
          </Button>
          <Button
            variant="destructive"
            onClick={handleCloseAll}
            disabled={openCount === 0 || closeAllMutation.isPending}
            title={
              openCount === 0
                ? "No open positions to close"
                : `Close every open position at MARKET (${openCount})`
            }
          >
            {closeAllMutation.isPending
              ? "Closing…"
              : `Close All${openCount > 0 ? ` (${openCount})` : ""}`}
          </Button>
        </div>
      </div>

      {isPaused && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          Live updates paused (tab inactive) — showing last fetched prices.
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Open Positions
            {isLive ? (
              <Badge
                variant="outline"
                className="gap-1 border-green-500/40 text-green-600 dark:text-green-400"
              >
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-green-500" />
                Live
              </Badge>
            ) : isPaused ? (
              <Badge variant="outline" className="text-muted-foreground">
                Paused
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            {openPositions.length} position{openPositions.length !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {openPositions.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg Price</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {livePositions.map((pos, i) => {
                  const closeable = isCloseable(pos);
                  const closingThis =
                    closeOneMutation.isPending &&
                    closeOneMutation.variables?.symbol === pos.symbol &&
                    closeOneMutation.variables?.exchange === pos.exchange &&
                    closeOneMutation.variables?.product === pos.product;
                  return (
                    <TableRow key={`${pos.symbol}-${pos.exchange}-${pos.product}-${i}`} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                      <TableCell className="font-medium">{pos.symbol}</TableCell>
                      <TableCell>{pos.exchange}</TableCell>
                      <TableCell>{pos.product}</TableCell>
                      <TableCell className="text-right">{pos.quantity}</TableCell>
                      <TableCell className="text-right">
                        {pos.average_price.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right">
                        {pos.ltp.toFixed(2)}
                      </TableCell>
                      <TableCell className={`text-right font-medium ${getPnlColor(pos.pnl)}`}>
                        {pos.pnl >= 0 ? "+" : ""}
                        {pos.pnl.toFixed(2)}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={!closeable || closingThis}
                            onClick={() =>
                              setConfirming({ kind: "closeOne", position: pos })
                            }
                            title={
                              closeable
                                ? `Close at MARKET (${pos.quantity > 0 ? "SELL" : "BUY"} ${Math.abs(pos.quantity)})`
                                : "Position is already flat"
                            }
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          >
                            {closingThis ? "…" : "Close"}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No open positions.
            </p>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(o) => {
          if (!o && !closeOneMutation.isPending && !closeAllMutation.isPending) {
            setConfirming(null);
          }
        }}
        title={
          confirming?.kind === "closeAll"
            ? "Close all positions"
            : "Close position"
        }
        description={
          confirming?.kind === "closeAll" ? (
            <>
              This will square off <strong>{confirming.count}</strong> open
              position{confirming.count === 1 ? "" : "s"} at MARKET. The
              action can't be undone.
            </>
          ) : confirming?.kind === "closeOne" ? (
            <>
              Close{" "}
              <span className="font-mono font-semibold text-foreground">
                {confirming.position.symbol}
              </span>{" "}
              ({confirming.position.quantity > 0 ? "SELL" : "BUY"}{" "}
              <span className="font-mono">
                {Math.abs(confirming.position.quantity)}
              </span>{" "}
              @ MARKET)?
            </>
          ) : null
        }
        confirmLabel={
          confirming?.kind === "closeAll" ? "Close all" : "Close position"
        }
        cancelLabel="Keep"
        variant="destructive"
        loading={closeOneMutation.isPending || closeAllMutation.isPending}
        onConfirm={() => {
          if (confirming?.kind === "closeAll") {
            closeAllMutation.mutate();
          } else if (confirming?.kind === "closeOne") {
            closeOneMutation.mutate(confirming.position);
          }
        }}
      />
    </div>
  );
}
