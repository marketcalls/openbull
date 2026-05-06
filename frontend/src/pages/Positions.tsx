import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getPositions } from "@/api/dashboard";
import { closeAllPositions } from "@/api/orders";
import { placeOrder } from "@/api/optionchain";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
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

  const openCount = useMemo(
    () => (positions ?? []).filter(isCloseable).length,
    [positions],
  );

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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Positions</h1>
          <p className="text-sm text-muted-foreground">View your open positions</p>
        </div>
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

      <Card>
        <CardHeader>
          <CardTitle>Open Positions</CardTitle>
          <CardDescription>
            {positions?.length ?? 0} position{(positions?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {positions && positions.length > 0 ? (
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
                {positions.map((pos, i) => {
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
