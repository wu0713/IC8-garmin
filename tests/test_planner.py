"""排課引擎：逐條對照《自動排跑步課表：演算法規格書》。"""
from datetime import date, timedelta

import pytest

from app import engine, planner
from app import plan_tables as T
from app.week import _violations, build_workout, schedule_week

MON = date(2026, 11, 16)
HALF = 21.0975


def ath(**kw):
    a = {"weekly_km": [30, 28, 32, 29, 29, 31, 27, 30, 26, 30, 28, 29], "max_hr": 190,
         "best_effort": {"km": 10, "seconds": 49 * 60, "date": "2026-10-01"},
         "long_runs": [{"km": 14, "seconds": 14 * 360, "avg_hr": 150, "date": "2026-11-01"}],
         "easy_hr_share": 0.75, "age": 35, "novice": False}
    a.update(kw)
    return a


def goal(**kw):
    g = {"distance_km": HALF, "target_s": 3600 + 45 * 60, "race_date": "2027-03-14", "name": "半馬", "type": "race"}
    g.update(kw)
    return g


# ───────── L1 診斷 ─────────

def test_consistency_limiter_when_two_weeks_broken():
    d = engine.diagnose(ath(weekly_km=[30, 10, 30, 30, 8, 30, 30, 30]), goal())
    assert d["limiter"] == "consistency" and d["needs_frequent_deload"]


def test_endurance_limiter_high_ratio_high_hr():
    a = ath(long_runs=[{"km": 14, "seconds": 14 * 400, "avg_hr": 172, "date": "2026-11-01"}])
    d = engine.diagnose(a, goal())
    assert d["decouple_ratio"] > 1.25 and d["limiter"] == "endurance"


def test_pacing_limiter_high_ratio_low_hr():
    a = ath(long_runs=[{"km": 14, "seconds": 14 * 400, "avg_hr": 140, "date": "2026-11-01"}])
    assert engine.diagnose(a, goal())["limiter"] == "pacing"


def test_speed_limiter_low_ratio_but_too_slow():
    a = ath(long_runs=[{"km": 16, "seconds": 16 * 318, "avg_hr": 160, "date": "2026-11-01"}])
    d = engine.diagnose(a, goal(target_s=3600 + 35 * 60))
    assert d["decouple_ratio"] <= 1.15 and d["limiter"] == "speed"


def test_no_long_run_means_endurance():
    assert engine.diagnose(ath(long_runs=[]), goal())["limiter"] == "endurance"


def test_baseline_is_median_not_mean():
    d = engine.diagnose(ath(weekly_km=[30, 30, 30, 90, 30, 30, 30, 30]), goal())
    assert d["baseline_km"] == 30


# ───────── L2 可行性 ─────────

def test_feasibility_formula():
    d = engine.diagnose(ath(), goal())
    f = engine.feasibility(d, goal(), 17, None)
    w_ramp = engine.ramp_weeks(29.5, f["v_target"], 0.08)
    assert f["w_need"] == w_ramp + w_ramp // 3 + 3
    assert f["verdict"] == "ok"


@pytest.mark.parametrize("base,r", [(20, 0.06), (30, 0.08), (60, 0.10)])
def test_growth_rate_tiers(base, r):
    assert T.growth_rate(base) == r


def test_infeasible_returns_achievable_and_later_date():
    a = ath(weekly_km=[12] * 12)
    g = goal(target_s=3600 + 25 * 60)
    d = engine.diagnose(a, g)
    f = engine.feasibility(d, g, 10, None)
    assert f["verdict"] == "infeasible"
    assert f["achievable_s"] > g["target_s"]
    assert f["suggested_date"] > g["race_date"]


def test_tight_band():
    d = engine.diagnose(ath(), goal())
    f = engine.feasibility(d, goal(), 9, None)
    assert f["verdict"] in ("tight", "infeasible") and f["ratio"] > 0.85


# ───────── L3 分期 / L4 跑量 ─────────

def test_phase_split_default_and_limiters():
    base = {"flags": []}
    ph = engine.split_phases(20, base)
    assert ph[-3:] == ["taper"] * 3 and ph.count("base") / 20 == pytest.approx(0.30, abs=0.06)
    assert engine.split_phases(20, {"flags": ["endurance"]}).count("base") >= 7
    sp = engine.split_phases(20, {"flags": ["speed"]})
    assert sp.count("base") < ph.count("base") and sp.count("specific") > ph.count("specific")


def test_ramp_rules():
    d = {"flags": [], "baseline_km": 30, "needs_frequent_deload": False}
    ph = engine.split_phases(20, d)
    v, tags = engine.ramp_volume(30, 45, ph, d)
    for i in range(1, 17):
        if tags[i] == "deload":
            assert v[i] == pytest.approx(max(x for x, t in zip(v[:i], tags[:i]) if t != "deload") * 0.7, abs=0.15)
        elif tags[i] in ("normal", "peak") and tags[i - 1] != "deload":
            assert v[i] - v[i - 1] <= max(4, v[i - 1] * 0.10) + 0.05
    assert tags[3] == "deload" and tags[7] == "deload"          # 每 4 週一次
    peak = max(v[:17])
    assert v[17:] == [round(peak * f, 1) for f in (0.75, 0.55, 0.35)]
    assert v.index(peak) <= 16                                   # 峰值在賽前第 3–4 週以前


def test_low_base_absolute_cap_and_consistency_flat():
    d = {"flags": ["consistency"], "baseline_km": 15, "needs_frequent_deload": True}
    ph = engine.split_phases(20, d)
    v, tags = engine.ramp_volume(15, 30, ph, d)
    assert v[:2] == [15] * 2                                     # 前 2 週不疊量
    assert all(b - a <= 4.05 for a, b, ta in zip(v[:17], v[1:17], tags) if b > a and ta != "deload")
    assert tags[2] == "deload"                                   # 每 3 週一次
    v2, _ = engine.ramp_volume(15, 30, ph, d, level="experienced")
    assert v2[1] > 15                                            # 自己選「有經驗」：不空轉


def test_long_run_three_caps():
    ph = ["base"] * 6
    longs = engine.long_run_series([30, 40, 60, 60, 60, 60], ph, HALF, 10, None)
    assert longs[0] == round(30 * engine.long_ratio(30, "base"), 1)   # 跑量低時比例放寬（30 km → 35%）
    assert longs[1] <= longs[0] + 2                              # 上週 + 2
    assert max(longs) <= HALF * 1.4
    assert engine.long_run_series([80] * 5, ["specific"] * 5, 42.195, 30, None)[-1] <= 42.195 * 0.75


# ───────── L5 每週排程 ─────────

def _zones(phase="specific"):
    return engine.derive_zones({"cur_vdot": 41, "flags": []}, goal(), phase, 190, None)["zones"]


def _sched(phase, days=(1, 3, 5, 6), V=40, flags=(), idx=0, long_km=14, event_days=None, tail=None, cp=None):
    w = {"idx": idx, "week_start": MON.isoformat(), "phase": phase, "week_in_phase": idx, "target_km": V, "long_km": long_km,
         "checkpoint": cp}
    return schedule_week(w, {"avail": set(days), "long_run_day": 6, "zones": _zones(phase), "diag": {"flags": list(flags)},
                             "goal": goal(), "event_days": event_days or {}, "prev_tail": tail or {},
                             "max_minutes_weekday": 90, "liked": {"tempo", "intervals", "strides"}, "max_hr": 190})


@pytest.mark.parametrize("phase,n", [("base", 0), ("build", 1), ("specific", 2)])
def test_quality_count_per_phase(phase, n):
    r = _sched(phase, days=(0, 1, 2, 3, 4, 5, 6))
    q = [w for w in r["days"].values() if w["kind"] in engine.QUALITY_KINDS]
    assert len(q) == n


@pytest.mark.parametrize("days", [(1, 3, 5, 6), (0, 1, 2, 3, 4, 5, 6), (0, 1, 2, 4, 5, 6), (2, 4, 6)])
@pytest.mark.parametrize("phase", ["base", "build", "specific", "taper"])
@pytest.mark.parametrize("flags", [(), ("consistency",), ("speed",)])
def test_hard_constraints_always_hold(days, phase, flags):
    r = _sched(phase, days=days, flags=flags)
    assign = {date.fromisoformat(k): v["kind"] for k, v in r["days"].items()}
    lim = 2 if "consistency" in flags else 3
    assert not _violations(assign, phase, lim)
    assert r["check"]["hard_share"] <= 0.20 + 1e-9 or not any(w["kind"] in engine.QUALITY_KINDS for w in r["days"].values())


def test_long_run_on_preferred_day_and_volume_close():
    r = _sched("build")
    sun = r["days"][(MON + timedelta(days=6)).isoformat()]
    assert sun["kind"] == "long"
    assert abs(r["check"]["total_km"] - 40) / 40 <= 0.05


def test_previous_week_long_run_blocks_monday_quality():
    tail = {(MON - timedelta(days=1)).isoformat(): "long"}
    r = _sched("specific", days=(0, 1, 2, 3, 4, 5, 6), tail=tail)
    assert r["days"].get(MON.isoformat(), {}).get("kind") not in engine.QUALITY_KINDS


def test_disliked_intervals_swapped_for_variant():
    w = {"idx": 0, "week_start": MON.isoformat(), "phase": "specific", "week_in_phase": 0, "target_km": 40, "long_km": 14}
    r = schedule_week(w, {"avail": {1, 3, 5, 6}, "long_run_day": 6, "zones": _zones(), "diag": {"flags": []}, "goal": goal(),
                          "event_days": {}, "prev_tail": {}, "max_minutes_weekday": 90, "liked": {"tempo", "hills"}, "max_hr": 190})
    kinds = {x["kind"] for x in r["days"].values()}
    assert "intervals" not in kinds and "hills" in kinds


def test_base_phase_has_strides_but_no_quality():
    r = _sched("base")
    assert not any(w["kind"] in engine.QUALITY_KINDS for w in r["days"].values())
    assert any("快步" in w["title"] for w in r["days"].values())


# ───────── L6 配速區間 ─────────

def test_easy_uses_current_quality_uses_target():
    d = {"cur_vdot": 41, "flags": []}
    base = engine.derive_zones(d, goal(), "base", 190, None)["zones"]
    spec = engine.derive_zones(d, goal(), "specific", 190, None)["zones"]
    assert base["E"] == {**spec["E"], "source": base["E"]["source"]}
    assert spec["T"]["min"] < base["T"]["min"]                   # 專項期的節奏跑比基礎期快
    assert spec["E"]["hrCap"] == round(190 * 0.74) and spec["T"]["hrCap"] == round(190 * 0.88)
    assert spec["E"]["alert"] == spec["E"]["hrCap"] - 2 and spec["R"]["hrCap"] is None


def test_easy_too_fast_offset_in_base_only():
    d = {"cur_vdot": 41, "flags": [], "easy_too_fast": True}
    a = engine.derive_zones(d, goal(), "base", 190, None)["zones"]["E"]
    b = engine.derive_zones(d, goal(), "build", 190, None)["zones"]["E"]
    assert a["min"] - b["min"] == T.EASY_SLOWDOWN_S


def _samples(n, drift=0.02):
    return [{"speed": 2.6 + 0.05 * i, "hr": 120 + 10 * (2.6 + 0.05 * i) + 0.3 * (25 + i % 3), "wbgt": 25 + i % 3, "drift": drift}
            for i in range(n)]


def test_regression_needs_eight_clean_samples():
    assert not engine.fit_hr_pace(_samples(7))["active"]
    assert not engine.fit_hr_pace(_samples(12, drift=0.08))["active"]   # 漂移 > 5% 的不算
    m = engine.fit_hr_pace(_samples(10))
    assert m["active"] and m["uses_wbgt"]


def test_regression_overrides_easy_but_capped():
    m = engine.fit_hr_pace(_samples(10))
    d = {"cur_vdot": 41, "flags": []}
    table = engine.derive_zones(d, goal(), "base", 190, None)["zones"]["E"]
    cal = engine.derive_zones(d, goal(), "base", 190, m)["zones"]["E"]
    assert cal["source"] == "regression"
    assert cal["min"] >= table["min"] - T.REGRESSION_MAX_SPEEDUP_S


# ───────── L7 事件 ─────────

def test_b_race_windows_and_union():
    evs = [{"type": "race_b", "name": "10K", "date": "2026-12-06", "distance_km": 10},
           {"type": "race_c", "name": "5K", "date": "2026-12-08", "distance_km": 5}]
    e = engine.expand_events(evs, goal(), MON, 17)
    assert e["days"]["2026-12-06"][0] == "event"
    assert all(e["days"][f"2026-12-0{i}"][0] == "light" for i in (3, 4, 5))
    assert e["days"]["2026-12-07"][0] == "rest"                  # 賽後第一天休息 vs 下一場的賽前輕量：取較強的，不相加
    assert e["days"]["2026-12-09"][0] == "easy"


def test_travel_and_ride_and_guard():
    evs = [{"type": "travel", "name": "出差", "date": "2026-12-08", "end_date": "2026-12-10"},
           {"type": "endurance", "name": "環島", "date": "2027-01-16"},
           {"type": "race_b", "name": "太近", "date": "2027-03-01", "distance_km": 10}]
    e = engine.expand_events(evs, goal(), MON, 17)
    assert e["weeks"]["2026-12-07"]["mult"] == 0.6
    assert e["weeks"]["2027-01-11"]["max_runs"] == 2
    assert all(e["days"][f"2027-01-{d}"][0] == "hrcap" for d in (17, 18, 19))
    assert any("太近" in w for w in e["warnings"])


# ───────── L8 檢核點 ─────────

def test_checkpoints_positions_and_bands():
    ph = engine.split_phases(17, {"flags": []})
    cps = engine.make_checkpoints(17, ph, goal(), MON)
    assert [c["test_km"] for c in cps] == [round(HALF / 4, 1), round(HALF / 2, 1), round(HALF, 2)]
    eq = cps[1]["equivalent_s"]
    assert engine.judge_checkpoint(cps[1], eq - 10, cps[1]["test_km"])["action"] == "hold"
    assert engine.judge_checkpoint(cps[1], eq * 1.03, cps[1]["test_km"])["action"] == "on_track"
    assert engine.judge_checkpoint(cps[1], eq * 1.07, cps[1]["test_km"])["action"] == "slow_small"
    assert engine.judge_checkpoint(cps[1], eq * 1.12, cps[1]["test_km"])["action"] == "slow_big"


def test_test_week_puts_test_on_long_day():
    cp = {"test_km": 5.3, "effort": "全力", "label": "測驗：5.3 km 全力", "date": (MON + timedelta(days=6)).isoformat(),
          "purpose": "x"}
    r = _sched("base", cp=cp)
    assert r["days"][cp["date"]]["kind"] == "test"


# ───────── L9 停損 ─────────

def test_stoploss_rules():
    today = date(2026, 11, 20)
    ck = {(today - timedelta(days=i)).isoformat(): {"pain": "knee"} for i in range(3)}
    rhr = {(today - timedelta(days=i)).isoformat(): 52 for i in range(4, 30)}
    rhr.update({(today - timedelta(days=i)).isoformat(): 61 for i in range(3)})
    comp = [{"week_start": "2026-11-02", "planned_km": 40, "actual_km": 20, "runs": 2},
            {"week_start": "2026-11-09", "planned_km": 40, "actual_km": 0, "runs": 0}]
    out = {t["rule"] for t in engine.evaluate_stoploss(today=today, checkins=ck, rhr=rhr, week_completion=comp,
                                                        drifts=[0.12, 0.11, 0.15], resume_after=None)}
    assert out == {"pain", "rhr", "completion", "drift", "missed_week"}
    calm = engine.evaluate_stoploss(today=today, checkins={}, rhr={}, week_completion=[], drifts=[0.12, 0.04, 0.15],
                                    resume_after=None)
    assert calm == []


def test_insertions_shift_ramp():
    d = {"flags": [], "baseline_km": 30, "needs_frequent_deload": False}
    ph = engine.split_phases(20, d)
    v0, _ = engine.ramp_volume(30, 50, ph, d)
    v1, t1 = engine.ramp_volume(30, 50, ph, d, insertions={5: "repeat"})
    assert t1[5] == "repeat" and v1[5] == v1[4] and v1[6] <= v0[6]
    v2, _ = engine.ramp_volume(30, 50, ph, d, freeze_from=5)
    assert max(v2[5:17]) <= v2[4]


# ───────── 每日組裝 ─────────

def _assemble(**kw):
    p = planner.default_prefs()
    z = _zones("build")
    sched = {(MON + timedelta(days=1)).isoformat(): build_workout("tempo", zones=z, week_km=40)}
    args = dict(today=MON, horizon=[MON + timedelta(days=i) for i in range(7)], prefs=p, goal=goal(), sched=sched,
                zones_for=lambda d: z, stoploss=[], checkins={}, done_dates=set(), overrides={}, week_target={})
    args.update(kw)
    return planner.assemble_days(**args)


def test_skip_means_rest_and_no_makeup():
    days, _ = _assemble(checkins={(MON + timedelta(days=1)).isoformat(): {"skip": 1}})
    assert days[1]["kind"] == "rest"
    assert not any(d["kind"] == "tempo" for d in days)


def test_pain_pauses_everything():
    days, notes = _assemble(stoploss=[{"rule": "pain", "label": "膝蓋", "title": "膝蓋連續 3 天疼痛", "action": "停跑"}])
    assert all(d["kind"] == "rest" for d in days) and any("停損" in n for n in notes)


def test_every_workout_builds_and_converts_to_garmin():
    from app.garmin_push import build_running_workout
    z = _zones()
    for k in ["easy", "recovery", "long", "long_m", "tempo", "intervals", "hills", "fartlek", "race_pace", "progression"]:
        w = build_workout(k, km=10, zones=z, week_km=45, goal=goal(), strides=6 if k == "easy" else 0)
        assert w["distance_km"] > 0 and w["steps"]
        build_running_workout({**w, "date": "2026-11-20"})
    t = build_workout("test", zones=z, test={"test_km": 5, "effort": "全力", "label": "測驗"})
    build_running_workout({**t, "date": "2026-11-20"})


def test_goal_parsing():
    p = planner.default_prefs()
    p["goal"].update({"type": "race", "event_date": "2027-03-14", "event_distance_km": 21.1, "target_time": "1:45"})
    g = planner.goal_from_prefs(p, MON)
    assert g["target_s"] == 6300 and g["race_date"] == "2027-03-14"
    assert planner.goal_from_prefs(p, date(2027, 4, 1))["race_date"] is None


def test_long_run_never_shorter_than_easy_run_at_low_volume():
    r = _sched("base", days=(2, 5), V=8, long_km=3.6)
    days = r["days"].values()
    long_km = next(d["distance_km"] for d in days if d["kind"] == "long")
    easy_km = max(d["distance_km"] for d in days if d["kind"] in ("easy", "recovery"))
    assert long_km > easy_km
