import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
