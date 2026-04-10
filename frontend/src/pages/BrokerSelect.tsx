import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { listBrokers, getBrokerRedirectUrl } from "@/api/broker";

export default function BrokerSelect() {
  const [redirecting, setRedirecting] = useState<string | null>(null);

  const { data: brokers, isLoading, error } = useQuery({
    queryKey: ["brokers"],
    queryFn: listBrokers,
  });

  const handleBrokerClick = async (brokerName: string) => {
    setRedirecting(brokerName);
    try {
      const response = await getBrokerRedirectUrl(brokerName);
      window.location.href = response.url;
    } catch {
      setRedirecting(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading brokers...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load brokers. Please try again.
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="w-full max-w-3xl space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold tracking-tight">Select Broker</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose a broker to authenticate with
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {brokers?.map((broker) => (
            <Card key={broker.name} className="transition-shadow hover:shadow-md">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>{broker.display_name}</CardTitle>
                  {broker.is_configured ? (
                    <Badge variant="secondary">Configured</Badge>
                  ) : (
                    <Badge variant="outline">Not Configured</Badge>
                  )}
                </div>
                <CardDescription>
                  Exchanges: {broker.supported_exchanges.join(", ")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {broker.is_configured ? (
                  <Button
                    className="w-full"
                    onClick={() => handleBrokerClick(broker.name)}
                    disabled={redirecting === broker.name}
                  >
                    {redirecting === broker.name
                      ? "Redirecting..."
                      : "Login with " + broker.display_name}
                  </Button>
                ) : (
                  <Link to="/broker/config">
                    <Button variant="outline" className="w-full">
                      Configure
                    </Button>
                  </Link>
                )}
              </CardContent>
            </Card>
          ))}
        </div>

        {brokers?.length === 0 && (
          <div className="text-center">
            <p className="text-sm text-muted-foreground">
              No brokers available.{" "}
              <Link to="/broker/config" className="font-medium text-primary underline underline-offset-4">
                Configure a broker
              </Link>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
