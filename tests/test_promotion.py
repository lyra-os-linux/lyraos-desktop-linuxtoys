from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/promote-linuxtoys-staging.sh"


class PromotionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="linuxtoys-promotion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture = self.root / "osc"
        shutil.copyfile(ROOT / "tests/promotion_osc_fixture.py", fixture)
        fixture.chmod(0o755)
        self.state_path = self.root / "state.json"
        self.write({"head": "2", "advance_after_metadata": True, "calls": []})
        self.env = dict(os.environ, PROMOTION_FIXTURE=str(self.state_path),
                        PATH=str(self.root) + os.pathsep + os.environ["PATH"])

    def read(self):
        return json.loads(self.state_path.read_text())

    def write(self, state):
        self.state_path.write_text(json.dumps(state))

    def execute(self, *args, expected=0, answer=""):
        result = subprocess.run(["bash", str(SCRIPT), *args], env=self.env, input=answer,
                                cwd=self.root, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def assert_pinned(self, version, checksum):
        state = self.read()
        self.assertEqual(state["head"], "3")
        self.assertEqual(state["submitted"]["version"], version)
        self.assertEqual(state["submitted"]["hash"], checksum)
        self.assertIn("Source revision: " + checksum, state["submitted"]["message"])
        for call in state["calls"]:
            if call[0] in ("cat", "submitrequest"):
                self.assertEqual(call[call.index("-r") + 1], checksum)
            if call[0] == "cat":
                self.assertIn("--unexpand", call)

    def test_default_pins_before_reading_version_and_submitting(self):
        result = self.execute("--evidence", "verified fixture", "--yes")
        self.assertIn("version 6.9", result.stdout)
        self.assertNotIn("version 6.10", result.stdout)
        self.assert_pinned("6.9", "b" * 32)

    def test_explicit_number_hash_and_latest_resolve_to_immutable_sources(self):
        for revision, version, checksum in [("1", "6.7.1", "a" * 32),
                                            ("a" * 32, "6.7.1", "a" * 32),
                                            ("latest", "6.9", "b" * 32)]:
            with self.subTest(revision=revision):
                self.write({"head": "2", "advance_after_metadata": True, "calls": []})
                result = self.execute("--evidence", "fixture", "--revision", revision, "--yes")
                self.assertIn("version " + version, result.stdout)
                self.assert_pinned(version, checksum)

    def test_head_change_while_waiting_for_human_confirmation(self):
        self.write({"head": "2", "advance_after_metadata": False, "calls": []})
        output = self.root / "interactive.log"
        with output.open("w") as log:
            process = subprocess.Popen(["bash", str(SCRIPT), "--evidence", "human fixture"],
                                       env=self.env, cwd=self.root, stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, text=True)
            try:
                deadline = time.monotonic() + 5
                while "Test evidence: human fixture" not in output.read_text():
                    self.assertIsNone(process.poll(), output.read_text())
                    self.assertLess(time.monotonic(), deadline, output.read_text())
                    time.sleep(0.01)
                state = self.read()
                self.assertNotIn("submitted", state)
                state["head"] = "3"
                self.write(state)
                process.communicate("yes\n", timeout=10)
                self.assertEqual(process.returncode, 0, output.read_text())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assert_pinned("6.9", "b" * 32)

    def test_diff_pins_both_sides_and_has_promotion_direction(self):
        for revision, version, checksum in [(None, "6.9", "b" * 32), ("1", "6.7.1", "a" * 32)]:
            with self.subTest(revision=revision):
                self.write({"head": "2", "advance_after_metadata": True, "calls": []})
                args = ["--diff"] + (["--revision", revision] if revision else [])
                result = self.execute(*args)
                self.assertIn("+Version: " + version, result.stdout)
                state = self.read()
                call = next(c for c in state["calls"] if c[0] == "rdiff")
                self.assertEqual(call[call.index("-r") + 1], "d" * 32 + ":" + checksum)
                self.assertNotIn("submitted", state)
                self.assertNotIn("accepted", state)

    def test_cancel_and_eof_never_submit(self):
        for answer in ("n\n", ""):
            with self.subTest(answer=answer):
                self.write({"head": "2", "advance_after_metadata": True, "calls": []})
                self.execute("--evidence", "fixture", answer=answer, expected=1)
                self.assertNotIn("submitted", self.read())

    def test_invalid_metadata_or_spec_never_submit(self):
        for error in ("metadata", "xml", "missing_hash", "invalid_hash", "spec", "missing_version", "duplicate_version"):
            with self.subTest(error=error):
                self.write({"head": "2", "advance_after_metadata": True, "calls": [], "error": error})
                self.execute("--evidence", "fixture", "--yes", expected=2)
                self.assertNotIn("submitted", self.read())

    def test_invalid_arguments_fail_before_contacting_obs(self):
        for args in [("--revision",), ("--evidence",), ("--revision", ""),
                     ("--evidence", "fixture", "--revision", "latest&rev=3"),
                     ("--evidence", "fixture", "--revision", "missing"),
                     ("--yes",), ("--evidence", "fixture", "--accept")]:
            with self.subTest(args=args):
                self.execute(*args, expected=2)
                self.assertEqual(self.read()["calls"], [])

    def test_accept_uses_the_created_request_id_not_warning_numbers(self):
        self.execute("--evidence", "fixture", "--accept", "--yes")
        self.assert_pinned("6.9", "b" * 32)
        self.assertEqual(self.read()["accepted"], "12345")

    def test_ambiguous_or_missing_request_id_is_never_accepted(self):
        for error in ("missing_request_id", "multiple_request_ids"):
            with self.subTest(error=error):
                self.write({"head": "2", "advance_after_metadata": True, "calls": [], "error": error})
                self.execute("--evidence", "fixture", "--accept", "--yes", expected=2)
                self.assertIn("submitted", self.read())
                self.assertNotIn("accepted", self.read())

    def test_submit_failure_never_accepts(self):
        state = self.read()
        state["error"] = "submit"
        self.write(state)
        self.execute("--evidence", "fixture", "--accept", "--yes", expected=1)
        self.assertNotIn("accepted", self.read())

    def test_evidence_is_preserved_as_data(self):
        evidence = "Line 1\nrevision 777; $(touch NEVER) `touch NEVER`"
        self.execute("--evidence", evidence, "--yes")
        self.assertIn("Test evidence: " + evidence, self.read()["submitted"]["message"])
        self.assertFalse((self.root / "NEVER").exists())


if __name__ == "__main__":
    unittest.main()
