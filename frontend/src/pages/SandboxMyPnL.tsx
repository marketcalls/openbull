import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getSandboxMyPnL, type SandboxDailyPnLRow } from "@/api/sandbox";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { cn } from "@/lib/utils";

function fmtINR(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v < 0 ? "-" : "") + "₹" + Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function pnlToneClass(v: number): string {
  if (v > 0) return "text-green-600";
  if (v < 0) return "text-red-600";
  return "text-muted-foreground";
}

/**
 * Minimal CSS-only chart: bars rendered with divs, sized against the max
 * absolute value in the window. Avoids pulling in a chart library for a
 * single glance-friendly history view. Positive bars grow upward (green),
 * negative bars grow downward (red).
 */
function PnLSparkBars({ rows }: { rows: SandboxDailyPnLRow[] }) {
  const data = useMemo(() => [...rows].reverse(), [rows]); // oldest -> newest
  const maxAbs = useMemo(() => {
    let m = 0;
    for (const r of data) m = Math.max(m, Math.abs(r.total_pnl));
    return m || 1;
  }, [data]);

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No P&L snapshots yet. They land at 23:55 IST each day, or when an admin
        hits the "Settle now" button on /sandbox.
      </p>
    );
  }

  return (
    <div className="flex items-stretch gap-0.5 overflow-x-auto pb-2">
      {data.map((r) => {
        const ratio = Math.abs(r.total_pnl) / maxAbs;
        const heightPct = Math.max(2, ratio * 100); // at least 2px visible
        const positive = r.total_pnl >= 0;
        return (
          <div
            key={r.date}
            className="flex min-w-[10px] flex-col items-center"
            title={`${r.date}: ${fmtINR(r.total_pnl)}`}
          >
            <div className="relative flex h-24 w-full items-center justify-center">
              <div
                className={cn(
                  "absolute w-full rounded-sm transition-all",
                  positive
                    ? "bottom-1/2 bg-green-500/70"
                    : "top-1/2 bg-red-500/70"
                )}
                style={{ height: `${heightPct / 2}%` }}
              />
              {/* zero line */}
              <div className="absolute left-0 right-0 top-1/2 border-t border-border" />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function SandboxMyPnL() {
  const { isSandbox } = useTradingMode();
  const query = useQuery({
    queryKey: ["sandbox-mypnl"],
    queryFn: () => getSandboxMyPnL(180),
    refetchInterval: isSandbox ? 30_000 : false,
  });

  const rows = query.data ?? [];
  const totals = useMemo(() => {
    let realized = 0;
    let unrealized = 0;
    let trades = 0;
    for (const r of rows) {
      realized += r.realized_pnl;
      unrealized += r.unrealized_pnl;
      trades += r.trades_count;
    }
    return { realized, unrealized, trades };
  }, [rows]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Sandbox P&amp;L History</h1>
        <p className="text-sm text-muted-foreground">
          One snapshot per trading day written at 23:55 IST. Shows your own
          sandbox performance — other users' rows are never returned.
        </p>
      </div>

      {/* Aggregates */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="Snapshots"
          value={rows.length}
        />
        <StatCard
          label="Cumulative realized"
          value={fmtINR(totals.realized)}
          toneClass={pnlToneClass(totals.realized)}
        />
        <StatCard
          label="Latest unrealized"
          value={fmtINR(rows[0]?.unrealized_pnl ?? 0)}
          toneClass={pnlToneClass(rows[0]?.unrealized_pnl ?? 0)}
        />
        <StatCard
          label="Trades in window"
          value={totals.trades}
        />
      </div>

      {/* Chart */}
      <Card>
        <CardHeader>
          <CardTitle>Daily total P&amp;L</CardTitle>
          <CardDescription>
            Newest day on the right. Bars scale to the max absolute total P&amp;L in
            the window.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <PnLSparkBars rows={rows} />
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>Rows</CardTitle>
          <CardDescription>Most recent 180 days, newest first.</CardDescription>
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No snapshots yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Starting</TableHead>
                    <TableHead className="text-right">Available</TableHead>
                    <TableHead className="text-right">Used margin</TableHead>
                    <TableHead className="text-right">Realized</TableHead>
                    <TableHead className="text-right">Unrealized</TableHead>
                    <TableHead className="text-right">Total P&amp;L</TableHead>
                    <TableHead className="text-right">Trades</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.date}>
                      <TableCell className="whitespace-nowrap">
                        <Badge variant="outline">{r.date}</Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtINR(r.starting_capital)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtINR(r.available)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {fmtINR(r.used_margin)}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono", pnlToneClass(r.realized_pnl))}
                      >
                        {fmtINR(r.realized_pnl)}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono", pnlToneClass(r.unrealized_pnl))}
                      >
                        {fmtINR(r.unrealized_pnl)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono font-medium",
                          pnlToneClass(r.total_pnl)
                        )}
                      >
                        {fmtINR(r.total_pnl)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {r.trades_count}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({
  label,
  value,
  toneClass,
}: {
  label: string;
  value: string | number;
  toneClass?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={cn("text-xl font-semibold", toneClass)}>{value}</p>
    </div>
  );
}
