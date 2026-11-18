"""匯入自己寫好的逐日課表（.docx），之後完全照表排課。

支援的文件格式（就是《半馬 1:35 訓練課表》那種）：
  - 一張「配速與代號」表：第一欄是「E 輕鬆跑」這種代號，中間幾欄的表頭寫階段日期（例如「階段一 11/9–12/20」），
    最後一欄是心率上限
  - 一張每日行事曆表：第一欄是週次（W0、W1 北高週…），後面七欄是週一到週日，每格是「11/10 5 km E」這種寫法
  - 內文有「起算日 2026/11/5」用來推年份（沒有的話用匯入當天的年份）

每一格會轉成一堂課（分段、配速、心率上限都照文件的配速表），存進 imported_plan 表。
看不懂的格子不會丟掉：照原文當成一堂「自訂」課顯示，並列在匯入結果的提醒裡。
"""
from __future__ import annotations

import io
import json
import re
from datetime import date

from . import db
from .week import _rep, _step, _wrap, build_workout

DASH = r"[–—\-~～]"
PACE = r"(\d{1,2}:\d{2})"

ZONE_CODES = {"E", "M", "HMP", "T", "I", "R"}


def _sec(p: str) -> int:
    m, s = p.split(":")
    return int(m) * 60 + int(s)


def _year_for(month: int, start: date) -> int:
    return start.year + (1 if month < start.month - 2 else 0)


def _md(text: str, start: date) -> date | None:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})", text)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    return date(_year_for(mo, start), mo, d)


# ───────────────────────────── 讀文件 ─────────────────────────────

def read_docx(data: bytes) -> tuple[list[str], list[list[list[str]]]]:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(data))
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    tables = [[[c.text.strip() for c in r.cells] for r in t.rows] for t in d.tables]
    return paras, tables


def _start_date(paras: list[str], today: date) -> date:
    for p in paras:
        m = re.search(r"起算日\s*(\d{4})/(\d{1,2})/(\d{1,2})", p)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return today


def _zone_table(tables, start: date) -> list[dict] | None:
    """回傳各階段：[{"from": date, "to": date, "label": "階段一", "zones": {...}}]"""
    for t in tables:
        head = t[0]
        cols = [(i, h) for i, h in enumerate(head) if re.search(rf"\d{{1,2}}/\d{{1,2}}\s*{DASH}\s*\d{{1,2}}/\d{{1,2}}", h)]
        if not cols or not any(r and r[0][:1] == "E" for r in t[1:]):
            continue
        cap_col = next((i for i, h in enumerate(head) if "心率" in h), len(head) - 1)
        phases = []
        for i, h in cols:
            a, b = re.findall(r"(\d{1,2}/\d{1,2})", h)[:2]
            fr, to = _md(a, start), _md(b, start)
            if to < fr:
                to = date(to.year + 1, to.month, to.day)
            zones = {}
            for r in t[1:]:
                code = r[0].split()[0] if r[0] else ""
                if code not in ZONE_CODES:
                    continue
                ps = re.findall(PACE, r[i])
                cap = re.search(r"\d{3}", r[cap_col]) if cap_col < len(r) else None
                if not ps:
                    continue
                lo, hi = _sec(ps[0]), _sec(ps[-1])
                if lo == hi:
                    lo, hi = lo - 5, hi + 5
                zones[code] = {"min": min(lo, hi), "max": max(lo, hi), "hrCap": int(cap.group()) if cap else None}
            phases.append({"from": fr, "to": to, "label": re.sub(rf"\s*\d.*$", "", h).strip() or f"階段{len(phases) + 1}",
                           "zones": zones})
        return phases
    return None


def _fill_zones(z: dict, prev: dict | None) -> dict:
    """文件某階段沒列的強度（例如階段一沒有 I）：借上一個或下一個階段的，最後才用推算值。"""
    out = {k: dict(v) for k, v in (prev or {}).items()}
    out.update({k: dict(v) for k, v in z.items()})
    e = out.get("E") or {"min": 400, "max": 430, "hrCap": 150}
    out["E"] = e
    out.setdefault("M", {"min": e["min"] - 60, "max": e["min"] - 50, "hrCap": None})
    out.setdefault("T", {"min": e["min"] - 75, "max": e["min"] - 65, "hrCap": None})
    out.setdefault("I", {"min": out["T"]["min"] - 25, "max": out["T"]["min"] - 15, "hrCap": None})
    out.setdefault("HMP", {"min": out["T"]["min"] + 10, "max": out["T"]["min"] + 15, "hrCap": None})
    out.setdefault("R", {"min": out["I"]["min"] - 15, "max": out["I"]["min"] - 5, "hrCap": None})
    out["RP"] = dict(out["HMP"])
    out["TEST"] = {"min": None, "max": None, "hrCap": None}
    for v in out.values():
        v.setdefault("hrCap", None)
    return out


# ───────────────────────────── 解析每一格 ─────────────────────────────

def _strip_date(cell: str) -> str:
    return re.sub(r"^\s*\d{1,2}/\d{1,2}\s*", "", cell).strip()


def _paren(text: str) -> tuple[str, str]:
    m = re.search(r"[（(]([^）)]*)[）)]", text)
    return (re.sub(r"\s*[（(][^）)]*[）)]\s*", " ", text).strip(), m.group(1).strip()) if m else (text, "")


def _custom(text: str, why: str) -> dict:
    km = re.search(r"(\d+(?:\.\d+)?)\s*km", text)
    return {"kind": "custom", "title": text, "intensity": "moderate", "minutes": 0,
            "distance_km": float(km.group(1)) if km else 0, "steps": [], "summary": text, "why": why}


def _fill(main: list, total_km: float, z: dict) -> list:
    """「8 km 含節奏 2×10 分」：主課以外的距離拆成熱身和緩和。"""
    from .week import _flat
    used = sum(s["km"] for s in _flat(main))
    rest = total_km - used
    wu = max(0.5, round(rest * 0.55 * 2) / 2)
    cd = max(0.5, round((rest - wu) * 10) / 10)
    return [_step("warmup", z, "E", km=wu, note="輕鬆熱身"), *main, _step("cooldown", z, "E", km=cd, note="輕鬆緩和")]


def parse_cell(cell: str, z: dict, why: str) -> tuple[dict | None, bool]:
    """一格 → (課, 看得懂嗎)。None 表示這格不是課（例如「—」）。"""
    raw = _strip_date(cell)
    if not raw or raw in ("—", "-", "–"):
        return None, True
    text, note = _paren(raw)
    t = text.replace("×", "x").replace("＋", "+").replace("，", ",")
    noted = (lambda w: {**w, "summary": w["summary"] + (f"（{note}）" if note else "")})

    if re.fullmatch(r"(今天,?\s*)?休|完全休息|休息", t):
        w = build_workout("rest", zones=z, why=why)
        if "完全" in t:
            w["title"] = "完全休息"
        return w, True
    if t.startswith("肌力"):
        return {"kind": "strength", "title": "肌力 30 分", "intensity": "rest", "minutes": 30, "distance_km": 0, "steps": [],
                "summary": "自體重量肌力 30 分（課表第三節的動作）。長跑前一天不要做。", "why": why}, True
    if "測驗" in t:
        m = re.search(r"(\d+(?:\.\d+)?)\s*km\s*測驗", t)
        if "半馬" in t:
            return {"kind": "race", "title": f"🎯 {t}", "intensity": "race", "minutes": 0, "distance_km": 21.1, "steps": [],
                    "summary": raw + "。這是檢核點：照比賽跑，跑完看成績判讀進度。", "why": why}, True
        if m:
            tk = float(m.group(1))
            wu = re.search(r"暖身\s*(\d+(?:\.\d+)?)", note)
            cd = re.search(r"緩和\s*(\d+(?:\.\d+)?)", note)
            main = _step("interval", z, None, km=tk, note="全力跑完，看成績判讀進度")
            main["zone"] = "TEST"
            st = [_step("warmup", z, "E", km=float(wu.group(1)) if wu else 2.0, note="熱身，最後加 3–4 次快步"), main,
                  _step("cooldown", z, "E", km=float(cd.group(1)) if cd else 1.5)]
            return noted(_wrap("test", f"🎯 {tk:g} km 測驗", "hard", st, f"{tk:g} km 全力測驗，是課表的檢核點。", why)), True
    if t == "比賽" or (not re.match(r"\d", t) and ("半馬" in t or "全馬" in t or t.endswith("賽"))):
        title = "🏁 " + (t if t != "比賽" else "比賽日")
        return {"kind": "race", "title": title, "intensity": "race", "minutes": 0,
                "distance_km": 42.2 if "全馬" in t else 21.1 if ("半馬" in t or t == "比賽") else 0,
                "steps": [], "summary": raw, "why": why}, True
    if "北高" in t or "單車" in t:
        return {"kind": "event", "title": f"🚴 {t}", "intensity": "rest", "minutes": 0, "distance_km": 0, "steps": [],
                "summary": raw, "why": why}, True

    m = re.match(r"(\d+(?:\.\d+)?)\s*km", t)
    if not m:
        return _custom(raw, why), False
    km = float(m.group(1))
    rest_t = t[m.end():].strip(" ,")
    hr = re.search(r"心率\s*(\d{3})\s*以下", note)
    cap = int(hr.group(1)) if hr else None

    # 長跑：「24 km,末 5 km M」/「15 km E 長跑」
    mm = re.match(r"末\s*(\d+(?:\.\d+)?)\s*km\s*M", rest_t)
    if mm:
        mk = float(mm.group(1))
        w = build_workout("long_m", km=km, zones=z, m_km=mk, why=why)
        w["steps"][1]["zone"] = "M"
        w["steps"][1].update({"pace_hi": z["M"]["min"], "pace_lo": z["M"]["max"], "note": "最後一段跑馬拉松配速"})
        if z["M"].get("hrCap"):
            w["steps"][1]["hr_cap"] = z["M"]["hrCap"]
        w = _wrap("long_m", f"長跑（末 {mk:g} km M）", "hard", w["steps"],
                  f"{km:g} km 長跑，前段 E，最後 {mk:g} km 馬拉松配速 {_p(z['M'])}/km。", why)
        return noted(w), True
    if re.fullmatch(r"E\s*長跑", rest_t):
        w = build_workout("long", km=km, zones=z, why=why)
        w["summary"] = f"{km:g} km 長跑，全程 E 配速 {_p(z['E'])}/km，心率不超過 {z['E']['hrCap']}。"
        if km * z["E"]["max"] / 60 > 90:
            w["summary"] += "超過 90 分鐘：每 40 分鐘補一包能量膠配水。"
        return noted(w), True
    se = re.fullmatch(r"E(\s*鬆腿)?(\s*\+\s*快步\s*(?:x\s*(\d+))?)?", rest_t)
    if se:
        strides = int(se.group(3)) if se.group(3) else (4 if se.group(2) else 0)
        w = build_workout("easy", km=km, zones=z, strides=strides, hr_cap=cap, why=why)
        if se.group(1):
            w["title"] = "輕鬆跑（鬆腿）"
        w["summary"] = w["summary"].replace("輕鬆跑", f"輕鬆跑 @ {_p(z['E'])}/km", 1)
        return noted(w), True

    # 含節奏 a×b 分 / 含節奏 35 分
    q = re.match(r"含\s*(節奏|間歇|半馬配速)\s*(?:(\d+)\s*x\s*)?(\d+(?:\.\d+)?)\s*(分|km|m)\b", rest_t) or \
        re.match(r"含\s*()(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(km|m)\s*@\s*" + PACE, rest_t)
    if q:
        kind_zh, n, amt, unit = q.group(1), int(q.group(2) or 1), float(q.group(3)), q.group(4)
        zone, kind = {"節奏": ("T", "tempo"), "間歇": ("I", "intervals"), "半馬配速": ("HMP", "race_pace"), "": ("HMP", "race_pace")}[kind_zh]
        if kind_zh == "" and q.lastindex >= 5:
            p = _sec(q.group(5))
            z = {**z, "HMP": {**z["HMP"], "min": p - 3, "max": p + 3}}
        zz = {**z, "RP": z[zone]}
        rep_km = amt / 1000 if unit == "m" else (amt if unit == "km" else None)
        rep = _step("interval", zz, zone, km=rep_km, minutes=None if rep_km else amt,
                    note={"T": "舒服的辛苦", "I": "辛苦但每趟一致", "HMP": "半馬目標配速"}[zone])
        rec_min = {"T": 1.5, "I": 2, "HMP": 1.5}[zone]
        main = [_rep(n, rep, _step("recovery", z, None, minutes=rec_min, note=f"慢跑 {rec_min:g} 分"))] if n > 1 else [rep]
        st = _fill(main, km, z)
        amt_s = f"{int(amt)} m" if unit == "m" else f"{amt:g} {'km' if unit == 'km' else '分'}"
        desc = f"{n} × {amt_s}" if n > 1 else f"連續 {amt_s}"
        title = {"tempo": "節奏跑", "intervals": "間歇跑", "race_pace": "半馬配速"}[kind]
        cap_s = f"，心率上限 {z[zone]['hrCap']}" if z[zone].get("hrCap") else ""
        return noted(_wrap(kind, title, "hard", st,
                           f"共 {km:g} km：{desc} @ {_p(z[zone])}/km{cap_s}，其餘輕鬆跑熱身和緩和。", why)), True
    return _custom(raw, why), False


def _p(zone: dict) -> str:
    f = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
    return f"{f(zone['min'])}–{f(zone['max'])}"


# ───────────────────────────── 整份文件 ─────────────────────────────

def parse_plan(paras: list[str], tables, today: date) -> dict:
    start = _start_date(paras, today)
    phases = _zone_table(tables, start)
    if not phases:
        raise ValueError("找不到「配速與代號」表（第一欄是 E、M、T… 代號，表頭寫階段日期）")
    prev = None
    for ph in phases:
        ph["zones"] = _fill_zones(ph["zones"], prev)
        prev = ph["zones"]
    # 後面階段才有的強度，回填給前面階段（例如 I 只在階段二之後出現）
    cal = next((t for t in tables if t and len(t[0]) >= 8 and re.search(r"[一1]", t[0][1] or "")), None)
    if not cal:
        raise ValueError("找不到每日行事曆表（第一欄週次，後面七欄週一到週日）")

    def zones_on(d: date) -> tuple[dict, str]:
        for ph in phases:
            if ph["from"] <= d <= ph["to"]:
                return ph["zones"], ph["label"]
        return (phases[0] if d < phases[0]["from"] else phases[-1])["zones"], (phases[0] if d < phases[0]["from"] else phases[-1])["label"]

    days, unknown = {}, []
    for row in cal[1:]:
        week = row[0].strip()
        for cell in row[1:8]:
            d = _md(cell, start)
            if not d:
                continue
            z, label = zones_on(d)
            why = f"匯入課表 {week} · {label}"
            w, ok = parse_cell(cell, z, why)
            if w is None:
                continue
            w["source_text"] = _strip_date(cell)
            days[d.isoformat()] = w
            if not ok:
                unknown.append(f"{d.month}/{d.day} {_strip_date(cell)}")
    title = re.split(r"[\u3000]|\s{2,}", paras[0])[0].strip() if paras else "匯入的課表"
    return {"name": title[:40], "start": min(days) if days else None, "end": max(days) if days else None,
            "days": days, "unknown": unknown, "n_days": len(days)}


# ───────────────────────────── 存取 ─────────────────────────────

def save(uid: int, plan: dict):
    with db.tx() as c:
        c.execute("DELETE FROM imported_plan WHERE user_id=?", (uid,))
        c.executemany("INSERT INTO imported_plan(user_id, date, data) VALUES(?,?,?)",
                      [(uid, ds, json.dumps(w, ensure_ascii=False)) for ds, w in plan["days"].items()])
    prefs = db.get_prefs(uid)
    prefs["plan_mode"] = "imported"
    prefs["imported"] = {"name": plan["name"], "start": plan["start"], "end": plan["end"], "n_days": plan["n_days"],
                         "unknown": plan["unknown"][:30]}
    db.save_prefs(uid, prefs)


def clear(uid: int):
    with db.tx() as c:
        c.execute("DELETE FROM imported_plan WHERE user_id=?", (uid,))
    prefs = db.get_prefs(uid)
    prefs["plan_mode"] = "auto"
    prefs.pop("imported", None)
    db.save_prefs(uid, prefs)


def load(uid: int, frm: str, to: str | None = None) -> dict[str, dict]:
    sql, args = "SELECT date, data FROM imported_plan WHERE user_id=? AND date>=?", [uid, frm]
    if to:
        sql += " AND date<=?"
        args.append(to)
    return {r["date"]: json.loads(r["data"]) for r in db.q(sql, tuple(args))}


def active(prefs: dict) -> bool:
    return prefs.get("plan_mode") == "imported" and bool(prefs.get("imported"))
