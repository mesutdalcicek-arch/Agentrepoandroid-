import threading
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.label import Label
from kivy.utils import platform
import backend


def open_webview(*a):
    from android.runnable import run_on_ui_thread
    from jnius import autoclass

    @run_on_ui_thread
    def make():
        WebView = autoclass("android.webkit.WebView")
        Client = autoclass("android.webkit.WebViewClient")
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wv = WebView(act)
        s = wv.getSettings()
        s.setJavaScriptEnabled(True)
        s.setDomStorageEnabled(True)
        wv.setWebViewClient(Client())
        act.getWindow().setSoftInputMode(16)  # adjustResize
        act.setContentView(wv)
        wv.loadUrl("http://127.0.0.1:%d/" % backend.PORT)

    make()


class AjanApp(App):
    def build(self):
        threading.Thread(target=backend.serve, daemon=True).start()
        return Label(text="Ajan yukleniyor...")

    def on_start(self):
        if platform == "android":
            Clock.schedule_once(open_webview, 1)

    def on_pause(self):
        return True


AjanApp().run()
