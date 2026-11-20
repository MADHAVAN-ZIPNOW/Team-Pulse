import { useEffect, useState } from "react";
import { ErrorState, MsLogo, Spinner } from "../components/common";

const jsonFetch = async (path: string, init?: RequestInit) => {
  const res = await fetch(path, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (HTTP ${res.status})`);
  return body;
};

/** base: "/auth/cli" = one shared az login on this machine; "/auth/az" = each person signs in with their own az login. */
function CliSignIn({ base }: { base: "/auth/cli" | "/auth/az" }) {
  const [step, setStep] = useState<"idle" | "starting" | "waiting" | "done">("idle");
  const [login, setLogin] = useState<{ code: string; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    setStep("starting");
    try {
      // If `az` is already signed in, this finishes immediately.
      if (base === "/auth/cli" && (await jsonFetch(`${base}/status`)).signedIn) return window.location.reload();
      setLogin(await jsonFetch(`${base}/start`, { method: "POST" }));
      setStep("waiting");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start sign-in");
      setStep("idle");
    }
  }

  useEffect(() => {
    if (step !== "waiting") return;
    const started = Date.now();
    let inFlight = false;
    let finished = false;
    const timer = setInterval(async () => {
      if (inFlight || finished) return;  // never let two checks overlap: the second would report "expired"
      if (Date.now() - started > 15 * 60_000) {
        clearInterval(timer);
        setError("Sign-in timed out. Try again.");
        setStep("idle");
        return;
      }
      inFlight = true;
      try {
        const res = await jsonFetch(`${base}/status`);
        if (finished) return;
        if (res.signedIn) {
          finished = true;
          clearInterval(timer);
          setStep("done");
          try { sessionStorage.setItem("justSignedIn", "1"); } catch { /* storage may be blocked */ }
          window.location.reload();
        } else if (res.expired) {
          finished = true;
          clearInterval(timer);
          setError("The sign-in expired. Start again.");
          setStep("idle");
        }
      } catch (e) {
        if (e instanceof Error && /not allowed/i.test(e.message)) {  // a clear refusal: stop and show it
          clearInterval(timer);
          setError(e.message);
          setStep("idle");
        }
        /* otherwise keep polling */
      } finally {
        inFlight = false;
      }
    }, 1500);
    return () => { finished = true; clearInterval(timer); };
  }, [step, base]);

  return (
    <div className="space-y-4">
      {error && <ErrorState message={error} />}
      {step === "done" ? (
        <p className="rounded border border-emerald-400/40 bg-emerald-950 px-3 py-3 text-sm font-medium text-emerald-300" role="status">
          ✓ Signed in. Opening the app…
        </p>
      ) : step === "waiting" && login ? (
        <div className="space-y-3 text-left text-sm text-slate-300">
          <p>
            1. Open{" "}
            <a className="text-sky-400 underline" href={login.url} target="_blank" rel="noreferrer">{login.url}</a>
          </p>
          <p>2. Enter this code and sign in with your Microsoft account:</p>
          <p className="rounded bg-slate-950 py-3 text-center font-mono text-2xl tracking-widest text-sky-300 select-all">{login.code}</p>
          <Spinner label="Waiting for you to finish signing in… the app opens by itself." />
        </div>
      ) : (
        <button
          onClick={start}
          disabled={step === "starting"}
          className="flex w-full items-center justify-center gap-2 rounded bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
        >
          <MsLogo />
          {step === "starting" ? "Getting your code…" : "Sign in with Microsoft"}
        </button>
      )}
      {step === "idle" && base === "/auth/az" && (
        <p className="text-xs text-slate-500">You will get a short code to enter at microsoft.com/devicelogin.</p>
      )}
    </div>
  );
}

function MicrosoftSignIn() {
  return (
    <a
      href="/auth/login"
      className="flex w-full items-center justify-center gap-2 rounded bg-sky-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-sky-500"
    >
      <MsLogo />
      Sign in with Microsoft
    </a>
  );
}

export function LoginPage({ error }: { error: string | null }) {
  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => {
    jsonFetch("/api/health").then((h) => setMode(h.authMode)).catch(() => setMode("msal"));
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6 rounded-2xl border border-slate-800 bg-white p-8 text-center shadow-xl">
        <div>
          <span className="mx-auto flex justify-center"><MsLogo size={44} /></span>
          <h1 className="mt-2 text-xl font-semibold text-slate-100">Azure DevOps PR Assistant</h1>
          <p className="mt-1 text-sm text-slate-400">Ask Claude about your Azure DevOps code, pull requests, tasks and attachments.</p>
        </div>
        {error && <ErrorState message={error} />}
        {mode === null ? <Spinner /> : mode === "az_login" ? <CliSignIn base="/auth/az" /> : mode === "azure_cli" ? <CliSignIn base="/auth/cli" /> : <MicrosoftSignIn />}
      </div>
    </main>
  );
}
