import { useEffect, useState } from "react";
import { api, type PrRef } from "../lib/api";
import { useAsync } from "../hooks/useAsync";
import { branchName, formatDate } from "../lib/format";
import { Badge, Card, Empty, ErrorState, Spinner } from "./common";
import { DiffViewer } from "./DiffViewer";

export type PrTab = "overview" | "files" | "commits" | "comments";
export const PR_TABS: { id: PrTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "files", label: "Files & diff" },
  { id: "commits", label: "Commits" },
  { id: "comments", label: "Comments" },
];

const changeTone = (t: string) => (t.includes("add") ? "green" : t.includes("delete") ? "red" : "sky");

export function PrPanel({ prId, repo, tab }: { prId: number; repo: PrRef; tab: PrTab }) {
  const key = [repo.organization, repo.project, repo.repository, prId];
  const detail = useAsync(() => api.pullRequest(prId, repo), key);
  const changes = useAsync(() => api.changes(prId, repo), key);
  const commits = useAsync(() => api.commits(prId, repo), key);
  const comments = useAsync(() => api.comments(prId, repo), key);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  useEffect(() => {
    setSelectedPath(null);
  }, [prId]);
  const files = changes.data?.files ?? [];
  const current = files.find((f) => f.path === selectedPath) ?? files[0];

  return (
    <div className="space-y-3">
      {tab === "overview" && (
        <Card>
          {detail.loading && <Spinner label="Loading pull request…" />}
          {detail.error && <ErrorState message={detail.error} onRetry={detail.reload} />}
          {detail.data && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold text-slate-100">{detail.data.title}</h2>
                <Badge tone="green">{detail.data.status}</Badge>
                {detail.data.isDraft && <Badge tone="amber">Draft</Badge>}
                {detail.data.mergeStatus && <Badge>{detail.data.mergeStatus}</Badge>}
              </div>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                <Row k="PR">#{detail.data.pullRequestId}{detail.data.webUrl && <> · <a className="text-sky-400 underline" href={detail.data.webUrl} target="_blank" rel="noreferrer">open in Azure DevOps</a></>}</Row>
                <Row k="Author">{detail.data.author.displayName}</Row>
                <Row k="Branches"><span className="font-mono text-xs">{branchName(detail.data.sourceBranch)} → {branchName(detail.data.targetBranch)}</span></Row>
                <Row k="Created">{formatDate(detail.data.creationDate)}</Row>
                <Row k="Latest iteration">{detail.data.latestIterationId ?? "–"}</Row>
              </dl>
              <div>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Reviewers</h3>
                {detail.data.reviewers.length === 0 ? (
                  <p className="text-sm text-slate-500">No reviewers assigned.</p>
                ) : (
                  <ul className="flex flex-wrap gap-2">
                    {detail.data.reviewers.map((r) => (
                      <li key={r.id ?? r.displayName}>
                        <Badge tone={r.vote > 0 ? "green" : r.vote < 0 ? "red" : "slate"}>
                          {r.displayName}{r.isRequired ? " *" : ""} · {r.voteLabel}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Description</h3>
                <p className="whitespace-pre-wrap text-sm text-slate-300">{detail.data.description || "No description."}</p>
              </div>
            </div>
          )}
        </Card>
      )}

      {tab === "files" && (
        <div className="grid gap-3 xl:grid-cols-[280px_minmax(0,1fr)]">
          <Card title="Changed files">
            {changes.loading && <Spinner label="Loading changes and computing diffs…" />}
            {changes.error && <ErrorState message={changes.error} onRetry={changes.reload} />}
            {changes.data && files.length === 0 && <Empty>No changed files.</Empty>}
            <ul className="max-h-72 space-y-1 overflow-y-auto xl:max-h-[60vh]">
              {files.map((f) => (
                <li key={f.path}>
                  <button
                    onClick={() => setSelectedPath(f.path)}
                    className={`w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-slate-800 ${current?.path === f.path ? "bg-slate-800" : ""}`}
                  >
                    <span className="mb-0.5 flex items-center gap-1.5">
                      <Badge tone={changeTone(f.changeType)}>{f.changeType}</Badge>
                      <span className="text-emerald-400">+{f.additions}</span>
                      <span className="text-red-400">-{f.deletions}</span>
                    </span>
                    <span className="block break-all font-mono text-slate-300">{f.path}</span>
                  </button>
                </li>
              ))}
            </ul>
            {changes.data?.filesTruncated && (
              <p className="mt-2 text-xs text-amber-400">Showing {files.length} of {changes.data.totalFiles} files.</p>
            )}
          </Card>
          <Card title={current ? current.path : "Diff"}>
            {current ? <DiffViewer file={current} /> : <Empty>Select a file to see its diff.</Empty>}
          </Card>
        </div>
      )}

      {tab === "commits" && (
        <Card>
          {commits.loading && <Spinner label="Loading commits…" />}
          {commits.error && <ErrorState message={commits.error} onRetry={commits.reload} />}
          {commits.data?.length === 0 && <Empty>No commits.</Empty>}
          <ul className="divide-y divide-slate-800">
            {commits.data?.map((c) => (
              <li key={c.commitId} className="py-2">
                <p className="text-sm text-slate-200">{c.comment.split("\n")[0]}</p>
                <p className="text-xs text-slate-500">
                  <span className="font-mono">{c.commitId.slice(0, 8)}</span> · {c.authorName} · {formatDate(c.authorDate)}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {tab === "comments" && (
        <Card>
          {comments.loading && <Spinner label="Loading comments…" />}
          {comments.error && <ErrorState message={comments.error} onRetry={comments.reload} />}
          {comments.data?.length === 0 && <Empty>No review comments yet.</Empty>}
          <ul className="space-y-3">
            {comments.data?.map((t) => (
              <li key={t.id} className="rounded border border-slate-800 p-3">
                <p className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  {t.status && <Badge>{t.status}</Badge>}
                  {t.filePath && <span className="font-mono">{t.filePath}{t.line ? `:${t.line}` : ""}</span>}
                </p>
                {t.comments.map((c) => (
                  <p key={c.id} className="text-sm text-slate-300"><strong className="text-slate-100">{c.author}:</strong> {c.content}</p>
                ))}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-28 shrink-0 text-slate-500">{k}</dt>
      <dd className="text-slate-200">{children}</dd>
    </div>
  );
}
