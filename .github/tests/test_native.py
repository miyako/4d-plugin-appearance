"""Run the actual workflow Bash in a closed PATH with native tools stubbed."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import textwrap
import unittest
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
SPEC = importlib.util.spec_from_file_location(
    "native_source_version", ROOT / ".github/scripts/source_version.py"
)
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def step(name):
    matches = re.findall(
        rf"^      - name: {re.escape(name)}\n"
        r"(?:(?!      - ).*\n)*?        run: \|\n"
        r"((?:          .*(?:\n|$)|[ \t]*\n)+)",
        WORKFLOW, re.MULTILINE,
    )
    if len(matches) != 1:
        raise AssertionError(f"Expected one actual run block: {name}")
    return textwrap.dedent(matches[0])


STUB = r'''
import json, os, plistlib, re, sys
from pathlib import Path
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as out:
    out.write(json.dumps([tool, *args]) + "\n")
bundle = Path(os.environ["BUNDLE_PATH"])
contents = bundle / "Contents"
mode = os.environ.get("BUILD_MODE", "complete")
if tool == "xcodebuild":
    for path in ("MacOS/Appearance", "Info.plist",
                 "Resources/English.lproj/InfoPlist.strings", "_CodeSignature"):
        assert not (contents / path).exists(), path
    assert args == [
        "-project", "Appearance.xcodeproj", "-target", "Appearance",
        "-configuration", "Default", "ARCHS=arm64 x86_64", "ONLY_ACTIVE_ARCH=NO",
        "MACOSX_DEPLOYMENT_TARGET=11.0",
        "CONFIGURATION_BUILD_DIR=" + str(Path.cwd() / "test/Plugins"), "build"]
    if mode == "failed": sys.exit(70)
    if mode != "missing-binary":
        (contents / "MacOS/Appearance").write_bytes(b"fresh native binary")
    if mode != "missing-plist":
        data = plistlib.loads(Path("Info.plist").read_bytes())
        data["CFBundleShortVersionString"] = os.environ[
            "STALE_VERSION" if mode == "stale-plist" else "SOURCE_VERSION"]
        if mode == "stale-build": data["CFBundleVersion"] = os.environ["STALE_VERSION"]
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    if mode != "missing-localized":
        data = Path("English.lproj/InfoPlist.strings").read_bytes()
        if mode == "stale-localized":
            data = data.replace(os.environ["SOURCE_VERSION"].encode(),
                                os.environ["STALE_VERSION"].encode())
        (contents / "Resources/English.lproj/InfoPlist.strings").write_bytes(data)
elif tool == "lipo":
    assert args == ["-info", str(contents / "MacOS/Appearance")]
    assert Path(args[-1]).is_file()
    print("Architectures in the fat file: " + os.environ.get("ARCHS", "arm64 x86_64"))
elif tool == "python3":
    assert args[0] in (".github/scripts/source_version.py", "-c")
    if args[0] != "-c":
        assert args == [".github/scripts/source_version.py", "verify-bundle", str(bundle)]
    os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *args])
elif tool == "security":
    assert args == ["find-identity", "-v", "-p", "codesigning", os.environ["KEYCHAIN_PATH"]]
    if os.environ.get("SIGN_MODE") != "missing":
        print('1) FAKE "Developer ID Application: Fixture (TEST)"')
elif tool == "codesign":
    verify = args[0] == "--verify"
    expected = (["--verify", "--deep", "--strict", "--verbose=2"] if verify else
                ["--verbose", "--deep", "--timestamp", "--force", "--options=runtime",
                 "--sign", "Developer ID Application: Fixture (TEST)"])
    assert args == [*expected, str(bundle)]
    if os.environ.get("SIGN_MODE") == ("verify" if verify else "sign"): sys.exit(71)
elif tool in ("ditto", "hdiutil"):
    expected = (["-c", "-k", "--keepParent", str(bundle)] if tool == "ditto" else
                ["create", "-format", "UDBZ", "-volname", "Appearance", "-srcfolder", str(bundle)])
    assert args[:-1] == expected
    if os.environ.get("PACKAGE_MODE") == tool: sys.exit(72)
    Path(args[-1]).write_bytes(b"fresh archive")
elif tool == "xcrun":
    scenario = os.environ.get("NOTARY_MODE", "accepted")
    if args[:2] == ["stapler", "staple"]:
        assert args == ["stapler", "staple", "Appearance-" + os.environ["SOURCE_VERSION"] + ".dmg"]
        if scenario == "staple": sys.exit(73)
    else:
        assert args[:2] == ["notarytool", "submit"]
        assert args[3:] == ["--keychain-profile", "notarytool-profile", "--keychain",
                           os.environ["KEYCHAIN_PATH"], "--wait", "--output-format", "json"]
        assert Path(args[2]).is_file()
        second = args[2].endswith(".dmg")
        active = scenario.removeprefix("second-") if second else scenario
        if active == "transport": sys.exit(74)
        if active == "malformed": print("bad json")
        elif active == "missing-status": print("{}")
        else:
            print(json.dumps({"id": "fixture", "status":
                              "Invalid" if active == "invalid" else "Accepted"}))
        if active == "accepted-nonzero": sys.exit(75)
else:
    raise AssertionError((tool, args))
'''


class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".native-fixture-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        for path in (*source.SOURCE_PATHS, Path(".github/scripts/source_version.py")):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        self.version = source.read(self.root)
        major, minor, patch = map(int, self.version.split("."))
        self.stale = f"{major}.{minor}.{patch + 1}"
        self.relative = "test/Plugins/Appearance.bundle"
        self.contents = self.root / self.relative / "Contents"
        self.log = self.root / "calls.jsonl"
        self.env_file = self.root / "github-env"
        self.env_file.touch()
        self.bin = self.root / "stub-bin"
        self.bin.mkdir()
        for name in ("xcodebuild", "lipo", "python3", "security", "codesign",
                     "ditto", "hdiutil", "xcrun"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(STUB))
            path.chmod(0o755)
        for name in ("rm", "grep", "head", "sed"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = {
            "PATH": str(self.bin), "REAL_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1", "CALL_LOG": str(self.log),
            "BUNDLE_PATH": self.relative, "SOURCE_VERSION": self.version,
            "STALE_VERSION": self.stale, "KEYCHAIN_PATH": "fake.keychain-db",
            "GITHUB_ENV": str(self.env_file),
        }
        self.preserved = {}
        for relative in (
            "Windows/Appearance.4DX", "Windows64/Appearance.4DX",
            "QuickLook/Thumbnail.png", "Resources/manifest.json",
            "Resources/constants.xlf", "manifest.json", "MacOS/Dependency.dylib",
            "Frameworks/Dependency.framework/Versions/A/Dependency",
        ):
            original = ROOT / self.relative / "Contents" / relative
            data = original.read_bytes() if original.exists() else b"preserved dependency"
            self.write(relative, data)
            self.preserved[relative] = data
        link = self.contents / "Frameworks/Dependency.framework/Versions/Current"
        link.symlink_to("A")
        for relative in ("MacOS/Appearance", "Info.plist",
                         "Resources/English.lproj/InfoPlist.strings", "_CodeSignature/CodeResources"):
            self.write(relative, b"stale generated output")
        self.default = self.root / "test/Plugins/Default/Other.bundle/resource"
        self.default.parent.mkdir(parents=True)
        self.default.write_bytes(b"Default sibling must survive")

    def write(self, relative, data):
        path = self.contents / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_step(self, name, prefix="", **env):
        script = step(name)
        values = {
            "env.PRODUCT_NAME": "Appearance", "env.XCODE_PROJECT": "Appearance.xcodeproj",
            "env.MACOS_DEPLOYMENT_TARGET": "11.0",
            "needs.version.outputs.version": self.version,
        }
        for key, value in values.items():
            script = script.replace("${{ " + key + " }}", value)
        self.assertNotIn("${{", script)
        return subprocess.run(
            ["/bin/bash", "-c", prefix + "\n" + script], cwd=self.root,
            env={**self.env, **env}, text=True, capture_output=True, timeout=30,
        )

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self, tool):
        return [args[1:] for line in self.log.read_text().splitlines()
                if (args := json.loads(line))[0] == tool] if self.log.exists() else []

    def build(self, **env):
        self.ok(self.run_step("build macOS plugin (universal)", **env))

    def test_build_freshness_exact_destination_and_selective_preservation(self):
        self.build()
        self.ok(self.run_step("verify universal binary"))
        for relative, data in self.preserved.items():
            self.assertEqual((self.contents / relative).read_bytes(), data, relative)
        self.assertTrue((self.contents / "Frameworks/Dependency.framework/Versions/Current").is_symlink())
        self.assertEqual(self.default.read_bytes(), b"Default sibling must survive")
        self.assertFalse((self.contents / "_CodeSignature").exists())
        self.assertEqual((self.contents / "MacOS/Appearance").read_bytes(), b"fresh native binary")
        self.assertEqual(len(self.calls("xcodebuild")), 1)

    def test_cleanup_and_build_failures_stop(self):
        result = self.run_step("build macOS plugin (universal)", prefix="rm() { return 79; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("xcodebuild"), [])
        self.assertNotEqual(self.run_step("build macOS plugin (universal)", BUILD_MODE="failed").returncode, 0)

    def test_missing_and_stale_generated_outputs_fail(self):
        for mode in ("missing-binary", "missing-plist", "missing-localized",
                     "stale-plist", "stale-build", "stale-localized"):
            with self.subTest(mode=mode):
                self.build(BUILD_MODE=mode)
                self.assertNotEqual(self.run_step("verify universal binary").returncode, 0)

    def test_exact_required_architecture_tokens_without_slice_count_restriction(self):
        self.build()
        for arch in ("arm64", "x86_64", "arm64e x86_64", "arm64 x86_64h",
                     "notarm64 x86_64", "arm64 x86_64_extra"):
            with self.subTest(arch=arch):
                self.assertNotEqual(self.run_step("verify universal binary", ARCHS=arch).returncode, 0)
        self.assertEqual(self.calls("python3"), [])
        self.ok(self.run_step("verify universal binary", ARCHS="arm64 x86_64 i386"))

    def test_compiled_xml_binary_and_utf16_metadata(self):
        self.build()
        text = (self.root / source.LOCALIZED).read_text()
        values = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)";', text))
        plist = plistlib.loads((self.contents / "Info.plist").read_bytes())
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            self.write("Info.plist", plistlib.dumps(plist, fmt=fmt))
            self.write("Resources/English.lproj/InfoPlist.strings", plistlib.dumps(values, fmt=fmt))
            self.ok(self.run_step("verify universal binary"))
            for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
                stale = {**values, key: values[key].replace(self.version, self.stale)}
                self.write("Resources/English.lproj/InfoPlist.strings", plistlib.dumps(stale, fmt=fmt))
                self.assertNotEqual(self.run_step("verify universal binary").returncode, 0)
        for encoding in ("utf-16", "utf-16-be"):
            for stale in (False, True):
                content = text.replace(self.version, self.stale) if stale else text
                data = content.encode(encoding)
                if encoding == "utf-16-be":
                    data = b"\xfe\xff" + data
                self.write("Resources/English.lproj/InfoPlist.strings", data)
                result = self.run_step("verify universal binary")
                self.assertEqual(result.returncode == 0, not stale, result.stderr)

    def test_identity_signing_and_strict_verification_are_mandatory(self):
        self.ok(self.run_step("codesign"))
        self.assertEqual(len(self.calls("codesign")), 2)
        for mode, count in (("missing", 0), ("sign", 1), ("verify", 2)):
            self.log.unlink()
            self.assertNotEqual(self.run_step("codesign", SIGN_MODE=mode).returncode, 0)
            self.assertEqual(len(self.calls("codesign")), count)

    def test_both_notaries_must_exit_zero_and_return_accepted_then_staple(self):
        for mode in (
            "accepted", "transport", "accepted-nonzero", "invalid", "malformed",
            "missing-status", "second-transport", "second-accepted-nonzero",
            "second-invalid", "second-malformed", "second-missing-status", "staple",
        ):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                self.env_file.write_text("")
                result = self.run_step("package, notarize & staple", NOTARY_MODE=mode)
                self.assertEqual(result.returncode == 0, mode == "accepted", result.stderr)
                calls = self.calls("xcrun")
                submits = [args for args in calls if args[:2] == ["notarytool", "submit"]]
                expected = 2 if mode in ("accepted", "staple") or mode.startswith("second-") else 1
                self.assertEqual(len(submits), expected)
                self.assertEqual([args[2] for args in submits], [
                    f"Appearance-{self.version}.zip", f"Appearance-{self.version}.dmg"][:expected])
                staples = [args for args in calls if args[:2] == ["stapler", "staple"]]
                self.assertEqual(len(staples), int(mode in ("accepted", "staple")))
                if mode == "accepted":
                    self.assertEqual(self.env_file.read_text().splitlines(), [
                        f"ZIP_PATH=Appearance-{self.version}.zip",
                        f"DMG_PATH=Appearance-{self.version}.dmg"])
                else:
                    self.assertEqual(self.env_file.read_text(), "")

    def test_packaging_failures_block_notaries(self):
        for tool in ("ditto", "hdiutil"):
            self.log.unlink(missing_ok=True)
            self.assertNotEqual(self.run_step("package, notarize & staple", PACKAGE_MODE=tool).returncode, 0)
            self.assertEqual(self.calls("xcrun"), [])


if __name__ == "__main__":
    unittest.main()
