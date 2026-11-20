// Thin typed client for the local backend. Auth is a cookie; no token is ever visible here.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export interface User { name: string; email: string | null; tenantId: string | null }
export interface Organization { name: string; url?: string | null }
export interface Project { id: string; name: string; description?: string | null }
export interface Repository { id: string; name: string; defaultBranch?: string | null }
export interface Identity { id?: string | null; displayName: string; uniqueName?: string | null; imageUrl?: string | null }
export interface Reviewer extends Identity { vote: number; voteLabel: string; isRequired: boolean }
export interface PullRequestSummary {
  pullRequestId: number; title: string; status: string; isDraft: boolean;
  author: Identity; sourceBranch: string; targetBranch: string; creationDate?: string | null;
}
export interface PullRequestDetail extends PullRequestSummary {
  description?: string | null; reviewers: Reviewer[]; mergeStatus?: string | null;
  latestIterationId?: number | null; webUrl?: string | null;
}
export interface Commit { commitId: string; comment: string; authorName?: string | null; authorDate?: string | null }
export interface FileChange {
  path: string; changeType: string; originalPath?: string | null; isBinary: boolean;
  additions: number; deletions: number; diff?: string | null; diffSkippedReason?: string | null;
}
export interface Changes { iterationId: number | null; totalFiles: number; files: FileChange[]; filesTruncated: boolean }
export interface CommentThread {
  id: number; status?: string | null; filePath?: string | null; line?: number | null;
  comments: { id: number; author: string; content: string; publishedDate?: string | null }[];
}
export interface AskResponse {
  answer: string; pullRequestId?: number | null; filesAnalyzed: number; filesTotal: number; contextTruncated: boolean;
}
export interface ClaudeStatus { required: boolean; hasToken: boolean }
export interface AskScope { organization: string; project: string; repository?: string }
export interface PrRef { organization: string; project: string; repository: string }

let unauthorizedHandler: (() => void) | null = null;
/** Called when any API call (other than the initial session check) returns 401, e.g. expired session. */
export const setUnauthorizedHandler = (fn: (() => void) | null) => { unauthorizedHandler = fn; };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError("Cannot reach the backend. Is it running on port 8000?", 0);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && path !== "/auth/me") unauthorizedHandler?.();
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (HTTP ${res.status})`;
    throw new ApiError(detail, res.status);
  }
  return body as T;
}

const qs = (params: Record<string, string>) => new URLSearchParams(params).toString();
const prQs = (r: PrRef) => qs({ organization: r.organization, project: r.project, repository: r.repository });
const prPath = (id: number, r: PrRef, sub = "") => `/api/azure/pull-requests/${id}${sub}?${prQs(r)}`;

export const api = {
  me: () => request<User>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  claudeStatus: () => request<ClaudeStatus>("/api/claude/status"),
  saveClaudeToken: (token: string) =>
    request<void>("/api/claude/token", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }),
  removeClaudeToken: () => request<void>("/api/claude/token", { method: "DELETE" }),
  organizations: () => request<Organization[]>("/api/azure/organizations"),
  projects: (organization: string) => request<Project[]>(`/api/azure/projects?${qs({ organization })}`),
  repositories: (organization: string, project: string) =>
    request<Repository[]>(`/api/azure/repositories?${qs({ organization, project })}`),
  pullRequests: (r: PrRef) => request<PullRequestSummary[]>(`/api/azure/pull-requests?${prQs(r)}`),
  pullRequest: (id: number, r: PrRef) => request<PullRequestDetail>(prPath(id, r)),
  changes: (id: number, r: PrRef) => request<Changes>(prPath(id, r, "/changes")),
  commits: (id: number, r: PrRef) => request<Commit[]>(prPath(id, r, "/commits")),
  comments: (id: number, r: PrRef) => request<CommentThread[]>(prPath(id, r, "/comments")),
  /** Ask about a PR (repository + pullRequestId), a repository, or the whole project. */
  ask: (scope: AskScope, pullRequestId: number | null, question: string) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...scope, pullRequestId: pullRequestId ?? undefined, question }),
    }),
};
