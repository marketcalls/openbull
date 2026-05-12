import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { getApiKey, generateApiKey } from "@/api/apikey";
import { cn } from "@/lib/utils";

const AUTO_HIDE_AFTER_REVEAL_MS = 30_000;

function maskKey(key: string): string {
  if (!key) return "";
  const last4 = key.slice(-4);
  // Fixed-width mask so the field's footprint doesn't visually leak length.
  return `${"•".repeat(28)} ${last4}`;
}

export default function ApiKey() {
  const queryClient = useQueryClient();
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const tickTimer = useRef<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["apikey"],
    queryFn: getApiKey,
  });

  const generateMutation = useMutation({
    mutationFn: generateApiKey,
    onSuccess: (newData) => {
      queryClient.setQueryData(["apikey"], newData);
      // Auto-reveal so user can capture the brand-new key; auto-hide soon after.
      startReveal();
    },
  });

  const clearTimers = () => {
    if (hideTimer.current) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    if (tickTimer.current) {
      window.clearInterval(tickTimer.current);
      tickTimer.current = null;
    }
  };

  const startReveal = () => {
    clearTimers();
    setRevealed(true);
    setCountdown(Math.round(AUTO_HIDE_AFTER_REVEAL_MS / 1000));
    hideTimer.current = window.setTimeout(() => {
      setRevealed(false);
      setCountdown(null);
      clearTimers();
    }, AUTO_HIDE_AFTER_REVEAL_MS);
    tickTimer.current = window.setInterval(() => {
      setCountdown((c) => (c === null ? null : Math.max(0, c - 1)));
    }, 1000);
  };

  const handleToggleReveal = () => {
    if (revealed) {
      clearTimers();
      setRevealed(false);
      setCountdown(null);
    } else {
      startReveal();
    }
  };

  const handleCopy = async () => {
    if (data?.api_key) {
      await navigator.clipboard.writeText(data.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  useEffect(() => () => clearTimers(), []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading API key…</p>
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

  const hasKey = !!data?.api_key;
  const displayValue = hasKey
    ? revealed
      ? data!.api_key
      : maskKey(data!.api_key)
    : "No API key generated yet";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          API Key
        </h1>
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
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="apikey-input">Current API Key</Label>
              {revealed && countdown !== null && (
                <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                  Auto-hides in {countdown}s
                </span>
              )}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="apikey-input"
                type="text"
                value={displayValue}
                readOnly
                aria-label="API key"
                onFocus={(e) => {
                  if (revealed) e.currentTarget.select();
                }}
                className={cn(
                  "font-mono text-xs tracking-tight tabular-nums",
                  !hasKey && "text-muted-foreground italic"
                )}
              />
              {hasKey && (
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    onClick={handleToggleReveal}
                    className="flex-1 sm:flex-none"
                    aria-pressed={revealed}
                    title={revealed ? "Hide API key" : "Reveal API key"}
                  >
                    {revealed ? "Hide" : "Show"}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={handleCopy}
                    className="flex-1 sm:flex-none"
                    title="Copy API key to clipboard"
                  >
                    {copied ? "Copied" : "Copy"}
                  </Button>
                </div>
              )}
            </div>
            {hasKey && !revealed && (
              <p className="text-[11px] text-muted-foreground">
                The key is hidden. Click Show to reveal, or Copy to copy it
                directly to your clipboard.
              </p>
            )}
          </div>

          <div className="flex gap-2 pt-2">
            <Button
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? "Generating…" : "Generate New Key"}
            </Button>
          </div>

          {hasKey && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
              <span className="font-semibold uppercase tracking-[0.1em]">
                Warning
              </span>
              {" — Generating a new key will invalidate the current one. Update any external integrations immediately after rotation."}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
