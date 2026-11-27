import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { api, type RadarProblem, type RadarReport, type RadarSearch, type RadarSource, type RadarStatus } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { useMedia } from "../hooks/useMedia";
import { ErrorState, Spinner } from "../components/common";
import { formatWhen } from "../lib/time";

// The page always fits the screen: nothing scrolls. Each problem is one "page" with the problem on the left and the solution on
// the right; the buttons at the bottom (or the arrow keys) move to the next problem. Long text is shortened, never scrolled.
const POLL_MS = 15_000; // the radar works by itself; the page just keeps itself up to date

const SEV: Record<string, { dot: string; chip: string; name: string }> = {
  critical: { dot: "bg-red-600", chip: "bg-red-950 text-red-200", name: "Critical" },
  high: { dot: "bg-red-500", chip: "bg-red-950 text-red-200", name: "High" },
  medium: { dot: "bg-amber-500", chip: "bg-amber-950 text-amber-300", name: "Medium" },
  low: { dot: "bg-sky-500", chip: "bg-sky-950 text-sky-300", name: "Low" },
};
const SLOT_LABEL: Record<string, string> = { morning: "Morning", afternoon: "Afternoon", night: "Night" };
const CONF: Record<string, string> = { high: "text-emerald-300", medium: "text-amber-300", low: "text-red-300" };

const pretty = (s?: string) => (s ?? "").replace(/_/g, " ");
const hourLabel = (h: number) => `${h % 12 === 0 ? 12 : h % 12}:00 ${h < 12 ? "AM" : "PM"}`;
const sev = (p: RadarProblem) => SEV[p.severity ?? ""] ?? { dot: "bg-slate-500", chip: "bg-slate-900 text-slate-300", name: p.severity || "Unrated" };

function host(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{children}</p>;
}

function Short({ title, items, ordered, max, clamp = "line-clamp-2" }: { title: string; items?: string[]; ordered?: boolean; max: number; clamp?: string }) {
  if (!items || items.length === 0) return null;
  const Tag = ordered ? "ol" : "ul";
  return (
    <div className="mt-2.5">
      <Eyebrow>{title}</Eyebrow>
      <Tag className={`mt-1 space-y-0.5 pl-5 text-[13px] leading-snug text-slate-300 ${ordered ? "list-decimal" : "list-disc"}`}>
        {items.slice(0, max).map((x, i) => <li key={i} className={clamp}>{x}</li>)}
      </Tag>
    </div>
  );
}

/** "Where Claude saw it": one compact card per web page. */
function Sources({ title, items, empty }: { title: string; items?: RadarSource[]; empty: string }) {
  const shown = (items ?? []).slice(0, 3);
  return (
    <div className="mt-auto pt-3">
      <Eyebrow>{title}</Eyebrow>
      {shown.length === 0 ? (
        <p className="mt-1 text-[12px] italic leading-snug text-slate-500">{empty}</p>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {shown.map((s, i) => (
            <li key={i}>
              <a href={s.url} target="_blank" rel="noreferrer noopener" className="block rounded border border-slate-800 bg-white px-2.5 py-1.5 hover:border-sky-500 hover:bg-slate-950">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[13px] font-semibold text-sky-400">{s.title || host(s.url)}</span>
                  <span aria-hidden className="shrink-0 text-[11px] text-slate-500">↗</span>
                </span>
                <span className="block truncate text-[11px] text-slate-500">
                  {[host(s.url), pretty(s.source_type), s.credibility ? `${s.credibility} credibility` : "", s.published_or_updated ?? ""].filter(Boolean).join(" · ")}
                </span>
                {s.key_finding && <span className="mt-0.5 block line-clamp-1 text-[12px] text-slate-400">{s.key_finding}</span>}
              </a>
            </li>
          ))}
        </ul>
      )}
      {(items?.length ?? 0) > 3 && <p className="mt-1 text-[11px] text-slate-500">+ {(items?.length ?? 0) - 3} more in the email</p>}
    </div>
  );
}

function Page({ p, half }: { p: RadarProblem; half: 0 | 1 | null }) {
  const a = p.recommended_action;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${sev(p).chip}`}>{sev(p).name}</span>
          {p.domain && <span className="rounded bg-sky-950 px-1.5 py-0.5 text-[11px] font-semibold text-sky-300">{p.domain}</span>}
          {p.technology && <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-300">{p.technology}</span>}
          {p.problem_type && <span className="text-[12px] text-slate-500">{pretty(p.problem_type)}</span>}
          {p.confidence && <span className={`text-[12px] font-semibold ${CONF[p.confidence] ?? "text-slate-400"}`}>{p.confidence} confidence</span>}
          {p.evidence_strength && <span className="text-[12px] text-slate-500">· {p.evidence_strength} evidence</span>}
        </div>
        <h2 className="mt-1.5 line-clamp-2 text-lg font-semibold leading-snug text-slate-100">{p.title}</h2>
      </div>

      <div className={`mt-3 grid min-h-0 flex-1 grid-rows-1 gap-3 ${half === null ? "grid-cols-2" : "grid-cols-1"}`}>
        {(half === null || half === 0) && <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-red-900/40 bg-red-950/20 p-3.5" aria-label="The problem">
          <h3 className="text-[13px] font-bold uppercase tracking-wide text-red-300">⚠ The problem</h3>
          {p.people_asked && <p className="mt-1.5 line-clamp-2 rounded border border-red-900/40 bg-white px-2.5 py-1.5 text-[13px] italic leading-snug text-slate-300">“{p.people_asked}”</p>}
          <p className="mt-1.5 line-clamp-4 text-[14px] leading-relaxed text-slate-200">{p.problem_summary || p.why_important_today}</p>
          {p.root_cause && <p className="mt-1.5 line-clamp-2 text-[13px] text-slate-300"><strong className="text-slate-100">Cause: </strong>{p.root_cause}</p>}
          {p.affected_versions_or_services && <p className="mt-1 line-clamp-1 text-[13px] text-slate-300"><strong className="text-slate-100">Affected: </strong>{p.affected_versions_or_services}</p>}
          <Short title="Symptoms" items={p.symptoms} max={3} clamp="line-clamp-1" />
          <Sources title="Where Claude saw this problem" items={p.problem_sources ?? p.sources} empty="No page was recorded." />
        </section>}

        {(half === null || half === 1) && <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-emerald-900/40 bg-emerald-950/20 p-3.5" aria-label="The solution">
          <h3 className="text-[13px] font-bold uppercase tracking-wide text-emerald-300">✔ The solution</h3>
          {p.community_answer && <p className="mt-1.5 line-clamp-3 rounded border border-emerald-900/40 bg-white px-2.5 py-1.5 text-[13px] leading-snug text-slate-300"><strong className="text-emerald-300">What worked: </strong>{p.community_answer}</p>}
          {a?.summary ? <p className="mt-1.5 line-clamp-3 text-[14px] leading-relaxed text-slate-200">{a.summary}</p> : <p className="mt-1.5 text-[13px] italic text-slate-500">No fix was found yet.</p>}
          <Short title="Steps" items={a?.steps} ordered max={4} />
          {a?.code_or_config && <pre className="mt-2 max-h-24 overflow-hidden rounded bg-slate-950 p-2 text-[12px] leading-snug text-slate-200">{a.code_or_config}</pre>}
          <Short title="Risks" items={a?.risks} max={2} clamp="line-clamp-1" />
          <Sources title="Where Claude found this fix" items={p.solution_sources} empty="No page gave this fix: it is Claude's own suggestion. Test it before production use." />
        </section>}
      </div>
    </div>
  );
}

export function RadarPage() {
  const overview = useAsync(() => api.radar(), []);
  const [key, setKey] = useState<string | null>(null);
  const [picked, setPicked] = useState<RadarReport | null>(null);
  const [index, setIndex] = useState(0);
  const [half, setHalf] = useState<0 | 1>(0); // narrow screens: 0 = the problem, 1 = its solution
  const wide = useMedia("(min-width: 1024px)");
  const [err, setErr] = useState<string | null>(null);
  const status: RadarStatus | undefined = overview.data?.status;

  // ---- search: a person types a problem or picks a domain; Claude researches it now and the result opens as its own page set
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const [searchId, setSearchId] = useState<string | null>(null);
  const [search, setSearch] = useState<RadarSearch | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const searching = searchId !== null && search?.status !== "done" && search?.status !== "error";

  const startSearch = async (e?: FormEvent) => {
    e?.preventDefault();
    if (q.trim().length < 3 || searching) return;
    setSearchErr(null);
    setSearch(null);
    try { setSearchId((await api.radarSearch(q.trim(), domain)).id); }
    catch (er) { setSearchErr(er instanceof Error ? er.message : "Could not start the search."); }
  };
  useEffect(() => {
    if (!searchId) return;
    let live = true;
    const tick = async () => {
      try {
        const s = await api.radarSearchState(searchId);
        if (!live) return;
        setSearch(s);
        if (s.status === "running") timer = setTimeout(tick, 4000);
      } catch (er) { if (live) { setSearchErr(er instanceof Error ? er.message : "The search was lost."); setSearchId(null); } }
    };
    let timer = setTimeout(tick, 1500);
    return () => { live = false; clearTimeout(timer); };
  }, [searchId]);
  const [showSearch, setShowSearch] = useState(false);
  useEffect(() => { if (search?.status === "done") setShowSearch(true); }, [search?.status]);

  useEffect(() => {
    const t = setInterval(() => overview.reload(), POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const latestKey = overview.data?.report?.key ?? null;
  useEffect(() => {
    if (!key || key === latestKey) { setPicked(null); return; }
    let live = true;
    api.radarReport(key).then((r) => live && setPicked(r)).catch((e) => live && setErr(e instanceof Error ? e.message : "Could not open that report."));
    return () => { live = false; };
  }, [key, latestKey]);

  const report = showSearch && search?.report ? search.report : picked ?? overview.data?.report ?? null;
  const problems = report?.problems ?? [];
  useEffect(() => { setIndex(0); setHalf(0); }, [report?.key, search?.report]);

  const last = problems.length - 1;
  const atStart = index === 0 && (wide || half === 0);
  const atEnd = index >= last && (wide || half === 1);
  const next = () => {
    if (!wide && half === 0) setHalf(1);
    else if (index < last) { setIndex(index + 1); setHalf(0); }
  };
  const prev = () => {
    if (!wide && half === 1) setHalf(0);
    else if (index > 0) { setIndex(index - 1); setHalf(wide ? 0 : 1); }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) return;
      if (e.key === "ArrowRight") document.getElementById("radar-next")?.click();
      if (e.key === "ArrowLeft") document.getElementById("radar-prev")?.click();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const today = status?.currentKey.slice(0, 10);
  const todays = useMemo(() => {
    const have = new Map((status?.keys ?? []).filter((k) => k.date === today).map((k) => [k.slot, k.key]));
    return (status?.schedule ?? []).map((s) => ({ ...s, key: have.get(s.slot) ?? null }));
  }, [status, today]);

  if (overview.error && !overview.data) return <div className="p-4"><ErrorState message={overview.error} onRetry={overview.reload} /></div>;
  if (!overview.data || !status) return <div className="p-4"><Spinner label="Loading the radar…" /></div>;

  const current = problems[index];
  const mail = status.email;
  const times = status.schedule.map((s) => hourLabel(s.hour)).join(", ");
  const emailNote = !mail.on
    ? "Email is off"
    : report?.emailed_at ? `Emailed to ${mail.to.length} people ${formatWhen(report.emailed_at)}` : report?.email_error ? "Email not sent yet, will retry" : `Email to ${mail.to.length} people after each run`;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-slate-950/30">
      <header className="shrink-0 border-b border-slate-800 bg-white px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight text-slate-100">Problem Radar</h1>
            <p className="truncate text-[12px] text-slate-500" title={status.domains.join(", ")}>Everyday problems and their fixes, from Reddit, X, Stack Overflow, Hacker News, Medium, GitHub and official docs · {status.domains.length} domains</p>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <div className="flex overflow-hidden rounded border border-slate-700" role="tablist" aria-label="Today's reports">
              {todays.map((t) => {
                const active = report?.key === t.key;
                return (
                  <button
                    key={t.slot} role="tab" aria-selected={active} disabled={!t.key} onClick={() => { if (t.key) { setKey(t.key); setShowSearch(false); } }}
                    title={t.key ? `${SLOT_LABEL[t.slot]} report` : `Runs at ${hourLabel(t.hour)}`}
                    className={`px-3 py-1 text-left text-[12px] leading-tight ${active ? "bg-sky-600 text-white" : "text-slate-200 hover:bg-slate-950"} disabled:cursor-not-allowed disabled:opacity-45`}
                  >
                    <span className="block font-semibold">{SLOT_LABEL[t.slot]}</span>
                    <span className="block text-[10px] opacity-80">{t.key ? hourLabel(t.hour) : `at ${hourLabel(t.hour)}`}</span>
                  </button>
                );
              })}
            </div>
            {status.keys.length > 0 && (
              <select value={report?.key ?? ""} onChange={(e) => { setKey(e.target.value); setShowSearch(false); }} aria-label="Earlier reports" className="h-9 rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100">
                {status.keys.map((k) => <option key={k.key} value={k.key}>{k.date}{k.slot ? ` · ${SLOT_LABEL[k.slot]}` : ""}</option>)}
              </select>
            )}
          </div>
        </div>
        <form onSubmit={startSearch} className="mt-2 flex flex-wrap items-center gap-2" role="search">
          <input
            value={q} onChange={(e) => setQ(e.target.value)} maxLength={200} aria-label="Search for a problem"
            placeholder="Search a problem in your domain, e.g. Kubernetes pods CrashLoopBackOff, PostgreSQL slow queries, React hydration error"
            className="h-9 min-w-[16rem] flex-1 rounded border border-slate-700 bg-slate-900 px-3 text-sm text-slate-100 outline-none focus:border-sky-500"
          />
          <select value={domain} onChange={(e) => setDomain(e.target.value)} aria-label="Domain" className="h-9 max-w-[14rem] rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100">
            <option value="">Any domain</option>
            {status.domains.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <button type="submit" disabled={q.trim().length < 3 || searching} className="h-9 rounded bg-sky-600 px-4 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50">{searching ? "Searching…" : "Search"}</button>
          {search?.status === "done" && !showSearch && <button type="button" onClick={() => setShowSearch(true)} className="h-9 rounded border border-slate-700 px-3 text-sm text-slate-200 hover:bg-slate-950">Show results for “{search.query}”</button>}
        </form>
        <p className="mt-1 flex flex-wrap gap-x-4 text-[12px] text-slate-500">
          <span><span className="font-semibold text-emerald-300">●</span> Automatic: {times} ({status.timezone}). Next: {status.nextRun}</span>
          <span className={mail.on && report?.email_error && !report.emailed_at ? "text-amber-300" : ""}>✉ {emailNote}</span>
        </p>
      </header>

      {searching && (
        <p role="status" className="flex shrink-0 items-center gap-2 bg-sky-950 px-4 py-1 text-[13px] text-sky-300">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-300/40 border-t-sky-600" />
          Claude is searching the web for “{q.trim()}”{domain ? ` in ${domain}` : ""}. The results open here by themselves in a few minutes.
        </p>
      )}
      {(searchErr || search?.status === "error") && <div className="shrink-0 px-4 pt-2"><ErrorState message={searchErr || `The search failed: ${search?.error}`} /></div>}
      {status.running && (
        <p role="status" className="flex shrink-0 items-center gap-2 bg-sky-950 px-4 py-1 text-[13px] text-sky-300">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-300/40 border-t-sky-600" />
          Claude is searching the web right now. The new report appears here by itself in a few minutes.
        </p>
      )}
      {(err || (status.error && !status.running)) && <div className="shrink-0 px-4 pt-2"><ErrorState message={err || `The last run failed: ${status.error}. It will try again by itself.`} /></div>}
      {report?.email_error && !report.emailed_at && <p className="line-clamp-1 shrink-0 bg-amber-950 px-4 py-1 text-[12px] text-amber-300">The email could not be sent: {report.email_error}</p>}

      {!report ? (
        <div className="m-auto max-w-md px-4 text-center text-[14px] text-slate-500">
          <p className="text-[15px] font-semibold text-slate-200">The first report is on its way</p>
          <p className="mt-1">Claude researches by itself every morning, afternoon and night. Nothing to press. Next: {status.nextRun}.</p>
        </div>
      ) : (
        <>
          <div className="shrink-0 px-4 pt-2.5">
            <p className="text-[12px] text-slate-500">{showSearch && search?.report ? <><strong className="text-sky-300">Search results</strong> for “{search.query}”{search.domain ? ` in ${search.domain}` : ""} · <button onClick={() => setShowSearch(false)} className="text-sky-400 underline">Back to the daily reports</button></> : <>{SLOT_LABEL[report.slot] ?? ""} report · {report.report_date}</>}{report.generated_at ? ` · made ${formatWhen(report.generated_at)}` : ""}</p>
            {report.daily_summary && <p className="line-clamp-2 text-[13px] leading-snug text-slate-300">{report.daily_summary}</p>}
          </div>
          <main className="min-h-0 flex-1 px-4 py-2.5">
            {current ? <Page p={current} half={wide ? null : half} /> : (
              <p className="mx-auto mt-10 max-w-md text-center text-[13px] text-slate-500">No meaningful problems were found in this run. The summary above explains the research gaps.</p>
            )}
          </main>
          {problems.length > 0 && (
            <footer className="flex shrink-0 items-center gap-3 border-t border-slate-800 bg-white px-4 py-2">
              <button id="radar-prev" onClick={prev} disabled={atStart} className="h-9 rounded border border-slate-700 px-4 text-sm font-semibold text-slate-200 hover:bg-slate-950 disabled:opacity-40">← Previous</button>
              <div className="flex min-w-0 flex-1 items-center justify-center gap-1.5" role="group" aria-label="Problems">
                {problems.map((p, i) => (
                  <button
                    key={p.rank} onClick={() => { setIndex(i); setHalf(0); }} aria-label={`Problem ${i + 1}: ${p.title}`} aria-current={i === index} title={p.title}
                    className={`flex h-7 min-w-7 items-center justify-center rounded-full px-1.5 text-[12px] font-semibold text-white ${sev(p).dot} ${i === index ? "ring-2 ring-sky-500 ring-offset-2" : "opacity-60 hover:opacity-100"}`}
                  >{i + 1}</button>
                ))}
                <span className="ml-2 text-[12px] text-slate-500">{index + 1} of {problems.length}{wide ? "" : half === 0 ? " · the problem" : " · the solution"}</span>
              </div>
              <button id="radar-next" onClick={next} disabled={atEnd} className="h-9 rounded bg-sky-600 px-5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40">Next →</button>
            </footer>
          )}
        </>
      )}
    </div>
  );
}
