"""一鍵「這週很忙」：本週剩下的日子減量，再按一次恢復。"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["POLL_MINUTES"] = "0"
os.environ["INVITE_CODE"] = "friends"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_busy_week_toggle(client):
    r = client.post("/api/register", json={"username": "busy_user", "password": "secret12", "invite": "friends"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/garmin/demo", headers=h)
    client.post("/api/sync", headers=h)
    before = client.get("/api/week", headers=h).json()
    assert before["busy"] is False

    on = client.post("/api/week/busy", json={"on": True}, headers=h).json()
    assert on["busy"] is True and on["planned_km"] <= before["planned_km"]
    # 按兩次不會重複加
    client.post("/api/week/busy", json={"on": True}, headers=h)
    evs = [e for e in client.get("/api/prefs", headers=h).json()["events"] if e["name"] == "這週很忙"]
    assert len(evs) == 1 and evs[0]["type"] == "travel"

    off = client.post("/api/week/busy", json={"on": False}, headers=h).json()
    assert off["busy"] is False and off["planned_km"] == before["planned_km"]
