"""Appearance-specific preservation evidence, valid after later source bumps."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
BASE = "69351ce1737ec926ec4d02ac19097cdb27864dae"
SPEC = importlib.util.spec_from_file_location(
    "contract_source", ROOT / ".github/scripts/source_version.py"
)
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def original(path):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"])


def block(workflow, name):
    return workflow.split(f"      - name: {name}\n", 1)[1].split(
        "\n      - name:", 1
    )[0].rstrip()


class RepositoryContractTests(unittest.TestCase):
    def test_all_unrelated_original_bytes_preserved(self):
        paths = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", BASE], text=True,
        ).splitlines()
        allowed = {*map(str, source.SOURCE_PATHS), "README.md",
                   ".github/workflows/bump-version.yml", ".github/workflows/release.yml"}
        for path in paths:
            if path not in allowed:
                with self.subTest(path=path):
                    self.assertEqual((ROOT / path).read_bytes(), original(path))

    def test_native_topology_was_and_remains_macos_only(self):
        before = original(".github/workflows/release.yml").decode()
        after = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("Windows (x64) first", before.split("name:", 1)[0])
        for workflow in (before, after):
            self.assertNotIn("runs-on: windows", workflow)
            self.assertNotIn("build-windows:", workflow)
            self.assertIn("runs-on: macos-latest", workflow)
            self.assertIn("needs: [version]", workflow)
        self.assertIn("  push:\n    tags:\n      - 'v*.*.*'", before)
        self.assertRegex(after, r"(?m)^name: Build$")
        self.assertEqual(after.split("\non:\n", 1)[1].split("\npermissions:", 1)[0].strip(),
                         "workflow_dispatch:")
        self.assertEqual(after.count("uses: actions/checkout@v4"), 2)
        self.assertEqual(after.count("ref: ${{ github.sha }}"), 2)
        self.assertIn("group: release-publication\n  cancel-in-progress: false", after)
        for forbidden in ("github.ref ==", "github.ref_name ==", "default_branch",
                          "continue-on-error:", "always()", "softprops/"):
            self.assertNotIn(forbidden, after)

    def test_original_native_settings_and_secrets_preserved(self):
        before = original(".github/workflows/release.yml").decode()
        after = (ROOT / ".github/workflows/release.yml").read_text()
        for name in ("import Developer ID certificate", "store notarytool credentials"):
            self.assertEqual(block(before, name), block(after, name))
        secrets = r"\$\{\{ secrets\.([A-Z_]+) \}\}"
        self.assertEqual(re.findall(secrets, before), re.findall(secrets, after))
        for token in (
            "PRODUCT_NAME: Appearance", "XCODE_PROJECT: Appearance.xcodeproj",
            "VCX_SOLUTION: Appearance.sln", "MACOS_DEPLOYMENT_TARGET: '11.0'",
            'ARCHS="arm64 x86_64"', "-configuration Default", "ONLY_ACTIVE_ARCH=NO",
            'MACOSX_DEPLOYMENT_TARGET="${{ env.MACOS_DEPLOYMENT_TARGET }}"',
            'codesign --verbose --deep --timestamp --force --options=runtime',
            'codesign --verify --deep --strict --verbose=2 "$BUNDLE_PATH"',
            'ditto -c -k --keepParent "$BUNDLE_PATH" "$ZIP_PATH"',
            'hdiutil create -format UDBZ -volname "${{ env.PRODUCT_NAME }}" -srcfolder "$BUNDLE_PATH" "$DMG_PATH"',
            'xcrun stapler staple "$DMG_PATH"',
        ):
            self.assertIn(token, before)
            self.assertIn(token, after)
        self.assertNotIn("CODE_SIGNING_ALLOWED=", after)
        self.assertNotIn("i386", after)
        self.assertNotIn("*.dylib", after)

    def test_actual_default_output_copy_phases_and_windows_settings(self):
        project = (ROOT / "Appearance.xcodeproj/project.pbxproj").read_text()
        # Version tokens are the only authorized project edits, even after a bump.
        normalize = lambda text: re.sub(
            r"(?m)(\b(?:MARKETING_VERSION|CURRENT_PROJECT_VERSION) = )[^;]+;",
            r"\1<VERSION>;", text,
        )
        self.assertEqual(normalize(project),
                         normalize(original("Appearance.xcodeproj/project.pbxproj").decode()))
        for setting in ('CONFIGURATION_BUILD_DIR = "$(BUILD_DIR)";',
                        'SYMROOT = "$(SRCROOT)/test/Plugins";',
                        "manifest.json in CopyFiles", "constants.xlf in CopyFiles",
                        "Cocoa.framework in Frameworks", "InfoPlist.strings in Resources",
                        "English.lproj/InfoPlist.strings", "MACOSX_DEPLOYMENT_TARGET = 11.5;"):
            self.assertIn(setting, project)
        self.assertEqual(project.count('CONFIGURATION_BUILD_DIR = "$(BUILD_DIR)";'), 3)
        self.assertEqual(project.count('SYMROOT = "$(SRCROOT)/test/Plugins";'), 3)
        scheme = ROOT / "Appearance.xcodeproj/xcshareddata/xcschemes/Appearance.xcscheme"
        self.assertEqual(scheme.read_bytes(), original(str(scheme.relative_to(ROOT))))
        tree = ET.parse(ROOT / "Appearance.vcxproj")
        ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
        self.assertEqual({node.text for node in tree.findall(".//m:PlatformToolset", ns)}, {"v140"})
        self.assertEqual(
            {node.get("Include") for node in tree.findall(".//m:ProjectConfiguration", ns)},
            {"Debug|Win32", "Debug|x64", "Release|Win32", "Release|x64"},
        )
        self.assertEqual([
            node.text for node in tree.findall(".//m:OutDir", ns)
            if node.get("Condition") == "'$(Configuration)|$(Platform)'=='Release|x64'"
        ], [r"$(ProjectDir)test\plugins\$(ProjectName).bundle\contents\windows64" + "\\"])

    def test_gate_order_and_final_recheck_after_stapling(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        names = [
            "validate source and publication availability", "build macOS plugin (universal)",
            "verify universal binary", "codesign", "package, notarize & staple",
            "final availability check and create-only publication",
        ]
        positions = [workflow.index(f"- name: {name}\n") for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('CONFIGURATION_BUILD_DIR="$PWD/$PLUGINS_DIR"', workflow)
        self.assertIn('source_version.py verify-bundle "$BUNDLE_PATH"', workflow)
        final = block(workflow, names[-1])
        self.assertLess(final.index("release.py check"), final.index("release.py publish"))
        self.assertIn("GH_TOKEN: ${{ github.token }}", final)
        self.assertIn("SELECTED_SHA: ${{ github.sha }}", final)
        for token in ('notarize "$ZIP_PATH"', 'notarize "$DMG_PATH"',
                      'if [ "$STATUS" != "Accepted" ]; then', "--output-format json"):
            self.assertIn(token, workflow)


if __name__ == "__main__":
    unittest.main()
