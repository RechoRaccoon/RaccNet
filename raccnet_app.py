"""
RaccNet Desktop App Launcher
────────────────────────────
Opens RaccNet as a native desktop window (no separate browser needed).
The server and web UI share a single combined window.

Requirements:
    pip install pywebview

To build RaccNet.exe:
    pip install pyinstaller
    pyinstaller --onefile --windowed --name RaccNet --icon="RaccNet Icon.ico" raccnet_app.py

Usage:
    python raccnet_app.py        — run as desktop app
    python raccnet_server.py     — run in browser (original mode)
"""

import threading
import time
import sys
import os
import webbrowser

PORT = 8080

def _start_server():
    """Import and start the RaccNet server in a background thread."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "raccnet_server",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "raccnet_server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "raccnet_server"
    spec.loader.exec_module(mod)
    # Disable keepalive shutdown in app mode
    mod._server_shutdown_enabled[0] = False
    threading.Thread(target=mod.run_server, daemon=True).start()

def _wait_for_server(timeout=15):
    """Poll until the server is accepting connections."""
    import urllib.request, urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False

def _launch_webview():
    """Launch a native pywebview window."""
    try:
        import webview
    except ImportError:
        print("pywebview not installed. Install it with:  pip install pywebview")
        print(f"Opening in browser instead: http://localhost:{PORT}")
        webbrowser.open(f"http://localhost:{PORT}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    window = webview.create_window(
        title="RaccNet",
        url=f"http://localhost:{PORT}",
        width=1280,
        height=800,
        min_size=(800, 600),
        resizable=True,
        text_select=True,
    )

    # pywebview on Windows requires an ICO file for the icon parameter;
    # skip it to avoid crashes and let the OS use the default window icon.
    webview.start(debug=False)


def main():
    print("RaccNet Desktop App")
    print("Starting server…")

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    time.sleep(0.5)

    print("Waiting for server to be ready…")
    if not _wait_for_server():
        print("Server did not start in time. Check raccnet_server.py for errors.")
        sys.exit(1)

    print(f"Server ready at http://localhost:{PORT}")
    print("Opening RaccNet window…")
    _launch_webview()


if __name__ == "__main__":
    main()
