import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Area, AreaChart, Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowDownRight, ArrowUpRight, CheckCircle2, Flame, Mail, RefreshCw, UserCheck } from "lucide-react";
import { api, type ClaudeDashboard, type ClaudeFeatureStat, type ClaudeLiveAccount, type ClaudeTotals, type ClaudeUsage, type ClaudeWindow } from "../lib/api";

// Claude Monitor (admins only). Fixed to the viewport: nothing on this page scrolls the window.
// Live data: every Claude call the app makes (chat, Radar, Trends, Meetings) is recorded by the server, and each plan's
// real 5-hour / weekly limits are read from Anthropic. Counters start at zero when the server starts.

type Status = "Healthy" | "Warning" | "Critical";
type RangeId = "1h" | "24h" | "7d" | "30d";

const BLUE = "#0078d4", GREEN = "#107c10", AMBER = "#b87a00", AMBER_FILL = "#ffb900", RED = "#d13438", TEXT2 = "#605e5c";
const STATUS_STYLE: Record<Status, { chip: string; dot: string; color: string }> = {
  Healthy: { chip: "bg-[#dff6dd] text-[#0e700e]", dot: "bg-[#107c10]", color: GREEN },
  Warning: { chip: "bg-[#fff4ce] text-[#7a5200]", dot: "bg-[#ffb900]", color: AMBER_FILL },
  Critical: { chip: "bg-[#fde7e9] text-[#a4262c]", dot: "bg-[#d13438]", color: RED },
};
const RANGES: { id: RangeId; label: string }[] = [
  { id: "1h", label: "Last 1 hour" }, { id: "24h", label: "24 hours" }, { id: "7d", label: "7 days" }, { id: "30d", label: "30 days" },
];

const money = (n: number) => (n >= 100 ? `$${Math.round(n).toLocaleString("en-US")}` : n > 0 && n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
const compact = (n: number) => Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const pct = (n: number) => `${n.toFixed(n >= 10 || n === 0 ? 0 : 1)}%`;

function tickLabel(range: RangeId, ts: number) {
  const d = new Date(ts * 1000);
  return range === "1h" || range === "24h" ? d.toLocaleTimeString([], { hour: "numeric", minute: range === "1h" ? "2-digit" : undefined }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}
const fmtAgo = (t: number | null) => {
  if (!t) return "never used";
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};
const fmtReset = (t: number | null) => {
  if (!t) return "";
  const secs = t - Date.now() / 1000;
  if (secs <= 0) return "resets now";
  const h = Math.floor(secs / 3600), m = Math.round((secs % 3600) / 60);
  const when = new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  return `resets in ${h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h ? `${h}h ${m}m` : `${m}m`} (${when})`;
};

function Card({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`flex min-h-0 min-w-0 flex-col rounded-lg border border-[#e1dfdd] bg-white p-3 shadow-[0_1px_2px_rgba(0,0,0,0.05)] ${className}`}>
      {title && (
        <header className="mb-1.5 flex shrink-0 items-center justify-between gap-2">
          <h2 className="truncate text-[13px] font-semibold text-[#201f1e]">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

function StatusBadge({ s }: { s: Status }) {
  const st = STATUS_STYLE[s];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.chip}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} aria-hidden />{s}
    </span>
  );
}

function Spark({ data, color }: { data: number[]; color: string }) {
  const d = data.map((v, i) => ({ i, v }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={d} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={color} fillOpacity={0.12} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function Kpi({ label, value, now, before, goodWhenUp, spark, plain }: { label: string; value: string; now?: number; before?: number; goodWhenUp?: boolean; spark: number[]; plain?: boolean }) {
  const delta = now !== undefined && before !== undefined && before > 0 ? ((now - before) / before) * 100 : null;
  const up = (delta ?? 0) >= 0;
  const color = delta === null ? TEXT2 : up === (goodWhenUp ?? true) ? GREEN : RED;
  const Arrow = up ? ArrowUpRight : ArrowDownRight;
  return (
    <Card className="!p-2.5">
      <p className="truncate text-[11px] font-medium text-[#605e5c]">{label}</p>
      <p className="mt-0.5 text-[22px] font-semibold leading-tight text-[#201f1e]">{value}</p>
      <div className="mt-auto flex items-end justify-between gap-2">
        <span className="inline-flex items-center text-[11px] font-semibold" style={{ color }} title="vs. previous period">
          {plain ? <span className="font-normal">live</span> : delta === null ? <span className="font-normal">no earlier data</span> : (
            <><Arrow size={13} aria-hidden />{Math.abs(delta).toFixed(1)}%<span className="sr-only">{up ? " up" : " down"} versus previous period</span></>
          )}
        </span>
        <div className="h-6 w-16" aria-hidden><Spark data={spark} color={color === TEXT2 ? BLUE : color} /></div>
      </div>
    </Card>
  );
}

function LimitBar({ label, w }: { label: string; w: ClaudeWindow | null | undefined }) {
  if (!w) return <div className="text-[11px] text-[#605e5c]">{label}: not available</div>;
  const p = Math.min(100, w.usedPercent);
  const color = p >= 90 ? RED : p >= 70 ? AMBER_FILL : GREEN;
  return (
    <div>
      <div className="flex justify-between text-[11px] text-[#605e5c]">
        <span>{label}</span><span className="font-semibold text-[#201f1e]">{pct(p)} used</span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded bg-[#edebe9]" role="progressbar" aria-label={`${label} used`} aria-valuenow={Math.round(p)} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded" style={{ width: `${p}%`, background: color }} />
      </div>
      <div className="mt-0.5 text-[10px] text-[#605e5c]">{fmtReset(w.resetsAt)}</div>
    </div>
  );
}

function Ring({ value, color }: { value: number; color: string }) {
  const r = 20, c = 2 * Math.PI * r;
  return (
    <svg width="52" height="52" viewBox="0 0 52 52" role="img" aria-label={`Health ${value} out of 100`}>
      <circle cx="26" cy="26" r={r} fill="none" stroke="#edebe9" strokeWidth="6" />
      <circle cx="26" cy="26" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" strokeDasharray={`${(value / 100) * c} ${c}`} transform="rotate(-90 26 26)" />
      <text x="26" y="30" textAnchor="middle" fontSize="12" fontWeight="600" fill="#201f1e">{value}</text>
    </svg>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return (
    <ul className="mt-1 flex shrink-0 justify-center gap-3 text-[11px] text-[#605e5c]">
      {items.map(([n, c]) => <li key={n} className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: c }} aria-hidden />{n}</li>)}
    </ul>
  );
}

function Empty({ text, icon }: { text: string; icon?: boolean }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-1 p-2 text-center text-[12px] text-[#605e5c]">
      {icon && <CheckCircle2 size={22} color={GREEN} aria-hidden />}{text}
    </div>
  );
}

function Skeleton() {
  const b = "animate-pulse rounded-lg bg-[#edebe9]";
  return (
    <div className="grid h-full grid-cols-[1fr_280px] gap-3 p-3" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid min-h-0 grid-rows-[76px_92px_1fr_150px_1fr] gap-3"><div className={b} /><div className={b} /><div className={b} /><div className={b} /><div className={b} /></div>
      <div className={b} />
    </div>
  );
}

// ----------------------------------------------------------------------------------------------------------------------

export function ClaudeDashboardPage() {
  const [range, setRange] = useState<RangeId>("24h");
  const [sel, setSel] = useState<string>("all");
  const [data, setData] = useState<ClaudeDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      setBusy(true);
      api.claudeDashboard(range, sel)
        .then((d) => { if (!cancelled) { setData(d); setError(null); setUpdated(new Date()); } })
        .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the dashboard"); })
        .finally(() => { if (!cancelled) setBusy(false); });
    };
    load();
    const t = setInterval(load, 5_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [range, sel, tick]);

  const series = useMemo(() => (data?.series ?? []).map((b) => ({ t: tickLabel(range, b.ts), requests: b.requests, cost: +b.cost.toFixed(2), input: b.input, output: b.output })), [data, range]);
  const accounts = data?.accounts ?? [];
  const shown = sel === "all" ? accounts : accounts.filter((a) => a.name === sel);
  const worst: Status = accounts.some((a) => a.status === "Critical") ? "Critical" : accounts.some((a) => a.status === "Warning") ? "Warning" : "Healthy";
  const tot = data?.totals;
  const prev = data?.previous;
  const empty = !!tot && tot.requests === 0;
  const spark = (f: (b: ClaudeDashboard["series"][number]) => number) => (data?.series ?? []).map(f);

  const tipStyle = { fontSize: 11, borderRadius: 6, border: "1px solid #e1dfdd", padding: "4px 8px" };
  const axis = { fontSize: 10, fill: TEXT2 } as const;

  const selectorCards: { id: string; name: string; status: Status; t: ClaudeTotals }[] = [
    { id: "all", name: "All Accounts", status: worst, t: tot ?? { requests: 0, tokens: 0, cost: 0, rateLimited: 0, errorRate: 0 } },
    ...accounts.map((a) => ({ id: a.name, name: a.name, status: a.status, t: a as ClaudeTotals })),
  ];

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#faf9f8] text-[#201f1e]">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-[#e1dfdd] bg-white px-4">
        <h1 className="text-base font-semibold">Claude Operations Dashboard</h1>
        <span className="hidden items-center gap-1.5 text-[11px] text-[#605e5c] md:inline-flex" aria-live="off">
          <span className="relative flex h-2.5 w-2.5" aria-hidden>
            <span className={`absolute inline-flex h-full w-full rounded-full ${error ? "bg-[#d13438]" : "animate-ping bg-[#107c10]"} opacity-60`} />
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${error ? "bg-[#d13438]" : "bg-[#107c10]"}`} />
          </span>
          <strong className={error ? "text-[#a4262c]" : "text-[#0e700e]"}>{error ? "Reconnecting…" : "LIVE"}</strong>
          {updated ? `· updated ${Math.max(0, Math.round((now - updated.getTime()) / 1000))}s ago` : "· loading…"}
          {data ? ` · counting since ${new Date(data.since * 1000).toLocaleTimeString()}` : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <div role="group" aria-label="Time range" className="flex overflow-hidden rounded border border-[#c8c6c4]">
            {RANGES.map((r) => (
              <button key={r.id} onClick={() => setRange(r.id)} aria-pressed={range === r.id}
                className={`px-2.5 py-1 text-xs ${range === r.id ? "bg-[#0078d4] font-semibold text-white" : "bg-white text-[#201f1e] hover:bg-[#f3f2f1]"}`}>{r.label}</button>
            ))}
          </div>
          <button onClick={() => setTick((t) => t + 1)} aria-label="Refresh data" title="Refresh" className="flex h-8 w-8 items-center justify-center rounded border border-[#c8c6c4] bg-white hover:bg-[#f3f2f1]">
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {error && !data ? (
        <div role="alert" className="m-4 rounded border border-[#f1bbbc] bg-[#fde7e9] p-3 text-sm text-[#a4262c]">{error}</div>
      ) : !data || !tot || !prev ? <Skeleton /> : (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_280px] gap-3 p-3">
          <div className="grid min-h-0 min-w-0 grid-rows-[auto_auto_minmax(0,1fr)_auto_minmax(0,1.05fr)] gap-3">
            {/* Account selector */}
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${selectorCards.length}, minmax(0, 1fr))` }} role="tablist" aria-label="Account">
              {selectorCards.map((a) => {
                const active = sel === a.id;
                return (
                  <button key={a.id} role="tab" aria-selected={active} onClick={() => setSel(a.id)}
                    className={`rounded-lg border bg-white p-2 text-left shadow-[0_1px_2px_rgba(0,0,0,0.05)] ${active ? "border-[#0078d4] ring-1 ring-[#0078d4]" : "border-[#e1dfdd] hover:border-[#a19f9d]"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-semibold">{a.name}</span><StatusBadge s={a.status} />
                    </div>
                    <dl className="mt-1 grid grid-cols-3 gap-x-2 gap-y-0.5 text-[10px] text-[#605e5c]">
                      {([["Reqs", compact(a.t.requests)], ["Tokens", compact(a.t.tokens)], ["Cost", money(a.t.cost)], ["429s", String(a.t.rateLimited)], ["Errors", `${a.t.errorRate.toFixed(1)}%`]] as const).map(([k, v]) => (
                        <div key={k}><dt className="inline">{k} </dt><dd className="inline font-semibold text-[#201f1e]">{v}</dd></div>
                      ))}
                    </dl>
                  </button>
                );
              })}
            </div>

            {/* KPI row */}
            <div className="grid grid-cols-6 gap-3">
              <Kpi label="Claude calls" value={compact(tot.requests)} now={tot.requests} before={prev.requests} spark={spark((b) => b.requests)} />
              <Kpi label="Tokens used" value={compact(tot.tokens)} now={tot.tokens} before={prev.tokens} spark={spark((b) => b.input + b.output)} />
              <Kpi label="Est. cost (API price)" value={money(tot.cost)} now={tot.cost} before={prev.cost} goodWhenUp={false} spark={spark((b) => b.cost)} />
              <Kpi label="Rate-limited (429)" value={String(tot.rateLimited)} now={tot.rateLimited} before={prev.rateLimited} goodWhenUp={false} spark={spark((b) => b.requests)} />
              <Kpi label="Error rate" value={`${tot.errorRate.toFixed(1)}%`} now={tot.errorRate} before={prev.errorRate} goodWhenUp={false} spark={spark((b) => b.requests)} />
              <Kpi label="Running now" value={String(data.jobs.running)} plain spark={spark((b) => b.requests)} />
            </div>

            {/* Charts */}
            <div className="grid min-h-0 grid-cols-2 gap-3">
              <Card title="Cost & Calls Over Time" right={<span className="text-[11px] text-[#605e5c]">{RANGES.find((r) => r.id === range)?.label}</span>}>
                {empty ? <Empty text="No Claude calls recorded in this range yet. Ask a question or wait for a Radar/Trends run." /> : (<>
                  <div className="min-h-0 flex-1">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: -10 }}>
                        <CartesianGrid stroke="#edebe9" vertical={false} />
                        <XAxis dataKey="t" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
                        <YAxis yAxisId="l" tick={axis} tickLine={false} axisLine={false} tickFormatter={(v) => `$${compact(v)}`} width={44} />
                        <YAxis yAxisId="r" orientation="right" tick={axis} tickLine={false} axisLine={false} tickFormatter={compact} width={40} allowDecimals={false} />
                        <Tooltip contentStyle={tipStyle} />
                        <Area yAxisId="l" type="monotone" dataKey="cost" name="Est. cost ($)" stroke={BLUE} fill={BLUE} fillOpacity={0.12} strokeWidth={2} isAnimationActive={false} />
                        <Line yAxisId="r" type="monotone" dataKey="requests" name="Calls" stroke="#5c2d91" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                  <Legend items={[["Est. cost ($)", BLUE], ["Calls", "#5c2d91"]]} />
                </>)}
              </Card>
              <Card title="Token Usage Over Time" right={<span className="text-[11px] text-[#605e5c]">Total {compact(tot.tokens)} · Cost {money(tot.cost)}</span>}>
                {empty ? <Empty text="No tokens used in this range yet." /> : (<>
                  <div className="min-h-0 flex-1">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={series} margin={{ top: 4, right: 0, bottom: 0, left: -10 }}>
                        <CartesianGrid stroke="#edebe9" vertical={false} />
                        <XAxis dataKey="t" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
                        <YAxis yAxisId="l" tick={axis} tickLine={false} axisLine={false} tickFormatter={compact} width={44} />
                        <YAxis yAxisId="r" orientation="right" tick={axis} tickLine={false} axisLine={false} tickFormatter={(v) => `$${compact(v)}`} width={40} />
                        <Tooltip contentStyle={tipStyle} />
                        <Bar yAxisId="l" stackId="t" dataKey="input" name="Input tokens" fill={BLUE} isAnimationActive={false} />
                        <Bar yAxisId="l" stackId="t" dataKey="output" name="Output tokens" fill="#8fc7f0" isAnimationActive={false} />
                        <Line yAxisId="r" type="monotone" dataKey="cost" name="Cost ($)" stroke="#c239b3" strokeWidth={2} dot={false} isAnimationActive={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                  <Legend items={[["Input tokens", BLUE], ["Output tokens", "#8fc7f0"], ["Cost ($)", "#c239b3"]]} />
                </>)}
              </Card>
            </div>

            {/* Account health */}
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(1, shown.length)}, minmax(0, 1fr))` }}>
              {shown.length === 0 ? <Card><Empty text="No accounts yet. Add SHARED_CLAUDE_TOKENS to the server .env." /></Card> : shown.map((a) => <HealthCard key={a.name} a={a} />)}
            </div>

            {/* Features: who uses the tokens */}
            <FeatureRow data={data} />
          </div>

          <PeopleControl accounts={accounts} active={data.active} reload={() => setTick((t) => t + 1)} />
        </div>
      )}
    </div>
  );
}

const modelName = (id: string, models: { id: string; label: string }[]) => {
  const hit = models.find((m) => m.id === id);
  if (hit) return hit.label.replace(/^Claude /, "");
  const m = /(opus|sonnet|haiku)/i.exec(id);
  return m ? m[1][0].toUpperCase() + m[1].slice(1).toLowerCase() : id || "Default";
};

function FeatureRow({ data }: { data: ClaudeDashboard }) {
  const top = [...data.features].sort((a, b) => b.tokens - a.tokens)[0];
  return (
    <div className="grid min-h-0 grid-cols-4 gap-3">
      {data.features.map((f) => <FeatureCard key={f.name} f={f} data={data} heaviest={!!top && top.tokens > 0 && top.name === f.name} />)}
    </div>
  );
}

function FeatureCard({ f, data, heaviest }: { f: ClaudeFeatureStat; data: ClaudeDashboard; heaviest: boolean }) {
  const override = data.featureModels[f.name];
  const modelId = override || data.defaultModels[f.name] || f.model;
  const unused = f.calls === 0;
  const inPct = f.tokens ? (f.inputTokens / f.tokens) * 100 : 0;
  return (
    <Card className={`!p-2.5 ${heaviest ? "!border-[#d83b01] ring-1 ring-[#d83b01]" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="truncate text-[13px] font-semibold">{f.name}</h3>
        <span className="shrink-0 rounded bg-[#deecf9] px-1.5 py-0.5 text-[10px] font-semibold text-[#004578]" title={override ? "Model chosen by an admin" : "This feature's default model"}>
          {modelName(modelId, data.models)}{override ? "" : " · default"}
        </span>
      </div>
      {heaviest && (
        <p className="mt-0.5 inline-flex w-fit items-center gap-1 rounded-full bg-[#fed9cc] px-2 py-0.5 text-[10px] font-semibold text-[#a80000]">
          <Flame size={11} aria-hidden /> Uses the most tokens
        </p>
      )}
      {unused ? <Empty text="No calls in this range yet." /> : (<>
        <p className="mt-1 text-[22px] font-semibold leading-none">{compact(f.tokens)} <span className="text-[11px] font-normal text-[#605e5c]">tokens · {f.share.toFixed(0)}% of all</span></p>
        <div className="mt-1.5 h-2 overflow-hidden rounded bg-[#edebe9]" role="img" aria-label={`${f.share.toFixed(0)} percent of all tokens`}>
          <div className="h-full rounded" style={{ width: `${Math.max(2, f.share)}%`, background: heaviest ? "#d83b01" : BLUE }} />
        </div>
        <p className="mt-0.5 flex justify-between text-[10px] text-[#605e5c]"><span>Input {compact(f.inputTokens)} ({inPct.toFixed(0)}%)</span><span>Output {compact(f.outputTokens)}</span></p>
        <dl className="mt-auto grid grid-cols-3 gap-x-2 gap-y-1 pt-1.5 text-[10px] text-[#605e5c]">
          <div><dt>Calls</dt><dd className="text-[13px] font-semibold text-[#201f1e]">{f.calls}</dd></div>
          <div><dt>Tokens / request</dt><dd className="text-[13px] font-semibold text-[#201f1e]">{compact(f.tokensPerCall)}</dd></div>
          <div><dt>Cost / request</dt><dd className="text-[13px] font-semibold text-[#201f1e]">{money(f.costPerCall)}</dd></div>
          <div><dt>Total cost</dt><dd className="text-[13px] font-semibold text-[#201f1e]">{money(f.cost)}</dd></div>
          <div><dt>Fail rate</dt><dd className="text-[13px] font-semibold" style={{ color: f.failRate > 5 ? RED : f.failRate > 2 ? AMBER : GREEN }}>{f.failRate}%</dd></div>
          <div><dt>Avg time</dt><dd className="text-[13px] font-semibold text-[#201f1e]">{f.avgLatency}s</dd></div>
        </dl>
      </>)}
    </Card>
  );
}

function HealthCard({ a }: { a: ClaudeLiveAccount }) {
  const st = STATUS_STYLE[a.status];
  return (
    <Card className="!p-2.5">
      <div className="flex items-center gap-2.5">
        <span title={`Health score (not a fixed number): 100 − 0.6 × ${Math.max(a.limits?.fiveHour?.usedPercent ?? 0, a.limits?.sevenDay?.usedPercent ?? 0).toFixed(0)}% (the fuller of the 5-hour and weekly limits) − ${Math.min(30, a.errorRate * 3).toFixed(0)} for the error rate − ${Math.min(20, a.rateLimited15m * 5)} for recent 429s = ${a.health}`}><Ring value={a.health} color={st.color} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2"><span className="truncate text-[13px] font-semibold">{a.name}</span><StatusBadge s={a.status} /></div>
          <p className="truncate text-[10px] text-[#605e5c]">{a.active ? "Used by the whole app · " : ""}last used {fmtAgo(a.lastUsed)}</p>
          <p className="text-[11px]"><strong>{a.requests}</strong> calls · <strong>{compact(a.tokens)}</strong> tokens · <strong>{money(a.cost)}</strong></p>
        </div>
      </div>
      <div className="mt-1.5 grid gap-1.5">
        <LimitBar label="5-hour limit" w={a.limits?.fiveHour} />
        <LimitBar label="Weekly limit" w={a.limits?.sevenDay} />
      </div>
      <p className="mt-1.5 flex justify-between text-[11px] text-[#605e5c]">
        <span>429s (15 min): <strong style={{ color: a.rateLimited15m > 0 ? RED : "#201f1e" }}>{a.rateLimited15m}</strong></span>
        <span>Error rate: <strong style={{ color: a.errorRate > 5 ? RED : a.errorRate > 2 ? AMBER : "#201f1e" }}>{a.errorRate.toFixed(1)}%</strong></span>
      </p>
    </Card>
  );
}

/** Admin control: choose the one Claude account the whole app uses, and the model each feature runs on. */
function PeopleControl({ accounts, active, reload }: { accounts: ClaudeLiveAccount[]; active: string | null; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [usage, setUsage] = useState<ClaudeUsage | null>(null);
  const loadUsage = () => api.claudeUsage().then(setUsage).catch(() => setUsage(null));
  useEffect(() => { loadUsage(); }, [active]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setErr(null);
    try { await action(); await loadUsage(); reload(); } catch (e) { setErr(e instanceof Error ? e.message : "Could not save the change"); } finally { setBusy(false); }
  }
  return (
    <Card title="Claude account & models">
      <div role="status" className={`mb-2 rounded border px-2 py-1.5 text-[12px] leading-snug ${active ? "border-[#0078d4] bg-[#eff6fc] text-[#004578]" : "border-[#e1dfdd] bg-[#faf9f8] text-[#323130]"}`}>
        {active
          ? <>Right now <strong>everyone who signs in</strong> uses <strong>{active}</strong>&apos;s Claude account, for every feature.</>
          : <>No app-wide account is set. Each person uses their own token, or the shared account they pick themselves.</>}
      </div>
      {err && <p role="alert" className="mb-1 text-[11px] text-[#a4262c]">{err}</p>}
      {usage && (
        <div className="mb-2 rounded border border-[#e1dfdd] bg-white p-2 text-[11px] text-[#323130]">
          <label className="flex items-center justify-between gap-2">
            <span><strong>Auto-switch at {usage.switchPercent}%</strong><br /><span className="text-[#605e5c]">5-hour or weekly limit full: move everyone to the account with the most room.</span></span>
            <input type="checkbox" checked={usage.autoSwitch} disabled={busy} onChange={(e) => run(() => api.setClaudeAuto(e.target.checked))} className="h-4 w-4 shrink-0 accent-[#0078d4]" aria-label="Auto-switch accounts" />
          </label>
          <p className="mt-1 flex items-start gap-1 text-[#605e5c]"><Mail size={12} className="mt-0.5 shrink-0" aria-hidden />{usage.mailTo.length ? `Emails to ${usage.mailTo.join(", ")} when the account changes.` : "No email set up: add RADAR_MAIL_FROM and CLAUDE_NOTIFY_TO (or ADMIN_EMAILS) to .env."}</p>
          {usage.switchLog.length > 0 && (
            <ul className="mt-1 space-y-0.5 border-t border-[#edebe9] pt-1 text-[10px] text-[#605e5c]">
              {[...usage.switchLog].reverse().slice(0, 3).map((l, i) => (
                <li key={i}>{new Date(l.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} · {l.from ?? "none"} → <strong>{l.to ?? "each person"}</strong> · {l.reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
        {accounts.map((a) => {
          const inUse = a.name === active;
          return (
            <li key={a.name} className={`rounded border p-2 ${inUse ? "border-[#0078d4] bg-[#eff6fc]" : "border-[#e1dfdd] bg-white"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1 truncate text-[13px] font-semibold">{inUse && <UserCheck size={14} color={BLUE} aria-label="In use by the whole app" />}{a.name}</span>
                <StatusBadge s={a.status} />
              </div>
              <p className="mt-0.5 text-[11px] text-[#605e5c]">5-hour {pct(a.limits?.fiveHour?.usedPercent ?? 0)} · weekly {pct(a.limits?.sevenDay?.usedPercent ?? 0)}</p>
              <button disabled={busy || inUse} onClick={() => run(() => api.setActiveClaudeAccount(a.name))}
                className="mt-1.5 w-full rounded bg-[#0078d4] px-2 py-1 text-[11px] font-semibold text-white hover:bg-[#106ebe] disabled:cursor-default disabled:bg-[#c8c6c4] disabled:text-[#323130]">
                {inUse ? "In use by everyone" : "Use for everyone"}
              </button>
              {inUse && usage && (
                <div className="mt-2 border-t border-[#c7e0f4] pt-1.5">
                  <p className="text-[11px] font-semibold text-[#004578]">Model for each feature</p>
                  {usage.features.map((f) => (
                    <label key={f} className="mt-1 flex items-center justify-between gap-2 text-[11px] text-[#323130]">
                      <span className="truncate">{f}</span>
                      <select value={usage.featureModels[f] ?? ""} disabled={busy} aria-label={`Model for ${f}`}
                        onChange={(e) => run(() => api.setFeatureModel(f, e.target.value || null))}
                        className="w-[140px] rounded border border-[#8a8886] bg-white px-1 py-0.5 text-[11px]">
                        <option value="">Default ({usage.models.find((m) => m.id === usage.defaultModels[f])?.label.replace(/^Claude /, "") ?? usage.defaultModels[f]})</option>
                        {usage.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {active && <button disabled={busy} onClick={() => run(() => api.setActiveClaudeAccount(null))} className="mt-2 shrink-0 text-left text-[11px] font-semibold text-[#0f6cbd] hover:underline">Choose the best account automatically</button>}
      <p className="mt-1.5 shrink-0 text-[10px] leading-snug text-[#605e5c]">
        Limits are each plan&apos;s real usage (checked about once a minute). Calls, tokens and cost count what this app has run since the server started; cost is the API-price equivalent, not a bill. These choices reset when the server restarts.
      </p>
    </Card>
  );
}
