"""Keeps track of every video you've submitted and processes them one at a time in the background."""
import json
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from clipper.config import project_path
from clipper.errors import TryLater, UserFixable

JOB_ID = re.compile(r"^[0-9a-f]{12}$")
STAGES = ["download", "audio", "transcribe", "rank", "plan", "render"]


class Job:
    def __init__(self, store: "JobStore", data: dict):
        self.store = store
        self.data = data
        self.dir: Path = store.root / data["id"]
        self.cfg_video = store.cfg["video"]

    @property
    def folder_name(self) -> str:
        from clipper.video.pipeline import slug
        day = self.data["created_at"][:10]
        return f"{day}_{slug(self.data.get('title') or 'video')}_{self.data['id'][:6]}"

    # --- called by the pipeline while it works ---
    def update(self, **fields) -> None:
        with self.store.lock:
            self.data.update(fields)
            self.save()

    def stage(self, name: str, label: str) -> None:
        self.update(stage=name, stage_label=label, progress=None, progress_text="")
        self.log(label + "...")

    def progress(self, fraction: float, text: str = "") -> None:
        # Called very often while downloading/transcribing: only keep it in memory.
        with self.store.lock:
            self.data["progress"] = round(fraction, 3)
            self.data["progress_text"] = text

    def log(self, message: str) -> None:
        with self.store.lock:
            self.data.setdefault("log", []).append(
                {"t": datetime.now().strftime("%H:%M:%S"), "msg": message})
            self.save()

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / "job.json")


class JobStore:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.root = project_path("cache/jobs")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        for f in self.root.glob("*/job.json"):
            try:
                job = Job(self, json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError):
                continue
            if job.data["status"] in ("queued", "running"):   # the app was closed mid-way
                job.data.update(status="error", error="Stopped because the app was closed. Press Retry.")
                job.save()
            self.jobs[job.data["id"]] = job
        threading.Thread(target=self._worker, daemon=True, name="job-worker").start()

    # --- used by the web server ---
    def create(self, source: dict, creator: str, permission: str, layout: str,
               facecam: list[int], title: str = "") -> Job:
        data = {"id": uuid.uuid4().hex[:12],
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "source": source, "title": title, "uploader": "",
                "creator": creator, "permission": permission,
                "layout": layout, "facecam": facecam, "picker": "auto", "picked_by": None,
                "status": "queued", "stage": None, "stage_label": "Waiting in line",
                "progress": None, "progress_text": "", "error": None,
                "log": [], "clips": [], "plan": [], "loud": [], "video": None}
        job = Job(self, data)
        with self.lock:
            self.jobs[data["id"]] = job
            job.save()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id) if JOB_ID.match(job_id or "") else None

    def all(self) -> list[dict]:
        with self.lock:
            return sorted((self.summary(j) for j in self.jobs.values()),
                          key=lambda d: d["created_at"], reverse=True)

    def summary(self, job: Job) -> dict:
        d = {k: v for k, v in job.data.items() if k not in ("log", "plan", "loud")}
        d["clip_count"] = len(job.data.get("clips", []))
        return d

    def enqueue(self, job: Job, mode: str = "full") -> None:
        job.update(status="queued", error=None, stage_label="Waiting in line", progress=None)
        self.queue.put((job.data["id"], mode))

    def delete(self, job: Job) -> None:
        from clipper.video.pipeline import clip_folder
        if job.data["status"] in ("queued", "running"):
            raise UserFixable("Wait for this video to finish before deleting it.")
        with self.lock:
            self.jobs.pop(job.data["id"], None)
        shutil.rmtree(clip_folder(job), ignore_errors=True)
        shutil.rmtree(job.dir, ignore_errors=True)

    # --- the background worker: one video at a time (the GPU and Claude are shared) ---
    def _worker(self) -> None:
        from clipper.video.pipeline import run_job
        while True:
            job_id, mode = self.queue.get()
            job = self.jobs.get(job_id)
            if job is None or job.data["status"] != "queued":
                continue
            job.update(status="running")
            started = time.time()
            try:
                run_job(job, self.cfg, mode)
                job.update(status="done", stage="done", stage_label="Done", progress=None,
                           progress_text="")
                job.log(f"Finished in {time.time() - started:.0f}s.")
            except TryLater as e:
                job.update(status="error", error=f"{e} Press Retry later.")
                job.log(f"Paused: {e}")
            except UserFixable as e:
                job.update(status="error", error=str(e))
                job.log(f"Problem: {e}")
            except Exception as e:   # unexpected: keep the details in the log for debugging
                job.update(status="error", error=f"{type(e).__name__}: {e}")
                job.log("Unexpected error:\n" + traceback.format_exc())
