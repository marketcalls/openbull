import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const REPO_URL = "https://github.com/marketcalls/openbull";

export default function Home() {
  const { user, loading } = useAuth();
  const primaryCta = user
    ? { label: "Go to Dashboard", to: "/dashboard" }
    : { label: "Get Started", to: "/login" };

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      {/* Decorative background: a faint grid plus a soft radial glow behind
          the wordmark. Pure CSS, no images. */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-0",
          "bg-[linear-gradient(to_right,oklch(0.6_0_0/0.06)_1px,transparent_1px),linear-gradient(to_bottom,oklch(0.6_0_0/0.06)_1px,transparent_1px)]",
          "[background-size:48px_48px]",
          "[mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]",
        )}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[-10%] h-[60vh] w-[60vw] -translate-x-1/2 rounded-full bg-foreground/[0.04] blur-3xl"
      />

      {/* Top strip: version, license, repo link. Acts as a header without
          being a navbar. */}
      <header className="relative z-10 mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-5">
        <Link
          to="/"
          className="inline-flex items-baseline gap-1 text-lg font-bold tracking-tight"
        >
          <span className="text-muted-foreground/80">Open</span>
          <span className="text-foreground">Bull</span>
        </Link>
        <div className="flex items-center gap-2">
          <Pill label="v1.0" />
          <Pill label="AGPL 3.0" />
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "inline-flex h-7 items-center rounded-md border border-border bg-background px-2.5 text-[12px] font-medium text-muted-foreground transition-colors",
              "hover:border-foreground/30 hover:text-foreground",
            )}
          >
            GitHub
          </a>
        </div>
      </header>

      {/* Hero */}
      <main className="relative z-10 mx-auto max-w-6xl px-6 pt-12 sm:pt-20 lg:pt-24">
        <div className="mx-auto max-w-4xl text-center">
          {/* Tag line above the wordmark */}
          <p className="inline-flex items-center gap-2 rounded-full border border-border bg-muted/40 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            Self-hosted · Open source · Options first
          </p>

          {/* Wordmark: supersized take on the navbar mark */}
          <h1 className="mt-7 font-bold leading-[0.9] tracking-[-0.04em]">
            <span className="block text-[clamp(3.5rem,12vw,9rem)]">
              <span className="text-muted-foreground/70">Open</span>
              <span className="text-foreground">Bull</span>
            </span>
          </h1>

          {/* Slogan: editorial, italic-free, confident */}
          <p className="mx-auto mt-6 max-w-2xl text-balance text-lg font-medium tracking-tight text-foreground sm:text-xl lg:text-2xl">
            Self-hosted, open-source options trading platform. Built for Indian
            markets, owned by you.
          </p>

          <p className="mx-auto mt-4 max-w-2xl text-balance text-sm leading-relaxed text-muted-foreground sm:text-base">
            One canonical API across every supported Indian broker. Real-time
            ticks over a pooled WebSocket. A full options-analytics stack
            (chain, greeks, IV smile, GEX, max-pain) plus strategy automation
            with cron or TradingView webhooks. All on your machine.
          </p>

          {/* CTAs */}
          <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
            <Link to={primaryCta.to}>
              <Button size="lg" disabled={loading}>
                {primaryCta.label}
              </Button>
            </Link>
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" size="lg">
                View on GitHub
              </Button>
            </a>
          </div>

          {/* Feature ribbon: concrete capabilities at a glance, no fluff. */}
          <div className="mx-auto mt-8 flex max-w-3xl flex-wrap items-center justify-center gap-x-3 gap-y-2 text-[11px] text-muted-foreground">
            <FeatureBadge dot="emerald" label="⌘K symbol search" />
            <FeatureBadge dot="indigo" label="10+ option analytics tools" />
            <FeatureBadge dot="amber" label="Cron + TradingView automation" />
            <FeatureBadge dot="sky" label="Pooled WebSocket ticks" />
            <FeatureBadge dot="rose" label="Multi-broker, one symbol format" />
          </div>

          {/* Tiny credibility line */}
          <p className="mt-6 text-[11px] text-muted-foreground/80">
            No accounts on someone else's server. No telemetry. Bring your own
            broker API keys.
          </p>
        </div>

        {/* Stat strip */}
        <div className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-5">
          <Stat label="Option tools" value="10+" />
          <Stat
            label="Supported brokers"
            value="5"
            hint="Major Indian brokers"
          />
          <Stat label="Sandbox accuracy" value="Deterministic" />
          <Stat label="License" value="AGPL 3.0" />
        </div>

        {/* Pillars */}
        <section className="mx-auto mt-20 max-w-5xl">
          <h2 className="text-center text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            What makes it different
          </h2>
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Pillar
              kicker="01"
              title="Self-hosted by default"
              body="Run the whole stack on your own machine: backend, frontend, websocket pool, sandbox. No SaaS lock-in, no cloud middleman."
            />
            <Pillar
              kicker="02"
              title="One symbol format"
              body="OpenAlgo-style canonical symbology across every broker. Write a strategy once; switch brokers without touching the code."
            />
            <Pillar
              kicker="03"
              title="Sandbox mirror"
              body="A deterministic sandbox engine that mirrors live order semantics. Same code path, same UI, no broker. Validate a strategy end-to-end before flipping it live."
            />
            <Pillar
              kicker="04"
              title="Options native"
              body="Option chain, OI tracker, max pain, IV smile, vol surface, GEX dashboard, straddle scanner, payoff builder. All built in."
            />
          </div>
        </section>

        {/* Inside the box */}
        <section className="mx-auto mt-20 max-w-4xl">
          <h2 className="text-center text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            What's inside
          </h2>
          <ul className="mt-6 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
            <Feature
              label="WebSocket feed pool"
              text="Broker-agnostic ticks streamed through a pooled proxy."
            />
            <Feature
              label="Strategy module"
              text="Cron-scheduled or webhook-triggered runs with live & sandbox modes."
            />
            <Feature
              label="TradingView webhooks"
              text="Receive signed signals from TradingView and route to any broker."
            />
            <Feature
              label="Trade audit log"
              text="Event-driven log of every order action with secrets redacted."
            />
            <Feature
              label="Playground"
              text="Full REST + WebSocket tester to hit any endpoint without writing code."
            />
            <Feature
              label="Themed UI"
              text="Light, dark, and a fixed sandbox palette so 'not live' is impossible to forget."
            />
          </ul>
        </section>

        {/* Closing CTA */}
        <section className="mx-auto mt-20 max-w-3xl rounded-2xl border border-border bg-muted/30 p-8 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Ready to trade
          </p>
          <h3 className="mt-2 text-2xl font-bold tracking-tight sm:text-3xl">
            Pull the repo, deploy in minutes.
          </h3>
          <p className="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">
            The full stack runs locally. Validate your strategy in the sandbox,
            then flip the same switch live. Same code, same UI, real broker.
          </p>
          <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
            <Link to={primaryCta.to}>
              <Button size="lg" disabled={loading}>
                {primaryCta.label}
              </Button>
            </Link>
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" size="lg">
                Star on GitHub
              </Button>
            </a>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="relative z-10 mx-auto mt-24 max-w-6xl border-t border-border px-6 py-6">
        <div className="flex flex-col items-center justify-between gap-3 text-[11px] text-muted-foreground sm:flex-row">
          <p>
            <span className="text-muted-foreground/80">Open</span>
            <span className="text-foreground">Bull</span>
            . Self-hosted, open-source options trading platform.
          </p>
          <p className="flex items-center gap-3">
            <span>AGPL 3.0 licensed</span>
            <span aria-hidden>·</span>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground"
            >
              Source on GitHub
            </a>
          </p>
        </div>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Pill({ label }: { label: string }) {
  return (
    <span className="inline-flex h-7 items-center rounded-md border border-border bg-background px-2.5 font-mono text-[11px] font-medium tracking-tight text-muted-foreground">
      {label}
    </span>
  );
}

const DOT_TONE: Record<
  "emerald" | "indigo" | "amber" | "sky" | "rose",
  string
> = {
  emerald: "bg-emerald-500",
  indigo: "bg-indigo-500",
  amber: "bg-amber-500",
  sky: "bg-sky-500",
  rose: "bg-rose-500",
};

function FeatureBadge({
  label,
  dot,
}: {
  label: string;
  dot: keyof typeof DOT_TONE;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-[11px] font-medium text-foreground/80">
      <span
        aria-hidden
        className={cn("inline-block h-1.5 w-1.5 rounded-full", DOT_TONE[dot])}
      />
      {label}
    </span>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/20 px-3 py-3 text-center sm:px-4 sm:py-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-xl font-bold tracking-tight text-foreground sm:text-2xl">
        {value}
      </p>
      {hint && (
        <p className="text-[10px] text-muted-foreground/70">{hint}</p>
      )}
    </div>
  );
}

function Pillar({
  kicker,
  title,
  body,
}: {
  kicker: string;
  title: string;
  body: string;
}) {
  return (
    <div className="group/pillar relative flex h-full flex-col gap-2 rounded-xl border border-border bg-muted/20 p-5 transition-colors hover:border-foreground/30 hover:bg-muted/40">
      <span className="font-mono text-[11px] font-semibold tracking-[0.14em] text-muted-foreground/70">
        {kicker}
      </span>
      <h3 className="text-base font-semibold tracking-tight text-foreground">
        {title}
      </h3>
      <p className="text-[13px] leading-relaxed text-muted-foreground">
        {body}
      </p>
    </div>
  );
}

function Feature({ label, text }: { label: string; text: string }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-foreground/70"
      />
      <div>
        <p className="text-sm font-semibold tracking-tight text-foreground">
          {label}
        </p>
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          {text}
        </p>
      </div>
    </li>
  );
}
