"""The app was renamed from "GTA VI Clipper" to 5tarClipper: people's folders must move across safely."""
from clipper import config, updater


def test_old_folder_is_moved_to_the_new_name(tmp_path):
    old, new = tmp_path / "GTA6 Clipper", tmp_path / "5tarClipper"
    (old / "tools").mkdir(parents=True)
    (old / "settings.json").write_text("{}")
    assert config.move_old_folder(old, new) == new
    assert (new / "settings.json").exists() and (new / "tools").is_dir()
    assert not old.exists()


def test_existing_new_folder_is_left_alone(tmp_path):
    old, new = tmp_path / "GTA6 Clipper", tmp_path / "5tarClipper"
    old.mkdir(); new.mkdir()
    (new / "keep.txt").write_text("new")
    assert config.move_old_folder(old, new) == new
    assert old.exists()                      # never merges or deletes anything
    assert (new / "keep.txt").read_text() == "new"


def test_fresh_install_uses_new_name(tmp_path):
    new = tmp_path / "5tarClipper"
    assert config.move_old_folder(tmp_path / "GTA6 Clipper", new) == new


def test_busy_old_folder_keeps_working(tmp_path, monkeypatch):
    old, new = tmp_path / "GTA6 Clipper", tmp_path / "5tarClipper"
    old.mkdir()
    monkeypatch.setattr(config.time, "sleep", lambda s: None)
    def busy(self, target):
        raise PermissionError("in use by the installer")
    monkeypatch.setattr(type(old), "rename", busy)
    assert config.move_old_folder(old, new, tries=3) == old   # use the old folder this time, retry next launch


def test_updater_prefers_new_installer_name_but_accepts_old(monkeypatch):
    base = "https://github.com/callofguns/GTA6-Clipper/releases/download/v0.5-Beta/"
    both = {n: base + n for n in ("5tarClipper-Setup.exe", "5tarClipper-Setup.exe.sha256",
                                  "GTA6-Clipper-Setup.exe", "GTA6-Clipper-Setup.exe.sha256")}
    old_only = {n: u for n, u in both.items() if n.startswith("GTA6")}
    pick = lambda assets: next((n for n in updater.ASSETS if n in assets and n + ".sha256" in assets), None)
    assert pick(both) == "5tarClipper-Setup.exe"
    assert pick(old_only) == "GTA6-Clipper-Setup.exe"
