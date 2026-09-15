from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv, set_key


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    group_url: str = "https://gitlab.apps.ge-healthcare.net/pia_restricted"
    token: str = ""
    enable_write_actions: bool = False
    host: str = "127.0.0.1"
    port: int = 8770
    cache_seconds: int = 90
    stale_days: int = 5
    request_timeout: float = 25.0
    verify_ssl: bool = True
    ca_bundle: str = ""
    code_review_root: str = r"C:\Users\250020392\project"
    code_review_timeout: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            group_url=os.getenv(
                "GITLAB_GROUP_URL",
                "https://gitlab.apps.ge-healthcare.net/pia_restricted",
            ).rstrip("/"),
            token=os.getenv("GITLAB_TOKEN", "").strip(),
            enable_write_actions=_as_bool(
                os.getenv("GITLAB_ENABLE_WRITE_ACTIONS"), False
            ),
            host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            port=int(os.getenv("DASHBOARD_PORT", "8765")),
            cache_seconds=max(10, int(os.getenv("DASHBOARD_CACHE_SECONDS", "90"))),
            stale_days=max(1, int(os.getenv("DASHBOARD_STALE_DAYS", "5"))),
            request_timeout=float(os.getenv("GITLAB_REQUEST_TIMEOUT", "25")),
            verify_ssl=_as_bool(os.getenv("GITLAB_VERIFY_SSL"), True),
            ca_bundle=os.getenv("GITLAB_CA_BUNDLE", "").strip(),
            code_review_root=os.getenv(
                "CODE_REVIEW_ROOT", r"C:\Users\250020392\project"
            ).strip(),
            code_review_timeout=max(60, int(os.getenv("CODE_REVIEW_TIMEOUT", "600"))),
        )

    @property
    def demo_mode(self) -> bool:
        return not bool(self.token)

    @property
    def gitlab_origin(self) -> str:
        parsed = urlparse(self.group_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def api_base(self) -> str:
        return f"{self.gitlab_origin}/api/v4"

    @property
    def group_path(self) -> str:
        return unquote(urlparse(self.group_url).path.strip("/"))


def save_gitlab_token(env_path: Path, token: str) -> None:
    """Persist the token without exposing it to the browser configuration API."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(
        dotenv_path=str(env_path),
        key_to_set="GITLAB_TOKEN",
        value_to_set=token,
        quote_mode="always",
        encoding="utf-8",
    )


def save_code_review_root(env_path: Path, root: str) -> None:
    """Persist the local folder where GitLab repositories are checked out."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(
        dotenv_path=str(env_path),
        key_to_set="CODE_REVIEW_ROOT",
        value_to_set=root,
        quote_mode="always",
        encoding="utf-8",
    )


def save_write_actions_setting(env_path: Path, enabled: bool) -> None:
    """Persist the independent write-action safety switch."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(
        dotenv_path=str(env_path),
        key_to_set="GITLAB_ENABLE_WRITE_ACTIONS",
        value_to_set="true" if enabled else "false",
        quote_mode="never",
        encoding="utf-8",
    )
