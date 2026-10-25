"""Delivery helper safety and revision are tested without Docker or public credentials."""
import importlib.util
import os
import stat
from pathlib import Path
import secrets
import pytest
from pwdlib import PasswordHash

ROOT = Path(__file__).resolve().parents[2]
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
