"""Apple Watch：iPhone 捷徑上傳。"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["POLL_MINUTES"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import apple_ingest as ai  # noqa: E402
from app import db  # noqa: E402
from app.garmin_client import today_local  # noqa: E402


def test_number_and_duration_parsing():
    assert ai.num("55 count/min") == 55 and ai.num("1,234") == 1234 and ai.num({"qty": 4.2}) == 4.2
    assert ai.minutes("0:29:47") == pytest.approx(29.78, 0.01) and ai.minutes("29:47") == pytest.approx(29.78, 0.01)
    assert ai.minutes("1 hr 5 min") == 65 and ai.minutes({"qty": 1800, "units": "s"}) == 30 and ai.minutes("45") == 45
    assert ai.km("4.0 km") == 4.0 and ai.km("2 mi") == pytest.approx(3.22, 0.01) and ai.km("5000 m") == 5
    assert ai.when("2026-11-17 20:44:00 +0800").hour == 20 and ai.when("2026-11-17T12:44:00Z").hour == 20


def test_parse_running_and_other_workouts():
    acts, days, skipped = ai.parse({"date": "2026-11-18", "resting_hr": "54 count/min", "hrv": "48 ms",
                                    "workouts": [
                                        {"type": "跑步", "start": "2026-11-17T20:44:00+08:00", "duration_min": "29:47", "distance_km": "4.0 km", "avg_hr": 135},
                                        {"type": "Outdoor Cycle", "start": "2026-11-13 06:00:00 +0800", "duration": "13 hr 8 min", "distance": {"qty": 293.9, "units": "km"}},
                                        {"type": "Yoga", "start": "2026-11-12T19:00:00+08:00", "duration_min": 40},
                                        {"type": "Running"},
                                    ]})
    run = next(a for a in acts if not a.get("cross"))
    assert run["date"] == "2026-11-17" and run["distance_m"] == 4000 and run["duration_s"] == 1787 and run["type"] == "running"
    bike = next(a for a in acts if a.get("sport") == "road_biking")
    assert bike["sport_group"] == "bike" and bike["distance_m"] == 293900
    assert any(a.get("sport") == "yoga" for a in acts)
    assert len(skipped) == 1                       # 沒有開始時間的那筆
    assert days == [{"date": "2026-11-18", "rhr": 54.0, "hrv_avg": 48.0, "sleep_hours": None, "steps": None, "active_kcal": None}]


def test_out_of_range_values_dropped():
    _, days, _ = ai.parse({"resting_hr": 3300, "hrv": 48})
    assert days[0]["rhr"] is None and days[0]["hrv_avg"] == 48


@pytest.fixture(scope="module")
def ctx():
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/register", json={"username": "applefan", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        yield c, h


def test_link_upload_and_use(ctx):
    c, h = ctx
    info = c.post("/api/apple/link", headers=h).json()
    url = info["url"]
    token = url.rsplit("/", 1)[1]
    assert "/api/ingest/apple/" in url and info["last_at"] is None
    assert c.post("/api/ingest/apple/wrong-token-xxxxxxxxxxxxxxxx", json={}).status_code == 404
    assert c.post(f"/api/ingest/apple/{token}", content=b"not json").status_code == 400
    assert c.post(f"/api/ingest/apple/{token}", json={"foo": 1}).status_code == 400

    t = today_local()
    r = c.post(f"/api/ingest/apple/{token}", json={
        "resting_hr": 52, "hrv": 61, "sleep_hours": 7.4,
        "workouts": [{"type": "Running", "start": f"{t.isoformat()}T06:30:00+08:00", "duration_min": 32, "distance_km": 5.1, "avg_hr": 140}]})
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 1 and "1 筆跑步" in r.json()["message"]
    # 同一筆再送一次不會重複
    c.post(f"/api/ingest/apple/{token}", json={"workouts": [{"type": "Running", "start": f"{t.isoformat()}T06:30:00+08:00", "duration_min": 32, "distance_km": 5.1}]})
    uid = db.q1("SELECT id FROM users WHERE username='applefan'")["id"]
    assert db.q1("SELECT COUNT(*) n FROM activities WHERE user_id=?", (uid,))["n"] == 1
    assert db.q1("SELECT rhr FROM daily WHERE user_id=? AND date=?", (uid, t.isoformat()))["rhr"] == 52

    me = c.get("/api/me", headers=h).json()
    assert me["provider"] == "apple"
    assert c.post("/api/sync", headers=h).status_code == 200          # 同步按鈕對 Apple 只重排課表
    acts = c.get("/api/activities", headers=h).json()["activities"]
    assert acts[0]["km"] == 5.1
    st = c.get("/api/apple", headers=h).json()
    assert st["last_at"] and st["last"]["runs"] == 1
    morning = c.get("/api/brief/morning", headers=h).json()
    assert next(m for m in morning["metrics"] if m["key"] == "rhr")["today"] == 52

    new = c.post("/api/apple/rotate", headers=h).json()["url"]
    assert new != url and c.post(f"/api/ingest/apple/{token}", json={"resting_hr": 50}).status_code == 404
