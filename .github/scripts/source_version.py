"""Coordinated source versions and guarded, branch-only manual bumps.

The original Bump header coordinates both Xcode version settings and source
Info.plist version fields. Built products, dependencies and 4D data are not inputs.
"""

import argparse
import codecs
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from xml.parsers.expat import ExpatError


sys.dont_write_bytecode = True
PROJECT = Path("Appearance.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
LOCALIZED = Path("English.lproj/InfoPlist.strings")
SOURCE_FILES = (PROJECT, PLIST, LOCALIZED)
SOURCE_PATHS = SOURCE_FILES
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
SETTING_COUNT = 6
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
REFERENCES = {spelling: key for key in KEYS
              for spelling in (f"$({key})", f"${{{key}}}")}
SHORT = "CFBundleShortVersionString"
BUILD = "CFBundleVersion"
INFO = "CFBundleGetInfoString"
INFO_PREFIX = "Appearance version "
INFO_SUFFIX = ", Copyright 2023 miyako."
TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<comment>/\*.*?\*/|//[^\r\n]*)|'
    r'(?P<quoted>"(?:\\.|[^"\\])*")|'
    r'(?P<reference>\$\([^()\r\n]*\)|\$\{[^{}\r\n]*\})|'
    r'(?P<word>[^\s"=;{}(),]+)|(?P<punct>[=;{}(),])',
    re.DOTALL,
)
XML_TOKEN = re.compile(r"<!--.*?-->|<[^>]*>|[^<]+", re.DOTALL)


class UniqueDictionary(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError(f"Duplicate plist key: {key}")
        super().__setitem__(key, value)


def decode(data):
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding), encoding, bom
    return data.decode("utf-8"), "utf-8", b""


def text(path):
    return decode(Path(path).read_bytes())[0]


def unquote(value):
    if value.startswith('"') and value.endswith('"'):
        return re.sub(r"\\(.)", r"\1", value[1:-1], flags=re.DOTALL)
    return value


def tokens(value):
    result = []
    offset = 0
    while offset < len(value):
        match = TOKEN.match(value, offset)
        if match is None:
            raise ValueError(f"Unsupported source token at offset {offset}")
        if match.lastgroup not in ("space", "comment"):
            result.append(match)
        offset = match.end()
    return result


def scalar(token):
    start, end = token.span()
    if token.lastgroup == "quoted":
        start, end = start + 1, end - 1
    return unquote(token[0]), start, end


def assignments(value, *, localized=False):
    """Parse OpenStep syntax, retaining only actual scalar assignment spans."""
    stream = tokens(value)
    result = []
    offset = 0

    def consume(wanted):
        nonlocal offset
        if offset >= len(stream) or stream[offset][0] != wanted:
            raise ValueError(f"Expected {wanted!r} in source metadata")
        offset += 1

    def item():
        nonlocal offset
        if offset >= len(stream):
            raise ValueError("Missing assignment value")
        token = stream[offset]
        if token[0] == "{":
            offset += 1
            dictionary("}")
            consume("}")
            return None
        if token[0] == "(":
            offset += 1
            while offset < len(stream) and stream[offset][0] != ")":
                item()
                if offset < len(stream) and stream[offset][0] != ")":
                    consume(",")
            consume(")")
            return None
        if token.lastgroup not in ("quoted", "word", "reference"):
            raise ValueError("Invalid scalar assignment")
        offset += 1
        return token

    def dictionary(end=None):
        nonlocal offset
        seen = set()
        while offset < len(stream) and (end is None or stream[offset][0] != end):
            key_token = stream[offset]
            if key_token.lastgroup not in ("quoted", "word"):
                raise ValueError("Invalid source dictionary key")
            key = unquote(key_token[0])
            if key in seen:
                raise ValueError(f"Duplicate source assignment: {key}")
            seen.add(key)
            offset += 1
            consume("=")
            token = item()
            consume(";")
            if token is not None:
                result.append((key, *scalar(token)))
            elif localized or key in KEYS:
                raise ValueError(f"{key} must have a scalar source value")

    if not localized and stream and stream[0][0] == "{":
        offset += 1
        dictionary("}")
        consume("}")
    else:
        dictionary()
    if offset != len(stream):
        raise ValueError("Trailing source metadata tokens")
    return result


def load_plist(data):
    try:
        result = plistlib.loads(data, dict_type=UniqueDictionary)
    except (plistlib.InvalidFileException, ExpatError, UnicodeError) as exc:
        raise ValueError("Invalid source plist serialization") from exc
    if not isinstance(result, dict):
        raise ValueError("Info.plist must be a dictionary")
    return result


def plist_spans(value, fields):
    """Locate root XML string values, never matching keys in comments/nested data."""
    stack = []
    pending = None
    key_start = None
    string_start = None
    found = {}
    for match in XML_TOKEN.finditer(value):
        token = match[0]
        if token.startswith(("<!--", "<?", "<!")):
            continue
        if token.startswith("</"):
            name = token[2:-1].strip()
            if name == "key" and stack == ["plist", "dict", "key"]:
                pending = value[key_start:match.start()]
            if name == "string" and stack == ["plist", "dict", "string"]:
                if pending in (SHORT, BUILD):
                    raw = value[string_start:match.start()]
                    if pending in found or fields.get(pending) != raw:
                        raise ValueError(f"{pending} has unsupported XML serialization")
                    found[pending] = (pending, raw, string_start, match.start())
                pending = None
            if not stack or stack.pop() != name:
                raise ValueError("Invalid XML element nesting")
        elif token.startswith("<"):
            name = token[1:-1].strip().rstrip("/").strip()
            name = name.split()[0]
            if stack == ["plist", "dict"]:
                if name == "key":
                    key_start = match.end()
                elif name == "string":
                    string_start = match.end()
                else:
                    pending = None
            if not token.endswith("/>"):
                stack.append(name)
    expected = {key for key in (SHORT, BUILD) if key in fields}
    if set(found) != expected or SHORT not in fields:
        raise ValueError("CFBundleShortVersionString is required; version fields must be XML strings")
    return list(found.values())


def _state(data):
    decoded = {path: decode(data[path])[0] for path in SOURCE_FILES}
    settings = [field for field in assignments(decoded[PROJECT]) if field[0] in KEYS]
    grouped = {key: [field[1] for field in settings if field[0] == key] for key in KEYS}
    if any(len(values) != SETTING_COUNT for values in grouped.values()):
        raise ValueError(f"Expected {SETTING_COUNT} source settings for each coordinated version key")

    def resolve(value, trail=()):
        if value in REFERENCES:
            key = REFERENCES[value]
            if key in trail:
                raise ValueError("Cyclic source version reference")
            resolved = {resolve(other, (*trail, key)) for other in grouped[key]}
            if len(resolved) != 1:
                raise ValueError(f"{key} has drifted across configurations")
            return next(iter(resolved))
        if not re.fullmatch(VERSION, value):
            raise ValueError("Version must be strict X.Y.Z or a known, resolved reference")
        return value

    versions = {resolve(value, (key,)) for key, values in grouped.items() for value in values}
    if len(versions) != 1:
        raise ValueError("Coordinated source versions have drifted")
    version = next(iter(versions))
    plist = load_plist(data[PLIST])
    plist_fields = plist_spans(decoded[PLIST], plist)
    for key, value, _, _ in plist_fields:
        if not isinstance(value, str) or resolve(value) != version:
            raise ValueError(f"{key} is invalid, unresolved or has drifted")
    localized = assignments(decoded[LOCALIZED], localized=True)
    fields = {field[0]: field for field in localized}
    if SHORT not in fields or INFO not in fields:
        raise ValueError("Localized display versions are required")
    short = fields[SHORT]
    if resolve(short[1]) != version:
        raise ValueError("Localized short version has drifted")
    info = fields[INFO]
    if not info[1].startswith(INFO_PREFIX) or not info[1].endswith(INFO_SUFFIX):
        raise ValueError("Localized product/copyright metadata has drifted")
    display = info[1][len(INFO_PREFIX):-len(INFO_SUFFIX)]
    if resolve(display) != version:
        raise ValueError("Localized info version has drifted")
    # Escaped version serialization cannot be edited safely using raw token spans.
    for field in settings + localized:
        if field[0] in (*KEYS, SHORT, INFO) and decoded[
            PROJECT if field in settings else LOCALIZED
        ][field[2]:field[3]] != field[1]:
            raise ValueError("Unsupported escaped source version serialization")
    info_span = (INFO, display, info[2] + len(INFO_PREFIX), info[3] - len(INFO_SUFFIX))
    return {
        "version": version,
        "build_version": version,
        "bundle_version": version if BUILD in plist else None,
        "plist": plist,
        "_spans": {PROJECT: settings, PLIST: plist_fields, LOCALIZED: [short, info_span]},
    }


def state(root=Path(".")):
    return _state({path: (Path(root) / path).read_bytes() for path in SOURCE_FILES})


def read(root=Path(".")):
    return state(root)["version"]


def next_version(version, part):
    if part not in ("major", "minor", "patch"):
        raise ValueError("Unknown version part")
    index = ("major", "minor", "patch").index(part)
    parts = list(map(int, version.split(".")))
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    return ".".join(map(str, parts))


def update(part, root=Path(".")):
    root = Path(root)
    original = {path: (root / path).read_bytes() for path in SOURCE_FILES}
    current = _state(original)
    new = next_version(current["version"], part)
    updates = {}
    for path in SOURCE_FILES:
        value, encoding, bom = decode(original[path])
        for _, literal, start, end in sorted(current["_spans"][path],
                                             key=lambda field: field[2], reverse=True):
            if literal not in REFERENCES:
                value = value[:start] + new + value[end:]
        updates[path] = bom + value.encode(encoding)
    if _state(updates)["version"] != new:
        raise ValueError("Prepared source metadata did not validate")
    for path, data in updates.items():
        if data != original[path]:
            (root / path).write_bytes(data)
    return new


bump = update


def expected_bundle(root=Path(".")):
    current = state(root)
    expected = {SHORT: current["version"]}
    if current["bundle_version"] is not None:
        expected[BUILD] = current["bundle_version"]
    return expected


def verify_bundle(bundle, root=Path(".")):
    current = state(root)
    version = current["version"]
    contents = Path(bundle) / "Contents"
    info = load_plist((contents / "Info.plist").read_bytes())
    if info.get(SHORT) != version:
        raise ValueError("Built bundle display version differs from selected source")
    if (BUILD in current["plist"] or BUILD in info) and info.get(BUILD) != version:
        raise ValueError("Built bundle build version differs from selected source")
    localized_data = (contents / "Resources" / LOCALIZED).read_bytes()
    decoded = None
    if not localized_data.startswith(b"bplist00"):
        decoded = decode(localized_data)[0]
    if decoded is None or decoded.lstrip().startswith("<?xml"):
        localized = load_plist(localized_data)
    else:
        localized = {key: value for key, value, _, _ in assignments(decoded, localized=True)}
    if localized.get(SHORT) != version or localized.get(INFO) != INFO_PREFIX + version + INFO_SUFFIX:
        raise ValueError("Built localized display differs from selected source")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def selected_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable full commit SHA")
    if run("git", "rev-parse", "HEAD") != sha or run("git", "cat-file", "-t", sha) != "commit":
        raise ValueError("Checkout does not match the selected commit SHA")


def remote_ref(ref):
    rows = run("git", "ls-remote", "--refs", "origin", ref).splitlines()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Ambiguous advertised remote ref")
    columns = rows[0].split()
    if len(columns) != 2 or columns[1] != ref or not re.fullmatch(r"[0-9a-f]{40}", columns[0]):
        raise ValueError("Malformed advertised remote ref")
    return columns[0]


def require_guard():
    guard = Path(__file__).resolve().parent / "bump-hooks/pre-push"
    if not guard.is_file() or not os.access(guard, os.X_OK):
        raise ValueError("Mandatory pre-push guard is missing or not executable")
    return guard


def bump_branch(sha, branch, part):
    selected_sha(sha)
    if run("git", "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Bump requires a clean checkout")
    ref = f"refs/heads/{branch}"
    run("git", "check-ref-format", ref)
    require_guard()
    if remote_ref(ref) != sha:
        raise ValueError("Selected remote branch moved before preparation")
    version = update(part)
    prepared = {str(path): path.read_bytes() for path in SOURCE_FILES}
    run("git", "add", "--", *map(str, SOURCE_FILES))
    run("git", "commit", "-m", f"chore: bump version to {version}\n\n{TRAILER}")
    new = run("git", "rev-parse", "HEAD")
    if run("git", "rev-list", "--parents", "-n", "1", new) != f"{new} {sha}":
        raise ValueError("Bump must be a single-parent direct child of the selected commit")
    changed = set(run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", new).splitlines())
    if not changed or not changed.issubset(prepared):
        raise ValueError("Bump commit must contain nonempty source version metadata only")
    if run("git", "diff", "--summary", sha, new):
        raise ValueError("Bump must preserve source file types and modes")
    for path in prepared:
        committed = subprocess.run(
            ["git", "show", f"{new}:{path}"], check=True, capture_output=True,
        ).stdout
        if committed != prepared[path]:
            raise ValueError("Bump commit differs from prepared source version metadata")
    trailers = run("git", "interpret-trailers", "--parse",
                   input=run("git", "log", "-1", "--format=%B"))
    if TRAILER not in trailers.splitlines():
        raise ValueError("Bump commit is missing the required Copilot App trailer")
    if run("git", "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Bump commit left a dirty checkout")
    if remote_ref(ref) != sha:
        raise ValueError("Selected remote branch moved during preparation")
    env = {**os.environ, "BUMP_EXPECTED_SHA": sha, "BUMP_EXPECTED_REF": ref, "BUMP_NEW_SHA": new}
    guard = require_guard()
    run("git", "-c", "push.followTags=false", "-c", f"core.hooksPath={guard.parent}",
        "push", "--porcelain", "--no-follow-tags", "origin", f"{new}:{ref}", env=env)
    if remote_ref(ref) != new:
        raise ValueError("Pushed branch failed exact-SHA verification")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("read")
    bump_parser = commands.add_parser("bump")
    bump_parser.add_argument("--part", required=True, choices=("patch", "minor", "major"))
    bump_parser.add_argument("--expected-sha", required=True)
    bump_parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME"))
    verify = commands.add_parser("verify-bundle")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()
    if args.command == "bump":
        if os.environ.get("GITHUB_REF_TYPE", "branch") != "branch":
            raise ValueError("Bump accepts branches only")
        branch = args.branch or run("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        print(bump_branch(args.expected_sha, branch, args.part))
    elif args.command == "verify-bundle":
        verify_bundle(args.bundle)
    else:
        print(read())


if __name__ == "__main__":
    main()
