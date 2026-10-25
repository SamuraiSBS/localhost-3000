"""Loopback-only UI review with disposable seed data and random operator credentials."""
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pwdlib import PasswordHash
import uvicorn
from app.config import RuntimeConfig
from app.main import create_app

if __name__ == '__main__':
    config = RuntimeConfig(data_dir=Path('.state/redesign-preview'),
        login='local-review', password_hash=PasswordHash.recommended().hash(secrets.token_urlsafe(32)),
        metrics_token=secrets.token_urlsafe(32), public_origin='http://127.0.0.1:8397')
    uvicorn.run(create_app(config), host='127.0.0.1', port=8397, access_log=False)
