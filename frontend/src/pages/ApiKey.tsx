import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { getApiKey, generateApiKey } from "@/api/apikey";

export default function ApiKey() {
  const [copied, setCopied] = useState(false);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["apikey"],
    queryFn: getApiKey,
  });

  const generateMutation = useMutation({
    mutationFn: generateApiKey,
    onSuccess: (newData) => {
      queryClient.setQueryData(["apikey"], newData);
    },
  });

  const handleCopy = async () => {
    if (data?.api_key) {
      await navigator.clipboard.writeText(data.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading API key...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load API key.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">API Key</h1>
        <p className="text-sm text-muted-foreground">
          Manage your API key for external access
        </p>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Your API Key</CardTitle>
          <CardDescription>
            Use this key to authenticate external API requests
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Current API Key</Label>
            <div className="flex gap-2">
              <Input
                type="text"
                value={data?.api_key || "No API key generated yet"}
                readOnly
                className="font-mono text-xs"
              />
              {data?.api_key && (
                <Button
                  variant="outline"
                  onClick={handleCopy}
                >
                  {copied ? "Copied" : "Copy"}
                </Button>
              )}
            </div>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? "Generating..." : "Generate New Key"}
            </Button>
          </div>

          {data?.api_key && (
            <p className="text-xs text-muted-foreground">
              Warning: Generating a new key will invalidate the current one.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
