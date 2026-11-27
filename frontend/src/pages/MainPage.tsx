import { useEffect, useState } from "react";
import { DEFAULT_ORG, type AskScope, type User } from "../lib/api";
import { Header } from "../components/Header";
import { ChatPanel } from "../components/ChatPanel";
import { SideNav, type PageId } from "../components/SideNav";
import { TrendsPage } from "./TrendsPage";
import { RadarPage } from "./RadarPage";
import { MeetingsPage } from "./MeetingsPage";

export function MainPage({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [page, setPage] = useState<PageId>(() => (window.location.hash === "#/trends" ? "trends" : window.location.hash === "#/meetings" ? "meetings" : window.location.hash === "#/radar" ? "radar" : "assistant"));
  const selectPage = (p: PageId) => {
    setPage(p);
    window.history.replaceState(null, "", p === "trends" ? "#/trends" : p === "meetings" ? "#/meetings" : p === "radar" ? "#/radar" : "#/");
  };
  const [view, setView] = useState<"chat">("chat");
  const [welcome, setWelcome] = useState(() => {
    try {
      const fresh = sessionStorage.getItem("justSignedIn") === "1";
      sessionStorage.removeItem("justSignedIn");
      return fresh;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (!welcome) return;
    const t = setTimeout(() => setWelcome(false), 8000);
    return () => clearTimeout(t);
  }, [welcome]);

  const scope: AskScope = { organization: DEFAULT_ORG };

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <Header user={user} onLogout={onLogout} />
      {welcome && (
        <div role="status" className="mx-4 mt-2 flex shrink-0 items-center justify-between gap-3 rounded border border-emerald-400/40 bg-emerald-950 px-4 py-2 text-sm text-emerald-300">
          <span>✓ Signed in as <strong>{user.email ?? user.name}</strong>. Claude can now work in your Azure DevOps with your own access.</span>
          <button onClick={() => setWelcome(false)} className="text-xs underline">Dismiss</button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <SideNav page={page} onSelect={selectPage} />
        {page === "radar" ? (
          <main className="min-h-0 min-w-0 flex-1 overflow-hidden"><RadarPage /></main>
        ) : page === "trends" ? (
          <main className="min-h-0 min-w-0 flex-1 overflow-hidden"><TrendsPage /></main>
        ) : page === "meetings" ? (
          <main className="min-h-0 min-w-0 flex-1 overflow-hidden"><MeetingsPage /></main>
        ) : (
      <main className="mx-auto grid min-h-0 min-w-0 w-full max-w-[1800px] flex-1 gap-4 p-4">
        <section className="flex min-h-0 min-w-0 flex-col gap-3">
          <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-800" role="tablist">
            {[{ id: "chat" as const, label: "Chat" }].map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={view === t.id}
                onClick={() => setView(t.id)}
                className={`whitespace-nowrap px-4 py-2 text-sm font-semibold ${
                  view === t.id ? "border-b-2 border-sky-600 text-sky-600" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {/* Chat stays mounted so the conversation survives switching tabs. */}
          <div className="min-h-0 flex-1" >
            <ChatPanel
              userName={user.name}
              prId={null}
              scope={scope}
            />
          </div>
        </section>
      </main>
        )}
      </div>
    </div>
  );
}
