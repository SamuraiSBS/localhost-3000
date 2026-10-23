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
FIXTURES = REPOSITORY / ".state" / "mvp-coordination-tests"


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
        ]}
        self.save_plan()
        self.write("a.py", "old a\n")
        self.write("b.py", "old b\n")
        self.write("c.py", "old c\n")
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

    def review(self, bound, result="GO"):
        attempt = bound["attempt"]
        report = {"task_id": attempt["task_id"], "attempt_id": attempt["id"], "iteration": attempt["iteration"],
                  "candidate_digest": bound["candidate_digest"], "plan_hash": bound["plan_hash"], "review_nonce": bound["review_nonce"],
                  "result": result, "summary": "Independent fixture review",
                  "checks": [{"name": "Acceptance", "status": "passed" if result == "GO" else "failed", "evidence": "Actual fixture result"}],
                  "findings": [] if result == "GO" else ["Repair fixture calculation"]}
        return self.call("review", attempt=attempt["id"], actor=attempt["reviewer"]["target"], run_id=attempt["reviewer"]["run_id"], evidence=self.json_file(report))

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


if __name__ == "__main__":
    unittest.main()
