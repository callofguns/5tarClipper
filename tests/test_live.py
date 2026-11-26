"""Live mode against a fake Twitch (a local WebSocket server + canned API replies). No internet needed."""
import asyncio
import json
import threading
import time
import uuid

import httpx
import pytest
from websockets.asyncio.server import serve

from clipper.live.auth import LoginNeeded, TwitchAuth
from clipper.live.eventsub import EventSubSession
from clipper.live.helix import API, Helix, HelixError
from clipper.live.manager import LiveManager, clean_channel

SCOPES = ["user:read:chat", "clips:edit"]


def msg(kind, payload, sub_type=None, mid=None):
    meta = {"message_id": mid or uuid.uuid4().hex, "message_type": kind,
            "message_timestamp": "2026-11-26T00:00:00Z"}
    if sub_type:
        meta["subscription_type"] = sub_type
    return json.dumps({"metadata": meta, "payload": payload})


def welcome(sid, keepalive=10):
    return msg("session_welcome", {"session": {"id": sid, "status": "connected",
                                               "keepalive_timeout_seconds": keepalive}})


def chat(broadcaster, chatter, text, source=None):
    return msg("notification", {"subscription": {"type": "channel.chat.message"},
                                "event": {"broadcaster_user_id": broadcaster, "chatter_user_login": chatter,
                                          "chatter_user_id": chatter, "message": {"text": text},
                                          "source_broadcaster_user_id": source}},
               sub_type="channel.chat.message")


def make_auth(tmp_path, handler, tokens=True):
    env = tmp_path / ".env"
    env.write_text("TWITCH_CLIENT_ID=abcdefghijklmnopqrstuvwxyz1234\n" +
                   ("TWITCH_ACCESS_TOKEN=old\nTWITCH_REFRESH_TOKEN=r1\n" if tokens else ""))
    return TwitchAuth(env_path=env, http=httpx.Client(transport=httpx.MockTransport(handler)))


# ------------------------------------------------------------------ login
def test_device_code_login(tmp_path):
    polls = []

    def handler(req):
        if req.url.path == "/oauth2/device":
            assert b"scopes=user%3Aread%3Achat+clips%3Aedit" in req.content
            return httpx.Response(200, json={"device_code": "dev", "user_code": "ABCD1234", "expires_in": 60,
                                             "interval": 0, "verification_uri": "https://www.twitch.tv/activate?public=true&device-code=ABCD1234"})
        if req.url.path == "/oauth2/token":
            polls.append(1)
            if len(polls) < 2:
                return httpx.Response(400, json={"status": 400, "message": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 14000})
        if req.url.path == "/oauth2/validate":
            return httpx.Response(200, json={"login": "mybot", "user_id": "999", "scopes": SCOPES})
        raise AssertionError(req.url)

    auth = make_auth(tmp_path, handler, tokens=False)
    st = auth.start_login()
    assert st["user_code"] == "ABCD1234" and st["state"] == "waiting"
    for _ in range(50):
        if auth.login_state["state"] != "waiting":
            break
        time.sleep(0.1)
    assert auth.login_state == {"state": "done"}
    assert auth.access_token == "new" and auth.user["login"] == "mybot"
    assert "TWITCH_REFRESH_TOKEN='r2'" in (tmp_path / ".env").read_text()


def test_expired_token_is_refreshed_and_saved(tmp_path):
    def handler(req):
        if req.url.path == "/oauth2/validate":
            ok = req.headers["authorization"] == "OAuth new"
            return httpx.Response(200 if ok else 401, json={"login": "mybot", "user_id": "999", "scopes": SCOPES})
        if req.url.path == "/oauth2/token":
            assert b"refresh_token=r1" in req.content and b"client_secret" not in req.content
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})
        raise AssertionError(req.url)

    auth = make_auth(tmp_path, handler)
    assert auth.validate()["login"] == "mybot"
    assert auth.access_token == "new"
    assert "r2" in (tmp_path / ".env").read_text()     # one-time refresh tokens: the new one is kept


def test_dead_refresh_token_asks_to_log_in_again(tmp_path):
    def handler(req):
        if req.url.path == "/oauth2/validate":
            return httpx.Response(401, json={"status": 401, "message": "invalid access token"})
        return httpx.Response(400, json={"status": 400, "message": "Invalid refresh token"})

    auth = make_auth(tmp_path, handler)
    with pytest.raises(LoginNeeded):
        auth.validate()
    assert not auth.has_tokens


def test_missing_permission_is_explained(tmp_path):
    auth = make_auth(tmp_path, lambda req: httpx.Response(200, json={"login": "b", "user_id": "1", "scopes": ["user:read:chat"]}))
    with pytest.raises(LoginNeeded, match="clips:edit"):
        auth.validate()


def test_client_id_is_checked(tmp_path):
    auth = make_auth(tmp_path, lambda req: httpx.Response(200), tokens=False)
    with pytest.raises(ValueError):
        auth.set_client_id("not a client id!")


# ------------------------------------------------------------------ API manners
def test_helix_waits_on_rate_limit_and_refreshes_on_401(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})
        if len(calls) == 1:
            return httpx.Response(429, headers={"Ratelimit-Reset": str(int(time.time()))})
        if req.headers["authorization"] != "Bearer new":
            return httpx.Response(401, json={"message": "Invalid OAuth token"})
        return httpx.Response(200, json={"data": [{"id": "G6"}]})

    auth = make_auth(tmp_path, handler)
    helix = Helix(auth, httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler)))
    assert asyncio.run(helix.game_id("Grand Theft Auto VI")) == "G6"
    assert calls.count("/helix/games") == 3 and "/oauth2/token" in calls


def test_helix_error_message(tmp_path):
    handler = lambda req: httpx.Response(403, json={"message": "subscription missing proper authorization"})
    helix = Helix(make_auth(tmp_path, handler), httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler)))
    with pytest.raises(HelixError) as e:
        asyncio.run(helix.subscribe_chat("1", "999", "S1"))
    assert e.value.status == 403


# ------------------------------------------------------------------ chat connection
def test_session_moves_server_and_skips_duplicates():
    async def scenario():
        welcomes, events = [], []

        async def server_b(ws):
            await ws.send(welcome("S2"))
            await ws.send(chat("1", "b", "from new server"))
            await asyncio.sleep(5)

        async def server_a(ws):
            await ws.send(welcome("S1"))
            await ws.send(chat("1", "a", "hello", ))
            dup = chat("1", "a", "dup")
            await ws.send(dup)
            await ws.send(dup)                        # Twitch may resend a message: handled once
            await ws.send(msg("session_reconnect", {"session": {"id": "S1", "reconnect_url": f"ws://127.0.0.1:{port_b}"}}))
            await asyncio.sleep(5)

        async with serve(server_a, "127.0.0.1", 0) as a, serve(server_b, "127.0.0.1", 0) as b:
            port_a, port_b = a.sockets[0].getsockname()[1], b.sockets[0].getsockname()[1]

            async def on_welcome(sid, moved):
                welcomes.append((sid, moved))
            s = EventSubSession(on_welcome, lambda k, e: events.append(e["message"]["text"]), print,
                                url=f"ws://127.0.0.1:{port_a}")
            task = asyncio.create_task(s.run())
            for _ in range(50):
                if len(events) >= 3:
                    break
                await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return welcomes, events

    welcomes, events = asyncio.run(scenario())
    assert welcomes == [("S1", False), ("S2", True)]
    assert events == ["hello", "dup", "from new server"]


def test_silent_connection_is_replaced():
    async def scenario():
        welcomes, n = [], [0]

        async def server(ws):
            n[0] += 1
            await ws.send(welcome(f"S{n[0]}", keepalive=1))
            await asyncio.sleep(10)                   # then goes silent: no keepalives

        async with serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]

            async def on_welcome(sid, moved):
                welcomes.append((sid, moved))
            s = EventSubSession(on_welcome, lambda k, e: None, print, url=f"ws://127.0.0.1:{port}")
            s.slack = 0.2
            task = asyncio.create_task(s.run())
            for _ in range(60):
                if len(welcomes) >= 2:
                    break
                await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return welcomes

    assert asyncio.run(asyncio.wait_for(scenario(), 15))[:2] == [("S1", False), ("S2", False)]


# ------------------------------------------------------------------ the whole thing
class FakeTwitch:
    """A fake EventSub server in a background thread, plus fake API replies."""

    def __init__(self):
        self.subscribed, self.deleted = [], []
        self.outbox: asyncio.Queue | None = None
        self.ready = threading.Event()
        self.port = None
        threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True).start()
        assert self.ready.wait(5)

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.outbox = asyncio.Queue()

        async def handler(ws):
            await ws.send(welcome("S1"))
            while True:
                await ws.send(await self.outbox.get())

        async with serve(handler, "127.0.0.1", 0) as srv:
            self.port = srv.sockets[0].getsockname()[1]
            self.ready.set()
            await asyncio.Future()

    def send(self, text):
        self.loop.call_soon_threadsafe(self.outbox.put_nowait, text)

    def http(self, req):
        p = req.url.path
        if p == "/oauth2/validate":
            return httpx.Response(200, json={"login": "mybot", "user_id": "999", "scopes": SCOPES})
        if p == "/helix/games":
            return httpx.Response(200, json={"data": [{"id": "G6", "name": "Grand Theft Auto VI"}]})
        if p == "/helix/users":
            known = {"streamer1": "1", "streamer2": "2"}
            logins = req.url.params.get_list("login")
            return httpx.Response(200, json={"data": [{"id": known[x], "login": x} for x in logins if x in known]})
        if p == "/helix/streams":
            return httpx.Response(200, json={"data": [
                {"user_id": "1", "user_login": "streamer1", "game_id": "G6", "game_name": "Grand Theft Auto VI",
                 "type": "live", "title": "heists", "viewer_count": 500},
                {"user_id": "2", "user_login": "streamer2", "game_id": "27471", "game_name": "Minecraft",
                 "type": "live", "title": "blocks", "viewer_count": 50}]})
        if p == "/helix/eventsub/subscriptions" and req.method == "POST":
            body = json.loads(req.content)
            self.subscribed.append(body)
            return httpx.Response(202, json={"data": [{"id": "sub-" + body["condition"]["broadcaster_user_id"]}]})
        if p == "/helix/eventsub/subscriptions" and req.method == "DELETE":
            self.deleted.append(req.url.params["id"])
            return httpx.Response(204)
        if p.startswith("/helix/clips"):
            raise AssertionError("test mode must never make clips")
        raise AssertionError(f"unexpected {req.method} {req.url}")


def wait_for(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_live_mode_end_to_end_in_test_mode(tmp_path):
    tw = FakeTwitch()
    transport = httpx.MockTransport(tw.http)
    auth = make_auth(tmp_path, tw.http)
    mgr = LiveManager({"twitch": {"warmup_seconds": 0}}, auth=auth, data_root=tmp_path,
                      helix_factory=lambda a: Helix(a, httpx.AsyncClient(base_url=API, transport=transport)),
                      eventsub_url=f"ws://127.0.0.1:{tw.port}")
    for c in ("streamer1", "https://www.twitch.tv/Streamer2", "ghost_channel"):
        mgr.add_channel(c)
    mgr.start()
    try:
        assert wait_for(lambda: tw.subscribed)
        sub = tw.subscribed[0]
        assert sub["type"] == "channel.chat.message"
        assert sub["condition"] == {"broadcaster_user_id": "1", "user_id": "999"}
        assert sub["transport"] == {"method": "websocket", "session_id": "S1"}
        assert len(tw.subscribed) == 1                   # Minecraft stream isn't watched

        st = {c["login"]: c for c in mgr.status()["channels"]}
        assert st["streamer1"]["status"] == "watching"
        assert st["streamer2"]["status"] == "other_game" and "Minecraft" in st["streamer2"]["text"]
        assert st["ghost_channel"]["status"] == "not_found"

        # Ignored: chat bots, the bot itself, shared-chat messages from another channel
        for _ in range(20):
            tw.send(chat("1", "nightbot", "KEKW KEKW"))
            tw.send(chat("1", "mybot", "KEKW"))
            tw.send(chat("1", "visitor", "KEKW", source="77"))
        time.sleep(1.5)
        assert not mgr.moments
        for i in range(8):
            tw.send(chat("1", f"viewer{i}", "KEKW no way"))
        assert wait_for(lambda: mgr.moments, 5)
        m = mgr.moments[-1]
        assert m["channel"] == "streamer1" and m["status"] == "would_clip" and m["mode"] == "test"
        assert "8 people" in m["reason"] and "KEKW" in m["reason"]
        assert "streamer1" in (tmp_path / "logs" / "clips.csv").read_text(encoding="utf-8")
    finally:
        mgr.stop()
        mgr.wait_stopped()
    assert not mgr.running
    # Channels are remembered for next time
    assert LiveManager({}, auth=auth, data_root=tmp_path).channels == ["streamer1", "streamer2", "ghost_channel"]


def test_channel_names():
    assert clean_channel(" @Shroud ") == "shroud"
    assert clean_channel("https://www.twitch.tv/some_streamer/videos") == "some_streamer"
    with pytest.raises(ValueError):
        clean_channel("not a name!")


def test_start_explains_what_is_missing(tmp_path):
    auth = make_auth(tmp_path, lambda r: httpx.Response(200), tokens=False)
    mgr = LiveManager({}, auth=auth, data_root=tmp_path)
    with pytest.raises(LoginNeeded, match="Log in"):
        mgr.start()
