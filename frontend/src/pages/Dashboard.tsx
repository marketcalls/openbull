import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { getDashboard } from "@/api/dashboard";

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(value);
}

function getPnlColor(value: number): string {
  if (value > 0) return "text-green-600 dark:text-green-400";
  if (value < 0) return "text-red-600 dark:text-red-400";
  return "text-foreground";
}

export default function Dashboard() {
  const { data: funds, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load dashboard data. Please try again.
        </div>
      </div>
    );
  }

  const fundCards = [
    { label: "Available Cash", value: funds?.availablecash ?? 0, isPnl: false },
    { label: "Collateral", value: funds?.collateral ?? 0, isPnl: false },
    { label: "M2M Unrealized", value: funds?.m2munrealized ?? 0, isPnl: true },
    { label: "M2M Realized", value: funds?.m2mrealized ?? 0, isPnl: true },
    { label: "Utilized Debits", value: funds?.utiliseddebits ?? 0, isPnl: false },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Overview of your trading account funds
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {fundCards.map((card) => (
          <Card key={card.label}>
            <CardHeader>
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {card.label}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p
                className={`text-2xl font-bold ${
                  card.isPnl ? getPnlColor(card.value) : "text-foreground"
                }`}
              >
                {formatCurrency(card.value)}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
