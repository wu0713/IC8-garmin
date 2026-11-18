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
    first = a["activities"][0]
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
    w = c.put("/api/override", headers=h, json={"date": a, "kind": "hills", "km": 8}).json()
    day = next(d for d in w["days"] if d["date"] == a)
    assert day["plan"]["title"] == "坡道間歇" and day["plan"]["override"]
    assert abs(day["plan"]["distance_km"] - 8) <= 1.5
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
    assert [z["key"] for z in r["zones"]][:5] == ["E", "M", "T", "I", "R"]
    caps = {z["key"]: z["hr_cap"] for z in r["zones"]}
    assert caps["E"] == round(r["max_hr"] * 0.74) and caps["T"] == round(r["max_hr"] * 0.88) and caps["R"] is None
    assert "Z2" in r["garmin_note"]


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
    assert c.put("/api/override", headers=h, json={"date": target, "kind": "hills", "km": 9}).status_code == 200
    mm = c.get(f"/api/month?ym={target[:7]}", headers=h).json()
    day = next(d for d in mm["days"] if d["date"] == target)
    assert day["plan"]["title"] == "坡道間歇" and day["plan"]["override"]
    c.delete(f"/api/override?date={target}", headers=h)
    far = (date.today() + timedelta(days=90)).isoformat()
    assert c.put("/api/override", headers=h, json={"date": far, "kind": "easy"}).status_code == 400


def test_events_soften_days_around(ctx):
    from datetime import date, timedelta
    c, h = ctx
    race = date.today() + timedelta(days=4)
    r = c.put("/api/prefs", headers=h, json={"events": [{"type": "race_b", "name": "測試 10K", "date": race.isoformat(), "distance_km": 10}]})
    assert r.status_code == 200
    days = {d["date"]: d for d in c.get("/api/dashboard", headers=h).json()["plan"]["days"]}
    assert days[race.isoformat()]["kind"] == "race"
    for g in (1, 2, 3):
        d = days.get((race - timedelta(days=g)).isoformat())
        if d:
            assert d["intensity"] in ("easy", "rest", "done")
    nxt = days.get((race + timedelta(days=1)).isoformat())
    if nxt:
        assert nxt["kind"] == "rest"
    c.put("/api/prefs", headers=h, json={"events": []})


def test_manual_mode_follows_template(ctx):
    c, h = ctx
    tpl = {"0": {"kind": "easy", "km": 6}, "2": {"kind": "intervals", "km": 9}, "5": {"kind": "long", "km": 14}}
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
            assert abs(d["plan"]["distance_km"] - tpl[wd]["km"]) <= 1.5
    c.put("/api/prefs", headers=h, json={"plan_mode": "auto"})


def test_bad_race_date_rejected(ctx):
    c, h = ctx
    assert c.put("/api/prefs", headers=h, json={"events": [{"type": "race_b", "name": "x", "date": "下週"}]}).status_code == 400
    assert c.put("/api/prefs", headers=h, json={"events": [{"type": "race_a", "date": "2027-01-01"}]}).status_code == 400


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
    a = c.get("/api/activities", headers=h).json()["activities"][3]
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


def test_skip_is_not_made_up(ctx):
    """規格書：錯過單次課跳過，不補。"""
    from datetime import date
    c, h = ctx
    today = date.today().isoformat()
    w0 = c.get("/api/week", headers=h).json()
    before = [d["plan"]["kind"] for d in w0["days"] if d["date"] > today and d["plan"]]
    c.post("/api/checkin", headers=h, json={"skip": True})
    w = c.get("/api/week", headers=h).json()
    td = next(d for d in w["days"] if d["is_today"])
    assert td["plan"]["kind"] in ("rest", "done")
    after = [d["plan"]["kind"] for d in w["days"] if d["date"] > today and d["plan"]]
    assert after == before
    assert not any("挪到" in t["title"] for t in w["tips"])


def test_pain_three_days_pauses_and_resume(ctx):
    from datetime import date, timedelta
    from app import db
    c, h = ctx
    uid = db.q1("SELECT id FROM users WHERE username='views'")["id"]
    with db.tx() as x:
        for i in range(3):
            d = (date.today() - timedelta(days=i)).isoformat()
            x.execute("INSERT INTO checkins(user_id, date, pain) VALUES(?,?,?) ON CONFLICT(user_id, date) DO UPDATE SET pain=excluded.pain",
                      (uid, d, "knee"))
    w = c.get("/api/week", headers=h).json()
    assert any(s["rule"] == "pain" for s in w["stoploss"])
    assert all(d["plan"]["kind"] in ("rest", "done", "race", "event") for d in w["days"] if d["editable"] and d["plan"])
    assert any(t["do"] and t["do"]["type"] == "resume" for t in w["tips"])
    c.post("/api/plan/resume", headers=h)
    w = c.get("/api/week", headers=h).json()
    assert not w["stoploss"]


def test_season_api_and_decision(ctx):
    from datetime import date, timedelta
    c, h = ctx
    ev = (date.today() + timedelta(weeks=6)).isoformat()
    r = c.put("/api/prefs", headers=h, json={"goal": {"type": "race", "event_date": ev, "event_distance_km": 42.195,
                                                      "target_time": "3:30:00", "name": "全馬"},
                                             "max_minutes_weekday": 150, "max_long_minutes": 300})
    assert r.status_code == 200
    s = c.get("/api/season", headers=h).json()
    assert s["feasibility"]["verdict"] == "infeasible" and s["hold"]
    assert s["feasibility"]["achievable"] and s["feasibility"]["suggested_date"] > ev
    assert all(w["phase"] == "hold" for w in s["weeks"][:-1])
    s2 = c.post("/api/plan/decision", headers=h, json={"choice": "accept_risk"}).json()
    assert not s2["hold"] and s2["decision"] == "accept_risk"
    s3 = c.post("/api/plan/decision", headers=h, json={"choice": "postpone"}).json()
    assert s3["goal"]["race_date"] == s["feasibility"]["suggested_date"]
    c.put("/api/prefs", headers=h, json={"goal": {"type": "fun", "event_date": None, "target_time": ""},
                                         "max_minutes_weekday": 70, "max_long_minutes": 150})


def test_treadmill_cadence_not_judged():
    from app.views import run_report
    r = run_report({"duration_s": 1767, "distance_m": 2460, "avg_cadence": 108, "type": "treadmill_running"}, [], [], [], None, "easy", 190)
    assert r["score"] is None and not r["items"]
