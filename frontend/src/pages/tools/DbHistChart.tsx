// DB Hist Chart — historical OHLCV read back from Databricks Delta tables
// (analysis_indices / futures / options). Archive/EOD-exported data, not live.
//
// Options mode adds an Expiry selector + an ATM-centered strike range (±N) so you
// pick real contracts (no more typing a strike that doesn't exist), and renders an
// SMC signals card — trade count, direction split, entry/SL/TP, RR, P(win) and a
// plain-English "why it triggered" — under EACH contract in the ATM±N set.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { init, dispose, type Chart, type KLineData } from "klinecharts";
import { toast } from "sonner";
import { useTheme } from "@/contexts/ThemeContext";
import { fetchDbSymbols, fetchDbCandles, type DbSource, type DbCandle } from "@/api/dbhist";
import { scoreSmc, type SmcSignal, type SmcMeta } from "@/api/smc";

const SOURCES: { value: DbSource; label: string }[] = [
  { value: "indices", label: "Indices" },
  { value: "futures", label: "Futures" },
  { value: "options", label: "Options" },
];

const fmtDate = (d: Date) => d.toISOString().slice(0, 10);

// NIFTY15SEP2624500PE -> {underlying:NIFTY, expiry:15SEP26, strike:24500, type:PE}
const OPT_RE = /^([A-Z]+[0-9]*?)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$/;
interface Opt { sym: string; underlying: string; expiry: string; strike: number; type: "CE" | "PE" }
function parseOpt(s: string): Opt | null {
  const m = OPT_RE.exec(s);
  if (!m) return null;
  return { sym: s, underlying: m[1], expiry: m[2], strike: Number(m[3]), type: m[4] as "CE" | "PE" };
}
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
function expiryKey(exp: string): number {
  const m = /^(\d{2})([A-Z]{3})(\d{2})$/.exec(exp);
  if (!m) return 0;
  return 2000 + Number(m[3]) + MONTHS.indexOf(m[2]) / 100 + Number(m[1]) / 10000;
}

interface ContractCard {
  sym: string; strike: number; type: "CE" | "PE";
  loading: boolean; err?: string; candles: number;
  signals: SmcSignal[]; meta: SmcMeta | null; longs: number; shorts: number;
}

function styles(isDark: boolean) {
  const grid = isDark ? "#232733" : "#ecedf1";
  const text = isDark ? "#cbd5e1" : "#334155";
  return {
    grid: { horizontal: { color: grid }, vertical: { color: grid } },
    candle: {
      bar: { upColor: "#26A69A", downColor: "#EF5350", upBorderColor: "#26A69A", downBorderColor: "#EF5350", upWickColor: "#26A69A", downWickColor: "#EF5350" },
      tooltip: { text: { color: text } },
      priceMark: { last: { text: { color: "#fff" } } },
    },
    xAxis: { tickText: { color: text }, axisLine: { color: grid } },
    yAxis: { tickText: { color: text }, axisLine: { color: grid } },
    indicator: { lastValueMark: { show: true, text: { show: true } } },
  } as Record<string, unknown>;
}

const reasonFor = (s: SmcSignal) => {
  const bull = s.direction === "long";
  const kind = s.is_choch ? "CHoCH (trend reversal)" : "BOS (trend continuation)";
  const broke = bull ? "closed above the last swing high" : "closed below the last swing low";
  return `${kind} — price ${broke}; entry on the break, stop at the opposing swing, target ${s.rr}R.`;
};

export default function DbHistChart() {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [source, setSource] = useState<DbSource>("options");
  const [symbol, setSymbol] = useState("");
  const [interval, setIntervalKey] = useState("5m");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [intervals, setIntervals] = useState<string[]>(["1m", "5m", "15m", "D"]);
  const [start, setStart] = useState(fmtDate(new Date(Date.now() - 30 * 86400000)));
  const [end, setEnd] = useState(fmtDate(new Date()));
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");
  const [scoreOn, setScoreOn] = useState(true);
  const [smcSignals, setSmcSignals] = useState<SmcSignal[]>([]);
  const [smcMeta, setSmcMeta] = useState<SmcMeta | null>(null);
  const rawCandles = useRef<DbCandle[]>([]);

  // options-mode structured selectors
  const [underlying, setUnderlying] = useState("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [centerStrike, setCenterStrike] = useState<number | null>(null);
  const [spread, setSpread] = useState(3);
  const [optType, setOptType] = useState<"CE" | "PE" | "BOTH">("CE");
  const [cards, setCards] = useState<ContractCard[]>([]);
  const [cardsBusy, setCardsBusy] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  const isOptions = source === "options";

  // parsed option universe
  const parsed = useMemo(() => isOptions ? symbols.map(parseOpt).filter((o): o is Opt => !!o) : [], [symbols, isOptions]);
  const underlyings = useMemo(() => Array.from(new Set(parsed.map((o) => o.underlying))).sort(), [parsed]);
  const expiries = useMemo(
    () => Array.from(new Set(parsed.filter((o) => o.underlying === underlying).map((o) => o.expiry)))
      .sort((a, b) => expiryKey(a) - expiryKey(b)),
    [parsed, underlying]);
  const strikes = useMemo(
    () => Array.from(new Set(parsed.filter((o) => o.underlying === underlying && o.expiry === expiry).map((o) => o.strike)))
      .sort((a, b) => a - b),
    [parsed, underlying, expiry]);
  const symSet = useMemo(() => new Set(symbols), [symbols]);

  // init chart once
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const c = init(el, { styles: styles(isDark) });
    chartRef.current = c;
    if (c) c.createIndicator("VOL", false);
    const ro = new ResizeObserver(() => chartRef.current?.resize());
    ro.observe(el);
    return () => { ro.disconnect(); dispose(el); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { chartRef.current?.setStyles(styles(isDark)); }, [isDark]);

  // load symbol list when source changes
  useEffect(() => {
    let stop = false;
    (async () => {
      setErr("");
      const r = await fetchDbSymbols(source);
      if (stop) return;
      if (r.status === "success") {
        setSymbols(r.symbols ?? []);
        if (r.intervals?.length) setIntervals(r.intervals);
        if (!isOptions && r.symbols?.length && !r.symbols.includes(symbol)) setSymbol(r.symbols[0]);
      } else {
        setErr(r.message ?? "Failed to list symbols");
      }
    })();
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  // when the option universe resolves, pick sane defaults for underlying/expiry/center
  useEffect(() => {
    if (!isOptions || !underlyings.length) return;
    if (!underlyings.includes(underlying)) setUnderlying(underlyings.includes("NIFTY") ? "NIFTY" : underlyings[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlyings, isOptions]);
  useEffect(() => {
    if (!isOptions) return;
    if (expiries.length && !expiries.includes(expiry)) setExpiry(expiries[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiries, isOptions]);
  useEffect(() => {
    if (!isOptions || !strikes.length) return;
    if (centerStrike == null || !strikes.includes(centerStrike)) setCenterStrike(strikes[Math.floor(strikes.length / 2)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strikes, isOptions]);

  const buildSym = useCallback(
    (strike: number, type: "CE" | "PE") => `${underlying}${expiry}${strike}${type}`,
    [underlying, expiry]);

  // fetch + optionally score a single symbol into the MAIN chart
  const load = useCallback(async (sym?: string) => {
    const target = sym ?? symbol;
    if (!target) return;
    setSymbol(target);
    setLoading(true); setErr(""); setInfo("");
    try {
      const r = await fetchDbCandles({ source, symbol: target, interval, start, end });
      if (r.status === "success" && r.candles) {
        rawCandles.current = r.candles;
        const data: KLineData[] = r.candles.map((c) => ({
          timestamp: c.timestamp * 1000, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume,
        }));
        chartRef.current?.applyNewData(data);
        requestAnimationFrame(() => chartRef.current?.resize());
        setInfo(data.length ? `${target} · ${data.length} candles · ${r.candles[0].datetime} → ${r.candles[r.candles.length - 1].datetime}` : `No rows for ${target} in this range.`);
        if (!data.length) toast.info("No rows in Databricks for that symbol/interval/range.");
        if (scoreOn && data.length) {
          const sc = await scoreSmc(r.candles.map((c) => ({ timestamp: c.timestamp, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume, oi: c.oi })));
          if (sc.status === "success") { setSmcSignals(sc.signals ?? []); setSmcMeta(sc.meta ?? null); }
          else { setSmcSignals([]); toast.error(sc.message ?? "SMC model not available"); }
        } else setSmcSignals([]);
      } else {
        setErr(r.message ?? "Failed to load"); chartRef.current?.applyNewData([]);
      }
    } catch {
      setErr("Failed to load from Databricks.");
    } finally { setLoading(false); }
  }, [source, symbol, interval, start, end, scoreOn]);

  // build the ATM±N contract set and score each -> one card per contract
  const loadAtmSet = useCallback(async () => {
    if (!isOptions || centerStrike == null || !strikes.length) return;
    const ci = strikes.indexOf(centerStrike);
    const chosen = strikes.slice(Math.max(0, ci - spread), ci + spread + 1);
    const types: ("CE" | "PE")[] = optType === "BOTH" ? ["CE", "PE"] : [optType];
    const targets: { sym: string; strike: number; type: "CE" | "PE" }[] = [];
    for (const strike of chosen)
      for (const type of types) {
        const sym = buildSym(strike, type);
        if (symSet.has(sym)) targets.push({ sym, strike, type });
      }
    if (!targets.length) { setCards([]); toast.info("No stored contracts in that ATM±N range."); return; }

    setCardsBusy(true);
    setCards(targets.map((t) => ({ ...t, loading: true, candles: 0, signals: [], meta: null, longs: 0, shorts: 0 })));
    // load the center CE (or first) into the main chart for context
    const centerCard = targets.find((t) => t.strike === centerStrike) ?? targets[0];
    void load(centerCard.sym);

    const results = await Promise.all(targets.map(async (t): Promise<ContractCard> => {
      try {
        const r = await fetchDbCandles({ source: "options", symbol: t.sym, interval, start, end });
        if (r.status !== "success" || !r.candles?.length)
          return { ...t, loading: false, candles: 0, signals: [], meta: null, longs: 0, shorts: 0, err: r.message ?? "no rows" };
        const sc = await scoreSmc(r.candles.map((c) => ({ timestamp: c.timestamp, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume, oi: c.oi })));
        const sigs = sc.status === "success" ? (sc.signals ?? []) : [];
        const longs = sigs.filter((s) => s.direction === "long").length;
        return { ...t, loading: false, candles: r.candles.length, signals: sigs, meta: sc.meta ?? null, longs, shorts: sigs.length - longs, err: sc.status === "success" ? undefined : sc.message };
      } catch {
        return { ...t, loading: false, candles: 0, signals: [], meta: null, longs: 0, shorts: 0, err: "load failed" };
      }
    }));
    setCards(results);
    setCardsBusy(false);
  }, [isOptions, centerStrike, strikes, spread, optType, buildSym, symSet, interval, start, end, load]);

  // non-options: auto-load first symbol
  useEffect(() => { if (!isOptions && symbol) void load(symbol); /* eslint-disable-next-line */ }, [symbol, interval, isOptions]);

  const symListId = "dbhist-syms";
  const totalTrades = cards.reduce((a, c) => a + c.signals.length, 0);

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col gap-2 overflow-y-auto p-3">
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <h1 className="text-sm font-semibold">DB Hist Chart</h1>
          <p className="text-[11px] text-muted-foreground">Historical OHLCV from Databricks (archive / EOD-exported — not live).</p>
        </div>
        <label className="flex flex-col text-xs text-muted-foreground">Source
          <select value={source} onChange={(e) => { setSource(e.target.value as DbSource); setCards([]); }} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
            {SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>

        {isOptions ? (
          <>
            <label className="flex flex-col text-xs text-muted-foreground">Underlying
              <select value={underlying} onChange={(e) => setUnderlying(e.target.value)} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
                {underlyings.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
            <label className="flex flex-col text-xs text-muted-foreground">Expiry
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
                {expiries.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
            <label className="flex flex-col text-xs text-muted-foreground">Center (ATM)
              <select value={centerStrike ?? ""} onChange={(e) => setCenterStrike(Number(e.target.value))} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
                {strikes.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="flex flex-col text-xs text-muted-foreground">± strikes
              <input type="number" min={0} max={10} value={spread} onChange={(e) => setSpread(Math.max(0, Math.min(10, Number(e.target.value))))} className="mt-0.5 h-8 w-16 rounded-lg border border-input bg-background px-2 text-sm" />
            </label>
            <label className="flex flex-col text-xs text-muted-foreground">Type
              <select value={optType} onChange={(e) => setOptType(e.target.value as "CE" | "PE" | "BOTH")} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
                <option value="CE">CE</option><option value="PE">PE</option><option value="BOTH">CE + PE</option>
              </select>
            </label>
          </>
        ) : (
          <label className="flex flex-col text-xs text-muted-foreground">Symbol
            <input list={symListId} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="mt-0.5 h-8 w-56 rounded-lg border border-input bg-background px-2 text-sm" placeholder="type / pick" />
            <datalist id={symListId}>{symbols.map((s) => <option key={s} value={s} />)}</datalist>
          </label>
        )}

        <label className="flex flex-col text-xs text-muted-foreground">Interval
          <select value={interval} onChange={(e) => setIntervalKey(e.target.value)} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm">
            {intervals.map((iv) => <option key={iv} value={iv}>{iv}</option>)}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted-foreground">From
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm" />
        </label>
        <label className="flex flex-col text-xs text-muted-foreground">To
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="mt-0.5 h-8 rounded-lg border border-input bg-background px-2 text-sm" />
        </label>

        {isOptions ? (
          <button onClick={() => void loadAtmSet()} disabled={cardsBusy || centerStrike == null} className="h-8 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50">
            {cardsBusy ? "Loading…" : `Load ATM ±${spread}`}
          </button>
        ) : (
          <button onClick={() => void load()} disabled={loading} className="h-8 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50">
            {loading ? "Loading…" : "Load"}
          </button>
        )}
        <label className="ml-2 flex items-center gap-1 text-xs text-muted-foreground" title="Run the trained SMC win/loss model over the loaded candles">
          <input type="checkbox" checked={scoreOn} onChange={(e) => setScoreOn(e.target.checked)} />
          Score SMC (model)
        </label>
      </div>

      {err && <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600">{err}</div>}
      {info && !err && <div className="text-[11px] text-muted-foreground">{info}</div>}

      <div ref={containerRef} className="h-[360px] shrink-0 rounded-lg border border-border/60" />

      {/* Options mode: one SMC card per ATM±N contract */}
      {isOptions && cards.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between px-1 text-xs">
            <span className="font-medium">{cards.length} contracts · {totalTrades} total trades recommended</span>
            {cards[0]?.meta && <span className="text-muted-foreground">model: AUC {cards[0].meta.auc ?? "—"} · base {cards[0].meta.base_win_rate != null ? (cards[0].meta.base_win_rate * 100).toFixed(0) + "%" : "—"} · RR {cards[0].meta.rr}{cards[0].meta.intraday ? " · intraday" : ""}{cards[0].meta.long_only ? " · long-only" : ""}{cards[0].meta.sources ? ` · ${cards[0].meta.sources}` : ""}</span>}
          </div>
          {cards.map((c) => <ContractPanel key={c.sym} card={c} onPick={() => void load(c.sym)} />)}
        </div>
      )}

      {/* Non-options mode: single signals table */}
      {!isOptions && scoreOn && (
        <SignalTable signals={smcSignals} meta={smcMeta} />
      )}
    </div>
  );
}

function ContractPanel({ card, onPick }: { card: ContractCard; onPick: () => void }) {
  const c = card;
  return (
    <div className="rounded-lg border border-border">
      <button onClick={onPick} className="flex w-full items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-1.5 text-left text-xs hover:bg-muted/70">
        <span className="font-medium tabular-nums">
          {c.sym}
          <span className="ml-2 rounded px-1.5 py-0.5 text-[10px]" style={{ background: c.type === "CE" ? "#26A69A22" : "#EF535022", color: c.type === "CE" ? "#26A69A" : "#EF5350" }}>{c.type}</span>
          <span className="ml-2 text-muted-foreground">strike {c.strike}</span>
        </span>
        <span className="text-muted-foreground">
          {c.loading ? "loading…" : c.err ? c.err : `${c.signals.length} trades ${c.meta?.long_only ? "(long-only)" : `(${c.longs}L · ${c.shorts}S)`} · ${c.candles} candles`}
        </span>
      </button>
      {!c.loading && c.signals.length > 0 && <SignalRows signals={c.signals} baseRate={c.meta?.base_win_rate ?? 0.33} />}
      {!c.loading && !c.err && c.signals.length === 0 && <div className="px-3 py-3 text-center text-xs text-muted-foreground">No SMC signals for this contract in range.</div>}
    </div>
  );
}

function SignalTable({ signals, meta }: { signals: SmcSignal[]; meta: SmcMeta | null }) {
  const longs = signals.filter((s) => s.direction === "long").length;
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-1.5 text-xs">
        <span className="font-medium">{signals.length} trades recommended <span className="text-muted-foreground">({longs} long · {signals.length - longs} short)</span> · P(win) from model</span>
        {meta && <span className="text-muted-foreground">AUC {meta.auc ?? "—"} · base {meta.base_win_rate != null ? (meta.base_win_rate * 100).toFixed(0) + "%" : "—"} · RR {meta.rr} · {meta.n_total?.toLocaleString("en-IN")} trained{meta.intraday ? " · intraday" : ""}</span>}
      </div>
      {signals.length ? <SignalRows signals={signals} baseRate={meta?.base_win_rate ?? 0.33} />
        : <div className="px-3 py-5 text-center text-xs text-muted-foreground">No SMC signals in this range (or model not trained).</div>}
    </div>
  );
}

function SignalRows({ signals, baseRate }: { signals: SmcSignal[]; baseRate: number }) {
  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-background text-muted-foreground">
        <tr>
          <th className="px-3 py-1.5 text-left font-medium">When</th>
          <th className="px-3 py-1.5 text-left font-medium">Dir</th>
          <th className="px-3 py-1.5 text-left font-medium">Type</th>
          <th className="px-3 py-1.5 text-right font-medium">Entry</th>
          <th className="px-3 py-1.5 text-right font-medium">SL</th>
          <th className="px-3 py-1.5 text-right font-medium">TP</th>
          <th className="px-3 py-1.5 text-right font-medium">RR</th>
          <th className="px-3 py-1.5 text-right font-medium">P(win)</th>
          <th className="px-3 py-1.5 text-left font-medium">Why it triggered</th>
        </tr>
      </thead>
      <tbody>
        {signals.map((s) => {
          const col = s.win_prob >= baseRate + 0.08 ? "#26A69A" : s.win_prob <= baseRate - 0.08 ? "#EF5350" : "#9E9E9E";
          const t = new Date(s.timestamp * 1000 + 5.5 * 3600 * 1000).toISOString().slice(5, 16).replace("T", " ");
          return (
            <tr key={`${s.i}-${s.timestamp}`} className="border-t border-border/60 tabular-nums">
              <td className="px-3 py-1 text-muted-foreground">{t}</td>
              <td className="px-3 py-1" style={{ color: s.direction === "long" ? "#26A69A" : "#EF5350" }}>{s.direction === "long" ? "LONG" : "SHORT"}</td>
              <td className="px-3 py-1 text-muted-foreground">{s.is_choch ? "CHoCH" : "BOS"}</td>
              <td className="px-3 py-1 text-right">{s.entry}</td>
              <td className="px-3 py-1 text-right text-muted-foreground">{s.sl}</td>
              <td className="px-3 py-1 text-right text-muted-foreground">{s.tp}</td>
              <td className="px-3 py-1 text-right text-muted-foreground">1:{s.rr}</td>
              <td className="px-3 py-1 text-right font-semibold" style={{ color: col }}>{(s.win_prob * 100).toFixed(0)}%</td>
              <td className="px-3 py-1 text-left text-muted-foreground">{reasonFor(s)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
