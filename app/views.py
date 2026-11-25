"""新版 UI 各頁需要的資料組裝（摘要、指標趨勢、活動、本週課表、健康參考範圍）。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import mean

from . import analysis, auth, coach, db, game, metrics, strength, sync, progress
from .config import TZ
from .garmin_client import DemoGarmin, RealGarmin, today_local
from .metrics import fmt_pace

# ───────────────────────────── 指標定義 ─────────────────────────────

METRICS = {
    "steps": {"label": "步數", "unit": "步", "col": "steps", "chart": "bar", "agg": "mean", "digits": 0,
              "about": "一天走的總步數，包含跑步。每天 7,000–10,000 步和較低的整體健康風險有關。"},
    "rhr": {"label": "安靜心率", "unit": "bpm", "col": "rhr", "chart": "line", "agg": "mean", "digits": 0,
            "about": "完全休息時的心率。有氧體能變好通常會慢慢下降；突然比平常高 5 bpm 以上，常見原因是疲勞、睡眠不足或快感冒。"},
    "hrv": {"label": "HRV", "unit": "ms", "col": "hrv_avg", "chart": "line", "agg": "mean", "digits": 0,
            "about": "夜間心率變異度。數值因人而異，重點是和你自己的基準比：比平常低代表身體壓力較大、恢復較差。"},
    "sleep": {"label": "睡眠", "unit": "小時", "col": "sleep_hours", "chart": "bar", "agg": "mean", "digits": 1,
              "about": "每晚總睡眠時間。成人建議 7–9 小時，訓練量大時需要更多。"},
    "sleep_score": {"label": "睡眠分數", "unit": "分", "col": "sleep_score", "chart": "line", "agg": "mean", "digits": 0,
                    "about": "Garmin 依睡眠長度、深睡與 REM 比例、清醒次數給的分數，80 以上算好。"},
    "stress": {"label": "壓力", "unit": "", "col": "stress_avg", "chart": "line", "agg": "mean", "digits": 0,
               "about": "Garmin 依心率變異估算的全天平均壓力（0–100）。25 以下休息、26–50 低、51–75 中、76 以上高。"},
    "body_battery": {"label": "Body Battery", "unit": "", "col": "body_battery", "chart": "bar", "agg": "mean", "digits": 0,
                     "about": "一天中最高的身體能量值（0–100）。睡得好、壓力低時會充得比較滿。"},
    "active_kcal": {"label": "活動熱量", "unit": "kcal", "col": "active_kcal", "chart": "bar", "agg": "mean", "digits": 0,
                    "about": "活動消耗的熱量，不包含基礎代謝。"},
    "intensity": {"label": "強度分鐘", "unit": "分", "col": "intensity_min", "chart": "bar", "agg": "mean", "digits": 0,
                  "about": "中等強度 1 分鐘算 1 分、高強度 1 分鐘算 2 分。世界衛生組織建議每週至少 150 分。"},
    "run_km": {"label": "跑步里程", "unit": "km", "source": "activities", "chart": "bar", "agg": "sum", "digits": 1,
               "about": "跑步的總距離。長期看趨勢比單週重要，每週增加最好不超過 10%。"},
    "ctl": {"label": "體能 CTL", "unit": "", "source": "pmc", "chart": "line", "agg": "last", "digits": 0,
            "about": "近 42 天訓練負荷的加權平均，代表長期累積的體能。穩定上升 = 正在進步。"},
}

DEFAULT_CARDS = ["readiness", "steps", "rhr", "sleep", "hrv", "run_km", "body_battery", "stress", "partner"]
CARD_LABELS = {"readiness": "恢復分數", "partner": "我的夥伴", **{k: v["label"] for k, v in METRICS.items()}}


def _daily_rows(user_id: int, start: date, end: date) -> dict[str, dict]:
    return {r["date"]: r for r in db.q("SELECT * FROM daily WHERE user_id=? AND date BETWEEN ? AND ?",
                                         (user_id, start.isoformat(), end.isoformat()))}


def daily_values(user_id: int, key: str, start: date, end: date) -> dict[str, float | None]:
    m = METRICS[key]
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if m.get("source") == "activities":
        acc = {d: 0.0 for d in days}
        for a in db.q("SELECT date, distance_m FROM activities WHERE user_id=? AND date BETWEEN ? AND ?",
                      (user_id, start.isoformat(), end.isoformat())):
            acc[a["date"]] += (a["distance_m"] or 0) / 1000
        return acc
    if m.get("source") == "pmc":
        prefs = db.get_prefs(user_id)
        s0 = start - timedelta(days=120)
        acts = db.q("SELECT * FROM activities WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, s0.isoformat(), end.isoformat()))
        daily = db.q("SELECT * FROM daily WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date", (user_id, s0.isoformat(), end.isoformat()))
        cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, s0.isoformat(), end.isoformat()))
        rows = {r["date"]: r["ctl"] for r in metrics.pmc(metrics.daily_loads(acts, daily, prefs, s0, end, cross))}
        return {d: rows.get(d) for d in days}
    rows = _daily_rows(user_id, start, end)
    return {d: (rows.get(d) or {}).get(m["col"]) for d in days}


def metric_series(user_id: int, key: str, rng: str) -> dict:
    if key not in METRICS:
        raise KeyError(key)
    m = METRICS[key]
    today = today_local()
    span = {"W": 7, "M": 30, "6M": 182, "Y": 365}[rng]
    start = today - timedelta(days=span - 1)
    vals = daily_values(user_id, key, start, today)
    prev = daily_values(user_id, key, start - timedelta(days=span), start - timedelta(days=1))

    def agg(xs):
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        if m["agg"] == "sum":
            return sum(xs)
        if m["agg"] == "last":
            return xs[-1]
        return mean(xs)

    pts = []
    if rng in ("W", "M"):
        for d, v in vals.items():
            dd = date.fromisoformat(d)
            label = ["一", "二", "三", "四", "五", "六", "日"][dd.weekday()] if rng == "W" else str(dd.day)
            pts.append({"label": label, "start": d, "end": d, "value": v})
    else:
        groups: dict[str, list] = {}
        for d, v in vals.items():
            dd = date.fromisoformat(d)
            k = (dd - timedelta(days=dd.weekday())).isoformat() if rng == "6M" else d[:7]
            groups.setdefault(k, []).append((d, v))
        for k, items in groups.items():
            xs = [v for _, v in items]
            nn = [x for x in xs if x is not None]
            label = k[5:].replace("-", "/") if rng == "6M" else f"{int(k[5:7])}月"
            pts.append({"label": label, "start": items[0][0], "end": items[-1][0], "value": agg(xs),
                        "min": min(nn) if nn and m["chart"] == "line" else None,
                        "max": max(nn) if nn and m["chart"] == "line" else None})

    nums = [v for v in vals.values() if v is not None and (m["agg"] != "sum" or v > 0)]
    per_day = [v for v in vals.values() if v is not None]
    if m["agg"] == "sum":
        days_n = max(1, len(vals))
        avg = sum(per_day) / days_n * 7  # 每週平均
        prev_avg = sum(v for v in prev.values() if v) / max(1, len(prev)) * 7
    else:
        avg = mean(nums) if nums else None
        pn = [v for v in prev.values() if v is not None]
        prev_avg = mean(pn) if pn else None
    r = lambda x: None if x is None else round(x, m["digits"])
    for p in pts:
        for k in ("value", "min", "max"):
            if p.get(k) is not None:
                p[k] = r(p[k])
    return {
        "key": key, "label": m["label"], "unit": m["unit"], "chart": m["chart"], "about": m["about"],
        "range": rng, "points": pts,
        "avg_label": "每週平均" if m["agg"] == "sum" else "每日平均" if rng in ("W", "M") else "平均",
        "avg": r(avg), "min": r(min(nums)) if nums else None, "max": r(max(nums)) if nums else None,
        "total": r(sum(per_day)) if m["agg"] == "sum" else None,
        "prev_avg": r(prev_avg), "period": f"{start.isoformat()} – {today.isoformat()}",
        "goal": _goal(user_id, key),
    }


def _goal(user_id, key):
    p = db.get_prefs(user_id)
    if key == "steps":
        row = db.q1("SELECT steps_goal FROM daily WHERE user_id=? AND steps_goal IS NOT NULL ORDER BY date DESC LIMIT 1", (user_id,))
        return p.get("step_goal") or (row and row["steps_goal"]) or 8000
    if key == "active_kcal":
        return p.get("kcal_goal") or 500
    if key == "sleep":
        return p.get("sleep_goal") or 8
    return None


# ───────────────────────────── 摘要 ─────────────────────────────

def _latest(user_id: int, col: str, days: int = 3):
    since = (today_local() - timedelta(days=days)).isoformat()
    row = db.q1(f"SELECT date, {col} AS v FROM daily WHERE user_id=? AND date>=? AND {col} IS NOT NULL ORDER BY date DESC LIMIT 1",
                (user_id, since))
    return (row["v"], row["date"]) if row else (None, None)


def _strength_today(user_id: int, today, plan_today: dict) -> dict | None:
    if plan_today.get("kind") not in (None, "rest", "strength") or not db.get_prefs(user_id).get("strength_enabled", True):
        return None
    w0 = today - timedelta(days=today.weekday())
    prev = db.q1("SELECT data FROM plan WHERE user_id=? AND date=?", (user_id, (w0 - timedelta(days=1)).isoformat()))
    rmap = strength.plan_week(strength.week_kinds(user_id, w0), json.loads(prev["data"]).get("kind") if prev else None)
    logged = strength.done_dates(user_id, today.isoformat(), today.isoformat())
    rid = logged.get(today.isoformat()) or rmap.get(today.isoformat()) or "core"
    return {**strength.summary_for(rid), "done": today.isoformat() in logged, "date": today.isoformat()}


def _ask_intro(user_id: int, today: date) -> bool:
    """紀錄不到 4 週、也還沒填過問卷：在今天頁請他填「最近的跑步狀況」。"""
    if (db.get_prefs(user_id).get("self_report") or {}).get("weekly_km") is not None:
        return False
    first = db.q1("SELECT MIN(date) AS d FROM activities WHERE user_id=?", (user_id,))
    return not (first and first["d"]) or (today - date.fromisoformat(first["d"])).days < 28


def summary(user_id: int) -> dict:
    user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
    prefs = db.get_prefs(user_id)
    today = today_local()
    dash = sync.replan(user_id)
    dev = db.q1("SELECT data FROM device WHERE user_id=?", (user_id,))
    device = json.loads(dev["data"]) if dev else None

    week_start = today - timedelta(days=today.weekday())
    wk = db.q("SELECT duration_s, distance_m FROM activities WHERE user_id=? AND date>=?", (user_id, week_start.isoformat()))
    week_min = sum((a["duration_s"] or 0) for a in wk) / 60
    week_km = sum((a["distance_m"] or 0) for a in wk) / 1000
    week_target = dash["plan"].get("target_km") or 20
    t = _daily_rows(user_id, today, today).get(today.isoformat(), {})
    steps_goal = _goal(user_id, "steps")
    kcal_goal = _goal(user_id, "active_kcal")
    rings = [
        {"key": "run", "label": "本週跑步", "value": round(week_km, 1), "goal": round(week_target), "unit": "km"},
        {"key": "kcal", "label": "活動熱量", "value": round(t.get("active_kcal") or 0), "goal": kcal_goal, "unit": "kcal"},
        {"key": "steps", "label": "步數", "value": round(t.get("steps") or 0), "goal": steps_goal, "unit": "步"},
    ]

    g = game.compute(user_id)
    cards = []
    spark_start = today - timedelta(days=6)
    for key in prefs.get("dashboard") or DEFAULT_CARDS:
        if key == "readiness":
            r = dash["readiness"]
            cards.append({"key": key, "label": "恢復分數", "value": r["score"], "unit": "", "level": r["level"],
                          "sub": {"green": "狀態不錯", "yellow": "普通，別勉強", "red": "需要恢復"}[r["level"]]})
        elif key == "partner":
            ch = g["character"]
            cards.append({"key": key, "label": "我的夥伴", "value": f"Lv {g['level']}" if ch else "還沒選", "unit": "",
                          "sub": ch["stage_name"] if ch else "點這裡選一隻夥伴",
                          "species": ch and ch["species"], "stage": g["stage"],
                          "progress": (g["xp"] - g["level_floor"]) / max(1, g["level_ceil"] - g["level_floor"])})
        elif key in METRICS:
            m = METRICS[key]
            spark = list(daily_values(user_id, key, spark_start, today).values())
            if key == "run_km":
                val, sub = round(week_km, 1), "本週"
            elif key == "ctl":
                val, sub = spark[-1] and round(spark[-1]), "長期體能"
            else:
                v, d = _latest(user_id, m["col"])
                val = None if v is None else round(v, m["digits"])
                sub = "今天" if d == today.isoformat() else (d and f"{d[5:].replace('-', '/')}") or "沒有資料"
                if key == "steps" and v is not None:
                    sub = f"目標 {int(steps_goal):,}"
            cards.append({"key": key, "label": m["label"], "value": val, "unit": m["unit"], "sub": sub,
                          "spark": [None if x is None else round(x, 2) for x in spark]})
    return {
        "device": device, "provider": user["provider"], "last_sync": user["last_sync"],
        "last_sync_error": user["last_sync_error"], "rings": rings, "today": dash["plan"]["days"][0],
        "strength_today": _strength_today(user_id, today, dash["plan"]["days"][0]),
        "new_pbs": progress.recent_pbs(user_id, days=3),
        "ask_intro": _ask_intro(user_id, today),
        "plan_hold": bool(dash["plan"].get("hold")), "plan_provisional": dash["plan"].get("provisional") and {
            "time": dash["plan"].get("provisional_time")},
        "readiness": dash["readiness"], "novice": dash.get("novice"), "cards": cards, "card_options": CARD_LABELS,
        "dashboard": prefs.get("dashboard") or DEFAULT_CARDS,
        "today_activity": (db.q1("SELECT activity_id FROM activities WHERE user_id=? AND date=? ORDER BY start_local DESC LIMIT 1",
                                 (user_id, today.isoformat())) or {}).get("activity_id"),
        "partner": {"species": g["character"] and g["character"]["species"], "stage": g["stage"], "level": g["level"]},
    }


# ───────────────────────────── 活動 ─────────────────────────────

def _act_row(a, max_hr):
    km = (a.get("distance_m") or 0) / 1000
    return {"id": a["activity_id"], "date": a["date"], "start": a.get("start_local"), "name": a.get("name") or "跑步",
            "km": round(km, 2), "minutes": round((a.get("duration_s") or 0) / 60),
            "pace": fmt_pace(a["duration_s"] / km) if km and a.get("duration_s") else None,
            "avg_hr": a.get("avg_hr") and round(a["avg_hr"]), "kcal": a.get("calories") and round(a["calories"]),
            "zone": analysis.classify(a.get("avg_hr"), max_hr)}


def _cross_row(a):
    km = (a.get("distance_m") or 0) / 1000
    return {"id": a["activity_id"], "date": a["date"], "start": a.get("start_local"), "name": a.get("name") or a.get("sport_label"),
            "km": round(km, 2) if km else None, "minutes": round((a.get("duration_s") or 0) / 60), "pace": None,
            "avg_hr": a.get("avg_hr") and round(a["avg_hr"]), "kcal": a.get("calories") and round(a["calories"]),
            "cross": True, "sport": a.get("sport"), "sport_label": a.get("sport_label"), "group": a.get("sport_group") or "other"}


def load_verdict(ratio: float | None, partial: bool = False) -> dict | None:
    """近 7 天總負荷 ÷ 前 4 週平均週負荷。0.8–1.3 是一般認為比較安全的加量範圍，超過 1.5 受傷風險明顯變高。"""
    if ratio is None:
        return None
    if ratio > 1.5:
        return {"level": "bad", "label": "過多", "text": "比平常多一半以上，容易累積疲勞或受傷，接下來幾天建議減量。"}
    if ratio > 1.3:
        return {"level": "warn", "label": "偏多", "text": "比平常多 3 成以上，注意睡眠和恢復，別再額外加課。"}
    if ratio < 0.8:
        return {"level": "low", "label": "偏少" if not partial else "還在累積", "text": "比平常少，如果不是刻意減量，可以照課表慢慢補回來。" if not partial else "這週還沒結束，先看近 7 天的總量。"}
    return {"level": "ok", "label": "適中", "text": "活動量在平常的範圍內。"}


def activity_overview(user_id: int, before: str | None = None, limit: int = 40) -> dict:
    prefs = db.get_prefs(user_id)
    today = today_local()
    w0 = today - timedelta(days=today.weekday())
    since = w0 - timedelta(weeks=12)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? ORDER BY date", (user_id, since.isoformat()))
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=? ORDER BY date", (user_id, since.isoformat()))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? ORDER BY date", (user_id, since.isoformat()))
    max_hr = metrics.estimate_max_hr(acts, prefs)
    rest = metrics.estimate_rest_hr(daily)
    sex = prefs.get("sex", "M")
    weeks = []
    for i in range(12, -1, -1):
        ws = w0 - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        wa = [a for a in acts if ws.isoformat() <= a["date"] <= we.isoformat()]
        wc = [a for a in cross if ws.isoformat() <= a["date"] <= we.isoformat()]
        run_load = sum(metrics.trimp(a.get("duration_s"), a.get("avg_hr"), rest, max_hr, sex) for a in wa)
        other_load = sum(metrics.cross_trimp(a, rest, max_hr, sex) for a in wc)
        weeks.append({"week_start": ws.isoformat(), "km": round(sum((a["distance_m"] or 0) for a in wa) / 1000, 1),
                      "minutes": round(sum((a["duration_s"] or 0) for a in wa) / 60), "runs": len(wa),
                      "other_minutes": round(sum((a["duration_s"] or 0) for a in wc) / 60), "others": len(wc),
                      "run_load": round(run_load), "other_load": round(other_load), "load": round(run_load + other_load)})
    for i, w in enumerate(weeks):
        prev = weeks[max(0, i - 4):i]
        w["baseline"] = round(mean(p["load"] for p in prev)) if len(prev) == 4 else None
    this = weeks[-1]

    # 近 7 天（含今天）總活動量 vs 再往前 28 天（第 8–35 天）的平均週負荷。
    # 兩段完全不重疊；以前拿「前 4 個日曆週」當基準，會跟近 7 天重疊好幾天，比值被拉向 100%。
    def _load_between(lo: str, hi: str) -> float:
        return sum(metrics.trimp(a.get("duration_s"), a.get("avg_hr"), rest, max_hr, sex) for a in acts if lo <= a["date"] <= hi) + \
            sum(metrics.cross_trimp(a, rest, max_hr, sex) for a in cross if lo <= a["date"] <= hi)
    d7 = (today - timedelta(days=6)).isoformat()
    last7 = _load_between(d7, today.isoformat())
    base_lo, base_hi = today - timedelta(days=34), today - timedelta(days=7)
    base4 = [_load_between((base_lo + timedelta(weeks=k)).isoformat(), (base_lo + timedelta(weeks=k, days=6)).isoformat()) for k in range(4)]
    first = db.q1("SELECT MIN(d) d FROM (SELECT MIN(date) d FROM activities WHERE user_id=? UNION ALL "
                  "SELECT MIN(date) FROM cross_activities WHERE user_id=?)", (user_id, user_id))["d"]
    enough = bool(first) and (today - date.fromisoformat(first)).days >= 35 and mean(base4) > 0
    ratio = round(last7 / mean(base4), 2) if enough else None
    by_sport: dict[str, dict] = {}
    for a in cross:
        if a["date"] >= w0.isoformat():
            s = by_sport.setdefault(a.get("sport_label") or "其他", {"label": a.get("sport_label") or "其他",
                                                                     "group": a.get("sport_group") or "other", "minutes": 0, "count": 0})
            s["minutes"] += round((a.get("duration_s") or 0) / 60)
            s["count"] += 1
    volume = {"last7_load": round(last7), "baseline": round(mean(base4)) if enough else None, "ratio": ratio,
              "baseline_range": [base_lo.isoformat(), base_hi.isoformat()], "last7_range": [d7, today.isoformat()],
              "verdict": load_verdict(ratio), "run_minutes": this["minutes"], "other_minutes": this["other_minutes"],
              "run_share": round(this["run_load"] / this["load"] * 100) if this["load"] else None,
              "sports": sorted(by_sport.values(), key=lambda s: -s["minutes"])}

    lim = (" AND date<?" if before else "")
    args = (user_id, before, limit) if before else (user_id, limit)
    rows = [{**_act_row(a, max_hr), "cross": False} for a in db.q(
        "SELECT * FROM activities WHERE user_id=?" + lim + " ORDER BY date DESC, start_local DESC LIMIT ?", args)]
    rows += [_cross_row(a) for a in db.q(
        "SELECT * FROM cross_activities WHERE user_id=?" + lim + " ORDER BY date DESC, start_local DESC LIMIT ?", args)]
    rows.sort(key=lambda r: (r["date"], r.get("start") or ""), reverse=True)
    rows = rows[:limit]
    return {"weeks": weeks, "this_week": this, "activities": rows, "volume": volume,
            "load_change": round(this["load"] / this["baseline"] - 1, 2) if this.get("baseline") else None}


def cross_detail(user_id: int, a: dict) -> dict:
    prefs = db.get_prefs(user_id)
    acts = db.q("SELECT max_hr FROM activities WHERE user_id=?", (user_id,))
    daily = db.q("SELECT rhr FROM daily WHERE user_id=? ORDER BY date DESC LIMIT 60", (user_id,))
    max_hr = metrics.estimate_max_hr(acts, prefs)
    rest = metrics.estimate_rest_hr(daily)
    load = metrics.cross_trimp(a, rest, max_hr, prefs.get("sex", "M"))
    run_loads = [metrics.trimp(r.get("duration_s"), r.get("avg_hr"), rest, max_hr, prefs.get("sex", "M")) for r in db.q(
        "SELECT duration_s, avg_hr FROM activities WHERE user_id=? AND date>=?", (user_id, (date.fromisoformat(a["date"]) - timedelta(days=28)).isoformat()))]
    easy_ref = sorted(run_loads)[len(run_loads) // 3] if run_loads else None
    pct = a["avg_hr"] / max_hr if a.get("avg_hr") and max_hr else None
    if pct is None:
        effect = "沒有心率資料，負荷是用時間粗估的。"
    elif pct < 0.65:
        effect = "心率很低，屬於恢復性質，對跑步課表的影響很小。"
    elif pct < 0.8:
        effect = "中低強度，算進有氧量。隔天如果有強度課，照常跑沒問題。"
    else:
        effect = "心率偏高，會增加疲勞；如果隔天是強度課或長跑，留意狀態，必要時降一級。"
    return {**_cross_row(a), "max_hr": a.get("max_hr") and round(a["max_hr"]),
            "elevation_gain": a.get("elevation_gain"), "load": round(load),
            "load_vs_easy_run": round(load / easy_ref, 1) if easy_ref else None,
            "hr_pct": round(pct * 100) if pct else None, "effect": effect}


def activity_detail(user_id: int, activity_id: str) -> dict:
    a = db.q1("SELECT * FROM activities WHERE user_id=? AND activity_id=?", (user_id, activity_id))
    if not a:
        c = db.q1("SELECT * FROM cross_activities WHERE user_id=? AND activity_id=?", (user_id, activity_id))
        if c:
            return cross_detail(user_id, c)
        raise KeyError(activity_id)
    prefs = db.get_prefs(user_id)
    acts = db.q("SELECT max_hr FROM activities WHERE user_id=?", (user_id,))
    max_hr = metrics.estimate_max_hr(acts, prefs)
    cached = db.q1("SELECT data FROM activity_series WHERE user_id=? AND activity_id=?", (user_id, activity_id))
    series, series_error = None, None
    if cached:
        series = json.loads(cached["data"])
    else:
        user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
        try:
            if user["provider"] == "demo":
                series = DemoGarmin(user_id).fetch_series(activity_id, a)
            elif user["provider"] == "garmin":
                series = RealGarmin(auth.decrypt(user["garmin_tokens"])).fetch_series(activity_id)
            if series and series["points"]:
                with db.tx() as c:
                    c.execute("INSERT OR REPLACE INTO activity_series(user_id, activity_id, data) VALUES(?,?,?)",
                              (user_id, activity_id, json.dumps(series)))
        except Exception as e:
            series_error = "暫時抓不到這趟的心率曲線。到摘要頁按一次同步後再試；一直不行就到「手錶與資料來源」重新連結。"
            import logging
            logging.getLogger("ic8").warning("activity series %s: %s", activity_id, e)
    pts = (series or {}).get("points") or []

    # 心率區間時間分布（以最大心率百分比）
    bounds = [0.6, 0.7, 0.8, 0.9]
    zone_s = [0] * 5
    for p0, p1 in zip(pts, pts[1:]):
        if p0.get("hr"):
            pct = p0["hr"] / max_hr
            z = sum(1 for b in bounds if pct >= b)
            zone_s[z] += p1["t"] - p0["t"]
    # 每公里分段（用配速積分出距離）
    splits, dist, last_t, km_start_t, hrs = [], 0.0, 0, 0, []
    for p0, p1 in zip(pts, pts[1:]):
        dt = p1["t"] - p0["t"]
        if p0.get("pace"):
            dist += dt / p0["pace"] * 1000
        if p0.get("hr"):
            hrs.append(p0["hr"])
        if dist >= 1000 * (len(splits) + 1):
            splits.append({"km": len(splits) + 1, "pace": fmt_pace(p1["t"] - km_start_t),
                           "hr": round(mean(hrs)) if hrs else None})
            km_start_t, hrs = p1["t"], []

    plan = db.q1("SELECT data FROM plan WHERE user_id=? AND date=?", (user_id, a["date"]))
    plan_d = json.loads(plan["data"]) if plan else None
    run_notes = next((r["notes"] for r in analysis.analyze(user_id)["runs"] if r["date"] == a["date"]), [])
    hrs_all = [p["hr"] for p in pts if p.get("hr")]
    zones_out = [{"zone": f"Z{i + 1}", "label": lbl, "minutes": round(s / 60, 1)}
                 for i, (lbl, s) in enumerate(zip(["暖身", "燃脂", "有氧", "乳酸閾值", "最大"], zone_s))]
    zone_cls = analysis.classify(a.get("avg_hr"), max_hr)
    try:
        report = coach.review(user_id, a, pts, plan_d, max_hr, zone_cls)
    except Exception as e:      # 新評分出錯時退回舊版，報告不能整頁壞掉
        import logging
        logging.getLogger("ic8").warning("coach.review %s: %s", activity_id, e)
        report = run_report(a, pts, splits, zones_out, plan_d, zone_cls, max_hr)
    try:
        tired = coach.fatigue(user_id, a, report, plan_d)
    except Exception as e:
        import logging
        logging.getLogger("ic8").warning("coach.fatigue %s: %s", activity_id, e)
        tired = None
    xp = round((a.get("distance_m") or 0) / 1000 * game.XP_PER_KM) + (
        game.XP_PLANNED if plan_d and plan_d["kind"] not in ("rest", "done", "strength") else 0)
    return {
        "report": report, "xp": xp, "focus": [], "fatigue": tired, "progress": coach.goal_progress(user_id),
        **_act_row(a, max_hr), "max_hr": a.get("max_hr") and round(a["max_hr"]),
        "elevation_gain": a.get("elevation_gain"), "cadence": a.get("avg_cadence") and round(a["avg_cadence"]),
        "points": pts, "series_error": series_error,
        "hr_low": min(hrs_all) if hrs_all else None, "hr_high": max(hrs_all) if hrs_all else None,
        "zones": zones_out,
        "splits": splits,
        "plan": plan_d and {"title": plan_d["title"], "minutes": plan_d.get("minutes"), "kind": plan_d["kind"]},
        "notes": run_notes, "max_hr_ref": round(max_hr),
        "device": (json.loads(db.q1("SELECT data FROM device WHERE user_id=?", (user_id,))["data"])["name"]
                   if db.q1("SELECT 1 AS x FROM device WHERE user_id=?", (user_id,)) else None),
    }


# ───────────────────────────── 訓練報告 ─────────────────────────────

STEADY = {"easy", "recovery", "long", "tempo", None}
PTS = {"good": 100, "ok": 70, "bad": 40}


def _sec(p: str) -> int:
    m, s_ = p.split(":")
    return int(m) * 60 + int(s_)


def run_report(a: dict, pts: list, splits: list, zones: list, plan: dict | None, zone: str, max_hr: float = 190) -> dict:
    """跑完之後的訓練報告：每個面向給「好／普通／要加強」和一句白話說明。"""
    items = []

    def add(key, label, value, verdict, text, tip=None):
        items.append({"key": key, "label": label, "value": value, "verdict": verdict, "text": text, "tip": tip})

    mins = (a.get("duration_s") or 0) / 60
    kind = (plan or {}).get("kind")
    if kind in ("rest", "done", "race", "strength", "event"):
        plan, kind = None, None
    steady = kind in STEADY and zone != "hard" if kind is None else kind in STEADY

    if plan and plan.get("minutes"):
        ratio = mins / plan["minutes"]
        v = "good" if 0.9 <= ratio <= 1.15 else "ok" if 0.7 <= ratio <= 1.35 else "bad"
        add("volume", "完成度", f"{round(ratio * 100)}%", v,
            f"計畫 {round(plan['minutes'])} 分鐘，實際 {round(mins)} 分鐘。",
            None if v == "good" else ("時間不夠時，寧可縮短主課也別跳過熱身。" if ratio < 0.9 else "多跑的量會吃掉後面幾天的恢復，下次照計畫就好。"))

    hr_pts = [p["hr"] for p in pts if p.get("hr")]
    if plan and hr_pts:
        # 和分析頁同一套門檻：最大心率 82% 以下算輕鬆、85% 以上算高強度
        low = sum(1 for h in hr_pts if h < 0.82 * max_hr) / len(hr_pts)
        high = sum(1 for h in hr_pts if h >= 0.85 * max_hr) / len(hr_pts)
        if plan.get("intensity") in ("easy", "moderate") and kind != "tempo":
            v = "good" if low >= 0.85 else "ok" if low >= 0.65 else "bad"
            add("intensity", "強度控制", f"{round(low * 100)}% 在輕鬆心率", v,
                f"這堂是「{plan['title']}」，應該大部分時間低於 {round(0.82 * max_hr)} bpm。",
                None if v == "good" else "輕鬆跑太快是最常見的問題：放慢到能完整講話的速度，強度課才有力氣跑好。")
        else:
            v = "good" if high >= 0.25 else "ok" if high >= 0.12 else "bad"
            add("intensity", "強度到位", f"{round(high * 100)}% 在高心率", v,
                f"這堂是「{plan['title']}」，主課要有足夠時間高於 {round(0.85 * max_hr)} bpm。",
                None if v == "good" else "主課段可以再用力一點；如果是太累跑不上去，前一天改成休息或恢復跑。")

    sp = [_sec(x["pace"]) for x in splits]
    if steady and len(sp) >= 3:
        mean_p = mean(sp)
        cv = (sum((x - mean_p) ** 2 for x in sp) / len(sp)) ** 0.5 / mean_p
        v = "good" if cv < 0.03 else "ok" if cv < 0.06 else "bad"
        add("pacing", "配速穩定度", f"±{round(cv * 100, 1)}%", v, "每公里配速的起伏程度，越小代表配速感越好。",
            None if v == "good" else "試著前 1 公里刻意慢 10 秒，之後盯著手錶維持同一個配速。")
        half = len(sp) // 2
        fade = mean(sp[half:]) / mean(sp[:half]) - 1
        v = "good" if fade <= 0.01 else "ok" if fade <= 0.04 else "bad"
        add("fade", "後半表現", ("後半快 " if fade < 0 else "後半慢 ") + f"{abs(round(fade * 100, 1))}%", v,
            "後半比前半快（或持平）代表前面配速保守、體力分配得好。",
            None if v == "good" else "後半掉速通常是起跑太快或耐力不足：起跑放慢，並把每週長跑慢慢拉長。")

    hp = [p for p in pts if p.get("hr") and p.get("pace") and p["pace"] < 900 and p["t"] > 300]
    if steady and mins >= 30 and len(hp) >= 20:
        h = len(hp) // 2
        ef1 = mean(1000 / p["pace"] / p["hr"] for p in hp[:h])
        ef2 = mean(1000 / p["pace"] / p["hr"] for p in hp[h:])
        drift = (ef1 - ef2) / ef1
        v = "good" if drift < 0.05 else "ok" if drift < 0.10 else "bad"
        add("drift", "心率飄移", f"{round(drift * 100, 1)}%", v,
            "同樣配速下，後半心率比前半高多少。5% 以內代表有氧耐力足以應付這個距離。",
            None if v == "good" else "飄移偏高常見原因：有氧底子還不夠、天氣熱或補水不足。多累積輕鬆長跑會改善。")

    cad = a.get("avg_cadence")
    if cad and cad >= 130 and "treadmill" not in (a.get("type") or ""):   # 跑步機／異常低的步頻多半是量測問題
        v = "good" if cad >= 170 else "ok" if cad >= 162 else "bad"
        add("cadence", "步頻", f"{round(cad)} spm", v, "每分鐘步數。一般建議輕鬆跑 165–180。",
            None if v == "good" else "步頻偏低通常是步幅跨太大：試著小步快頻、腳落在身體正下方，一次提高 5% 就好。")

    if len(items) < 2:
        why = "沒有心率曲線或每公里資料" if not pts else "距離太短或沒有對應的課表"
        return {"score": None, "headline": f"這趟資料不夠做完整評分（{why}）", "items": items, "strengths": [],
                "improve": [{"label": i["label"], "tip": i["tip"]} for i in items if i["tip"]]}
    score = round(mean(PTS[i["verdict"]] for i in items))
    bad = [i for i in items if i["verdict"] == "bad"]
    okk = [i for i in items if i["verdict"] == "ok"]
    head = ("很扎實的一堂課" if score >= 90 else "整體不錯，有一兩個地方可以更好" if score >= 75
            else "有完成最重要，下面幾點值得注意" if score >= 60 else "這堂跑得有點掙扎，看看哪裡可以調整")
    return {"score": score, "headline": head, "items": items,
            "strengths": [i["label"] for i in items if i["verdict"] == "good"],
            "improve": [{"label": i["label"], "tip": i["tip"]} for i in bad + okk if i["tip"]]}


def ability_profile(user_id: int) -> dict:
    """近 4 週的五個面向，找出最需要補足的地方。"""
    a = analysis.analyze(user_id)
    four = a["weeks"][-5:-1]
    acts = db.q("SELECT duration_s, avg_cadence FROM activities WHERE user_id=? AND date>=?",
                (user_id, (today_local() - timedelta(days=28)).isoformat()))
    runs_pw = mean(w["runs"] for w in four) if four else 0
    longest = max([(x["duration_s"] or 0) / 60 for x in acts] or [0])
    hs = a["hard_share_4w"]
    rec = a["recovery"]
    clamp = lambda x: int(max(5, min(100, round(x))))
    dims = [
        {"key": "consistency", "label": "規律性", "score": clamp(runs_pw / 4 * 100), "value": f"每週 {runs_pw:.1f} 次",
         "tip": "先把「每週固定 3 次」當成唯一目標，時間短也沒關係。"},
        {"key": "endurance", "label": "耐力", "score": clamp(longest / 90 * 100), "value": f"最長 {round(longest)} 分",
         "tip": "每週一次長跑，每次比上次多 5–10 分鐘，速度放慢。"},
        {"key": "speed", "label": "速度刺激", "score": clamp(hs / 0.15 * 100 if hs <= 0.25 else 100 - (hs - 0.25) * 250),
         "value": f"中高強度佔 {round(hs * 100)}%",
         "tip": "每週加一堂法特雷克或節奏跑。" if hs < 0.15 else "強度已經偏多，把輕鬆跑真的跑輕鬆。"},
        {"key": "aerobic", "label": "有氧效率", "score": clamp(70 + (a["ef_change"] or 0) * 500),
         "value": "持平" if not a["ef_change"] else f"{'+' if a['ef_change'] > 0 else ''}{round(a['ef_change'] * 100)}%",
         "tip": "多累積低心率的輕鬆跑，有氧效率是用時間換來的。"},
        {"key": "recovery", "label": "恢復", "score": clamp((rec["sleep_h_7"] or 7) / 7.5 * 100
                                                         - max(0, ((rec["rhr_7"] or 0) - (rec["rhr_base"] or 0))) * 6),
         "value": f"睡 {rec['sleep_h_7']} 小時" if rec["sleep_h_7"] else "—",
         "tip": "睡滿 7 小時比多跑一堂課有效；安靜心率偏高時就降強度。"},
    ]
    weakest = sorted(dims, key=lambda d: d["score"])[:2]
    return {"dims": dims, "focus": [d for d in weakest if d["score"] < 80]}


# ───────────────────────────── 本週課表（分析頁） ─────────────────────────────

def _skipped(ck):
    try:
        n = json.loads((ck or {}).get("note") or "")
        return (n["kind"], n["title"], n.get("minutes")) if (ck or {}).get("skip") else None
    except (ValueError, KeyError, TypeError):
        return None


def this_week(user_id: int) -> dict:
    today = today_local()
    w0 = today - timedelta(days=today.weekday())
    days_ = [w0 + timedelta(days=i) for i in range(7)]
    dash = sync.replan(user_id)
    current = {d["date"]: d for d in dash["plan"]["days"]}
    stored = {r["date"]: json.loads(r["data"]) for r in db.q(
        "SELECT date, data FROM plan WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, w0.isoformat(), days_[-1].isoformat()))}
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY start_local",
                (user_id, w0.isoformat(), days_[-1].isoformat()))
    prefs = db.get_prefs(user_id)
    max_hr = metrics.estimate_max_hr(db.q("SELECT max_hr FROM activities WHERE user_id=?", (user_id,)), prefs)
    checks = {r["date"]: r for r in db.q("SELECT * FROM checkins WHERE user_id=? AND date BETWEEN ? AND ?",
                                          (user_id, w0.isoformat(), days_[-1].isoformat()))}
    out, done, planned_n, missed, too_hard = [], 0, 0, [], []
    for d in days_:
        ds = d.isoformat()
        plan = current.get(ds) if ds >= today.isoformat() else stored.get(ds)
        da = [_act_row(a, max_hr) for a in acts if a["date"] == ds]
        is_run = bool(plan and plan["kind"] not in ("rest", "done", "event", "race", "strength"))
        if plan and plan["kind"] == "done":
            is_run = True
            plan = stored.get(ds) or plan
        if ds < today.isoformat():
            status = "done" if da and is_run else "extra" if da else "missed" if is_run else "rest"
        elif ds == today.isoformat():
            status = "done" if da else "today" if is_run else "rest"
        else:
            status = "upcoming" if is_run else "rest"
        if checks.get(ds, {}).get("skip") and not da:
            status = "skipped"
        if is_run:
            planned_n += 1
        if status == "done":
            done += 1
        if status == "missed":
            missed.append(ds)
        for x in da:
            cap = plan and plan.get("intensity") == "easy" and plan.get("hr_cap")
            if cap and x.get("avg_hr") and x["avg_hr"] > cap:
                too_hard.append((ds, cap, x["avg_hr"]))
        out.append({"date": ds, "weekday": f"週{['一', '二', '三', '四', '五', '六', '日'][d.weekday()]}",
                    "plan": plan and {k: plan.get(k) for k in ("kind", "title", "minutes", "distance_km", "intensity",
                                                               "summary", "time", "override", "steps", "why", "hr_cap",
                                                               "pace_range", "phase")},
                    "actual": da, "status": status, "is_today": ds == today.isoformat(),
                    "editable": ds >= today.isoformat()})
    # 休息日（和匯入課表的肌力日）的肌力／伸展菜單
    if prefs.get("strength_enabled", True):
        prev = db.q1("SELECT data FROM plan WHERE user_id=? AND date=?", (user_id, (w0 - timedelta(days=1)).isoformat()))
        rmap = strength.plan_week([{"date": x["date"], "kind": (x["plan"] or {}).get("kind")} for x in out],
                                  json.loads(prev["data"]).get("kind") if prev else None)
        logged = strength.done_dates(user_id, w0.isoformat(), days_[-1].isoformat())
        for x in out:
            rid = logged.get(x["date"]) or rmap.get(x["date"])
            if rid and not x["actual"]:
                x["strength"] = {**strength.summary_for(rid), "done": x["date"] in logged}
    run_days = [x for x in out if x["plan"] and x["plan"]["kind"] not in ("rest", "done", "event", "race", "strength")]
    planned_km = sum(x["plan"].get("distance_km") or 0 for x in run_days)
    actual_km = sum(a["km"] for x in out for a in x["actual"])
    pl = dash["plan"]
    tips = week_tips(out, done, planned_n, missed, too_hard, planned_km, actual_km, dash, today)
    return {"week_start": w0.isoformat(), "days": out, "done": done, "planned": planned_n,
            "planned_km": round(planned_km, 1), "actual_km": round(actual_km, 1), "target_km": pl.get("target_km"),
            "phase_label": pl.get("phase_label"), "week_idx": pl.get("week_idx"), "n_weeks": pl.get("n_weeks"),
            "limiter_label": pl.get("limiter_label"), "verdict": pl.get("verdict"), "hold": pl.get("hold"),
            "decision": pl.get("decision"), "provisional": pl.get("provisional"), "provisional_time": pl.get("provisional_time"),
            "imported": pl.get("imported"),
            "stoploss": pl.get("stoploss") or [], "next_checkpoint": pl.get("next_checkpoint"), "check": pl.get("check"),
            "readiness": dash["readiness"], "notes": pl["notes"], "tips": tips,
            "checkin": dash.get("checkin")}


def week_tips(days, done, planned_n, missed, too_hard, planned_km, actual_km, dash, today):
    tips = []

    def add(level, title, why, action, act=None):
        tips.append({"level": level, "title": title, "why": why, "action": action, "do": act})

    pl = dash["plan"]
    for s in pl.get("stoploss") or []:
        if s["rule"] == "pain":
            add("alert", s["title"], "停損規則優先於任何課表：同一處痛三天沒退，繼續跑最容易變成真的受傷。",
                "先停跑，建議就醫。好了之後按下面的按鈕恢復課表。", {"type": "resume", "label": "已經好了，恢復課表"})
        elif s["rule"] == "rhr":
            add("alert", s["title"], "晨間靜止心率偏高通常代表恢復不足、生病前兆或睡眠不夠。", s["action"] + "（已自動套用）。")
    left = [d for d in days if d["status"] in ("upcoming", "today")]
    if missed:
        add("warn", f"這週漏了 {len(missed)} 堂課",
            "規格書的規則：錯過單次課跳過、不補。把漏掉的量塞進剩下幾天，反而是受傷的典型起點。",
            f"剩下 {len(left)} 堂照常跑就好。")
    if too_hard:
        ds, cap, hr = too_hard[0]
        add("warn", "輕鬆跑的心率超過上限",
            f"{'、'.join(d[0][5:].replace('-', '/') for d in too_hard)} 的輕鬆跑平均心率 {round(hr)}，上限是 {cap}。",
            f"下次盯著心率跑：到 {cap - 2} 就放慢，必要時走一段。課表上的配速是參考，心率上限才是硬規則。")
    r = dash["readiness"]
    nxt = next((d for d in days if d["status"] in ("today", "upcoming") and d["plan"]
                and d["plan"]["intensity"] == "hard" and d["date"] <= (today + timedelta(days=1)).isoformat()), None)
    if r["score"] < 40 and nxt:
        add("info", "今天恢復分數偏低", f"恢復分數 {r['score']}，{nxt['weekday']}是「{nxt['plan']['title']}」。",
            "要不要改成輕鬆跑由你決定；改了就不補回來。", {"type": "easy", "date": nxt["date"], "label": "改成輕鬆跑"})
    if planned_n and done == planned_n and not left:
        add("good", "本週課表全部完成！", f"完成 {done} 堂，共 {round(actual_km, 1)} km。", "好好休息，下週繼續。")
    elif planned_n and actual_km > planned_km * 1.15 and actual_km > 10:
        add("warn", "這週跑得比計畫多", f"實際 {round(actual_km, 1)} km，計畫 {round(planned_km, 1)} km。",
            "多出來的量會吃掉後面的恢復；接下來幾天照表走，不要再加。")
    cp = pl.get("next_checkpoint")
    if cp and cp["date"] <= (today + timedelta(days=10)).isoformat():
        add("info", f"{cp['date'][5:].replace('-', '/')} 是檢核點：{cp['label']}", cp["purpose"] + "。",
            "照計畫全力（或控速）跑，不要另外加量。" if pl.get("imported") else "測驗完會依成績重算配速；照計畫全力（或控速）跑，不要另外加量。")
    if not tips:
        add("info", "照計畫進行中", f"已完成 {done} / {planned_n} 堂。", "有狀況隨時在下面調整課表。")
    return tips


# ───────────────────────────── 健康參考範圍 ─────────────────────────────

def health_ranges(user_id: int) -> dict:
    from . import season as season_mod
    from .engine import fmt_pace as fp
    daily = db.q("SELECT * FROM daily WHERE user_id=? ORDER BY date DESC LIMIT 60", (user_id,))[::-1]
    sp = season_mod.season_plan(user_id)
    mx, rest = sp["max_hr"], metrics.estimate_rest_hr(daily)
    z = sp["zones"]["zones"]
    names = [("E", "輕鬆跑 E", "超過就放慢，必要時走一段"), ("M", "馬拉松配速 M", ""), ("T", "節奏跑 T", ""),
             ("I", "間歇 I", "單趟末段才該到"), ("R", "快步 R", "20 秒太短，心率來不及反應，不看心率"),
             ("RP", "目標比賽配速", "")]
    zones = [{"key": k, "name": n, "pace": f"{fp(z[k]['min'])}–{fp(z[k]['max'])}", "hr_cap": z[k]["hrCap"],
              "alert": z[k]["alert"], "note": note} for k, n, note in names if k in z]
    hrv = [d["hrv_avg"] for d in daily[-30:] if d.get("hrv_avg")]
    return {
        "max_hr": mx, "rest_hr": round(rest), "max_hr_source": "實測／設定" if sp["max_hr_source"] == "measured" else "用年齡公式估算（建議實測後填入）",
        "zones": zones, "vdot": sp["zones"]["vdot_easy"], "vdot_quality": sp["zones"]["vdot_quality"],
        "zone_notes": sp["zones"]["notes"], "model": sp["model"],
        "garmin_note": "手錶預設的五區間是用 %最大心率切，Z2 通常定在 60–70%，比這裡輕鬆跑的上限（74%）低，"
                       "所以你的輕鬆跑常會被手錶標成 Z3。這不是課表排錯：以這裡的心率上限（bpm）為準。",
        "refs": [
            {"name": "安靜心率", "range": "60–100 bpm（規律運動者常見 40–60）", "you": f"{round(rest)} bpm"},
            {"name": "HRV", "range": "因人而異，和自己的基準比", "you": f"近 30 天平均 {round(mean(hrv))} ms" if hrv else "—"},
            {"name": "睡眠", "range": "每晚 7–9 小時", "you": None},
        ],
        "disclaimer": "以上僅供訓練參考，不能用來診斷。身體不適請就醫。",
    }
