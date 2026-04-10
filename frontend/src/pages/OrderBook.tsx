import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { getOrderbook } from "@/api/dashboard";

function getStatusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  const s = status.toLowerCase();
  if (s === "complete" || s === "filled") return "default";
  if (s === "rejected" || s === "cancelled") return "destructive";
  if (s === "open" || s === "pending") return "secondary";
  return "outline";
}

export default function OrderBook() {
  const { data: orders, isLoading, error } = useQuery({
    queryKey: ["orderbook"],
    queryFn: getOrderbook,
    refetchInterval: 15000,
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Orderbook</h1>
        <p className="text-sm text-muted-foreground">
          View all your orders
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Orders</CardTitle>
          <CardDescription>
            {orders?.length ?? 0} order{(orders?.length ?? 0) !== 1 ? "s" : ""} found
          </CardDescription>
        </CardHeader>
        <CardContent>
          {orders && orders.length > 0 ? (
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
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {orders.map((order, i) => (
                  <TableRow key={i} className={i % 2 === 0 ? "bg-muted/30" : ""}>
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
                    <TableCell>
                      <Badge variant={getStatusVariant(order.order_status)}>
                        {order.order_status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No orders found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
