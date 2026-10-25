import asyncio
from dataclasses import replace
import secrets
import time

from authlib.integrations.starlette_client import OAuth
from joserfc import jwt
from joserfc.jwk import RSAKey
import pytest

from app.config import RuntimeConfig
from app.oidc import OIDCFailure, OIDCService, PendingFlows, safe_destination, validate_oidc_config

ISSUER = "https://idp.example.test/realms/local"
CLIENT = "panel"
SUBJECT = "allowed-subject"
NONCE = "unit-test-nonce"
KEY = RSAKey.generate_key(2048, parameters={"kid": "unit-key"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "unit-key"})


def configuration(**changes):
    return replace(RuntimeConfig(public_origin="http://127.0.0.1:8373", oidc_enabled=True,
        oidc_issuer_url=ISSUER, oidc_client_id=CLIENT, oidc_client_secret="unit-secret",
        oidc_redirect_uri="http://127.0.0.1:8373/api/v1/auth/oidc/callback",
        oidc_allowed_subjects=(SUBJECT,)), **changes)


def test_disabled_has_no_network_or_optional_credential_requirement():
    validate_oidc_config(RuntimeConfig())
    OIDCService(RuntimeConfig())


@pytest.mark.parametrize("change", [
    {"oidc_allowed_subjects": ()}, {"oidc_client_secret": ""},
    {"oidc_redirect_uri": "https://elsewhere.test/callback"},
    {"oidc_issuer_url": "http://remote-idp.test/realm"},
    {"oidc_issuer_url": ISSUER + "/"}, {"oidc_issuer_url": ISSUER + "?token=bad"},
    {"oidc_metadata_url": "http://untrusted-host:8080/.well-known/openid-configuration"},
])
def test_configuration_fails_closed(change):
    with pytest.raises(ValueError):
        validate_oidc_config(configuration(**change))


def test_pending_single_use_kind_browser_binding_and_expiry():
    clock = [100.0]
    pending = PendingFlows(clock=lambda: clock[0], capacity=1)
    browser = secrets.token_urlsafe(32)
    token = pending.issue("login", browser, {"nonce": "private"})
    assert pending.consume(token, secrets.token_urlsafe(32), "login") is None
    assert pending.consume(token, browser, "logout_ticket") is None
    with pytest.raises(OIDCFailure):
        pending.issue("login", browser, {})
    assert pending.consume(token, browser, "login") == {"nonce": "private"}
    assert pending.consume(token, browser, "login") is None
    expired = pending.issue("login", browser, {})
    clock[0] += 301
    assert pending.consume(expired, browser, "login") is None


@pytest.mark.parametrize("destination", ["//evil.test/", "https://evil.test", "/customers?next=bad", "/api/v1/docs", "/customers/0"])
def test_no_open_redirect(destination):
    assert safe_destination(destination) == "/customers"


@pytest.mark.parametrize("change", [
    {"nonce": None}, {"nonce": "other"}, {"nonce": None, "nonce_supported": False},
    {"iss": "https://evil.test"}, {"aud": "another-client"}, {"azp": "another-client"},
    {"aud": [CLIENT, "another-client"], "azp": None},
    {"exp": 1}, {"iat": int(time.time()) + 3600}, {"sub": "not-allowed"},
    {"exp": None}, {"signature": "wrong"},
])
def test_real_signature_and_oidc_claims_reject_invalid_token(monkeypatch, change):
    with pytest.raises(OIDCFailure):
        asyncio.run(_authenticate(monkeypatch, change))


def test_real_signature_and_oidc_claims_accept_allowed_subject(monkeypatch):
    asyncio.run(_authenticate(monkeypatch, {}))


async def _authenticate(monkeypatch, change):
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": SUBJECT, "iat": int(time.time()),
        "exp": int(time.time()) + 300, "nonce": NONCE, "azp": CLIENT}
    change = dict(change)
    key = OTHER_KEY if change.pop("signature", None) else KEY
    for name, value in change.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    encoded = jwt.encode({"alg": "RS256", "kid": "unit-key"}, claims, key)
    oauth = OAuth()
    client = oauth.register("test", client_id=CLIENT, client_secret="unit-secret", issuer=ISSUER,
        id_token_signing_alg_values_supported=["RS256"], jwks={"keys": [KEY.as_dict(private=False)]})
    async def fetch_access_token(**kwargs):
        assert kwargs["code_verifier"] == "unit-verifier"
        return {"id_token": encoded, "access_token": "unit-access-token"}
    monkeypatch.setattr(client, "fetch_access_token", fetch_access_token)
    service = OIDCService(configuration())
    async def trusted_client():
        return client
    monkeypatch.setattr(service, "client", trusted_client)
    await service.authenticate({"nonce": NONCE, "code_verifier": "unit-verifier"}, "unit-code")
