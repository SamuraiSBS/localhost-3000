"""Generate an optional private local Keycloak profile without printing secrets.

Existing environments are never overwritten. Realm templates contain env
placeholders only. Uses the existing private-file creation policy.
"""
import argparse
import getpass
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from configure_local import configure, private_permissions, read_env, _write_private_file

ROOT = Path(__file__).resolve().parents[1]
SUBJECT = "409921e4-c410-4c5e-86f3-e3a3816dc183"


def configure_oidc(directory, *, origin="http://127.0.0.1:8373", provider_origin="http://127.0.0.1:8390",
                   login="analyst", password=None, reuse_env=None, project="p1-b13", subnet=None):
    parsed = urlsplit(provider_origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Local Keycloak must use an exact http://127.0.0.1:PORT origin")
    target = Path(directory).resolve()
    if target.exists():
        raise FileExistsError("Choose a new private output directory; existing files are not overwritten")
    reused = read_env(reuse_env) if reuse_env else {}
    credentials = {
        "KC_BOOTSTRAP_ADMIN_USERNAME": reused.get("KC_BOOTSTRAP_ADMIN_USERNAME", "p1-admin"),
        "KC_BOOTSTRAP_ADMIN_PASSWORD": reused.get("KC_BOOTSTRAP_ADMIN_PASSWORD") or secrets.token_urlsafe(32),
        "P1_OIDC_CLIENT_SECRET": reused.get("P1_OIDC_CLIENT_SECRET") or secrets.token_urlsafe(32),
        "P1_OIDC_PASSWORD": reused.get("P1_OIDC_PASSWORD") or secrets.token_urlsafe(32),
    }
    env_path = configure(target, origin, login, password or secrets.token_urlsafe(32), project=project, ephemeral=True, subnet=subnet)
    template = json.loads((ROOT / "infra/keycloak/localhost3000-p1-realm.json").read_text(encoding="utf-8"))
    client = template["clients"][0]
    client["redirectUris"] = [origin + "/api/v1/auth/oidc/callback"]
    client["webOrigins"] = [origin]
    client["attributes"]["post.logout.redirect.uris"] = origin + "/api/v1/auth/oidc/logout-return"
    realm_path = target / "realm.json"
    _write_private_file(realm_path, json.dumps(template, ensure_ascii=False, indent=2) + "\n")
    private_permissions(env_path)
    additions = {**credentials, "APP_IMAGE": project + ":local", "OIDC_ENABLED": "true", "OIDC_CLIENT_ID": "localhost3000-panel",
        "OIDC_ISSUER_URL": provider_origin + "/realms/localhost3000-p1",
        "OIDC_METADATA_URL": provider_origin + "/realms/localhost3000-p1/.well-known/openid-configuration",
        "OIDC_CLIENT_SECRET": credentials["P1_OIDC_CLIENT_SECRET"],
        "OIDC_REDIRECT_URI": origin + "/api/v1/auth/oidc/callback", "OIDC_ALLOWED_SUBJECTS": SUBJECT,
        "OIDC_PROVIDER_ORIGIN": provider_origin, "OIDC_PROVIDER_PORT": str(parsed.port),
        "OIDC_REALM_FILE": realm_path.as_posix()}
    with env_path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in additions.items():
            if any(c in value for c in "\r\n'"):
                raise ValueError("Unsupported configuration value")
            handle.write(f"{key}='{value}'\n")
    private_permissions(env_path)
    return env_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".state/oidc")
    parser.add_argument("--origin", default="http://127.0.0.1:8373")
    parser.add_argument("--provider-origin", default="http://127.0.0.1:8390")
    parser.add_argument("--login", default="analyst")
    parser.add_argument("--project", default="p1-b13")
    parser.add_argument("--subnet")
    parser.add_argument("--reuse-env", help="Read existing task-owned secrets; never changes this file")
    args = parser.parse_args()
    password = getpass.getpass("Local panel password (not printed): ")
    path = configure_oidc(args.directory, origin=args.origin, provider_origin=args.provider_origin,
        login=args.login, password=password, reuse_env=args.reuse_env, project=args.project, subnet=args.subnet)
    print("Private configuration created:", path)
    print("Keycloak account: p1-marketer; password is only in the private P1_OIDC_PASSWORD entry.")
