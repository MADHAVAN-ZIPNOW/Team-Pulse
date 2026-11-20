import { useMemo, useState } from "react";
import { api, type PrRef, type User } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { Card, Selector } from "../components/common";
import { Header } from "../components/Header";
import { PullRequestList } from "../components/PullRequestList";
import { PR_TABS, PrPanel, type PrTab } from "../components/PrPanel";
import { ChatPanel } from "../components/ChatPanel";

export function MainPage({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [org, setOrg] = useState("");
  const [project, setProject] = useState("");
  const [repoName, setRepoName] = useState("");
  const [prId, setPrId] = useState<number | null>(null);
  const [view, setView] = useState<"chat" | PrTab>("chat");

  const orgs = useAsync(() => api.organizations(), []);
  const projects = useAsync(() => api.projects(org), [org], !!org);
  const repos = useAsync(() => api.repositories(org, project), [org, project], !!org && !!project);
  const ref: PrRef | null = org && project && repoName ? { organization: org, project, repository: repoName } : null;
  const prs = useAsync(() => api.pullRequests(ref!), [org, project, repoName], !!ref);

  const orgOptions = useMemo(() => (orgs.data ?? []).map((o) => ({ value: o.name, label: o.name })), [orgs.data]);
  const projectOptions = useMemo(() => (projects.data ?? []).map((p) => ({ value: p.name, label: p.name })), [projects.data]);
  const repoOptions = useMemo(() => (repos.data ?? []).map((r) => ({ value: r.name, label: r.name })), [repos.data]);

  return (
    <div className="min-h-screen">
      <Header user={user} onLogout={onLogout} />
      <main className="mx-auto grid max-w-[1800px] gap-4 p-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <Card title="Source">
            <div className="space-y-3">
              <Selector
                label="Organization" value={org} options={orgOptions} loading={orgs.loading}
                error={orgs.error} onRetry={orgs.reload}
                onChange={(v) => { setOrg(v); setProject(""); setRepoName(""); setPrId(null); }}
                placeholder={orgs.data?.length === 0 ? "No organizations found" : undefined}
              />
              <Selector
                label="Project" value={project} options={projectOptions} loading={projects.loading} disabled={!org}
                error={projects.error} onRetry={projects.reload}
                onChange={(v) => { setProject(v); setRepoName(""); setPrId(null); }}
              />
              <Selector
                label="Repository" value={repoName} options={repoOptions} loading={repos.loading} disabled={!project}
                error={repos.error} onRetry={repos.reload}
                onChange={(v) => { setRepoName(v); setPrId(null); }}
              />
            </div>
          </Card>
          <Card title="Active pull requests">
            <PullRequestList
              prs={prs.data} loading={prs.loading} error={prs.error} onRetry={prs.reload}
              selectedId={prId} onSelect={(id) => { setPrId(id); }}
            />
          </Card>
        </aside>

        <section className="min-w-0 space-y-3">
          <nav className="flex gap-1 overflow-x-auto border-b border-slate-800" role="tablist">
            {[{ id: "chat" as const, label: "Chat" }, ...(ref && prId !== null ? PR_TABS : [])].map((t) => (
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
          <div hidden={view !== "chat" && !!ref && prId !== null}>
            <ChatPanel
              prId={prId}
              scope={org && project ? { organization: org, project, repository: repoName || undefined } : null}
            />
          </div>
          {ref && prId !== null && view !== "chat" && <PrPanel prId={prId} repo={ref} tab={view} />}
        </section>
      </main>
    </div>
  );
}
