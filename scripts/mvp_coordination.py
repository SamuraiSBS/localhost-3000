"""Local, cooperative MVP coordination. No agent spawning or product execution."""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
import uuid


ACTIVE = {"reserved", "bound", "in_progress", "awaiting_review", "changes_requested", "blocked"}
CLOSED = {"accepted", "released", "cancelled"}
READ_ONLY = {"status", "next", "check"}
HANDOFF_EVIDENCE_KIND = "mvp_orchestrator_handoff_authorization"
RECOVERY_EVIDENCE_KIND = "mvp_orchestrator_recovery_authorization"
SNAPSHOT_EVIDENCE_KIND = "mvp_separate_workspace_snapshot"
SNAPSHOT_AUTH_SOURCE = "direct_user_reply_in_current_chat"
SNAPSHOT_RECOVERY_AUTH_SOURCE = "direct_user_reply_in_current_chat"
SNAPSHOT_RECOVERY_EVIDENCE_KIND = "mvp_snapshot_recovery_proposal"
WORKSPACE_DELTA_RECOVERY_AUTHORIZATION = "Разработать reviewed recovery — расширить scope на отдельный проверенный механизм координатора, сохранив исходный snapshot."
SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION = WORKSPACE_DELTA_RECOVERY_AUTHORIZATION
TARGET = re.compile(r"^/root(?:/[a-z][a-z0-9_]*)*$")
RUNTIME_KEYS = {"status", "evidence"}
PROTECTED = {".git", ".agent-state", ".state", ".agents", ".codex", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
SAFE_ENV = {".env.example", ".env.production.example"}


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise Refusal(code, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_json", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(payload):
    return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(Refusal("invalid_json", f"Nonfinite JSON: {value}")))


def read_json(path):
    return decode_json(path.read_bytes())


def now():
    return datetime.now(timezone.utc)


def iso():
    return now().isoformat(timespec="microseconds").replace("+00:00", "Z")


def date(value):
    require(isinstance(value, str), "invalid_evidence", "Timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "invalid_evidence", "Timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def fresh(value):
    age = (now() - date(value)).total_seconds()
    require(-30 <= age <= 300, "stale_evidence", "Evidence must be observed within five minutes, not in the future")


def uuid_text(value):
    require(isinstance(value, str), "invalid_identity", "Actual native run UUID required")
    try:
        require(str(uuid.UUID(value)) == value.lower(), "invalid_identity", "Canonical run UUID required")
    except ValueError as exc:
        raise Refusal("invalid_identity", "Actual native run UUID required") from exc


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def no_reparse(path):
    for current in (path, *path.parents):
        if current.exists() or current.is_symlink():
            details = current.lstat()
            require(not stat.S_ISLNK(details.st_mode) and not (getattr(details, "st_file_attributes", 0) & 0x400),
                    "unsafe_path", f"Symlink/reparse path is unsupported: {current}")


class Coordinator:
    def __init__(self, root):
        candidate = Path(os.path.abspath(root))
        no_reparse(candidate)
        self.root = candidate.resolve(strict=True)
        top = Path(self.git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()).resolve()
        git_dir = Path(self.git("rev-parse", "--absolute-git-dir").decode().strip()).resolve()
        require(top == self.root and common == git_dir, "workspace_mismatch", "v1 requires the primary shared Git checkout root, no worktrees")
        self.identity = str(self.root)
        self.git_identity = str(common)
        self.directory = self.root / ".agent-state" / "mvp-coordination"
        self.state_path = self.directory / "claims.json"
        no_reparse(self.state_path)

    def git(self, *args):
        result = subprocess.run(["git", "-c", f"safe.directory={self.root.as_posix()}", "-C", str(self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        require(result.returncode == 0, "git_error", result.stderr.decode("utf-8", "replace").strip() or "Git inspection failed")
        return result.stdout

    def path(self, value, *, evidence=False, inventory=False):
        require(isinstance(value, str), "unsafe_path", "Path must be a string")
        value = value.replace("\\", "/")
        parts = value.split("/")
        require(value and not value.startswith("/") and not re.search(r'[<>:"|?*\x00-\x1f]', value), "unsafe_path", "Exact relative file path required")
        require(all(part and part not in {".", ".."} and not part.endswith((".", " ")) for part in parts), "unsafe_path", "Ambiguous path segment")
        for part in parts:
            require(not re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I), "unsafe_path", "Windows device path rejected")
            require(part.lower() not in PROTECTED, "protected_path", "Git, state, configuration and dependency paths are protected")
            require(not part.lower().startswith(".env") or part.lower() in SAFE_ENV, "protected_path", "Secret environment paths are protected")
        require(Path(value).suffix.lower() not in {".pem", ".key", ".p12"}, "protected_path", "Secret key paths are protected")
        if not evidence and not inventory:
            require(parts[0].casefold() != "материалы", "protected_path", "Original organizer materials are read only")
        target = self.root.joinpath(*parts)
        no_reparse(target)
        require(target.resolve().is_relative_to(self.root), "unsafe_path", "Path escapes workspace")
        require(not target.is_dir(), "unsafe_path", "Scope must name a file, not a directory")
        return value

    def manifest(self, files):
        result = {}
        for name in files:
            target = self.root / name
            no_reparse(target)
            if not target.exists():
                result[name] = {"kind": "absent"}
                continue
            before = target.stat()
            require(stat.S_ISREG(before.st_mode), "unsafe_path", f"Not a regular file: {name}")
            payload = target.read_bytes()
            after = target.stat()
            require((before.st_mtime_ns, before.st_size, before.st_ino) == (after.st_mtime_ns, after.st_size, after.st_ino),
                    "unstable_file", f"File changed while hashing: {name}")
            result[name] = {"kind": "file", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
                            "executable": bool(before.st_mode & stat.S_IXUSR)}
        return result

    def exact_manifest(self, files):
        """Return a finite, portable path/mode/hash/size snapshot manifest."""
        current = self.manifest(files)
        result = []
        for name in files:
            entry = current[name]
            require(entry.get("kind") == "file", "snapshot_missing_file", f"Snapshot file must exist: {name}")
            result.append({"path": name, "mode": "100755" if entry["executable"] else "100644",
                           "sha256": entry["sha256"], "size": entry["size"]})
        return result

    def inventory(self):
        names = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        safe = []
        for raw in names:
            if not raw:
                continue
            name = os.fsdecode(raw)
            try:
                safe.append(self.path(name, inventory=True))
            except Refusal as exc:
                if exc.code == "protected_path":
                    continue
                raise
        return self.manifest(sorted(set(safe)))

    def dirty(self):
        records = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
        result, index = set(), 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            require(len(record) >= 4, "git_error", "Malformed Git status")
            result.add(os.fsdecode(record[3:]).replace("\\", "/"))
            if b"R" in record[:2] or b"C" in record[:2]:
                require(index < len(records) and records[index], "git_error", "Malformed Git rename status")
                result.add(os.fsdecode(records[index]).replace("\\", "/"))
                index += 1
            require(b"U" not in record[:2] and record[:2] not in {b"AA", b"DD"}, "git_busy", "Resolve unmerged Git paths explicitly")
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            marker_path = self.git("rev-parse", "--git-path", marker).decode().strip()
            require(not (self.root / marker_path).exists(), "git_busy", "Pending Git operation; no automatic reset or abort")
        return result

    def index_digest(self):
        return hashlib.sha256(self.git("ls-files", "--stage", "-z")).hexdigest()

    def load_plan(self):
        backlog = read_json(self.root / "docs" / "backlog.json")
        require(isinstance(backlog, dict) and isinstance(backlog.get("tasks"), list), "invalid_plan", "Backlog requires tasks")
        definitions = {key: value for key, value in backlog.items() if key not in {"updated_on", "product_status", "planning_status", "tasks"}}
        tasks = {}
        for item in backlog["tasks"]:
            require(isinstance(item, dict) and re.fullmatch(r"B\d{2}", item.get("id", "")), "invalid_plan", "Stable Bxx task IDs required")
            require(item["id"] not in tasks and isinstance(item.get("depends_on"), list), "invalid_plan", "Duplicate task or missing dependencies")
            tasks[item["id"]] = {key: value for key, value in item.items() if key not in RUNTIME_KEYS}
        visited, visiting = set(), set()

        def visit(task_id):
            require(task_id in tasks, "invalid_plan", f"Missing dependency: {task_id}")
            require(task_id not in visiting, "invalid_plan", "Dependency cycle")
            if task_id not in visited:
                visiting.add(task_id)
                for dep in tasks[task_id]["depends_on"]:
                    visit(dep)
                visiting.remove(task_id)
                visited.add(task_id)

        for task_id in tasks:
            visit(task_id)
        definitions["tasks"] = list(tasks.values())
        contracts = {}
        for entry in backlog.get("contract_documents", []):
            name = self.path("docs/" + entry)
            contracts[name] = self.manifest([name])[name]
            require(contracts[name]["kind"] == "file", "invalid_plan", f"Missing contract: {name}")
        return {"hash": digest({"definitions": definitions, "contracts": contracts}), "tasks": tasks,
                "task_hashes": {key: digest(value) for key, value in tasks.items()}, "contracts": contracts}

    def empty(self):
        return {"schema_version": 1, "root": self.identity, "git_common_dir": self.git_identity, "revision": 0,
                "orchestrator": None, "attempts": [], "gates": {}, "snapshots": [],
                "snapshot_recoveries": [], "snapshot_recovery_amendments": [],
                "snapshot_recovery_continuations": [],
                "workspace_delta_recoveries": [], "workspace_delta_recovery_amendments": [], "events": []}

    def read(self):
        no_reparse(self.state_path)
        if not self.state_path.exists():
            return self.empty()
        try:
            state = read_json(self.state_path)
            require(isinstance(state, dict) and state.get("schema_version") == 1 and type(state.get("revision")) is int,
                    "corrupt_state", "Unsupported/corrupt ledger schema")
            require(state.get("root") == self.identity and state.get("git_common_dir") == self.git_identity,
                    "workspace_mismatch", "Ledger belongs to another workspace")
            require(isinstance(state.get("attempts"), list) and isinstance(state.get("events"), list) and isinstance(state.get("gates"), dict),
                    "corrupt_state", "Malformed ledger collections")
            snapshots = state.setdefault("snapshots", [])
            require(isinstance(snapshots, list), "corrupt_state", "Malformed snapshot collection")
            ids, live_tasks = set(), set()
            for attempt in state["attempts"]:
                require(isinstance(attempt, dict) and attempt.get("status") in ACTIVE | CLOSED and attempt.get("id") not in ids,
                        "corrupt_state", "Invalid or duplicate attempt")
                require(all(key in attempt for key in ("id", "task_id", "files", "resources", "plan_hash", "task_hash", "iteration", "baseline", "reviewers")),
                        "corrupt_state", "Incomplete attempt")
                ids.add(attempt["id"])
                if attempt["status"] in ACTIVE:
                    require(attempt["task_id"] not in live_tasks, "corrupt_state", "Duplicate active task")
                    live_tasks.add(attempt["task_id"])
            snapshot_ids, snapshot_paths = set(), set()
            for snapshot in snapshots:
                require(isinstance(snapshot, dict) and all(key in snapshot for key in
                        ("id", "source_chat", "files", "manifest", "authorization", "evidence", "actor", "run_id", "recorded_at", "reason")),
                        "corrupt_state", "Incomplete snapshot record")
                uuid_text(snapshot["id"])
                require(snapshot["id"] not in snapshot_ids, "corrupt_state", "Duplicate snapshot ID")
                snapshot_ids.add(snapshot["id"])
                require(isinstance(snapshot["files"], list) and snapshot["files"], "corrupt_state", "Snapshot requires exact files")
                files = [self.path(value) for value in snapshot["files"]]
                require(files == sorted(set(files)), "corrupt_state", "Snapshot paths must be sorted and unique")
                require(not any(self.overlap(first, second) for index, first in enumerate(files) for second in files[index + 1:]),
                        "corrupt_state", "Snapshot paths overlap")
                require(not any(self.overlap(name, prior) for name in files for prior in snapshot_paths),
                        "corrupt_state", "Snapshot records cannot replace or overlap prior paths")
                snapshot_paths.update(files)
                require(isinstance(snapshot["manifest"], list) and len(snapshot["manifest"]) == len(files),
                        "corrupt_state", "Snapshot manifest does not match paths")
                for name, entry in zip(files, snapshot["manifest"]):
                    require(isinstance(entry, dict) and set(entry) == {"path", "mode", "sha256", "size"}
                            and entry.get("path") == name and entry.get("mode") in {"100644", "100755"}
                            and isinstance(entry.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                            and type(entry.get("size")) is int and entry["size"] >= 0,
                            "corrupt_state", "Malformed snapshot manifest entry")
                source = snapshot["source_chat"]
                require(isinstance(source, dict) and all(key in source for key in ("id", "title", "status", "root"))
                        and nonempty(source.get("title")) and source.get("status") == "completed"
                        and source.get("root") == str(self.root), "corrupt_state", "Malformed snapshot source chat")
                uuid_text(source["id"])
                authorization = snapshot["authorization"]
                require(isinstance(authorization, dict) and authorization.get("source") == SNAPSHOT_AUTH_SOURCE
                        and authorization.get("authorized") is True and nonempty(authorization.get("user_authorization")),
                        "corrupt_state", "Malformed snapshot authorization")
                require(authorization.get("current_chat_run_id") == snapshot["run_id"],
                        "corrupt_state", "Snapshot authorization owner mismatch")
                require(TARGET.fullmatch(snapshot["actor"] or ""), "corrupt_state", "Malformed snapshot actor")
                uuid_text(snapshot["run_id"])
                date(snapshot["recorded_at"])
                evidence = snapshot["evidence"]
                require(isinstance(evidence, dict) and nonempty(evidence.get("path"))
                        and isinstance(evidence.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])
                        and isinstance(evidence.get("payload"), dict), "corrupt_state", "Malformed snapshot evidence")
                self.path(evidence["path"], evidence=True)
                payload = evidence["payload"]
                stored_source = {**source, "files": files}
                observation = payload.get("source_observation")
                stored_authorization = payload.get("authorization")
                require(payload.get("kind") == SNAPSHOT_EVIDENCE_KIND and isinstance(payload.get("observed_at"), str)
                        and isinstance(observation, dict) and observation.get("tool") == "mcp__codex_app__read_thread"
                        and observation.get("thread_id") == source["id"] and observation.get("status") == source["status"]
                        and observation.get("title") == source["title"] and observation.get("root") == source["root"]
                        and observation.get("files") == files and observation.get("observed_at") == payload.get("observed_at")
                        and stored_authorization == authorization and payload.get("source_chat") == stored_source,
                        "corrupt_state", "Snapshot evidence differs from its immutable record")
                date(payload["observed_at"])
            recoveries = state.get("snapshot_recoveries", [])
            require(isinstance(recoveries, list), "corrupt_state", "Malformed snapshot recovery collection")
            amendments = state.get("snapshot_recovery_amendments", [])
            require(isinstance(amendments, list), "corrupt_state", "Malformed snapshot recovery amendment collection")
            self.validate_snapshot_recovery_amendments(state, snapshots, recoveries, amendments)
            continuations = state.get("snapshot_recovery_continuations", [])
            require(isinstance(continuations, list), "corrupt_state", "Malformed snapshot recovery continuation collection")
            self.validate_snapshot_recovery_continuations(state, snapshots, recoveries, amendments, continuations)
            self.validate_snapshot_recoveries(state, snapshots, recoveries, amendments)
            workspace_recoveries = state.get("workspace_delta_recoveries", [])
            require(isinstance(workspace_recoveries, list), "corrupt_state", "Malformed workspace delta recovery collection")
            workspace_amendments = state.get("workspace_delta_recovery_amendments", [])
            require(isinstance(workspace_amendments, list), "corrupt_state", "Malformed workspace delta recovery amendment collection")
            self.validate_workspace_delta_recoveries(state, workspace_recoveries, workspace_amendments)
            return state
        except (ValueError, TypeError, KeyError) as exc:
            raise Refusal("corrupt_state", "Ledger is corrupt; preserve it and recover explicitly") from exc

    @contextmanager
    def locked(self):
        no_reparse(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o777)
        lock_path = self.directory / "coordination.lock"
        no_reparse(lock_path)
        with lock_path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10
            acquired = False
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    require(time.monotonic() < deadline, "lock_timeout", "Coordinator lock timeout; never delete a live lock")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def write(self, state):
        temporary = self.directory / (".claims-" + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical(state) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def evidence(self, name):
        require(nonempty(name), "missing_evidence", "Evidence file required")
        path = Path(name)
        if path.is_absolute():
            try:
                name = str(path.relative_to(self.root))
            except ValueError as exc:
                raise Refusal("unsafe_path", "Evidence must stay within workspace") from exc
        name = self.path(name, evidence=True)
        raw = (self.root / name).read_bytes()
        payload = decode_json(raw)
        require(isinstance(payload, dict), "invalid_evidence", "Evidence must be an object")
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "payload": payload}

    @staticmethod
    def attempt(state, attempt_id):
        matches = [item for item in state["attempts"] if item["id"] == attempt_id]
        require(len(matches) == 1, "unknown_attempt", "Exact attempt ID required")
        return matches[0]

    @staticmethod
    def snapshot_recovery_source(snapshot):
        return {"snapshot_id": snapshot["id"],
                "source_chat": copy.deepcopy(snapshot["source_chat"]),
                "snapshot_actor": snapshot["actor"], "snapshot_run_id": snapshot["run_id"],
                "snapshot_authorization": copy.deepcopy(snapshot["authorization"]),
                "snapshot_evidence": {"path": snapshot["evidence"]["path"], "sha256": snapshot["evidence"]["sha256"]},
                "snapshot_recorded_at": snapshot["recorded_at"],
                "snapshot_manifest_sha256": digest(snapshot["manifest"])}

    def validate_snapshot_recovery_amendments(self, state, snapshots, recoveries, amendments):
        proposal_by_id = {item["id"]: item for item in recoveries}
        snapshot_by_id = {item["id"]: item for item in snapshots}
        attempt_by_id = {item["id"]: item for item in state["attempts"]}
        seen, bindings = set(), set()
        events = [item for item in state["events"] if isinstance(item, dict)]
        for amendment in amendments:
            fields = {"id", "proposal_id", "snapshot_id", "target_attempt_id", "task_id",
                      "proposal_iteration", "iteration", "manifest", "manifest_sha256",
                      "proposal_manifest_sha256", "proposal_sha256", "actor", "run_id",
                      "authorization", "amended_at", "reason"}
            require(isinstance(amendment, dict) and set(amendment) == fields,
                    "corrupt_state", "Malformed snapshot recovery amendment")
            uuid_text(amendment["id"])
            uuid_text(amendment["proposal_id"])
            uuid_text(amendment["snapshot_id"])
            uuid_text(amendment["target_attempt_id"])
            require(amendment["id"] not in seen, "corrupt_state", "Duplicate snapshot recovery amendment ID")
            seen.add(amendment["id"])
            binding = (amendment["snapshot_id"], amendment["target_attempt_id"])
            require(binding not in bindings, "corrupt_state", "Duplicate snapshot recovery amendment for an attempt")
            bindings.add(binding)
            proposal = proposal_by_id.get(amendment["proposal_id"])
            snapshot = snapshot_by_id.get(amendment["snapshot_id"])
            attempt = attempt_by_id.get(amendment["target_attempt_id"])
            require(proposal is not None and snapshot is not None and attempt is not None
                    and proposal["snapshot_id"] == snapshot["id"]
                    and proposal["target_attempt_id"] == attempt["id"]
                    and amendment["task_id"] == "B14" and proposal["task_id"] == "B14"
                    and attempt["task_id"] == "B14" and attempt.get("claimed_at") and attempt.get("worker"),
                    "corrupt_state", "Snapshot recovery amendment must bind its proposal and claimed B14 attempt")
            require(type(amendment["proposal_iteration"]) is int
                    and amendment["proposal_iteration"] == proposal["iteration"]
                    and type(amendment["iteration"]) is int
                    and amendment["iteration"] == proposal["iteration"],
                    "corrupt_state", "Snapshot recovery amendment changed the proposal iteration")
            manifest = amendment["manifest"]
            require(isinstance(manifest, list) and len(manifest) == len(snapshot["files"])
                    and manifest != proposal["manifest"] and manifest != snapshot["manifest"],
                    "corrupt_state", "Snapshot recovery amendment must pin a distinct drifted manifest")
            for name, entry in zip(snapshot["files"], manifest):
                require(isinstance(entry, dict) and set(entry) == {"path", "mode", "sha256", "size"}
                        and entry.get("path") == name and entry.get("mode") in {"100644", "100755"}
                        and isinstance(entry.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                        and type(entry.get("size")) is int and entry["size"] >= 0,
                        "corrupt_state", "Malformed snapshot recovery amendment manifest entry")
            require(amendment["manifest_sha256"] == digest(manifest)
                    and amendment["proposal_manifest_sha256"] == digest(proposal["manifest"])
                    and amendment["proposal_sha256"] == self.snapshot_recovery_proposal_digest(proposal),
                    "corrupt_state", "Snapshot recovery amendment digest differs from its immutable proposal")
            authorization = amendment["authorization"]
            require(isinstance(authorization, dict) and set(authorization) ==
                    {"source", "current_chat_run_id", "authorized", "user_authorization"}
                    and authorization.get("source") == SNAPSHOT_RECOVERY_AUTH_SOURCE
                    and authorization.get("current_chat_run_id") == amendment["run_id"]
                    and authorization.get("authorized") is True
                    and nonempty(authorization.get("user_authorization")),
                    "corrupt_state", "Snapshot recovery amendment requires direct current-run authorization")
            require(TARGET.fullmatch(amendment["actor"] or "") and amendment["actor"] == proposal["actor"]
                    and amendment["run_id"] == proposal["run_id"]
                    and amendment["actor"] == attempt.get("orchestrator", {}).get("target")
                    and amendment["run_id"] == attempt.get("orchestrator", {}).get("run_id"),
                    "corrupt_state", "Snapshot recovery amendment owner differs from its proposal")
            uuid_text(amendment["run_id"])
            date(amendment["amended_at"])
            require(nonempty(amendment["reason"]), "corrupt_state", "Snapshot recovery amendment reason is required")
            matching = [event for event in events if event.get("action") == "amend-snapshot-recovery"
                        and event.get("snapshot_recovery_amendment_id") == amendment["id"]
                        and event.get("attempt_id") == attempt["id"]]
            require(len(matching) == 1, "corrupt_state", "Snapshot recovery amendment is missing its immutable audit event")
            event = matching[0]
            previous = event.get("previous")
            proposal_events = [item for item in events if item.get("action") == "propose-snapshot-recovery"
                               and item.get("snapshot_recovery_proposal_id") == proposal["id"]]
            proposal_sequence = proposal_events[0].get("sequence", 0) if len(proposal_events) == 1 else 0
            require(len(proposal_events) == 1 and proposal_sequence < event.get("sequence", 0)
                    and event.get("snapshot_recovery_amendment") == amendment
                    and event.get("task_id") == "B14" and event.get("actor") == amendment["actor"]
                    and event.get("run_id") == amendment["run_id"]
                    and isinstance(previous, dict) and previous.get("status") == "in_progress"
                    and previous.get("iteration") == amendment["iteration"],
                    "corrupt_state", "Snapshot recovery amendment audit binding is inconsistent")
            between = [item for item in events if item.get("attempt_id") == attempt["id"]
                       and proposal_sequence < item.get("sequence", 0) < event.get("sequence", 0)]
            forbidden_before = [item for item in between
                                if item.get("action") in {"submit", "review", "accept", "release", "cancel"}]
            rebases = [item for item in between if item.get("action") == "rebase"]
            require(not forbidden_before and len(rebases) <= 1
                    and all(isinstance(item.get("previous"), dict)
                            and item["previous"].get("status") == "in_progress"
                            and item["previous"].get("iteration") == amendment["iteration"]
                            for item in rebases),
                    "corrupt_state", "Snapshot recovery amendment was recorded after candidate, review, close, or iteration change")
            later_rebases = [item for item in events if item.get("attempt_id") == attempt["id"]
                             and item.get("sequence", 0) > event.get("sequence", 0)
                             and item.get("action") == "rebase"]
            require(not later_rebases, "corrupt_state", "An amended recovery cannot be rebased after its pinned manifest is recorded")

    def snapshot_recovery_attempt_pins(self, attempt, iteration=None):
        worker = attempt.get("worker") or {}
        return {"attempt_id": attempt.get("id"), "task_id": attempt.get("task_id"),
                "iteration": attempt.get("iteration") if iteration is None else iteration,
                "plan_hash": attempt.get("plan_hash"),
                "task_hash": attempt.get("task_hash"), "base_head": attempt.get("base_head"),
                "baseline_index_sha256": attempt.get("baseline_index_sha256"),
                "baseline_sha256": digest(attempt.get("baseline")),
                "baseline_scope_sha256": digest(attempt.get("baseline_scope")),
                "scope_sha256": digest(attempt.get("files")),
                "worker": {"target": worker.get("target"), "run_id": worker.get("run_id")}}

    def snapshot_recovery_no_go_binding(self, state, attempt, proposal, iteration, amendment):
        submissions = [item for item in attempt.get("submissions", []) if isinstance(item, dict)
                       and item.get("attempt_id") == attempt.get("id") and item.get("iteration") == iteration]
        if len(submissions) != 1:
            return None
        candidate = submissions[0]
        continuation = self.snapshot_recovery_continuation_for_iteration(state, proposal, iteration)
        expected_summary = self.snapshot_recovery_candidate_summary(
            proposal, attempt, iteration, amendment, continuation)
        if (candidate.get("task_id") != "B14" or candidate.get("task_hash") != attempt.get("task_hash")
                or candidate.get("plan_hash") != attempt.get("plan_hash")
                or candidate.get("base_head") != attempt.get("base_head")
                or candidate.get("index_sha256") != attempt.get("baseline_index_sha256")
                or not isinstance(candidate.get("manifest"), dict)
                or sorted(candidate.get("manifest", {})) != attempt.get("files")
                or candidate.get("snapshot_recoveries") != [expected_summary]):
            return None
        unsigned = copy.deepcopy(candidate)
        candidate_digest = unsigned.pop("digest", None)
        if not candidate_digest or candidate_digest != digest(unsigned):
            return None
        reviews = [item for item in attempt.get("reviews", []) if isinstance(item, dict)
                   and item.get("iteration") == iteration and item.get("candidate_digest") == candidate_digest]
        if len(reviews) != 1:
            return None
        review = reviews[0]
        evidence = review.get("evidence")
        payload = evidence.get("payload") if isinstance(evidence, dict) else None
        reviewer = next((item for item in attempt.get("reviewers", []) if isinstance(item, dict)
                         and item.get("target") == review.get("reviewer")
                         and item.get("run_id") == review.get("run_id")), None)
        submit_events = [item for item in state.get("events", []) if isinstance(item, dict)
                         and item.get("action") == "submit" and item.get("attempt_id") == attempt["id"]
                         and item.get("candidate_digest") == candidate_digest]
        review_events = [item for item in state.get("events", []) if isinstance(item, dict)
                         and item.get("action") == "review" and item.get("attempt_id") == attempt["id"]
                         and item.get("candidate_digest") == candidate_digest]
        if (review.get("result") != "NO_GO" or not isinstance(payload, dict) or reviewer is None
                or len(submit_events) != 1 or len(review_events) != 1
                or submit_events[0].get("candidate_iteration") != iteration
                or review_events[0].get("review_result") != "NO_GO"
                or review_events[0].get("sequence", 0) <= submit_events[0].get("sequence", 0)
                or payload.get("task_id") != "B14" or payload.get("attempt_id") != attempt["id"]
                or payload.get("iteration") != iteration or payload.get("candidate_digest") != candidate_digest
                or payload.get("plan_hash") != attempt.get("plan_hash") or payload.get("result") != "NO_GO"
                or payload.get("review_nonce") != reviewer.get("nonce")):
            return None
        if continuation is not None and payload.get("snapshot_recoveries") != candidate.get("snapshot_recoveries"):
            return None
        return {"iteration": iteration, "candidate_digest": candidate_digest,
                "reviewer": review.get("reviewer"), "reviewer_run_id": review.get("run_id"),
                "review_evidence_sha256": evidence.get("sha256"),
                "review_sequence": review_events[0].get("sequence")}

    def validate_snapshot_recovery_continuations(self, state, snapshots, recoveries, amendments, continuations):
        proposal_by_id = {item["id"]: item for item in recoveries}
        snapshot_by_id = {item["id"]: item for item in snapshots}
        attempt_by_id = {item["id"]: item for item in state["attempts"]}
        amendment_by_proposal = {item["proposal_id"]: item for item in amendments}
        recovery_by_attempt = {item["attempt_id"]: item for item in state.get("workspace_delta_recoveries", [])}
        events = [item for item in state.get("events", []) if isinstance(item, dict)]
        fields = {"id", "proposal_id", "snapshot_id", "target_attempt_id", "task_id",
                  "proposal_iteration", "amendment_id", "amendment_sha256", "iteration", "sequence",
                  "previous_entry_id", "previous_entry_sha256", "manifest", "manifest_sha256",
                  "proposal_manifest_sha256", "proposal_sha256", "attempt_pins",
                  "workspace_delta_recovery_id", "workspace_delta_recovery_sha256", "previous_candidate",
                  "authorization", "actor", "run_id", "continued_at", "reason"}
        seen_ids, seen_bindings, grouped, all_ids = set(), set(), {}, set()
        for continuation in continuations:
            require(isinstance(continuation, dict) and set(continuation) == fields,
                    "corrupt_state", "Malformed snapshot recovery continuation")
            for name in ("id", "proposal_id", "snapshot_id", "target_attempt_id", "amendment_id",
                         "workspace_delta_recovery_id"):
                uuid_text(continuation[name])
            require(continuation["id"] not in seen_ids, "corrupt_state", "Duplicate snapshot continuation ID")
            seen_ids.add(continuation["id"])
            proposal = proposal_by_id.get(continuation["proposal_id"])
            snapshot = snapshot_by_id.get(continuation["snapshot_id"])
            attempt = attempt_by_id.get(continuation["target_attempt_id"])
            amendment = amendment_by_proposal.get(continuation["proposal_id"])
            workspace_origin = recovery_by_attempt.get(continuation["target_attempt_id"])
            require(proposal is not None and snapshot is not None and attempt is not None and amendment is not None
                    and workspace_origin is not None
                    and proposal["snapshot_id"] == snapshot["id"]
                    and proposal["target_attempt_id"] == attempt["id"]
                    and proposal["task_id"] == attempt["task_id"] == continuation["task_id"] == "B14"
                    and attempt.get("claimed_at") and attempt.get("worker")
                    and continuation["amendment_id"] == amendment["id"]
                    and continuation["amendment_sha256"] == digest(amendment)
                    and continuation["workspace_delta_recovery_id"] == workspace_origin["id"]
                    and continuation["workspace_delta_recovery_sha256"] == digest(workspace_origin),
                    "corrupt_state", "Snapshot continuation changed its B14 proposal, amendment, or workspace origin")
            require(type(continuation["proposal_iteration"]) is int
                    and continuation["proposal_iteration"] == proposal["iteration"] == amendment["iteration"]
                    and type(continuation["iteration"]) is int
                    and continuation["iteration"] <= attempt.get("iteration")
                    and type(continuation["sequence"]) is int and continuation["sequence"] >= 1,
                    "corrupt_state", "Snapshot continuation iteration binding is invalid")
            binding = (attempt["id"], snapshot["id"], continuation["iteration"])
            require(binding not in seen_bindings, "corrupt_state", "Duplicate snapshot continuation in one iteration")
            seen_bindings.add(binding)
            manifest = continuation["manifest"]
            require(isinstance(manifest, list) and len(manifest) == len(snapshot["files"]),
                    "corrupt_state", "Snapshot continuation manifest does not match source paths")
            for name, entry in zip(snapshot["files"], manifest):
                require(isinstance(entry, dict) and set(entry) == {"path", "mode", "sha256", "size"}
                        and entry.get("path") == name and entry.get("mode") in {"100644", "100755"}
                        and isinstance(entry.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                        and type(entry.get("size")) is int and entry["size"] >= 0,
                        "corrupt_state", "Malformed snapshot continuation manifest entry")
            require(continuation["manifest_sha256"] == digest(manifest)
                    and manifest != snapshot["manifest"]
                    and continuation["proposal_manifest_sha256"] == digest(proposal["manifest"])
                    and continuation["proposal_sha256"] == self.snapshot_recovery_proposal_digest(proposal),
                    "corrupt_state", "Snapshot continuation manifest or immutable proposal digest is inconsistent")
            pins = self.snapshot_recovery_attempt_pins(attempt, continuation["iteration"])
            require(continuation["attempt_pins"] == pins,
                    "corrupt_state", "Snapshot continuation changed attempt, plan, HEAD, index, baseline, or scope pins")
            authorization = continuation["authorization"]
            require(isinstance(authorization, dict) and set(authorization) ==
                    {"source", "current_chat_run_id", "authorized", "user_authorization"}
                    and authorization.get("source") == SNAPSHOT_RECOVERY_AUTH_SOURCE
                    and authorization.get("current_chat_run_id") == continuation["run_id"]
                    and authorization.get("authorized") is True
                    and authorization.get("user_authorization") == SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION,
                    "corrupt_state", "Snapshot continuation requires fresh exact direct user authorization")
            require(TARGET.fullmatch(continuation["actor"] or "")
                    and continuation["actor"] == attempt.get("orchestrator", {}).get("target") == "/root"
                    and continuation["run_id"] == attempt.get("orchestrator", {}).get("run_id"),
                    "corrupt_state", "Snapshot continuation must bind the current root orchestrator")
            uuid_text(continuation["run_id"])
            date(continuation["continued_at"])
            require(nonempty(continuation["reason"]), "corrupt_state", "Snapshot continuation reason is required")
            grouped.setdefault(attempt["id"], []).append(continuation)
            all_ids.add(continuation["id"])

        for attempt_id, chain in grouped.items():
            attempt = attempt_by_id[attempt_id]
            prior = next((item for item in amendments if item["target_attempt_id"] == attempt_id), None)
            proposal = next((item for item in recoveries if item["target_attempt_id"] == attempt_id), None)
            require(prior is not None and proposal is not None,
                    "corrupt_state", "Snapshot continuation has no immutable proposal amendment base")
            snapshot_amendment = prior
            origin_iteration = snapshot_amendment["iteration"]
            for sequence, continuation in enumerate(chain, start=1):
                require(continuation["sequence"] == sequence
                        and continuation["iteration"] > origin_iteration
                        and (sequence == 1 or continuation["iteration"] == prior["iteration"] + 1),
                        "corrupt_state", "Snapshot continuation chain has duplicate or missing iterations")
                require(continuation["previous_entry_id"] == prior["id"]
                        and continuation["previous_entry_sha256"] == digest(prior),
                        "corrupt_state", "Snapshot continuation chain has a gap or forged previous entry")
                no_go = self.snapshot_recovery_no_go_binding(
                    state, attempt, proposal, continuation["iteration"] - 1, snapshot_amendment)
                require(no_go is not None and continuation["previous_candidate"] == no_go,
                        "corrupt_state", "Snapshot continuation lacks a matching prior candidate and NO_GO")
                matching = [event for event in events if event.get("action") == "continue-snapshot-recovery"
                            and event.get("snapshot_recovery_continuation_id") == continuation["id"]
                            and event.get("attempt_id") == attempt_id]
                require(len(matching) == 1, "corrupt_state", "Snapshot continuation is missing its immutable audit event")
                event = matching[0]
                previous = event.get("previous")
                no_go_event = next((item for item in events if item.get("action") == "review"
                                    and item.get("attempt_id") == attempt_id
                                    and item.get("candidate_digest") == no_go["candidate_digest"]), None)
                previous_action, previous_key = (
                    ("continue-snapshot-recovery", "snapshot_recovery_continuation_id")
                    if prior is not None and prior is not amendment
                    else ("amend-snapshot-recovery", "snapshot_recovery_amendment_id"))
                previous_events = [item for item in events if item.get("action") == previous_action
                                   and item.get(previous_key) == prior["id"]
                                   and item.get("attempt_id") == attempt_id]
                require(event.get("snapshot_recovery_continuation") == continuation
                        and event.get("task_id") == "B14" and event.get("actor") == continuation["actor"]
                        and event.get("run_id") == continuation["run_id"]
                        and isinstance(previous, dict) and previous.get("status") == "changes_requested"
                        and previous.get("iteration") == continuation["iteration"]
                        and previous.get("plan_hash") == attempt.get("plan_hash")
                        and previous.get("task_hash") == attempt.get("task_hash")
                        and previous.get("base_head") == attempt.get("base_head")
                        and previous.get("baseline_index_sha256") == attempt.get("baseline_index_sha256")
                        and previous.get("files") == attempt.get("files")
                        and len(previous_events) == 1
                        and previous_events[0].get("sequence", 0) < event.get("sequence", 0)
                        and no_go_event is not None
                        and no_go_event.get("sequence", 0) < event.get("sequence", 0),
                        "corrupt_state", "Snapshot continuation audit does not follow its bound NO_GO")
                before_next_candidate = [item for item in events if item.get("attempt_id") == attempt_id
                                         and item.get("action") in {"submit", "bind-reviewer", "review", "accept"}
                                         and item.get("sequence", 0) < event.get("sequence", 0)
                                         and item.get("candidate_iteration", (item.get("previous") or {}).get("iteration"))
                                         == continuation["iteration"]]
                require(not before_next_candidate,
                        "corrupt_state", "Snapshot continuation was recorded after its candidate or review")
                prior = continuation

    def validate_snapshot_recoveries(self, state, snapshots, recoveries, amendments=None):
        amendments = amendments or []
        amendment_by_proposal = {item["proposal_id"]: item for item in amendments}
        snapshot_by_id = {item["id"]: item for item in snapshots}
        attempt_by_id = {item["id"]: item for item in state["attempts"]}
        seen, bindings = set(), set()
        for proposal in recoveries:
            fields = {"id", "snapshot_id", "target_attempt_id", "task_id", "iteration", "manifest",
                      "source_provenance", "authorization", "actor", "run_id", "proposed_at", "reason"}
            require(isinstance(proposal, dict) and set(proposal) == fields,
                    "corrupt_state", "Malformed snapshot recovery proposal")
            uuid_text(proposal["id"])
            uuid_text(proposal["snapshot_id"])
            uuid_text(proposal["target_attempt_id"])
            require(proposal["id"] not in seen, "corrupt_state", "Duplicate snapshot recovery proposal ID")
            seen.add(proposal["id"])
            binding = (proposal["snapshot_id"], proposal["target_attempt_id"])
            require(binding not in bindings, "corrupt_state", "Duplicate snapshot recovery proposal for an attempt")
            bindings.add(binding)
            snapshot = snapshot_by_id.get(proposal["snapshot_id"])
            attempt = attempt_by_id.get(proposal["target_attempt_id"])
            require(snapshot is not None and attempt is not None and proposal["task_id"] == "B14"
                    and attempt["task_id"] == "B14" and attempt.get("claimed_at") and attempt.get("worker"),
                    "corrupt_state", "Snapshot recovery must reference its claimed B14 attempt and source snapshot")
            require(type(proposal["iteration"]) is int and proposal["iteration"] >= 1,
                    "corrupt_state", "Malformed snapshot recovery iteration")
            require(type(attempt.get("iteration")) is int and proposal["iteration"] <= attempt["iteration"],
                    "corrupt_state", "Snapshot recovery proposal iteration is ahead of its B14 attempt")
            manifest = proposal["manifest"]
            require(isinstance(manifest, list) and len(manifest) == len(snapshot["files"]),
                    "corrupt_state", "Snapshot recovery manifest does not match source paths")
            require(manifest != snapshot["manifest"], "corrupt_state", "Snapshot recovery proposal cannot repeat original manifest")
            for name, entry in zip(snapshot["files"], manifest):
                require(isinstance(entry, dict) and set(entry) == {"path", "mode", "sha256", "size"}
                        and entry.get("path") == name and entry.get("mode") in {"100644", "100755"}
                        and isinstance(entry.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                        and type(entry.get("size")) is int and entry["size"] >= 0,
                        "corrupt_state", "Malformed snapshot recovery manifest entry")
            require(proposal["source_provenance"] == self.snapshot_recovery_source(snapshot),
                    "corrupt_state", "Snapshot recovery source provenance differs from immutable snapshot")
            authorization = proposal["authorization"]
            require(isinstance(authorization, dict) and set(authorization) ==
                    {"source", "current_chat_run_id", "authorized", "user_authorization"}
                    and authorization.get("source") == SNAPSHOT_RECOVERY_AUTH_SOURCE
                    and authorization.get("current_chat_run_id") == proposal["run_id"]
                    and authorization.get("authorized") is True
                    and nonempty(authorization.get("user_authorization")),
                    "corrupt_state", "Malformed snapshot recovery authorization")
            require(TARGET.fullmatch(proposal["actor"] or ""), "corrupt_state", "Malformed snapshot recovery actor")
            uuid_text(proposal["run_id"])
            require(attempt.get("orchestrator") == {"target": proposal["actor"], "run_id": proposal["run_id"]},
                    "corrupt_state", "Snapshot recovery proposal owner differs from B14 owner")
            date(proposal["proposed_at"])
            require(nonempty(proposal["reason"]), "corrupt_state", "Snapshot recovery reason is required")
        proposal_events = {(event.get("snapshot_recovery_proposal_id"), event.get("attempt_id")): event
                           for event in state["events"] if isinstance(event, dict)
                           and event.get("action") == "propose-snapshot-recovery"}
        for proposal in recoveries:
            event = proposal_events.get((proposal["id"], proposal["target_attempt_id"]))
            require(event is not None and event.get("snapshot_recovery_proposal") == proposal
                    and event.get("task_id") == "B14" and event.get("actor") == proposal["actor"]
                    and event.get("run_id") == proposal["run_id"],
                    "corrupt_state", "Snapshot recovery proposal is missing its immutable audit event")
            attempt = attempt_by_id[proposal["target_attempt_id"]]
            amendment = amendment_by_proposal.get(proposal["id"])
            for candidate in attempt.get("submissions", []):
                if not isinstance(candidate, dict):
                    continue
                summaries = [item for item in candidate.get("snapshot_recoveries", [])
                             if isinstance(item, dict) and item.get("proposal_id") == proposal["id"]]
                if summaries:
                    continuation = self.snapshot_recovery_continuation_for_iteration(
                        state, proposal, candidate.get("iteration"))
                    require(len(summaries) == 1
                            and summaries[0] == self.snapshot_recovery_candidate_summary(
                                proposal, attempt, candidate.get("iteration"), amendment, continuation)
                            and self.snapshot_recovery_iteration_history_valid(
                                state, attempt, proposal, candidate.get("iteration"), amendment),
                            "corrupt_state", "Snapshot recovery candidate history is inconsistent")

    def validate_workspace_delta_manifest(self, state, attempt, manifest, manifest_sha256):
        require(isinstance(manifest, list) and manifest,
                "corrupt_state", "Workspace delta recovery requires a nonempty exact manifest")
        paths = []
        for entry in manifest:
            require(isinstance(entry, dict) and "path" in entry,
                    "corrupt_state", "Malformed workspace delta manifest entry")
            name = self.path(entry["path"], inventory=True)
            paths.append(name)
            if entry.get("absent") is True:
                require(set(entry) == {"path", "absent"},
                        "corrupt_state", "Absent workspace delta entry cannot carry file metadata")
                current = {"kind": "absent"}
            else:
                require(set(entry) == {"path", "mode", "sha256", "size"}
                        and entry["mode"] in {"100644", "100755"}
                        and isinstance(entry["sha256"], str)
                        and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                        and type(entry["size"]) is int and entry["size"] >= 0,
                        "corrupt_state", "Malformed workspace delta file metadata")
                current = {"kind": "file", "sha256": entry["sha256"], "size": entry["size"],
                           "executable": entry["mode"] == "100755"}
            require(not any(self.overlap(name, owned) for owned in attempt["files"]),
                    "corrupt_state", "Workspace delta manifest overlaps the B14 claim")
            require(not any(self.overlap(name, snap_path)
                            for snapshot in state.get("snapshots", [])
                            for snap_path in snapshot.get("files", [])),
                    "corrupt_state", "Workspace delta manifest overlaps an immutable snapshot")
            baseline = attempt["baseline"].get(name, {"kind": "absent"})
            require(baseline != current,
                    "corrupt_state", "Workspace delta manifest entry was unchanged from the B14 baseline")
        require(paths == sorted(set(paths)),
                "corrupt_state", "Workspace delta manifest paths must be sorted and unique")
        require(manifest_sha256 == digest(manifest),
                "corrupt_state", "Workspace delta manifest digest mismatch")

    def validate_workspace_delta_stale_observation(self, state, attempt, candidate, observation, *, require_live):
        fields = {"source", "observed_at", "observed_by", "observed_run_id", "task_id", "attempt_id",
                  "iteration", "plan_hash", "base_head", "index_sha256", "baseline_sha256", "scope_sha256",
                  "candidate_digest", "chain_head_id", "chain_head_sha256", "manifest", "manifest_sha256"}
        require(isinstance(observation, dict) and set(observation) == fields
                and observation.get("source") == "root_read_only_workspace_delta_observation",
                "invalid_review", "Stale workspace recovery requires an exact root observation")
        owner = attempt.get("orchestrator")
        summary = candidate.get("workspace_delta_recovery") if isinstance(candidate, dict) else None
        require(isinstance(owner, dict) and observation.get("observed_by") == owner.get("target")
                and observation.get("observed_run_id") == owner.get("run_id")
                and owner == state.get("orchestrator"),
                "invalid_review", "Workspace delta observation must bind the current root orchestrator")
        require(observation.get("task_id") == "B14" and observation.get("attempt_id") == attempt.get("id")
                and observation.get("iteration") == candidate.get("iteration")
                and observation.get("plan_hash") == candidate.get("plan_hash")
                and observation.get("base_head") == candidate.get("base_head")
                and observation.get("index_sha256") == candidate.get("index_sha256")
                and observation.get("baseline_sha256") == digest(attempt.get("baseline"))
                and observation.get("scope_sha256") == digest(attempt.get("files"))
                and observation.get("candidate_digest") == candidate.get("digest")
                and isinstance(summary, dict)
                and observation.get("chain_head_id") == summary.get("chain_head_id")
                and observation.get("chain_head_sha256") == summary.get("chain_head_sha256"),
                "invalid_review", "Workspace delta observation does not bind the submitted candidate and recovery chain")
        date(observation.get("observed_at"))
        if require_live:
            fresh(observation.get("observed_at"))
        self.validate_workspace_delta_manifest(state, attempt, observation.get("manifest"),
                                               observation.get("manifest_sha256"))
        require(observation["manifest"] != summary.get("manifest"),
                "invalid_review", "A stale-candidate NO_GO observation must show exact manifest drift")
        if require_live:
            require(self.unexplained_workspace_delta(state, attempt) == observation["manifest"],
                    "stale_workspace_delta_observation", "Root observation does not match the current exact delta")
        return True

    def validate_workspace_delta_recoveries(self, state, recoveries, amendments=()):
        """Validate append-only root receipts and their hash-linked amendments."""
        attempt_by_id = {item["id"]: item for item in state["attempts"]}
        events = [item for item in state["events"] if isinstance(item, dict)]
        seen_ids, seen_attempts = set(), set()
        fields = {"id", "attempt_id", "task_id", "iteration", "plan_hash", "base_head",
                  "baseline_index_sha256", "baseline_sha256", "scope_sha256",
                  "snapshot_recovery_bindings", "manifest", "manifest_sha256", "authorization",
                  "actor", "run_id", "recorded_at", "reason"}
        for record in recoveries:
            require(isinstance(record, dict) and set(record) == fields,
                    "corrupt_state", "Malformed workspace delta recovery record")
            uuid_text(record["id"])
            uuid_text(record["attempt_id"])
            require(record["id"] not in seen_ids and record["attempt_id"] not in seen_attempts,
                    "corrupt_state", "Workspace delta recovery origin is one-time per attempt")
            seen_ids.add(record["id"])
            seen_attempts.add(record["attempt_id"])
            attempt = attempt_by_id.get(record["attempt_id"])
            require(attempt is not None and attempt.get("task_id") == "B14"
                    and attempt.get("claimed_at") and attempt.get("worker"),
                    "corrupt_state", "Workspace delta recovery must bind a claimed B14 attempt")
            require(record["task_id"] == "B14" and type(record["iteration"]) is int
                    and record["iteration"] >= 1 and isinstance(record["plan_hash"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", record["plan_hash"])
                    and record["plan_hash"] == attempt.get("plan_hash")
                    and record["iteration"] <= attempt.get("iteration"),
                    "corrupt_state", "Workspace delta recovery plan or iteration binding is invalid")
            require(record["base_head"] == attempt.get("base_head")
                    and record["baseline_index_sha256"] == attempt.get("baseline_index_sha256")
                    and record["baseline_sha256"] == digest(attempt.get("baseline"))
                    and record["scope_sha256"] == digest(attempt.get("files")),
                    "corrupt_state", "Workspace delta recovery baseline binding is invalid")
            for name in ("baseline_index_sha256", "baseline_sha256", "scope_sha256"):
                require(isinstance(record[name], str) and re.fullmatch(r"[0-9a-f]{64}", record[name]),
                        "corrupt_state", f"Malformed workspace delta recovery {name}")
            require(isinstance(record["base_head"], str) and re.fullmatch(r"[0-9a-f]{40,64}", record["base_head"]),
                    "corrupt_state", "Malformed workspace delta recovery base HEAD")
            authorization = record["authorization"]
            require(isinstance(authorization, dict) and set(authorization) ==
                    {"source", "current_chat_run_id", "authorized", "user_authorization"}
                    and authorization.get("source") == SNAPSHOT_RECOVERY_AUTH_SOURCE
                    and authorization.get("current_chat_run_id") == record["run_id"]
                    and authorization.get("authorized") is True
                    and authorization.get("user_authorization") == WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                    "corrupt_state", "Workspace delta recovery requires its direct exact user authorization")
            require(TARGET.fullmatch(record["actor"] or "") and record["actor"] == "/root"
                    and record["actor"] == attempt.get("orchestrator", {}).get("target")
                    and record["run_id"] == attempt.get("orchestrator", {}).get("run_id"),
                    "corrupt_state", "Workspace delta recovery must bind its root orchestrator")
            uuid_text(record["run_id"])
            date(record["recorded_at"])
            require(nonempty(record["reason"]), "corrupt_state", "Workspace delta recovery reason is required")
            self.validate_workspace_delta_manifest(state, attempt, record["manifest"], record["manifest_sha256"])
            bindings = self.snapshot_recovery_bindings(state, attempt)
            require(record["snapshot_recovery_bindings"] == bindings and len(bindings) == 1,
                    "corrupt_state", "Workspace delta recovery must preserve the exact current snapshot recovery lineage")
            matching = [event for event in events if event.get("action") == "record-workspace-recovery"
                        and event.get("workspace_delta_recovery_id") == record["id"]
                        and event.get("attempt_id") == attempt["id"]]
            require(len(matching) == 1, "corrupt_state", "Workspace delta recovery is missing its immutable audit event")
            event = matching[0]
            previous = event.get("previous")
            require(event.get("workspace_delta_recovery") == record
                    and event.get("task_id") == "B14" and event.get("actor") == record["actor"]
                    and event.get("run_id") == record["run_id"]
                    and event.get("workspace_delta_recovery_baseline_sha256") == record["baseline_sha256"]
                    and isinstance(previous, dict) and previous.get("status") == "in_progress"
                    and previous.get("iteration") == record["iteration"]
                    and previous.get("plan_hash") == record["plan_hash"]
                    and previous.get("base_head") == record["base_head"]
                    and previous.get("baseline_index_sha256") == record["baseline_index_sha256"]
                    and previous.get("files") == attempt["files"],
                    "corrupt_state", "Workspace delta recovery audit binding is inconsistent")
            earlier = [item for item in events if item.get("attempt_id") == attempt["id"]
                       and item.get("sequence", 0) < event.get("sequence", 0)]
            require(not any(item.get("action") in {"submit", "review", "accept", "release", "cancel"}
                            for item in earlier),
                    "corrupt_state", "Workspace delta recovery was recorded after submission, review, or close")

        amendment_fields = {"id", "attempt_id", "task_id", "origin_recovery_id", "origin_recovery_sha256",
                            "sequence", "previous_entry_id", "previous_entry_sha256", "iteration", "plan_hash",
                            "base_head", "baseline_index_sha256", "baseline_sha256", "scope_sha256",
                            "snapshot_recovery_bindings", "manifest", "manifest_sha256", "authorization",
                            "actor", "run_id", "recorded_at", "reason"}
        amendment_ids = set(seen_ids)
        for attempt_id in seen_attempts:
            record = next(item for item in recoveries if item["attempt_id"] == attempt_id)
            attempt = attempt_by_id[attempt_id]
            chain = [item for item in amendments if isinstance(item, dict) and item.get("attempt_id") == attempt_id]
            prior = record
            seen_iterations = set()
            for sequence, amendment in enumerate(chain, start=1):
                require(set(amendment) == amendment_fields,
                        "corrupt_state", "Malformed workspace delta recovery amendment")
                uuid_text(amendment["id"])
                require(amendment["id"] not in amendment_ids,
                        "corrupt_state", "Duplicate workspace delta recovery amendment ID")
                amendment_ids.add(amendment["id"])
                require(amendment["task_id"] == "B14" and amendment["origin_recovery_id"] == record["id"]
                        and amendment["origin_recovery_sha256"] == digest(record)
                        and amendment["sequence"] == sequence
                        and amendment["previous_entry_id"] == prior["id"]
                        and amendment["previous_entry_sha256"] == digest(prior),
                        "corrupt_state", "Workspace delta recovery amendment chain has a gap or forged parent")
                require(type(amendment["iteration"]) is int
                        and record["iteration"] <= amendment["iteration"] <= attempt.get("iteration")
                        and amendment["iteration"] not in seen_iterations,
                        "corrupt_state", "Workspace delta recovery amendment iteration is duplicate or stale")
                seen_iterations.add(amendment["iteration"])
                for key in ("plan_hash", "base_head", "baseline_index_sha256", "baseline_sha256", "scope_sha256",
                            "snapshot_recovery_bindings"):
                    require(amendment[key] == record[key],
                            "corrupt_state", "Workspace delta recovery amendment changed a pinned binding")
                authorization = amendment["authorization"]
                require(isinstance(authorization, dict) and set(authorization) ==
                        {"source", "current_chat_run_id", "authorized", "user_authorization"}
                        and authorization.get("source") == SNAPSHOT_RECOVERY_AUTH_SOURCE
                        and authorization.get("current_chat_run_id") == amendment["run_id"]
                        and authorization.get("authorized") is True
                        and authorization.get("user_authorization") == WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                        "corrupt_state", "Workspace delta amendment requires fresh direct user authorization")
                require(amendment["actor"] == record["actor"] == "/root"
                        and amendment["run_id"] == record["run_id"] == attempt.get("orchestrator", {}).get("run_id"),
                        "corrupt_state", "Workspace delta amendment must bind the same root orchestrator")
                uuid_text(amendment["run_id"])
                date(amendment["recorded_at"])
                require(nonempty(amendment["reason"]), "corrupt_state", "Workspace delta amendment reason is required")
                self.validate_workspace_delta_manifest(state, attempt, amendment["manifest"], amendment["manifest_sha256"])
                matching = [event for event in events if event.get("action") == "amend-workspace-recovery"
                            and event.get("workspace_delta_recovery_amendment_id") == amendment["id"]
                            and event.get("attempt_id") == attempt_id]
                require(len(matching) == 1, "corrupt_state", "Workspace delta amendment is missing its immutable audit event")
                event = matching[0]
                previous = event.get("previous")
                previous_entry_event = self.workspace_delta_recovery_entry_event(state, record, prior)
                require(event.get("workspace_delta_recovery_amendment") == amendment
                        and event.get("task_id") == "B14" and event.get("actor") == amendment["actor"]
                        and event.get("run_id") == amendment["run_id"]
                        and previous_entry_event is not None
                        and event.get("sequence", 0) > previous_entry_event.get("sequence", 0)
                        and isinstance(previous, dict) and previous.get("iteration") == amendment["iteration"]
                        and previous.get("plan_hash") == record["plan_hash"]
                        and previous.get("base_head") == record["base_head"]
                        and previous.get("baseline_index_sha256") == record["baseline_index_sha256"]
                        and previous.get("files") == attempt["files"],
                        "corrupt_state", "Workspace delta amendment audit binding is inconsistent")
                if amendment["iteration"] == record["iteration"]:
                    require(previous.get("status") == "in_progress",
                            "corrupt_state", "Initial workspace amendment must precede the first candidate")
                else:
                    require(previous.get("status") == "changes_requested"
                            and self.workspace_delta_recovery_iteration_history_valid(
                                state, attempt, record, amendment["iteration"]),
                            "corrupt_state", "Workspace amendment after NO_GO lacks a complete matching repair history")
                previous_events = [item for item in events if item.get("attempt_id") == attempt_id
                                   and item.get("sequence", 0) < event.get("sequence", 0)]
                require(not any(item.get("action") in {"submit", "review", "accept", "release", "cancel"}
                                and item.get("candidate_iteration", (item.get("previous") or {}).get("iteration"))
                                == amendment["iteration"] for item in previous_events),
                        "corrupt_state", "Workspace delta amendment was recorded after a candidate or review")
                prior = amendment

        require(not any(isinstance(item, dict) and item.get("attempt_id") not in seen_attempts
                        for item in amendments),
                "corrupt_state", "Workspace delta amendment has no immutable origin receipt")

        for attempt_id in seen_attempts:
            record = next(item for item in recoveries if item["attempt_id"] == attempt_id)
            attempt = attempt_by_id[attempt_id]
            for candidate in attempt.get("submissions", []):
                if not isinstance(candidate, dict):
                    continue
                iteration = candidate.get("iteration")
                if type(iteration) is int and iteration >= record["iteration"]:
                    entry = self.workspace_delta_recovery_entry_for_iteration(state, record, iteration)
                    summary = self.workspace_delta_recovery_candidate_summary(record, iteration, entry)
                    require(entry is not None and candidate.get("workspace_delta_recovery") == summary
                            and candidate.get("plan_hash") == record["plan_hash"],
                            "corrupt_state", "Workspace delta recovery candidate chain binding is inconsistent")
            require(self.workspace_delta_recovery_iteration_history_valid(
                        state, attempt, record, attempt.get("iteration")),
                    "corrupt_state", "Workspace delta recovery iteration history is inconsistent")

    def workspace_delta_recovery_chain_for_attempt(self, state, attempt):
        record = self.workspace_delta_recovery_for_attempt(state, attempt)
        if record is None:
            return []
        amendments = [item for item in state.get("workspace_delta_recovery_amendments", [])
                      if item.get("attempt_id") == attempt["id"]]
        return [record, *amendments]

    def workspace_delta_recovery_entry_for_iteration(self, state, record, iteration):
        attempt = next((item for item in state["attempts"] if item.get("id") == record.get("attempt_id")), None)
        if attempt is None or type(iteration) is not int:
            return None
        chain = self.workspace_delta_recovery_chain_for_attempt(state, attempt)
        eligible = [item for item in chain if item.get("iteration", 0) <= iteration]
        return eligible[-1] if eligible else None

    def workspace_delta_recovery_candidate_summary(self, record, iteration=None, entry=None):
        head = entry or record
        return {"recovery_id": record["id"], "recovery_sha256": digest(record),
                "origin_iteration": record["iteration"], "iteration": record["iteration"] if iteration is None else iteration,
                "chain_head_id": head["id"], "chain_head_sha256": digest(head),
                "chain_sequence": head.get("sequence", 0),
                "manifest_sha256": head["manifest_sha256"],
                "manifest": copy.deepcopy(head["manifest"])}

    def workspace_delta_recovery_entry_event(self, state, record, entry):
        if entry["id"] == record["id"]:
            action, key = "record-workspace-recovery", "workspace_delta_recovery_id"
        else:
            action, key = "amend-workspace-recovery", "workspace_delta_recovery_amendment_id"
        matches = [item for item in state.get("events", []) if isinstance(item, dict)
                   and item.get("action") == action and item.get(key) == entry["id"]
                   and item.get("attempt_id") == record["attempt_id"]]
        return matches[0] if len(matches) == 1 else None

    def workspace_delta_recovery_iteration_history_valid(self, state, attempt, record, target_iteration):
        origin = record.get("iteration")
        if (type(origin) is not int or type(target_iteration) is not int or target_iteration < origin
                or attempt.get("id") != record.get("attempt_id") or attempt.get("task_id") != "B14"):
            return False
        submissions = attempt.get("submissions", [])
        reviews = attempt.get("reviews", [])
        events = state.get("events", [])
        if not isinstance(submissions, list) or not isinstance(reviews, list):
            return False
        for iteration in range(origin, target_iteration):
            candidates = [item for item in submissions if isinstance(item, dict)
                          and item.get("attempt_id") == attempt["id"] and item.get("iteration") == iteration]
            if len(candidates) != 1:
                return False
            candidate = candidates[0]
            entry = self.workspace_delta_recovery_entry_for_iteration(state, record, iteration)
            if entry is None:
                return False
            summary = self.workspace_delta_recovery_candidate_summary(record, iteration, entry)
            unsigned = copy.deepcopy(candidate)
            recorded_digest = unsigned.pop("digest", None)
            if (candidate.get("task_id") != "B14" or candidate.get("attempt_id") != attempt["id"]
                    or candidate.get("task_hash") != attempt.get("task_hash")
                    or candidate.get("workspace_delta_recovery") != summary
                    or candidate.get("plan_hash") != record.get("plan_hash")
                    or candidate.get("base_head") != record.get("base_head")
                    or candidate.get("index_sha256") != record.get("baseline_index_sha256")
                    or not recorded_digest or recorded_digest != digest(unsigned)):
                return False
            entry_event = self.workspace_delta_recovery_entry_event(state, record, entry)
            submit_events = [item for item in events if isinstance(item, dict) and item.get("action") == "submit"
                             and item.get("attempt_id") == attempt["id"]
                             and item.get("candidate_digest") == recorded_digest]
            if (entry_event is None or len(submit_events) != 1
                    or submit_events[0].get("sequence", 0) <= entry_event.get("sequence", 0)
                    or submit_events[0].get("candidate_iteration") != iteration
                    or submit_events[0].get("workspace_delta_recovery") != summary):
                return False
            matching = [item for item in reviews if isinstance(item, dict)
                        and item.get("iteration") == iteration
                        and item.get("candidate_digest") == recorded_digest]
            if len(matching) != 1:
                return False
            review = matching[0]
            evidence = review.get("evidence")
            payload = evidence.get("payload") if isinstance(evidence, dict) else None
            reviewer = next((item for item in attempt.get("reviewers", [])
                             if isinstance(item, dict) and item.get("target") == review.get("reviewer")
                             and item.get("run_id") == review.get("run_id")), None)
            review_events = [item for item in events if isinstance(item, dict) and item.get("action") == "review"
                             and item.get("attempt_id") == attempt["id"]
                             and item.get("candidate_digest") == recorded_digest]
            stale_observation = review.get("workspace_delta_stale_observation")
            if stale_observation is not None:
                try:
                    self.validate_workspace_delta_stale_observation(
                        state, attempt, candidate, stale_observation, require_live=False)
                except Refusal:
                    return False
            if (review.get("result") != "NO_GO" or review.get("workspace_delta_recovery") != summary
                    or not isinstance(payload, dict) or reviewer is None or len(review_events) != 1
                    or review_events[0].get("sequence", 0) <= submit_events[0].get("sequence", 0)
                    or review_events[0].get("review_result") != "NO_GO"
                    or review_events[0].get("workspace_delta_recovery") != summary
                    or review_events[0].get("workspace_delta_stale_observation") != stale_observation
                    or payload.get("task_id") != "B14" or payload.get("attempt_id") != attempt["id"]
                    or payload.get("iteration") != iteration or payload.get("candidate_digest") != recorded_digest
                    or payload.get("plan_hash") != record.get("plan_hash") or payload.get("result") != "NO_GO"
                    or payload.get("review_nonce") != reviewer.get("nonce")
                    or payload.get("workspace_delta_recovery") != summary
                    or payload.get("workspace_delta_stale_observation") != stale_observation):
                return False
        return True

    def snapshot_recovery_bindings(self, state, attempt):
        result = []
        for proposal in state.get("snapshot_recoveries", []):
            if proposal.get("target_attempt_id") != attempt["id"]:
                continue
            amendment = self.snapshot_recovery_amendment(state, proposal)
            result.append({"proposal_id": proposal["id"],
                           "proposal_sha256": self.snapshot_recovery_proposal_digest(proposal),
                           "snapshot_id": proposal["snapshot_id"],
                           "manifest_sha256": digest(self.snapshot_recovery_effective_manifest(proposal, amendment)),
                           "amendment_id": amendment["id"] if amendment else None,
                           "amendment_sha256": digest(amendment) if amendment else None})
        return sorted(result, key=lambda item: item["proposal_id"])

    def current_snapshot_recovery_bindings(self, state, attempt):
        """Return immutable origin bindings only when the current iteration is live."""
        proposals = [item for item in state.get("snapshot_recoveries", [])
                     if item.get("target_attempt_id") == attempt.get("id")]
        live = self.recovery_candidate_proposals(state, attempt)
        if len(live) != len(proposals):
            return []
        return self.snapshot_recovery_bindings(state, attempt)

    @staticmethod
    def snapshot_recovery_proposal_digest(proposal):
        return digest(proposal)

    @staticmethod
    def snapshot_recovery_amendment(state, proposal):
        matches = [item for item in state.get("snapshot_recovery_amendments", [])
                   if item.get("proposal_id") == proposal["id"]]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def snapshot_recovery_effective_manifest(proposal, amendment=None, continuation=None):
        if continuation is not None:
            return continuation["manifest"]
        return amendment["manifest"] if amendment else proposal["manifest"]

    def snapshot_recovery_continuation_for_iteration(self, state, proposal, iteration):
        matches = [item for item in state.get("snapshot_recovery_continuations", [])
                   if item.get("proposal_id") == proposal["id"]
                   and item.get("target_attempt_id") == proposal["target_attempt_id"]
                   and item.get("iteration") == iteration]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def snapshot_recovery_continuation_chain_started(state, proposal):
        return any(item.get("proposal_id") == proposal["id"]
                   and item.get("target_attempt_id") == proposal["target_attempt_id"]
                   for item in state.get("snapshot_recovery_continuations", []))

    def snapshot_recovery_entry_for_iteration(self, state, proposal, iteration, amendment=None):
        if type(iteration) is not int or iteration < proposal["iteration"]:
            return None, None
        if iteration == proposal["iteration"]:
            return amendment, None
        continuation = self.snapshot_recovery_continuation_for_iteration(state, proposal, iteration)
        if continuation is not None:
            return amendment, continuation
        # Existing same-manifest repair iterations may keep using the original
        # pre-candidate amendment. Once a continuation chain starts, every next
        # iteration must add its own continuation before another candidate.
        has_continuation = self.snapshot_recovery_continuation_chain_started(state, proposal)
        return (amendment, None) if not has_continuation else (None, None)

    def snapshot_recovery_candidate_summary(self, proposal, attempt, iteration, amendment=None, continuation=None):
        manifest = self.snapshot_recovery_effective_manifest(proposal, amendment, continuation)
        summary = {"proposal_id": proposal["id"], "snapshot_id": proposal["snapshot_id"],
                   "target_attempt_id": proposal["target_attempt_id"], "iteration": iteration,
                   "proposal_iteration": proposal["iteration"], "manifest": copy.deepcopy(manifest),
                   "manifest_sha256": digest(manifest),
                   "source_provenance": copy.deepcopy(proposal["source_provenance"]),
                   "authorization": copy.deepcopy(proposal["authorization"]),
                   "proposal_sha256": self.snapshot_recovery_proposal_digest(proposal)}
        if amendment:
            summary.update(amendment_id=amendment["id"], amendment_iteration=amendment["iteration"],
                           amendment_sha256=digest(amendment),
                           proposal_manifest_sha256=digest(proposal["manifest"]))
        if continuation:
            summary.update(continuation_id=continuation["id"],
                           continuation_iteration=continuation["iteration"],
                           continuation_sequence=continuation["sequence"],
                           continuation_sha256=digest(continuation),
                           continuation_manifest_sha256=continuation["manifest_sha256"],
                           continuation_previous_entry_id=continuation["previous_entry_id"],
                           continuation_previous_entry_sha256=continuation["previous_entry_sha256"],
                           workspace_delta_recovery_id=continuation["workspace_delta_recovery_id"],
                           workspace_delta_recovery_sha256=continuation["workspace_delta_recovery_sha256"])
        return summary

    def snapshot_recovery_iteration_history_valid(self, state, attempt, proposal, target_iteration, amendment=None):
        """Require each reused iteration to follow a submitted candidate and matching NO_GO."""
        origin = proposal["iteration"]
        if (type(origin) is not int or type(target_iteration) is not int or target_iteration < origin
                or attempt.get("task_id") != "B14" or attempt.get("id") != proposal["target_attempt_id"]):
            return False
        submissions = attempt.get("submissions", [])
        reviews = attempt.get("reviews", [])
        if not isinstance(submissions, list) or not isinstance(reviews, list):
            return False
        for iteration in range(origin, target_iteration):
            continuation = (self.snapshot_recovery_continuation_for_iteration(state, proposal, iteration)
                            if iteration > origin else None)
            earlier_continuation = any(item.get("proposal_id") == proposal["id"]
                                       and item.get("target_attempt_id") == attempt["id"]
                                       and item.get("iteration", 0) < iteration
                                       for item in state.get("snapshot_recovery_continuations", []))
            if iteration > origin and continuation is None and earlier_continuation:
                return False
            matching = []
            for candidate in submissions:
                if not isinstance(candidate, dict) or candidate.get("iteration") != iteration \
                        or candidate.get("attempt_id") != attempt["id"]:
                    continue
                summaries = [item for item in candidate.get("snapshot_recoveries", [])
                             if isinstance(item, dict) and item.get("proposal_id") == proposal["id"]]
                if summaries:
                    matching.append((candidate, summaries))
            if len(matching) != 1:
                return False
            candidate, summaries = matching[0]
            if len(summaries) != 1 or summaries[0] != self.snapshot_recovery_candidate_summary(
                    proposal, attempt, iteration, amendment, continuation):
                return False
            recorded_digest = candidate.get("digest")
            unsigned = copy.deepcopy(candidate)
            unsigned.pop("digest", None)
            if (not recorded_digest or recorded_digest != digest(unsigned)
                    or candidate.get("task_id") != "B14" or candidate.get("task_hash") != attempt.get("task_hash")
                    or candidate.get("plan_hash") != attempt.get("plan_hash")
                    or candidate.get("base_head") != attempt.get("base_head")
                    or candidate.get("index_sha256") != attempt.get("baseline_index_sha256")
                    or not isinstance(candidate.get("manifest"), dict)
                    or sorted(candidate.get("manifest", {})) != attempt.get("files")):
                return False
            matching_reviews = [review for review in reviews if isinstance(review, dict)
                                and review.get("iteration") == iteration
                                and review.get("candidate_digest") == recorded_digest]
            if len(matching_reviews) != 1:
                return False
            review = matching_reviews[0]
            evidence = review.get("evidence")
            payload = evidence.get("payload") if isinstance(evidence, dict) else None
            reviewer = next((item for item in attempt.get("reviewers", [])
                             if isinstance(item, dict) and item.get("target") == review.get("reviewer")
                             and item.get("run_id") == review.get("run_id")), None)
            if (review.get("result") != "NO_GO" or not isinstance(payload, dict) or reviewer is None
                    or payload.get("task_id") != "B14" or payload.get("attempt_id") != attempt["id"]
                    or payload.get("iteration") != iteration or payload.get("candidate_digest") != recorded_digest
                    or payload.get("plan_hash") != candidate.get("plan_hash") or payload.get("result") != "NO_GO"
                    or payload.get("review_nonce") != reviewer.get("nonce")):
                return False
            if continuation is not None and payload.get("snapshot_recoveries") != candidate.get("snapshot_recoveries"):
                return False
        return True

    def recovery_candidate_proposals(self, state, attempt):
        if attempt.get("task_id") != "B14" or not attempt.get("claimed_at"):
            return []
        snapshots = {item["id"]: item for item in state.get("snapshots", [])}
        result = []
        for proposal in state.get("snapshot_recoveries", []):
            amendment = self.snapshot_recovery_amendment(state, proposal)
            if (proposal["target_attempt_id"] == attempt["id"]
                    and self.snapshot_recovery_iteration_history_valid(
                        state, attempt, proposal, attempt["iteration"], amendment)):
                snapshot = snapshots.get(proposal["snapshot_id"])
                if snapshot is None:
                    continue
                amendment, continuation = self.snapshot_recovery_entry_for_iteration(
                    state, proposal, attempt["iteration"], amendment)
                if (amendment is None and continuation is None
                        and self.snapshot_recovery_continuation_chain_started(state, proposal)):
                    continue
                try:
                    current = self.exact_manifest(snapshot["files"])
                except Refusal:
                    continue
                manifest = self.snapshot_recovery_effective_manifest(proposal, amendment, continuation)
                if current != manifest or current == snapshot["manifest"]:
                    continue
                summary = self.snapshot_recovery_candidate_summary(
                    proposal, attempt, attempt["iteration"], amendment, continuation)
                result.append(summary)
        return sorted(result, key=lambda item: (item["snapshot_id"], item["proposal_id"]))

    def candidate_recoveries_current(self, state, attempt, *, allow_stale_workspace_delta=False):
        candidate = attempt.get("candidate") or {}
        require(candidate.get("snapshot_recoveries", []) == self.recovery_candidate_proposals(state, attempt),
                "stale_snapshot_recovery", "Snapshot recovery proposal changed after candidate submission")
        record = self.workspace_delta_recovery_for_attempt(state, attempt)
        if record is None:
            require(candidate.get("workspace_delta_recovery") is None,
                    "stale_workspace_delta_recovery", "Candidate workspace recovery has no immutable origin")
            self.outside_changes(state, attempt)
            return False
        require(self.workspace_delta_recovery_matches_attempt(state, record, attempt),
                "stale_workspace_delta_recovery", "Workspace delta recovery pins changed after candidate submission")
        entry = self.workspace_delta_recovery_current_entry(state, attempt)
        require(entry is not None
                and self.current_snapshot_recovery_bindings(state, attempt) == record["snapshot_recovery_bindings"],
                "stale_workspace_delta_recovery", "Snapshot recovery binding changed after candidate submission")
        expected_summary = self.workspace_delta_recovery_candidate_summary(record, attempt["iteration"], entry)
        require(candidate.get("workspace_delta_recovery") == expected_summary,
                "stale_workspace_delta_recovery", "Candidate workspace recovery chain binding changed")
        current_delta = self.unexplained_workspace_delta(state, attempt)
        if current_delta != entry["manifest"]:
            require(allow_stale_workspace_delta, "stale_workspace_delta_recovery",
                    "Workspace delta changed after candidate submission")
            return True
        self.outside_changes(state, attempt)
        return False

    def accepted_snapshot_recovery(self, state, proposal):
        amendment = self.snapshot_recovery_amendment(state, proposal)
        for event in reversed(state.get("events", [])):
            acceptances = event.get("snapshot_recovery_acceptances", []) if isinstance(event, dict) else []
            if not isinstance(acceptances, list):
                return None
            for acceptance in acceptances:
                if not isinstance(acceptance, dict) or acceptance.get("proposal_id") != proposal["id"]:
                    continue
                attempt = next((item for item in state["attempts"] if item["id"] == proposal["target_attempt_id"]), None)
                if not attempt or attempt.get("status") != "accepted" or attempt.get("task_id") != "B14":
                    return None
                candidate, review = attempt.get("candidate"), attempt.get("review")
                if not isinstance(candidate, dict) or not isinstance(review, dict):
                    return None
                continuation = self.snapshot_recovery_continuation_for_iteration(
                    state, proposal, candidate.get("iteration"))
                effective_manifest = self.snapshot_recovery_effective_manifest(proposal, amendment, continuation)
                candidate_summary = next((item for item in candidate.get("snapshot_recoveries", [])
                                          if isinstance(item, dict) and item.get("proposal_id") == proposal["id"]), None)
                review_evidence = review.get("evidence")
                if not isinstance(candidate_summary, dict) or not isinstance(review_evidence, dict):
                    return None
                if (event.get("action") != "accept" or event.get("attempt_id") != attempt["id"]
                        or event.get("sequence") != attempt.get("accepted_revision")
                        or acceptance.get("attempt_id") != attempt["id"]
                        or acceptance.get("snapshot_id") != proposal["snapshot_id"]
                        or acceptance.get("iteration") != attempt.get("iteration")
                        or acceptance.get("candidate_digest") != candidate.get("digest")
                        or acceptance.get("candidate_digest") != review.get("candidate_digest")
                        or acceptance.get("review_result") != "GO" or review.get("result") != "GO"
                        or acceptance.get("reviewer") != review.get("reviewer")
                        or acceptance.get("reviewer_run_id") != review.get("run_id")
                        or acceptance.get("review_evidence_sha256") != review_evidence.get("sha256")
                        or acceptance.get("acceptor") != attempt.get("orchestrator", {}).get("target")
                        or acceptance.get("acceptor_run_id") != attempt.get("orchestrator", {}).get("run_id")
                        or event.get("actor") != acceptance.get("acceptor")
                        or event.get("run_id") != acceptance.get("acceptor_run_id")
                        or acceptance.get("proposal_sha256") != self.snapshot_recovery_proposal_digest(proposal)
                        or acceptance.get("manifest_sha256") != digest(effective_manifest)
                        or acceptance.get("amendment_id") != (amendment["id"] if amendment else None)
                        or acceptance.get("amendment_sha256") != (digest(amendment) if amendment else None)
                        or acceptance.get("continuation_id") != (continuation["id"] if continuation else None)
                        or acceptance.get("continuation_sha256") != (digest(continuation) if continuation else None)
                        or acceptance.get("continuation_sequence") != (continuation["sequence"] if continuation else None)
                        or candidate_summary.get("snapshot_id") != proposal["snapshot_id"]
                        or candidate_summary.get("target_attempt_id") != proposal["target_attempt_id"]
                        or candidate_summary.get("proposal_iteration") != proposal["iteration"]
                        or candidate_summary.get("iteration") != candidate.get("iteration")
                        or candidate_summary.get("manifest") != effective_manifest
                        or candidate_summary.get("manifest_sha256") != digest(effective_manifest)
                        or candidate_summary.get("source_provenance") != proposal["source_provenance"]
                        or candidate_summary.get("authorization") != proposal["authorization"]
                        or candidate_summary.get("amendment_id") != (amendment["id"] if amendment else None)
                        or candidate_summary.get("amendment_sha256") != (digest(amendment) if amendment else None)
                        or candidate_summary.get("continuation_id") != (continuation["id"] if continuation else None)
                        or candidate_summary.get("continuation_sha256") != (digest(continuation) if continuation else None)
                        or candidate_summary.get("continuation_sequence") != (continuation["sequence"] if continuation else None)
                        or candidate_summary.get("continuation_manifest_sha256") != (
                            continuation["manifest_sha256"] if continuation else None)
                        or candidate_summary.get("continuation_previous_entry_id") != (
                            continuation["previous_entry_id"] if continuation else None)
                        or candidate_summary.get("continuation_previous_entry_sha256") != (
                            continuation["previous_entry_sha256"] if continuation else None)
                        or candidate_summary.get("workspace_delta_recovery_id") != (
                            continuation["workspace_delta_recovery_id"] if continuation else None)
                        or candidate_summary.get("workspace_delta_recovery_sha256") != (
                            continuation["workspace_delta_recovery_sha256"] if continuation else None)
                        or candidate.get("iteration") != attempt.get("iteration")
                        or review.get("iteration") != candidate.get("iteration")
                        or not self.snapshot_recovery_iteration_history_valid(
                            state, attempt, proposal, candidate.get("iteration"), amendment)):
                    return None
                if continuation is not None:
                    review_payload = review_evidence.get("payload")
                    if (not isinstance(review_payload, dict)
                            or review_payload.get("snapshot_recoveries") != candidate.get("snapshot_recoveries")
                            or review.get("snapshot_recoveries") != candidate.get("snapshot_recoveries")):
                        return None
                if candidate.get("workspace_delta_recovery") is not None and not self.paired_recovery_acceptances_valid(
                        attempt, event):
                    return None
                return acceptance
        return None

    def paired_recovery_acceptances_valid(self, attempt, event):
        """Require snapshot and workspace-delta receipts to be accepted as one bundle."""
        candidate, review = attempt.get("candidate"), attempt.get("review")
        if not isinstance(candidate, dict) or not isinstance(review, dict):
            return False
        workspace_summary = candidate.get("workspace_delta_recovery")
        snapshot_summaries = candidate.get("snapshot_recoveries", [])
        if workspace_summary is None or not isinstance(snapshot_summaries, list) or not snapshot_summaries:
            return False
        workspace_approvals = event.get("workspace_delta_recovery_acceptances", [])
        snapshot_approvals = event.get("snapshot_recovery_acceptances", [])
        if (not isinstance(workspace_approvals, list) or len(workspace_approvals) != 1
                or not isinstance(snapshot_approvals, list) or len(snapshot_approvals) != len(snapshot_summaries)):
            return False
        evidence = review.get("evidence")
        common = {"attempt_id": attempt.get("id"), "iteration": attempt.get("iteration"),
                  "candidate_digest": candidate.get("digest"), "review_result": "GO",
                  "reviewer": review.get("reviewer"), "reviewer_run_id": review.get("run_id"),
                  "review_evidence_sha256": evidence.get("sha256") if isinstance(evidence, dict) else None,
                  "acceptor": attempt.get("orchestrator", {}).get("target"),
                  "acceptor_run_id": attempt.get("orchestrator", {}).get("run_id")}
        unsigned = copy.deepcopy(candidate)
        recorded_digest = unsigned.pop("digest", None)
        if (event.get("action") != "accept" or event.get("attempt_id") != attempt.get("id")
                or event.get("sequence") != attempt.get("accepted_revision")
                or event.get("actor") != common["acceptor"] or event.get("run_id") != common["acceptor_run_id"]
                or not recorded_digest or recorded_digest != digest(unsigned)
                or candidate.get("task_id") != "B14" or candidate.get("attempt_id") != attempt.get("id")
                or candidate.get("task_hash") != attempt.get("task_hash")
                or candidate.get("plan_hash") != attempt.get("plan_hash")
                or candidate.get("base_head") != attempt.get("base_head")
                or candidate.get("index_sha256") != attempt.get("baseline_index_sha256")
                or review.get("result") != "GO" or review.get("candidate_digest") != candidate.get("digest")
                or review.get("iteration") != attempt.get("iteration")):
            return False
        workspace = workspace_approvals[0]
        if not isinstance(workspace, dict) or any(workspace.get(key) != value for key, value in common.items()):
            return False
        if (workspace.get("recovery_id") != workspace_summary.get("recovery_id")
                or workspace.get("recovery_sha256") != workspace_summary.get("recovery_sha256")
                or workspace.get("manifest_sha256") != workspace_summary.get("manifest_sha256")
                or workspace.get("chain_head_id") != workspace_summary.get("chain_head_id")
                or workspace.get("chain_head_sha256") != workspace_summary.get("chain_head_sha256")
                or workspace.get("chain_sequence") != workspace_summary.get("chain_sequence")):
            return False
        expected_snapshot_ids = {item.get("proposal_id") for item in snapshot_summaries if isinstance(item, dict)}
        if len(expected_snapshot_ids) != len(snapshot_summaries):
            return False
        observed_snapshot_ids = set()
        for approval in snapshot_approvals:
            if not isinstance(approval, dict) or any(approval.get(key) != value for key, value in common.items()):
                return False
            proposal_id = approval.get("proposal_id")
            summary = next((item for item in snapshot_summaries
                            if isinstance(item, dict) and item.get("proposal_id") == proposal_id), None)
            if (summary is None or proposal_id in observed_snapshot_ids
                    or approval.get("snapshot_id") != summary.get("snapshot_id")
                    or approval.get("proposal_sha256") != summary.get("proposal_sha256")
                    or approval.get("manifest_sha256") != summary.get("manifest_sha256")
                    or approval.get("amendment_id") != summary.get("amendment_id")
                    or approval.get("amendment_sha256") != summary.get("amendment_sha256")
                    or approval.get("continuation_id") != summary.get("continuation_id")
                    or approval.get("continuation_sha256") != summary.get("continuation_sha256")
                    or approval.get("continuation_sequence") != summary.get("continuation_sequence")
                    or approval.get("continuation_manifest_sha256") != summary.get("continuation_manifest_sha256")
                    or approval.get("continuation_previous_entry_id") != summary.get("continuation_previous_entry_id")
                    or approval.get("continuation_previous_entry_sha256") != summary.get("continuation_previous_entry_sha256")
                    or approval.get("workspace_delta_recovery_id") != summary.get("workspace_delta_recovery_id")
                    or approval.get("workspace_delta_recovery_sha256") != summary.get("workspace_delta_recovery_sha256")
                    or (bool(summary.get("continuation_id"))
                        and (summary.get("workspace_delta_recovery_id") != workspace_summary.get("recovery_id")
                             or summary.get("workspace_delta_recovery_sha256") != workspace_summary.get("recovery_sha256")))):
                return False
            observed_snapshot_ids.add(proposal_id)
        return observed_snapshot_ids == expected_snapshot_ids

    @staticmethod
    def lineage(state):
        result = {}
        for attempt in sorted((item for item in state["attempts"] if item["status"] == "accepted"), key=lambda item: item["accepted_revision"]):
            for name, entry in attempt["candidate"]["manifest"].items():
                result[name] = {"attempt_id": attempt["id"], "entry": entry}
        return result

    def accepted(self, state, plan, task_id, permitted_attempt=None):
        gate = state["gates"].get(task_id)
        if plan["tasks"][task_id].get("questions"):
            return bool(gate and gate["task_hash"] == plan["task_hashes"][task_id])
        if not self.gate_available(state, plan, task_id):
            return False
        records = [item for item in state["attempts"] if item["task_id"] == task_id and item["status"] == "accepted"
                   and item["task_hash"] == plan["task_hashes"][task_id]
                   and item.get("contract_hash") == digest(plan["contracts"])]
        if not records:
            return False
        record = records[-1]
        lineage = self.lineage(state)
        current = self.manifest(record["files"])
        return all(name in lineage and (current[name] == lineage[name]["entry"] or
                   (permitted_attempt is not None and name in permitted_attempt["files"]
                    and permitted_attempt["baseline_scope"].get(name) == lineage[name]["entry"])) for name in record["files"])

    @staticmethod
    def gate_available(state, plan, task_id):
        task = plan["tasks"][task_id]
        gate = state["gates"].get(task_id)
        return not (task.get("questions") or task.get("additional_gate")) or bool(
            gate and gate["task_hash"] == plan["task_hashes"][task_id])

    def ready_reasons(self, state, plan, task_id):
        require(task_id in plan["tasks"], "unknown_task", f"Unknown task: {task_id}")
        task = plan["tasks"][task_id]
        reasons = []
        if any(item["task_id"] == task_id and item["status"] in ACTIVE for item in state["attempts"]):
            reasons.append("occupied")
        if self.accepted(state, plan, task_id):
            reasons.append("accepted")
        if task.get("questions") and not self.accepted(state, plan, task_id):
            reasons.append("external_gate_required")
        gate = state["gates"].get(task_id)
        if task.get("additional_gate") and not (gate and gate["task_hash"] == plan["task_hashes"][task_id]):
            reasons.append("additional_gate_required")
        for dep in task["depends_on"]:
            if not self.accepted(state, plan, dep):
                reasons.append("dependency_not_accepted_or_changed:" + dep)
        if any(item["status"] in ACTIVE and item["plan_hash"] != plan["hash"] for item in state["attempts"]):
            reasons.append("active_plan_drift")
        return reasons

    @staticmethod
    def overlap(first, second):
        a, b = first.casefold().rstrip("/"), second.casefold().rstrip("/")
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")

    def scope(self, files):
        require(files, "invalid_scope", "At least one exact file required")
        result = sorted({self.path(value) for value in files})
        for index, name in enumerate(result):
            for other in result[index + 1:]:
                require(not self.overlap(name, other), "invalid_scope", "Duplicate/case alias/ancestor scopes rejected")
        return result

    @staticmethod
    def resources(values):
        require(all(nonempty(value) and re.fullmatch(r"[a-z0-9][a-z0-9:._/-]*", value) for value in values),
                "invalid_resource", "Canonical lowercase resource identifiers required")
        return sorted(set(values))

    def conflicts(self, state, files, resources, except_id=None):
        for attempt in state["attempts"]:
            if attempt["status"] not in ACTIVE or attempt["id"] == except_id:
                continue
            require(not any(self.overlap(a, b) for a in files for b in attempt["files"]), "path_conflict", f"Paths reserved by {attempt['id']}")
            require(not set(resources).intersection(attempt["resources"]), "resource_conflict", f"Resources reserved by {attempt['id']}")

    def clean_scope(self, state, files, adopted, recovered=(), task_id=None):
        recovered_entries = {}
        for prior_id in recovered:
            prior = self.attempt(state, prior_id)
            recovery = prior.get("operator_abandonment", {})
            require(prior["status"] == "cancelled" and prior.get("claimed_at")
                    and prior["task_id"] == task_id and recovery.get("next_orchestrator") == state["orchestrator"]
                    and recovery.get("kind") == RECOVERY_EVIDENCE_KIND,
                    "invalid_recovery_adoption", "Only the recipient may explicitly adopt a recovered claim for the same task")
            require(self.manifest(prior["files"]) == prior.get("closed_manifest"),
                    "recovery_manifest_drift", "Recovered bytes or modes changed before adoption")
            require(any(name in prior["files"] for name in files),
                    "invalid_recovery_adoption", "Recovery adoption must include a previously claimed path")
            recovered_entries.update(prior["closed_manifest"])
        dirty = {value.casefold() for value in self.dirty()}
        tracked = {os.fsdecode(value).casefold() for value in self.git("ls-files", "-z").split(b"\0") if value}
        lineage = self.lineage(state)
        current = self.manifest(files)
        for name in files:
            is_dirty = name.casefold() in dirty or (current[name]["kind"] != "absent" and name.casefold() not in tracked)
            if is_dirty:
                prior = lineage.get(name)
                require((prior and prior["attempt_id"] in adopted and current[name] == prior["entry"])
                        or recovered_entries.get(name) == current[name],
                        "dirty_scope", f"Preserve pre-existing dirty file: {name}; only exact accepted lineage may be explicitly adopted")
        for prior_id in adopted:
            require(any(item["id"] == prior_id and item["status"] == "accepted" for item in state["attempts"]),
                    "invalid_adoption", "Adoption must reference an accepted attempt")

    def check(self, state, plan, args):
        reasons = self.ready_reasons(state, plan, args.task)
        files, resources = self.scope(args.file), self.resources(args.resource)
        self.conflicts(state, files, resources)
        self.clean_scope(state, files, args.adopt_accepted, args.adopt_recovered, args.task)
        return {"ready": not reasons, "reasons": reasons, "files": files, "resources": resources}

    def require_plan(self, attempt, plan):
        require(attempt["plan_hash"] == plan["hash"] and attempt["task_hash"] == plan["task_hashes"].get(attempt["task_id"]),
                "plan_drift", "Plan changed; orchestrator must explicitly rebase and obtain fresh review")

    @staticmethod
    def root_actor(state, actor, run_id):
        require(TARGET.fullmatch(actor or ""), "invalid_identity", "Canonical native orchestrator target required")
        require(state["orchestrator"] == {"target": actor, "run_id": run_id}, "wrong_role", "Only the recorded orchestrator target/run may perform this action")

    @staticmethod
    def role(attempt, actor, run_id, role):
        binding = attempt.get(role)
        require(binding and binding["target"] == actor and binding["run_id"] == run_id, "wrong_role", f"Actual bound {role} target/run required")

    def binding(self, state, attempt, evidence, role):
        data = evidence["payload"]
        require(data.get("tool") == "collaboration.spawn_agent" and data.get("parent_target") == state["orchestrator"]["target"]
                and data.get("parent_run_id") == state["orchestrator"]["run_id"], "invalid_binding", "Actual parent target/run spawn receipt required")
        require(data.get("attempt_id") == attempt["id"] and data.get("task_id") == attempt["task_id"] and data.get("role") == role,
                "invalid_binding", "Binding task/attempt/role mismatch")
        target, run_id = data.get("target"), data.get("run_id")
        require(isinstance(target, str) and TARGET.fullmatch(target) and target != state["orchestrator"]["target"], "invalid_identity", "Distinct actual native target required")
        uuid_text(run_id)
        uuid_text(data.get("parent_run_id"))
        require(run_id != data["parent_run_id"], "invalid_identity", "Child and parent run IDs must differ")
        fresh(data.get("observed_at"))
        for item in state["attempts"]:
            for prior in ([item["worker"]] if item.get("worker") else []) + item["reviewers"]:
                require(prior["target"] != target and prior["run_id"] != run_id, "reused_identity", "Worker/reviewer identities must be fresh globally")
        return {"target": target, "run_id": run_id, "evidence": evidence}

    @staticmethod
    def checks(payload, *, review=False):
        checks = payload.get("checks")
        require(nonempty(payload.get("summary")) and isinstance(checks, list) and checks, "invalid_evidence", "Summary and nonempty checks required")
        for check in checks:
            require(isinstance(check, dict) and nonempty(check.get("name")) and nonempty(check.get("evidence"))
                    and check.get("status") in {"passed", "failed", "not_run", "not_required"}, "invalid_evidence", "Each check requires name/status/evidence")
        if review and payload.get("result") == "GO":
            require(all(item["status"] in {"passed", "not_required"} for item in checks) and any(item["status"] == "passed" for item in checks),
                    "invalid_review", "GO requires observed passed checks; failed/not_run cannot be accepted")

    def candidate_current(self, attempt):
        candidate = attempt.get("candidate")
        require(isinstance(candidate, dict), "stale_candidate", "Submitted candidate is missing")
        unsigned = copy.deepcopy(candidate)
        recorded_digest = unsigned.pop("digest", None)
        require(recorded_digest and recorded_digest == digest(unsigned),
                "stale_candidate", "Submitted candidate digest no longer matches its contents")
        require(candidate.get("task_id") == attempt.get("task_id")
                and candidate.get("attempt_id") == attempt.get("id")
                and candidate.get("iteration") == attempt.get("iteration")
                and candidate.get("plan_hash") == attempt.get("plan_hash")
                and candidate.get("task_hash") == attempt.get("task_hash")
                and candidate.get("base_head") == attempt.get("base_head")
                and candidate.get("index_sha256") == attempt.get("baseline_index_sha256"),
                "stale_candidate", "Submitted candidate identity or plan binding changed")
        require(self.manifest(attempt["files"]) == candidate["manifest"],
                "stale_candidate", "Scope bytes, mode, creation or deletion changed after submit/GO")
        require(self.git("rev-parse", "HEAD").decode().strip() == candidate["base_head"], "base_changed", "HEAD differs from submitted candidate")
        require(self.index_digest() == candidate["index_sha256"], "index_changed", "Staged modes/blobs changed after submission/GO")

    @staticmethod
    def invalidate(attempt):
        attempt["iteration"] += 1
        attempt["candidate"] = None
        attempt["reviewer"] = None
        attempt["review"] = None

    def snapshot_exemptions(self, state, attempt):
        snapshots = state.get("snapshots", [])
        snapshotted = {}
        for snapshot in snapshots:
            try:
                current_snapshot = self.exact_manifest(snapshot["files"])
            except Refusal as exc:
                raise Refusal("snapshot_drift", f"Snapshotted file is unavailable or unsafe: {snapshot['id']}") from exc
            expected = snapshot["manifest"]
            authorized_manifest = expected if current_snapshot == expected else None
            proposals = [item for item in state.get("snapshot_recoveries", []) if item["snapshot_id"] == snapshot["id"]]
            accepted_proposals = [item for item in proposals if self.accepted_snapshot_recovery(state, item)]
            if accepted_proposals:
                # Once accepted, a recovery pins the exact recovered manifest. A
                # later proposal cannot reopen that snapshot after further drift.
                for item in accepted_proposals:
                    accepted_attempt = next((candidate for candidate in state["attempts"]
                                             if candidate.get("id") == item.get("target_attempt_id")), None)
                    candidate = accepted_attempt.get("candidate") if accepted_attempt else None
                    iteration = candidate.get("iteration") if isinstance(candidate, dict) else None
                    continuation = self.snapshot_recovery_continuation_for_iteration(state, item, iteration)
                    manifest = self.snapshot_recovery_effective_manifest(
                        item, self.snapshot_recovery_amendment(state, item), continuation)
                    if manifest == current_snapshot:
                        authorized_manifest = manifest
                        break
            elif (authorized_manifest is None and attempt.get("task_id") == "B14" and attempt.get("claimed_at")
                  and attempt.get("status") in {"in_progress", "awaiting_review", "changes_requested"}):
                for item in proposals:
                    if (item["target_attempt_id"] != attempt["id"]
                            or not self.snapshot_recovery_iteration_history_valid(
                                state, attempt, item, attempt.get("iteration"),
                                self.snapshot_recovery_amendment(state, item))):
                        continue
                    amendment = self.snapshot_recovery_amendment(state, item)
                    amendment, continuation = self.snapshot_recovery_entry_for_iteration(
                        state, item, attempt.get("iteration"), amendment)
                    if (amendment is None and continuation is None
                            and self.snapshot_recovery_continuation_chain_started(state, item)):
                        continue
                    manifest = self.snapshot_recovery_effective_manifest(item, amendment, continuation)
                    if manifest == current_snapshot:
                        authorized_manifest = manifest
                        break
            require(authorized_manifest is not None, "snapshot_drift",
                    f"Snapshotted bytes or mode changed without matching recovery: {snapshot['id']}")
            for entry in authorized_manifest:
                snapshotted[entry["path"]] = {"kind": "file", "sha256": entry["sha256"], "size": entry["size"],
                                               "executable": entry["mode"] == "100755"}
        return snapshotted

    def unexplained_workspace_delta(self, state, attempt):
        """Return exact changed outside paths not covered by claim, lineage, or snapshot recovery."""
        snapshotted = self.snapshot_exemptions(state, attempt)
        before, current = attempt["baseline"], self.inventory()
        own = set(attempt["files"])
        foreign = [name for item in state["attempts"] if item["id"] != attempt["id"] and item["status"] in ACTIVE and item.get("claimed_at") for name in item["files"]]
        lineage = self.lineage(state)
        preserved = {}
        for peer in state["attempts"]:
            if peer["id"] != attempt["id"] and peer["status"] in {"released", "cancelled"} and peer.get("claimed_at"):
                preserved.update(peer.get("closed_manifest", {}))
        result = []
        for name in set(before) | set(current):
            old, new = before.get(name, {"kind": "absent"}), current.get(name, {"kind": "absent"})
            if old == new or name in own or any(self.overlap(name, permitted) for permitted in foreign):
                continue
            if ((name in lineage and lineage[name]["entry"] == new)
                    or preserved.get(name) == new or snapshotted.get(name) == new):
                continue
            if new.get("kind") == "absent":
                result.append({"path": name, "absent": True})
            else:
                result.append({"path": name, "mode": "100755" if new["executable"] else "100644",
                               "sha256": new["sha256"], "size": new["size"]})
        return sorted(result, key=lambda item: item["path"])

    def workspace_delta_recovery_for_attempt(self, state, attempt):
        matches = [item for item in state.get("workspace_delta_recoveries", [])
                   if item.get("attempt_id") == attempt["id"]]
        return matches[0] if len(matches) == 1 else None

    def workspace_delta_recovery_current_entry(self, state, attempt):
        chain = self.workspace_delta_recovery_chain_for_attempt(state, attempt)
        return chain[-1] if chain else None

    def workspace_delta_recovery_matches_attempt(self, state, record, attempt):
        return (attempt.get("task_id") == "B14" and attempt.get("claimed_at") and attempt.get("worker")
                and attempt.get("status") in {"in_progress", "awaiting_review", "changes_requested", "accepted"}
                and type(record.get("iteration")) is int
                and record.get("iteration") <= attempt.get("iteration")
                and record.get("plan_hash") == attempt.get("plan_hash")
                and record.get("base_head") == attempt.get("base_head")
                and record.get("baseline_index_sha256") == attempt.get("baseline_index_sha256")
                and record.get("baseline_sha256") == digest(attempt.get("baseline"))
                and record.get("scope_sha256") == digest(attempt.get("files"))
                and record.get("snapshot_recovery_bindings") == self.snapshot_recovery_bindings(state, attempt)
                and self.workspace_delta_recovery_iteration_history_valid(
                    state, attempt, record, attempt.get("iteration")))

    def current_workspace_delta_recovery_summary(self, state, attempt):
        if attempt.get("task_id") != "B14":
            return None
        record = self.workspace_delta_recovery_for_attempt(state, attempt)
        if record is None:
            return None
        require(self.workspace_delta_recovery_matches_attempt(state, record, attempt),
                "stale_workspace_delta_recovery", "Workspace delta recovery is bound to a different attempt iteration or plan")
        current_entry = self.workspace_delta_recovery_current_entry(state, attempt)
        require(current_entry is not None
                and self.current_snapshot_recovery_bindings(state, attempt) == record["snapshot_recovery_bindings"],
                "stale_workspace_delta_recovery",
                "The exact amended snapshot recovery bound by the workspace record is no longer current")
        current_delta = self.unexplained_workspace_delta(state, attempt)
        require(current_delta == current_entry["manifest"], "stale_workspace_delta_recovery",
                "Workspace delta changed after its latest append-only recovery record")
        return self.workspace_delta_recovery_candidate_summary(record, attempt.get("iteration"), current_entry)

    def accepted_workspace_delta_recovery(self, state, record):
        attempt = next((item for item in state["attempts"] if item.get("id") == record.get("attempt_id")), None)
        if not attempt or attempt.get("status") != "accepted" or attempt.get("task_id") != "B14":
            return None
        if not self.workspace_delta_recovery_matches_attempt(state, record, attempt):
            return None
        candidate, review = attempt.get("candidate"), attempt.get("review")
        if not isinstance(candidate, dict) or not isinstance(review, dict):
            return None
        entry = self.workspace_delta_recovery_entry_for_iteration(state, record, candidate.get("iteration"))
        if entry is None:
            return None
        summary = self.workspace_delta_recovery_candidate_summary(record, candidate.get("iteration"), entry)
        if candidate.get("workspace_delta_recovery") != summary:
            return None
        review_evidence = review.get("evidence")
        payload = review_evidence.get("payload") if isinstance(review_evidence, dict) else None
        if (review.get("result") != "GO" or review.get("workspace_delta_recovery") != summary
                or not isinstance(payload, dict) or payload.get("workspace_delta_recovery") != summary):
            return None
        if (candidate.get("iteration") != attempt.get("iteration")
                or candidate.get("plan_hash") != record.get("plan_hash")
                or review.get("iteration") != candidate.get("iteration")
                or review.get("candidate_digest") != candidate.get("digest")
                or candidate.get("digest") != digest({key: value for key, value in candidate.items() if key != "digest"})):
            return None
        for event in reversed(state.get("events", [])):
            approvals = event.get("workspace_delta_recovery_acceptances", []) if isinstance(event, dict) else []
            for approval in approvals if isinstance(approvals, list) else []:
                if not isinstance(approval, dict) or approval.get("recovery_id") != record["id"]:
                    continue
                if (event.get("action") == "accept" and event.get("attempt_id") == attempt["id"]
                        and event.get("sequence") == attempt.get("accepted_revision")
                        and approval.get("attempt_id") == attempt["id"]
                        and approval.get("iteration") == attempt.get("iteration")
                        and approval.get("candidate_digest") == candidate.get("digest")
                        and approval.get("review_result") == "GO"
                        and approval.get("reviewer") == review.get("reviewer")
                        and approval.get("reviewer_run_id") == review.get("run_id")
                        and approval.get("review_evidence_sha256") == review_evidence.get("sha256")
                        and approval.get("acceptor") == attempt.get("orchestrator", {}).get("target")
                        and approval.get("acceptor_run_id") == attempt.get("orchestrator", {}).get("run_id")
                        and approval.get("recovery_sha256") == digest(record)
                        and approval.get("manifest_sha256") == entry.get("manifest_sha256")
                        and approval.get("chain_head_id") == entry.get("id")
                        and approval.get("chain_head_sha256") == digest(entry)
                        and approval.get("chain_sequence") == entry.get("sequence", 0)
                        and approval.get("recovery_id") == summary["recovery_id"]
                        and event.get("actor") == approval.get("acceptor")
                        and event.get("run_id") == approval.get("acceptor_run_id")
                        and self.paired_recovery_acceptances_valid(attempt, event)):
                    return approval
                return None
        return None

    def outside_changes(self, state, attempt):
        require(attempt.get("status") in ACTIVE, "closed_attempt", "Closed attempt cannot use snapshot recovery")
        delta = self.unexplained_workspace_delta(state, attempt)
        own_record = self.workspace_delta_recovery_for_attempt(state, attempt)
        if own_record is not None:
            summary = self.current_workspace_delta_recovery_summary(state, attempt)
            head = self.workspace_delta_recovery_current_entry(state, attempt)
            require(head is not None and delta == head["manifest"] and summary is not None,
                    "stale_workspace_delta_recovery", "Workspace delta changed after its latest append-only recovery record")
            return
        if not delta:
            return
        accepted = []
        for record in state.get("workspace_delta_recoveries", []):
            approval = self.accepted_workspace_delta_recovery(state, record)
            attempt = next((item for item in state["attempts"] if item.get("id") == record.get("attempt_id")), None)
            head = self.workspace_delta_recovery_current_entry(state, attempt) if attempt else None
            if approval and head is not None and head["manifest"] == delta:
                accepted.append(record)
        require(len(accepted) == 1, "out_of_scope_change",
                f"Unexplained change outside exact scope: {delta[0]['path']}")

    def record_workspace_delta_recovery(self, state, plan, args, actor, attempt):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Workspace delta authorization must belong to this current chat run")
        require(nonempty(args.reason), "missing_reason", "Workspace delta recovery reason is required")
        require(args.user_authorization == WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                "invalid_workspace_delta_recovery_authorization", "The exact direct user authorization is required")
        require(not attempt.get("candidate") and not attempt.get("submissions") and not attempt.get("reviews")
                and not attempt.get("reviewers"), "workspace_delta_recovery_late",
                "Workspace delta recovery must be recorded before any candidate or review")
        require(attempt.get("task_id") == "B14" and attempt.get("status") == "in_progress"
                and attempt.get("claimed_at") and attempt.get("worker")
                and attempt.get("orchestrator") == state.get("orchestrator"),
                "invalid_workspace_delta_recovery_target", "A live claimed B14 attempt is required")
        self.require_plan(attempt, plan)
        require(not any(item.get("attempt_id") == attempt["id"]
                        for item in state.get("workspace_delta_recoveries", [])),
                "duplicate_workspace_delta_recovery", "Only one workspace delta recovery is allowed per B14 attempt")
        bindings = self.snapshot_recovery_bindings(state, attempt)
        require(len(bindings) == 1 and bindings[0].get("amendment_id"),
                "workspace_delta_recovery_snapshot", "The exact amended snapshot recovery must already be present")
        require(self.current_snapshot_recovery_bindings(state, attempt) == bindings,
                "workspace_delta_recovery_snapshot",
                "The amended snapshot manifest must still be the exact current candidate recovery")
        require(self.git("rev-parse", "HEAD").decode().strip() == attempt["base_head"]
                and self.index_digest() == attempt["baseline_index_sha256"],
                "workspace_delta_recovery_base", "Attempt HEAD and index must still match the live B14 claim")
        manifest = self.unexplained_workspace_delta(state, attempt)
        require(manifest, "workspace_delta_recovery_empty", "No unexplained workspace delta is available to preserve")
        require(isinstance(args.manifest_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256),
                "workspace_delta_manifest_digest_required", "Root must supply the pre-observed exact manifest SHA-256")
        require(args.manifest_sha256 == digest(manifest), "workspace_delta_manifest_changed",
                "Current unexplained workspace manifest differs from the pre-observed manifest")
        authorization = {"source": SNAPSHOT_RECOVERY_AUTH_SOURCE, "current_chat_run_id": args.run_id,
                         "authorized": True, "user_authorization": args.user_authorization}
        record = {"id": str(uuid.uuid4()), "attempt_id": attempt["id"], "task_id": "B14",
                  "iteration": attempt["iteration"], "plan_hash": attempt["plan_hash"],
                  "base_head": attempt["base_head"], "baseline_index_sha256": attempt["baseline_index_sha256"],
                  "baseline_sha256": digest(attempt["baseline"]), "scope_sha256": digest(attempt["files"]),
                  "snapshot_recovery_bindings": bindings, "manifest": manifest,
                  "manifest_sha256": digest(manifest), "authorization": authorization,
                  "actor": actor, "run_id": args.run_id, "recorded_at": iso(), "reason": args.reason}
        state.setdefault("workspace_delta_recoveries", []).append(record)
        return {"attempt": attempt, "workspace_delta_recovery": record}

    def amend_workspace_delta_recovery(self, state, plan, args, actor, attempt):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Workspace amendment authorization must belong to this current chat run")
        require(nonempty(args.reason), "missing_reason", "Workspace delta amendment reason is required")
        require(args.user_authorization == WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                "invalid_workspace_delta_recovery_authorization", "The fresh exact direct user authorization is required")
        require(attempt.get("task_id") == "B14" and attempt.get("claimed_at") and attempt.get("worker")
                and attempt.get("orchestrator") == state.get("orchestrator"),
                "invalid_workspace_delta_recovery_target", "A live claimed B14 attempt is required")
        require(attempt.get("status") in {"in_progress", "changes_requested"}
                and not attempt.get("candidate") and attempt.get("review") is None,
                "workspace_delta_recovery_amendment_late",
                "Amendment is allowed only before a candidate or after a matching NO_GO repair transition")
        self.require_plan(attempt, plan)
        record = self.workspace_delta_recovery_for_attempt(state, attempt)
        require(record is not None, "workspace_delta_recovery_missing", "Original immutable workspace recovery receipt is required")
        require(record.get("plan_hash") == attempt.get("plan_hash")
                and record.get("base_head") == attempt.get("base_head")
                and record.get("baseline_index_sha256") == attempt.get("baseline_index_sha256")
                and record.get("baseline_sha256") == digest(attempt.get("baseline"))
                and record.get("scope_sha256") == digest(attempt.get("files"))
                and record.get("snapshot_recovery_bindings") == self.snapshot_recovery_bindings(state, attempt),
                "workspace_delta_recovery_pins_changed", "Original recovery pins no longer match the live B14 attempt")
        require(self.git("rev-parse", "HEAD").decode().strip() == record["base_head"]
                and self.index_digest() == record["baseline_index_sha256"],
                "workspace_delta_recovery_pins_changed", "HEAD or index changed from the original recovery pins")
        require(self.current_snapshot_recovery_bindings(state, attempt) == record["snapshot_recovery_bindings"],
                "workspace_delta_recovery_pins_changed", "The exact amended snapshot recovery changed")

        chain = self.workspace_delta_recovery_chain_for_attempt(state, attempt)
        require(chain and chain[0] == record, "corrupt_state", "Original workspace recovery chain is unavailable")
        require(not any(item.get("iteration") == attempt.get("iteration") for item in chain[1:]),
                "duplicate_workspace_delta_recovery_amendment",
                "Only one workspace recovery amendment is allowed in each attempt iteration")
        require(not any(isinstance(item, dict) and item.get("iteration") == attempt.get("iteration")
                        for item in attempt.get("submissions", [])),
                "workspace_delta_recovery_amendment_late", "A candidate already exists for this iteration")
        if attempt["status"] == "in_progress":
            require(attempt.get("iteration") == record["iteration"] and not attempt.get("submissions")
                    and not attempt.get("reviews") and not attempt.get("reviewers"),
                    "workspace_delta_recovery_amendment_late",
                    "Pre-submit amendment must precede every candidate and review")
        else:
            require(attempt.get("iteration", 0) > record["iteration"]
                    and self.workspace_delta_recovery_iteration_history_valid(
                        state, attempt, record, attempt.get("iteration")),
                    "workspace_delta_recovery_amendment_history",
                    "A repair amendment requires matching NO_GO history for every earlier iteration")

        require(isinstance(args.manifest_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256),
                "workspace_delta_manifest_digest_required", "Root must supply the observed exact manifest SHA-256")
        manifest = self.unexplained_workspace_delta(state, attempt)
        require(manifest, "workspace_delta_recovery_empty", "No unexplained workspace delta is available to preserve")
        require(args.manifest_sha256 == digest(manifest), "workspace_delta_manifest_changed",
                "Current unexplained workspace manifest differs from the root pre-observation")
        previous = chain[-1]
        require(manifest != previous.get("manifest"), "workspace_delta_manifest_unchanged",
                "An amendment must pin a new exact workspace delta")
        self.validate_workspace_delta_manifest(state, attempt, manifest, digest(manifest))

        authorization = {"source": SNAPSHOT_RECOVERY_AUTH_SOURCE, "current_chat_run_id": args.run_id,
                         "authorized": True, "user_authorization": args.user_authorization}
        amendment = {"id": str(uuid.uuid4()), "attempt_id": attempt["id"], "task_id": "B14",
                     "origin_recovery_id": record["id"], "origin_recovery_sha256": digest(record),
                     "sequence": len(chain), "previous_entry_id": previous["id"],
                     "previous_entry_sha256": digest(previous), "iteration": attempt["iteration"],
                     "plan_hash": record["plan_hash"], "base_head": record["base_head"],
                     "baseline_index_sha256": record["baseline_index_sha256"],
                     "baseline_sha256": record["baseline_sha256"], "scope_sha256": record["scope_sha256"],
                     "snapshot_recovery_bindings": copy.deepcopy(record["snapshot_recovery_bindings"]),
                     "manifest": manifest, "manifest_sha256": digest(manifest),
                     "authorization": authorization, "actor": actor, "run_id": args.run_id,
                     "recorded_at": iso(), "reason": args.reason}
        state.setdefault("workspace_delta_recovery_amendments", []).append(amendment)
        return {"attempt": attempt, "workspace_delta_recovery_amendment": amendment}

    def propose_snapshot_recovery(self, state, plan, args, actor):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Snapshot recovery authorization must belong to this current chat run")
        require(nonempty(args.reason), "missing_reason", "Snapshot recovery reason required")
        require(nonempty(args.user_authorization), "invalid_snapshot_recovery_authorization",
                "Direct user authorization is required")
        uuid_text(args.snapshot_id)
        attempt = self.attempt(state, args.attempt)
        require(attempt.get("task_id") == "B14", "invalid_snapshot_recovery_target",
                "Only a claimed B14 attempt may receive pending snapshot recovery")
        require(attempt.get("status") in {"in_progress", "changes_requested"} and attempt.get("claimed_at")
                and attempt.get("worker"), "invalid_snapshot_recovery_target",
                "Pending recovery requires the live claimed B14 worker attempt")
        require(attempt.get("orchestrator") == state["orchestrator"], "wrong_owner",
                "B14 attempt must remain bound to the current orchestrator")
        self.require_plan(attempt, plan)
        matches = [item for item in state.get("snapshots", []) if item["id"] == args.snapshot_id]
        require(len(matches) == 1, "unknown_snapshot", "Exact immutable source snapshot ID required")
        snapshot = matches[0]
        require(any(self.overlap(name, owned) for name in snapshot["files"] for owned in attempt["files"]),
                "invalid_snapshot_recovery_target", "Source snapshot must overlap a path claimed by this B14 attempt")
        require(not any(item["target_attempt_id"] == attempt["id"] and item["snapshot_id"] == snapshot["id"]
                        for item in state.get("snapshot_recoveries", [])),
                "duplicate_snapshot_recovery", "This B14 attempt already has an append-only proposal for the source snapshot")
        try:
            manifest = self.exact_manifest(snapshot["files"])
        except Refusal as exc:
            raise Refusal("snapshot_recovery_manifest", "Every original snapshot path must exist as a regular file") from exc
        require(manifest != snapshot["manifest"], "snapshot_recovery_not_needed",
                "Recovery proposal requires an observed drift from the immutable source snapshot")
        authorization = {"source": SNAPSHOT_RECOVERY_AUTH_SOURCE, "current_chat_run_id": args.run_id,
                         "authorized": True, "user_authorization": args.user_authorization}
        proposal = {"id": str(uuid.uuid4()), "snapshot_id": snapshot["id"],
                    "target_attempt_id": attempt["id"], "task_id": attempt["task_id"],
                    "iteration": attempt["iteration"], "manifest": manifest,
                    "source_provenance": self.snapshot_recovery_source(snapshot),
                    "authorization": authorization, "actor": actor, "run_id": args.run_id,
                    "proposed_at": iso(), "reason": args.reason}
        state.setdefault("snapshot_recoveries", []).append(proposal)
        return {"proposal": proposal}

    def amend_snapshot_recovery(self, state, plan, args, actor, attempt):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Snapshot recovery amendment must belong to this current orchestrator run")
        require(nonempty(args.reason), "missing_reason", "Snapshot recovery amendment reason required")
        require(nonempty(args.user_authorization), "invalid_snapshot_recovery_authorization",
                "Direct user authorization is required for a recovery amendment")
        require(attempt.get("task_id") == "B14" and attempt.get("status") == "in_progress"
                and attempt.get("claimed_at") and attempt.get("worker")
                and attempt.get("orchestrator") == state["orchestrator"],
                "invalid_snapshot_recovery_amendment_target",
                "Amendment requires the same live claimed B14 attempt and orchestrator")
        require(type(attempt.get("iteration")) is int,
                "invalid_snapshot_recovery_amendment_target", "B14 attempt iteration must be valid")
        self.require_plan(attempt, plan)
        matches = [item for item in state.get("snapshot_recoveries", [])
                   if item["target_attempt_id"] == attempt["id"] and item["snapshot_id"] == args.snapshot_id]
        require(len(matches) == 1, "unknown_snapshot_recovery_proposal",
                "Exact original proposal for this B14 attempt and snapshot required")
        proposal = matches[0]
        require(attempt.get("iteration") == proposal.get("iteration"),
                "snapshot_recovery_amendment_iteration",
                "Amendment is allowed only in the original proposal iteration")
        require(not any(item.get("target_attempt_id") == attempt["id"]
                        and item.get("snapshot_id") == proposal["snapshot_id"]
                        for item in state.get("snapshot_recovery_amendments", [])),
                "duplicate_snapshot_recovery_amendment",
                "Only one pre-submit amendment is allowed for this attempt and snapshot")
        require(not attempt.get("candidate") and not attempt.get("submissions") and not attempt.get("reviews")
                and not attempt.get("reviewer") and not attempt.get("review"),
                "snapshot_recovery_amendment_late",
                "Amendment must precede every candidate submission and review")
        snapshots = [item for item in state.get("snapshots", []) if item["id"] == proposal["snapshot_id"]]
        require(len(snapshots) == 1, "unknown_snapshot", "Exact immutable source snapshot ID required")
        snapshot = snapshots[0]
        require(snapshot["id"] == args.snapshot_id
                and any(self.overlap(name, owned) for name in snapshot["files"] for owned in attempt["files"]),
                "invalid_snapshot_recovery_amendment_target",
                "Amendment must name the source snapshot overlapping this B14 claim")
        try:
            manifest = self.exact_manifest(snapshot["files"])
        except Refusal as exc:
            raise Refusal("snapshot_recovery_manifest", "Every original snapshot path must exist as a regular file") from exc
        require(manifest != proposal["manifest"] and manifest != snapshot["manifest"],
                "snapshot_recovery_amendment_not_needed",
                "Amendment requires a stale proposal and a currently drifted snapshot")
        amendment = {"id": str(uuid.uuid4()), "proposal_id": proposal["id"],
                     "snapshot_id": snapshot["id"], "target_attempt_id": attempt["id"],
                     "task_id": "B14", "proposal_iteration": proposal["iteration"],
                     "iteration": attempt["iteration"], "manifest": manifest,
                     "manifest_sha256": digest(manifest),
                     "proposal_manifest_sha256": digest(proposal["manifest"]),
                     "proposal_sha256": self.snapshot_recovery_proposal_digest(proposal),
                     "authorization": {"source": SNAPSHOT_RECOVERY_AUTH_SOURCE,
                                       "current_chat_run_id": args.run_id, "authorized": True,
                                       "user_authorization": args.user_authorization},
                     "actor": actor, "run_id": args.run_id, "amended_at": iso(), "reason": args.reason}
        state.setdefault("snapshot_recovery_amendments", []).append(amendment)
        return {"amendment": amendment, "attempt": attempt}

    def continue_snapshot_recovery(self, state, plan, args, actor, attempt):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Snapshot continuation must use the current orchestrator run")
        require(args.user_authorization == SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION,
                "invalid_snapshot_recovery_continuation_authorization",
                "Fresh exact direct user authorization is required")
        require(nonempty(args.reason), "missing_reason", "Snapshot continuation reason is required")
        require(attempt.get("task_id") == "B14" and attempt.get("status") == "changes_requested"
                and attempt.get("claimed_at") and attempt.get("worker")
                and attempt.get("orchestrator") == state.get("orchestrator"),
                "invalid_snapshot_recovery_continuation_target",
                "Continuation requires the same live claimed B14 attempt immediately after NO_GO")
        self.require_plan(attempt, plan)
        proposal_matches = [item for item in state.get("snapshot_recoveries", [])
                            if item.get("target_attempt_id") == attempt["id"]
                            and item.get("snapshot_id") == args.snapshot_id]
        require(len(proposal_matches) == 1, "unknown_snapshot_recovery_proposal",
                "Exact original B14 proposal and snapshot are required")
        proposal = proposal_matches[0]
        amendments = [item for item in state.get("snapshot_recovery_amendments", [])
                      if item.get("proposal_id") == proposal["id"]]
        require(len(amendments) == 1 and amendments[0].get("iteration") == proposal.get("iteration"),
                "snapshot_recovery_continuation_base",
                "Continuation requires the immutable one-time pre-candidate amendment")
        amendment = amendments[0]
        chain = [item for item in state.get("snapshot_recovery_continuations", [])
                 if item.get("target_attempt_id") == attempt["id"]
                 and item.get("proposal_id") == proposal["id"]]
        require(not any(item.get("target_attempt_id") == attempt["id"]
                        and item.get("snapshot_id") == proposal["snapshot_id"]
                        and item.get("iteration") == attempt["iteration"]
                        for item in state.get("snapshot_recovery_continuations", [])),
                "duplicate_snapshot_recovery_continuation",
                "Only one snapshot continuation is allowed for this attempt and iteration")
        if chain:
            require(attempt.get("iteration") == chain[-1]["iteration"] + 1,
                    "snapshot_recovery_continuation_iteration",
                    "Continuation must immediately follow the previous continuation iteration")
        else:
            require(attempt.get("iteration", 0) > proposal.get("iteration", 0),
                    "snapshot_recovery_continuation_iteration",
                    "Continuation requires a later live iteration with matching NO_GO history")
        require(not attempt.get("candidate") and attempt.get("reviewer") is None and attempt.get("review") is None,
                "snapshot_recovery_continuation_late",
                "Continuation is permitted only after NO_GO invalidated the prior candidate")
        require(self.git("rev-parse", "HEAD").decode().strip() == attempt.get("base_head")
                and self.index_digest() == attempt.get("baseline_index_sha256"),
                "snapshot_recovery_continuation_pins_changed", "Attempt HEAD or index changed")
        workspace_origin = self.workspace_delta_recovery_for_attempt(state, attempt)
        require(workspace_origin is not None
                and self.workspace_delta_recovery_matches_attempt(state, workspace_origin, attempt)
                and self.workspace_delta_recovery_iteration_history_valid(
                    state, attempt, workspace_origin, attempt["iteration"])
                and workspace_origin.get("snapshot_recovery_bindings") == self.snapshot_recovery_bindings(state, attempt),
                "snapshot_recovery_continuation_workspace_binding",
                "The immutable workspace-delta origin and prior candidate/NO_GO chain must remain bound")
        snapshots = [item for item in state.get("snapshots", []) if item.get("id") == proposal["snapshot_id"]]
        require(len(snapshots) == 1 and any(self.overlap(name, owned)
                                            for name in snapshots[0]["files"] for owned in attempt["files"]),
                "invalid_snapshot_recovery_continuation_target",
                "Exact immutable B14 source snapshot required")
        snapshot = snapshots[0]
        require(self.snapshot_recovery_iteration_history_valid(
                    state, attempt, proposal, attempt["iteration"], amendment),
                "snapshot_recovery_continuation_history",
                "A matching candidate and independent NO_GO are required for every prior iteration")
        previous_candidate = self.snapshot_recovery_no_go_binding(
            state, attempt, proposal, attempt["iteration"] - 1, amendment)
        require(previous_candidate is not None,
                "snapshot_recovery_continuation_history", "The immediately preceding iteration has no matching NO_GO")
        try:
            manifest = self.exact_manifest(snapshot["files"])
        except Refusal as exc:
            raise Refusal("snapshot_recovery_manifest", "Every original snapshot path must exist as a regular file") from exc
        require(isinstance(args.manifest_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256),
                "snapshot_recovery_continuation_digest_required",
                "Root must supply the pre-observed exact snapshot manifest SHA-256")
        require(args.manifest_sha256 == digest(manifest), "snapshot_recovery_continuation_manifest_changed",
                "Current exact snapshot manifest differs from the root observation")
        require(manifest != snapshot["manifest"] and manifest != proposal["manifest"]
                and (bool(chain) or manifest != amendment["manifest"]),
                "snapshot_recovery_continuation_not_needed",
                "Continuation requires a live non-original exact snapshot manifest")
        prior = chain[-1] if chain else amendment
        sequence = len(chain) + 1
        require(prior.get("iteration") == attempt["iteration"] - 1,
                "snapshot_recovery_continuation_iteration",
                "Continuation must extend the immediately preceding snapshot recovery entry")
        continuation = {"id": str(uuid.uuid4()), "proposal_id": proposal["id"],
                        "snapshot_id": snapshot["id"], "target_attempt_id": attempt["id"],
                        "task_id": "B14", "proposal_iteration": proposal["iteration"],
                        "amendment_id": amendment["id"], "amendment_sha256": digest(amendment),
                        "iteration": attempt["iteration"], "sequence": sequence,
                        "previous_entry_id": prior["id"], "previous_entry_sha256": digest(prior),
                        "manifest": manifest, "manifest_sha256": digest(manifest),
                        "proposal_manifest_sha256": digest(proposal["manifest"]),
                        "proposal_sha256": self.snapshot_recovery_proposal_digest(proposal),
                        "attempt_pins": self.snapshot_recovery_attempt_pins(attempt),
                        "workspace_delta_recovery_id": workspace_origin["id"],
                        "workspace_delta_recovery_sha256": digest(workspace_origin),
                        "previous_candidate": previous_candidate,
                        "authorization": {"source": SNAPSHOT_RECOVERY_AUTH_SOURCE,
                                          "current_chat_run_id": args.run_id, "authorized": True,
                                          "user_authorization": args.user_authorization},
                        "actor": actor, "run_id": args.run_id, "continued_at": iso(),
                        "reason": args.reason}
        state.setdefault("snapshot_recovery_continuations", []).append(continuation)
        return {"attempt": attempt, "continuation": continuation}

    def record_snapshot(self, state, args, actor):
        self.root_actor(state, actor, args.run_id)
        require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                "current_run_mismatch", "Snapshot authorization must belong to this current chat run")
        require(nonempty(args.reason), "missing_reason", "Snapshot provenance reason required")
        raw_files = [self.path(value) for value in args.file]
        require(raw_files and len(set(raw_files)) == len(raw_files), "invalid_scope", "Snapshot requires distinct exact files")
        files = sorted(raw_files)
        require(raw_files == files, "invalid_scope", "Snapshot CLI paths must be in canonical sorted order")
        for index, name in enumerate(files):
            require(not any(self.overlap(name, other) for other in files[index + 1:]),
                    "invalid_scope", "Snapshot paths cannot overlap")
        self.conflicts(state, files, [])
        for previous in state.get("snapshots", []):
            require(not any(self.overlap(name, prior) for name in files for prior in previous["files"]),
                    "snapshot_path_conflict", "Snapshot paths cannot replace an existing immutable snapshot")

        evidence = self.evidence(args.evidence)
        payload = evidence["payload"]
        require(payload.get("kind") == SNAPSHOT_EVIDENCE_KIND, "invalid_snapshot_evidence", "Snapshot evidence kind mismatch")
        fresh(payload.get("observed_at"))
        source = payload.get("source_chat")
        require(isinstance(source, dict), "invalid_snapshot_source", "Completed source chat observation required")
        require(nonempty(source.get("root")) and isinstance(source.get("root"), str),
                "invalid_snapshot_source", "Source checkout root must be explicit")
        try:
            source_root_path = Path(source["root"])
            require(source_root_path.is_absolute(), "invalid_snapshot_source", "Source checkout root must be absolute")
            source_root = source_root_path.resolve(strict=True)
        except (OSError, TypeError, ValueError) as exc:
            raise Refusal("invalid_snapshot_source", "Source chat must name this existing checkout") from exc
        require(source_root == self.root and source["root"] == str(self.root) and source.get("status") == "completed"
                and nonempty(source.get("title")), "invalid_snapshot_source", "Completed source chat in this checkout required")
        uuid_text(source.get("id"))
        require(source["id"] != args.run_id, "invalid_snapshot_source", "Source chat must be separate from the current chat")
        require(source.get("files") == files, "snapshot_file_mismatch", "Source chat file list must exactly match the CLI list")
        observation = payload.get("source_observation")
        require(isinstance(observation, dict) and observation.get("tool") == "mcp__codex_app__read_thread"
                and observation.get("thread_id") == source["id"] and observation.get("status") == source["status"]
                and observation.get("title") == source["title"] and observation.get("root") == source["root"]
                and observation.get("files") == source["files"] and observation.get("observed_at") == payload["observed_at"],
                "invalid_snapshot_observation", "Fresh source-chat observation must match the exact source record")
        authorization = payload.get("authorization")
        require(isinstance(authorization, dict) and authorization.get("source") == SNAPSHOT_AUTH_SOURCE
                and authorization.get("current_chat_run_id") == args.run_id
                and authorization.get("authorized") is True
                and nonempty(authorization.get("user_authorization")),
                "invalid_snapshot_authorization", "Direct authorization from this current chat is required")
        manifest = self.exact_manifest(files)
        snapshot = {"id": str(uuid.uuid4()),
                    "source_chat": {"id": source["id"], "title": source["title"], "status": source["status"], "root": str(source_root)},
                    "files": files, "manifest": manifest,
                    "authorization": {"source": authorization["source"], "current_chat_run_id": authorization["current_chat_run_id"],
                                     "authorized": True, "user_authorization": authorization["user_authorization"]},
                    "evidence": evidence, "actor": actor, "run_id": args.run_id, "recorded_at": iso(), "reason": args.reason}
        state.setdefault("snapshots", []).append(snapshot)
        return {"snapshot": snapshot}

    def mutate(self, state, plan, args):
        action, actor = args.action, args.actor
        require(TARGET.fullmatch(actor or ""), "invalid_identity", "Canonical native actor target required")
        uuid_text(args.run_id)
        if action in {"handoff", "recover-handoff"}:
            previous = state["orchestrator"]
            require(previous is not None, "no_orchestrator", "An initialized orchestrator is required")
            uuid_text(args.from_run_id)
            require(actor == "/root" and previous.get("target") == actor,
                    "invalid_handoff_target", "Handoff may transfer the recorded /root target only to the same target")
            require(previous.get("run_id") == args.from_run_id, "wrong_owner", "--from-run-id must match the recorded orchestrator owner")
            require(args.run_id != args.from_run_id, "same_owner", "Handoff requires a different current chat run")
            require(os.environ.get("CODEX_THREAD_ID") == args.run_id,
                    "current_run_mismatch", "--run-id must match this process CODEX_THREAD_ID")
            recovery_attempt = self.attempt(state, args.attempt) if action == "recover-handoff" else None
            require(not any(item["status"] in ACTIVE and item is not recovery_attempt for item in state["attempts"]),
                    "active_attempts", "Close or resolve every active attempt before handoff")
            require(nonempty(args.reason), "missing_reason", "Handoff reason required")
            evidence = self.evidence(args.evidence)
            payload = evidence["payload"]
            require(payload.get("kind") == (RECOVERY_EVIDENCE_KIND if recovery_attempt else HANDOFF_EVIDENCE_KIND)
                    and payload.get("source") == "direct_user_reply_in_current_chat"
                    and payload.get("current_chat_selection") == "в текущем чате"
                    and payload.get("current_chat_selected") is True
                    and nonempty(payload.get("user_authorization")),
                    "invalid_handoff_evidence", "Fresh direct user authorization for this current chat is required")
            require(payload.get("previous_orchestrator") == previous
                    and payload.get("next_orchestrator") == {"target": actor, "run_id": args.run_id},
                    "invalid_handoff_evidence", "Authorization evidence must name the exact previous and current owners")
            fresh(payload.get("observed_at"))
            next_owner = {"target": actor, "run_id": args.run_id}
            if recovery_attempt:
                require(recovery_attempt["status"] in ACTIVE and recovery_attempt.get("claimed_at")
                        and recovery_attempt.get("worker") and recovery_attempt.get("orchestrator") == previous,
                        "invalid_recovery_attempt", "Exact live claimed worker attempt of the recorded owner required")
                worker = {key: recovery_attempt["worker"][key] for key in ("target", "run_id")}
                require(payload.get("attempt_id") == recovery_attempt["id"]
                        and payload.get("task_id") == recovery_attempt["task_id"]
                        and payload.get("abandoned_worker") == worker
                        and payload.get("policy") == "abandon_unverified_worker"
                        and payload.get("abandonment_authorized") is True
                        and "writers_stopped" not in payload and "quiescence" not in payload,
                        "invalid_recovery_evidence", "Explicit operator abandonment of the exact unverified worker required")
                manifest = self.manifest(recovery_attempt["files"])
                require(payload.get("manifest") == manifest, "recovery_manifest_drift", "Recovery must name the exact current scope manifest")
                recovery_attempt.update(status="cancelled", closed_manifest=manifest, closed_at=iso(), close_reason=args.reason,
                                        operator_abandonment={"kind": RECOVERY_EVIDENCE_KIND,
                                                              "previous_orchestrator": copy.deepcopy(previous),
                                                              "next_orchestrator": next_owner, "authorization": evidence})
            state["orchestrator"] = next_owner
            result = {"handoff": {"previous_orchestrator": copy.deepcopy(previous),
                                  "orchestrator": next_owner, "authorization": evidence}}
            if recovery_attempt:
                result["attempt"] = recovery_attempt
            return result
        if state["orchestrator"] is None:
            require(action in {"reserve", "record-gate"}, "wrong_role", "Orchestrator is established by first reserve/gate")
            state["orchestrator"] = {"target": actor, "run_id": args.run_id}
        if action == "record-snapshot":
            return self.record_snapshot(state, args, actor)
        if action == "propose-snapshot-recovery":
            return self.propose_snapshot_recovery(state, plan, args, actor)
        if action in {"reserve", "record-gate"}:
            self.root_actor(state, actor, args.run_id)
            require(nonempty(args.reason), "missing_reason", "Reason required")
            require(args.task in plan["tasks"], "unknown_task", "Unknown task")
            task = plan["tasks"][args.task]
            if action == "record-gate":
                require(task.get("questions") or task.get("additional_gate"), "invalid_gate", "Task has no external gate")
                evidence = self.evidence(args.evidence)
                data = evidence["payload"]
                require(data.get("task_id") == args.task and data.get("actor") == actor, "invalid_gate", "Gate task/actor mismatch")
                answers = data.get("answers")
                required = set(task.get("questions", [])) | ({"additional_gate"} if task.get("additional_gate") else set())
                require(isinstance(answers, list) and {item.get("question_id") for item in answers if isinstance(item, dict)} == required
                        and len(answers) == len(required), "invalid_gate", "All and only required sourced answers must be present")
                for answer in answers:
                    require(answer.get("decision") == "allowed" and nonempty(answer.get("answer")) and nonempty(answer.get("source")),
                            "gate_not_allowed", "Unknown/denied/null assumptions do not open a gate")
                    require((now() - date(answer.get("received_at"))).total_seconds() >= -30, "invalid_gate", "Gate answer cannot be from the future")
                state["gates"][args.task] = {"task_hash": plan["task_hashes"][args.task], "evidence": evidence, "recorded_at": iso()}
                return {"task_id": args.task, "gate_recorded": True, "task_accepted": bool(task.get("questions"))}
            require(not task.get("questions"), "external_gate_required", "External facts use record-gate, never a fabricated worker review")
            report = self.check(state, plan, args)
            require(report["ready"], "task_not_ready", "; ".join(report["reasons"]))
            baseline = self.inventory()
            attempt = {"id": str(uuid.uuid4()), "task_id": args.task, "status": "reserved", "iteration": 1,
                       "orchestrator": copy.deepcopy(state["orchestrator"]), "plan_hash": plan["hash"], "task_hash": plan["task_hashes"][args.task],
                       "contract_hash": digest(plan["contracts"]),
                       "files": report["files"], "resources": report["resources"], "adopted": args.adopt_accepted,
                       "adopted_recovered": args.adopt_recovered,
                       "base_head": self.git("rev-parse", "HEAD").decode().strip(), "baseline": baseline,
                       "baseline_index_sha256": self.index_digest(),
                       "baseline_scope": self.manifest(report["files"]), "worker": None, "reviewer": None,
                       "reviewers": [], "reviews": [], "submissions": [], "candidate": None, "review": None, "created_at": iso()}
            state["attempts"].append(attempt)
            return {"attempt": attempt}

        attempt = self.attempt(state, args.attempt)
        require(attempt["status"] in ACTIVE, "closed_attempt", "Closed attempts are immutable history")
        if action in {"claim", "submit", "accept", "rebase"}:
            require(attempt["task_id"] in plan["tasks"], "unknown_task", "Task removed from current plan")
            require(self.gate_available(state, plan, attempt["task_id"]), "additional_gate_required", "Current task gate requires actual sourced answers")
        if action not in {"block", "release", "cancel", "rebase"}:
            self.require_plan(attempt, plan)
        if action in {"bind", "bind-reviewer", "accept", "resume", "release", "cancel", "extend", "rebase",
                      "amend-snapshot-recovery", "continue-snapshot-recovery",
                      "record-workspace-recovery", "amend-workspace-recovery"}:
            self.root_actor(state, actor, args.run_id)
        else:
            if action == "block" and {"target": actor, "run_id": args.run_id} == state["orchestrator"]:
                pass
            else:
                self.role(attempt, actor, args.run_id, "reviewer" if action == "review" else "worker")

        if action == "bind":
            require(attempt["status"] == "reserved" and not attempt["worker"], "invalid_transition", "Only an unbound reservation may bind")
            attempt["worker"] = self.binding(state, attempt, self.evidence(args.tool_evidence), "worker")
            attempt["status"] = "bound"
        elif action == "amend-snapshot-recovery":
            return self.amend_snapshot_recovery(state, plan, args, actor, attempt)
        elif action == "continue-snapshot-recovery":
            return self.continue_snapshot_recovery(state, plan, args, actor, attempt)
        elif action == "record-workspace-recovery":
            return self.record_workspace_delta_recovery(state, plan, args, actor, attempt)
        elif action == "amend-workspace-recovery":
            return self.amend_workspace_delta_recovery(state, plan, args, actor, attempt)
        elif action == "claim":
            require(attempt["status"] == "bound", "invalid_transition", "Only bound worker may claim once")
            self.conflicts(state, attempt["files"], attempt["resources"], attempt["id"])
            require(self.manifest(attempt["files"]) == attempt["baseline_scope"], "baseline_drift", "Own scope changed after reserve")
            require(self.git("rev-parse", "HEAD").decode().strip() == attempt["base_head"], "base_changed", "HEAD changed after reserve")
            require(self.index_digest() == attempt["baseline_index_sha256"], "index_changed", "Index changed after reserve")
            self.dirty()
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep), "dependency_changed", f"Dependency not available: {dep}")
            attempt["status"] = "in_progress"
            attempt["claimed_at"] = iso()
        elif action == "submit":
            require(attempt["status"] in {"in_progress", "changes_requested"}, "invalid_transition", "Submit requires active worker/repair")
            self.outside_changes(state, attempt)
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep, attempt), "dependency_changed", "Dependency changed before submission")
            self.dirty()
            require(self.git("rev-parse", "HEAD").decode().strip() == attempt["base_head"], "base_changed", "Unexpected HEAD change; explicit rebase required")
            require(self.index_digest() == attempt["baseline_index_sha256"], "index_changed", "No staging is authorized by a worker claim")
            evidence = self.evidence(args.evidence)
            payload = evidence["payload"]
            require(payload.get("task_id") == attempt["task_id"] and payload.get("attempt_id") == attempt["id"], "invalid_evidence", "Submit task/attempt mismatch")
            self.checks(payload)
            candidate = {"task_id": attempt["task_id"], "attempt_id": attempt["id"],
                         "iteration": attempt["iteration"], "base_head": attempt["base_head"],
                         "index_sha256": self.index_digest(),
                         "plan_hash": attempt["plan_hash"], "task_hash": attempt["task_hash"], "manifest": self.manifest(attempt["files"]),
                         "report": evidence, "snapshot_recoveries": self.recovery_candidate_proposals(state, attempt)}
            workspace_recovery = self.current_workspace_delta_recovery_summary(state, attempt)
            if workspace_recovery is not None:
                candidate["workspace_delta_recovery"] = workspace_recovery
            candidate["digest"] = digest(candidate)
            attempt["submissions"].append(copy.deepcopy(candidate))
            attempt.update(candidate=candidate, status="awaiting_review", reviewer=None, review=None)
        elif action == "bind-reviewer":
            require(attempt["status"] == "awaiting_review" and not attempt["reviewer"], "invalid_transition", "Fresh submitted result without reviewer required")
            self.candidate_current(attempt)
            self.candidate_recoveries_current(state, attempt, allow_stale_workspace_delta=True)
            reviewer = self.binding(state, attempt, self.evidence(args.tool_evidence), "reviewer")
            reviewer["nonce"] = secrets.token_hex(16)
            attempt["reviewer"] = reviewer
            attempt["reviewers"].append(reviewer)
        elif action == "review":
            require(attempt["status"] == "awaiting_review" and not attempt["review"], "invalid_transition", "Exactly one review per submission")
            self.candidate_current(attempt)
            evidence = self.evidence(args.evidence)
            payload = evidence["payload"]
            candidate = attempt["candidate"]
            workspace_delta_stale = self.candidate_recoveries_current(
                state, attempt, allow_stale_workspace_delta=True)
            observation = payload.get("workspace_delta_stale_observation")
            if workspace_delta_stale:
                require(payload.get("result") == "NO_GO",
                        "stale_workspace_delta_recovery", "A stale workspace recovery candidate can receive only NO_GO")
                record = self.workspace_delta_recovery_for_attempt(state, attempt)
                entry = self.workspace_delta_recovery_current_entry(state, attempt)
                require(record is not None and entry is not None
                        and self.workspace_delta_recovery_iteration_history_valid(
                            state, attempt, record, attempt["iteration"]),
                        "stale_workspace_delta_recovery", "Workspace recovery history is not valid for stale-candidate review")
                self.validate_workspace_delta_stale_observation(state, attempt, candidate, observation,
                                                                require_live=True)
            else:
                require(observation is None, "invalid_review",
                        "Root stale-delta observation is accepted only when the submitted recovery is stale")
            for key, expected in {"task_id": attempt["task_id"], "attempt_id": attempt["id"], "iteration": attempt["iteration"],
                                  "candidate_digest": candidate["digest"], "plan_hash": attempt["plan_hash"], "review_nonce": attempt["reviewer"]["nonce"]}.items():
                require(payload.get(key) == expected, "invalid_review", f"Review metadata mismatch: {key}")
            require(payload.get("result") in {"GO", "NO_GO"}, "invalid_review", "Explicit GO or NO_GO required")
            self.checks(payload, review=True)
            findings = payload.get("findings")
            require(isinstance(findings, list) and all(nonempty(value) for value in findings), "invalid_review", "Findings must be a list of strings")
            require((payload["result"] == "GO" and not findings) or (payload["result"] == "NO_GO" and findings), "invalid_review", "NO_GO requires findings; GO cannot hide findings")
            review = {"evidence": evidence, "reviewer": attempt["reviewer"]["target"], "run_id": attempt["reviewer"]["run_id"],
                      "candidate_digest": candidate["digest"], "iteration": attempt["iteration"], "result": payload["result"]}
            if candidate.get("workspace_delta_recovery") is not None:
                require(payload.get("workspace_delta_recovery") == candidate["workspace_delta_recovery"],
                        "invalid_review", "Review must bind the exact workspace delta recovery")
                review["workspace_delta_recovery"] = copy.deepcopy(candidate["workspace_delta_recovery"])
            continuation_summaries = [item for item in candidate.get("snapshot_recoveries", [])
                                      if item.get("continuation_id")]
            if continuation_summaries:
                require(payload.get("snapshot_recoveries") == candidate.get("snapshot_recoveries"),
                        "invalid_review", "Review must bind the exact snapshot continuation and workspace origin")
                review["snapshot_recoveries"] = copy.deepcopy(candidate["snapshot_recoveries"])
            if workspace_delta_stale:
                review["workspace_delta_stale_observation"] = copy.deepcopy(observation)
            attempt["review"] = review
            attempt["reviews"].append(review)
            if payload["result"] == "NO_GO":
                attempt["status"] = "changes_requested"
                self.invalidate(attempt)
        elif action == "accept":
            require(nonempty(args.reason), "missing_reason", "Acceptance evidence reason required")
            require(attempt["status"] == "awaiting_review" and attempt["review"] and attempt["review"]["result"] == "GO", "no_current_go", "Current independent GO required")
            self.candidate_current(attempt)
            self.candidate_recoveries_current(state, attempt)
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep, attempt), "dependency_changed", "Dependency changed before acceptance")
            require(attempt["review"]["candidate_digest"] == attempt["candidate"]["digest"] and attempt["review"]["iteration"] == attempt["iteration"], "stale_review", "GO must match current candidate")
            self.outside_changes(state, attempt)
            approvals = []
            for candidate_recovery in attempt["candidate"].get("snapshot_recoveries", []):
                approvals.append({"proposal_id": candidate_recovery["proposal_id"],
                                  "snapshot_id": candidate_recovery["snapshot_id"],
                                  "attempt_id": attempt["id"], "iteration": attempt["iteration"],
                                  "candidate_digest": attempt["candidate"]["digest"],
                                  "review_result": attempt["review"]["result"],
                                  "reviewer": attempt["review"]["reviewer"],
                                  "reviewer_run_id": attempt["review"]["run_id"],
                                  "review_evidence_sha256": attempt["review"]["evidence"]["sha256"],
                                  "acceptor": actor, "acceptor_run_id": args.run_id,
                                  "proposal_sha256": candidate_recovery["proposal_sha256"],
                                  "manifest_sha256": candidate_recovery["manifest_sha256"],
                                  "amendment_id": candidate_recovery.get("amendment_id"),
                                  "amendment_sha256": candidate_recovery.get("amendment_sha256"),
                                  "continuation_id": candidate_recovery.get("continuation_id"),
                                  "continuation_sha256": candidate_recovery.get("continuation_sha256"),
                                  "continuation_sequence": candidate_recovery.get("continuation_sequence"),
                                  "continuation_manifest_sha256": candidate_recovery.get("continuation_manifest_sha256"),
                                  "continuation_previous_entry_id": candidate_recovery.get("continuation_previous_entry_id"),
                                  "continuation_previous_entry_sha256": candidate_recovery.get("continuation_previous_entry_sha256"),
                                  "workspace_delta_recovery_id": candidate_recovery.get("workspace_delta_recovery_id"),
                                  "workspace_delta_recovery_sha256": candidate_recovery.get("workspace_delta_recovery_sha256")})
            data = {"attempt": attempt}
            if approvals:
                data["snapshot_recovery_acceptances"] = approvals
            workspace_recovery = attempt["candidate"].get("workspace_delta_recovery")
            if workspace_recovery is not None:
                recovery_record = self.workspace_delta_recovery_for_attempt(state, attempt)
                recovery_entry = (self.workspace_delta_recovery_entry_for_iteration(
                    state, recovery_record, attempt["candidate"].get("iteration")) if recovery_record else None)
                require(recovery_record is not None and recovery_entry is not None
                        and workspace_recovery == self.workspace_delta_recovery_candidate_summary(
                            recovery_record, attempt["candidate"].get("iteration"), recovery_entry),
                        "stale_workspace_delta_recovery", "Accepted candidate recovery record changed")
                approval = {"recovery_id": workspace_recovery["recovery_id"],
                            "recovery_sha256": workspace_recovery["recovery_sha256"],
                            "manifest_sha256": workspace_recovery["manifest_sha256"],
                            "chain_head_id": workspace_recovery["chain_head_id"],
                            "chain_head_sha256": workspace_recovery["chain_head_sha256"],
                            "chain_sequence": workspace_recovery["chain_sequence"],
                            "attempt_id": attempt["id"], "iteration": attempt["iteration"],
                            "candidate_digest": attempt["candidate"]["digest"],
                            "review_result": attempt["review"]["result"],
                            "reviewer": attempt["review"]["reviewer"],
                            "reviewer_run_id": attempt["review"]["run_id"],
                            "review_evidence_sha256": attempt["review"]["evidence"]["sha256"],
                            "acceptor": actor, "acceptor_run_id": args.run_id}
                data["workspace_delta_recovery_acceptances"] = [approval]
                expected_bindings = self.snapshot_recovery_bindings(state, attempt)
                current_bindings = self.current_snapshot_recovery_bindings(state, attempt)
                require(current_bindings == expected_bindings
                        and len(expected_bindings) == 1
                        and len(approvals) == len(attempt["candidate"].get("snapshot_recoveries", [])) == 1,
                        "stale_snapshot_recovery",
                        "Accept requires the exact amended snapshot recovery paired with workspace recovery")
                preview_attempt = copy.deepcopy(attempt)
                preview_attempt.update(status="accepted", accepted_revision=state["revision"] + 1)
                preview_event = {"action": "accept", "attempt_id": attempt["id"],
                                 "sequence": state["revision"] + 1, "actor": actor, "run_id": args.run_id,
                                 "snapshot_recovery_acceptances": approvals,
                                 "workspace_delta_recovery_acceptances": data["workspace_delta_recovery_acceptances"]}
                require(self.paired_recovery_acceptances_valid(preview_attempt, preview_event),
                        "incomplete_recovery_acceptance",
                        "Accept must produce the complete paired snapshot and workspace recovery receipts")
            attempt["status"] = "accepted"
            attempt["accepted_revision"] = state["revision"] + 1
            attempt["accepted_at"] = iso()
            if approvals or data.get("workspace_delta_recovery_acceptances"):
                return data
        elif action == "block":
            require(nonempty(args.reason), "missing_reason", "Block reason required")
            if attempt["status"] != "blocked":
                attempt["resume_status"] = "in_progress" if attempt.get("worker") and attempt["status"] != "bound" else attempt["status"]
            attempt.update(status="blocked", blocker=args.reason)
        elif action == "resume":
            require(attempt["status"] == "blocked" and nonempty(args.reason), "invalid_transition", "Blocked attempt and reason required")
            previous = attempt.get("resume_status", "in_progress")
            if attempt.get("candidate"):
                self.invalidate(attempt)
            attempt.update(status=previous, blocker=None)
        elif action in {"release", "cancel"}:
            require(nonempty(args.reason), "missing_reason", "Explicit close reason required")
            if attempt.get("worker"):
                evidence = self.evidence(args.evidence)
                payload = evidence["payload"]
                require(payload.get("tool") in {"collaboration.list_agents", "collaboration.interrupt_agent"}
                        and payload.get("target") == attempt["worker"]["target"] and payload.get("run_id") == attempt["worker"]["run_id"]
                        and payload.get("status") in {"idle", "completed", "interrupted"} and payload.get("writers_stopped") is True
                        and nonempty(payload.get("evidence")), "invalid_quiescence", "Fresh evidence of exact worker and stopped writers required")
                fresh(payload.get("observed_at"))
                attempt["quiescence"] = evidence
            attempt["status"] = "released" if action == "release" else "cancelled"
            attempt["closed_manifest"] = self.manifest(attempt["files"])
            attempt["closed_at"] = iso()
            attempt["close_reason"] = args.reason
        elif action == "extend":
            require(nonempty(args.reason), "missing_reason", "Extension reason required")
            require(not any(item.get("attempt_id") == attempt["id"]
                            for item in state.get("workspace_delta_recoveries", [])),
                    "workspace_delta_recovery_late", "A recorded workspace delta recovery cannot be extended")
            added = self.scope(args.file)
            resources = self.resources(args.resource)
            self.conflicts(state, added, resources, attempt["id"])
            self.clean_scope(state, [name for name in added if name not in attempt["files"]], [])
            attempt["files"] = self.scope(attempt["files"] + added)
            attempt["resources"] = sorted(set(attempt["resources"] + resources))
            attempt["baseline_scope"].update(self.manifest([name for name in added if name not in attempt["baseline_scope"]]))
            self.invalidate(attempt)
            if attempt["status"] in {"awaiting_review", "changes_requested"}:
                attempt["status"] = "in_progress"
        elif action == "rebase":
            require(nonempty(args.reason), "missing_reason", "Rebase requires explicit plan review reason")
            require(attempt["task_id"] in plan["tasks"], "unknown_task", "Cannot rebase a removed task")
            require(not any(item.get("attempt_id") == attempt["id"]
                            for item in state.get("workspace_delta_recoveries", [])),
                    "workspace_delta_recovery_late", "A recorded workspace delta recovery cannot be rebased")
            require(not any(item.get("target_attempt_id") == attempt["id"]
                            for item in state.get("snapshot_recovery_amendments", [])),
                    "snapshot_recovery_amendment_late",
                    "An amendment pins the reviewed plan context and cannot be rebased afterward")
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep, attempt), "dependency_changed", "Rebased dependencies must be accepted and available")
            pre_candidate_recovery = (not attempt.get("candidate") and not attempt.get("submissions")
                                      and not attempt.get("reviews")
                                      and any(item.get("target_attempt_id") == attempt["id"]
                                              for item in state.get("snapshot_recoveries", [])))
            attempt["plan_hash"] = plan["hash"]
            attempt["task_hash"] = plan["task_hashes"][attempt["task_id"]]
            attempt["contract_hash"] = digest(plan["contracts"])
            attempt["base_head"] = self.git("rev-parse", "HEAD").decode().strip()
            attempt["baseline_index_sha256"] = self.index_digest()
            if not pre_candidate_recovery:
                self.invalidate(attempt)
            if attempt["status"] in {"awaiting_review", "changes_requested"}:
                attempt["status"] = "in_progress"
        else:
            raise Refusal("unknown_action", action)
        return {"attempt": attempt}

    def execute(self, args):
        if args.action in READ_ONLY:
            state, plan = self.read(), self.load_plan()
            if args.action == "status":
                data = {"state_exists": self.state_path.exists(), "plan_hash": plan["hash"], "state": state}
            elif args.action == "check":
                try:
                    data = self.check(state, plan, args)
                except Refusal as exc:
                    data = {"ready": False, "reasons": [exc.code], "detail": str(exc)}
            else:
                rows = [{"task_id": task_id, "priority": task.get("priority", "P0"), "reasons": self.ready_reasons(state, plan, task_id)}
                        for task_id, task in plan["tasks"].items()]
                data = {"plan_hash": plan["hash"], "tasks": rows,
                        "ready_p0": [row["task_id"] for row in rows if row["priority"] == "P0" and not row["reasons"]],
                        "ready_p1": [row["task_id"] for row in rows if row["priority"] != "P0" and not row["reasons"]]}
            return {"ok": True, "action": args.action, "revision": state["revision"], **data}
        require(type(args.expected_revision) is int and args.expected_revision >= 0, "missing_cas", "Mutation requires --expected-revision")
        with self.locked():
            state, plan = self.read(), self.load_plan()
            require(state["revision"] == args.expected_revision, "stale_revision", f"Expected revision {args.expected_revision}; actual {state['revision']}")
            prior = None
            if args.attempt:
                previous = self.attempt(state, args.attempt)
                prior = {key: copy.deepcopy(previous.get(key)) for key in ("status", "iteration", "plan_hash", "task_hash", "base_head", "baseline_index_sha256", "files", "resources")}
            previous_gate = copy.deepcopy(state["gates"].get(args.task)) if args.action == "record-gate" else None
            data = self.mutate(state, plan, args)
            state["revision"] += 1
            event = {"sequence": state["revision"], "action": args.action, "actor": args.actor, "run_id": args.run_id, "at": iso(),
                     "attempt_id": args.attempt or data.get("attempt", {}).get("id"),
                     "task_id": args.task or data.get("attempt", {}).get("task_id") or data.get("proposal", {}).get("task_id")
                               or data.get("amendment", {}).get("task_id")
                               or data.get("workspace_delta_recovery_amendment", {}).get("task_id"),
                     "reason": args.reason, "previous": prior, "previous_gate": previous_gate}
            if args.action in {"handoff", "recover-handoff"}:
                event["handoff"] = copy.deepcopy(data["handoff"])
            if args.action == "record-snapshot":
                event["snapshot_id"] = data["snapshot"]["id"]
            if args.action == "propose-snapshot-recovery":
                event["snapshot_recovery_proposal_id"] = data["proposal"]["id"]
                event["snapshot_recovery_proposal"] = copy.deepcopy(data["proposal"])
            if args.action == "amend-snapshot-recovery":
                event["snapshot_recovery_amendment_id"] = data["amendment"]["id"]
                event["snapshot_recovery_amendment"] = copy.deepcopy(data["amendment"])
            if args.action == "continue-snapshot-recovery":
                event["snapshot_recovery_continuation_id"] = data["continuation"]["id"]
                event["snapshot_recovery_continuation"] = copy.deepcopy(data["continuation"])
            if args.action == "record-workspace-recovery":
                record = data["workspace_delta_recovery"]
                event["workspace_delta_recovery_id"] = record["id"]
                event["workspace_delta_recovery"] = copy.deepcopy(record)
                event["workspace_delta_recovery_baseline_sha256"] = record["baseline_sha256"]
            if args.action == "amend-workspace-recovery":
                amendment = data["workspace_delta_recovery_amendment"]
                event["workspace_delta_recovery_amendment_id"] = amendment["id"]
                event["workspace_delta_recovery_amendment"] = copy.deepcopy(amendment)
                event["workspace_delta_recovery_chain_head_sha256"] = amendment["previous_entry_sha256"]
            if args.action == "submit":
                candidate = data["attempt"]["candidate"]
                event["candidate_digest"] = candidate["digest"]
                event["candidate_iteration"] = candidate["iteration"]
                if candidate.get("workspace_delta_recovery") is not None:
                    event["workspace_delta_recovery"] = copy.deepcopy(candidate["workspace_delta_recovery"])
            if args.action == "review":
                review = data["attempt"]["review"]
                if review is None:
                    review = next(iter(reversed(data["attempt"].get("reviews", []))), None)
                require(isinstance(review, dict), "corrupt_state", "Review transition did not retain its audit record")
                event["candidate_digest"] = review["candidate_digest"]
                event["candidate_iteration"] = review["iteration"]
                event["review_result"] = review["result"]
                if review.get("workspace_delta_recovery") is not None:
                    event["workspace_delta_recovery"] = copy.deepcopy(review["workspace_delta_recovery"])
                if review.get("snapshot_recoveries") is not None:
                    event["snapshot_recoveries"] = copy.deepcopy(review["snapshot_recoveries"])
                if review.get("workspace_delta_stale_observation") is not None:
                    event["workspace_delta_stale_observation"] = copy.deepcopy(
                        review["workspace_delta_stale_observation"])
            if data.get("snapshot_recovery_acceptances"):
                event["snapshot_recovery_acceptances"] = copy.deepcopy(data["snapshot_recovery_acceptances"])
            if data.get("workspace_delta_recovery_acceptances"):
                event["workspace_delta_recovery_acceptances"] = copy.deepcopy(data["workspace_delta_recovery_acceptances"])
            state["events"].append(event)
            if "attempt" in data:
                data["attempt"]["updated_at"] = event["at"]
            self.write(state)
            output = {"ok": True, "action": args.action, "revision": state["revision"], **data}
            if data.get("attempt", {}).get("candidate"):
                output.update(candidate_digest=data["attempt"]["candidate"]["digest"], iteration=data["attempt"]["iteration"], plan_hash=data["attempt"]["plan_hash"])
            if args.action == "bind-reviewer":
                output["review_nonce"] = data["attempt"]["reviewer"]["nonce"]
            return output


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("usage_error", message)


def parser():
    result = JsonParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result.add_argument("action", choices=sorted(READ_ONLY | {"record-gate", "record-snapshot", "propose-snapshot-recovery", "amend-snapshot-recovery", "continue-snapshot-recovery", "record-workspace-recovery", "amend-workspace-recovery", "reserve", "bind", "claim", "submit", "bind-reviewer", "review", "accept", "block", "resume", "release", "cancel", "extend", "rebase", "handoff", "recover-handoff"}))
    result.add_argument("--expected-revision", type=int)
    result.add_argument("--actor")
    result.add_argument("--run-id")
    result.add_argument("--from-run-id")
    result.add_argument("--task")
    result.add_argument("--attempt")
    result.add_argument("--snapshot-id")
    result.add_argument("--manifest-sha256")
    result.add_argument("--user-authorization")
    result.add_argument("--file", action="append", default=[])
    result.add_argument("--resource", action="append", default=[])
    result.add_argument("--adopt-accepted", action="append", default=[])
    result.add_argument("--adopt-recovered", action="append", default=[])
    result.add_argument("--reason")
    result.add_argument("--tool-evidence")
    result.add_argument("--evidence")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        response = Coordinator(args.root).execute(args)
        print(json.dumps(response, ensure_ascii=True, allow_nan=False))
        return 0
    except Refusal as exc:
        response = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        response = {"ok": False, "error": {"code": "io_or_input_error", "message": str(exc)}}
    print(json.dumps(response, ensure_ascii=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
