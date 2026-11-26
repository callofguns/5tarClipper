"""The Windows app: 5tarClipper in its own window (no browser needed).

The interface is the same page as before, shown in a native window by pywebview using Windows'
built-in Edge WebView2. The processing engine runs inside the app, reachable only from this PC.
"""
import json
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

TITLE = "5tarClipper"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ask_running_app_to_show(port: int) -> bool:
    """If the app is already open, bring its window to the front instead of opening a second one."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/focus", method="POST",
                                     headers={"x-clipper": "1"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


class WindowApi:
    """Functions the page can call directly (window.pywebview.api.*). Only on this PC."""

    def __init__(self, store):
        self._store = store
        self._window = None

    def save_clip(self, job_id: str, name: str) -> str | None:
        """Show a normal Windows "Save as" box and copy the clip there."""
        import webview

        from clipper.video.pipeline import clip_folder
        job = self._store.get(job_id)
        if job is None or name not in {c["file"] for c in job.data.get("clips", [])}:
            return None
        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name,
                                                 file_types=("MP4 video (*.mp4)",))
        if not result:
            return None                       # cancelled
        dest = result if isinstance(result, str) else result[0]
        shutil.copyfile(clip_folder(job) / name, dest)
        return dest


def main() -> None:
    from clipper.config import DATA_ROOT, FROZEN, code_path, load_config

    if FROZEN:  # no console window: send any output to a log file instead
        logs = DATA_ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(logs / "app.log", "a", encoding="utf-8", buffering=1)
        print(f"\n--- started {time.ctime()} ---")

    cfg = load_config()
    port = cfg.get("web", {}).get("port", 8765)
    url = f"http://127.0.0.1:{port}"

    if _port_in_use(port):
        if not _ask_running_app_to_show(port):
            print(f"Port {port} is used by another program. Change web.port in config.yaml.")
        return

    import uvicorn

    from clipper.web.server import create_app

    app = create_app(cfg)
    store = app.state.store
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)

    try:
        import webview
    except ImportError:               # window support missing: fall back to the browser
        webbrowser.open(url)
        print(f"Running at {url}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    api = WindowApi(store)
    window = webview.create_window(TITLE, url, js_api=api, width=1320, height=860,
                                   min_size=(900, 620), background_color="#0c0b12", text_select=True)
    api._window = window

    def bring_to_front():
        window.restore()
        window.show()
        window.on_top = True          # a quick on-top flip brings it above other windows
        window.on_top = False
    app.state.on_focus = bring_to_front

    from clipper import updater
    quitting_for_update = threading.Event()

    def quit_for_update():
        quitting_for_update.set()
        window.destroy()
    updater.set_quit_hook(quit_for_update)

    def on_closing():
        if quitting_for_update.is_set():         # the update asked us to close
            return True
        busy = any(j["status"] in ("queued", "running") for j in store.all())
        if busy and not window.create_confirmation_dialog(
                "Quit 5tarClipper?",
                "A video is still being processed. If you quit now it stops, "
                "and you can press Retry next time to continue where it left off."):
            return False
        updater.install_on_exit_if_ready()       # automatic updates install as the app closes
        return True
    window.events.closing += on_closing
    updater.startup()

    webview.settings["SHOW_DEFAULT_MENUS"] = False   # no browser-style right-click menu
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.start(private_mode=False, storage_path=str(DATA_ROOT / "webview"),
                  icon=str(code_path("assets/icon.ico")))
    server.should_exit = True         # window closed: stop the engine too


if __name__ == "__main__":
    main()
