import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("bump_source", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(path, *args, input=None):
    location = ["--git-dir", str(path)] if path.name.endswith(".git") else ["-C", str(path)]
    return subprocess.run(
        ["git", *location, *args], input=input, text=True, check=True,
        capture_output=True,
    ).stdout.strip()


class BumpGitTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".bump-fixture-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.remote = self.root / "remote.git"
        self.local = self.root / "local"
        self.peer = self.root / "peer"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "selected", str(self.local))
        for key, value in (("user.name", "Lifecycle Test"), ("user.email", "test@example.invalid"),
                           ("commit.gpgsign", "false"), ("push.followTags", "true"),
                           ("core.hooksPath", ".git/hooks")):
            git(self.local, "config", key, value)
        git(self.local, "commit", "--allow-empty", "-m", "ancestor")
        self.ancestor = git(self.local, "rev-parse", "HEAD")
        for name in source.SOURCE_FILES:
            target = self.local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        scripts = self.local / ".github/scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / ".github/scripts/source_version.py", scripts / "source_version.py")
        shutil.copytree(ROOT / ".github/scripts/bump-hooks", scripts / "bump-hooks")
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "selected source fixture")
        self.sha = git(self.local, "rev-parse", "HEAD")
        git(self.local, "remote", "add", "origin", str(self.remote))
        git(self.local, "push", "--no-follow-tags", "origin", "HEAD:refs/heads/selected")
        git(self.root, "clone", "--branch", "selected", str(self.remote), str(self.peer))
        git(self.peer, "config", "user.name", "Peer")
        git(self.peer, "config", "user.email", "peer@example.invalid")
        git(self.peer, "config", "commit.gpgsign", "false")
        self.current = source.read(self.local)
        self.context = cwd(self.local)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def remote_sha(self):
        return source.remote_ref("refs/heads/selected")

    def bump(self, part="patch"):
        return source.bump_branch(self.sha, "selected", part)

    def advance_peer(self):
        (self.peer / "peer.txt").write_text("Concurrent branch update")
        git(self.peer, "add", "peer.txt")
        git(self.peer, "commit", "-m", "peer")
        git(self.peer, "push", "--no-follow-tags", "origin", "HEAD:refs/heads/selected")
        return git(self.peer, "rev-parse", "HEAD")

    def assert_remote_unchanged(self):
        self.assertEqual(self.remote_sha(), self.sha)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_explicit_source_only_nonempty_direct_child_trailer_no_follow_tags(self):
        git(self.local, "tag", "-a", "unrelated-annotated", "-m", "must not follow")
        git(self.remote, "update-ref", "refs/heads/other", self.sha)
        real_run = source.run
        with patch.object(source, "run", wraps=real_run) as run:
            new_version = self.bump("minor")
        new = git(self.local, "rev-parse", "HEAD")
        self.assertEqual(self.remote_sha(), new)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/other"), self.sha)
        self.assertEqual(new_version, source.next_version(self.current, "minor"))
        self.assertEqual(source.read(self.local), new_version)
        self.assertEqual(git(self.local, "rev-list", "--parents", "-n", "1", new),
                         f"{new} {self.sha}")
        changed = set(git(self.local, "diff-tree", "--no-commit-id", "--name-only",
                          "-r", new).splitlines())
        self.assertEqual(changed, {str(source.PROJECT), str(source.LOCALIZED)})
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assertEqual(git(self.remote, "tag", "--list"), "")
        self.assertIn(source.TRAILER, git(self.local, "log", "-1", "--format=%B"))
        pushes = [call.args for call in run.call_args_list if "push" in call.args]
        self.assertEqual(len(pushes), 1)
        self.assertIn("push.followTags=false", pushes[0])
        self.assertIn("--no-follow-tags", pushes[0])
        self.assertEqual(pushes[0][-1], f"{new}:refs/heads/selected")
        self.assertFalse(any("--force" in str(arg) for arg in pushes[0]))

    def test_genuine_cli_does_not_create_bytecode_or_publish_tags(self):
        env = os.environ.copy()
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run(
            [sys.executable, ".github/scripts/source_version.py", "bump", "--part", "patch",
             "--expected-sha", self.sha, "--branch", "selected"],
            env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), source.next_version(self.current, "patch"))
        self.assertEqual(self.remote_sha(), git(self.local, "rev-parse", "HEAD"))
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assertFalse(list(self.local.rglob("__pycache__")))
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_immutable_checkout_clean_worktree_and_branch_validation(self):
        for sha in ("selected", "a" * 40, "a" * 39, "a" * 41, self.ancestor):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                source.bump_branch(sha, "selected", "patch")
        with self.assertRaises(subprocess.CalledProcessError):
            source.bump_branch(self.sha, "invalid..branch", "patch")
        (self.local / "untracked.txt").write_text("dirty")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            self.bump()
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
        self.assert_remote_unchanged()

    def test_cli_selected_tag_is_rejected_without_writes(self):
        result = subprocess.run(
            [sys.executable, ".github/scripts/source_version.py", "bump", "--part", "patch",
             "--expected-sha", self.sha, "--branch", "selected"],
            env={**os.environ, "GITHUB_REF_TYPE": "tag"}, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assert_remote_unchanged()

    def test_remote_moved_before_edit_is_rejected(self):
        peer = self.advance_peer()
        with self.assertRaisesRegex(ValueError, "moved before"):
            self.bump()
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
        self.assertEqual(self.remote_sha(), peer)
        self.assertEqual(git(self.local, "status", "--porcelain"), "")

    def test_remote_moved_during_preparation_is_rejected(self):
        real_update = source.update
        peer = None

        def mutate(part):
            nonlocal peer
            result = real_update(part)
            peer = self.advance_peer()
            return result

        with patch.object(source, "update", side_effect=mutate), \
                self.assertRaisesRegex(ValueError, "moved during"):
            self.bump()
        self.assertEqual(self.remote_sha(), peer)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def advertised_race(self, kind):
        real_run = source.run
        expected = None
        pushes = 0
        reads = 0

        def mutate(*args, **kwargs):
            nonlocal expected, pushes, reads
            if "ls-remote" in args:
                reads += 1
            if "push" in args:
                self.assertEqual(reads, 2, "Race must be AFTER the final read")
                pushes += 1
                if kind == "advance":
                    expected = self.advance_peer()
                elif kind == "rewind":
                    expected = self.ancestor
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                elif kind == "delete":
                    git(self.remote, "update-ref", "-d", "refs/heads/selected")
                elif kind == "same-result":
                    expected = git(self.local, "rev-parse", "HEAD")
                    git(self.remote, "fetch", "--no-tags", str(self.local), "HEAD")
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                else:
                    self.fail(kind)
            return real_run(*args, **kwargs)

        with patch.object(source, "run", side_effect=mutate), \
                self.assertRaises(subprocess.CalledProcessError) as error:
            self.bump()
        self.assertIn("Bump rejected", error.exception.stderr)
        if kind == "same-result":
            self.assertIn("exactly one advertised", error.exception.stderr)
        self.assertEqual(pushes, 1)
        self.assertEqual(self.remote_sha(), expected)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_after_final_read_before_advertisement_advance(self):
        self.advertised_race("advance")

    def test_after_final_read_before_advertisement_rewind(self):
        self.advertised_race("rewind")

    def test_after_final_read_before_advertisement_delete(self):
        self.advertised_race("delete")

    def test_after_final_read_before_advertisement_same_result_zero_updates(self):
        self.advertised_race("same-result")

    def runtime(self):
        runtime = self.root / "runtime"
        shutil.copytree(self.local / ".github/scripts", runtime)
        return runtime

    def test_missing_and_nonexecutable_hook_initially_rejected_before_edits(self):
        runtime = self.runtime()
        guard = runtime / "bump-hooks/pre-push"
        for missing in (False, True):
            if missing:
                guard.unlink()
            else:
                guard.chmod(0o644)
            before = {name: (self.local / name).read_bytes() for name in source.SOURCE_FILES}
            with self.subTest(missing=missing), \
                    patch.object(source, "__file__", str(runtime / "source_version.py")), \
                    patch.object(source, "update") as update, \
                    self.assertRaisesRegex(ValueError, "guard is missing or not executable"):
                self.bump()
            update.assert_not_called()
            self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
            self.assertEqual(before, {name: (self.local / name).read_bytes() for name in source.SOURCE_FILES})
            self.assert_remote_unchanged()

    def late_guard_loss(self, missing):
        runtime = self.runtime()
        guard = runtime / "bump-hooks/pre-push"
        real_remote_ref = source.remote_ref
        reads = 0

        def lose_guard(ref):
            nonlocal reads
            result = real_remote_ref(ref)
            reads += 1
            if reads == 2:
                if missing:
                    guard.unlink()
                else:
                    guard.chmod(0o644)
            return result

        with patch.object(source, "__file__", str(runtime / "source_version.py")), \
                patch.object(source, "remote_ref", side_effect=lose_guard), \
                self.assertRaisesRegex(ValueError, "guard is missing or not executable"):
            self.bump()
        self.assertEqual(reads, 2)
        self.assert_remote_unchanged()

    def test_missing_hook_rechecked_after_final_read_immediately_before_push(self):
        self.late_guard_loss(True)

    def test_nonexecutable_hook_rechecked_after_final_read_immediately_before_push(self):
        self.late_guard_loss(False)

    def commit_attack(self, kind):
        real_run = source.run
        pushes = 0

        def corrupt(*args, **kwargs):
            nonlocal pushes
            if "push" in args:
                pushes += 1
            if args[:2] == ("git", "commit") and kind == "non-source":
                (self.local / "unrelated.txt").write_text("not source metadata")
                git(self.local, "add", "unrelated.txt")
            result = real_run(*args, **kwargs)
            if args[:2] == ("git", "commit") and kind != "non-source":
                tree = git(self.local, "rev-parse",
                           f"{self.sha}^{{tree}}" if kind == "empty" else "HEAD^{tree}")
                parents = ["-p", self.sha]
                if kind == "multiparent":
                    parents += ["-p", self.ancestor]
                message = "bump fixture\n\n" + (
                    "Co-authored-by: Wrong <wrong@example.invalid>" if kind == "trailer"
                    else source.TRAILER) + "\n"
                if kind == "source-token":
                    path = self.local / source.PLIST
                    path.write_bytes(path.read_bytes().replace(b"com.4D.Appearance", b"com.4D.Other"))
                    git(self.local, "add", str(source.PLIST))
                    tree = git(self.local, "write-tree")
                if kind == "source-mode":
                    path = self.local / source.PLIST
                    path.chmod(0o755)
                    git(self.local, "add", str(source.PLIST))
                    tree = git(self.local, "write-tree")
                new = git(self.local, "commit-tree", tree, *parents, input=message)
                git(self.local, "update-ref", "HEAD", new)
            return result

        message = {
            "multiparent": "single-parent direct child", "non-source": "source version metadata only",
            "empty": "nonempty source", "trailer": "Copilot App trailer",
            "source-token": "differs from prepared source",
            "source-mode": "preserve source file types and modes",
        }[kind]
        with patch.object(source, "run", side_effect=corrupt), \
                self.assertRaisesRegex(ValueError, message):
            self.bump()
        self.assertEqual(pushes, 0)
        self.assert_remote_unchanged()

    def test_multiparent_bump_commit_rejected_before_push(self):
        self.commit_attack("multiparent")

    def test_non_source_bump_commit_rejected_before_push(self):
        self.commit_attack("non-source")

    def test_empty_bump_commit_rejected_before_push(self):
        self.commit_attack("empty")

    def test_missing_required_trailer_rejected_before_push(self):
        self.commit_attack("trailer")

    def test_unrelated_source_token_modification_rejected_before_push(self):
        self.commit_attack("source-token")

    def test_source_file_mode_modification_rejected_before_push(self):
        self.commit_attack("source-mode")

    def hook(self, rows, env):
        return subprocess.run(
            [str(ROOT / ".github/scripts/bump-hooks/pre-push")], input=rows, env=env,
            text=True, capture_output=True,
        )

    def test_guard_actual_rows_exact_sha_ref_one_update_head_and_single_parent(self):
        git(self.local, "commit", "--allow-empty", "-m", "direct child")
        new = git(self.local, "rev-parse", "HEAD")
        env = {**os.environ, "BUMP_EXPECTED_SHA": self.sha,
               "BUMP_EXPECTED_REF": "refs/heads/selected", "BUMP_NEW_SHA": new}
        valid = f"{new} {new} refs/heads/selected {self.sha}\n"
        self.assertEqual(self.hook(valid, env).returncode, 0)
        for rows in ("", valid * 2,
                     f"HEAD {new} refs/heads/selected {self.sha}\n",
                     f"{new} {new} refs/heads/wrong {self.sha}\n",
                     f"{new} {self.ancestor} refs/heads/selected {self.sha}\n",
                     f"{new} {new} refs/heads/selected {self.ancestor}\n",
                     f"{new} {new} refs/heads/selected {'0' * 40}\n",
                     valid.rstrip() + " extra\n"):
            with self.subTest(rows=rows):
                self.assertNotEqual(self.hook(rows, env).returncode, 0)
        self.assertNotEqual(self.hook(valid, {}).returncode, 0)
        tree = git(self.local, "rev-parse", "HEAD^{tree}")
        merge = git(self.local, "commit-tree", tree, "-p", self.sha, "-p", self.ancestor,
                    input="merge fixture\n")
        git(self.local, "update-ref", "HEAD", merge)
        env["BUMP_NEW_SHA"] = merge
        result = subprocess.run(
            ["git", "-c", f"core.hooksPath={ROOT / '.github/scripts/bump-hooks'}",
             "-c", "push.followTags=false", "push", "--porcelain", "--no-follow-tags",
             "origin", f"{merge}:refs/heads/selected"],
            env=env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("advertised branch state differs", result.stderr)
        self.assert_remote_unchanged()

    def test_post_push_exact_remote_verification_fails_closed(self):
        real_remote_ref = source.remote_ref
        reads = 0

        def wrong_final(ref):
            nonlocal reads
            actual = real_remote_ref(ref)
            reads += 1
            return self.sha if reads == 3 else actual

        with patch.object(source, "remote_ref", side_effect=wrong_final), \
                self.assertRaisesRegex(ValueError, "exact-SHA verification"):
            self.bump()
        self.assertEqual(reads, 3)
        self.assertNotEqual(self.remote_sha(), self.sha)

    def test_malformed_remote_advertisement_fails_closed(self):
        for output in ("invalid\trefs/heads/selected", "a" * 40,
                       "a" * 40 + "\trefs/heads/wrong",
                       ("a" * 40 + "\trefs/heads/selected\n") * 2):
            with patch.object(source, "run", return_value=output), self.assertRaises(ValueError):
                source.remote_ref("refs/heads/selected")


class BumpWiringTests(unittest.TestCase):
    def test_exact_name_manual_zero_inputs_immutable_branch_only(self):
        workflow = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("\nname: Bump\n", workflow)
        trigger = workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip()
        self.assertEqual(trigger, "workflow_dispatch:")
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("if: github.ref_type == 'branch'", workflow)
        self.assertIn('test "$REF_TYPE" = branch', workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn("BUMP_PART: patch", workflow)
        self.assertIn("patch, minor or major", workflow)
        self.assertIn('bump --part "$BUMP_PART" --expected-sha "$SELECTED_SHA"', workflow)
        self.assertIn("group: source-version-bump\n  cancel-in-progress: false", workflow)
        for prohibited in ("inputs:", "${{ inputs.", "git tag", "--force", "gh release",
                           "gh workflow", "workflow_call:", "release.py", "xcodebuild"):
            self.assertNotIn(prohibited, workflow)
        self.assertTrue(os.access(ROOT / ".github/scripts/bump-hooks/pre-push", os.X_OK))


if __name__ == "__main__":
    unittest.main()
