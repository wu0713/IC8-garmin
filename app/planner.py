"""偏好設定、目標解析，以及「今天起 7 天」的每日課表組裝。

排課策略依《自動排跑步課表：演算法規格書》：engine.py（第 1–4、6–9 層）+ week.py（第 5 層）。
這個檔只負責把整季週表裡的這 7 天拿出來，套上執行期的覆寫（停損、跳過、手動調整、自己排課模式）。
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from . import plan_tables as T
from .engine import fmt_pace

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

# 「想做的課」：同一個強度裡選哪一種變化（間歇類可以換成坡道或變速跑）
WORKOUT_LABELS = {
    "tempo": "節奏跑", "intervals": "間歇跑", "hills": "坡道間歇", "fartlek": "變速跑", "reps": "快跑（200/400 m）", "strides": "快步（20 秒）",
}
MANUAL_KINDS = {"easy", "recovery", "long", "tempo", "intervals", "hills", "fartlek", "reps", "race_pace", "progression"}
LEVELS = {"auto": "自動判斷", "novice": "新手", "intermediate": "有基礎", "experienced": "有經驗"}

GOAL_TYPES = {
    "race": "準備一場比賽",
    "speed": "變快（5K/10K）",
    "base": "打有氧底子、跑更久",
    "fun": "隨興跑、保持新鮮感",
}
OPEN_DISTANCE = {"speed": 5.0, "base": 21.0975, "fun": 10.0}


def default_prefs() -> dict:
    return {
        "goal": {"type": "fun", "name": "跑得開心、穩定進步", "event_date": None,
                 "event_distance_km": None, "target_time": "", "note": ""},
        "days": [1, 3, 5, 6],           # 0=週一 … 6=週日
        "long_run_day": 6,
        "run_time": "06:30",
        "day_times": {},
        "max_minutes_weekday": 70,
        "max_long_minutes": 150,
        "workouts": {k: True for k in WORKOUT_LABELS},
        "push_to_garmin": False,
        "show_on_board": True,
        "plan_mode": "auto",            # auto = 依規格書自動排；manual = 照我自己的每週課表
        "template": {},
        "events": [],                   # 固定事件 [{type, name, date, end_date?, distance_km?, target_time?}]
        "dashboard": None,
        "level": "auto",
        "self_report": {},
        "excused_weeks": [],
        "brief_enabled": True,          # 每日晨報、晚報
        "brief_morning": "07:30",
        "brief_evening": "21:30",
        "weekly_review": True,          # 週日晚上的每週回顧
        "brief_weekly": "20:30",
        "strength_enabled": True,       # 休息日排肌力／伸展
        "step_goal": None, "kcal_goal": 500, "sleep_goal": 8,
        "calendar_alarm_min": 30,
        "sex": "M", "age": None, "max_hr": None,
        "easy_pace_s": None, "threshold_pace_s": None,
    }


def migrate_prefs(p: dict) -> dict:
    """舊版設定升級：races → events（B 級測驗賽），移除規格書改由演算法決定的欄位。"""
    if p.get("races") and not p.get("events"):
        p["events"] = [{"type": "race_b", "name": r.get("name") or "比賽", "date": r["date"],
                        "distance_km": r.get("distance_km"), "target_time": r.get("target_time") or ""}
                       for r in p["races"] if r.get("date")]
    for k in ("races", "intensity", "progression", "deload_every", "fixed_weekly_minutes", "min_week_minutes", "add_strides"):
        p.pop(k, None)
    for k in list(p.get("workouts") or {}):
        if k not in WORKOUT_LABELS:
            p["workouts"].pop(k)
    for k in WORKOUT_LABELS:
        p.setdefault("workouts", {}).setdefault(k, True)
    return p


def parse_hms(s: str | None) -> int | None:
    try:
        parts = [int(x) for x in (s or "").strip().split(":")]
    except ValueError:
        return None
    if not parts or not s.strip():
        return None
    if len(parts) == 2:   # 「h:mm」或「mm:ss」：小於 10 當小時
        return parts[0] * 3600 + parts[1] * 60 if parts[0] < 10 else parts[0] * 60 + parts[1]
    sec = 0
    for x in parts:
        sec = sec * 60 + x
    return sec


def goal_from_prefs(prefs: dict, today: date) -> dict:
    g = prefs["goal"]
    race_date = None
    if g.get("type") == "race" and g.get("event_date") and g.get("event_distance_km"):
        try:
            d = date.fromisoformat(g["event_date"])
            if d >= today:
                race_date = d.isoformat()
        except ValueError:
            pass
    if race_date:
        return {"distance_km": float(g["event_distance_km"]), "target_s": parse_hms(g.get("target_time")),
                "race_date": race_date, "name": g.get("name") or "目標賽", "type": "race"}
    dist = float(g.get("event_distance_km") or OPEN_DISTANCE.get(g.get("type"), 10.0))
    return {"distance_km": dist, "target_s": None, "race_date": None, "name": g.get("name") or "", "type": g.get("type") or "fun",
            "race_passed": g.get("type") == "race" and bool(g.get("event_date")) and g["event_date"] < today.isoformat()}


# ───────────────────────────── 每日組裝 ─────────────────────────────

EVENT_ICON = {"race_a": "🏁", "race_b": "🏁", "race_c": "🏁", "endurance": "🚴", "trail": "⛰️", "hike": "🥾",
              "other": "📌", "illness": "🤒"}


def event_day(ds: str, prefs: dict, goal: dict) -> dict | None:
    if goal.get("race_date") == ds:
        return {"kind": "race", "title": f"🏁 {goal['name']}", "intensity": "race", "minutes": 0, "distance_km": goal["distance_km"],
                "steps": [], "summary": f"{goal['distance_km']:g} 公里" + (f"，目標 {prefs['goal'].get('target_time')}" if prefs["goal"].get("target_time") else "")
                + "。前段別衝太快。", "why": "目標賽（A 級）"}
    for e in prefs.get("events") or []:
        d0, d1 = e.get("date"), e.get("end_date") or e.get("date")
        if d0 and d0 <= ds <= d1 and e.get("type") not in ("travel",):
            spec = T.EVENT_TYPES.get(e.get("type"), T.EVENT_TYPES["race_b"])
            is_race = e.get("type") in ("race_b", "race_c", "trail")
            if e.get("type") == "illness":
                return {"kind": "rest", "title": "🤒 生病，休息", "intensity": "rest", "minutes": 0, "distance_km": 0, "steps": [],
                        "summary": "發燒或生病時不跑。好了之後會自動安排幾天輕量。", "why": spec["label"]}
            return {"kind": "race" if is_race else "event", "title": f"{EVENT_ICON.get(e.get('type'), '📌')} {e.get('name') or spec['label']}",
                    "intensity": "race" if is_race else "rest", "minutes": 0, "distance_km": e.get("distance_km") or 0, "steps": [],
                    "summary": f"{spec['label']}" + (f"，{e['distance_km']:g} 公里" if e.get("distance_km") else "")
                    + (f"，目標 {e['target_time']}" if e.get("target_time") else "") + f"。恢復按「{spec['damage']}」安排。",
                    "why": spec["label"]}
    return None


def assemble_days(*, today: date, horizon: list[date], prefs: dict, goal: dict, sched: dict[str, dict],
                  zones_for, stoploss: list[dict], checkins: dict, done_dates: set[str], overrides: dict,
                  week_target: dict[str, float], imported: dict | None = None) -> tuple[list[dict], list[str]]:
    """sched：日期 → 第 5 層排好的課；zones_for(date) → 那天適用的配速區間。"""
    from .week import build_to_km, build_workout

    notes = []
    manual = prefs.get("plan_mode") == "manual" and any((prefs.get("template") or {}).values())
    paused = next((s for s in stoploss if s["rule"] == "pain"), None)
    rhr = next((s for s in stoploss if s["rule"] == "rhr"), None)
    week_end = today + timedelta(days=6 - today.weekday())
    out = []
    for d in horizon:
        ds = d.isoformat()
        z = zones_for(d)
        base = {"date": ds, "weekday": f"週{WEEKDAY_ZH[d.weekday()]}"}
        ev = event_day(ds, prefs, goal)
        imp = (imported or {}).get(ds)
        if imp and not paused and not (ds in done_dates and d == today):
            w = copy.deepcopy(imp)        # 匯入的課表：照表，不經過自動排課
        elif ev:
            w = ev
        elif ds in done_dates and d == today:
            w = {"kind": "done", "title": "今天已完成 ✅", "intensity": "done", "minutes": 0, "distance_km": 0, "steps": [],
                 "summary": "已同步到今天的跑步紀錄，好好恢復。", "why": ""}
        elif paused:
            w = {"kind": "rest", "title": "停跑（疼痛）", "intensity": "rest", "minutes": 0, "distance_km": 0, "steps": [],
                 "summary": f"{paused['label']}連續 {T.PAIN_DAYS} 天疼痛：課表暫停，建議就醫。", "why": "停損規則：同一處疼痛連續 3 天"}
        elif manual:
            t = (prefs.get("template") or {}).get(str(d.weekday()))
            if t and t.get("kind") not in (None, "rest"):
                e_mid = (z["E"]["min"] + z["E"]["max"]) / 2
                km = t.get("km") or round(float(t.get("minutes") or 40) * 60 / e_mid, 1)
                w = build_to_km(t["kind"], float(km), zones=z, goal=goal, why="你自己安排的每週課表")
            else:
                w = build_workout("rest", zones=z, why="你自己安排的每週課表")
        elif ds in sched:
            w = copy.deepcopy(sched[ds])
        else:
            w = build_workout("rest", zones=z, why="")
        if checkins.get(ds, {}).get("skip") and w["kind"] not in ("race", "event", "done", "strength"):
            w = build_workout("rest", zones=z, why="你回報今天沒辦法跑：跳過，不補（停損規則：錯過單次課跳過不補）")
            w["title"] = "今天跳過"
        if rhr and not paused and today <= d <= week_end and w["kind"] not in ("rest", "race", "event", "done", "strength", "custom"):
            km = round(w["distance_km"] * (1 - T.RHR_VOLUME_CUT) * 2) / 2
            k = "long" if w["kind"] in ("long", "long_m") else "easy"
            w = build_workout(k, km=max(3, km), zones=z, why="停損規則：晨間靜止心率連續 3 天偏高 → 跑量砍 30%、質量課改輕鬆跑")
        ov = overrides.get(ds)
        if ov and not paused:
            if ov["kind"] == "rest":
                w = build_workout("rest", zones=z, why="你手動改成休息")
            else:
                e_mid = (z["E"]["min"] + z["E"]["max"]) / 2
                km = ov.get("km") or (ov.get("minutes") and round(ov["minutes"] * 60 / e_mid, 1)) or {
                    "long": 12, "recovery": 5, "easy": 7}.get(ov["kind"], 8)
                w = build_to_km(ov["kind"], float(km), zones=z, goal=goal, why="你手動調整的課")
            w["override"] = True
        w["time"] = (prefs.get("day_times") or {}).get(str(d.weekday())) or prefs.get("run_time", "06:30")
        out.append({**base, **w})
    if paused:
        notes.append(f"停損：{paused['title']}。{paused['action']}")
    if rhr:
        notes.append(f"停損：{rhr['title']}。{rhr['action']}")
    if manual:
        notes.insert(0, "使用你自己安排的每週課表；配速和心率上限仍依你的能力計算")
    return out, notes


def describe_steps(steps, depth=0) -> list[str]:
    """把結構化步驟轉成人看得懂的文字（行事曆描述用）。"""
    names = {"warmup": "熱身", "cooldown": "緩和", "interval": "主課", "recovery": "恢復", "run": "跑"}
    out = []
    for s in steps:
        pad = "  " * depth
        if s["type"] == "repeat":
            out.append(f"{pad}重複 {s['n']} 次：")
            out += describe_steps(s["steps"], depth + 1)
            continue
        if s.get("km") and s.get("minutes", 0) >= 3 and s["type"] != "recovery":
            amt = f"{s['km']:g} km"
        else:
            m = s.get("minutes") or 0
            amt = f"{int(round(m))} 分" if m >= 1 else f"{int(round(m * 60))} 秒"
        pace = f" @ {fmt_pace(s['pace_hi'])}–{fmt_pace(s['pace_lo'])}/km" if s.get("pace_lo") else ""
        cap = f"，心率 ≤ {s['hr_cap']}" if s.get("hr_cap") else ""
        note = f"（{s['note']}）" if s.get("note") else ""
        out.append(f"{pad}• {names.get(s['type'], s['type'])} {amt}{pace}{cap}{note}")
    return out
