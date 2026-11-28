import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setenv("CLAUDE_STATE_FILE", str(tmp_path / "claude_control.json"))  # remembered admin choices never leak between tests
    monkeypatch.setenv("SHARED_CLAUDE_TOKENS", "")  # a developer's own .env must not change what the tests see
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
