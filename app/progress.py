"""進步的感覺：預估比賽成績隨時間的變化、最近破的個人紀錄。

預估成績用的是排課引擎同一套 VDOT：每個時間點，取「往前 120 天內 3–10.5 公里的最好一趟」（或 5K／10K 紀錄）
換算成目標距離的成績。跟整季規劃的實力評估一致，只是多算了過去每一週的值。
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db, engine, game, planner
from .garmin_client import today_local

WINDOW_DAYS = 120
RACE_KM = {"5k": 5.0, "10k": 10.0}


def _km(a: dict) -> float:
    return (a.get("distance_m") or 0) / 1000


def _best_vdot(acts: list[dict], recs: list[dict], end: date) -> float | None:
    lo, hi = (end - timedelta(days=WINDOW_DAYS)).isoformat(), end.isoformat()
    best = None
    for a in acts:
        if lo < a["date"] <= hi and a.get("duration_s") and 3 <= _km(a) <= 10.5:
            v = engine.vdot(_km(a), a["duration_s"])
            best = v if best is None or v > best else best
    for r in recs:
        if r["rtype"] in RACE_KM and lo < r["date"] <= hi:
            v = engine.vdot(RACE_KM[r["rtype"]], r["value"])
            best = v if best is None or v > best else best
    return best


def prediction(user_id: int, weeks: int = 16) -> dict:
    """每週一個點：那一週結束時，目標距離大概能跑多快。"""
    prefs = db.get_prefs(user_id)
    today = today_local()
    goal = planner.goal_from_prefs(prefs, today)
    race_km = float(goal["distance_km"])
    target_s = goal.get("target_s")
    acts = db.q("SELECT date, distance_m, duration_s FROM activities WHERE user_id=? AND date>=? ORDER BY date",
                (user_id, (today - timedelta(weeks=weeks, days=WINDOW_DAYS)).isoformat()))
    recs = db.q("SELECT rtype, value, date FROM records WHERE user_id=? AND rtype IN ('5k','10k')", (user_id,))
    points = []
    for i in range(weeks, -1, -1):
        end = today - timedelta(weeks=i)
        v = _best_vdot(acts, recs, end)
        points.append({"date": end.isoformat(), "seconds": round(engine.equivalent_time(v, race_km)) if v else None,
                       "vdot": round(v, 1) if v else None})
    have = [p for p in points if p["seconds"]]
    change = have[-1]["seconds"] - have[0]["seconds"] if len(have) >= 2 else None
    return {"race_km": race_km, "race_label": _race_label(race_km), "target_s": target_s,
            "target": engine.fmt_time(target_s) if target_s else None, "points": points,
            "now": engine.fmt_time(have[-1]["seconds"]) if have else None,
            "change_s": change, "weeks": weeks}


def _race_label(km: float) -> str:
    for k, lab in ((5, "5 公里"), (10, "10 公里"), (21.0975, "半馬"), (42.195, "全馬")):
        if abs(km - k) < 0.2:
            return lab
    return f"{km:g} 公里"


def recent_pbs(user_id: int, days: int = 7) -> list[dict]:
    """最近幾天破的個人紀錄（要比之前的紀錄好；第一次跑半馬、全馬距離也算）。"""
    acts = db.q("SELECT * FROM activities WHERE user_id=? ORDER BY start_local, date", (user_id,))
    records, _ = game.records_for(user_id, acts)
    since = (today_local() - timedelta(days=days)).isoformat()
    best: dict[str, float] = {}
    out = []
    for r in sorted(records, key=lambda r: r["date"]):
        k, v = r["rtype"], r["value"]
        if k not in game.RECORD_LABEL:
            continue
        prev = best.get(k)
        better = prev is None or (v > prev + 1 if k == "longest" else v < prev - 0.5)
        if not better:
            continue
        if r["date"] >= since and (prev is not None or k in ("half", "full")):
            out.append({"key": k, "label": game.RECORD_LABEL[k], "date": r["date"], "activity_id": r["activity_id"],
                        "value": (f"{v / 1000:.1f} km" if k == "longest" else engine.fmt_time(v)),
                        "prev": None if prev is None else (f"{prev / 1000:.1f} km" if k == "longest" else engine.fmt_time(prev)),
                        "gain": None if prev is None else (f"多跑 {(v - prev) / 1000:.1f} km" if k == "longest"
                                                           else f"快了 {engine.fmt_time(prev - v)}"),
                        "id": f"{k}:{r['activity_id']}"})
        best[k] = v
    return out
