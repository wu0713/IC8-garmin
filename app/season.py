"""長期週期規劃（月曆頁）。

從本週往後推到比賽日（沒有比賽就推 12 週），每週一個目標量：
- 依「進步速度」逐週加量，每 N 週一個減量週
- 有比賽：打底 → 強化 → 巔峰 → 減量 → 比賽週
- 週跑量上限 = 你設定的每天最長時間 × 可跑天數，不會排出做不到的量
這是「方向」而不是死板課表：每天的實際課表仍然由每日排課依當天狀態決定。
"""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta

from . import db, metrics, planner
from .garmin_client import today_local

PHASE_FOCUS = {
    "open": "依訓練階段安排：先打底，基礎起來後以節奏跑、間歇跑為主",
    "base": "累積有氧底子：輕鬆跑、長跑，基礎起來後加入節奏跑",
    "build": "加入節奏跑與間歇，長跑慢慢拉長",
    "peak": "比賽配速練習＋最長的長跑",
    "taper": "減量但保留強度，讓身體充飽電",
    "race_week": "輕鬆跑＋短加速，比賽日全力以赴",
    "post": "賽後恢復，想跑再跑",
}


def season_plan(user_id: int, today: date | None = None) -> dict:
    today = today or today_local()
    prefs = db.get_prefs(user_id)
    acts = db.q("SELECT date, duration_s, distance_m FROM activities WHERE user_id=? AND date>=?",
                (user_id, (today - timedelta(days=42)).isoformat()))
    recent = metrics.weekly_minutes(acts, today - timedelta(days=1), 4)
    meta = db.q1("SELECT data FROM plan_meta WHERE user_id=?", (user_id,))
    start_target = json.loads(meta["data"])["target_minutes"] if meta else max(
        sum(sorted(recent, reverse=True)[:3]) / 3 if any(recent) else 0, prefs.get("min_week_minutes") or 60)

    g = prefs["goal"]
    event = None
    if g.get("type") == "race" and g.get("event_date"):
        try:
            event = date.fromisoformat(g["event_date"])
        except ValueError:
            event = None

    monday = today - timedelta(days=today.weekday())
    n_weeks = max(1, min(40, ((event - monday).days // 7 + 1))) if event and event >= today else 12
    cap = float(prefs["max_minutes_weekday"]) * max(1, len(prefs["days"]) - 1) + float(prefs["max_long_minutes"])
    factor = {"gentle": 1.03, "normal": 1.07, "bold": 1.10}.get(prefs.get("progression"), 1.07)
    de = int(prefs.get("deload_every") or 0)

    weeks = []
    level = float(start_target)
    for i in range(n_weeks):
        ws = monday + timedelta(weeks=i)
        phase, _ = planner.phase_for(prefs, ws)
        if event and ws <= event <= ws + timedelta(days=6):
            phase = "race_week"
        deload = bool(de) and phase in ("open", "base", "build") and ws.isocalendar().week % de == 0
        if i > 0 and not deload and phase in ("open", "base", "build", "peak"):
            level = min(cap, level * factor, level + 45)
        mult = {"taper": 0.7, "race_week": 0.5, "post": 0.6}.get(phase, 0.75 if deload else 1.0)
        if prefs.get("fixed_weekly_minutes"):
            target = float(prefs["fixed_weekly_minutes"]) * (0.75 if deload else 1) * (mult if phase in ("taper", "race_week") else 1)
        else:
            target = level * mult
        long_m = min(float(prefs["max_long_minutes"]), target * (0.3 if len(prefs["days"]) > 2 else 0.45))
        if phase == "race_week":
            long_m = 0
        week_acts = [a for a in acts if ws.isoformat() <= a["date"] <= (ws + timedelta(days=6)).isoformat()]
        weeks.append({
            "week_start": ws.isoformat(),
            "phase": phase, "phase_label": planner.PHASE_ZH[phase],
            "deload": deload,
            "target_minutes": round(target),
            "long_minutes": round(long_m),
            "actual_minutes": round(sum((a.get("duration_s") or 0) for a in week_acts) / 60) if i == 0 else None,
            "focus": "減量週：量降到 75%，讓訓練效果被吸收" if deload else PHASE_FOCUS[phase],
            "has_event": any(ws.isoformat() <= ds <= (ws + timedelta(days=6)).isoformat() for ds in planner.all_races(prefs)),
            "races": [r["name"] for ds, r in sorted(planner.all_races(prefs).items())
                      if ws.isoformat() <= ds <= (ws + timedelta(days=6)).isoformat()],
        })
    return {"event_date": event.isoformat() if event else None, "goal_name": g.get("name"), "weeks": weeks}


def projected_days(user_id: int, upto: date) -> dict[str, dict]:
    """今天 +7 天之後的「預估課表」：照長期規劃的每週目標量、你的偏好、比賽和手動調整排出來。
    真的到那一週時，還是會依當時的身體狀態重排。"""
    today = today_local()
    prefs = db.get_prefs(user_id)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? ORDER BY date", (user_id, (today - timedelta(days=90)).isoformat()))
    paces = metrics.estimate_paces(acts, prefs)
    targets = {w["week_start"]: w for w in season_plan(user_id, today)["weeks"]}
    last_t = list(targets.values())[-1]["target_minutes"] if targets else 150
    ov = {r["date"]: r for r in db.q("SELECT date, kind, minutes FROM plan_overrides WHERE user_id=? AND date>=?", (user_id, today.isoformat()))}
    kinds = [json.loads(r["data"]).get("kind") for r in db.q(
        "SELECT data FROM plan WHERE user_id=? AND date>=? ORDER BY date", (user_id, (today - timedelta(days=14)).isoformat()))]
    races = planner.all_races(prefs)
    base_now = planner.base_weeks(metrics.weekly_minutes(acts, today - timedelta(days=1), 12))
    out = {}
    horizon_end = today + timedelta(days=6)
    w = (today + timedelta(days=7)) - timedelta(days=(today + timedelta(days=7)).weekday())
    while w <= upto:
        wk = targets.get(w.isoformat())
        tgt = wk["target_minutes"] if wk else last_t
        if any(w.isoformat() <= ds <= (w + timedelta(days=6)).isoformat() and not r.get("main") for ds, r in races.items()):
            tgt *= 0.8
        p2 = copy.deepcopy(prefs)
        p2["fixed_weekly_minutes"] = tgt
        plan = planner.make_plan(today=w, prefs=p2, paces=paces, recent_weeks=[tgt] * 4, rel_tsb=0, ready={"score": 75},
                                 checkins={}, done_dates=set(), recent_kinds=kinds, overrides=ov,
                                 base_wk=base_now + (w - today).days // 7 + 1)   # 照計畫練下去，有氧基礎會一週一週累積
        for d in plan["days"]:
            if date.fromisoformat(d["date"]) > horizon_end:
                out[d["date"]] = {**d, "projected": True, "phase_label": (wk or {}).get("phase_label")}
        kinds += [d["kind"] for d in plan["days"]]
        w += timedelta(days=7)
    return out


def _month_totals(acts, cross) -> list[dict]:
    """這個月各運動的合計：有距離的顯示公里，沒距離的（重訓、瑜珈）顯示時間。"""
    out = []
    if acts:
        out.append({"group": "run", "label": "跑步", "count": len(acts),
                    "km": round(sum((a.get("distance_m") or 0) for a in acts) / 1000, 1),
                    "minutes": round(sum((a.get("duration_s") or 0) for a in acts) / 60)})
    by: dict[str, dict] = {}
    for c in cross:
        k = c.get("sport_label") or "其他運動"
        t = by.setdefault(k, {"group": c.get("sport_group") or "other", "label": k, "count": 0, "km": 0.0, "minutes": 0})
        t["count"] += 1
        t["km"] += (c.get("distance_m") or 0) / 1000
        t["minutes"] += round((c.get("duration_s") or 0) / 60)
    for t in by.values():
        t["km"] = round(t["km"], 1) or None
        out.append(t)
    return out


def month(user_id: int, ym: str) -> dict:
    y, m = (int(x) for x in ym.split("-"))
    first = date(y, m, 1)
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    acts = db.q("SELECT activity_id, date, distance_m, duration_s, name, avg_hr FROM activities WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date",
                (user_id, first.isoformat(), last.isoformat()))
    plans = {r["date"]: json.loads(r["data"]) for r in db.q(
        "SELECT date, data FROM plan WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, first.isoformat(), last.isoformat()))}
    prefs = db.get_prefs(user_id)
    races = planner.all_races(prefs)
    td = today_local()
    today = td.isoformat()
    if last > td + timedelta(days=6) and last <= td + timedelta(days=120):
        for ds, d in projected_days(user_id, last).items():
            if ds > (td + timedelta(days=6)).isoformat():
                plans[ds] = d
    cross = db.q("SELECT activity_id, date, sport_label, sport_group, name, duration_s, distance_m FROM cross_activities "
                 "WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY start_local", (user_id, first.isoformat(), last.isoformat()))
    keys = ("kind", "title", "minutes", "distance_km", "intensity", "summary", "time", "override", "projected")
    days = []
    d = first
    while d <= last:
        ds = d.isoformat()
        da = [a for a in acts if a["date"] == ds]
        p = plans.get(ds)
        show = p and p["kind"] != "done"
        days.append({
            "date": ds, "weekday": f"週{planner.WEEKDAY_ZH[d.weekday()]}",
            "actual_km": round(sum((a.get("distance_m") or 0) for a in da) / 1000, 1) if da else None,
            "actual_min": round(sum((a.get("duration_s") or 0) for a in da) / 60) if da else None,
            "activity_id": da[0]["activity_id"] if da else None,
            "runs": [{"id": a["activity_id"], "km": round((a.get("distance_m") or 0) / 1000, 1),
                      "minutes": round((a.get("duration_s") or 0) / 60)} for a in da],
            "plan": {k: p.get(k) for k in keys} if show else None,
            "past": ds < today, "is_today": ds == today,
            "editable": today <= ds <= (td + timedelta(days=60)).isoformat(),
            "swappable": today <= ds <= (td + timedelta(days=6)).isoformat(),
            "event": ds in races, "race": races.get(ds),
            "others": [{"id": c["activity_id"], "label": c.get("sport_label") or "其他運動", "group": c.get("sport_group") or "other",
                        "name": c.get("name"), "minutes": round((c.get("duration_s") or 0) / 60),
                        "km": round((c.get("distance_m") or 0) / 1000, 1) or None} for c in cross if c["date"] == ds],
        })
        d += timedelta(days=1)
    total = round(sum((a.get("distance_m") or 0) for a in acts) / 1000, 1)
    fut = [x for x in days if x["plan"] and not x["past"] and x["plan"]["kind"] not in ("rest", "race")]
    return {"month": ym, "days": days, "total_km": total, "runs": len(acts),
            "others": len(cross), "other_minutes": round(sum((c.get("duration_s") or 0) for c in cross) / 60),
            "totals": _month_totals(acts, cross),
            "planned_runs": len(fut), "planned_minutes": round(sum(x["plan"]["minutes"] or 0 for x in fut)),
            "mode": prefs.get("plan_mode", "auto")}
