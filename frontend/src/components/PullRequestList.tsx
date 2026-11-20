import type { PullRequestSummary } from "../lib/api";
import { branchName, formatDate } from "../lib/format";
import { Badge, Empty, ErrorState, Spinner } from "./common";

export function PullRequestList({
  prs, loading, error, onRetry, selectedId, onSelect,
}: {
  prs: PullRequestSummary[] | null; loading: boolean; error: string | null; onRetry: () => void;
  selectedId: number | null; onSelect: (id: number) => void;
}) {
  if (loading) return <Spinner label="Loading pull requests…" />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!prs) return <Empty>Pick a repository to see its active pull requests.</Empty>;
  if (prs.length === 0) return <Empty>No active pull requests in this repository.</Empty>;
  return (
    <ul className="space-y-2">
      {prs.map((pr) => (
        <li key={pr.pullRequestId}>
          <button
            onClick={() => onSelect(pr.pullRequestId)}
            aria-current={selectedId === pr.pullRequestId}
            className={`w-full rounded border p-3 text-left transition ${
              selectedId === pr.pullRequestId ? "border-sky-500 bg-sky-950/40" : "border-slate-800 bg-slate-900 hover:border-slate-600"
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-sm font-medium text-slate-100">{pr.title}</span>
              {pr.isDraft && <Badge tone="amber">Draft</Badge>}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              #{pr.pullRequestId} · {pr.author.displayName} · {formatDate(pr.creationDate)}
            </p>
            <p className="mt-1 truncate font-mono text-xs text-slate-400">
              {branchName(pr.sourceBranch)} → {branchName(pr.targetBranch)}
            </p>
          </button>
        </li>
      ))}
    </ul>
  );
}
