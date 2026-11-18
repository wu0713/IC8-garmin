"""訓練負荷與恢復狀態的計算。

用到的都是公開、常見的運動科學模型：
- Banister TRIMP：用心率儲備 × 時間估算一次訓練的負荷
- CTL / ATL / TSB（體能 / 疲勞 / 狀態）：42 天與 7 天的指數加權平均（GoldenCheetah PMC 同款）
- 恢復分數：綜合 TSB、HRV、睡眠、Body Battery、安靜心率、主觀感受
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean, median


def trimp(duration_s: float | None, avg_hr: float | None, rest_hr: float, max_hr: float, sex: str = "M") -> float:
    if not duration_s:
        return 0.0
    mins = duration_s / 60
    if not avg_hr or max_hr <= rest_hr:
        return mins * 0.9  # 沒心率：當作輕鬆強度粗估
    hrr = max(0.0, min(1.0, (avg_hr - rest_hr) / (max_hr - rest_hr)))
    k = (0.86, 1.67) if sex == "F" else (0.64, 1.92)
    return mins * hrr * k[0] * math.exp(k[1] * hrr)


def estimate_max_hr(activities: list[dict], prefs: dict) -> float:
    if prefs.get("max_hr"):
        return float(prefs["max_hr"])
    seen = [a["max_hr"] for a in activities if a.get("max_hr")]
    if seen:
        return max(seen)
    age = prefs.get("age") or 35
    return 208 - 0.7 * age


def estimate_rest_hr(daily: list[dict]) -> float:
    vals = [d["rhr"] for d in daily[-30:] if d.get("rhr")]
    return median(vals) if vals else 55.0


def daily_loads(activities, daily, prefs, start: date, end: date) -> dict[str, float]:
    rest = estimate_rest_hr(daily)
    mx = estimate_max_hr(activities, prefs)
    sex = prefs.get("sex", "M")
    loads = {(start + timedelta(days=i)).isoformat(): 0.0 for i in range((end - start).days + 1)}
    for a in activities:
        if a["date"] in loads:
            loads[a["date"]] += trimp(a.get("duration_s"), a.get("avg_hr"), rest, mx, sex)
    return loads


def pmc(loads: dict[str, float]) -> list[dict]:
    """Performance Management Chart：回傳每天的 CTL/ATL/TSB。"""
    ctl = atl = 0.0
    out = []
    kc, ka = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    for d in sorted(loads):
        tsb = ctl - atl  # 當天早上的狀態 = 前一天結束時的 CTL - ATL
        ctl += (loads[d] - ctl) * kc
        atl += (loads[d] - atl) * ka
        out.append({"date": d, "load": round(loads[d], 1), "ctl": round(ctl, 1),
                    "atl": round(atl, 1), "tsb": round(tsb, 1)})
    return out


def readiness(today: str, pmc_rows: list[dict], daily: list[dict], checkin: dict | None, novice: bool = False) -> dict:
    """0~100 的恢復分數，附上每個因子的說明，讓使用者知道為什麼。"""
    by_date = {d["date"]: d for d in daily}
    t = by_date.get(today) or (daily[-1] if daily else {})
    factors = []
    score = 70.0

    tsb = next((r["tsb"] for r in pmc_rows if r["date"] == today), pmc_rows[-1]["tsb"] if pmc_rows else 0)
    ctl = pmc_rows[-1]["ctl"] if pmc_rows else 0
    # 用 CTL 正規化，避免低跑量的人 TSB 數字很小就看不出差別
    rel = tsb / max(ctl, 10)
    adj = max(-25, min(12, rel * 40))
    if not novice:   # 剛開始記錄時，「體能」還沒累積起來，疲勞一定看起來偏高 → 先不計
        score += adj
        factors.append(("近期疲勞（TSB）", round(tsb, 1), adj))

    hist_hrv = [d["hrv_avg"] for d in daily[-35:-1] if d.get("hrv_avg")]
    if t.get("hrv_avg") and len(hist_hrv) >= 5:
        base = mean(hist_hrv)
        delta = (t["hrv_avg"] - base) / base
        adj = max(-15, min(8, delta * 60))
        score += adj
        factors.append(("HRV", t["hrv_avg"], adj))
    elif t.get("hrv_status") in ("LOW", "POOR"):
        score -= 10
        factors.append(("HRV 狀態", t["hrv_status"], -10))

    if t.get("sleep_score"):
        adj = max(-15, min(8, (t["sleep_score"] - 75) * 0.5))
        score += adj
        factors.append(("睡眠分數", t["sleep_score"], adj))

    if t.get("body_battery"):
        adj = max(-12, min(8, (t["body_battery"] - 70) * 0.3))
        score += adj
        factors.append(("Body Battery", t["body_battery"], adj))

    hist_rhr = [d["rhr"] for d in daily[-35:-1] if d.get("rhr")]
    if t.get("rhr") and len(hist_rhr) >= 5:
        diff = t["rhr"] - mean(hist_rhr)
        adj = max(-12, min(4, -diff * 2.5))
        score += adj
        factors.append(("安靜心率", t["rhr"], adj))

    if checkin and checkin.get("feeling"):
        adj = (checkin["feeling"] - 3) * 8
        score += adj
        factors.append(("主觀感受", checkin["feeling"], adj))

    score = round(max(0, min(100, score)))
    level = "green" if score >= 65 else "yellow" if score >= 40 else "red"
    return {
        "score": score,
        "level": level,
        "factors": [{"name": n, "value": v, "impact": round(a, 1)} for n, v, a in factors],
    }


def estimate_paces(activities: list[dict], prefs: dict) -> dict:
    """估算各強度配速（秒/公里）。使用者有填就用使用者的。"""
    if prefs.get("easy_pace_s"):
        easy = float(prefs["easy_pace_s"])
    else:
        recent = sorted(activities, key=lambda a: a["date"])[-30:]
        paces = [a["duration_s"] / (a["distance_m"] / 1000) for a in recent
                 if a.get("distance_m") and a["distance_m"] > 2000 and a.get("duration_s")]
        # 取偏慢的中位數當輕鬆配速（多數人大部分跑步都是輕鬆跑）
        easy = sorted(paces)[int(len(paces) * 0.6)] if paces else 400.0
    thr = float(prefs.get("threshold_pace_s") or easy * 0.84)
    return {
        "recovery": round(easy * 1.08),
        "easy": round(easy),
        "long": round(easy * 1.02),
        "marathon": round(thr * 1.07),
        "tempo": round(thr),
        "interval": round(thr * 0.93),
        "rep": round(thr * 0.87),
    }


def fmt_pace(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def weekly_minutes(activities: list[dict], end: date, weeks: int = 4) -> list[float]:
    out = []
    for w in range(weeks):
        we = end - timedelta(days=7 * w)
        ws = we - timedelta(days=6)
        out.append(sum((a.get("duration_s") or 0) / 60 for a in activities
                       if ws.isoformat() <= a["date"] <= we.isoformat()))
    return out
