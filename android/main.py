"""
RaccNet Android entrypoint
- Starts raccnet_server.py in a background thread
- Shows a full-screen WebView pointed at http://localhost:8080
- Back button navigates within the WebView (like a browser), exits app only when at root
"""

import threading
import traceback
import time
import os
import sys

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from kivy.utils import platform

# ── Start the RaccNet server in a background daemon thread ──────────────────
_server_ready = threading.Event()
_server_error = [None]   # mutable container so thread can write to it

def _start_server():
    try:
        import raccnet_server
        # Patch serve_forever to signal readiness right before blocking
        orig = raccnet_server.ThreadedHTTPServer.serve_forever
        def patched_serve(self, *a, **kw):
            _server_ready.set()
            orig(self, *a, **kw)
        raccnet_server.ThreadedHTTPServer.serve_forever = patched_serve
        raccnet_server.run_server()
    except Exception as e:
        _server_error[0] = traceback.format_exc()
        print(f"[RaccNet] Server error: {e}")
        _server_ready.set()  # unblock UI even on error

threading.Thread(target=_start_server, daemon=True).start()

# ── Android WebView setup (only runs on Android) ────────────────────────────
if platform == 'android':
    from android.runnable import run_on_ui_thread
    from jnius import autoclass, cast

    PythonActivity   = autoclass('org.kivy.android.PythonActivity')
    WebView          = autoclass('android.webkit.WebView')
    WebViewClient    = autoclass('android.webkit.WebViewClient')
    WebChromeClient  = autoclass('android.webkit.WebChromeClient')
    FrameLayout      = autoclass('android.widget.FrameLayout')
    FLP              = autoclass('android.widget.FrameLayout$LayoutParams')
    LP               = autoclass('android.view.ViewGroup$LayoutParams')
    View             = autoclass('android.view.View')
    Color            = autoclass('android.graphics.Color')

    _webview = None

    @run_on_ui_thread
    def _attach_webview():
        global _webview
        try:
            activity = PythonActivity.mActivity

            wv = WebView(activity)
            _webview = wv

            # Settings
            s = wv.getSettings()
            s.setJavaScriptEnabled(True)
            s.setDomStorageEnabled(True)
            s.setMediaPlaybackRequiresUserGesture(False)
            s.setLoadWithOverviewMode(True)
            s.setUseWideViewPort(True)
            s.setBuiltInZoomControls(False)
            s.setSupportZoom(False)

            # Keep navigation inside the WebView (no external browser)
            wv.setWebViewClient(WebViewClient())
            wv.setWebChromeClient(WebChromeClient())
            wv.setBackgroundColor(Color.parseColor('#0f0f0f'))

            # Full-screen overlay on top of the Kivy surface
            params = FLP(LP.MATCH_PARENT, LP.MATCH_PARENT)
            activity.addContentView(wv, params)

            wv.loadUrl('http://localhost:8080')
        except Exception as e:
            _server_error[0] = '[WebView error]\n' + traceback.format_exc()
            print(f"[RaccNet] WebView error: {e}")

    @run_on_ui_thread
    def _raccnet_nav_back():
        """Call RaccNet's own back function via JS (same as the header back button)."""
        if _webview is not None:
            _webview.loadUrl(
                'javascript:if(typeof window.raccnetNavBack==="function")'
                '{window.raccnetNavBack();}'
            )

    def _handle_back(window, key, *args):
        """Android back button / swipe → RaccNet's back navigation."""
        if key == 27 and _webview is not None:  # 27 = back key
            _raccnet_nav_back()
            return True   # always consumed — never exits app on back
        return False

    from kivy.core.window import Window
    Window.bind(on_keyboard=_handle_back)


# ── Loading screen — plain dark background while server starts ───────────────
class LoadingScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        from kivy.graphics import Color as KColor, Rectangle
        with self.canvas.before:
            KColor(rgba=(15/255, 15/255, 15/255, 1))   # #0f0f0f
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg)

        # Error display (only shown if server crashes)
        self._error_label = Label(
            text='',
            markup=False,
            font_size='11sp',
            color=(1, 0.3, 0.3, 1),
            size_hint=(1, None),
            height=0,
            text_size=(None, None),
            halign='left',
            valign='top',
            padding=(12, 8),
        )
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self._error_label)
        self.add_widget(scroll)

    def set_error(self, msg):
        self._error_label.text = '[color=#FF4444]Error:[/color]\n' + msg
        self._error_label.markup = True
        self._error_label.texture_update()
        self._error_label.height = self._error_label.texture_size[1] + 20

    def _update_bg(self, *_):
        self._bg.pos  = self.pos
        self._bg.size = self.size


class RaccNetApp(App):
    def build(self):
        self.title = 'RaccNet'
        self._screen = LoadingScreen()
        # Poll until server is ready, then show WebView
        Clock.schedule_interval(self._check_ready, 0.2)
        return self._screen

    def _check_ready(self, dt):
        if _server_ready.is_set():
            Clock.unschedule(self._check_ready)
            if _server_error[0]:
                self._screen.set_error(_server_error[0])
                return
            if platform == 'android':
                # Small extra delay so the server socket is fully open
                Clock.schedule_once(lambda dt: _attach_webview(), 0.5)
                # Check for WebView errors a moment later
                Clock.schedule_once(self._check_webview_error, 3.0)
            else:
                # Desktop fallback: open in system browser
                import webbrowser
                webbrowser.open('http://localhost:8080')

    def _check_webview_error(self, dt):
        if _server_error[0]:
            self._screen.set_error(_server_error[0])

    def on_pause(self):
        return True   # keep server running when app is backgrounded

    def on_resume(self):
        pass


if __name__ == '__main__':
    RaccNetApp().run()
