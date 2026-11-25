"""Runs one job through every step: download -> audio -> transcript -> rank -> cut points -> render.

Every step saves its result in the job's folder, so Retry continues where it stopped.
"""
import json
import re
import shutil
from datetime import datetime

from clipper.config import project_path
from clipper.download import download
from clipper.tools import find_program, run, video_info
from clipper.video import audio, boundaries, captions, rank, render, transcribe


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "clip"


def run_job(job, cfg: dict, mode: str = "full") -> None:
    """job is a clipper.jobs.Job. mode "full" = everything, "render" = only re-make the clips."""
    vcfg = cfg["video"]
    work = job.dir
    source = work / job.data["source"].get("file", "source.mp4")

    # --- 1. download ---
    if not source.exists():
        if job.data["source"]["type"] != "url":
            raise FileNotFoundError("The uploaded video is missing from the job folder.")
        job.stage("download", "Downloading video")
        meta = download(job.data["source"]["url"], work, job.progress)
        job.update(title=meta["title"] or job.data.get("title"), uploader=meta["uploader"])
        job.log(f"Downloaded \"{meta['title']}\" by {meta['uploader'] or 'unknown'}.")

    info = video_info(source)
    job.update(video=info)
    frame = work / "frame.jpg"
    if not frame.exists():
        run([find_program("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{min(info['duration'] * 0.25, 60):.2f}", "-i", str(source),
             "-frames:v", "1", "-q:v", "3", str(frame)], "Saving a preview frame")

    if mode == "full":
        # --- 2. loud moments ---
        job.stage("audio", "Finding loud moments")
        wav = audio.extract_audio(source, work / "audio.wav")
        db_curve = audio.loudness_curve(wav)
        loud = audio.find_loud_moments(db_curve, vcfg["loud_db_above_median"], vcfg["loud_min_seconds"])
        job.update(loud=loud)
        job.log(f"Found {len(loud)} loud moments.")

        # --- 3. transcript ---
        job.stage("transcribe", "Transcribing speech")
        tr = transcribe.transcribe(wav, work / "transcript.json", vcfg,
                                   on_progress=lambda f: job.progress(f, f"{f * 100:.0f}% of the video"),
                                   log=job.log)
        words = sum(len(s["words"]) for s in tr["segments"])
        job.log(f"Transcript: {words} words"
                + (f" in {tr['seconds_taken']}s." if "seconds_taken" in tr else "."))

        # --- 4. pick moments (Claude, local AI or no AI) ---
        choice = job.data.get("picker", "auto")
        picker = rank.resolve_picker(choice, vcfg)
        job.stage("rank", f"{rank.PICKER_NAMES[picker]} is picking the best moments")
        moments, picker = rank.rank_moments(
            tr, loud, db_curve, info["duration"], vcfg, job.data["creator"], work,
            (work / "transcript.json").stat().st_mtime, choice=choice, log=job.log)
        job.update(picked_by=picker)

        # --- 5. fix cut points ---
        job.stage("plan", "Fixing cut points")
        plan = []
        who = rank.PICKER_NAMES[picker]
        for m in moments:
            s, e, notes = boundaries.adjust(m["start"], m["end"], tr, loud, info["duration"],
                                            vcfg["min_clip_seconds"], vcfg["max_clip_seconds"])
            plan.append({**m, "picked_start": m["start"], "picked_end": m["end"],
                         "start": s, "end": e, "cut_notes": notes, "picked_by": picker})
            job.log(f"\"{m['title']}\": {who} {mmss(m['start'])}-{mmss(m['end'])} -> "
                    f"final {mmss(s)}-{mmss(e)} ({e - s:.0f}s)" + (f" [{'; '.join(notes)}]" if notes else ""))
        job.update(plan=plan)
    else:
        tr = json.loads((work / "transcript.json").read_text(encoding="utf-8"))
        plan = job.data.get("plan") or []
        if not plan:
            raise RuntimeError("No moments picked yet, so there's nothing to re-render. Press Retry.")

    # --- 6. render ---
    layout, facecam = job.data["layout"], job.data["facecam"]
    if layout == "facecam":
        render.check_facecam_fits(facecam, info["width"], info["height"])
    out_dir = clip_folder(job)
    if out_dir.exists():
        shutil.rmtree(out_dir)          # re-render replaces the old clips
    out_dir.mkdir(parents=True)
    all_words = boundaries.all_words(tr)
    _, cap_y = render.layout_filter(layout, facecam)
    clips = []
    job.update(clips=[])
    for i, p in enumerate(plan, 1):
        job.stage("render", f"Rendering clip {i} of {len(plan)}")
        job.progress((i - 1) / len(plan), p["title"])
        name = f"{i:02d}_{slug(p['title'])}"
        chunks = captions.caption_chunks(all_words, p["start"], p["end"], vcfg["caption_words_per_line"])
        ass = captions.write_ass(work / f"{name}.ass", chunks, vcfg["caption_font"],
                                 vcfg["caption_size"], cap_y) if chunks else None
        render.render_clip(source, out_dir / f"{name}.mp4", p["start"], p["end"], layout,
                           facecam, ass, info["fps"])
        meta = {"file": f"{name}.mp4", "title": p["title"], "reason": p["reason"],
                "score": p["score"], "start": p["start"], "end": p["end"],
                "picked_by": p.get("picked_by", "claude"),
                "picked_start": p.get("picked_start", p.get("claude_start")),
                "picked_end": p.get("picked_end", p.get("claude_end")),
                "cut_notes": p["cut_notes"], "layout": layout,
                "source": job.data["source"].get("url") or job.data["source"].get("filename"),
                "creator": job.data["creator"], "permission": job.data["permission"],
                "rendered_at": datetime.now().isoformat(timespec="seconds"),
                "status": "needs review - not posted"}
        (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        clips.append(meta)
        job.update(clips=clips)
    job.log(f"{len(clips)} clips saved to {out_dir}. Nothing was posted.")


def output_root(cfg: dict):
    return project_path(cfg["video"]["output_folder"])


def clip_folder(job):
    return project_path(job.cfg_video["output_folder"]) / job.folder_name
