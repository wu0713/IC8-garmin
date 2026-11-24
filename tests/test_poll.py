"""自動偵測新資料：手錶上傳時間有變才同步、抓到新活動才推播、失敗會拉長間隔。"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["POLL_MINUTES"] = "0"

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from app import auth, db, sync  # noqa: E402
from app.config import TZ  # noqa: E402
from app.garmin_client import DemoGarmin  # noqa: E402


class FakeGarmin(DemoGarmin):
    """用範例資料當 Garmin，但多了「手錶最後上傳時間」，而且可以控制有沒有新跑步。"""
    upload = None
    hide_today = True   # True：今天的跑步還沒上傳

    def last_upload(self):
        return FakeGarmin.upload

    def fetch(self, start, end):
        acts, days = super().fetch(start, end)
        acts = [a for a in acts if a["date"] < end.isoformat()]
        if not FakeGarmin.hide_today:   # 今天剛跑完、剛上傳的一趟
            run = next(a for a in acts if not a.get("cross"))
            acts.append({**run, "activity_id": "today-run", "date": end.isoformat(), "start_local": f"{end.isoformat()} 06:30:00",
                         "name": "晨跑", "distance_m": 6200})
        return acts, days


@pytest.fixture()
def user(monkeypatch):
    orig = db.DB_PATH
    db.reset_for_tests(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.conn()
    uid = auth.create_user(f"poll{datetime.now().timestamp()}", "secret12")
    with db.tx() as c:
        c.execute("UPDATE users SET provider='garmin', garmin_tokens=? WHERE id=?", (auth.encrypt('{"fake": true}'), uid))
    monkeypatch.setattr(sync, "provider_for", lambda u, t: FakeGarmin(seed=uid))
    FakeGarmin.upload, FakeGarmin.hide_today = None, True
    yield uid
    db.reset_for_tests(orig)   # 切回原本的資料庫，不影響其他測試檔


def test_needs_sync_rules():
    t = "2026-11-24T08:00:00+00:00"
    assert not sync.needs_sync(None, None, None)                       # 問不到上傳時間
    assert not sync.needs_sync(t, t, None)                             # 跟上次看到的一樣
    assert sync.needs_sync(t, None, None)                              # 從沒同步過
    assert sync.needs_sync(t, None, "2026-11-24T15:00:00+08:00")       # 上傳（16:00 台北）比上次同步晚
    assert not sync.needs_sync(t, None, "2026-11-24T16:30:00+08:00")   # 已經手動同步過了


def test_poll_syncs_only_when_upload_changes_and_notifies(user):
    sent = []
    notify = lambda uid, title, body, url: sent.append((title, url))  # noqa: E731

    # 先有一次完整同步（不含今天的跑步）
    sync.sync_user(user, prov=FakeGarmin(seed=user))
    with db.tx() as c:
        c.execute("UPDATE users SET last_sync=? WHERE id=?", ((datetime.now(TZ) - timedelta(hours=1)).isoformat(), user))

    # 手錶沒有新上傳：不同步
    assert sync.poll_user(user, notify=notify) is None and not sent

    # 手錶剛上傳了今天的跑步：同步、推播
    FakeGarmin.upload, FakeGarmin.hide_today = datetime.now(TZ).isoformat(), False
    r = sync.poll_user(user, notify=notify)
    assert r is not None and r["new_activities"] == 1
    assert sent == [("新的跑步已同步", "/#/workout/today-run")]

    # 同一個上傳時間再問一次：不會重複同步、不會重複推播
    n = len(sent)
    assert sync.poll_user(user, notify=notify) is None and len(sent) == n


def test_poll_skips_upload_older_than_manual_sync(user):
    with db.tx() as c:
        c.execute("UPDATE users SET last_sync=? WHERE id=?", (datetime.now(TZ).isoformat(), user))
    FakeGarmin.upload = (datetime.now(TZ) - timedelta(minutes=30)).isoformat()
    assert sync.poll_user(user, notify=lambda *a: None) is None
    assert db.q1("SELECT last_upload_seen FROM users WHERE id=?", (user,))["last_upload_seen"] == FakeGarmin.upload


def test_poll_all_respects_hours_and_backs_off(user, monkeypatch):
    calls = []

    def boom(uid):
        calls.append(uid)
        raise RuntimeError("429")

    monkeypatch.setattr(sync, "poll_user", boom)
    sync._poll_backoff.clear()
    night = datetime.now(TZ).replace(hour=2)
    sync.poll_all(now=night)
    assert calls == []                                  # 半夜不偵測
    day = datetime.now(TZ).replace(hour=10, minute=0)
    sync.poll_all(now=day)
    assert calls == [user]                              # 失敗一次
    sync.poll_all(now=day + timedelta(minutes=10))
    assert calls == [user]                              # 還在退避時間內，不再打 Garmin
    sync.poll_all(now=day + timedelta(minutes=25))
    assert calls == [user, user]                        # 過了 20 分鐘才再試
