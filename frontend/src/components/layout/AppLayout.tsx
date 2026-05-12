import { useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { useTradingMode } from "@/contexts/TradingModeContext";
import { cn } from "@/lib/utils";
import { MasterContractStatus } from "@/components/layout/MasterContractStatus";
import { TradingModeSwitch } from "@/components/layout/TradingModeSwitch";
import { SandboxBanner } from "@/components/layout/SandboxBanner";

// ---------------------------------------------------------------------------
// Nav model
// ---------------------------------------------------------------------------

interface NavLeaf {
  label: string;
  to: string;
  hint?: string;
}

interface NavGroup {
  label?: string;
  items: NavLeaf[];
}

interface NavItem {
  label: string;
  to?: string;
  /** Pathname prefixes that mark this item as active. */
  matches?: string[];
  groups?: NavGroup[];
}

const navItems: NavItem[] = [
  { label: "Dashboard", to: "/dashboard" },
  {
    label: "Orders",
    matches: ["/orderbook", "/tradebook"],
    groups: [
      {
        items: [
          { label: "Orderbook", to: "/orderbook", hint: "Open & historical orders" },
          { label: "Tradebook", to: "/tradebook", hint: "Executed fills" },
        ],
      },
    ],
  },
  {
    label: "Portfolio",
    matches: ["/positions", "/holdings"],
    groups: [
      {
        items: [
          { label: "Positions", to: "/positions", hint: "Intraday & overnight" },
          { label: "Holdings", to: "/holdings", hint: "Long-term inventory" },
        ],
      },
    ],
  },
  {
    label: "Strategies",
    matches: ["/strategy", "/tools/strategybuilder", "/tools/strategyportfolio"],
    groups: [
      {
        items: [
          { label: "All Strategies", to: "/strategy", hint: "Runtime list & controls" },
        ],
      },
      {
        label: "Author",
        items: [
          { label: "Strategy Builder", to: "/tools/strategybuilder", hint: "Compose payoff structures" },
          { label: "Strategy Portfolio", to: "/tools/strategyportfolio", hint: "Aggregate exposure" },
        ],
      },
    ],
  },
  {
    label: "Tools",
    matches: ["/tools"],
    groups: [
      { items: [{ label: "All Tools", to: "/tools", hint: "Browse the full catalogue" }] },
      {
        label: "Chain",
        items: [
          { label: "Option Chain", to: "/tools/optionchain" },
          { label: "Straddles & Strangle Chain", to: "/tools/straddles-strangle-chain" },
        ],
      },
      {
        label: "Analytics",
        items: [
          { label: "OI Tracker", to: "/tools/oitracker" },
          { label: "Max Pain", to: "/tools/maxpain" },
          { label: "Option Greeks", to: "/tools/greeks" },
          { label: "IV Smile", to: "/tools/ivsmile" },
          { label: "Volatility Surface", to: "/tools/volsurface" },
          { label: "GEX Dashboard", to: "/tools/gex" },
        ],
      },
      {
        label: "Charts",
        items: [{ label: "Straddle Chart", to: "/tools/straddle" }],
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isItemActive(item: NavItem, pathname: string): boolean {
  if (item.to && pathname === item.to) return true;
  if (item.matches?.some((m) => pathname === m || pathname.startsWith(m + "/"))) {
    return true;
  }
  if (item.matches?.includes(pathname)) return true;
  return false;
}

function initialsFor(name?: string | null, email?: string | null): string {
  const source = (name || email || "?").trim();
  if (!source) return "?";
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

// ---------------------------------------------------------------------------
// Top nav primitives
// ---------------------------------------------------------------------------

function NavLeafLink({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "relative inline-flex h-14 items-center px-3 text-sm font-medium tracking-tight transition-colors",
          "after:absolute after:inset-x-3 after:bottom-[-1px] after:h-[2px] after:bg-foreground after:transition-opacity after:content-['']",
          isActive
            ? "text-foreground after:opacity-100"
            : "text-muted-foreground hover:text-foreground after:opacity-0"
        )
      }
    >
      {label}
    </NavLink>
  );
}

function NavDropdown({ item }: { item: NavItem }) {
  const location = useLocation();
  const active = isItemActive(item, location.pathname);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "relative inline-flex h-14 items-center gap-1 px-3 text-sm font-medium tracking-tight outline-none transition-colors",
          "after:absolute after:inset-x-3 after:bottom-[-1px] after:h-[2px] after:bg-foreground after:transition-opacity after:content-['']",
          active
            ? "text-foreground after:opacity-100"
            : "text-muted-foreground hover:text-foreground after:opacity-0",
          "data-[popup-open]:text-foreground"
        )}
      >
        {item.label}
        <span aria-hidden className="text-[10px] leading-none text-muted-foreground/70">
          {"▾"}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={0}
        className="min-w-[240px] max-w-[calc(100vw-1rem)] p-2"
      >
        {item.groups?.map((group, gi) => (
          <DropdownMenuGroup key={gi}>
            {gi > 0 && <DropdownMenuSeparator />}
            {group.label && (
              <DropdownMenuLabel className="px-2 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                {group.label}
              </DropdownMenuLabel>
            )}
            {group.items.map((leaf) => (
              <DropdownMenuItem
                key={leaf.to}
                render={<NavLink to={leaf.to} />}
                className="min-h-[40px] px-2 py-1.5"
              >
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium text-foreground">
                    {leaf.label}
                  </span>
                  {leaf.hint && (
                    <span className="text-[11px] text-muted-foreground">
                      {leaf.hint}
                    </span>
                  )}
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SearchTrigger() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (location.pathname !== "/search") navigate("/search");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate, location.pathname]);

  return (
    <button
      type="button"
      onClick={() => navigate("/search")}
      className={cn(
        "group/search inline-flex h-8 items-center gap-3 rounded-md border border-border bg-muted/40 pl-2.5 pr-1.5 text-xs text-muted-foreground transition-colors",
        "hover:border-foreground/20 hover:bg-muted hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      )}
      aria-label="Open symbol search (Ctrl+K)"
      title="Search symbols (Ctrl+K)"
    >
      <span className="tracking-tight">Search symbols</span>
      <kbd className="inline-flex h-5 items-center gap-0.5 rounded border border-border/80 bg-background px-1.5 font-mono text-[10px] font-medium text-muted-foreground/80">
        <span className="text-[11px] leading-none">{"⌘"}</span>K
      </kbd>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Profile menu
// ---------------------------------------------------------------------------

function ProfileMenu({ onLogout }: { onLogout: () => void }) {
  const { user } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { isSandbox } = useTradingMode();

  const initials = initialsFor(user?.username, user?.email);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "group/avatar inline-flex items-center rounded-full outline-none",
          "ring-offset-2 ring-offset-background transition-shadow",
          "hover:ring-2 hover:ring-foreground/15",
          "focus-visible:ring-2 focus-visible:ring-ring",
          "data-[popup-open]:ring-2 data-[popup-open]:ring-foreground/25"
        )}
        aria-label="Account menu"
      >
        <Avatar size="default">
          <AvatarFallback className="bg-foreground text-background text-[11px] font-semibold tracking-tight uppercase">
            {initials}
          </AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={8}
        className="min-w-[260px] max-w-[calc(100vw-1rem)] p-2"
      >
        {/* Identity card */}
        <div className="flex items-center gap-3 px-2 pt-1.5 pb-2.5">
          <Avatar size="lg">
            <AvatarFallback className="bg-foreground text-background text-sm font-semibold uppercase">
              {initials}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground">
              {user?.username || "Account"}
            </p>
            {user?.email && (
              <p className="truncate text-[11px] text-muted-foreground">
                {user.email}
              </p>
            )}
            {user?.broker && (
              <p className="mt-0.5 inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground/80">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {user.broker}
              </p>
            )}
          </div>
        </div>

        <DropdownMenuSeparator />

        {/* Theme toggle — live only */}
        <DropdownMenuItem
          onClick={(e) => {
            if (isSandbox) return;
            e.preventDefault();
            toggleTheme();
          }}
          disabled={isSandbox}
          className="min-h-[40px] justify-between px-2"
        >
          <span className="text-sm">Theme</span>
          <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            {isSandbox ? "Sandbox" : theme === "dark" ? "Dark" : "Light"}
          </span>
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            Account
          </DropdownMenuLabel>
          <DropdownMenuItem
            render={<NavLink to="/broker/config" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">Broker Configuration</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            render={<NavLink to="/apikey" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">API Key</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            Sandbox
          </DropdownMenuLabel>
          <DropdownMenuItem
            render={<NavLink to="/sandbox" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">Sandbox Configuration</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            render={<NavLink to="/sandbox/mypnl" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">Sandbox P&amp;L</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            Developer
          </DropdownMenuLabel>
          <DropdownMenuItem
            render={<NavLink to="/playground" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">Playground</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            render={<NavLink to="/logs" />}
            className="min-h-[40px] px-2"
          >
            <span className="text-sm">Trade Logs</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          variant="destructive"
          onClick={onLogout}
          className="min-h-[40px] px-2"
        >
          <span className="text-sm">Sign out</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// Mobile menu (below lg)
// ---------------------------------------------------------------------------

function MobileNav() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="lg"
            aria-label="Open navigation"
            className="-ml-2 h-10 px-3"
          >
            Menu
          </Button>
        }
      />
      <DropdownMenuContent
        align="start"
        sideOffset={8}
        className="min-w-[240px] max-w-[calc(100vw-1rem)] p-2"
      >
        <DropdownMenuItem
          render={<NavLink to="/dashboard" />}
          className="min-h-[44px] px-2"
        >
          <span className="text-sm font-medium">Dashboard</span>
        </DropdownMenuItem>
        <DropdownMenuItem
          render={<NavLink to="/search" />}
          className="min-h-[44px] px-2"
        >
          <span className="text-sm font-medium">Search symbols</span>
        </DropdownMenuItem>
        {navItems
          .filter((i) => i.groups)
          .map((item) => (
            <DropdownMenuGroup key={item.label}>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="px-2 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                {item.label}
              </DropdownMenuLabel>
              {item.groups?.flatMap((g) => g.items).map((leaf) => (
                <DropdownMenuItem
                  key={leaf.to}
                  render={<NavLink to={leaf.to} />}
                  className="min-h-[44px] px-2"
                >
                  <span className="text-sm">{leaf.label}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// Layout shell
// ---------------------------------------------------------------------------

export function AppLayout() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      {/* Top navbar */}
      <header
        className={cn(
          "sticky top-0 z-40 flex h-14 items-stretch border-b border-border",
          "bg-card/85 backdrop-blur supports-[backdrop-filter]:bg-card/70"
        )}
      >
        <div className="flex w-full items-center gap-1 px-4">
          {/* Wordmark */}
          <NavLink
            to="/dashboard"
            className="mr-2 inline-flex shrink-0 items-baseline gap-1 text-base font-bold tracking-tight sm:mr-4 sm:text-base"
          >
            <span className="text-muted-foreground/80">Open</span>
            <span className="text-foreground">Bull</span>
          </NavLink>

          {/* Primary nav (desktop) */}
          <nav className="hidden h-full items-stretch lg:flex">
            {navItems.map((item) =>
              item.groups ? (
                <NavDropdown key={item.label} item={item} />
              ) : item.to ? (
                <NavLeafLink key={item.to} to={item.to} label={item.label} />
              ) : null
            )}
          </nav>

          {/* Mobile menu trigger */}
          <div className="lg:hidden">
            <MobileNav />
          </div>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Right cluster */}
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="hidden md:block">
              <SearchTrigger />
            </div>
            <div className="hidden h-6 w-px bg-border md:block" />
            <TradingModeSwitch />
            <div className="hidden h-6 w-px bg-border sm:block" />
            <div className="hidden sm:block">
              <MasterContractStatus />
            </div>
            <div className="hidden h-6 w-px bg-border sm:block" />
            <ProfileMenu onLogout={handleLogout} />
          </div>
        </div>
      </header>

      {/* Sandbox mode banner */}
      <SandboxBanner />

      {/* Page content */}
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}
