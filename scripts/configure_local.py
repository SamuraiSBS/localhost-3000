"""Create private Compose configuration; never print credentials or overwrite files."""
import argparse
import getpass
import ipaddress
import json
import os
import subprocess
from pathlib import Path
import secrets
from urllib.parse import urlsplit


# Pass paths as JSON over stdin, never interpolate them into PowerShell source.
_WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$request = ($input | Out-String) | ConvertFrom-Json
$path = [string]$request.path
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$item = Get-Item -LiteralPath $path -Force
if ($request.restrict) {
    if ($item.PSIsContainer) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    } else {
        $acl = New-Object System.Security.AccessControl.FileSecurity
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
    }
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule($rule)
    if ($item.PSIsContainer) { [System.IO.Directory]::SetAccessControl($path, $acl) }
    else { [System.IO.File]::SetAccessControl($path, $acl) }
}
if ($item.PSIsContainer) { $actual = [System.IO.Directory]::GetAccessControl($path) }
else { $actual = [System.IO.File]::GetAccessControl($path) }
$entries = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
    @{sid=$_.IdentityReference.Value; inherited=$_.IsInherited; access=$_.AccessControlType.ToString(); rights=$_.FileSystemRights.ToString()}
})
@{user_sid=$sid.Value; owner_sid=$actual.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; protected=$actual.AreAccessRulesProtected; entries=$entries} | ConvertTo-Json -Depth 4 -Compress
"""


def private_permissions(path, *, restrict=False):
    """Apply/verify current-user-only Windows DACL or POSIX owner-only mode.

    Only metadata is returned. A failed ACL change/inspection raises before any
    credentials can be written; chmod alone is never trusted on Windows.
    """
    path = Path(path)
    if os.name != "nt":
        expected = 0o700 if path.is_dir() else 0o600
        if restrict:
            path.chmod(expected)
        if path.stat().st_mode & 0o777 != expected:
            raise PermissionError("Private POSIX permissions could not be verified")
        return {"mode": oct(expected)}
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_ACL_SCRIPT],
        input=json.dumps({"path": str(path.resolve()), "restrict": restrict}),
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise PermissionError("Private Windows ACL could not be applied or inspected")
    try:
        metadata = json.loads(result.stdout)
        entries = metadata["entries"]
        user = metadata["user_sid"]
        valid = (metadata["protected"] and metadata["owner_sid"] == user and
                 len(entries) == 1 and entries[0]["sid"] == user and
                 entries[0]["access"] == "Allow" and not entries[0]["inherited"] and
                 entries[0]["rights"] == "FullControl")
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise PermissionError("Private Windows ACL verification failed")
    return metadata


def _write_private_file(path, value):
    # Directory protection precedes creation; the empty file DACL is verified
    # again before the first secret byte is written.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        private_permissions(path, restrict=True)
        handle.write(value)
    private_permissions(path)


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("'\"")
    return values


def configure(directory, origin, login, password, project="localhost-3000", ephemeral=False, subnet=None):
    from pwdlib import PasswordHash
    target = Path(directory).resolve()
    if any(c in target.as_posix() for c in "'\r\n"):
        raise ValueError("Configuration directory cannot contain quotes/newlines")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Provide an absolute origin without path/query/fragment")
    if not login or any(c in login for c in "'\r\n") or not password:
        raise ValueError("Nonempty login/password required; login cannot contain quotes/newlines")
    if not project or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in project):
        raise ValueError("Use a lowercase Compose project name")
    if subnet is not None:
        ipaddress.ip_network(subnet)
    env_path = target / "compose.env"
    if target.exists() and any(target.iterdir()):
        raise FileExistsError("Configuration already exists; preserve it or choose another directory")
    target.mkdir(parents=True, exist_ok=True)
    private_permissions(target, restrict=True)
    token = secrets.token_urlsafe(48)
    token_path = target / "metrics-token"
    _write_private_file(token_path, token)
    values = {"COMPOSE_PROJECT_NAME": project, "BIND_ADDRESS": "127.0.0.1",
              "HTTP_PORT": str(parsed.port or (443 if parsed.scheme == "https" else 80)),
              "SITE_ADDRESS": ":80", "PUBLIC_ORIGIN": origin, "APP_LOGIN": login,
              "APP_PASSWORD_HASH": PasswordHash.recommended().hash(password),
              "METRICS_TOKEN": token, "METRICS_TOKEN_FILE": token_path.as_posix()}
    if subnet is not None:
        values["COMPOSE_SUBNET"] = subnet
    _write_private_file(env_path, "".join(key + "='" + value + "'\n" for key, value in values.items()))
    if ephemeral:
        password_path = target / "smoke-password"
        _write_private_file(password_path, password)
    return env_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".state/local")
    parser.add_argument("--origin", default="http://127.0.0.1:8080")
    parser.add_argument("--project", default="localhost-3000")
    parser.add_argument("--subnet", help="Optional verified free subnet for this project only")
    parser.add_argument("--ephemeral", action="store_true", help="Generate private test-only credentials and smoke-password file")
    args = parser.parse_args()
    login = "local-" + secrets.token_hex(6) if args.ephemeral else input("Operator login: ").strip()
    password = secrets.token_urlsafe(32) if args.ephemeral else getpass.getpass("Operator password: ")
    if not args.ephemeral and password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    path = configure(args.directory, args.origin, login, password, args.project, args.ephemeral, args.subnet)
    print("Private configuration created:", path, "(credentials omitted)")


if __name__ == "__main__":
    main()
