"""Apple Watch／Apple 健康：由 iPhone「捷徑」把資料 POST 過來。

Apple 沒有雲端 API，伺服器抓不到，只能由使用者的 iPhone 主動送。每個人有一組專屬上傳網址（含密鑰），
捷徑每天早上送一次身體資料、跑完送運動紀錄。

接受的格式（欄位都可以省略，有什麼送什麼）：

    {
      "date": "2026-11-18",                  # 這筆身體資料是哪一天（省略 = 今天）
      "resting_hr": 55, "hrv": 48,           # 安靜心率、HRV（ms）
      "sleep_hours": 7.2, "steps": 8123, "active_kcal": 450,
      "workouts": [
        {"type": "Running", "start": "2026-11-17T20:44:00+08:00",
         "duration_min": 29.8, "distance_km": 4.0, "avg_hr": 135, "max_hr": 160, "kcal": 239, "name": "戶外跑步"}
      ]
    }

也接受 {"days": [ {...}, {...} ]} 一次送多天，以及常見匯出 App 的寫法：數值可以是字串（"55 count/min"、
"4.0 km"、"0:29:47"）或 {"qty": 4.0, "units": "km"}。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta

from . import db
from .config import TZ
from .garmin_client import sport_info, today_local

# HealthKit 運動類型（英文名稱、中文顯示名稱、匯出 App 常見寫法）→ Garmin 風格的 typeKey
APPLE_TYPES = {
    "running": "running", "run": "running", "outdoor run": "running", "indoor run": "treadmill_running",
    "跑步": "running", "戶外跑步": "running", "室內跑步": "treadmill_running", "trail running": "trail_running",
    "cycling": "cycling", "outdoor cycle": "road_biking", "indoor cycle": "indoor_cycling", "騎單車": "cycling",
    "戶外騎車": "road_biking", "室內騎車": "indoor_cycling", "swimming": "lap_swimming", "pool swim": "lap_swimming",
    "open water swim": "open_water_swimming", "游泳": "lap_swimming", "泳池游泳": "lap_swimming", "開放水域游泳": "open_water_swimming",
    "traditionalstrengthtraining": "strength_training", "traditional strength training": "strength_training",
    "functionalstrengthtraining": "strength_training", "functional strength training": "strength_training",
    "傳統肌力訓練": "strength_training", "功能性肌力訓練": "strength_training", "肌力訓練": "strength_training",
    "yoga": "yoga", "瑜伽": "yoga", "瑜珈": "yoga", "pilates": "pilates", "皮拉提斯": "pilates",
    "walking": "walking", "outdoor walk": "walking", "indoor walk": "walking", "步行": "walking", "戶外步行": "walking", "室內步行": "walking",
    "hiking": "hiking", "健行": "hiking", "highintensityintervaltraining": "hiit", "high intensity interval training": "hiit",
    "hiit": "hiit", "高強度間歇訓練": "hiit", "elliptical": "elliptical", "橢圓機": "elliptical", "rowing": "indoor_rowing",
    "划船": "indoor_rowing", "stairclimbing": "stair_climbing", "stair stepper": "stair_climbing", "爬樓梯": "stair_climbing",
    "tennis": "tennis", "網球": "tennis", "badminton": "badminton", "羽球": "badminton", "basketball": "basketball",
    "籃球": "basketball", "soccer": "soccer", "足球": "soccer", "tabletennis": "table_tennis", "桌球": "table_tennis",
}
RUN_KEYS = {"running", "treadmill_running", "trail_running"}


# ───────────────────────────── 連結 ─────────────────────────────

def link(user_id: int, rotate: bool = False) -> dict:
    row = db.q1("SELECT * FROM apple_link WHERE user_id=?", (user_id,))
    if row and not rotate:
        return row
    token = secrets.token_urlsafe(24)
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with db.tx() as c:
        c.execute("INSERT INTO apple_link(user_id, token, created_at) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, created_at=excluded.created_at",
                  (user_id, token, now))
    return db.q1("SELECT * FROM apple_link WHERE user_id=?", (user_id,))


def user_for_token(token: str) -> int | None:
    if not token or len(token) < 20:
        return None
    r = db.q1("SELECT user_id FROM apple_link WHERE token=?", (token,))
    return r["user_id"] if r else None


# ───────────────────────────── 解析 ─────────────────────────────

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def num(v) -> float | None:
    """"55"、"55 count/min"、"1,234"、{"qty": 4}、4 → 數字；看不懂就回 None。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return num(v.get("qty", v.get("value")))
    s = str(v).replace(",", "").strip()
    m = _NUM.search(s)
    return float(m.group()) if m else None


def minutes(v) -> float | None:
    """"0:29:47"、"29:47"、"29.8"、"29 min"、"1 hr 5 min"、{"qty": 1787, "units": "s"} → 分鐘。"""
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        q, u = num(v.get("qty")), str(v.get("units") or "").lower()
        if q is None:
            return None
        return q / 60 if u.startswith("s") else q * 60 if u.startswith("h") else q
    s = str(v).strip()
    if re.fullmatch(r"\d+:\d{1,2}(:\d{1,2})?", s):
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 2:
            return parts[0] + parts[1] / 60
        return parts[0] * 60 + parts[1] + parts[2] / 60
    h = re.search(r"([\d.]+)\s*(?:hr|h|小時)", s)
    m = re.search(r"([\d.]+)\s*(?:min|m(?!s)|分)", s)
    sec = re.search(r"([\d.]+)\s*(?:sec|s|秒)\b", s)
    if h or m or sec:
        return (float(h.group(1)) * 60 if h else 0) + (float(m.group(1)) if m else 0) + (float(sec.group(1)) / 60 if sec else 0)
    return num(s)


def km(v, unit_hint: str = "km") -> float | None:
    if isinstance(v, dict):
        q, u = num(v.get("qty")), str(v.get("units") or unit_hint).lower()
    else:
        q, u = num(v), (str(v).lower() if isinstance(v, str) else unit_hint)
    if q is None:
        return None
    if "mi" in u:
        return q * 1.609344
    if re.search(r"(^|[^k])m\b", u) and "km" not in u:
        return q / 1000
    return q


def when(v) -> datetime | None:
    """ISO 8601（含或不含時區）、"2026-11-17 20:44:00 +0800"、"2026/11/17 20:44" → 台北時間。"""
    if not v:
        return None
    s = str(v).strip().replace("/", "-")
    s = re.sub(r"\s([+-]\d{2})(\d{2})$", r"\1:\2", s)       # "+0800" → "+08:00"
    s = s.replace(" ", "T", 1) if re.match(r"\d{4}-\d{2}-\d{2} \d", s) else s
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def parse_workout(w: dict) -> dict | None:
    raw_type = str(_pick(w, "type", "workout_type", "activity", "name") or "").strip()
    tkey = APPLE_TYPES.get(raw_type.lower()) or APPLE_TYPES.get(raw_type.lower().replace(" ", ""))
    if not tkey:
        low = raw_type.lower()
        tkey = "running" if ("run" in low or "跑" in low) else "cycling" if ("cycl" in low or "騎" in low) else \
            "lap_swimming" if ("swim" in low or "泳" in low) else "walking" if ("walk" in low or "走" in low) else "other"
    start = when(_pick(w, "start", "start_date", "startDate", "start_time"))
    if not start:
        return None
    end = when(_pick(w, "end", "end_date", "endDate"))
    dur = minutes(_pick(w, "duration_min", "duration"))
    if _pick(w, "duration_s") is not None:
        dur = (num(w["duration_s"]) or 0) / 60
    if dur is None and end:
        dur = (end - start).total_seconds() / 60
    if not dur or dur <= 0:
        return None
    dist = _pick(w, "distance_km", "distance")
    dist_km = km(dist) if dist is not None else None
    if _pick(w, "distance_m") is not None:
        dist_km = (num(w["distance_m"]) or 0) / 1000
    avg = num(_pick(w, "avg_hr", "average_hr", "avgHeartRate", "heart_rate_avg"))
    if isinstance(w.get("heartRate"), dict):     # 匯出 App：{"heartRate": {"avg": {...}, "max": {...}}}
        avg = avg or num(w["heartRate"].get("avg"))
    mx = num(_pick(w, "max_hr", "maxHeartRate", "heart_rate_max"))
    if isinstance(w.get("heartRate"), dict):
        mx = mx or num(w["heartRate"].get("max"))
    kcal = num(_pick(w, "kcal", "active_kcal", "activeEnergyBurned", "active_energy", "calories"))
    name = str(_pick(w, "name", "title") or raw_type or "運動")
    ident = str(_pick(w, "id", "uuid") or "")
    aid = "apple-" + (ident or hashlib.sha1(f"{tkey}{start.isoformat()}".encode()).hexdigest()[:16])
    base = {"activity_id": aid, "date": start.date().isoformat(), "start_local": start.strftime("%Y-%m-%d %H:%M:%S"),
            "name": name, "distance_m": round(dist_km * 1000) if dist_km else None, "duration_s": round(dur * 60),
            "avg_hr": avg, "max_hr": mx, "calories": kcal}
    if tkey in RUN_KEYS:
        return {**base, "type": tkey, "training_load": None,
                "avg_speed": (dist_km * 1000) / (dur * 60) if dist_km else None, "elevation_gain": num(w.get("elevation_gain")),
                "avg_cadence": num(_pick(w, "cadence", "avg_cadence"))}
    label, group = sport_info(tkey)
    return {**base, "cross": True, "sport": tkey, "sport_label": label, "sport_group": group,
            "elevation_gain": num(w.get("elevation_gain")), "training_load": None}


def parse_day(d: dict, default_date: date) -> dict | None:
    dt = when(d.get("date")) if d.get("date") and "T" in str(d.get("date")) else None
    try:
        day = dt.date() if dt else (date.fromisoformat(str(d["date"]).replace("/", "-")[:10]) if d.get("date") else default_date)
    except ValueError:
        day = default_date
    sleep = num(_pick(d, "sleep_hours"))
    if sleep is None and _pick(d, "sleep_min", "sleep_minutes") is not None:
        sleep = (minutes(_pick(d, "sleep_min", "sleep_minutes")) or 0) / 60
    row = {"date": day.isoformat(),
           "rhr": num(_pick(d, "resting_hr", "restingHeartRate", "resting_heart_rate", "rhr")),
           "hrv_avg": num(_pick(d, "hrv", "heart_rate_variability", "hrv_sdnn")),
           "sleep_hours": round(sleep, 1) if sleep else None,
           "steps": num(_pick(d, "steps", "step_count")),
           "active_kcal": num(_pick(d, "active_kcal", "active_energy", "activeEnergyBurned"))}
    # 合理範圍檢查：捷徑設定錯時常把單位弄錯，與其存錯不如不存
    if row["rhr"] and not 25 <= row["rhr"] <= 130:
        row["rhr"] = None
    if row["hrv_avg"] and not 5 <= row["hrv_avg"] <= 300:
        row["hrv_avg"] = None
    if row["sleep_hours"] and not 0 < row["sleep_hours"] <= 16:
        row["sleep_hours"] = None
    return row if any(v is not None for k, v in row.items() if k != "date") else None


def parse(payload) -> tuple[list[dict], list[dict], list[str]]:
    """回傳（運動紀錄、每日身體資料、看不懂的項目說明）。"""
    if isinstance(payload, list):
        payload = {"days": payload}
    if not isinstance(payload, dict):
        raise ValueError("資料格式不對：要是 JSON 物件")
    if isinstance(payload.get("data"), dict):        # 匯出 App 常包一層 data
        payload = {**payload, **payload["data"]}
    today = today_local()
    days_in = payload.get("days") if isinstance(payload.get("days"), list) else [payload]
    acts, days, skipped = [], [], []
    for d in days_in:
        if not isinstance(d, dict):
            continue
        row = parse_day(d, today)
        if row:
            days.append(row)
        for w in d.get("workouts") or []:
            a = parse_workout(w) if isinstance(w, dict) else None
            if a:
                acts.append(a)
            else:
                skipped.append(f"看不懂的運動紀錄：{json.dumps(w, ensure_ascii=False)[:120]}")
    return acts, days, skipped


def ingest(user_id: int, payload) -> dict:
    from . import sync

    acts, days, skipped = parse(payload)
    if not acts and not days:
        raise ValueError("沒有收到可用的資料。請確認捷徑裡的欄位名稱，例如 resting_hr、workouts。")
    sync.store(user_id, acts, days)
    now = datetime.now(TZ).isoformat(timespec="seconds")
    summary = {"at": now, "runs": sum(1 for a in acts if not a.get("cross")), "others": sum(1 for a in acts if a.get("cross")),
               "days": [d["date"] for d in days], "fields": sorted({k for d in days for k, v in d.items() if v is not None and k != "date"}),
               "skipped": skipped[:5]}
    with db.tx() as c:
        c.execute("UPDATE apple_link SET last_at=?, last_summary=? WHERE user_id=?", (now, json.dumps(summary, ensure_ascii=False), user_id))
        c.execute("UPDATE users SET last_sync=?, last_sync_error=NULL WHERE id=?", (now, user_id))
        c.execute("INSERT INTO device(user_id, data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                  (user_id, json.dumps({"name": "Apple Watch", "brand": "Apple（透過 iPhone 捷徑）", "firmware": None,
                                        "last_upload": now, "count": 1, "source": "apple"}, ensure_ascii=False)))
    sync.replan(user_id)
    return summary
