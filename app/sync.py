"""同步流程：Garmin → 資料庫 → 計算 → 排課 →（選用）推回 Garmin。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta

from . import athlete, auth, db, metrics, season
from .config import HISTORY_DAYS, TZ
from .garmin_client import provider_for, today_local

log = logging.getLogger(__name__)
_user_locks: dict[int, threading.Lock] = {}


def _lock(uid):
    return _user_locks.setdefault(uid, threading.Lock())


def store(user_id: int, acts: list[dict], days: list[dict]):
    with db.tx() as c:
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
                "steps", "steps_goal", "active_kcal", "intensity_min"]
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
    """算出儀表板需要的所有東西 + 新課表（不寫入）。排課策略在 season.daily_plan（規格書九層）。"""
    prefs = db.get_prefs(user_id)
    today, start, acts, daily = load_state(user_id, today)
    loads = metrics.daily_loads(acts, daily, prefs, start, today)
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
    done = {a["date"] for a in acts if a["date"] == today.isoformat()}
    plan = season.daily_plan(user_id, today, checkins=checkins, done_dates=done, ready=ready)
    return {"novice": novice, "prefs": prefs, "pmc": rows[-60:], "readiness": ready, "paces": paces,
            "recent_weeks": [round(w) for w in recent], "plan": plan,
            "today_metrics": next((d for d in reversed(daily)), None),
            "recent_activities": acts[-10:][::-1],
            "checkin": checkins.get(today.isoformat())}


def enrich_recent(user_id: int, prov, days: int = 14, limit: int = 6):
    """新跑步補上心率漂移（第 6 層回歸、第 9 層過度訓練偵測）和天氣（WBGT）。每次同步最多抓幾趟，避免打太多請求。"""
    since = (today_local() - timedelta(days=days)).isoformat()
    rows = db.q("SELECT * FROM activities WHERE user_id=? AND date>=? AND (hr_drift IS NULL OR weather_checked IS NULL) "
                "ORDER BY date DESC", (user_id, since))
    fetched = 0
    for a in rows:
        aid = a["activity_id"]
        if a.get("hr_drift") is None:
            cached = db.q1("SELECT data FROM activity_series WHERE user_id=? AND activity_id=?", (user_id, aid))
            series = json.loads(cached["data"]) if cached else None
            if not series and fetched < limit and hasattr(prov, "fetch_series"):
                fetched += 1
                try:
                    series = prov.fetch_series(aid, a) if getattr(prov, "is_demo", False) else prov.fetch_series(aid)
                    if series and series.get("points"):
                        with db.tx() as c:
                            c.execute("INSERT OR REPLACE INTO activity_series(user_id, activity_id, data) VALUES(?,?,?)",
                                      (user_id, aid, json.dumps(series)))
                except Exception as e:
                    log.warning("series %s: %s", aid, e)
            drift = athlete.compute_drift((series or {}).get("points") or [])
            if drift is not None:
                with db.tx() as c:
                    c.execute("UPDATE activities SET hr_drift=? WHERE user_id=? AND activity_id=?", (drift, user_id, aid))
        if a.get("weather_checked") is None and hasattr(prov, "fetch_weather"):
            w = None
            try:
                w = prov.fetch_weather(aid, a)
            except Exception as e:
                log.warning("weather %s: %s", aid, e)
            with db.tx() as c:
                c.execute("UPDATE activities SET temp_c=?, humidity=?, weather_checked=1 WHERE user_id=? AND activity_id=?",
                          ((w or {}).get("temp_c"), (w or {}).get("humidity"), user_id, aid))


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
            enrich_recent(user_id, prov, days=120 if (full or not has_data) else 14, limit=40 if (full or not has_data) else 6)
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
            result["synced"] = {"activities": len(acts), "days": len(days)}
            return result
        except Exception as e:
            log.exception("同步失敗 user=%s", user_id)
            with db.tx() as c:
                c.execute("UPDATE users SET last_sync_error=? WHERE id=?", (str(e)[:500], user_id))
            raise


def sync_all():
    for u in db.q("SELECT id FROM users WHERE provider IN ('garmin','demo')"):
        try:
            sync_user(u["id"])
        except Exception:
            pass  # 已記錄在 last_sync_error
