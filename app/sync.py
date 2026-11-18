"""同步流程：Garmin → 資料庫 → 計算 → 排課 →（選用）推回 Garmin。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta

from . import auth, db, metrics, planner
from .config import HISTORY_DAYS, TZ
from .garmin_client import provider_for, today_local

log = logging.getLogger(__name__)
_user_locks: dict[int, threading.Lock] = {}


def _lock(uid):
    return _user_locks.setdefault(uid, threading.Lock())


def store(user_id: int, acts: list[dict], days: list[dict]):
    cross = [a for a in acts if a.get("cross")]
    acts = [a for a in acts if not a.get("cross")]
    with db.tx() as c:
        for a in cross:
            c.execute(
                """INSERT OR REPLACE INTO cross_activities(user_id, activity_id, date, start_local, sport, sport_label, sport_group,
                       name, distance_m, duration_s, avg_hr, max_hr, calories, elevation_gain, training_load)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, a["activity_id"], a["date"], a.get("start_local"), a.get("sport"), a.get("sport_label"),
                 a.get("sport_group"), a.get("name"), a.get("distance_m"), a.get("duration_s"), a.get("avg_hr"),
                 a.get("max_hr"), a.get("calories"), a.get("elevation_gain"), a.get("training_load")))
        for a in acts:
            c.execute(
                """INSERT INTO activities(user_id, activity_id, date, start_local, type, name, distance_m,
                       duration_s, avg_hr, max_hr, training_load, avg_speed, calories, elevation_gain, avg_cadence)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, activity_id) DO UPDATE SET
                     date=excluded.date, name=excluded.name, distance_m=excluded.distance_m,
                     duration_s=excluded.duration_s, avg_hr=excluded.avg_hr, max_hr=excluded.max_hr,
                     training_load=excluded.training_load, avg_speed=excluded.avg_speed,
                     calories=excluded.calories, elevation_gain=excluded.elevation_gain, avg_cadence=excluded.avg_cadence""",
                (user_id, a["activity_id"], a["date"], a.get("start_local"), a.get("type"), a.get("name"),
                 a.get("distance_m"), a.get("duration_s"), a.get("avg_hr"), a.get("max_hr"),
                 a.get("training_load"), a.get("avg_speed"), a.get("calories"), a.get("elevation_gain"),
                 a.get("avg_cadence")))
        cols = ["rhr", "hrv_avg", "hrv_weekly", "hrv_status", "sleep_score", "sleep_hours", "body_battery", "stress_avg",
                "steps", "steps_goal", "active_kcal", "intensity_min", "bb_now", "bb_low"]
        for d in days:
            vals = [d.get(k) for k in cols]
            # 只覆寫有值的欄位（不同端點分批回來）
            c.execute(
                f"""INSERT INTO daily(user_id, date, {', '.join(cols)}) VALUES(?,?,{','.join('?' * len(cols))})
                    ON CONFLICT(user_id, date) DO UPDATE SET {', '.join(f'{k}=COALESCE(excluded.{k}, {k})' for k in cols)}""",
                (user_id, d["date"], *vals))


def load_state(user_id: int, today=None):
    today = today or today_local()
    start = today - timedelta(days=HISTORY_DAYS + 42)
    acts = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? ORDER BY date", (user_id, start.isoformat()))
    daily = db.q("SELECT * FROM daily WHERE user_id=? AND date>=? AND date<=? ORDER BY date",
                 (user_id, start.isoformat(), today.isoformat()))
    return today, start, acts, daily


def compute(user_id: int, today=None) -> dict:
    """算出儀表板需要的所有東西 + 新課表（不寫入）。"""
    prefs = db.get_prefs(user_id)
    today, start, acts, daily = load_state(user_id, today)
    cross = db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=?", (user_id, start.isoformat()))
    loads = metrics.daily_loads(acts, daily, prefs, start, today, cross)   # 其他運動也算進疲勞
    rows = metrics.pmc(loads)
    checkins = {r["date"]: r for r in db.q("SELECT * FROM checkins WHERE user_id=? AND date>=?",
                                             (user_id, today.isoformat()))}
    first = db.q1("SELECT MIN(date) d FROM activities WHERE user_id=?", (user_id,))["d"]
    runs28 = sum(1 for a in acts if a["date"] >= (today - timedelta(days=28)).isoformat())
    novice = (not first) or (today - date.fromisoformat(first)).days < 21 or runs28 < 6
    ready = metrics.readiness(today.isoformat(), rows, daily, checkins.get(today.isoformat()), novice)
    paces = metrics.estimate_paces(acts, prefs)
    yesterday = today - timedelta(days=1)
    recent = metrics.weekly_minutes(acts, yesterday, 4)
    base_wk = planner.base_weeks(metrics.weekly_minutes(acts, yesterday, 16))
    ctl = rows[-1]["ctl"] if rows else 0
    tsb = next((r["tsb"] for r in rows if r["date"] == today.isoformat()), 0)
    rel_tsb = 0 if novice else tsb / max(ctl, 10)
    recent_kinds = [json.loads(r["data"]).get("kind") for r in db.q(
        "SELECT data FROM plan WHERE user_id=? AND date>=? AND date<? ORDER BY date",
        (user_id, (today - timedelta(days=21)).isoformat(), today.isoformat()))]
    todays = [a for a in acts if a["date"] == today.isoformat()]
    done = {a["date"] for a in todays}
    done_min = sum((a.get("duration_s") or 0) / 60 for a in todays)
    overrides = {r["date"]: r for r in db.q("SELECT date, kind, minutes FROM plan_overrides WHERE user_id=? AND date>=?",
                                             (user_id, today.isoformat()))}
    plan = planner.make_plan(today=today, prefs=prefs, paces=paces, recent_weeks=recent, rel_tsb=rel_tsb,
                             ready=ready, checkins=checkins, done_dates=done, recent_kinds=recent_kinds,
                             done_minutes=done_min, overrides=overrides, novice=novice,
                             base_wk=base_wk)
    return {"novice": novice, "prefs": prefs, "pmc": rows[-60:], "readiness": ready, "paces": paces,
            "recent_weeks": [round(w) for w in recent], "plan": plan,
            "today_metrics": next((d for d in reversed(daily)), None),
            "recent_activities": acts[-10:][::-1],
            "checkin": checkins.get(today.isoformat())}


def save_plan(user_id: int, plan: dict):
    with db.tx() as c:
        for d in plan["days"]:
            if d["kind"] == "done" and c.execute("SELECT 1 FROM plan WHERE user_id=? AND date=?",
                                                 (user_id, d["date"])).fetchone():
                continue  # 保留跑之前排的課，訓練報告才能對照「計畫 vs 實際」
            c.execute("INSERT INTO plan(user_id, date, data) VALUES(?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET data=excluded.data",
                      (user_id, d["date"], json.dumps(d, ensure_ascii=False)))
        meta = {k: v for k, v in plan.items() if k != "days"}
        c.execute("INSERT INTO plan_meta(user_id, data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                  (user_id, json.dumps(meta, ensure_ascii=False)))


def replan(user_id: int, api=None) -> dict:
    result = compute(user_id)
    save_plan(user_id, result["plan"])
    prefs = result["prefs"]
    if api is not None and prefs.get("push_to_garmin") and hasattr(api, "upload_running_workout"):
        from .garmin_push import push_plan
        result["garmin_push"] = push_plan(user_id, api, result["plan"]["days"], result["plan"]["generated_for"])
    return result


def sync_user(user_id: int, full: bool = False) -> dict:
    with _lock(user_id):
        user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
        if user["provider"] == "apple":
            # Apple 沒有雲端可以抓，資料是 iPhone 捷徑送進來的；這裡只依現有資料重排課表
            result = replan(user_id)
            result["synced"] = {"activities": 0, "other_activities": 0, "days": 0, "apple": True}
            return result
        tokens = auth.decrypt(user["garmin_tokens"])
        try:
            prov = provider_for(user, tokens)
            if prov is None:
                raise RuntimeError("尚未連結 Garmin 帳號")
            today = today_local()
            has_data = db.q1("SELECT 1 AS x FROM activities WHERE user_id=? LIMIT 1", (user_id,))
            start = today - timedelta(days=HISTORY_DAYS if (full or not has_data) else 10)
            acts, days = prov.fetch(start, today)
            store(user_id, acts, days)
            if user["provider"] == "demo":
                tpl = {"輕鬆跑": ("easy", "輕鬆跑", 40, "easy"), "節奏跑": ("tempo", "節奏跑", 45, "hard"),
                       "長距離": ("long", "長距離慢跑", 85, "moderate")}
                with db.tx() as c:
                    for x in acts:
                        k = tpl.get(x.get("name"))
                        if k:
                            c.execute("INSERT OR IGNORE INTO plan(user_id, date, data) VALUES(?,?,?)",
                                      (user_id, x["date"], json.dumps({"date": x["date"], "kind": k[0], "title": k[1],
                                                                       "minutes": k[2], "intensity": k[3], "steps": []}, ensure_ascii=False)))
            dev = prov.fetch_device() if hasattr(prov, "fetch_device") else None
            if dev:
                with db.tx() as c:
                    c.execute("INSERT INTO device(user_id, data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                              (user_id, json.dumps(dev, ensure_ascii=False)))
            if hasattr(prov, "fetch_metrics"):
                from . import runner
                try:
                    runner.save_metrics(user_id, prov.fetch_metrics())
                except Exception as e:
                    log.warning("跑者數值抓取失敗: %s", e)
            recs = prov.fetch_records() if hasattr(prov, "fetch_records") else None
            if recs:
                with db.tx() as c:
                    for r in recs:
                        c.execute("INSERT OR REPLACE INTO records(user_id, rtype, value, activity_id, date) VALUES(?,?,?,?,?)",
                                  (user_id, r["rtype"], r["value"], r["activity_id"], r["date"]))
            if user["provider"] == "garmin":
                new_tokens = prov.tokens()
                if new_tokens and new_tokens != tokens:
                    with db.tx() as c:
                        c.execute("UPDATE users SET garmin_tokens=? WHERE id=?", (auth.encrypt(new_tokens), user_id))
            result = replan(user_id, getattr(prov, "api", None))
            with db.tx() as c:
                c.execute("UPDATE users SET last_sync=?, last_sync_error=NULL WHERE id=?",
                          (datetime.now(TZ).isoformat(timespec="seconds"), user_id))
            result["synced"] = {"activities": sum(1 for a in acts if not a.get("cross")),
                                "other_activities": sum(1 for a in acts if a.get("cross")), "days": len(days)}
            return result
        except Exception as e:
            log.exception("同步失敗 user=%s", user_id)
            with db.tx() as c:
                c.execute("UPDATE users SET last_sync_error=? WHERE id=?", (str(e)[:500], user_id))
            raise


def sync_all():
    for u in db.q("SELECT id FROM users WHERE provider IN ('garmin','demo','apple')"):
        try:
            sync_user(u["id"])
        except Exception:
            pass  # 已記錄在 last_sync_error
