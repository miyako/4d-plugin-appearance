![version](https://img.shields.io/badge/version-19%2B-5682DF)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-appearance)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-appearance/total)

# 4d-plugin-appearance
Observe changes for the macOS appearance

inspired by [ruiaureliano/macOS-Appearance](https://github.com/ruiaureliano/macOS-Appearance)

#### Syntax

```4d
scheme:=Get system color scheme
```

1. On Catalina and above, read `NSUserDefaults` `AppleInterfaceStyleSwitchesAutomatically` for "auto".

2. If not "auto", on Mojave or later, read `NSUserDefaults` `AppleInterfaceStyle` for "light" or "dark".

```4d
ON APPEARANCE CHANGE CALL(method)
```

`#DECLARE($mode:Text)` $1 is either light or dark

[miyako.github.io](https://miyako.github.io/2022/04/29/scheme-notification.html)
