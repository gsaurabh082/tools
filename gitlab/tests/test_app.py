from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def client() -> TestClient:
    settings = Settings(token="", group_url="https://gitlab.example.test/my-group")
    return TestClient(create_app(settings))


def test_health_reports_demo_mode() -> None:
    with client() as test_client:
        response = test_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "demo",
        "group_path": "my-group",
    }


def test_dashboard_has_actionable_demo_data() -> None:
    with client() as test_client:
        response = test_client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "demo"
    assert payload["summary"]["needs_my_approval"] == 2
    assert len(payload["projects"]) >= 4
    assert all("project_path" in mr for mr in payload["merge_requests"])


def test_merge_request_filters() -> None:
    with client() as test_client:
        response = test_client.get(
            "/api/merge-requests",
            params={"attention": "needs_approval", "q": "analysis"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["iid"] == 44


def test_reviewer_readiness_reports_required_review_signals() -> None:
    with client() as test_client:
        response = test_client.get("/api/merge-requests/101/248/reviewer-readiness")
    assert response.status_code == 200
    payload = response.json()
    checks = {item["id"]: item for item in payload["checks"]}
    assert checks["jira"]["status"] == "pass"
    assert checks["description"]["status"] == "pass"
    assert checks["testing"]["status"] == "pass"
    assert checks["pipeline"]["status"] == "pass"
    assert checks["approvals"]["status"] == "blocker"
    assert any(comment["check_id"] == "approvals" for comment in payload["suggested_comments"])
    assert payload["summary"]["blocking_count"] >= 1


def test_reviewer_readiness_flags_missing_description_and_ticket() -> None:
    with client() as test_client:
        response = test_client.get("/api/merge-requests/101/251/reviewer-readiness")
    assert response.status_code == 200
    checks = {item["id"]: item for item in response.json()["checks"]}
    assert checks["jira"]["status"] == "blocker"
    assert checks["description"]["status"] == "blocker"
    assert checks["testing"]["status"] == "blocker"
    assert checks["pipeline"]["status"] == "warning"


def test_reviewer_readiness_requires_wiki_reference_for_docs_change() -> None:
    with client() as test_client:
        response = test_client.get("/api/merge-requests/103/47/reviewer-readiness")
    assert response.status_code == 200
    checks = {item["id"]: item for item in response.json()["checks"]}
    assert checks["documentation"]["status"] == "blocker"


def test_demo_mode_blocks_approval() -> None:
    with client() as test_client:
        response = test_client.post(
            "/api/merge-requests/101/248/approve",
            headers={"X-GitLab-Focus-Action": "approve"},
        )
    assert response.status_code == 409
    assert "demo" in response.json()["detail"].lower()


def test_suggested_comment_requires_confirmation_and_posts_when_enabled(monkeypatch) -> None:
    created: dict[str, object] = {}

    class FakeGitLabClient:
        def __init__(self, settings):
            assert settings.enable_write_actions is True

        async def create_note(self, project_id, iid, body):
            created.update(project_id=project_id, iid=iid, body=body)
            return {"id": 42}

        async def close(self):
            return None

    monkeypatch.setattr("app.service.GitLabClient", FakeGitLabClient)
    app = create_app(
        Settings(
            token="configured-token",
            enable_write_actions=True,
            group_url="https://gitlab.example.test/my-group",
        )
    )
    with TestClient(app) as test_client:
        missing_header = test_client.post(
            "/api/merge-requests/101/248/notes",
            json={"body": "Could you add the test evidence?"},
        )
        response = test_client.post(
            "/api/merge-requests/101/248/notes",
            json={"body": "Could you add the test evidence?"},
            headers={"X-GitLab-Focus-Action": "create-note"},
        )

    assert missing_header.status_code == 400
    assert response.status_code == 200
    assert response.json() == {"status": "created", "note_id": 42}
    assert created == {
        "project_id": 101,
        "iid": 248,
        "body": "Could you add the test evidence?",
    }


def test_connection_requires_confirmation_header(tmp_path) -> None:
    app = create_app(
        Settings(token="", group_url="https://gitlab.example.test/my-group"),
        env_path=tmp_path / ".env",
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/connection/token",
            json={"token": "glpat-example-token"},
        )
    assert response.status_code == 400
    assert not (tmp_path / ".env").exists()


def test_validated_token_is_saved_without_being_returned(tmp_path, monkeypatch) -> None:
    class FakeGitLabClient:
        def __init__(self, settings):
            assert settings.token == "glpat-example-token"

        async def current_user(self):
            return {"id": 7, "name": "Test User", "username": "test.user"}

        async def group(self):
            return {"id": 9, "name": "My Group", "full_path": "my-group"}

        async def close(self):
            return None

    monkeypatch.setattr("app.service.GitLabClient", FakeGitLabClient)
    env_path = tmp_path / ".env"
    app = create_app(
        Settings(token="", group_url="https://gitlab.example.test/my-group"),
        env_path=env_path,
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/connection/token",
            json={"token": "glpat-example-token"},
            headers={"X-GitLab-Focus-Action": "connect"},
        )
        config_response = test_client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["refresh_required"] is True
    assert "glpat-example-token" not in response.text
    assert "GITLAB_TOKEN='glpat-example-token'" in env_path.read_text(encoding="utf-8")
    assert config_response.json()["token_configured"] is True


def test_write_actions_can_be_enabled_and_persisted(tmp_path) -> None:
    env_path = tmp_path / ".env"
    app = create_app(
        Settings(
            token="configured-token",
            group_url="https://gitlab.example.test/my-group",
        ),
        env_path=env_path,
    )
    with TestClient(app) as test_client:
        response = test_client.put(
            "/api/config/write-actions",
            json={"enabled": True},
            headers={"X-GitLab-Focus-Action": "write-actions"},
        )
        config_response = test_client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["write_actions_enabled"] is True
    assert "GITLAB_ENABLE_WRITE_ACTIONS=true" in env_path.read_text(encoding="utf-8")
    assert config_response.json()["write_actions_enabled"] is True


def test_write_actions_cannot_be_enabled_without_token(tmp_path) -> None:
    app = create_app(
        Settings(token="", group_url="https://gitlab.example.test/my-group"),
        env_path=tmp_path / ".env",
    )
    with TestClient(app) as test_client:
        response = test_client.put(
            "/api/config/write-actions",
            json={"enabled": True},
            headers={"X-GitLab-Focus-Action": "write-actions"},
        )
    assert response.status_code == 409
