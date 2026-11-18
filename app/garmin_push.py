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
    from garminconnect.workout import (PaceTarget, create_cooldown_step, create_recovery_step,
                                       create_repeat_group, create_targeted_interval_step,
                                       create_warmup_step)

    def pace_target(s):
        if not s.get("pace_lo"):
            return None
        # Garmin 用 m/s；慢配速 = 低速度
        return PaceTarget(lower_limit=1000 / s["pace_lo"], upper_limit=1000 / s["pace_hi"])

    def tt(s):
        t = pace_target(s)
        if not t:
            return None
        return {"workoutTargetTypeId": t.target_type, "workoutTargetTypeKey": t.target_type_key, "displayOrder": 1}

    out = []
    for s in steps:
        counter[0] += 1
        order = counter[0]
        secs = s.get("minutes", 0) * 60
        if s["type"] == "repeat":
            inner = _to_garmin_steps(s["steps"], counter)
            out.append(create_repeat_group(s["n"], inner, order))
            continue
        if s["type"] in ("warmup", "cooldown", "recovery"):
            fn = {"warmup": create_warmup_step, "cooldown": create_cooldown_step,
                  "recovery": create_recovery_step}[s["type"]]
            st = fn(secs, order, target_type=tt(s))
            if st.targetType and s.get("pace_lo"):
                st.targetValueOne, st.targetValueTwo = 1000 / s["pace_lo"], 1000 / s["pace_hi"]
        else:
            st = create_targeted_interval_step(secs, order, target=pace_target(s))
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
