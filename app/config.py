"""Environment configuration with no public default credentials."""

from dataclasses import dataclass, field
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATH = PROJECT_ROOT / "материалы" / "data" / "data.json"


@dataclass(frozen=True)
class RuntimeConfig:
    source_path: Path = DEFAULT_SOURCE_PATH
    data_dir: Path = PROJECT_ROOT / ".state"
    login: str | None = None
    password_hash: str | None = field(default=None, repr=False)
    metrics_token: str | None = field(default=None, repr=False)
    public_origin: str = "http://127.0.0.1:8311"
    session_ttl_seconds: int = 28800
    max_snapshot_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            source_path=Path(os.environ.get("SOURCE_SNAPSHOT_PATH", str(DEFAULT_SOURCE_PATH))),
            data_dir=Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / ".state"))),
            login=os.environ.get("APP_LOGIN"),
            password_hash=os.environ.get("APP_PASSWORD_HASH"),
            metrics_token=os.environ.get("METRICS_TOKEN"),
            public_origin=os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8311"),
        )

    def require_credentials(self) -> None:
        """Protected product startup will call this when auth is implemented."""
        if not self.login or not self.password_hash or not self.metrics_token:
            raise ValueError("Set APP_LOGIN, APP_PASSWORD_HASH and METRICS_TOKEN locally")
