import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { getTradebook } from "@/api/dashboard";

export default function TradeBook() {
  const { data: trades, isLoading, error } = useQuery({
    queryKey: ["tradebook"],
    queryFn: getTradebook,
    refetchInterval: 15000,
  });

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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Tradebook</h1>
        <p className="text-sm text-muted-foreground">
          View all executed trades
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Trades</CardTitle>
          <CardDescription>
            {trades?.length ?? 0} trade{(trades?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {trades && trades.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Price Type</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead className="text-right">Trade Value</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((trade, i) => (
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
                    <TableCell>{trade.price_type}</TableCell>
                    <TableCell className="text-right">{trade.quantity}</TableCell>
                    <TableCell className="text-right">
                      {trade.price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      {trade.trade_value.toFixed(2)}
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
