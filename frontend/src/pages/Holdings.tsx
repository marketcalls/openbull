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
import { getHoldings } from "@/api/dashboard";

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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Holdings</h1>
        <p className="text-sm text-muted-foreground">
          View your stock holdings
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Stock Holdings</CardTitle>
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
                {holdings.map((holding, i) => (
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
