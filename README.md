![version](https://img.shields.io/badge/version-19%2B-5682DF)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-appearance)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-appearance/total)

# 4d-plugin-appearance
Observe changes for the macOS appearance

inspired by [ruiaureliano/macOS-Appearance](https://github.com/ruiaureliano/macOS-Appearance)

#### Syntax

```
scheme:=Get_system_color_scheme
```

<div class="grid">
<div class="syntax-th cell cell--2">Parameter</div>
<div class="syntax-th cell cell--2">Type</div>
<div class="syntax-th cell cell--8">Description</div>
<div class="syntax-td cell cell--2">scheme</div>
<div class="syntax-td cell cell--2">TEXT</div>
<div class="syntax-td cell cell--8">light, dark or auto</div>   
</div>

1. On Catalina and above, read `NSUserDefaults` `AppleInterfaceStyleSwitchesAutomatically` for "auto".

2. If not "auto", on Mojave or later, read `NSUserDefaults` `AppleInterfaceStyle` for "light" or "dark".

```
ON APPEARANCE CHANGE CALL(method)
```

<div class="grid">
<div class="syntax-th cell cell--2">Parameter</div>
<div class="syntax-th cell cell--2">Type</div>
<div class="syntax-th cell cell--8">Description</div>
<div class="syntax-td cell cell--2">method</div>
<div class="syntax-td cell cell--2">TEXT</div>
<div class="syntax-td cell cell--8">#DECLARE($mode:Text) $1 is light or dark</div>   
</div>
