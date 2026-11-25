# PyInstaller recipe for the Windows app.  Build with:  pyinstaller packaging/gta6clipper.spec
# Output: dist/GTA6 Clipper/GTA6 Clipper.exe  (a folder, which the installer then packs up)
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "clipper" / "web" / "static"), "clipper/web/static"),
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "assets" / "icon.png"), "assets"),
]
datas += collect_data_files("faster_whisper")      # the voice-detection model file
datas += collect_data_files("yt_dlp_ejs")          # YouTube helper scripts
binaries = collect_dynamic_libs("ctranslate2")
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("yt_dlp")
                 + ["pystray._win32", "multipart"])

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # The NVIDIA GPU files are huge, so the app downloads them on demand instead.
    excludes=["nvidia", "pytest", "tkinter", "matplotlib", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GTA6 Clipper",
    icon=str(ROOT / "assets" / "icon.ico"),
    console=False,              # no black window: it lives in the system tray
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GTA6 Clipper", upx=False)
