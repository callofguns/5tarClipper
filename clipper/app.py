"""The Windows app: runs the clipper in the background with a tray icon, and opens it in your browser."""
import os
import socket
import sys
import threading
import time
import webbrowser


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


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

    if _port_in_use(port):            # already running: just open the page again
        webbrowser.open(url)
        return

    import uvicorn

    from clipper.video.pipeline import output_root
    from clipper.web.server import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    webbrowser.open(url)

    try:
        import pystray
        from PIL import Image
    except ImportError:               # no tray support: keep running until Ctrl+C
        print(f"Running at {url}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    def open_clips(icon, item):
        folder = output_root(cfg)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon("GTA6Clipper", Image.open(code_path("assets/icon.png")), "GTA VI Clipper",
                        menu=pystray.Menu(
                            pystray.MenuItem("Open GTA VI Clipper", lambda i, it: webbrowser.open(url),
                                             default=True),
                            pystray.MenuItem("Open clips folder", open_clips),
                            pystray.MenuItem("Quit", quit_app)))
    icon.run()


if __name__ == "__main__":
    main()
