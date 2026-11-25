"""Command line entry point.

    python -m clipper web      start the web app and open it in your browser
"""
import argparse
import sys
import threading
import webbrowser

from clipper.config import ConfigError, load_config


def cmd_web(cfg: dict, args) -> None:
    import uvicorn

    from clipper.web.server import create_app

    port = args.port or cfg.get("web", {}).get("port", 8765)
    url = f"http://127.0.0.1:{port}"
    print(f"GTA VI Clipper is running at {url}  (only this PC can open it; Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 = reachable only from this computer, not from your network or the internet.
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m clipper")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("web", help="Start the web app")
    p.add_argument("--port", type=int, help="port number (default from config.yaml)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    args = parser.parse_args()
    try:
        cfg = load_config()
        if args.command == "web":
            cmd_web(cfg, args)
    except ConfigError as e:
        print(f"Config problem: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
