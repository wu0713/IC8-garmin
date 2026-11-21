"""依程度與距離排課、目標合理性、跑後評分與疲勞、身體狀況建議、夥伴對話互動。"""
import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import coach, db, engine  # noqa: E402
from app import plan_tables as T  # noqa: E402
from app.garmin_client import today_local  # noqa: E402
from app.week import schedule_week  # noqa: E402


def _ath(base=40, hard=5, vdot_5k_s=21 * 60):
    return {"weekly_km": [base] * 12, "max_hr": 190, "best_effort": {"km": 5, "seconds": vdot_5k_s, "date": "2026-11-01"},
            "long_runs": [{"km": 16, "seconds": 16 * 330, "avg_hr": 150, "date": "2026-11-01"}], "easy_hr_share": 0.72,
            "novice": False, "hard_runs_8w": hard, "history_weeks": 12, "recent_longest_km": 14}


def _season(dist, target_s, level=None, weeks=16, ath=None):
    ath = ath or _ath()
    start = date(2026, 11, 23)
    goal = {"distance_km": dist, "target_s": target_s, "race_date": (start + timedelta(weeks=weeks, days=-1)).isoformat(),
            "name": "x", "type": "race"}
    diag = engine.diagnose(ath, goal)
    lv = engine.athlete_level(ath, diag, level)
    feas = engine.feasibility(diag, goal, weeks, None)
    snap = {"plan_start": start.isoformat(), "diag": diag, "v_base": 40, "feasibility": feas, "level": lv["level"],
            "start_long_km": 14, "insertions": {}}
    prefs = {"days": [1, 2, 4, 5, 6], "long_run_day": 6, "max_long_minutes": 150, "events": []}
    se = engine.build_season(ath=ath, goal=goal, prefs=prefs, snap=snap, today=start)
    out = []
    for w in se["weeks"]:
        z = engine.derive_zones(diag, goal, w["phase"], 190, None)["zones"]
        sch = schedule_week(w, {"avail": {1, 2, 4, 5, 6}, "long_run_day": 6, "zones": z, "diag": diag, "goal": goal,
                                "event_days": se["event_days"], "prev_tail": {}, "max_minutes_weekday": 70,
                                "liked": {"tempo", "intervals", "hills", "fartlek", "reps", "strides"}, "max_hr": 190,
                                "level": lv["level"]})
        out.append((w, [d["kind"] for _, d in sorted(sch["days"].items())]))
    return se, lv, out


def test_level_detection():
    diag = engine.diagnose(_ath(), {"distance_km": 21.0975, "target_s": None})
    assert engine.athlete_level(_ath(), diag)["level"] == "experienced"
    low = _ath(base=12)
    assert engine.athlete_level(low, engine.diagnose(low, {"distance_km": 10, "target_s": None}))["level"] == "novice"
    mid = _ath(hard=0)
    assert engine.athlete_level(mid, engine.diagnose(mid, {"distance_km": 10, "target_s": None}))["level"] == "intermediate"
    assert engine.athlete_level(low, diag, "experienced")["source"] == "manual"


def test_experienced_mixes_quality_from_week_one_novice_does_not():
    _, _, exp = _season(21.0975, 88 * 60, "experienced")
    base_q = [k for w, ks in exp if w["phase"] == "base" for k in ks if k in engine.QUALITY_KINDS]
    assert base_q, "有經驗的跑者基礎期就要有強度課"
    _, _, nov = _season(21.0975, 88 * 60, "novice")
    assert not [k for w, ks in nov if w["phase"] == "base" for k in ks if k in engine.QUALITY_KINDS - {"test"}]


def test_5k_plan_differs_from_half():
    _, _, five = _season(5, 19 * 60 + 30, "experienced")
    _, _, half = _season(21.0975, 88 * 60, "experienced")
    longest5 = max(w["long_km"] for w, _ in five)
    longest21 = max(w["long_km"] for w, _ in half)
    assert longest5 <= 5 * T.DIST_PROFILE["5k"]["kappa"] and longest21 > longest5 + 5
    kinds5 = {k for _, ks in five for k in ks}
    assert "reps" in kinds5 and "long_m" not in kinds5
    assert "long_m" in {k for _, ks in half for k in ks}
    # 5K 專項期長跑不再加長
    spec = [w["long_km"] for w, _ in five if w["phase"] == "specific"]
    build = [w["long_km"] for w, _ in five if w["phase"] in ("base", "build")]
    assert max(spec) <= max(build)


def test_unrealistic_target_flagged_and_capped():
    ath = _ath(base=20, hard=0, vdot_5k_s=30 * 60)   # 5K 30 分的人要半馬 1:24
    goal = {"distance_km": 21.0975, "target_s": 84 * 60 + 23, "race_date": "2027-03-01"}
    diag = engine.diagnose(ath, goal)
    lv = engine.athlete_level(ath, diag)["level"]
    a = engine.assess(diag=diag, goal=goal, level=lv, weeks=14, feas=None, ath=ath)
    assert a["verdict"] == "unrealistic" and a["realistic_s"] > goal["target_s"]
    assert any(r["severity"] == "danger" for r in a["risks"])


def test_band():
    b = coach.band([50, 51, 49, 50, 52, 50, 48, 51])
    assert b["lo"] < 50 < b["hi"]
    assert coach.band([50, 51]) is None


def test_segments_detect_intervals():
    pts = []
    t = 0
    for rep in range(5):
        for _ in range(24):       # 4 分鐘快
            pts.append({"t": t, "pace": 240, "hr": 165})
            t += 10
        for _ in range(12):       # 2 分鐘慢
            pts.append({"t": t, "pace": 420, "hr": 140})
            t += 10
    segs = coach._segments(pts, 330)
    assert len(segs) == 5 and all(abs(s["pace"] - 240) < 1 for s in segs)


@pytest.fixture(scope="module")
def ctx():
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/register", json={"username": "coach", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        c.post("/api/garmin/demo", headers=h)
        c.post("/api/sync", headers=h)
        uid = db.q1("SELECT id FROM users WHERE username=?", ("coach",))["id"]
        yield c, h, uid


def test_checkin_pain_gives_options_and_care(ctx):
    c, h, uid = ctx
    ds = today_local().isoformat()
    c.put("/api/override", headers=h, json={"date": ds, "kind": "tempo", "km": 8})
    r = c.post("/api/checkin", headers=h, json={"pain": "shin"}).json()
    a = r["advice"]
    assert a["severity"] in ("reduce", "rest") and a["care"]["label"] == "小腿脛骨" and a["care"]["rehab"]
    assert a["options"] and a["options"][-1]["id"] == "keep"
    c.post("/api/checkin", headers=h, json={"pain": "none"})


def test_brief_reply_feel_and_option(ctx):
    c, h, uid = ctx
    ds = today_local().isoformat()
    c.put("/api/override", headers=h, json={"date": ds, "kind": "tempo", "km": 8})
    r = c.post("/api/brief/morning/reply", headers=h, json={"type": "feel", "value": 1}).json()
    assert r["lines"] and r["actions"] and r["actions"][0]["type"] == "option"
    opt = r["actions"][0]
    r2 = c.post("/api/brief/morning/reply", headers=h, json={"type": "option", "label": opt["label"], "apply": opt["apply"]})
    assert r2.status_code == 200 and "改好了" in r2.json()["lines"][0]
    day = next(d for d in c.get("/api/week", headers=h).json()["days"] if d["date"] == opt["apply"]["date"])
    assert day["plan"]["kind"] == opt["apply"]["kind"]
    assert c.post("/api/brief/morning/reply", headers=h, json={"type": "feel", "value": 9}).status_code == 400


def test_season_has_level_and_assess(ctx):
    c, h, uid = ctx
    s = c.get("/api/season", headers=h).json()
    assert s["level"]["level"] in T.LEVEL_ZH and "risks" in s["assess"] and s["distance_label"]


def test_level_pref_saved(ctx):
    c, h, uid = ctx
    p = c.get("/api/prefs", headers=h).json()
    p = p.get("prefs", p)
    r = c.put("/api/prefs", headers=h, json={"level": "experienced"})
    assert r.status_code == 200
    s = c.get("/api/season", headers=h).json()
    assert s["level"]["level"] == "experienced" and s["level"]["source"] == "manual"
    c.put("/api/prefs", headers=h, json={"level": "bogus"})
    assert c.get("/api/season", headers=h).json()["level"]["source"] == "auto"


def test_unliked_workouts_are_substituted():
    from app.week import _pick_variant
    liked = {"tempo", "intervals", "reps", "strides"}          # 不喜歡坡道和變速
    assert _pick_variant("hills", liked, 0, "base") is None   # 基礎期直接拿掉
    assert _pick_variant("hills", liked, 0, "build") == "intervals"
    assert _pick_variant("fartlek", liked, 0, "build") == "tempo"
    assert _pick_variant("hills", liked | {"hills"}, 0, "base") == "hills"
    assert _pick_variant("intervals", {"hills", "fartlek"}, 1, "specific") == "fartlek"


def test_strides_respect_preference():
    _, _, with_s = _season(21.0975, 88 * 60, "novice")
    from app import week
    se, lv, _ = _season(21.0975, 88 * 60, "novice")
    # 直接檢查排程：沒勾快步就不會有「輕鬆跑＋快步」
    w = se["weeks"][1]
    goal = {"distance_km": 21.0975, "target_s": 88 * 60, "race_date": "2027-03-14"}
    z = engine.derive_zones(se["diag"], goal, w["phase"], 190, None)["zones"]
    base_ctx = {"avail": {1, 2, 4, 5, 6}, "long_run_day": 6, "zones": z, "diag": se["diag"], "goal": goal,
                "event_days": se["event_days"], "prev_tail": {}, "max_minutes_weekday": 70, "max_hr": 190, "level": "novice"}
    on = week.schedule_week(w, {**base_ctx, "liked": {"tempo", "intervals", "strides"}})
    off = week.schedule_week(w, {**base_ctx, "liked": {"tempo", "intervals"}})
    assert any("快步" in d["title"] for d in on["days"].values())
    assert not any("快步" in d["title"] for d in off["days"].values())


def test_month_shows_strength_on_rest_days(ctx):
    c, h, uid = ctx
    ym = (today_local().replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
    m = c.get(f"/api/month?ym={ym}", headers=h).json()
    st = [d for d in m["days"] if d.get("strength")]
    assert st and all(not d["runs"] and (not d["plan"] or d["plan"]["kind"] in ("rest", "strength")) for d in st)
    c.put("/api/prefs", headers=h, json={"strength_enabled": False})
    assert not any(d.get("strength") for d in c.get(f"/api/month?ym={ym}", headers=h).json()["days"])
    c.put("/api/prefs", headers=h, json={"strength_enabled": True})
