import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type AskScope } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { ErrorState, MsLogo } from "./common";
import { ModelPicker } from "./ModelPicker";
import { VOICE_LANGS, useSpeechInput, useSpeechOutput, type VoiceLang } from "../hooks/useVoice";
import { ClaudeAccount } from "./ClaudeAccount";
import { AzAccount } from "./AzAccount";

interface Suggestion { title: string; text: string; icon: string }

const PR_SUGGESTIONS: Suggestion[] = [
  { icon: "🛡️", title: "Review this PR", text: "Review this pull request completely and tell me what is risky, with a verdict" },
  { icon: "🔍", title: "Security check", text: "Are there security issues in this PR?" },
  { icon: "🧪", title: "Missing tests", text: "What tests are missing in this PR?" },
  { icon: "💡", title: "Explain simply", text: "Explain the changes in this PR in simple terms" },
];
const REPO_SUGGESTIONS: Suggestion[] = [
  { icon: "🛡️", title: "Review latest PR", text: "Review the latest pull request completely and compare it with its branch. Tell me what is risky and give a verdict" },
  { icon: "🗂️", title: "Code structure", text: "Explain the structure of the latest code" },
  { icon: "🔀", title: "Active PRs", text: "What are the active pull requests about?" },
  { icon: "🖼️", title: "Screenshots", text: "Which work items have attachments or screenshots?" },
];
const PROJECT_SUGGESTIONS: Suggestion[] = [
  { icon: "✅", title: "Work in progress", text: "What tasks are in progress and who is working on them?" },
  { icon: "📋", title: "Open work items", text: "Summarize the open work items" },
  { icon: "📦", title: "Repositories", text: "Which repositories are in this project?" },
  { icon: "🖼️", title: "Screenshots", text: "Which work items have attachments or screenshots?" },
];
const STAGES = [
  "Reading your Azure DevOps…",
  "Opening pull requests, files and attachments…",
  "Comparing code and checking risks…",
  "Writing the answer…",
];
const MAX_LEN = 2000;

interface Msg { role: "user" | "assistant"; text: string; note?: string }

function CopilotBadge({ size = 28 }: { size?: number }) {
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full border border-slate-800 bg-white shadow-sm"
      style={{ width: size, height: size }}
      aria-hidden
    >
      <MsLogo size={Math.round(size * 0.5)} />
    </span>
  );
}

function ListenButton({ id, text, voice }: { id: string; text: string; voice: ReturnType<typeof useSpeechOutput> }) {
  if (!voice.supported) return null;
  const speaking = voice.speakingId === id;
  return (
    <button
      onClick={() => (speaking ? voice.stop() : voice.speak(id, text))}
      className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-950 hover:text-slate-200"
      aria-label={speaking ? "Stop reading" : "Read this answer aloud"}
    >
      {speaking ? "■ Stop" : "🔊 Listen"}
    </button>
  );
}

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v4" />
    </svg>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch { /* clipboard may be blocked */ }
      }}
      className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-950 hover:text-slate-200"
    >
      {done ? "✓ Copied" : "Copy"}
    </button>
  );
}

/** Microsoft four-square logo whose squares light up one after another while Claude works. */
function MsLoader({ size = 26 }: { size?: number }) {
  const colours = ["#f25022", "#7fba00", "#ffb900", "#00a4ef"]; // clockwise from top-left
  const place = ["top-0 left-0", "top-0 right-0", "bottom-0 right-0", "bottom-0 left-0"];
  return (
    <span className="relative inline-block" style={{ width: size, height: size }} aria-hidden>
      {colours.map((c, i) => (
        <span
          key={c}
          className={`ms-square absolute ${place[i]}`}
          style={{ background: c, width: size * 0.46, height: size * 0.46, animationDelay: `${i * 0.25}s` }}
        />
      ))}
    </span>
  );
}

function Thinking({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.floor((now - startedAt) / 1000);
  const current = Math.min(Math.floor(secs / 8), STAGES.length - 1);
  return (
    <div className="flex gap-3" role="status">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center"><MsLoader /></span>
      <div className="min-w-0 flex-1">
        <ul className="space-y-1 text-sm">
          {STAGES.slice(0, current + 1).map((s, i) => (
            <li key={s} className={i === current ? "font-medium text-slate-200" : "text-slate-500"}>
              <span className="mr-2 inline-block w-3 text-center">{i === current ? "›" : "✓"}</span>
              {s}
              {i === current && <span className="ml-2 text-xs font-normal text-slate-600">{secs}s</span>}
            </li>
          ))}
        </ul>
        <div className="ms-bar mt-3 h-[3px] w-full max-w-xs overflow-hidden rounded-full bg-slate-800" aria-hidden />
        {secs > 20 && <p className="mt-2 text-xs text-slate-600">Big reviews can take a few minutes. You can keep this page open.</p>}
      </div>
    </div>
  );
}

export function ChatPanel({ prId, scope, userName }: { prId: number | null; scope: AskScope | null; userName: string }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [showAccounts, setShowAccounts] = useState(false);
  const [modelChoice, setModelChoice] = useState(() => { try { return localStorage.getItem("pra_model") ?? ""; } catch { return ""; } });
  const [voiceLang, setVoiceLang] = useState<VoiceLang>(() => {
    try { return localStorage.getItem("pra_voice_lang") === "ta-IN" ? "ta-IN" : "en-IN"; } catch { return "en-IN"; }
  });
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // A new PR starts a fresh conversation.
  useEffect(() => {
    setMessages([]);
    setError(null);
    setInput("");
  }, [prId, scope?.organization, scope?.project, scope?.repository]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => {  // grow the box with the text, up to about 6 lines
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [input]);

  const claude = useAsync(() => api.claudeStatus(), []);
  const modelList = useAsync(() => api.claudeModels(), []);
  const model = modelList.data?.models.some((m) => m.id === modelChoice) ? modelChoice : modelList.data?.default ?? "";
  const mic = useSpeechInput(voiceLang, setInput);
  const voice = useSpeechOutput(voiceLang);
  const pickModel = (id: string) => { setModelChoice(id); try { localStorage.setItem("pra_model", id); } catch { /* storage may be blocked */ } };
  const pickLang = (l: VoiceLang) => { setVoiceLang(l); try { localStorage.setItem("pra_voice_lang", l); } catch { /* storage may be blocked */ } };
  const az = useAsync(() => api.azStatus(), []);
  const needsToken = !!claude.data?.required && claude.data.source === "own" && !claude.data.hasToken;
  const disabled = scope === null || busy || needsToken;
  const suggestions = prId !== null ? PR_SUGGESTIONS : scope?.repository ? REPO_SUGGESTIONS : PROJECT_SUGGESTIONS;
  const target = prId !== null ? `PR #${prId}` : scope?.repository ? scope.repository : scope ? (scope.project ?? scope.organization) : null;
  const firstName = (userName || "there").split(/[ .@]/)[0];

  async function send(question: string, addUserMessage = true) {
    const q = question.trim();
    if (!q || !scope || busy) return;
    setError(null);
    setLastQuestion(q);
    if (addUserMessage) setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    mic.stop();
    voice.stop();
    setStartedAt(Date.now());
    setBusy(true);
    try {
      const res = await api.ask(scope, prId, q, model);
      const note = res.contextTruncated
        ? `Analyzed ${res.filesAnalyzed} of ${res.filesTotal} changed files (some content was truncated).`
        : undefined;
      setMessages((m) => [...m, { role: "assistant", text: res.answer, note }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const empty = messages.length === 0 && !busy;

  return (
    <div className="flex h-[78vh] min-h-[420px] flex-col overflow-hidden lg:h-full rounded-2xl border border-slate-800 bg-white shadow-sm">
      {/* thin toolbar: who Claude works as, and a new-chat button */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-950/60 px-4 py-2">
        <CopilotBadge size={22} />
        <span className="text-sm font-semibold text-slate-200">{target ? `Ask Claude · ${target}` : "Ask Claude"}</span>
        {messages.length > 0 && (
          <button
            onClick={() => { setMessages([]); setError(null); }}
            className="ml-auto rounded-full border border-slate-700 px-3 py-1 text-xs font-medium text-slate-300 hover:bg-white"
          >
            + New chat
          </button>
        )}
      </div>
      {(claude.data?.required || az.data?.enabled) && (
        <div className="border-b border-slate-800 px-4 py-1.5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
            {claude.data?.required && (
              <span>
                Claude: <strong className="text-slate-300">{claude.data.source === "own" ? "your own account" : `shared (${claude.data.source})`}</strong>{" "}
                {claude.data.hasToken || claude.data.source !== "own" ? <span className="text-emerald-400">✓ connected</span> : <span className="text-amber-400">not connected</span>}
              </span>
            )}
            {az.data?.enabled && az.data.signedIn && <span>Azure DevOps: <strong className="text-slate-300">as you</strong> ({az.data.user})</span>}
            <button onClick={() => setShowAccounts((v) => !v)} className="ml-auto text-sky-600 hover:underline">
              {showAccounts || needsToken ? "Hide settings" : "Account settings"}
            </button>
          </div>
          {(showAccounts || needsToken) && (
            <div className="mt-2 flex max-h-[40vh] flex-wrap items-start gap-2 overflow-y-auto pb-1">
              {claude.data?.required && <div className="min-w-0 flex-1 basis-80"><ClaudeAccount status={claude.data} onChanged={claude.reload} /></div>}
              {az.data?.enabled && <div className="min-w-0 flex-1 basis-80"><AzAccount status={az.data} onChanged={az.reload} /></div>}
            </div>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto" aria-live="polite">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {empty && (
            <div className="pt-6 text-center sm:pt-12">
              <div className="mx-auto mb-4 flex justify-center"><CopilotBadge size={56} /></div>
              <h2 className="text-3xl font-semibold text-slate-100">
                Hello, {firstName}
              </h2>
              <p className="mt-2 text-base text-slate-500">
                {scope === null
                  ? "Connecting…"
                  : "How can I help with your Azure DevOps today?"}
              </p>
              {scope !== null && (
                <div className="mt-8 grid gap-3 text-left sm:grid-cols-2">
                  {suggestions.map((s) => (
                    <button
                      key={s.title}
                      disabled={disabled}
                      onClick={() => send(s.text)}
                      className="group h-full rounded-xl border border-slate-800 bg-white p-4 text-left shadow-sm transition hover:border-slate-600 hover:shadow-md disabled:opacity-40"
                    >
                      <span className="text-xl" aria-hidden>{s.icon}</span>
                      <p className="mt-2 text-sm font-semibold text-slate-100">{s.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{s.text}</p>
                    </button>
                  ))}
                </div>
              )}
              {needsToken && <p className="mt-6 text-sm text-amber-400">Connect your Claude account (top right) to start.</p>}
            </div>
          )}

          <div className="space-y-6">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-sky-900 px-4 py-2.5 text-sm text-slate-100">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex gap-3">
                  <CopilotBadge />
                  <div className="min-w-0 flex-1">
                    <div className="prose prose-sm max-w-none text-slate-200 prose-headings:mb-2 prose-headings:mt-5 prose-headings:font-semibold prose-p:leading-relaxed prose-pre:rounded-lg prose-pre:bg-slate-950 prose-pre:text-slate-200 prose-code:before:content-none prose-code:after:content-none prose-table:text-xs">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                    </div>
                    {m.note && <p className="mt-2 text-xs text-amber-400">{m.note}</p>}
                    <div className="mt-2 flex items-center gap-1 border-t border-slate-800 pt-1.5">
                      <CopyButton text={m.text} />
                      <ListenButton id={String(i)} text={m.text} voice={voice} />
                      <span className="ml-auto text-[11px] text-slate-600">AI-generated. Check important findings before acting.</span>
                    </div>
                  </div>
                </div>
              ),
            )}
            {busy && <Thinking startedAt={startedAt} />}
            {error && (
              <div className="space-y-2">
                <ErrorState message={error} onRetry={lastQuestion ? () => send(lastQuestion, false) : undefined} />
                {/limit/i.test(error) && claude.data?.required && (
                  <p className="text-xs text-amber-400">Usage limit reached. Use the <strong>Claude account</strong> switch above to pick another account, then retry.</p>
                )}
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>
      </div>

      {/* composer */}
      <div className="border-t border-slate-800 bg-white px-4 pb-3 pt-3">
        <form
          className="mx-auto w-full max-w-3xl"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <div className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-white px-3 py-2 shadow-sm transition focus-within:border-slate-500 focus-within:shadow-md">
            <textarea
              ref={box}
              value={input}
              maxLength={MAX_LEN}
              rows={1}
              disabled={disabled}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder={needsToken ? "Connect your Claude account first" : `Message Claude about ${target}…`}
              className="no-ring max-h-[150px] min-h-[28px] flex-1 resize-none bg-transparent py-1 text-sm text-slate-100 outline-none placeholder:text-slate-500 disabled:opacity-50"
            />
            {mic.supported && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => (mic.listening ? mic.stop() : mic.start(input))}
                aria-label={mic.listening ? "Stop voice input" : "Speak your question"}
                title={mic.listening ? "Stop" : `Speak your question (${voiceLang === "ta-IN" ? "Tamil" : "English"})`}
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition disabled:opacity-40 ${
                  mic.listening ? "mic-live border-red-400 bg-red-950 text-red-400" : "border-slate-700 text-slate-400 hover:bg-slate-950"
                }`}
              >
                <MicIcon />
              </button>
            )}
            <button
              type="submit"
              disabled={disabled || !input.trim()}
              aria-label="Send"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky-600 text-white transition hover:bg-sky-500 disabled:bg-slate-700 disabled:opacity-60"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {modelList.data && <ModelPicker models={modelList.data.models} value={model} onChange={pickModel} disabled={busy} />}
            {(mic.supported || voice.supported) && (
              <div className="flex h-8 items-center overflow-hidden rounded-full border border-slate-700 text-xs" role="group" aria-label="Voice language">
                {VOICE_LANGS.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    onClick={() => pickLang(l.id)}
                    aria-pressed={voiceLang === l.id}
                    title={`Voice language: ${l.label}`}
                    className={`px-2.5 py-1 ${voiceLang === l.id ? "bg-sky-900 font-medium text-slate-100" : "text-slate-500 hover:bg-slate-950"}`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            )}
            <span className="ml-auto text-[11px] text-slate-600">
              {mic.listening ? "Listening… speak now" : `Enter to send · Shift+Enter for a new line${input.length > MAX_LEN * 0.8 ? ` · ${input.length}/${MAX_LEN}` : ""}`}
            </span>
          </div>
          {mic.error && <p className="mt-1 text-xs text-red-400" role="alert">{mic.error}</p>}
        </form>
      </div>
    </div>
  );
}
