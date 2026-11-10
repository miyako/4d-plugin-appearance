"""Compile actual plugin, helpers and SDK against an ownership-tracking engine."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.uname().sysname == "Darwin", "requires Apple's frameworks")
class StringOwnershipTests(unittest.TestCase):
    def test_actual_sources(self):
        source = Path(os.environ.get("APPEARANCE_SOURCE_ROOT", ROOT))
        with tempfile.TemporaryDirectory(prefix="appearance-ownership-") as directory:
            binary = Path(directory) / "ownership"
            build = subprocess.run([
                "clang++", "-std=c++11", "-x", "objective-c++",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-Wno-deprecated-declarations", "-Wno-multichar",
                "-I", str(source), "-I", str(source / "4D Plugin API"),
                "-I", str(source / "4D Plugin API/Classes"),
                str(ROOT / ".github/tests/string_ownership.mm"),
                "-framework", "Cocoa", "-framework", "CoreServices",
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=30,
                env={**os.environ, "UBSAN_OPTIONS": "halt_on_error=1"},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
