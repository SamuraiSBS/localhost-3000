"""Optional OIDC auth code flow; temporary secrets stay on the server.

The single-process MVP deliberately fails pending flows closed on restart.
Established Authlib / joserfc code handles PKCE, signatures and OIDC claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import re
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth

from app.config import RuntimeConfig

PREFIX = "/api/v1/auth/oidc"
FLOW_COOKIE = "oidc_flow"
FLOW_SECONDS = 300
OPAQUE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PUBLIC_ROUTES = frozenset(("GET", PREFIX + suffix) for suffix in
    ("/config", "/start", "/callback", "/logout", "/logout-return"))


class OIDCFailure(Exception):
    """Fixed public error code; never a provider response or token."""


def safe_destination(value: str | None) -> str:
    value = value or ""
    return value if re.fullmatch(r"/customers(?:/[1-9][0-9]*)?", value) or value == "/settings" else "/customers"


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OIDC URLs must be absolute HTTP(S) URLs without credentials/query/fragment")
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def _permitted_url(value: str, *, internal: bool = False) -> None:
    scheme, host, port = _origin(value)
    if scheme == "https":
        return
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local and not (internal and host == "keycloak" and port == 8080):
        raise ValueError("OIDC HTTP is permitted only for loopback or the local Keycloak service")


def validate_oidc_config(config: RuntimeConfig) -> None:
    if type(config.oidc_enabled) is not bool:
        raise ValueError("OIDC_ENABLED must be a boolean")
    if not config.oidc_enabled:
        return
    _permitted_url(config.oidc_issuer_url)
    _permitted_url(config.oidc_metadata_url or config.oidc_issuer_url, internal=True)
    if config.oidc_issuer_url.endswith("/"):
        raise ValueError("OIDC_ISSUER_URL must match the issuer exactly without a trailing slash")
    if config.oidc_redirect_uri != config.public_origin + PREFIX + "/callback":
        raise ValueError("OIDC_REDIRECT_URI must be the exact same-origin OIDC callback")
    if not config.oidc_client_id or not config.oidc_client_secret or not config.oidc_allowed_subjects:
        raise ValueError("Set OIDC_CLIENT_ID, OIDC_CLIENT_SECRET and OIDC_ALLOWED_SUBJECTS")
    if any(not isinstance(s, str) or not s or len(s) > 255 for s in config.oidc_allowed_subjects):
        raise ValueError("OIDC_ALLOWED_SUBJECTS must contain exact nonempty subject identifiers")


@dataclass(frozen=True)
class Pending:
    kind: str
    browser_hash: str
    expires_at: float
    data: dict


class PendingFlows:
    """Bounded single-use transactions, atomically consumed after browser binding."""
    def __init__(self, *, clock=time.monotonic, capacity=256):
        self.clock = clock
        self.capacity = capacity
        self._items: dict[str, Pending] = {}
        self._lock = Lock()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    def issue(self, kind: str, browser: str, data: dict) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            self._items = {k: v for k, v in self._items.items() if v.expires_at > now}
            if len(self._items) >= self.capacity:
                raise OIDCFailure("provider_unavailable")
            self._items[self._hash(token)] = Pending(kind, self._hash(browser), now + FLOW_SECONDS, data)
        return token

    def consume(self, token: str | None, browser: str | None, kind: str) -> dict | None:
        if not token or not browser or not OPAQUE.fullmatch(token) or not OPAQUE.fullmatch(browser):
            return None
        with self._lock:
            key = self._hash(token)
            item = self._items.get(key)
            if item is None or item.kind != kind or not hmac.compare_digest(item.browser_hash, self._hash(browser)):
                return None
            del self._items[key]
            return item.data if item.expires_at > self.clock() else None


class OIDCService:
    def __init__(self, config: RuntimeConfig):
        validate_oidc_config(config)
        self.config = config
        self.pending = PendingFlows()

    async def client(self):
        """Fresh bounded discovery; startup and primary login need no IdP request."""
        if not self.config.oidc_enabled:
            raise OIDCFailure("disabled")
        metadata_url = self.config.oidc_metadata_url or self.config.oidc_issuer_url + "/.well-known/openid-configuration"
        try:
            async with httpx2.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = await client.get(metadata_url)
                response.raise_for_status()
                metadata = response.json()
            if metadata.get("issuer") != self.config.oidc_issuer_url:
                raise ValueError("issuer_mismatch")
            public_origin = _origin(self.config.oidc_issuer_url)
            origins = {public_origin, _origin(metadata_url)}
            for name in ("authorization_endpoint", "token_endpoint", "jwks_uri", "end_session_endpoint"):
                endpoint = metadata[name]
                _permitted_url(endpoint, internal=True)
                if _origin(endpoint) not in origins:
                    raise ValueError("unexpected_endpoint_origin")
                if name in {"authorization_endpoint", "end_session_endpoint"} and _origin(endpoint) != public_origin:
                    raise ValueError("unexpected_browser_endpoint")
            if "RS256" not in metadata.get("id_token_signing_alg_values_supported", []):
                raise ValueError("unsupported_signing_algorithm")
            # Restrict algorithms independently of the provider's offered set.
            metadata["id_token_signing_alg_values_supported"] = ["RS256"]
            oauth = OAuth()
            return oauth.register("keycloak", client_id=self.config.oidc_client_id,
                client_secret=self.config.oidc_client_secret,
                client_kwargs={"scope": "openid", "code_challenge_method": "S256", "timeout": 5,
                    "trust_env": False, "follow_redirects": False}, **metadata)
        except Exception:
            raise OIDCFailure("provider_unavailable") from None

    async def authorize(self, browser: str, destination: str) -> str:
        client = await self.client()
        data = {"nonce": secrets.token_urlsafe(32), "code_verifier": secrets.token_urlsafe(48),
            "destination": safe_destination(destination)}
        state = self.pending.issue("login", browser, data)
        try:
            result = await client.create_authorization_url(self.config.oidc_redirect_uri,
                state=state, nonce=data["nonce"], code_verifier=data["code_verifier"])
            return result["url"]
        except Exception:
            self.pending.consume(state, browser, "login")
            raise OIDCFailure("provider_unavailable") from None

    async def authenticate(self, data: dict, code: str) -> None:
        try:
            client = await self.client()
            token = await client.fetch_access_token(redirect_uri=self.config.oidc_redirect_uri,
                code=code, code_verifier=data["code_verifier"])
            claims = await client.parse_id_token(token, nonce=data["nonce"], leeway=30,
                claims_options={"iss": {"essential": True, "value": self.config.oidc_issuer_url},
                    "aud": {"essential": True, "value": self.config.oidc_client_id}})
            # Explicit checks complement Authlib's signature/iss/aud/exp/iat/azp/at_hash/nonce checks.
            if not claims or not hmac.compare_digest(str(claims.get("nonce", "")), data["nonce"]):
                raise OIDCFailure("invalid_response")
            if claims.get("sub") not in self.config.oidc_allowed_subjects:
                raise OIDCFailure("access_denied")
        except OIDCFailure:
            raise
        except Exception:
            raise OIDCFailure("invalid_response") from None

    async def logout_url(self, browser: str) -> str:
        try:
            client = await self.client()
            state = self.pending.issue("logout_return", browser, {})
            result = await client.create_logout_url(post_logout_redirect_uri=self.config.public_origin + PREFIX + "/logout-return",
                client_id=self.config.oidc_client_id, state=state)
            return result["url"]
        except Exception:
            raise OIDCFailure("logout_incomplete") from None
