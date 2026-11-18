"""月曆長期規劃、分析建議、角色經驗值。"""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from app import analysis, auth, db, game, season, sync  # noqa: E402
from app.garmin_client import normalize_records  # noqa: E402


def _demo_user(name):
    uid = auth.create_user(name, "secret12")
    with db.tx() as c:
        c.execute("UPDATE users SET provider='demo' WHERE id=?", (uid,))
    sync.sync_user(uid)
    return uid


@pytest.fixture(scope="module")
def uid():
    return _demo_user("feat")


# ── 經驗值 ──

def test_level_curve():
    assert game.level_from_xp(0) == 1
    assert game.level_from_xp(game.xp_for_level(5)) == 5
    assert game.level_from_xp(game.xp_for_level(5) - 1) == 4
    assert [game.stage_for_level(lv) for lv in (1, 3, 4, 8, 9, 16, 24, 25, 60)] == [0, 0, 1, 1, 2, 3, 3, 4, 4]


def test_manifest_matches_image_files():
    from app.config import WEB_DIR
    assert len(game.SPECIES) == 12
    for sp in game._MANIFEST["species"]:
        assert len(sp["stages"]) == len(game.STAGE_LEVELS) == 5
        for st in sp["stages"]:
            assert (WEB_DIR / st["file"].lstrip("/")).exists(), st["file"]


def test_new_character_starts_as_egg_with_capped_gift(uid):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO character(user_id, species, chosen_at) VALUES(?, 'flame_dragon', ?)",
                  (uid, date.today().isoformat() + " 00:00:00"))
    g = game.compute(uid)
    gift = [e for e in g["events"] if e["kind"] == "gift"]
    assert gift and gift[0]["xp"] <= game.HATCH_GIFT_CAP
    # 孵化禮 + 今天可能的一趟跑步，不會一下子就進化好幾階
    assert g["stage"] <= 1 and g["character"]["stage_name"] in ("龍蛋", "幼龍")


def test_xp_grows_with_time_and_species_change_keeps_level(uid):
    with db.tx() as c:
        c.execute("UPDATE character SET chosen_at=? WHERE user_id=?",
                  ((date.today() - timedelta(days=60)).isoformat() + " 00:00:00", uid))
    g1 = game.compute(uid)
    assert g1["level"] > 4
    with db.tx() as c:
        c.execute("UPDATE character SET species='golem' WHERE user_id=?", (uid,))
    g2 = game.compute(uid)
    assert g2["level"] == g1["level"] and g2["character"]["species_name"] == "魔像"


def test_removed_species_asks_to_repick(uid):
    with db.tx() as c:
        c.execute("UPDATE character SET species='old_pixel_fox' WHERE user_id=?", (uid,))
    assert game.compute(uid)["character"] is None
    with db.tx() as c:
        c.execute("UPDATE character SET species='golem' WHERE user_id=?", (uid,))


def test_pb_bonus_only_when_improving():
    acts = [
        {"activity_id": "a", "date": "2026-01-01", "start_local": "2026-01-01", "distance_m": 5000, "duration_s": 1800},
        {"activity_id": "b", "date": "2026-01-08", "start_local": "2026-01-08", "distance_m": 5000, "duration_s": 1900},  # 比較慢
        {"activity_id": "c", "date": "2026-01-15", "start_local": "2026-01-15", "distance_m": 5000, "duration_s": 1700},  # PB
    ]
    recs = game.derive_records(acts)
    fives = [r for r in recs if r["rtype"] == "5k"]
    assert [r["activity_id"] for r in fives] == ["a", "c"]


def test_garmin_record_parsing():
    raw = [
        {"typeId": 3, "value": 1500.0, "activityId": 11, "prStartTimeGmtFormatted": "2026-05-01T07:00:00.0", "activityType": "running"},
        {"typeId": 7, "value": 21100.0, "activityId": 12, "prStartTimeGmtFormatted": "2026-06-01T07:00:00.0"},
        {"typeId": 3, "value": 900.0, "activityId": 13, "prStartTimeGmtFormatted": "2026-05-01", "activityType": "cycling"},
        {"typeId": 99, "value": 1.0, "activityId": 14, "prStartTimeGmtFormatted": "2026-05-01"},
    ]
    out = normalize_records(raw)
    assert [(r["rtype"], r["date"]) for r in out] == [("5k", "2026-05-01"), ("longest", "2026-06-01")]


def test_leaderboard_respects_privacy(uid):
    other = _demo_user("hidden")
    p = db.get_prefs(other)
    p["show_on_board"] = False
    db.save_prefs(other, p)
    ids = {r["user_id"] for r in game.leaderboard()}
    assert uid in ids and other not in ids


# ── 分析 ──

def test_analysis_shape_and_insights(uid):
    a = analysis.analyze(uid)
    assert len(a["weeks"]) == 12 and a["runs"]
    assert all(i["level"] in ("alert", "warn", "info", "good") for i in a["insights"])
    assert all(i["why"] and i["action"] for i in a["insights"])


def test_insight_rules_fire():
    weeks = [{"km": 20, "runs": 3, "minutes": 150, "long_km": 8, "hard_share": 0.5}] * 3 + \
            [{"km": 30, "runs": 1, "minutes": 200, "long_km": 18, "hard_share": 0.5}]
    rec = {"rhr_7": 60, "rhr_base": 54, "hrv_7": 40, "hrv_base": 55, "sleep_h_7": 5.8, "sleep_score_7": 60}
    ins = analysis.build_insights(weeks, weeks, 0.5, 1.7, rec, 1.0, 1.1, {"intensity": 50})
    titles = " ".join(i["title"] for i in ins)
    for t in ("加量太猛", "跑太快", "加太多", "長跑", "安靜心率", "睡眠", "有氧效率下降"):
        assert t in titles, t
    assert ins[0]["level"] == "alert"


# ── 長期規劃 / 月曆 ──

def test_season_without_event_is_12_weeks(uid):
    s = season.season_plan(uid)
    assert len(s["weeks"]) == 12
    assert any(w["tag"] == "deload" for w in s["weeks"])
    assert all(w["target_km"] > 0 for w in s["weeks"])


def test_season_race_phases(uid):
    p = db.get_prefs(uid)
    ev = date.today() + timedelta(weeks=14)
    p["goal"] = {**p["goal"], "type": "race", "event_date": ev.isoformat(), "event_distance_km": 21.1}
    db.save_prefs(uid, p)
    s = season.season_plan(uid)
    phases = [w["phase"] for w in s["weeks"]]
    if s["hold"]:
        assert set(phases) <= {"hold", "race"}
        return
    assert phases[-1] == "race" and s["weeks"][-1]["events"]
    assert "taper" in phases and "build" in phases
    assert phases.index("build") < phases.index("taper")
    assert [c["test_km"] for c in s["checkpoints"]][:1] == [5.3]


def test_month_view(uid):
    m = season.month(uid, date.today().isoformat()[:7])
    assert len(m["days"]) >= 28 and m["runs"] >= 1
