"""每日晨報與晚報。

晨報：以今天早上的安靜心率為主，搭配 HRV、睡眠、Body Battery、近 7 天活動量，判斷今天要照課表、減量還是休息。
晚報：今天的身體狀況（步數、熱量、壓力、電量）、今天的運動（含其他運動）、明天的課。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from statistics import mean

from . import db, metrics, views
from .config import TZ
from .garmin_client import today_local
from .planner import WORKOUT_LABELS

log = logging.getLogger("ic8.briefs")

HARD = set(WORKOUT_LABELS) - {"strides"}


def _avg(vals, need=3):
    vals = [v for v in vals if v is not None]
    return mean(vals) if len(vals) >= need else None


def _plan(user_id: int, ds: str) -> dict | None:
    r = db.q1("SELECT data FROM plan WHERE user_id=? AND date=?", (user_id, ds))
    return json.loads(r["data"]) if r else None


def _day_acts(user_id: int, ds: str) -> list[dict]:
    runs = db.q("SELECT * FROM activities WHERE user_id=? AND date=?", (user_id, ds))
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date=?", (user_id, ds))
    out = [{"name": a.get("name") or "跑步", "label": "跑步", "group": "run", "minutes": round((a.get("duration_s") or 0) / 60),
            "km": round((a.get("distance_m") or 0) / 1000, 1), "avg_hr": a.get("avg_hr") and round(a["avg_hr"]), "_a": a, "_run": True}
           for a in runs]
    out += [{"name": a.get("name") or a.get("sport_label"), "label": a.get("sport_label") or "其他運動", "group": a.get("sport_group") or "other",
             "minutes": round((a.get("duration_s") or 0) / 60), "km": round((a.get("distance_m") or 0) / 1000, 1) or None,
             "avg_hr": a.get("avg_hr") and round(a["avg_hr"]), "_a": a, "_run": False} for a in cross]
    return out


def _loads(user_id: int, today: date):
    """每天的總負荷（跑步＋其他運動），以及計算用的靜止／最大心率。"""
    prefs = db.get_prefs(user_id)
    start = today - timedelta(days=60)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=?", (user_id, start.isoformat()))
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=?", (user_id, start.isoformat()))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? ORDER BY date", (user_id, start.isoformat()))
    loads = metrics.daily_loads(acts, daily, prefs, start, today, cross)
    rest, mx = metrics.estimate_rest_hr(daily), metrics.estimate_max_hr(acts, prefs)
    return loads, rest, mx, prefs


def _act_load(x, rest, mx, sex):
    a = x["_a"]
    return metrics.trimp(a.get("duration_s"), a.get("avg_hr"), rest, mx, sex) if x["_run"] else metrics.cross_trimp(a, rest, mx, sex)


def _ratio(loads: dict, today: date, include_today: bool):
    keys = sorted(k for k in loads if k <= today.isoformat())
    if not include_today:
        keys = [k for k in keys if k < today.isoformat()]
    if len(keys) < 35:
        return None
    last7 = sum(loads[k] for k in keys[-7:])
    prev28 = sum(loads[k] for k in keys[-35:-7]) / 4
    return round(last7 / prev28, 2) if prev28 > 0 else None


def _clean(acts):
    return [{k: v for k, v in x.items() if not k.startswith("_")} for x in acts]


# ───────────────────────────── 晨報 ─────────────────────────────

def morning(user_id: int, today: date | None = None) -> dict:
    today = today or today_local()
    ds, yd = today.isoformat(), (today - timedelta(days=1)).isoformat()
    rows = db.q("SELECT * FROM daily WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date",
                (user_id, (today - timedelta(days=35)).isoformat(), ds))
    by = {r["date"]: r for r in rows}
    t, y = by.get(ds) or {}, by.get(yd) or {}
    hist = [r for r in rows if r["date"] < ds]
    h7 = hist[-7:]

    points, reasons, metrics_rows = 0, [], []

    def row(key, label, unit, today_v, yest_v, base_v, verdict, text):
        metrics_rows.append({"key": key, "label": label, "unit": unit, "today": today_v, "yesterday": yest_v,
                             "baseline": None if base_v is None else round(base_v, 1), "verdict": verdict, "text": text})

    # 安靜心率：今天 vs 昨天 vs 前 7 天平均
    rhr_base = _avg([r.get("rhr") for r in h7])
    rhr = t.get("rhr")
    if rhr:
        dv = rhr - rhr_base if rhr_base else 0
        dy = rhr - y["rhr"] if y.get("rhr") else 0
        if rhr_base and dv >= 7:
            points += 2
            v, txt = "bad", f"比平常高 {round(dv)} 下。常見原因是還沒從訓練恢復、睡不好、喝酒或快感冒。"
        elif rhr_base and dv >= 4:
            points += 1
            v, txt = "warn", f"比平常高 {round(dv)} 下，身體有點疲勞。"
        elif rhr_base and dv <= -3:
            v, txt = "good", f"比平常低 {round(-dv)} 下，恢復得不錯。"
        else:
            v, txt = "ok", "在平常的範圍內。"
        if dy >= 5 and not (rhr_base and dv >= 7):
            points += 1
            txt += f" 而且比昨天一下子高了 {round(dy)} 下。"
            v = "warn" if v in ("ok", "good") else v
        row("rhr", "安靜心率", "bpm", rhr, y.get("rhr"), rhr_base, v, txt)
        reasons.append(f"安靜心率 {round(rhr)}（平常 {round(rhr_base)}）" if rhr_base else f"安靜心率 {round(rhr)}")
    else:
        row("rhr", "安靜心率", "bpm", None, y.get("rhr"), rhr_base, "none",
            "今天的安靜心率還沒同步。手錶要先同步到手機的 Garmin Connect，這裡才抓得到。")

    # HRV
    hrv_base = _avg([r.get("hrv_avg") for r in h7])
    if t.get("hrv_avg") and hrv_base:
        drop = (hrv_base - t["hrv_avg"]) / hrv_base
        if drop > 0.25:
            points += 2
            v, txt = "bad", f"比平常低 {round(drop * 100)}%，自律神經還在疲勞狀態。"
        elif drop > 0.12:
            points += 1
            v, txt = "warn", f"比平常低 {round(drop * 100)}%。"
        elif drop < -0.08:
            v, txt = "good", "比平常高，恢復得不錯。"
        else:
            v, txt = "ok", "在平常的範圍內。"
        row("hrv", "昨晚 HRV", "ms", t["hrv_avg"], y.get("hrv_avg"), hrv_base, v, txt)
    elif t.get("hrv_status") in ("LOW", "POOR"):
        points += 1
        row("hrv", "HRV 狀態", "", t["hrv_status"], y.get("hrv_status"), None, "warn", "Garmin 判定 HRV 偏低。")

    # 睡眠
    sc, hrs = t.get("sleep_score"), t.get("sleep_hours")
    if sc or hrs:
        bad = (sc is not None and sc < 40) or (hrs is not None and hrs < 4.5)
        warn = (sc is not None and sc < 55) or (hrs is not None and hrs < 5.5)
        points += 2 if bad else 1 if warn else 0
        v = "bad" if bad else "warn" if warn else "good" if (sc or 0) >= 80 else "ok"
        txt = {"bad": "睡得很少或很差，今天別勉強。", "warn": "睡得不太夠。", "good": "睡得很好。", "ok": "睡眠普通。"}[v]
        row("sleep", "睡眠", "分" if sc else "小時", sc if sc else hrs, y.get("sleep_score") if sc else y.get("sleep_hours"),
            _avg([r.get("sleep_score") if sc else r.get("sleep_hours") for r in h7]), v,
            txt + (f"（{hrs} 小時）" if sc and hrs else ""))

    # Body Battery（早上的電量）
    bb = t.get("bb_now") if t.get("bb_now") is not None else t.get("body_battery")
    if bb is not None:
        if bb < 30:
            points += 2
            v, txt = "bad", "電量很低，睡眠沒有把電充回來。"
        elif bb < 50:
            points += 1
            v, txt = "warn", "電量偏低。"
        else:
            v, txt = ("good" if bb >= 75 else "ok"), ("電量充足。" if bb >= 75 else "電量普通。")
        row("bb", "Body Battery", "", bb, y.get("bb_now") or y.get("body_battery"), None, v, txt)

    # 昨天的運動與近 7 天活動量
    loads, rest, mx, prefs = _loads(user_id, today)
    sex = prefs.get("sex", "M")
    yacts = _day_acts(user_id, yd)
    for x in yacts:
        x["load"] = round(_act_load(x, rest, mx, sex))
    ratio = _ratio(loads, today, include_today=False)
    vol = views.load_verdict(ratio)
    if ratio and ratio > 1.5:
        points += 1
    hist_loads = [loads[k] for k in sorted(loads) if k < ds][-28:]
    nonzero = [v for v in hist_loads if v > 0]
    y_load = sum(x["load"] for x in yacts)
    big_day = bool(nonzero) and y_load > 1.8 * mean(nonzero) and y_load > 80

    have = sum(1 for r in metrics_rows if r["verdict"] != "none")
    level = "go" if points <= 1 else "reduce" if points <= 4 else "rest"

    plan = _plan(user_id, ds)
    run_today = plan and plan.get("kind") not in (None, "rest", "done")
    suggestion = None
    if plan and plan.get("kind") == "done":
        advice = "今天已經跑過了，好好恢復。"
    elif not run_today:
        advice = "今天本來就是休息日。" + ("身體有點累，連散步都放輕鬆就好。" if level != "go" else "想動的話散步或伸展就好。")
    else:
        k, m, title = plan["kind"], plan.get("minutes") or 40, plan.get("title") or "跑步"
        if level == "go":
            advice = f"照課表：{title}（{m} 分）。"
        elif level == "reduce":
            if k in HARD or k == "race_pace":
                suggestion = {"kind": "easy", "minutes": max(20, round(m * 0.8)), "title": "輕鬆跑"}
                advice = f"把「{title}」改成 {suggestion['minutes']} 分鐘輕鬆跑，強度課等身體回來再補。"
            elif k == "long":
                suggestion = {"kind": "long", "minutes": max(30, round(m * 0.7)), "title": "縮短的長跑"}
                advice = f"長跑縮短到 {suggestion['minutes']} 分鐘，配速放慢。"
            else:
                suggestion = {"kind": "easy" if k != "recovery" else "recovery", "minutes": max(20, round(m * 0.8)), "title": "縮短的輕鬆跑"}
                advice = f"輕鬆跑縮短到 {suggestion['minutes']} 分鐘。"
        else:
            if points >= 7:
                suggestion = {"kind": "rest", "minutes": None, "title": "休息"}
                advice = f"今天休息，「{title}」先不跑。"
            else:
                suggestion = {"kind": "recovery", "minutes": 20, "title": "恢復跑"}
                advice = f"「{title}」改成 20 分鐘超輕鬆恢復跑，或直接休息。"

    headline = {"go": "狀態不錯，可以照課表練", "reduce": "有點疲勞，今天建議減量", "rest": "身體需要恢復，今天建議休息"}[level]
    if have < 2:
        headline = "資料還不夠，先參考就好"
    notes = []
    if big_day:
        notes.append(f"昨天的運動量是平常的 {round(y_load / mean(nonzero), 1)} 倍。")
    if vol:
        notes.append(f"近 7 天活動量{vol['label']}（平常的 {round(ratio * 100)}%）。")
    if not rhr:
        notes.append("同步到安靜心率後，晨報會自動更新。")

    return {"kind": "morning", "date": ds, "level": level, "headline": headline, "advice": advice, "suggestion": suggestion,
            "points": points, "metrics": metrics_rows, "yesterday_acts": _clean(yacts), "volume": vol, "ratio": ratio,
            "notes": notes, "plan": plan and {k: plan.get(k) for k in ("kind", "title", "minutes", "intensity")},
            "has_rhr": bool(rhr), "generated_at": datetime.now(TZ).isoformat(timespec="minutes")}


# ───────────────────────────── 晚報 ─────────────────────────────

def evening(user_id: int, today: date | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    today = today or now.date()
    ds = today.isoformat()
    rows = db.q("SELECT * FROM daily WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date",
                (user_id, (today - timedelta(days=28)).isoformat(), ds))
    by = {r["date"]: r for r in rows}
    t = by.get(ds) or {}
    hist = [r for r in rows if r["date"] < ds]
    loads, rest, mx, prefs = _loads(user_id, today)
    sex = prefs.get("sex", "M")

    acts = _day_acts(user_id, ds)
    for x in acts:
        x["load"] = round(_act_load(x, rest, mx, sex))
    day_load = sum(x["load"] for x in acts)
    nonzero = [loads[k] for k in sorted(loads) if k < ds and loads[k] > 0][-28:]
    typical = mean(nonzero) if nonzero else None

    items = []

    def item(key, label, value, unit, verdict, text):
        items.append({"key": key, "label": label, "value": value, "unit": unit, "verdict": verdict, "text": text})

    steps, goal = t.get("steps"), t.get("steps_goal") or prefs.get("step_goal") or 8000
    if steps is not None:
        pct = steps / goal if goal else 0
        item("steps", "步數", round(steps), "步", "good" if pct >= 1 else "ok" if pct >= 0.6 else "warn",
             f"目標 {round(goal)} 步，完成 {round(pct * 100)}%。")
    if t.get("active_kcal") is not None:
        item("kcal", "活動熱量", round(t["active_kcal"]), "kcal", "ok", f"平常約 {round(_avg([r.get('active_kcal') for r in hist]) or 0)} kcal。")
    if t.get("stress_avg") is not None:
        s = t["stress_avg"]
        item("stress", "平均壓力", round(s), "", "bad" if s >= 50 else "warn" if s >= 38 else "good",
             "壓力偏高，睡前放鬆一下。" if s >= 50 else "壓力中等。" if s >= 38 else "壓力不高。")
    bb_now = t.get("bb_now")
    if bb_now is not None:
        item("bb", "目前電量", round(bb_now), "", "bad" if bb_now < 25 else "warn" if bb_now < 40 else "good",
             f"今天最高 {round(t['body_battery'])}、最低 {round(t['bb_low'])}。" if t.get("body_battery") and t.get("bb_low") is not None else "")
    elif t.get("body_battery") is not None:
        item("bb", "今天最高電量", round(t["body_battery"]), "", "ok", "")

    if day_load:
        rel = day_load / typical if typical else None
        v = "bad" if rel and rel > 2 else "warn" if rel and rel > 1.4 else "ok"
        item("load", "今天運動負荷", day_load, "", v,
             (f"是你運動日平均的 {round(rel * 100)}%。" if rel else "") + ("今天練得比較重，晚餐補足碳水和蛋白質。" if v != "ok" else ""))

    ratio = _ratio(loads, today, include_today=True)
    vol = views.load_verdict(ratio)

    plan = _plan(user_id, ds)
    ran = any(x["_run"] for x in acts)
    # 晚報時間還沒到：只是「到目前為止」的狀況，不下「今天消耗很大」這種結論
    h, mi = _hm(prefs.get("brief_evening"), "21:30")
    preliminary = today == now.date() and (now.hour, now.minute) < (h, mi)
    plan_txt = None
    if plan and plan.get("kind") not in (None, "rest"):
        if ran or plan.get("kind") == "done":
            plan_txt = f"今天的課「{plan.get('title')}」已完成。"
        elif preliminary:
            plan_txt = f"今天的課「{plan.get('title')}」還沒跑，時間到了再出門就好。"
        else:
            plan_txt = f"今天的課「{plan.get('title')}」沒跑到，課表會自動重排，不用硬補。"
    tomorrow = _plan(user_id, (today + timedelta(days=1)).isoformat())


    tips = []
    heavy_day = bool(typical) and day_load > 1.8 * typical
    tired_today = (bb_now is not None and bb_now < 30) or heavy_day
    week_heavy = bool(vol) and vol["level"] in ("warn", "bad")
    tired = tired_today or week_heavy
    sleep_goal = prefs.get("sleep_goal") or 8
    tips.append(f"今晚目標睡滿 {sleep_goal} 小時" + ("，比平常早 30 分鐘上床。" if tired else "。"))
    if tomorrow and tomorrow.get("kind") in HARD | {"long", "race_pace"}:
        if tired:
            tips.append(f"明天是「{tomorrow.get('title')}」。今天比較累，明早晨報會看安靜心率決定要不要減量。")
        else:
            tips.append(f"明天是「{tomorrow.get('title')}」，今晚吃好睡好，明天就能練得到位。")
    elif tomorrow and tomorrow.get("kind") not in (None, "rest"):
        tips.append(f"明天是「{tomorrow.get('title')}」（{tomorrow.get('minutes')} 分），輕鬆跑就好。")
    else:
        tips.append("明天休息，讓身體吸收這幾天的訓練。")
    if t.get("stress_avg") and t["stress_avg"] >= 50:
        tips.append("今天壓力偏高，睡前少看螢幕、做幾分鐘深呼吸。")

    # 等級只看「今天」：電量和今天的運動量。這週累積偏多另外說，不算成今天的消耗
    level = "bad" if (bb_now is not None and bb_now < 20) or (typical and day_load > 2.5 * typical) else "warn" if tired_today else "good"
    if preliminary:
        headline = f"到 {now.strftime('%H:%M')} 為止" + {"good": "狀況穩定", "warn": "有點累了，下午別太操", "bad": "消耗不少，接下來放輕鬆"}[level]
    else:
        headline = {"good": "今天的身體狀況穩定", "warn": "今天有點累，今晚好好恢復", "bad": "今天消耗很大，今晚優先休息"}[level]
    if week_heavy and level == "good":
        headline += "，但這週累積偏多"

    return {"kind": "evening", "date": ds, "level": level, "headline": headline, "items": items, "acts": _clean(acts),
            "day_load": day_load, "volume": vol, "ratio": ratio, "plan_text": plan_txt,
            "tomorrow": tomorrow and {k: tomorrow.get(k) for k in ("kind", "title", "minutes", "intensity")},
            "tips": tips, "preliminary": preliminary, "evening_time": f"{h:02d}:{mi:02d}",
            "generated_at": datetime.now(TZ).isoformat(timespec="minutes")}


# ───────────────────────────── 儲存、排程、推播 ─────────────────────────────

BUILDERS = {"morning": morning, "evening": evening}


def build_and_save(user_id: int, kind: str, today: date | None = None, now: datetime | None = None) -> dict:
    data = BUILDERS[kind](user_id, today, now) if kind == "evening" else BUILDERS[kind](user_id, today)
    stamp = (now or datetime.now(TZ)).isoformat(timespec="seconds")
    with db.tx() as c:
        c.execute("INSERT INTO briefs(user_id, date, kind, data, created_at, pushed) VALUES(?,?,?,?,?,0) "
                  "ON CONFLICT(user_id, date, kind) DO UPDATE SET data=excluded.data, created_at=excluded.created_at",
                  (user_id, data["date"], kind, json.dumps(data, ensure_ascii=False), stamp))
    return data


def get(user_id: int, kind: str, ds: str | None = None) -> dict | None:
    r = db.q1("SELECT data FROM briefs WHERE user_id=? AND date=? AND kind=?", (user_id, ds or today_local().isoformat(), kind))
    return json.loads(r["data"]) if r else None


def push_text(b: dict) -> tuple[str, str]:
    if b["kind"] == "morning":
        rhr = next((m for m in b["metrics"] if m["key"] == "rhr"), None)
        head = f"晨報：{b['headline']}"
        body = (f"安靜心率 {round(rhr['today'])}" + (f"（平常 {round(rhr['baseline'])}）" if rhr.get("baseline") else "") + "。"
                if rhr and rhr.get("today") else "") + b["advice"]
        return head, body
    acts = "、".join(f"{a['label']} {a['minutes']} 分" for a in b["acts"]) or "今天沒有運動紀錄"
    return f"晚報：{b['headline']}", f"{acts}。{b['tips'][0]}"


def _hm(s: str, default: str):
    try:
        h, m = (int(x) for x in (s or default).split(":"))
        return h, m
    except ValueError:
        h, m = (int(x) for x in default.split(":"))
        return h, m


def tick(now: datetime | None = None, do_sync=None, do_push=None):
    """排程每 10 分鐘跑一次：時間到了就同步、產生報告、推播。

    晨報要等到抓得到「今天的安靜心率」才推（手錶要先同步到手機）。最多等 3 小時，之後就用現有資料推。
    錯過太久（超過 4 小時）的報告只產生、不推播，避免半夜收到晨報。"""
    from . import push, sync

    now = now or datetime.now(TZ)
    do_sync = do_sync or sync.sync_user
    do_push = do_push or push.send
    ds = now.date().isoformat()
    for u in db.q("SELECT id FROM users WHERE provider IN ('garmin','demo','apple')"):
        uid = u["id"]
        prefs = db.get_prefs(uid)
        if not prefs.get("brief_enabled", True):
            continue
        for kind, key, default in (("morning", "brief_morning", "07:30"), ("evening", "brief_evening", "21:30")):
            h, m = _hm(prefs.get(key), default)
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now < due:
                continue
            row = db.q1("SELECT created_at, pushed FROM briefs WHERE user_id=? AND date=? AND kind=?", (uid, ds, kind))
            if row and row["pushed"]:
                continue
            if row and timedelta(0) <= now - datetime.fromisoformat(row["created_at"]) < timedelta(minutes=25):
                continue   # 剛剛才試過，等下一輪
            try:
                do_sync(uid)
            except Exception as e:
                log.warning("報告前同步失敗 user=%s: %s", uid, e)
            data = build_and_save(uid, kind, now.date(), now)
            late = now - due > timedelta(hours=4)
            waiting = kind == "morning" and not data.get("has_rhr") and now - due < timedelta(hours=3)
            if waiting and not late:
                continue
            if not late:
                from . import game, pet_voice
                title, body = pet_voice.push_text(data, game.compute(uid)["character"])
                try:
                    do_push(uid, title, body, f"/#/brief/{kind}")
                except Exception as e:
                    log.warning("推播失敗 user=%s: %s", uid, e)
            with db.tx() as c:
                c.execute("UPDATE briefs SET pushed=1 WHERE user_id=? AND date=? AND kind=?", (uid, ds, kind))
