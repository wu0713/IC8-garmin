"""把排好的課表推回 Garmin Connect 行事曆 → 手錶同步後就能直接照著跑（含配速提示）。

只會動到名稱以 "[IC8]" 開頭、而且是這個 App 自己建立的課表，不會碰你其他的課表。
"""
from __future__ import annotations

import hashlib
import json
import logging

from . import db

log = logging.getLogger(__name__)
PREFIX = "[IC8]"


def _to_garmin_steps(steps: list[dict], counter: list[int]):
    """輕鬆類（E）的步驟用心率上限當目標（規格書：心率上限是硬約束），強度課用配速區間。
    有距離的步驟用「距離」結束，短的（快步、間休）用時間。"""
    from garminconnect.workout import (CustomHeartRateTarget, PaceTarget, create_repeat_group,
                                       create_targeted_distance_interval_step, create_targeted_interval_step)

    type_meta = {"warmup": (1, "warmup"), "cooldown": (2, "cooldown"), "recovery": (4, "recovery")}

    def target(s):
        if s.get("zone") == "E" or (s.get("hr_cap") and not s.get("pace_lo")):
            if s.get("hr_cap"):
                return CustomHeartRateTarget(lower_limit=max(80, s["hr_cap"] - 25), upper_limit=s["hr_cap"])
        if s.get("pace_lo"):
            # Garmin 用 m/s；慢配速 = 低速度
            return PaceTarget(lower_limit=1000 / s["pace_lo"], upper_limit=1000 / s["pace_hi"])
        return None

    out = []
    for s in steps:
        counter[0] += 1
        order = counter[0]
        if s["type"] == "repeat":
            inner = _to_garmin_steps(s["steps"], counter)
            out.append(create_repeat_group(s["n"], inner, order))
            continue
        if s.get("by") == "km" and s.get("km"):
            st = create_targeted_distance_interval_step(round(s["km"] * 1000), order, target=target(s))
        else:
            st = create_targeted_interval_step(round(s.get("minutes", 0) * 60), order, target=target(s))
        if s["type"] in type_meta:
            i, k = type_meta[s["type"]]
            st.stepType = {"stepTypeId": i, "stepTypeKey": k, "displayOrder": i}
        if s.get("note"):
            st.description = s["note"][:100]
        out.append(st)
    return out


def build_running_workout(day: dict):
    from garminconnect.workout import RunningWorkout, WorkoutSegment

    steps = _to_garmin_steps(day["steps"], [0])
    return RunningWorkout(
        workoutName=f"{PREFIX} {day['date'][5:]} {day['title']}"[:60],
        estimatedDurationInSecs=int(day["minutes"] * 60),
        description=day.get("summary", "")[:500],
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType={"sportTypeId": 1, "sportTypeKey": "running"},
                                        workoutSteps=steps)],
    )


def _hash(day: dict) -> str:
    return hashlib.sha1(json.dumps([day["title"], day["steps"]], sort_keys=True).encode()).hexdigest()[:16]


def push_plan(user_id: int, api, days: list[dict], today: str) -> dict:
    """差異更新：只上傳有變動的天數，改成休息的天就刪掉原本的課。"""
    stats = {"uploaded": 0, "deleted": 0, "unchanged": 0, "errors": []}
    rows = {r["date"]: r for r in db.q("SELECT date, garmin_workout_id, garmin_pushed_hash FROM plan WHERE user_id=? AND date>=?",
                                       (user_id, today))}
    for day in days:
        if day["date"] < today:
            continue
        row = rows.get(day["date"], {})
        old_id, old_hash = row.get("garmin_workout_id"), row.get("garmin_pushed_hash")
        wants = bool(day.get("steps"))
        h = _hash(day) if wants else None
        if wants and h == old_hash and old_id:
            stats["unchanged"] += 1
            continue
        try:
            if old_id:
                api.delete_workout(old_id)  # 刪 workout 範本會一併移除行事曆上的排程
                stats["deleted"] += 1
            new_id = None
            if wants:
                res = api.upload_running_workout(build_running_workout(day))
                new_id = str(res.get("workoutId"))
                api.schedule_workout(new_id, day["date"])
                stats["uploaded"] += 1
            with db.tx() as c:
                c.execute("UPDATE plan SET garmin_workout_id=?, garmin_pushed_hash=? WHERE user_id=? AND date=?",
                          (new_id, h, user_id, day["date"]))
        except Exception as e:
            log.warning("推送 %s 失敗: %s", day["date"], e)
            stats["errors"].append(f"{day['date']}: {e}")
    return stats
