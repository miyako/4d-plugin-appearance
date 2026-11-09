![version](https://img.shields.io/badge/version-19%2B-5682DF)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-appearance)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-appearance/total)

# 4d-plugin-appearance

Lets a 4D application read the macOS system appearance (Light/Dark/Auto) and react when it changes, driven by `NSUserDefaults`, `NSApplication.effectiveAppearance`, and a distributed-notification listener (`AppleInterfaceThemeChangedNotification`). All three commands return or deliver plain `Text` values (`"light"`, `"dark"`, or `"auto"`) — there's no dedicated object/enum type.

| Command | Returns | Purpose |
|---|---|---|
| [`Get system color scheme`](#get-system-color-scheme) | Text | Reads the OS-level appearance *preference* (`"light"` / `"dark"` / `"auto"`). |
| [`ON APPEARANCE CHANGE CALL`](#on-appearance-change-call) | — | Registers (or clears) a 4D method called whenever the system appearance changes. |
| [`Get effective color scheme`](#get-effective-color-scheme) | Text | Reads the appearance actually *applied* to the 4D application right now (`"light"` / `"dark"`). |

**Platforms:** macOS only (Intel & Apple Silicon). The plugin's source has no Windows (`VERSIONWIN`) code path at all — it's Cocoa/Foundation/CoreServices end to end — matching its own `mac-intel | mac-arm` platform badge above.

---

## Requirements & platform notes

- **macOS 10.14 (Mojave) or later** is required for light/dark detection at all. Both [`Get system color scheme`](#get-system-color-scheme) and [`Get effective color scheme`](#get-effective-color-scheme) always return `"light"` on earlier systems — this is a silent default, not a 4D error.
- **macOS 10.15 (Catalina) or later** is required for `"auto"` detection. On earlier systems, [`Get system color scheme`](#get-system-color-scheme) never returns `"auto"`.
- All three commands are declared `threadSafe: true` in `manifest.json` and can be called from any 4D process.
- Only **one** [`ON APPEARANCE CHANGE CALL`](#on-appearance-change-call) registration is active at a time, plugin-wide — registering a new method silently replaces the previous one rather than adding a second listener.
- `"auto"` is a system-*preference* value only. It's never returned by [`Get effective color scheme`](#get-effective-color-scheme) and never passed to an [`ON APPEARANCE CHANGE CALL`](#on-appearance-change-call) callback — both of those always resolve to a concrete `"light"` or `"dark"`.
- The change-notification mechanism (`AppleInterfaceThemeChangedNotification`) is a long-standing but not formally Apple-documented distributed notification, not a public API guaranteed in the same way `NSUserDefaults` keys are.

---

## Get system color scheme

### Syntax

```4d
scheme:=Get system color scheme
```

| Parameter | Type | Description |
|---|---|---|
| `Result` | Text | `"light"`, `"dark"`, or `"auto"` |

### Description

Reads the OS-level appearance **preference** — not what's currently rendered, `NSUserDefaults` state:

1. On macOS 10.15+, checks the `AppleInterfaceStyleSwitchesAutomatically` default. If true, returns `"auto"`.
2. Otherwise, on macOS 10.14+, checks whether the `AppleInterfaceStyle` default is set: present → `"dark"`, absent → `"light"`.
3. **Below macOS 10.14**, both checks above are skipped entirely and the command always returns `"light"`.

### Example

From the plugin's own test method (`TEST.4dm`):

```4d
//%attributes = {}
$effective:=Get effective color scheme

$scheme:=Get system color scheme

//ON APPEARANCE CHANGE CALL("CALLBACK")
```

Branching on the result:

```4d
//%attributes = {}
$scheme:=Get system color scheme

If($scheme="dark")
	ALERT:C41("System appearance is set to Dark.")
Else If($scheme="light")
	ALERT:C41("System appearance is set to Light.")
Else //"auto"
	ALERT:C41("System appearance follows the automatic schedule.")
End if
```

---

## ON APPEARANCE CHANGE CALL

### Syntax

```4d
ON APPEARANCE CHANGE CALL(method)
```

| Parameter | Type | Description |
|---|---|---|
| `method` | Text | Mandatory — the parameter is read unconditionally, so it can't be omitted from the call. Name of a 4D project method to invoke whenever the system appearance changes. Pass an empty string `""` to **unregister** (stop receiving callbacks); this is the documented way to clear a registration, not an error case. |
| `Result` | — | This command has no declared return value (`manifest.json` gives it no `:TypeChar` suffix) and always completes without a result. |

### Description

Registers (or, with an empty string, clears) a callback fired whenever macOS posts an interface-theme-changed notification. The plugin listens for this via a background process it spawns at plugin startup, so the call itself returns immediately — it doesn't block waiting for the next appearance change.

- **Callback signature:** the registered method must declare a single `Text` parameter:

  ```4d
  //%attributes = {}
  #DECLARE($mode : Text)
  ```

  `$mode` is `"light"` or `"dark"` — the effective appearance at the time of the change (equivalent to what [`Get effective color scheme`](#get-effective-color-scheme) would return at that moment). It is never `"auto"`.

- **Runs on a background process.** The callback executes on the plugin's own listener process, not your interactive/front 4D process. Commands that expect to run on the front process may not behave as they would if called directly from an interactive method — hand off to your main process (e.g. via `CALL PROCESS` or a flag your main process polls) if the callback needs to drive the interface, rather than calling UI commands directly from it.
- **Timing is not instantaneous by design.** The listener process sleeps between checks rather than polling continuously; in the worst case there can be a real, if usually brief, delay between the OS-level change and your callback firing. If your workflow depends on near-immediate delivery, treat this as a background notification rather than a synchronous event.
- Registering again with a new method name replaces the previous registration outright — there's no way to have two callbacks active simultaneously.

### Example

From the plugin's own callback method (`CALLBACK.4dm`):

```4d
//%attributes = {}
#DECLARE($mode : Text)

ALERT:C41($mode)
```

Registering it (from `TEST.4dm`, shown commented out in the shipped sample — uncomment to activate):

```4d
//%attributes = {}
ON APPEARANCE CHANGE CALL("CALLBACK")
```

Unregistering:

```4d
//%attributes = {}
ON APPEARANCE CHANGE CALL("")  //stop listening
```

A callback that hands off to the front process instead of alerting directly:

```4d
//%attributes = {}
#DECLARE($mode : Text)

CALL PROCESS(Current process; "MAIN_APPLY_THEME"; $mode)
```

---

## Get effective color scheme

### Syntax

```4d
scheme:=Get effective color scheme
```

| Parameter | Type | Description |
|---|---|---|
| `Result` | Text | `"light"` or `"dark"` — never `"auto"` |

### Description

Reads the appearance **actually applied to the 4D application right now**, via `NSApplication.effectiveAppearance` matched against `NSAppearanceNameAqua` / `NSAppearanceNameDarkAqua` on the main process. Because it reports what's actually being rendered rather than the underlying preference, this always resolves to a concrete `"light"` or `"dark"` — even when [`Get system color scheme`](#get-system-color-scheme) would report `"auto"`.

**Below macOS 10.14**, the `NSAppearance` check is skipped entirely and the command always returns `"light"`.

### Example

From the plugin's own test method (`TEST.4dm`):

```4d
//%attributes = {}
$effective:=Get effective color scheme
```

Using it to pick a stylesheet:

```4d
//%attributes = {}
$effective:=Get effective color scheme

If($effective="dark")
	SET SVG STYLE SHEET($form; "dark-theme.css")
Else
	SET SVG STYLE SHEET($form; "light-theme.css")
End if
```

---

## Error handling & troubleshooting

- **No 4D errors are raised by any of these commands.** All failure/edge-case paths (unsupported OS version, no registered callback, an empty method name) resolve to a default value or a silent no-op rather than an error — check the returned/received text yourself rather than wrapping calls in error-catching methods.
- **`"auto"` only ever comes from [`Get system color scheme`](#get-system-color-scheme).** If your callback or [`Get effective color scheme`](#get-effective-color-scheme) logic has a branch for `"auto"`, it will never be taken — both of those always resolve to `"light"`/`"dark"`.
- **Pre-10.14 systems silently report `"light"` for everything**, including [`Get effective color scheme`](#get-effective-color-scheme) and any [`ON APPEARANCE CHANGE CALL`](#on-appearance-change-call) callback payload — this can look like "dark mode detection isn't working" when it's actually a minimum-OS-version gap.
- **The callback runs on a background process, not your interactive one.** If a callback that calls a UI-only command (a dialog, a form action) seems to do nothing or misbehave, route the work to your main process instead of calling it directly from the callback.
- **Appearance-change delivery isn't guaranteed instantaneous.** If a callback fires later than expected after a Dark Mode toggle, that's consistent with the listener's sleep/wake cycle rather than a bug in your registered method — this plugin doesn't currently document (nor did we independently verify against Apple's runtime) a hard upper bound on that delay, so treat it as "eventually, not synchronously."
- **Only one callback is ever active.** If a previously-working callback stops firing after some other part of your code calls [`ON APPEARANCE CHANGE CALL`](#on-appearance-change-call) with a different method name (or an empty string), that's expected — the new registration replaced the old one.
- **As of the source reviewed and fixed alongside this documentation**, [`Get system color scheme`](#get-system-color-scheme) and [`Get effective color scheme`](#get-effective-color-scheme) are guaranteed to always return a `Text` value, even in the (very unlikely) case of an internal exception — this specifically prevents the calling 4D process from being left waiting on a return value that never arrives. This is a property of the fixed source, not necessarily of whatever compiled binary you currently have installed — rebuild from the fixed source to pick it up.

---

## Quick reference

```4d
scheme   := Get system color scheme        // "light" | "dark" | "auto"
effective:= Get effective color scheme     // "light" | "dark"

ON APPEARANCE CHANGE CALL("MyMethod")      // MyMethod(#DECLARE($mode : Text)) fires on change
ON APPEARANCE CHANGE CALL("")              // stop listening
```

## Manual versioning and releases

**Bump** and **Build** are independent, manual-only Actions workflows. Both use
GitHub's built-in ref selector. Bump has one required `mode` choice:
`patch` (default), `minor`, or `major`. Build has **zero custom inputs**.
A branch push, pull request, or tag push does not start either workflow.
Bump never builds, tags, publishes a release, or dispatches Build.
Build never increments a version.

### Source authority and migration

The source version is a canonical `X.Y.Z` (no leading zeros or suffixes), read
from `Appearance.xcodeproj/project.pbxproj` and co-validated against `Info.plist`
and the copied source `English.lproj/InfoPlist.strings`. The previous Bump
workflow's explicit header prescribed coordinated `MARKETING_VERSION`,
`CURRENT_PROJECT_VERSION`, and plist version fields; its marketing-only
implementation did not fulfill that contract. The migration therefore retains
coordinated versioning rather than treating the stale build number as independent.

There is **no release-version increment at migration**. Rename-follow history
shows `2504123`, `2052b25`, and `69351ce` advancing only the six marketing tokens
from `1.2.0` to `1.2.3`. The six current-project tokens and two localized display
tokens were stranded at `1.2.0`; those source tokens alone are repaired to the
existing `1.2.3` authority. The resource build phase copies
`English.lproj/InfoPlist.strings`, whose version and
`CFBundleGetInfoString` must agree with the source version. Its CR-only bytes
are preserved apart from the two version tokens.

`CFBundleShortVersionString` is required. The source plist's existing
`$(MARKETING_VERSION)` reference is retained; `CFBundleVersion` is absent and
stays absent. Known `$(...)` / `${...}` build-setting references are resolved
and preserved; unknown, cyclic, inconsistent, missing, or duplicate version
settings fail before edits. Source updates preserve encodings, BOMs, individual
newline bytes, file modes, and unrelated text. Prebuilt files under
`test/Plugins/Appearance.bundle`, archives, dependencies, native configuration
outside version tokens, and the 4D project are not intake-repair targets.

### Bump

Select a **branch**, not a tag, choose `patch` (default), `minor`, or `major`
in Bump's required `mode` dropdown, and run Bump. No workflow-file edit is
needed to select a mode. The choice is passed through a quoted environment
variable to the guarded source-only helper, which independently validates it.
The source helper also supports these three modes for a deliberate local bump.

Bump checks out the selected immutable Actions SHA and requires a clean
checkout matching the exact remote branch tip. It validates its executable
pre-push guard before editing and again immediately before push. It creates
one nonempty source-only commit with exactly the selected commit as its single
parent and a `Co-authored-by: Copilot App
<223556219+Copilot@users.noreply.github.com>` trailer. The push disables automatic
tag following both in Git configuration and with `--no-follow-tags`.
The guard validates the actual server advertisement, the literal local `HEAD`
update, and the exact old and new SHAs. Advance, rewind, deletion, or an
already-applied same-result race fails closed; do not retry an old run against
a changed branch. Review the branch and start a fresh run instead.

### Build

Select the desired ref and run Build separately. Every checkout uses the
selected `github.sha`; there is no default-branch-only restriction. One
repository-wide concurrency group serializes Builds across refs without
cancelling an active run.

The original executable workflow builds **macOS only**, notwithstanding its
old Windows-first comment. That topology is retained: Xcode target
`Appearance`, configuration `Default`, `ARCHS="arm64 x86_64"`,
`ONLY_ACTIVE_ARCH=NO`, and the original workflow's macOS deployment override
`11.0`. The project's `CONFIGURATION_BUILD_DIR="$(BUILD_DIR)"` and
`SYMROOT="$(SRCROOT)/test/Plugins"` establish the canonical output
`test/Plugins/Appearance.bundle`; no recursive first-match output discovery is
used. The Deployment configuration's separate signing script is not invoked.
The existing Cocoa framework, library paths, copy phases, Visual Studio v140
project, and prebuilt Windows siblings are preserved.

Only known generated macOS files are cleared for a fresh build. The bundle,
Resources tree, Windows/Windows64 files, dependencies, and any `Default` sibling
resources are never removed wholesale. Build verifies the fresh executable's
required architecture tokens and fresh plist and localized destination against
the selected source, including binary plists and UTF-16 strings. It retains
Developer ID signing and strict verification. Both notary submissions must
exit successfully **and** return JSON status `Accepted`; DMG stapling must
also succeed.

The existing secret **names** are unchanged:
`APPLE_DEVELOPER_ID_CERTIFICATE`, `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD`,
`KEYCHAIN_PASSWORD`, `NOTARYTOOL_APPLE_ID`, `NOTARYTOOL_TEAM_ID`, and
`NOTARYTOOL_PASSWORD`. Configure their values through repository settings,
not source control.

### Create-only publication and recovery

Before native work, and again after all build/sign/notary/staple gates, Build
authenticates and checks every page of releases (including drafts) plus the
exact candidate tag. Either existing item blocks publication. API/authentication,
network, malformed-response, or pagination failures also block publication.
The selected HEAD and source must remain unchanged; generated tracked bundle
outputs may legitimately differ after the native build and are not reset to
manufacture a clean checkout.

Publication creates the tag at the immutable selected SHA, verifies its remote
target, creates a **new public, non-prerelease** release with explicit tag,
name, and target, then uploads the exact `Appearance-X.Y.Z.zip` and
`Appearance-X.Y.Z.dmg` assets separately to that new release ID. The original
release-notes policy is preserved: **empty body, no generated release notes**.
There is no combined create-and-upload operation, reuse, update, clobber,
rollback, deletion, or automatic retry.

A failure may leave a tag, a release, or some uploaded assets. **Keep those
partial results for manual recovery.** Inspect the run and GitHub's exact tag,
release ID, and assets; resolve the interruption manually under the
repository's release policy. Do not rerun expecting an overwrite: the absence
checks intentionally reject partial publication. A race while creating a tag,
release, or asset is an error, not permission to reuse someone else's result.

### Offline checks

Run `python3 -m unittest discover -s .github/tests -v` and `actionlint`.
The tests exercise real disposable Git repositories and local bare origins,
fake authenticated GitHub APIs, and actual extracted workflow shell blocks
against closed-PATH native stubs. They do not build, sign, notarize, run 4D,
dispatch Actions, or mutate production tags/releases.
