"""Bounded, temporary workspaces for anonymous reviewers; no operator data is copied."""
from dataclasses import replace
from tempfile import TemporaryDirectory
from threading import Lock
import time
from types import SimpleNamespace

from starlette.datastructures import State

from app.auth import AuthError
from app.config import DEFAULT_SOURCE_PATH
from app.contracts import load_source
from app.evaluation import EvaluationCoordinator
from app.storage import SQLiteRepository
from app.workflow_storage import WorkflowStore


class DemoWorkspaces:
    def __init__(self, config):
        self.config = config
        self.entries = {}
        self.lock = Lock()

    def create(self, key):
        with self.lock:
            now = time.monotonic()
            for expired in [k for k, v in self.entries.items() if v.expires <= now]:
                self.entries.pop(expired).directory.cleanup()
            if len(self.entries) >= 24:
                raise AuthError(503, "demo_capacity", "Demo is busy. Try again later.")
            root = self.config.data_dir / "demo-workspaces"
            root.mkdir(parents=True, exist_ok=True)
            directory = TemporaryDirectory(prefix="reviewer-", dir=root)
            from pathlib import Path
            path = Path(directory.name)
            repository = SQLiteRepository(path / "app.sqlite3")
            coordinator = EvaluationCoordinator(repository)
            try:
                coordinator.start()
                coordinator.replace_snapshot(load_source(DEFAULT_SOURCE_PATH))
                workflow = WorkflowStore(path / "app.sqlite3")
                workflow.initialize()
            except BaseException:
                directory.cleanup()
                raise
            self.entries[key] = SimpleNamespace(
                directory=directory, expires=now + self.config.session_ttl_seconds,
                overrides=dict(repository=repository, coordinator=coordinator,
                    workflow_store=workflow, source_previews={},
                    config=replace(self.config, data_dir=path, source_path=DEFAULT_SOURCE_PATH)),
            )

    def application_for(self, application, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry is None or entry.expires <= time.monotonic():
                raise AuthError(401, "demo_expired", "Start a new demo session.")
            state = State({**application.state._state, **entry.overrides})
        return _ApplicationView(application, state)

    def close(self):
        with self.lock:
            for entry in self.entries.values():
                entry.directory.cleanup()
            self.entries.clear()

    def release(self, key):
        with self.lock:
            entry = self.entries.pop(key, None)
            if entry is not None:
                entry.directory.cleanup()


class _ApplicationView:
    def __init__(self, application, state):
        self.application, self.state = application, state

    def __getattr__(self, name):
        return getattr(self.application, name)
