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
