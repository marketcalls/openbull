import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { SortableHead, type SortState } from "@/components/ui/sortable-head";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { getTradebook } from "@/api/dashboard";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import {
  formatOrderDateTime,
  formatOrderTime,
  parseOrderTimestamp,
} from "@/lib/orderTimestamp";
import type { TradebookItem } from "@/types/order";

type TradeSortKey =
  | "timestamp"
  | "symbol"
  | "exchange"
  | "action"
  | "product"
  | "quantity"
  | "average_price"
  | "trade_value";

const TRADE_NUMERIC_KEYS = new Set<TradeSortKey>([
  "timestamp",
  "quantity",
  "average_price",
  "trade_value",
]);

function tradeSortValue(row: TradebookItem, key: TradeSortKey): string | number {
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
    case "quantity":
      return row.quantity;
    case "average_price":
      return row.average_price;
    case "trade_value":
      return row.trade_value;
  }
}

function cmp(a: string | number, b: string | number): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { sensitivity: "base" });
}

export default function TradeBook() {
  const [sort, setSort] = useState<SortState<TradeSortKey>>({
    key: "timestamp",
    direction: "desc",
  });

  const { data: trades, isLoading, error } = useQuery({
    queryKey: ["tradebook"],
    queryFn: getTradebook,
    refetchInterval: 15000,
  });

  const handleSort = (key: TradeSortKey) => {
    setSort((cur) =>
      cur && cur.key === key
        ? { key, direction: cur.direction === "asc" ? "desc" : "asc" }
        : { key, direction: TRADE_NUMERIC_KEYS.has(key) ? "desc" : "asc" },
    );
  };

  const sortedTrades = useMemo(() => {
    const rows = trades ?? [];
    return [...rows].sort((a, b) => {
      const av = tradeSortValue(a, sort.key);
      const bv = tradeSortValue(b, sort.key);
      const c = cmp(av, bv);
      return sort.direction === "asc" ? c : -c;
    });
  }, [trades, sort]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading trades...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load tradebook.
        </div>
      </div>
    );
  }

  const handleExportCsv = () => {
    const rows = trades ?? [];
    if (rows.length === 0) return;
    const columns: CsvColumn<TradebookItem>[] = [
      { header: "Timestamp", value: (r) => r.timestamp },
      { header: "Order ID", value: (r) => r.orderid },
      { header: "Symbol", value: (r) => r.symbol },
      { header: "Exchange", value: (r) => r.exchange },
      { header: "Action", value: (r) => r.action },
      { header: "Product", value: (r) => r.product },
      { header: "Quantity", value: (r) => r.quantity },
      { header: "Average Price", value: (r) => r.average_price.toFixed(2) },
      { header: "Trade Value", value: (r) => r.trade_value.toFixed(2) },
    ];
    downloadCsv({ filename: "tradebook", columns, rows });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Tradebook</h1>
          <p className="text-sm text-muted-foreground">
            View all executed trades
          </p>
        </div>
        <Button
          variant="outline"
          onClick={handleExportCsv}
          disabled={(trades?.length ?? 0) === 0}
          title={
            (trades?.length ?? 0) === 0
              ? "No trades to export"
              : `Export ${trades?.length} trade${trades?.length === 1 ? "" : "s"} as CSV`
          }
        >
          Export CSV
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Trades</CardTitle>
          <CardDescription>
            {trades?.length ?? 0} trade{(trades?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sortedTrades.length > 0 ? (
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
                  <SortableHead
                    sortKey="quantity"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                  >
                    Qty
                  </SortableHead>
                  <SortableHead
                    sortKey="average_price"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                  >
                    Avg Price
                  </SortableHead>
                  <SortableHead
                    sortKey="trade_value"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                  >
                    Trade Value
                  </SortableHead>
                  <SortableHead
                    sortKey="timestamp"
                    current={sort}
                    onSort={handleSort}
                    align="right"
                    className="w-[110px]"
                  >
                    Time
                  </SortableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedTrades.map((trade, i) => (
                  <TableRow key={i} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                    <TableCell className="font-medium">{trade.symbol}</TableCell>
                    <TableCell>{trade.exchange}</TableCell>
                    <TableCell>
                      <span
                        className={
                          trade.action.toUpperCase() === "BUY"
                            ? "font-medium text-green-600 dark:text-green-400"
                            : "font-medium text-red-600 dark:text-red-400"
                        }
                      >
                        {trade.action}
                      </span>
                    </TableCell>
                    <TableCell>{trade.product}</TableCell>
                    <TableCell className="text-right">{trade.quantity}</TableCell>
                    <TableCell className="text-right">
                      {trade.average_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      {trade.trade_value.toFixed(2)}
                    </TableCell>
                    <TableCell
                      className="whitespace-nowrap text-right font-mono text-xs tabular-nums text-muted-foreground"
                      title={formatOrderDateTime(trade.timestamp)}
                    >
                      {formatOrderTime(trade.timestamp)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No trades found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
