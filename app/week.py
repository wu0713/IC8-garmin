"""第 5 層：把「這週跑 34 km」變成「週二跑什麼、週四跑什麼」。

步驟：
  1. 課表組成（engine.phase_quality）：幾堂質量課、哪一種、長跑要不要加比賽配速
  2. 放進可跑的日子：長跑 → 質量課 → 輕鬆跑，用回溯搜尋，每一步檢查硬約束
  3. 分配公里數，跑三個自檢：總量誤差 ≤ 5%、高強度時間 ≤ 20%、硬約束都滿足

也放了課表的產生器（每堂課的分段、配速、心率上限），每日排課、月曆預估、推手錶都用同一份。
"""
from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations, permutations

from . import plan_tables as T
from .engine import QUALITY_KINDS, fmt_pace, phase_quality

LONGISH = {"long", "long_m", "test"}
I_KINDS = {"intervals", "hills", "fartlek", "reps"}
KIND_ZH = {"easy": "輕鬆跑", "recovery": "恢復跑", "long": "長跑", "long_m": "長跑（末段比賽配速）",
           "tempo": "節奏跑", "intervals": "間歇跑", "hills": "坡道間歇", "fartlek": "變速跑", "reps": "快跑",
           "race_pace": "比賽配速跑", "test": "測驗", "progression": "漸進跑", "rest": "休息", "race": "比賽"}
INTRO = {"tempo": "進展期", "intervals": "專項期", "hills": "專項期", "fartlek": "專項期", "race_pace": "專項期",
         "long_m": "專項期", "reps": "專項期"}


# ───────────────────────────── 課表產生器 ─────────────────────────────

def _step(kind: str, zones: dict, zone: str | None, km: float | None = None, minutes: float | None = None,
          note: str = "", hr_cap: int | None = None) -> dict:
    s = {"type": kind, "note": note}
    z = zones.get(zone) if zone else None
    if z:
        s["pace_hi"], s["pace_lo"] = z["min"], z["max"]     # hi = 快、lo = 慢
        s["zone"] = zone
    cap = hr_cap if hr_cap is not None else (z or {}).get("hrCap")
    if cap:
        s["hr_cap"] = cap
    pace = ((z["min"] + z["max"]) / 2) if z else (zones["E"]["max"] + 15)
    if km is not None:
        s["km"] = round(km, 2)
        s["minutes"] = round(km * pace / 60, 1)
        s["by"] = "km"
    else:
        s["minutes"] = round(minutes, 2)
        s["km"] = round(minutes * 60 / pace, 2)
    return s


def _rep(n, *steps):
    return {"type": "repeat", "n": n, "steps": list(steps)}


def _flat(steps):
    for s in steps:
        if s["type"] == "repeat":
            for _ in range(s["n"]):
                yield from _flat(s["steps"])
        else:
            yield s


HARD_ZONES = {"T", "I", "R", "M", "RP", "TEST"}


def build_workout(kind: str, *, km: float = 0, zones: dict, week_km: float = 30, goal: dict | None = None,
                  scale: float = 1.0, strides: int = 0, hr_cap: int | None = None, m_km: float | None = None,
                  test: dict | None = None, why: str = "") -> dict:
    goal = goal or {"distance_km": 10}
    z = zones
    if kind == "rest":
        return {"kind": "rest", "title": "休息日", "intensity": "rest", "minutes": 0, "distance_km": 0, "steps": [],
                "summary": "完全休息，或散步、伸展、滾筒放鬆。", "why": why, "hard_minutes": 0}
    title = KIND_ZH.get(kind, kind)
    if kind in ("easy", "recovery"):
        cap = hr_cap or (z["E"]["hrCap"] - (6 if kind == "recovery" else 0))
        body = km - (0.8 if strides else 0)
        st = [_step("run", z, "E", km=max(1.0, body), note="輕鬆有氧，能完整講話", hr_cap=cap)]
        summary = f"{km:g} km 輕鬆跑，心率不超過 {cap}"
        if strides:
            st.append(_rep(strides, _step("interval", z, "R", minutes=20 / 60, note="快步：放鬆地快，練神經征召"),
                           _step("recovery", z, None, minutes=40 / 60, note="走路或慢跑")))
            title = "輕鬆跑＋快步"
            summary += f"，最後 {strides} × 20 秒快步"
        if kind == "recovery":
            summary = f"{km:g} km 超輕鬆，心率不超過 {cap}，目的是讓腿動一動"
        return _wrap(kind, title, "easy", st, summary + "。", why)
    if kind == "long":
        st = [_step("run", z, "E", km=km, note="穩定、舒服，可以練補給")]
        return _wrap(kind, "長跑", "moderate", st, f"{km:g} km 長跑，重點是時間不是速度；心率不超過 {z['E']['hrCap']}。", why)
    if kind == "long_m":
        m = m_km or max(3.0, min(12.0, round(km * 0.3)))
        st = [_step("run", z, "E", km=km - m, note="前段輕鬆"), _step("interval", z, "RP", km=m, note="最後一段跑比賽配速")]
        return _wrap(kind, KIND_ZH[kind], "hard", st,
                     f"{km:g} km 長跑，最後 {m:g} km 用比賽配速 {fmt_pace(z['RP']['min'] + 3)}/km。", why)

    wu, cd = 2.0, 1.5
    if kind == "tempo":
        block = max(3.0, min(10.0, week_km * 0.10)) * scale
        block = max(2.0, round(block * 2) / 2)
        if block >= 6:
            n = 3 if block >= 8 else 2
            each = round(block / n * 2) / 2
            st = [_step("warmup", z, "E", km=wu), _rep(n, _step("interval", z, "T", km=each, note="舒服的辛苦"),
                                                       _step("recovery", z, None, minutes=1, note="慢跑 1 分")),
                  _step("cooldown", z, "E", km=cd)]
            desc = f"{n} × {each:g} km 節奏跑 @ {fmt_pace(z['T']['min'])}–{fmt_pace(z['T']['max'])}/km，中間慢跑 1 分"
        else:
            st = [_step("warmup", z, "E", km=wu), _step("interval", z, "T", km=block, note="舒服的辛苦"), _step("cooldown", z, "E", km=cd)]
            desc = f"連續 {block:g} km 節奏跑 @ {fmt_pace(z['T']['min'])}–{fmt_pace(z['T']['max'])}/km"
        return _wrap(kind, title, "hard", st, desc + f"，心率上限 {z['T']['hrCap']}。", why)
    if kind == "intervals":
        tot = max(3.0, min(8.0, week_km * 0.08)) * scale
        rep = 1.0 if tot >= 4 else 0.8
        n = max(2, round(tot / rep))
        st = [_step("warmup", z, "E", km=wu), _rep(n, _step("interval", z, "I", km=rep, note="辛苦但每趟配速一致"),
                                                   _step("recovery", z, None, minutes=2, note="慢跑 2 分")),
              _step("cooldown", z, "E", km=cd)]
        return _wrap(kind, title, "hard", st, f"{n} × {int(rep * 1000)} m @ {fmt_pace(z['I']['min'])}–{fmt_pace(z['I']['max'])}/km，"
                     f"間休慢跑 2 分；心率到 {z['I']['hrCap']} 只該出現在每趟末段。", why)
    if kind == "hills":
        n = max(6, min(12, round(max(3.0, min(8.0, week_km * 0.08)) * scale * 1.5)))
        st = [_step("warmup", z, "E", km=wu + 0.5), _rep(n, _step("interval", z, None, minutes=1.25, note="上坡用力推（間歇強度）"),
                                                         _step("recovery", z, None, minutes=1.5, note="慢跑下坡")),
              _step("cooldown", z, "E", km=cd)]
        for s in _flat(st):
            if s["note"].startswith("上坡"):
                s["zone"] = "I"
        return _wrap(kind, title, "hard", st, f"找一段緩坡，{n} × 75 秒上坡，慢跑下坡恢復。", why)
    if kind == "fartlek":
        n = max(4, min(10, round(max(3.0, min(8.0, week_km * 0.08)) * scale * 1.6)))
        st = [_step("warmup", z, "E", km=wu), _rep(n, _step("interval", z, "I", minutes=2, note="快但可控"),
                                                   _step("recovery", z, "E", minutes=2, note="慢跑")),
              _step("cooldown", z, "E", km=cd)]
        return _wrap(kind, title, "hard", st, f"{n} × (2 分快 / 2 分慢)。", why)
    if kind == "reps":
        tot = max(2.0, min(4.0, week_km * 0.05)) * scale
        rep = 0.4 if tot >= 3 else 0.2
        n = max(4, min(12, round(tot / rep)))
        st = [_step("warmup", z, "E", km=wu, note="熱身，最後加 2–3 次快步"),
              _rep(n, _step("interval", z, "R", km=rep, note="快、放鬆、跑姿不走樣"),
                   _step("recovery", z, None, minutes=2 if rep >= 0.4 else 1, note="慢跑或走路，完全恢復")),
              _step("cooldown", z, "E", km=cd)]
        return _wrap(kind, "快跑（R）", "hard", st, f"{n} × {int(rep * 1000)} m @ {fmt_pace(z['R']['min'])}–{fmt_pace(z['R']['max'])}/km，"
                     "每趟之間完全恢復；練速度和跑步經濟性，不看心率。", why)
    if kind == "race_pace":
        if goal["distance_km"] >= 15:
            block = max(4.0, min(16.0, week_km * 0.15)) * scale
            n = 2 if block > 8 else 1
            each = round(block / n * 2) / 2
            main = (_rep(n, _step("interval", z, "RP", km=each, note="比賽配速，練節奏感"),
                         _step("recovery", z, None, minutes=3, note="慢跑 3 分")) if n > 1
                    else _step("interval", z, "RP", km=each, note="比賽配速，練節奏感"))
            desc = f"{n} × {each:g} km" if n > 1 else f"連續 {each:g} km"
        else:
            tot = max(3.0, min(6.0, week_km * 0.08)) * scale
            rep = 2.0 if goal["distance_km"] >= 8 else 1.0
            n = max(2, round(tot / rep))
            each = rep
            main = _rep(n, _step("interval", z, "RP", km=rep, note="比賽配速"), _step("recovery", z, None, minutes=2, note="慢跑 2 分"))
            desc = f"{n} × {rep:g} km"
        st = [_step("warmup", z, "E", km=wu), main, _step("cooldown", z, "E", km=cd)]
        return _wrap(kind, title, "hard", st, f"{desc} @ 目標配速 {fmt_pace(z['RP']['min'] + 3)}/km。", why)
    if kind == "test":
        t = test or {"test_km": km, "effort": "全力"}
        control = t.get("effort") == "控速"
        main = _step("interval", z, "RP" if control else None, km=t["test_km"],
                     note="照比賽配速控速，演練裝備與補給" if control else "全力跑完，跑完會依成績重算配速")
        main["zone"] = "TEST"
        st = [_step("warmup", z, "E", km=2.5, note="熱身，最後加 3–4 次快步"), main, _step("cooldown", z, "E", km=1.5)]
        eq = t.get("equivalent_s")
        return _wrap("test", f"🎯 {t.get('label') or '測驗'}", "hard", st,
                     f"{t['purpose'] if t.get('purpose') else '檢核點'}。" + (f"目標進度的等效成績約 {_fmt(eq)}。" if eq else ""), why)
    if kind == "progression":
        third = km / 3
        st = [_step("run", z, "E", km=third, note="輕鬆"), _step("run", z, "M", km=third, note="穩定"),
              _step("run", z, "T", km=third, note="最後一段帶快")]
        return _wrap(kind, title, "moderate", st, f"{km:g} km，分三段由慢到快。", why)
    raise ValueError(kind)


def build_to_km(kind: str, km: float, *, zones: dict, goal: dict | None = None, why: str = "", **kw) -> dict:
    """手動指定公里數的課（自己排課、手動調整）：質量課依指定的總距離縮放主課。"""
    if kind in ("easy", "recovery", "long", "long_m", "progression", "rest"):
        return build_workout(kind, km=km, zones=zones, goal=goal, why=why, **kw)
    w = build_workout(kind, zones=zones, week_km=km * 5, goal=goal, why=why)
    main = w["distance_km"] - 3.5
    if main > 0.5:
        w = build_workout(kind, zones=zones, week_km=km * 5, goal=goal, why=why,
                          scale=max(0.3, min(2.5, (km - 3.5) / main)))
    return w


def _fmt(s):
    s = int(round(s))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _wrap(kind, title, intensity, steps, summary, why):
    flat = list(_flat(steps))
    km = sum(s["km"] for s in flat)
    mins = sum(s["minutes"] for s in flat)
    hard = sum(s["minutes"] for s in flat if s.get("zone") in HARD_ZONES)
    main = next((s for s in flat if s["type"] == "interval" and s.get("pace_lo")), None) or next(
        (s for s in flat if s.get("pace_lo")), None)
    caps = [s["hr_cap"] for s in flat if s.get("hr_cap")]
    return {"kind": kind, "title": title, "intensity": intensity, "distance_km": round(km, 1), "minutes": round(mins),
            "steps": steps, "summary": summary, "why": why, "hard_minutes": round(hard, 1),
            "pace_range": [main["pace_hi"], main["pace_lo"]] if main else None,
            "hr_cap": max(caps) if caps else None}


# ───────────────────────────── 硬約束 ─────────────────────────────

def _violations(assign: dict[date, str], phase: str, cons_limit: int) -> list[str]:
    """assign：日期 → 課種（含上週最後幾天）。回傳違反的硬約束。"""
    bad = []
    runs = sorted(d for d, k in assign.items() if k not in ("rest", None))
    streak, prev = 0, None
    for d in runs:
        streak = streak + 1 if prev and (d - prev).days == 1 else 1
        prev = d
        if streak > cons_limit:
            bad.append(f"連續跑步超過 {cons_limit} 天")
            break
    q = sorted(d for d, k in assign.items() if k in QUALITY_KINDS)
    for a, b in zip(q, q[1:]):
        if (b - a).days < 2:
            bad.append("兩堂質量課間隔不到 48 小時")
    longs = [d for d, k in assign.items() if k in LONGISH]
    for L in longs:
        for off in (-1, 1):
            k = assign.get(L + timedelta(days=off))
            if k in QUALITY_KINDS:
                bad.append("長跑前一天或後一天排了質量課")
        if phase in ("specific", "taper"):
            for d, k in assign.items():
                if k in I_KINDS and 0 < abs((d - L).days) < 3:
                    bad.append("專項期的間歇和長跑間隔不到 72 小時")
    return bad


# 使用者在設定裡取消勾選的課，換成功能接近的課；都不喜歡就拿掉（只剩輕鬆跑＋快步也練得到）
SUBSTITUTE = {
    "reps": ("intervals", "hills", "fartlek"),
    "intervals": ("hills", "fartlek", "tempo"),
    "hills": ("fartlek", "intervals", "tempo"),
    "fartlek": ("hills", "tempo", "intervals"),
}


def _pick_variant(kind: str, liked: set[str], week_idx: int, phase: str = "") -> str | None:
    if kind not in SUBSTITUTE or kind in liked:
        return kind
    opts = [k for k in SUBSTITUTE[kind] if k in liked or k == "tempo"]
    if phase == "base":                        # 基礎期的強度課只是點綴：不喜歡就不排，不換成更硬的
        opts = [k for k in opts if k in ("hills", "fartlek")]
    if kind == "intervals":                   # 間歇不喜歡時，坡道和變速輪流
        alt = [k for k in ("hills", "fartlek") if k in liked]
        if alt:
            return alt[week_idx % len(alt)]
    return opts[0] if opts else None


# ───────────────────────────── 每週排程 ─────────────────────────────

def schedule_week(week: dict, ctx: dict) -> dict:
    """week：engine.build_season 的一週；ctx：可跑日、偏好、配速區間、事件、上週尾巴。"""
    ws = date.fromisoformat(week["week_start"])
    dates = [ws + timedelta(days=i) for i in range(7)]
    ev = ctx.get("event_days") or {}
    zones = ctx["zones"]
    diag = ctx["diag"]
    goal = ctx["goal"]
    phase = week["phase"]
    cons_limit = T.MAX_CONSECUTIVE_RUN_DAYS_CONSISTENCY if "consistency" in diag["flags"] else T.MAX_CONSECUTIVE_RUN_DAYS
    notes: list[str] = []
    mode = {d: (ev.get(d.isoformat()) or (None, ""))[0] for d in dates}
    cands = [d for d in dates if d.weekday() in ctx["avail"] and mode[d] not in ("rest", "event")]
    normal = {d for d in cands if mode[d] is None}
    tail = {date.fromisoformat(k): v for k, v in (ctx.get("prev_tail") or {}).items()}

    q_kinds, long_kind = phase_quality(phase, diag, goal, week["week_in_phase"], len(cands), ctx.get("level") or "novice")
    q_kinds = [k for k in dict.fromkeys(_pick_variant(k, ctx["liked"], week["idx"], phase) for k in q_kinds) if k]
    if week.get("no_quality") or ctx.get("novice"):
        q_kinds = []
        long_kind = "long" if long_kind == "long_m" else long_kind
    cp = week.get("checkpoint")
    long_slot = None if (week.get("no_long") or phase == "race" or ctx.get("novice")) else long_kind
    if cp:
        long_slot = "test"
        if long_kind == "long_m":
            long_kind = "long"
        q_kinds = q_kinds[:max(0, len(q_kinds) - 1)]     # 測驗本身就是一堂質量課
    max_runs = week.get("max_runs")
    pref_long = int(ctx.get("long_run_day", 6))

    def score(S, L, Q):
        s = 100 * len(S) + 60 * len(Q) + (40 if L else 0)
        if L:
            s += 20 if L.weekday() == pref_long else 8 if L.weekday() >= 5 else 0
        keys = sorted([*Q, *([L] if L else [])])
        s += sum(min((b - a).days, 4) for a, b in zip(keys, keys[1:])) * 2
        return s

    best = None
    target_q = len(q_kinds)
    for drop in range(0, min(4, len(cands)) + 1):
        for removed in combinations(cands, drop):
            S = [d for d in cands if d not in removed]
            if max_runs and len(S) > max_runs:
                continue
            base_assign = {**tail, **{d: "easy" for d in S}}
            if _violations(base_assign, phase, cons_limit):
                continue
            Ls = [d for d in S if d in normal] if long_slot else [None]
            if cp and cp.get("date"):
                Ls = [d for d in Ls if d and d.isoformat() == cp["date"]] or Ls
            for nq in range(target_q, -1, -1):
                for L in sorted(Ls, key=lambda d: (d is None or d.weekday() != pref_long, -(d.weekday() if d else 0))):
                    rest_days = [d for d in S if d != L and d in normal]
                    for Q in combinations(rest_days, nq):
                        for perm in (set(permutations(q_kinds[:nq])) or {()}):
                            a = dict(base_assign)
                            if L:
                                a[L] = long_slot
                            for d, k in zip(Q, perm):
                                a[d] = k
                            if _violations(a, phase, cons_limit):
                                continue
                            sc = score(S, L, Q)
                            if not best or sc > best[0]:
                                best = (sc, S, L, dict(zip(Q, perm)))
                if best and len(best[3]) == nq:
                    break
            if best and len(best[3]) == target_q and (best[2] or not long_slot):
                break
        if best and len(best[3]) == target_q and (best[2] or not long_slot):
            break
    if not best:
        return {"days": {}, "notes": ["這週沒有可以跑的日子"], "check": {"ok": True}}
    _, S, L, Q = best
    if len(Q) < target_q:
        notes.append(f"可跑的日子排不下 {target_q} 堂質量課又同時滿足間隔規則，本週少排 {target_q - len(Q)} 堂")
    if long_slot and not L:
        notes.append("可跑的日子沒有適合放長跑的一天")
    if len(S) < len(cands):
        notes.append(f"為了連續跑步不超過 {cons_limit} 天，{len(cands) - len(S)} 個可跑日改休息")

    return _distribute(week, ctx, S, L, long_slot, Q, mode, notes, cons_limit, tail)


def _distribute(week, ctx, S, L, long_slot, Q, mode, notes, cons_limit, tail):
    zones, goal = ctx["zones"], ctx["goal"]
    V = week["target_km"]
    phase = week["phase"]
    e_pace = (zones["E"]["min"] + zones["E"]["max"]) / 2
    max_wd_km = float(ctx.get("max_minutes_weekday") or 60) * 60 / e_pace
    min_e_km = max(3.0, T.MIN_EASY_MINUTES * 60 / e_pace)
    scale = 0.5 if phase == "taper" else 0.4 if phase == "race" else 1.0
    cp = week.get("checkpoint")
    ph_zh = T.PHASE_ZH.get(phase, phase)

    def why_q(k):
        return f"{ph_zh}第 {week['week_in_phase'] + 1} 週的質量課（{KIND_ZH[k]}從{INTRO.get(k, '進展期')}開始解鎖）"

    for _ in range(4):
        fixed: dict[date, dict] = {}
        if L:
            if long_slot == "test":
                fixed[L] = build_workout("test", zones=zones, test=cp, goal=goal, why="檢核點：測驗日")
            else:
                fixed[L] = build_workout(long_slot, km=week["long_km"], zones=zones, goal=goal,
                                         why=f"長跑 = min(週量 × ρ, 比賽距離 × κ, 上週 + 2 km) = {week['long_km']:g} km")
        for d, k in Q.items():
            fixed[d] = build_workout(k, zones=zones, week_km=V, goal=goal, scale=scale, why=why_q(k))
        easy = [d for d in S if d not in fixed]
        rest_km = V - sum(w["distance_km"] for w in fixed.values())
        # 量不夠分：減少輕鬆跑天數（與其每天 2 km 不如休息）
        while easy and rest_km / len(easy) < min_e_km and len(easy) > 1:
            easy.pop(len(easy) // 2)
        per = max(min_e_km if easy else 0, rest_km / len(easy)) if easy else 0
        out = dict(fixed)
        stride_quota = (1 if Q else 2) if phase == "base" else 0 if phase in ("taper", "race", "hold", "recover") else 1
        for d in sorted(easy):
            m = mode[d]
            km = min(per, max_wd_km)
            if m == "light":
                km = min(km, max(min_e_km, 5.0))
            km = round(km * 2) / 2
            nxt = d + timedelta(days=1)
            use_strides = ("strides" in ctx.get("liked", {"strides"}) and stride_quota > 0 and m is None and nxt != L and km >= 5 and not ctx.get("novice"))
            if use_strides:
                stride_quota -= 1
            hr = round(ctx["max_hr"] * 0.70) if m == "hrcap" else None
            kind = "recovery" if m in ("light", "hrcap") else "easy"
            reason = (ev_why(ctx, d) if m else "其餘跑量平均分給輕鬆跑")
            out[d] = build_workout(kind, km=km, zones=zones, strides=6 if use_strides else 0, hr_cap=hr, why=reason)
        total_min = sum(w["minutes"] for w in out.values())
        hard_min = sum(w["hard_minutes"] for w in out.values())
        share = hard_min / total_min if total_min else 0
        if share <= T.HARD_TIME_SHARE_MAX or scale <= 0.4 or not Q:
            break
        scale *= 0.8
    if share > T.HARD_TIME_SHARE_MAX:
        notes.append(f"高強度時間佔 {round(share * 100)}%，超過 20%" + ("（測驗週，測驗本身就是全力）" if cp else "：質量課已縮到最短"))
    elif scale < (0.5 if phase == "taper" else 0.4 if phase == "race" else 1.0):
        notes.append("高強度時間原本超過 20%：質量課主課已縮短")
    total = sum(w["distance_km"] for w in out.values())
    assign = {**tail, **{d: w["kind"] for d, w in out.items()}}
    viol = _violations(assign, phase, cons_limit)
    err = (total - V) / V if V else 0
    if V and abs(err) > T.VOLUME_TOLERANCE:
        if err < 0:
            notes.append(f"排出 {round(total, 1)} km，比目標 {V:g} km 少 {round(-err * 100)}%（可跑的日子或時間不夠）")
        else:
            notes.append(f"排出 {round(total, 1)} km，比目標 {V:g} km 多 {round(err * 100)}%（每堂課有最短距離）")
    return {"days": {d.isoformat(): w for d, w in out.items()}, "notes": notes,
            "check": {"total_km": round(total, 1), "target_km": V, "volume_ok": abs(err) <= T.VOLUME_TOLERANCE or not V,
                      "hard_share": round(share, 3), "hard_ok": share <= T.HARD_TIME_SHARE_MAX, "violations": viol}}


def ev_why(ctx, d: date) -> str:
    v = (ctx.get("event_days") or {}).get(d.isoformat())
    return v[1] if v else ""
