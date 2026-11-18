"""從資料庫組出第 1、6、9 層需要的跑者資料（規格書的 Athlete）。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import mean, median

from . import db, engine, metrics


def compute_drift(points: list[dict]) -> float | None:
    """前後半的有氧脫鉤（同心率下的速度掉多少）。排除前 10 分鐘熱身和停表／走路的點。"""
    pts = [p for p in points if p.get("hr") and p.get("pace") and p["pace"] < 900 and p.get("t", 0) > 600]
    if len(pts) < 20 or pts[-1]["t"] - pts[0]["t"] < 15 * 60:
        return None
    mid = (pts[0]["t"] + pts[-1]["t"]) / 2
    a = [1000 / p["pace"] / p["hr"] for p in pts if p["t"] <= mid]
    b = [1000 / p["pace"] / p["hr"] for p in pts if p["t"] > mid]
    if len(a) < 8 or len(b) < 8:
        return None
    ef1, ef2 = mean(a), mean(b)
    return round((ef1 - ef2) / ef1, 4)


def _fallback_vdot(easy_pace: float) -> float:
    lo, hi = 20.0, 85.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if engine.pace_at(mid, 0.66) > easy_pace:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


def build(user_id: int, today: date, prefs: dict | None = None) -> dict:
    prefs = prefs or db.get_prefs(user_id)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? AND date<=? ORDER BY date",
                (user_id, (today - timedelta(days=400)).isoformat(), today.isoformat()))
    daily = db.q("SELECT date, rhr FROM daily WHERE user_id=? AND date>=? ORDER BY date",
                 (user_id, (today - timedelta(days=60)).isoformat()))
    plans = {r["date"]: json.loads(r["data"]).get("kind") for r in db.q(
        "SELECT date, data FROM plan WHERE user_id=? AND date>=?", (user_id, (today - timedelta(days=120)).isoformat()))}
    km = lambda a: (a.get("distance_m") or 0) / 1000

    # 跑量：當週以前、完整的週，最近一週在前；只算第一筆紀錄之後的週
    first = date.fromisoformat(acts[0]["date"]) if acts else today
    mon = engine.monday(today)
    weekly = []
    for i in range(1, 13):
        ws = mon - timedelta(weeks=i)
        if ws + timedelta(days=6) < first:
            break
        we = ws + timedelta(days=6)
        weekly.append(round(sum(km(a) for a in acts if ws.isoformat() <= a["date"] <= we.isoformat()), 1))

    # 最大心率：設定 > 實測峰值 > 公式
    seen = [a["max_hr"] for a in acts if a.get("max_hr")]
    if prefs.get("max_hr"):
        max_hr, src = float(prefs["max_hr"]), "measured"
    elif seen:
        max_hr, src = float(max(seen)), "measured"
    else:
        max_hr, src = 208 - 0.7 * (prefs.get("age") or 35), "estimated"

    recent = [a for a in acts if a["date"] >= (today - timedelta(days=120)).isoformat() and a.get("duration_s") and km(a) > 0]
    best = None
    for a in recent:
        if 3 <= km(a) <= 10.5:
            v = engine.vdot(km(a), a["duration_s"])
            if not best or v > best[0]:
                best = (v, {"km": round(km(a), 2), "seconds": a["duration_s"], "date": a["date"], "source": "activity"})
    for r in db.q("SELECT rtype, value, date FROM records WHERE user_id=? AND rtype IN ('5k','10k') AND date>=?",
                  (user_id, (today - timedelta(days=120)).isoformat())):
        d = {"5k": 5.0, "10k": 10.0}[r["rtype"]]
        v = engine.vdot(d, r["value"])
        if not best or v > best[0]:
            best = (v, {"km": d, "seconds": r["value"], "date": r["date"], "source": "record"})
    paces = sorted(a["duration_s"] / km(a) for a in recent if km(a) > 2)
    fallback = _fallback_vdot(paces[int(len(paces) * 0.6)]) if paces else None

    twelve = (today - timedelta(weeks=12)).isoformat()
    long_runs = [{"km": round(km(a), 2), "seconds": a["duration_s"], "avg_hr": a.get("avg_hr"), "date": a["date"]}
                 for a in acts if a["date"] >= twelve and a.get("duration_s") and km(a) >= 5]
    four = (today - timedelta(weeks=4)).isoformat()
    longest4 = max([km(a) for a in acts if a["date"] >= four] or [0])

    eight = (today - timedelta(weeks=8)).isoformat()
    short = [a for a in acts if a["date"] >= eight and a.get("avg_hr") and a.get("duration_s") and a["duration_s"] < 3600 and km(a) > 1]
    if short:
        cut = sorted(a["duration_s"] / km(a) for a in short)[len(short) // 4]   # 排除最快的四分之一（多半是強度課）
        easyish = [a for a in short if a["duration_s"] / km(a) >= cut] or short
        easy_share = mean(a["avg_hr"] / max_hr for a in easyish)
    else:
        easy_share = None

    samples, drifts = [], []
    for a in acts:
        if a["date"] < (today - timedelta(days=120)).isoformat() or not a.get("avg_hr") or not a.get("duration_s") or km(a) < 1:
            continue
        kind = plans.get(a["date"])
        steady = kind in ("easy", "long", "recovery") or (kind is None and a["avg_hr"] < 0.82 * max_hr)
        if not steady:
            continue
        if a["duration_s"] >= 20 * 60:
            samples.append({"speed": km(a) * 1000 / a["duration_s"], "hr": a["avg_hr"], "drift": a.get("hr_drift"),
                            "wbgt": engine.wbgt(a.get("temp_c"), a.get("humidity")), "date": a["date"]})
        if a.get("hr_drift") is not None and a["date"] >= (today - timedelta(days=21)).isoformat():
            drifts.append(a["hr_drift"])

    runs28 = sum(1 for a in acts if a["date"] >= (today - timedelta(days=28)).isoformat())
    novice = (not acts) or (today - first).days < 21 or runs28 < 6
    rhr = {d["date"]: d["rhr"] for d in daily if d.get("rhr")}
    hist = [v for k, v in rhr.items() if k < (today - timedelta(days=2)).isoformat()]
    return {
        "weekly_km": weekly, "max_hr": max_hr, "max_hr_source": src,
        "best_effort": best[1] if best else None, "fallback_vdot": fallback,
        "long_runs": long_runs, "recent_longest_km": round(longest4, 1), "easy_hr_share": easy_share and round(easy_share, 3),
        "easy_samples": samples, "drifts_recent": drifts, "age": prefs.get("age"), "novice": novice,
        "rhr": rhr, "rest_hr_base": round(median(hist)) if len(hist) >= 5 else None,
        "easy_pace": paces[int(len(paces) * 0.6)] if paces else None,
        "fallback_pace": metrics.estimate_paces(acts[-60:], prefs)["easy"],
    }
