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
                "orchestrator": None, "attempts": [], "gates": {}, "snapshots": [], "events": []}

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
        require(attempt.get("candidate") and self.manifest(attempt["files"]) == attempt["candidate"]["manifest"],
                "stale_candidate", "Scope bytes, mode, creation or deletion changed after submit/GO")
        require(self.git("rev-parse", "HEAD").decode().strip() == attempt["candidate"]["base_head"], "base_changed", "HEAD differs from submitted candidate")
        require(self.index_digest() == attempt["candidate"]["index_sha256"], "index_changed", "Staged modes/blobs changed after submission/GO")

    @staticmethod
    def invalidate(attempt):
        attempt["iteration"] += 1
        attempt["candidate"] = None
        attempt["reviewer"] = None
        attempt["review"] = None

    def outside_changes(self, state, attempt):
        snapshots = state.get("snapshots", [])
        snapshotted = {}
        for snapshot in snapshots:
            expected = snapshot["manifest"]
            try:
                current_snapshot = self.exact_manifest(snapshot["files"])
            except Refusal as exc:
                raise Refusal("snapshot_drift", f"Snapshotted file is unavailable or unsafe: {snapshot['id']}") from exc
            require(current_snapshot == expected, "snapshot_drift", f"Snapshotted bytes or mode changed: {snapshot['id']}")
            for entry in expected:
                snapshotted[entry["path"]] = {"kind": "file", "sha256": entry["sha256"], "size": entry["size"],
                                               "executable": entry["mode"] == "100755"}
        before, current = attempt["baseline"], self.inventory()
        own = set(attempt["files"])
        foreign = [name for item in state["attempts"] if item["id"] != attempt["id"] and item["status"] in ACTIVE and item.get("claimed_at") for name in item["files"]]
        lineage = self.lineage(state)
        preserved = {}
        for peer in state["attempts"]:
            if peer["id"] != attempt["id"] and peer["status"] in {"released", "cancelled"} and peer.get("claimed_at"):
                preserved.update(peer.get("closed_manifest", {}))
        for name in set(before) | set(current):
            old, new = before.get(name, {"kind": "absent"}), current.get(name, {"kind": "absent"})
            if old == new or name in own or any(self.overlap(name, permitted) for permitted in foreign):
                continue
            require((name in lineage and lineage[name]["entry"] == new) or preserved.get(name) == new or snapshotted.get(name) == new,
                    "out_of_scope_change", f"Unexplained change outside exact scope: {name}")

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
        if action in {"bind", "bind-reviewer", "accept", "resume", "release", "cancel", "extend", "rebase"}:
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
            candidate = {"attempt_id": attempt["id"], "iteration": attempt["iteration"], "base_head": attempt["base_head"],
                         "index_sha256": self.index_digest(),
                         "plan_hash": attempt["plan_hash"], "task_hash": attempt["task_hash"], "manifest": self.manifest(attempt["files"]),
                         "report": evidence}
            candidate["digest"] = digest(candidate)
            attempt["submissions"].append(copy.deepcopy(candidate))
            attempt.update(candidate=candidate, status="awaiting_review", reviewer=None, review=None)
        elif action == "bind-reviewer":
            require(attempt["status"] == "awaiting_review" and not attempt["reviewer"], "invalid_transition", "Fresh submitted result without reviewer required")
            self.candidate_current(attempt)
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
            attempt["review"] = review
            attempt["reviews"].append(review)
            if payload["result"] == "NO_GO":
                attempt["status"] = "changes_requested"
                self.invalidate(attempt)
        elif action == "accept":
            require(nonempty(args.reason), "missing_reason", "Acceptance evidence reason required")
            require(attempt["status"] == "awaiting_review" and attempt["review"] and attempt["review"]["result"] == "GO", "no_current_go", "Current independent GO required")
            self.candidate_current(attempt)
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep, attempt), "dependency_changed", "Dependency changed before acceptance")
            require(attempt["review"]["candidate_digest"] == attempt["candidate"]["digest"] and attempt["review"]["iteration"] == attempt["iteration"], "stale_review", "GO must match current candidate")
            self.outside_changes(state, attempt)
            attempt["status"] = "accepted"
            attempt["accepted_revision"] = state["revision"] + 1
            attempt["accepted_at"] = iso()
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
            for dep in plan["tasks"][attempt["task_id"]]["depends_on"]:
                require(self.accepted(state, plan, dep), "dependency_changed", "Rebased dependencies must be accepted and available")
            attempt["plan_hash"] = plan["hash"]
            attempt["task_hash"] = plan["task_hashes"][attempt["task_id"]]
            attempt["contract_hash"] = digest(plan["contracts"])
            attempt["base_head"] = self.git("rev-parse", "HEAD").decode().strip()
            attempt["baseline_index_sha256"] = self.index_digest()
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
                     "attempt_id": args.attempt or data.get("attempt", {}).get("id"), "task_id": args.task or data.get("attempt", {}).get("task_id"),
                     "reason": args.reason, "previous": prior, "previous_gate": previous_gate}
            if args.action in {"handoff", "recover-handoff"}:
                event["handoff"] = copy.deepcopy(data["handoff"])
            if args.action == "record-snapshot":
                event["snapshot_id"] = data["snapshot"]["id"]
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
    result.add_argument("action", choices=sorted(READ_ONLY | {"record-gate", "record-snapshot", "reserve", "bind", "claim", "submit", "bind-reviewer", "review", "accept", "block", "resume", "release", "cancel", "extend", "rebase", "handoff", "recover-handoff"}))
    result.add_argument("--expected-revision", type=int)
    result.add_argument("--actor")
    result.add_argument("--run-id")
    result.add_argument("--from-run-id")
    result.add_argument("--task")
    result.add_argument("--attempt")
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
