"""Takes the project page screenshots at 2x resolution (sharp on high-DPI screens).

Run the app from source first:   python -m clipper web --no-browser --port 8766
Then:                            python packaging/screenshots.py

Uses a hidden (headless) copy of Chrome controlled through the DevTools protocol, in dark mode,
with a 1440x900 window at 2x pixel density. Only the self-made and Creative Commons test videos
are shown. Saves site/img/clips.webp, facecam.webp and form.webp.
"""
import asyncio
import base64
import io
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "img"
APP = "http://127.0.0.1:8766/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
SHOW_ONLY = "Big Buck Bunny"           # the Creative Commons test video
W, H, SCALE = 1440, 900, 2

STAGE = """
(async () => {
  // Only list the self-made and Creative Commons test videos.
  const realFetch = window.fetch.bind(window);
  window.fetch = async (u, o) => {
    const r = await realFetch(u, o);
    if (String(u) === '/api/jobs') {
      const data = (await r.json()).filter(j => /Big Buck Bunny|speech_test/.test(j.title));
      return new Response(JSON.stringify(data.slice(0, 3)), {status: 200, headers: {'content-type': 'application/json'}});
    }
    return r;
  };
  const all = await (await realFetch('/api/jobs')).json();
  selected = all.find(j => j.title.includes('%s')).id; view = null; lastDetailKey = '';
  await refresh();
  // wait for the clip previews to show a frame
  await Promise.all([...document.querySelectorAll('video')].map(v => v.readyState >= 2 ? 0 :
    new Promise(res => { v.addEventListener('loadeddata', res, {once: true}); setTimeout(res, 8000); })));
  return document.querySelectorAll('video').length;
})()
""" % SHOW_ONLY


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, code):
        r = await self.send("Runtime.evaluate", expression=code, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r.get("result", {}).get("value")

    async def rect(self, selector, pad=0):
        return await self.js(f"""(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();
            return {{x: Math.max(0, r.x - {pad}), y: Math.max(0, r.y - {pad}), width: r.width + {2 * pad}, height: r.height + {2 * pad}}}; }})()""")

    async def shot(self, name, clip):
        """Capture losslessly, then save as WebP: as sharp as PNG for UI text, a fraction of the size."""
        from PIL import Image
        data = await self.send("Page.captureScreenshot", format="png", clip={**clip, "scale": 1})
        img = Image.open(io.BytesIO(base64.b64decode(data["data"]))).convert("RGB")
        path = OUT / f"{name}.webp"
        img.save(path, "WEBP", quality=92, method=6)
        print(f"saved {path.name}: {img.width}x{img.height} px, {path.stat().st_size // 1024} KB "
              f"(shown at {img.width // SCALE}x{img.height // SCALE})")


async def main():
    profile = tempfile.mkdtemp(prefix="shots-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                               f"--user-data-dir={profile}", "--hide-scrollbars", "--mute-audio",
                               f"--window-size={W},{H}", "about:blank"])
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list"))
                break
            except OSError:
                time.sleep(0.2)
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            p = Page(ws)
            await p.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=SCALE, mobile=False)
            await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
            await p.send("Page.enable")
            await p.send("Page.navigate", url=APP)
            await asyncio.sleep(3)
            print("clip previews:", await p.js(STAGE))
            await asyncio.sleep(1.5)

            # 1. The main view: sidebar + video with the sunset line + the first row of clips
            await p.js("document.querySelector('#content').scrollTop = 0")
            await p.shot("clips", {"x": 0, "y": 0, "width": W, "height": 760})

            # 2. The facecam editor
            await p.js("""(async () => {
                document.querySelector('#reLayout button[data-v=facecam]').click();
                fcDraft = {layout: 'facecam', facecam: [1336, 95, 370, 318]};
                document.querySelector('#fcText').value = '1336, 95, 370, 318';
                const img = document.querySelector('#fcImg');
                if (!img.complete) await new Promise(r => img.onload = r);
                drawBox();
                document.querySelector('#reLayout').closest('.card').scrollIntoView({block: 'center'});
            })()""")
            await asyncio.sleep(1)
            box = await p.js("(() => { const r = document.querySelector('#reLayout').closest('.card').getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; })()")
            await p.shot("facecam", box)

            # 3. The New video form, filled in with the Creative Commons example
            await p.js("""(() => {
                document.querySelector('#newBtn').click();
                document.querySelector('#url').value = 'https://www.youtube.com/watch?v=aqz-KE-bpKQ';
                document.querySelector('#creator').value = 'Blender Foundation';
                document.querySelector('#permission').value = 'Licensed CC BY 3.0 (blender.org)';
                document.activeElement.blur();
                document.querySelector('#content').scrollTop = 0;
            })()""")
            await asyncio.sleep(1)
            await p.shot("form", await p.rect("#newView", 24))
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
