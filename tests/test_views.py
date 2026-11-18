"""新版 UI 用到的 API：摘要、指標趨勢、活動詳情、本週課表調整、健康參考範圍、資料庫遷移。"""
import os
import sqlite3
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.garmin_client import parse_series  # noqa: E402


@pytest.fixture(scope="module")
def ctx():
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/register", json={"username": "views", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        c.post("/api/garmin/demo", headers=h)
        c.post("/api/sync", headers=h)
        yield c, h


def test_summary(ctx):
    c, h = ctx
    s = c.get("/api/summary", headers=h).json()
    assert s["device"]["brand"] == "Garmin"
    assert [r["key"] for r in s["rings"]] == ["run", "kcal", "steps"]
    keys = [x["key"] for x in s["cards"]]
    assert keys[:2] == ["readiness", "steps"] and "partner" in keys


def test_dashboard_is_customisable(ctx):
    c, h = ctx
    c.put("/api/prefs", headers=h, json={"dashboard": ["hrv", "nope", "steps"]})
    assert [x["key"] for x in c.get("/api/summary", headers=h).json()["cards"]] == ["hrv", "steps"]
    c.put("/api/prefs", headers=h, json={"dashboard": None})


@pytest.mark.parametrize("key,rng,n", [("steps", "W", 7), ("rhr", "M", 30), ("run_km", "6M", 26), ("sleep", "Y", 12)])
def test_metric_ranges(ctx, key, rng, n):
    c, h = ctx
    m = c.get(f"/api/metric/{key}?range={rng}", headers=h).json()
    assert n <= len(m["points"]) <= n + 1
    assert m["avg"] is not None and m["min"] <= m["max"]


def test_metric_errors(ctx):
    c, h = ctx
    assert c.get("/api/metric/nope", headers=h).status_code == 404
    assert c.get("/api/metric/steps?range=X", headers=h).status_code == 400


def test_activity_overview_and_detail(ctx):
    c, h = ctx
    a = c.get("/api/activities", headers=h).json()
    assert len(a["weeks"]) == 13 and a["activities"]
    first = next(x for x in a["activities"] if not x["cross"])
    d = c.get(f"/api/activities/{first['id']}", headers=h).json()
    assert d["points"] and len(d["zones"]) == 5 and d["splits"]
    assert sum(z["minutes"] for z in d["zones"]) == pytest.approx(d["minutes"], abs=2)
    older = c.get(f"/api/activities?before={a['activities'][-1]['date']}", headers=h).json()["activities"]
    assert all(x["date"] < a["activities"][-1]["date"] for x in older)


def test_parse_garmin_series():
    raw = {"metricDescriptors": [{"key": "sumDuration", "metricsIndex": 0}, {"key": "directHeartRate", "metricsIndex": 1},
                                 {"key": "directSpeed", "metricsIndex": 2}],
           "activityDetailMetrics": [{"metrics": [0, 120, 2.8]}, {"metrics": [10, 130, 3.0]}, {"metrics": [20, None, 0.1]}]}
    pts = parse_series(raw)["points"]
    assert pts[1] == {"t": 10, "hr": 130, "pace": 333, "ele": None, "cad": None}
    assert pts[2]["pace"] is None  # 停下來時不算配速


def test_week_override_and_swap(ctx):
    c, h = ctx
    w = c.get("/api/week", headers=h).json()
    assert len(w["days"]) == 7 and w["tips"]
    editable = [d["date"] for d in w["days"] if d["editable"]]
    if len(editable) < 2:
        pytest.skip("本週剩不到兩天可調整")
    a, b = editable[-2], editable[-1]
    w = c.put("/api/override", headers=h, json={"date": a, "kind": "hills", "minutes": 40}).json()
    day = next(d for d in w["days"] if d["date"] == a)
    assert day["plan"]["title"] == "坡道衝刺" and day["plan"]["override"]
    before = {d["date"]: (d["plan"] or {}).get("title") for d in w["days"]}
    w = c.post("/api/swap", headers=h, json={"a": a, "b": b}).json()
    after = {d["date"]: (d["plan"] or {}).get("title") for d in w["days"]}
    assert after[a] == before[b] or (before[b] == "休息日" and after[a] == "休息日")
    assert after[b] == before[a]
    w = c.put("/api/override", headers=h, json={"date": a, "kind": "rest"}).json()
    assert next(d for d in w["days"] if d["date"] == a)["plan"]["kind"] == "rest"
    c.delete(f"/api/override?date={a}", headers=h)
    c.delete(f"/api/override?date={b}", headers=h)
    assert c.put("/api/override", headers=h, json={"date": "2020-01-01", "kind": "easy"}).status_code == 400
    assert c.put("/api/override", headers=h, json={"date": a, "kind": "swimming"}).status_code == 400


def test_health_ranges(ctx):
    c, h = ctx
    r = c.get("/api/health", headers=h).json()
    assert [z["zone"] for z in r["zones"]] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert all(z["lo"] < z["hi"] for z in r["zones"])


def test_old_database_is_migrated(tmp_path):
    from app import db

    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily (user_id INTEGER, date TEXT, rhr REAL, PRIMARY KEY(user_id, date))")
    con.execute("CREATE TABLE activities (user_id INTEGER, activity_id TEXT, date TEXT, PRIMARY KEY(user_id, activity_id))")
    con.commit()
    db._migrate(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily)")}
    assert {"steps", "active_kcal", "intensity_min"} <= cols
    assert "calories" in {r[1] for r in con.execute("PRAGMA table_info(activities)")}


# ── 月課表、比賽、自己排課、訓練報告 ──

def test_month_has_projected_plan_and_far_override(ctx):
    from datetime import date, timedelta
    c, h = ctx
    nxt = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
    m = c.get(f"/api/month?ym={nxt.isoformat()[:7]}", headers=h).json()
    proj = [d for d in m["days"] if d["plan"] and d["plan"].get("projected")]
    assert len(proj) >= 20 and m["planned_runs"] >= 8
    target = (date.today() + timedelta(days=20)).isoformat()
    assert c.put("/api/override", headers=h, json={"date": target, "kind": "hills", "minutes": 45}).status_code == 200
    mm = c.get(f"/api/month?ym={target[:7]}", headers=h).json()
    day = next(d for d in mm["days"] if d["date"] == target)
    assert day["plan"]["title"] == "坡道衝刺" and day["plan"]["override"]
    c.delete(f"/api/override?date={target}", headers=h)
    far = (date.today() + timedelta(days=90)).isoformat()
    assert c.put("/api/override", headers=h, json={"date": far, "kind": "easy"}).status_code == 400


def test_races_soften_days_around():
    from datetime import date, timedelta
    from app import planner
    today = date(2026, 11, 16)  # 週一
    p = planner.default_prefs()
    p.update(days=[0, 1, 2, 3, 4, 5, 6], intensity=90)
    race = today + timedelta(days=4)
    p["races"] = [{"name": "測試半馬", "date": race.isoformat(), "distance_km": 21.1}]
    paces = {"recovery": 412, "easy": 382, "long": 389, "marathon": 343, "tempo": 321, "interval": 298, "rep": 279}
    pl = planner.make_plan(today=today, prefs=p, paces=paces, recent_weeks=[300] * 4, rel_tsb=0, ready={"score": 80},
                           checkins={}, done_dates=set(), recent_kinds=[])
    by = {d["date"]: d for d in pl["days"]}
    assert by[race.isoformat()]["kind"] == "race"
    assert by[(race - timedelta(days=1)).isoformat()]["kind"] in ("recovery", "rest")
    for g in (2, 3):
        assert by[(race - timedelta(days=g)).isoformat()]["intensity"] in ("easy", "rest")
    assert by[(race + timedelta(days=1)).isoformat()]["kind"] == "rest"
    assert by[(race + timedelta(days=2)).isoformat()]["kind"] in ("recovery", "rest")
    assert any("比賽" in n for n in pl["notes"])


def test_manual_mode_follows_template(ctx):
    c, h = ctx
    tpl = {"0": {"kind": "easy", "minutes": 30}, "2": {"kind": "intervals", "minutes": 50}, "5": {"kind": "long", "minutes": 70}}
    r = c.put("/api/prefs", headers=h, json={"plan_mode": "manual", "template": {**tpl, "9": {"kind": "easy"}, "3": {"kind": "swim"}}})
    assert r.status_code == 200 and set(r.json()["template"]) == {"0", "2", "5"}
    from datetime import date
    w = c.get("/api/week", headers=h).json()
    today = date.today().isoformat()
    for d in w["days"]:
        if d["date"] <= today or not d["plan"]:
            continue
        wd = str(date.fromisoformat(d["date"]).weekday())
        assert d["plan"]["kind"] == (tpl[wd]["kind"] if wd in tpl else "rest")
        if wd in tpl:
            assert abs(d["plan"]["minutes"] - tpl[wd]["minutes"]) <= 3
    c.put("/api/prefs", headers=h, json={"plan_mode": "auto"})


def test_bad_race_date_rejected(ctx):
    c, h = ctx
    assert c.put("/api/prefs", headers=h, json={"races": [{"name": "x", "date": "下週"}]}).status_code == 400


def test_run_report_flags_problems():
    from app.views import run_report
    a = {"duration_s": 3000, "distance_m": 8000, "avg_cadence": 156}
    plan = {"kind": "easy", "title": "輕鬆跑", "minutes": 40, "intensity": "easy"}
    # 越跑越慢、心率越來越高、全程偏高心率
    pts = [{"t": t, "hr": 160 + t // 150, "pace": 340 + t // 25} for t in range(0, 3000, 10)]
    splits = [{"km": i + 1, "pace": f"{5 + (i * 12 + 30) // 60}:{(i * 12 + 30) % 60:02d}", "hr": 165} for i in range(8)]
    r = run_report(a, pts, splits, [], plan, "hard", 190)
    v = {i["key"]: i["verdict"] for i in r["items"]}
    assert v["intensity"] == "bad" and v["fade"] == "bad" and v["cadence"] == "bad" and v["drift"] in ("ok", "bad")
    assert r["score"] < 65 and len(r["improve"]) >= 3
    good_pts = [{"t": t, "hr": 138, "pace": 380} for t in range(0, 2400, 10)]
    good = run_report({"duration_s": 2400, "distance_m": 6300, "avg_cadence": 172}, good_pts,
                      [{"km": i + 1, "pace": "6:20", "hr": 138} for i in range(6)], [], plan, "easy", 190)
    assert good["score"] == 100 and not good["improve"]


def test_activity_detail_has_report_and_original_plan_kept(ctx):
    c, h = ctx
    a = [x for x in c.get("/api/activities", headers=h).json()["activities"] if not x["cross"]][3]
    d = c.get(f"/api/activities/{a['id']}", headers=h).json()
    assert d["report"]["items"] and d["plan"] and d["plan"]["kind"] != "done" and d["xp"] > 0
    assert len(c.get("/api/profile", headers=h).json()["dims"]) == 5


def test_switching_source_wipes_other_data_and_real_only_endpoints(ctx):
    from app import db, main
    c, h = ctx
    uid = db.q1("SELECT id FROM users WHERE username='views'")["id"]
    assert c.get("/api/garmin/diagnose", headers=h).status_code == 409      # 範例資料不能跑連線檢查
    assert c.post("/api/garmin/push-test", headers=h).status_code == 409
    assert db.q1("SELECT COUNT(*) n FROM activities WHERE user_id=?", (uid,))["n"] > 0
    main._linked(uid, "me@example.com", "{}")                               # 換成真的 Garmin → 範例資料清掉
    assert db.q1("SELECT COUNT(*) n FROM activities WHERE user_id=?", (uid,))["n"] == 0
    assert db.q1("SELECT COUNT(*) n FROM device WHERE user_id=?", (uid,))["n"] == 0


def test_device_picks_last_used_watch():
    from app.garmin_client import RealGarmin

    class Api:
        def get_devices(self):
            return [{"deviceId": 1, "productDisplayName": "Forerunner 165", "currentFirmwareVersion": "29.05"},
                    {"deviceId": 2, "productDisplayName": "Forerunner 265", "currentFirmwareVersion": "21.22"}]

        def get_device_last_used(self):
            return {"userDeviceId": 2, "lastUsedDeviceName": "Forerunner 265", "lastUsedDeviceUploadTime": 1794900000000}

    g = RealGarmin.__new__(RealGarmin)
    g.api = Api()
    d = g.fetch_device()
    assert d["name"] == "Forerunner 265" and d["firmware"] == "21.22" and d["count"] == 2


def test_novice_gets_only_easy_runs_and_no_false_alarms():
    from datetime import date, timedelta
    from app import auth, db, sync, analysis
    uid = auth.create_user("novice1", "secret12")
    T = date.today()
    with db.tx() as c:
        c.execute("UPDATE users SET provider='garmin' WHERE id=?", (uid,))
        for i, (d, dist, dur, hr) in enumerate([(2, 5024, 1519, 177), (4, 5380, 2333, 129), (6, 2460, 1767, 137)]):
            ds = (T - timedelta(days=d)).isoformat()
            c.execute("INSERT INTO activities(user_id,activity_id,date,start_local,type,name,distance_m,duration_s,avg_hr,max_hr) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (uid, f"n{i}", ds, ds + " 19:00:00", "running", "跑步", dist, dur, hr, 192))
    r = sync.compute(uid)
    assert r["novice"] is True
    kinds = {d["kind"] for d in r["plan"]["days"]}
    assert kinds <= {"easy", "rest", "done", "recovery"}
    assert sum(1 for d in r["plan"]["days"] if d["kind"] == "easy") >= 3
    assert all(f["name"] != "近期疲勞（TSB）" for f in r["readiness"]["factors"])
    a = analysis.analyze(uid)
    assert a["acwr"] is None and not any("加量太猛" in i["title"] for i in a["insights"])


def test_skip_offers_to_move_workout(ctx):
    from datetime import date
    from app import db
    c, h = ctx
    uid = db.q1("SELECT id FROM users WHERE username='views'")["id"]
    today = date.today().isoformat()
    with db.tx() as x:   # 讓今天有一堂還沒跑的課
        x.execute("UPDATE users SET provider='demo' WHERE id=?", (uid,))
        x.execute("DELETE FROM activities WHERE user_id=? AND date=?", (uid, today))
        x.execute("INSERT OR REPLACE INTO plan(user_id,date,data) VALUES(?,?,?)",
                  (uid, today, '{"date":"%s","kind":"tempo","title":"節奏跑","minutes":45,"intensity":"hard","steps":[]}' % today))
    c.post("/api/checkin", headers=h, json={"skip": True})
    w = c.get("/api/week", headers=h).json()
    tip = next((t for t in w["tips"] if "挪到" in t["title"]), None)
    if any(d["editable"] and not d["is_today"] and (not d["plan"] or d["plan"]["kind"] == "rest") for d in w["days"]):
        assert tip and tip["do"]["type"] == "tempo" and tip["do"]["label"].startswith("挪到")


def test_treadmill_cadence_not_judged():
    from app.views import run_report
    r = run_report({"duration_s": 1767, "distance_m": 2460, "avg_cadence": 108, "type": "treadmill_running"}, [], [], [], None, "easy", 190)
    assert r["score"] is None and not r["items"]
