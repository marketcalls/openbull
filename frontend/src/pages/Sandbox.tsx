import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/contexts/AuthContext";
import { useTradingMode } from "@/contexts/TradingModeContext";
import {
  getSandboxConfigs,
  getSandboxSummary,
  resetSandbox,
  settleNow,
  squareoffNow,
  updateSandboxConfig,
  type SandboxConfigMap,
} from "@/api/sandbox";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/**
 * Groups the flat key/value config list into logical sections so the UI
 * reads like a settings page rather than a 10-row key-value dump. Keys that
 * aren't recognised still render, in a "Misc" bucket.
 */
const GROUPS: { title: string; keys: string[]; hint?: string }[] = [
  {
    title: "Capital",
    keys: ["starting_capital"],
    hint: "Funds available when a user first switches to sandbox mode or resets.",
  },
  {
    title: "Leverage",
    keys: ["leverage_mis", "leverage_nrml", "leverage_cnc"],
    hint: "Margin multiplier per product type. 5 = 20% margin for MIS.",
  },
  {
    title: "Auto Square-off (IST, HH:MM)",
    keys: [
      "squareoff_nse_nfo_bse_bfo",
      "squareoff_cds",
      "squareoff_mcx",
    ],
    hint: "MIS positions are auto-flattened at these exchange-specific times. (Scheduler lands in Phase 2b.)",
  },
  {
    title: "Engine",
    keys: ["order_check_interval_seconds", "mtm_update_interval_seconds"],
    hint: "How often the execution engine polls pending orders as a fallback to the tick-driven path.",
  },
  {
    title: "Weekly Reset",
    keys: ["reset_day", "reset_time"],
    hint: "Optional: auto-reset all sandbox state on this day/time. (Scheduler lands in Phase 2b.)",
  },
];

function formatNumber(v: number | undefined): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function Sandbox() {
  const { user } = useAuth();
  const { isSandbox } = useTradingMode();
  const qc = useQueryClient();

  const cfgQuery = useQuery({
    queryKey: ["sandbox-config"],
    queryFn: getSandboxConfigs,
    staleTime: 30_000,
  });
  const summaryQuery = useQuery({
    queryKey: ["sandbox-summary"],
    queryFn: getSandboxSummary,
    refetchInterval: isSandbox ? 5_000 : false,
  });

  const updateMutation = useMutation({
    mutationFn: (args: { key: string; value: string }) =>
      updateSandboxConfig(args.key, args.value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sandbox-config"] });
    },
  });

  const resetMutation = useMutation({
    mutationFn: resetSandbox,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sandbox-summary"] });
      // Force funds/positions/orderbook to refetch so the UI reflects the wipe.
      qc.invalidateQueries({ predicate: (q) => {
        const k = q.queryKey[0];
        return k === "orderbook" || k === "tradebook" || k === "positions" ||
               k === "holdings" || k === "funds" || k === "dashboard";
      }});
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Sandbox</h1>
          <p className="text-sm text-muted-foreground">
            Capital, leverage and square-off settings for the simulated trading
            engine. Changes apply globally; the per-user reset below only wipes
            your own orders / positions / funds.
          </p>
        </div>
        <Badge variant={isSandbox ? "default" : "outline"} className="self-start">
          {isSandbox ? "Active" : "Inactive"}
        </Badge>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Sandbox orders (all users)"
          value={summaryQuery.data?.total_orders ?? "—"}
        />
        <StatCard
          label="Your available"
          value={`₹${formatNumber(summaryQuery.data?.funds.availablecash)}`}
          tone="good"
        />
        <StatCard
          label="Your used margin"
          value={`₹${formatNumber(summaryQuery.data?.funds.utiliseddebits)}`}
        />
        <StatCard
          label="Your realized P&L"
          value={`₹${formatNumber(summaryQuery.data?.funds.m2mrealized)}`}
          tone={
            (summaryQuery.data?.funds.m2mrealized ?? 0) > 0
              ? "good"
              : (summaryQuery.data?.funds.m2mrealized ?? 0) < 0
                ? "bad"
                : undefined
          }
        />
      </div>

      {/* Config groups */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {GROUPS.map((g) => (
          <ConfigGroupCard
            key={g.title}
            title={g.title}
            hint={g.hint}
            keys={g.keys}
            configs={cfgQuery.data}
            canEdit={!!user?.is_admin}
            onSave={(key, value) => updateMutation.mutateAsync({ key, value })}
          />
        ))}
      </div>

      {/* Reset */}
      <Card>
        <CardHeader>
          <CardTitle>Reset my sandbox</CardTitle>
          <CardDescription>
            Deletes your sandbox orders, trades, positions, holdings and resets
            funds to the current <code className="font-mono">starting_capital</code>.
            Other users are unaffected. Cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="destructive"
            disabled={resetMutation.isPending}
            onClick={() => {
              if (
                window.confirm(
                  "This will wipe your sandbox orders, trades, positions and holdings, and reset your funds. Continue?"
                )
              ) {
                resetMutation.mutate();
              }
            }}
          >
            {resetMutation.isPending ? "Resetting…" : "Reset my sandbox"}
          </Button>
          {resetMutation.isSuccess && (
            <span className="ml-3 text-sm text-green-600">Reset complete.</span>
          )}
          {resetMutation.isError && (
            <span className="ml-3 text-sm text-destructive">
              Reset failed. Check the error log.
            </span>
          )}
        </CardContent>
      </Card>

      {/* Manual triggers + mypnl link */}
      {user?.is_admin && <AdminActions />}
      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
          <CardDescription>
            Daily P&amp;L snapshots are written at 23:55 IST (or via "Settle
            now" below). View them at{" "}
            <Link to="/sandbox/mypnl" className="font-medium underline">
              /sandbox/mypnl
            </Link>
            .
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

function AdminActions() {
  const qc = useQueryClient();
  const [lastMessage, setLastMessage] = useState<string | null>(null);

  const squareoff = useMutation({
    mutationFn: (bucket: "nse_nfo_bse_bfo" | "cds" | "mcx") =>
      squareoffNow(bucket),
    onSuccess: (r, bucket) => {
      setLastMessage(`Placed ${r.placed} reverse MARKET order(s) for ${bucket}.`);
      qc.invalidateQueries({ queryKey: ["sandbox-summary"] });
      qc.invalidateQueries({ predicate: (q) => {
        const k = q.queryKey[0];
        return k === "positions" || k === "orderbook" || k === "funds";
      }});
    },
    onError: () => setLastMessage("Square-off failed; see server logs."),
  });

  const settle = useMutation({
    mutationFn: settleNow,
    onSuccess: (r) => {
      setLastMessage(
        `Moved ${r.holdings_moved} CNC position(s) to holdings, wrote ${r.pnl_snapshots_written} P&L snapshot(s).`
      );
      qc.invalidateQueries({ queryKey: ["sandbox-summary"] });
      qc.invalidateQueries({ queryKey: ["sandbox-mypnl"] });
      qc.invalidateQueries({ predicate: (q) => {
        const k = q.queryKey[0];
        return k === "holdings" || k === "positions";
      }});
    },
    onError: () => setLastMessage("Settlement failed; see server logs."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Admin — manual triggers</CardTitle>
        <CardDescription>
          Fire the scheduled jobs on demand. Normally the scheduler runs these
          at the configured IST times; these buttons are for testing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => squareoff.mutate("nse_nfo_bse_bfo")}
            disabled={squareoff.isPending}
          >
            Square-off NSE/NFO/BSE/BFO
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => squareoff.mutate("cds")}
            disabled={squareoff.isPending}
          >
            Square-off CDS
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => squareoff.mutate("mcx")}
            disabled={squareoff.isPending}
          >
            Square-off MCX
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => settle.mutate()}
            disabled={settle.isPending}
          >
            Settle now (T+1 + P&amp;L snapshot)
          </Button>
        </div>
        {lastMessage && (
          <p className="mt-3 text-sm text-muted-foreground">{lastMessage}</p>
        )}
      </CardContent>
    </Card>
  );
}

function ConfigGroupCard({
  title,
  hint,
  keys,
  configs,
  canEdit,
  onSave,
}: {
  title: string;
  hint?: string;
  keys: string[];
  configs: SandboxConfigMap | undefined;
  canEdit: boolean;
  onSave: (key: string, value: string) => Promise<void>;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {hint && <CardDescription>{hint}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-3">
        {keys.map((k) => {
          const entry = configs?.[k];
          if (!entry) {
            return (
              <div key={k} className="text-xs text-muted-foreground">
                <span className="font-mono">{k}</span>: <em>loading…</em>
              </div>
            );
          }
          return (
            <ConfigRow
              key={k}
              configKey={k}
              entry={entry}
              canEdit={canEdit}
              onSave={onSave}
            />
          );
        })}
      </CardContent>
    </Card>
  );
}

function ConfigRow({
  configKey,
  entry,
  canEdit,
  onSave,
}: {
  configKey: string;
  entry: { value: string; description: string; is_editable: boolean };
  canEdit: boolean;
  onSave: (key: string, value: string) => Promise<void>;
}) {
  const [value, setValue] = useState(entry.value);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const dirty = useMemo(() => value !== entry.value, [value, entry.value]);
  const editable = canEdit && entry.is_editable;

  const handleSave = useCallback(async () => {
    setErr(null);
    setSaving(true);
    try {
      await onSave(configKey, value);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [onSave, configKey, value]);

  return (
    <div>
      <Label htmlFor={`cfg-${configKey}`} className="text-xs">
        <span className="font-mono">{configKey}</span>
        {entry.description && (
          <span className="ml-2 font-normal text-muted-foreground">
            — {entry.description}
          </span>
        )}
      </Label>
      <div className="mt-1 flex items-center gap-2">
        <Input
          id={`cfg-${configKey}`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={!editable || saving}
          className={cn(
            "font-mono",
            !editable && "cursor-not-allowed opacity-70"
          )}
        />
        {dirty && editable && (
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? "…" : "Save"}
          </Button>
        )}
        {dirty && editable && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setValue(entry.value);
              setErr(null);
            }}
            disabled={saving}
          >
            Cancel
          </Button>
        )}
      </div>
      {err && <p className="mt-1 text-xs text-destructive">{err}</p>}
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "good" | "bad";
}) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-xl font-semibold",
          tone === "good" && "text-green-600",
          tone === "bad" && "text-red-600"
        )}
      >
        {value}
      </p>
    </div>
  );
}
