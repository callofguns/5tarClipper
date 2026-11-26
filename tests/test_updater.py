import json

import pytest

from clipper import updater


@pytest.mark.parametrize("latest,current,newer", [
    ("0.4-Beta", "0.3-Beta", True),
    ("v0.4-Beta", "0.3-Beta", True),       # tags have a "v"
    ("0.3-Beta", "0.3-Beta", False),
    ("0.3-Beta", "0.4-Beta", False),
    ("0.10-Beta", "0.9-Beta", True),       # numbers, not text: 10 > 9
    ("0.3", "0.3-Beta", True),             # the final 0.3 comes after 0.3-Beta
    ("0.3-Beta", "0.3", False),
    ("1.0.0", "0.9.9", True),
    ("0.2.1-Beta", "0.2-Beta", True),
])
def test_version_order(latest, current, newer):
    assert updater.is_newer(latest, current) is newer


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(updater, "UPDATES_DIR", tmp_path / "updates")
    monkeypatch.setattr(updater, "FROZEN", True)
    monkeypatch.setattr(updater, "VERSION", "0.4-Beta")
    monkeypatch.setattr(updater, "_check_loop", lambda: None)   # no network in tests
    return tmp_path


def test_offers_auto_updates_after_a_one_click_update(app_data):
    updater.save_settings(updating_from="0.3-Beta")
    updater.startup()
    st = updater.status()
    assert st["just_updated"] == {"from": "0.3-Beta", "to": "0.4-Beta", "offer_auto": True}
    updater.set_auto(True)                        # "Turn on automatic updates"
    st = updater.status()
    assert st["auto"] is True and st["just_updated"] is None


def test_no_offer_when_auto_is_already_on(app_data):
    updater.save_settings(updating_from="0.3-Beta", auto_update=True)
    updater.startup()
    assert updater.status()["just_updated"]["offer_auto"] is False


def test_failed_update_is_forgotten(app_data):
    updater.save_settings(updating_from="0.4-Beta")   # still the same version: install didn't happen
    updater.startup()
    s = json.loads((app_data / "settings.json").read_text())
    assert s["updating_from"] is None and not s.get("just_updated")


def test_only_downloads_from_this_repos_releases():
    assert updater._safe_asset_url(       # the repo's current name
        "https://github.com/callofguns/5tarClipper/releases/download/v0.4.2-Beta/5tarClipper-Setup.exe")
    assert updater._safe_asset_url(
        "https://github.com/callofguns/GTA6-Clipper/releases/download/v0.4-Beta/GTA6-Clipper-Setup.exe")
    assert updater._safe_asset_url(       # capitals don't matter on GitHub
        "https://github.com/callofguns/gta6-clipper/releases/download/v0.4-Beta/GTA6-Clipper-Setup.exe")
    assert not updater._safe_asset_url("https://evil.example.com/GTA6-Clipper-Setup.exe")
    assert not updater._safe_asset_url(
        "https://github.com/someone-else/gta6-clipper/releases/download/v9/GTA6-Clipper-Setup.exe")


def test_disabled_when_running_from_source(monkeypatch):
    monkeypatch.setattr(updater, "FROZEN", False)
    assert updater.status()["enabled"] is False
