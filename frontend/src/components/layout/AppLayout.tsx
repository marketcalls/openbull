import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { cn } from "@/lib/utils";
import { MasterContractStatus } from "@/components/layout/MasterContractStatus";
import { TradingModeSwitch } from "@/components/layout/TradingModeSwitch";
import { SandboxBanner } from "@/components/layout/SandboxBanner";

interface NavItem {
  label: string;
  to: string;
  children?: NavItem[];
}

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "Symbol Search", to: "/search" },
  {
    label: "Orders",
    to: "/orderbook",
    children: [
      { label: "Orderbook", to: "/orderbook" },
      { label: "Tradebook", to: "/tradebook" },
    ],
  },
  {
    label: "Portfolio",
    to: "/positions",
    children: [
      { label: "Positions", to: "/positions" },
      { label: "Holdings", to: "/holdings" },
    ],
  },
  {
    label: "Sandbox",
    to: "/sandbox",
    children: [
      { label: "Configuration", to: "/sandbox" },
      { label: "My P&L", to: "/sandbox/mypnl" },
    ],
  },
  {
    label: "Tools",
    to: "/tools",
    children: [
      { label: "All Tools", to: "/tools" },
      { label: "Option Chain", to: "/tools/optionchain" },
      { label: "OI Tracker", to: "/tools/oitracker" },
      { label: "Max Pain", to: "/tools/maxpain" },
      { label: "Option Greeks", to: "/tools/greeks" },
      { label: "IV Smile", to: "/tools/ivsmile" },
      { label: "Vol Surface", to: "/tools/volsurface" },
      { label: "Straddle Chart", to: "/tools/straddle" },
      { label: "GEX Dashboard", to: "/tools/gex" },
      { label: "Strategy Builder", to: "/tools/strategybuilder" },
      { label: "Strategy Portfolio", to: "/tools/strategyportfolio" },
    ],
  },
  {
    label: "Settings",
    to: "/broker/config",
    children: [
      { label: "Broker Config", to: "/broker/config" },
      { label: "API Key", to: "/apikey" },
    ],
  },
];

function SidebarLink({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        )
      }
    >
      {label}
    </NavLink>
  );
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { isSandbox } = useTradingMode();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-card transition-transform duration-200 lg:static lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex h-14 items-center px-4">
          <NavLink to="/dashboard" className="text-lg font-bold tracking-tight">
            OpenBull
          </NavLink>
        </div>
        <Separator />
        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {navItems.map((item) =>
            item.children ? (
              <div key={item.label} className="space-y-1">
                <p className="px-3 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {item.label}
                </p>
                {item.children.map((child) => (
                  <SidebarLink key={child.to} to={child.to} label={child.label} />
                ))}
              </div>
            ) : (
              <SidebarLink key={item.to} to={item.to} label={item.label} />
            )
          )}
        </nav>
        <Separator />
        <div className="p-3">
          <div className="rounded-md bg-muted/50 p-3">
            <p className="text-sm font-medium">{user?.username}</p>
            <p className="text-xs text-muted-foreground">{user?.email}</p>
            {user?.broker && (
              <p className="mt-1 text-xs text-muted-foreground">
                Broker: {user.broker}
              </p>
            )}
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Topbar */}
        <header className="flex h-14 items-center justify-between border-b border-border bg-card px-4">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              className="lg:hidden"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              Menu
            </Button>
            <span className="text-sm font-medium text-muted-foreground lg:hidden">
              OpenBull
            </span>
          </div>

          <div className="flex items-center gap-2">
            <TradingModeSwitch />
            <Separator orientation="vertical" className="mx-1 h-6" />
            <MasterContractStatus />
            <Separator orientation="vertical" className="mx-1 h-6" />
            {/* Theme toggle is live-only — sandbox uses a fixed amber palette
                so light/dark would have nothing to flip. */}
            <Button
              variant="outline"
              size="sm"
              onClick={toggleTheme}
              disabled={isSandbox}
              title={
                isSandbox
                  ? "Sandbox mode uses a fixed theme"
                  : `Switch to ${theme === "dark" ? "light" : "dark"} theme`
              }
            >
              {theme === "dark" ? "Light" : "Dark"}
            </Button>
            <Separator orientation="vertical" className="mx-1 h-6" />
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {user?.username}
            </span>
            <Button variant="ghost" size="sm" onClick={handleLogout}>
              Logout
            </Button>
          </div>
        </header>

        {/* Sandbox mode banner — above scrollable content so it stays visible */}
        <SandboxBanner />

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
