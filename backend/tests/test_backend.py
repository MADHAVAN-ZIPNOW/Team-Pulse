import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth.microsoft_auth import AuthError, MicrosoftAuth
from app.azure_devops import models as m
from app.azure_devops.client import AzureDevOpsClient, AzureDevOpsError, build_url
from app.claude.claude_code_service import is_safe_bash
from app.config import get_settings
from app.schemas.ask import AskRequest
from app.services.pr_context import build_pr_context
from app.services.pull_requests import unified_diff
from app.services.session_store import PendingFlowStore, SessionStore, sign_value, unsign_value


# ---------------------------------------------------------------- OAuth state generation
class FakeMsalApp:
    def __init__(self):
        self.calls = 0

    def initiate_auth_code_flow(self, scopes, redirect_uri=None, **kw):
        self.calls += 1
        return {"auth_uri": f"https://login.example/authorize?state=s{self.calls}", "state": f"s{self.calls}", "code_verifier": "v"}


def make_auth(monkeypatch):
    settings = get_settings()
    flows = PendingFlowStore()
    auth = MicrosoftAuth(settings, SessionStore(60), flows)
    fake = FakeMsalApp()
    monkeypatch.setattr(auth, "_build_app", lambda: fake)
    return auth, flows


def test_login_state_is_unique_and_stored(monkeypatch):
    auth, flows = make_auth(monkeypatch)
    _, s1 = auth.start_login()
    _, s2 = auth.start_login()
    assert s1 != s2
    assert flows.pop(s1)["code_verifier"] == "v"
    assert flows.pop(s1) is None  # one-time use


def test_callback_rejects_state_mismatch(monkeypatch):
    auth, _ = make_auth(monkeypatch)
    _, state = auth.start_login()
    with pytest.raises(AuthError):
        auth.complete_login({"state": state, "code": "x"}, cookie_state="other")
    with pytest.raises(AuthError):
        auth.complete_login({"state": "unknown", "code": "x"}, cookie_state="unknown")


def test_cookie_signing_detects_tampering():
    signed = sign_value("abc", "k")
    assert unsign_value(signed, "k") == "abc"
    assert unsign_value(signed + "x", "k") is None
    assert unsign_value(signed, "other") is None


# ---------------------------------------------------------------- Azure DevOps URLs
def test_build_url_encodes_segments():
    url = build_url("my org", "git", "repositories", "repo/../x", "pullrequests", project="Proj A")
    assert url == "https://dev.azure.com/my%20org/Proj%20A/_apis/git/repositories/repo%2F..%2Fx/pullrequests"


def test_build_url_without_project():
    assert build_url("acme", "projects") == "https://dev.azure.com/acme/_apis/projects"


async def test_client_sends_bearer_and_retries_on_429():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"value": [{"id": "1", "name": "p"}]})

    async def token():
        return "tok"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AzureDevOpsClient(http, token, backoff_base=0.001)
        projects = await client.list_projects("acme")
    assert len(seen) == 2
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert seen[0].url.params["api-version"] == "7.1"
    assert projects == [{"id": "1", "name": "p"}]


async def test_client_maps_401_to_friendly_error():
    async def token():
        return "tok"

    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(AzureDevOpsError) as exc:
            await AzureDevOpsClient(http, token).list_projects("acme")
    assert exc.value.status_code == 401
    assert "tok" not in exc.value.message


# ---------------------------------------------------------------- PR context building
def sample_pr() -> m.PullRequestDetail:
    return m.parse_pr_detail(
        {
            "pullRequestId": 7,
            "title": "Fix login",
            "status": "active",
            "createdBy": {"displayName": "Ada"},
            "sourceRefName": "refs/heads/fix",
            "targetRefName": "refs/heads/main",
            "description": "desc",
            "reviewers": [{"displayName": "Bob", "vote": 10}],
        },
        latest_iteration_id=2,
    )


def make_changes(n_files: int, diff_size: int) -> m.ChangesResponse:
    files = [
        m.FileChange(path=f"/src/f{i}.py", change_type="edit", diff="+" + "x" * diff_size, additions=1)
        for i in range(n_files)
    ]
    return m.ChangesResponse(iteration_id=2, total_files=n_files, files=files)


def build(changes, **limits):
    args = dict(max_files=3, max_chars_per_file=50, max_chars_total=120, max_comments=5)
    args.update(limits)
    return build_pr_context(sample_pr(), [m.Commit(commit_id="abcdef123456", comment="fix it")], changes, [], **args)


def test_pr_context_contains_metadata_and_diff():
    ctx = build(make_changes(1, 10))
    prompt = ctx.to_prompt()
    assert "Title: Fix login" in prompt and "Bob (Approved)" in prompt
    assert "/src/f0.py" in prompt and "abcdef12 fix it" in prompt
    assert not ctx.truncated and ctx.files_included == 1


def test_pr_context_enforces_limits():
    ctx = build(make_changes(6, 500))
    prompt = ctx.to_prompt()
    assert ctx.truncated
    assert "[diff truncated" in prompt
    assert "/src/f5.py" not in prompt  # beyond max_files
    assert len(ctx.to_prompt()) == len(prompt)  # rendering is idempotent


def test_unified_diff_counts():
    diff, add, rem = unified_diff("a\nb\n", "a\nc\nd\n", "/f", "/f")
    assert (add, rem) == (2, 1) and "+c" in diff and "-b" in diff


# ---------------------------------------------------------------- request validation
def valid_body(**over):
    body = {"organization": "acme", "project": "P", "repository": "R", "pullRequestId": 5, "question": "Review it"}
    body.update(over)
    return body


def test_ask_request_accepts_valid_and_strips():
    req = AskRequest.model_validate(valid_body(question="  hi  "))
    assert req.question == "hi" and req.pull_request_id == 5


@pytest.mark.parametrize(
    "over",
    [
        {"question": "x" * 2001},
        {"question": "   "},
        {"pullRequestId": 0},
        {"organization": "a/b"},
        {"repository": ""},
        {"extra": "nope"},
    ],
)
def test_ask_request_rejects_invalid(over):
    with pytest.raises(ValidationError):
        AskRequest.model_validate(valid_body(**over))


def test_ask_requires_authentication():
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.post("/api/ask", json=valid_body()).status_code == 401
        assert client.get("/api/azure/organizations").status_code == 401
        assert client.get("/auth/me").status_code == 401


# ---------------------------------------------------------------- read-only bash guard
@pytest.mark.parametrize(
    "cmd,ok",
    [
        ("git log -n 5 --oneline", True),
        ("git diff main...HEAD", True),
        ("grep -rn foo src", True),
        ("ls -la", True),
        ("rm -rf /", False),
        ("git push origin main", False),
        ("git checkout .", False),
        ("cat a.txt > b.txt", False),
        ("ls; rm x", False),
        ("echo $(whoami)", False),
        ("find . -name x -delete", False),
        ("curl http://evil", False),
    ],
)
def test_bash_guard(cmd, ok):
    assert is_safe_bash(cmd)[0] is ok


# ---------------------------------------------------------------- general (non-PR) questions
def test_ask_request_without_pr_or_repo():
    req = AskRequest.model_validate({"organization": "acme", "project": "P", "question": "What tasks are open?"})
    assert req.repository is None and req.pull_request_id is None


def test_ask_request_pr_requires_repository():
    with pytest.raises(ValidationError):
        AskRequest.model_validate({"organization": "acme", "project": "P", "pullRequestId": 3, "question": "hi"})


# ---------------------------------------------------------------- tool confinement
def test_tool_confinement(tmp_path):
    from app.claude.claude_code_service import _check_tool

    root = tmp_path.resolve()
    (root / "a.txt").write_text("x")
    assert _check_tool("Read", {"file_path": str(root / "a.txt")}, root) is None
    assert _check_tool("Read", {"file_path": str(root.parent / "secret.txt")}, root)
    assert _check_tool("Read", {"file_path": "../secret.txt"}, root)
    assert _check_tool("Glob", {"pattern": "**/*.py"}, root) is None
    assert _check_tool("Glob", {"pattern": "../**/*.py"}, root)
    assert _check_tool("Grep", {"pattern": "x", "path": str(root.parent)}, root)
    assert _check_tool("Bash", {"command": "cat a.txt"}, root) is None
    assert _check_tool("Bash", {"command": f"cat {root.parent / 'secret.txt'}"}, root)
    assert _check_tool("Bash", {"command": "cat ~/.ssh/id_rsa"}, root)
    assert _check_tool("mcp__azure__get_file", {"path": "/x"}, root) is None
    assert _check_tool("Edit", {}, root)
    assert _check_tool("Read", {"file_path": "a.txt"}, None)  # no local repo -> no local tools


def test_attachment_text_extraction():
    import io
    import zipfile

    from app.services.attachments import extract_document_text

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:p><w:t>Hello</w:t></w:p><w:p><w:t>World</w:t></w:p>")
    kind, text = extract_document_text(buf.getvalue())
    assert kind == "docx" and "Hello" in text and "World" in text
    assert extract_document_text(b"plain text")[0] == "text"
    assert extract_document_text(b"\x00\x01\x02binary") is None


# ---------------------------------------------------------------- per-user Claude account
GOOD_TOKEN = "sk-ant-oat01-" + ("A1b2C3d4" * 12)[:95]  # real `setup-token` tokens are 108 characters


@pytest.mark.parametrize("token,ok", [(GOOD_TOKEN, True), ("  " + GOOD_TOKEN + "  ", True), ("sk-ant-short", False),
                                       (GOOD_TOKEN + "Store this", True), ("not-a-token", False), ("", False)])
def test_claude_token_validation(token, ok):
    from app.schemas.claude import ClaudeTokenRequest

    if ok:
        assert ClaudeTokenRequest(token=token).token == GOOD_TOKEN
    else:
        with pytest.raises(ValidationError):
            ClaudeTokenRequest(token=token)


def _authed_client(app, client):
    from app.services.session_store import UserProfile

    session = app.state.sessions.create(None, {}, UserProfile("Test User", "t@example.com", "tid", None))
    client.cookies.set("pra_session", sign_value(session.id, get_settings().session_secret))
    return session


def test_claude_token_lifecycle_never_echoed(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("REQUIRE_USER_CLAUDE_TOKEN", "true")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/claude/status").status_code == 401  # needs sign-in
        session = _authed_client(app, client)
        assert client.get("/api/claude/status").json() == {"required": True, "hasToken": False, "source": "own", "pool": []}
        assert client.put("/api/claude/token", json={"token": "bad"}).status_code == 422
        r = client.put("/api/claude/token", json={"token": GOOD_TOKEN})
        assert r.status_code == 204 and GOOD_TOKEN not in r.text
        status = client.get("/api/claude/status")
        assert status.json()["hasToken"] is True and GOOD_TOKEN not in status.text
        assert GOOD_TOKEN not in client.get("/auth/me").text
        assert session.claude_token == GOOD_TOKEN and GOOD_TOKEN not in repr(session)
        assert client.put("/api/claude/token", json={"token": GOOD_TOKEN}, headers={"Origin": "http://evil.com"}).status_code == 403
        assert client.delete("/api/claude/token").status_code == 204
        assert session.claude_token is None


def test_ask_blocked_without_own_token_when_required(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("REQUIRE_USER_CLAUDE_TOKEN", "true")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        _authed_client(app, client)
        r = client.post("/api/ask", json=valid_body())
        assert r.status_code == 400 and "Claude account" in r.json()["detail"]


def test_options_use_the_persons_own_token():
    from pathlib import Path

    from app.claude.claude_code_service import build_options

    own = build_options(None, oauth_token=GOOD_TOKEN, config_dir="/tmp/x")
    assert own.env["CLAUDE_CODE_OAUTH_TOKEN"] == GOOD_TOKEN and own.env["CLAUDE_CONFIG_DIR"] == "/tmp/x"
    assert own.env["ANTHROPIC_API_KEY"] == ""
    shared = build_options(Path.cwd().resolve())
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in shared.env


# ---------------------------------------------------------------- shared accounts / switching
POOL_TOKEN = "sk-ant-oat01-" + "Zy9X8w7V" * 6


def test_shared_token_parsing(monkeypatch):
    from app.config import _parse_shared_tokens

    parsed = _parse_shared_tokens(f"Abinaya={POOL_TOKEN};bad entry;Own={POOL_TOKEN};Monika Sri={POOL_TOKEN}\nX=sk-short")
    assert [label for label, _ in parsed] == ["Abinaya", "Monika Sri"]  # malformed + reserved 'own' skipped


def test_switching_accounts_and_effective_token(monkeypatch):
    from app.main import create_app
    from app.routes.claude_routes import effective_claude_token

    monkeypatch.setenv("REQUIRE_USER_CLAUDE_TOKEN", "true")
    monkeypatch.setenv("SHARED_CLAUDE_TOKENS", f"Abinaya={POOL_TOKEN}")
    get_settings.cache_clear()
    app = create_app()
    settings = get_settings()
    with TestClient(app) as client:
        session = _authed_client(app, client)
        st = client.get("/api/claude/status")
        assert st.json() == {"required": True, "hasToken": False, "source": "own", "pool": ["Abinaya"]}
        assert POOL_TOKEN not in st.text                                   # only labels are ever sent
        assert effective_claude_token(session, settings) is None
        assert client.put("/api/claude/source", json={"source": "Nobody"}).status_code == 422
        assert client.put("/api/claude/source", json={"source": "Abinaya"}).status_code == 204
        assert effective_claude_token(session, settings) == POOL_TOKEN     # limit hit? switch to a shared account
        client.put("/api/claude/token", json={"token": GOOD_TOKEN})
        client.put("/api/claude/source", json={"source": "own"})
        assert effective_claude_token(session, settings) == GOOD_TOKEN     # and back to your own
        assert client.put("/api/claude/source", json={"source": "Abinaya"}, headers={"Origin": "http://evil.com"}).status_code == 403


def test_token_paste_is_forgiving_and_explains_login_codes():
    from app.schemas.claude import ClaudeTokenRequest, normalize_claude_token

    wrapped = GOOD_TOKEN[:40] + "  \r\n " + GOOD_TOKEN[40:]
    assert normalize_claude_token(wrapped) == GOOD_TOKEN                                  # terminal wrap / padding
    assert normalize_claude_token(f"export CLAUDE_CODE_OAUTH_TOKEN={GOOD_TOKEN}") == GOOD_TOKEN
    assert normalize_claude_token(f'"{GOOD_TOKEN}"\nStore this token securely.') == GOOD_TOKEN  # glued trailing text
    code = "HDBm4Jm01eAAdpWGJfbnZnNNKzuUv8BExg1kcUXtvLcEKo1I#3fXCsWFbDCLKumGU7PSgXe_lZ-CE-eqX4IiUcFVwLjE"
    with pytest.raises(ValidationError) as exc:
        ClaudeTokenRequest(token=code)
    assert "one-time login code" in str(exc.value)
    with pytest.raises(ValidationError):
        ClaudeTokenRequest(token="hello world")


# ---------------------------------------------------------------- az CLI policy (Claude acting as the signed-in person)
ORG = "https://dev.azure.com/dteksoftware"


@pytest.mark.parametrize(
    "cmd,policy,ok",
    [
        (f"az boards work-item show --id 1803 --org {ORG} --output json", "read", True),
        (f"az boards query --wiql SELECT --org {ORG}", "read", True),
        (f"az repos pr list --repository R --project P --organization {ORG}", "read", True),
        (f"az boards work-item show --id 1 --org={ORG}", "read", True),
        (f"az boards work-item update --id 1 --state Closed --org {ORG}", "read", False),   # change needs write
        (f"az boards work-item update --id 1 --state Closed --org {ORG}", "write", True),
        (f"az boards work-item create --title T --type Task --org {ORG}", "write", True),
        (f"az boards work-item delete --id 1 --yes --org {ORG}", "write", False),            # delete needs full
        (f"az boards work-item delete --id 1 --yes --org {ORG}", "full", True),
        (f"az boards work-item delete --id 1 --destroy --org {ORG}", "write", False),
        (f"az boards work-item relation remove --id 1 --org {ORG}", "write", False),
        ("az boards work-item show --id 1", "read", False),                                    # org must be given
        ("az boards work-item show --id 1 --org https://dev.azure.com/othercompany", "read", False),
        (f"az login --use-device-code --org {ORG}", "full", False),                            # not an allowed group
        (f"az account get-access-token --org {ORG}", "full", False),
        (f"az rest --method delete --url https://x --org {ORG}", "full", False),
        (f"az devops invoke --area wit --org {ORG}", "full", False),
        (f"az devops project list --org {ORG}", "read", True),
        (f"az boards work-item show --id 1 --org {ORG} --debug", "read", False),               # --debug leaks tokens
        (f"az boards work-item show --id 1 --org {ORG}; rm -rf /", "full", False),
        (f"az boards work-item show --id 1 --org {ORG} | cat", "full", False),
        (f"az boards work-item show --id $(whoami) --org {ORG}", "full", False),
        (f"ls -la", "full", False),
    ],
)
def test_az_policy(cmd, policy, ok):
    from app.services.az_policy import check_az_command

    allowed, reason = check_az_command(cmd, "dteksoftware", policy)
    assert allowed is ok, reason


def test_hook_routes_bash_through_az_policy_only_when_enabled():
    from app.claude.claude_code_service import _check_tool
    from app.services.az_policy import AzContext

    az = AzContext(config_dir="/tmp/az-user-x", org="dteksoftware", policy="read")
    show = {"command": f"az boards work-item show --id 1 --org {ORG}"}
    assert _check_tool("Bash", show, None, az) is None
    assert _check_tool("Bash", {"command": f"az boards work-item delete --id 1 --org {ORG}"}, None, az)
    assert _check_tool("Bash", {"command": "cat /etc/passwd"}, None, az)       # no local repo: only az passes
    assert _check_tool("Bash", show, None, None)                               # az disabled: Bash stays blocked
    opts_on = __import__("app.claude.claude_code_service", fromlist=["build_options"]).build_options(None, az=az)
    assert opts_on.env["AZURE_CONFIG_DIR"] == "/tmp/az-user-x" and "Bash" in opts_on.allowed_tools


def test_az_status_and_login_require_signin_and_enabled(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("AZ_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/az/status").status_code == 401
        _authed_client(app, client)
        st = client.get("/api/az/status").json()
        assert st["enabled"] is False and st["signedIn"] is False
        assert client.post("/api/az/login").status_code == 404                 # disabled -> no login possible


# ---------------------------------------------------------------- AUTH_MODE=az_login: the app sign-in IS the az login
def _az_login_app(monkeypatch, allowed="a@example.com"):
    from app.main import create_app
    from app.services import azure_cli

    monkeypatch.setenv("AUTH_MODE", "az_login")
    monkeypatch.setenv("COOKIE_SECURE", "false")  # the test client talks plain http
    monkeypatch.setenv("ALLOWED_USERS", allowed)
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    get_settings.cache_clear()
    started = {}

    async def fake_start(config_dir=None, tenant=None):
        started["dir"], started["tenant"] = config_dir, tenant
        return {"code": "ABC123XYZ", "url": "https://microsoft.com/devicelogin"}

    monkeypatch.setattr(azure_cli, "start_device_login", fake_start)
    return create_app(), azure_cli, started


def test_az_login_flow_creates_session_with_per_person_az_folder(monkeypatch):
    app, azure_cli, started = _az_login_app(monkeypatch)
    state = {"user": None}

    async def fake_user(config_dir=None):
        return state["user"]

    monkeypatch.setattr(azure_cli, "current_user", fake_user)
    with TestClient(app) as client:
        assert get_settings().az_enabled is True                                  # implied by az_login
        r = client.post("/auth/az/start")
        assert r.status_code == 200 and r.json()["code"] == "ABC123XYZ"
        assert started["tenant"] == "tenant-123"                                  # restricted to our directory
        assert client.get("/auth/az/status").json()["signedIn"] is False         # code not entered yet
        assert client.get("/auth/me").status_code == 401
        state["user"] = {"name": "a@example.com", "email": "A@Example.com", "tenantId": "tenant-123"}
        done = client.get("/auth/az/status")
        assert done.json()["signedIn"] is True
        assert client.get("/auth/me").json()["email"] == "a@example.com"
        session = next(iter(app.state.sessions._sessions.values()))
        assert session.az_dir == started["dir"] and session.az_user == "a@example.com"   # Claude's az runs as this person
        assert client.get("/api/az/status").json()["managedByLogin"] is True


def test_az_login_rejects_people_not_allowed_or_from_other_tenants(monkeypatch):
    import os

    app, azure_cli, started = _az_login_app(monkeypatch, allowed="a@example.com")

    async def outsider(config_dir=None):
        return {"name": "x", "email": "someone@else.com", "tenantId": "tenant-123"}

    monkeypatch.setattr(azure_cli, "current_user", outsider)
    with TestClient(app) as client:
        client.post("/auth/az/start")
        assert os.path.isdir(started["dir"])
        r = client.get("/auth/az/status")
        assert r.status_code == 403 and client.get("/auth/me").status_code == 401
        assert not os.path.exists(started["dir"])                                 # their sign-in is wiped from disk

    async def other_tenant(config_dir=None):
        return {"name": "a", "email": "a@example.com", "tenantId": "another-tenant"}

    monkeypatch.setattr(azure_cli, "current_user", other_tenant)
    with TestClient(app) as client:
        client.post("/auth/az/start")
        assert client.get("/auth/az/status").status_code == 403


def test_logout_wipes_the_persons_az_folder(monkeypatch):
    import os

    app, azure_cli, started = _az_login_app(monkeypatch)

    async def ok_user(config_dir=None):
        return {"name": "a", "email": "a@example.com", "tenantId": "tenant-123"}

    monkeypatch.setattr(azure_cli, "current_user", ok_user)
    with TestClient(app) as client:
        client.post("/auth/az/start")
        client.get("/auth/az/status")
        assert os.path.isdir(started["dir"])
        assert client.post("/auth/logout").status_code == 204
        assert not os.path.exists(started["dir"])


def test_az_start_not_available_in_other_modes(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("AUTH_MODE", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.post("/auth/az/start").status_code == 404
