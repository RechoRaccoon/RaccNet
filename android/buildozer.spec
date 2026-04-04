[app]

# App metadata
title = RaccNet
package.name = raccnet
package.domain = org.raccnet
version = 1.0

# Source
source.dir = .
source.include_exts = py
source.include_patterns = raccnet_server.py, main.py

# Entry point
entrypoint = main.py

# Requirements — raccnet_server uses stdlib only, kivy for the shell + pyjnius for WebView
requirements = python3==3.11.0,kivy==2.3.0,pyjnius,android

# Orientation: support both so rotating to landscape works
orientation = portrait,landscape

# Android permissions
android.permissions = INTERNET, ACCESS_NETWORK_STATE

# Target/min SDK
android.api = 33
android.minapi = 21
android.ndk = 25b

# Architecture — arm64-v8a covers all modern phones; add x86_64 for emulators if needed
android.archs = arm64-v8a

# App icon
icon.filename = %(source.dir)s/icon.png

# Presplash (optional — place a presplash.png here)
# presplash.filename = %(source.dir)s/presplash.png
presplash.color = #0f0f0f

# Keep Python .pyc files out of the APK source
android.copy_libs = 1

# Fullscreen (hides Android status bar)
fullscreen = 0

# Log level (0=error, 1=info, 2=debug)
log_level = 1

[buildozer]
# Warn if buildozer is older than this version
warn_on_root = 1
