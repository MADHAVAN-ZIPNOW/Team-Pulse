import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type AskScope } from "../lib/api";
import { Card, ErrorState, Spinner } from "./common";

const PR_SUGGESTIONS = [
  "Summarize this PR",
  "What are the risks?",
  "Are there security issues?",
  "What tests are missing?",
  "Explain the changes in simple terms",
  "Which files need closest review?",
];
const REPO_SUGGESTIONS = [
  "Explain the structure of the latest code",
  "What are the active pull requests about?",
  "What tasks are in progress?",
  "Which work items have attachments or screenshots?",
];
const PROJECT_SUGGESTIONS = [
  "What tasks are in progress and who is working on them?",
  "Summarize the open work items",
  "Which repositories are in this project?",
  "Which work items have attachments or screenshots?",
];
const MAX_LEN = 2000;

interface Msg { role: "user" | "assistant"; text: string; note?: string }

export function ChatPanel({ prId, scope }: { prId: number | null; scope: AskScope | null }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastQuestion, setLastQuestion] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // A new PR starts a fresh conversation.
  useEffect(() => {
    setMessages([]);
    setError(null);
    setInput("");
  }, [prId, scope?.organization, scope?.project, scope?.repository]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const disabled = scope === null || busy;
  const suggestions = prId !== null ? PR_SUGGESTIONS : scope?.repository ? REPO_SUGGESTIONS : PROJECT_SUGGESTIONS;
  const target = prId !== null ? `PR #${prId}` : scope?.repository ? `repository ${scope.repository}` : scope ? `project ${scope.project}` : null;

  async function send(question: string, addUserMessage = true) {
    const q = question.trim();
    if (!q || !scope || busy) return;
    setError(null);
    setLastQuestion(q);
    if (addUserMessage) setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
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

  return (
    <Card title={target ? `Ask Claude · ${target}` : "Ask Claude"}>
      <div className="flex h-[70vh] min-h-[420px] flex-col lg:h-[calc(100vh-215px)]">
        <div className="flex-1 space-y-3 overflow-y-auto pr-1" aria-live="polite">
          {messages.length === 0 && !busy && (
            <p className="text-sm text-slate-500">
              {scope === null
                ? "Select an organization and project to start asking questions."
                : `Ask anything about ${target}. Claude reads the latest code, pull requests, work items (tasks, bugs), images and attachments from your Azure DevOps.`}
            </p>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="ml-8 rounded-lg bg-sky-900/50 px-3 py-2 text-sm text-sky-50">{m.text}</div>
            ) : (
              <div key={i} className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3">
                <div className="prose prose-sm max-w-none prose-pre:bg-slate-950 prose-headings:mt-4 prose-headings:mb-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                </div>
                {m.note && <p className="mt-2 text-xs text-amber-400">{m.note}</p>}
              </div>
            ),
          )}
          {busy && (
            <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3">
              <Spinner label="Claude is looking things up in Azure DevOps… this can take a minute or two." />
            </div>
          )}
          {error && <ErrorState message={error} onRetry={lastQuestion ? () => send(lastQuestion, false) : undefined} />}
          <div ref={bottom} />
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {suggestions.map((s) => (
            <button
              key={s}
              disabled={disabled}
              onClick={() => send(s)}
              className="rounded-full border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-300 disabled:opacity-40"
            >
              {s}
            </button>
          ))}
        </div>

        <form
          className="mt-3 flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <div className="flex-1">
            <textarea
              value={input}
              maxLength={MAX_LEN}
              rows={2}
              disabled={disabled}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              placeholder={scope === null ? "Select a project first" : `Ask about ${target}…`}
              className="w-full resize-none rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-500 disabled:opacity-50"
            />
            <p className="text-right text-[10px] text-slate-600">{input.length}/{MAX_LEN}</p>
          </div>
          <button
            type="submit"
            disabled={disabled || !input.trim()}
            className="mb-5 rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40"
          >
            Send
          </button>
        </form>
      </div>
    </Card>
  );
}
