"""Delivery helper safety and revision are tested without Docker or public credentials."""
import hashlib
import importlib.util
import json
import os
import shutil
import stat
from pathlib import Path
import secrets
import zipfile
import pytest
from pwdlib import PasswordHash

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path():
    # Keep ACL tests away from pytest's numbered temp root on Windows.
    scratch = ROOT / ".state" / "delivery-test-fixtures" / secrets.token_hex(12)
    scratch.mkdir(parents=True)
    try:
        yield scratch
    finally:
        shutil.rmtree(scratch)


spec = importlib.util.spec_from_file_location("configure_local", ROOT / "scripts/configure_local.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_private_config_roundtrip_and_no_overwrite(tmp_path):
    password = secrets.token_urlsafe(32)
    target = tmp_path / "private"
    env = helper.configure(target, "http://127.0.0.1:8359", "operator", password, "delivery-test")
    values = helper.read_env(env)
    assert PasswordHash.recommended().verify(password, values["APP_PASSWORD_HASH"])
    assert values["METRICS_TOKEN"] == Path(values["METRICS_TOKEN_FILE"]).read_text()
    assert password not in env.read_text()
    assert not (target / "smoke-password").exists()
    original = env.read_bytes()
    with pytest.raises(FileExistsError):
        helper.configure(target, "http://127.0.0.1:8359", "other", password)
    assert env.read_bytes() == original


@pytest.mark.parametrize("origin", ["file:///tmp", "http://host/path", "http://host?query", "http://host#hash"])
def test_invalid_origin_does_not_write(tmp_path, origin):
    with pytest.raises(ValueError):
        helper.configure(tmp_path / "absent", origin, "operator", "private")
    assert not (tmp_path / "absent").exists()


def test_private_permissions_verified_for_all_generated_secrets(tmp_path):
    target = tmp_path / "private-ephemeral"
    helper.configure(target, "http://127.0.0.1:8359", "operator", secrets.token_urlsafe(32), "delivery-test", True)
    for path in [target, *(target / name for name in ("compose.env", "metrics-token", "smoke-password"))]:
        metadata = helper.private_permissions(path)
        if os.name == "nt":
            assert metadata["protected"]
            assert [entry["sid"] for entry in metadata["entries"]] == [metadata["user_sid"]]
            assert not any(entry["sid"] in {"S-1-1-0", "S-1-5-11", "S-1-5-32-545"} for entry in metadata["entries"])
        else:
            assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_acl_failure_writes_no_credentials(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("simulated ACL failure")
    monkeypatch.setattr(helper, "private_permissions", denied)
    target = tmp_path / "denied"
    with pytest.raises(PermissionError):
        helper.configure(target, "http://127.0.0.1:8359", "operator", "never-write-this")
    assert list(target.iterdir()) == []


def test_existing_nonempty_directory_is_preserved(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    unrelated = target / "unrelated.txt"
    unrelated.write_text("preserve")
    with pytest.raises(FileExistsError):
        helper.configure(target, "http://127.0.0.1:8359", "operator", "private")
    assert unrelated.read_text() == "preserve"
    assert not (target / "compose.env").exists()


package_spec = importlib.util.spec_from_file_location("package_delivery", ROOT / "scripts/package_delivery.py")
package = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(package)

LAB_MEMBERS = {
    "app/api/lab.py", "app/lab_contracts.py", "app/domain/lab.py",
    "app/domain/lab_case.py", "app/domain/lab_sensitivity.py", "app/domain/lab_solver.py",
    "app/domain/lab_source_diff.py", "app/domain/lab_worklist.py",
    "app/domain/renewal_calendar.py",
    "web/lab.html", "web/assets/lab.js", "web/assets/lab.css",
    "design/terminal-field.js", "design/terminal-field.css", "docs/design/THIRD_PARTY_NOTICES.md",
}

INTERNAL_REVIEW_EXCLUSIONS = {
    "docs/START_HERE.md", "docs/DECISION_LAB_PLAN.md", "docs/EXECUTE_DECISION_LAB.md",
    "docs/EXECUTE_PLAN.md", "docs/decision-lab-contract.md", "docs/decision-lab-fixtures.md",
    "docs/decision-lab-implementation.md", "docs/decision-lab-verification.md",
    "artifacts/acceptance/decision-lab/results.json", "artifacts/acceptance/decision-lab/wide.png",
    "artifacts/acceptance/decision-lab/submission-README.md",
}

EXTENSION_RUNTIME_MEMBERS = {
    "app/api/data_quality.py", "app/api/exports.py", "app/api/observations.py",
    "app/api/workflow.py", "app/data_quality.py", "app/export_csv.py",
    "app/observation_storage.py", "app/workflow_storage.py",
}


def test_decision_lab_exact_package_boundary():
    members = set(package.MEMBERS)
    assert len(members) == len(package.MEMBERS)
    assert LAB_MEMBERS <= members
    assert EXTENSION_RUNTIME_MEMBERS <= members
    assert not members & INTERNAL_REVIEW_EXCLUSIONS
    assert not any(name.startswith(("docs/coordination/", "artifacts/")) for name in members)
    assert all((package.ROOT / name).is_file() for name in LAB_MEMBERS | EXTENSION_RUNTIME_MEMBERS)
    assert {"app/main.py", "app/demo.py", "app/evaluation.py", "app/api/source.py", "openapi.json",
            "web/assets/api.js", "web/assets/shell.js", "web/customer.html",
            "web/assets/customer.js", "README.md", "docs/demo.md", "data/seed.json"} <= members
    assert {name for name in members if name.startswith("app/") and "lab" in name} == {
        name for name in LAB_MEMBERS if name.startswith("app/") and "lab" in name}
    for name in members:
        parts = Path(name).parts
        assert not set(parts) & {".state", ".agent-state", ".git", "tests", "scratch", "materials", "материалы"}
        assert not name.endswith((".jpg", ".jpeg", ".sqlite", ".db", ".zip"))
        if name.endswith('.woff2'):
            # Only the approved OFL Geist build outputs; proprietary source fonts stay excluded.
            assert name.startswith('web/assets/interface/geist-')
            assert 'web/assets/interface/licenses/fontsource-variable-geist.txt' in members
            assert 'web/assets/interface/licenses/fontsource-variable-geist-mono.txt' in members
        assert name != ".env" and "credentials" not in name
    assert package.OUTPUT.as_posix() == "artifacts/acceptance/decision-lab/submission.zip"
    assert package.MANIFEST.as_posix() == "artifacts/acceptance/decision-lab/package-results.json"


def test_package_is_exact_deterministic_and_hashes_every_member(tmp_path, monkeypatch):
    # Synthetic files only: never build the real submission before B21 acceptance.
    monkeypatch.setattr(package, "ROOT", tmp_path)
    for name in package.MEMBERS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic:" + name).encode())
    excluded = [".env", ".state/private.db", ".agent-state/claims.json", "scratch/local.txt",
                "photo_1.jpg", "материалы/source.json", "tests/private.py",
                "design/assets/fonts/pp-neue-machina-inktrap-variable.woff2",
                "artifacts/acceptance/b13-results.json", "credentials.json"]
    for name in excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must stay outside archive")
    def no_walk(*args, **kwargs):
        raise AssertionError("Package must use the exact allowlist")
    monkeypatch.setattr(Path, "rglob", no_walk)
    monkeypatch.setattr(Path, "glob", no_walk)
    first = package.build()
    archive_path, manifest_path = tmp_path / package.OUTPUT, tmp_path / package.MANIFEST
    archive_bytes, manifest_bytes = archive_path.read_bytes(), manifest_path.read_bytes()
    assert json.loads(manifest_bytes) == first
    assert first["task_id"] == "B22"
    assert first["archive"] == package.OUTPUT.as_posix()
    assert first["archive_sha256"] == hashlib.sha256(archive_bytes).hexdigest()
    assert first["archive_size_bytes"] == len(archive_bytes)
    assert first["member_count"] == len(package.MEMBERS)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == sorted(package.MEMBERS)
        assert not set(archive.namelist()) & set(excluded)
        for entry, info in zip(first["members"], archive.infolist(), strict=True):
            raw = archive.read(info.filename)
            assert entry == {"path": info.filename, "size_bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest()}
            assert info.date_time == package.ZIP_TIMESTAMP
            assert info.create_system == 3 and info.external_attr >> 16 == 0o100644
    # Source mtime and allowlist ordering cannot affect ZIP bytes or manifest.
    for name in package.MEMBERS:
        os.utime(tmp_path / name, (1700000000, 1700000000))
    monkeypatch.setattr(package, "MEMBERS", tuple(reversed(package.MEMBERS)))
    assert package.build() == first
    assert archive_path.read_bytes() == archive_bytes
    assert manifest_path.read_bytes() == manifest_bytes
    assert first["docker"] == first["ci"] == first["public_environment"] == "not_run"


@pytest.mark.parametrize("name", ["../secret", "/secret", "C:/secret", "C:secret",
                                  "folder/../secret", "folder/./secret", "folder//secret",
                                  "folder\\secret", ""])
def test_package_rejects_unsafe_paths_before_output(tmp_path, monkeypatch, name):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "MEMBERS", (name,))
    with pytest.raises(ValueError, match="unsafe_package_path"):
        package.build()
    assert not (tmp_path / package.OUTPUT).exists()
    assert not (tmp_path / package.MANIFEST).exists()


def test_package_missing_member_preserves_previous_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "MEMBERS", ("required.txt",))
    archive = tmp_path / package.OUTPUT
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"previous archive")
    with pytest.raises(ValueError, match="missing_or_unsafe_package_member:required.txt"):
        package.build()
    assert archive.read_bytes() == b"previous archive"
    assert not (tmp_path / package.MANIFEST).exists()


def test_package_rejects_symlink_ancestors(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "MEMBERS", ("linked/secret.txt",))
    # Portable simulation also runs on Windows accounts without symlink privileges.
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == tmp_path / "linked" or original(path))
    with pytest.raises(ValueError, match="missing_or_unsafe_package_member"):
        package.build()
    assert not (tmp_path / package.OUTPUT).exists()


def test_package_rejects_duplicate_members(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "MEMBERS", ("same.txt", "same.txt"))
    with pytest.raises(ValueError, match="duplicate_package_member"):
        package.build()
    assert not (tmp_path / package.OUTPUT).exists()
