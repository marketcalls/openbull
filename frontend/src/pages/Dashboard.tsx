import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  getDashboard,
  getHoldings,
  getOrderbook,
  getPositions,
  getTradebook,
} from "@/api/dashboard";
import { listStrategies } from "@/api/strategy_module";
import { cn } from "@/lib/utils";
import type {
  OrderbookItem,
  PositionItem,
  TradebookItem,
} from "@/types/order";
import type { StrategyListItem } from "@/types/strategy_module";

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});

const COMPACT_NUMBER = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 0,
});

function formatCurrency(value: number): string {
  return INR.format(value);
}

function formatCompactINR(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(1)} K`;
  return `${sign}₹${abs.toFixed(2)}`;
}

function pnlColor(value: number): string {
  if (value > 0) return "text-emerald-600 dark:text-emerald-400";
  if (value < 0) return "text-rose-600 dark:text-rose-400";
  return "text-foreground";
}

function pnlBgTint(value: number): string {
  if (value > 0)
    return "bg-emerald-500/8 ring-emerald-500/30 dark:bg-emerald-500/10";
  if (value < 0) return "bg-rose-500/8 ring-rose-500/30 dark:bg-rose-500/10";
  return "bg-muted/40 ring-border";
}

function withSign(value: number, formatter: (n: number) => string): string {
  if (value > 0) return `+${formatter(value)}`;
  return formatter(value);
}

// ---------------------------------------------------------------------------
// Order/trade visual helpers
// ---------------------------------------------------------------------------

type OrderTone = "complete" | "pending" | "rejected" | "cancelled";

function orderTone(status: string): OrderTone {
  const s = status.toLowerCase();
  if (s.includes("complete") || s.includes("filled") || s.includes("executed"))
    return "complete";
  if (s.includes("reject")) return "rejected";
  if (s.includes("cancel")) return "cancelled";
  return "pending";
}

const orderToneStyles: Record<OrderTone, { dot: string; label: string }> = {
  complete: { dot: "bg-emerald-500", label: "text-emerald-600 dark:text-emerald-400" },
  pending: { dot: "bg-amber-500", label: "text-amber-600 dark:text-amber-400" },
  rejected: { dot: "bg-rose-500", label: "text-rose-600 dark:text-rose-400" },
  cancelled: { dot: "bg-muted-foreground/60", label: "text-muted-foreground" },
};

function actionTone(action: string): string {
  return action.toUpperCase() === "BUY"
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

// ---------------------------------------------------------------------------
// Loaders for sub-sections (all reuse-existing-endpoints, low staleness)
// ---------------------------------------------------------------------------

function useDashboardData() {
  const funds = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 30_000,
  });
  const positions = useQuery({
    queryKey: ["dashboard", "positions"],
    queryFn: getPositions,
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
  const holdings = useQuery({
    queryKey: ["dashboard", "holdings"],
    queryFn: getHoldings,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  const orders = useQuery({
    queryKey: ["dashboard", "orderbook"],
    queryFn: getOrderbook,
    refetchInterval: 20_000,
    staleTime: 10_000,
  });
  const trades = useQuery({
    queryKey: ["dashboard", "tradebook"],
    queryFn: getTradebook,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  const strategies = useQuery({
    queryKey: ["dashboard", "strategies"],
    queryFn: () => listStrategies(),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  return { funds, positions, holdings, orders, trades, strategies };
}

// ---------------------------------------------------------------------------
// Section: KPI strip
// ---------------------------------------------------------------------------

function KpiStrip({
  dayPnl,
  openPositions,
  holdingsCount,
  runningStrategies,
  totalStrategies,
}: {
  dayPnl: number;
  openPositions: number;
  holdingsCount: number;
  runningStrategies: number;
  totalStrategies: number;
}) {
  const items = [
    {
      label: "Day P&L",
      value: withSign(dayPnl, formatCompactINR),
      hint: "Realized + unrealized today",
      valueClass: pnlColor(dayPnl),
      tint: pnlBgTint(dayPnl),
    },
    {
      label: "Open Positions",
      value: COMPACT_NUMBER.format(openPositions),
      hint: openPositions === 0 ? "No active exposure" : "Net active legs",
      valueClass: "text-foreground",
      tint: "bg-muted/40 ring-border",
    },
    {
      label: "Holdings",
      value: COMPACT_NUMBER.format(holdingsCount),
      hint: "Long-term inventory",
      valueClass: "text-foreground",
      tint: "bg-muted/40 ring-border",
    },
    {
      label: "Strategies",
      value: `${runningStrategies} / ${totalStrategies}`,
      hint: runningStrategies > 0 ? "Currently running" : "None running",
      valueClass: runningStrategies > 0 ? "text-foreground" : "text-muted-foreground",
      tint:
        runningStrategies > 0
          ? "bg-emerald-500/8 ring-emerald-500/25 dark:bg-emerald-500/10"
          : "bg-muted/40 ring-border",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {items.map((it) => (
        <div
          key={it.label}
          className={cn(
            "rounded-lg px-3 py-3 ring-1 ring-inset sm:px-4 sm:py-3.5",
            it.tint
          )}
        >
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {it.label}
          </p>
          <p
            className={cn(
              "mt-1 text-xl font-bold tracking-tight tabular-nums sm:text-2xl",
              it.valueClass
            )}
          >
            {it.value}
          </p>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground/85">
            {it.hint}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Positions snapshot
// ---------------------------------------------------------------------------

function PositionsSnapshot({
  positions,
  isLoading,
}: {
  positions: PositionItem[];
  isLoading: boolean;
}) {
  const sorted = useMemo(
    () =>
      [...positions]
        .filter((p) => p.quantity !== 0)
        .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
        .slice(0, 6),
    [positions]
  );

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-3">
        <div>
          <CardTitle className="text-sm font-semibold tracking-tight">
            Open Positions
          </CardTitle>
          <p className="text-[11px] text-muted-foreground">
            Sorted by absolute P&amp;L
          </p>
        </div>
        <Link
          to="/positions"
          className="text-[11px] font-medium tracking-tight text-muted-foreground hover:text-foreground"
        >
          View all →
        </Link>
      </CardHeader>
      <CardContent className="flex-1 pt-0">
        {isLoading ? (
          <SkeletonRows />
        ) : sorted.length === 0 ? (
          <EmptyState
            title="No open positions"
            hint="Place an order to see live P&L here."
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {sorted.map((p) => (
              <li
                key={`${p.symbol}-${p.exchange}-${p.product}`}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold tracking-tight">
                      {p.symbol}
                    </span>
                    <span className="rounded border border-border/70 bg-muted/40 px-1 py-0.5 text-[9px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                      {p.exchange}
                    </span>
                    <span className="rounded border border-border/70 bg-muted/40 px-1 py-0.5 text-[9px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                      {p.product}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                    {p.quantity > 0 ? "LONG" : "SHORT"}{" "}
                    {Math.abs(p.quantity).toLocaleString("en-IN")} · avg{" "}
                    {p.average_price.toFixed(2)} · ltp {p.ltp.toFixed(2)}
                  </p>
                </div>
                <div className="text-right">
                  <p
                    className={cn(
                      "text-sm font-semibold tabular-nums",
                      pnlColor(p.pnl)
                    )}
                  >
                    {withSign(p.pnl, formatCompactINR)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Active strategies
// ---------------------------------------------------------------------------

function StrategiesSnapshot({
  strategies,
  isLoading,
}: {
  strategies: StrategyListItem[];
  isLoading: boolean;
}) {
  const ranked = useMemo(
    () =>
      [...strategies]
        .sort((a, b) => {
          // Running first, then by abs pnl
          const aRun = a.status === "running" ? 1 : 0;
          const bRun = b.status === "running" ? 1 : 0;
          if (aRun !== bRun) return bRun - aRun;
          return Math.abs(b.pnl_total) - Math.abs(a.pnl_total);
        })
        .slice(0, 5),
    [strategies]
  );

  const statusToneMap: Record<string, { dot: string; label: string }> = {
    running: { dot: "bg-emerald-500 animate-pulse", label: "Running" },
    paused: { dot: "bg-amber-500", label: "Paused" },
    errored: { dot: "bg-rose-500", label: "Error" },
    stopped: { dot: "bg-muted-foreground/40", label: "Idle" },
  };

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-3">
        <div>
          <CardTitle className="text-sm font-semibold tracking-tight">
            Strategies
          </CardTitle>
          <p className="text-[11px] text-muted-foreground">
            Live runtime + cumulative P&amp;L
          </p>
        </div>
        <Link
          to="/strategy"
          className="text-[11px] font-medium tracking-tight text-muted-foreground hover:text-foreground"
        >
          View all →
        </Link>
      </CardHeader>
      <CardContent className="flex-1 pt-0">
        {isLoading ? (
          <SkeletonRows />
        ) : ranked.length === 0 ? (
          <EmptyState
            title="No strategies yet"
            hint="Author your first strategy from Strategies → Strategy Builder."
            cta={{ label: "Build a strategy", to: "/tools/strategybuilder" }}
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {ranked.map((s) => {
              const tone = statusToneMap[s.status] || statusToneMap.stopped;
              return (
                <li
                  key={s.id}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <Link
                    to={`/strategy/${s.id}`}
                    className="min-w-0 flex-1 group"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
                          tone.dot
                        )}
                        aria-hidden
                      />
                      <span className="truncate text-sm font-semibold tracking-tight group-hover:underline">
                        {s.name}
                      </span>
                      {s.live_enabled && (
                        <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] text-emerald-600 dark:text-emerald-400">
                          Live
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {s.underlying} · {s.strategy_type} · {tone.label}
                    </p>
                  </Link>
                  <div className="text-right">
                    <p
                      className={cn(
                        "text-sm font-semibold tabular-nums",
                        pnlColor(s.pnl_total)
                      )}
                    >
                      {withSign(s.pnl_total, formatCompactINR)}
                    </p>
                    <p className="text-[10px] text-muted-foreground tabular-nums">
                      U {withSign(s.pnl_unrealized, formatCompactINR)}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Recent activity (orders + trades)
// ---------------------------------------------------------------------------

function RecentOrders({
  orders,
  isLoading,
}: {
  orders: OrderbookItem[];
  isLoading: boolean;
}) {
  const recent = useMemo(() => orders.slice(0, 6), [orders]);

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-3">
        <div>
          <CardTitle className="text-sm font-semibold tracking-tight">
            Recent Orders
          </CardTitle>
          <p className="text-[11px] text-muted-foreground">
            Latest entries from orderbook
          </p>
        </div>
        <Link
          to="/orderbook"
          className="text-[11px] font-medium tracking-tight text-muted-foreground hover:text-foreground"
        >
          View all →
        </Link>
      </CardHeader>
      <CardContent className="flex-1 pt-0">
        {isLoading ? (
          <SkeletonRows />
        ) : recent.length === 0 ? (
          <EmptyState title="No orders today" hint="New orders will appear here." />
        ) : (
          <ul className="divide-y divide-border/60">
            {recent.map((o) => {
              const tone = orderToneStyles[orderTone(o.order_status)];
              return (
                <li
                  key={o.orderid}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", tone.dot)}
                        aria-hidden
                      />
                      <span className="truncate text-sm font-semibold tracking-tight">
                        {o.symbol}
                      </span>
                      <span className={cn("text-[10px] font-semibold uppercase tracking-[0.1em]", actionTone(o.action))}>
                        {o.action}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                      {o.quantity}{" "}
                      <span className="text-muted-foreground/70">@</span>{" "}
                      {o.pricetype === "MARKET"
                        ? "MKT"
                        : o.price?.toFixed(2) ?? "—"}{" "}
                      · {o.product}
                    </p>
                  </div>
                  <span className={cn("text-[10px] font-semibold uppercase tracking-[0.1em]", tone.label)}>
                    {o.order_status}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RecentTrades({
  trades,
  isLoading,
}: {
  trades: TradebookItem[];
  isLoading: boolean;
}) {
  const recent = useMemo(() => trades.slice(0, 6), [trades]);

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-3">
        <div>
          <CardTitle className="text-sm font-semibold tracking-tight">
            Recent Trades
          </CardTitle>
          <p className="text-[11px] text-muted-foreground">Executed fills</p>
        </div>
        <Link
          to="/tradebook"
          className="text-[11px] font-medium tracking-tight text-muted-foreground hover:text-foreground"
        >
          View all →
        </Link>
      </CardHeader>
      <CardContent className="flex-1 pt-0">
        {isLoading ? (
          <SkeletonRows />
        ) : recent.length === 0 ? (
          <EmptyState
            title="No trades yet"
            hint="Filled orders will land here as trades."
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {recent.map((t) => (
              <li
                key={`${t.orderid}-${t.symbol}`}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold tracking-tight">
                      {t.symbol}
                    </span>
                    <span className={cn("text-[10px] font-semibold uppercase tracking-[0.1em]", actionTone(t.action))}>
                      {t.action}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                    {t.quantity}{" "}
                    <span className="text-muted-foreground/70">@</span>{" "}
                    {t.average_price.toFixed(2)} · {t.product}
                  </p>
                </div>
                <p className="text-[11px] font-medium text-muted-foreground tabular-nums">
                  {formatCompactINR(t.trade_value)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Quick actions + market clock
// ---------------------------------------------------------------------------

const quickActions: Array<{
  label: string;
  to: string;
  hint: string;
}> = [
  { label: "Option Chain", to: "/tools/optionchain", hint: "Strikes, OI, IV" },
  { label: "OI Tracker", to: "/tools/oitracker", hint: "Intraday OI shifts" },
  { label: "Max Pain", to: "/tools/maxpain", hint: "Expiry magnet" },
  { label: "Strategy Builder", to: "/tools/strategybuilder", hint: "Payoff design" },
  { label: "GEX Dashboard", to: "/tools/gex", hint: "Dealer gamma" },
  { label: "Search Symbol", to: "/search", hint: "⌘K shortcut" },
];

function QuickActions() {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold tracking-tight">
          Quick Actions
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">
          Common tools, one tap away
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {quickActions.map((a) => (
            <Link
              key={a.to}
              to={a.to}
              className={cn(
                "group flex flex-col gap-1 rounded-lg border border-border bg-muted/30 px-3 py-2.5 transition-colors",
                "hover:border-foreground/30 hover:bg-muted",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              )}
            >
              <span className="text-[13px] font-semibold tracking-tight text-foreground">
                {a.label}
              </span>
              <span className="text-[10px] text-muted-foreground">{a.hint}</span>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section: Market clock — Asia/Kolkata, NSE hours
// ---------------------------------------------------------------------------

function MarketClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(tick);
  }, []);

  // Format IST regardless of user's local TZ
  const ist = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);

  // Determine market session for NSE F&O equity (9:15–15:30 IST, Mon–Fri)
  const istParts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
    .formatToParts(now)
    .reduce<Record<string, string>>((acc, p) => {
      acc[p.type] = p.value;
      return acc;
    }, {});
  const istWeekday = istParts.weekday;
  const istHour = parseInt(istParts.hour || "0", 10);
  const istMin = parseInt(istParts.minute || "0", 10);
  const minutesSinceMidnight = istHour * 60 + istMin;
  const isWeekend = istWeekday === "Sat" || istWeekday === "Sun";

  let session: { dot: string; label: string; hint: string };
  if (isWeekend) {
    session = {
      dot: "bg-muted-foreground/40",
      label: "Markets closed",
      hint: "Weekend",
    };
  } else if (minutesSinceMidnight < 9 * 60) {
    session = {
      dot: "bg-muted-foreground/50",
      label: "Pre-open",
      hint: "Markets open at 09:15 IST",
    };
  } else if (minutesSinceMidnight < 9 * 60 + 15) {
    session = {
      dot: "bg-amber-500 animate-pulse",
      label: "Pre-open auction",
      hint: "Cash auction 09:00–09:15",
    };
  } else if (minutesSinceMidnight < 15 * 60 + 30) {
    session = {
      dot: "bg-emerald-500 animate-pulse",
      label: "Market open",
      hint: "Closes at 15:30 IST",
    };
  } else {
    session = {
      dot: "bg-rose-500",
      label: "Market closed",
      hint: "Reopens 09:15 IST",
    };
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold tracking-tight">
          Market Clock
        </CardTitle>
        <p className="text-[11px] text-muted-foreground">NSE — Asia/Kolkata</p>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex items-center gap-2">
          <span
            className={cn("inline-block h-2 w-2 rounded-full", session.dot)}
            aria-hidden
          />
          <span className="text-[12px] font-semibold uppercase tracking-[0.12em]">
            {session.label}
          </span>
        </div>
        <p className="mt-2 font-mono text-xl font-bold tracking-tight tabular-nums sm:text-2xl">
          {ist}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {session.hint}
        </p>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function SkeletonRows() {
  return (
    <ul className="divide-y divide-border/60">
      {Array.from({ length: 4 }).map((_, i) => (
        <li key={i} className="flex items-center justify-between gap-3 py-2.5">
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
            <div className="h-2.5 w-1/3 animate-pulse rounded bg-muted/70" />
          </div>
          <div className="h-3 w-12 animate-pulse rounded bg-muted" />
        </li>
      ))}
    </ul>
  );
}

function EmptyState({
  title,
  hint,
  cta,
}: {
  title: string;
  hint: string;
  cta?: { label: string; to: string };
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-md border border-dashed border-border/70 bg-muted/30 px-4 py-5">
      <p className="text-sm font-semibold tracking-tight text-foreground">
        {title}
      </p>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
      {cta && (
        <Link
          to={cta.to}
          className="mt-1 text-[11px] font-medium tracking-tight text-foreground underline-offset-2 hover:underline"
        >
          {cta.label} →
        </Link>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const navigate = useNavigate();
  const { funds, positions, holdings, orders, trades, strategies } =
    useDashboardData();

  if (funds.isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading dashboard…</p>
        </div>
      </div>
    );
  }

  if (funds.error) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load dashboard data. Please try again.
        </div>
      </div>
    );
  }

  const f = funds.data;

  const fundCards = [
    { label: "Available Cash", value: f?.availablecash ?? 0, isPnl: false },
    { label: "Collateral", value: f?.collateral ?? 0, isPnl: false },
    { label: "M2M Unrealized", value: f?.m2munrealized ?? 0, isPnl: true },
    { label: "M2M Realized", value: f?.m2mrealized ?? 0, isPnl: true },
    { label: "Utilized Debits", value: f?.utiliseddebits ?? 0, isPnl: false },
  ];

  const positionsData = positions.data ?? [];
  const holdingsData = holdings.data ?? [];
  const ordersData = orders.data ?? [];
  const tradesData = trades.data ?? [];
  const strategiesData = strategies.data ?? [];

  const dayPnl = (f?.m2munrealized ?? 0) + (f?.m2mrealized ?? 0);
  const openPositions = positionsData.filter((p) => p.quantity !== 0).length;
  const runningStrategies = strategiesData.filter(
    (s) => s.status === "running"
  ).length;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Dashboard
          </h1>
          <p className="text-sm text-muted-foreground">
            Overview of your trading account
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate("/search")}
          className="hidden text-[11px] font-medium tracking-tight text-muted-foreground hover:text-foreground sm:inline"
        >
          Press ⌘K to search symbols
        </button>
      </div>

      {/* Funds — primary tile grid */}
      <div className="grid gap-3 sm:gap-4 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        {fundCards.map((card) => (
          <Card key={card.label}>
            <CardHeader className="pb-2">
              <CardTitle className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                {card.label}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p
                className={cn(
                  "text-lg font-bold tracking-tight tabular-nums sm:text-xl lg:text-2xl",
                  card.isPnl ? pnlColor(card.value) : "text-foreground"
                )}
              >
                {formatCurrency(card.value)}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* KPI strip */}
      <KpiStrip
        dayPnl={dayPnl}
        openPositions={openPositions}
        holdingsCount={holdingsData.length}
        runningStrategies={runningStrategies}
        totalStrategies={strategiesData.length}
      />

      {/* Positions + Strategies */}
      <div className="grid gap-4 lg:grid-cols-2">
        <PositionsSnapshot
          positions={positionsData}
          isLoading={positions.isLoading}
        />
        <StrategiesSnapshot
          strategies={strategiesData}
          isLoading={strategies.isLoading}
        />
      </div>

      {/* Recent activity */}
      <div className="grid gap-4 lg:grid-cols-2">
        <RecentOrders orders={ordersData} isLoading={orders.isLoading} />
        <RecentTrades trades={tradesData} isLoading={trades.isLoading} />
      </div>

      {/* Quick actions + market clock */}
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <QuickActions />
        <MarketClock />
      </div>
    </div>
  );
}
