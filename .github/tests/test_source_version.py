import codecs
import importlib.util
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("source_version", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".source-fixture-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.copy_source()
        self.current = source.read(self.root)
        self.drift = source.next_version(self.current, "major")

    def copy_source(self):
        for name in source.SOURCE_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)

    def snapshot(self):
        return {name: ((self.root / name).read_bytes(),
                       stat.S_IMODE((self.root / name).stat().st_mode))
                for name in source.SOURCE_FILES if (self.root / name).exists()}

    def plist(self, **values):
        path = self.root / source.PLIST
        fields = plistlib.loads(path.read_bytes())
        fields.update(values)
        path.write_bytes(plistlib.dumps(fields))

    def setting(self, key, value, count=None):
        path = self.root / source.PROJECT
        content = source.text(path)
        fields = [field for field in source.assignments(content) if field[0] == key]
        if count is not None:
            fields = fields[:count]
        for _, _, start, end in reversed(fields):
            content = content[:start] + value + content[end:]
        path.write_bytes(content.encode())

    def rejected(self):
        before = self.snapshot()
        with self.assertRaises((ValueError, UnicodeError)):
            source.read(self.root)
        with patch.object(Path, "write_bytes") as write:
            with self.assertRaises((ValueError, UnicodeError)):
                source.update("patch", self.root)
            write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_live_authority_api_and_all_parts(self):
        self.assertEqual(source.SETTING_COUNT, 6)
        self.assertEqual(source.SOURCE_FILES, (
            Path("Appearance.xcodeproj/project.pbxproj"), Path("Info.plist"),
            Path("English.lproj/InfoPlist.strings"),
        ))
        self.assertEqual(source.state(self.root)["version"], self.current)
        self.assertEqual(source.state(self.root)["build_version"], self.current)
        self.assertIsNone(source.state(self.root)["bundle_version"])
        self.assertEqual(source.expected_bundle(self.root),
                         {source.SHORT: self.current})
        for part in ("patch", "minor", "major"):
            with self.subTest(part=part):
                self.copy_source()
                before = self.snapshot()
                new = source.next_version(self.current, part)
                self.assertEqual(source.update(part, self.root), new)
                self.assertEqual(source.read(self.root), new)
                self.assertNotIn(source.BUILD, plistlib.loads((self.root / source.PLIST).read_bytes()))
                for name, (original, mode) in before.items():
                    path = self.root / name
                    self.assertEqual(path.read_bytes(), original.replace(
                        self.current.encode(), new.encode()))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_authority_repair_changes_only_proven_stale_version_tokens(self):
        baseline = "69351ce1737ec926ec4d02ac19097cdb27864dae"
        version = self.current.encode()
        for name in source.SOURCE_FILES:
            original = subprocess.run(
                ["git", "show", f"{baseline}:{name}"], cwd=ROOT, check=True, capture_output=True,
            ).stdout
            if name == source.PROJECT:
                self.assertEqual(original.count(b"CURRENT_PROJECT_VERSION = 1.2.0;"), 6)
                self.assertEqual(original.count(b"MARKETING_VERSION = 1.2.3;"), 6)
                expected = original.replace(b"CURRENT_PROJECT_VERSION = 1.2.0;",
                                            b"CURRENT_PROJECT_VERSION = " + version + b";")
                expected = expected.replace(b"MARKETING_VERSION = 1.2.3;",
                                            b"MARKETING_VERSION = " + version + b";")
            elif name == source.LOCALIZED:
                self.assertEqual(original.count(b"1.2.0"), 2)
                self.assertNotIn(b"\n", original)
                expected = original.replace(b"1.2.0", version)
            else:
                expected = original
            self.assertEqual((ROOT / name).read_bytes(), expected)
            mode = subprocess.run(
                ["git", "ls-tree", baseline, "--", str(name)], cwd=ROOT, check=True,
                capture_output=True, text=True,
            ).stdout.split()[0]
            self.assertEqual(stat.S_IMODE((ROOT / name).stat().st_mode), int(mode, 8) & 0o777)

    def test_stable_coordinated_fixture(self):
        self.setting("MARKETING_VERSION", "3.2.1")
        self.setting("CURRENT_PROJECT_VERSION", "3.2.1")
        self.plist(CFBundleShortVersionString="3.2.1", CFBundleVersion="3.2.1")
        path = self.root / source.LOCALIZED
        path.write_bytes(path.read_bytes().replace(self.current.encode(), b"3.2.1"))
        self.assertEqual(source.update("minor", self.root), "3.3.0")
        current = source.state(self.root)
        self.assertEqual(current["version"], "3.3.0")
        self.assertEqual(current["bundle_version"], "3.3.0")
        self.assertEqual(current["plist"][source.BUILD], "3.3.0")

    def test_required_short_version_missing_alone_or_with_build(self):
        for keep_build in (False, True):
            with self.subTest(keep_build=keep_build):
                self.copy_source()
                path = self.root / source.PLIST
                values = plistlib.loads(path.read_bytes())
                values.pop(source.SHORT)
                if keep_build:
                    values[source.BUILD] = self.current
                path.write_bytes(plistlib.dumps(values))
                self.rejected()

    def test_optional_build_absence_and_present_literal_co_bump(self):
        for with_build in (False, True):
            self.copy_source()
            if with_build:
                self.plist(CFBundleVersion=self.current)
            new = source.update("minor", self.root)
            fields = plistlib.loads((self.root / source.PLIST).read_bytes())
            self.assertEqual(source.BUILD in fields, with_build)
            if with_build:
                self.assertEqual(fields[source.BUILD], new)

    def test_invalid_missing_extra_and_drifted_project_settings(self):
        for key in source.KEYS:
            for value in ("1.2", "14.3", "77", "v1.2.3", "01.2.3",
                          "1.2.3-beta", "$(UNKNOWN)", "${UNKNOWN}", "", self.drift):
                for count in (1, 6):
                    with self.subTest(key=key, value=value, count=count):
                        self.copy_source()
                        self.setting(key, value, count)
                        self.rejected()
            self.copy_source()
            path = self.root / source.PROJECT
            original = path.read_bytes()
            path.write_bytes(original.replace(f"{key} = {self.current};".encode(), b"", 1))
            self.rejected()

    def test_quoted_and_unquoted_inline_project_duplicates(self):
        for key in source.KEYS:
            for quoted in (False, True):
                for position in ("same-assignment", "other-assignment", "same-dictionary"):
                    for value in (self.current, self.drift, "$(UNKNOWN)"):
                        with self.subTest(key=key, quoted=quoted, position=position, value=value):
                            self.copy_source()
                            path = self.root / source.PROJECT
                            original = source.text(path)
                            name = f'"{key}"' if quoted else key
                            duplicate = f'{name} = "{value}";'
                            if position == "same-assignment":
                                anchor = f"{key} = {self.current};"
                                mutated = original.replace(anchor, anchor + " " + duplicate, 1)
                            elif position == "other-assignment":
                                mutated = original.replace(
                                    "buildSettings = {", 'buildSettings = { OTHER = "x"; ' + duplicate, 1)
                            else:
                                anchor = f"{key} = {self.current};"
                                mutated = original.replace(anchor, anchor + " " + duplicate, 1)
                                mutated = mutated.replace(anchor, "", 1)
                                mutated = mutated.replace(anchor, duplicate + " " + anchor, 1)
                            self.assertNotEqual(mutated, original)
                            path.write_bytes(mutated.encode())
                            self.rejected()

    def test_missing_terminator_unterminated_comments_and_quoted_noise(self):
        for tail in ('MARKETING_VERSION = "bad"', '/* unterminated',
                     'OTHER = "unterminated', 'CURRENT_PROJECT_VERSION = ;'):
            self.copy_source()
            path = self.root / source.PROJECT
            path.write_bytes(path.read_bytes() + tail.encode())
            self.rejected()
        self.copy_source()
        path = self.root / source.PROJECT
        original = path.read_bytes()
        noise = (
            f'\n/* MARKETING_VERSION = {self.current}; */\n'
            f'/*\rCURRENT_PROJECT_VERSION = {self.current};\r*/\n'
            f'// "MARKETING_VERSION" = {self.current};\r\n'
        ).encode()
        original = noise + original
        original = original.replace(b"buildSettings = {", (
            'buildSettings = { OTHER = "MARKETING_VERSION = '
            + self.current + '; \\"CURRENT_PROJECT_VERSION\\" = ' + self.current + ';";'
        ).encode(), 1)
        path.write_bytes(original)
        new = source.update("patch", self.root)
        self.assertTrue(path.read_bytes().startswith(noise))
        self.assertIn(f'MARKETING_VERSION = {self.current}; \\"CURRENT_PROJECT_VERSION\\"'.encode(),
                      path.read_bytes())
        self.assertEqual(source.read(self.root), new)

    def test_recursive_references_preserved(self):
        for anchor in source.KEYS:
            other = next(key for key in source.KEYS if key != anchor)
            for reference in (f"$({anchor})", f"${{{anchor}}}"):
                for plist_ref in source.REFERENCES:
                    with self.subTest(anchor=anchor, reference=reference, plist_ref=plist_ref):
                        self.copy_source()
                        self.setting(other, reference)
                        self.plist(CFBundleShortVersionString=plist_ref, CFBundleVersion=plist_ref)
                        before = self.snapshot()
                        new = source.update("patch", self.root)
                        for name, (data, _) in before.items():
                            self.assertEqual((self.root / name).read_bytes(),
                                             data.replace(self.current.encode(), new.encode()))
        for key in source.KEYS:
            for reference in (f"$({key})", f"${{{key}}}"):
                self.copy_source()
                self.setting(key, reference)
                self.rejected()
        self.copy_source()
        self.setting("MARKETING_VERSION", "$(CURRENT_PROJECT_VERSION)")
        self.setting("CURRENT_PROJECT_VERSION", "${MARKETING_VERSION}")
        self.rejected()

    def test_localized_known_references_resolve_and_stay_literal(self):
        for reference in source.REFERENCES:
            self.copy_source()
            self.setting("CURRENT_PROJECT_VERSION", "${MARKETING_VERSION}")
            path = self.root / source.LOCALIZED
            before = path.read_bytes().replace(self.current.encode(), reference.encode())
            path.write_bytes(before)
            source.update("major", self.root)
            self.assertEqual(path.read_bytes(), before)

    def test_plist_invalid_values_duplicate_keys_and_serialization(self):
        for key in (source.SHORT, source.BUILD):
            for value in ("$(UNKNOWN)", "${UNKNOWN}", "$(PRODUCT_NAME)", "", "invalid",
                          123, True, [], {}, "01.2.3", "1.2", self.drift):
                with self.subTest(key=key, value=value):
                    self.copy_source()
                    self.plist(**{key: value})
                    self.rejected()
            for value in (self.current, self.drift):
                self.copy_source()
                self.plist(**{key: self.current})
                path = self.root / source.PLIST
                path.write_bytes(path.read_bytes().replace(
                    b"</dict>", f"<key>{key}</key><string>{value}</string></dict>".encode()))
                self.rejected()
        self.copy_source()
        original = (self.root / source.PLIST).read_bytes()
        for data in (b"not a plist", b"", original.replace(b"</dict>", b""),
                     plistlib.dumps([], fmt=plistlib.FMT_XML),
                     plistlib.dumps(plistlib.loads(original), fmt=plistlib.FMT_BINARY),
                     original.replace(b"$(MARKETING_VERSION)", b"&#36;(MARKETING_VERSION)")):
            (self.root / source.PLIST).write_bytes(data)
            self.rejected()

    def test_plist_comments_nested_unrelated_fields_and_version_noise_unchanged(self):
        self.plist(CFBundleShortVersionString=self.current, UnrelatedVersion=self.current,
                   Nested={source.SHORT: self.current})
        path = self.root / source.PLIST
        original = path.read_bytes().replace(
            b"<dict>", f"<!-- <key>{source.SHORT}</key><string>{self.current}</string> -->\n<dict>".encode(), 1)
        path.write_bytes(original)
        new = source.update("major", self.root)
        expected = original.replace(
            f"<key>{source.SHORT}</key>\n\t<string>{self.current}</string>".encode(),
            f"<key>{source.SHORT}</key>\n\t<string>{new}</string>".encode(), 1)
        self.assertEqual(path.read_bytes(), expected)

    def test_localized_missing_invalid_and_inline_duplicates(self):
        original = (self.root / source.LOCALIZED).read_bytes()
        for key in (source.SHORT, source.INFO):
            for quoted in (False, True):
                for prefix in ("", 'OTHER = "unchanged"; '):
                    for value in (self.current, self.drift):
                        self.copy_source()
                        name = f'"{key}"' if quoted else key
                        path = self.root / source.LOCALIZED
                        path.write_bytes(original + f'\r{prefix}{name} = "{value}";\r'.encode())
                        self.rejected()
            self.copy_source()
            path = self.root / source.LOCALIZED
            path.write_bytes(original.replace(key.encode(), b"UnknownKey", 1))
            self.rejected()
        for old, new in ((self.current, self.drift), (self.current, "$(UNKNOWN)"),
                         ("Appearance", "Other Product"), ("Copyright 2023", "Copyright 2024")):
            self.copy_source()
            (self.root / source.LOCALIZED).write_bytes(original.replace(old.encode(), new.encode()))
            self.rejected()

    def test_quotes_whitespace_comments_and_unrelated_literals_preserved(self):
        path = self.root / source.PROJECT
        data = path.read_bytes()
        for key in source.KEYS:
            data = data.replace(f"{key} = {self.current};".encode(),
                                f'"{key}" \t= \t"{self.current}" /* leave */ \t;'.encode())
        path.write_bytes(data)
        localized = self.root / source.LOCALIZED
        data = localized.read_bytes()
        for key in (source.SHORT, source.INFO):
            data = data.replace(key.encode() + b" =", f'"{key}" \t='.encode())
        noise = f'\rOTHER = "{self.current}"; /* {source.SHORT} = "bad"; */\r'.encode()
        localized.write_bytes(data + noise)
        source.update("patch", self.root)
        self.assertTrue(localized.read_bytes().endswith(noise))
        self.assertIn(b"/* leave */", path.read_bytes())

    def test_encoding_bom_every_mixed_newline_byte_and_modes(self):
        for encoding, bom in (
            ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
            ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
        ):
            for newline in ("\n", "\r", "\r\n", "mixed"):
                with self.subTest(encoding=encoding, newline=newline):
                    self.copy_source()
                    for i, name in enumerate(source.SOURCE_FILES):
                        path = self.root / name
                        content = source.text(path)
                        if name == source.PLIST and encoding.startswith("utf-16"):
                            content = content.replace('encoding="UTF-8"', 'encoding="UTF-16"')
                        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                        content = "".join(line + (
                            ("\r", "\n", "\r\n")[j % 3] if newline == "mixed" else newline)
                            for j, line in enumerate(lines[:-1])) + lines[-1]
                        path.write_bytes(bom + content.encode(encoding))
                        path.chmod((0o755, 0o640, 0o644)[i])
                    before = self.snapshot()
                    new = source.update("minor", self.root)
                    for name, (data, mode) in before.items():
                        self.assertEqual((self.root / name).read_bytes(),
                                         data.replace(self.current.encode(encoding), new.encode(encoding)))
                        self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), mode)

    def test_missing_and_invalid_encoding_sources_do_not_partially_write(self):
        for name in source.SOURCE_FILES:
            self.copy_source()
            (self.root / name).unlink()
            before = self.snapshot()
            with self.assertRaises(FileNotFoundError):
                source.update("patch", self.root)
            self.assertEqual(self.snapshot(), before)
        self.copy_source()
        (self.root / source.LOCALIZED).write_bytes(b"\xffinvalid UTF8")
        self.rejected()

    def test_prebuilt_destinations_dependencies_and_4d_schema_untouched(self):
        excluded = (
            "test/Plugins/Appearance.bundle/Contents/Info.plist",
            "test/Plugins/Appearance.bundle/Contents/Resources/English.lproj/InfoPlist.strings",
            "test/Data/data.4DD", "test/Project/Sources/catalog.4DCatalog",
            "manifest.json", "constants.xlf", "constants.json", "include/json/version.h",
            "4D Plugin API/4DPluginAPI.h", "Appearance.vcxproj", "Appearance.sln",
            "Appearance.dmg", "Appearance.zip", "a/libjsoncpp.a",
        )
        for name in excluded:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"untouched source-independent fixture " + self.current.encode())
        before = {name: (self.root / name).read_bytes() for name in excluded}
        source.update("major", self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in excluded})

    def test_built_plist_and_localized_formats_optional_build_gate(self):
        bundle = self.root / "built.bundle"
        contents = bundle / "Contents"
        localized = contents / "Resources" / source.LOCALIZED
        localized.parent.mkdir(parents=True)
        info = contents / "Info.plist"
        fields = {source.SHORT: self.current,
                  source.INFO: source.INFO_PREFIX + self.current + source.INFO_SUFFIX}
        for with_build in (False, True):
            if with_build:
                self.plist(CFBundleVersion=self.current)
            info.write_bytes(plistlib.dumps(source.expected_bundle(self.root)))
            for data in (
                (self.root / source.LOCALIZED).read_bytes(),
                source.text(self.root / source.LOCALIZED).encode("utf-16"),
                plistlib.dumps(fields), plistlib.dumps(fields, fmt=plistlib.FMT_BINARY),
            ):
                localized.write_bytes(data)
                source.verify_bundle(bundle, self.root)
            if with_build:
                info.write_bytes(plistlib.dumps({source.SHORT: self.current}))
                with self.assertRaisesRegex(ValueError, "build version"):
                    source.verify_bundle(bundle, self.root)
        for values in ([], {source.SHORT: self.drift},
                       {source.SHORT: self.current, source.BUILD: self.drift}):
            info.write_bytes(plistlib.dumps(values))
            with self.assertRaises(ValueError):
                source.verify_bundle(bundle, self.root)
        info.write_bytes(plistlib.dumps(source.expected_bundle(self.root)))
        localized.write_bytes(plistlib.dumps({**fields, source.SHORT: self.drift}))
        with self.assertRaisesRegex(ValueError, "localized"):
            source.verify_bundle(bundle, self.root)

    def test_read_cli_and_invalid_part_are_side_effect_free(self):
        before = self.snapshot()
        for args in ([], ["read"]):
            result = subprocess.run(
                [sys.executable, str(ROOT / ".github/scripts/source_version.py"), *args],
                cwd=self.root, check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout.strip(), self.current)
        with self.assertRaises(ValueError):
            source.update("other", self.root)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
