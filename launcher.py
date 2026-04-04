"""
RaccNet Launcher
- Downloads FFmpeg automatically if not found (needed for video uploads)
- Starts the local proxy server
- Opens your browser to http://localhost:8080
"""
import os
import sys
import shutil
import subprocess
import threading
import time
import webbrowser
import urllib.request
import zipfile

# ── FFmpeg auto-install ─────────────────────────────────────────────────────────
_APP_DATA   = os.environ.get("APPDATA", os.path.expanduser("~"))
FFMPEG_DIR  = os.path.join(_APP_DATA, "RaccNet", "ffmpeg")
FFMPEG_EXE  = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
# BtbN's static GPL builds — reliable, well-known source
FFMPEG_URL  = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)


def _reporthook(count, block, total):
    if total > 0:
        pct = min(count * block * 100 // total, 100)
        print(f"\r  Downloading FFmpeg... {pct}%   ", end="", flush=True)


def ensure_ffmpeg():
    """Make sure ffmpeg is on PATH. Downloads it if necessary."""
    if shutil.which("ffmpeg"):
        return  # already available system-wide

    if os.path.isfile(FFMPEG_EXE):
        # Use our local copy
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
        return

    print("FFmpeg not found — downloading it for video upload support (~80 MB).")
    print("Press Ctrl+C to skip (video uploads will not work without it).\n")
    try:
        os.makedirs(FFMPEG_DIR, exist_ok=True)
        zip_path = os.path.join(FFMPEG_DIR, "ffmpeg_dl.zip")

        urllib.request.urlretrieve(FFMPEG_URL, zip_path, _reporthook)
        print("\n  Extracting...")

        with zipfile.ZipFile(zip_path, "r") as z:
            # The zip contains a nested folder like ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
            target = next(
                (n for n in z.namelist() if n.endswith("/bin/ffmpeg.exe")), None
            )
            if target:
                data = z.read(target)
                with open(FFMPEG_EXE, "wb") as fh:
                    fh.write(data)

        os.remove(zip_path)
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
        print("  FFmpeg ready.\n")

    except KeyboardInterrupt:
        print("\n  Skipped. Video uploads will not work.\n")
    except Exception as exc:
        print(f"\n  Warning: could not download FFmpeg ({exc}).")
        print("  Video uploads will not work.\n")


# ── First-run shortcut setup ────────────────────────────────────────────────────
_RACCTUBE_DIR   = os.path.join(_APP_DATA, "RaccNet")
_SHORTCUT_FLAG  = os.path.join(_RACCTUBE_DIR, "shortcuts_asked.flag")
_ICON_DEST      = os.path.join(_RACCTUBE_DIR, "RaccNet Icon.ico")


def _extract_icon():
    """Copy the bundled .ico to AppData so shortcuts can reference a stable path."""
    try:
        if getattr(sys, "frozen", False):
            src = os.path.join(sys._MEIPASS, "RaccNet Icon.ico")
        else:
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RaccNet Icon.ico")
        if os.path.isfile(src) and not os.path.isfile(_ICON_DEST):
            os.makedirs(_RACCTUBE_DIR, exist_ok=True)
            shutil.copy2(src, _ICON_DEST)
    except Exception:
        pass
    return _ICON_DEST if os.path.isfile(_ICON_DEST) else None


def _create_desktop_shortcut(exe_path, icon_path):
    icon_arg = f"{icon_path}, 0" if icon_path else f"{exe_path}, 0"

    # Escape single quotes for PowerShell single-quoted strings (apostrophes in paths)
    def ps_esc(s):
        return s.replace("'", "''")

    ps = f"""
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders('Desktop')
$lnk = Join-Path $desktop 'RaccNet.lnk'
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = '{ps_esc(exe_path)}'
$s.IconLocation = '{ps_esc(icon_arg)}'
$s.Description = 'RaccNet - Bluesky Video Platform'
$s.Save()
"""
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                capture_output=True, timeout=15)
        if result.returncode == 0:
            print("  Desktop shortcut created.")
        else:
            print(f"  Shortcut creation failed: {result.stderr.decode(errors='replace').strip()}")
    except Exception as exc:
        print(f"  Could not create desktop shortcut: {exc}")


def maybe_create_shortcuts():
    """On first run only, ask the user about a desktop shortcut."""
    if not getattr(sys, "frozen", False):
        return  # Only relevant when running as a compiled .exe
    if os.path.exists(_SHORTCUT_FLAG):
        return  # Already asked

    exe_path = sys.executable
    icon_path = _extract_icon()

    print()
    print("┌─────────────────────────────────────────┐")
    print("│         First-time setup                 │")
    print("└─────────────────────────────────────────┘")
    print()

    want_desktop = input("  Create Desktop shortcut? (Y/N): ").strip().lower() in ("y", "yes")

    # Save flag so we never ask again
    os.makedirs(_RACCTUBE_DIR, exist_ok=True)
    with open(_SHORTCUT_FLAG, "w") as fh:
        fh.write("done")

    if want_desktop:
        _create_desktop_shortcut(exe_path, icon_path)

    print()


# ── Browser auto-open ───────────────────────────────────────────────────────────
def _open_browser():
    time.sleep(1.8)
    webbrowser.open("http://localhost:8080")


# ── Entry point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("╔══════════════════════════════════════════╗")
    print("║              RaccNet                    ║")
    print("╠══════════════════════════════════════════╣")
    print("║  Bluesky video platform                  ║")
    print("╚══════════════════════════════════════════╝")
    print()

    maybe_create_shortcuts()
    ensure_ffmpeg()

    # Resolve the server module whether running as .py or frozen PyInstaller exe
    if getattr(sys, "frozen", False):
        _here = sys._MEIPASS          # PyInstaller extracts files here
    else:
        _here = os.path.dirname(os.path.abspath(__file__))

    if _here not in sys.path:
        sys.path.insert(0, _here)

    threading.Thread(target=_open_browser, daemon=True).start()
    print("Opening http://localhost:8080 in your browser...")
    print("Keep this window open while using RaccNet.")
    print("Press Ctrl+C to stop the server.\n")

    import raccnet_server
    raccnet_server.run_server()
