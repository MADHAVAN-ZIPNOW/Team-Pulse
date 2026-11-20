import { useState } from "react";
import { api, type ClaudeStatus } from "../lib/api";
import { ErrorState } from "./common";

/**
 * Which Claude account answers your questions: your own (paste a token from `claude setup-token`) or one of the
 * shared accounts the owner made available. Switch any time, e.g. when your own usage limit is reached.
 */
export function ClaudeAccount({ status, onChanged }: { status: ClaudeStatus; onChanged: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);
  const usingShared = status.source !== "own";

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const switcher = (
    <label className="flex items-center gap-2 text-xs text-slate-300">
      <span className="font-semibold">Claude account</span>
      <select
        value={status.source}
        disabled={busy}
        onChange={(e) => run(() => api.setClaudeSource(e.target.value))}
        className="rounded border border-slate-700 bg-white px-2 py-1 text-xs outline-none focus:border-sky-500"
      >
        <option value="own">My own account{status.hasToken ? "" : " (not connected)"}</option>
        {status.pool.map((label) => (
          <option key={label} value={label}>Shared: {label}</option>
        ))}
      </select>
    </label>
  );

  if (usingShared) {
    return (
      <div className="space-y-1 rounded border border-slate-800 bg-slate-950 px-3 py-2">
        {switcher}
        <p className="text-[11px] text-slate-500">
          Questions now run on <strong>{status.source}</strong>&apos;s account and count against their limits. Switch back when yours resets.
        </p>
        {error && <ErrorState message={error} />}
      </div>
    );
  }

  if (status.hasToken && !changing) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-800 bg-slate-950 px-3 py-2">
        {switcher}
        <span className="flex items-center gap-3 text-xs">
          <span className="text-slate-500">✓ your own token is connected</span>
          <button onClick={() => setChanging(true)} className="text-sky-600 hover:underline">Change token</button>
          <button onClick={() => run(() => api.removeClaudeToken())} disabled={busy} className="text-sky-600 hover:underline">Remove</button>
        </span>
        {error && <div className="w-full"><ErrorState message={error} /></div>}
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border border-sky-500/40 bg-sky-950 p-3 text-sm">
      {status.pool.length > 0 && switcher}
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
          onClick={() =>
            run(async () => {
              await api.saveClaudeToken(token.trim());
              setToken("");
              setChanging(false);
            })
          }
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
