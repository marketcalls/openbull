import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getOrderbook } from "@/api/dashboard";
import { cancelAllOrders, cancelOrder } from "@/api/orders";
import { ModifyOrderDialog } from "@/components/trading/ModifyOrderDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SortableHead, type SortState } from "@/components/ui/sortable-head";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import { formatOrderDateTime, formatOrderTime, parseOrderTimestamp } from "@/lib/orderTimestamp";
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
import type { OrderbookItem } from "@/types/order";

type OrderSortKey =
  | "timestamp"
  | "symbol"
  | "exchange"
  | "action"
  | "product"
  | "pricetype"
  | "quantity"
  | "price"
  | "order_status";

const ORDER_NUMERIC_KEYS = new Set<OrderSortKey>(["timestamp", "quantity", "price"]);

function orderSortValue(row: OrderbookItem, key: OrderSortKey): string | number {
  switch (key) {
    case "timestamp":
      return parseOrderTimestamp(row.timestamp);
    case "symbol":
      return row.symbol;
    case "exchange":
      return row.exchange;
    case "action":
      return row.action;
    case "product":
      return row.product;
    case "pricetype":
      return row.pricetype;
    case "quantity":
      return row.quantity;
    case "price":
      return row.price;
    case "order_status":
      return row.order_status;
  }
}

function cmp(a: string | number, b: string | number): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { sensitivity: "base" });
}

function getStatusVariant(
  status: string,
): "default" | "secondary" | "destructive" | "outline" {
  const s = status.toLowerCase();
  if (s === "complete" || s === "filled") return "default";
  if (s === "rejected" || s === "cancelled") return "destructive";
  if (s === "open" || s === "pending" || s === "trigger_pending" || s === "trigger pending") return "secondary";
  return "outline";
}

/** Open orders are anything still working at the broker — cancellable +
 *  modifiable. Filled / rejected / cancelled rows are read-only. */
function isMutableStatus(status: string): boolean {
  const s = status.toLowerCase();
  return s === "open" || s === "pending" || s === "trigger_pending" || s === "trigger pending";
}

/** Pending confirm state — `null` = no dialog open. `kind` discriminates so
 *  the same ConfirmDialog can serve both "cancel one" and "cancel all". */
type PendingConfirm =
  | { kind: "cancelOne"; order: OrderbookItem }
  | { kind: "cancelAll"; count: number };

export default function OrderBook() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<OrderbookItem | null>(null);
  const [confirming, setConfirming] = useState<PendingConfirm | null>(null);
  // Newest first by default — what a trader almost always wants.
  const [sort, setSort] = useState<SortState<OrderSortKey>>({
    key: "timestamp",
    direction: "desc",
  });

  const { data: orders, isLoading, error } = useQuery({
    queryKey: ["orderbook"],
    queryFn: getOrderbook,
    refetchInterval: 15000,
  });

  const openCount = useMemo(
    () => (orders ?? []).filter((o) => isMutableStatus(o.order_status)).length,
    [orders],
  );

  const handleSort = (key: OrderSortKey) => {
    setSort((cur) => {
      if (cur && cur.key === key) {
        return { key, direction: cur.direction === "asc" ? "desc" : "asc" };
      }
      // Numeric / time columns default to descending (latest / largest first);
      // text columns default to ascending (A–Z).
      return { key, direction: ORDER_NUMERIC_KEYS.has(key) ? "desc" : "asc" };
    });
  };

  const sortedOrders = useMemo(() => {
    const rows = orders ?? [];
    return [...rows].sort((a, b) => {
      const av = orderSortValue(a, sort.key);
      const bv = orderSortValue(b, sort.key);
      const c = cmp(av, bv);
      return sort.direction === "asc" ? c : -c;
    });
  }, [orders, sort]);

  const cancelMutation = useMutation({
    mutationFn: (orderid: string) => cancelOrder({ orderid }),
    onSuccess: (resp, orderid) => {
      if (resp.status === "success") {
        toast.success(`Cancelled #${orderid}`);
        queryClient.invalidateQueries({ queryKey: ["orderbook"] });
      } else {
        toast.error(resp.message ?? "Cancel failed");
      }
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        "Cancel failed";
      toast.error(msg);
    },
    onSettled: () => setConfirming(null),
  });

  const cancelAllMutation = useMutation({
    mutationFn: () => cancelAllOrders(),
    onSuccess: (resp) => {
      if (resp.status === "success") {
        const cancelled = resp.data?.canceled?.length ?? 0;
        const failed = resp.data?.failed?.length ?? 0;
        if (failed === 0) {
          toast.success(
            `Cancelled ${cancelled} order${cancelled === 1 ? "" : "s"}`,
          );
        } else {
          toast.warning(
            `Cancelled ${cancelled}, ${failed} failed. See details on the broker.`,
          );
        }
        queryClient.invalidateQueries({ queryKey: ["orderbook"] });
      } else {
        toast.error(resp.message ?? "Cancel-all failed");
      }
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        "Cancel-all failed";
      toast.error(msg);
    },
    onSettled: () => setConfirming(null),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading orders...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load orderbook.
        </div>
      </div>
    );
  }

  const handleCancelAll = () => {
    if (openCount === 0) return;
    setConfirming({ kind: "cancelAll", count: openCount });
  };

  const handleExportCsv = () => {
    const rows = orders ?? [];
    if (rows.length === 0) return;
    const columns: CsvColumn<OrderbookItem>[] = [
      { header: "Timestamp", value: (r) => r.timestamp },
      { header: "Order ID", value: (r) => r.orderid },
      { header: "Symbol", value: (r) => r.symbol },
      { header: "Exchange", value: (r) => r.exchange },
      { header: "Action", value: (r) => r.action },
      { header: "Product", value: (r) => r.product },
      { header: "Price Type", value: (r) => r.pricetype },
      { header: "Quantity", value: (r) => r.quantity },
      { header: "Price", value: (r) => r.price.toFixed(2) },
      { header: "Trigger Price", value: (r) => r.trigger_price.toFixed(2) },
      { header: "Status", value: (r) => r.order_status },
    ];
    downloadCsv({ filename: "orderbook", columns, rows });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Orderbook</h1>
          <p className="text-sm text-muted-foreground">View all your orders</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            onClick={handleExportCsv}
            disabled={(orders?.length ?? 0) === 0}
            title={
              (orders?.length ?? 0) === 0
                ? "No orders to export"
                : `Export ${orders?.length} order${orders?.length === 1 ? "" : "s"} as CSV`
            }
          >
            Export CSV
          </Button>
          <Button
            variant="destructive"
            onClick={handleCancelAll}
            disabled={openCount === 0 || cancelAllMutation.isPending}
            title={
              openCount === 0
                ? "No open orders to cancel"
                : `Cancel every open order (${openCount})`
            }
          >
            {cancelAllMutation.isPending
              ? "Cancelling…"
              : `Cancel All${openCount > 0 ? ` (${openCount})` : ""}`}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Orders</CardTitle>
          <CardDescription>
            {orders?.length ?? 0} order{(orders?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sortedOrders.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHead sortKey="symbol" current={sort} onSort={handleSort}>
                    Symbol
                  </SortableHead>
                  <SortableHead sortKey="exchange" current={sort} onSort={handleSort}>
                    Exchange
                  </SortableHead>
                  <SortableHead sortKey="action" current={sort} onSort={handleSort}>
                    Action
                  </SortableHead>
                  <SortableHead sortKey="product" current={sort} onSort={handleSort}>
                    Product
                  </SortableHead>
                  <SortableHead sortKey="pricetype" current={sort} onSort={handleSort}>
                    Price Type
                  </SortableHead>
                  <SortableHead
                    sortKey="quantity"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                  >
                    Qty
                  </SortableHead>
                  <SortableHead
                    sortKey="price"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                  >
                    Price
                  </SortableHead>
                  <SortableHead
                    sortKey="timestamp"
                    current={sort}
                    onSort={handleSort}
                    className="w-[100px]"
                  >
                    Time
                  </SortableHead>
                  <SortableHead
                    sortKey="order_status"
                    current={sort}
                    onSort={handleSort}
                  >
                    Status
                  </SortableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedOrders.map((order, i) => {
                  const mutable = isMutableStatus(order.order_status);
                  const cancellingThis =
                    cancelMutation.isPending && cancelMutation.variables === order.orderid;
                  return (
                    <TableRow key={order.orderid || i} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                      <TableCell className="font-medium">{order.symbol}</TableCell>
                      <TableCell>{order.exchange}</TableCell>
                      <TableCell>
                        <span
                          className={
                            order.action.toUpperCase() === "BUY"
                              ? "font-medium text-green-600 dark:text-green-400"
                              : "font-medium text-red-600 dark:text-red-400"
                          }
                        >
                          {order.action}
                        </span>
                      </TableCell>
                      <TableCell>{order.product}</TableCell>
                      <TableCell>{order.pricetype}</TableCell>
                      <TableCell className="text-right">{order.quantity}</TableCell>
                      <TableCell className="text-right">
                        {order.price.toFixed(2)}
                      </TableCell>
                      <TableCell
                        className="whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground"
                        title={formatOrderDateTime(order.timestamp)}
                      >
                        {formatOrderTime(order.timestamp)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={getStatusVariant(order.order_status)}>
                          {order.order_status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={!mutable || cancellingThis}
                            onClick={() => setEditing(order)}
                            title={mutable ? "Modify order" : "Order is not modifiable"}
                          >
                            Modify
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!mutable || cancellingThis}
                            onClick={() =>
                              setConfirming({ kind: "cancelOne", order })
                            }
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                            title={mutable ? "Cancel order" : "Order is not cancellable"}
                          >
                            {cancellingThis ? "…" : "Cancel"}
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
              No orders found.
            </p>
          )}
        </CardContent>
      </Card>

      <ModifyOrderDialog
        open={editing !== null}
        onOpenChange={(o) => {
          if (!o) setEditing(null);
        }}
        order={editing}
        onModified={() => {
          queryClient.invalidateQueries({ queryKey: ["orderbook"] });
          setEditing(null);
        }}
      />

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(o) => {
          if (!o && !cancelMutation.isPending && !cancelAllMutation.isPending) {
            setConfirming(null);
          }
        }}
        title={
          confirming?.kind === "cancelAll"
            ? "Cancel all open orders"
            : "Cancel order"
        }
        description={
          confirming?.kind === "cancelAll" ? (
            <>
              This will cancel <strong>{confirming.count}</strong> open
              order{confirming.count === 1 ? "" : "s"}. The action can't be
              undone.
            </>
          ) : confirming?.kind === "cancelOne" ? (
            <>
              Cancel{" "}
              <span className="font-mono font-semibold text-foreground">
                {confirming.order.symbol}
              </span>{" "}
              order{" "}
              <span className="font-mono text-foreground">
                #{confirming.order.orderid}
              </span>
              ?
            </>
          ) : null
        }
        confirmLabel={
          confirming?.kind === "cancelAll" ? "Cancel all" : "Cancel order"
        }
        cancelLabel="Keep"
        variant="destructive"
        loading={cancelMutation.isPending || cancelAllMutation.isPending}
        onConfirm={() => {
          if (confirming?.kind === "cancelAll") {
            cancelAllMutation.mutate();
          } else if (confirming?.kind === "cancelOne") {
            cancelMutation.mutate(confirming.order.orderid);
          }
        }}
      />
    </div>
  );
}
