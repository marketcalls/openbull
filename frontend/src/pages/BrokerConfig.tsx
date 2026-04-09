import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { listBrokers, getBrokerCredentials, saveBrokerCredentials } from "@/api/broker";
import type { BrokerConfigData } from "@/types/broker";

export default function BrokerConfig() {
  const [selectedBroker, setSelectedBroker] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [redirectUrl, setRedirectUrl] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const queryClient = useQueryClient();

  const { data: brokers, isLoading: brokersLoading } = useQuery({
    queryKey: ["brokers"],
    queryFn: listBrokers,
  });

  useEffect(() => {
    if (selectedBroker) {
      getBrokerCredentials(selectedBroker)
        .then((creds) => {
          setApiKey(creds.api_key || "");
          setApiSecret(creds.api_secret || "");
          setRedirectUrl(creds.redirect_url || "");
        })
        .catch(() => {
          setApiKey("");
          setApiSecret("");
          setRedirectUrl("");
        });
    }
  }, [selectedBroker]);

  const saveMutation = useMutation({
    mutationFn: (data: BrokerConfigData) => saveBrokerCredentials(data),
    onSuccess: () => {
      setMessage("Broker credentials saved successfully.");
      setError("");
      queryClient.invalidateQueries({ queryKey: ["brokers"] });
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setError(axiosErr.response?.data?.detail ?? "Failed to save credentials.");
      setMessage("");
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!selectedBroker) {
      setError("Please select a broker.");
      return;
    }
    saveMutation.mutate({
      broker: selectedBroker,
      api_key: apiKey,
      api_secret: apiSecret,
      redirect_url: redirectUrl,
    });
  };

  if (brokersLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading brokers...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Broker Configuration</h1>
        <p className="text-sm text-muted-foreground">
          Configure your broker API credentials
        </p>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>API Credentials</CardTitle>
          <CardDescription>
            Select a broker and enter your API credentials
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {message && (
              <div className="rounded-md bg-green-600/10 p-3 text-sm text-green-700 dark:text-green-400">
                {message}
              </div>
            )}
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="broker-select">Broker</Label>
              <select
                id="broker-select"
                value={selectedBroker}
                onChange={(e) => setSelectedBroker(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
              >
                <option value="">Select a broker...</option>
                {brokers?.map((b) => (
                  <option key={b.name} value={b.name}>
                    {b.display_name}
                    {b.is_configured ? " (Configured)" : ""}
                  </option>
                ))}
              </select>
            </div>

            {selectedBroker && (
              <>
                {brokers?.find((b) => b.name === selectedBroker)?.is_configured && (
                  <Badge variant="secondary">Currently Configured</Badge>
                )}

                <div className="space-y-2">
                  <Label htmlFor="api-key">API Key</Label>
                  <Input
                    id="api-key"
                    type="text"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="Enter API key"
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="api-secret">API Secret</Label>
                  <Input
                    id="api-secret"
                    type="password"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="Enter API secret"
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="redirect-url">Redirect URL</Label>
                  <Input
                    id="redirect-url"
                    type="url"
                    value={redirectUrl}
                    onChange={(e) => setRedirectUrl(e.target.value)}
                    placeholder="https://your-domain.com/callback"
                    required
                  />
                </div>

                <Button
                  type="submit"
                  disabled={saveMutation.isPending}
                >
                  {saveMutation.isPending ? "Saving..." : "Save Credentials"}
                </Button>
              </>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
