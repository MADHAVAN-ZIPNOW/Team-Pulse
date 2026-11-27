import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type TrendItem, type TrendPastResult } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { useMedia } from "../hooks/useMedia";
import { ErrorState, Spinner } from "../components/common";
import { MsLoader } from "../components/ChatPanel";
import { formatDuration, formatWhen, regionName } from "../lib/time";

type Status = "active" | "ended" | "all";
const WORLD = "WW";
const ROW_H = 44; // every row is exactly this tall, so the number of rows that fit is known and the page never scrolls
const POLL_MS = 20_000; // asks again by itself; the server re-reads Google when its short in-memory copy is old
const LIVE = [
  { id: "4", label: "Past 4 hours" },
  { id: "24", label: "Past 24 hours" },
  { id: "48", label: "Past 48 hours" },
  { id: "168", label: "Past 7 days" },
];
const DEFAULT_SORT = "traffic"; // loudest first: the list reads top to bottom by search volume
const SORTS = [
  { id: "traffic", label: "Most searches" },
  { id: "rank", label: "Google's own order" },
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
  { id: "longest", label: "Trending longest" },
];
const ctl = "h-8 rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100 outline-none focus:border-sky-500";
const COLS = "grid-cols-[2.25rem_minmax(0,1fr)_5.5rem_5.25rem] xl:grid-cols-[2.25rem_minmax(0,1fr)_5.5rem_5.25rem_13.5rem] 2xl:grid-cols-[2.25rem_minmax(0,1fr)_5.5rem_5.25rem_13.5rem_9rem_8.5rem]";
const today = () => new Date().toISOString().slice(0, 10);

const hue = (s: string) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };

/** Filtering, searching and sorting happen here, in the browser, so every click and keystroke is instant. */
function selectItems(items: TrendItem[], o: { status: Status; category: string; q: string; sort: string }): TrendItem[] {
  let rows = items;
  if (o.status === "active") rows = rows.filter((i) => i.endedAt === null);
  else if (o.status === "ended") rows = rows.filter((i) => i.endedAt !== null);
  if (o.category) rows = rows.filter((i) => i.category === o.category);
  if (o.q) {
    const n = o.q.toLowerCase();
    rows = rows.filter((i) => i.title.toLowerCase().includes(n) || i.related.some((r) => r.toLowerCase().includes(n)));
  }
  const dur = (i: TrendItem) => (i.endedAt ? Date.parse(i.endedAt) : Date.now()) - Date.parse(i.startedAt);
  const by: Record<string, (a: TrendItem, b: TrendItem) => number> = {
    rank: (a, b) => b.countries.length - a.countries.length || a.pos - b.pos,
    newest: (a, b) => b.startedAt.localeCompare(a.startedAt),
    oldest: (a, b) => a.startedAt.localeCompare(b.startedAt),
    longest: (a, b) => dur(b) - dur(a),
    traffic: (a, b) => b.volume - a.volume || a.pos - b.pos,
    traffic_asc: (a, b) => a.volume - b.volume,
    title_asc: (a, b) => a.title.localeCompare(b.title),
    title_desc: (a, b) => b.title.localeCompare(a.title),
    increase: (a, b) => b.increaseValue - a.increaseValue || b.volume - a.volume,
    increase_asc: (a, b) => a.increaseValue - b.increaseValue || a.volume - b.volume,
    category_asc: (a, b) => a.category.localeCompare(b.category) || a.pos - b.pos,
    category_desc: (a, b) => b.category.localeCompare(a.category) || a.pos - b.pos,
    countries_desc: (a, b) => b.countries.length - a.countries.length || a.pos - b.pos,
    countries_asc: (a, b) => a.countries.length - b.countries.length || a.pos - b.pos,
  };
  return [...rows].sort(by[o.sort] ?? by.rank);
}

function Chip({ text }: { text: string }) {
  const h = hue(text);
  return (
    <span style={{ background: `hsl(${h} 70% 93%)`, color: `hsl(${h} 55% 26%)` }} className="inline-block max-w-full truncate rounded px-2 py-0.5 text-[13px] font-medium">
      {text}
    </span>
  );
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
      {label}
      {children}
    </label>
  );
}

interface Msg { role: "user" | "assistant"; text: string; steps?: string[]; startedAt?: number; error?: boolean }

export function TrendsPage() {
  const meta = useAsync(() => api.trendsMeta(), []);
  const [geo, setGeo] = useState("IN");
  const [status, setStatus] = useState<Status>("active");
  const [category, setCategory] = useState("");
  const [period, setPeriod] = useState("24"); // "4" | "24" | "48" | "168" (live) | "custom" | "year:2024" (Claude looks it up)
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [panel, setPanel] = useState(false);
  const [explain, setExplain] = useState(false);
  const explainRef = useRef<HTMLSpanElement>(null);
  const [sel, setSel] = useState<TrendItem | null>(null);
  const [modal, setModal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [chats, setChats] = useState<Record<string, Msg[]>>({}); // one conversation per trend; kept in this tab only
  const [asking, setAsking] = useState<Record<string, boolean>>({});
  const [deep, setDeep] = useState(false);
  const aborts = useRef<Record<string, AbortController>>({});
  const bodyRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const wide = useMedia("(min-width: 1024px)");

  const q = search.trim();
  const isPast = period === "custom" || period.startsWith("year:");
  const hours = Number(period) || 24;
  const years = useMemo(() => Array.from({ length: 10 }, (_, i) => new Date().getFullYear() - i), []);
  const pastRange = useMemo(() => {
    if (period.startsWith("year:")) {
      const y = Number(period.slice(5));
      return { from: `${y}-01-01`, to: y === new Date().getFullYear() ? today() : `${y}-12-31` };
    }
    if (period === "custom" && fromDate) return { from: fromDate, to: toDate || fromDate };
    return null;
  }, [period, fromDate, toDate]);

  useEffect(() => { setPage(0); }, [geo, status, category, period, fromDate, toDate, q, sort]);
  useEffect(() => {  // rows that fit: measured, so there is never anything to scroll
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setPageSize(Math.max(3, Math.floor(el.clientHeight / ROW_H))));
    setPageSize(Math.max(3, Math.floor(el.clientHeight / ROW_H)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [!!meta.data]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // close the filter panel on outside click / Escape
    if (!panel) return;
    const down = (e: MouseEvent) => { if (!panelRef.current?.contains(e.target as Node)) setPanel(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setPanel(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [panel]);

  useEffect(() => {  // close the chart explainer on outside click / Escape
    if (!explain) return;
    const down = (e: MouseEvent) => { if (!explainRef.current?.contains(e.target as Node)) setExplain(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setExplain(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [explain]);

  // ---- live list: the WHOLE list is loaded once, then everything is instant in the browser ------
  const all = useAsync(
    () => api.trends({ geo, hours, status: "all", category: "", q: "", sort: "rank", limit: 1500, offset: 0 }),
    [geo, hours],
    !!meta.data && !isPast,
  );
  const lastAll = useRef(all.data);
  if (all.data) lastAll.current = all.data;
  useEffect(() => {  // updates itself: no clicking needed
    const t = setInterval(() => { setNow(Date.now()); if (!isPast) all.reload(); }, POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPast]);

  // ---- old dates: Claude looks them up on the web (slow, so poll until ready) ---------------------
  const [past, setPast] = useState<TrendPastResult | null>(null);
  useEffect(() => {
    if (!isPast || !pastRange) { setPast(null); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const r = await api.trendPast(geo, pastRange.from, pastRange.to);
        if (cancelled) return;
        setPast(r);
        if (r.status === "loading") timer = setTimeout(run, 2500);
      } catch (e) {
        if (!cancelled) setPast({ status: "error", error: e instanceof Error ? e.message : "Could not fetch that date." });
      }
    };
    setPast({ status: "loading" });
    void run();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [isPast, geo, pastRange?.from, pastRange?.to]);  // eslint-disable-line react-hooks/exhaustive-deps

  const liveData = isPast ? null : all.data ?? lastAll.current;
  const source: TrendItem[] = isPast ? (past?.status === "ready" ? past.items : []) : liveData?.items ?? [];
  const filtered = useMemo(
    () => selectItems(source, { status: isPast ? "all" : status, category, q, sort }),
    [source, isPast, status, category, q, sort],
  );
  const hiddenByFilters = source.length - filtered.length;
  const total = filtered.length;
  const items = filtered.slice(page * pageSize, page * pageSize + pageSize);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => { if (page > pages - 1) setPage(pages - 1); }, [page, pages]);
  const categories = useMemo(() => [...new Set(source.map((i) => i.category))].sort(), [source]);
  useEffect(() => { if (!sel && items.length && wide) setSel(items[0]); }, [items, sel, wide]);

  type Curve = { values: number[]; t0: string; t1: string } | null;
  const [curves, setCurves] = useState<Record<string, Curve>>({}); // undefined = still loading, null = Google has none right now
  const asked = useRef(new Set<string>());
  const pageKey = items.map((i) => i.id).join("|");
  useEffect(() => {
    if (isPast) return;
    const todo = items.filter((t) => !asked.current.has(`${geo}|${hours}|${t.title}`));
    todo.forEach((t) => asked.current.add(`${geo}|${hours}|${t.title}`));
    const run = async (t: TrendItem) => {
      const k = `${geo}|${hours}|${t.title}`;
      try {
        const r = await api.trendSeries(geo, hours, t.title);
        setCurves((c) => ({ ...c, [k]: r.values && r.t0 && r.t1 ? { values: r.values, t0: r.t0, t1: r.t1 } : null }));
        if (!r.values) setTimeout(() => asked.current.delete(k), 90_000);  // try again later if Google was busy
      } catch {
        setCurves((c) => ({ ...c, [k]: null }));
        setTimeout(() => asked.current.delete(k), 90_000);
      }
    };
    const queue = [...todo];
    const worker = async () => { while (queue.length) await run(queue.shift()!); };
    void Promise.all([worker(), worker(), worker()]);  // three at a time: kind to Google, and rows fill in as they arrive
  }, [pageKey, geo, hours, isPast]);  // eslint-disable-line react-hooks/exhaustive-deps

  const countries = useMemo(() => [...(meta.data?.countries ?? [])].sort((a, b) => regionName(a).localeCompare(regionName(b))), [meta.data?.countries]);

  const refresh = async () => {
    setActionError(null);
    setBusy(true);
    try { await api.refreshTrends(geo, hours); all.reload(); }
    catch (e) { setActionError(e instanceof Error ? e.message : "Could not read Google right now."); }
    finally { setBusy(false); }
  };
  const pickPeriod = (v: string) => {
    setPeriod(v);
    if (v === "custom") setPanel(true);
    if (v === "custom" || v.startsWith("year:")) { setSearch(""); setCategory(""); setSort(DEFAULT_SORT); }  // live-list filters rarely apply to an old date
  };
  const reset = () => { setStatus("active"); setCategory(""); setPeriod("24"); setFromDate(""); setToDate(""); setSearch(""); setSort(DEFAULT_SORT); };
  const clearFilters = () => { setStatus("active"); setCategory(""); setSearch(""); setSort(DEFAULT_SORT); };
  const applied = [
    status !== "active" && !isPast ? (status === "ended" ? "Ended only" : "Everything") : "",
    category ? `Category: ${category}` : "",
    period !== "24" ? (LIVE.find((x) => x.id === period)?.label ?? (period.startsWith("year:") ? `Year ${period.slice(5)}` : "Picked dates")) : "",
    q ? `Search: “${q}”` : "",
    sort !== DEFAULT_SORT ? `Sorted: ${SORTS.find((x) => x.id === sort)?.label ?? sort.replace(/_asc$|_desc$/, "").replace("traffic", "volume")}` : "",
  ].filter(Boolean);
  const activeFilters = (status !== "active" ? 1 : 0) + (category ? 1 : 0) + (sort !== DEFAULT_SORT ? 1 : 0);
  const select = (t: TrendItem) => { setSel(t); if (!wide) setModal(true); };

  // ---- chat about a trend (streams from Claude) -------------------------------------------------
  const ask = async (t: TrendItem, question: string) => {
    const id = t.id;
    if (asking[id] || !question.trim()) return;
    const history = (chats[id] ?? []).filter((m) => !m.error && m.text).map((m) => ({ role: m.role, text: m.text }));
    setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), { role: "user", text: question }, { role: "assistant", text: "", steps: [], startedAt: Date.now() }] }));
    setAsking((a) => ({ ...a, [id]: true }));
    const ctrl = new AbortController();
    aborts.current[id] = ctrl;
    const patch = (fn: (m: Msg) => Msg) => setChats((c) => {
      const list = [...(c[id] ?? [])];
      if (list.length) list[list.length - 1] = fn(list[list.length - 1]);
      return { ...c, [id]: list };
    });
    try {
      await api.trendChat({ geo: t.geo, title: t.title, question, history, deep }, (ev) => {
        if (ev.type === "delta") patch((m) => ({ ...m, text: m.text + ev.text }));
        else if (ev.type === "status") patch((m) => ({ ...m, steps: [...(m.steps ?? []), ev.text] }));
        else if (ev.type === "error") patch((m) => ({ ...m, error: true, text: m.text || ev.text }));
      }, ctrl.signal);
    } catch (e) {
      if (!ctrl.signal.aborted) patch((m) => ({ ...m, error: true, text: m.text || (e instanceof Error ? e.message : "Could not reach Claude.") }));
    } finally {
      setAsking((a) => ({ ...a, [id]: false }));
    }
  };
  const stop = (id: string) => aborts.current[id]?.abort();
  const clearChat = (id: string) => setChats((c) => ({ ...c, [id]: [] }));
  const chatPane = (t: TrendItem | null) => (
    <TrendChat t={t} past={isPast} now={now} curve={t ? curves[`${geo}|${hours}|${t.title}`] : undefined} hours={hours} messages={t ? chats[t.id] ?? [] : []} busy={!!(t && asking[t.id])}
      deep={deep} setDeep={setDeep} onAsk={(text) => t && ask(t, text)} onStop={() => t && stop(t.id)} onClear={() => t && clearChat(t.id)} />
  );

  if (meta.error && !meta.data) return <div className="p-4"><ErrorState message={meta.error} onRetry={meta.reload} /></div>;
  if (!meta.data) return <div className="p-6"><Spinner label="Loading trends…" /></div>;

  const pastWaiting = isPast && !!pastRange && (past === null || past.status === "loading");
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      {/* Command bar */}
      <div className="relative flex shrink-0 flex-wrap items-end gap-x-3 gap-y-2 border-b border-slate-800 bg-white px-4 py-2" role="search" aria-label="Trend filters">
        <h2 className="flex items-center gap-2 self-center pr-1 text-base font-semibold text-slate-100">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0078d4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></svg>
          Google Trends
        </h2>
        <Labeled label="Country">
          <select value={geo} onChange={(e) => { setGeo(e.target.value); setActionError(null); }} className={`${ctl} w-40`}>
            <option value={WORLD}>🌍 Worldwide</option>
            {countries.map((c) => <option key={c} value={c}>{regionName(c)}</option>)}
          </select>
        </Labeled>
        <Labeled label="Period">
          <select value={period} onChange={(e) => pickPeriod(e.target.value)} className={`${ctl} w-40`}>
            <optgroup label="Live from Google">
              {LIVE.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </optgroup>
            <optgroup label="Older (Claude looks it up)">
              <option value="custom">Pick a date or range…</option>
              {years.map((y) => <option key={y} value={`year:${y}`}>Year {y}</option>)}
            </optgroup>
          </select>
        </Labeled>
        <div className="flex min-w-[9rem] flex-1 items-end">
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search trends" aria-label="Search trends" className={`${ctl} w-full`} />
        </div>
        <div ref={panelRef} className="relative">
          <button
            onClick={() => setPanel((o) => !o)}
            aria-expanded={panel}
            className={`h-8 rounded border px-3 text-sm ${panel || activeFilters ? "border-sky-600 bg-sky-950 text-sky-600" : "border-slate-700 text-slate-300 hover:bg-slate-950"}`}
          >
            Filters{activeFilters ? ` (${activeFilters})` : " ▾"}
          </button>
          {panel && (
            <div className="absolute right-0 top-full z-20 mt-1 flex w-72 flex-col gap-3 rounded-lg border border-slate-800 bg-white p-4 shadow-lg">
              {period === "custom" && (
                <div className="grid grid-cols-2 gap-2">
                  <Labeled label="From"><input type="date" value={fromDate} max={toDate || today()} onChange={(e) => setFromDate(e.target.value)} className={`${ctl} w-full`} /></Labeled>
                  <Labeled label="To"><input type="date" value={toDate} min={fromDate || undefined} max={today()} onChange={(e) => setToDate(e.target.value)} className={`${ctl} w-full`} /></Labeled>
                </div>
              )}
              <Labeled label="Show">
                <select value={status} onChange={(e) => setStatus(e.target.value as Status)} disabled={isPast} className={`${ctl} w-full disabled:opacity-50`}>
                  <option value="active">Trending now</option>
                  <option value="ended">Ended</option>
                  <option value="all">Everything</option>
                </select>
              </Labeled>
              <Labeled label="Category">
                <select value={category} onChange={(e) => setCategory(e.target.value)} className={`${ctl} w-full`}>
                  <option value="">All categories</option>
                  {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Labeled>
              <Labeled label="Sorted by">
                <select value={sort} onChange={(e) => setSort(e.target.value)} className={`${ctl} w-full`}>
                  {SORTS.filter((s) => !isPast || s.id === "rank" || s.id === "traffic").map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </Labeled>
              <button onClick={reset} className="self-start rounded px-1 text-sm text-sky-600 hover:underline">Reset all filters</button>
            </div>
          )}
        </div>
        {!isPast && (
          <button onClick={refresh} disabled={busy} className="h-8 rounded bg-sky-600 px-3 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-60" title="Read Google Trends again right now">
            {busy ? "Reading…" : "↻ Refresh now"}
          </button>
        )}
      </div>
      {actionError && <div className="shrink-0 px-4 pt-2"><ErrorState message={actionError} /></div>}

      {/* Table + chat */}
      <div className="flex min-h-0 flex-1 gap-3 px-4 py-3">
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-800 bg-white shadow-sm">
          {isPast && (
            <div className="shrink-0 border-b border-slate-800 bg-sky-950 px-4 py-1.5 text-[13px] text-sky-300">
              {!pastRange ? "Pick a date or range (Period → Pick a date or range) and Claude looks up what was trending then."
                : pastWaiting ? "Claude is looking this up on the web. Older dates can take a minute…"
                : past?.status === "ready" ? (
                  <span className="block truncate" title={past.note}>
                    <strong>{past.fromGoogle ? "From Google" : "Not Google's own list"}</strong>
                    {" · found by Claude (best effort)"}
                    {past.source ? <> · <a href={past.source.url} target="_blank" rel="noopener noreferrer" className="underline">{past.source.title}</a></> : null}
                    {past.note ? ` · ${past.note}` : ""}
                  </span>
                ) : ""}
            </div>
          )}
          <div role="row" className={`relative grid shrink-0 ${COLS} items-center gap-x-3 border-b border-slate-800 bg-slate-950 px-4 py-2 text-[13px] font-semibold text-slate-300`}>
            <Th label="#" a="rank" b="rank" ia="" ib="" sort={sort} setSort={setSort} title="Google's own order (relevance)" />
            <Th label="Trend" a="title_asc" b="title_desc" ia="▲" ib="▼" sort={sort} setSort={setSort} />
            <Th label="Volume" a="traffic" b="traffic_asc" ia="▼" ib="▲" sort={sort} setSort={setSort} />
            <Th label="Increase" a="increase" b="increase_asc" ia="▼" ib="▲" sort={sort} setSort={setSort} />
            <span ref={explainRef} className="relative hidden items-center gap-1 xl:flex">
              Search interest · {({ 4: "4 h", 24: "24 h", 48: "48 h", 168: "7 d" } as Record<number, string>)[hours] ?? ""}
              <button onClick={() => setExplain((o) => !o)} aria-expanded={explain} aria-label="What does this chart show?" className="flex h-4 w-4 items-center justify-center rounded-full border border-slate-600 text-[10px] font-bold text-slate-400 hover:border-sky-600 hover:text-sky-600">i</button>
              {explain && (
                <div role="dialog" className="absolute left-0 top-full z-20 mt-2 w-80 rounded-lg border border-slate-800 bg-white p-3 text-[13px] font-normal leading-relaxed text-slate-300 shadow-lg">
                  <p className="mb-1 font-semibold text-slate-100">How to read this chart</p>
                  <p>Each line is how many people searched for that topic on Google, from <strong>{hours === 168 ? "7 days" : `${hours} hours`} ago (left)</strong> to <strong>now (right)</strong>. Higher means more searches.</p>
                  <p className="mt-1">The ring marks the <strong>peak</strong>. The pulsing dot is <strong>now</strong>. Every trend is scaled to its own peak, so compare the shapes, not the heights. Search volume is in the Volume column.</p>
                  <ul className="mt-2 space-y-0.5">
                    {(Object.values(MOMENTUM)).map((m) => <li key={m.label}><span className="font-semibold" style={{ color: m.color }}>{m.icon} {m.label}</span> · {m.hint}</li>)}
                  </ul>
                </div>
              )}
            </span>
            {geo === WORLD
              ? <Th label="Countries" a="countries_desc" b="countries_asc" ia="▼" ib="▲" sort={sort} setSort={setSort} cls="hidden 2xl:flex" />
              : <Th label="Category" a="category_asc" b="category_desc" ia="▲" ib="▼" sort={sort} setSort={setSort} cls="hidden 2xl:flex" />}
            <Th label="Status" a="newest" b="oldest" ia="▼" ib="▲" sort={sort} setSort={setSort} cls="hidden 2xl:flex" title="Newest first / longest running first" />
          </div>
          <div ref={bodyRef} className="min-h-0 flex-1 overflow-hidden" role="table" aria-label="Trends">
            {pastWaiting ? (
              <div className="p-6"><Spinner label="Claude is looking this up…" /></div>
            ) : isPast && past?.status === "error" ? (
              <div className="p-4"><ErrorState message={past.error} /></div>
            ) : !isPast && all.error && !liveData ? (
              <div className="p-4"><ErrorState message={all.error} onRetry={all.reload} /></div>
            ) : !isPast && !liveData ? (
              <Skeleton n={pageSize} />
            ) : items.length === 0 ? (
              <div className="p-6 text-[15px] text-slate-400">
                {isPast && !pastRange ? "" : hiddenByFilters > 0 ? (
                  <>{hiddenByFilters} {hiddenByFilters === 1 ? "result is" : "results are"} hidden by your filters ({applied.filter((a) => !a.startsWith("Year") && a !== "Picked dates").join(" · ") || "search"}).{" "}
                    <button onClick={clearFilters} className="font-semibold text-sky-600 underline">Clear filters</button></>
                ) : "Nothing found."}
              </div>
            ) : (
              items.map((t, i) => (
                <TrendRow key={t.id} t={t} n={page * pageSize + i + 1} world={geo === WORLD} past={isPast} now={now} hours={hours} curve={curves[`${geo}|${hours}|${t.title}`]} selected={sel?.id === t.id} onSelect={() => select(t)} />
              ))
            )}
          </div>
          {/* Bottom pager: no scrolling anywhere */}
          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-800 bg-white px-4 py-2">
            <span className="text-xs text-slate-500">
              {total ? `${page * pageSize + 1}–${Math.min(total, page * pageSize + pageSize)} of ${total}` : "0 trends"}
              {" · "}page {Math.min(page + 1, pages)} of {pages}
            </span>
            <div className="flex gap-2">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page <= 0} className="h-8 rounded border border-slate-700 px-4 text-sm font-medium text-slate-200 hover:bg-slate-950 disabled:opacity-40">‹ Previous</button>
              <button onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} disabled={page >= pages - 1} className="h-8 rounded bg-sky-600 px-4 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40">Next ›</button>
            </div>
          </div>
        </section>

        {wide && <aside className="w-[24rem] shrink-0 overflow-hidden rounded-lg border border-slate-800 bg-white shadow-sm">{chatPane(sel)}</aside>}
      </div>

      {!wide && modal && sel && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={() => setModal(false)}>
          <div className="relative h-[85dvh] w-full max-w-md overflow-hidden rounded-lg bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => setModal(false)} aria-label="Close" className="absolute right-2 top-2 z-10 h-8 w-8 rounded-full bg-white/90 text-lg text-slate-500 shadow hover:bg-slate-950">×</button>
            {chatPane(sel)}
          </div>
        </div>
      )}

      {/* Status bar */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-0.5 border-t border-slate-800 bg-white px-4 py-1 text-xs text-slate-500" aria-live="polite">
        {!isPast && liveData && <span>Read from Google <strong className="font-semibold text-slate-300">{formatWhen(liveData.fetchedAt, now)}</strong> · updates itself</span>}
        {!isPast && liveData?.stale && <span className="rounded bg-amber-950 px-1.5 py-0.5 text-amber-300">Google could not be reached; showing the last list</span>}
        {applied.length > 0 && (
          <span className="rounded bg-amber-950 px-1.5 py-0.5 text-amber-300">
            Filtered: {applied.join(" · ")} <button onClick={reset} className="font-semibold underline">Show all</button>
          </span>
        )}
        <span className="ml-auto">Live from Google · nothing is stored</span>
      </div>
    </div>
  );
}

/** A column header you can click to sort by that column; click again to reverse. The arrow shows the current order. */
function Th({ label, a, b, ia, ib, sort, setSort, cls = "", title }: {
  label: string; a: string; b: string; ia: string; ib: string; sort: string; setSort: (s: string) => void; cls?: string; title?: string;
}) {
  const on = sort === a || sort === b;
  return (
    <button
      role="columnheader"
      aria-sort={on ? (sort === a ? (ia === "▲" ? "ascending" : "descending") : ib === "▲" ? "ascending" : "descending") : "none"}
      onClick={() => setSort(a === b ? a : sort === a ? b : a)}
      title={title ?? `Sort by ${label.toLowerCase()}`}
      className={`flex items-center gap-1 text-left hover:text-sky-600 ${on ? "text-sky-600" : ""} ${cls}`}
    >
      {label}{on && <span className="text-[10px]">{sort === a ? ia : ib}</span>}
    </button>
  );
}

function Skeleton({ n }: { n: number }) {
  return (
    <div aria-busy="true" aria-label="Loading trends">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} style={{ height: ROW_H }} className="flex items-center gap-4 border-b border-slate-800 px-4">
          <span className="h-3 w-5 animate-pulse rounded bg-slate-800" />
          <span className="h-3 animate-pulse rounded bg-slate-800" style={{ width: `${30 + ((i * 17) % 35)}%` }} />
          <span className="ml-auto h-3 w-16 animate-pulse rounded bg-slate-800" />
        </div>
      ))}
    </div>
  );
}

/** What a trend is doing right now, read from its real interest curve. Drives the colour and the label. */
const MOMENTUM = {
  peak: { label: "At peak", icon: "🔥", color: "#d83b01", hint: "searches are at their highest right now" },
  rising: { label: "Rising", icon: "▲", color: "#107c10", hint: "more people are searching than a little while ago" },
  steady: { label: "Steady", icon: "●", color: "#0078d4", hint: "holding a stable level" },
  fading: { label: "Fading", icon: "▼", color: "#8764b8", hint: "interest is dropping off" },
  ended: { label: "Ended", icon: "○", color: "#8a8886", hint: "no longer trending" },
} as const;

function momentum(values: number[], active: boolean) {
  const peak = Math.max(...values);
  if (!active || peak <= 0) return { ...MOMENTUM.ended, pct: 0 };
  const avg = (a: number[]) => a.reduce((x, y) => x + y, 0) / Math.max(1, a.length);
  const last = avg(values.slice(-4));
  const before = avg(values.slice(-12, -4));
  const pct = Math.round((last / peak) * 100);
  const m = pct >= 80 ? MOMENTUM.peak : last > before * 1.15 && pct > 25 ? MOMENTUM.rising : pct < 35 ? MOMENTUM.fading : MOMENTUM.steady;
  return { ...m, pct };
}

/** The real up-and-down line: Google's search-interest curve for one trend as a smooth area chart, coloured by what the
 * trend is doing now, with its peak ringed and a pulsing dot at "now". 0-100, scaled to that trend's own peak. */
function Spark({ values, t0, t1, color, live, size = "sm" }: { values: number[]; t0: string; t1: string; color: string; live: boolean; size?: "sm" | "lg" }) {
  const id = useId();
  const W = 100, H = 30, pad = 3;
  const lg = size === "lg";
  const n = values.length;
  const px = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const py = (v: number) => H - pad - (v / 100) * (H - pad * 2);
  const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(2)},${py(v).toFixed(2)}`).join(" ");
  const peak = values.reduce((b, v, i) => (v > values[b] ? i : b), 0);
  const peakAt = new Date(Date.parse(t0) + (n > 1 ? (peak / (n - 1)) * (Date.parse(t1) - Date.parse(t0)) : 0));
  const dot = lg ? 10 : 7;
  return (
    <span className={`relative block ${lg ? "h-16" : "h-7"} w-full`} title={`Search interest: peaked around ${peakAt.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" role="img" aria-label="Search interest over time">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={lg ? 0.45 : 0.5} />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <line x1="0" x2={W} y1={H - pad} y2={H - pad} stroke="#e1dfdd" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <path d={`${line} L${W},${H - pad} L0,${H - pad} Z`} fill={`url(#${id})`} />
        <path d={line} fill="none" stroke={color} strokeWidth={lg ? 2.5 : 2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
      {values[peak] > 0 && <span className="absolute rounded-full bg-white" style={{ width: dot, height: dot, left: `${px(peak)}%`, top: `${(py(values[peak]) / H) * 100}%`, transform: "translate(-50%,-50%)", boxShadow: `0 0 0 2px ${color}` }} />}
      {live && <span className="absolute animate-pulse rounded-full ring-2 ring-white" style={{ width: dot + 2, height: dot + 2, background: color, right: 0, top: `${(py(values[n - 1]) / H) * 100}%`, transform: "translate(50%,-50%)" }} />}
    </span>
  );
}

/** One row. The flexible space holds this trend's real search-interest curve, with a label saying what it is doing now. */
function TrendRow({ t, n, world, past, now, hours, curve, selected, onSelect }: {
  t: TrendItem; n: number; world: boolean; past: boolean; now: number; hours: number;
  curve: { values: number[]; t0: string; t1: string } | null | undefined; selected: boolean; onSelect: () => void;
}) {
  const active = t.endedAt === null;
  const span = hours * 3600_000;
  const ws = now - span;
  const started = Date.parse(t.startedAt);
  const ended = t.endedAt ? Math.min(Date.parse(t.endedAt), now) : now;
  const left = Math.max(0, ((Math.max(started, ws) - ws) / span) * 100);
  const width = Math.min(100 - left, Math.max(2, ((ended - Math.max(started, ws)) / span) * 100));
  const mo = curve ? momentum(curve.values, active) : null;
  return (
    <button
      role="row"
      onClick={onSelect}
      aria-selected={selected}
      style={{ height: ROW_H }}
      className={`relative grid w-full ${COLS} items-center gap-x-3 border-b border-slate-800 px-4 text-left hover:bg-slate-950 ${selected ? "bg-sky-950" : ""}`}
    >
      {selected && <span className="absolute inset-y-1.5 left-0 w-[3px] rounded-r bg-sky-600" aria-hidden />}
      <span className="text-sm font-semibold text-slate-400">{n}</span>
      <span className="truncate text-[15px] font-semibold text-slate-100">{t.title}</span>
      <span className="truncate text-[15px] text-slate-200">{t.traffic || "—"}</span>
      <span className="truncate text-[15px] font-semibold text-emerald-300">{t.increase ? `↑ ${t.increase}` : ""}</span>
      <span className="hidden min-w-0 items-center gap-2 xl:flex" aria-hidden={past}>
        {past ? <span className="text-slate-500">—</span> : curve && mo ? (
          <>
            <span className="min-w-0 flex-1"><Spark values={curve.values} t0={curve.t0} t1={curve.t1} color={mo.color} live={active} /></span>
            <span className="w-[4.1rem] shrink-0 text-[12px] font-bold leading-tight" style={{ color: mo.color }} title={`${mo.label}: ${mo.hint}${active ? ` (now at ${mo.pct}% of its peak)` : ""}`}>{mo.icon} {mo.label}</span>
          </>
        ) : curve === undefined ? (
          <span className="block h-7 w-full animate-pulse rounded bg-slate-950" aria-label="Loading search interest" />
        ) : (
          <span
            className="relative block h-3 w-full overflow-hidden rounded-full bg-slate-950 ring-1 ring-slate-800"
            title={`Started ${formatWhen(t.startedAt, now)} · ${active ? "still trending" : `ended ${formatWhen(t.endedAt, now)}`} · ${formatDuration(t.startedAt, t.endedAt, now)}`}
          >
            <span className={`absolute inset-y-0 ${active ? "bg-sky-600" : "bg-slate-600"} ${started < ws ? "" : "rounded-l-full"} ${active ? "" : "rounded-r-full"}`} style={{ left: `${left}%`, width: `${width}%` }} />
            {active && <span className="absolute right-0 top-0 h-3 w-3 animate-pulse rounded-full bg-emerald-400 ring-2 ring-white" />}
          </span>
        )}
      </span>
      <span className="hidden min-w-0 2xl:block">{world ? <span className="truncate text-[15px] text-slate-300">{t.countries.length === 1 ? regionName(t.countries[0]) : `${t.countries.length} countries`}</span> : <Chip text={t.category} />}</span>
      <span className="hidden truncate text-[13px] 2xl:block">
        {past ? <span className="text-slate-500">{formatWhen(t.startedAt, now).split(",")[0]}</span>
          : active ? <><span className="font-semibold text-emerald-300">● Active</span> <span className="text-slate-500">{formatDuration(t.startedAt, null, now)}</span></>
          : <><span className="font-semibold text-slate-400">○ Ended</span> <span className="text-slate-500">{formatDuration(t.startedAt, t.endedAt)}</span></>}
      </span>
    </button>
  );
}

const WARMUP = [
  "Reading the trend…", "Checking Google's numbers…", "Deciding what to look up…", "Warming up the search…", "Gathering sources…", "Putting it together…",
];

/** What Claude is doing right now: the Microsoft squares, the real steps (each web search), a timer. */
function Working({ steps, startedAt, deep }: { steps: string[]; startedAt: number; deep: boolean }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const secs = Math.floor((now - startedAt) / 1000);
  const current = steps.length ? steps[steps.length - 1] : WARMUP[Math.min(Math.floor(secs / 3), WARMUP.length - 1)];
  return (
    <div role="status" aria-live="polite" className="mr-4 overflow-hidden rounded-xl border border-sky-600/30 bg-sky-950 px-3 py-3">
      <div className="flex items-center gap-3">
        <MsLoader size={26} />
        <div className="min-w-0 flex-1">
          <p className="shimmer truncate text-[14px] font-semibold">{current}</p>
          <p className="text-[12px] text-slate-500">{secs}s · {deep ? "Deep research" : "Fast answer"}{steps.length ? ` · ${steps.length} ${steps.length === 1 ? "step" : "steps"} so far` : ""}</p>
        </div>
      </div>
      {steps.length > 1 && (
        <ul className="mt-2 space-y-0.5 border-t border-sky-600/20 pt-2 text-[12px] text-slate-500">
          {steps.slice(-4, -1).map((s, k) => <li key={k} className="truncate">✓ {s}</li>)}
        </ul>
      )}
      <div className="ms-bar mt-3 h-[3px] w-full overflow-hidden rounded-full bg-slate-800" aria-hidden />
    </div>
  );
}

function TrendChat({ t, past, now, curve, hours, messages, busy, deep, setDeep, onAsk, onStop, onClear }: {
  t: TrendItem | null; past: boolean; now: number; curve: { values: number[]; t0: string; t1: string } | null | undefined; hours: number; messages: Msg[]; busy: boolean; deep: boolean; setDeep: (d: boolean) => void;
  onAsk: (text: string) => void; onStop: () => void; onClear: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [photo, setPhoto] = useState<{ url: string; source: string } | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {  // Wikipedia answers at once; if it has none, Claude searches the web and the photo appears when found
    setPhoto(null);
    if (!t) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const run = async () => {
      try {
        const r = await api.trendPhoto(t.title);
        if (cancelled) return;
        if (r.url) setPhoto({ url: r.url, source: r.source ?? "" });
        else if (r.pending && ++tries < 30) timer = setTimeout(run, 2500);
      } catch { /* a missing photo never matters */ }
    };
    void run();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [t?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [messages, busy]);
  if (!t) return <div className="flex h-full items-center justify-center p-8 text-center text-[15px] text-slate-500">Select a trend, then ask Claude anything about it.</div>;
  const h = hue(t.title);
  const active = t.endedAt === null;
  const send = (text: string) => { if (!text.trim() || busy) return; onAsk(text.trim()); setDraft(""); };
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Trend header */}
      <div className="shrink-0 border-b border-slate-800 p-4">
        <div className="flex items-start gap-3">
          {photo ? (
            <img src={photo.url} alt="" title={`Photo: ${photo.source}`} referrerPolicy="no-referrer" onError={() => setPhoto(null)} className="h-16 w-16 shrink-0 rounded-lg object-cover" />
          ) : (
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg text-2xl font-semibold" style={{ background: `hsl(${h} 70% 90%)`, color: `hsl(${h} 45% 34%)` }}>{t.title.slice(0, 1).toUpperCase()}</div>
          )}
          <div className="min-w-0 flex-1">
            <h3 className="line-clamp-2 text-lg font-semibold leading-snug text-slate-100">{t.title}</h3>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
              <Chip text={t.category} />
              {past ? <span className="text-slate-500">{formatWhen(t.startedAt, now).split(",")[0]}</span>
                : active ? <span className="font-semibold text-emerald-300">● Active {formatDuration(t.startedAt, null, now)}</span> : <span className="font-semibold text-slate-400">○ Ended</span>}
            </p>
          </div>
        </div>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-[13px] text-slate-400">
          {t.traffic && <span><strong className="text-slate-200">{t.traffic}</strong> searches</span>}
          {t.increase && <span className="font-semibold text-emerald-300">↑ {t.increase}</span>}
          <span>since {formatWhen(t.startedAt, now)}</span>
        </p>
        {!past && curve !== undefined && (
          <div className="show-720 mt-3">
            {curve ? (() => {
              const m = momentum(curve.values, active);
              const peakI = curve.values.reduce((b, v, i) => (v > curve.values[b] ? i : b), 0);
              const peakAt = new Date(Date.parse(curve.t0) + (peakI / Math.max(1, curve.values.length - 1)) * (Date.parse(curve.t1) - Date.parse(curve.t0)));
              return (
                <>
                  <p className="mb-1 flex items-baseline justify-between text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    <span>Search interest · last {hours === 168 ? "7 days" : `${hours} hours`}</span>
                    <span className="text-[13px] normal-case tracking-normal" style={{ color: m.color }}>{m.icon} {m.label}</span>
                  </p>
                  <Spark values={curve.values} t0={curve.t0} t1={curve.t1} color={m.color} live={active} size="lg" />
                  <p className="mt-1 flex justify-between text-[11px] text-slate-500">
                    <span>{hours === 168 ? "7 d" : `${hours} h`} ago</span>
                    <span>peak {peakAt.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}{active ? ` · now at ${m.pct}% of peak` : ""}</span>
                    <span>now</span>
                  </p>
                </>
              );
            })() : <p className="text-[13px] text-slate-500">Google has no interest curve for this trend right now.</p>}
          </div>
        )}
      </div>

      {/* Conversation */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" aria-live="polite">
        {messages.length === 0 ? (
          <div>
            <p className="text-[15px] font-semibold text-slate-100">Ask Claude about this trend</p>
            <p className="mt-1 text-[13px] text-slate-500">Claude searches the web live, so answers are current. Type your question below.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((m, i) => m.role === "user" ? (
              <div key={i} className="ml-8 self-end rounded-2xl rounded-br-sm bg-sky-600 px-3 py-2 text-[14px] text-white">{m.text}</div>
            ) : !m.text && !m.error && busy && i === messages.length - 1 ? (
              <Working key={i} steps={m.steps ?? []} startedAt={m.startedAt ?? Date.now()} deep={deep} />
            ) : (
              <div key={i} className={`mr-4 rounded-2xl rounded-bl-sm border px-3 py-2 text-[14px] ${m.error ? "border-red-900/60 bg-red-950 text-red-200" : "border-slate-800 bg-white text-slate-200"}`}>
                {(m.steps?.length ?? 0) > 0 && !m.error && (
                  <details className="mb-1 text-[12px] text-slate-500">
                    <summary className="cursor-pointer select-none hover:text-slate-300">🔎 Researched in {m.steps!.length} {m.steps!.length === 1 ? "step" : "steps"}</summary>
                    <ul className="mt-1 space-y-0.5 pl-1">{m.steps!.map((s, k) => <li key={k} className="truncate">✓ {s}</li>)}</ul>
                  </details>
                )}
                {m.text ? (
                  <div className={`prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0 ${busy && i === messages.length - 1 && !m.error ? "caret" : ""}`}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: (p) => <a {...p} target="_blank" rel="noopener noreferrer" /> }}>{m.text}</ReactMarkdown>
                  </div>
                ) : null}
              </div>
            ))}
            <div ref={end} />
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t border-slate-800 p-3">
        {messages.length > 0 && !busy && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            <button onClick={onClear} className="rounded-full px-2.5 py-0.5 text-[13px] text-slate-500 hover:bg-slate-950">New chat</button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(draft); } }}
            rows={2}
            maxLength={1500}
            placeholder={`Ask about “${t.title.slice(0, 28)}${t.title.length > 28 ? "…" : ""}”`}
            aria-label="Ask Claude about this trend"
            className="min-h-0 flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-[14px] text-slate-100 outline-none focus:border-sky-500"
          />
          {busy ? (
            <button onClick={onStop} className="h-10 rounded-lg border border-slate-700 px-3 text-sm font-semibold text-slate-200 hover:bg-slate-950">■ Stop</button>
          ) : (
            <button onClick={() => send(draft)} disabled={!draft.trim()} className="h-10 rounded-lg bg-sky-600 px-4 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40">Send</button>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between text-[12px] text-slate-500">
          <span role="radiogroup" aria-label="Answer mode" className="inline-flex overflow-hidden rounded-full border border-slate-700">
            <button role="radio" aria-checked={!deep} onClick={() => setDeep(false)} className={`px-3 py-0.5 ${!deep ? "bg-sky-600 text-white" : "hover:bg-slate-950"}`}>⚡ Fast</button>
            <button role="radio" aria-checked={deep} onClick={() => setDeep(true)} className={`px-3 py-0.5 ${deep ? "bg-sky-600 text-white" : "hover:bg-slate-950"}`}>🔬 Deep</button>
          </span>
          <span>Enter to send · Shift+Enter for a new line</span>
        </div>
      </div>
    </div>
  );
}
