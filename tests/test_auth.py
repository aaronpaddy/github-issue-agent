from types import SimpleNamespace

import pytest
from github.GithubException import UnknownObjectException

from src.config import Settings
from src.github import auth as auth_module
from src.github.auth import GitHubAuthError, resolve_auth


def make_settings(**overrides) -> Settings:
    base = {"github_token": None, "github_app_id": None, "github_app_private_key_path": None}
    return Settings(_env_file=None, **{**base, **overrides})


def test_falls_back_to_the_personal_token_and_generic_identity():
    result = resolve_auth(make_settings(github_token="pat-123"), "o/r")
    assert result.token == "pat-123"
    assert result.commit_name == "Issue Agent"
    assert "personal" in result.identity


def test_errors_when_no_credentials_are_configured():
    with pytest.raises(GitHubAuthError, match="No GitHub credentials"):
        resolve_auth(make_settings(), "o/r")


def test_errors_when_the_private_key_file_is_missing(tmp_path):
    settings = make_settings(github_app_id=1, github_app_private_key_path=str(tmp_path / "nope.pem"))
    with pytest.raises(GitHubAuthError, match="private key not found"):
        resolve_auth(settings, "o/r")


def fake_github_api(monkeypatch, installation_error=None):
    calls = {}

    class FakeIntegration:
        def __init__(self, auth):
            calls["auth"] = auth

        def get_repo_installation(self, owner, name):
            calls["repo"] = (owner, name)
            if installation_error:
                raise installation_error
            return SimpleNamespace(id=42, raw_data={"app_slug": "issue-agent"})

        def get_access_token(self, installation_id):
            calls["installation_id"] = installation_id
            return SimpleNamespace(token="inst-token")

    class FakeGithub:
        def __init__(self, auth):
            calls["user_lookup_token"] = auth

        def get_user(self, login):
            calls["bot_login"] = login
            return SimpleNamespace(id=999)

    fake_auth = SimpleNamespace(AppAuth=lambda app_id, key: ("app", app_id, key), Token=lambda t: t)
    monkeypatch.setattr(auth_module, "GithubIntegration", FakeIntegration)
    monkeypatch.setattr(auth_module, "Github", FakeGithub)
    monkeypatch.setattr(auth_module, "Auth", fake_auth)
    return calls


def test_uses_the_github_app_installation_and_bot_identity(tmp_path, monkeypatch):
    pem = tmp_path / "app.pem"
    pem.write_text("PRIVATE KEY")
    calls = fake_github_api(monkeypatch)
    settings = make_settings(github_app_id=7, github_app_private_key_path=str(pem), github_token="ignored")

    result = resolve_auth(settings, "aaronpaddy/expense-splitter")

    assert result.token == "inst-token"
    assert result.commit_name == "issue-agent[bot]"
    assert result.commit_email == "999+issue-agent[bot]@users.noreply.github.com"
    assert calls["repo"] == ("aaronpaddy", "expense-splitter")
    assert calls["installation_id"] == 42
    assert calls["auth"] == ("app", 7, "PRIVATE KEY")


def test_explains_when_the_app_is_not_installed_on_the_repo(tmp_path, monkeypatch):
    pem = tmp_path / "app.pem"
    pem.write_text("PRIVATE KEY")
    fake_github_api(monkeypatch, installation_error=UnknownObjectException(404, {}, {}))
    settings = make_settings(github_app_id=7, github_app_private_key_path=str(pem))

    with pytest.raises(GitHubAuthError, match="not installed on o/r"):
        resolve_auth(settings, "o/r")
