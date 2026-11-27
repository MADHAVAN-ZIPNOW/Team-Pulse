import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, DEFAULT_ORG, type MeetingAction, type MeetingFull, type MeetingLine, type MeetingSummary, type MeetingsStatus, type TeamsMeeting } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { useLiveMic, type MicLang } from "../hooks/useLiveMic";
import { ErrorState, Spinner } from "../components/common";
import { formatWhen } from "../lib/time";

const hue = (s: string) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const WAKE = /\b(claude|claud|clod)\b|\bcloud\b\s*[,:]|கிளாட்/i; // the same idea as the server: only used to highlight lines
const STATUS: Record<MeetingAction["status"], { icon: string; label: string; cls: string }> = {
  working: { icon: "⏳", label: "Creating…", cls: "border-sky-600/30 bg-sky-950" },
  created: { icon: "✅", label: "Created", cls: "border-emerald-400/40 bg-emerald-950" },
  failed: { icon: "⚠️", label: "Problem", cls: "border-red-900/60 bg-red-950" },
  undone: { icon: "↩️", label: "Undone", cls: "border-slate-800 bg-slate-950" },
  skipped: { icon: "⏸", label: "Skipped", cls: "border-amber-400/40 bg-amber-950" },
};
const lastProject = () => { try { return localStorage.getItem("pra_meeting_project") ?? ""; } catch { return ""; } };

export function MeetingsPage() {
  const status = useAsync(() => api.meetingsStatus(), []);
  const list = useAsync(() => api.meetings(), [], status.data?.enabled === true);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"meetings" | "calendar">("meetings");
  const fileRef = useRef<HTMLInputElement>(null);

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setCreating(true);
    setError(null);
    try {
      const m = await api.importFile(file, DEFAULT_ORG, lastProject());
      list.reload();
      setOpenId(m.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not import that file."); }
    finally { setCreating(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  useEffect(() => { if (!openId && list.data?.length) setOpenId(list.data[0].id); }, [openId, list.data]);  // pick up where you left off

  const create = async () => {
    setCreating(true);
    setError(null);
    try {
      const m = await api.newMeeting(`Meeting ${new Date().toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`, DEFAULT_ORG, lastProject());
      list.reload();
      setOpenId(m.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start a meeting."); }
    finally { setCreating(false); }
  };

  if (status.error) return <div className="p-4"><ErrorState message={status.error} onRetry={status.reload} /></div>;
  if (!status.data) return <div className="p-6"><Spinner label="Loading…" /></div>;
  if (!status.data.enabled) return <Off />;

  return (
    <div className="flex h-full min-h-0 gap-3 bg-slate-950 p-3">
      <aside className="flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border border-slate-800 bg-white shadow-sm">
        <div className="border-b border-slate-800 p-3">
          <h2 className="mb-2 text-base font-semibold text-slate-100">Meetings</h2>
          <button onClick={create} disabled={creating} className="h-9 w-full rounded bg-sky-600 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-60">＋ New meeting</button>
          <button onClick={() => fileRef.current?.click()} disabled={creating} title="A transcript (.vtt, .txt) or a recording (.mp4, .m4a, .mp3)" className="mt-2 h-8 w-full rounded border border-slate-700 text-[13px] font-medium text-slate-200 hover:bg-slate-950 disabled:opacity-60">⬆ Import a transcript or recording</button>
          <div role="tablist" className="mt-2 grid grid-cols-2 overflow-hidden rounded border border-slate-700 text-[13px]">
            <button role="tab" aria-selected={tab === "meetings"} onClick={() => setTab("meetings")} className={`py-1.5 ${tab === "meetings" ? "bg-sky-600 font-semibold text-white" : "text-slate-300 hover:bg-slate-950"}`}>My meetings</button>
            <button role="tab" aria-selected={tab === "calendar"} onClick={() => setTab("calendar")} className={`py-1.5 ${tab === "calendar" ? "bg-sky-600 font-semibold text-white" : "text-slate-300 hover:bg-slate-950"}`}>Teams calendar</button>
          </div>
          <input ref={fileRef} type="file" accept=".vtt,.srt,.txt,.md,.mp4,.m4a,.mp3,.wav,.webm,.ogg,.aac,.flac,.mkv,.mov" className="hidden" onChange={(e) => void importFile(e.target.files?.[0])} />
        </div>
        {tab === "calendar" ? (
          <TeamsCalendar status={status.data} onOpen={(id) => { setTab("meetings"); setOpenId(id); }} onStarted={(id) => { setTab("meetings"); list.reload(); setOpenId(id); }} />
        ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {list.error && <div className="p-3"><ErrorState message={list.error} onRetry={list.reload} /></div>}
          {!list.error && !list.data && <div className="p-4"><Spinner label="Loading…" /></div>}
          {list.data?.length === 0 && <p className="p-4 text-[13px] text-slate-500">No meetings yet. Press “New meeting”.</p>}
          {list.data?.map((m) => <MeetingItem key={m.id} m={m} active={m.id === openId} onOpen={() => setOpenId(m.id)} />)}
        </div>
        )}
        <p className="border-t border-slate-800 p-3 text-[12px] leading-snug text-slate-500">Meetings are saved encrypted on the server. Only you can open them. They are deleted after 90 days.</p>
      </aside>

      <section className="min-w-0 flex-1 overflow-hidden rounded-lg border border-slate-800 bg-white shadow-sm">
        {error && <div className="p-3"><ErrorState message={error} /></div>}
        {openId ? (
          <MeetingView key={openId} id={openId} onChanged={list.reload} onDeleted={() => { setOpenId(null); list.reload(); }} />
        ) : (
          <Welcome onStart={create} busy={creating} />
        )}
      </section>
    </div>
  );
}

function Off() {
  return (
    <div className="flex h-full items-center justify-center bg-slate-950 p-6">
      <div className="max-w-lg rounded-lg border border-slate-800 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-100">The meeting agent is switched off</h2>
        <p className="mt-2 text-[15px] text-slate-400">It can create work items in your Azure DevOps by itself, so it stays off until you turn it on. To turn it on, add this line to the server&apos;s <code>.env</code> file and restart the backend:</p>
        <pre className="mt-3 rounded bg-slate-950 p-3 text-sm text-slate-200">MEETING_AGENT_ENABLED=true</pre>
        <p className="mt-3 text-[13px] text-slate-500">Try it on a test project first.</p>
      </div>
    </div>
  );
}

function MeetingItem({ m, active, onOpen }: { m: MeetingSummary; active: boolean; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className={`block w-full border-b border-slate-800 px-3 py-2.5 text-left hover:bg-slate-950 ${active ? "bg-sky-950" : ""}`}>
      <p className="truncate text-[14px] font-semibold text-slate-100">{m.title}</p>
      <p className="mt-0.5 flex items-center gap-2 text-[12px] text-slate-500">
        {m.ended_at ? <span>○ Ended</span> : <span className="font-semibold text-emerald-300">● Live</span>}
        <span>{m.lines} lines</span>
        {m.auto && <span className="rounded bg-sky-950 px-1.5 font-semibold text-sky-600" title="Processed automatically">Auto</span>}
        {m.actions > 0 && <span>· {m.actions} action{m.actions === 1 ? "" : "s"}</span>}
        <span className="ml-auto">{formatWhen(m.created_at)}</span>
      </p>
    </button>
  );
}

function Welcome({ onStart, busy }: { onStart: () => void; busy: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 overflow-y-auto p-8 text-center">
      <div className="text-5xl" aria-hidden>🎙️</div>
      <h2 className="text-xl font-semibold text-slate-100">Say it in the meeting, Claude does it</h2>
      <p className="max-w-lg text-[15px] text-slate-400">Claude follows the conversation. When someone says <strong>“Claude, create a PBI for this and add subtasks”</strong>, it creates the work items in Azure DevOps and shows them here. Every action has an Undo.</p>
      <button onClick={onStart} disabled={busy} className="h-10 rounded bg-sky-600 px-5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-60">＋ Start a meeting</button>
      <TeamsStatus />
    </div>
  );
}

/** Honest status of the Teams connection: what works today and what still needs to be set up. */
function TeamsStatus() {
  return (
    <details className="w-full max-w-lg rounded-lg border border-slate-800 bg-slate-950 p-3 text-left text-[13px] text-slate-400">
      <summary className="cursor-pointer select-none font-semibold text-slate-200">Microsoft Teams: not connected yet (what is still needed)</summary>
      <p className="mt-2">Hearing a Teams meeting live needs a bot that joins the meeting. It is a separate service and is not part of this app yet:</p>
      <ol className="mt-1 list-decimal space-y-0.5 pl-5">
        <li>An <strong>Azure Bot</strong> registration with Teams calling turned on.</li>
        <li>A <strong>Windows (.NET) service</strong> on a server reachable from the internet, using Microsoft&apos;s real-time media library.</li>
        <li><strong>Admin consent</strong> in your Microsoft 365 tenant for the calling permissions.</li>
        <li>An <strong>Azure AI Speech</strong> key for Tamil and English speech-to-text.</li>
      </ol>
      <p className="mt-2">The bot sends each spoken line to this page. Teams shows everyone that the call is being recorded. Until then you can try it with your microphone (<strong>Listen</strong>) or by typing lines.</p>
    </details>
  );
}

function MeetingView({ id, onChanged, onDeleted }: { id: string; onChanged: () => void; onDeleted: () => void }) {
  const [m, setM] = useState<MeetingFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speaker, setSpeaker] = useState("Me");
  const [draft, setDraft] = useState("");
  const [micLang, setMicLang] = useState<MicLang>("en-IN");
  const [undoing, setUndoing] = useState<string | null>(null);
  const [target, setTarget] = useState<"auto" | "en" | "ta">("auto");
  const projects = useAsync(() => api.projects(DEFAULT_ORG), []);
  const endRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoError, setVideoError] = useState(false);
  const seek = (seconds: number) => { const v = videoRef.current; if (v) { v.currentTime = seconds; void v.play().catch(() => undefined); } };

  const upsertAction = useCallback((a: MeetingAction) => setM((c) => {
    if (!c) return c;
    const i = c.actions.findIndex((x) => x.id === a.id);
    return { ...c, actions: i >= 0 ? c.actions.map((x) => (x.id === a.id ? a : x)) : [...c.actions, a] };
  }), []);
  const addLine = useCallback((l: MeetingLine) => setM((c) => (c ? { ...c, lines: c.lines.some((x) => x.id === l.id) ? c.lines.map((x) => (x.id === l.id ? l : x)) : [...c.lines, l] } : c)), []);

  const [done, setDone] = useState<string | null>(null);
  const [excelUrl, setExcelUrl] = useState("");
  useEffect(() => { if (done) api.excelLink().then((l) => setExcelUrl(l.url || "")).catch(() => undefined); }, [done]);
  const seenKey = `pra_done_${id}`;
  const dismiss = () => { setDone(null); try { if (m?.result) localStorage.setItem(seenKey, m.result); } catch { /* storage may be blocked */ } };
  useEffect(() => {  // the finished message is kept on the meeting, so it shows even if this page was closed or reconnecting when the job ended
    if (!m?.result || m.stage) return;
    let seen = "";
    try { seen = localStorage.getItem(seenKey) ?? ""; } catch { /* ignore */ }
    if (seen !== m.result) setDone(m.result);
  }, [m?.result, m?.stage, seenKey]);
  useEffect(() => { api.meeting(id).then(setM).catch((e) => setError(e instanceof Error ? e.message : "Could not open the meeting.")); }, [id]);
  useEffect(() => {  // lines and Claude's actions arrive live
    const es = new EventSource(`/api/meetings/${id}/stream`);
    es.onopen = () => { api.meeting(id).then(setM).catch(() => undefined); };  // catch up on anything that happened before the live feed connected
    es.onmessage = (ev) => {
      try {
        const e = JSON.parse(ev.data) as { type: string; line?: MeetingLine; lines?: MeetingLine[]; action?: MeetingAction; ended_at?: string; stage?: string; error?: boolean; message?: string };
        if (e.type === "done") setM((c) => (c ? { ...c, stage: "", result: e.message ?? "All done." } : c));
        if (e.type === "line" && e.line) addLine(e.line);
        if (e.type === "lines" && e.lines) setM((c) => (c ? { ...c, lines: e.lines ?? c.lines } : c));
        if (e.type === "stage") setM((c) => (c ? { ...c, stage: e.stage ?? "", stage_error: !!e.error } : c));
        if (e.type === "action" && e.action) upsertAction(e.action);
        if (e.type === "video") setM((c) => (c ? { ...c, hasVideo: true } : c));
        if (e.type === "end") setM((c) => (c ? { ...c, ended_at: e.ended_at ?? new Date().toISOString() } : c));
      } catch { /* ignore a broken event */ }
    };
    return () => es.close();
  }, [id, addLine, upsertAction]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [m?.lines.length]);

  const send = useCallback(async (who: string, text: string) => {
    if (!text.trim()) return;
    try { addLine(await api.addMeetingLine(id, who, text.trim())); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not add the line."); }
  }, [id, addLine, onChanged]);

  const mic = useLiveMic(micLang, (text) => void send(speaker || "Me", text));
  if (error && !m) return <div className="p-4"><ErrorState message={error} /></div>;
  if (!m) return <div className="p-6"><Spinner label="Opening meeting…" /></div>;
  const ended = !!m.ended_at;

  const pickProject = async (p: string) => {
    setM({ ...m, project: p });
    try { localStorage.setItem("pra_meeting_project", p); } catch { /* storage may be blocked */ }
    try { await api.setMeetingProject(id, p); } catch (e) { setError(e instanceof Error ? e.message : "Could not set the project."); }
  };
  const finish = async () => { mic.stop(); try { await api.endMeeting(id); setM({ ...m, ended_at: new Date().toISOString() }); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : "Could not end the meeting."); } };
  const remove = async () => {
    if (!window.confirm("Delete this meeting and its transcript? The work items Claude created stay in Azure DevOps.")) return;
    mic.stop();
    try { await api.deleteMeeting(id); onDeleted(); } catch (e) { setError(e instanceof Error ? e.message : "Could not delete."); }
  };
  const undo = async (a: MeetingAction) => {
    if (!window.confirm(`Undo this? ${a.items.length} work item${a.items.length === 1 ? "" : "s"} will be moved to the recycle bin in Azure DevOps.`)) return;
    setUndoing(a.id);
    try { upsertAction(await api.undoMeetingAction(id, a.id)); } catch (e) { setError(e instanceof Error ? e.message : "Could not undo."); }
    finally { setUndoing(null); }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {done && (
        <DonePopup message={done} excelUrl={excelUrl} onClose={dismiss} />
      )}
      {/* Controls */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-800 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold text-slate-100">{m.title}</h2>
          <p className="text-[12px] text-slate-500">{ended ? `Ended ${formatWhen(m.ended_at)}` : <span className="font-semibold text-emerald-300">● Live</span>} · {m.organization}</p>
        </div>
        <label className="flex items-center gap-2 text-[13px] text-slate-400">
          Project
          <select value={m.project} onChange={(e) => void pickProject(e.target.value)} className="h-8 max-w-[14rem] rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100 outline-none focus:border-sky-500">
            <option value="">{projects.loading ? "Loading…" : "Choose a project"}</option>
            {m.project && !projects.data?.some((p) => p.name === m.project) && <option value={m.project}>{m.project}</option>}
            {projects.data?.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        <span role="radiogroup" aria-label="Translate to" className="inline-flex overflow-hidden rounded border border-slate-700 text-[13px]">
          {([["auto", "Auto"], ["en", "→ English"], ["ta", "→ தமிழ்"]] as const).map(([v, label]) => (
            <button key={v} role="radio" aria-checked={target === v} onClick={() => setTarget(v)} className={`px-2.5 py-1.5 ${target === v ? "bg-sky-600 font-semibold text-white" : "text-slate-300 hover:bg-slate-950"}`}>{label}</button>
          ))}
        </span>
        <button onClick={() => { setError(null); api.translateMeeting(id, target).catch((e) => setError(e instanceof Error ? e.message : "Could not start the translation.")); }} disabled={m.lines.length === 0 || !!m.stage} className="rounded border border-slate-700 px-3 py-1.5 text-[13px] text-slate-200 hover:bg-slate-950 disabled:opacity-40">🌐 Translate</button>
        <button onClick={() => { setError(null); api.fillExcelAgain(id).catch((e) => setError(e instanceof Error ? e.message : "Could not start.")); }} disabled={m.lines.length === 0 || !!m.stage} title="Claude reads the saved text again and updates your Excel. No download." className="rounded border border-slate-700 px-3 py-1.5 text-[13px] text-slate-200 hover:bg-slate-950 disabled:opacity-40">📊 Fill Excel again</button>
        <a href={`/api/meetings/${id}/export`} download className="rounded border border-slate-700 px-3 py-1.5 text-[13px] text-slate-300 hover:bg-slate-950">⬇ Export</a>
        {!ended && <button onClick={finish} className="rounded border border-slate-700 px-3 py-1.5 text-[13px] text-slate-300 hover:bg-slate-950">End meeting</button>}
        <button onClick={remove} className="rounded px-2 py-1.5 text-[13px] text-red-300 hover:bg-red-950">Delete</button>
      </div>
      {error && <div className="shrink-0 px-4 pt-2"><ErrorState message={error} /></div>}
      {m.stage && (
        <p role="status" className={`flex shrink-0 items-center gap-2 px-4 py-1.5 text-[13px] ${m.stage_error ? "bg-red-950 text-red-200" : "bg-sky-950 text-sky-300"}`}>
          {!m.stage_error && <span className="h-3 w-3 animate-spin rounded-full border-2 border-sky-300/40 border-t-sky-600" />}
          {m.stage}
        </p>
      )}
      {!m.project && !ended && <p className="shrink-0 bg-amber-950 px-4 py-1.5 text-[13px] text-amber-300">Choose a project so Claude knows where to create work items (or say the project name in the request).</p>}

      <div className="flex min-h-0 flex-1">
        {/* Live transcript */}
        <div className="flex min-w-0 flex-1 flex-col border-r border-slate-800">
          <p className="shrink-0 border-b border-slate-800 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{m.hasVideo && m.lines.length === 0 ? "Recording" : "What is being said"}</p>
          {m.hasVideo && (
            <div className="shrink-0 border-b border-slate-800 bg-black">
              {videoError ? (
                <p className="bg-slate-950 px-4 py-3 text-[13px] text-slate-400">The recording could not be opened here. Only the person who organized the meeting can watch it in Team Pulse. You can open it in Teams.</p>
              ) : (
                <video ref={videoRef} controls preload="metadata" src={`/api/meetings/${id}/video`} onError={() => setVideoError(true)} className="mx-auto max-h-60 w-full" />
              )}
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" aria-live="polite">
            {m.lines.length === 0 ? (m.hasVideo ? null : (
              <div className="mx-auto mt-8 max-w-md text-center text-[14px] text-slate-500">
                <p className="text-[15px] font-semibold text-slate-200">Nothing said yet</p>
                <p className="mt-1">Press <strong>🎙 Listen</strong> and talk, or type a line below. Try: <em>“Claude, create a PBI for the login page and add two subtasks.”</em></p>
                <div className="mt-5"><TeamsStatus /></div>
              </div>
            )) : (
              <ul className="space-y-1.5">{m.lines.map((l) => <Line key={l.id} l={l} onSeek={m.hasVideo && !videoError ? seek : undefined} />)}</ul>
            )}
            <div ref={endRef} />
          </div>
        </div>

        {/* What Claude did */}
        <div className="flex w-[22rem] shrink-0 flex-col">
          <p className="shrink-0 border-b border-slate-800 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">What Claude did</p>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {m.actions.length === 0 ? <p className="p-2 text-[13px] text-slate-500">Nothing yet. When someone asks Claude to create work items, they appear here with an Undo button.</p> : (
              <ul className="space-y-2">{m.actions.map((a) => <ActionCard key={a.id} a={a} onUndo={() => void undo(a)} busy={undoing === a.id} />)}</ul>
            )}
          </div>
          <AskPanel id={id} disabled={m.lines.length === 0} />
        </div>
      </div>

      {/* Add lines */}
      <div className="shrink-0 border-t border-slate-800 bg-white p-3">
        {mic.error && <p className="mb-2 text-[13px] text-red-300">{mic.error}</p>}
        {mic.listening && (
          <p className="mb-2 flex items-center gap-2 text-[13px] text-slate-400" role="status">
            <span className="mic-live h-2.5 w-2.5 shrink-0 rounded-full bg-red-600" />
            <span className="font-semibold text-red-300">Listening ({micLang === "ta-IN" ? "தமிழ்" : "English"})</span>
            <span className="truncate italic">{mic.interim}</span>
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <input value={speaker} onChange={(e) => setSpeaker(e.target.value)} maxLength={60} aria-label="Who is speaking" placeholder="Speaker" className="h-9 w-28 rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100 outline-none focus:border-sky-500" />
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { void send(speaker || "Me", draft); setDraft(""); } }}
            disabled={ended}
            maxLength={2000}
            placeholder={ended ? "This meeting has ended" : "Type what was said, then press Enter"}
            aria-label="Type a line"
            className="h-9 min-w-[10rem] flex-1 rounded border border-slate-700 bg-slate-900 px-3 text-sm text-slate-100 outline-none focus:border-sky-500 disabled:opacity-50"
          />
          <button onClick={() => { void send(speaker || "Me", draft); setDraft(""); }} disabled={ended || !draft.trim()} className="h-9 rounded bg-sky-600 px-4 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-40">Add</button>
          <select value={micLang} onChange={(e) => setMicLang(e.target.value as MicLang)} aria-label="Language you will speak" className="h-9 rounded border border-slate-700 bg-slate-900 px-2 text-sm text-slate-100">
            <option value="en-IN">Speak English</option>
            <option value="ta-IN">Speak தமிழ்</option>
          </select>
          {mic.supported ? (
            <button onClick={mic.listening ? mic.stop : mic.start} disabled={ended} className={`h-9 rounded px-4 text-sm font-semibold disabled:opacity-40 ${mic.listening ? "bg-red-600 text-white hover:bg-red-500" : "border border-slate-700 text-slate-200 hover:bg-slate-950"}`}>
              {mic.listening ? "■ Stop" : "🎙 Listen"}
            </button>
          ) : <span className="text-[12px] text-slate-500">Voice needs Chrome or Edge.</span>}
        </div>
        <p className="mt-2 text-[12px] text-slate-500">Claude creates work items <strong>as you</strong>, straight away, when it hears “Claude, …”. Use Undo if it got something wrong. Tell everyone in the meeting that it is being transcribed.</p>
      </div>
    </div>
  );
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function Line({ l, onSeek }: { l: MeetingLine; onSeek?: (seconds: number) => void }) {
  const h = hue(l.speaker);
  const addressed = WAKE.test(l.text);
  return (
    <li className={`rounded-lg px-3 py-1.5 ${addressed ? "border-l-4 border-sky-600 bg-sky-950" : "bg-white"}`}>
      <p className="flex items-center gap-2 text-[12px]">
        <span className="rounded-full px-2 py-0.5 font-semibold" style={{ background: `hsl(${h} 70% 92%)`, color: `hsl(${h} 55% 26%)` }}>{l.speaker}</span>
        <span className="text-slate-500">{clock(l.t)}</span>
        {onSeek && typeof l.offset === "number" && <button onClick={() => onSeek(l.offset as number)} title="Play the video from here" className="font-semibold text-sky-600 hover:underline">▶ {mmss(l.offset)}</button>}
        {addressed && <span className="font-semibold text-sky-600">to Claude</span>}
      </p>
      <p className="mt-0.5 text-[15px] leading-snug text-slate-100 [overflow-wrap:anywhere]">{l.text}</p>
      {l.translation && (
        <p className="mt-0.5 border-l-2 border-sky-600/40 pl-2 text-[14px] leading-snug text-slate-300 [overflow-wrap:anywhere]">
          <span className="mr-1 text-[11px] font-semibold uppercase tracking-wide text-sky-600">{l.to === "ta" ? "தமிழ்" : "English"}</span>{l.translation}
        </p>
      )}
    </li>
  );
}

function ActionCard({ a, onUndo, busy }: { a: MeetingAction; onUndo: () => void; busy: boolean }) {
  const s = STATUS[a.status];
  const canUndo = (a.status === "created" || a.status === "failed") && a.items.length > 0;
  return (
    <li className={`rounded-lg border p-3 ${s.cls}`}>
      <p className="flex items-center gap-2 text-[13px] font-semibold text-slate-100"><span aria-hidden>{s.icon}</span>{s.label}<span className="ml-auto text-[12px] font-normal text-slate-500">{clock(a.at)}</span></p>
      <p className="mt-1 text-[14px] text-slate-300">{a.summary}</p>
      {a.items.length > 0 && (
        <ul className="mt-2 space-y-1 text-[13px]">
          {a.items.map((i) => (
            <li key={i.id} className={i.parent ? "ml-5" : ""}>
              <a href={i.url} target="_blank" rel="noopener noreferrer" className={`hover:underline ${a.status === "undone" ? "text-slate-500 line-through" : "text-sky-600"}`}>
                <span className="text-slate-500">{i.parent ? "↳ " : ""}{i.type} #{i.id}</span> {i.title}
              </a>
            </li>
          ))}
        </ul>
      )}
      {canUndo && <button onClick={onUndo} disabled={busy} className="mt-2 rounded border border-slate-700 bg-white px-3 py-1 text-[13px] font-semibold text-slate-200 hover:bg-slate-950 disabled:opacity-50">{busy ? "Undoing…" : "↩ Undo"}</button>}
    </li>
  );
}

/** Your Teams calendar inside Team Pulse: what is on now, what is coming, and what has finished (ready for Claude). */
function TeamsCalendar({ status, onOpen, onStarted }: { status: MeetingsStatus; onOpen: (id: string) => void; onStarted: (id: string) => void }) {
  const cal = useAsync(() => api.teamsCalendar(), [], status.teams);
  const auto = useAsync(() => api.autoStatus(), [], status.teams);
  const projects = useAsync(() => api.projects(DEFAULT_ORG), [], status.teams);
  const [autoProject, setAutoProject] = useState(lastProject());
  const excel = useAsync(() => api.excelLink(), [], status.teams);
  const [excelUrl, setExcelUrl] = useState<string | null>(null);
  const [excelSaved, setExcelSaved] = useState(false);
  const saveExcel = async () => {
    setAutoError(null);
    try { await api.setExcelLink((excelUrl ?? excel.data?.url ?? "").trim()); excel.reload(); setExcelSaved(true); setTimeout(() => setExcelSaved(false), 2500); }
    catch (e) { setAutoError(e instanceof Error ? e.message : "Could not save the Excel link."); }
  };
  const [autoBusy, setAutoBusy] = useState(false);
  const [autoError, setAutoError] = useState<string | null>(null);
  const a = auto.data;

  const toggleAuto = async (on: boolean) => {
    setAutoBusy(true);
    setAutoError(null);
    try { await api.setAuto(on, DEFAULT_ORG, autoProject || a?.project || ""); auto.reload(); }
    catch (e) { setAutoError(e instanceof Error ? e.message : "Could not change that."); }
    finally { setAutoBusy(false); }
  };
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const start = async (t: TeamsMeeting) => {
    setBusy(t.id);
    setError(null);
    try { onStarted((await api.importTeams(t.joinUrl, t.subject, DEFAULT_ORG, lastProject(), t.start, t.end)).id); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not start."); }
    finally { setBusy(null); }
  };

  if (!status.teams) return <p className="min-h-0 flex-1 p-4 text-[13px] text-slate-400">Teams meetings need the “Sign in with Microsoft” sign-in. You are signed in another way, so use <strong>Import</strong> instead.</p>;
  const items = cal.data ?? [];
  const groups: [string, TeamsMeeting[]][] = [
    ["Now and coming up", items.filter((t) => t.state !== "finished").reverse()],
    ["Finished", items.filter((t) => t.state === "finished")],
  ];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="border-b border-slate-800 p-3">
        <label className="flex items-start gap-2.5">
          <input
            type="checkbox" role="switch" className="mt-1 h-4 w-4 shrink-0 accent-sky-600"
            checked={!!a?.enabled} disabled={autoBusy || !a?.available} onChange={(e) => void toggleAuto(e.target.checked)}
          />
          <span className="text-[13px] leading-snug text-slate-300">
            <strong className="text-slate-100">Process my finished meetings automatically</strong>
            <span className="mt-0.5 block text-[12px] text-slate-500">When a meeting you organized ends and Teams has its transcript or recording, Claude reads it and carries out the “Claude, …” requests as you, without you pressing anything. Each meeting is processed once, and every action has an Undo.</span>
          </span>
        </label>
        <label className="mt-2 flex items-center gap-2 text-[12px] text-slate-500">
          Create work items in
          <select
            value={autoProject || a?.project || ""}
            onChange={(e) => {
              const p = e.target.value;
              setAutoProject(p);
              try { localStorage.setItem("pra_meeting_project", p); } catch { /* storage may be blocked */ }
              if (a?.enabled) api.setAuto(true, DEFAULT_ORG, p).then(() => auto.reload()).catch((err) => setAutoError(err instanceof Error ? err.message : "Could not change the project."));
            }}
            className="h-7 min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-1.5 text-[12px] text-slate-100"
          >
            <option value="">Choose a project</option>
            {projects.data?.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        <div className="mt-2 text-[12px] text-slate-500">
          <label className="block">
            Excel Discussion Log (OneDrive or SharePoint share link)
            <span className="mt-1 flex gap-1.5">
              <input
                value={excelUrl ?? excel.data?.url ?? ""}
                onChange={(e) => setExcelUrl(e.target.value)}
                placeholder="https://…sharepoint.com/:x:/…"
                className="h-7 min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-1.5 text-[12px] text-slate-100"
              />
              <button onClick={saveExcel} className="h-7 rounded border border-slate-700 px-2.5 text-[12px] font-semibold text-slate-200 hover:bg-slate-950">{excelSaved ? "Saved ✓" : "Save"}</button>
            </span>
          </label>
          <span className="mt-1 block">After each meeting you organize, Claude listens to the recording, then adds new topics to the sheet “Discussion Log” and updates topics that are already there.</span>
        </div>
        {autoError && <div className="mt-2"><ErrorState message={autoError} /></div>}
        {a && !a.available && <p className="mt-2 text-[12px] text-amber-300">Not available on this server: it needs the Microsoft sign-in and the meeting agent switched on.</p>}
        {a?.enabled && (
          <p className="mt-2 text-[12px] text-slate-500">
            <span className="font-semibold text-emerald-300">● On</span> since {formatWhen(a.enabledAt ?? null)}
            {a.lastCheck ? ` · checked ${formatWhen(a.lastCheck)}` : " · first check soon"} · {a.processed ?? 0} meeting{(a.processed ?? 0) === 1 ? "" : "s"} done.
            {" "}Only meetings that end after you turned it on. It works while you are signed in to Team Pulse.
            {!autoProject && !a.project && <span className="block text-amber-300">Choose a project, or Claude can only use a project named out loud.</span>}
            {a.lastError && <span className="block text-red-300">{a.lastError}</span>}
          </p>
        )}
      </div>
      <p className="border-b border-slate-800 bg-slate-950 px-3 py-2 text-[12px] leading-snug text-slate-500">
        Your Teams meetings. <strong>Whoever organizes a meeting</strong> can bring its recording into Team Pulse: after it ends, the organizer presses <strong>Process with Claude</strong>, and Claude reads what was said and does the “Claude, …” requests. Teams must have recorded or transcribed the meeting. For a meeting someone else organized, ask them to do it, or import the file.
      </p>
      {error && <div className="p-3"><ErrorState message={error} /></div>}
      {cal.error ? <div className="p-3"><ErrorState message={cal.error} onRetry={cal.reload} /></div>
        : !cal.data ? <div className="p-4"><Spinner label="Reading your calendar…" /></div>
        : items.length === 0 ? <p className="p-4 text-[13px] text-slate-500">No Teams meetings in your calendar for the last two weeks or the coming week.</p>
        : groups.map(([title, rows]) => rows.length > 0 && (
          <div key={title}>
            <p className="sticky top-0 z-[1] border-b border-slate-800 bg-white px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</p>
            <ul>
              {rows.map((t) => (
                <li key={t.id} className="border-b border-slate-800 px-3 py-2.5">
                  <p className="truncate text-[14px] font-semibold text-slate-100" title={t.subject}>{t.subject}</p>
                  <p className="mt-0.5 text-[12px] text-slate-500">
                    {formatWhen(t.start)} · {t.isOrganizer ? <span className="font-semibold text-emerald-300">you organized</span> : t.organizer || "someone else"}
                  </p>
                  <div className="mt-1.5 flex items-center gap-2">
                    {t.state === "live" && <span className="rounded-full bg-emerald-950 px-2 py-0.5 text-[12px] font-semibold text-emerald-300">● On now</span>}
                    {t.state === "upcoming" && <span className="rounded-full bg-slate-950 px-2 py-0.5 text-[12px] text-slate-500">Coming up</span>}
                    {t.state === "finished" && (t.processedId
                      ? <button onClick={() => onOpen(t.processedId!)} className="h-7 rounded border border-slate-700 px-3 text-[12px] font-semibold text-slate-200 hover:bg-slate-950">✓ Read by Claude · Open</button>
                      : t.isOrganizer
                        ? <button onClick={() => void start(t)} disabled={busy !== null} className="h-7 rounded bg-sky-600 px-3 text-[12px] font-semibold text-white hover:bg-sky-500 disabled:opacity-50">{busy === t.id ? "Starting…" : "Process with Claude"}</button>
                        : <span className="text-[12px] leading-snug text-slate-500">{t.organizer || "The organizer"} organized this, so {t.organizer ? "they" : "they"} can process it in Team Pulse, or you can import the file.</span>)}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}

/** Ask Claude about the meeting: a summary, the action items, the decisions. Read-only. */
function AskPanel({ id, disabled }: { id: string; disabled: boolean }) {
  const [qa, setQa] = useState<{ q: string; a: string | null; error?: boolean }[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [qa]);

  const ask = async () => {
    const q = draft.trim();
    if (!q || busy) return;
    setDraft("");
    setBusy(true);
    setQa((x) => [...x, { q, a: null }]);
    try {
      const r = await api.askMeeting(id, q);
      setQa((x) => x.map((e, i) => (i === x.length - 1 ? { ...e, a: r.answer } : e)));
    } catch (e) {
      setQa((x) => x.map((it, i) => (i === x.length - 1 ? { ...it, a: e instanceof Error ? e.message : "Could not answer.", error: true } : it)));
    } finally { setBusy(false); }
  };

  return (
    <div className="flex max-h-[55%] shrink-0 flex-col border-t border-slate-800">
      <p className="shrink-0 border-b border-slate-800 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Ask Claude about this meeting</p>
      {qa.length > 0 && (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2">
          {qa.map((e, i) => (
            <div key={i}>
              <p className="ml-6 rounded-2xl rounded-br-sm bg-sky-600 px-3 py-1.5 text-[13px] text-white">{e.q}</p>
              <div className={`mr-2 mt-1 rounded-2xl rounded-bl-sm border px-3 py-1.5 text-[13px] ${e.error ? "border-red-900/60 bg-red-950 text-red-200" : "border-slate-800 bg-white text-slate-200"}`}>
                {e.a === null ? <span className="text-slate-500">Claude is reading the transcript…</span> : (
                  <div className="prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0"><ReactMarkdown remarkPlugins={[remarkGfm]}>{e.a}</ReactMarkdown></div>
                )}
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
      )}
      <div className="flex shrink-0 gap-2 p-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void ask(); }}
          disabled={disabled}
          maxLength={1000}
          placeholder={disabled ? "No transcript yet" : "Summarize it, list the action items…"}
          aria-label="Ask Claude about this meeting"
          className="h-8 min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 text-[13px] text-slate-100 outline-none focus:border-sky-500 disabled:opacity-50"
        />
        <button onClick={() => void ask()} disabled={disabled || busy || !draft.trim()} className="h-8 rounded bg-sky-600 px-3 text-[13px] font-semibold text-white hover:bg-sky-500 disabled:opacity-40">Ask</button>
      </div>
    </div>
  );
}


/** The "finished" pop-up: Microsoft look (Segoe UI, Fluent colours) with a premium finish: blurred backdrop, animated check, big number tiles. */
function DonePopup({ message, excelUrl, onClose }: { message: string; excelUrl: string; onClose: () => void }) {
  const added = /(\d+)\s+new/.exec(message)?.[1];
  const changed = /(\d+)\s+row\(s\)\s+changed/.exec(message)?.[1];
  const font = "'Segoe UI Variable Display', 'Segoe UI', -apple-system, system-ui, Roboto, sans-serif";
  const tiles = added !== undefined && changed !== undefined ? [["New rows", added, "#107C41", "#E9F6EE"], ["Rows updated", changed, "#0F6CBD", "#E8F1FA"]] : [];
  return (
    <div role="alertdialog" aria-label="Finished" onClick={onClose} className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(12,18,32,0.5)", backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)", fontFamily: font, animation: "tpFade .2s ease-out" }}>
      <style>{`
        @keyframes tpFade{from{opacity:0}to{opacity:1}}
        @keyframes tpPop{0%{opacity:0;transform:translateY(14px) scale(.96)}100%{opacity:1;transform:none}}
        @keyframes tpRing{0%{transform:scale(.6);opacity:0}60%{transform:scale(1.08);opacity:1}100%{transform:scale(1)}}
        @keyframes tpDraw{to{stroke-dashoffset:0}}
        @keyframes tpUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
      `}</style>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-[500px] overflow-hidden"
        style={{ background: "#fff", borderRadius: 16, boxShadow: "0 40px 80px rgba(0,0,0,.32), 0 0 0 1px rgba(0,0,0,.06)", color: "#1b1b1b", animation: "tpPop .32s cubic-bezier(.2,.8,.2,1)" }}>
        {/* hero */}
        <div className="relative flex flex-col items-center px-6 pb-5 pt-8 text-center" style={{ background: "linear-gradient(135deg,#E6F5EC 0%,#EAF3FB 55%,#F2EEFB 100%)" }}>
          <button aria-label="Close" onClick={onClose} style={{ position: "absolute", right: 12, top: 10, width: 32, height: 32, borderRadius: 6, border: 0, background: "transparent", color: "#5c5c5c", fontSize: 18, cursor: "pointer" }}>✕</button>
          <div className="relative" style={{ width: 84, height: 84 }}>
            {/* Excel tile */}
            <svg viewBox="0 0 64 64" width="84" height="84" aria-hidden style={{ filter: "drop-shadow(0 10px 18px rgba(16,124,65,.35))" }}>
              <defs><linearGradient id="tpx" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#21A366" /><stop offset="1" stopColor="#0B6A3B" /></linearGradient></defs>
              <rect x="4" y="4" width="56" height="56" rx="12" fill="url(#tpx)" />
              <path d="M40 4h8a12 12 0 0 1 12 12v8H40z" fill="#33C481" opacity=".55" />
              <rect x="30" y="18" width="22" height="6" rx="1.5" fill="#fff" opacity=".35" /><rect x="30" y="28" width="22" height="6" rx="1.5" fill="#fff" opacity=".35" /><rect x="30" y="38" width="22" height="6" rx="1.5" fill="#fff" opacity=".35" />
              <text x="17" y="42" fontSize="28" fontWeight="700" fill="#fff" fontFamily="Segoe UI, Arial, sans-serif">X</text>
            </svg>
            {/* animated check badge */}
            <div style={{ position: "absolute", right: -8, bottom: -6, width: 34, height: 34, borderRadius: 17, background: "#fff", boxShadow: "0 4px 12px rgba(0,0,0,.2)", display: "flex", alignItems: "center", justifyContent: "center", animation: "tpRing .5s .15s both cubic-bezier(.2,.8,.2,1)" }}>
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden><circle cx="12" cy="12" r="11" fill="#107C10" /><path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="18" strokeDashoffset="18" style={{ animation: "tpDraw .4s .55s forwards ease-out" }} /></svg>
            </div>
          </div>
          <h3 style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.01em", margin: "18px 0 0", animation: "tpUp .4s .2s both" }}>Discussion Log updated</h3>
          <p style={{ fontSize: 14, color: "#5c5c5c", margin: "4px 0 0", animation: "tpUp .4s .28s both" }}>Claude finished reading your meeting.</p>
        </div>
        {/* result */}
        <div className="px-6 pt-5" style={{ animation: "tpUp .4s .36s both" }}>
          {tiles.length > 0 ? (
            <div className="grid grid-cols-2 gap-3">
              {tiles.map(([label, n, color, bg]) => (
                <div key={label} style={{ background: bg, borderRadius: 12, padding: "14px 16px", textAlign: "left" }}>
                  <div style={{ fontSize: 38, lineHeight: "40px", fontWeight: 600, color, letterSpacing: "-0.02em" }}>{n}</div>
                  <div style={{ fontSize: 13, color: "#424242", marginTop: 2 }}>{label}</div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ background: "#F1FAF1", border: "1px solid #C7E6C7", borderRadius: 8, padding: "12px 14px", fontSize: 14 }}>✓ {message}</div>
          )}
          <p style={{ fontSize: 13.5, lineHeight: "20px", color: "#616161", margin: "14px 2px 0" }}>Open the <b style={{ color: "#242424", fontWeight: 600 }}>Discussion Log</b> tab in your Excel to see the new and changed rows.</p>
        </div>
        {/* actions */}
        <div className="flex justify-end gap-2 px-6 pb-6 pt-5">
          <button onClick={onClose} style={{ minWidth: 100, height: 38, padding: "0 22px", borderRadius: 8, border: "1px solid #d1d1d1", background: "#fff", color: "#242424", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>Close</button>
          {excelUrl && <a href={excelUrl} target="_blank" rel="noopener noreferrer" onClick={onClose}
            style={{ minWidth: 130, height: 38, padding: "0 22px", borderRadius: 8, background: "linear-gradient(180deg,#1478CE,#0F6CBD)", color: "#fff", fontSize: 14, fontWeight: 600, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, textDecoration: "none", boxShadow: "0 6px 14px rgba(15,108,189,.35)" }}>Open Excel <span aria-hidden>↗</span></a>}
        </div>
      </div>
    </div>
  );
}
