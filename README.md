# 5tarClipper

*Formerly GTA VI Clipper.*

Paste a YouTube or Twitch VOD link, or upload a file. The app makes vertical 1080x1920 clips of the best moments, with captions. Clips are saved for you to review and are **never posted automatically**.

**Live mode** (in testing) watches Twitch channels while they're live in GTA VI and spots the moments their chat goes wild.

**Download the Windows app:** [5tarClipper-Setup.exe](https://github.com/callofguns/5tarClipper/releases/latest/download/5tarClipper-Setup.exe). Project page: https://callofguns.github.io/5tarClipper/

Only clip videos you have permission to use.

---

## Running from the source code

All commands below are typed in PowerShell **inside this folder**.

### One-time setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

You also need:
- **FFmpeg**: `winget install Gyan.FFmpeg`
- **Node.js** (or Deno): YouTube downloads need a JavaScript runtime
- Optional: **Claude Code** logged in to your Claude plan, and/or **Ollama** for the free local AI (see "Picking moments")

## Start the web app

```powershell
.\.venv\Scripts\python.exe -m clipper web
```

Your browser opens at **http://127.0.0.1:8765**. Keep the PowerShell window open while you use it, and press Ctrl+C there to stop. Only this PC can open the page; it isn't reachable from your network or the internet.

To open it in its own window instead (like the installed app), run `.\.venv\Scripts\python.exe -m clipper app`.

### Making clips
1. Paste a YouTube or Twitch VOD link, or switch to **Upload file**.
2. Fill in the **creator's name** and **the permission you have**. Both are required and are saved with every clip.
3. Pick a layout and press **Make clips**.
4. Watch the progress: Download → Loud moments → Transcript → Pick moments → Cut points → Render.
5. Review the clips on the page, download them, or press **Open folder**. They're saved in `output\review\<date>_<title>_<id>\`, each with a `.json` file holding the title, why it was picked, the times, creator and permission.

Videos are processed one at a time; extra ones wait in line.

### Facecam layout
Switch **Layout** to *Facecam on top* and **drag a box around the facecam** on the frame shown. Then press **Re-render clips with this layout**. Re-rendering is quick: it doesn't download, transcribe or pick moments again.

### If something goes wrong
The page shows what happened and a **Retry** button. Finished steps are saved, so Retry continues where it stopped. Details are in **Activity log**.

### What each step does
| Step | What it does |
|---|---|
| Download | yt-dlp downloads the video, up to 1080p |
| Loud moments | Finds explosions, crashes and screaming, because big action doesn't always show up in a transcript |
| Transcript | faster-whisper writes down every word with its exact time, on your graphics card |
| Pick moments | Claude, a local AI model or the No AI scorer picks the best 20–60 s moments, with a title and reason |
| Cut points | Moves each start and end so no sentence or loud action gets cut off |
| Render | FFmpeg makes a 1080x1920 clip with big captions |

### Picking moments
Choose per video under **Pick moments with**, or change your mind later with **Pick again**. The download and transcript are reused, so only picking and rendering run again.

| Option | What it is | Needs |
|---|---|---|
| **Automatic** | Claude if installed, else Local AI if set up, else No AI. Falls back automatically if one fails | – |
| **Claude** | Best picks and titles | Claude Code + a Claude plan |
| **Local AI** | A model running privately in [Ollama](https://ollama.com) (default `granite4.2:8b`, about 5 GB; change `local_model` in `config.yaml`) | Ollama, plus the model from the Setup checklist |
| **No AI** | Scores every stretch by loudness, reaction words ("no way", laughing...) and talking speed; titles come from what's said | Nothing |

#### Claude usage
The Claude option runs `claude -p` (Claude Code headless mode) **once per video**. It uses your Claude plan's normal usage limits, shared with claude.ai and Claude Code, and costs nothing extra. In testing, one call used about 6,000 input + 900 output tokens; most of that is fixed overhead, and a long video's transcript adds roughly 2,000 tokens per 5 minutes of talking. If you hit a limit, the job stops with a message and you press Retry later.

The app refuses to run Claude if the `ANTHROPIC_API_KEY` environment variable is set. With that set, Claude Code would bill the API instead of your plan.

Settings like the number of clips, clip lengths, caption size and the Claude model are in `config.yaml`.

## Live mode (Twitch, test mode)
The **Live** page watches Twitch channels while they're live in GTA VI and notes the moments their chat suddenly goes wild. For now it only **notes** them (in the app and in `logs/clips.csv`); making real Twitch clips comes in a later update.

One-time setup:
1. Turn on two-factor authentication on your main Twitch account.
2. Register an app at https://dev.twitch.tv/console/apps/create: any unique name, OAuth Redirect URL `http://localhost`, Category *Application Integration*, Client Type **Public**. Copy its **Client ID** into the Live page. (No client secret is needed or used.)
3. Press **Log in to Twitch** and approve the code while signed in to Twitch as your **bot account**. It asks for `user:read:chat` (read chat) and `clips:edit` (for the clipping update).
4. Add channels and press **Start watching**.

How a moment is spotted: every chat message scores 1 point, plus up to 2 for reactions like LUL, KEKW, OMG, W, "CLIP IT" and "no way". One person adds at most 3 points per 10 seconds, so a spammer can't fake it. When the last 10 seconds score at least **3x** the channel's normal level over the last 5 minutes, with at least **5** different people chatting, it's a moment. After a moment there's a 90-second wait on that channel. All of these numbers are in the `twitch:` part of `config.yaml`.

From a terminal: `python -m clipper login` logs the bot in, and `python -m clipper live --channel NAME` runs live mode without the window.

The login is kept in `.env` in the app's data folder (`%LOCALAPPDATA%\5tarClipper` for the installed app). The access token is refreshed automatically; if it's been unused for 30 days, Twitch asks you to log in again.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Building the Windows app
```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller pillow
.\.venv\Scripts\python.exe -m PyInstaller packaging\gta6clipper.spec --noconfirm   # -> dist\5tarClipper\
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=0.5-Beta packaging\installer.iss   # -> dist\5tarClipper-Setup.exe
```
Releases are built automatically by GitHub Actions (`.github/workflows/release.yml`) when a version tag like `v0.5-Beta` is pushed. Each release needs its notes in `packaging/release-notes/<tag>.md` (copy `TEMPLATE.md`), written in the same pull request as the changes; the build stops if they are missing. The project page in `site/` is published to GitHub Pages by `.github/workflows/pages.yml`.

The installed app keeps its settings, caches and downloaded tools in `%LOCALAPPDATA%\5tarClipper`, and saves clips to `Videos\5tarClipper` (folders from the old "GTA6 Clipper" name are moved there automatically).

## Secrets
Secrets go in `.env` (copy `.env.example`). `.env`, downloaded videos, clips, logs and caches are all listed in `.gitignore`, so they never go into Git.
