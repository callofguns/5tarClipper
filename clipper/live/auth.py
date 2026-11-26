"""Logging the Twitch bot account in, and keeping it logged in.

Uses Twitch's "device code" login, made for apps like this one that run on someone's own PC:
the app shows a short code, you open twitch.tv/activate (signed in as the BOT account),
type the code and press Authorize. No password ever passes through the app, and no client
secret is needed (the Twitch app is registered as a "Public" client).

The tokens are saved in a ".env" file in the app's data folder (never in the project's Git):
- access token: lasts about 4 hours, then it's refreshed automatically
- refresh token: one-time use, so every new one is saved straight away
Twitch requires checking the token when starting and every hour (validate()).
"""
import threading
import time

import httpx
from dotenv import dotenv_values, set_key, unset_key

from clipper.config import DATA_ROOT

ENV_PATH = DATA_ROOT / ".env"
SCOPES = ["user:read:chat", "clips:edit"]      # read chat now; make clips in the next update
BUILT_IN_CLIENT_ID = ""      # 5tarClipper's own Twitch app (filled in once it's registered)
ID_URL = "https://id.twitch.tv/oauth2"


class LoginNeeded(Exception):
    """The bot isn't logged in (or Twitch logged it out): log in again from the Live page."""


class TwitchAuth:
    def __init__(self, env_path=ENV_PATH, http: httpx.Client | None = None):
        self.env_path = env_path
        self.http = http or httpx.Client(timeout=20)
        self._lock = threading.RLock()
        self.user: dict | None = None             # {"login", "user_id", "scopes"} after validate()
        self.login_state: dict = {"state": "idle"}
        self._login_cancel = threading.Event()

    # ------------------------------------------------------------ stored values
    def _env(self) -> dict:
        return dotenv_values(self.env_path) if self.env_path.exists() else {}

    def _save(self, **values) -> None:
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.env_path.exists():
            self.env_path.write_text("# 5tarClipper's Twitch login. Private: don't share this file.\n",
                                     encoding="utf-8")
        for k, v in values.items():
            if v:
                set_key(str(self.env_path), k, v)
            else:
                unset_key(str(self.env_path), k)

    @property
    def client_id(self) -> str:
        return (self._env().get("TWITCH_CLIENT_ID") or BUILT_IN_CLIENT_ID).strip()

    def set_client_id(self, value: str) -> None:
        value = value.strip()
        if not (20 <= len(value) <= 40 and value.isalnum()):
            raise ValueError("That doesn't look like a Twitch Client ID (about 30 letters and numbers).")
        with self._lock:
            if value != self.client_id:
                self.logout()                  # tokens belong to the old app
            self._save(TWITCH_CLIENT_ID=value)

    @property
    def access_token(self) -> str:
        return self._env().get("TWITCH_ACCESS_TOKEN") or ""

    @property
    def has_tokens(self) -> bool:
        return bool(self.access_token)

    # ------------------------------------------------------------ device code login
    def start_login(self) -> dict:
        """Ask Twitch for a code to show; a background thread waits for you to approve it."""
        if not self.client_id:
            raise LoginNeeded("Add your Twitch app's Client ID first.")
        r = self.http.post(f"{ID_URL}/device", data={"client_id": self.client_id, "scopes": " ".join(SCOPES)})
        if r.status_code != 200:
            raise LoginNeeded(f"Twitch didn't accept the login request ({_message(r)}). "
                              "Check the Client ID, and that the app's Client Type is Public.")
        d = r.json()
        self._login_cancel.set()                       # stop any earlier attempt
        self._login_cancel = cancel = threading.Event()
        self.login_state = {"state": "waiting", "user_code": d["user_code"],
                            "verification_uri": d["verification_uri"],
                            "expires_at": time.time() + d.get("expires_in", 1800)}
        threading.Thread(target=self._wait_for_approval, daemon=True,
                         args=(d["device_code"], max(1, d.get("interval", 5)), cancel)).start()
        return self.login_state

    def cancel_login(self) -> None:
        self._login_cancel.set()
        self.login_state = {"state": "idle"}

    def _wait_for_approval(self, device_code: str, interval: int, cancel: threading.Event) -> None:
        expires_at = self.login_state["expires_at"]
        while not cancel.wait(interval):
            if time.time() > expires_at:
                self.login_state = {"state": "error", "error": "The code expired. Press Log in to get a new one."}
                return
            try:
                r = self.http.post(f"{ID_URL}/token", data={
                    "client_id": self.client_id, "scopes": " ".join(SCOPES), "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except httpx.HTTPError:
                continue                               # network blip: keep waiting
            if r.status_code == 200:
                tok = r.json()
                with self._lock:
                    self._save(TWITCH_ACCESS_TOKEN=tok["access_token"], TWITCH_REFRESH_TOKEN=tok["refresh_token"])
                try:
                    self.validate()
                    self.login_state = {"state": "done"}
                except LoginNeeded as e:
                    self.login_state = {"state": "error", "error": str(e)}
                return
            msg = _message(r)
            if "authorization_pending" in msg or "slow_down" in msg:
                if "slow_down" in msg:
                    interval += 5
                continue
            self.login_state = {"state": "error", "error": f"Twitch said: {msg}. Press Log in to try again."}
            return

    # ------------------------------------------------------------ keeping the login working
    def validate(self) -> dict:
        """Check the token with Twitch (required at start and hourly). Refreshes it if it expired."""
        with self._lock:
            for attempt in range(2):
                token = self.access_token
                if not token:
                    raise LoginNeeded("Log in to Twitch with your bot account.")
                r = self.http.get(f"{ID_URL}/validate", headers={"Authorization": f"OAuth {token}"})
                if r.status_code == 200:
                    d = r.json()
                    missing = [s for s in SCOPES if s not in (d.get("scopes") or [])]
                    if missing:
                        raise LoginNeeded("The login is missing permissions (" + ", ".join(missing) +
                                          "). Log out and log in again.")
                    self.user = {"login": d["login"], "user_id": d["user_id"], "scopes": d["scopes"]}
                    return self.user
                if r.status_code == 401 and attempt == 0:
                    self.refresh()
                    continue
                r.raise_for_status()
            raise LoginNeeded("Twitch logged the bot out. Log in again.")

    def refresh(self, failed_token: str | None = None) -> str:
        """Swap the refresh token for a new access token. Safe to call from several places at once:
        if another caller already refreshed since `failed_token` was used, just return the new one."""
        with self._lock:
            if failed_token and self.access_token and self.access_token != failed_token:
                return self.access_token
            refresh = self._env().get("TWITCH_REFRESH_TOKEN")
            if not refresh:
                raise LoginNeeded("Log in to Twitch with your bot account.")
            r = self.http.post(f"{ID_URL}/token", data={
                "grant_type": "refresh_token", "refresh_token": refresh, "client_id": self.client_id})
            if r.status_code in (400, 401):
                self._save(TWITCH_ACCESS_TOKEN="", TWITCH_REFRESH_TOKEN="")
                self.user = None
                raise LoginNeeded("Twitch logged the bot out (logins last up to 30 days). Log in again.")
            r.raise_for_status()
            tok = r.json()
            self._save(TWITCH_ACCESS_TOKEN=tok["access_token"],
                       TWITCH_REFRESH_TOKEN=tok.get("refresh_token") or refresh)
            return tok["access_token"]

    def logout(self) -> None:
        with self._lock:
            token = self.access_token
            if token and self.client_id:
                try:                               # tell Twitch to cancel it too
                    self.http.post(f"{ID_URL}/revoke", data={"client_id": self.client_id, "token": token})
                except httpx.HTTPError:
                    pass
            self._save(TWITCH_ACCESS_TOKEN="", TWITCH_REFRESH_TOKEN="")
            self.user = None
            self.login_state = {"state": "idle"}


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message") or r.json().get("error") or r.status_code)
    except ValueError:
        return f"HTTP {r.status_code}"
