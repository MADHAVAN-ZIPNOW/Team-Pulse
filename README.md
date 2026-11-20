# Azure DevOps PR Assistant (local-only prototype)

Sign in with your own Microsoft account, pick an Azure DevOps PR, and ask Claude Code questions about it.
Claude Code analyzes the PR diff **and** your local checkout of the repository.

```
Browser (React)  ──cookie──►  FastAPI backend  ──Bearer (your Entra token)──►  Azure DevOps REST API
                                    │
                                    └──Claude Agent SDK──►  local `claude` CLI (your subscription), cwd = LOCAL_REPO_PATH
```

Everything runs on your machine. No cloud deployment, no database, no PAT, no Anthropic API key.

## 1. Prerequisites

- Python 3.11+ (3.12 recommended) and [uv](https://docs.astral.sh/uv/) (or plain pip)
- Node.js 20+
- **Claude Code CLI**, installed and logged in with your Claude subscription (`claude`, then `/login`)
  and Git (on Windows Claude Code's Bash tool needs Git for Windows)
- A local clone of the repository whose PRs you want to review
- An Azure DevOps organization connected to the same Entra tenant as your account

## 2. Register the Microsoft Entra ID application

Azure portal → **Microsoft Entra ID** → **App registrations** → **New registration**

1. **Name:** `PR Assistant (local)`
2. **Supported account types:** *Accounts in this organizational directory only (single tenant)*
3. **Redirect URI:** platform **Web**, value `http://localhost:8000/auth/callback` → **Register**
4. On the overview page copy **Application (client) ID** and **Directory (tenant) ID**.

Then configure:

| Where | What |
|---|---|
| **Authentication** → Web redirect URIs | `http://localhost:8000/auth/callback` (required). Optionally also `http://localhost:5173/auth/callback` — the Vite dev server proxies it to the backend, so either works as `AZURE_REDIRECT_URI`. |
| **Authentication** → Implicit grant and hybrid flows | Tick **ID tokens** (the app requests one; harmless with the code flow). |
| **Certificates & secrets** → New client secret | Copy the secret **value** immediately → `AZURE_CLIENT_SECRET`. |
| **API permissions** → Add a permission → **Azure DevOps** → Delegated → **user_impersonation** | Required. Then **Grant admin consent** if your tenant requires it. |

## 3. Redirect URIs and permissions (summary)

- Redirect URI (Web): `http://localhost:8000/auth/callback`  (optional extra: `http://localhost:5173/auth/callback`)
- Delegated permission: **Azure DevOps → user_impersonation** (scope `499b84ac-1321-427f-aa17-267ca6975798/user_impersonation`)
- OpenID scopes (`openid`, `profile`, `offline_access`) are added automatically by MSAL; no Graph permissions are needed.
- Client-credentials flow is **not** used; every Azure DevOps call runs as you.

Your Azure DevOps organization must also allow third-party/Entra OAuth apps (Organization settings → Policies).

## 4. Configure `.env`

```powershell
copy .env.example .env
```

| Variable | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_CLIENT_SECRET` | Client secret **value** |
| `AZURE_REDIRECT_URI` | `http://localhost:8000/auth/callback` (must match the registration exactly) |
| `FRONTEND_URL` | `http://localhost:5173` |
| `SESSION_SECRET` | random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SESSION_DIR`, `SESSION_TTL_DAYS` | where signed-in sessions are saved encrypted (default: temp folder) and days idle before sign-in is needed again (default 30). Use a Render persistent disk path to survive deploys. |
| `LOCAL_REPO_PATH` | absolute path of your local clone, e.g. `C:/src/my-repo` |
| `AZURE_DEVOPS_API_VERSION` | `7.1` |
| `BACKEND_PORT` | `8000` (the Vite proxy and redirect URI assume 8000) |

The backend starts even if `.env` is incomplete and warns about what is missing; `/api/health` reports it.

## 5. Run locally

**Backend** (terminal 1):

```powershell
cd backend
uv venv --python 3.12
uv pip install -r requirements.txt        # or: pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

**Frontend** (terminal 2):

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Run backend tests with `cd backend; .venv\Scripts\python -m pytest`.

## 6. How the Claude Code subscription is used

The backend uses the Python **Claude Agent SDK** (`claude-agent-sdk`), which spawns the local `claude` CLI
and talks to it. The CLI authenticates with the login you made via `claude` → `/login`, so usage counts against
your Claude subscription. No Anthropic API key or Console account is involved; the backend even blanks
`ANTHROPIC_API_KEY` for the child process so a stray key can't switch billing.

Claude Code runs with `cwd = LOCAL_REPO_PATH` and is restricted to **Read, Grep, Glob and Bash**:

- Editing/writing/web tools are disabled; `setting_sources=[]` ignores repo/user settings and hooks.
- A `PreToolUse` hook allows only a single simple read-only Bash command (`git log/show/diff/status/blame…`, `ls`, `cat`, `grep`, `rg`, `find` without `-exec/-delete`, …). Pipes, redirects, `;`, `&&`, `$()` and anything else are denied.
- PR text is treated as untrusted data (prompt-injection note added to the system prompt).

## 7. Test with a real Azure DevOps PR

1. Make sure the repo at `LOCAL_REPO_PATH` has the PR's source branch checked out (`git fetch` then `git checkout <source-branch>`), so Claude sees the code as the PR changes it.
2. Open <http://localhost:5173> → **Sign in with Microsoft** → approve consent.
3. Select organization → project → repository → an active PR.
4. Check **Overview**, **Files & diff**, **Commits**, **Comments**.
5. Click **Summarize this PR** or ask your own question (≤ 2000 chars). Expect 30–120 s for the first answer.

Quick API checks after signing in (the cookie lives in your browser, so use the UI or browser devtools; `curl` without a session returns 401): `GET /api/health` is public.

## Ask about anything in Azure DevOps (not just PRs)

Once an organization and project are selected you can chat - a repository or PR is optional. Claude gets
read-only tools that run with **your** sign-in (`mcp__azure__*`), so it looks things up instead of guessing:

| Tool | What Claude can do |
|---|---|
| `list_repositories`, `list_branches` | explore the project |
| `list_files`, `get_file` | read the **latest code** of any branch (images are shown to Claude) |
| `list_pull_requests`, `get_pull_request` | PRs with diffs, commits and review comments |
| `search_work_items`, `get_work_item` | tasks/bugs/stories: description, acceptance criteria, comments, links |
| `get_attachment` | screenshots (seen as images) and Word/PowerPoint/Excel/PDF attachments (read as text) |

Examples: "What tasks are in progress?", "Open the attachment on task 1804 and summarise it",
"Explain the structure of the latest code on main", "Does PR 267 match what task 1803 asks for?".
With a PR selected, the PR-review format is used for review questions; other questions are answered directly.
The local checkout (`LOCAL_REPO_PATH`) is optional: without it Claude uses only the Azure tools.
Read/Grep/Glob/Bash are confined to that folder (paths outside it are denied).

## Azure CLI sign-in (no app registration)

Set `AUTH_MODE=azure_cli` and click **Sign in with code**. The backend runs `az login --use-device-code` and then
uses `az account get-access-token` for Azure DevOps (token cached in memory until shortly before it expires).
Set `AZ_CLI_PATH` if `az` is not on PATH (`~/.azcli/Scripts/az.bat` is also detected on Windows).

## 8. Security notes

- Tokens live only in server memory (an MSAL token cache per session). The browser gets an HTTP-only, `SameSite=Lax`, `Secure` signed session-id cookie. `Secure` cookies work on `http://localhost` in Chrome/Edge/Firefox; set `COOKIE_SECURE=false` for Safari.
- OAuth: authorization-code flow + PKCE + `state` + `nonce` (MSAL), with state additionally bound to the browser via a signed cookie. State is single-use and expires after 10 minutes.
- Access tokens are refreshed automatically via the cached refresh token; logout clears the session.
- All inputs validated with Pydantic; question ≤ 2000 chars; ≤ 40 files, 12k chars/file, 100k chars total sent to Claude.
- Rate limits: 10 `/api/ask` per minute per session, 240 requests/minute overall. Errors never include stack traces or tokens. POST endpoints reject foreign `Origin` headers.

## 9. Known limitations

- **No streaming**: the UI shows a loading state until Claude Code finishes (up to 10 minutes).
- **Diffs are computed locally**: Azure DevOps has no unified-diff endpoint, so the backend fetches each file at the base and source commit and runs `difflib`. Binary/large (>1 MB) files are skipped; at most 60 files get diffs in the UI, 40 go to Claude.
- Claude analyzes whatever is **checked out** at `LOCAL_REPO_PATH`; if that is not the PR's branch, its view of surrounding code may differ from the PR.
- Sessions are in memory: restarting the backend signs you out. Single user, single process.
- Profile pictures aren't shown (that would need a separate Microsoft Graph token); initials are used.
- Conversations are not persisted and each question is independent (no multi-turn memory in Claude Code).
- Organization listing relies on the Azure DevOps profile/accounts API; if your org doesn't appear, check the Azure DevOps third-party OAuth policy and that your account is a member.
- The Claude Agent SDK / CLI is evolving; if a hook or option name changes, see `backend/app/claude/claude_code_service.py`.

## Deploying to Render (optional)

One Docker web service serves the UI and the API (`Dockerfile`, `render.yaml`). Claude Code runs inside the
container using your subscription through a long-lived token.

1. `claude setup-token` on your PC (sign in with your Claude account) and copy the token.
2. Push this folder to a Git repo and create a Render web service from it (Docker runtime), or apply `render.yaml`.
3. Note the service URL, `https://<service>.onrender.com`.
4. Entra app registration (single tenant) with Web redirect URI `https://<service>.onrender.com/auth/callback`,
   Azure DevOps `user_impersonation` permission, and a client secret.
5. Set the Render environment variables: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
   `AZURE_REDIRECT_URI`, `FRONTEND_URL`, `ALLOWED_USERS`, `CLAUDE_CODE_OAUTH_TOKEN` (and leave `AUTH_MODE` unset).

Notes: sessions are in memory, so keep one instance (a restart signs everyone out). `ALLOWED_USERS` limits who can
spend your subscription. Treat the token like a password and revoke it if it leaks. Do not use `AUTH_MODE=azure_cli` on a server.

### Each person uses their own Claude account (hosted)

Set `REQUIRE_USER_CLAUDE_TOKEN=true`. Each person then pastes their own token (`claude setup-token`) in a box above
the chat. The token lives only in that person's server-side session (memory), is never returned by the API, is passed
to Claude only for that person's questions (with a private, throw-away Claude config folder per question) and is
dropped on logout or restart. With this setting the shared `CLAUDE_CODE_OAUTH_TOKEN` is never used, so you can
delete it from the server.

### Switching accounts when a limit is reached

The owner can make teammates' accounts available by setting `SHARED_CLAUDE_TOKENS` on the server:

```
SHARED_CLAUDE_TOKENS=Abinaya=sk-ant-...;Monika=sk-ant-...
```

Each person then sees a **Claude account** switch above the chat (My own account / Shared: Abinaya / ...). The browser
only ever receives the labels, never the tokens. Only add a token with its owner's consent, and check that your Claude plan
and Anthropic's terms allow sharing it; questions asked on a shared account use that person's limits.

### One sign-in that also lets Claude use the Azure CLI as you (`AUTH_MODE=az_login`)

Each person signs in once, with a code (`az login --use-device-code` runs on the server in a private folder for that
person, restricted to your tenant). That login *is* the app sign-in, and Claude's `az` commands run as that person,
so there is no second sign-in and no Entra app registration or admin approval to maintain.

- `ALLOWED_USERS` and `AZURE_TENANT_ID` still decide who gets in; anyone else's sign-in folder is deleted at once.
- Claude may only run `az boards`, `az repos`, `az pipelines` and `az devops project list|show`, one plain command at a
  time, pinned to the selected organization, enforced by a hook before the command runs.
- `AZ_POLICY` sets what it may do: `read` (list/show/query), `write` (also create/update/add, never delete) or `full`.
- Logging out, expiry or a restart deletes the person's sign-in folder.
