"""The local web app: paste a link (or upload a file), watch progress, review the clips.

Only reachable from this PC (127.0.0.1). Nothing is ever posted anywhere.
"""
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from clipper.config import ConfigError, check_facecam
from clipper.download import check_link
from clipper.errors import UserFixable
from clipper.jobs import JobStore

STATIC = Path(__file__).parent / "static"
UPLOAD_TYPES = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
PICKERS = ("auto", "claude", "local", "none")


def create_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="GTA VI Clipper", docs_url=None, redoc_url=None)
    # Block other websites from talking to this app (DNS rebinding / cross-site requests).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    store = JobStore(cfg)
    vcfg = cfg["video"]
    app.state.store = store
    app.state.on_focus = None         # set by the Windows app to bring its window to the front

    @app.middleware("http")
    async def require_app_header(request: Request, call_next):
        # Changes must come from our own page, which always sends this header.
        if request.method in ("POST", "DELETE") and request.headers.get("x-clipper") != "1":
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return await call_next(request)

    def job_or_404(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job.")
        return job

    def parse_facecam(text: str | None) -> list[int]:
        if not text:
            return list(vcfg["facecam"])
        try:
            return check_facecam([int(v) for v in text.replace(" ", "").split(",")], "Facecam area")
        except (ValueError, ConfigError) as e:
            raise HTTPException(400, "Facecam area must be 4 whole numbers: x, y, width, height.") from e

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    # ---- updates (installed app only) ----
    @app.get("/api/update")
    def update_status():
        from clipper import updater
        return updater.status()

    @app.post("/api/update/check")
    def update_check():
        from clipper import updater
        updater.check_now()
        return updater.status()

    class UpdateInstall(BaseModel):
        force: bool = False

    @app.post("/api/update/install")
    def update_install(body: UpdateInstall):
        from clipper import updater
        st = updater.status()
        if not st["enabled"] or not st["available"]:
            raise HTTPException(409, "There's no update to install.")
        if not body.force and any(j["status"] in ("queued", "running") for j in store.all()):
            raise HTTPException(409, "busy")
        updater.start_download(install_after=True)
        return updater.status()

    class AutoUpdate(BaseModel):
        on: bool

    @app.post("/api/update/auto")
    def update_auto(body: AutoUpdate):
        from clipper import updater
        updater.set_auto(body.on)
        return updater.status()

    @app.post("/api/update/dismiss")
    def update_dismiss():
        from clipper import updater
        updater.dismiss_prompt()
        return updater.status()

    @app.post("/api/focus")
    def focus():
        """Opening the app a second time asks the running one to show its window."""
        if app.state.on_focus is None:
            return {"ok": False}
        app.state.on_focus()
        return {"ok": True}

    @app.get("/fonts/{name}")
    def font(name: str):
        """The bundled Inter font (SIL Open Font License)."""
        path = STATIC / "fonts" / name
        if name not in ("InterVariable.woff2", "Inter-OFL.txt") or not path.exists():
            raise HTTPException(404, "No such font.")
        return FileResponse(path, headers={"Cache-Control": "max-age=31536000"})

    @app.get("/favicon.png")
    def favicon():
        from clipper.config import code_path
        return FileResponse(code_path("assets/icon.png"))

    @app.get("/api/setup")
    def setup_status():
        from clipper import deps
        return deps.status()

    @app.post("/api/setup/{name}/install")
    def setup_install(name: str):
        from clipper import deps
        try:
            deps.start_install(name)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True}

    @app.get("/api/settings")
    def settings():
        return {"layout": vcfg["layout"], "facecam": vcfg["facecam"],
                "min_clip_seconds": vcfg["min_clip_seconds"],
                "max_clip_seconds": vcfg["max_clip_seconds"], "max_clips": vcfg["max_clips"],
                "claude_model": vcfg["claude_model"], "local_model": vcfg["local_model"],
                "picker": vcfg["picker"]}

    @app.get("/api/pickers")
    def pickers():
        """Which moment pickers work on this PC right now."""
        from clipper.video import local_ai, rank
        return {"claude": rank.claude_available(), "local": local_ai.model_ready(vcfg["local_model"]),
                "none": True, "auto_uses": rank.resolve_picker("auto", vcfg),
                "local_model": vcfg["local_model"], "default": vcfg["picker"]}

    @app.get("/api/jobs")
    def list_jobs():
        return store.all()

    @app.post("/api/jobs")
    def new_job(creator: str = Form(""), permission: str = Form(""), layout: str = Form("center"),
                facecam: str = Form(""), url: str = Form(""), picker: str = Form("auto"),
                file: UploadFile | None = File(None)):
        creator, permission = creator.strip(), permission.strip()
        if not creator or not permission:
            raise HTTPException(400, "Fill in the creator's name and the permission you have.")
        if layout not in ("center", "facecam"):
            raise HTTPException(400, "Layout must be center or facecam.")
        if picker not in PICKERS:
            raise HTTPException(400, "Moment picker must be auto, claude, local or none.")
        fc = parse_facecam(facecam)
        has_file = file is not None and file.filename
        if bool(url.strip()) == bool(has_file):
            raise HTTPException(400, "Paste a link OR choose a file (not both).")
        if has_file:
            ext = Path(file.filename).suffix.lower()
            if ext not in UPLOAD_TYPES:
                raise HTTPException(400, f"Unsupported file type {ext}. Use mp4, mkv, mov, webm or avi.")
            job = store.create({"type": "upload", "filename": Path(file.filename).name,
                                "file": "source" + ext}, creator, permission, layout, fc,
                               title=Path(file.filename).stem)
            with (job.dir / ("source" + ext)).open("wb") as out:
                shutil.copyfileobj(file.file, out, 8 * 1024 * 1024)
        else:
            try:
                link = check_link(url)
            except UserFixable as e:
                raise HTTPException(400, str(e)) from e
            job = store.create({"type": "url", "url": link}, creator, permission, layout, fc)
        job.update(picker=picker)
        job.log("Added. Creator: " + creator + " | Permission: " + permission)
        store.enqueue(job)
        return store.summary(job)

    class Repick(BaseModel):
        picker: str

    @app.post("/api/jobs/{job_id}/repick")
    def repick(job_id: str, body: Repick):
        """Pick the moments again with a different picker (download/transcript are reused)."""
        job = job_or_404(job_id)
        if job.data["status"] in ("queued", "running"):
            raise HTTPException(409, "It's already in progress.")
        if body.picker not in PICKERS:
            raise HTTPException(400, "Moment picker must be auto, claude, local or none.")
        job.update(picker=body.picker)
        job.log(f"Picking moments again with: {body.picker}.")
        store.enqueue(job, "full")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = job_or_404(job_id)
        with store.lock:
            return dict(job.data)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        job = job_or_404(job_id)
        if job.data["status"] in ("queued", "running"):
            raise HTTPException(409, "It's already in progress.")
        store.enqueue(job, "full")
        return {"ok": True}

    class Rerender(BaseModel):
        layout: str
        facecam: str = ""

    @app.post("/api/jobs/{job_id}/rerender")
    def rerender(job_id: str, body: Rerender):
        job = job_or_404(job_id)
        if job.data["status"] in ("queued", "running"):
            raise HTTPException(409, "It's already in progress.")
        if body.layout not in ("center", "facecam"):
            raise HTTPException(400, "Layout must be center or facecam.")
        if not job.data.get("plan"):
            raise HTTPException(409, "No moments picked yet. Press Retry instead.")
        job.update(layout=body.layout, facecam=parse_facecam(body.facecam))
        job.log(f"Re-rendering with layout {body.layout}, facecam {job.data['facecam']}.")
        store.enqueue(job, "render")
        return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    def delete(job_id: str):
        job = job_or_404(job_id)
        try:
            store.delete(job)
        except UserFixable as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/frame.jpg")
    def frame(job_id: str):
        path = job_or_404(job_id).dir / "frame.jpg"
        if not path.exists():
            raise HTTPException(404, "No preview frame yet.")
        return FileResponse(path)

    @app.get("/api/jobs/{job_id}/clips/{name}")
    def clip_file(job_id: str, name: str):
        from clipper.video.pipeline import clip_folder
        job = job_or_404(job_id)
        allowed = {c["file"] for c in job.data.get("clips", [])}
        if name not in allowed:
            raise HTTPException(404, "No such clip.")
        return FileResponse(clip_folder(job) / name, media_type="video/mp4")

    @app.post("/api/jobs/{job_id}/open-folder")
    def open_folder(job_id: str):
        from clipper.video.pipeline import clip_folder
        folder = clip_folder(job_or_404(job_id))
        if not folder.exists():
            raise HTTPException(404, "No clips folder yet.")
        os.startfile(folder)  # opens File Explorer on this PC
        return {"ok": True}

    return app
