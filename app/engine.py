"""排課規格書的九層管線（純函式，不碰資料庫）。

  L1 diagnose          能力診斷 → 限制因子
  L2 feasibility       可行性檢查 → 可行／無緩衝／不可行（附可達成成績、建議賽期）
  L3 split_phases      分期：四階段的週數
  L4 ramp_volume       跑量疊加（含減量週、減量期曲線）
  L6 derive_zones      配速區間 + 絕對心率上限 + HR-配速回歸校正
  L7 expand_events     固定事件 → 每天的限制與每週的倍率
  L8 make_checkpoints  測驗插入點與門檻表
  L9 evaluate_stoploss 執行期停損條件
  L5（每週組成與排程）在 week.py。build_season 把以上串起來，產生整季的週表。

所有決定都附上理由字串（why），讓使用者問「為什麼今天跑這個」時答得出來。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

from . import plan_tables as T

# ═════════════════════════════ 共用：VDOT 與等效成績 ═════════════════════════════


def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(km: float, seconds: float) -> float:
    t = seconds / 60
    return _vo2(km * 1000 / t) / _pct_max(t)


def pace_at(vd: float, fraction: float) -> float:
    """VDOT 的某個 %VO2max 對應的配速（秒/公里）。"""
    a, b, c = 0.000104, 0.182258, -4.60 - vd * fraction
    v = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)
    return 60000 / v


def equivalent_time(vd: float, km: float) -> float:
    lo, hi = 60.0, 60 * 60 * 12.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if vdot(km, mid) > vd:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def riegel(seconds: float, km_from: float, km_to: float) -> float:
    return seconds * (km_to / km_from) ** T.RIEGEL_EXP


def fmt_time(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_pace(s: float) -> str:
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ═════════════════════════════ L1 能力診斷 ═════════════════════════════

LIMITER_ZH = {"endurance": "耐力受限", "speed": "速度受限", "pacing": "配速自我控制",
              "consistency": "一致性受限", "balanced": "沒有明顯短板"}
LIMITER_SHAPE = {
    "endurance": "基礎期拉長（≥ 40% 總週數），前期只有輕鬆跑和 20 秒快步，沒有強度課",
    "speed": "跑量維持，強度課提高到每週 3 次，專項期拉長",
    "pacing": "基礎期正常，但輕鬆跑配速會先往慢調、配心率警示，跑滿 8 趟後用資料校正",
    "consistency": "前 6 週不加量，先把「每週跑幾次」固定下來；連續跑步最多 2 天",
    "balanced": "標準分期：基礎 35%、進展 25%、專項 30%、減量 10%",
}


def diagnose(ath: dict, goal: dict) -> dict:
    """五個指標 → 限制因子。ath 由 athlete.py 組出來。"""
    why = []
    wk = ath.get("weekly_km") or []
    base8 = wk[:8]
    baseline = round(median(base8), 1) if base8 else 0.0
    why.append(f"跑量基線：近 {len(base8)} 週週跑量中位數 {baseline} km")
    low_weeks = sum(1 for w in wk[:12] if baseline > 0 and w < baseline * T.CONSISTENCY_LOW_SHARE)
    why.append(f"一致性：近 {min(12, len(wk))} 週有 {low_weeks} 週跑量不到基線一半")

    max_hr = ath["max_hr"]
    best = ath.get("best_effort")
    cur_vdot = vdot(best["km"], best["seconds"]) if best else ath.get("fallback_vdot")
    goal_km = goal["distance_km"]
    pred_goal_s = equivalent_time(cur_vdot, goal_km) if cur_vdot else None
    if best:
        why.append(f"速度能力：{best['km']:.1f} km {fmt_time(best['seconds'])}（{best['date']}）→ VDOT {cur_vdot:.1f}，"
                   f"目標距離等效 {fmt_time(pred_goal_s)}")
    elif cur_vdot:
        why.append(f"速度能力：近期沒有 3–10 km 的全力紀錄，用平常配速粗估 VDOT {cur_vdot:.1f}")

    long = None
    for r in sorted(ath.get("long_runs") or [], key=lambda r: -r["km"]):
        if r["km"] >= goal_km * T.LONG_MIN_SHARE:
            long = r
            break
    ratio = long_hr = None
    if long:
        long_pace = long["seconds"] / long["km"]
        long_hr = (long["avg_hr"] / max_hr) if long.get("avg_hr") else None
        why.append(f"耐力能力：{long['km']:.1f} km 長跑，配速 {fmt_pace(long_pace)}/km"
                   + (f"、平均心率 {round(long['avg_hr'])}（{round(long_hr * 100)}% max）" if long_hr else ""))
        if best:
            pred_pace = riegel(best["seconds"], best["km"], long["km"]) / long["km"]
            ratio = round(long_pace / pred_pace, 2)
            why.append(f"脅鉤比：長距離實際配速 ÷ 短距離推估配速 = {ratio}")
    else:
        why.append(f"耐力能力：近 12 週沒有 ≥ 目標距離 60%（{goal_km * T.LONG_MIN_SHARE:.1f} km）的長跑")

    easy_share = ath.get("easy_hr_share")
    easy_too_fast = bool(easy_share and easy_share > T.EASY_TOO_FAST_HR)
    if easy_share:
        why.append(f"輕鬆跑心率：短課平均 {round(easy_share * 100)}% max"
                   + ("，偏高（輕鬆跑常跑太快）" if easy_too_fast else ""))

    flags: list[str] = []
    rule = None
    if low_weeks >= T.CONSISTENCY_LIMIT or ath.get("novice"):
        flags.append("consistency")
        rule = ("新手：紀錄還不夠，先固定頻率" if ath.get("novice") and low_weeks < T.CONSISTENCY_LIMIT
                else f"一致性指標 {low_weeks} ≥ {T.CONSISTENCY_LIMIT}")
    if ratio is not None and ratio > T.DECOUPLE_HIGH:
        if long_hr is not None and long_hr < T.LONG_HR_PACING:
            flags.append("pacing")
            rule = rule or f"脅鉤比 {ratio} > {T.DECOUPLE_HIGH} 且長距離心率 < 80%：不是跑不動，是沒有在控速"
        else:
            flags.append("endurance")
            rule = rule or (f"脅鉤比 {ratio} > {T.DECOUPLE_HIGH} 且長距離心率 > 88%" if long_hr and long_hr > T.LONG_HR_ENDURANCE
                            else f"脅鉤比 {ratio} > {T.DECOUPLE_HIGH}（心率 80–88% 或沒有心率：規格書未定義，視為耐力受限）")
    elif ratio is not None and ratio <= T.DECOUPLE_LOW and goal.get("target_s") and pred_goal_s and pred_goal_s > goal["target_s"]:
        flags.append("speed")
        rule = rule or f"脅鉤比 {ratio} ≤ {T.DECOUPLE_LOW}，耐力夠，但目前速度推估 {fmt_time(pred_goal_s)} 慢於目標"
    elif long is None:
        flags.append("endurance")
        rule = rule or "沒有夠長的長跑可以證明耐力，先當作耐力受限"
    limiter = flags[0] if flags else "balanced"
    if not flags:
        rule = "沒有符合任何受限條件"
    return {
        "baseline_km": baseline, "low_weeks": low_weeks, "cur_vdot": cur_vdot and round(cur_vdot, 1),
        "pred_goal_s": pred_goal_s and round(pred_goal_s), "decouple_ratio": ratio,
        "long_hr_pct": long_hr and round(long_hr, 3), "easy_too_fast": easy_too_fast,
        "limiter": limiter, "flags": flags, "limiter_label": LIMITER_ZH[limiter], "rule": rule,
        "shape": LIMITER_SHAPE[limiter], "why": why,
        "needs_frequent_deload": "consistency" in flags or (ath.get("age") or 0) > 45,
    }


# ═════════════════════════════ L1b 跑者程度與目標合理性 ═════════════════════════════

def athlete_level(ath: dict, diag: dict, override: str | None = None) -> dict:
    """新手／有基礎／有經驗。決定基礎期要不要就有強度課，以及能期待多快的進步。"""
    base = diag.get("baseline_km") or 0
    v = diag.get("cur_vdot") or 0
    hard = ath.get("hard_runs_8w") or 0
    weeks = ath.get("history_weeks") or len(ath.get("weekly_km") or [])
    why = [f"近 8 週週跑量中位數 {base:g} km、VDOT {v or '—'}、近 8 週強度課 {hard} 次、有紀錄的週數 {weeks}"]
    if override in T.LEVEL_ZH:
        return {"level": override, "label": T.LEVEL_ZH[override], "source": "manual",
                "why": why + ["你在設定裡指定了程度"], "shape": T.LEVEL_SHAPE[override]}
    if ath.get("novice") or base < T.LEVEL_NOVICE_KM or weeks < 4:
        lv = "novice"
        why.append(f"跑量 < {T.LEVEL_NOVICE_KM} km 或紀錄不到 4 週 → 先當新手，安全第一")
    elif (base >= T.LEVEL_EXP_KM and v >= T.LEVEL_EXP_VDOT and diag.get("low_weeks", 9) <= 1
          and hard >= T.LEVEL_EXP_HARD_RUNS):
        lv = "experienced"
        why.append(f"跑量 ≥ {T.LEVEL_EXP_KM} km、VDOT ≥ {T.LEVEL_EXP_VDOT}、規律而且有在練強度 → 可以從一開始就混合練")
    else:
        lv = "intermediate"
        miss = []
        if base < T.LEVEL_EXP_KM:
            miss.append(f"週跑量到 {T.LEVEL_EXP_KM} km")
        if v < T.LEVEL_EXP_VDOT:
            miss.append(f"VDOT 到 {T.LEVEL_EXP_VDOT}")
        if hard < T.LEVEL_EXP_HARD_RUNS:
            miss.append(f"近 8 週練過 {T.LEVEL_EXP_HARD_RUNS} 次以上強度課")
        if diag.get("low_weeks", 0) > 1:
            miss.append("每週跑量更穩定")
        why.append("有基礎；要被判為「有經驗」還差：" + "、".join(miss) if miss else "有基礎")
    return {"level": lv, "label": T.LEVEL_ZH[lv], "source": "auto", "why": why, "shape": T.LEVEL_SHAPE[lv]}


def assess(*, diag: dict, goal: dict, level: str, weeks: int, feas: dict | None, ath: dict,
           decision: str | None = None) -> dict:
    """目標合不合理、課表會不會讓人受傷。不合理時課表照「做得到的」排，並明白說出來。"""
    cur = diag.get("cur_vdot")
    out = {"level": level, "cur_vdot": cur, "pred_goal_s": diag.get("pred_goal_s"), "verdict": "none", "risks": []}
    risks = out["risks"]

    def risk(sev, title, text):
        risks.append({"severity": sev, "title": title, "text": text})

    gmax = min(T.VDOT_GAIN_CAP[level], T.VDOT_GAIN_PER_WEEK[level] * max(1, weeks))
    out["gain_max"] = round(gmax, 3)
    if cur and goal.get("target_s"):
        tv = vdot(goal["distance_km"], goal["target_s"])
        gain = tv / cur - 1
        out.update({"target_vdot": round(tv, 1), "gain": round(gain, 3)})
        real = equivalent_time(cur * (1 + gmax), goal["distance_km"])
        out["realistic_s"] = round(real)
        if gain <= gmax * 0.6:
            out["verdict"], out["text"] = "realistic", "目標在你目前能力的合理進步範圍內。"
        elif gain <= gmax:
            out["verdict"], out["text"] = "ambitious", "目標偏積極，但照表練、沒有中斷的話有機會。"
        else:
            out["verdict"] = "unrealistic"
            out["text"] = (f"以目前能力（約 {fmt_time(diag.get('pred_goal_s'))}）和 {weeks} 週的時間，比較實際的成績是 "
                           f"{fmt_time(real)} 左右。課表會先照這個成績排配速，等檢核點跑出進步再往目標推。")
            risk("danger", f"目標配速比目前能力快 {round(gain * 100)}%",
                 "直接用目標配速練強度課，配速會超出身體能負荷的範圍，是最常見的受傷原因（脛前疼痛、跟腱、足底）。"
                 "課表已改用做得到的配速；如果你自己加速去追目標，請特別注意疼痛訊號。")
    if level == "novice" and goal["distance_km"] >= 15 and goal.get("race_date") and weeks < 16:
        risk("warn", f"新手準備{'全馬' if goal['distance_km'] >= 30 else '半馬'}只有 {weeks} 週",
             "一般建議新手至少 16 週（全馬 20 週以上）。可以完賽，但請以完賽為目標，不要追時間。")
    if feas and feas.get("verdict") == "infeasible" and decision == "accept_risk":
        risk("danger", "你選了在時間不夠的情況下照原目標練",
             f"跑量只會疊到每週 {feas.get('v_reachable')} km；若再自行加量，週增幅超過 10% 時受傷機率明顯上升。")
    elif feas and feas.get("verdict") == "tight":
        risk("warn", "時間沒有緩衝", "生病或受傷一週就會影響進度。寧可少練一堂，也不要硬補。")
    vb, vt = (feas or {}).get("v_base"), (feas or {}).get("v_target")
    if vb and vt and vt > vb * 1.8:
        risk("warn", f"週跑量要從 {vb:g} km 疊到 {vt:g} km",
             "跑量會增加超過八成。疊量期間如果小腿、足底、膝蓋開始痛，先停一兩天，不要撐。")
    if diag.get("easy_too_fast"):
        risk("warn", "你的輕鬆跑常常跑太快",
             "加了強度課之後，輕鬆跑再跑快就等於天天都在練強度，容易累積成傷。輕鬆跑請守住心率上限。")
    if (ath.get("age") or 0) >= 50:
        risk("info", "50 歲以上", "恢復需要比較久：每 3 週會排一次減量週，強度課之間至少隔 2 天。")
    return out


# ═════════════════════════════ L2 可行性檢查 ═════════════════════════════

def _rows_for(km: float):
    dists = sorted({r[0] for r in T.VOLUME_NEEDS})
    d = min(dists, key=lambda x: abs(math.log(km / x)))
    return sorted([r for r in T.VOLUME_NEEDS if r[0] == d], key=lambda r: -r[1]), d   # 慢 → 快


def volume_need(km: float, target_s: float) -> tuple[float, float]:
    rows, d = _rows_for(km)
    t = riegel(target_s, km, d) if abs(d - km) > 0.5 else target_s   # 非標準距離：先換算成最近的標準距離
    if t >= rows[0][1]:
        return rows[0][2], rows[0][3]
    if t <= rows[-1][1]:
        return rows[-1][2], rows[-1][3]
    for a, b in zip(rows, rows[1:]):
        if b[1] <= t <= a[1]:
            f = (a[1] - t) / (a[1] - b[1])
            return a[2] + f * (b[2] - a[2]), a[3] + f * (b[3] - a[3])
    return rows[-1][2], rows[-1][3]


def time_for_volume(km: float, vol: float) -> float:
    """反查：週跑量 vol 大約撐得起多快的成績（取需求區間中點）。"""
    rows, d = _rows_for(km)
    mids = [(r[1], (r[2] + r[3]) / 2) for r in rows]
    if vol <= mids[0][1]:
        t = mids[0][0]
    elif vol >= mids[-1][1]:
        t = mids[-1][0]
    else:
        t = mids[-1][0]
        for (ta, va), (tb, vb) in zip(mids, mids[1:]):
            if va <= vol <= vb:
                t = ta + (vol - va) / (vb - va) * (tb - ta)
                break
    return riegel(t, d, km) if abs(d - km) > 0.5 else t


def ramp_weeks(v_base: float, v_target: float, r: float) -> int:
    if v_target <= v_base:
        return 0
    return math.ceil(math.log(v_target / v_base) / math.log(1 + r))


def weeks_needed(w_ramp: int, consistency: bool) -> int:
    return w_ramp + w_ramp // 3 + T.TAPER_WEEKS + (T.CONSISTENCY_FLAT_WEEKS if consistency else 0)


def feasibility(diag: dict, goal: dict, weeks_available: int, cap_km: float | None) -> dict:
    v_base = max(diag["baseline_km"], 8.0)
    r = T.growth_rate(diag["baseline_km"])
    cons = "consistency" in diag["flags"]
    r_eff = r * (T.CONSISTENCY_RAMP_FACTOR if cons else 1)
    target_s = goal.get("target_s") or diag.get("pred_goal_s")
    if not target_s:
        target_s = time_for_volume(goal["distance_km"], v_base)
    lo, hi = volume_need(goal["distance_km"], target_s)
    v_need = round((lo + hi) / 2, 1)
    v_target = max(v_need, round(v_base, 1))
    w_ramp = ramp_weeks(v_base, v_target, r_eff)
    w_need = weeks_needed(w_ramp, cons)
    ratio = w_need / max(1, weeks_available)
    why = [f"目標 {fmt_time(target_s)} 需要週跑量約 {round(lo)}–{round(hi)} km（取 {v_need}）",
           f"從 {round(v_base, 1)} km 起、週增率 {round(r_eff * 100, 1)}%：疊量 {w_ramp} 週 + 減量週 {w_ramp // 3} + 賽前減量 {T.TAPER_WEEKS}"
           + (f" + 一致性 {T.CONSISTENCY_FLAT_WEEKS} 週不疊量" if cons else "") + f" = 需要 {w_need} 週，可用 {weeks_available} 週"]
    time_short = bool(cap_km and cap_km < v_need * 0.9)
    if time_short:
        why.append(f"你設定的可跑時間大約只撐得起每週 {round(cap_km)} km")
    if ratio <= T.FEASIBLE_BUFFER and not time_short:
        verdict = "ok"
    elif ratio <= 1.0 and not time_short:
        verdict = "tight"
    else:
        verdict = "infeasible"
    out = {"verdict": verdict, "v_base": round(v_base, 1), "v_target": v_target, "v_need_range": [round(lo), round(hi)],
           "r": round(r_eff, 3), "w_ramp": w_ramp, "w_need": w_need, "weeks_available": weeks_available,
           "ratio": round(ratio, 2), "target_s": round(target_s), "time_short": time_short, "why": why,
           "assumed_target": not goal.get("target_s")}
    if verdict == "infeasible":
        # 可用週數內最多能疊到多少
        w = 0
        while weeks_needed(w + 1, cons) <= weeks_available:
            w += 1
        v_max = v_base * (1 + r_eff) ** w
        if cap_km:
            v_max = min(v_max, cap_km)
        ach = max(time_for_volume(goal["distance_km"], v_max), target_s)
        out["achievable_s"] = round(ach)
        out["v_reachable"] = round(v_max, 1)
        out["required_weeks"] = w_need
        if goal.get("race_date") and w_need > weeks_available and not time_short:
            out["suggested_date"] = (date.fromisoformat(goal["race_date"]) + timedelta(weeks=w_need - weeks_available)).isoformat()
    if goal.get("target_s") and diag.get("cur_vdot"):
        gain = vdot(goal["distance_km"], goal["target_s"]) / diag["cur_vdot"] - 1
        if gain > T.TARGET_VDOT_MAX_GAIN:
            out["speed_gap"] = round(gain, 3)
            why.append(f"目標比目前能力快 {round(gain * 100)}%：強度課配速會先以 +{round(T.TARGET_VDOT_MAX_GAIN * 100)}% 為上限，等檢核點再更新")
    return out


# ═════════════════════════════ L3 分期 ═════════════════════════════

def split_phases(n_weeks: int, diag: dict, open_mode: bool = False, race_km: float | None = None,
                 level: str | None = None) -> list[str]:
    flags = diag["flags"]
    key = "endurance" if "endurance" in flags else "speed" if "speed" in flags else "default"
    sb, sbu, ss = T.PHASE_SPLIT[key]
    if key == "default" and race_km:
        sb, sbu, ss = T.DIST_PROFILE[T.dist_key(race_km)]["split"]
    if level == "experienced" and key != "endurance":
        sb = max(20, sb - 8)          # 有經驗的人基礎期本來就有強度課，不需要那麼長
    if open_mode:
        taper = 1
    else:
        taper = T.TAPER_WEEKS if n_weeks >= 10 else max(1, round(n_weeks * 0.1))
        taper = min(taper, n_weeks)
    rest = n_weeks - taper
    if rest <= 0:
        return ["taper"] * n_weeks
    if rest < 4:
        b, bu = 0, rest // 2
    else:
        tot = sb + sbu + ss
        b = max(1, round(rest * sb / tot))
        bu = max(1, round(rest * sbu / tot))
        if b + bu >= rest:
            bu = max(0, rest - b - 1)
    s = rest - b - bu
    tail = ["recover"] if open_mode else ["taper"] * taper
    return ["base"] * b + ["build"] * bu + ["specific"] * s + tail


# ═════════════════════════════ L4 跑量疊加 ═════════════════════════════

def ramp_volume(v_base: float, v_target: float, phases: list[str], diag: dict, *,
                insertions: dict[int, str] | None = None, freeze_from: int | None = None) -> tuple[list[float], list[str]]:
    """回傳每週目標跑量與每週的標記（normal / deload / flat / repeat / phase_repeat / taper / peak）。

    insertions：停損或錯過整週造成的插入 {週索引: deload | repeat | phase_repeat}。
    freeze_from：檢核點落後時，從這週起跑量不再上調。"""
    insertions = insertions or {}
    n = len(phases)
    taper_n = sum(1 for p in phases if p == "taper")
    pre = n - taper_n
    cons = "consistency" in diag["flags"]
    k = 3 if diag.get("needs_frequent_deload") else 4
    r = T.growth_rate(diag["baseline_km"])
    low_base = diag["baseline_km"] < 25
    vols, tags = [], []
    last = v_base           # 疊加曲線上「最近一個非減量週」的值
    hist: list[float] = []
    for w in range(pre):
        ins = insertions.get(w)
        flat = cons and w < T.CONSISTENCY_FLAT_WEEKS
        if ins == "deload":
            v, tag = last * T.DELOAD_FACTOR, "deload"
        elif ins == "repeat":
            v, tag = last, "repeat"
        elif ins == "phase_repeat":
            v = hist[-2] if len(hist) >= 2 else last
            last = v
            tag = "phase_repeat"
        elif w == 0 or flat:
            v, tag = last, ("flat" if flat else "normal")
        elif (w + 1) % k == 0 and w < pre - 1:
            v, tag = last * T.DELOAD_FACTOR, "deload"
        else:
            rr = r * (T.CONSISTENCY_RAMP_FACTOR if cons and phases[w] == "base" else 1)
            inc = last * rr
            inc = min(inc, T.LOW_BASE_ABS_CAP_KM) if low_base else min(inc, max(T.LOW_BASE_ABS_CAP_KM, last * 0.10))
            v = min(max(v_target, last), last + inc)
            if freeze_from is not None and w >= freeze_from:
                v = min(v, last)
            last, tag = v, "normal"
        vols.append(round(v, 1))
        tags.append(tag)
        hist.append(v)
    if pre:
        peak_i = max(range(pre), key=lambda i: (vols[i], i))
        if tags[peak_i] == "normal":
            tags[peak_i] = "peak"
    peak = max(vols) if vols else v_base
    curve = T.TAPER_CURVE[-taper_n:] if taper_n <= len(T.TAPER_CURVE) else T.TAPER_CURVE + [T.TAPER_CURVE[-1]] * (taper_n - 3)
    for f in curve:
        vols.append(round(peak * f, 1))
        tags.append("taper")
    return vols, tags


def long_run_series(vols: list[float], phases: list[str], race_km: float, start_long: float,
                    max_long_km: float | None) -> list[float]:
    out, prev = [], [start_long, start_long]
    kappa = T.long_race_cap(race_km)
    hold = T.DIST_PROFILE[T.dist_key(race_km)]["long_hold_specific"]
    build_max = 0.0
    for v, ph in zip(vols, phases):
        cap_prev = max(prev[-1], prev[-2]) + T.LONG_STEP_KM
        d = min(v * T.LONG_RATIO.get(ph, 0.3), race_km * kappa, cap_prev)
        if hold and ph in ("specific", "taper", "race") and build_max:
            d = min(d, build_max)          # 5K/10K：專項期長跑不再加長，力氣留給間歇
        elif ph in ("base", "build"):
            build_max = max(build_max, d)
        if max_long_km:
            d = min(d, max_long_km)
        d = round(max(d, 0), 1)
        out.append(d)
        prev.append(d)
    return out


# ═════════════════════════════ L6 配速區間 ═════════════════════════════

def fit_hr_pace(samples: list[dict]) -> dict | None:
    """HR = β0 + β1·speed + β2·WBGT。樣本要先過「前後半漂移 < 5%」。沒有天氣資料時退回兩參數。"""
    ok = [s for s in samples if s.get("drift") is not None and s["drift"] < T.REGRESSION_MAX_DRIFT
          and s.get("speed") and s.get("hr")]
    if len(ok) < T.REGRESSION_MIN_SAMPLES:
        return {"active": False, "sample_count": len(ok), "reason": f"合格樣本 {len(ok)} 筆，滿 {T.REGRESSION_MIN_SAMPLES} 筆才啟用"}
    use_w = all(s.get("wbgt") is not None for s in ok)
    X = [[1.0, s["speed"]] + ([s["wbgt"]] if use_w else []) for s in ok]
    y = [s["hr"] for s in ok]
    p = len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) for b in range(p)] for a in range(p)]
    bvec = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(p)]
    beta = _solve(A, bvec)
    if beta is None or beta[1] <= 0:
        return {"active": False, "sample_count": len(ok), "reason": "資料太集中（配速都差不多），還解不出心率和配速的關係"}
    if not use_w:
        beta.append(0.0)
    resid = [y[i] - sum(beta[j] * X[i][j] for j in range(p)) for i in range(len(X))]
    sd = (sum(e * e for e in resid) / max(1, len(resid) - p)) ** 0.5
    ref_w = median([s["wbgt"] for s in ok[-10:]]) if use_w else None
    return {"active": True, "beta": [round(b, 4) for b in beta], "sample_count": len(ok), "uses_wbgt": use_w,
            "ref_wbgt": ref_w and round(ref_w, 1), "resid_sd": round(sd, 1)}


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-9:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def wbgt(temp_c: float | None, rh: float | None) -> float | None:
    """簡化 WBGT（澳洲氣象局近似式，陰天、無風）。"""
    if temp_c is None or rh is None:
        return None
    e = rh / 100 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return round(0.567 * temp_c + 0.393 * e + 3.94, 1)


def derive_zones(diag: dict, goal: dict, phase: str, max_hr: float, model: dict | None) -> dict:
    cur = diag.get("cur_vdot") or 35.0
    tgt = cur
    if goal.get("target_s"):
        tgt = vdot(goal["distance_km"], goal["target_s"])
        tgt = min(tgt, cur * (1 + T.TARGET_VDOT_MAX_GAIN))
        tgt = max(tgt, cur * 0.9)
    q_vdot = {"base": cur, "build": (cur + tgt) / 2}.get(phase, tgt) if phase not in ("hold", "recover") else cur
    notes = []
    e_slow, e_fast = pace_at(cur, T.VDOT_FRACTION["E"][0]), pace_at(cur, T.VDOT_FRACTION["E"][1])
    hr = {k: round(max_hr * v) for k, v in T.HR_CAP.items()}
    e_source = "table"
    if model and model.get("active"):
        b0, b1, b2 = model["beta"]
        w = model.get("ref_wbgt") or 0
        speed = (hr["E"] - b0 - b2 * w) / b1
        if 1.2 < speed < 6.5:
            cal = 1000 / speed
            cal = max(cal, e_fast - T.REGRESSION_MAX_SPEEDUP_S)
            span = e_slow - e_fast
            e_fast, e_slow = cal, cal + span
            e_source = "regression"
            notes.append(f"輕鬆跑配速已用你 {model['sample_count']} 趟輕鬆跑的心率資料校正"
                         + (f"（以 WBGT {model['ref_wbgt']}°C 計）" if model.get("uses_wbgt") else "（還沒有天氣資料，未扣除溫濕度影響）"))
    elif diag.get("easy_too_fast") or "pacing" in diag["flags"]:
        if phase in ("base", "hold"):
            e_fast += T.EASY_SLOWDOWN_S
            e_slow += T.EASY_SLOWDOWN_S
            e_source = "offset"
            notes.append(f"你的輕鬆跑平常偏快：基礎期的輕鬆配速先往慢調 {T.EASY_SLOWDOWN_S} 秒/km，配合心率警示")
    m_pace = equivalent_time(q_vdot, 42.195) / 42.195
    z = {
        "E": {"min": round(e_fast), "max": round(e_slow), "hrCap": hr["E"], "alert": hr["E"] - T.HR_ALERT_MARGIN, "source": e_source},
        "M": {"min": round(m_pace - 5), "max": round(m_pace + 5), "hrCap": hr["M"], "alert": hr["M"] - T.HR_ALERT_MARGIN},
        "T": {"min": round(pace_at(q_vdot, T.VDOT_FRACTION["T"]) - 4), "max": round(pace_at(q_vdot, T.VDOT_FRACTION["T"]) + 4),
              "hrCap": hr["T"], "alert": hr["T"] - T.HR_ALERT_MARGIN},
        "I": {"min": round(pace_at(q_vdot, T.VDOT_FRACTION["I"]) - 4), "max": round(pace_at(q_vdot, T.VDOT_FRACTION["I"]) + 4),
              "hrCap": hr["I"], "alert": hr["I"] - T.HR_ALERT_MARGIN},
        "R": {"min": round(pace_at(q_vdot, T.VDOT_FRACTION["R"]) - 3), "max": round(pace_at(q_vdot, T.VDOT_FRACTION["R"]) + 3),
              "hrCap": None, "alert": None},
    }
    if goal.get("target_s"):
        rp = goal["target_s"] / goal["distance_km"]
        cap = hr["M"] if goal["distance_km"] >= 30 else hr["T"] if goal["distance_km"] >= 15 else hr["I"]
        z["RP"] = {"min": round(rp - 3), "max": round(rp + 3), "hrCap": cap, "alert": cap - T.HR_ALERT_MARGIN}
    else:
        z["RP"] = dict(z["T"] if goal["distance_km"] >= 15 else z["I"])
    return {"zones": z, "vdot_easy": round(cur, 1), "vdot_quality": round(q_vdot, 1), "notes": notes}


# ═════════════════════════════ L7 固定事件 ═════════════════════════════

MODE_RANK = {"rest": 6, "event": 5, "light": 4, "hrcap": 3, "easy": 2, None: 0}


def _post_days(spec: dict, km) -> int:
    if "post_by_km" in spec:
        km = float(km or 10)
        return next(d for lim, d in spec["post_by_km"] if km <= lim)
    return (spec.get("post") or (0, None))[0]


def expand_events(events: list[dict], goal: dict, plan_start: date, n_weeks: int, tests: list[dict] | None = None) -> dict:
    """事件 → {days: {date: (mode, why)}, weeks: {週一: {...}}, warnings}。

    重疊時取最強的限制（聯集），不是相加。"""
    days: dict[str, tuple[str, str]] = {}
    weeks: dict[str, dict] = {}
    warnings = []

    def put(d: date, mode: str, why: str):
        ds = d.isoformat()
        cur = days.get(ds)
        if not cur or MODE_RANK[mode] > MODE_RANK[cur[0]]:
            days[ds] = (mode, why)

    def wk(d: date) -> dict:
        return weeks.setdefault(monday(d).isoformat(), {})

    race = date.fromisoformat(goal["race_date"]) if goal.get("race_date") else None
    if race:
        put(race, "event", "目標賽")
        put(race - timedelta(days=1), "light", "賽前一天：短短動一下")
        for i in (2, 3):
            put(race - timedelta(days=i), "easy", "賽前三天不排強度、不排長跑")
    for ev in [*events, *[{**t, "_test": True} for t in (tests or [])]]:
        try:
            d0 = date.fromisoformat(ev["date"])
            d1 = date.fromisoformat(ev.get("end_date") or ev["date"])
        except (KeyError, ValueError, TypeError):
            continue
        spec = T.EVENT_TYPES.get(ev.get("type") or "race_b", T.EVENT_TYPES["race_b"])
        name = ev.get("name") or spec["label"]
        tag = f"{name}（{spec['label']}）"
        if race and not ev.get("_test") and timedelta(0) < race - d0 <= timedelta(days=T.A_RACE_GUARD_DAYS) and ev.get("type") != "travel":
            warnings.append(f"「{name}」落在目標賽前 3 週內：建議改期，或降為 C 級練習賽")
        t = ev.get("type")
        if t == "travel":
            for i in range((d1 - d0).days + 1):
                w = wk(d0 + timedelta(days=i))
                w["mult"] = min(w.get("mult", 1), T.EVENT_TYPES["travel"]["week_mult"])
                w.setdefault("why", []).append(f"{tag}：該週跑量打六折")
            continue
        if t == "illness":
            n = (d1 - d0).days + 1
            for i in range(n):
                put(d0 + timedelta(days=i), "rest", f"{name}：生病期間不跑")
            for i in range(n):
                put(d1 + timedelta(days=1 + i), "light", f"{name}：病後每病 1 天配 1 天輕量")
            continue
        if not ev.get("_test"):
            for i in range((d1 - d0).days + 1):
                put(d0 + timedelta(days=i), "event", tag)
        if spec.get("pre_week"):
            w = wk(d0 - timedelta(days=1))
            w.update({"max_runs": spec["pre_week"]["max_runs"], "no_quality": True, "no_long": True})
            w.setdefault("why", []).append(f"{tag} 前一週只跑 2 次輕鬆跑")
        pre = spec.get("pre")
        if pre and pre[1] == "taper":
            for i, f in enumerate(T.TAPER_CURVE):
                ws = monday(d0) - timedelta(weeks=len(T.TAPER_CURVE) - 1 - i)
                w = weeks.setdefault(ws.isoformat(), {})
                w["mult"] = min(w.get("mult", 1), f)
                w.setdefault("why", []).append(f"{tag} 前的減量（{round(f * 100)}%）")
            put(d0 - timedelta(days=1), "light", f"{tag} 前一天")
        elif pre:
            for i in range(1, pre[0] + 1):
                put(d0 - timedelta(days=i), pre[1], f"{tag} 前 {pre[0]} 天輕量")
        post_n = _post_days(spec, ev.get("distance_km"))
        for i in range(1, post_n + 1):
            mode = spec.get("post_mode") or (spec.get("post") or (0, "easy"))[1]
            if i == 1 and spec.get("post_first_rest"):
                mode = "rest"
            if i <= spec.get("post_hrcap_days", 0):
                mode = "hrcap"
            put(d1 + timedelta(days=i), mode, f"{tag} 後恢復（按{spec['damage']}評估）")
        if spec.get("week_after_mult"):
            w = wk(d1 + timedelta(days=1))
            w["mult"] = min(w.get("mult", 1), spec["week_after_mult"])
            w.setdefault("why", []).append(f"{tag} 後的恢復週")
        if t == "trail":
            for j, f in enumerate((0.5, 0.7, 0.85)):
                w = weeks.setdefault((monday(d1) + timedelta(weeks=1 + j)).isoformat(), {})
                w["mult"] = min(w.get("mult", 1), f)
                w.setdefault("why", []).append(f"{tag} 後第 {j + 1} 週恢復（{round(f * 100)}%）")
        if t in ("race_b", "race_c", "trail") and ev.get("distance_km") and not ev.get("_test"):
            w = wk(d0)
            w["race_km"] = w.get("race_km", 0) + float(ev["distance_km"])
    return {"days": days, "weeks": weeks, "warnings": warnings}


# ═════════════════════════════ L8 檢核點 ═════════════════════════════

def make_checkpoints(n_weeks: int, phases: list[str], goal: dict, plan_start: date) -> list[dict]:
    if not goal.get("race_date") or n_weeks < 6:
        return []
    pre = sum(1 for p in phases if p != "taper")
    out, used = [], set()
    target = goal.get("target_s")
    for spec in T.CHECKPOINTS:
        idx = max(1, min(pre - 1, int(round(n_weeks * spec["at"])) - 1))
        if idx in used or idx <= 0:
            continue
        used.add(idx)
        km = goal["distance_km"] / spec["div"]
        if spec["div"] > 1:
            km = max(T.MIN_TEST_KM, round(km, 1))
        cp = {"week": idx, "week_start": (plan_start + timedelta(weeks=idx)).isoformat(), "test_km": round(km, 2),
              "effort": spec["effort"], "purpose": spec["purpose"], "event_type": spec["event"],
              "key": bool(spec.get("key")), "label": f"測驗：{km:g} km {spec['effort']}"}
        if target:
            eq = riegel(target, goal["distance_km"], km)
            cp["equivalent_s"] = round(eq)
            cp["thresholds"] = [{"max_s": round(eq * (1 + b)), "verdict": v, "action": a, "text": txt}
                                for b, v, a, txt in T.CHECK_BANDS]
        out.append(cp)
    return out


def judge_checkpoint(cp: dict, seconds: float, km: float) -> dict:
    s = riegel(seconds, km, cp["test_km"]) if abs(km - cp["test_km"]) > 0.05 else seconds
    for th in cp.get("thresholds") or []:
        if s <= th["max_s"]:
            return {"seconds": round(s), **th}
    return {"seconds": round(s), "verdict": "完成", "action": "on_track", "text": "沒有設定目標成績，只用來重算配速"}


# ═════════════════════════════ L9 停損 ═════════════════════════════

def evaluate_stoploss(*, today: date, checkins: dict[str, dict], rhr: dict[str, float], week_completion: list[dict],
                      drifts: list[float], resume_after: str | None) -> list[dict]:
    """回傳觸發中的停損規則（優先於任何課表）。"""
    out = []
    # 1. 同一處疼痛連續 3 天
    sites = [(checkins.get((today - timedelta(days=i)).isoformat()) or {}).get("pain") for i in range(T.PAIN_DAYS)]
    if sites[0] and all(s == sites[0] for s in sites):
        since = (today - timedelta(days=T.PAIN_DAYS - 1)).isoformat()
        if not resume_after or resume_after < since:
            out.append({"rule": "pain", "site": sites[0], "label": T.PAIN_SITES.get(sites[0], sites[0]),
                        "title": f"{T.PAIN_SITES.get(sites[0], sites[0])}連續 {T.PAIN_DAYS} 天疼痛",
                        "action": "課表暫停：先停跑，建議就醫。好了之後按「恢復課表」。"})
    # 2. 晨間靜止心率 > 基準 +7 連續 3 天
    hist = [v for d, v in sorted(rhr.items()) if v and (today - timedelta(days=33)).isoformat() <= d < (today - timedelta(days=T.RHR_DAYS - 1)).isoformat()]
    recent = [rhr.get((today - timedelta(days=i)).isoformat()) for i in range(T.RHR_DAYS)]
    if len(hist) >= 5 and all(recent):
        base = median(hist)
        if all(v > base + T.RHR_DELTA for v in recent):
            out.append({"rule": "rhr", "title": f"晨間靜止心率連續 {T.RHR_DAYS} 天比基準 {round(base)} 高 {T.RHR_DELTA} 以上",
                        "action": f"本週剩下的跑量砍 {round(T.RHR_VOLUME_CUT * 100)}%，質量課改成輕鬆跑", "base": round(base)})
    # 3. 連續 2 週完成度 < 80%
    done = [w for w in week_completion if w.get("planned_km")]
    if len(done) >= T.COMPLETION_WEEKS and all(w["actual_km"] / w["planned_km"] < T.COMPLETION_MIN for w in done[-T.COMPLETION_WEEKS:]):
        out.append({"rule": "completion", "title": f"連續 {T.COMPLETION_WEEKS} 週完成度低於 {round(T.COMPLETION_MIN * 100)}%",
                    "action": "回到上一個階段重做 2 週", "weeks": [w["week_start"] for w in done[-T.COMPLETION_WEEKS:]]})
    # 4. 單次課漂移 > 10% 連續 3 次
    if len(drifts) >= T.DRIFT_RUNS and all(d is not None and d > T.DRIFT_OVERTRAIN for d in drifts[-T.DRIFT_RUNS:]):
        out.append({"rule": "drift", "title": f"最近 {T.DRIFT_RUNS} 趟輕鬆課心率漂移都超過 {round(T.DRIFT_OVERTRAIN * 100)}%",
                    "action": "視為過度訓練的早期訊號：插入一個減量週"})
    # 5. 錯過整週
    if done and done[-1].get("runs", 1) == 0 and done[-1]["planned_km"] > 0 and not done[-1].get("excused"):
        out.append({"rule": "missed_week", "title": "上週整週沒有跑", "action": "重跑那一週，後面的課表往後順延",
                    "week": done[-1]["week_start"]})
    return out


# ═════════════════════════════ 串起來：整季週表 ═════════════════════════════

QUALITY_KINDS = {"tempo", "intervals", "hills", "fartlek", "race_pace", "long_m", "test", "reps"}


def phase_quality(phase: str, diag: dict, goal: dict, week_in_phase: int, run_days: int,
                  level: str = "novice") -> tuple[list[str], str]:
    """L5 課表組成：這週有哪些質量課、長跑要不要加比賽配速。回傳 (質量課種類, 長跑種類)。

    依「目標距離」和「跑者程度」決定：
      新手：基礎期沒有強度課，進展期節奏跑，專項期才加間歇（線性週期）。
      有基礎：基礎期隔週一堂坡道衝刺；進展期節奏和間歇輪替。
      有經驗：從基礎期就每週一堂強度（混合週期），進展期兩堂，專項期最多三堂。
    """
    speed = "speed" in diag["flags"]
    dk = T.dist_key(goal["distance_km"])
    odd = week_in_phase % 2 == 1
    exp = level == "experienced"
    mid = level == "intermediate"
    long_kind = "long"
    if phase in ("hold", "recover"):
        return [], long_kind
    if phase == "race":
        return ["race_pace"], long_kind
    if phase == "base":
        if exp:
            return ([("hills" if dk in ("5k", "10k") else "tempo")] if not odd
                    else [("tempo" if dk in ("5k", "10k") else "hills")]), long_kind
        if mid:
            return (["hills"] if odd else []), long_kind
        return [], long_kind
    if phase == "build":
        if dk == "5k":
            q = ["intervals", "tempo"] if exp else (["tempo"] if odd else ["intervals"]) if mid else ["tempo"]
        elif dk == "10k":
            q = ["tempo", "intervals"] if exp else (["intervals"] if odd else ["tempo"]) if mid else ["tempo"]
        elif dk == "half":
            q = ["tempo", "fartlek" if odd else "hills"] if exp else (["fartlek"] if odd else ["tempo"]) if mid else ["tempo"]
        else:
            q = ["tempo"]
            if exp and odd:
                long_kind = "long_m"
        if speed and run_days >= 5 and len(q) < 2:
            q = q + ["tempo" if q[0] != "tempo" else "intervals"]
        return q, long_kind
    # specific / taper
    alt = "intervals" if week_in_phase % 2 == 0 else "tempo"
    if dk == "5k":
        q = ["intervals", "tempo"] if not odd else ["race_pace", "reps"]
        if exp:
            q = q + (["reps"] if not odd else ["tempo"])
    elif dk == "10k":
        q = ["intervals", "tempo"] if not odd else ["race_pace", "tempo"]
        if exp:
            q = q + ["reps"]
    elif dk == "half":
        if odd:
            q, long_kind = [alt], "long_m"           # 長跑末段比賽配速 → 算一堂質量課
        else:
            q = [alt, "race_pace"]
        if exp:
            q = q + (["intervals"] if "intervals" not in q else ["tempo"])
    else:
        if odd:
            q, long_kind = ["tempo"], "long_m"
        else:
            q = ["race_pace", "tempo"]
        if exp:
            q = q + ["intervals"]
    if speed and run_days >= 5 and len(q) < 3:
        q = q + [k for k in ("intervals", "tempo", "race_pace") if k not in q][:1]
    if level == "novice":
        q = q[:1] if long_kind == "long_m" else q[:2]
    if run_days <= 3:
        q = q[:1]
    elif run_days == 4:
        q = q[:2]
    return q, long_kind


def build_season(*, ath: dict, goal: dict, prefs: dict, snap: dict, today: date) -> dict:
    """整季的週表（不含每天的排程）。snap 是開季時凍結的狀態（見 season.py）。"""
    plan_start = date.fromisoformat(snap["plan_start"])
    diag = snap["diag"]
    open_mode = not goal.get("race_date")
    if open_mode:
        n = 12
    else:
        n = max(1, (monday(date.fromisoformat(goal["race_date"])) - plan_start).days // 7 + 1)
    level = snap.get("level") or "novice"
    phases = split_phases(n, diag, open_mode, goal["distance_km"], level)
    feas = snap.get("feasibility") or {}
    v_base = snap["v_base"]
    v_target = feas.get("v_target") or v_base * 1.15
    decision = snap.get("decision")
    hold = (feas.get("verdict") == "infeasible" and decision not in ("accept_risk",)) and not open_mode
    if feas.get("verdict") == "infeasible" and decision == "accept_risk":
        v_target = min(v_target, feas.get("v_reachable") or v_target)
    ins = {int(k): v for k, v in (snap.get("insertions") or {}).items()}
    freeze = snap.get("freeze_from")
    if hold:
        vols, tags = [v_base] * n, ["hold"] * n
        phases = ["hold"] * n
    else:
        vols, tags = ramp_volume(v_base, v_target, phases, diag, insertions=ins, freeze_from=freeze)
    # 插入「回到上一階段」的週：課表組成用上一階段
    for w, t in ins.items():
        if t == "phase_repeat" and 0 < w < len(phases):
            prev = next((phases[i] for i in range(w - 1, -1, -1) if phases[i] != phases[w]), phases[w])
            phases[w] = prev
            if w + 1 < len(phases) and phases[w + 1] != "taper":
                phases[w + 1] = prev
    pace_e = 360.0
    max_long_km = float(prefs.get("max_long_minutes") or 150) * 60 / pace_e
    longs = long_run_series(vols, phases, goal["distance_km"], snap.get("start_long_km") or v_base * 0.3, max_long_km)
    cps = [] if hold else make_checkpoints(n, phases, goal, plan_start)
    avail = sorted(int(d) for d in prefs.get("days") or [1, 3, 5, 6])
    lrd = int(prefs.get("long_run_day", 6))
    tests = []
    user_events = prefs.get("events") or []
    for cp in cps:
        wd = lrd if lrd in avail else max(avail)
        cp["date"] = (date.fromisoformat(cp["week_start"]) + timedelta(days=wd)).isoformat()
        # 附近剛好有 B/C 級比賽、距離也夠：直接拿那場比賽當測驗賽
        near = [e for e in user_events if e.get("type") in ("race_b", "race_c") and e.get("distance_km")
                and float(e["distance_km"]) >= cp["test_km"] * 0.8
                and abs((date.fromisoformat(e["date"]) - date.fromisoformat(cp["date"])).days) <= 10]
        if near:
            e = min(near, key=lambda e: abs((date.fromisoformat(e["date"]) - date.fromisoformat(cp["date"])).days))
            cp.update({"date": e["date"], "test_km": float(e["distance_km"]), "race_name": e.get("name") or "比賽",
                       "label": f"測驗賽：{e.get('name') or '比賽'}", "week_start": monday(date.fromisoformat(e["date"])).isoformat(),
                       "week": (monday(date.fromisoformat(e["date"])) - plan_start).days // 7})
            if goal.get("target_s"):
                eq = riegel(goal["target_s"], goal["distance_km"], cp["test_km"])
                cp["equivalent_s"] = round(eq)
                cp["thresholds"] = [{"max_s": round(eq * (1 + b)), "verdict": v, "action": a, "text": txt}
                                    for b, v, a, txt in T.CHECK_BANDS]
            continue
        tests.append({"date": cp["date"], "type": cp["event_type"], "name": cp["label"]})
    ev = expand_events(prefs.get("events") or [], goal, plan_start, n, tests)
    cp_by_week = {cp["week"]: cp for cp in cps if not cp.get("race_name")}
    weeks = []
    phase_counter: dict[str, int] = {}
    for i in range(n):
        ws = plan_start + timedelta(weeks=i)
        ph = phases[i]
        k = phase_counter.get(ph, 0)
        phase_counter[ph] = k + 1
        wm = ev["weeks"].get(ws.isoformat(), {})
        mult = wm.get("mult", 1)
        if tags[i] == "deload" and mult < 1:      # 減量週又遇到事件：取較強的那個，不相乘
            v = vols[i] / T.DELOAD_FACTOR * min(T.DELOAD_FACTOR, mult)
        else:
            v = vols[i] * mult
        is_race_week = bool(goal.get("race_date")) and ws <= date.fromisoformat(goal["race_date"]) <= ws + timedelta(days=6)
        notes = list(dict.fromkeys(wm.get("why", [])))
        if tags[i] == "deload":
            notes.append("減量週：跑量 × 0.7，下週從減量前的值繼續疊")
        if wm.get("race_km"):
            v = max(0, v - wm["race_km"])
            notes.append(f"這週的比賽 {wm['race_km']:g} km 算進週跑量")
        weeks.append({
            "idx": i, "week_start": ws.isoformat(), "phase": "race" if is_race_week else ph,
            "phase_label": T.PHASE_ZH["race" if is_race_week else ph], "week_in_phase": k,
            "target_km": round(v, 1), "curve_km": vols[i], "tag": tags[i], "long_km": longs[i],
            "max_runs": wm.get("max_runs"), "no_quality": wm.get("no_quality", False), "no_long": wm.get("no_long", False),
            "checkpoint": cp_by_week.get(i), "notes": notes,
        })
    # 有效訓練週數：被事件吃掉大半的週不算 → 重跑第 2 層
    lost = sum(1 for w in weeks if w["target_km"] < w["curve_km"] * 0.7 and w["phase"] not in ("taper", "race"))
    eff_warn = None
    if feas and not open_mode and lost and feas.get("verdict") in ("ok", "tight"):
        n_eff = n - lost
        if feas["w_need"] > n_eff:
            eff_warn = f"行程讓 {lost} 週的訓練量大減，有效訓練週數 {n_eff} 少於需要的 {feas['w_need']} 週：可行性變更"
        elif feas["w_need"] > n_eff * T.FEASIBLE_BUFFER and feas["verdict"] == "ok":
            eff_warn = f"行程讓 {lost} 週的訓練量大減，時間變得沒有緩衝"
    return {"mode": "open" if open_mode else "race", "hold": hold, "plan_start": plan_start.isoformat(), "n_weeks": n,
            "weeks": weeks, "checkpoints": cps, "event_days": ev["days"], "warnings": ev["warnings"] + ([eff_warn] if eff_warn else []),
            "diag": diag, "feasibility": feas, "v_base": v_base, "v_target": v_target, "level": level}
