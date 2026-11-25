"""Speech-to-text with faster-whisper, keeping the exact time of every word."""
import json
import os
import sys
import time
from pathlib import Path


_dlls_added = False


def _add_cuda_dlls() -> None:
    """On Windows, tell Python where NVIDIA's cuBLAS/cuDNN files are so the GPU can be used."""
    global _dlls_added
    if sys.platform != "win32" or _dlls_added:
        return
    from clipper.deps import cuda_dll_dirs
    for dll_dir in cuda_dll_dirs():
        os.add_dll_directory(str(dll_dir))
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ["PATH"]
    _dlls_added = True


_model = None
_model_device = None


def pick_device(vcfg: dict) -> tuple[str, str]:
    """("cuda", "float16") when an NVIDIA card and its files are available, else ("cpu", "int8")."""
    from clipper.deps import gpu_files_present, nvidia_gpu_present
    device = vcfg.get("whisper_device", "auto")
    if device == "auto":
        device = "cuda" if nvidia_gpu_present() and gpu_files_present() else "cpu"
    compute = vcfg.get("whisper_compute_type", "auto")
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def load_model(vcfg: dict, force_cpu: bool = False):
    global _model, _model_device
    device, compute = ("cpu", "int8") if force_cpu else pick_device(vcfg)
    if _model is None or _model_device != device:
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        _model = WhisperModel(vcfg["whisper_model"], device=device, compute_type=compute)
        _model_device = device
    return _model


def transcribe(wav_path: Path, out_path: Path, vcfg: dict, on_progress=None, log=print) -> dict:
    """Returns {"language", "segments": [{start, end, text, words: [{start, end, word}]}]}."""
    if out_path.exists() and out_path.stat().st_mtime >= wav_path.stat().st_mtime:
        return json.loads(out_path.read_text(encoding="utf-8"))

    # Give Whisper the sound as numbers ourselves (the WAV is already 16 kHz mono). This skips
    # faster-whisper's own file reader, which breaks with newer versions of the "av" library.
    import wave

    import numpy as np
    with wave.open(str(wav_path), "rb") as w:
        sound = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

    device, _ = pick_device(vcfg)
    try:
        return _run(sound, out_path, vcfg, on_progress, log, force_cpu=False)
    except RuntimeError as e:
        if device != "cuda":
            raise
        log(f"The graphics card didn't work ({str(e)[:150]}). Using the CPU instead (slower).")
        return _run(sound, out_path, vcfg, on_progress, log, force_cpu=True)


def _run(sound, out_path: Path, vcfg: dict, on_progress, log, force_cpu: bool) -> dict:
    device = "cpu" if force_cpu else pick_device(vcfg)[0]
    if _model is None or _model_device != device:
        log(f"Loading Whisper model '{vcfg['whisper_model']}' on the "
            f"{'graphics card' if device == 'cuda' else 'CPU'} (the very first time it downloads ~1.6 GB).")
    model = load_model(vcfg, force_cpu=force_cpu)
    t0 = time.time()
    segments, info = model.transcribe(
        sound,
        language=vcfg.get("language"),
        word_timestamps=True,
        vad_filter=True,                               # skip long silences / music-only parts
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,              # fewer made-up repeats over game noise
        beam_size=5,
    )
    result = {"language": info.language, "device": device, "segments": []}
    for seg in segments:                               # transcription actually happens here
        if on_progress and info.duration:
            on_progress(min(seg.end / info.duration, 1.0))
        words = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
                 for w in (seg.words or []) if w.word.strip()]
        if not words:
            continue
        result["segments"].append({"start": words[0]["start"], "end": words[-1]["end"],
                                   "text": seg.text.strip(), "words": words})
    result["seconds_taken"] = round(time.time() - t0, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result
