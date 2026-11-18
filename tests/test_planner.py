from datetime import date, timedelta

from app import metrics, planner

PACES = {"recovery": 412, "easy": 382, "long": 389, "marathon": 343, "tempo": 321, "interval": 298, "rep": 279}
TUE = date(2026, 11, 17)


def plan(prefs=None, score=75, weeks=(200, 200, 200, 200), rel_tsb=0.0, checkins=None, done=None, today=TUE, recent=None):
    p = planner.default_prefs()
    for k, v in (prefs or {}).items():
        if isinstance(v, dict):
            p[k].update(v)
        else:
            p[k] = v
    return planner.make_plan(today=today, prefs=p, paces=PACES, recent_weeks=list(weeks), rel_tsb=rel_tsb,
                             ready={"score": score}, checkins=checkins or {}, done_dates=done or set(),
                             recent_kinds=recent or [])


def kinds(pl):
    return [d["kind"] for d in pl["days"]]


def test_only_runs_on_chosen_days():
    pl = plan({"days": [0, 2, 4]})
    for d in pl["days"]:
        wd = date.fromisoformat(d["date"]).weekday()
        if wd not in (0, 2, 4):
            assert d["kind"] == "rest"


def test_long_run_on_chosen_day_and_capped():
    pl = plan({"long_run_day": 6, "max_long_minutes": 70}, weeks=(400, 400, 400, 400))
    sun = next(d for d in pl["days"] if date.fromisoformat(d["date"]).weekday() == 6)
    assert sun["kind"] == "long"
    assert sun["minutes"] <= 75


def test_intensity_zero_means_no_hard_sessions():
    pl = plan({"intensity": 0})
    assert not any(d["intensity"] == "hard" for d in pl["days"])


def test_disliked_workouts_never_scheduled():
    off = {k: False for k in planner.WORKOUT_LABELS}
    off["hills"] = True
    for i in range(7):
        pl = plan({"intensity": 90, "workouts": off}, today=TUE + timedelta(days=i))
        q = [d["kind"] for d in pl["days"] if d["intensity"] == "hard"]
        assert set(q) <= {"hills"}


def test_low_readiness_downgrades_today():
    p = {"days": [1, 3, 5, 6]}
    good = plan(p, score=80)
    bad = plan(p, score=30)
    assert good["days"][0]["kind"] != "recovery"
    assert bad["days"][0]["kind"] in ("recovery", "rest")
    assert any("恢復分數" in n for n in bad["notes"])


def test_skip_checkin_turns_today_into_rest():
    pl = plan({"days": [1, 3, 5, 6]}, checkins={TUE.isoformat(): {"skip": 1}})
    assert pl["days"][0]["kind"] == "rest"


def test_volume_ramps_but_is_bounded():
    pl = plan({"progression": "bold", "deload_every": 0}, weeks=(300, 300, 300, 300))
    assert 300 < pl["target_minutes"] <= 345


def test_fixed_weekly_minutes_respected():
    pl = plan({"fixed_weekly_minutes": 150})
    assert pl["target_minutes"] == 150


def test_race_week_and_event_day():
    ev = TUE + timedelta(days=5)
    pl = plan({"goal": {"type": "race", "event_date": ev.isoformat(), "event_distance_km": 21.1, "target_time": "1:45:00"}})
    assert pl["phase"] == "race_week"
    race = next(d for d in pl["days"] if d["date"] == ev.isoformat())
    assert race["kind"] == "race"


def test_race_pace_from_target_time():
    p = planner.default_prefs()
    p["goal"].update(event_distance_km=21.1, target_time="1:45:00")
    assert abs(planner.race_pace(p, PACES) - 299) <= 1  # 4:59/km


def test_rotation_avoids_repeating_last_workout():
    a = plan({"intensity": 50}, recent=["fartlek"])
    q = [d["kind"] for d in a["days"] if d["kind"] in planner.WORKOUT_LABELS]
    assert q and q[0] != "fartlek"


def test_trimp_and_pmc_shape():
    assert metrics.trimp(3600, 150, 50, 190) > metrics.trimp(3600, 130, 50, 190) > 0
    loads = {(TUE - timedelta(days=i)).isoformat(): (60 if i % 2 else 0) for i in range(60)}
    rows = metrics.pmc(loads)
    assert rows[-1]["ctl"] > 0 and len(rows) == 60


def test_every_workout_builds_and_converts_to_garmin():
    from app.garmin_push import build_running_workout

    p = planner.default_prefs()
    for k in ["easy", "recovery", "long", *planner.WORKOUT_LABELS.keys() - {"strides"}]:
        w = planner.build_workout(k, 55, PACES, p, "build", strides=True)
        assert 0 < w["minutes"] <= 75, (k, w["minutes"])
        d = build_running_workout({**w, "date": "2026-11-20"}).to_dict()
        assert d["workoutSegments"][0]["workoutSteps"]


# ───────── 訓練階段：有氧基礎起來後，強度課要從打底課轉成節奏跑、間歇跑 ─────────

def _staged(base_wk, days=(1, 3, 5, 6), recent=None, goal=None, intensity=50):
    p = planner.default_prefs()
    p["days"] = list(days)
    p["intensity"] = intensity
    if goal:
        p["goal"] = goal
    return planner.make_plan(today=date(2026, 11, 16), prefs=p, paces=PACES, recent_weeks=[200] * 4, rel_tsb=0,
                             ready={"score": 80}, checkins={}, done_dates=set(), recent_kinds=recent or [],
                             base_wk=base_wk)


def test_base_weeks_counts_consistency_and_tolerates_one_light_week():
    assert planner.base_weeks([150, 120, 80, 130, 140, 30, 20, 200]) == 4
    assert planner.base_weeks([50, 40, 300]) == 0
    assert planner.training_stage(2) == "foundation"
    assert planner.training_stage(5) == "aerobic"
    assert planner.training_stage(10) == "developed"
    assert planner.training_stage(10, novice=True) == "foundation"


def test_foundation_stage_only_light_quality():
    ks = set(kinds(_staged(2)))
    assert not ks & {"tempo", "intervals", "race_pace"}


def test_aerobic_stage_alternates_tempo_with_speed_work():
    first = kinds(_staged(5))
    assert "tempo" in first
    second = kinds(_staged(5, recent=first))
    assert "tempo" not in second and set(second) & {"fartlek", "hills"}


def test_developed_stage_with_five_days_gets_tempo_and_intervals():
    ks = kinds(_staged(10, days=(0, 1, 3, 5, 6)))
    assert "tempo" in ks and "intervals" in ks


def test_developed_four_days_alternates_tempo_and_intervals():
    seen, recent = set(), []
    for _ in range(4):
        ks = kinds(_staged(10, recent=recent))
        seen |= set(ks)
        recent += ks
    assert {"tempo", "intervals"} <= seen
    assert "progression" not in seen


def test_progression_not_in_auto_rotation_any_more():
    for stage in ("foundation", "aerobic", "developed"):
        for gt in ("fun", "base", "speed", "race"):
            p = planner.default_prefs()
            p["goal"] = {"type": gt}
            for phase in ("open", "base", "build", "peak"):
                assert "progression" not in planner.quality_menu(p, phase, stage)
