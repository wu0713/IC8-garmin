"""跑步狀態分析與建議（全部是可解釋的規則，每條建議都附上「為什麼」）。

指標：
- 有氧效率 EF：每分鐘跑幾公尺 ÷ 平均心率。同樣心率跑得更快 = 有氧能力進步。只用輕鬆跑來比，避免強度課干擾。
- 強度分布：用平均心率佔最大心率的比例分輕鬆 / 中等 / 高強度，對照常見的「80/20」原則。
- 急慢性負荷比 ACWR：近 7 天負荷 ÷ 近 28 天平均週負荷。> 1.5 時受傷風險明顯上升。
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from . import db, metrics
from .garmin_client import today_local
from .metrics import fmt_pace


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def classify(avg_hr, max_hr) -> str:
    if not avg_hr or not max_hr:
        return "unknown"
    p = avg_hr / max_hr
    return "easy" if p < 0.82 else "moderate" if p < 0.88 else "hard"


def ef(a) -> float | None:
    if not (a.get("avg_hr") and a.get("distance_m") and a.get("duration_s")):
        return None
    return (a["distance_m"] / (a["duration_s"] / 60)) / a["avg_hr"]


def analyze(user_id: int) -> dict:
    prefs = db.get_prefs(user_id)
    today = today_local()
    since = today - timedelta(days=7 * 13)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? ORDER BY date", (user_id, since.isoformat()))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? ORDER BY date", (user_id, since.isoformat()))
    max_hr = metrics.estimate_max_hr(acts, prefs)
    rest_hr = metrics.estimate_rest_hr(daily)

    # ── 每週統計（近 12 週）──
    weeks = []
    w0 = _week_start(today)
    for i in range(11, -1, -1):
        ws = w0 - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        wa = [a for a in acts if ws.isoformat() <= a["date"] <= we.isoformat()]
        mins = sum((a.get("duration_s") or 0) for a in wa) / 60
        hard = sum((a.get("duration_s") or 0) for a in wa if classify(a.get("avg_hr"), max_hr) != "easy") / 60
        weeks.append({
            "week_start": ws.isoformat(),
            "km": round(sum((a.get("distance_m") or 0) for a in wa) / 1000, 1),
            "runs": len(wa),
            "minutes": round(mins),
            "long_km": round(max([(a.get("distance_m") or 0) for a in wa] or [0]) / 1000, 1),
            "hard_share": round(hard / mins, 2) if mins else 0,
        })

    # ── 單趟分析（最近 10 趟）──
    easy_ef = [(a["date"], ef(a)) for a in acts if classify(a.get("avg_hr"), max_hr) == "easy" and ef(a)]
    base_ef = mean([v for _, v in easy_ef[:-1]][-15:]) if len(easy_ef) > 3 else None
    runs = []
    for a in acts[-10:][::-1]:
        km = (a.get("distance_m") or 0) / 1000
        if km <= 0:
            continue
        pace = (a.get("duration_s") or 0) / km
        zone = classify(a.get("avg_hr"), max_hr)
        e = ef(a)
        notes = []
        if zone == "easy" and e and base_ef:
            diff = (e - base_ef) / base_ef
            if diff > 0.04:
                notes.append(f"有氧效率比你平常好 {diff:.0%}，身體狀況不錯")
            elif diff < -0.05:
                notes.append(f"有氧效率比平常差 {abs(diff):.0%}，可能是疲勞、天氣熱或睡不好")
        if a.get("avg_hr") and max_hr:
            pct = a["avg_hr"] / max_hr
            if km >= 4 and 0.82 <= pct < 0.88 and (a.get("name") or "").find("輕鬆") >= 0:
                notes.append("名稱是輕鬆跑，但心率落在中等強度，下次再放慢一點")
        if km >= 15:
            notes.append("長距離完成，記得補充碳水和蛋白質")
        runs.append({"date": a["date"], "name": a.get("name") or "跑步", "km": round(km, 2),
                     "pace": fmt_pace(pace), "avg_hr": a.get("avg_hr"),
                     "hr_pct": round(a["avg_hr"] / max_hr * 100) if a.get("avg_hr") else None,
                     "zone": zone, "ef": round(e, 3) if e else None, "notes": notes})

    # ── 負荷與恢復趨勢 ──
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=?", (user_id, since.isoformat()))
    loads = metrics.daily_loads(acts, daily, prefs, since, today, cross)   # 其他運動也算進負荷
    lv = [loads[k] for k in sorted(loads)]
    acute = sum(lv[-7:])
    chronic = sum(lv[-28:]) / 4 if len(lv) >= 28 else None
    first = db.q1("SELECT MIN(date) d FROM activities WHERE user_id=?", (user_id,))["d"]
    enough = bool(first) and (today - date.fromisoformat(first)).days >= 28
    acwr = round(acute / chronic, 2) if chronic and enough else None   # 紀錄不到 4 週時比不出來，不顯示以免誤報

    def avg(key, a, b):
        vals = [d[key] for d in daily if d.get(key) and a <= d["date"] <= b]
        return round(mean(vals), 1) if vals else None

    t = today.isoformat()
    d7, d14, d35 = [(today - timedelta(days=n)).isoformat() for n in (7, 14, 35)]
    recovery = {
        "rhr_7": avg("rhr", d7, t), "rhr_base": avg("rhr", d35, d7),
        "hrv_7": avg("hrv_avg", d7, t), "hrv_base": avg("hrv_avg", d35, d7),
        "sleep_h_7": avg("sleep_hours", d7, t), "sleep_score_7": avg("sleep_score", d7, t),
    }

    ef_trend = [{"date": d, "ef": round(v, 3)} for d, v in easy_ef[-40:]]
    ef_now = mean([v for _, v in easy_ef[-6:]]) if len(easy_ef) >= 6 else None
    ef_prev = mean([v for _, v in easy_ef[-14:-6]]) if len(easy_ef) >= 12 else None

    four = weeks[-5:-1]  # 最近 4 個完整週
    hard_share_4w = (sum(w["hard_share"] * w["minutes"] for w in four) / sum(w["minutes"] for w in four)
                     if sum(w["minutes"] for w in four) else 0)

    insights = build_insights(weeks, four, hard_share_4w, acwr, recovery, ef_now, ef_prev, prefs)
    return {"max_hr": round(max_hr), "rest_hr": round(rest_hr), "weeks": weeks, "runs": runs,
            "acwr": acwr, "recovery": recovery, "ef_trend": ef_trend,
            "ef_change": round((ef_now - ef_prev) / ef_prev, 3) if ef_now and ef_prev else None,
            "hard_share_4w": round(hard_share_4w, 2), "insights": insights}


def build_insights(weeks, four, hard_share, acwr, rec, ef_now, ef_prev, prefs) -> list[dict]:
    out = []

    def add(level, title, why, action):
        out.append({"level": level, "title": title, "why": why, "action": action})

    if acwr is not None:
        if acwr > 1.5:
            add("alert", "最近一週加量太猛",
                f"近 7 天負荷是過去 4 週平均的 {acwr} 倍，超過 1.5 時受傷風險明顯上升。",
                "接下來 3–4 天以休息或輕鬆跑為主，讓身體跟上。")
        elif acwr > 1.3:
            add("warn", "跑量增加得有點快",
                f"急慢性負荷比 {acwr}，在 0.8–1.3 之間最安全。", "這週先別再加量，維持就好。")
        elif acwr < 0.7 and sum(w["runs"] for w in four) >= 4:
            add("info", "最近跑得比平常少",
                f"急慢性負荷比 {acwr}，體能可能開始流失。", "如果身體沒問題，可以慢慢回到平常的量。")

    if sum(w["minutes"] for w in four) >= 60:
        if hard_share > 0.35:
            add("warn", "輕鬆跑可能跑太快了",
                f"近 4 週約 {hard_share:.0%} 的時間心率在中高強度區（平均心率 ≥ 最大心率 82%）。"
                "多數耐力訓練建議大約 80% 時間是輕鬆強度。",
                "輕鬆跑把心率壓在最大心率 75% 以下，能講完整句子的速度。")
        elif hard_share < 0.08 and int(prefs.get("intensity", 50)) >= 40:
            add("info", "幾乎沒有強度刺激",
                "近 4 週幾乎都是輕鬆跑，有氧底子會進步，但速度提升有限。",
                "每週加一堂法特雷克或節奏跑。")
        else:
            add("good", "強度分配很健康", f"近 4 週約 {1 - hard_share:.0%} 時間是輕鬆強度，符合 80/20 原則。",
                "繼續保持。")

    if len(four) == 4:
        prev, last = four[-2]["km"], four[-1]["km"]
        if prev >= 5 and last > prev * 1.2:
            add("warn", "週跑量一次加太多", f"上週 {last} 公里，比前一週多了 {last / prev - 1:.0%}。",
                "一般建議每週增加不超過 10%。")
        longest = max(w["long_km"] for w in four)
        avg_km = mean(w["km"] for w in four)
        if avg_km >= 10 and longest > avg_km * 0.5:
            add("info", "長跑佔週跑量比例偏高",
                f"最長一趟 {longest} 公里，約佔平均週跑量 {avg_km:.0f} 公里的 {longest / avg_km:.0%}。",
                "長跑最好不超過週跑量的 30–40%，可以把其他天的量補起來。")
        runs_pw = mean(w["runs"] for w in four)
        if runs_pw < 2:
            add("info", "跑步頻率偏低", f"近 4 週平均每週 {runs_pw:.1f} 次。",
                "每週固定 3 次比偶爾跑一次長的更容易進步，也比較不會受傷。")
        elif runs_pw >= 3 and all(w["runs"] >= 3 for w in four):
            add("good", "連續 4 週都很規律", f"平均每週 {runs_pw:.1f} 次。", "規律是進步最大的關鍵，繼續保持！")

    if ef_now and ef_prev:
        ch = (ef_now - ef_prev) / ef_prev
        if ch > 0.03:
            add("good", "有氧能力在進步", f"輕鬆跑的有氧效率提升了 {ch:.0%}：同樣心率下跑得更快了。", "照目前方式繼續練。")
        elif ch < -0.04:
            add("warn", "有氧效率下降", f"輕鬆跑的有氧效率下降了 {abs(ch):.0%}。可能是累積疲勞、天氣變熱或生病前兆。",
                "注意睡眠，必要時安排一個減量週。")

    if rec["rhr_7"] and rec["rhr_base"] and rec["rhr_7"] - rec["rhr_base"] >= 4:
        hrv_down = rec["hrv_7"] and rec["hrv_base"] and rec["hrv_7"] < rec["hrv_base"] * 0.92
        add("alert" if hrv_down else "warn", "安靜心率偏高" + ("、HRV 偏低" if hrv_down else ""),
            f"近 7 天安靜心率 {rec['rhr_7']}，比平常 {rec['rhr_base']} 高。" + ("HRV 也比平常低，" if hrv_down else "")
            + "常見原因是疲勞累積、睡眠不足、壓力或快感冒。",
            "這幾天降低強度，多睡一點；有發燒或不舒服就休息。")
    if rec["sleep_h_7"] and rec["sleep_h_7"] < 6.5:
        add("warn", "睡眠不足", f"近 7 天平均只睡 {rec['sleep_h_7']} 小時。", "睡眠是最有效的恢復，目標 7 小時以上。")

    order = {"alert": 0, "warn": 1, "info": 2, "good": 3}
    out.sort(key=lambda x: order[x["level"]])
    if not out:
        add("info", "資料還不夠多", "累積幾週有心率的跑步紀錄後，這裡會出現更具體的建議。", "先照課表跑起來！")
    return out
