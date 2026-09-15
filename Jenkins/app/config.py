from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv, set_key


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Jenkins/GitLab on *.apps.ge-healthcare.net present a certificate chain issued by
# the internal GE HealthCare corporate CA, which isn't in the public certifi trust
# store httpx ships with. local-setup already keeps that bundle alongside every
# other local tool - reuse it by default so this app works without manual .env
# setup, but only when the file is actually present (e.g. a different machine).
_CORP_CA_BUNDLE = Path(__file__).resolve().parent.parent.parent / "certs" / "ge-corp-ca-bundle.pem"


def _default_ca_bundle() -> str:
    return str(_CORP_CA_BUNDLE) if _CORP_CA_BUNDLE.is_file() else ""


def build_verify(verify_ssl: bool, ca_bundle: str) -> "bool | ssl.SSLContext":
    """httpx `verify` value that trusts the public CA store *and* an extra bundle.

    ssl.create_default_context(cafile=...) uses ONLY that file as trust anchors,
    dropping the system/public CA store - fine for a host whose chain relies on
    the corporate root (Jenkins), but it breaks a host validated by a public CA
    (GitLab), which then fails with "self-signed certificate in certificate
    chain" once the public roots are gone. Loading the bundle into a default
    context instead appends it, so both kinds of hosts verify.
    """
    if not verify_ssl:
        return False
    if not ca_bundle:
        return True
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_bundle)
    return context


@dataclass(frozen=True, slots=True)
class Settings:
    jenkins_user: str = ""
    jenkins_token: str = ""
    jenkins_verify_ssl: bool = True
    jenkins_ca_bundle: str = ""
    jenkins_poll_seconds: int = 15
    jenkins_max_retries: int = 2
    jenkins_retry_delay_seconds: int = 30
    jenkins_max_wait_minutes: int = 180
    jenkins_default_origin: str = "https://jenkins-dose.apps.ge-healthcare.net"

    gitlab_token: str = ""
    gitlab_default_origin: str = "https://gitlab.apps.ge-healthcare.net"
    gitlab_verify_ssl: bool = True
    gitlab_ca_bundle: str = ""

    host: str = "127.0.0.1"
    port: int = 8790
    request_timeout: float = 25.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            jenkins_user=os.getenv("JENKINS_USER", "").strip(),
            jenkins_token=os.getenv("JENKINS_API_TOKEN", "").strip(),
            jenkins_verify_ssl=_as_bool(os.getenv("JENKINS_VERIFY_SSL"), True),
            jenkins_ca_bundle=os.getenv("JENKINS_CA_BUNDLE", _default_ca_bundle()).strip(),
            jenkins_poll_seconds=max(5, int(os.getenv("JENKINS_POLL_SECONDS", "15"))),
            jenkins_max_retries=max(0, int(os.getenv("JENKINS_MAX_RETRIES", "2"))),
            jenkins_retry_delay_seconds=max(
                5, int(os.getenv("JENKINS_RETRY_DELAY_SECONDS", "30"))
            ),
            jenkins_max_wait_minutes=max(
                1, int(os.getenv("JENKINS_MAX_WAIT_MINUTES", "180"))
            ),
            jenkins_default_origin=os.getenv(
                "JENKINS_DEFAULT_ORIGIN", "https://jenkins-dose.apps.ge-healthcare.net"
            ).rstrip("/"),
            gitlab_token=os.getenv("GITLAB_TOKEN", "").strip(),
            gitlab_default_origin=os.getenv(
                "GITLAB_DEFAULT_ORIGIN", "https://gitlab.apps.ge-healthcare.net"
            ).rstrip("/"),
            gitlab_verify_ssl=_as_bool(os.getenv("GITLAB_VERIFY_SSL"), True),
            gitlab_ca_bundle=os.getenv("GITLAB_CA_BUNDLE", _default_ca_bundle()).strip(),
            host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            port=int(os.getenv("DASHBOARD_PORT") or "8790"),
        )

    @property
    def jenkins_configured(self) -> bool:
        return bool(self.jenkins_user and self.jenkins_token)

    @property
    def gitlab_configured(self) -> bool:
        return bool(self.gitlab_token)


def _save(env_path: Path, key: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(dotenv_path=str(env_path), key_to_set=key, value_to_set=value, quote_mode="always", encoding="utf-8")


def save_jenkins_credentials(env_path: Path, user: str, token: str) -> None:
    _save(env_path, "JENKINS_USER", user)
    _save(env_path, "JENKINS_API_TOKEN", token)


def save_gitlab_token(env_path: Path, token: str) -> None:
    _save(env_path, "GITLAB_TOKEN", token)


def save_poll_settings(
    env_path: Path,
    *,
    poll_seconds: int,
    max_retries: int,
    retry_delay_seconds: int,
) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(env_path), "JENKINS_POLL_SECONDS", str(poll_seconds), quote_mode="never", encoding="utf-8")
    set_key(str(env_path), "JENKINS_MAX_RETRIES", str(max_retries), quote_mode="never", encoding="utf-8")
    set_key(
        str(env_path),
        "JENKINS_RETRY_DELAY_SECONDS",
        str(retry_delay_seconds),
        quote_mode="never",
        encoding="utf-8",
    )
