"""Run with: python -B -m unittest discover -s tests -p test_session_board.py -v"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "session_board.py"
ROLES = ["orchestrator", "understanding", "data", "algorithm", "api", "user-ui",
         "operator-ui", "ux-review", "qa", "release"]


class SessionBoardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="session-board-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "synthetic-project"
        self.project.mkdir()
        self.board = self.project / ".sprint"

    def run_cli(self, *arguments, code=0, project=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments, "--project", str(project or self.project)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        output = result.stdout if code == 0 else result.stderr
        value = json.loads(output)
        self.assertEqual(value["ok"], code == 0)
        self.assertNotIn("Traceback", result.stderr)
        return value

    def init(self, sessions=10):
        return self.run_cli("init", "--sessions", str(sessions), "--session", "owner", "--apply")

    def tree(self):
        return {str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def handoff(self, role="understanding", session="worker", state="done", code=0):
        return self.run_cli("handoff", "--role", role, "--session", session,
                            "--state", state, "--summary", "Synthetic work acknowledged.",
                            "--evidence", "test command passed", "--evidence", "reviewed sample",
                            "--next", "orchestrator review", code=code)

    def phase(self, value, session="owner", code=0):
        return self.run_cli("phase", "--session", session, "--value", value, code=code)

    def test_preview_writes_nothing(self):
        result = self.run_cli("init", "--sessions", "3")
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["writes"])
        self.assertEqual(result["requested_sessions"], 3)
        self.assertEqual(result["roles"], ROLES)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_init_claims_orchestrator_and_copies_available_templates(self):
        result = self.init(2)
        self.assertEqual(result["session"], "owner")
        self.assertEqual(result["requested_sessions"], 2)
        self.assertEqual([row["role"] for row in result["roles"]], ROLES)
        self.assertEqual(result["roles"][0]["session"], "owner")
        self.assertEqual(result["roles"][1]["handoff_state"], "pending")
        config = json.loads((self.board / "config.json").read_text(encoding="utf-8"))
        for name in config["templates"]:
            self.assertEqual((self.board / name).read_bytes(), (SKILL / "templates" / name).read_bytes())

    def test_reinit_preserves_existing_state_and_template_edits(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        self.handoff()
        self.phase("freeze")
        (self.board / "SPRINT.md").write_text("User's retained notes.\n", encoding="utf-8")
        before = self.tree()
        result = self.run_cli("init", "--sessions", "1", "--session", "different", "--apply")
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["session"], "owner")
        self.assertEqual(result["requested_sessions"], 10)
        self.assertEqual(self.tree(), before)
        self.run_cli("status")
        self.assertEqual(self.tree(), before)

    def test_default_join_is_idempotent(self):
        self.init()
        first = self.run_cli("join", "--session", "worker")
        before = self.tree()
        second = self.run_cli("join", "--session", "worker")
        explicit = self.run_cli("join", "--session", "worker", "--role", "understanding")
        self.assertEqual(first["role"], "understanding")
        self.assertEqual(second["status"], "already-joined")
        self.assertEqual(explicit["status"], "already-joined")
        self.assertEqual(self.tree(), before)

    def test_role_conflict_preserves_claim(self):
        self.init()
        self.run_cli("join", "--session", "worker", "--role", "data")
        before = self.tree()
        conflict = self.run_cli("join", "--session", "other", "--role", "data", code=2)
        self.assertEqual(conflict["error"], "ownership")
        self.assertEqual(self.tree(), before)

    def test_one_session_can_cover_roles_without_extra_session_capacity(self):
        self.init(2)
        self.run_cli("join", "--session", "worker", "--role", "data")
        result = self.run_cli("join", "--session", "worker", "--role", "qa")
        self.assertEqual(result["roles"], ["data", "qa"])
        result = self.run_cli("join", "--session", "other", code=2)
        self.assertEqual(result["error"], "no-slot")
        result = self.run_cli("join", "--session", "owner", "--role", "release")
        self.assertEqual(result["roles"], ["orchestrator", "release"])

    def test_one_total_session_has_no_worker_slot(self):
        self.init(1)
        result = self.run_cli("join", "--session", "worker", code=2)
        self.assertEqual(result["error"], "no-slot")
        self.assertEqual(self.run_cli("join", "--session", "owner")["role"], "orchestrator")

    def test_generated_ids_are_reusable(self):
        initialized = self.run_cli("init", "--apply")
        joined = self.run_cli("join")
        self.assertNotEqual(initialized["session"], joined["session"])
        resumed = self.run_cli("join", "--session", joined["session"])
        self.assertEqual(resumed["role"], joined["role"])

    def test_concurrent_independent_processes_respect_unique_roles_and_capacity(self):
        self.init(10)
        processes = [subprocess.Popen(
            [sys.executable, str(SCRIPT), "join", "--project", str(self.project), "--session", f"worker-{index}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        ) for index in range(14)]
        successes, errors = [], []
        for process in processes:
            output, error = process.communicate(timeout=20)
            if process.returncode == 0:
                successes.append(json.loads(output))
            else:
                self.assertEqual(process.returncode, 2, error)
                errors.append(json.loads(error))
        self.assertEqual(len(successes), 9)
        self.assertEqual(len({value["role"] for value in successes}), 9)
        self.assertEqual(len({value["session"] for value in successes}), 9)
        self.assertEqual([value["error"] for value in errors], ["no-slot"] * 5)
        status = self.run_cli("status")
        self.assertTrue(all(row["claim_status"] == "claimed" for row in status["roles"]))
        self.assertFalse((self.board / ".mutation.lock").exists())

    def test_partial_claim_is_reserved_and_never_taken_over(self):
        self.init(3)
        path = self.board / "claims" / "understanding.json"
        path.write_bytes(b"")
        self.assertEqual(self.run_cli("status")["roles"][1]["claim_status"], "reserved")
        conflict = self.run_cli("join", "--session", "worker", "--role", "understanding", code=2)
        self.assertEqual(conflict["error"], "ownership")
        joined = self.run_cli("join", "--session", "worker")
        self.assertEqual(joined["role"], "data")
        self.assertEqual(self.run_cli("join", "--session", "other", code=2)["error"], "no-slot")
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(self.phase("independent", code=2)["error"], "stop-ack-required")

    def test_handoff_checks_owner_and_preserves_previous_content(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        recorded = self.handoff(state="working")
        self.assertEqual(recorded["handoff"]["evidence"], ["test command passed", "reviewed sample"])
        before = self.tree()
        self.assertEqual(self.handoff(session="other", code=2)["error"], "ownership")
        self.assertEqual(self.tree(), before)
        self.handoff()
        self.assertEqual(self.run_cli("status")["roles"][1]["handoff_state"], "done")
        self.assertFalse(list((self.board / "handoffs").glob("*.tmp")))
        self.assertEqual(self.handoff(role="qa", code=2)["error"], "ownership")

    def test_unrelated_existing_handoff_is_not_overwritten(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        path = self.board / "handoffs" / "understanding.json"
        path.write_text('{"unrelated":"retain me"}\n', encoding="utf-8")
        before = path.read_bytes()
        self.assertEqual(self.handoff(code=2)["error"], "invalid-handoff")
        self.assertEqual(path.read_bytes(), before)

    def test_stranded_mutation_lock_is_never_taken_over(self):
        self.init()
        lock = self.board / ".mutation.lock"
        lock.write_bytes(b"previous-process-reservation")
        before = self.tree()
        self.assertEqual(self.run_cli("join", "--session", "worker", code=2)["error"], "board-busy")
        self.run_cli("status")
        self.assertEqual(self.tree(), before)

    def test_phase_owner_monotone_and_stop_guards(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        before = self.tree()
        self.assertEqual(self.phase("development", session="worker", code=2)["error"], "ownership")
        self.assertEqual(self.tree(), before)
        self.phase("freeze")
        self.assertEqual(self.phase("planning", code=2)["error"], "phase-regression")
        self.assertEqual(self.phase("independent", code=2)["error"], "stop-ack-required")
        self.handoff(state="blocked")
        self.assertEqual(self.phase("independent", code=2)["error"], "stop-ack-required")
        self.handoff(state="stopped")
        self.phase("independent")
        before = self.tree()
        self.assertEqual(self.run_cli("join", "--session", "new", code=2)["error"], "ai-stop-phase")
        self.assertEqual(self.handoff(code=2)["error"], "ai-stop-phase")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.run_cli("status")["phase"], "independent")
        self.phase("submitted")
        self.assertEqual(self.phase("freeze", code=2)["error"], "phase-regression")

    def test_submitted_cannot_skip_worker_acknowledgement(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        self.assertEqual(self.phase("submitted", code=2)["error"], "stop-ack-required")
        self.handoff(state="done")
        self.phase("submitted")
        self.assertEqual(self.handoff(code=2)["error"], "ai-stop-phase")

    def test_invalid_session_ids_and_session_counts_write_nothing(self):
        for session in ("../escape", "a/b", "a\\b", ".", "a..b", "", "x" * 65):
            with self.subTest(session=session):
                # An explicitly empty ID must also fail, rather than generate a UUID.
                result = self.run_cli("init", "--apply", "--session", session, code=2)
                self.assertEqual(result["error"], "unsafe-session")
                self.assertFalse(self.board.exists())
        for count in (0, 11):
            self.assertEqual(self.run_cli("init", "--sessions", str(count), "--apply", code=2)["error"], "sessions-range")
            self.assertFalse(self.board.exists())

    def test_every_command_requires_explicit_project_and_known_roles(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "status"], capture_output=True,
                                text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"], "arguments")
        self.init()
        before = self.tree()
        self.assertEqual(self.run_cli("join", "--role", "../escape", code=2)["error"], "arguments")
        self.assertEqual(self.tree(), before)

    def test_rejects_missing_root_home_and_skill_project_paths(self):
        for path in (self.root / "missing", Path(self.project.anchor), Path.home(), SKILL, SKILL / "scripts"):
            with self.subTest(path=path):
                self.run_cli("init", "--apply", project=path, code=2)
        self.assertFalse(self.board.exists())

    def test_unrelated_or_incomplete_board_is_preserved(self):
        self.board.mkdir()
        unrelated = self.board / "notes.txt"
        unrelated.write_text("Retain unrelated state.\n", encoding="utf-8")
        before = self.tree()
        self.run_cli("init", "--apply", code=2)
        self.assertEqual(self.tree(), before)

    def test_malformed_config_values_return_safe_json_errors(self):
        self.init()
        path = self.board / "config.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for change in ({"templates": [{}]}, {"version": True}, {"requested_sessions": True},
                       {"roles": ["orchestrator"]}, {"phase": "unexpected"}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**original, **change}), encoding="utf-8")
                before = self.tree()
                self.run_cli("init", "--apply", code=2)
                self.run_cli("join", "--session", "worker", code=2)
                self.assertEqual(self.tree(), before)
        (self.board / "config.json").write_text('{"kind":"other-tool"}', encoding="utf-8")
        before = self.tree()
        self.run_cli("init", "--apply", code=2)
        self.run_cli("join", "--session", "worker", code=2)
        self.assertEqual(self.tree(), before)

    def test_incomplete_orchestrator_and_missing_template_stop_reinit(self):
        self.init()
        claim = self.board / "claims" / "orchestrator.json"
        original = claim.read_bytes()
        claim.write_bytes(b"")
        before = self.tree()
        self.run_cli("init", "--apply", code=2)
        self.assertEqual(self.tree(), before)
        claim.write_bytes(original)
        (self.board / "SPRINT.md").unlink()
        before = self.tree()
        self.run_cli("init", "--apply", code=2)
        self.assertEqual(self.tree(), before)

    def symlink(self, target, link, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlink creation unsupported: {error}")

    def test_symlink_project_is_rejected(self):
        linked = self.root / "linked-project"
        self.symlink(self.project, linked, directory=True)
        self.assertEqual(self.run_cli("init", "--apply", project=linked, code=2)["error"], "unsafe-path")
        self.assertFalse(self.board.exists())

    def test_symlink_board_destination_is_rejected(self):
        outside = self.root / "external"
        outside.mkdir()
        self.symlink(outside, self.board, directory=True)
        self.assertEqual(self.run_cli("init", "--apply", code=2)["error"], "unsafe-path")
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_handoff_does_not_modify_target(self):
        self.init()
        self.run_cli("join", "--session", "worker")
        outside = self.root / "external.txt"
        outside.write_text("Keep this.\n", encoding="utf-8")
        self.symlink(outside, self.board / "handoffs" / "understanding.json")
        self.assertEqual(self.handoff(code=2)["error"], "unsafe-path")
        self.assertEqual(outside.read_text(encoding="utf-8"), "Keep this.\n")

    @unittest.skipUnless(os.name == "nt", "Windows junction coverage")
    def test_windows_junction_project_is_rejected(self):
        linked = self.root / "junction-project"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(self.project)],
                                capture_output=True, timeout=20)
        if result.returncode:
            self.skipTest("Directory junction creation is unavailable.")
        try:
            self.assertEqual(self.run_cli("init", "--apply", project=linked, code=2)["error"], "unsafe-path")
            self.assertFalse(self.board.exists())
        finally:
            # Remove only this test-created junction, never traverse its target.
            os.rmdir(linked)


if __name__ == "__main__":
    unittest.main()
