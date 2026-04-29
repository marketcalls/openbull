import { Link } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface ToolDef {
  to: string;
  title: string;
  description: string;
  status: "available" | "coming-soon";
}

const TOOLS: ToolDef[] = [
  {
    to: "/tools/optionchain",
    title: "Option Chain",
    description:
      "Live CE/PE chain with strikes around ATM, OI bars, PCR, and one-click order placement.",
    status: "available",
  },
  {
    to: "/tools/oitracker",
    title: "OI Tracker",
    description:
      "Open Interest snapshot per strike (CE vs PE), futures price, PCR (OI + Volume) and ATM marker.",
    status: "available",
  },
  {
    to: "/tools/maxpain",
    title: "Max Pain",
    description:
      "Strike where total option-writer payout to buyers is minimized — the settle level most adverse to net buyers.",
    status: "available",
  },
  {
    to: "/tools/greeks",
    title: "Option Greeks (Historical)",
    description:
      "Intraday IV, Delta, Gamma, Theta and Vega for ATM CE & PE — Black-76 computed from candle history.",
    status: "available",
  },
  {
    to: "/tools/ivsmile",
    title: "IV Smile",
    description:
      "Call vs Put IV across strikes for a single expiry, with ATM IV and 25-delta proxy skew.",
    status: "available",
  },
  {
    to: "/tools/volsurface",
    title: "Volatility Surface",
    description:
      "3D IV surface across strikes × expiries — OTM convention (CE for K≥ATM, PE for K<ATM).",
    status: "available",
  },
  {
    to: "/tools/straddle",
    title: "Straddle Chart",
    description:
      "Dynamic ATM straddle (CE+PE) time series with synthetic-future overlay (K + CE − PE).",
    status: "available",
  },
  {
    to: "/tools/gex",
    title: "GEX Dashboard",
    description:
      "Gamma Exposure per strike (γ × OI × lot size) plus OI walls and top |γ| strikes table.",
    status: "available",
  },
  {
    to: "/tools/strategybuilder",
    title: "Strategy Builder",
    description:
      "Multi-leg option strategy designer with live Greeks, payoff chart, and one-click basket execute. Save strategies and reload them from your portfolio.",
    status: "available",
  },
];

export default function Tools() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Tools</h1>
        <p className="text-sm text-muted-foreground">
          Trading utilities built on top of OpenBull's market data and order APIs.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {TOOLS.map((tool) => {
          const available = tool.status === "available";
          const Wrapper = available
            ? ({ children }: { children: React.ReactNode }) => (
                <Link to={tool.to} className="block">
                  {children}
                </Link>
              )
            : ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
          return (
            <Wrapper key={tool.to}>
              <Card
                className={cn(
                  "transition-colors",
                  available ? "hover:border-primary hover:bg-muted/40" : "opacity-60"
                )}
              >
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">{tool.title}</CardTitle>
                    {!available && <Badge variant="outline">Coming soon</Badge>}
                  </div>
                  <CardDescription>{tool.description}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="text-xs text-muted-foreground">
                    {available ? "Open tool →" : "Not yet available"}
                  </div>
                </CardContent>
              </Card>
            </Wrapper>
          );
        })}
      </div>
    </div>
  );
}
