"""Talking to Twitch's API (Helix), politely.

- Sends the app's Client ID and the bot's token with every request.
- 401 (token expired): refreshes the token once and tries again.
- 429 (too many requests): waits until Twitch's Ratelimit-Reset time, then tries again.
- 5xx / network hiccups: a couple of short retries.
"""
import asyncio
import time

import httpx

from clipper.live.auth import TwitchAuth

API = "https://api.twitch.tv/helix"


class HelixError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Twitch API error {status}: {message}")
        self.status = status
        self.message = message


class Helix:
    def __init__(self, auth: TwitchAuth, client: httpx.AsyncClient | None = None):
        self.auth = auth
        self.client = client or httpx.AsyncClient(base_url=API, timeout=20)
        self.calls = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        refreshed = False
        for attempt in range(4):
            token = self.auth.access_token
            headers = {"Client-Id": self.auth.client_id, "Authorization": f"Bearer {token}"}
            try:
                r = await self.client.request(method, path, headers=headers, **kw)
                self.calls += 1
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 401 and not refreshed:
                refreshed = True
                await asyncio.to_thread(self.auth.refresh, token)
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset") or time.time() + 5)
                await asyncio.sleep(min(60.0, max(1.0, reset - time.time())))
                continue
            if r.status_code >= 500 and attempt < 3:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise HelixError(r.status_code, msg)
            return r
        raise HelixError(r.status_code, "gave up after several tries")

    async def get(self, path: str, params) -> list[dict]:
        return (await self.request("GET", path, params=params)).json().get("data", [])

    # ---- the few calls live mode needs ----
    async def game_id(self, name: str) -> str | None:
        data = await self.get("/games", {"name": name})
        return data[0]["id"] if data else None

    async def users(self, logins: list[str]) -> dict[str, dict]:
        """login -> user, for up to 100 logins."""
        out = {}
        for i in range(0, len(logins), 100):
            for u in await self.get("/users", [("login", x) for x in logins[i:i + 100]]):
                out[u["login"].lower()] = u
        return out

    async def live_streams(self, user_ids: list[str]) -> dict[str, dict]:
        """user_id -> stream, only for channels that are live right now."""
        out = {}
        for i in range(0, len(user_ids), 100):
            params = [("user_id", x) for x in user_ids[i:i + 100]] + [("first", "100")]
            for s in await self.get("/streams", params):
                if s.get("type") == "live":
                    out[s["user_id"]] = s
        return out

    async def subscribe_chat(self, broadcaster_id: str, bot_id: str, session_id: str) -> str:
        r = await self.request("POST", "/eventsub/subscriptions", json={
            "type": "channel.chat.message", "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id, "user_id": bot_id},
            "transport": {"method": "websocket", "session_id": session_id}})
        return r.json()["data"][0]["id"]

    async def unsubscribe(self, subscription_id: str) -> None:
        try:
            await self.request("DELETE", "/eventsub/subscriptions", params={"id": subscription_id})
        except HelixError as e:
            if e.status != 404:          # already gone is fine
                raise
