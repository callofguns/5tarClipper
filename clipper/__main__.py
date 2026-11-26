"""Command line entry point.

    python -m clipper app      start the app in its own window
    python -m clipper web      start it in your browser instead (for development)
    python -m clipper login    log the Twitch bot account in (also possible on the app's Live page)
    python -m clipper live     run live mode in this terminal (test mode: notes moments, no clips)
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
    print(f"5tarClipper is running at {url}  (only this PC can open it; Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 = reachable only from this computer, not from your network or the internet.
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")


def cmd_login(cfg: dict, args) -> None:
    import time

    from clipper.live.auth import TwitchAuth
    auth = TwitchAuth()
    if not auth.client_id:
        auth.set_client_id(input("Your Twitch app's Client ID: "))
    st = auth.start_login()
    print()
    print(f"1. Open {st['verification_uri']}")
    print("   (signed in to Twitch as your BOT account, not your main one)")
    print(f"2. Check the code says {st['user_code']} and press Authorize.")
    print()
    print("Waiting...")
    while auth.login_state["state"] == "waiting":
        time.sleep(1)
    if auth.login_state["state"] == "done":
        print(f"Logged in as {auth.user['login']}.")
    else:
        print(auth.login_state.get("error"))
        sys.exit(1)


def cmd_live(cfg: dict, args) -> None:
    import time

    from clipper.live.manager import LiveManager
    live = LiveManager(cfg)
    for c in args.channel or []:
        live.add_channel(c)
    if not live.channels:
        print("No channels yet. Add some: python -m clipper live --channel NAME")
        sys.exit(1)
    print("Watching: " + ", ".join(live.channels) + "  (Ctrl+C to stop)")
    live.start()
    try:
        while live.running:
            time.sleep(1)
    except KeyboardInterrupt:
        live.stop()
        live.wait_stopped()
    if live.error:
        print(live.error)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m clipper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("app", help="Start the app in its own window (like the installed version)")
    p = sub.add_parser("web", help="Start it in your browser instead (handy for development)")
    p.add_argument("--port", type=int, help="port number (default from config.yaml)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")

    sub.add_parser("login", help="Log the Twitch bot account in")
    p = sub.add_parser("live", help="Run live mode here (test mode: notes moments, makes no clips)")
    p.add_argument("--channel", action="append", help="add a channel to watch (can repeat)")

    args = parser.parse_args()
    try:
        if args.command == "app":
            from clipper.app import main as app_main
            app_main()
            return
        cfg = load_config()
        if args.command == "web":
            cmd_web(cfg, args)
        elif args.command == "login":
            cmd_login(cfg, args)
        elif args.command == "live":
            cmd_live(cfg, args)
    except ConfigError as e:
        print(f"Config problem: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
