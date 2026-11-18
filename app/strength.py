"""休息日的肌力與伸展。

跑者最常見的傷（髂脛束、膝蓋、阿基里斯腱、足底）多半跟臀部、核心、小腿肌力不足有關。
休息日排 10–15 分鐘、不用器材的菜單，三套輪流；前一天是長跑、強度課或比賽，就改排伸展放鬆。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import db
from .config import TZ

XP_STRENGTH = 15

ROUTINES = {
    "core": {
        "title": "核心穩定", "kind": "strength", "minutes": 12,
        "why": "核心穩，跑後段才不會塌腰、晃骨盆，膝蓋和下背的負擔比較小。",
        "items": [
            {"name": "死蟲式", "dose": "2 組 × 每側 8 下", "cue": "躺著，下背貼地，對側手腳慢慢伸直再收回。下背離地就是做太遠。"},
            {"name": "棒式", "dose": "3 組 × 30 秒", "secs": 30, "cue": "手肘在肩膀正下方，肚臍往內收，身體一直線，不要翹屁股。"},
            {"name": "側棒式", "dose": "2 組 × 每側 25 秒", "secs": 25, "cue": "手肘撐地，髖部往上推，頭到腳一直線。太難就膝蓋著地。"},
            {"name": "鳥狗式", "dose": "2 組 × 每側 8 下", "cue": "四足跪姿，對側手腳伸直，停 2 秒。骨盆不要轉。"},
            {"name": "臀橋", "dose": "3 組 × 12 下", "cue": "腳跟踩地把屁股推高，頂端夾緊臀部 1 秒，不要用腰撐。"},
        ],
    },
    "legs": {
        "title": "臀腿肌力", "kind": "strength", "minutes": 14,
        "why": "臀中肌和小腿是跑步的避震器，練起來能減少膝蓋和阿基里斯腱的傷。",
        "items": [
            {"name": "徒手深蹲", "dose": "3 組 × 12 下", "cue": "腳與肩同寬，屁股往後坐，膝蓋跟腳尖同方向，不要內夾。"},
            {"name": "後跨弓箭步", "dose": "2 組 × 每側 8 下", "cue": "往後跨一大步，前腳膝蓋不超過腳尖太多，身體保持直立。"},
            {"name": "單腳臀橋", "dose": "2 組 × 每側 10 下", "cue": "一腳抬起，另一腳推地把屁股推高，骨盆保持水平。"},
            {"name": "單腳提踵", "dose": "3 組 × 每側 12 下", "cue": "扶牆單腳站，慢慢踮到最高再慢慢放下，放下要花 3 秒。"},
            {"name": "蚌殼式", "dose": "2 組 × 每側 15 下", "cue": "側躺膝蓋彎曲，腳跟貼著，上面的膝蓋打開，骨盆不要往後倒。"},
        ],
    },
    "mobility": {
        "title": "伸展放鬆", "kind": "mobility", "minutes": 12,
        "why": "昨天練得比較重，今天放鬆髖部、大腿後側和小腿，幫身體恢復。",
        "items": [
            {"name": "弓步髖屈肌伸展", "dose": "每側 40 秒", "secs": 40, "cue": "單膝跪地，骨盆往前推，後腳那側的大腿前面會有感覺。"},
            {"name": "腿後側伸展", "dose": "每側 40 秒", "secs": 40, "cue": "腳跟放在矮椅上，背打直，從髖部往前傾，不要彎腰。"},
            {"name": "小腿推牆伸展", "dose": "每側 40 秒", "secs": 40, "cue": "雙手推牆，後腳伸直、腳跟踩地。再微彎膝蓋做一次，伸展比目魚肌。"},
            {"name": "鴿式", "dose": "每側 45 秒", "secs": 45, "cue": "前腳彎曲放在身前，後腳往後伸直，身體慢慢往前趴。膝蓋痛就改成仰躺的 4 字伸展。"},
            {"name": "貓牛式", "dose": "10 次", "cue": "四足跪姿，吸氣抬頭塌腰，吐氣拱背，配合呼吸慢慢做。"},
            {"name": "嬰兒式", "dose": "45 秒", "secs": 45, "cue": "跪坐往前趴，手往前伸，放鬆下背，慢慢深呼吸。"},
        ],
    },
}
ROTATION = ["core", "legs", "mobility"]
HEAVY = {"long", "tempo", "intervals", "race_pace", "hills", "race", "fartlek"}


def pick(rest_index: int, prev_kind: str | None) -> str:
    """前一天練得重就排伸展，否則核心、臀腿、伸展輪流。"""
    if prev_kind in HEAVY:
        return "mobility"
    return ROTATION[rest_index % len(ROTATION)]


def plan_week(days: list[dict], prev_kind: str | None = None) -> dict[str, str]:
    """days：這週每天的 {date, kind}（依日期排序）。回傳 {休息日日期: 菜單 id}。"""
    out, prev, idx = {}, prev_kind, 0
    week_no = date.fromisoformat(days[0]["date"]).isocalendar()[1] if days else 0
    for d in days:
        if d.get("kind") in (None, "rest"):
            out[d["date"]] = pick(idx + week_no, prev)
            idx += 1
        elif d.get("kind") == "strength":      # 匯入課表裡的肌力日：給肌力菜單（不給伸展）
            out[d["date"]] = ["core", "legs"][(idx + week_no) % 2]
            idx += 1
        prev = d.get("kind")
    return out


def done_dates(user_id: int, start: str, end: str) -> dict[str, str]:
    return {r["date"]: r["routine"] for r in db.q(
        "SELECT date, routine FROM strength_log WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, start, end))}


def detail(rid: str) -> dict:
    r = ROUTINES[rid]
    return {"id": rid, **r}


def mark(user_id: int, ds: str, rid: str, done: bool = True):
    if rid not in ROUTINES:
        raise ValueError("沒有這個菜單")
    with db.tx() as c:
        if done:
            c.execute("INSERT INTO strength_log(user_id, date, routine, done_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET routine=excluded.routine, done_at=excluded.done_at",
                      (user_id, ds, rid, datetime.now(TZ).isoformat(timespec="seconds")))
        else:
            c.execute("DELETE FROM strength_log WHERE user_id=? AND date=?", (user_id, ds))


def summary_for(rid: str) -> dict:
    r = ROUTINES[rid]
    return {"id": rid, "title": r["title"], "kind": r["kind"], "minutes": r["minutes"]}


def week_kinds(user_id: int, w0: date) -> list[dict]:
    """這週每天的課表種類（過去用存檔，今天之後用最新排程）。"""
    rows = {r["date"]: json.loads(r["data"]).get("kind") for r in db.q(
        "SELECT date, data FROM plan WHERE user_id=? AND date BETWEEN ? AND ?",
        (user_id, (w0 - timedelta(days=1)).isoformat(), (w0 + timedelta(days=6)).isoformat()))}
    return [{"date": (w0 + timedelta(days=i)).isoformat(), "kind": rows.get((w0 + timedelta(days=i)).isoformat())}
            for i in range(7)]
