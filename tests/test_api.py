import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["INVITE_CODE"] = "friends"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _user(c, name):
    r = c.post("/api/register", json={"username": name, "password": "secret12", "invite": "friends"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_invite_required(client):
    r = client.post("/api/register", json={"username": "stranger", "password": "secret12", "invite": "nope"})
    assert r.status_code == 403


def test_full_demo_flow_and_calendar(client):
    h = _user(client, "ben")
    assert client.get("/api/dashboard", headers=h).json() == {"needs_link": True}
    assert client.post("/api/garmin/demo", headers=h).status_code == 200
    r = client.post("/api/sync", headers=h)
    assert r.status_code == 200 and r.json()["synced"]["activities"] > 10
    d = client.get("/api/dashboard", headers=h).json()
    assert len(d["plan"]["days"]) == 7 and 0 <= d["readiness"]["score"] <= 100

    me = client.get("/api/me", headers=h).json()
    assert me["calendar_webcal"].startswith("webcal://")
    path = me["calendar_url"].split("testserver")[1]
    ics = client.get(path)
    assert ics.status_code == 200 and "BEGIN:VEVENT" in ics.text
    assert client.get("/cal/wrong-token.ics").status_code == 404


def test_friends_are_isolated(client):
    a, b = _user(client, "amy"), _user(client, "bob")
    client.post("/api/garmin/demo", headers=a)
    client.post("/api/sync", headers=a)
    assert client.get("/api/dashboard", headers=b).json() == {"needs_link": True}


def test_prefs_validation_and_replan(client):
    h = _user(client, "cara")
    client.post("/api/garmin/demo", headers=h)
    client.post("/api/sync", headers=h)
    assert client.put("/api/prefs", headers=h, json={"days": []}).status_code == 400
    assert client.put("/api/prefs", headers=h, json={"goal": {"type": "race", "event_date": None}}).status_code == 400
    assert client.put("/api/prefs", headers=h, json={"goal": {"type": "fun", "target_time": "abc"}}).status_code == 400
    r = client.put("/api/prefs", headers=h, json={"goal": {"type": "base", "target_time": ""}, "days": [0, 2, 4]})
    assert r.status_code == 200
    d = client.get("/api/dashboard", headers=h).json()
    assert d["plan"]["target_km"] > 0
    runs = [x for x in d["plan"]["days"] if x["kind"] not in ("rest", "done")]
    from datetime import date
    assert all(date.fromisoformat(x["date"]).weekday() in (0, 2, 4) for x in runs)


def test_checkin_rejects_past_dates(client):
    h = _user(client, "dan")
    client.post("/api/garmin/demo", headers=h)
    assert client.post("/api/checkin", headers=h, json={"date": "2020-01-01", "feeling": 3}).status_code == 400
