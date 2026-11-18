"""晨報、晚報、其他運動。"""
import os
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import briefs, db  # noqa: E402
from app.config import TZ  # noqa: E402
from app.garmin_client import normalize_activity, today_local  # noqa: E402


@pytest.fixture(scope="module")
def ctx():
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/register", json={"username": "briefs", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        c.post("/api/garmin/demo", headers=h)
        c.post("/api/sync", headers=h)
        uid = db.q1("SELECT id FROM users WHERE username=?", ("briefs",))["id"]
        yield c, h, uid


def _set_today(uid, **vals):
    ds = today_local().isoformat()
    with db.tx() as c:
        for k, v in vals.items():
            c.execute(f"UPDATE daily SET {k}=? WHERE user_id=? AND date=?", (v, uid, ds))


def _put_plan(c, h, kind="tempo", minutes=45):
    r = c.put("/api/override", headers=h, json={"date": today_local().isoformat(), "kind": kind, "minutes": minutes})
    assert r.status_code == 200


def test_normalize_keeps_other_sports():
    run = normalize_activity({"activityId": 1, "activityType": {"typeKey": "treadmill_running"}, "startTimeLocal": "2026-11-01 07:00:00"})
    bike = normalize_activity({"activityId": 2, "activityType": {"typeKey": "road_biking"}, "startTimeLocal": "2026-11-01 07:00:00",
                               "duration": 3600, "averageHR": 130})
    assert not run.get("cross")
    assert bike["cross"] and bike["sport_label"] == "公路車" and bike["sport_group"] == "bike"


def test_other_sports_listed_and_counted(ctx):
    c, h, uid = ctx
    a = c.get("/api/activities", headers=h).json()
    assert any(x["cross"] for x in a["activities"]) and any(not x["cross"] for x in a["activities"])
    assert any(w["other_load"] > 0 for w in a["weeks"])
    assert a["volume"]["verdict"]["level"] in ("ok", "low", "warn", "bad")
    x = next(x for x in a["activities"] if x["cross"])
    d = c.get(f"/api/activities/{x['id']}", headers=h).json()
    assert d["cross"] and d["load"] > 0 and d["effect"]


def test_morning_normal_follows_plan(ctx):
    c, h, uid = ctx
    _put_plan(c, h)
    base = db.q1("SELECT AVG(rhr) r FROM (SELECT rhr FROM daily WHERE user_id=? AND date<? ORDER BY date DESC LIMIT 7)",
                 (uid, today_local().isoformat()))["r"]
    _set_today(uid, rhr=round(base), hrv_avg=60, sleep_score=82, sleep_hours=7.5, bb_now=80)
    b = briefs.build_and_save(uid, "morning")
    assert b["level"] == "go" and b["suggestion"] is None and "照課表" in b["advice"]


def test_morning_high_rhr_suggests_reduce_and_apply(ctx):
    c, h, uid = ctx
    _put_plan(c, h, "tempo", 45)
    base = db.q1("SELECT AVG(rhr) r FROM (SELECT rhr FROM daily WHERE user_id=? AND date<? ORDER BY date DESC LIMIT 7)",
                 (uid, today_local().isoformat()))["r"]
    _set_today(uid, rhr=round(base) + 8, hrv_avg=60, sleep_score=60, sleep_hours=6.5, bb_now=60)
    b = briefs.build_and_save(uid, "morning")
    rhr = next(m for m in b["metrics"] if m["key"] == "rhr")
    assert rhr["verdict"] == "bad" and b["level"] == "reduce"
    assert b["suggestion"]["kind"] == "easy" and b["suggestion"]["minutes"] == 36
    r = c.post("/api/brief/morning/apply", headers=h)
    assert r.status_code == 200
    today = next(d for d in c.get("/api/week", headers=h).json()["days"] if d["date"] == today_local().isoformat())
    assert today["plan"]["kind"] == "easy"


def test_morning_missing_rhr_explains(ctx):
    c, h, uid = ctx
    _set_today(uid, rhr=None)
    b = briefs.build_and_save(uid, "morning")
    assert not b["has_rhr"] and next(m for m in b["metrics"] if m["key"] == "rhr")["verdict"] == "none"


def test_evening(ctx):
    c, h, uid = ctx
    _set_today(uid, steps=9000, steps_goal=8000, bb_now=18, bb_low=12, body_battery=70, stress_avg=55)
    night = datetime.now(TZ).replace(hour=22, minute=0)
    b = briefs.build_and_save(uid, "evening", night.date(), night)
    assert c.get("/api/brief/evening", headers=h).json()["headline"] == b["headline"]
    assert b["level"] == "bad" and any(i["key"] == "steps" and i["verdict"] == "good" for i in b["items"])
    assert "早 30 分鐘" in b["tips"][0]


def test_tick_waits_for_rhr_then_pushes_once(ctx):
    c, h, uid = ctx
    with db.tx() as cx:
        cx.execute("DELETE FROM briefs WHERE user_id=?", (uid,))
    _set_today(uid, rhr=None)
    sent = []
    now = datetime.now(TZ).replace(hour=7, minute=40)
    briefs.tick(now, do_sync=lambda u: None, do_push=lambda *a: sent.append(a))
    assert not [s for s in sent if s[0] == uid]                     # 還沒有安靜心率：先等
    _set_today(uid, rhr=55)
    briefs.tick(now + timedelta(minutes=30), do_sync=lambda u: None, do_push=lambda *a: sent.append(a))
    mine = [s for s in sent if s[0] == uid]
    assert len(mine) == 1 and "晨報" in mine[0][1]
    briefs.tick(now + timedelta(minutes=60), do_sync=lambda u: None, do_push=lambda *a: sent.append(a))
    assert len([s for s in sent if s[0] == uid]) == 1               # 同一天只推一次


def test_push_endpoints(ctx):
    c, h, uid = ctx
    info = c.get("/api/push", headers=h).json()
    assert info["available"] and len(info["key"]) > 60 and info["devices"] == 0
    bad = c.post("/api/push/subscribe", headers=h, json={"subscription": {"endpoint": "http://x"}})
    assert bad.status_code == 400
    ok = c.post("/api/push/subscribe", headers=h, json={"subscription": {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "x", "auth": "y"}}})
    assert ok.json()["devices"] == 1
    assert c.delete("/api/push/subscribe", headers=h).json()["devices"] == 0


def test_brief_times_validated(ctx):
    c, h, uid = ctx
    assert c.put("/api/prefs", headers=h, json={"brief_morning": "25:00"}).status_code == 400
    assert c.put("/api/prefs", headers=h, json={"brief_morning": "06:45"}).json()["brief_morning"] == "06:45"


def test_pet_voice_morning_reduce():
    from app import pet_voice
    b = {"kind": "morning", "date": "2026-11-18", "level": "reduce", "advice": "把「節奏跑」改成 36 分鐘輕鬆跑。",
         "suggestion": {"kind": "easy", "minutes": 36, "title": "輕鬆跑"}, "plan": {"kind": "tempo"},
         "metrics": [{"key": "rhr", "label": "安靜心率", "unit": "bpm", "today": 63, "yesterday": 56, "baseline": 55, "verdict": "bad", "text": ""}],
         "yesterday_acts": [], "notes": [], "volume": None, "ratio": None}
    c = pet_voice.chat(b, {"species": "orc", "nickname": "阿獸"})
    assert c["name"] == "阿獸" and any("63" in l and "8 下" in l for l in c["lines"])
    assert [a["id"] for a in c["actions"]] == ["apply", "keep"] and "俺" in "".join(c["lines"])
    egg = pet_voice.chat(b, None)
    assert egg["name"] == "還沒孵化的蛋"


def test_evening_before_time_is_preliminary(ctx):
    c, h, uid = ctx
    _set_today(uid, bb_now=60, bb_low=40, body_battery=80, stress_avg=30)
    morning = datetime.now(TZ).replace(hour=10, minute=40)
    b = briefs.build_and_save(uid, "evening", morning.date(), morning)
    assert b["preliminary"] and b["headline"].startswith("到 10:40 為止") and "消耗很大" not in b["headline"]
    from app import pet_voice
    lines = pet_voice.chat(b, {"species": "succubus", "nickname": "123"})["lines"]
    assert "還沒到晚報時間" in lines[0] and not any("晚安" in l for l in lines)


def test_month_lists_other_sports(ctx):
    c, h, uid = ctx
    m = c.get(f"/api/month?ym={today_local().isoformat()[:7]}", headers=h).json()
    assert "others" in m and any(d["others"] for d in m["days"]) == (m["others"] > 0)
