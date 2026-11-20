import { useEffect, useState } from "react";
import { api, type AzStatus } from "../lib/api";
import { ErrorState } from "./common";

/** Sign in to the Azure CLI as yourself, so Claude can run az commands with YOUR permissions (within the server policy). */
export function AzAccount({ status, onChanged }: { status: AzStatus; onChanged: () => void }) {
  const [login, setLogin] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!login || status.signedIn) return;
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started > 15 * 60_000) {
        clearInterval(timer);
        setLogin(null);
        setError("Sign-in timed out. Try again.");
        return;
      }
      onChanged();
    }, 4000);
    return () => clearInterval(timer);
  }, [login, status.signedIn, onChanged]);

  useEffect(() => {
    if (status.signedIn) setLogin(null);
  }, [status.signedIn]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setLogin(await api.azLogin());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start sign-in");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    try {
      await api.azLogout();
      setLogin(null);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  if (status.signedIn && status.managedByLogin) {
    return (
      <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
        Claude works in Azure DevOps <strong>as you</strong> ({status.user}). It may {status.policyText}.
      </div>
    );
  }

  if (status.signedIn) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
        <span>
          <strong>Azure CLI</strong> signed in as {status.user ?? "you"} · Claude may {status.policyText}
        </span>
        <button onClick={signOut} disabled={busy} className="text-sky-600 hover:underline">Sign out</button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          <strong>Azure CLI</strong> not signed in. Sign in so Claude can work in Azure DevOps as you ({status.policyText}).
        </span>
        {!login && (
          <button onClick={start} disabled={busy} className="rounded bg-sky-600 px-3 py-1 font-medium text-white hover:bg-sky-500 disabled:opacity-40">
            {busy ? "Starting…" : "Sign in with a code"}
          </button>
        )}
      </div>
      {login && (
        <div className="space-y-1">
          <p>
            Open <a className="text-sky-600 underline" href={login.url} target="_blank" rel="noreferrer">{login.url}</a> and enter this code with your
            company account:
          </p>
          <p className="select-all rounded bg-white py-2 text-center font-mono text-lg tracking-widest text-sky-600">{login.code}</p>
          <p className="text-slate-500">Waiting for you to finish…</p>
        </div>
      )}
      {error && <ErrorState message={error} />}
    </div>
  );
}
