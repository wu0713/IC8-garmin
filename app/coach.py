"""教練判斷：跑後依課型評分、疲勞評估、改課選項、個人基線、身體狀況處理建議。

晨報、晚報、跑後報告、身體狀況填寫都用這裡的同一套規則，避免各說各話。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import mean, median, pstdev

from . import db, metrics
from . import plan_tables as T

QUALITY = {"tempo", "intervals", "hills", "fartlek", "reps", "race_pace", "long_m", "test", "progression"}
KIND_GROUP = {"easy": "easy", "recovery": "easy", "long": "long", "tempo": "tempo", "progression": "tempo",
              "intervals": "intervals", "hills": "intervals", "fartlek": "intervals", "reps": "intervals",
              "race_pace": "race_pace", "long_m": "race_pace", "test": "race_pace"}
PTS = {"good": 100, "ok": 70, "bad": 40}


def fmt_pace(s: float | None) -> str:
    if not s:
        return "—"
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


# ───────────────────────────── 個人基線 ─────────────────────────────

def band(values: list[float | None], k: float = 1.0, need: int = 7) -> dict | None:
    """個人正常範圍：近 28 天的平均 ± k 個標準差（標準差太小時至少給 ±2%）。"""
    v = [x for x in values if x is not None]
    if len(v) < need:
        return None
    m = mean(v)
    sd = max(pstdev(v), abs(m) * 0.02, 0.5)
    return {"mean": round(m, 1), "sd": round(sd, 2), "lo": round(m - k * sd, 1), "hi": round(m + k * sd, 1), "n": len(v)}


# ───────────────────────────── 改課選項 ─────────────────────────────

def _plan_rows(uid: int, start: str, end: str) -> dict[str, dict]:
    return {r["date"]: json.loads(r["data"]) for r in db.q("SELECT date, data FROM plan WHERE user_id=? AND date BETWEEN ? AND ?",
                                                             (uid, start, end))}


def _swap_target(uid: int, d: date) -> str | None:
    """找 1–3 天後的一天輕鬆跑／休息日來對調：對調後兩邊都不能貼著長跑或另一堂強度課。"""
    rows = _plan_rows(uid, (d - timedelta(days=1)).isoformat(), (d + timedelta(days=5)).isoformat())
    kind = lambda x: (rows.get(x.isoformat()) or {}).get("kind")  # noqa: E731
    for off in (1, 2, 3):
        t = d + timedelta(days=off)
        if kind(t) not in ("easy", "recovery", "rest"):
            continue
        neigh = [kind(t + timedelta(days=i)) for i in (-1, 1) if t + timedelta(days=i) != d]
        if any(k in QUALITY or k in ("long", "long_m") for k in neigh):
            continue
        return t.isoformat()
    return None


def day_options(uid: int, ds: str, severity: str, plan: dict | None = None) -> list[dict]:
    """某一天的課可以怎麼改。severity：trim（小幅）、reduce（減量）、rest（休息）。

    每個選項附 apply：前端直接打 /api/override 或 /api/swap。第一個是建議的做法。"""
    plan = plan if plan is not None else (_plan_rows(uid, ds, ds).get(ds))
    if not plan or plan.get("kind") in (None, "rest", "done", "race", "event", "strength"):
        return []
    k = plan["kind"]
    km = plan.get("distance_km") or round((plan.get("minutes") or 40) / 6, 1)
    title = plan.get("title") or "這堂課"
    r1 = lambda x: max(2.0, round(x * 2) / 2)  # noqa: E731
    opts = []

    def opt(oid, label, detail, apply):
        opts.append({"id": oid, "label": label, "detail": detail, "apply": apply})

    ov = lambda kind, kmv: {"type": "override", "date": ds, "kind": kind, "km": kmv}  # noqa: E731
    if severity == "rest":
        opt("rest", "今天休息", f"「{title}」先不跑，課表後面會自動接上。", ov("rest", None))
        opt("recovery", f"改成 {r1(min(km, 4))} km 恢復跑", "只讓腿動一動，心率壓低。", ov("recovery", r1(min(km, 4))))
        return opts
    if k in QUALITY:
        sw = _swap_target(uid, date.fromisoformat(ds))
        if severity == "reduce":
            opt("easy", f"改成 {r1(km * 0.75)} km 輕鬆跑", f"強度課先不練，「{title}」等身體回來再說。", ov("easy", r1(km * 0.75)))
            if sw:
                opt("swap", f"和 {sw[5:].replace('-', '/')} 對調", f"「{title}」延後到 {sw[5:].replace('-', '/')}，今天跑那天的輕鬆課。",
                    {"type": "swap", "a": ds, "b": sw})
        else:
            opt("trim", f"主課縮短，總共 {r1(km * 0.8)} km", "強度照練，但趟數或距離少兩成。", ov(k if k != "long_m" else "long", r1(km * 0.8)))
            if sw:
                opt("swap", f"和 {sw[5:].replace('-', '/')} 對調", f"多休一天再練「{title}」。", {"type": "swap", "a": ds, "b": sw})
    elif k in ("long",):
        f = 0.7 if severity == "reduce" else 0.85
        opt("short", f"長跑縮短到 {r1(km * f)} km", "配速再放慢，心率守住上限。", ov("long", r1(km * f)))
    else:
        f = 0.7 if severity == "reduce" else 0.85
        opt("short", f"縮短到 {r1(km * f)} km", "一樣輕鬆跑，只是短一點。", ov("recovery" if severity == "reduce" else k, r1(km * f)))
    opt("keep", "照原本的課", "我覺得還可以。", {"type": "keep"})
    return opts


# ───────────────────────────── 跑後：依課型評分 ─────────────────────────────

def _segments(pts: list, thr: float, min_s: int = 30) -> list[dict]:
    """配速快於 thr 的連續區段（間歇的每一趟）。"""
    segs, cur = [], []
    for p in pts:
        fast = p.get("pace") and p["pace"] < thr
        if fast:
            cur.append(p)
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    out = []
    for s in segs:
        dur = s[-1]["t"] - s[0]["t"]
        if dur < min_s:
            continue
        hrs = [p["hr"] for p in s if p.get("hr")]
        out.append({"start": s[0]["t"], "end": s[-1]["t"], "pace": mean(p["pace"] for p in s),
                    "hr": mean(hrs) if hrs else None, "hr_end": hrs[-1] if hrs else None})
    return out


def _fastest_block(pts: list, minutes: float) -> list:
    """配速最快、連續 minutes 分鐘的一段（節奏跑、比賽配速的主課）。"""
    good = [p for p in pts if p.get("pace") and p["pace"] < 900]
    if len(good) < 6 or minutes <= 0:
        return []
    best, bi, j = None, 0, 0
    for i in range(len(good)):
        while j < len(good) and good[j]["t"] - good[i]["t"] < minutes * 60:
            j += 1
        if j >= len(good):
            break
        seg = good[i:j]
        avg = mean(p["pace"] for p in seg)
        if best is None or avg < best:
            best, bi = avg, i
    if best is None:
        return []
    out = [p for p in good if good[bi]["t"] <= p["t"] <= good[bi]["t"] + minutes * 60]
    return out


def _drift(pts: list) -> float | None:
    hp = [p for p in pts if p.get("hr") and p.get("pace") and p["pace"] < 900 and p["t"] > 300]
    if len(hp) < 20:
        return None
    h = len(hp) // 2
    ef1 = mean(1000 / p["pace"] / p["hr"] for p in hp[:h])
    ef2 = mean(1000 / p["pace"] / p["hr"] for p in hp[h:])
    return round((ef1 - ef2) / ef1, 4)


def _main_step(plan: dict) -> dict | None:
    def flat(steps):
        for s in steps or []:
            if s.get("type") == "repeat":
                yield from flat(s.get("steps"))
            else:
                yield s
    st = list(flat(plan.get("steps")))
    return next((s for s in st if s.get("type") == "interval" and s.get("pace_lo")), None) or next(
        (s for s in st if s.get("pace_lo")), None)


ZONE_OF = {"easy": "E", "recovery": "E", "long": "E", "tempo": "T", "progression": "T", "intervals": "I", "hills": "I",
           "fartlek": "I", "reps": "R", "race_pace": "RP", "long_m": "RP", "test": "RP"}


def _zone_fallback(uid: int, kind: str, ds: str) -> dict | None:
    """舊的課表紀錄沒有分段配速：用排課引擎現在的配速區間補上。"""
    try:
        from . import season
        cx = season.Ctx(uid)
        w = cx.week(cx.week_idx(date.fromisoformat(ds)))
        z = cx.zones(w["phase"] if w else "base")["zones"].get(ZONE_OF.get(kind, "E"))
    except Exception:
        return None
    if not z:
        return None
    return {"pace_hi": z["min"], "pace_lo": z["max"], "hr_cap": z.get("hrCap"), "zone": ZONE_OF.get(kind, "E")}


def _easy_ef_history(uid: int, before: str) -> list[float]:
    rows = db.q("SELECT distance_m, duration_s, avg_hr FROM activities WHERE user_id=? AND date<? AND date>=? "
                "AND avg_hr IS NOT NULL ORDER BY date DESC LIMIT 30",
                (uid, before, (date.fromisoformat(before) - timedelta(days=42)).isoformat()))
    out = []
    for r in rows:
        if r["duration_s"] and r["distance_m"] and r["duration_s"] >= 20 * 60:
            out.append(r["distance_m"] / (r["duration_s"] / 60) / r["avg_hr"])
    return out


def review(uid: int, a: dict, pts: list, plan: dict | None, max_hr: float, zone: str) -> dict:
    """依課型評分。回傳 {score, headline, group, items, strengths, improve}。"""
    items = []

    def add(key, label, value, verdict, text, tip=None):
        items.append({"key": key, "label": label, "value": value, "verdict": verdict, "text": text, "tip": tip})

    mins = (a.get("duration_s") or 0) / 60
    km_done = (a.get("distance_m") or 0) / 1000
    kind = (plan or {}).get("kind")
    if kind in ("rest", "done", "race", "strength", "event", None):
        plan, kind = None, None
    group = KIND_GROUP.get(kind) if kind else ("easy" if zone == "easy" else "tempo" if zone == "moderate" else "intervals")
    if kind is None and mins >= 75 and zone != "hard":
        group = "long"
    main = _main_step(plan) if plan else None
    if plan and not main:
        main = _zone_fallback(uid, kind, a["date"])
    hr_cap = (plan or {}).get("hr_cap") or (main or {}).get("hr_cap") if group in ("easy", "long") else None
    hr_cap = hr_cap or round(T.HR_CAP["E"] * max_hr)

    # 完成度：有排公里數就看距離，否則看時間
    if plan and (plan.get("distance_km") or plan.get("minutes")):
        if plan.get("distance_km"):
            ratio, txt = km_done / plan["distance_km"], f"計畫 {plan['distance_km']:g} km，實際 {km_done:.1f} km。"
        else:
            ratio, txt = mins / plan["minutes"], f"計畫 {round(plan['minutes'])} 分鐘，實際 {round(mins)} 分鐘。"
        v = "good" if 0.9 <= ratio <= 1.12 else "ok" if 0.7 <= ratio <= 1.3 else "bad"
        add("volume", "完成度", f"{round(ratio * 100)}%", v, txt,
            None if v == "good" else ("時間不夠時，寧可縮短主課也別跳過熱身。" if ratio < 0.9 else "多跑的量會吃掉後面幾天的恢復，下次照計畫就好。"))

    hr_pts = [p["hr"] for p in pts if p.get("hr")]
    drift = _drift(pts) if mins >= 30 else None

    if group in ("easy", "long"):
        if hr_pts:
            under = sum(1 for h in hr_pts if h <= hr_cap + 3) / len(hr_pts)     # 手錶心率有誤差，容許 3 下
            v = "good" if under >= 0.85 else "ok" if under >= 0.65 else "bad"
            add("hr_cap", "心率守住上限", f"{round(under * 100)}% ≤ {hr_cap}", v,
                "輕鬆跑和長跑的重點是心率，不是配速。", None if v == "good" else
                "放慢到能完整講話；上坡用走的也可以。輕鬆跑跑太快，強度課就沒力氣練到位。")
        if drift is not None:
            v = "good" if drift < 0.05 else "ok" if drift < 0.08 else "bad"
            add("drift", "有氧耐力（心率漂移）", f"{round(drift * 100, 1)}%", v,
                "同樣配速下後半心率高了多少。5% 以內代表這個距離對你的有氧能力來說是輕鬆的。",
                None if v == "good" else "漂移偏高：有氧底子還在累積，或是天氣熱、沒補水。長跑記得帶水。")
        if a.get("avg_hr") and a.get("duration_s") and a.get("distance_m") and mins >= 20:
            ef = a["distance_m"] / (a["duration_s"] / 60) / a["avg_hr"]
            hist = _easy_ef_history(uid, a["date"])
            if len(hist) >= 4:
                ref = median(hist)
                ch = ef / ref - 1
                v = "good" if ch >= 0.01 else "ok" if ch >= -0.04 else "bad"
                add("ef", "有氧效率", f"{'+' if ch >= 0 else ''}{round(ch * 100, 1)}%", v,
                    "同樣心率能跑多快，和你近 6 週比。持續變好代表有氧能力在進步。",
                    None if v != "bad" else "明顯變差通常是疲勞、睡不好或天氣熱；連續幾次都這樣就該安排減量。")
        if main and plan and hr_pts and a.get("duration_s") and km_done:
            pace = a["duration_s"] / km_done
            if pace < main["pace_hi"] - 10:
                add("too_fast", "配速", f"{fmt_pace(pace)}/km", "ok",
                    f"比建議的輕鬆配速（{fmt_pace(main['pace_hi'])}–{fmt_pace(main['pace_lo'])}）快。心率有守住就沒關係。")
    elif group == "tempo":
        need = max(10.0, (plan or {}).get("hard_minutes") or 20)
        blk = _fastest_block(pts, need)
        if blk:
            bp = mean(p["pace"] for p in blk)
            bh = [p["hr"] for p in blk if p.get("hr")]
            if main:
                lo, hi = main["pace_hi"], main["pace_lo"]
                if bp < lo - 5:
                    v, txt = "ok", f"比目標 {fmt_pace(lo)}–{fmt_pace(hi)} 快。節奏跑跑太快會變成比賽，練不到閾值。"
                elif bp <= hi + 5:
                    v, txt = "good", f"在目標 {fmt_pace(lo)}–{fmt_pace(hi)} 內。"
                else:
                    v, txt = ("ok" if bp <= hi + 15 else "bad"), f"比目標 {fmt_pace(lo)}–{fmt_pace(hi)} 慢。"
                add("main_pace", f"主課配速（{round(need)} 分）", f"{fmt_pace(bp)}/km", v, txt,
                    None if v == "good" else "用手錶的配速警示，主課前 1 公里特別注意別衝。")
            if bh and main and main.get("hr_cap"):
                over = sum(1 for h in bh if h > main["hr_cap"]) / len(bh)
                v = "good" if over <= 0.15 else "ok" if over <= 0.35 else "bad"
                add("main_hr", "主課心率", f"平均 {round(mean(bh))}", v,
                    f"節奏跑的心率上限 {main['hr_cap']}，超過的時間 {round(over * 100)}%。",
                    None if v == "good" else "心率一直超過上限，代表目前的節奏配速對你偏快，或是今天累了。")
            if len(blk) >= 12:
                d2 = _drift(blk)
                if d2 is not None:
                    v = "good" if d2 < 0.04 else "ok" if d2 < 0.08 else "bad"
                    add("main_drift", "主課後段", f"漂移 {round(d2 * 100, 1)}%", v, "主課後段心率爬升越少，代表閾值能力越好。")
    elif group == "intervals":
        thr = None
        if main:
            easy = next((s for s in (plan.get("steps") or []) if s.get("zone") == "E" and s.get("pace_lo")), None)
            thr = (main["pace_lo"] + (easy["pace_lo"] if easy else main["pace_lo"] * 1.25)) / 2
        else:
            ps = sorted(p["pace"] for p in pts if p.get("pace") and p["pace"] < 900)
            thr = ps[len(ps) // 2] * 0.93 if ps else None
        reps = _segments(pts, thr) if thr else []
        if len(reps) >= 3:
            rp = [r["pace"] for r in reps]
            cv = pstdev(rp) / mean(rp)
            fade = mean(rp[-2:]) / mean(rp[:2]) - 1
            v = "good" if cv < 0.03 and fade < 0.03 else "ok" if cv < 0.06 and fade < 0.06 else "bad"
            add("reps", f"每趟一致性（{len(reps)} 趟）", f"±{round(cv * 100, 1)}%", v,
                f"最快 {fmt_pace(min(rp))}、最慢 {fmt_pace(max(rp))}；最後兩趟比前兩趟{'慢' if fade > 0 else '快'} {abs(round(fade * 100, 1))}%。",
                None if v == "good" else "前幾趟收一點，最後一趟還能維持同樣配速才是剛好的強度。")
            if main:
                lo, hi = main["pace_hi"], main["pace_lo"]
                avg = mean(rp)
                v = "good" if lo - 6 <= avg <= hi + 6 else "ok" if avg <= hi + 15 else "bad"
                if avg < lo - 6:
                    v = "ok"
                add("rep_pace", "間歇配速", f"{fmt_pace(avg)}/km", v,
                    f"目標 {fmt_pace(lo)}–{fmt_pace(hi)}。" + ("跑太快會變成無氧衝刺，恢復也會拖到後面幾天。" if avg < lo - 6 else ""))
            drops = []
            for r in reps[:-1]:
                after = [p["hr"] for p in pts if p.get("hr") and r["end"] + 50 <= p["t"] <= r["end"] + 70]
                if after and r.get("hr_end"):
                    drops.append(r["hr_end"] - mean(after))
            if drops:
                dd = mean(drops)
                v = "good" if dd >= 20 else "ok" if dd >= 12 else "bad"
                add("hr_recovery", "組間心率恢復", f"1 分鐘降 {round(dd)}", v,
                    "每趟結束後 1 分鐘心率降多少。降得越快，代表心肺恢復能力越好、也越不累。",
                    None if v != "bad" else "恢復慢：今天可能本來就累，或間歇休息太短。")
        elif plan:
            add("reps", "每趟一致性", "—", "ok", "從心率和配速曲線切不出每一趟（GPS 不穩、跑步機或休息用走的都可能）。")
    elif group == "race_pace":
        rp_min = max(8.0, ((plan or {}).get("hard_minutes") or 15))
        blk = _fastest_block(pts, rp_min)
        if blk and main:
            bp = mean(p["pace"] for p in blk)
            lo, hi = main["pace_hi"], main["pace_lo"]
            v = "good" if lo - 5 <= bp <= hi + 5 else "ok" if bp <= hi + 15 else "bad"
            add("rp", "比賽配速段", f"{fmt_pace(bp)}/km", v, f"目標 {fmt_pace(lo)}–{fmt_pace(hi)}。")
            bh = [p["hr"] for p in blk if p.get("hr")]
            if bh:
                add("rp_hr", "比賽配速時的心率", f"平均 {round(mean(bh))}", "good" if not main.get("hr_cap") or mean(bh) <= main["hr_cap"] else "ok",
                    "記下這個心率，比賽當天如果同樣配速心率高很多，就要保守一點。")

    cad = a.get("avg_cadence")
    if cad and cad >= 130 and "treadmill" not in (a.get("type") or ""):
        add("cadence", "步頻", f"{round(cad)} spm", "info", "參考就好：步頻跟速度和身高都有關，輕鬆跑 160 出頭很常見。")

    scored = [i for i in items if i["verdict"] in PTS]
    if len(scored) < 2:
        why = "沒有心率曲線或每公里資料" if not pts else "距離太短或沒有對應的課表"
        return {"score": None, "group": group, "headline": f"這趟資料不夠做完整評分（{why}）", "items": items, "strengths": [],
                "improve": [{"label": i["label"], "tip": i["tip"]} for i in items if i["tip"]]}
    score = round(mean(PTS[i["verdict"]] for i in scored))
    GZ = {"easy": "輕鬆跑", "long": "長跑", "tempo": "節奏跑", "intervals": "間歇", "race_pace": "比賽配速課"}
    gz = GZ.get(group, "這堂課")
    head = (f"{gz}練得很到位" if score >= 90 else f"{gz}整體不錯，有一兩個地方可以更好" if score >= 75
            else f"{gz}有完成，下面幾點值得注意" if score >= 60 else f"這堂{gz}跑得有點掙扎")
    bad = [i for i in scored if i["verdict"] == "bad"]
    okk = [i for i in scored if i["verdict"] == "ok"]
    return {"score": score, "group": group, "headline": head, "items": items,
            "strengths": [i["label"] for i in scored if i["verdict"] == "good"],
            "improve": [{"label": i["label"], "tip": i["tip"]} for i in bad + okk if i["tip"]]}


# ───────────────────────────── 跑後：疲勞與明天 ─────────────────────────────

def fatigue(uid: int, a: dict, rev: dict, plan: dict | None) -> dict:
    """這堂課讓你有多累，會不會影響明天。"""
    prefs = db.get_prefs(uid)
    ds = a["date"]
    d = date.fromisoformat(ds)
    start = d - timedelta(days=60)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? AND date<=?", (uid, start.isoformat(), ds))
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=? AND date<=?", (uid, start.isoformat(), ds))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? ORDER BY date", (uid, start.isoformat()))
    rest, mx = metrics.estimate_rest_hr(daily), metrics.estimate_max_hr(acts, prefs)
    load = metrics.trimp(a.get("duration_s"), a.get("avg_hr"), rest, mx, prefs.get("sex", "M"))
    loads = metrics.daily_loads(acts, daily, prefs, start, d, cross)
    prev = [v for k, v in sorted(loads.items()) if k < ds and v > 0][-28:]
    typical = mean(prev) if prev else None
    pts, why = 0, []
    rel = load / typical if typical and load else None
    if rel:
        if rel >= 1.8:
            pts += 2
            why.append(f"負荷是你平常一次運動的 {rel:.1f} 倍")
        elif rel >= 1.3:
            pts += 1
            why.append(f"負荷是平常的 {rel:.1f} 倍")
    it = {i["key"]: i for i in rev.get("items") or []}
    if it.get("drift") and it["drift"]["verdict"] == "bad":
        pts += 1
        why.append("後段心率漂移偏高")
    if it.get("hr_cap") and it["hr_cap"]["verdict"] == "bad":
        pts += 1
        why.append("輕鬆課的心率超過上限太多，實際上練得比計畫重")
    if it.get("hr_recovery") and it["hr_recovery"]["verdict"] == "bad":
        pts += 1
        why.append("組間心率恢復慢")
    if it.get("volume") and it["volume"]["value"].rstrip("%").isdigit() and int(it["volume"]["value"].rstrip("%")) > 125:
        pts += 1
        why.append("跑得比計畫多")
    keys = sorted(loads)
    if len(keys) >= 35:
        last7 = sum(loads[k] for k in keys[-7:])
        prev28 = sum(loads[k] for k in keys[-35:-7]) / 4
        if prev28 > 0 and last7 / prev28 > 1.5:
            pts += 1
            why.append(f"近 7 天累積是平常的 {round(last7 / prev28 * 100)}%")
    mb = db.q1("SELECT data FROM briefs WHERE user_id=? AND date=? AND kind='morning'", (uid, ds))
    if mb:
        lv = json.loads(mb["data"]).get("level")
        if lv in ("reduce", "rest"):
            pts += 1 if lv == "reduce" else 2
            why.append("早上的恢復指標就已經偏低")
    ck = db.q1("SELECT feeling, pain, rpe FROM checkins WHERE user_id=? AND date=?", (uid, ds))
    if ck and ck.get("feeling") and ck["feeling"] <= 2:
        pts += 1
        why.append("你填的感覺偏累")
    if ck and ck.get("rpe") and ck["rpe"] >= 4:
        pts += 2 if ck["rpe"] >= 5 else 1
        why.append("你說這堂課跑起來" + ("快撐不住" if ck["rpe"] >= 5 else "很硬"))
    elif ck and ck.get("rpe") and ck["rpe"] <= 2 and pts > 0:
        pts -= 1
        why.append("你說跑起來很輕鬆")
    level = "low" if pts <= 1 else "moderate" if pts <= 3 else "high"
    label = {"low": "低：明天照課表", "moderate": "中等：明天的強度課可以視情況縮短", "high": "高：明天需要減量"}[level]
    tomorrow_ds = (d + timedelta(days=1)).isoformat()
    tmr = _plan_rows(uid, tomorrow_ds, tomorrow_ds).get(tomorrow_ds)
    opts = []
    advice = None
    if tmr and tmr.get("kind") not in (None, "rest", "done"):
        hard_tmr = tmr["kind"] in QUALITY or tmr["kind"] in ("long",)
        if level == "high":
            opts = day_options(uid, tomorrow_ds, "reduce", tmr)
            advice = f"明天的「{tmr.get('title')}」建議改輕一點，讓身體吸收今天的訓練。"
        elif level == "moderate" and hard_tmr:
            opts = day_options(uid, tomorrow_ds, "trim", tmr)
            advice = f"明天是「{tmr.get('title')}」。今晚睡好的話可以照練；明早晨報偏低就選下面的縮短版。"
        else:
            advice = f"明天是「{tmr.get('title')}」，照課表就好。"
    elif tmr:
        advice = "明天休息，剛好讓身體恢復。"
    from .garmin_client import today_local
    is_today = ds >= (today_local() - timedelta(days=1)).isoformat()
    return {"level": level, "label": label, "points": pts, "why": why, "load": round(load or 0), "rel": rel and round(rel, 2),
            "tomorrow": tmr and {k: tmr.get(k) for k in ("kind", "title", "distance_km", "minutes")}, "tomorrow_date": tomorrow_ds,
            "advice": advice, "options": opts if is_today else []}


def goal_progress(uid: int) -> dict | None:
    """目前能力離目標還差多少（每次跑完都看得到）。"""
    try:
        from . import engine, season
        cx = season.Ctx(uid)
    except Exception:
        return None
    a, g = cx.assess, cx.goal
    if not a.get("cur_vdot"):
        return None
    out = {"distance_label": T.DIST_PROFILE[T.dist_key(g["distance_km"])]["label"], "cur_vdot": a["cur_vdot"],
           "pred": engine.fmt_time(a.get("pred_goal_s")) if a.get("pred_goal_s") else None,
           "target": engine.fmt_time(g["target_s"]) if g.get("target_s") else None, "verdict": a.get("verdict")}
    if a.get("target_vdot"):
        gap = a["target_vdot"] / a["cur_vdot"] - 1
        out["gap_pct"] = round(gap * 100, 1)
        out["target_vdot"] = a["target_vdot"]
        out["realistic"] = engine.fmt_time(a["realistic_s"]) if a.get("realistic_s") else None
    return out


# ───────────────────────────── 身體狀況：處理建議 ─────────────────────────────

CARE = {
    "knee": {"likely": "膝蓋前側或外側痛，常跟臀部和大腿肌力不足、步幅太大、跑量加太快有關。",
             "do": ["這幾天避開下坡和樓梯", "冰敷 10–15 分鐘", "跑步時步伐小一點、步頻快一點"],
             "rehab": ["側躺抬腿 3 × 15（臀中肌）", "靠牆半蹲 3 × 30 秒（不痛的角度）", "臀橋 3 × 12"],
             "stretch": ["股四頭肌伸展 2 × 30 秒", "滾筒放鬆大腿外側（髂脛束）1 分鐘"]},
    "shin": {"likely": "脛骨內側痛常見於跑量或強度加太快、硬地太多（脛前疼痛）。",
             "do": ["接下來幾天減量 30–50%、暫停間歇和坡道", "換到草地、PU 跑道", "按壓骨頭某一點很痛、走路也痛就要就醫排除疲勞性骨折"],
             "rehab": ["坐姿勾腳尖（脛前肌）3 × 20", "單腳提踵 3 × 12", "腳趾抓毛巾 2 × 1 分鐘"],
             "stretch": ["小腿肚伸展 2 × 30 秒", "跪坐伸展脛前 30 秒"]},
    "calf": {"likely": "小腿肚緊或痛，常見於配速突然加快、坡道、換薄底鞋。",
             "do": ["先暫停快跑和坡道", "輕鬆跑縮短", "按壓有硬塊可以用滾筒放鬆"],
             "rehab": ["雙腳提踵 → 單腳提踵 3 × 15", "屈膝提踵（比目魚肌）3 × 15"],
             "stretch": ["直膝小腿伸展 2 × 30 秒", "屈膝小腿伸展 2 × 30 秒"]},
    "ankle": {"likely": "腳踝痛可能是扭到、阿基里斯腱或外側肌腱發炎。腫起來或有瘀青先別跑。",
              "do": ["腫脹時：休息、冰敷、抬高", "早上起床腳跟後方特別緊痛，可能是阿基里斯腱，先停快跑"],
              "rehab": ["離心提踵（慢慢放下）3 × 15", "單腳平衡 3 × 30 秒", "彈力帶腳踝四方向 2 × 15"],
              "stretch": ["小腿伸展 2 × 30 秒"]},
    "foot": {"likely": "腳跟或足弓痛，早上下床第一步最痛，常見是足底筋膜炎。",
             "do": ["減量、暫停坡道和快跑", "避免赤腳走硬地板", "鞋子底磨平了就換"],
             "rehab": ["腳趾抓毛巾 2 × 1 分鐘", "單腳提踵 3 × 12", "足弓收縮（短足運動）3 × 10"],
             "stretch": ["網球或冰水瓶滾足底 2 分鐘", "小腿伸展 2 × 30 秒"]},
    "hip": {"likely": "髖部痛常跟臀肌無力、核心不穩有關。鼠蹊深處痛、走路也痛要就醫。",
            "do": ["暫停長跑和快跑", "輕鬆跑縮短"],
            "rehab": ["臀橋 3 × 12", "蚌殼式 3 × 15", "側棒式 3 × 20 秒"],
            "stretch": ["髂腰肌弓箭步伸展 2 × 30 秒", "梨狀肌伸展 2 × 30 秒"]},
    "thigh": {"likely": "大腿後側突然刺痛可能是拉傷，先別跑；只是緊就是疲勞。",
              "do": ["突然刺痛：停跑、冰敷，先找物理治療師", "只是緊：輕鬆跑、別跑快"],
              "rehab": ["臀橋 3 × 12", "北歐腿彎舉（離心，慢慢倒下）2 × 5"],
              "stretch": ["大腿後側伸展 2 × 30 秒", "滾筒放鬆 1 分鐘"]},
    "back": {"likely": "下背痛常見於核心無力或久坐。腳麻、無力要就醫。",
             "do": ["跑完不要馬上坐著不動", "輕鬆跑可以照常，但先不要跑快"],
             "rehab": ["鳥狗式 3 × 10", "死蟲式 3 × 10", "棒式 3 × 30 秒"],
             "stretch": ["嬰兒式 1 分鐘", "髂腰肌伸展 2 × 30 秒"]},
    "other": {"likely": "", "do": ["痛的地方跑步時會越跑越痛，就先停下來"], "rehab": [], "stretch": []},
}
RED_FLAGS = ["明顯腫脹或瘀青", "走路、上下樓梯就會痛", "晚上休息時也會痛", "按壓骨頭某一點特別痛", "痛超過一週沒有改善"]


def checkin_advice(uid: int, ds: str, feel: int | None, pain: str | None) -> dict:
    """填完身體狀況之後：今天的課要不要改、痛的地方怎麼處理、要不要就醫。"""
    days = [(date.fromisoformat(ds) - timedelta(days=i)).isoformat() for i in range(T.PAIN_DAYS)]
    rows = {r["date"]: r for r in db.q("SELECT date, pain, feeling FROM checkins WHERE user_id=? AND date IN (%s)" % ",".join("?" * len(days)),
                                       (uid, *days))}
    streak = 0
    for d in days:
        if pain and (rows.get(d) or {}).get("pain") == pain or (d == ds and pain):
            streak += 1
        else:
            break
    severity = None
    msg = []
    if pain:
        severity = "reduce" if streak < 2 else "rest"
        msg.append(f"{T.PAIN_SITES.get(pain, '這個地方')}痛" + (f"已經連續 {streak} 天" if streak >= 2 else ""))
    if feel is not None and feel <= 1:
        severity = "rest" if severity or feel == 1 else "reduce"
        msg.append("感覺很糟")
    elif feel is not None and feel == 2:
        severity = severity or "reduce"
        msg.append("感覺很累")
    target = ds
    if db.q1("SELECT 1 AS x FROM activities WHERE user_id=? AND date=?", (uid, ds)):   # 今天已經跑完：改的是明天
        target = (date.fromisoformat(ds) + timedelta(days=1)).isoformat()
    plan_today = _plan_rows(uid, target, target).get(target)
    opts = day_options(uid, target, severity, plan_today) if severity else []
    care = None
    if pain:
        c = CARE.get(pain, CARE["other"])
        care = {"site": pain, "label": T.PAIN_SITES.get(pain, pain), **c, "red_flags": RED_FLAGS,
                "streak": streak, "stop_in": max(0, T.PAIN_DAYS - streak)}
    headline = None
    when = "今天" if target == ds else "明天"
    if severity == "rest":
        headline = f"{when}建議休息或只跑恢復跑"
    elif severity == "reduce":
        headline = f"{when}建議減量"
    elif feel and feel >= 4:
        headline = "狀況不錯，照課表練"
    return {"severity": severity, "headline": headline, "why": msg, "options": opts, "care": care, "target": target,
            "target_label": "今天" if target == ds else "明天",
            "plan": plan_today and {k: plan_today.get(k) for k in ("kind", "title", "distance_km")}}
