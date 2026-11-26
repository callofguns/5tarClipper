"""Reading chat through Twitch EventSub over a WebSocket.

How the connection works (from Twitch's EventSub WebSocket docs):
- Connect, and Twitch sends a "welcome" with a session id. Within 10 seconds we must subscribe to
  something (the chat of each channel we watch), or Twitch closes the connection.
- Twitch sends a "keepalive" whenever it's quiet. If nothing at all arrives for longer than the
  keepalive time, the connection is dead: reconnect.
- "session_reconnect" means Twitch is moving us to another server: connect to the new address,
  wait for its welcome, then close the old one. Subscriptions move across by themselves.
- After any other disconnect, subscriptions are gone: reconnect (waiting 1 s, 2 s, 4 s ... up to
  60 s between tries) and subscribe again.
"""
import asyncio
import json
from collections import deque
from typing import Awaitable, Callable

import websockets

URL = "wss://eventsub.wss.twitch.tv/ws"


class EventSubSession:
    def __init__(self, on_welcome: Callable[[str, bool], Awaitable[None]],
                 on_event: Callable[[str, dict], None], log: Callable[[str], None],
                 on_revoked: Callable[[dict], None] = lambda sub: None, url: str = URL):
        self.on_welcome = on_welcome          # (session_id, moved_server) -> subscribe as needed
        self.on_event = on_event              # (subscription_type, event)
        self.on_revoked = on_revoked
        self.log = log
        self.url = url
        self.slack = 5                        # extra seconds to wait beyond Twitch's keepalive time
        self.max_delay = 60
        self.session_id: str | None = None
        self.connected = False
        self._seen: deque[str] = deque(maxlen=500)     # Twitch may send a message twice

    async def _connect(self, url: str):
        ws = await websockets.connect(url, max_size=2 ** 20, ping_interval=None)
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if msg["metadata"]["message_type"] != "session_welcome":
                raise ConnectionError("Twitch didn't send a welcome message.")
            session = msg["payload"]["session"]
            return ws, session["id"], int(session.get("keepalive_timeout_seconds") or 10)
        except BaseException:
            await ws.close()
            raise

    async def run(self) -> None:
        """Stay connected until cancelled."""
        delay = 1
        while True:
            ws = None
            try:
                ws, self.session_id, keepalive = await self._connect(self.url)
                self.connected = True
                await self.on_welcome(self.session_id, False)
                delay = 1
                while True:
                    raw = await asyncio.wait_for(ws.recv(), keepalive + self.slack)
                    msg = json.loads(raw)
                    meta, payload = msg["metadata"], msg.get("payload", {})
                    kind = meta["message_type"]
                    if meta.get("message_id") in self._seen:
                        continue
                    self._seen.append(meta.get("message_id"))
                    if kind == "notification":
                        self.on_event(meta.get("subscription_type", ""), payload.get("event", {}))
                    elif kind == "session_reconnect":
                        new_url = payload["session"]["reconnect_url"]
                        self.log("Twitch is moving the chat connection to another server.")
                        new_ws, self.session_id, keepalive = await self._connect(new_url)
                        await ws.close()
                        ws = new_ws
                        await self.on_welcome(self.session_id, True)
                    elif kind == "revocation":
                        self.on_revoked(payload.get("subscription", {}))
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException,
                    ConnectionError, json.JSONDecodeError, KeyError) as e:
                why = "no message from Twitch in time" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
                self.log(f"Chat connection dropped ({why}). Reconnecting in {delay}s.")
            finally:
                self.connected = False
                self.session_id = None
                if ws is not None:
                    await ws.close()
            await asyncio.sleep(delay)
            delay = min(self.max_delay, delay * 2)
