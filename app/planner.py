"""排課引擎。

設計原則：沒有寫死的「16 週半馬課表」。每個人用幾個參數描述自己想怎麼跑：
  - 目標：自由文字 + 類型（隨興／打底／速度／備賽）+ 可選的比賽日期與目標時間
  - 哪幾天能跑、長跑放哪天、平日最多跑多久
  - 強度偏好滑桿（0 = 全部輕鬆跑，100 = 喜歡操）
  - 進步速度（保守／一般／積極）
  - 喜歡／不喜歡哪些課（間歇、節奏跑、坡道、法特雷克、加速跑…）
引擎依照「最近實際跑量 + 今天恢復狀態」每天重排未來 7 天。
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from .metrics import fmt_pace

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

WORKOUT_LABELS = {
    "strides": "加速跑", "fartlek": "變速跑", "tempo": "節奏跑", "intervals": "間歇跑",
    "hills": "坡道衝刺", "progression": "漸進跑", "race_pace": "比賽配速跑",
}

GOAL_TYPES = {
    "fun": "隨興跑、保持新鮮感",
    "base": "打有氧底子、跑更久",
    "speed": "變快（5K/10K）",
    "race": "準備一場比賽",
}


def default_prefs() -> dict:
    return {
        "goal": {"type": "fun", "name": "跑得開心、穩定進步", "event_date": None,
                 "event_distance_km": None, "target_time": "", "note": ""},
        "days": [1, 3, 5, 6],           # 0=週一 … 6=週日
        "long_run_day": 6,
        "run_time": "06:30",
        "day_times": {},                # 個別日期時間，例如 {"5": "19:30"}
        "max_minutes_weekday": 60,
        "max_long_minutes": 120,
        "min_week_minutes": 60,
        "fixed_weekly_minutes": None,   # 想自己指定週跑量就填
        "intensity": 50,
        "progression": "normal",        # gentle | normal | bold
        "deload_every": 4,              # 每 N 週一個減量週，0 = 不要
        "workouts": {k: True for k in WORKOUT_LABELS},
        "add_strides": True,
        "push_to_garmin": False,
        "show_on_board": True,          # 在跑友排行榜顯示我
        "plan_mode": "auto",            # auto = 自動排課；manual = 照我自己的每週課表
        "template": {},                 # 自己安排時的每週課表 {"1": {"kind": "easy", "minutes": 40}, ...}
        "races": [],                    # 其他比賽 [{name, date, distance_km, target_time}]
        "dashboard": None,              # 摘要頁儀表板卡片（None = 預設）
        "strength_enabled": True,       # 休息日排肌力／伸展
        "weekly_review": True,          # 週日晚上的每週回顧
        "brief_weekly": "20:30",
        "brief_enabled": True,          # 每日晨報、晚報
        "brief_morning": "07:30",
        "brief_evening": "21:30",
        "step_goal": None, "kcal_goal": 500, "sleep_goal": 8,
        "calendar_alarm_min": 30,
        "sex": "M", "age": None, "max_hr": None,
        "easy_pace_s": None, "threshold_pace_s": None,
    }


# ───────────────────────────── 小工具 ─────────────────────────────

def _parse_hms(s: str) -> int | None:
    try:
        parts = [int(p) for p in s.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    if not parts:
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    if len(parts) == 2:  # 「mm:ss」或「h:mm」？ >= 10 分鐘的比賽多半是 h:mm
        sec = parts[0] * 3600 + parts[1] * 60 if parts[0] < 10 else sec
    return sec


def race_pace(prefs: dict, paces: dict) -> int:
    g = prefs["goal"]
    t = _parse_hms(g.get("target_time") or "")
    km = g.get("event_distance_km")
    if t and km:
        return round(t / float(km))
    if km and float(km) >= 30:
        return paces["marathon"]
    if km and float(km) >= 15:
        return round((paces["marathon"] + paces["tempo"]) / 2)
    return paces["tempo"] if km and float(km) >= 8 else paces["interval"]


def phase_for(prefs: dict, today: date) -> tuple[str, int | None]:
    g = prefs["goal"]
    if g.get("type") != "race" or not g.get("event_date"):
        return "open", None
    try:
        ev = date.fromisoformat(g["event_date"])
    except ValueError:
        return "open", None
    days = (ev - today).days
    if days < 0:
        return "post", days
    w = days / 7
    if w <= 1:
        return "race_week", days
    if w <= 2.5:
        return "taper", days
    if w <= 5:
        return "peak", days
    if w <= 12:
        return "build", days
    return "base", days


PHASE_ZH = {"open": "一般訓練", "base": "打底期", "build": "強化期", "peak": "巔峰期",
            "taper": "減量期", "race_week": "比賽週", "post": "賽後恢復"}


def all_races(prefs: dict) -> dict[str, dict]:
    """目標賽事 + 其他比賽 → {日期: 比賽}。"""
    out = {}
    g = prefs["goal"]
    if g.get("type") == "race" and g.get("event_date"):
        out[g["event_date"]] = {"name": g.get("name") or "目標賽事", "date": g["event_date"],
                                "distance_km": g.get("event_distance_km"), "target_time": g.get("target_time"), "main": True}
    for r in prefs.get("races") or []:
        if r.get("date") and r["date"] not in out:
            out[r["date"]] = {**r, "main": False}
    return out


def recovery_days_after(km) -> int:
    km = float(km or 10)
    return 1 if km < 10 else 2 if km < 21 else 4 if km < 42 else 6


def step(kind, minutes, pace=None, note="", spread=0.04):
    s = {"type": kind, "minutes": round(minutes, 1), "note": note}
    if pace:
        s["pace_lo"] = round(pace * (1 + spread))   # 慢
        s["pace_hi"] = round(pace * (1 - spread))   # 快
    return s


def repeat(n, *steps):
    return {"type": "repeat", "n": n, "steps": list(steps)}


def steps_minutes(steps):
    total = 0
    for s in steps:
        total += s["n"] * steps_minutes(s["steps"]) if s["type"] == "repeat" else s["minutes"]
    return total


# ───────────────────────────── 各種課表 ─────────────────────────────

def build_workout(kind: str, minutes: float, paces: dict, prefs: dict, phase: str, strides: bool = False) -> dict:
    p = paces
    m = max(15, minutes)
    if kind == "rest":
        return {"kind": "rest", "title": "休息日", "intensity": "rest", "minutes": 0, "steps": [],
                "summary": "完全休息或散步、伸展、滾筒放鬆。"}
    if kind == "recovery":
        st = [step("run", m, p["recovery"], "非常輕鬆，能完整講話")]
        return _wrap("recovery", "恢復跑", "easy", st, p, f"{round(m)} 分鐘超輕鬆跑，目的是讓腿動一動、促進恢復。")
    if kind == "easy":
        if strides and m >= 30:
            st = [step("run", m - 6, p["easy"], "輕鬆有氧"),
                  repeat(6, step("interval", 20 / 60, p["rep"], "加速跑：放鬆地快", 0.03), step("recovery", 40 / 60, None, "走路或慢跑"))]
            return _wrap("easy", "輕鬆跑＋加速跑", "easy", st, p, f"{round(m - 6)} 分鐘輕鬆跑，最後 6 × 20 秒加速跑，練跑姿與腿部反應。")
        st = [step("run", m, p["easy"], "輕鬆有氧，鼻子能呼吸的程度")]
        return _wrap("easy", "輕鬆跑", "easy", st, p, f"{round(m)} 分鐘輕鬆跑。")
    if kind == "long":
        rp = race_pace(prefs, p)
        km = prefs["goal"].get("event_distance_km") or 0
        if phase in ("build", "peak") and km and float(km) >= 15 and m >= 70:
            fast = min(25, m * 0.25)
            st = [step("run", m - fast, p["long"], "輕鬆長跑"), step("interval", fast, rp, "最後一段跑比賽配速")]
            return _wrap("long", "長距離（後段比賽配速）", "moderate", st, p,
                         f"{round(m)} 分鐘長跑，最後 {round(fast)} 分鐘提到比賽配速 {fmt_pace(rp)}/km。")
        st = [step("run", m, p["long"], "穩定、舒服，可補給練習")]
        return _wrap("long", "長距離慢跑", "moderate", st, p, f"{round(m)} 分鐘長跑，重點是時間不是速度。")

    wu, cd = (12, 10) if m >= 45 else (10, 8)
    main = m - wu - cd
    if kind == "fartlek":
        n = max(4, min(10, int(main // 4)))
        st = [step("warmup", wu, p["easy"]),
              repeat(n, step("interval", 2, p["interval"], "快但可控"), step("recovery", 2, p["recovery"], "慢跑")),
              step("cooldown", cd, p["easy"])]
        return _wrap(kind, "變速跑", "hard", st, p, f"{n} × (2 分快 / 2 分慢)，用身體感覺玩速度。")
    if kind == "tempo":
        block = max(15, min(40, main))
        if block >= 30:
            half = round((block - 6) / 2)
            st = [step("warmup", wu, p["easy"]),
                  repeat(2, step("interval", half, p["tempo"], "舒服的辛苦"), step("recovery", 3, p["recovery"])),
                  step("cooldown", cd, p["easy"])]
            desc = f"2 × {half} 分鐘節奏跑 @ {fmt_pace(p['tempo'])}/km，中間慢跑 3 分。"
        else:
            st = [step("warmup", wu, p["easy"]), step("interval", block, p["tempo"], "舒服的辛苦"), step("cooldown", cd, p["easy"])]
            desc = f"連續 {round(block)} 分鐘節奏跑 @ {fmt_pace(p['tempo'])}/km。"
        return _wrap(kind, "節奏跑", "hard", st, p, desc)
    if kind == "intervals":
        n = max(3, min(8, int(main // 5)))
        st = [step("warmup", wu + 3, p["easy"]),
              repeat(n, step("interval", 3, p["interval"], "辛苦但每趟配速一致", 0.03), step("recovery", 2, p["recovery"], "慢跑")),
              step("cooldown", cd, p["easy"])]
        return _wrap(kind, "間歇跑", "hard", st, p, f"{n} × 3 分鐘 @ {fmt_pace(p['interval'])}/km，間休慢跑 2 分。")
    if kind == "hills":
        n = max(5, min(12, int(main // 2.5)))
        st = [step("warmup", wu + 3, p["easy"]),
              repeat(n, step("interval", 1, None, "上坡用力推，保持跑姿"), step("recovery", 1.5, None, "慢跑下坡")),
              step("cooldown", cd, p["easy"])]
        return _wrap(kind, "坡道衝刺", "hard", st, p, f"找一段緩坡，{n} × 60 秒上坡，慢跑下坡恢復。")
    if kind == "progression":
        third = m / 3
        st = [step("run", third, p["easy"], "輕鬆"), step("run", third, p["marathon"], "穩定"),
              step("run", third, p["tempo"], "最後一段帶快")]
        return _wrap(kind, "漸進跑", "moderate", st, p, f"{round(m)} 分鐘，分三段由慢到快。")
    if kind == "race_pace":
        rp = race_pace(prefs, p)
        n = max(2, min(5, int(main // 10)))
        st = [step("warmup", wu + 3, p["easy"]),
              repeat(n, step("interval", 8, rp, "比賽配速，練節奏感", 0.025), step("recovery", 2, p["recovery"])),
              step("cooldown", cd, p["easy"])]
        return _wrap(kind, "比賽配速跑", "hard", st, p, f"{n} × 8 分鐘 @ 目標配速 {fmt_pace(rp)}/km。")
    if kind == "race":
        g = prefs["goal"]
        return {"kind": "race", "title": f"🏁 {g.get('name') or '比賽日'}", "intensity": "race", "minutes": 0,
                "steps": [], "summary": f"比賽日！目標 {g.get('target_time') or '享受過程'}，前段別衝太快。"}
    raise ValueError(kind)


def _wrap(kind, title, intensity, steps, paces, summary):
    mins = steps_minutes(steps)
    km = _est_km(steps, paces)
    return {"kind": kind, "title": title, "intensity": intensity, "minutes": round(mins),
            "distance_km": round(km, 1), "steps": steps, "summary": summary}


def _est_km(steps, paces):
    km = 0.0
    for s in steps:
        if s["type"] == "repeat":
            km += s["n"] * _est_km(s["steps"], paces)
        else:
            pace = (s.get("pace_lo", 0) + s.get("pace_hi", 0)) / 2 or paces["easy"] * 1.15
            km += s["minutes"] * 60 / pace
    return km


# ───────────────────────────── 主流程 ─────────────────────────────

STAGE_ZH = {"foundation": "打底期", "aerobic": "有氧基礎已建立", "developed": "基礎穩固"}
STAGE_FOCUS = {
    "foundation": "強度課只用變速跑、坡道衝刺輕輕碰速度",
    "aerobic": "改以節奏跑（乳酸閾值）為主，變速跑、坡道為輔",
    "developed": "節奏跑＋間歇跑為主軸，量夠時一週兩堂強度課",
}


def base_weeks(weekly_recent_first: list[float], floor: float = 90) -> int:
    """連續穩定訓練的週數（最近一週在前）。容許中間偶爾一週沒跑滿（減量週、出差），連兩週沒跑滿才中斷。"""
    n = miss = 0
    for w in weekly_recent_first:
        if w >= floor:
            n += 1
            miss = 0
        else:
            miss += 1
            if miss >= 2:
                break
    return n


def training_stage(base_wk: int, novice: bool = False) -> str:
    if novice or base_wk < 4:
        return "foundation"
    return "aerobic" if base_wk < 8 else "developed"


def quality_slots(prefs: dict, phase: str, stage: str) -> tuple[list[str], list[str]]:
    """強度課分兩類：A = 閾值類（節奏跑、比賽配速），B = 速度類（間歇、變速、坡道）。
    依「訓練階段（有氧基礎）× 比賽週期 × 目標」決定各類可以排哪些課。"""
    gt = prefs["goal"].get("type", "fun")
    if phase in ("taper", "race_week"):
        A, B = (["race_pace"] if gt == "race" else ["tempo"]), ["fartlek"]
    elif phase == "post":
        A, B = ["fartlek"], ["fartlek"]
    elif stage == "foundation":
        if phase in ("build", "peak"):        # 起步晚、離比賽又近：只放溫和版本
            A, B = ["tempo"], ["fartlek"]
        else:
            A, B = ["fartlek"], ["hills", "fartlek"]
    elif phase == "peak":
        A, B = ["race_pace", "tempo"], ["intervals"]
    elif phase == "build":
        A, B = ["tempo", "race_pace"], ["intervals"]
    elif stage == "aerobic":
        A = ["tempo"]
        B = ["fartlek", "hills"]
    else:  # developed, open / base 期
        A = ["tempo"]
        B = {"speed": ["intervals"], "fun": ["intervals", "fartlek"],
             "base": ["fartlek", "hills"], "race": ["intervals", "hills"]}.get(gt, ["intervals"])
    liked = {k for k, v in prefs["workouts"].items() if v and k != "strides"}
    A2, B2 = [k for k in A if k in liked], [k for k in B if k in liked]
    if not A2 and not B2:
        fallback = [k for k in ("tempo", "intervals", "fartlek", "hills", "progression") if k in liked] or ["fartlek"]
        return fallback, fallback
    return A2 or B2, B2 or A2


def quality_menu(prefs: dict, phase: str, stage: str = "aerobic") -> list[str]:
    A, B = quality_slots(prefs, phase, stage)
    return list(dict.fromkeys(A + B))


def _lru(options: list[str], recent_kinds: list[str]) -> str:
    """選最久沒做的那一種。"""
    rev = recent_kinds[::-1]
    return max(options, key=lambda k: rev.index(k) if k in rev else 999)


def week_target(prefs, recent_weeks: list[float], phase: str, phase_days, rel_tsb: float, today: date) -> tuple[float, list[str]]:
    notes = []
    if prefs.get("fixed_weekly_minutes"):
        return float(prefs["fixed_weekly_minutes"]), ["使用你指定的週跑量"]
    nonzero = sorted([w for w in recent_weeks if w > 0], reverse=True)
    base = sum(nonzero[:3]) / len(nonzero[:3]) if nonzero else 0
    base = max(base, float(prefs.get("min_week_minutes") or 60))
    factor = {"gentle": 1.03, "normal": 1.07, "bold": 1.10}.get(prefs.get("progression"), 1.07)
    target = min(base * factor, base + 45)
    notes.append(f"近期週跑量約 {round(base)} 分鐘，本週目標 +{round((target / base - 1) * 100)}%")

    de = int(prefs.get("deload_every") or 0)
    if de and phase in ("open", "base", "build") and today.isocalendar().week % de == 0:
        target = base * 0.75
        notes.append(f"每 {de} 週一次減量週，讓身體吸收訓練")
    if phase == "taper":
        target = base * 0.7
        notes.append("比賽前減量期")
    if phase == "race_week":
        target = base * 0.5
        notes.append("比賽週，保持腿感就好")
    if phase == "post":
        target = base * 0.6
        notes.append("賽後恢復")
    if rel_tsb < -0.35:
        target *= 0.85
        notes.append("近期疲勞累積偏高，本週量降 15%")
    return target, notes


def make_plan(*, today: date, prefs: dict, paces: dict, recent_weeks: list[float], rel_tsb: float,
              ready: dict, checkins: dict[str, dict], done_dates: set[str], recent_kinds: list[str],
              done_minutes: float = 0, overrides: dict[str, dict] | None = None, novice: bool = False,
              base_wk: int | None = None) -> dict:
    prefs = copy.deepcopy(prefs)
    if base_wk is None:
        base_wk = base_weeks(recent_weeks)
    stage = training_stage(base_wk, novice)
    phase, phase_days = phase_for(prefs, today)
    target, notes = week_target(prefs, recent_weeks, phase, phase_days, rel_tsb, today)

    horizon = [today + timedelta(days=i) for i in range(7)]
    avail_wd = set(int(d) for d in prefs["days"])
    races = all_races(prefs)
    manual = prefs.get("plan_mode") == "manual" and any(prefs.get("template", {}).values())
    race_in_week = [r for ds, r in races.items() if today.isoformat() <= ds <= horizon[-1].isoformat()]
    if race_in_week and phase not in ("taper", "race_week") and not prefs.get("fixed_weekly_minutes") and not manual:
        target *= 0.8
        notes.append(f"這週有比賽「{race_in_week[0]['name']}」，跑量降 20%，賽前賽後自動放輕鬆")

    run_days = [d for d in horizon
                if d.weekday() in avail_wd
                and not checkins.get(d.isoformat(), {}).get("skip")
                and d.isoformat() not in races]
    # 今天已經跑過：今天不再排課，跑量也扣掉
    if today.isoformat() in done_dates and today in run_days:
        run_days.remove(today)

    long_day = next((d for d in run_days if d.weekday() == int(prefs["long_run_day"])), None)
    if phase == "race_week":
        long_day = None
    if long_day is None and len(run_days) >= 3 and phase != "race_week":
        long_day = run_days[-1]
    if novice and target < 120:
        long_day = None   # 跑量還少時不特別排「長跑」，每次差不多長度比較好養成習慣

    # 強度課數量
    inten = int(prefs.get("intensity", 50))
    gt = prefs["goal"].get("type")
    n_q = 0 if inten < 20 else 1 if inten < 60 else 2
    if (stage == "developed" and inten >= 40 and gt != "base" and len(run_days) >= 5
            and phase in ("open", "base", "build", "peak")):
        n_q = 2    # 基礎穩固、一週跑 5 天以上：閾值課＋速度課各一堂（跑 4 天的話把強度拉到「偏多」也會排兩堂）
    if stage == "foundation":
        n_q = min(n_q, 1)
    if gt == "base" or phase in ("taper", "race_week", "post"):
        n_q = min(n_q, 1)
    n_q = max(0, min(n_q, len(run_days) - (2 if long_day else 1)))
    if novice:
        n_q = 0
        notes.append("你才剛開始累積紀錄：先用輕鬆跑養成習慣，規律跑滿 3 週、累積 6 次以上後，才會開始排強度課")
    elif n_q and not manual:
        notes.append(f"訓練階段：{STAGE_ZH[stage]}（已穩定訓練 {base_wk} 週）→ {STAGE_FOCUS[stage]}")
    min_easy = 20 if novice else 25

    # 強度課盡量不跟長跑、彼此相鄰
    candidates = [d for d in run_days if d != long_day]
    q_days: list[date] = []
    for _ in range(n_q):
        best, best_score = None, -1
        for d in candidates:
            if d in q_days:
                continue
            taken = q_days + ([long_day] if long_day else [])
            gap = min([abs((d - t).days) for t in taken] or [7])
            if gap > best_score:
                best, best_score = d, gap
        if best and best_score >= 2:
            q_days.append(best)

    slot_a, slot_b = quality_slots(prefs, phase, stage)
    q_kinds: list[str] = []
    if n_q >= 2:
        q_kinds = [_lru(slot_b, recent_kinds), _lru(slot_a, recent_kinds)]   # 速度課排前面（腿比較新鮮），閾值課在後
    elif n_q == 1:
        # 一週只有一堂：閾值類、速度類輪流；上一堂是閾值課，這週就換速度課
        last_q = next((k for k in reversed(recent_kinds) if k in WORKOUT_LABELS and k != "strides"), None)
        use_b = last_q in slot_a and last_q not in slot_b
        q_kinds = [_lru(slot_b if use_b else slot_a, recent_kinds)]

    max_wd = float(prefs["max_minutes_weekday"])
    sessions: dict[date, tuple[str, float]] = {}
    remaining = target - done_minutes  # 今天已經跑掉的量算進這週
    if long_day:
        share = 0.45 if len(run_days) <= 2 else 0.32
        lm = min(float(prefs["max_long_minutes"]), max(45, target * share))
        sessions[long_day] = ("long", lm)
        remaining -= lm
    for i, d in enumerate(sorted(q_days)):
        qm = min(max_wd, max(35, target * 0.18))
        sessions[d] = (q_kinds[i % len(q_kinds)], qm)
        remaining -= qm
    easy_days = [d for d in run_days if d not in sessions]
    while easy_days and remaining / len(easy_days) < min_easy:
        # 量不夠分：減少輕鬆跑天數，與其每天跑 15 分不如休息
        easy_days.pop(len(easy_days) // 2)
    for d in easy_days:
        sessions[d] = ("easy", min(max_wd, max(min_easy, remaining / len(easy_days))))

    if manual:
        # 照使用者自己排的每週課表，不做自動分配
        sessions = {}
        for d in horizon:
            t = prefs["template"].get(str(d.weekday()))
            if not t or t.get("kind") in (None, "rest") or d.isoformat() in races:
                continue
            if checkins.get(d.isoformat(), {}).get("skip") or (d == today and today.isoformat() in done_dates):
                continue
            sessions[d] = (t["kind"], float(t.get("minutes") or 40))
        target = sum(float(t.get("minutes") or 40) for t in prefs["template"].values() if t and t.get("kind") not in (None, "rest"))
        notes = ["使用你自己安排的每週課表"]
        if ready["score"] < 40 and today in sessions and sessions[today][0] not in ("easy", "recovery"):
            notes.append(f"恢復分數 {ready['score']}，今天的課可能太吃力，可以在分析頁改成輕鬆跑")

    # 比賽前後自動放輕鬆
    if not manual:
        for ds, r in races.items():
            rd = date.fromisoformat(ds)
            for d in list(sessions):
                gap = (rd - d).days
                kind, mins = sessions[d]
                if gap == 1:
                    sessions[d] = ("recovery", 20)          # 賽前一天：短短動一下
                elif 1 < gap <= 3 and (kind in WORKOUT_LABELS or kind == "long"):
                    sessions[d] = ("easy", min(mins, 40))  # 賽前三天不排強度、不排長跑
                elif -recovery_days_after(r.get("distance_km")) <= gap < 0:
                    if gap == -1:
                        del sessions[d]                      # 賽後隔天休息
                    else:
                        sessions[d] = ("recovery", min(mins, 30))

    # 依今天的恢復分數調整「今天」與「明天」
    adjust_note = None
    score = ready["score"]
    for d in ((today, today + timedelta(days=1)) if not manual else ()):
        if d not in sessions:
            continue
        kind, mins = sessions[d]
        if d != today:
            # 明天：只有狀態很差時才先降一級，之後每天同步會再重算
            if score < 30 and kind in WORKOUT_LABELS:
                sessions[d] = ("easy", mins)
            continue
        if score < 40:
            if kind in WORKOUT_LABELS or kind == "long":
                sessions[d] = ("recovery", min(30, mins * 0.5))
                adjust_note = f"恢復分數 {score}，今天改成恢復跑，原本的課往後延"
                # 把強度課挪到下一個輕鬆跑日
                nxt = next((x for x in sorted(sessions) if x > d + timedelta(days=1) and sessions[x][0] == "easy"), None)
                if nxt and kind != "long":
                    sessions[nxt] = (kind, sessions[nxt][1])
            elif score < 25:
                del sessions[d]
                adjust_note = f"恢復分數 {score}，今天建議休息"
            else:
                sessions[d] = ("recovery", min(mins, 30))
                adjust_note = f"恢復分數 {score}，今天輕鬆就好"
        elif score < 65:
            if kind in ("intervals", "race_pace"):
                sessions[d] = ("fartlek", mins * 0.85)
                adjust_note = f"恢復分數 {score}（普通），間歇改成較溫和的法特雷克"
            elif kind == "tempo":
                sessions[d] = ("progression", mins * 0.9)
                adjust_note = f"恢復分數 {score}（普通），節奏跑改成漸進跑"
            elif kind == "long":
                sessions[d] = ("long", mins * 0.85)
                adjust_note = f"恢復分數 {score}（普通），長跑縮短 15%"

    # 使用者手動調整過的日子：以使用者為準（在恢復狀態調整之後套用）
    overrides = overrides or {}
    for d in horizon:
        ov = overrides.get(d.isoformat())
        if not ov:
            continue
        if ov["kind"] == "rest":
            sessions.pop(d, None)
        else:
            default = {"long": min(float(prefs["max_long_minutes"]), max(60, target * 0.3)),
                       "recovery": 30, "easy": 40}.get(ov["kind"], min(max_wd, 45))
            sessions[d] = (ov["kind"], float(ov.get("minutes") or default))

    strides_left = 2 if prefs.get("add_strides", True) and prefs["workouts"].get("strides", True) else 0
    days_out = []
    for d in horizon:
        ds = d.isoformat()
        base = {"date": ds, "weekday": f"週{WEEKDAY_ZH[d.weekday()]}"}
        if ds in races:
            r = races[ds]
            w = {"kind": "race", "title": f"🏁 {r['name']}", "intensity": "race", "minutes": 0, "steps": [],
                 "summary": (f"{r['distance_km']} 公里，" if r.get("distance_km") else "")
                 + f"目標 {r.get('target_time') or '享受過程'}。前段別衝太快。"}
        elif ds in done_dates and d == today:
            w = {"kind": "done", "title": "今天已完成 ✅", "intensity": "done", "minutes": 0, "steps": [],
                 "summary": "已同步到今天的跑步紀錄，好好恢復。"}
        elif d in sessions:
            kind, mins = sessions[d]
            use_strides = kind == "easy" and strides_left > 0 and d.weekday() != (int(prefs["long_run_day"]) - 1) % 7
            if use_strides:
                strides_left -= 1
            w = build_workout(kind, mins, paces, prefs, phase, strides=use_strides)
        else:
            w = build_workout("rest", 0, paces, prefs, phase)
        w["time"] = prefs.get("day_times", {}).get(str(d.weekday())) or prefs.get("run_time", "06:30")
        if ds in overrides:
            w["override"] = True
        days_out.append({**base, **w})

    planned = sum(x["minutes"] for x in days_out) + done_minutes
    return {
        "generated_for": today.isoformat(),
        "phase": phase,
        "stage": stage, "stage_label": STAGE_ZH[stage], "base_weeks": base_wk,
        "phase_label": PHASE_ZH[phase],
        "days_to_event": phase_days,
        "target_minutes": round(target),
        "planned_minutes": round(planned),
        "notes": notes + ([adjust_note] if adjust_note else []),
        "days": days_out,
    }


def describe_steps(steps, depth=0) -> list[str]:
    """把結構化步驟轉成人看得懂的文字（行事曆描述用）。"""
    names = {"warmup": "熱身", "cooldown": "緩和", "interval": "主課", "recovery": "恢復", "run": "跑"}
    out = []
    for s in steps:
        pad = "  " * depth
        if s["type"] == "repeat":
            out.append(f"{pad}重複 {s['n']} 次：")
            out += describe_steps(s["steps"], depth + 1)
        else:
            mins = s["minutes"]
            t = f"{int(mins)} 分" if mins >= 1 else f"{int(round(mins * 60))} 秒"
            pace = f" @ {fmt_pace(s['pace_hi'])}–{fmt_pace(s['pace_lo'])}/km" if s.get("pace_lo") else ""
            note = f"（{s['note']}）" if s.get("note") else ""
            out.append(f"{pad}• {names.get(s['type'], s['type'])} {t}{pace}{note}")
    return out
