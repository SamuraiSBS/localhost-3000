"""Environment configuration with no public default credentials."""

from dataclasses import dataclass, field
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATH = PROJECT_ROOT / "data" / "seed.json"


@dataclass(frozen=True)
class RuntimeConfig:
    source_path: Path = DEFAULT_SOURCE_PATH
    data_dir: Path = PROJECT_ROOT / ".state"
    login: str | None = None
    password_hash: str | None = field(default=None, repr=False)
    metrics_token: str | None = field(default=None, repr=False)
    public_origin: str = "http://127.0.0.1:8311"
    revision: str = "local"
    session_ttl_seconds: int = 28800
    max_snapshot_bytes: int = 10 * 1024 * 1024
    demo_enabled: bool = True
    oidc_enabled: bool = False
    oidc_issuer_url: str = ""
    oidc_metadata_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = field(default="", repr=False)
    oidc_redirect_uri: str = ""
    oidc_allowed_subjects: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            source_path=Path(os.environ.get("SOURCE_SNAPSHOT_PATH", str(DEFAULT_SOURCE_PATH))),
            data_dir=Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / ".state"))),
            revision=os.environ.get("APP_REVISION", "local"),
            login=os.environ.get("APP_LOGIN"),
            password_hash=os.environ.get("APP_PASSWORD_HASH"),
            metrics_token=os.environ.get("METRICS_TOKEN"),
            public_origin=os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8311"),
            demo_enabled=os.environ.get("DEMO_ENABLED", "true").lower() == "true",
            oidc_enabled=os.environ.get("OIDC_ENABLED", "false").lower() == "true",
            oidc_issuer_url=os.environ.get("OIDC_ISSUER_URL", ""),
            oidc_metadata_url=os.environ.get("OIDC_METADATA_URL", ""),
            oidc_client_id=os.environ.get("OIDC_CLIENT_ID", ""),
            oidc_client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
            oidc_redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
            oidc_allowed_subjects=tuple(x.strip() for x in os.environ.get("OIDC_ALLOWED_SUBJECTS", "").split(",") if x.strip()),
        )

    def require_credentials(self) -> None:
        """Protected product startup will call this when auth is implemented."""
        if not self.login or not self.password_hash or not self.metrics_token:
            raise ValueError("Set APP_LOGIN, APP_PASSWORD_HASH and METRICS_TOKEN locally")
