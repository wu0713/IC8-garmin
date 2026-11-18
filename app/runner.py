"""跑者數據：個人最佳、成績預測、最大攝氧量、心率與配速。

成績預測優先用 Garmin 自己的預測；沒有的話（範例、Apple Watch），用 Jack Daniels 的 VDOT 公式：
從近半年最好的一次表現推出 VDOT，再換算各距離的預測成績。VDOT 也可當作「推估最大攝氧量」。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from . import db, game, metrics
from .config import TZ
from .garmin_client import today_local

RACES = [("5k", "5 公里", 5000), ("10k", "10 公里", 10000), ("half", "半程馬拉松", 21097.5), ("full", "全程馬拉松", 42195)]


# ───────────────────────────── VDOT（Daniels & Gilbert） ─────────────────────────────

def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(dist_m: float, secs: float) -> float | None:
    if dist_m < 1500 or secs <= 0:
        return None
    t = secs / 60
    return _vo2(dist_m / t) / _pct_max(t)


def predict(v: float, dist_m: float) -> float:
    """給定 VDOT，二分搜尋這個距離的預測秒數。"""
    lo, hi = dist_m / 1000 * 120, dist_m / 1000 * 900      # 每公里 2 分 ~ 15 分
    for _ in range(60):
        mid = (lo + hi) / 2
        if vdot(dist_m, mid) > v:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def best_effort(acts: list[dict], records: list[dict], since: str) -> dict | None:
    """近期最能代表實力的一次：PB 或整趟紀錄中 VDOT 最高的（3 公里以上）。"""
    cands = []
    for r in records:
        m = dict(RACES_M).get(r["rtype"])
        if m and r["date"] >= since and r.get("value"):
            cands.append({"vdot": vdot(m, r["value"]), "dist": m, "secs": r["value"], "date": r["date"], "from": "pb"})
    for a in acts:
        if a["date"] >= since and (a.get("distance_m") or 0) >= 3000 and a.get("duration_s"):
            cands.append({"vdot": vdot(a["distance_m"], a["duration_s"]), "dist": a["distance_m"], "secs": a["duration_s"],
                          "date": a["date"], "from": "run", "id": a["activity_id"]})
    cands = [c for c in cands if c["vdot"]]
    return max(cands, key=lambda c: c["vdot"]) if cands else None


RACES_M = [(k, m) for k, _, m in RACES]


def fmt_time(s: float | None) -> str | None:
    if not s:
        return None
    s = round(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ───────────────────────────── 組合 ─────────────────────────────

def stored_metrics(user_id: int) -> dict:
    r = db.q1("SELECT data, updated_at FROM runner_metrics WHERE user_id=?", (user_id,))
    return {**json.loads(r["data"]), "updated_at": r["updated_at"]} if r else {}


def save_metrics(user_id: int, data: dict):
    if not data:
        return
    with db.tx() as c:
        c.execute("INSERT INTO runner_metrics(user_id, data, updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                  (user_id, json.dumps(data, ensure_ascii=False), datetime.now(TZ).isoformat(timespec="seconds")))


def profile(user_id: int) -> dict:
    prefs = db.get_prefs(user_id)
    today = today_local()
    acts = db.q("SELECT * FROM activities WHERE user_id=? ORDER BY date", (user_id,))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? ORDER BY date", (user_id, (today - timedelta(days=60)).isoformat()))
    records, rsrc = game.records_for(user_id, acts)
    gm = stored_metrics(user_id)

    # 個人最佳：每個距離取最好的
    best = {}
    for r in records:
        k = r["rtype"]
        if k in dict(RACES_M) and (k not in best or r["value"] < best[k]["value"]):
            best[k] = r
    pbs = [{"key": k, "label": lab, "time": fmt_time(best[k]["value"]) if k in best else None,
            "pace": metrics.fmt_pace(best[k]["value"] / (m / 1000)) if k in best else None,
            "date": best[k]["date"] if k in best else None, "activity_id": best[k].get("activity_id") if k in best else None}
           for k, lab, m in RACES]

    # 成績預測
    since = (today - timedelta(days=180)).isoformat()
    eff = best_effort(acts, records, since)
    v = eff["vdot"] if eff else None
    gp = gm.get("predictions") or {}
    preds = []
    for k, lab, m in RACES:
        secs, src = (gp.get(k), "garmin") if gp.get(k) else ((predict(v, m), "vdot") if v else (None, None))
        preds.append({"key": k, "label": lab, "time": fmt_time(secs), "pace": metrics.fmt_pace(secs / (m / 1000)) if secs else None,
                      "source": src})

    # 我的數值
    rest = [d["rhr"] for d in daily[-7:] if d.get("rhr")]
    hrv = [d["hrv_avg"] for d in daily[-7:] if d.get("hrv_avg")]
    max_hr = metrics.estimate_max_hr(acts, prefs)
    paces = metrics.estimate_paces(acts, prefs)
    lt_hr = gm.get("lt_hr") or round(max_hr * 0.88)
    lt_pace = gm.get("lt_pace_s") or paces["tempo"]
    vo2 = gm.get("vo2max")
    four = [a for a in acts if a["date"] >= (today - timedelta(days=28)).isoformat()]
    numbers = [
        {"key": "vo2max", "label": "最大攝氧量", "value": round(vo2) if vo2 else (round(v, 1) if v else None), "unit": "ml/kg/min",
         "note": "Garmin 手錶估算" if vo2 else ("用你的跑步成績推算（VDOT）" if v else "需要 3 公里以上的跑步紀錄")},
        {"key": "max_hr", "label": "最大心率", "value": round(max_hr), "unit": "bpm",
         "note": "你自己設定的" if prefs.get("max_hr") else "從紀錄裡的最高心率估算，可以在設定裡改"},
        {"key": "rhr", "label": "安靜心率", "value": round(sum(rest) / len(rest)) if rest else None, "unit": "bpm", "note": "最近 7 天平均"},
        {"key": "hrv", "label": "HRV", "value": round(sum(hrv) / len(hrv)) if hrv else None, "unit": "ms", "note": "最近 7 天夜間平均"},
        {"key": "lt", "label": "乳酸閾值", "value": round(lt_hr), "unit": "bpm",
         "note": f"配速約 {metrics.fmt_pace(lt_pace)}/km" + ("（Garmin 測得）" if gm.get("lt_hr") else "（估算）")},
        {"key": "weekly", "label": "近 4 週平均週跑量", "value": round(sum((a.get("distance_m") or 0) for a in four) / 4000, 1), "unit": "km",
         "note": f"{len(four)} 次跑步"},
    ]

    # 訓練配速與心率上限（心率上限以最大心率的百分比計）
    zones = [("easy", "輕鬆跑", "E", 0.74), ("long", "長跑", "L", 0.76), ("marathon", "馬拉松配速", "M", 0.82),
             ("tempo", "節奏跑／閾值", "T", 0.88), ("interval", "間歇", "I", 0.92), ("rep", "短衝刺", "R", None)]
    pace_rows = [{"key": k, "label": lab, "code": code, "pace": metrics.fmt_pace(paces[k]),
                  "hr_cap": round(max_hr * pct) if pct else None} for k, lab, code, pct in zones]

    return {"pbs": pbs, "pb_source": rsrc, "predictions": preds,
            "basis": eff and {"km": round(eff["dist"] / 1000, 1), "time": fmt_time(eff["secs"]), "date": eff["date"],
                              "vdot": round(eff["vdot"], 1), "id": eff.get("id")},
            "prediction_source": "garmin" if gp else ("vdot" if v else None),
            "numbers": numbers, "paces": pace_rows, "updated_at": gm.get("updated_at")}
