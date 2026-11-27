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
export interface ClaudeStatus { required: boolean; hasToken: boolean; source: string; pool: string[]; activeAccount?: string | null; isAdmin?: boolean }
export interface ClaudeAccountUsage { name: string; active: boolean; requests: number; errors: number; rateLimited: number; inputTokens: number; outputTokens: number; costUsd: number; lastUsed: number | null; requestsLastHour: number; limits?: { fiveHour: ClaudeWindow | null; sevenDay: ClaudeWindow | null } | null }
export interface ClaudeWindow { usedPercent: number; resetsAt: number | null; status: string }
export interface ClaudeTotals { requests: number; tokens: number; cost: number; rateLimited: number; errorRate: number }
export interface ClaudeLiveAccount extends ClaudeTotals {
  name: string; pool: boolean; active: boolean; inputTokens: number; outputTokens: number; rateLimited15m: number; lastUsed: number | null
  status: "Healthy" | "Warning" | "Critical"; health: number; limits?: { fiveHour: ClaudeWindow | null; sevenDay: ClaudeWindow | null } | null
}
export interface ClaudeFeatureStat {
  name: string; calls: number; tokens: number; inputTokens: number; outputTokens: number; cost: number; share: number
  tokensPerCall: number; costPerCall: number; failRate: number; avgLatency: number; model: string
}
export interface ClaudeSwitch { ts: number; from: string | null; to: string | null; reason: string }
export interface ClaudeDashboard {
  range: string; totals: ClaudeTotals; previous: ClaudeTotals; active: string | null; since: number
  series: { ts: number; requests: number; cost: number; input: number; output: number }[]
  features: ClaudeFeatureStat[]
  autoSwitch: boolean; switchPercent: number; switchLog: ClaudeSwitch[]; mailTo: string[]
  featureModels: Record<string, string>; defaultModels: Record<string, string>; models: { id: string; label: string }[]
  accounts: ClaudeLiveAccount[]
  jobs: { total: number; ok: number; failed: number; running: number }
}
export interface ClaudeUsage {
  autoSwitch: boolean; switchPercent: number; switchLog: ClaudeSwitch[]; mailTo: string[]
  features: string[]; featureModels: Record<string, string>; defaultModels: Record<string, string>; models: { id: string; label: string }[]; active: string | null; isAdmin: boolean; accounts: ClaudeLiveAccount[] }
export interface AzStatus { enabled: boolean; policy: string; policyText: string; signedIn: boolean; user?: string | null; managedByLogin?: boolean }
export interface ClaudeModel { id: string; label: string; note: string }
export interface ClaudeModels { models: ClaudeModel[]; default: string }
export const DEFAULT_ORG: string = (import.meta.env.VITE_AZURE_ORG as string | undefined) || "dteksoftware";
export interface AskScope { organization: string; project?: string; repository?: string }
export interface PrRef { organization: string; project: string; repository: string }

export interface TrendNews { title: string; url: string }
export interface TrendItem {
  id: string; geo: string; countries: string[]; title: string; category: string; traffic: string; increase: string;
  summary: string; news: TrendNews[]; startedAt: string; endedAt: string | null;
  pos: number; volume: number; increaseValue: number; related: string[];
}
export interface TrendsMeta { countries: string[]; world: string[]; windows: number[]; cacheSeconds: number; now: string }
export interface TrendQuery {
  geo: string; hours: number; status: string; category: string; q: string; sort: string; limit: number; offset: number;
}
export interface TrendList { items: TrendItem[]; total: number; fetchedAt: string; stale: boolean; categories: string[] }
export interface TrendStats {
  active: number; newLastHour: number; endedLastHour: number;
  topTrend: { title: string; traffic: string } | null; risingTrend: { title: string; increase: string } | null;
  categories: { name: string; count: number }[];
}
export interface MeetingSummary { id: string; title: string; organization: string; project: string; created_at: string; ended_at: string | null; lines: number; actions: number; source: string; auto?: boolean; hasVideo?: boolean }
export interface MeetingLine { id: number; t: string; speaker: string; text: string; offset?: number | null; translation?: string; lang?: string; to?: string }
export interface MeetingItem { id: number; type: string; title: string; parent: number | null; url: string }
export interface MeetingAction {
  id: string; line_id: number | ""; at: string; status: "working" | "created" | "failed" | "undone" | "skipped"; summary: string;
  items: MeetingItem[]; error: string; project: string;
}
export interface MeetingFull { id: string; title: string; organization: string; project: string; created_at: string; ended_at: string | null; lines: MeetingLine[]; actions: MeetingAction[]; source: string; stage: string; stage_error: boolean; hasVideo?: boolean; result?: string }
export interface TeamsMeeting {
  id: string; subject: string; start: string; end: string; joinUrl: string; isOrganizer: boolean; organizer: string;
  state: "finished" | "live" | "upcoming"; processedId: string | null;
}
export interface AutoStatus {
  available: boolean; enabled: boolean; project?: string; organization?: string; enabledAt?: string | null; lastCheck?: string | null; lastError?: string; processed?: number;
}
export interface MeetingsStatus { enabled: boolean; speech: boolean; ffmpeg: boolean; teams: boolean; whisper?: boolean }
export interface RadarSource { source_type?: string; title?: string; url: string; published_or_updated?: string | null; key_finding?: string; credibility?: string }
export interface RadarProblem {
  rank: number; title: string; technology?: string; problem_type?: string; severity?: string; confidence?: string; evidence_strength?: string;
  domain?: string; people_asked?: string; community_answer?: string; problem_summary?: string; why_important_today?: string; symptoms?: string[]; root_cause?: string; affected_versions_or_services?: string;
  problem_sources?: RadarSource[]; solution_sources?: RadarSource[]; sources?: RadarSource[];
  recommended_action?: { summary?: string; steps?: string[]; code_or_config?: string | null; risks?: string[]; verification_steps?: string[] };
  alternative_workarounds?: { summary?: string; when_to_use?: string; source_url?: string }[];
}
export interface RadarReport {
  key: string; report_date: string; slot: string; generated_at?: string; daily_summary: string; topics_selected: string[]; problems: RadarProblem[];
  low_priority_watchlist?: { title?: string; technology?: string; reason_to_watch?: string; source_url?: string }[];
  sources_reviewed?: { url: string; title?: string; source_type?: string; relevance?: string }[]; tomorrows_focus?: string[];
  emailed_at?: string | null; email_error?: string; query?: string; domain?: string;
}
export interface RadarSearch { status: "running" | "done" | "error"; query: string; domain: string; report: RadarReport | null; error: string }
export interface RadarStatus {
  running: boolean; startedAt: string | null; error: string; timezone: string; watchlist: string; currentKey: string; hasCurrent: boolean; nextRun: string;
  domains: string[]; schedule: { slot: string; hour: number }[]; keys: { key: string; date: string; slot: string }[]; email: { on: boolean; to: string[] };
}
export interface RadarOverview { status: RadarStatus; report: RadarReport | null }
export interface ExcelLink { available: boolean; url: string }
export interface ChatEvent { type: "status" | "delta" | "error" | "done"; text: string }
export interface ChatBody { geo: string; title: string; question: string; history: { role: "user" | "assistant"; text: string }[]; deep: boolean }
export type TrendPastResult =
  | { status: "ready"; items: TrendItem[]; note: string; source: TrendNews | null; fromGoogle: boolean }
  | { status: "loading" }
  | { status: "error"; error: string };

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
  azStatus: () => request<AzStatus>("/api/az/status"),
  azLogin: () => request<{ code: string; url: string }>("/api/az/login", { method: "POST" }),
  azLogout: () => request<void>("/api/az/login", { method: "DELETE" }),
  claudeStatus: () => request<ClaudeStatus>("/api/claude/status"),
  claudeDashboard: (range: string, account: string) => request<ClaudeDashboard>(`/api/claude/dashboard?range=${range}&account=${encodeURIComponent(account)}`),
  setFeatureModel: (feature: string, model: string | null) =>
    request<void>("/api/claude/feature-model", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ feature, model }) }),
  setClaudeAuto: (enabled: boolean) =>
    request<void>("/api/claude/auto", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) }),
  claudeUsage: () => request<ClaudeUsage>("/api/claude/usage"),
  setActiveClaudeAccount: (account: string | null) =>
    request<void>("/api/claude/active", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ account }) }),
  claudeModels: () => request<ClaudeModels>("/api/claude/models"),
  saveClaudeToken: (token: string) =>
    request<void>("/api/claude/token", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }),
  setClaudeSource: (source: string) =>
    request<void>("/api/claude/source", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }),
  removeClaudeToken: () => request<void>("/api/claude/token", { method: "DELETE" }),
  trendsMeta: () => request<TrendsMeta>("/api/trends/meta"),
  trends: (p: TrendQuery) =>
    request<TrendList>(
      `/api/trends?${qs({ geo: p.geo, hours: String(p.hours), status: p.status, sort: p.sort, limit: String(p.limit), offset: String(p.offset),
        ...(p.category ? { category: p.category } : {}), ...(p.q ? { q: p.q } : {}) })}`,
    ),
  meetingsStatus: () => request<MeetingsStatus>("/api/meetings/status"),
  autoStatus: () => request<AutoStatus>("/api/meetings/auto"),
  setAuto: (enabled: boolean, organization: string, project: string) =>
    request<AutoStatus>("/api/meetings/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, organization, project }) }),
  radar: () => request<RadarOverview>("/api/radar"),
  radarSearch: (query: string, domain: string) =>
    request<{ id: string }>("/api/radar/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, domain }) }),
  radarSearchState: (id: string) => request<RadarSearch>(`/api/radar/search/${id}`),
  radarReport: (key: string) => request<RadarReport>(`/api/radar/${key}`),
  excelLink: () => request<ExcelLink>("/api/meetings/excel"),
  setExcelLink: (url: string) =>
    request<ExcelLink>("/api/meetings/excel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) }),
  teamsCalendar: () => request<TeamsMeeting[]>("/api/meetings/teams/calendar"),
  askMeeting: (id: string, question: string) =>
    request<{ answer: string }>(`/api/meetings/${id}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) }),
  fillExcelAgain: (id: string) => request<{ started: boolean }>(`/api/meetings/${id}/fill-excel`, { method: "POST" }),
  importTeams: (joinUrl: string, subject: string, organization: string, project: string, start = "", end = "") =>
    request<MeetingFull>("/api/meetings/teams/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ joinUrl, subject, organization, project, start, end }) }),
  importFile: (file: File, organization: string, project: string) =>
    request<MeetingFull>(`/api/meetings/import?${qs({ filename: file.name, organization, project })}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file }),
  translateMeeting: (id: string, target: "auto" | "ta" | "en") =>
    request<{ started: boolean }>(`/api/meetings/${id}/translate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target }) }),
  meetings: () => request<MeetingSummary[]>("/api/meetings"),
  meeting: (id: string) => request<MeetingFull>(`/api/meetings/${id}`),
  newMeeting: (title: string, organization: string, project: string) =>
    request<MeetingFull>("/api/meetings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, organization, project }) }),
  deleteMeeting: (id: string) => request<{ deleted: boolean }>(`/api/meetings/${id}`, { method: "DELETE" }),
  endMeeting: (id: string) => request<{ ended: boolean }>(`/api/meetings/${id}/end`, { method: "POST" }),
  setMeetingProject: (id: string, project: string) =>
    request<{ project: string }>(`/api/meetings/${id}/project`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project }) }),
  addMeetingLine: (id: string, speaker: string, text: string) =>
    request<MeetingLine>(`/api/meetings/${id}/lines`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ speaker, text }) }),
  undoMeetingAction: (id: string, actionId: string) => request<MeetingAction>(`/api/meetings/${id}/actions/${actionId}/undo`, { method: "POST" }),
  trendSeries: (geo: string, hours: number, title: string) =>
    request<{ t0: string | null; t1: string | null; values: number[] | null }>(`/api/trends/series?${qs({ geo, hours: String(hours), title })}`),
  trendPhoto: (title: string) => request<{ url: string | null; title: string | null; source: string | null; pending: boolean }>(`/api/trends/photo?${qs({ title })}`),
  trendStats: (geo: string, hours: number) => request<TrendStats>(`/api/trends/stats?${qs({ geo, hours: String(hours) })}`),
  refreshTrends: (geo: string, hours: number) =>
    request<{ fetchedAt: string; stale: boolean }>(`/api/trends/refresh?${qs({ geo, hours: String(hours) })}`, { method: "POST" }),
  /** Ask Claude about one trend. Events arrive as they happen: status (what it is doing), delta (answer text), done / error. */
  trendChat: async (body: ChatBody, onEvent: (e: ChatEvent) => void, signal: AbortSignal): Promise<void> => {
    const res = await fetch("/api/trends/chat", {
      method: "POST", credentials: "same-origin", signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      const j = await res.json().catch(() => null);
      throw new ApiError(typeof j?.detail === "string" ? j.detail : `Request failed (HTTP ${res.status})`, res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (chunk.startsWith("data: ")) {
          try { onEvent(JSON.parse(chunk.slice(6)) as ChatEvent); } catch { /* ignore a broken line */ }
        }
      }
    }
  },
  trendPast: (geo: string, from: string, to: string) =>
    request<TrendPastResult>(`/api/trends/past?${qs({ geo, from, to })}`, { method: "POST" }),
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
  ask: (scope: AskScope, pullRequestId: number | null, question: string, model?: string) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...scope, pullRequestId: pullRequestId ?? undefined, question, model: model || undefined }),
    }),
};
