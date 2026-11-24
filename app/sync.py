"""同步流程：Garmin → 資料庫 → 計算 → 排課 →（選用）推回 Garmin。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta

from . import athlete, auth, db, metrics, season
from .config import HISTORY_DAYS, POLL_HOURS, TZ
from .garmin_client import provider_for, today_local

log = logging.getLogger(__name__)
_user_locks: dict[int, threading.Lock] = {}


def _lock(uid):
    return _user_locks.setdefault(uid, threading.Lock())


def store(user_id: int, acts: list[dict], days: list[dict]):
    cross = [a for a in acts if a.get("cross")]
    acts = [a for a in acts if not a.get("cross")]
    with db.tx() as c:
        for a in cross:   # 其他運動（騎車、重訓…）：算進活動量，不算跑量
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
    """算出儀表板需要的所有東西 + 新課表（不寫入）。排課策略在 season.daily_plan（規格書九層）。"""
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


def sync_user(user_id: int, full: bool = False, days: int | None = None, prov=None) -> dict:
    """days：一般同步往回抓幾天（預設 10）；自動偵測只抓 2 天。prov：已經建好的連線就沿用，不用再載入一次 token。"""
    with _lock(user_id):
        user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
        if user["provider"] == "apple":
            # Apple 沒有雲端可以抓，資料是 iPhone 捷徑送進來的；這裡只依現有資料重排課表
            result = replan(user_id)
            result["synced"] = {"activities": 0, "other_activities": 0, "days": 0, "apple": True}
            return result
        tokens = auth.decrypt(user["garmin_tokens"])
        try:
            prov = prov or provider_for(user, tokens)
            if prov is None:
                raise RuntimeError("尚未連結 Garmin 帳號")
            today = today_local()
            has_data = db.q1("SELECT 1 AS x FROM activities WHERE user_id=? LIMIT 1", (user_id,))
            start = today - timedelta(days=HISTORY_DAYS if (full or not has_data) else (days or 10))
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


# ───────── 自動偵測：在 Garmin Connect 同步後，這裡自己把資料抓進來 ─────────
_poll_backoff: dict[int, tuple[int, datetime]] = {}   # user_id → (連續失敗次數, 下次再試的時間)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def needs_sync(upload: str | None, seen: str | None, last_sync: str | None) -> bool:
    """手錶上傳時間跟上次看到的不一樣，而且比上次同步還晚，才需要同步。"""
    up = _parse_dt(upload)
    if up is None or upload == seen:
        return False
    ls = _parse_dt(last_sync)
    return ls is None or up > ls


def poll_user(user_id: int, notify=None) -> dict | None:
    """問一次「手錶最後上傳時間」；有新上傳就只同步最近 2 天，抓到新活動就推播。回傳同步結果，沒同步回傳 None。"""
    user = db.q1("SELECT * FROM users WHERE id=?", (user_id,))
    if not user or user["provider"] != "garmin" or not user["garmin_tokens"]:
        return None
    tokens = auth.decrypt(user["garmin_tokens"])
    prov = provider_for(user, tokens)
    if prov is None or not hasattr(prov, "last_upload"):
        return None
    upload = prov.last_upload()
    if not needs_sync(upload, user.get("last_upload_seen"), user.get("last_sync")):
        if upload and upload != user.get("last_upload_seen"):
            with db.tx() as c:
                c.execute("UPDATE users SET last_upload_seen=? WHERE id=?", (upload, user_id))
        new_tokens = prov.tokens()
        if new_tokens and new_tokens != tokens:
            with db.tx() as c:
                c.execute("UPDATE users SET garmin_tokens=? WHERE id=?", (auth.encrypt(new_tokens), user_id))
        return None
    since = (today_local() - timedelta(days=2)).isoformat()
    before_runs = {r["activity_id"] for r in db.q("SELECT activity_id FROM activities WHERE user_id=? AND date>=?", (user_id, since))}
    before_cross = {r["activity_id"] for r in db.q("SELECT activity_id FROM cross_activities WHERE user_id=? AND date>=?", (user_id, since))}
    result = sync_user(user_id, days=2, prov=prov)
    with db.tx() as c:
        c.execute("UPDATE users SET last_upload_seen=? WHERE id=?", (upload, user_id))
    new_runs = [r for r in db.q("SELECT * FROM activities WHERE user_id=? AND date>=? ORDER BY date DESC, start_local DESC",
                                (user_id, since)) if r["activity_id"] not in before_runs]
    new_cross = [r for r in db.q("SELECT * FROM cross_activities WHERE user_id=? AND date>=? ORDER BY date DESC, start_local DESC",
                                 (user_id, since)) if r["activity_id"] not in before_cross]
    send = notify
    if send is None:
        from . import push
        send = push.send
    try:
        if new_runs:
            a = new_runs[0]
            km = (a.get("distance_m") or 0) / 1000
            send(user_id, "新的跑步已同步", f"{a.get('name') or '跑步'} {km:.1f} km，來看訓練報告",
                 f"/#/workout/{a['activity_id']}")
        elif new_cross:
            a = new_cross[0]
            send(user_id, "新的活動已同步", f"{a.get('sport_label') or a.get('name') or '運動'} "
                 f"{round((a.get('duration_s') or 0) / 60)} 分，已算進總活動量", "/#/activity")
    except Exception as e:
        log.warning("同步通知送不出去 user=%s: %s", user_id, e)
    result["new_activities"] = len(new_runs) + len(new_cross)
    return result


def poll_all(now: datetime | None = None):
    """排程每幾分鐘呼叫一次。只在 POLL_HOURS 時段內做；失敗的帳號逐步拉長間隔，避免一直打 Garmin。"""
    now = now or datetime.now(TZ)
    lo, hi = POLL_HOURS
    if not (lo <= now.hour < hi):
        return
    for u in db.q("SELECT id FROM users WHERE provider='garmin' AND garmin_tokens IS NOT NULL"):
        uid = u["id"]
        fails, until = _poll_backoff.get(uid, (0, None))
        if until and now < until:
            continue
        try:
            poll_user(uid)
            _poll_backoff.pop(uid, None)
        except Exception as e:
            fails += 1
            _poll_backoff[uid] = (fails, now + timedelta(minutes=min(240, 10 * 2 ** fails)))
            log.warning("自動偵測失敗 user=%s（第 %s 次）: %s", uid, fails, e)


def sync_all():
    for u in db.q("SELECT id FROM users WHERE provider IN ('garmin','demo','apple')"):
        try:
            sync_user(u["id"])
        except Exception:
            pass  # 已記錄在 last_sync_error
