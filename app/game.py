"""角色養成：用跑步里程、PB、照課表完成、連續訓練週賺經驗值，讓像素角色升級進化。

經驗值完全從跑步紀錄「重算」出來（不另外存累計值），所以：
- 補同步到舊資料、刪除重複活動，都會自動修正，不會有灌水或少算
- 換角色不會失去等級
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from . import db
from .garmin_client import today_local

from .config import WEB_DIR

# 角色清單來自 web/characters/manifest.json：之後新增角色只要放圖片＋改 manifest，不用改程式
_MANIFEST = json.loads((WEB_DIR / "characters" / "manifest.json").read_text(encoding="utf-8"))
SPECIES = {s["id"]: {"name": s["name"], "blurb": s["blurb"], "stages": [st["name"] for st in s["stages"]]}
           for s in _MANIFEST["species"]}
STAGE_LEVELS = _MANIFEST["stage_levels"]  # 進入各階段的等級

XP_PER_KM = 10
XP_PLANNED = 20
XP_STREAK_WEEK = 50           # 一週跑滿 3 次
PB_XP = {"1k": 80, "1mi": 80, "5k": 150, "10k": 200, "half": 300, "full": 500, "longest": 100}
FIRST_XP = {"5k": 100, "10k": 150, "half": 250, "full": 400}
RECORD_LABEL = {"1k": "1 公里", "1mi": "1 英里", "5k": "5 公里", "10k": "10 公里",
                "half": "半程馬拉松", "full": "全程馬拉松", "longest": "最長距離"}
RECORD_M = {"1k": 1000, "1mi": 1609.34, "5k": 5000, "10k": 10000, "half": 21097.5, "full": 42195}


def xp_for_level(level: int) -> int:
    """升到 level 需要的累計經驗值。Lv4≈45 km、Lv9≈250 km、Lv16≈800 km、Lv25≈2000 km（含獎勵會更快）。"""
    return 40 * level * (level - 1)


def level_from_xp(xp: int) -> int:
    lv = 1
    while xp >= xp_for_level(lv + 1):
        lv += 1
    return lv


def stage_for_level(level: int) -> int:
    return max(i for i, lv in enumerate(STAGE_LEVELS) if level >= lv)


def derive_records(acts: list[dict]) -> list[dict]:
    """沒有 Garmin PB 資料時（範例模式），用整趟平均配速估算各距離成績。"""
    out = []
    best: dict[str, float] = {}
    for a in sorted(acts, key=lambda a: a.get("start_local") or a["date"]):
        dist, dur = a.get("distance_m") or 0, a.get("duration_s") or 0
        if dist <= 0 or dur <= 0:
            continue
        cands = {k: dur * m / dist for k, m in RECORD_M.items() if dist >= m * 0.995}
        cands["longest"] = dist
        for k, v in cands.items():
            better = k not in best or (v > best[k] if k == "longest" else v < best[k] - 0.5)
            if better:
                best[k] = v
                out.append({"rtype": k, "value": v, "activity_id": a["activity_id"], "date": a["date"]})
    return out


def records_for(user_id: int, acts: list[dict]) -> tuple[list[dict], str]:
    rows = db.q("SELECT rtype, value, activity_id, date FROM records WHERE user_id=? ORDER BY date", (user_id,))
    if rows:
        return rows, "garmin"
    return derive_records(acts), "estimate"


HATCH_GIFT_RATE = 0.10
HATCH_GIFT_CAP = 400          # 孵化禮上限（Lv4 需要 480），第一次進化還是要自己跑出來


def compute(user_id: int) -> dict:
    acts = db.q("SELECT * FROM activities WHERE user_id=? ORDER BY start_local, date", (user_id,))
    plans = {r["date"]: json.loads(r["data"]).get("kind")
             for r in db.q("SELECT date, data FROM plan WHERE user_id=?", (user_id,))}
    records, rec_source = records_for(user_id, acts)
    ch = db.q1("SELECT * FROM character WHERE user_id=?", (user_id,))
    # 經驗值從「選角色那天」開始算；之前的紀錄只當 PB 基準和孵化禮
    hatch = (ch["chosen_at"] or "")[:10] if ch else today_local().isoformat()

    raw = []  # (date, xp, label, kind)
    planned_done = set()
    for a in acts:
        km = (a.get("distance_m") or 0) / 1000
        if km <= 0:
            continue
        raw.append((a["date"], round(km * XP_PER_KM), f"跑了 {km:.1f} 公里", "run"))
        if plans.get(a["date"]) not in (None, "rest", "done") and a["date"] not in planned_done:
            planned_done.add(a["date"])
            raw.append((a["date"], XP_PLANNED, "照課表完成", "plan"))

    by_week = defaultdict(list)
    for a in acts:
        y, w, _ = date.fromisoformat(a["date"]).isocalendar()
        by_week[(y, w)].append(a["date"])
    for wk, ds in by_week.items():
        ds = sorted(set(ds))
        if len(ds) >= 3:
            raw.append((ds[2], XP_STREAK_WEEK, "本週跑滿 3 次", "streak"))

    # PB：依時間順序，比之前更好才算破紀錄；第一次完成某距離有完賽獎勵
    best: dict[str, float] = {}
    current: dict[str, dict] = {}
    for r in sorted(records, key=lambda r: r["date"]):
        k, v = r["rtype"], r["value"]
        if k not in best:
            best[k] = v
            current[k] = r
            if k in FIRST_XP:
                raw.append((r["date"], FIRST_XP[k], f"第一次完成 {RECORD_LABEL[k]}", "first"))
            continue
        improved = v > best[k] if k == "longest" else v < best[k] - 0.5
        if improved:
            best[k] = v
            current[k] = r
            raw.append((r["date"], PB_XP[k], f"{RECORD_LABEL[k]} 破 PB！", "pb"))

    # 休息日肌力／伸展
    for r in db.q("SELECT date, routine FROM strength_log WHERE user_id=?", (user_id,)):
        from .strength import ROUTINES, XP_STRENGTH
        raw.append((r["date"], XP_STRENGTH, f"休息日{ROUTINES.get(r['routine'], {}).get('title', '肌力')}", "strength"))

    history_xp = sum(x for d, x, _, _ in raw if d < hatch)
    events = [e for e in raw if e[0] >= hatch]
    gift = min(HATCH_GIFT_CAP, round(history_xp * HATCH_GIFT_RATE))
    if gift:
        events.append((hatch, gift, "孵化禮：過去的努力", "gift"))
    events.sort(key=lambda e: e[0])
    xp = sum(e[1] for e in events)
    level = level_from_xp(xp)
    stage = stage_for_level(level)
    nxt = STAGE_LEVELS[stage + 1] if stage + 1 < len(STAGE_LEVELS) else None

    sp = SPECIES.get(ch["species"]) if ch else None
    if ch and not sp:   # 舊版角色已移除 → 請使用者重選
        ch, sp = None, None

    today = today_local()
    week_start = today - timedelta(days=today.weekday())
    week_km = sum((a.get("distance_m") or 0) for a in acts if a["date"] >= week_start.isoformat()) / 1000
    month_km = sum((a.get("distance_m") or 0) for a in acts if a["date"][:7] == today.isoformat()[:7]) / 1000
    total_km = sum((a.get("distance_m") or 0) for a in acts) / 1000

    return {
        "character": {"species": ch["species"], "nickname": ch["nickname"] or sp["stages"][stage],
                      "species_name": sp["name"], "stage_name": sp["stages"][stage]} if ch else None,
        "xp": xp,
        "level": level,
        "stage": stage,
        "level_floor": xp_for_level(level),
        "level_ceil": xp_for_level(level + 1),
        "next_stage_level": nxt,
        "events": [{"date": d, "xp": x, "label": l, "kind": k} for d, x, l, k in events[-30:][::-1]],
        "records": [{"rtype": k, "label": RECORD_LABEL[k], "value": r["value"], "date": r["date"]}
                    for k, r in sorted(current.items(), key=lambda kv: list(RECORD_LABEL).index(kv[0]))],
        "record_source": rec_source,
        "week_km": round(week_km, 1),
        "month_km": round(month_km, 1),
        "total_km": round(total_km, 1),
        "last_run": acts[-1]["date"] if acts else None,
        "hatch_date": hatch,
        "xp_by_day": {d: sum(e[1] for e in events if e[0] == d) for d in {e[0] for e in events}},
    }


def rules() -> dict:
    return {"xp_per_km": XP_PER_KM, "planned": XP_PLANNED, "streak_week": XP_STREAK_WEEK,
            "pb": {RECORD_LABEL[k]: v for k, v in PB_XP.items()},
            "first": {RECORD_LABEL[k]: v for k, v in FIRST_XP.items()},
            "stage_levels": STAGE_LEVELS, "gift_rate": HATCH_GIFT_RATE, "gift_cap": HATCH_GIFT_CAP}


def leaderboard() -> list[dict]:
    out = []
    for u in db.q("SELECT id, display_name, username FROM users WHERE provider IN ('garmin','demo')"):
        if not db.get_prefs(u["id"]).get("show_on_board", True):
            continue
        g = compute(u["id"])
        out.append({"name": u["display_name"] or u["username"], "user_id": u["id"],
                    "character": g["character"], "level": g["level"], "stage": g["stage"], "xp": g["xp"],
                    "week_km": g["week_km"], "month_km": g["month_km"], "last_run": g["last_run"]})
    return out
