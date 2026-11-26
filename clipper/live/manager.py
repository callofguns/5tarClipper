"""Live mode: watches your Twitch channels and notes the moments chat goes wild.

Every minute it checks which of your channels are live in the GTA VI category (one request for up
to 100 channels). For those, it reads chat through EventSub and runs the spike detector every
second. When chat spikes it records the moment.

This version is TEST MODE ONLY: it writes down "would clip" moments (in the app and in
logs/clips.csv) but never asks Twitch to make a clip. Real clips come in the next update.

It runs in its own background thread with its own asyncio loop, so the rest of the app is unaffected.
"""
import asyncio
import csv
import json
import re
import threading
import time
from collections import deque
from datetime import datetime

from clipper.config import DATA_ROOT
from clipper.live.auth import LoginNeeded, TwitchAuth
from clipper.live.detector import Detector
from clipper.live.eventsub import EventSubSession
from clipper.live.helix import Helix, HelixError

MAX_CHANNELS = 100           # Twitch's limit on chats one bot can read at once (without being a mod)
LOGIN_RE = re.compile(r"^[a-z0-9_]{3,25}$")
CSV_FIELDS = ["time", "channel", "mode", "status", "url", "reason"]
DEFAULT_IGNORE = ["nightbot", "streamelements", "moobot", "fossabot", "streamlabs"]


def clean_channel(text: str) -> str:
    """Accept "name", "@name" or a twitch.tv link; return the lowercase login name."""
    t = (text or "").strip().lower()
    m = re.search(r"twitch\.tv/([a-z0-9_]+)", t)
    t = m.group(1) if m else t.lstrip("@")
    if not LOGIN_RE.match(t):
        raise ValueError("That isn't a Twitch username. Use letters, numbers and _ (like shroud), or paste the channel link.")
    return t


class LiveManager:
    def __init__(self, cfg: dict, auth: TwitchAuth | None = None, data_root=DATA_ROOT,
                 helix_factory=Helix, eventsub_url: str | None = None):
        t = cfg.get("twitch") or {}
        self.tcfg = t
        self.category = t.get("category_name") or "Grand Theft Auto VI"
        self.poll_every = max(30, int(t.get("check_live_every_seconds") or 60))
        self.ignore = {x.lower() for x in (t.get("ignore_chatters") or DEFAULT_IGNORE)}
        self.auth = auth or TwitchAuth()
        self._helix_factory = helix_factory          # tests swap in a fake Twitch
        self._eventsub_url = eventsub_url
        self.settings_path = data_root / "live.json"
        self.csv_path = data_root / "logs" / "clips.csv"
        self.lock = threading.RLock()
        self.channels: list[str] = self._load_channels()
        self.channel_state: dict[str, dict] = {}       # login -> what we know about it
        self.moments: deque[dict] = deque(self._load_recent_moments(), maxlen=100)
        self.activity: deque[dict] = deque(maxlen=200)
        self.running = False
        self.error: str | None = None
        self.bot: dict | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._wake: asyncio.Event | None = None
        self._validating = False
        # while running
        self.game_id: str | None = None
        self.ids: dict[str, str] = {}                  # login -> user id
        self.active: dict[str, str] = {}               # user id -> login (live in GTA VI)
        self.detectors: dict[str, Detector] = {}       # user id -> detector
        self.subs: dict[str, str] = {}                 # user id -> EventSub subscription id
        self.chat_times: dict[str, deque] = {}         # user id -> recent message times
        self.session: EventSubSession | None = None

    # ------------------------------------------------------------ saved settings
    def _load_channels(self) -> list[str]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return [c for c in data.get("channels", []) if LOGIN_RE.match(c)][:MAX_CHANNELS]
        except (OSError, ValueError):
            return []

    def _save_channels(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"channels": self.channels}, indent=1), encoding="utf-8")
        tmp.replace(self.settings_path)

    def _load_recent_moments(self) -> list[dict]:
        try:
            with self.csv_path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            return rows[-100:]
        except OSError:
            return []

    def add_channel(self, text: str) -> str:
        login = clean_channel(text)
        with self.lock:
            if login in self.channels:
                return login
            if len(self.channels) >= MAX_CHANNELS:
                raise ValueError(f"You can watch up to {MAX_CHANNELS} channels.")
            self.channels.append(login)
            self._save_channels()
        self._poke()
        return login

    def remove_channel(self, login: str) -> None:
        with self.lock:
            if login in self.channels:
                self.channels.remove(login)
                self._save_channels()
            self.channel_state.pop(login, None)
        self._poke()

    # ------------------------------------------------------------ start / stop (any thread)
    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            if not self.auth.client_id:
                raise LoginNeeded("Add your Twitch app's Client ID first.")
            if not self.auth.has_tokens:
                raise LoginNeeded("Log in to Twitch with your bot account first.")
            if not self.channels:
                raise ValueError("Add at least one channel to watch.")
            self.running = True
            self.error = None
            self._thread = threading.Thread(target=self._thread_main, daemon=True, name="live-mode")
            self._thread.start()

    def stop(self) -> None:
        loop, stop = self._loop, self._stop
        if loop and stop:
            loop.call_soon_threadsafe(stop.set)

    def wait_stopped(self, timeout: float = 10) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _poke(self) -> None:
        """Channels changed: check them now instead of waiting for the next minute."""
        loop, wake = self._loop, self._wake
        if loop and wake:
            loop.call_soon_threadsafe(wake.set)

    def ensure_bot_known(self) -> None:
        """Find out which account is logged in (in the background), for the Live page."""
        if self.bot or self._validating or not self.auth.has_tokens or not self.auth.client_id:
            return
        self._validating = True

        def check():
            try:
                self.bot = self.auth.validate()
            except LoginNeeded as e:
                self.error = str(e)
            except Exception:
                pass                                   # offline: try again later
            finally:
                self._validating = False
        threading.Thread(target=check, daemon=True).start()

    def logout(self) -> None:
        self.stop()
        self.wait_stopped(5)
        self.auth.logout()
        self.bot = None

    # ------------------------------------------------------------ what the page shows
    def status(self) -> dict:
        now = time.time()
        with self.lock:
            channels = []
            for login in self.channels:
                st = dict(self.channel_state.get(login) or {"status": "idle"})
                uid = self.ids.get(login)
                if self.running and uid in self.detectors:
                    s = self.detectors[uid].stats(now)
                    times = self.chat_times.get(uid) or ()
                    st["chat"] = {"score": s["score"], "baseline": round(s["baseline"], 1),
                                  "ratio": round(s["score"] / s["baseline"], 1) if s["baseline"] else 0,
                                  "chatters": s["chatters"], "warming_up": s["warming_up"],
                                  "per_minute": sum(1 for t in times if now - t <= 60)}
                    if st.get("status") == "watching" and not (self.session and self.session.connected):
                        st["status"] = "connecting"
                channels.append({"login": login, **st})
            return {"client_id_set": bool(self.auth.client_id), "logged_in": self.auth.has_tokens,
                    "bot": (self.bot or {}).get("login"), "login": self.auth.login_state,
                    "running": self.running, "error": self.error, "test_mode": True,
                    "category": self.category, "channels": channels,
                    "moments": list(self.moments)[::-1][:50],
                    "activity": list(self.activity)[::-1][:40],
                    "log_file": str(self.csv_path)}

    def _log(self, msg: str) -> None:
        with self.lock:
            self.activity.append({"t": datetime.now().strftime("%H:%M:%S"), "msg": msg})
        print(f"[live] {msg}", flush=True)

    def _set_channel(self, login: str, **st) -> None:
        with self.lock:
            if login in self.channels:
                self.channel_state[login] = st

    # ------------------------------------------------------------ the background loop
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        finally:
            with self.lock:
                self.running = False
                self._loop = self._stop = self._wake = None

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop, self._wake = asyncio.Event(), asyncio.Event()
        helix = self._helix_factory(self.auth)
        session_task: asyncio.Task | None = None
        self._log("Starting live mode (test mode: notes moments, doesn't make clips).")
        try:
            self.bot = await asyncio.to_thread(self.auth.validate)
            last_validate = time.time()
            self.game_id = await helix.game_id(self.category)
            if not self.game_id:
                raise ValueError(f"Twitch has no category called “{self.category}”. "
                                 "Check twitch.category_name in config.yaml.")
            self.session = EventSubSession(lambda sid, moved: self._on_welcome(helix, sid, moved),
                                           self._on_event, self._log, self._on_revoked,
                                           **({"url": self._eventsub_url} if self._eventsub_url else {}))
            next_poll = 0.0
            while not self._stop.is_set():
                now = time.time()
                if now >= next_poll or self._wake.is_set():
                    self._wake.clear()
                    try:
                        await self._poll(helix)
                    except LoginNeeded:
                        raise
                    except HelixError as e:
                        self._log(f"Couldn't check the channels ({e}). Trying again in a minute.")
                    except Exception as e:     # network down etc.: keep going
                        self._log(f"Couldn't reach Twitch ({type(e).__name__}). Trying again in a minute.")
                    next_poll = time.time() + self.poll_every
                if now - last_validate > 3600:            # Twitch requires an hourly check
                    try:
                        await asyncio.to_thread(self.auth.validate)
                    except LoginNeeded:
                        raise
                    except Exception:
                        pass
                    last_validate = now
                self._tick(now)
                if self.active and session_task is None:
                    session_task = asyncio.create_task(self.session.run())
                elif not self.active and session_task is not None:
                    session_task.cancel()                 # nothing to watch: close the chat connection
                    await asyncio.gather(session_task, return_exceptions=True)
                    session_task = None
                    self.subs.clear()
                if session_task is not None and session_task.done():
                    err = None if session_task.cancelled() else session_task.exception()
                    self._log(f"Chat connection stopped unexpectedly ({err}). Restarting it.")
                    session_task = None
                try:
                    await asyncio.wait_for(self._stop.wait(), 1)
                except asyncio.TimeoutError:
                    pass
        except LoginNeeded as e:
            self.error = str(e)
            self._log(str(e))
        except (HelixError, ValueError) as e:
            self.error = str(e)
            self._log(str(e))
        except Exception as e:
            self.error = f"Live mode stopped: {type(e).__name__}: {e}"
            self._log(self.error)
        finally:
            if session_task is not None:
                session_task.cancel()
                await asyncio.gather(session_task, return_exceptions=True)
            await helix.close()
            with self.lock:
                self.active.clear()
                self.detectors.clear()
                self.subs.clear()
                self.chat_times.clear()
                self.channel_state.clear()
            self.session = None
            self._log("Live mode stopped.")

    async def _poll(self, helix: Helix) -> None:
        with self.lock:
            channels = list(self.channels)
        unknown = [c for c in channels if c not in self.ids]
        if unknown:
            found = await helix.users(unknown)
            for login in unknown:
                if login in found:
                    self.ids[login] = found[login]["id"]
                else:
                    self._set_channel(login, status="not_found", text="No Twitch channel with this name")
        watched = {self.ids[c]: c for c in channels if c in self.ids}
        streams = await helix.live_streams(list(watched))
        now_active = {}
        for uid, login in watched.items():
            s = streams.get(uid)
            if not s:
                self._set_channel(login, status="offline", text="Offline")
            elif s.get("game_id") != self.game_id:
                self._set_channel(login, status="other_game", text=f"Live, playing {s.get('game_name') or 'something else'}",
                                  title=s.get("title", ""), viewers=s.get("viewer_count", 0))
            else:
                now_active[uid] = login
                self._set_channel(login, status="watching", text=f"Live in {self.category}",
                                  title=s.get("title", ""), viewers=s.get("viewer_count", 0))
        for uid in set(self.active) - set(now_active):
            login = self.active[uid]
            self._log(f"{login} left {self.category} or went offline. Stopped watching chat.")
            sub = self.subs.pop(uid, None)
            if sub and self.session and self.session.connected:
                try:
                    await helix.unsubscribe(sub)
                except Exception:
                    pass
            with self.lock:
                self.detectors.pop(uid, None)
                self.chat_times.pop(uid, None)
        added = set(now_active) - set(self.active)
        with self.lock:
            self.active = now_active
            for uid in added:
                self.detectors[uid] = Detector(self.tcfg, started=time.time())
                self.chat_times[uid] = deque(maxlen=5000)
        for uid in added:
            self._log(f"{now_active[uid]} is live in {self.category}. Watching chat.")
            if self.session and self.session.session_id:
                await self._subscribe(helix, uid, self.session.session_id)

    async def _subscribe(self, helix: Helix, uid: str, session_id: str) -> None:
        login = self.active.get(uid, uid)
        try:
            self.subs[uid] = await helix.subscribe_chat(uid, self.bot["user_id"], session_id)
        except HelixError as e:
            if e.status == 409:              # already subscribed on this connection
                return
            why = "the bot may be banned from this chat" if e.status == 403 else e.message
            self._log(f"Can't read {login}'s chat ({why}).")
            self._set_channel(login, status="error", text=f"Can't read chat: {why}")

    async def _on_welcome(self, helix: Helix, session_id: str, moved: bool) -> None:
        if moved:
            return                           # subscriptions came along to the new server
        self.subs.clear()
        now = time.time()
        with self.lock:
            for uid in self.active:          # after a gap, start the "normal" level afresh
                self.detectors[uid] = Detector(self.tcfg, started=now)
        for uid in list(self.active):
            try:
                await self._subscribe(helix, uid, session_id)
            except LoginNeeded as e:
                self.error = str(e)
                self._stop.set()
                return
            except Exception as e:
                self._log(f"Couldn't subscribe to chat ({type(e).__name__}).")
        if self.subs:
            self._log(f"Connected to chat for {len(self.subs)} channel{'s' if len(self.subs) != 1 else ''}.")

    def _on_event(self, kind: str, event: dict) -> None:
        if kind != "channel.chat.message":
            return
        uid = event.get("broadcaster_user_id")
        source = event.get("source_broadcaster_user_id")
        if source and source != uid:         # "shared chat" message from another channel
            return
        chatter = (event.get("chatter_user_login") or "").lower()
        if not chatter or chatter in self.ignore or chatter == (self.bot or {}).get("login"):
            return
        with self.lock:
            det = self.detectors.get(uid)
            if det is None:
                return
            now = time.time()
            det.add(now, chatter, (event.get("message") or {}).get("text", ""))
            self.chat_times[uid].append(now)

    def _on_revoked(self, sub: dict) -> None:
        uid = (sub.get("condition") or {}).get("broadcaster_user_id")
        login = self.active.get(uid, uid)
        self.subs.pop(uid, None)
        reason = sub.get("status", "unknown reason")
        self._log(f"Twitch stopped sending {login}'s chat ({reason}).")
        if reason == "authorization_revoked":
            self.error = "The bot's Twitch login was cancelled. Log in again."
            if self._stop:
                self._stop.set()

    def _tick(self, now: float) -> None:
        with self.lock:
            for uid, det in list(self.detectors.items()):
                spike = det.check(now)
                if spike:
                    self._record(self.active.get(uid, uid), spike.reason)

    def _record(self, login: str, reason: str) -> None:
        row = {"time": datetime.now().isoformat(timespec="seconds"), "channel": login, "mode": "test",
               "status": "would_clip", "url": "", "reason": reason}
        self.moments.append(row)
        self._log(f"Would clip {login}: {reason}")
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.csv_path.exists()
            with self.csv_path.open("a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        except OSError as e:
            self._log(f"Couldn't write to {self.csv_path.name} ({e}).")
