"""預估成績曲線、最近破的個人紀錄。"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["POLL_MINUTES"] = "0"

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from app import auth, db, progress  # noqa: E402
from app.garmin_client import today_local  # noqa: E402


@pytest.fixture()
def uid():
    orig = db.DB_PATH
    db.reset_for_tests(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.conn()
    u = auth.create_user("pg_user", "secret12")
    with db.tx() as c:
        c.execute("UPDATE users SET provider='demo' WHERE id=?", (u,))
    yield u
    db.reset_for_tests(orig)


def _run(uid, days_ago, km, seconds, aid):
    d = (today_local() - timedelta(days=days_ago)).isoformat()
    with db.tx() as c:
        c.execute("INSERT INTO activities(user_id, activity_id, date, start_local, distance_m, duration_s, name) VALUES(?,?,?,?,?,?,?)",
                  (uid, aid, d, f"{d} 06:00:00", km * 1000, seconds, "跑步"))


def test_prediction_gets_faster_after_a_faster_run(uid):
    _run(uid, 60, 5, 30 * 60, "a1")   # 5K 30:00
    _run(uid, 3, 5, 25 * 60, "a2")    # 5K 25:00
    pg = progress.prediction(uid, weeks=12)
    secs = [p["seconds"] for p in pg["points"] if p["seconds"]]
    assert len(pg["points"]) == 13 and secs[-1] < secs[0]
    assert pg["change_s"] < 0 and pg["now"]


def test_recent_pbs_only_counts_real_improvements(uid):
    _run(uid, 40, 5.05, 27 * 60, "old")     # 第一次 5K：不算 PB（沒有之前的紀錄）
    _run(uid, 2, 5.05, 25 * 60, "new")      # 快了：算 PB
    _run(uid, 1, 5.05, 26 * 60, "slower")   # 比較慢：不算
    pbs = progress.recent_pbs(uid, days=7)
    five = [p for p in pbs if p["key"] == "5k"]
    assert len(five) == 1 and five[0]["activity_id"] == "new" and five[0]["gain"].startswith("快了")
