from __future__ import annotations

import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = ("_service", "linuxtoys.spec", "linuxtoys.changes",
         "linuxtoys-disable-self-update.patch", "linuxtoys-update-self")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="linuxtoys-pipeline-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ, PIPELINE_FIXTURE=str(self.root),
                        PIPELINE_REAL_GIT=shutil.which("git"),
                        AUTO_UPDATE_WORKDIR=str(self.root / "work"),
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "--bare", "--initial-branch=main", str(self.root / "origin.git"))
        seed = self.root / "seed"
        shutil.copytree(ROOT, seed, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        # Keep the simulated 6.7.1 -> 6.7.2 release independent of the real
        # packaged version, so this suite continues exercising an update.
        spec = seed / "linuxtoys.spec"
        packaged_version = re.search(r"^Version:\s+(\S+)", spec.read_text(), re.M)[1]
        spec.write_text(re.sub(r"^(Version:\s+).*", r"\g<1>6.7.1", spec.read_text(), flags=re.M))
        service = seed / "_service"
        service.write_text(service.read_text().replace(packaged_version, "6.7.1"))
        self.git("init", "--initial-branch=main", cwd=seed)
        self.git("config", "user.name", "Fixture", cwd=seed)
        self.git("config", "user.email", "fixture@example.invalid", cwd=seed)
        self.git("add", ".", cwd=seed)
        self.git("commit", "-m", "fixture main", cwd=seed)
        self.git("remote", "add", "origin", str(self.root / "origin.git"), cwd=seed)
        self.git("push", "origin", "main", cwd=seed)
        (self.root / "obs").mkdir()
        for name in FILES:
            shutil.copyfile(seed / name, self.root / "obs" / name)
        with tarfile.open(self.root / "release.tar.xz", "w:xz") as archive:
            entry = tarfile.TarInfo("linuxtoys-6.7.2/README")
            entry.size = 7
            archive.addfile(entry, io.BytesIO(b"fixture"))
        self.write({"release": {"tag_name": "6.7.2", "body": "- Fixture release"},
                    "prs": [], "events": [], "revision": 1})
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for tool in ("gh", "osc", "curl", "git", "patch", "rpmbuild"):
            target = bin_dir / tool
            shutil.copyfile(ROOT / "tests/pipeline_cli_fixture.py", target)
            target.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]

    def git(self, *args, cwd=None):
        return subprocess.check_output([self.env["PIPELINE_REAL_GIT"], *args], cwd=cwd,
                                       env=self.env, text=True, stderr=subprocess.PIPE).strip()

    def read(self):
        return json.loads((self.root / "state.json").read_text())

    def write(self, state):
        (self.root / "state.json").write_text(json.dumps(state))

    def execute(self, expected=0):
        script = os.environ.get("PIPELINE_SCRIPT_UNDER_TEST", str(ROOT / "scripts/auto-update-linuxtoys.sh"))
        result = subprocess.run(["bash", script], env=self.env, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def assert_delivered(self):
        state = self.read()
        self.assertEqual(len(state["prs"]), 1)
        head = state["prs"][0]["headRefOid"]
        for name in FILES:
            expected = subprocess.check_output([self.env["PIPELINE_REAL_GIT"], "--git-dir",
                                               str(self.root / "origin.git"), "show", f"{head}:{name}"])
            self.assertEqual((self.root / "obs" / name).read_bytes(), expected, name)
        body = state["prs"][0]["body"]
        self.assertIn("Sources verified in OBS staging", body)
        self.assertNotIn("pending", body)
        self.assertIn(head, body)

    def test_resume_after_each_partial_failure(self):
        # Each scenario has an independent remote and survives deletion of local runs.
        for failure in ("after_push", "pr_create", "after_pr", "osc_commit",
                        "after_osc", "obs_read", "pr_edit", "after_pr_edit"):
            with self.subTest(failure=failure):
                case = PipelineTests()
                case.setUp()
                try:
                    state = case.read()
                    state["fail"] = failure
                    case.write(state)
                    result = case.execute(2)
                    case.assertIn("injected failure: " + failure, result.stderr)
                    first = case.read()
                    if failure not in ("after_pr_edit",):
                        for pr in first["prs"]:
                            case.assertNotIn("Sources verified", pr["body"])
                    before_head = case.git("--git-dir", str(case.root / "origin.git"), "rev-parse",
                                           "refs/heads/packaging/linuxtoys-6.7.2")
                    case.execute()
                    case.assert_delivered()
                    after = case.read()
                    case.assertEqual(after["prs"][0]["headRefOid"], before_head)
                    case.assertEqual(after["events"].count("pr_created"), 1)
                    case.assertEqual(after["events"].count("obs_committed"), 1)
                    # Complete retries do not rebuild, push, create/edit PRs or commit OBS.
                    case.execute()
                    case.assertEqual(case.read()["events"], after["events"])
                finally:
                    case.doCleanups()

    def test_closed_pr_is_not_published(self):
        state = self.read()
        state["fail"] = "after_pr"
        self.write(state)
        self.assertIn("injected failure: after_pr", self.execute(2).stderr)
        state = self.read()
        state["prs"][0]["state"] = "CLOSED"
        self.write(state)
        self.assertIn("closed without merging", self.execute(2).stderr)
        self.assertNotIn("obs_committed", self.read()["events"])

    def test_pr_closed_during_validation_is_not_published(self):
        state = self.read()
        state["close_before_obs"] = True
        self.write(state)
        self.assertIn("PR closed or changed", self.execute(2).stderr)
        self.assertNotIn("obs_committed", self.read()["events"])

    def test_merged_pr_with_deleted_branch_resumes_even_when_main_is_current(self):
        state = self.read()
        state["fail"] = "after_pr"
        self.write(state)
        self.execute(2)
        state = self.read()
        state["prs"][0]["state"] = "MERGED"
        head = state["prs"][0]["headRefOid"]
        self.git("--git-dir", str(self.root / "origin.git"), "update-ref", "refs/heads/main", head)
        self.git("--git-dir", str(self.root / "origin.git"), "update-ref", "-d", "refs/heads/packaging/linuxtoys-6.7.2")
        self.write(state)
        self.execute()
        self.assert_delivered()

    def test_successful_commit_with_wrong_sources_is_not_reported_as_delivered(self):
        state = self.read()
        state["fail"] = "corrupt_obs"
        self.write(state)
        self.execute(2)
        self.assertNotIn("Sources verified", self.read()["prs"][0]["body"])
        self.execute()
        self.assert_delivered()

    def test_remote_errors_are_not_treated_as_no_existing_pr(self):
        state = self.read()
        state["fail"] = "pr_list"
        self.write(state)
        self.assertIn("injected failure: pr_list", self.execute(2).stderr)
        self.assertFalse(self.read()["events"])

    def test_replaced_tarball_is_rejected_when_resuming(self):
        state = self.read()
        state["fail"] = "after_pr"
        self.write(state)
        self.assertIn("injected failure: after_pr", self.execute(2).stderr)
        (self.root / "release.tar.xz").write_bytes(b"changed asset")
        self.assertIn("committed checksum", self.execute(2).stderr)
        self.assertNotIn("obs_committed", self.read()["events"])

    def test_validation_failure_prevents_push_and_pr(self):
        state = self.read()
        state["fail"] = "rpmbuild"
        self.write(state)
        self.assertIn("injected failure: rpmbuild", self.execute(2).stderr)
        self.assertFalse(self.read()["prs"])
        self.assertNotIn("git_pushed", self.read()["events"])

    def test_newer_staging_is_not_overwritten(self):
        spec = self.root / "obs/linuxtoys.spec"
        spec.write_text(spec.read_text().replace("6.7.1", "99.0.0"))
        self.assertIn("OBS staging is newer", self.execute(2).stderr)
        self.assertNotIn("obs_committed", self.read()["events"])

    def test_lock_preserves_other_run_and_workdir_contents(self):
        work = self.root / "work"
        work.mkdir()
        sentinel = work / "unrelated-file"
        sentinel.write_text("preserve")
        with (work / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.execute(2)
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertFalse(self.read()["events"])

    def test_branch_query_failure_is_not_treated_as_absence(self):
        state = self.read()
        state["fail"] = "branch_query"
        self.write(state)
        self.assertIn("cannot query branch", self.execute(2).stderr)
        self.assertFalse(self.read()["events"])

    def test_concurrent_delivery_does_not_make_an_empty_obs_commit(self):
        state = self.read()
        state["complete_before_checkout"] = True
        self.write(state)
        self.execute()
        self.assert_delivered()
        self.assertNotIn("obs_committed", self.read()["events"])
        self.assertIn("concurrent_delivery", self.read()["events"])

    def test_old_success_claim_becomes_pending_and_preserves_human_notes(self):
        state = self.read()
        state["fail"] = "after_pr"
        self.write(state)
        self.execute(2)
        state = self.read()
        state["prs"][0]["body"] = "Human review notes.\n\nAlso published to OBS staging (`project/package`) by this same run.\n\nRelease notes."
        state["fail"] = "patch"
        self.write(state)
        self.assertIn("injected failure: patch", self.execute(2).stderr)
        body = self.read()["prs"][0]["body"]
        self.assertIn("Human review notes.", body)
        self.assertIn("pending", body)
        self.assertNotIn("Also published", body)
        self.assertNotIn("obs_committed", self.read()["events"])

    def test_pr_head_mismatch_is_not_published(self):
        state = self.read()
        state["fail"] = "after_pr"
        self.write(state)
        self.execute(2)
        state = self.read()
        state["prs"][0]["headRefOid"] = "0" * 40
        self.write(state)
        self.assertIn("branch and PR disagree", self.execute(2).stderr)
        self.assertNotIn("obs_committed", self.read()["events"])


if __name__ == "__main__":
    unittest.main()
