import { useState } from "react";
import { api } from "../lib/api";
import { ErrorState } from "./common";

/** Each person connects their own Claude account by pasting a token from `claude setup-token`. */
export function ClaudeAccount({ hasToken, onChanged }: { hasToken: boolean; onChanged: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.saveClaudeToken(token.trim());
      setToken("");
      setChanging(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the token");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.removeClaudeToken();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  if (hasToken && !changing) {
    return (
      <div className="flex items-center justify-between rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
        <span>✓ Using <strong>your own</strong> Claude account for this session.</span>
        <span className="flex gap-3">
          <button onClick={() => setChanging(true)} className="text-sky-600 hover:underline">Change</button>
          <button onClick={remove} disabled={busy} className="text-sky-600 hover:underline">Remove</button>
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border border-sky-500/40 bg-sky-950 p-3 text-sm">
      <p className="font-semibold text-slate-200">Connect your Claude account</p>
      <ol className="list-decimal space-y-0.5 pl-5 text-xs text-slate-300">
        <li>Open a terminal on your computer and run <code className="rounded bg-white px-1">claude setup-token</code>.</li>
        <li>Sign in with your own Claude account and copy the token it prints (starts with <code>sk-ant-</code>).</li>
        <li>Paste it below. Your questions then use <strong>your</strong> Claude limits, not anyone else&apos;s.</li>
      </ol>
      <div className="flex gap-2">
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="sk-ant-…"
          className="min-w-0 flex-1 rounded border border-slate-700 bg-white px-3 py-1.5 font-mono text-xs outline-none focus:border-sky-500"
        />
        <button
          onClick={save}
          disabled={busy || token.trim().length < 20}
          className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        {changing && (
          <button onClick={() => { setChanging(false); setToken(""); }} className="rounded border border-slate-700 px-3 py-1.5 text-xs">Cancel</button>
        )}
      </div>
      <p className="text-[11px] text-slate-500">
        Kept only in this session&apos;s server memory; never shown again and cleared when you log out or the server restarts.
      </p>
      {error && <ErrorState message={error} />}
    </div>
  );
}
