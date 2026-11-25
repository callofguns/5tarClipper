"""Picks moments with a local AI model running in Ollama (https://ollama.com) on this PC.

Free and private (nothing leaves your computer), but a small local model understands videos
less well than Claude. Ollama runs as its own small background program at 127.0.0.1:11434.
"""
import json
import time

import httpx

from clipper.errors import TryLater, UserFixable

OLLAMA = "http://127.0.0.1:11434"


def server_running() -> bool:
    try:
        return httpx.get(f"{OLLAMA}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def installed_models() -> list[str]:
    try:
        r = httpx.get(f"{OLLAMA}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def _same_model(a: str, b: str) -> bool:
    norm = lambda n: n if ":" in n else n + ":latest"
    return norm(a) == norm(b)


def model_ready(model: str) -> bool:
    return server_running() and any(_same_model(m, model) for m in installed_models())


def pull(model: str, on_progress) -> None:
    """Download a model through Ollama, reporting (fraction, text) as it goes."""
    if not server_running():
        raise RuntimeError("Ollama isn't running. Install it from ollama.com and start it first.")
    with httpx.stream("POST", f"{OLLAMA}/api/pull", json={"model": model, "stream": True},
                      timeout=httpx.Timeout(60, read=600)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            ev = json.loads(line)
            if "error" in ev:
                raise RuntimeError(ev["error"])
            total, done = ev.get("total") or 0, ev.get("completed") or 0
            text = ev.get("status", "")
            if total:
                text = f"{model}: {done / 1e9:.1f} of {total / 1e9:.1f} GB"
            on_progress(done / total if total else 0.0, text)


def free_whisper_memory() -> None:
    """The graphics card is shared: unload the speech model before the AI model loads."""
    import gc

    from clipper.video import transcribe
    transcribe._model = None
    transcribe._model_device = None
    gc.collect()


def pick(prompt: str, system_prompt: str, schema: dict, vcfg: dict, log) -> list:
    model = vcfg["local_model"]
    if not server_running():
        raise UserFixable("Local AI needs Ollama running. Install it from ollama.com (it starts "
                          "by itself afterwards), or pick moments with Claude or No AI instead.")
    if not model_ready(model):
        raise UserFixable(f"The local AI model '{model}' isn't downloaded yet. "
                          "Download it from the Setup checklist.")
    free_whisper_memory()
    tokens = len(prompt) // 3 + 2000
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": prompt}],
        "format": schema,              # forces an answer in exactly our JSON shape
        "stream": False,
        "think": False,                # skip "thinking" mode: much faster, fine for this task
        "options": {"temperature": 0.2, "num_ctx": max(8192, min(32768, tokens))},
        "keep_alive": "2m",            # free the graphics card soon after
    }
    log(f"Asking the local AI model ({model}) to pick moments. This can take a minute or two.")
    t0 = time.time()
    try:
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=httpx.Timeout(30, read=1800))
        if r.status_code == 400 and "think" in r.text:      # model without a thinking switch
            body.pop("think")
            r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=httpx.Timeout(30, read=1800))
        r.raise_for_status()
    except httpx.TimeoutException as e:
        raise TryLater("The local AI model took longer than 30 minutes.") from e
    except httpx.HTTPError as e:
        raise TryLater(f"Ollama returned an error: {e}") from e
    data = r.json()
    try:
        moments = json.loads(data["message"]["content"]).get("moments", [])
    except (KeyError, ValueError, AttributeError) as e:
        raise TryLater("The local AI model's answer wasn't in the expected format. Try again.") from e
    log(f"Local AI answered in {time.time() - t0:.0f}s ({data.get('eval_count', 0)} tokens written).")
    return moments
