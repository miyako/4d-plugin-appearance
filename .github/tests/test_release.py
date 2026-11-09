"""Offline publication tests: no GitHub calls or mutations."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import call, patch
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
SPEC = importlib.util.spec_from_file_location("publication_release", ROOT / ".github/scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
VERSION = release.read(ROOT)
TAG = f"v{VERSION}"
REPO = "owner/repo"


def entry(tag="v0.0.1", draft=False):
    return {"id": 9, "tag_name": tag, "draft": draft, "prerelease": False}


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        token = patch.dict(os.environ, GH_TOKEN="offline-fixture-token")
        token.start()
        self.addCleanup(token.stop)

    def test_authentication_required_before_any_remote_calls(self):
        with patch.dict(os.environ, GH_TOKEN=""), patch.object(release, "run") as run:
            with self.assertRaisesRegex(ValueError, "Authenticated"):
                release.available(REPO, TAG)
            run.assert_not_called()

    def test_all_pages_and_drafts(self):
        for draft in (True, False):
            with self.subTest(draft=draft), \
                    patch.object(release, "remote_ref", return_value=None), \
                    patch.object(release, "api", side_effect=[
                        [entry()] * 100, [entry(TAG, draft)],
                    ]) as api:
                with self.assertRaisesRegex(ValueError, "draft or published"):
                    release.available(REPO, TAG)
                self.assertEqual(api.call_args_list, [
                    call(f"repos/{REPO}/releases?per_page=100&page=1"),
                    call(f"repos/{REPO}/releases?per_page=100&page=2"),
                ])
        with patch.object(release, "remote_ref", return_value=None), \
                patch.object(release, "api", side_effect=[
                    [entry()] * 100, [entry()] * 100, [],
                ]) as api:
            release.available(REPO, TAG)
            self.assertEqual(api.call_count, 3)

    def test_every_page_errors_and_malformed_fail_closed(self):
        errors = [
            *(subprocess.CalledProcessError(1, ["gh", str(code)])
              for code in (401, 403, 404, 422, 500)),
            OSError("network"), json.JSONDecodeError("bad", "?", 0),
        ]
        malformed = [
            {}, None, "[]", [None], [{}], [dict(entry(), id=True)],
            [dict(entry(), id=0)], [dict(entry(), id=-1)],
            [dict(entry(), tag_name=None)], [dict(entry(), tag_name="")],
            [dict(entry(), draft=0)], [dict(entry(), prerelease="false")],
            [entry()] * 101,
        ]
        for page in (1, 2, 3):
            for response in errors + malformed:
                with self.subTest(page=page, response=response), \
                        patch.object(release, "remote_ref", return_value=None), \
                        patch.object(release, "api", side_effect=(
                            [[entry()] * 100] * (page - 1) + [response]
                        )) as api:
                    with self.assertRaises((ValueError, OSError, subprocess.CalledProcessError)):
                        release.available(REPO, TAG)
                    self.assertEqual(api.call_count, page)

    def test_exact_remote_tag_and_malformed_remote(self):
        ref = f"refs/tags/{TAG}"
        with patch.object(release, "run", return_value=f"{SHA}\t{ref}") as run:
            self.assertEqual(release.remote_ref(ref), SHA)
            run.assert_called_once_with("git", "ls-remote", "--refs", "origin", ref)
        for output in ("invalid", f"{SHA}\t{ref}-other", f"bad\t{ref}",
                       f"{SHA}\t{ref}\n{SHA}\t{ref}"):
            with self.subTest(output=output), patch.object(release, "run", return_value=output):
                with self.assertRaises(ValueError):
                    release.remote_ref(ref)
        with patch.object(release, "remote_ref", return_value=SHA), \
                patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.available(REPO, TAG)
            api.assert_not_called()
        with patch.object(release, "run", side_effect=OSError("network")), \
                patch.object(release, "api") as api:
            with self.assertRaises(OSError):
                release.available(REPO, TAG)
            api.assert_not_called()

    def test_transport_explicit_methods_no_retry(self):
        with patch.object(release, "run", return_value="[]") as run:
            release.api("repos/o/r/releases?per_page=100&page=2")
            run.assert_called_once_with(
                "gh", "api", "--method", "GET",
                "repos/o/r/releases?per_page=100&page=2", input=None,
            )
            payload = {"ref": f"refs/tags/{TAG}", "sha": SHA}
            release.api("repos/o/r/git/refs", payload)
            self.assertIn("POST", run.call_args.args)
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), payload)
            release.api("https://uploads.github.com/repos/o/r/releases/123/assets",
                        asset=Path("asset.zip"))
            self.assertEqual(run.call_args.args[-4:], (
                "--header", "Content-Type: application/octet-stream", "--input", "asset.zip",
            ))
        with patch.object(release, "run", side_effect=subprocess.CalledProcessError(1, "gh")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.api("repos/o/r/releases")
            self.assertEqual(run.call_count, 1)

    def test_context_validates_selected_checkout_repository_and_source(self):
        with patch.dict(os.environ, GITHUB_REPOSITORY=REPO), \
                patch.object(release, "run", side_effect=[SHA, "", ""]), \
                patch.object(release, "read", return_value=VERSION):
            self.assertEqual(release.context(SHA), (REPO, VERSION, TAG))
        for sha in ("main", SHA.upper(), "a" * 39):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                release.selected_sha(sha)
        for repo in ("owner", "owner/../repo", "owner/repo?x"):
            with patch.dict(os.environ, GITHUB_REPOSITORY=repo), \
                    patch.object(release, "run", side_effect=[SHA, "", ""]), \
                    patch.object(release, "read", return_value=VERSION), \
                    self.assertRaises(ValueError):
                release.context(SHA)
        with patch.dict(os.environ, GITHUB_REPOSITORY=REPO), \
                patch.object(release, "run", return_value="b" * 40):
            with self.assertRaisesRegex(ValueError, "Checkout"):
                release.context(SHA)
        with patch.object(release, "run", side_effect=[SHA, "Info.plist"]):
            with self.assertRaisesRegex(ValueError, "Source differs"):
                release.context(SHA)

    def test_source_version_is_canonical_and_index_is_guarded(self):
        for version in ("01.2.3", "1.02.3", "1.2.03", "1.2.3-beta", "v1.2.3", "1.2"):
            with self.subTest(version=version), \
                    patch.object(release, "run", side_effect=[SHA, "", ""]), \
                    patch.object(release, "read", return_value=version), \
                    self.assertRaisesRegex(ValueError, "canonical"):
                release.context(SHA)
        with patch.object(release, "run", side_effect=[SHA, "", "Info.plist"]):
            with self.assertRaisesRegex(ValueError, "Source differs"):
                release.context(SHA)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        # All fixture files stay under the worktree, never a system temp directory.
        self.directory = ROOT / ".github/tests" / (".release-fixture-" + uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        previous = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, previous)
        self.assets = [Path(f"Appearance-{VERSION}.zip"), Path(f"Appearance-{VERSION}.dmg")]
        for asset in self.assets:
            asset.write_bytes(b"fixture asset")
        self.calls = []
        self.tag = None
        self.published = None
        self.uploaded = []
        self.fail = None
        self.race = None
        self.bad_kind = None
        self.bad_result = None
        self.pages = [[]]
        self.start_patch(patch.dict(os.environ, GH_TOKEN="offline-fixture-token"))
        self.start_patch(patch.object(release, "context", return_value=(REPO, VERSION, TAG)))
        self.start_patch(patch.object(release, "run", return_value=""))
        self.start_patch(patch.object(release, "remote_ref", side_effect=lambda ref: self.tag))
        self.start_patch(patch.object(release, "api", side_effect=self.api))
        self.start_patch(patch("sys.stdout", new_callable=io.StringIO))

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def api(self, endpoint, payload=None, asset=None):
        self.calls.append((endpoint, payload, asset))
        if "/releases?" in endpoint:
            page = int(endpoint.rsplit("=", 1)[1])
            result = self.pages[page - 1]
            if isinstance(result, Exception):
                raise result
            return result
        if endpoint.endswith("/git/refs"):
            kind = "tag"
            if self.race:
                self.tag = self.race
            if self.tag is not None or self.fail == kind:
                raise subprocess.CalledProcessError(1, ["gh", "api", "422"])
            self.tag = payload["sha"]
            result = {"ref": payload["ref"], "object": {"sha": self.tag, "type": "commit"}}
        elif endpoint.endswith("/releases"):
            kind = "release"
            if self.fail == kind:
                raise subprocess.CalledProcessError(1, ["gh", "api"])
            self.published = {"id": 123, **payload}
            result = self.published
        elif asset is not None:
            kind = asset.suffix
            if self.fail == kind:
                raise subprocess.CalledProcessError(1, ["gh", "api"])
            self.uploaded.append(asset.name)
            result = {"id": 456, "name": asset.name, "size": asset.stat().st_size,
                      "state": "uploaded"}
        else:
            raise AssertionError(endpoint)
        return self.bad_result if self.bad_kind == kind else result

    def publish(self):
        release.publish(SHA, self.assets)

    def test_create_only_order_payload_and_returned_release_id(self):
        self.publish()
        self.assertEqual(self.tag, SHA)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[1], (
            f"repos/{REPO}/git/refs", {"ref": f"refs/tags/{TAG}", "sha": SHA}, None,
        ))
        self.assertEqual(self.calls[2], (f"repos/{REPO}/releases", {
            "tag_name": TAG, "target_commitish": SHA, "name": TAG,
            "draft": False, "prerelease": False, "body": "",
            "generate_release_notes": False,
        }, None))
        for endpoint, payload, asset in self.calls[3:]:
            self.assertTrue(endpoint.startswith(
                f"https://uploads.github.com/repos/{REPO}/releases/123/assets?name="))
            self.assertNotIn(" ", endpoint)
            self.assertNotIn("#", endpoint)
            self.assertIsNone(payload)
            self.assertIn(asset, self.assets)
        self.assertEqual(self.uploaded, [asset.name for asset in self.assets])

    def test_same_and_different_sha_tag_races_fail_without_reuse(self):
        for sha in (SHA, "b" * 40):
            with self.subTest(sha=sha):
                self.calls.clear()
                self.tag = None
                self.race = sha
                with self.assertRaises(subprocess.CalledProcessError):
                    self.publish()
                self.assertEqual(self.tag, sha)
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.published)
                self.assertEqual(self.uploaded, [])

    def test_each_post_failure_retains_all_prior_state_without_retry(self):
        for kind, count, tag, published, uploaded in (
            ("tag", 2, None, False, []),
            ("release", 3, SHA, False, []),
            (".zip", 4, SHA, True, []),
            (".dmg", 5, SHA, True, [self.assets[0].name]),
        ):
            with self.subTest(kind=kind):
                self.calls.clear()
                self.tag, self.published, self.uploaded = None, None, []
                self.fail = kind
                with self.assertRaises(subprocess.CalledProcessError):
                    self.publish()
                self.assertEqual(len(self.calls), count)
                self.assertEqual(self.tag, tag)
                self.assertEqual(self.published is not None, published)
                self.assertEqual(self.uploaded, uploaded)

    def test_final_recheck_rejects_new_draft_and_later_page_errors(self):
        for result in ([entry(TAG, True)], {"message": "bad"},
                       subprocess.CalledProcessError(1, ["gh", "404"])):
            with self.subTest(result=result):
                self.calls.clear()
                self.pages = [[]]
                release.preflight(SHA)
                self.calls.clear()
                self.pages = [[entry()] * 100, result]
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.publish()
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.tag)
                self.assertIsNone(self.published)

    def test_final_recheck_rejects_new_remote_tag(self):
        release.preflight(SHA)
        self.calls.clear()
        self.tag = SHA
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_remote_verification_failure_never_creates_release(self):
        for response in (None, "b" * 40, OSError("network"), ValueError("malformed")):
            with self.subTest(response=response):
                self.tag = None
                self.calls.clear()
                with patch.object(release, "remote_ref", side_effect=[None, response]):
                    with self.assertRaises((ValueError, OSError)):
                        self.publish()
                self.assertEqual(self.tag, SHA)
                self.assertEqual(len(self.calls), 2)
                self.assertIsNone(self.published)

    def test_malformed_mutation_responses_stop_and_retain_created_state(self):
        tag_response = {"ref": f"refs/tags/{TAG}", "object": {"sha": SHA, "type": "commit"}}
        release_response = {"id": 123, "tag_name": TAG, "target_commitish": SHA,
                            "name": TAG, "draft": False, "prerelease": False, "body": ""}
        asset_response = {"id": 456, "name": self.assets[0].name, "size": 13, "state": "uploaded"}
        for kind, responses, count in (
            ("tag", [None, {}, dict(tag_response, ref="refs/tags/wrong"),
                     dict(tag_response, object={"sha": "b" * 40, "type": "commit"}),
                     dict(tag_response, object={"sha": SHA, "type": "tag"})], 2),
            ("release", [None, {}, dict(release_response, id=True),
                         dict(release_response, id=0), dict(release_response, draft=True),
                         dict(release_response, prerelease=True),
                         dict(release_response, tag_name="v9.9.9"),
                         dict(release_response, name="wrong"),
                         dict(release_response, target_commitish="b" * 40),
                         dict(release_response, body="unexpected generated notes")], 3),
            (".zip", [None, {}, dict(asset_response, id=True), dict(asset_response, size=True),
                      dict(asset_response, size=1), dict(asset_response, name="wrong"),
                      dict(asset_response, state="new")], 4),
        ):
            for response in responses:
                with self.subTest(kind=kind, response=response):
                    self.tag, self.published, self.uploaded = None, None, []
                    self.calls.clear()
                    self.bad_kind, self.bad_result = kind, response
                    with self.assertRaises(ValueError):
                        self.publish()
                    self.assertEqual(len(self.calls), count)
                    self.assertEqual(self.tag, SHA)
                    self.assertEqual(self.published is not None, kind != "tag")
                    self.assertEqual(self.uploaded, [self.assets[0].name] if kind == ".zip" else [])

    def test_invalid_assets_fail_before_remote_operations(self):
        for assets in ([], [self.assets[0], self.assets[0]], [self.directory / "missing.zip"],
                       [self.directory]):
            with self.subTest(assets=assets), patch.object(release, "remote_ref") as remote:
                with self.assertRaises(ValueError):
                    release.publish(SHA, assets)
                remote.assert_not_called()
                self.assertEqual(self.calls, [])
        self.assets[0].write_bytes(b"")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.calls, [])

    def test_boolean_upload_size_is_not_an_integer(self):
        self.assets[0].write_bytes(b"x")
        self.bad_kind = ".zip"
        self.bad_result = {"id": 456, "name": self.assets[0].name,
                           "size": True, "state": "uploaded"}
        with self.assertRaisesRegex(ValueError, "Malformed upload response"):
            self.publish()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.tag, SHA)
        self.assertIsNotNone(self.published)
        self.assertEqual(self.uploaded, [self.assets[0].name])


class CLITests(unittest.TestCase):
    def test_preflight_check_and_publish_interfaces(self):
        for command in ("preflight", "check"):
            with self.subTest(command=command), \
                    patch.object(release, command, return_value=(VERSION, TAG)) as check, \
                    patch.dict(os.environ, GITHUB_OUTPUT="fixture-output"), \
                    patch("builtins.open", unittest.mock.mock_open()) as output:
                release.main([command, "--sha", SHA])
                check.assert_called_once_with(SHA)
                output.assert_called_once_with("fixture-output", "a")
                output().write.assert_called_once_with(f"version={VERSION}\ntag={TAG}\n")
        with patch.object(release, "publish") as publish:
            release.main(["publish", "--sha", SHA, "first.zip", "second.dmg"])
            publish.assert_called_once_with(SHA, [Path("first.zip"), Path("second.dmg")])
        for args in (["bump", "--sha", SHA], ["check", "--sha", SHA, "asset.zip"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    release.main(args)

    def test_explicit_error_retention_message_and_nonzero_status(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / ".github/scripts/release.py"),
             "publish", "--sha", "invalid"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stopped without rollback/retry", result.stderr)
        self.assertIn("remote tag, release and uploaded assets are retained", result.stderr)


FAKE_REMOTE = r'''
import json, os, sys
from pathlib import Path
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
state_file = Path(os.environ["REMOTE_STATE"])
state = json.loads(state_file.read_text())
if tool == "git":
    if args[:1] == ["ls-remote"]:
        assert args == ["ls-remote", "--refs", "origin", "refs/tags/" + os.environ["TEST_TAG"]]
        if state["tag"]: print(state["tag"] + "\t" + args[-1])
        sys.exit(0)
    os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *args])
assert tool == "gh" and args[:2] == ["api", "--method"]
method, endpoint = args[2:4]
with open(os.environ["API_LOG"], "a") as log:
    log.write(json.dumps([method, endpoint]) + "\n")
if method == "GET":
    assert endpoint == "repos/owner/repo/releases?per_page=100&page=1"
    print("[]")
    sys.exit(0)
assert method == "POST"
if endpoint.endswith("/git/refs"):
    payload = json.load(sys.stdin)
    assert payload == {"ref": "refs/tags/" + os.environ["TEST_TAG"], "sha": os.environ["TEST_SHA"]}
    assert state["tag"] is None
    state["tag"] = payload["sha"]
    response = {"ref": payload["ref"], "object": {"sha": state["tag"], "type": "commit"}}
elif endpoint.endswith("/releases"):
    payload = json.load(sys.stdin)
    assert payload == {
        "tag_name": os.environ["TEST_TAG"], "name": os.environ["TEST_TAG"],
        "target_commitish": os.environ["TEST_SHA"], "body": "",
        "draft": False, "prerelease": False, "generate_release_notes": False}
    state["release"] = {"id": 789, **payload}
    response = state["release"]
else:
    assert endpoint.startswith("https://uploads.github.com/repos/owner/repo/releases/789/assets?name=")
    assert args[4:] == ["--header", "Content-Type: application/octet-stream", "--input", args[-1]]
    asset = Path(args[-1])
    assert asset.name == "Appearance-" + os.environ["TEST_TAG"][1:] + asset.suffix
    state["assets"].append(asset.name)
    response = {"id": 890, "name": asset.name, "size": asset.stat().st_size, "state": "uploaded"}
state_file.write_text(json.dumps(state))
print(json.dumps(response))
'''


class RealGitGeneratedOutputTests(unittest.TestCase):
    """CLI regression: tracked build output must not make final checks fail."""

    def setUp(self):
        self.root = ROOT / (".release-git-fixture-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.git = shutil.which("git")
        for path in (*release.GENERATED_FILES,
                     f"{release.BUNDLE}/_CodeSignature/CodeResources",
                     *map(str, __import__("source_version").SOURCE_PATHS),
                     "4DPlugin-Appearance.cpp", "4DPlugin-Appearance.h",
                     "manifest.json", "constants.xlf",
                     f"{release.BUNDLE}/Resources/manifest.json",
                     ".github/scripts/source_version.py", ".github/scripts/release.py"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        self.env = {
            "PATH": os.defpath, "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.git_run("init", "--quiet")
        self.git_run("add", ".")
        self.git_run("commit", "--quiet", "-m", "offline fixture")
        self.sha = self.git_run("rev-parse", "HEAD").strip()
        self.bin = self.root / "stub-bin"
        self.bin.mkdir()
        for name in ("gh", "git"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + FAKE_REMOTE)
            path.chmod(0o755)
        self.state = self.root / "remote.json"
        self.state.write_text(json.dumps({"tag": None, "release": None, "assets": []}))
        self.api_log = self.root / "api.jsonl"
        self.env.update({
            "PATH": str(self.bin), "REAL_GIT": self.git,
            "REMOTE_STATE": str(self.state), "API_LOG": str(self.api_log),
            "GITHUB_REPOSITORY": REPO, "GH_TOKEN": "offline-fixture-token",
            "TEST_SHA": self.sha, "TEST_TAG": TAG,
        })
        self.generated = {}
        for index, name in enumerate((*release.GENERATED_FILES,
                                     f"{release.BUNDLE}/_CodeSignature/CodeResources")):
            data = b"fresh signed native output\0" + str(index).encode()
            (self.root / name).write_bytes(data)
            self.generated[name] = data
        self.assets = [f"Appearance-{VERSION}.zip", f"Appearance-{VERSION}.dmg"]
        for name in self.assets:
            (self.root / name).write_bytes(b"packaged signed native output")

    def git_run(self, *args):
        return subprocess.check_output(
            [self.git, *args], cwd=self.root, env=self.env, text=True,
        )

    def cli(self, command):
        return subprocess.run(
            [sys.executable, ".github/scripts/release.py", command, "--sha", self.sha,
             *(self.assets if command == "publish" else [])],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=30,
        )

    def test_final_check_and_publish_keep_exact_tracked_native_bytes(self):
        self.assertIn(" M test/Plugins/Appearance.bundle/Contents/", self.git_run("status", "--porcelain"))
        for command in ("check", "publish"):
            result = self.cli(command)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, data in self.generated.items():
                self.assertEqual((self.root / name).read_bytes(), data, name)
        remote = json.loads(self.state.read_text())
        self.assertEqual(remote["tag"], self.sha)
        self.assertEqual(remote["release"]["id"], 789)
        self.assertEqual(remote["assets"], self.assets)
        self.assertEqual(len(self.api_log.read_text().splitlines()), 6)

    def test_unstaged_staged_and_index_cancelled_source_changes_fail_before_api(self):
        path = self.root / "Info.plist"
        before = path.read_bytes()
        for mode in ("unstaged", "staged", "index-cancelled"):
            with self.subTest(mode=mode):
                path.write_bytes(before + b"\n")
                if mode != "unstaged":
                    self.git_run("add", "Info.plist")
                if mode == "index-cancelled":
                    path.write_bytes(before)
                for command in ("preflight", "check", "publish"):
                    result = self.cli(command)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Source differs", result.stderr)
                    self.assertFalse(self.api_log.exists())
                path.write_bytes(before)
                self.git_run("add", "Info.plist")

    def test_changed_head_fails_before_api(self):
        self.git_run("commit", "--quiet", "--allow-empty", "-m", "moved fixture HEAD")
        for command in ("preflight", "check", "publish"):
            result = self.cli(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Checkout does not match", result.stderr)
            self.assertFalse(self.api_log.exists())

    def test_nonversion_build_inputs_are_guarded_before_any_api(self):
        for name in (
            "4DPlugin-Appearance.cpp", "4DPlugin-Appearance.h",
            "manifest.json", "constants.xlf",
            f"{release.BUNDLE}/Resources/manifest.json",
        ):
            path = self.root / name
            before = path.read_bytes()
            for mode in ("unstaged", "staged", "index-cancelled"):
                with self.subTest(path=name, mode=mode):
                    path.write_bytes(before + b"\n")
                    if mode != "unstaged":
                        self.git_run("add", "--", name)
                    if mode == "index-cancelled":
                        path.write_bytes(before)
                    for command in ("preflight", "check", "publish"):
                        result = self.cli(command)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("Source differs", result.stderr)
                        self.assertFalse(self.api_log.exists())
                    path.write_bytes(before)
                    self.git_run("add", "--", name)


if __name__ == "__main__":
    unittest.main()
