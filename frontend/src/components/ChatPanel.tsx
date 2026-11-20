import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type AskScope } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { ErrorState, MsLogo } from "./common";
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

function Thinking({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.floor((now - startedAt) / 1000);
  const stage = STAGES[Math.min(Math.floor(secs / 8), STAGES.length - 1)];
  return (
    <div className="flex gap-3">
      <CopilotBadge />
      <div className="pt-1">
        <div className="flex items-center gap-1.5" aria-hidden>
          {[0, 1, 2].map((i) => (
            <span key={i} className="h-2 w-2 animate-bounce rounded-full bg-[#7160e8]" style={{ animationDelay: `${i * 150}ms` }} />
          ))}
        </div>
        <p className="mt-2 text-sm text-slate-500" role="status">
          {stage} <span className="text-slate-600">{secs}s</span>
        </p>
        {secs > 20 && <p className="text-xs text-slate-600">Big reviews can take a few minutes. You can keep this page open.</p>}
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
  const az = useAsync(() => api.azStatus(), []);
  const needsToken = !!claude.data?.required && claude.data.source === "own" && !claude.data.hasToken;
  const disabled = scope === null || busy || needsToken;
  const suggestions = prId !== null ? PR_SUGGESTIONS : scope?.repository ? REPO_SUGGESTIONS : PROJECT_SUGGESTIONS;
  const target = prId !== null ? `PR #${prId}` : scope?.repository ? scope.repository : scope ? scope.project : null;
  const firstName = (userName || "there").split(/[ .@]/)[0];

  async function send(question: string, addUserMessage = true) {
    const q = question.trim();
    if (!q || !scope || busy) return;
    setError(null);
    setLastQuestion(q);
    if (addUserMessage) setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setStartedAt(Date.now());
    setBusy(true);
    try {
      const res = await api.ask(scope, prId, q);
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
    <div className="flex h-[calc(100vh-190px)] min-h-[520px] flex-col overflow-hidden rounded-2xl border border-slate-800 bg-white shadow-sm">
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
        <div className="flex flex-wrap items-start gap-2 border-b border-slate-800 px-4 py-2">
          {claude.data?.required && <div className="min-w-0 flex-1 basis-80"><ClaudeAccount status={claude.data} onChanged={claude.reload} /></div>}
          {az.data?.enabled && <div className="min-w-0 flex-1 basis-80"><AzAccount status={az.data} onChanged={az.reload} /></div>}
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
                  ? "Select an organization and project on the left to start."
                  : "How can I help with your Azure DevOps today?"}
              </p>
              {scope !== null && (
                <div className="mt-8 grid gap-3 text-left sm:grid-cols-2">
                  {suggestions.map((s) => (
                    <button
                      key={s.title}
                      disabled={disabled}
                      onClick={() => send(s.text)}
                      className="group h-full rounded-xl border border-slate-800 bg-white p-4 text-left shadow-sm transition hover:border-[#7160e8] hover:shadow-md disabled:opacity-40"
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
          <div className="flex items-end gap-2 rounded-2xl border border-slate-700 bg-white px-3 py-2 shadow-sm transition focus-within:border-[#7160e8] focus-within:shadow-md">
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
              placeholder={needsToken ? "Connect your Claude account first" : scope === null ? "Select a project first" : `Message Claude about ${target}…`}
              className="max-h-[150px] min-h-[28px] flex-1 resize-none bg-transparent py-1 text-sm text-slate-100 outline-none placeholder:text-slate-500 disabled:opacity-50"
            />
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
          <p className="mt-1.5 text-center text-[11px] text-slate-600">
            Enter to send · Shift+Enter for a new line{input.length > MAX_LEN * 0.8 ? ` · ${input.length}/${MAX_LEN}` : ""}
          </p>
        </form>
      </div>
    </div>
  );
}
