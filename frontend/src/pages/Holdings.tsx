import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { getHoldings } from "@/api/dashboard";
import { useLivePrice } from "@/hooks/useLivePrice";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import type { HoldingItem } from "@/types/order";

function getPnlColor(value: number): string {
  if (value > 0) return "text-green-600 dark:text-green-400";
  if (value < 0) return "text-red-600 dark:text-red-400";
  return "";
}

export default function Holdings() {
  const { data: holdings, isLoading, error } = useQuery({
    queryKey: ["holdings"],
    queryFn: getHoldings,
    refetchInterval: 30000,
  });

  // Live overlay: stream each holding's LTP over the WS proxy and recompute
  // P&L / P&L% per tick. Hook is called unconditionally (before early returns)
  // to respect the rules of hooks.
  const { data: liveHoldings, isLive, isPaused } = useLivePrice(holdings ?? [], {
    enabled: (holdings?.length ?? 0) > 0,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading holdings...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load holdings.
        </div>
      </div>
    );
  }

  const handleExportCsv = () => {
    const rows = holdings ?? [];
    if (rows.length === 0) return;
    const columns: CsvColumn<HoldingItem>[] = [
      { header: "Symbol", value: (r) => r.symbol },
      { header: "Exchange", value: (r) => r.exchange },
      { header: "Product", value: (r) => r.product },
      { header: "Quantity", value: (r) => r.quantity },
      { header: "Average Price", value: (r) => r.average_price.toFixed(2) },
      { header: "LTP", value: (r) => r.ltp.toFixed(2) },
      { header: "P&L", value: (r) => r.pnl.toFixed(2) },
      { header: "P&L %", value: (r) => r.pnlpercent.toFixed(2) },
    ];
    downloadCsv({ filename: "holdings", columns, rows });
    toast.success(`Exported ${rows.length} holding${rows.length === 1 ? "" : "s"} to CSV`);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Holdings</h1>
          <p className="text-sm text-muted-foreground">
            View your stock holdings
          </p>
        </div>
        <Button
          variant="outline"
          onClick={handleExportCsv}
          disabled={(holdings?.length ?? 0) === 0}
          title={
            (holdings?.length ?? 0) === 0
              ? "No holdings to export"
              : `Export ${holdings?.length} holding${holdings?.length === 1 ? "" : "s"} as CSV`
          }
        >
          Export CSV
        </Button>
      </div>

      {isPaused && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          Live updates paused (tab inactive) — showing last fetched prices.
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Stock Holdings
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
            {holdings?.length ?? 0} holding{(holdings?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {holdings && holdings.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg Price</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">P&L %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {liveHoldings.map((holding, i) => (
                  <TableRow key={i} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                    <TableCell className="font-medium">{holding.symbol}</TableCell>
                    <TableCell>{holding.exchange}</TableCell>
                    <TableCell className="text-right">{holding.quantity}</TableCell>
                    <TableCell className="text-right">
                      {holding.average_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      {holding.ltp.toFixed(2)}
                    </TableCell>
                    <TableCell className={`text-right font-medium ${getPnlColor(holding.pnl)}`}>
                      {holding.pnl >= 0 ? "+" : ""}
                      {holding.pnl.toFixed(2)}
                    </TableCell>
                    <TableCell className={`text-right font-medium ${getPnlColor(holding.pnlpercent)}`}>
                      {holding.pnlpercent >= 0 ? "+" : ""}
                      {holding.pnlpercent.toFixed(2)}%
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No holdings found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
