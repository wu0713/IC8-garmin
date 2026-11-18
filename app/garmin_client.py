"""Garmin 資料來源。

- RealGarmin：用 GitHub 開源的 python-garminconnect（非官方、免費）。
  它模擬官方 Garmin Connect 手機 App 的登入流程，取得 token 後就不需要再存密碼。
- DemoGarmin：產生擬真假資料，給朋友試玩、或 Garmin 那邊暫時壞掉時測試用。

兩者都輸出同樣的「正規化」格式，下游（排課引擎）完全不用管資料從哪來。
"""
from __future__ import annotations

import json
import logging
import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

RUN_TYPES = {"running", "trail_running", "treadmill_running", "track_running",
             "indoor_running", "street_running", "virtual_run", "ultra_run"}


def _find(obj: Any, keys: tuple[str, ...], depth: int = 0):
    """在巢狀 dict/list 中找第一個符合 key 的數值。Garmin 回傳格式常變，用這招比較耐打。"""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
            if isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
                return float(v["value"])
        for v in obj.values():
            if isinstance(v, (dict, list)):
                r = _find(v, keys, depth + 1)
                if r is not None:
                    return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, keys, depth + 1)
            if r is not None:
                return r
    return None


# 其他運動：名稱與分類（分類決定圖示顏色；沒列到的用 Garmin 的英文名）
SPORTS = {
    "cycling": ("騎車", "bike"), "road_biking": ("公路車", "bike"), "indoor_cycling": ("室內車", "bike"),
    "virtual_ride": ("虛擬騎乘", "bike"), "mountain_biking": ("登山車", "bike"), "gravel_cycling": ("礫石車", "bike"),
    "e_bike_fitness": ("電輔車", "bike"), "lap_swimming": ("游泳（泳池）", "swim"), "open_water_swimming": ("開放水域游泳", "swim"),
    "swimming": ("游泳", "swim"), "strength_training": ("重量訓練", "gym"), "hiit": ("HIIT", "gym"), "cardio": ("有氧器材", "gym"),
    "indoor_cardio": ("室內有氧", "gym"), "fitness_equipment": ("健身器材", "gym"), "elliptical": ("滑步機", "gym"),
    "stair_climbing": ("爬樓梯", "gym"), "indoor_rowing": ("划船機", "gym"), "rowing": ("划船", "swim"),
    "yoga": ("瑜珈", "calm"), "pilates": ("皮拉提斯", "calm"), "breathwork": ("呼吸練習", "calm"),
    "walking": ("走路", "walk"), "hiking": ("登山健行", "walk"), "mountaineering": ("登山", "walk"),
    "tennis": ("網球", "ball"), "badminton": ("羽球", "ball"), "basketball": ("籃球", "ball"), "soccer": ("足球", "ball"),
    "table_tennis": ("桌球", "ball"), "volleyball": ("排球", "ball"), "pickleball": ("匹克球", "ball"), "golf": ("高爾夫", "ball"),
}


def sport_info(type_key: str) -> tuple[str, str]:
    return SPORTS.get(type_key) or (type_key.replace("_", " ").title() if type_key else "其他運動", "other")


def is_run(type_key: str) -> bool:
    return type_key in RUN_TYPES or "run" in (type_key or "")


def normalize_activity(a: dict) -> dict | None:
    """跑步回傳跑步紀錄；其他運動回傳 cross=True 的紀錄（另存一張表，算進活動量但不算跑量）。"""
    t = (a.get("activityType") or {}).get("typeKey", "")
    start = a.get("startTimeLocal") or ""
    if not is_run(t):
        label, group = sport_info(t)
        return {
            "cross": True, "activity_id": str(a.get("activityId")), "date": start[:10], "start_local": start,
            "sport": t or "other", "sport_label": label, "sport_group": group, "name": a.get("activityName") or label,
            "distance_m": a.get("distance"), "duration_s": a.get("movingDuration") or a.get("duration"),
            "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR"), "calories": a.get("calories"),
            "elevation_gain": a.get("elevationGain"), "training_load": a.get("activityTrainingLoad"),
        }
    return {
        "activity_id": str(a.get("activityId")),
        "date": start[:10],
        "start_local": start,
        "type": t,
        "name": a.get("activityName"),
        "distance_m": a.get("distance"),
        "duration_s": a.get("movingDuration") or a.get("duration"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "training_load": a.get("activityTrainingLoad"),
        "avg_speed": a.get("averageSpeed"),
        "calories": a.get("calories"),
        "elevation_gain": a.get("elevationGain"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute"),
    }


def _chunks(start: date, end: date, days: int = 28):
    cur = start
    while cur <= end:
        ce = min(cur + timedelta(days=days - 1), end)
        yield cur.isoformat(), ce.isoformat()
        cur = ce + timedelta(days=1)


def parse_series(details: dict) -> dict:
    """Garmin activity details → 每 ~10 秒一點的時間序列（心率、配速、海拔、步頻）。"""
    desc = {d.get("key"): d.get("metricsIndex") for d in (details or {}).get("metricDescriptors") or []}
    rows = (details or {}).get("activityDetailMetrics") or []

    def col(key):
        i = desc.get(key)
        return [(r.get("metrics") or [None] * (i + 1))[i] if i is not None and i < len(r.get("metrics") or []) else None
                for r in rows]

    t = col("sumDuration") or col("sumElapsedDuration")
    hr, spd = col("directHeartRate"), col("directSpeed")
    ele, cad = col("directElevation"), col("directRunCadence")
    pts = []
    for k in range(len(rows)):
        if t[k] is None:
            continue
        pts.append({"t": round(t[k]), "hr": hr[k] and round(hr[k]),
                    "pace": round(1000 / spd[k]) if spd[k] and spd[k] > 0.5 else None,
                    "ele": ele[k] and round(ele[k], 1), "cad": cad[k] and round(cad[k])})
    # 降到最多 300 點，手機畫圖比較順
    step = max(1, len(pts) // 300)
    return {"points": pts[::step]}


PR_TYPES = {1: "1k", 2: "1mi", 3: "5k", 4: "10k", 5: "half", 6: "full", 7: "longest"}


def normalize_records(raw) -> list[dict]:
    """Garmin 個人紀錄（含歷史）→ [{rtype, value, activity_id, date}]。"""
    rows = raw if isinstance(raw, list) else (raw or {}).get("personalRecords") or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rtype = PR_TYPES.get(r.get("typeId"))
        at = str(r.get("activityType") or "running")
        if not rtype or "run" not in at or not isinstance(r.get("value"), (int, float)):
            continue
        d = (r.get("prStartTimeLocalFormatted") or r.get("prStartTimeGmtFormatted")
             or r.get("actStartDateTimeInGMTFormatted") or "")[:10]
        if not d:
            continue
        out.append({"rtype": rtype, "value": float(r["value"]),
                    "activity_id": str(r.get("activityId") or f"{rtype}-{d}"), "date": d})
    return out


# ───────────────────────────── 真正的 Garmin ─────────────────────────────

class MfaRequired(Exception):
    def __init__(self, state: dict):
        super().__init__("需要 MFA 驗證碼")
        self.state = state


class RealGarmin:
    @staticmethod
    def start_login(email: str, password: str) -> str:
        """回傳 token JSON；若帳號開了兩步驟驗證，丟出 MfaRequired(state)。"""
        from garminconnect import Garmin

        api = Garmin(email, password, return_on_mfa=True)
        status, state = api.login()
        if status == "needs_mfa":
            raise MfaRequired(state)
        return api.client.dumps()

    @staticmethod
    def finish_mfa(state: dict, code: str) -> str:
        from garminconnect import Garmin

        api = Garmin()
        api.resume_login(state, code)
        return api.client.dumps()

    def __init__(self, tokens_json: str):
        from garminconnect import Garmin

        self.api = Garmin()
        self.api.login(tokenstore=tokens_json)

    def tokens(self) -> str:
        """token 可能在呼叫過程中被刷新，同步完要存回去。"""
        return self.api.client.dumps()

    def _safe(self, name, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # 單一指標失敗不要拖垮整個同步
            log.warning("Garmin %s 失敗: %s", name, e)
            return None

    def fetch(self, start: date, end: date) -> tuple[list[dict], list[dict]]:
        api = self.api
        s, e = start.isoformat(), end.isoformat()
        raw = api.get_activities_by_date(s, e) or []   # 全部運動：跑步算跑量，其他運動算進活動量
        acts = [x for x in (normalize_activity(a) for a in raw) if x]

        days: dict[str, dict] = {}

        def day(d):
            return days.setdefault(d, {"date": d})

        for cs, ce in _chunks(start, end):
            for row in self._safe("rhr", api.get_rhr_daily, cs, ce) or []:
                if row.get("calendarDate"):
                    day(row["calendarDate"])["rhr"] = row.get("value")
            data = self._safe("hrv", api.get_hrv_data_range, cs, ce) or {}
            for h in data.get("hrvSummaries") or []:
                d = day(h.get("calendarDate"))
                d["hrv_avg"] = h.get("lastNightAvg")
                d["hrv_weekly"] = h.get("weeklyAvg")
                d["hrv_status"] = h.get("status")
            for row in self._safe("sleep", api.get_sleep_daily, cs, ce) or []:
                d = day(row.get("calendarDate"))
                d["sleep_score"] = _find(row, ("sleepScore", "overallSleepScore", "sleepScoreValue", "overall"))
                secs = _find(row, ("totalSleepTimeInSeconds", "sleepTimeSeconds", "totalSleepSeconds"))
                d["sleep_hours"] = round(secs / 3600, 2) if secs else None
            for row in self._safe("stress", api.get_daily_stress_stats, cs, ce) or []:
                if isinstance(row, dict) and row.get("calendarDate"):
                    day(row["calendarDate"])["stress_avg"] = _find(row, ("overallStressLevel", "averageStressLevel", "avgStress"))

        for row in self._safe("steps", api.get_daily_steps, s, e) or []:
            if row.get("calendarDate"):
                d = day(row["calendarDate"])
                d["steps"] = row.get("totalSteps")
                d["steps_goal"] = row.get("stepGoal")

        # Body Battery、卡路里、強度分鐘：只抓最近 14 天（每天一次請求，避免打太多）
        recent = max(start, end - timedelta(days=13))
        for row in self._safe("body_battery", api.get_body_battery, recent.isoformat(), e) or []:
            vals = [v[1] for v in (row.get("bodyBatteryValuesArray") or [])
                    if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], (int, float))]
            if row.get("date") and vals:
                day(row["date"])["body_battery"] = max(vals)
        d0 = recent
        while d0 <= end:
            us = self._safe("user_summary", api.get_user_summary, d0.isoformat()) or {}
            if us:
                d = day(d0.isoformat())
                d["active_kcal"] = us.get("activeKilocalories")
                mod, vig = us.get("moderateIntensityMinutes") or 0, us.get("vigorousIntensityMinutes") or 0
                d["intensity_min"] = mod + 2 * vig
                d["steps"] = us.get("totalSteps") or d.get("steps")
                if us.get("bodyBatteryMostRecentValue") is not None and not d.get("body_battery"):
                    d["body_battery"] = us.get("bodyBatteryHighestValue")
                d["bb_now"] = us.get("bodyBatteryMostRecentValue")
                d["bb_low"] = us.get("bodyBatteryLowestValue")
                if us.get("restingHeartRate") and not d.get("rhr"):
                    d["rhr"] = us.get("restingHeartRate")
            d0 += timedelta(days=1)

        return acts, [d for k, d in days.items() if k]

    def fetch_device(self) -> dict | None:
        devs = self._safe("devices", self.api.get_devices) or []
        last = self._safe("device_last_used", self.api.get_device_last_used) or {}
        if not isinstance(devs, list):
            devs = []
        if not devs and not last:
            return None

        def name_of(d):
            for k in ("productDisplayName", "displayName", "deviceName", "partNumberDescription", "deviceTypeName", "modelName"):
                if d.get(k):
                    return str(d[k])
            return None

        # 以「最後同步的那支」為準（帳號裡可能還留著舊手錶）
        last_id = last.get("userDeviceId") or last.get("deviceId")
        dev = next((d for d in devs if last_id and str(d.get("deviceId")) == str(last_id)), None)
        dev = dev or next((d for d in devs if name_of(d) and name_of(d) == last.get("lastUsedDeviceName")), None)
        dev = dev or (devs[0] if devs else {})
        up = last.get("lastUsedDeviceUploadTime")
        return {
            "name": last.get("lastUsedDeviceName") or name_of(dev) or "Garmin 裝置", "brand": "Garmin",
            "firmware": dev.get("currentFirmwareVersion") or dev.get("softwareVersionString") or dev.get("firmwareVersion"),
            "image": last.get("imageUrl") or dev.get("imageUrl"),
            "last_upload": datetime.fromtimestamp(up / 1000, timezone.utc).isoformat() if isinstance(up, (int, float)) else None,
            "count": len(devs), "others": [n for n in (name_of(d) for d in devs) if n][:6],
        }

    def diagnose(self) -> list[dict]:
        """逐項呼叫 Garmin，回報哪些資料抓得到。給「連線檢查」頁用。"""
        api = self.api
        t = today_local()
        s7, e = (t - timedelta(days=6)).isoformat(), t.isoformat()
        s30 = (t - timedelta(days=29)).isoformat()
        out = []

        def check(key, label, fn, summarize, needs=""):
            try:
                data = fn()
                n, detail = summarize(data)
                out.append({"key": key, "label": label, "ok": bool(n), "empty": not n, "detail": detail, "needs": needs})
                return data
            except Exception as ex:
                out.append({"key": key, "label": label, "ok": False, "empty": False, "detail": f"{type(ex).__name__}: {ex}"[:160], "needs": needs})
                return None

        def rows(key=None, val=None):
            def f(d):
                lst = d.get(key) if key and isinstance(d, dict) else d
                lst = [x for x in (lst or []) if not val or _find(x, val) is not None]
                return len(lst), f"近 7 天有 {len(lst)} 天有資料" if lst else "沒有資料"
            return f

        acts = check("activities", "跑步活動", lambda: api.get_activities_by_date(s30, e, "running"),
                     lambda d: (len(d or []), f"近 30 天 {len(d or [])} 筆"))
        dv = check("devices", "手錶清單", api.get_devices, lambda d: (len(d or []), "、".join(
            str(x.get("productDisplayName") or x.get("displayName") or x.get("deviceTypeName") or "?") for x in (d or [])) or "帳號裡沒有裝置"))
        check("last_device", "最後同步的手錶", api.get_device_last_used,
              lambda d: (bool(d), str((d or {}).get("lastUsedDeviceName") or "Garmin 沒有回傳名稱")))
        check("steps", "步數", lambda: api.get_daily_steps(s7, e), rows(val=("totalSteps",)), "全天配戴")
        check("rhr", "安靜心率", lambda: api.get_rhr_daily(s7, e), rows(val=("value",)), "全天配戴")
        check("sleep", "睡眠", lambda: api.get_sleep_daily(s7, e), rows(val=("totalSleepTimeInSeconds", "sleepTimeSeconds", "totalSleepSeconds")), "戴著睡覺")
        check("hrv", "HRV", lambda: api.get_hrv_data_range(s7, e), rows("hrvSummaries", ("lastNightAvg",)), "戴著睡覺；新手錶要先戴約 3 週建立基準")
        check("stress", "壓力", lambda: api.get_daily_stress_stats(s7, e), rows(val=("overallStressLevel", "averageStressLevel", "avgStress")), "全天配戴")
        check("body_battery", "Body Battery", lambda: api.get_body_battery(s7, e),
              lambda d: (sum(1 for x in (d or []) if x.get("bodyBatteryValuesArray")), f"近 7 天有 {sum(1 for x in (d or []) if x.get('bodyBatteryValuesArray'))} 天有資料"), "全天配戴")
        check("summary", "活動熱量、強度分鐘", lambda: api.get_user_summary(e),
              lambda d: ((d or {}).get("activeKilocalories") is not None, f"今天活動熱量 {(d or {}).get('activeKilocalories')} kcal"))
        check("records", "個人最佳（PB）", lambda: api.get_personal_record(True), lambda d: (len(normalize_records(d)), f"{len(normalize_records(d))} 筆跑步紀錄"))
        if acts:
            aid = str(acts[0].get("activityId"))
            check("series", "單趟心率／配速曲線", lambda: parse_series(api.get_activity_details(aid, maxchart=300, maxpoly=0)),
                  lambda d: (len(d["points"]), f"最近一趟有 {len(d['points'])} 個資料點"))
        check("workouts", "課表庫（推課表需要）", lambda: api.get_workouts(0, 20),
              lambda d: (True, f"目前有 {len(d or [])} 堂課，其中 {sum(1 for x in (d or []) if str(x.get('workoutName', '')).startswith('[IC8]'))} 堂是跑課建立的"))
        return out

    def push_test(self) -> dict:
        """建立一堂 10 分鐘的測試課、排到明天、直接送到手錶。"""
        from garminconnect.workout import RunningWorkout, WorkoutSegment, create_cooldown_step, create_interval_step, create_warmup_step

        steps = [create_warmup_step(180, 1), create_interval_step(300, 2), create_cooldown_step(120, 3)]
        w = RunningWorkout(workoutName="[IC8] 連線測試", estimatedDurationInSecs=600, description="跑課 App 的測試課，可以刪除。",
                           workoutSegments=[WorkoutSegment(segmentOrder=1, sportType={"sportTypeId": 1, "sportTypeKey": "running"}, workoutSteps=steps)])
        res = {"uploaded": False, "scheduled": False, "sent": False, "errors": []}
        r = self.api.upload_running_workout(w)
        wid = str(r.get("workoutId"))
        res.update(uploaded=True, workout_id=wid)
        day = (today_local() + timedelta(days=1)).isoformat()
        try:
            self.api.schedule_workout(wid, day)
            res.update(scheduled=True, date=day)
        except Exception as ex:
            res["errors"].append(f"排到行事曆失敗：{ex}"[:160])
        try:
            self.api.push_workout_to_device(wid)
            res["sent"] = True
        except Exception as ex:
            res["errors"].append(f"直接送到手錶失敗：{ex}"[:160])
        return res

    def delete_test(self, workout_id: str) -> bool:
        w = self.api.get_workout_by_id(workout_id) or {}
        if not str(w.get("workoutName", "")).startswith("[IC8]"):
            raise ValueError("這不是跑課建立的課表，不會刪除")
        self.api.delete_workout(workout_id)
        return True

    def fetch_series(self, activity_id: str) -> dict:
        return parse_series(self.api.get_activity_details(activity_id, maxchart=1500, maxpoly=0))

    def fetch_records(self) -> list[dict] | None:
        raw = self._safe("personal_record", self.api.get_personal_record, True)
        return normalize_records(raw) if raw is not None else None


# ───────────────────────────── Demo 假資料 ─────────────────────────────

class DemoGarmin:
    """可重現的擬真資料：每週 4 跑、偶爾漏跑、HRV/睡眠有日常起伏。"""

    def __init__(self, seed: int = 8):
        self.seed = seed

    def fetch_device(self):
        from .config import TZ
        return {"name": "Forerunner 165（範例）", "brand": "Garmin", "firmware": "29.05", "image": None,
                "last_upload": datetime.now(TZ).replace(minute=0, second=0, microsecond=0).isoformat(), "count": 1}

    def fetch_series(self, activity_id: str, act: dict | None = None) -> dict:
        """依活動的距離、時間、平均心率產生合理的心率／配速曲線。"""
        act = act or {}
        dur = act.get("duration_s") or 1800
        avg_hr = act.get("avg_hr") or 145
        pace = (dur / ((act.get("distance_m") or 5000) / 1000))
        tempo = (act.get("name") or "").find("節奏") >= 0
        rnd = random.Random(activity_id)
        pts, t = [], 0
        while t <= dur:
            f = t / dur
            warm = min(1, t / 420)
            hr = avg_hr - 22 * (1 - warm) + 6 * f + rnd.uniform(-3, 3)
            p = pace * (1.06 - 0.05 * warm) + rnd.uniform(-8, 8)
            if tempo and 0.25 < f < 0.8:
                hr += 8
                p -= 25
            pts.append({"t": t, "hr": round(hr), "pace": round(p), "ele": round(20 + 8 * math.sin(f * 6), 1),
                        "cad": round(166 + rnd.uniform(-4, 4))})
            t += max(10, dur // 240)
        return {"points": pts}

    def fetch_records(self):
        return None  # 由跑步紀錄自行估算

    def tokens(self) -> str:
        return json.dumps({"demo": True})

    def fetch(self, start: date, end: date):
        acts, days = [], []
        d = start
        while d <= end:
            rnd = random.Random(f"{self.seed}-{d.isoformat()}")
            wd = d.weekday()
            ds = d.isoformat()
            phase = (d.toordinal() % 28) / 28
            fatigue = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
            days.append({
                "date": ds,
                "rhr": round(52 + 4 * fatigue + rnd.uniform(-1.5, 1.5)),
                "hrv_avg": round(58 - 10 * fatigue + rnd.uniform(-4, 4)),
                "hrv_weekly": round(56 - 6 * fatigue),
                "hrv_status": "BALANCED" if fatigue < 0.75 else "UNBALANCED",
                "sleep_score": round(min(95, max(45, 78 - 12 * fatigue + rnd.uniform(-8, 8)))),
                "sleep_hours": round(7.1 + rnd.uniform(-1.2, 0.8), 1),
                "body_battery": round(min(100, max(30, 85 - 25 * fatigue + rnd.uniform(-8, 8)))),
                "stress_avg": round(28 + 10 * fatigue + rnd.uniform(-5, 5)),
                "steps": round(5200 + rnd.uniform(-2000, 2500)),
                "steps_goal": 8000,
                "active_kcal": round(300 + rnd.uniform(-150, 250)),
                "intensity_min": round(rnd.uniform(5, 45)),
            })
            bbh = days[-1]["body_battery"]
            days[-1]["bb_low"] = round(max(5, bbh - 45 - rnd.uniform(0, 20)))
            days[-1]["bb_now"] = round(max(days[-1]["bb_low"], bbh - 30 - rnd.uniform(0, 15))) if d < end else round(bbh - rnd.uniform(5, 20))
            # 其他運動：週一重訓、週三騎車（隔週）、週五偶爾瑜珈
            cross = {0: ("strength_training", 45, 112), 2: ("indoor_cycling", 60, 132), 4: ("yoga", 40, 92)}
            if wd in cross and d < end and rnd.random() > (0.25 if wd == 0 else 0.5):
                sk, cm, chr_ = cross[wd]
                label, group = sport_info(sk)
                cm = cm * rnd.uniform(0.8, 1.2)
                acts.append({
                    "cross": True, "activity_id": f"demo-x-{ds}", "date": ds, "start_local": f"{ds} 19:30:00",
                    "sport": sk, "sport_label": label, "sport_group": group, "name": label,
                    "distance_m": round(cm * 400) if sk == "indoor_cycling" else None, "duration_s": round(cm * 60),
                    "avg_hr": round(chr_ + rnd.uniform(-6, 6)), "max_hr": round(chr_ + 30), "calories": round(cm * 7),
                    "elevation_gain": None, "training_load": None,
                })
            plan = {1: ("easy", 40), 3: ("tempo", 45), 5: ("easy", 35), 6: ("long", 85)}
            if wd in plan and rnd.random() > 0.12:
                kind, mins = plan[wd]
                mins = mins * rnd.uniform(0.85, 1.15)
                pace = {"easy": 6.4, "tempo": 5.1, "long": 6.2}[kind] + rnd.uniform(-0.2, 0.2)
                hr = {"easy": 138, "tempo": 162, "long": 145}[kind] + rnd.uniform(-5, 5)
                dist = mins / pace * 1000
                acts.append({
                    "activity_id": f"demo-{ds}",
                    "date": ds,
                    "start_local": f"{ds} 06:30:00",
                    "type": "running",
                    "name": {"easy": "輕鬆跑", "tempo": "節奏跑", "long": "長距離"}[kind],
                    "distance_m": round(dist),
                    "duration_s": round(mins * 60),
                    "avg_hr": round(hr),
                    "max_hr": round(hr + 18),
                    "training_load": None,
                    "avg_speed": round(dist / (mins * 60), 3),
                    "calories": round(dist / 1000 * 68),
                    "elevation_gain": round(rnd.uniform(10, 90)),
                    "avg_cadence": round(166 + rnd.uniform(-6, 8)),
                })
                # 跑步當天的熱量與強度分鐘要對得上
                days[-1]["active_kcal"] += round(dist / 1000 * 68)
                days[-1]["intensity_min"] += round(mins * (0.6 if kind != "tempo" else 1.4))
                days[-1]["steps"] += round(dist / 1000 * 1150)
            d += timedelta(days=1)
        return acts, days


def provider_for(user: dict, tokens_json: str | None):
    if user["provider"] == "demo":
        return DemoGarmin(seed=user["id"])
    if user["provider"] == "garmin" and tokens_json:
        return RealGarmin(tokens_json)
    return None


def today_local() -> date:
    from .config import TZ
    return datetime.now(TZ).date()
