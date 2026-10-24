"""Disposable-repository tests. Never mutate the project's Git index or ledger."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock
import uuid
from datetime import datetime, timedelta, timezone


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "mvp_coordination.py"
SPEC = importlib.util.spec_from_file_location("mvp_coordination_test_target", SCRIPT)
COORD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COORD)
FIXTURES = Path(os.environ.get("MVP_COORDINATION_TEST_ROOT", REPOSITORY / ".state" / "mvp-coordination-tests")).resolve()
SNAPSHOT_RECOVERY_AUTHORIZATION = "Разработать reviewed recovery — расширить scope на отдельный проверенный механизм координатора, сохранив исходный snapshot"
SNAPSHOT_RECOVERY_AMENDMENT_AUTHORIZATION = "Разработать reviewed recovery — расширить scope на отдельный проверенный механизм координатора, сохранив исходный snapshot."
WORKSPACE_DELTA_RECOVERY_AUTHORIZATION = "Разработать reviewed recovery — расширить scope на отдельный проверенный механизм координатора, сохранив исходный snapshot."
SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION = WORKSPACE_DELTA_RECOVERY_AUTHORIZATION
B31_SNAPSHOT_ID = "ac1a0c41-a76b-46c6-84f2-aa2008ae2837"


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        FIXTURES.mkdir(parents=True, exist_ok=True, mode=0o777)
        self.root = FIXTURES / ("case-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o777)
        self.root_run = str(uuid.uuid4())
        self.write(".gitignore", ".state/\n.agent-state/\nartifacts/\n__pycache__/\n")
        self.write("docs/contract.md", "Original contract\n")
        self.plan = {"schema_version": 1, "updated_on": "2026-10-23", "product_status": "not_implemented",
                     "contract_documents": ["contract.md"], "tasks": [
            {"id": "B00", "priority": "P0", "status": "needs_external_answer", "depends_on": [], "questions": ["Q-001", "Q-002", "Q-003"], "evidence": []},
            {"id": "B01", "priority": "P0", "status": "planned", "depends_on": ["B00"], "suggested_paths": ["wrong/hint/**"]},
            {"id": "B02", "priority": "P0", "status": "planned", "depends_on": ["B01"]},
            {"id": "B03", "priority": "P0", "status": "planned", "depends_on": ["B00"]},
            {"id": "B14", "priority": "P0", "status": "planned", "depends_on": ["B01"], "suggested_paths": ["b.py"]},
            {"id": "B12", "priority": "P1", "status": "planned", "depends_on": ["B01"]},
            {"id": "B31", "priority": "P0", "status": "planned", "depends_on": ["B14"],
             "suggested_paths": ["AGENTS.md", "components.json"]},
        ]}
        self.save_plan()
        self.write("a.py", "old a\n")
        self.write("b.py", "old b\n")
        self.write("c.py", "old c\n")
        self.write("AGENTS.md", "Original commit rules\n")
        self.write("components.json", "{}\n")
        self.write("other.py", "Other snapshotted source\n")
        self.write("b12.py", "B12 fixture source\n")
        self.write("b03.py", "B03 fixture source\n")
        self.git("init", "--quiet")
        self.git("add", "--all")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
        self.coordinator = COORD.Coordinator(self.root)

    def tearDown(self):
        resolved = self.root.resolve()
        self.assertTrue(resolved.is_relative_to(FIXTURES.resolve()))
        self.assertRegex(resolved.name, r"^case-[a-f0-9]{32}$")
        # Git marks loose objects read-only on Windows. Only clear that flag
        # within this exact, validated disposable repository before retrying.
        def retry_read_only(function, path, exception):
            target = Path(path).resolve()
            self.assertTrue(target.is_relative_to(resolved))
            if not isinstance(exception, PermissionError):
                raise exception
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            function(path)
        shutil.rmtree(resolved, onexc=retry_read_only)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o777)
        target.write_text(value, encoding="utf-8")
        return target

    def json_file(self, payload):
        path = "artifacts/evidence/" + uuid.uuid4().hex + ".json"
        self.write(path, json.dumps(payload))
        return path

    def save_plan(self):
        self.write("docs/backlog.json", json.dumps(self.plan))

    def git(self, *args):
        result = subprocess.run(["git", "-c", f"safe.directory={self.root.as_posix()}", "-C", str(self.root), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def args(self, action, *, actor="/root", run_id=None, revision=None, **kwargs):
        values = ["--root", str(self.root), action]
        if action not in COORD.READ_ONLY:
            values += ["--actor", actor, "--run-id", run_id or self.root_run, "--expected-revision",
                       str(self.coordinator.read()["revision"] if revision is None else revision)]
        for key, value in kwargs.items():
            flag = "--" + key.replace("_", "-")
            for item in value if isinstance(value, list) else [value]:
                values.extend([flag, str(item)])
        return COORD.parser().parse_args(values)

    def call(self, action, **kwargs):
        return self.coordinator.execute(self.args(action, **kwargs))

    def refused(self, code, action, **kwargs):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        with self.assertRaises(COORD.Refusal) as caught:
            self.call(action, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected operation changed ledger")

    def gate(self, decision="allowed"):
        data = {"task_id": "B00", "actor": "/root", "answers": [
            {"question_id": question, "decision": decision, "answer": "Actual fixture answer", "source": "Fixture organizer record", "received_at": COORD.iso()}
            for question in ("Q-001", "Q-002", "Q-003")]}
        return self.call("record-gate", task="B00", evidence=self.json_file(data), reason="Sourced fixture answers")

    def reserve(self, task="B01", files=None, **kwargs):
        return self.call("reserve", task=task, file=files or ["a.py"], reason="Exact fixture slice", **kwargs)["attempt"]

    def handoff_payload(self, next_run, **updates):
        payload = {"kind": COORD.HANDOFF_EVIDENCE_KIND, "source": "direct_user_reply_in_current_chat",
                   "current_chat_selection": "в текущем чате", "current_chat_selected": True,
                   "user_authorization": "Да, добавить handoff (Recommended)",
                   "previous_orchestrator": {"target": "/root", "run_id": self.root_run},
                   "next_orchestrator": {"target": "/root", "run_id": next_run}, "observed_at": COORD.iso()}
        payload.update(updates)
        return payload

    def snapshot_payload(self, files, **updates):
        source_id = str(uuid.uuid4())
        observed_at = COORD.iso()
        paths = sorted(files)
        source = {"id": source_id, "title": "Completed separate workspace task", "status": "completed",
                  "root": str(self.root), "files": paths}
        payload = {"kind": COORD.SNAPSHOT_EVIDENCE_KIND, "observed_at": observed_at,
                   "source_chat": source,
                   "source_observation": {"tool": "mcp__codex_app__read_thread", "thread_id": source_id,
                                          "title": source["title"], "status": source["status"], "root": source["root"],
                                          "files": paths, "observed_at": observed_at},
                   "authorization": {"source": COORD.SNAPSHOT_AUTH_SOURCE, "current_chat_run_id": self.root_run,
                                     "authorized": True, "user_authorization": "Add an audit snapshot for this completed separate task"}}
        payload.update(updates)
        return payload

    def record_snapshot(self, files, payload=None, *, run_id=None, environment_run=None,
                        reason="User-authorized fixture snapshot", snapshot_id=None):
        run_id = run_id or self.root_run
        evidence = self.json_file(payload or self.snapshot_payload(files))
        environment_run = environment_run or run_id
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            if snapshot_id is None:
                result = self.call("record-snapshot", actor="/root", run_id=run_id, file=files,
                                   evidence=evidence, reason=reason)
            else:
                with mock.patch.object(COORD.uuid, "uuid4", return_value=uuid.UUID(snapshot_id)):
                    result = self.call("record-snapshot", actor="/root", run_id=run_id, file=files,
                                       evidence=evidence, reason=reason)
        return result["snapshot"], evidence

    def propose_snapshot_recovery(self, attempt, snapshot_id, *, authorization=SNAPSHOT_RECOVERY_AUTHORIZATION,
                                  actor="/root", run_id=None, environment_run=None, reason="Recover an explicitly drifted source snapshot"):
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            return self.call("propose-snapshot-recovery", actor=actor, run_id=run_id, attempt=attempt["id"],
                             snapshot_id=snapshot_id, user_authorization=authorization, reason=reason)

    def amend_snapshot_recovery(self, attempt, snapshot_id, *, actor="/root", run_id=None,
                                environment_run=None, authorization=SNAPSHOT_RECOVERY_AMENDMENT_AUTHORIZATION,
                                reason="One-time pre-submit manifest amendment"):
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            return self.call("amend-snapshot-recovery", actor=actor, run_id=run_id,
                             attempt=attempt["id"], snapshot_id=snapshot_id,
                             user_authorization=authorization, reason=reason)

    def continue_snapshot_recovery(self, attempt, snapshot_id, *, actor="/root", run_id=None,
                                   environment_run=None,
                                   authorization=SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION,
                                   manifest_sha256=None, reason="Append the next exact snapshot recovery continuation"):
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        current = next(item for item in self.coordinator.read()["attempts"]
                       if item["id"] == (attempt["id"] if isinstance(attempt, dict) else attempt))
        snapshot = next(item for item in self.coordinator.read()["snapshots"] if item["id"] == snapshot_id)
        if manifest_sha256 is None:
            manifest_sha256 = COORD.digest(self.coordinator.exact_manifest(snapshot["files"]))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            return self.call("continue-snapshot-recovery", actor=actor, run_id=run_id,
                             attempt=current["id"], snapshot_id=snapshot_id,
                             manifest_sha256=manifest_sha256, user_authorization=authorization,
                             reason=reason)

    def refused_snapshot_recovery_continuation(self, code, attempt, snapshot_id, *, actor="/root",
                                               run_id=None, environment_run=None,
                                               authorization=SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION,
                                               manifest_sha256=None):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        if manifest_sha256 is None:
            snapshot = next(item for item in self.coordinator.read()["snapshots"] if item["id"] == snapshot_id)
            manifest_sha256 = COORD.digest(self.coordinator.exact_manifest(snapshot["files"]))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("continue-snapshot-recovery", actor=actor, run_id=run_id,
                          attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                          snapshot_id=snapshot_id, manifest_sha256=manifest_sha256,
                          user_authorization=authorization,
                          reason="Append the next exact snapshot recovery continuation")
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected snapshot continuation changed the append-only ledger")

    def refused_snapshot_recovery_amendment(self, code, attempt, snapshot_id, *, actor="/root",
                                            run_id=None, environment_run=None):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run or run_id or self.root_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("amend-snapshot-recovery", actor=actor, run_id=run_id or self.root_run,
                          attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                          snapshot_id=snapshot_id, user_authorization=SNAPSHOT_RECOVERY_AMENDMENT_AUTHORIZATION,
                          reason="One-time pre-submit manifest amendment")
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected recovery amendment changed ledger")

    def refused_snapshot_recovery(self, code, attempt, snapshot_id, *, authorization=SNAPSHOT_RECOVERY_AUTHORIZATION,
                                  actor="/root", run_id=None, environment_run=None):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run or run_id or self.root_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("propose-snapshot-recovery", actor=actor, run_id=run_id or self.root_run,
                          attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                          snapshot_id=snapshot_id, user_authorization=authorization,
                          reason="Recover an explicitly drifted source snapshot")
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected recovery proposal changed ledger")

    def prepare_snapshot_recovery(self):
        self.gate()
        accepted = self.complete("B01", ["b.py"])
        snapshot, _ = self.record_snapshot(["b.py", "c.py"])
        attempt = self.worker(self.reserve("B14", ["b.py"], adopt_accepted=[accepted["id"]]))
        self.write("b.py", "reviewed B14 recovery state\n")
        proposal = self.propose_snapshot_recovery(attempt, snapshot["id"])["proposal"]
        return accepted, snapshot, attempt, proposal

    def prepare_workspace_delta_recovery(self):
        accepted, snapshot, attempt, proposal = self.prepare_snapshot_recovery()
        self.write("b.py", "amended B14 snapshot recovery state\n")
        amendment = self.amend_snapshot_recovery(attempt, snapshot["id"])["amendment"]
        return accepted, snapshot, attempt, proposal, amendment

    def prepare_b31_snapshot_attempt(self):
        self.gate()
        accepted = self.complete("B01", ["b.py"])
        self.complete("B14", ["b.py"], adopt_accepted=[accepted["id"]])
        snapshot, _ = self.record_snapshot(["AGENTS.md", "components.json"], snapshot_id=B31_SNAPSHOT_ID)
        attempt = self.worker(self.reserve("B31", ["AGENTS.md", "components.json"]))
        return snapshot, attempt

    def prepare_b31_snapshot_recovery(self):
        snapshot, attempt = self.prepare_b31_snapshot_attempt()
        self.write("AGENTS.md", "Restored task-owned commit rules\n")
        proposal = self.propose_snapshot_recovery(attempt, snapshot["id"])["proposal"]
        return snapshot, attempt, proposal

    def record_workspace_delta_recovery(self, attempt, *, authorization=WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                                        actor="/root", run_id=None, environment_run=None, manifest_sha256=None,
                                        reason="Preserve the reviewed exact workspace delta"):
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        if manifest_sha256 is None:
            state = self.coordinator.read()
            current_attempt = next(item for item in state["attempts"] if item["id"] ==
                                   (attempt["id"] if isinstance(attempt, dict) else attempt))
            manifest_sha256 = COORD.digest(self.coordinator.unexplained_workspace_delta(state, current_attempt))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            return self.call("record-workspace-recovery", actor=actor, run_id=run_id,
                             attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                             user_authorization=authorization, manifest_sha256=manifest_sha256, reason=reason)

    def amend_workspace_delta_recovery(self, attempt, *, authorization=WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                                       actor="/root", run_id=None, environment_run=None, manifest_sha256=None,
                                       reason="Append the next reviewed workspace delta manifest"):
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        if manifest_sha256 is None:
            state = self.coordinator.read()
            current_attempt = next(item for item in state["attempts"] if item["id"] ==
                                   (attempt["id"] if isinstance(attempt, dict) else attempt))
            manifest_sha256 = COORD.digest(self.coordinator.unexplained_workspace_delta(state, current_attempt))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            return self.call("amend-workspace-recovery", actor=actor, run_id=run_id, task="B14",
                             attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                             user_authorization=authorization, manifest_sha256=manifest_sha256, reason=reason)

    def refused_workspace_delta_amendment(self, code, attempt, *, authorization=WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                                          actor="/root", run_id=None, environment_run=None, manifest_sha256=None):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        if manifest_sha256 is None:
            state = self.coordinator.read()
            current_attempt = next(item for item in state["attempts"] if item["id"] ==
                                   (attempt["id"] if isinstance(attempt, dict) else attempt))
            manifest_sha256 = COORD.digest(self.coordinator.unexplained_workspace_delta(state, current_attempt))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("amend-workspace-recovery", actor=actor, run_id=run_id, task="B14",
                          attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                          user_authorization=authorization, manifest_sha256=manifest_sha256,
                          reason="Append the next reviewed workspace delta manifest")
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected workspace amendment changed the append-only ledger")

    def refused_workspace_delta_recovery(self, code, attempt, *, authorization=WORKSPACE_DELTA_RECOVERY_AUTHORIZATION,
                                         actor="/root", run_id=None, environment_run=None, manifest_sha256=None,
                                         reason="Preserve the reviewed exact workspace delta"):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        if manifest_sha256 is None:
            manifest_sha256 = "0" * 64
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("record-workspace-recovery", actor=actor, run_id=run_id,
                          attempt=attempt["id"] if isinstance(attempt, dict) else attempt,
                          user_authorization=authorization, manifest_sha256=manifest_sha256, reason=reason)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected workspace recovery changed ledger")

    def refused_snapshot(self, code, files, payload=None, *, run_id=None, environment_run=None):
        before = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        evidence = self.json_file(payload or self.snapshot_payload(files))
        run_id = run_id or self.root_run
        environment_run = environment_run or run_id
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": environment_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("record-snapshot", actor="/root", run_id=run_id, file=files,
                          evidence=evidence, reason="User-authorized fixture snapshot")
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.coordinator.state_path.read_bytes() if self.coordinator.state_path.exists() else None
        self.assertEqual(before, after, "Rejected snapshot changed ledger")

    def proof(self, attempt, role, target=None, run_id=None, parent_run=None):
        target = target or "/root/" + role + "_" + uuid.uuid4().hex
        run_id = run_id or str(uuid.uuid4())
        return {"tool": "collaboration.spawn_agent", "identity_source": "synthetic_disposable_fixture_only",
                "parent_target": "/root", "parent_run_id": parent_run or self.root_run,
                "target": target, "run_id": run_id, "observed_at": COORD.iso(),
                "task_id": attempt["task_id"], "attempt_id": attempt["id"], "role": role}

    def worker(self, attempt):
        proof = self.proof(attempt, "worker")
        self.call("bind", attempt=attempt["id"], tool_evidence=self.json_file(proof))
        result = self.call("claim", attempt=attempt["id"], actor=proof["target"], run_id=proof["run_id"])
        return result["attempt"]

    def submit(self, attempt):
        report = {"task_id": attempt["task_id"], "attempt_id": attempt["id"], "summary": "Fixture result",
                  "checks": [{"name": "Fixture check", "status": "passed", "evidence": "Observed isolated test result"}]}
        return self.call("submit", attempt=attempt["id"], actor=attempt["worker"]["target"], run_id=attempt["worker"]["run_id"], evidence=self.json_file(report))

    def reviewer(self, attempt):
        proof = self.proof(attempt, "reviewer")
        return self.call("bind-reviewer", attempt=attempt["id"], tool_evidence=self.json_file(proof))

    def review(self, bound, result="GO", *, workspace_delta_stale_observation=None):
        attempt = bound["attempt"]
        report = {"task_id": attempt["task_id"], "attempt_id": attempt["id"], "iteration": attempt["iteration"],
                  "candidate_digest": bound["candidate_digest"], "plan_hash": bound["plan_hash"], "review_nonce": bound["review_nonce"],
                  "result": result, "summary": "Independent fixture review",
                  "checks": [{"name": "Acceptance", "status": "passed" if result == "GO" else "failed", "evidence": "Actual fixture result"}],
                  "findings": [] if result == "GO" else ["Repair fixture calculation"]}
        if attempt.get("candidate", {}).get("workspace_delta_recovery") is not None:
            report["workspace_delta_recovery"] = copy.deepcopy(attempt["candidate"]["workspace_delta_recovery"])
        if (attempt.get("task_id") == COORD.SNAPSHOT_RECOVERY_B31_TASK
                or any(item.get("continuation_id") for item in attempt.get("candidate", {}).get("snapshot_recoveries", []))):
            report["snapshot_recoveries"] = copy.deepcopy(attempt["candidate"]["snapshot_recoveries"])
        if workspace_delta_stale_observation is not None:
            report["workspace_delta_stale_observation"] = copy.deepcopy(workspace_delta_stale_observation)
        return self.call("review", attempt=attempt["id"], actor=attempt["reviewer"]["target"], run_id=attempt["reviewer"]["run_id"], evidence=self.json_file(report))

    def workspace_delta_stale_observation(self, attempt, candidate=None):
        state = self.coordinator.read()
        live_attempt = next(item for item in state["attempts"] if item["id"] == attempt["id"])
        candidate = candidate or live_attempt["candidate"]
        manifest = self.coordinator.unexplained_workspace_delta(state, live_attempt)
        owner = live_attempt["orchestrator"]
        summary = candidate["workspace_delta_recovery"]
        return {"source": "root_read_only_workspace_delta_observation", "observed_at": COORD.iso(),
                "observed_by": owner["target"], "observed_run_id": owner["run_id"],
                "task_id": "B14", "attempt_id": attempt["id"], "iteration": candidate["iteration"],
                "plan_hash": candidate["plan_hash"], "base_head": candidate["base_head"],
                "index_sha256": candidate["index_sha256"],
                "baseline_sha256": COORD.digest(live_attempt["baseline"]),
                "scope_sha256": COORD.digest(live_attempt["files"]),
                "candidate_digest": candidate["digest"], "chain_head_id": summary["chain_head_id"],
                "chain_head_sha256": summary["chain_head_sha256"],
                "manifest": manifest, "manifest_sha256": COORD.digest(manifest)}

    def complete(self, task="B01", files=None, **reserve_kwargs):
        attempt = self.worker(self.reserve(task, files, **reserve_kwargs))
        self.write(attempt["files"][0], "accepted " + task + "\n")
        submitted = self.submit(attempt)
        self.review(self.reviewer(submitted["attempt"]))
        return self.call("accept", attempt=attempt["id"], reason="Independent fixture GO")["attempt"]

    def test_read_only_missing_state_and_external_gate(self):
        self.assertFalse(self.coordinator.directory.exists())
        self.assertEqual(self.call("status")["revision"], 0)
        following = self.call("next")
        self.assertEqual(following["ready_p0"], [])
        self.assertIn("external_gate_required", following["tasks"][0]["reasons"])
        self.assertFalse(self.call("check", task="B01", file=["a.py"])["ready"])
        self.assertFalse(self.coordinator.directory.exists())
        self.refused("task_not_ready", "reserve", task="B01", file=["a.py"], reason="Cannot bypass B00")
        for decision in ("unknown", "denied", None):
            self.refused("gate_not_allowed", "record-gate", task="B00", reason="Bad answer", evidence=self.json_file({
                "task_id": "B00", "actor": "/root", "answers": [{"question_id": q, "decision": decision, "answer": "Unknown", "source": "Fixture", "received_at": COORD.iso()} for q in ("Q-001", "Q-002", "Q-003")]}))
        self.gate()
        self.assertEqual(self.call("next")["ready_p0"], ["B01", "B03"])

    def test_handoff_refuses_wrong_stale_and_active_authority_without_mutating_ledger(self):
        self.gate()
        next_run = str(uuid.uuid4())
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            self.refused("wrong_owner", "handoff", run_id=next_run, from_run_id=str(uuid.uuid4()), evidence=self.json_file(self.handoff_payload(next_run)), reason="Fixture handoff")
            self.refused("invalid_handoff_target", "handoff", actor="/root/other", run_id=next_run,
                         from_run_id=self.root_run, evidence=self.json_file(self.handoff_payload(next_run)), reason="Fixture handoff")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": str(uuid.uuid4())}):
            self.refused("current_run_mismatch", "handoff", run_id=next_run, from_run_id=self.root_run, reason="Fixture handoff")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            same_owner = self.handoff_payload(self.root_run)
            self.refused("same_owner", "handoff", run_id=self.root_run, from_run_id=self.root_run,
                         evidence=self.json_file(same_owner), reason="Fixture handoff")
            self.refused("missing_evidence", "handoff", run_id=next_run, from_run_id=self.root_run, evidence="", reason="Fixture handoff")
            invalid_payloads = [
                self.handoff_payload(next_run, kind="wrong_kind"),
                self.handoff_payload(next_run, source="inferred"),
                self.handoff_payload(next_run, current_chat_selection="other chat"),
                self.handoff_payload(next_run, current_chat_selected=False),
                self.handoff_payload(next_run, user_authorization=""),
                self.handoff_payload(next_run, previous_orchestrator={"target": "/root/other", "run_id": self.root_run}),
                self.handoff_payload(next_run, next_orchestrator={"target": "/root", "run_id": str(uuid.uuid4())}),
            ]
            for invalid in invalid_payloads:
                self.refused("invalid_handoff_evidence", "handoff", run_id=next_run, from_run_id=self.root_run,
                             evidence=self.json_file(invalid), reason="Fixture handoff")
            stale = self.handoff_payload(next_run, observed_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
            self.refused("stale_evidence", "handoff", run_id=next_run, from_run_id=self.root_run,
                         evidence=self.json_file(stale), reason="Fixture handoff")
            self.refused("stale_revision", "handoff", run_id=next_run, from_run_id=self.root_run, revision=0, reason="Fixture handoff")

        self.reserve()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            self.refused("active_attempts", "handoff", run_id=next_run, from_run_id=self.root_run, reason="Fixture handoff")

    def test_handoff_records_authorization_and_only_new_run_can_continue(self):
        self.gate()
        self.complete()
        previous_state = copy.deepcopy(self.coordinator.read())
        next_run = str(uuid.uuid4())
        payload = self.handoff_payload(next_run)
        evidence_path = self.json_file(payload)
        raw_evidence = (self.root / evidence_path).read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            result = self.call("handoff", run_id=next_run, from_run_id=self.root_run, evidence=evidence_path,
                               reason="User explicitly selected the current chat for MVP continuation")
        self.assertEqual(result["action"], "handoff")
        self.assertEqual(result["revision"], previous_state["revision"] + 1)
        self.assertEqual(result["handoff"]["previous_orchestrator"], {"target": "/root", "run_id": self.root_run})
        self.assertEqual(result["handoff"]["orchestrator"], {"target": "/root", "run_id": next_run})
        state = self.coordinator.read()
        self.assertEqual(state["orchestrator"], {"target": "/root", "run_id": next_run})
        authorization = state["events"][-1]["handoff"]["authorization"]
        self.assertEqual(authorization["path"], evidence_path)
        self.assertEqual(authorization["sha256"], hashlib.sha256(raw_evidence).hexdigest())
        self.assertEqual(authorization["payload"]["user_authorization"], "Да, добавить handoff (Recommended)")
        self.assertEqual(state["attempts"], previous_state["attempts"])
        self.assertEqual(state["gates"], previous_state["gates"])
        self.assertEqual(state["events"][:-1], previous_state["events"])
        self.refused("wrong_role", "reserve", run_id=self.root_run, task="B03", file=["b.py"], reason="Old run")
        attempt = self.call("reserve", run_id=next_run, task="B03", file=["b.py"], reason="Current run owns ledger")["attempt"]
        self.assertEqual(attempt["status"], "reserved")

    def recovery_payload(self, attempt, next_run, **updates):
        payload = self.handoff_payload(next_run, kind=COORD.RECOVERY_EVIDENCE_KIND,
            attempt_id=attempt["id"], task_id=attempt["task_id"],
            abandoned_worker={key: attempt["worker"][key] for key in ("target", "run_id")},
            policy="abandon_unverified_worker", abandonment_authorized=True,
            manifest=self.coordinator.manifest(attempt["files"]))
        payload.update(updates)
        return payload

    def recover(self, attempt, next_run, payload=None, **kwargs):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            return self.call("recover-handoff", run_id=next_run, from_run_id=self.root_run,
                             attempt=attempt["id"], evidence=self.json_file(payload or self.recovery_payload(attempt, next_run)),
                             reason="User explicitly authorized recovery", **kwargs)

    def test_recovery_is_atomic_audited_nonacceptance_and_fences_old_actors(self):
        self.gate()
        attempt = self.worker(self.reserve(files=["a.py", "absent.py"]))
        self.write("a.py", "unfinished work\n")
        previous = copy.deepcopy(self.coordinator.read())
        next_run = str(uuid.uuid4())
        recovered = self.recover(attempt, next_run)["attempt"]
        state = self.coordinator.read()
        self.assertEqual(state["revision"], previous["revision"] + 1)
        self.assertEqual(state["events"][:-1], previous["events"])
        self.assertEqual(state["gates"], previous["gates"])
        self.assertEqual(recovered["status"], "cancelled")
        self.assertEqual(recovered["baseline"], attempt["baseline"])
        self.assertEqual(recovered["worker"], attempt["worker"])
        self.assertNotIn("quiescence", recovered)
        self.assertEqual(recovered["closed_manifest"]["absent.py"], {"kind": "absent"})
        receipt = recovered["operator_abandonment"]["authorization"]
        self.assertEqual(receipt["sha256"], hashlib.sha256((self.root / receipt["path"]).read_bytes()).hexdigest())
        self.assertEqual(state["events"][-1]["handoff"]["authorization"], receipt)
        self.assertFalse(self.coordinator.accepted(state, self.coordinator.load_plan(), "B01"))
        for action in ("claim", "submit", "block", "review", "resume", "accept", "release", "cancel", "rebase"):
            self.refused("closed_attempt", action, attempt=attempt["id"],
                         actor=attempt["worker"]["target"], run_id=attempt["worker"]["run_id"])
        self.refused("wrong_role", "reserve", task="B03", file=["b.py"], reason="Old owner")
        self.refused("dirty_scope", "reserve", run_id=next_run, task="B01", file=["a.py"], reason="No adoption flag")
        fresh = self.call("reserve", run_id=next_run, task="B01", file=["a.py", "absent.py"],
                          adopt_recovered=[attempt["id"]], reason="Explicitly inspect unfinished work")["attempt"]
        self.assertEqual(fresh["adopted_recovered"], [attempt["id"]])
        self.assertEqual(fresh["baseline_scope"], recovered["closed_manifest"])
        self.assertNotEqual(fresh["id"], attempt["id"])
        self.assertIsNone(fresh["candidate"])
        self.assertIsNone(fresh["worker"])

    def test_recovery_rejects_wrong_provenance_drift_other_live_and_cas(self):
        self.gate()
        attempt = self.worker(self.reserve())
        next_run = str(uuid.uuid4())
        invalid = [dict(policy="stopped"), dict(abandonment_authorized=False), dict(abandoned_worker={}),
                   dict(attempt_id=str(uuid.uuid4())), dict(task_id="B03"), dict(writers_stopped=True),
                   dict(quiescence={}), dict(kind=COORD.HANDOFF_EVIDENCE_KIND), dict(source="inferred"),
                   dict(current_chat_selected=False), dict(next_orchestrator={}), dict(previous_orchestrator={}),
                   dict(user_authorization="")]
        for updates in invalid:
            before = self.coordinator.state_path.read_bytes()
            with self.assertRaises(COORD.Refusal):
                self.recover(attempt, next_run, self.recovery_payload(attempt, next_run, **updates))
            self.assertEqual(self.coordinator.state_path.read_bytes(), before)
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            self.refused("recovery_manifest_drift", "recover-handoff", run_id=next_run, from_run_id=self.root_run,
                         attempt=attempt["id"], evidence=self.json_file(self.recovery_payload(attempt, next_run, manifest={})), reason="Recovery")
            self.refused("wrong_owner", "recover-handoff", run_id=next_run, from_run_id=str(uuid.uuid4()),
                         attempt=attempt["id"], reason="Recovery")
            self.refused("stale_evidence", "recover-handoff", run_id=next_run, from_run_id=self.root_run,
                         attempt=attempt["id"], reason="Recovery", evidence=self.json_file(self.recovery_payload(
                             attempt, next_run, observed_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": str(uuid.uuid4())}):
            self.refused("current_run_mismatch", "recover-handoff", run_id=next_run, from_run_id=self.root_run,
                         attempt=attempt["id"], reason="Recovery")
        before = self.coordinator.state_path.read_bytes()
        with self.assertRaises(COORD.Refusal) as stale:
            self.recover(attempt, next_run, revision=0)
        self.assertEqual(stale.exception.code, "stale_revision")
        self.assertEqual(self.coordinator.state_path.read_bytes(), before)
        self.reserve("B03", ["b.py"])
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": next_run}):
            self.refused("active_attempts", "recover-handoff", run_id=next_run, from_run_id=self.root_run,
                         attempt=attempt["id"], reason="Cannot abandon another claim implicitly")

    def test_recovery_adoption_rejects_other_task_changed_bytes_and_ordinary_cancel(self):
        self.gate()
        ordinary = self.reserve("B03", ["b.py"])
        self.call("cancel", attempt=ordinary["id"], reason="Ordinary unbound cancellation")
        self.refused("invalid_recovery_adoption", "reserve", task="B03", file=["b.py"],
                     adopt_recovered=[ordinary["id"]], reason="Not recovered")
        attempt = self.worker(self.reserve())
        self.write("a.py", "unfinished\n")
        next_run = str(uuid.uuid4())
        self.recover(attempt, next_run)
        self.refused("invalid_recovery_adoption", "reserve", run_id=next_run, task="B03", file=["a.py"],
                     adopt_recovered=[attempt["id"]], reason="Wrong task")
        self.write("a.py", "post recovery drift\n")
        self.refused("recovery_manifest_drift", "reserve", run_id=next_run, task="B01", file=["a.py"],
                     adopt_recovered=[attempt["id"]], reason="Changed bytes")

    def test_snapshot_records_exact_manifest_authorization_and_nonacceptance(self):
        self.gate()
        source = self.root / "a.py"
        payload = b"completed external work\n"
        source.write_bytes(payload)
        previous = copy.deepcopy(self.coordinator.read())
        snapshot, evidence_path = self.record_snapshot(["a.py"])
        raw_evidence = (self.root / evidence_path).read_bytes()
        state = self.coordinator.read()
        self.assertEqual(snapshot["source_chat"]["status"], "completed")
        self.assertEqual(snapshot["source_chat"]["root"], str(self.root))
        self.assertEqual(snapshot["files"], ["a.py"])
        self.assertEqual(snapshot["manifest"], [{"path": "a.py", "mode": "100644",
                                                  "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}])
        self.assertEqual(snapshot["evidence"]["sha256"], hashlib.sha256(raw_evidence).hexdigest())
        self.assertEqual(snapshot["evidence"]["payload"]["authorization"]["source"], COORD.SNAPSHOT_AUTH_SOURCE)
        self.assertEqual(snapshot["actor"], "/root")
        self.assertEqual(snapshot["run_id"], self.root_run)
        self.assertTrue(snapshot["recorded_at"])
        self.assertEqual(state["revision"], previous["revision"] + 1)
        self.assertEqual(state["events"][:-1], previous["events"])
        self.assertEqual(state["events"][-1]["snapshot_id"], snapshot["id"])
        self.assertEqual(state["events"][-1]["actor"], "/root")
        self.assertEqual(state["events"][-1]["run_id"], self.root_run)
        self.assertFalse(self.coordinator.accepted(state, self.coordinator.load_plan(), "B01"))
        self.assertNotIn("a.py", self.coordinator.lineage(state))
        self.assertEqual(state["attempts"], previous["attempts"])

    def test_snapshot_refuses_stale_wrong_source_authorization_observation_and_file_list(self):
        self.gate()
        self.write("a.py", "snapshot target\n")
        stale = self.snapshot_payload(["a.py"], observed_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
        self.refused_snapshot("stale_evidence", ["a.py"], stale)

        invalid = self.snapshot_payload(["a.py"])
        invalid["source_chat"]["status"] = "running"
        self.refused_snapshot("invalid_snapshot_source", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["source_chat"]["root"] = str(self.root.parent)
        invalid["source_observation"]["root"] = str(self.root.parent)
        self.refused_snapshot("invalid_snapshot_source", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["source_chat"]["files"] = ["b.py"]
        self.refused_snapshot("snapshot_file_mismatch", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["authorization"]["source"] = "inferred"
        self.refused_snapshot("invalid_snapshot_authorization", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["authorization"]["authorized"] = False
        self.refused_snapshot("invalid_snapshot_authorization", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["authorization"]["current_chat_run_id"] = str(uuid.uuid4())
        self.refused_snapshot("invalid_snapshot_authorization", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        invalid["source_observation"]["tool"] = "unverified"
        self.refused_snapshot("invalid_snapshot_observation", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"], kind="unverified")
        self.refused_snapshot("invalid_snapshot_evidence", ["a.py"], invalid)

        invalid = self.snapshot_payload(["a.py"])
        self.refused_snapshot("current_run_mismatch", ["a.py"], invalid,
                              environment_run=str(uuid.uuid4()))
        self.refused_snapshot("unsafe_path", ["../outside"], self.snapshot_payload(["../outside"]))
        self.refused_snapshot("invalid_scope", ["b.py", "a.py"], self.snapshot_payload(["a.py", "b.py"]))

    def test_snapshot_refuses_any_path_overlapping_an_active_claim(self):
        self.gate()
        self.worker(self.reserve(files=["a.py"]))
        self.refused_snapshot("path_conflict", ["a.py"])

    def test_snapshot_bytes_allow_exact_out_of_scope_change_then_fail_closed_on_drift(self):
        self.gate()
        attempt = self.worker(self.reserve(files=["a.py"]))
        self.write("b.py", "completed separate work\n")
        snapshot, _ = self.record_snapshot(["b.py"])
        self.write("a.py", "own implementation\n")
        submitted = self.submit(attempt)
        self.assertEqual(submitted["attempt"]["status"], "awaiting_review")
        self.assertFalse(self.coordinator.accepted(self.coordinator.read(), self.coordinator.load_plan(), "B03"))

        peer = self.worker(self.reserve(task="B03", files=["c.py"]))
        self.write("c.py", "own peer implementation\n")
        self.write("b.py", "changed after immutable capture\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"],
                     actor=peer["worker"]["target"], run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": peer["task_id"], "attempt_id": peer["id"], "summary": "Drift refusal",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        self.write("b.py", "completed separate work\n")
        (self.root / "b.py").unlink()
        self.refused("snapshot_drift", "submit", attempt=peer["id"],
                     actor=peer["worker"]["target"], run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": peer["task_id"], "attempt_id": peer["id"], "summary": "Missing snapshot refusal",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

    def test_snapshot_rejects_replacement(self):
        self.gate()
        self.write("a.py", "frozen source\n")
        self.record_snapshot(["a.py"])
        self.refused_snapshot("snapshot_path_conflict", ["a.py"])

    def test_snapshot_reads_legacy_v1_ledger_without_rewriting_event_history(self):
        self.gate()
        self.write("a.py", "frozen source\n")
        state = self.coordinator.read()
        prior_events = copy.deepcopy(state["events"])
        state.pop("snapshots")
        state.pop("snapshot_recoveries", None)
        state.pop("snapshot_recovery_amendments", None)
        state.pop("workspace_delta_recoveries", None)
        self.coordinator.state_path.write_bytes(COORD.canonical(state) + b"\n")
        legacy = self.coordinator.read()
        self.assertEqual(legacy["snapshots"], [])
        self.assertNotIn("snapshot_recoveries", legacy)
        self.assertNotIn("snapshot_recovery_amendments", legacy)
        self.assertNotIn("workspace_delta_recoveries", legacy)
        self.assertEqual(legacy["events"], prior_events)
        second, _ = self.record_snapshot(["b.py"])
        current = self.coordinator.read()
        self.assertEqual(current["snapshots"], [second])
        self.assertNotIn("snapshot_recoveries", current)
        self.assertNotIn("snapshot_recovery_amendments", current)
        self.assertNotIn("workspace_delta_recoveries", current)
        self.assertEqual(current["events"][:-1], prior_events)
        self.assertEqual(current["schema_version"], 1)

    def test_b31_snapshot_recovery_uses_exact_snapshot_and_preserves_b14_history(self):
        snapshot, b31 = self.prepare_b31_snapshot_attempt()
        before = self.coordinator.read()
        original_snapshot = copy.deepcopy(snapshot)
        b14_attempts = copy.deepcopy([item for item in before["attempts"] if item["task_id"] == "B14"])
        b14_ids = {item["id"] for item in b14_attempts}
        b14_events = copy.deepcopy([item for item in before["events"] if item.get("attempt_id") in b14_ids])
        original_recoveries = copy.deepcopy(before.get("snapshot_recoveries", []))
        self.write("AGENTS.md", "Restored task-owned commit rules\n")

        proposal = self.propose_snapshot_recovery(b31, snapshot["id"])["proposal"]
        state = self.coordinator.read()
        self.assertEqual(proposal["task_id"], "B31")
        self.assertEqual(proposal["target_attempt_id"], b31["id"])
        self.assertEqual(proposal["iteration"], b31["iteration"])
        self.assertEqual(proposal["snapshot_id"], B31_SNAPSHOT_ID)
        self.assertEqual(proposal["manifest"], self.coordinator.exact_manifest(snapshot["files"]))
        self.assertNotEqual(proposal["manifest"], snapshot["manifest"])
        self.assertEqual(proposal["source_provenance"], self.coordinator.snapshot_recovery_source(snapshot))
        self.assertEqual(proposal["authorization"]["source"], COORD.SNAPSHOT_RECOVERY_AUTH_SOURCE)
        self.assertTrue(proposal["authorization"]["authorized"])
        self.assertEqual(proposal["authorization"]["current_chat_run_id"], self.root_run)
        self.assertEqual(proposal["authorization"]["user_authorization"], SNAPSHOT_RECOVERY_AUTHORIZATION)
        self.assertEqual(state["snapshots"], before["snapshots"], "B31 must not rewrite source snapshots")
        self.assertEqual([item for item in state["attempts"] if item["task_id"] == "B14"], b14_attempts)
        self.assertEqual([item for item in state["events"] if item.get("attempt_id") in b14_ids], b14_events)
        self.assertEqual([item for item in state.get("snapshot_recoveries", [])
                          if item["task_id"] == "B14"], original_recoveries)
        self.assertEqual(self.coordinator.recovery_candidate_proposals(state, b31), [
            self.coordinator.snapshot_recovery_candidate_summary(proposal, b31, b31["iteration"])])

        submitted = self.submit(b31)["attempt"]
        summary = submitted["candidate"]["snapshot_recoveries"][0]
        self.assertEqual(submitted["candidate"]["task_id"], "B31")
        self.assertEqual(summary["proposal_id"], proposal["id"])
        self.assertEqual(summary["target_attempt_id"], b31["id"])
        self.assertEqual(summary["iteration"], b31["iteration"])
        self.assertEqual(summary["snapshot_id"], B31_SNAPSHOT_ID)
        self.assertEqual(summary["manifest"], proposal["manifest"])
        self.assertEqual(summary["manifest_sha256"], COORD.digest(proposal["manifest"]))
        self.assertEqual(summary["source_provenance"], proposal["source_provenance"])
        self.assertEqual(summary["authorization"], proposal["authorization"])
        reviewed = self.review(self.reviewer(submitted))["attempt"]
        self.assertEqual(reviewed["review"]["snapshot_recoveries"], submitted["candidate"]["snapshot_recoveries"])
        accepted = self.call("accept", attempt=b31["id"], reason="Fresh independent GO for exact B31 snapshot")["attempt"]
        self.assertEqual(accepted["status"], "accepted")
        accepted_state = self.coordinator.read()
        self.assertEqual(accepted_state["snapshots"], [original_snapshot])
        accept_event = next(item for item in reversed(accepted_state["events"])
                            if item.get("action") == "accept" and item.get("attempt_id") == b31["id"])
        self.assertEqual(accept_event["snapshot_recovery_acceptances"][0]["proposal_sha256"], COORD.digest(proposal))
        self.assertEqual(accept_event["snapshot_recovery_acceptances"][0]["manifest_sha256"],
                         COORD.digest(proposal["manifest"]))
        self.assertIsNotNone(self.coordinator.accepted_snapshot_recovery(accepted_state, proposal))
        self.assertEqual([item for item in accepted_state["attempts"] if item["task_id"] == "B14"], b14_attempts)
        self.assertEqual([item for item in accepted_state["events"] if item.get("attempt_id") in b14_ids], b14_events)

    def test_b31_snapshot_recovery_rejects_other_tasks_and_snapshots(self):
        snapshot, b31 = self.prepare_b31_snapshot_attempt()
        other_snapshot, _ = self.record_snapshot(["other.py"])
        b12 = self.worker(self.reserve("B12", ["b12.py"]))
        b03 = self.worker(self.reserve("B03", ["b03.py"]))
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", b12, snapshot["id"])
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", b03, snapshot["id"])
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", b31, other_snapshot["id"])
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", b31, str(uuid.uuid4()))

    def test_b31_snapshot_recovery_rejects_cross_task_tampering(self):
        snapshot, _, proposal = self.prepare_b31_snapshot_recovery()
        valid = self.coordinator.read()
        tampered = copy.deepcopy(valid)
        b14 = next(item for item in tampered["attempts"] if item["task_id"] == "B14")
        forged = next(item for item in tampered["snapshot_recoveries"] if item["id"] == proposal["id"])
        forged["task_id"] = "B14"
        forged["target_attempt_id"] = b14["id"]
        event = next(item for item in tampered["events"]
                     if item.get("snapshot_recovery_proposal_id") == proposal["id"])
        event["task_id"] = "B14"
        event["attempt_id"] = b14["id"]
        event["snapshot_recovery_proposal"] = copy.deepcopy(forged)
        self.assertEqual(snapshot["id"], B31_SNAPSHOT_ID)
        self.coordinator.write(tampered)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")

    def test_b31_snapshot_recovery_rejects_amendment_continuation_and_any_drift(self):
        snapshot, b31, _ = self.prepare_b31_snapshot_recovery()
        self.refused_snapshot_recovery_amendment("snapshot_recovery_amendment_unsupported", b31, snapshot["id"])
        self.refused_snapshot_recovery_continuation(
            "snapshot_recovery_continuation_unsupported", b31, snapshot["id"])

        self.write("AGENTS.md", "Drift after the B31 proposal\n")
        self.refused("stale_snapshot_recovery", "submit", attempt=b31["id"],
                     actor=b31["worker"]["target"], run_id=b31["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B31", "attempt_id": b31["id"],
                                              "summary": "Drift must block B31 Submit",
                                              "checks": [{"name": "fixture", "status": "passed",
                                                          "evidence": "not reached"}]}))
        self.write("AGENTS.md", "Restored task-owned commit rules\n")
        submitted = self.submit(b31)["attempt"]
        reviewed = self.review(self.reviewer(submitted))["attempt"]
        self.write("components.json", "{\"drift\":true}\n")
        self.refused("stale_candidate", "accept", attempt=b31["id"],
                     reason="Drift must block B31 Accept")
        self.assertEqual(self.coordinator.read()["attempts"][-1]["status"], "awaiting_review")

    def test_snapshot_recovery_is_pending_only_for_claimed_b14_then_reviewed_accept_unlocks_exact_manifest(self):
        _, snapshot, b14, proposal = self.prepare_snapshot_recovery()
        original_snapshots = copy.deepcopy(self.coordinator.read()["snapshots"])
        self.assertEqual(proposal["snapshot_id"], snapshot["id"])
        self.assertEqual(proposal["target_attempt_id"], b14["id"])
        self.assertEqual(proposal["task_id"], "B14")
        self.assertEqual(proposal["manifest"], self.coordinator.exact_manifest(snapshot["files"]))
        self.assertEqual(proposal["source_provenance"], self.coordinator.snapshot_recovery_source(snapshot))
        self.assertEqual(proposal["authorization"]["source"], COORD.SNAPSHOT_RECOVERY_AUTH_SOURCE)
        self.assertTrue(proposal["authorization"]["authorized"])
        self.assertEqual(proposal["authorization"]["user_authorization"], SNAPSHOT_RECOVERY_AUTHORIZATION)

        peer = self.worker(self.reserve("B03", ["a.py"]))
        self.write("a.py", "peer outside pending recovery\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Pending must not unlock peer",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        exact_c = (self.root / "c.py").read_bytes()
        self.write("c.py", "snapshot drift before B14 submit\n")
        self.refused("snapshot_drift", "submit", attempt=b14["id"], actor=b14["worker"]["target"],
                     run_id=b14["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B14", "attempt_id": b14["id"], "summary": "Stale proposal cannot submit",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        (self.root / "c.py").write_bytes(exact_c)

        submitted = self.submit(b14)
        candidate = submitted["attempt"]["candidate"]
        self.assertEqual(len(candidate["snapshot_recoveries"]), 1)
        self.assertEqual(candidate["snapshot_recoveries"][0]["proposal_id"], proposal["id"])
        self.assertEqual(candidate["snapshot_recoveries"][0]["manifest"], proposal["manifest"])
        candidate_digest_input = copy.deepcopy(candidate)
        candidate_digest_input.pop("digest")
        self.assertEqual(candidate["digest"], COORD.digest(candidate_digest_input))
        bound = self.reviewer(submitted["attempt"])
        self.review(bound, "GO")

        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "GO alone must not unlock",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        self.write("c.py", "snapshot drift after B14 GO\n")
        self.refused("stale_snapshot_recovery", "accept", attempt=b14["id"],
                     reason="Exact recovery manifest must still match at Accept")
        (self.root / "c.py").write_bytes(exact_c)
        accepted_b14 = self.call("accept", attempt=b14["id"], reason="Independent GO and explicit orchestrator Accept")["attempt"]
        state = self.coordinator.read()
        self.assertEqual(state["snapshots"], original_snapshots, "Recovery must never rewrite the original snapshot")
        b14_in_state = next(item for item in state["attempts"] if item["id"] == b14["id"])
        self.assertEqual(b14_in_state["candidate"]["digest"], candidate["digest"])
        acceptance_event = next(event for event in reversed(state["events"])
                                if event.get("action") == "accept" and event.get("attempt_id") == b14["id"])
        self.assertEqual(len(acceptance_event["snapshot_recovery_acceptances"]), 1)
        acceptance = acceptance_event["snapshot_recovery_acceptances"][0]
        self.assertEqual(acceptance["candidate_digest"], candidate["digest"])
        self.assertEqual(acceptance["iteration"], candidate["iteration"])
        self.assertEqual(acceptance["review_result"], "GO")
        self.assertEqual(acceptance["reviewer"], accepted_b14["review"]["reviewer"])
        self.assertEqual(self.coordinator.accepted_snapshot_recovery(state, proposal), acceptance)

        exact_bytes = (self.root / "b.py").read_bytes()
        self.write("b.py", "later byte drift\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Byte drift must close recovery",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        (self.root / "b.py").write_bytes(exact_bytes)

        exact_manifest = self.coordinator.exact_manifest
        def mode_drift(files):
            result = exact_manifest(files)
            if files == snapshot["files"]:
                result[0]["mode"] = "100755" if result[0]["mode"] == "100644" else "100644"
            return result
        with mock.patch.object(self.coordinator, "exact_manifest", side_effect=mode_drift):
            self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                         run_id=peer["worker"]["run_id"], evidence=self.json_file({
                             "task_id": "B03", "attempt_id": peer["id"], "summary": "Mode drift must close recovery",
                             "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        (self.root / "b.py").unlink()
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Deletion must close recovery",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        (self.root / "b.py").write_bytes(exact_bytes)

        peer_submitted = self.submit(peer)
        self.assertEqual(peer_submitted["attempt"]["status"], "awaiting_review")
        peer_review = self.reviewer(peer_submitted["attempt"])
        self.review(peer_review, "GO")
        self.write("b.py", "post-accept drift before accept\n")
        self.refused("snapshot_drift", "accept", attempt=peer["id"], reason="Drift must block Accept")
        (self.root / "b.py").write_bytes(exact_bytes)
        self.call("accept", attempt=peer["id"], reason="Exact accepted recovery manifest restored")

    def test_snapshot_recovery_rejects_wrong_snapshot_attempt_task_and_authorization(self):
        self.gate()
        accepted = self.complete("B01", ["b.py"])
        snapshot, _ = self.record_snapshot(["b.py"])
        other_snapshot, _ = self.record_snapshot(["c.py"])
        b14 = self.worker(self.reserve("B14", ["b.py"], adopt_accepted=[accepted["id"]]))
        self.write("b.py", "recovered b14 state\n")

        self.refused_snapshot_recovery("unknown_attempt", str(uuid.uuid4()), snapshot["id"])
        self.refused_snapshot_recovery("unknown_snapshot", b14, str(uuid.uuid4()))
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", b14, other_snapshot["id"])
        self.refused_snapshot_recovery("invalid_snapshot_recovery_authorization", b14, snapshot["id"], authorization="")
        self.refused_snapshot_recovery("current_run_mismatch", b14, snapshot["id"], environment_run=str(uuid.uuid4()))

        wrong_task = self.worker(self.reserve("B03", ["a.py"]))
        self.refused_snapshot_recovery("invalid_snapshot_recovery_target", wrong_task, snapshot["id"])
        proposal = self.propose_snapshot_recovery(b14, snapshot["id"])["proposal"]
        self.assertEqual(proposal["snapshot_id"], snapshot["id"])
        self.assertNotEqual(proposal["snapshot_id"], other_snapshot["id"])
        self.assertEqual(self.coordinator.read()["snapshots"], [snapshot, other_snapshot])

    def test_snapshot_recovery_rejects_tampered_append_only_proposal(self):
        _, _, _, proposal = self.prepare_snapshot_recovery()
        state = self.coordinator.read()
        state["snapshot_recoveries"][0]["manifest"][0]["sha256"] = "f" * 64
        self.coordinator.write(state)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")
        self.assertTrue(any(event.get("snapshot_recovery_proposal_id") == proposal["id"]
                            for event in state["events"]))

    def test_snapshot_recovery_no_go_and_closed_attempt_never_unlock_peer(self):
        _, snapshot, b14, proposal = self.prepare_snapshot_recovery()
        submitted = self.submit(b14)
        bound = self.reviewer(submitted["attempt"])
        no_go = self.review(bound, "NO_GO")["attempt"]
        self.assertEqual(no_go["status"], "changes_requested")
        self.assertIsNone(self.coordinator.accepted_snapshot_recovery(self.coordinator.read(), proposal))
        reusable = self.coordinator.recovery_candidate_proposals(self.coordinator.read(), no_go)
        self.assertEqual(len(reusable), 1)
        self.assertEqual(reusable[0]["proposal_id"], proposal["id"])
        self.assertEqual(reusable[0]["proposal_iteration"], proposal["iteration"])
        self.assertEqual(reusable[0]["iteration"], no_go["iteration"])
        self.assertIsNone(self.coordinator.outside_changes(self.coordinator.read(), no_go))
        self.refused_snapshot_recovery("duplicate_snapshot_recovery", b14, snapshot["id"])

        peer = self.worker(self.reserve("B03", ["a.py"]))
        self.write("a.py", "peer after NO_GO\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "NO_GO cannot unlock peer",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        quiescence = {"tool": "collaboration.list_agents", "target": b14["worker"]["target"],
                      "run_id": b14["worker"]["run_id"], "observed_at": COORD.iso(),
                      "status": "completed", "writers_stopped": True, "evidence": "Fixture worker stopped"}
        released = self.call("cancel", attempt=b14["id"], reason="Close proposal target", evidence=self.json_file(quiescence))["attempt"]
        self.assertEqual(released["status"], "cancelled")
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.outside_changes(self.coordinator.read(), released)
        self.assertEqual(caught.exception.code, "closed_attempt")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Closed proposal cannot unlock peer",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        self.assertIsNone(self.coordinator.accepted_snapshot_recovery(self.coordinator.read(), proposal))

    def test_snapshot_recovery_no_go_repair_resubmits_same_proposal_for_fresh_go_and_accept(self):
        self.gate()
        accepted = self.complete("B01", ["b.py"])
        snapshot, _ = self.record_snapshot(["b.py", "c.py"])
        b14 = self.worker(self.reserve("B14", ["b.py", "repair.py"], adopt_accepted=[accepted["id"]]))
        self.write("b.py", "reviewed B14 recovery state\n")
        proposal = self.propose_snapshot_recovery(b14, snapshot["id"])["proposal"]
        original_snapshots = copy.deepcopy(self.coordinator.read()["snapshots"])

        first = self.submit(b14)
        first_candidate = first["attempt"]["candidate"]
        first_reviewer = self.reviewer(first["attempt"])
        changes_requested = self.review(first_reviewer, "NO_GO")["attempt"]
        self.assertEqual(changes_requested["iteration"], first_candidate["iteration"] + 1)
        self.assertIsNone(self.coordinator.accepted_snapshot_recovery(self.coordinator.read(), proposal))
        self.refused_snapshot_recovery("duplicate_snapshot_recovery", b14, snapshot["id"])

        # Repair a B14-owned path outside the immutable source snapshot. The
        # original proposal remains usable only because its exact 13-path
        # manifest has not changed.
        self.write("repair.py", "reviewed repair\n")
        second = self.submit(changes_requested)
        second_candidate = second["attempt"]["candidate"]
        self.assertNotEqual(second_candidate["digest"], first_candidate["digest"])
        self.assertEqual(second_candidate["iteration"], changes_requested["iteration"])
        self.assertEqual(len(second_candidate["snapshot_recoveries"]), 1)
        recovery = second_candidate["snapshot_recoveries"][0]
        self.assertEqual(recovery["proposal_id"], proposal["id"])
        self.assertEqual(recovery["proposal_iteration"], proposal["iteration"])
        self.assertEqual(recovery["iteration"], second_candidate["iteration"])
        self.assertEqual(recovery["manifest"], proposal["manifest"])

        second_reviewer = self.reviewer(second["attempt"])
        self.review(second_reviewer, "GO")
        peer = self.worker(self.reserve("B03", ["peer.py"]))
        self.write("peer.py", "peer change while only B14 has pending access\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Fresh GO alone cannot unlock",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        accepted_b14 = self.call("accept", attempt=b14["id"],
                                 reason="Fresh independent GO and separate orchestrator Accept")['attempt']
        state = self.coordinator.read()
        self.assertEqual(state["snapshots"], original_snapshots)
        receipt = next(event for event in reversed(state["events"])
                       if event.get("action") == "accept" and event.get("attempt_id") == b14["id"])
        acceptance = receipt["snapshot_recovery_acceptances"][0]
        self.assertEqual(acceptance["proposal_id"], proposal["id"])
        self.assertEqual(acceptance["iteration"], second_candidate["iteration"])
        self.assertEqual(acceptance["candidate_digest"], second_candidate["digest"])
        self.assertEqual(acceptance["reviewer"], accepted_b14["review"]["reviewer"])
        self.assertEqual(self.coordinator.accepted_snapshot_recovery(state, proposal), acceptance)

        peer_submitted = self.submit(peer)
        self.assertEqual(peer_submitted["attempt"]["status"], "awaiting_review")

    def test_snapshot_recovery_continuation_chains_no_go_retries_with_current_workspace_bundle(self):
        _, snapshot, b14, proposal, amendment = self.prepare_workspace_delta_recovery()
        self.write("workspace-origin.txt", "origin delta\n")
        workspace_origin = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        original_snapshots = copy.deepcopy(self.coordinator.read()["snapshots"])
        original_proposal = copy.deepcopy(proposal)
        original_amendment = copy.deepcopy(amendment)

        # A pending proposal and continuation remain private to the exact claimed B14 attempt.
        peer = self.worker(self.reserve("B03", ["peer.py"]))
        first = self.submit(b14)["attempt"]
        first_candidate = first["candidate"]
        changes_requested = self.review(self.reviewer(first), "NO_GO")["attempt"]
        self.assertEqual(changes_requested["iteration"], first_candidate["iteration"] + 1)
        self.refused("snapshot_drift", "submit", attempt=peer["id"],
                     actor=peer["worker"]["target"], run_id=peer["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B03", "attempt_id": peer["id"],
                                              "summary": "Pending B14 continuation cannot unlock a peer",
                                              "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        self.write("c.py", "freshly reviewed continuation bytes\n")
        self.write("workspace-retry-1.txt", "new external delta after NO_GO\n")
        current_manifest = self.coordinator.exact_manifest(snapshot["files"])
        expected_manifest_sha256 = COORD.digest(current_manifest)
        self.refused_snapshot_recovery_continuation(
            "invalid_snapshot_recovery_continuation_authorization", changes_requested, snapshot["id"],
            authorization="missing the direct current user reply")
        self.refused_snapshot_recovery_continuation(
            "wrong_role", changes_requested, snapshot["id"], actor="/root/other")
        self.refused_snapshot_recovery_continuation(
            "current_run_mismatch", changes_requested, snapshot["id"],
            run_id=self.root_run, environment_run=str(uuid.uuid4()))
        self.refused_snapshot_recovery_continuation(
            "snapshot_recovery_continuation_manifest_changed", changes_requested, snapshot["id"],
            manifest_sha256="0" * 64)
        with mock.patch.object(self.coordinator, "index_digest", return_value="0" * 64):
            self.refused_snapshot_recovery_continuation(
                "snapshot_recovery_continuation_pins_changed", changes_requested, snapshot["id"],
                manifest_sha256=expected_manifest_sha256)
        original_git = self.coordinator.git
        with mock.patch.object(self.coordinator, "git", side_effect=lambda *args:
                               b"0" * 40 if args == ("rev-parse", "HEAD") else original_git(*args)):
            self.refused_snapshot_recovery_continuation(
                "snapshot_recovery_continuation_pins_changed", changes_requested, snapshot["id"],
                manifest_sha256=expected_manifest_sha256)

        continuation_2 = self.continue_snapshot_recovery(
            changes_requested, snapshot["id"], manifest_sha256=expected_manifest_sha256)["continuation"]
        self.assertEqual(continuation_2["iteration"], changes_requested["iteration"])
        self.assertEqual(continuation_2["proposal_id"], proposal["id"])
        self.assertEqual(continuation_2["manifest"], current_manifest)
        self.assertEqual(continuation_2["manifest_sha256"], expected_manifest_sha256)
        self.assertEqual(continuation_2["previous_entry_id"], amendment["id"])
        self.assertEqual(continuation_2["previous_entry_sha256"], COORD.digest(amendment))
        self.assertEqual(continuation_2["workspace_delta_recovery_id"], workspace_origin["id"])
        self.assertEqual(continuation_2["workspace_delta_recovery_sha256"], COORD.digest(workspace_origin))
        state_after_continuation = self.coordinator.read()
        self.assertEqual(state_after_continuation["snapshots"], original_snapshots)
        self.assertEqual(state_after_continuation["snapshot_recoveries"], [original_proposal])
        self.assertEqual(state_after_continuation["snapshot_recovery_amendments"], [original_amendment])
        self.assertEqual(state_after_continuation["workspace_delta_recoveries"], [workspace_origin])
        self.assertEqual(len(state_after_continuation["snapshot_recovery_continuations"]), 1)
        self.refused_snapshot_recovery_continuation(
            "duplicate_snapshot_recovery_continuation", changes_requested, snapshot["id"],
            manifest_sha256=expected_manifest_sha256)

        # Snapshot drift after recording closes Submit until a fresh continuation exists.
        self.write("c.py", "unrecorded second snapshot drift\n")
        self.refused("snapshot_drift", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"],
                                              "summary": "Continuation manifest must stay exact",
                                              "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        self.write("c.py", "freshly reviewed continuation bytes\n")

        workspace_amendment_2 = self.amend_workspace_delta_recovery(changes_requested)[
            "workspace_delta_recovery_amendment"]
        second = self.submit(changes_requested)["attempt"]
        second_candidate = second["candidate"]
        self.assertEqual(second_candidate["workspace_delta_recovery"]["chain_head_id"], workspace_amendment_2["id"])
        self.assertEqual(second_candidate["workspace_delta_recovery"]["manifest"], workspace_amendment_2["manifest"])
        second_summary = second_candidate["snapshot_recoveries"][0]
        self.assertEqual(second_summary["continuation_id"], continuation_2["id"])
        self.assertEqual(second_summary["continuation_sha256"], COORD.digest(continuation_2))
        self.assertEqual(second_summary["continuation_manifest_sha256"], expected_manifest_sha256)
        self.assertEqual(second_summary["workspace_delta_recovery_id"], workspace_origin["id"])

        # A second NO_GO may re-pin the unchanged exact snapshot manifest for the next iteration.
        second_review = self.review(self.reviewer(second), "NO_GO")["attempt"]
        self.assertEqual(second_review["iteration"], second_candidate["iteration"] + 1)
        self.write("workspace-retry-2.txt", "new external delta after second NO_GO\n")
        continuation_3 = self.continue_snapshot_recovery(
            second_review, snapshot["id"], manifest_sha256=expected_manifest_sha256)["continuation"]
        self.assertEqual(continuation_3["sequence"], 2)
        self.assertEqual(continuation_3["previous_entry_id"], continuation_2["id"])
        self.assertEqual(continuation_3["previous_entry_sha256"], COORD.digest(continuation_2))
        self.assertEqual(continuation_3["manifest"], continuation_2["manifest"])

        workspace_amendment_3 = self.amend_workspace_delta_recovery(second_review)[
            "workspace_delta_recovery_amendment"]
        third = self.submit(second_review)["attempt"]
        third_candidate = third["candidate"]
        third_summary = third_candidate["snapshot_recoveries"][0]
        self.assertEqual(third_summary["continuation_id"], continuation_3["id"])
        self.assertEqual(third_summary["iteration"], third_candidate["iteration"])
        self.assertEqual(third_candidate["workspace_delta_recovery"]["chain_head_id"], workspace_amendment_3["id"])

        fresh_review = self.review(self.reviewer(third), "GO")["attempt"]["review"]
        self.assertEqual(fresh_review["snapshot_recoveries"], third_candidate["snapshot_recoveries"])
        accepted = self.call("accept", attempt=b14["id"], reason="Fresh GO accepts the paired current recovery chain")
        state = self.coordinator.read()
        self.assertEqual(accepted["attempt"]["status"], "accepted")
        accept_event = next(item for item in reversed(state["events"])
                            if item.get("action") == "accept" and item.get("attempt_id") == b14["id"])
        self.assertEqual(len(accept_event["snapshot_recovery_acceptances"]), 1)
        self.assertEqual(len(accept_event["workspace_delta_recovery_acceptances"]), 1)
        snapshot_approval = accept_event["snapshot_recovery_acceptances"][0]
        workspace_approval = accept_event["workspace_delta_recovery_acceptances"][0]
        self.assertEqual(snapshot_approval["continuation_id"], continuation_3["id"])
        self.assertEqual(snapshot_approval["continuation_sha256"], COORD.digest(continuation_3))
        self.assertEqual(snapshot_approval["workspace_delta_recovery_id"], workspace_origin["id"])
        self.assertEqual(workspace_approval["chain_head_id"], workspace_amendment_3["id"])
        self.assertIsNotNone(self.coordinator.accepted_snapshot_recovery(state, proposal))
        self.assertIsNotNone(self.coordinator.accepted_workspace_delta_recovery(state, workspace_origin))

        # Removing either receipt breaks both unlocks; chain history and source snapshot stay immutable.
        for key in ("snapshot_recovery_acceptances", "workspace_delta_recovery_acceptances"):
            tampered = copy.deepcopy(state)
            receipt = next(item for item in reversed(tampered["events"])
                           if item.get("action") == "accept" and item.get("attempt_id") == b14["id"])
            receipt.pop(key)
            self.coordinator.write(tampered)
            current = self.coordinator.read()
            proposal_now = next(item for item in current["snapshot_recoveries"] if item["id"] == proposal["id"])
            record_now = self.coordinator.workspace_delta_recovery_for_attempt(
                current, next(item for item in current["attempts"] if item["id"] == b14["id"]))
            self.assertIsNone(self.coordinator.accepted_snapshot_recovery(current, proposal_now))
            self.assertIsNone(self.coordinator.accepted_workspace_delta_recovery(current, record_now))
        self.coordinator.write(state)

    def test_snapshot_recovery_continuation_chain_rejects_tamper_and_stale_or_late_action(self):
        _, snapshot, b14, proposal, amendment = self.prepare_workspace_delta_recovery()
        self.write("workspace-origin.txt", "origin delta\n")
        workspace_origin = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        first = self.submit(b14)["attempt"]
        changes_requested = self.review(self.reviewer(first), "NO_GO")["attempt"]
        self.write("c.py", "snapshot drift for next iteration\n")
        expected = COORD.digest(self.coordinator.exact_manifest(snapshot["files"]))

        # Plan drift, an unknown snapshot, and a forged predecessor fail before append.
        self.plan["tasks"][-1]["description"] = "changed after review"
        self.save_plan()
        self.refused_snapshot_recovery_continuation("plan_drift", changes_requested, snapshot["id"],
                                                    manifest_sha256=expected)
        self.plan["tasks"][-1].pop("description")
        self.save_plan()
        wrong_snapshot = str(uuid.uuid4())
        before = self.coordinator.state_path.read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.root_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("continue-snapshot-recovery", attempt=b14["id"], snapshot_id=wrong_snapshot,
                          manifest_sha256=expected,
                          user_authorization=SNAPSHOT_RECOVERY_CONTINUATION_AUTHORIZATION,
                          reason="Wrong snapshot must be rejected")
        self.assertEqual(caught.exception.code, "unknown_snapshot_recovery_proposal")
        self.assertEqual(before, self.coordinator.state_path.read_bytes())

        continuation = self.continue_snapshot_recovery(changes_requested, snapshot["id"],
                                                       manifest_sha256=expected)["continuation"]
        valid = self.coordinator.read()
        tampered = copy.deepcopy(valid)
        tampered_entry = tampered["snapshot_recovery_continuations"][0]
        tampered_entry["previous_entry_sha256"] = "0" * 64
        tampered_event = next(item for item in tampered["events"]
                              if item.get("action") == "continue-snapshot-recovery"
                              and item.get("snapshot_recovery_continuation_id") == continuation["id"])
        tampered_event["snapshot_recovery_continuation"] = copy.deepcopy(tampered_entry)
        self.coordinator.write(tampered)
        with self.assertRaises(COORD.Refusal) as caught_tamper:
            self.coordinator.read()
        self.assertEqual(caught_tamper.exception.code, "corrupt_state")
        self.coordinator.write(valid)

        # A later candidate cannot be amended retroactively; unrelated snapshot drift still blocks Submit.
        self.write("c.py", "unapproved later drift\n")
        self.write("workspace-later-drift.txt", "fresh workspace delta\n")
        self.refused("snapshot_drift", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"],
                                              "summary": "Stale continuation is closed",
                                              "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        self.write("c.py", "snapshot drift for next iteration\n")
        self.amend_workspace_delta_recovery(changes_requested)
        candidate = self.submit(changes_requested)["attempt"]
        self.refused_snapshot_recovery_continuation("invalid_snapshot_recovery_continuation_target",
                                                    candidate, snapshot["id"])
        self.assertEqual(self.coordinator.read()["snapshots"], [snapshot])
        self.assertEqual(self.coordinator.read()["snapshot_recoveries"], [proposal])
        self.assertEqual(self.coordinator.read()["snapshot_recovery_amendments"], [amendment])
        self.assertEqual(self.coordinator.read()["workspace_delta_recoveries"], [workspace_origin])

    def test_workspace_delta_recovery_records_exact_manifest_and_accepts_paired_receipts(self):
        _, snapshot, b14, proposal, amendment = self.prepare_workspace_delta_recovery()
        other = self.worker(self.reserve("B03", ["c.py"]))
        self.write("preserved-unrelated.txt", "outside the B14 scope\n")
        before = self.coordinator.read()
        original_snapshot = copy.deepcopy(before["snapshots"])
        original_proposal = copy.deepcopy(next(item for item in before["snapshot_recoveries"]
                                                if item["id"] == proposal["id"]))
        original_amendment = copy.deepcopy(next(item for item in before["snapshot_recovery_amendments"]
                                                 if item["id"] == amendment["id"]))
        snap_bytes = self.coordinator.exact_manifest(snapshot["files"])

        self.refused_workspace_delta_recovery("wrong_role", b14, actor="/root/other")
        self.refused_workspace_delta_recovery("wrong_role", b14, run_id=str(uuid.uuid4()))
        self.refused_workspace_delta_recovery("current_run_mismatch", b14, environment_run=str(uuid.uuid4()))
        self.refused_workspace_delta_recovery("invalid_workspace_delta_recovery_authorization", b14,
                                              authorization="stale or incomplete authorization")
        self.refused_workspace_delta_recovery("invalid_workspace_delta_recovery_target", other,
                                              manifest_sha256="0" * 64)

        recorded = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        state = self.coordinator.read()
        preserved_bytes = (self.root / "preserved-unrelated.txt").read_bytes()
        self.assertEqual(recorded["manifest"], [{"path": "preserved-unrelated.txt", "mode": "100644",
                                                   "sha256": hashlib.sha256(preserved_bytes).hexdigest(),
                                                   "size": len(preserved_bytes)}])
        self.assertEqual(recorded["manifest_sha256"], COORD.digest(recorded["manifest"]))
        self.assertEqual(recorded["authorization"]["user_authorization"], WORKSPACE_DELTA_RECOVERY_AUTHORIZATION)
        self.assertEqual(state["snapshots"], original_snapshot)
        self.assertEqual(next(item for item in state["snapshot_recoveries"] if item["id"] == proposal["id"]), original_proposal)
        self.assertEqual(next(item for item in state["snapshot_recovery_amendments"] if item["id"] == amendment["id"]), original_amendment)
        self.assertEqual(self.coordinator.exact_manifest(snapshot["files"]), snap_bytes)
        self.refused_workspace_delta_recovery("duplicate_workspace_delta_recovery", b14)

        submitted = self.submit(b14)
        self.refused_workspace_delta_recovery("workspace_delta_recovery_late", b14)
        candidate = submitted["attempt"]["candidate"]
        self.assertEqual(candidate["workspace_delta_recovery"],
                         self.coordinator.workspace_delta_recovery_candidate_summary(recorded, b14["iteration"]))
        self.review(self.reviewer(submitted["attempt"]), "GO")
        accepted_result = self.call("accept", attempt=b14["id"], reason="Reviewed paired recovery")
        self.assertEqual(len(accepted_result["snapshot_recovery_acceptances"]), 1)
        self.assertEqual(len(accepted_result["workspace_delta_recovery_acceptances"]), 1)
        state = self.coordinator.read()
        event = next(item for item in reversed(state["events"])
                     if item.get("action") == "accept" and item.get("attempt_id") == b14["id"])
        self.assertEqual(len(event["snapshot_recovery_acceptances"]), 1)
        self.assertEqual(len(event["workspace_delta_recovery_acceptances"]), 1)
        self.assertIsNotNone(self.coordinator.accepted_snapshot_recovery(state, proposal))
        self.assertIsNotNone(self.coordinator.accepted_workspace_delta_recovery(state, recorded))

        # A different live claim can use the accepted workspace delta only while
        # the exact bytes and paired snapshot acceptance remain current.
        peer = self.submit(other)
        self.assertEqual(peer["attempt"]["status"], "awaiting_review")
        self.review(self.reviewer(peer["attempt"]), "GO")
        self.write("preserved-unrelated.txt", "additional drift after reviewed Accept\n")
        self.refused("out_of_scope_change", "accept", attempt=other["id"],
                     reason="Only the accepted exact workspace manifest is preserved")
        self.refused_workspace_delta_recovery("closed_attempt", b14)

        # Either missing receipt invalidates both sides of the acceptance pair.
        for removed_key in ("snapshot_recovery_acceptances", "workspace_delta_recovery_acceptances"):
            tampered = copy.deepcopy(state)
            tampered_event = next(item for item in reversed(tampered["events"])
                                  if item.get("action") == "accept" and item.get("attempt_id") == b14["id"])
            tampered_event.pop(removed_key)
            self.coordinator.write(tampered)
            current = self.coordinator.read()
            record = self.coordinator.workspace_delta_recovery_for_attempt(
                current, next(item for item in current["attempts"] if item["id"] == b14["id"]))
            proposal_now = next(item for item in current["snapshot_recoveries"] if item["id"] == proposal["id"])
            self.assertIsNone(self.coordinator.accepted_snapshot_recovery(current, proposal_now))
            self.assertIsNone(self.coordinator.accepted_workspace_delta_recovery(current, record))

    def test_workspace_delta_recovery_pins_preobserved_digest_and_fails_on_later_drift(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("delta-a.txt", "first observed delta\n")
        (self.root / "a.py").unlink()
        observed = self.coordinator.read()
        observed_attempt = next(item for item in observed["attempts"] if item["id"] == b14["id"])
        preobserved_digest = COORD.digest(self.coordinator.unexplained_workspace_delta(observed, observed_attempt))
        self.write("unexpected-sixteenth.txt", "must not be swept in\n")
        self.refused_workspace_delta_recovery("workspace_delta_manifest_changed", b14,
                                              manifest_sha256=preobserved_digest)
        record = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        original = self.coordinator.read()
        original_a = "first observed delta\n"
        self.assertIn({"path": "a.py", "absent": True}, record["manifest"])

        self.write("delta-a.txt", "changed bytes after record\n")
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "drift",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        self.write("delta-a.txt", original_a)
        (self.root / "delta-a.txt").unlink()
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "deleted delta",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        self.write("delta-a.txt", original_a)
        self.write("later-added.txt", "new drift\n")
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "drift",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        (self.root / "later-added.txt").unlink()
        self.write("a.py", "old a\n")
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "drift",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        (self.root / "a.py").unlink()

        inventory = self.coordinator.inventory()
        inventory["delta-a.txt"]["executable"] = not inventory["delta-a.txt"]["executable"]
        with mock.patch.object(self.coordinator, "inventory", return_value=inventory):
            self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                         actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                         evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "mode drift",
                                                  "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        current = self.coordinator.read()
        self.assertEqual(self.coordinator.workspace_delta_recovery_for_attempt(current, b14), record)
        self.assertEqual(original["workspace_delta_recoveries"], [record])

    def test_workspace_delta_recovery_no_go_retry_is_same_record_and_fresh_accept(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("reviewed-external.txt", "retain across repair\n")
        record = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        first = self.submit(b14)
        first_candidate = first["attempt"]["candidate"]
        repaired = self.review(self.reviewer(first["attempt"]), "NO_GO")["attempt"]
        self.refused_workspace_delta_recovery("workspace_delta_recovery_late", b14)
        second = self.submit(repaired)
        second_candidate = second["attempt"]["candidate"]
        self.assertEqual(second_candidate["workspace_delta_recovery"]["recovery_id"], record["id"])
        self.assertEqual(second_candidate["workspace_delta_recovery"]["origin_iteration"], first_candidate["iteration"])
        self.assertEqual(second_candidate["workspace_delta_recovery"]["iteration"], repaired["iteration"])
        self.review(self.reviewer(second["attempt"]), "GO")
        accepted = self.call("accept", attempt=b14["id"], reason="Fresh GO after same-claim repair")
        self.assertEqual(accepted["attempt"]["status"], "accepted")
        self.assertEqual(accepted["snapshot_recovery_acceptances"][0]["iteration"], repaired["iteration"])
        self.assertEqual(accepted["workspace_delta_recovery_acceptances"][0]["candidate_digest"], second_candidate["digest"])

        state = self.coordinator.read()
        corrupted = copy.deepcopy(state)
        attempt = next(item for item in corrupted["attempts"] if item["id"] == b14["id"])
        prior = next(item for item in attempt["submissions"] if item.get("iteration") == first_candidate["iteration"])
        prior["task_hash"] = "0" * 64
        unsigned = copy.deepcopy(prior)
        unsigned.pop("digest", None)
        prior["digest"] = COORD.digest(unsigned)
        self.coordinator.write(corrupted)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")

    def test_workspace_delta_recovery_requires_current_snapshot_and_candidate_binding(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("workspace-delta.txt", "preserve\n")
        self.write("b.py", "accepted B01\n")
        self.refused_workspace_delta_recovery("workspace_delta_recovery_snapshot", b14)
        self.write("b.py", "amended B14 snapshot recovery state\n")
        record = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        saved_state = self.coordinator.read()
        corrupt = copy.deepcopy(saved_state)
        corrupt_record = self.coordinator.workspace_delta_recovery_for_attempt(
            corrupt, next(item for item in corrupt["attempts"] if item["id"] == b14["id"]))
        corrupt_record["manifest_sha256"] = "0" * 64
        self.coordinator.write(corrupt)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")
        self.coordinator.write(saved_state)
        # Returning the source path to its original snapshot bytes removes the
        # amended proposal from current candidate summaries and closes recovery.
        self.write("b.py", "accepted B01\n")
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "snapshot reverted",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        self.write("b.py", "amended B14 snapshot recovery state\n")
        submitted = self.submit(b14)
        state = self.coordinator.read()
        attempt = next(item for item in state["attempts"] if item["id"] == b14["id"])
        for forged_task_id in (True, False):
            forged = copy.deepcopy(state)
            forged_attempt = next(item for item in forged["attempts"] if item["id"] == b14["id"])
            if forged_task_id:
                forged_attempt["candidate"]["task_id"] = "B03"
                unsigned = copy.deepcopy(forged_attempt["candidate"])
                unsigned.pop("digest", None)
                forged_attempt["candidate"]["digest"] = COORD.digest(unsigned)
            else:
                forged_attempt["candidate"]["digest"] = "0" * 64
            self.coordinator.write(forged)
            with self.assertRaises(COORD.Refusal) as caught:
                self.reviewer(submitted["attempt"])
            self.assertEqual(caught.exception.code, "stale_candidate")
        self.assertEqual(record["attempt_id"], attempt["id"])

    def test_snapshot_recovery_amendment_is_one_time_append_only_and_binds_acceptance(self):
        _, snapshot, b14, proposal = self.prepare_snapshot_recovery()
        before = copy.deepcopy(self.coordinator.read())
        proposal_event_before = copy.deepcopy(next(event for event in before["events"]
                                                   if event.get("snapshot_recovery_proposal_id") == proposal["id"]))
        self.write("c.py", "second drift before B14 submission\n")

        amended = self.amend_snapshot_recovery(b14, snapshot["id"])
        amendment = amended["amendment"]
        current_manifest = self.coordinator.exact_manifest(snapshot["files"])
        self.assertEqual(amendment["manifest"], current_manifest)
        self.assertEqual(amendment["manifest_sha256"], COORD.digest(current_manifest))
        self.assertEqual(amendment["proposal_id"], proposal["id"])
        self.assertEqual(amendment["proposal_sha256"], COORD.digest(proposal))
        self.assertEqual(amendment["proposal_manifest_sha256"], COORD.digest(proposal["manifest"]))
        self.assertEqual(amendment["authorization"], {
            "source": COORD.SNAPSHOT_RECOVERY_AUTH_SOURCE, "current_chat_run_id": self.root_run,
            "authorized": True, "user_authorization": SNAPSHOT_RECOVERY_AMENDMENT_AUTHORIZATION})

        state = self.coordinator.read()
        self.assertEqual(state["snapshots"], before["snapshots"], "Amendment must preserve original snapshot evidence")
        self.assertEqual(state["snapshot_recoveries"], before["snapshot_recoveries"], "Original proposal is immutable")
        proposal_event_after = next(event for event in state["events"]
                                    if event.get("snapshot_recovery_proposal_id") == proposal["id"])
        self.assertEqual(proposal_event_after, proposal_event_before, "Original proposal audit event is immutable")
        self.assertEqual(state["snapshot_recovery_amendments"], [amendment])
        audit = state["events"][-1]
        self.assertEqual(audit["action"], "amend-snapshot-recovery")
        self.assertEqual(audit["snapshot_recovery_amendment"], amendment)
        self.assertEqual(audit["task_id"], "B14")
        self.refused_snapshot_recovery_amendment("duplicate_snapshot_recovery_amendment", b14, snapshot["id"])

        peer = self.worker(self.reserve("B03", ["a.py"]))
        self.write("a.py", "peer change while amendment is pending\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Amendment is B14-only while pending",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        submitted = self.submit(amended["attempt"])
        candidate = submitted["attempt"]["candidate"]
        summary = candidate["snapshot_recoveries"][0]
        self.assertEqual(summary["amendment_id"], amendment["id"])
        self.assertEqual(summary["amendment_sha256"], COORD.digest(amendment))
        self.assertEqual(summary["manifest"], current_manifest)
        reviewer = self.reviewer(submitted["attempt"])
        self.review(reviewer, "GO")
        accepted = self.call("accept", attempt=b14["id"], reason="Fresh GO and separate orchestrator Accept")["attempt"]
        state = self.coordinator.read()
        self.assertEqual(state["snapshots"], before["snapshots"])
        self.assertEqual(state["snapshot_recoveries"], before["snapshot_recoveries"])
        receipt = next(event for event in reversed(state["events"])
                       if event.get("action") == "accept" and event.get("attempt_id") == b14["id"])
        acceptance = receipt["snapshot_recovery_acceptances"][0]
        self.assertEqual(acceptance["manifest_sha256"], COORD.digest(current_manifest))
        self.assertEqual(acceptance["amendment_id"], amendment["id"])
        self.assertEqual(acceptance["amendment_sha256"], COORD.digest(amendment))
        self.assertEqual(acceptance["iteration"], candidate["iteration"])
        self.assertEqual(acceptance["candidate_digest"], candidate["digest"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.coordinator.accepted_snapshot_recovery(state, proposal), acceptance)

        exact_c = (self.root / "c.py").read_bytes()
        self.write("c.py", "byte drift after amended Accept\n")
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Byte drift closes amended recovery",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        (self.root / "c.py").write_bytes(exact_c)

        exact_manifest = self.coordinator.exact_manifest
        def amended_mode_drift(files):
            result = exact_manifest(files)
            if files == snapshot["files"]:
                result[0]["mode"] = "100755" if result[0]["mode"] == "100644" else "100644"
            return result
        with mock.patch.object(self.coordinator, "exact_manifest", side_effect=amended_mode_drift):
            self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                         run_id=peer["worker"]["run_id"], evidence=self.json_file({
                             "task_id": "B03", "attempt_id": peer["id"], "summary": "Mode drift closes amended recovery",
                             "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

        (self.root / "c.py").unlink()
        self.refused("snapshot_drift", "submit", attempt=peer["id"], actor=peer["worker"]["target"],
                     run_id=peer["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B03", "attempt_id": peer["id"], "summary": "Deletion closes amended recovery",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))
        (self.root / "c.py").write_bytes(exact_c)

        peer_submitted = self.submit(peer)
        self.assertEqual(peer_submitted["attempt"]["status"], "awaiting_review")

    def test_snapshot_recovery_amendment_stale_manifest_blocks_submit(self):
        _, snapshot, b14, _ = self.prepare_snapshot_recovery()
        self.write("c.py", "second drift before amendment\n")
        amendment = self.amend_snapshot_recovery(b14, snapshot["id"])["amendment"]
        self.assertEqual(amendment["manifest"], self.coordinator.exact_manifest(snapshot["files"]))
        self.write("c.py", "third drift after one-time amendment\n")
        self.refused("snapshot_drift", "submit", attempt=b14["id"], actor=b14["worker"]["target"],
                     run_id=b14["worker"]["run_id"], evidence=self.json_file({
                         "task_id": "B14", "attempt_id": b14["id"], "summary": "Second drift closes amended recovery",
                         "checks": [{"name": "fixture", "status": "passed", "evidence": "not reached"}]}))

    def test_snapshot_recovery_amendment_rejects_wrong_actor_run_and_snapshot(self):
        _, snapshot, b14, _ = self.prepare_snapshot_recovery()
        self.write("c.py", "second drift before amendment\n")
        self.refused_snapshot_recovery_amendment("wrong_role", b14, snapshot["id"], actor="/root/other")
        self.refused_snapshot_recovery_amendment("current_run_mismatch", b14, snapshot["id"],
                                                 environment_run=str(uuid.uuid4()))
        self.refused_snapshot_recovery_amendment("unknown_snapshot_recovery_proposal", b14, str(uuid.uuid4()))
        before = self.coordinator.state_path.read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.root_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("amend-snapshot-recovery", actor="/root", attempt=b14["id"],
                          snapshot_id=snapshot["id"], reason="Missing direct authorization")
        self.assertEqual(caught.exception.code, "invalid_snapshot_recovery_authorization")
        self.assertEqual(before, self.coordinator.state_path.read_bytes())

    def test_snapshot_recovery_amendment_rejects_candidate_no_go_iteration_and_closed_attempt(self):
        _, snapshot, b14, _ = self.prepare_snapshot_recovery()
        submitted = self.submit(b14)
        self.refused_snapshot_recovery_amendment("invalid_snapshot_recovery_amendment_target", b14, snapshot["id"])
        no_go = self.review(self.reviewer(submitted["attempt"]), "NO_GO")["attempt"]
        self.assertEqual(no_go["iteration"], b14["iteration"] + 1)
        self.refused_snapshot_recovery_amendment("invalid_snapshot_recovery_amendment_target", b14, snapshot["id"])

        quiescence = {"tool": "collaboration.list_agents", "target": b14["worker"]["target"],
                      "run_id": b14["worker"]["run_id"], "observed_at": COORD.iso(),
                      "status": "completed", "writers_stopped": True, "evidence": "Fixture worker stopped"}
        self.call("cancel", attempt=b14["id"], reason="Close amendment target", evidence=self.json_file(quiescence))
        self.refused_snapshot_recovery_amendment("closed_attempt", b14, snapshot["id"])

    def test_snapshot_recovery_amendment_rejects_iteration_change_without_no_go(self):
        _, snapshot, b14, _ = self.prepare_snapshot_recovery()
        self.write("c.py", "second drift before amendment\n")
        state = self.coordinator.read()
        current = next(item for item in state["attempts"] if item["id"] == b14["id"])
        current["iteration"] += 1
        self.coordinator.write(state)
        self.coordinator.read()
        before = self.coordinator.state_path.read_bytes()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.root_run}):
            with self.assertRaises(COORD.Refusal) as caught:
                self.call("amend-snapshot-recovery", actor="/root", attempt=b14["id"],
                          snapshot_id=snapshot["id"], user_authorization=SNAPSHOT_RECOVERY_AMENDMENT_AUTHORIZATION,
                          reason="One-time pre-submit manifest amendment")
        self.assertEqual(caught.exception.code, "snapshot_recovery_amendment_iteration")
        self.assertEqual(before, self.coordinator.state_path.read_bytes())

    def test_snapshot_recovery_amendment_allows_one_same_iteration_pre_candidate_rebase_only(self):
        _, snapshot, b14, proposal = self.prepare_snapshot_recovery()
        original_iteration = b14["iteration"]
        self.plan["updated_on"] = "2026-10-24"
        self.save_plan()
        rebased = self.call("rebase", attempt=b14["id"], reason="Reviewed B14 acceptance amendment")['attempt']
        self.assertEqual(rebased["iteration"], original_iteration)
        self.write("c.py", "second drift before recovery amendment\n")
        amendment = self.amend_snapshot_recovery(rebased, snapshot["id"])["amendment"]
        self.assertEqual(amendment["proposal_iteration"], proposal["iteration"])
        self.assertEqual(amendment["iteration"], original_iteration)
        self.refused("snapshot_recovery_amendment_late", "rebase", attempt=b14["id"],
                     reason="Plan cannot change after amended manifest pin")

    def test_snapshot_recovery_iteration_jump_without_matching_no_go_history_fails_closed(self):
        _, snapshot, b14, proposal = self.prepare_snapshot_recovery()
        submitted = self.submit(b14)
        self.review(self.reviewer(submitted["attempt"]), "NO_GO")
        state = self.coordinator.read()
        attempt = next(item for item in state["attempts"] if item["id"] == b14["id"])
        attempt["iteration"] += 1  # Simulate an iteration change without a resubmitted NO_GO candidate.
        self.coordinator.write(state)
        stale = self.coordinator.read()
        stale_attempt = next(item for item in stale["attempts"] if item["id"] == b14["id"])
        self.assertEqual(self.coordinator.recovery_candidate_proposals(stale, stale_attempt), [])
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.outside_changes(stale, stale_attempt)
        self.assertEqual(caught.exception.code, "snapshot_drift")
        self.refused_snapshot_recovery("duplicate_snapshot_recovery", b14, snapshot["id"])

    def test_snapshot_recovery_reused_candidate_requires_each_prior_no_go_audit(self):
        _, _, b14, proposal = self.prepare_snapshot_recovery()
        first = self.submit(b14)
        first_candidate = first["attempt"]["candidate"]
        first_reviewer = self.reviewer(first["attempt"])
        repaired = self.review(first_reviewer, "NO_GO")["attempt"]
        second = self.submit(repaired)
        self.assertEqual(second["attempt"]["candidate"]["iteration"], repaired["iteration"])

        state = self.coordinator.read()
        attempt = next(item for item in state["attempts"] if item["id"] == b14["id"])
        attempt["reviews"] = [review for review in attempt["reviews"]
                               if not (review.get("iteration") == first_candidate["iteration"]
                                       and review.get("candidate_digest") == first_candidate["digest"])]
        self.coordinator.write(state)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")
        self.assertTrue(any(item.get("proposal_id") == proposal["id"]
                            for item in next(item for item in attempt["submissions"]
                                             if item.get("iteration") == repaired["iteration"])
                            .get("snapshot_recoveries", [])))

    def test_full_lifecycle_dependency_and_wrong_root_run(self):
        self.gate()
        attempt = self.reserve()
        self.refused("wrong_role", "claim", attempt=attempt["id"], actor="/root/unbound", run_id=str(uuid.uuid4()))
        evidence = self.json_file(self.proof(attempt, "worker"))
        for action, kwargs in (("bind", {"tool_evidence": evidence}), ("accept", {"reason": "Wrong root"}),
                               ("cancel", {"reason": "Wrong root"}), ("rebase", {"reason": "Wrong root"})):
            self.refused("wrong_role", action, attempt=attempt["id"], run_id=str(uuid.uuid4()), **kwargs)
        wrong_parent = self.proof(attempt, "worker", parent_run=str(uuid.uuid4()))
        self.refused("invalid_binding", "bind", attempt=attempt["id"], tool_evidence=self.json_file(wrong_parent))
        attempt = self.worker(attempt)
        self.refused("wrong_role", "submit", attempt=attempt["id"], evidence="missing.json")
        self.write("a.py", "implemented\n")
        submitted = self.submit(attempt)
        bound = self.reviewer(submitted["attempt"])
        self.review(bound)
        self.refused("wrong_role", "accept", attempt=attempt["id"], actor=attempt["worker"]["target"], run_id=attempt["worker"]["run_id"], reason="Self accept")
        accepted = self.call("accept", attempt=attempt["id"], reason="Independent acceptance")["attempt"]
        self.assertEqual(accepted["status"], "accepted")
        self.assertIn("B02", self.call("next")["ready_p0"])
        self.assertEqual(len(accepted["submissions"]), 1)

    def test_cas_pending_path_resource_and_protected_scope(self):
        self.gate()
        self.reserve(resource=["host-port:8080"])
        self.refused("stale_revision", "reserve", task="B03", file=["c.py"], revision=0, reason="Stale")
        self.refused("path_conflict", "reserve", task="B03", file=["a.py"], reason="Overlap")
        self.refused("resource_conflict", "reserve", task="B03", file=["c.py"], resource=["host-port:8080"], reason="Busy port")
        for name in ("../outside", "app/**", "D:/outside", ".git/config", ".env", ".state/a", "материалы/new.json", "a.py/child", "CON.txt"):
            report = self.call("check", task="B03", file=[name])
            self.assertFalse(report["ready"], name)
        self.assertTrue(self.call("check", task="B03", file=[".env.example"])["ready"])

    def test_own_scope_drift_before_claim(self):
        self.gate()
        attempt = self.reserve()
        proof = self.proof(attempt, "worker")
        self.call("bind", attempt=attempt["id"], tool_evidence=self.json_file(proof))
        self.write("a.py", "someone wrote before claim\n")
        self.refused("baseline_drift", "claim", attempt=attempt["id"], actor=proof["target"], run_id=proof["run_id"])

    def test_dirty_preservation_outside_scope_and_accepted_handoff(self):
        self.gate()
        self.write("user-notes.txt", "Preserve unrelated dirty data\n")
        self.write("a.py", "Existing user edit\n")
        self.refused("dirty_scope", "reserve", task="B01", file=["a.py"], reason="Do not overwrite")
        self.write("a.py", "old a\n")
        first = self.complete()
        self.refused("dirty_scope", "reserve", task="B02", file=["a.py"], reason="Handoff must be explicit")
        second = self.complete("B02", ["a.py"], adopt_accepted=[first["id"]])
        self.assertEqual(second["status"], "accepted")
        self.assertEqual((self.root / "user-notes.txt").read_text(), "Preserve unrelated dirty data\n")
        self.assertTrue(self.coordinator.accepted(self.coordinator.read(), self.coordinator.load_plan(), "B01"))

    def test_unauthorized_outside_changes_and_other_claim_attribution(self):
        self.gate()
        first = self.worker(self.reserve())
        self.write("b.py", "unauthorized edit\n")
        with self.assertRaises(COORD.Refusal) as caught:
            self.submit(first)
        self.assertEqual(caught.exception.code, "out_of_scope_change")
        self.write("b.py", "old b\n")
        second = self.worker(self.reserve("B03", ["c.py"]))
        self.write("c.py", "other live worker\n")
        self.write("a.py", "own implementation\n")
        self.assertEqual(self.submit(first)["attempt"]["status"], "awaiting_review")
        self.assertEqual(second["status"], "in_progress")

    def test_stale_go_detects_changes_untracked_deletion_and_index_mode(self):
        self.gate()
        attempt = self.worker(self.reserve(files=["a.py", "new.py"]))
        self.write("a.py", "reviewed\n")
        submitted = self.submit(attempt)
        self.review(self.reviewer(submitted["attempt"]))
        for change in ("bytes", "new", "delete"):
            if change == "bytes":
                self.write("a.py", "changed after GO\n")
            elif change == "new":
                self.write("new.py", "created after GO\n")
            else:
                (self.root / "a.py").unlink()
            self.refused("stale_candidate", "accept", attempt=attempt["id"], reason="Cannot accept drift")
            self.write("a.py", "reviewed\n")
            (self.root / "new.py").unlink(missing_ok=True)
        self.git("update-index", "--chmod=+x", "a.py")
        self.refused("index_changed", "accept", attempt=attempt["id"], reason="Staged mode changed")

    def test_no_go_same_worker_new_reviewer_and_no_identity_reuse(self):
        self.gate()
        attempt = self.worker(self.reserve())
        self.write("a.py", "first implementation\n")
        submitted = self.submit(attempt)
        self_proof = self.proof(attempt, "reviewer", attempt["worker"]["target"], attempt["worker"]["run_id"])
        self.refused("reused_identity", "bind-reviewer", attempt=attempt["id"], tool_evidence=self.json_file(self_proof))
        bound = self.reviewer(submitted["attempt"])
        self.review(bound, "NO_GO")
        repaired = COORD.Coordinator.attempt(self.coordinator.read(), attempt["id"])
        self.assertEqual(repaired["status"], "changes_requested")
        self.assertEqual(repaired["iteration"], 2)
        self.write("a.py", "repaired implementation\n")
        self.submit(repaired)
        old = bound["attempt"]["reviewer"]
        reuse = self.proof(repaired, "reviewer", old["target"], old["run_id"])
        self.refused("reused_identity", "bind-reviewer", attempt=attempt["id"], tool_evidence=self.json_file(reuse))
        current = COORD.Coordinator.attempt(self.coordinator.read(), attempt["id"])
        self.review(self.reviewer(current))
        accepted = self.call("accept", attempt=attempt["id"], reason="Repaired GO")["attempt"]
        self.assertEqual(len(accepted["reviews"]), 2)
        self.assertEqual(len(accepted["submissions"]), 2)
        next_attempt = self.reserve("B03", ["c.py"])
        reuse = self.proof(next_attempt, "worker", attempt["worker"]["target"], attempt["worker"]["run_id"])
        self.refused("reused_identity", "bind", attempt=next_attempt["id"], tool_evidence=self.json_file(reuse))

    def test_plan_hash_normalization_and_explicit_scoped_rebase(self):
        self.gate()
        old_hash = self.coordinator.load_plan()["hash"]
        self.plan["updated_on"] = "2026-10-24"
        self.plan["product_status"] = "prepared"
        self.plan["tasks"][1].update(status="done", evidence=["Not runtime authority"])
        self.save_plan()
        self.assertEqual(old_hash, self.coordinator.load_plan()["hash"])
        # Restore tracked bytes before reserve; semantic hash normalization does not authorize dirty scope.
        self.git("restore", "--", "docs/backlog.json")
        attempt = self.worker(self.reserve(files=["docs/contract.md"]))
        self.write("docs/contract.md", "Legitimate planned contract update\n")
        with self.assertRaises(COORD.Refusal) as caught:
            self.submit(attempt)
        self.assertEqual(caught.exception.code, "plan_drift")
        rebased = self.call("rebase", attempt=attempt["id"], reason="Reviewed legitimate contract change")["attempt"]
        self.assertEqual(rebased["iteration"], 2)
        self.assertEqual(self.submit(rebased)["attempt"]["status"], "awaiting_review")

    def test_extend_then_fresh_submission(self):
        self.gate()
        attempt = self.worker(self.reserve())
        self.write("a.py", "first\n")
        self.submit(attempt)
        extended = self.call("extend", attempt=attempt["id"], file=["b.py"], reason="Needed second exact module")["attempt"]
        self.assertEqual(extended["status"], "in_progress")
        self.assertIsNone(extended["candidate"])
        self.write("b.py", "second\n")
        self.assertEqual(self.submit(extended)["attempt"]["status"], "awaiting_review")

    def test_contract_drift_invalidates_accepted_dependencies(self):
        self.gate()
        self.complete()
        self.assertIn("B02", self.call("next")["ready_p0"])
        self.write("docs/contract.md", "Incompatible new acceptance contract\n")
        self.assertFalse(self.coordinator.accepted(self.coordinator.read(), self.coordinator.load_plan(), "B01"))
        self.assertNotIn("B02", self.call("next")["ready_p0"])

    def test_released_peer_bytes_are_preserved_but_not_accepted(self):
        self.gate()
        first = self.worker(self.reserve())
        peer = self.worker(self.reserve("B03", ["c.py"]))
        self.write("c.py", "Unfinished peer output\n")
        proof = {"tool": "collaboration.list_agents", "target": peer["worker"]["target"],
                 "run_id": peer["worker"]["run_id"], "observed_at": COORD.iso(), "status": "completed",
                 "writers_stopped": True, "evidence": "Isolated stopped writer"}
        self.call("release", attempt=peer["id"], reason="Preserve unfinished peer", evidence=self.json_file(proof))
        self.write("a.py", "My implementation\n")
        self.assertEqual(self.submit(first)["attempt"]["status"], "awaiting_review")
        self.assertFalse(self.coordinator.accepted(self.coordinator.read(), self.coordinator.load_plan(), "B03"))
        self.refused("dirty_scope", "reserve", task="B03", file=["c.py"], reason="Not accepted lineage")
        self.write("c.py", "Changed after stopped receipt\n")
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.outside_changes(self.coordinator.read(), first)
        self.assertEqual(caught.exception.code, "out_of_scope_change")

    def test_rebase_cannot_bypass_new_additional_gate(self):
        self.gate()
        attempt = self.worker(self.reserve(files=["a.py", "docs/backlog.json"]))
        self.plan["tasks"][1]["additional_gate"] = "Actual new required approval"
        self.save_plan()
        self.refused("additional_gate_required", "rebase", attempt=attempt["id"], reason="Cannot assume approval")
        evidence = {"task_id": "B01", "actor": "/root", "answers": [{"question_id": "additional_gate", "decision": "allowed",
                    "answer": "Real isolated fixture answer", "source": "Fixture authority", "received_at": COORD.iso()}]}
        self.call("record-gate", task="B01", reason="Sourced approval", evidence=self.json_file(evidence))
        rebased = self.call("rebase", attempt=attempt["id"], reason="Approval now recorded")["attempt"]
        self.assertEqual(self.submit(rebased)["attempt"]["status"], "awaiting_review")

    def test_atomic_failure_corruption_and_workspace_binding(self):
        self.gate()
        before = self.coordinator.state_path.read_bytes()
        with mock.patch.object(COORD.os, "replace", side_effect=OSError("Fixture atomic replacement failure")):
            with self.assertRaises(OSError):
                self.reserve()
        self.assertEqual(before, self.coordinator.state_path.read_bytes())
        self.assertEqual(list(self.coordinator.directory.glob(".claims-*.tmp")), [])
        state = self.coordinator.read()
        state["root"] = str(self.root.parent)
        self.coordinator.state_path.write_bytes(COORD.canonical(state))
        with self.assertRaises(COORD.Refusal) as caught:
            self.call("status")
        self.assertEqual(caught.exception.code, "workspace_mismatch")
        self.coordinator.state_path.write_text("{broken", encoding="utf-8")
        corrupt = self.coordinator.state_path.read_bytes()
        with self.assertRaises(ValueError):
            # JSONDecodeError is translated to corrupt_state by read().
            json.loads(corrupt)
        with self.assertRaises(COORD.Refusal):
            self.reserve()
        self.assertEqual(corrupt, self.coordinator.state_path.read_bytes())

    def test_block_resume_release_history_and_bound_cancel_requires_quiescence(self):
        self.gate()
        attempt = self.worker(self.reserve())
        self.write("a.py", "Preserved unfinished work\n")
        self.call("block", attempt=attempt["id"], actor=attempt["worker"]["target"], run_id=attempt["worker"]["run_id"], reason="Actual fixture blocker")
        self.refused("missing_evidence", "cancel", attempt=attempt["id"], reason="Cannot silently cancel bound writer")
        self.call("resume", attempt=attempt["id"], reason="Recovered prerequisite")
        self.call("block", attempt=attempt["id"], reason="Still blocked")
        proof = {"tool": "collaboration.list_agents", "target": attempt["worker"]["target"], "run_id": attempt["worker"]["run_id"],
                 "observed_at": COORD.iso(), "status": "completed", "writers_stopped": True, "evidence": "Observed stopped isolated worker"}
        result = self.call("release", attempt=attempt["id"], reason="Return task without deleting work", evidence=self.json_file(proof))
        self.assertEqual(result["attempt"]["status"], "released")
        self.assertEqual((self.root / "a.py").read_text(), "Preserved unfinished work\n")
        self.assertEqual(self.coordinator.read()["events"][-1]["previous"]["status"], "blocked")
        self.refused("closed_attempt", "claim", attempt=attempt["id"], actor=attempt["worker"]["target"], run_id=attempt["worker"]["run_id"])

    def test_concurrent_reservation_and_kernel_lock_recovery(self):
        self.gate()
        revision = self.coordinator.read()["revision"]
        processes = []
        for task in ("B01", "B03"):
            args = self.args("reserve", task=task, file=["a.py"], reason="Concurrent fixture", revision=revision)
            command = [sys.executable, "-B", str(SCRIPT), "--root", str(self.root), "reserve", "--task", task,
                       "--file", "a.py", "--reason", "Concurrent fixture", "--actor", args.actor,
                       "--run-id", args.run_id, "--expected-revision", str(revision)]
            processes.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outputs = [process.communicate(timeout=20) for process in processes]
        self.assertEqual(sorted(process.returncode for process in processes), [0, 2], outputs)
        self.assertEqual(len(self.coordinator.read()["attempts"]), 1)
        # Abrupt exit releases the OS lock; mere lock-file presence is never expiry.
        child_code = "import runpy,sys,time\nm=runpy.run_path(sys.argv[1],run_name='fixture_lock')\nc=m['Coordinator'](sys.argv[2])\nwith c.locked():\n print('LOCKED',flush=True)\n time.sleep(30)\n"
        holder = subprocess.Popen([sys.executable, "-B", "-c", child_code, str(SCRIPT), str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
        finally:
            holder.terminate()
            holder.communicate(timeout=10)
        winner = self.coordinator.read()["attempts"][0]
        self.call("cancel", attempt=winner["id"], reason="Unbound fixture ended")


    def test_workspace_delta_recovery_amendment_appends_exact_pre_submit_head(self):
        _, snapshot, b14, proposal, snapshot_amendment = self.prepare_workspace_delta_recovery()
        self.write("external-first.txt", "first authorized version\n")
        original_snapshot_manifest = self.coordinator.exact_manifest(snapshot["files"])
        origin = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        original_origin = copy.deepcopy(origin)
        self.write("external-second.txt", "newly observed path\n")

        amended = self.amend_workspace_delta_recovery(b14)["workspace_delta_recovery_amendment"]
        state = self.coordinator.read()
        self.assertEqual(state["workspace_delta_recoveries"], [original_origin])
        self.assertEqual(len(state["workspace_delta_recovery_amendments"]), 1)
        self.assertEqual(amended["sequence"], 1)
        self.assertEqual(amended["origin_recovery_id"], origin["id"])
        self.assertEqual(amended["origin_recovery_sha256"], COORD.digest(origin))
        self.assertEqual(amended["previous_entry_id"], origin["id"])
        self.assertEqual(amended["previous_entry_sha256"], COORD.digest(origin))
        self.assertEqual(amended["manifest"], self.coordinator.unexplained_workspace_delta(
            state, next(item for item in state["attempts"] if item["id"] == b14["id"])))
        self.assertEqual(amended["manifest_sha256"], COORD.digest(amended["manifest"]))
        self.assertEqual(amended["snapshot_recovery_bindings"], origin["snapshot_recovery_bindings"])
        self.assertEqual(self.coordinator.exact_manifest(snapshot["files"]), original_snapshot_manifest)
        self.assertEqual(next(item for item in state["snapshot_recoveries"] if item["id"] == proposal["id"]), proposal)
        self.assertEqual(next(item for item in state["snapshot_recovery_amendments"]
                              if item["id"] == snapshot_amendment["id"]), snapshot_amendment)

        candidate = self.submit(b14)["attempt"]["candidate"]
        summary = candidate["workspace_delta_recovery"]
        self.assertEqual(summary["chain_head_id"], amended["id"])
        self.assertEqual(summary["chain_head_sha256"], COORD.digest(amended))
        self.assertEqual(summary["chain_sequence"], 1)
        self.assertEqual(summary["manifest"], amended["manifest"])
        submit_event = next(item for item in self.coordinator.read()["events"]
                            if item.get("action") == "submit" and item.get("attempt_id") == b14["id"])
        self.assertEqual(submit_event["candidate_digest"], candidate["digest"])
        self.assertEqual(submit_event["workspace_delta_recovery"], summary)
        self.refused_workspace_delta_amendment("workspace_delta_recovery_amendment_late", b14)

    def test_workspace_delta_recovery_amendment_binds_no_go_versions_and_accepts_current_head(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("repairable-external.txt", "origin bytes\n")
        origin = self.record_workspace_delta_recovery(b14)["workspace_delta_recovery"]
        first = self.submit(b14)["attempt"]
        first_candidate = first["candidate"]
        repaired = self.review(self.reviewer(first), "NO_GO")["attempt"]
        self.assertEqual(repaired["iteration"], first_candidate["iteration"] + 1)
        self.write("repairable-external.txt", "freshly authorized amended bytes\n")

        amendment = self.amend_workspace_delta_recovery(repaired)["workspace_delta_recovery_amendment"]
        second = self.submit(repaired)["attempt"]
        second_candidate = second["candidate"]
        self.assertEqual(first_candidate["workspace_delta_recovery"]["chain_head_id"], origin["id"])
        self.assertEqual(first_candidate["workspace_delta_recovery"]["chain_sequence"], 0)
        self.assertEqual(second_candidate["workspace_delta_recovery"]["chain_head_id"], amendment["id"])
        self.assertEqual(second_candidate["workspace_delta_recovery"]["chain_sequence"], 1)
        review = self.review(self.reviewer(second), "GO")["attempt"]["review"]
        self.assertEqual(review["workspace_delta_recovery"], second_candidate["workspace_delta_recovery"])
        accepted = self.call("accept", attempt=b14["id"], reason="Accept reviewed amended workspace chain")
        self.assertEqual(accepted["attempt"]["status"], "accepted")
        approval = accepted["workspace_delta_recovery_acceptances"][0]
        self.assertEqual(approval["chain_head_id"], amendment["id"])
        self.assertEqual(approval["chain_head_sha256"], COORD.digest(amendment))
        self.assertEqual(approval["chain_sequence"], 1)

        valid_state = self.coordinator.read()
        corrupted = copy.deepcopy(valid_state)
        tampered = corrupted["workspace_delta_recovery_amendments"][0]
        tampered["previous_entry_sha256"] = "0" * 64
        event = next(item for item in corrupted["events"]
                     if item.get("action") == "amend-workspace-recovery"
                     and item.get("workspace_delta_recovery_amendment_id") == tampered["id"])
        event["workspace_delta_recovery_amendment"] = copy.deepcopy(tampered)
        event["workspace_delta_recovery_chain_head_sha256"] = tampered["previous_entry_sha256"]
        self.coordinator.write(corrupted)
        with self.assertRaises(COORD.Refusal) as caught:
            self.coordinator.read()
        self.assertEqual(caught.exception.code, "corrupt_state")
        self.coordinator.write(valid_state)

    def test_workspace_delta_recovery_amendment_fails_closed_on_authorization_pins_hash_and_duplicate(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("pinned-delta.txt", "first bytes\n")
        self.record_workspace_delta_recovery(b14)
        self.write("pinned-delta.txt", "changed before submit\n")
        state = self.coordinator.read()
        attempt = next(item for item in state["attempts"] if item["id"] == b14["id"])
        expected = COORD.digest(self.coordinator.unexplained_workspace_delta(state, attempt))

        self.refused_workspace_delta_amendment("invalid_workspace_delta_recovery_authorization", b14,
                                               authorization="stale authorization")
        self.refused_workspace_delta_amendment("wrong_role", b14, actor="/root/other")
        self.refused_workspace_delta_amendment("current_run_mismatch", b14,
                                               run_id=self.root_run, environment_run=str(uuid.uuid4()))
        self.refused_workspace_delta_amendment("workspace_delta_manifest_changed", b14,
                                               manifest_sha256="0" * 64)
        with mock.patch.object(self.coordinator, "index_digest", return_value="0" * 64):
            self.refused_workspace_delta_amendment("workspace_delta_recovery_pins_changed", b14,
                                                   manifest_sha256=expected)

        amendment = self.amend_workspace_delta_recovery(b14, manifest_sha256=expected)[
            "workspace_delta_recovery_amendment"]
        self.refused_workspace_delta_amendment("duplicate_workspace_delta_recovery_amendment", b14)
        current = self.coordinator.read()
        self.assertEqual(current["workspace_delta_recoveries"][0]["id"],
                         amendment["origin_recovery_id"])
        self.assertEqual(len(current["workspace_delta_recovery_amendments"]), 1)

    def test_workspace_delta_recovery_amendment_rejects_post_record_drift_and_post_go(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("stable-external.txt", "first\n")
        self.record_workspace_delta_recovery(b14)
        self.write("second-external.txt", "second\n")
        self.amend_workspace_delta_recovery(b14)
        stable = "second\n"
        self.write("second-external.txt", "drift after amendment\n")
        self.refused("stale_workspace_delta_recovery", "submit", attempt=b14["id"],
                     actor=b14["worker"]["target"], run_id=b14["worker"]["run_id"],
                     evidence=self.json_file({"task_id": "B14", "attempt_id": b14["id"], "summary": "stale delta",
                                              "checks": [{"name": "focused", "status": "passed", "evidence": "fixture"}]}))
        self.write("second-external.txt", stable)
        submitted = self.submit(b14)["attempt"]
        self.review(self.reviewer(submitted), "GO")
        self.refused_workspace_delta_amendment("workspace_delta_recovery_amendment_late", b14)
        accepted = self.call("accept", attempt=b14["id"], reason="Close after fresh GO")
        self.assertEqual(accepted["attempt"]["status"], "accepted")
        self.refused_workspace_delta_amendment("closed_attempt", b14)

    def test_workspace_delta_drift_after_submit_allows_only_root_observed_no_go(self):
        _, _, b14, _, _ = self.prepare_workspace_delta_recovery()
        self.write("post-submit-drift.txt", "recorded version\n")
        self.record_workspace_delta_recovery(b14)
        submitted = self.submit(b14)["attempt"]
        candidate = submitted["candidate"]
        self.write("post-submit-drift.txt", "changed after Submit\n")

        # Candidate bytes and snapshot pins remain exact; only the recovery delta is stale.
        bound = self.reviewer(submitted)
        observation = self.workspace_delta_stale_observation(b14, candidate)
        before = self.coordinator.state_path.read_bytes()
        with self.assertRaises(COORD.Refusal) as caught_go:
            self.review(bound, "GO", workspace_delta_stale_observation=observation)
        self.assertEqual(caught_go.exception.code, "stale_workspace_delta_recovery")
        with self.assertRaises(COORD.Refusal) as caught_missing:
            self.review(bound, "NO_GO")
        self.assertEqual(caught_missing.exception.code, "invalid_review")
        forged = copy.deepcopy(observation)
        forged["candidate_digest"] = "0" * 64
        with self.assertRaises(COORD.Refusal) as caught_forged:
            self.review(bound, "NO_GO", workspace_delta_stale_observation=forged)
        self.assertEqual(caught_forged.exception.code, "invalid_review")
        stale_observation = copy.deepcopy(observation)
        stale_observation["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with self.assertRaises(COORD.Refusal) as caught_stale:
            self.review(bound, "NO_GO", workspace_delta_stale_observation=stale_observation)
        self.assertEqual(caught_stale.exception.code, "stale_evidence")
        wrong_manifest_digest = copy.deepcopy(observation)
        wrong_manifest_digest["manifest_sha256"] = "0" * 64
        with self.assertRaises(COORD.Refusal) as caught_digest:
            self.review(bound, "NO_GO", workspace_delta_stale_observation=wrong_manifest_digest)
        self.assertEqual(caught_digest.exception.code, "corrupt_state")
        self.assertEqual(self.coordinator.state_path.read_bytes(), before)

        reviewed = self.review(bound, "NO_GO", workspace_delta_stale_observation=observation)["attempt"]
        self.assertEqual(reviewed["status"], "changes_requested")
        self.assertEqual(reviewed["iteration"], candidate["iteration"] + 1)
        historical = reviewed["reviews"][-1]
        self.assertEqual(historical["workspace_delta_stale_observation"], observation)
        review_event = next(item for item in self.coordinator.read()["events"]
                            if item.get("action") == "review" and item.get("candidate_digest") == candidate["digest"])
        self.assertEqual(review_event["workspace_delta_stale_observation"], observation)

        amendment = self.amend_workspace_delta_recovery(reviewed)["workspace_delta_recovery_amendment"]
        self.assertEqual(amendment["iteration"], reviewed["iteration"])
        repaired = self.submit(reviewed)["attempt"]
        fresh_review = self.review(self.reviewer(repaired), "GO")["attempt"]
        self.assertEqual(fresh_review["review"]["result"], "GO")
        accepted = self.call("accept", attempt=b14["id"], reason="Accept fresh candidate after stale recovery NO_GO")
        self.assertEqual(accepted["attempt"]["status"], "accepted")
        self.assertEqual(accepted["workspace_delta_recovery_acceptances"][0]["chain_head_id"], amendment["id"])


if __name__ == "__main__":
    unittest.main()
