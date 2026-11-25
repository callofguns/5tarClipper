import pytest
from fastapi.testclient import TestClient

from clipper.config import load_config
from clipper.download import check_link
from clipper.errors import UserFixable
from clipper.web.server import create_app


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc123",
    "https://youtu.be/abc123",
    "https://www.twitch.tv/videos/123456789",
])
def test_good_links(url):
    assert check_link(url) == url


@pytest.mark.parametrize("url", [
    "https://www.twitch.tv/somestreamer",          # a live channel, not a VOD
    "https://evil.example.com/watch?v=1",
    "https://youtube.com.evil.com/watch",
    "file:///C:/Windows/win.ini",
    "not a link",
])
def test_bad_links(url):
    with pytest.raises(UserFixable):
        check_link(url)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(load_config()), base_url="http://127.0.0.1")


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "GTA VI" in r.text


def test_changes_need_the_app_header(client):
    r = client.post("/api/jobs", data={"creator": "a", "permission": "b", "url": "https://youtu.be/x"})
    assert r.status_code == 403


def test_other_hosts_are_blocked():
    c = TestClient(create_app(load_config()), base_url="http://attacker.example")
    assert c.get("/api/jobs").status_code == 400


def test_creator_and_permission_required(client):
    r = client.post("/api/jobs", headers={"x-clipper": "1"},
                    data={"creator": "  ", "permission": "ok", "url": "https://youtu.be/x"})
    assert r.status_code == 400 and "creator" in r.json()["detail"]


def test_bad_link_rejected(client):
    r = client.post("/api/jobs", headers={"x-clipper": "1"},
                    data={"creator": "a", "permission": "b", "url": "https://example.com/v.mp4"})
    assert r.status_code == 400 and "YouTube" in r.json()["detail"]


def test_bad_job_id(client):
    assert client.get("/api/jobs/..%2F..%2Fsecret").status_code == 404
    assert client.get("/api/jobs/zzzzzzzzzzzz").status_code == 404
