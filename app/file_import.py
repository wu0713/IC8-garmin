"""匯入舊紀錄：Strava 完整匯出檔（.zip）、或手錶／其他 App 匯出的 .fit／.gpx／.tcx。

檔案是使用者自己下載的，不經過 Strava API。只取每趟的摘要（日期、距離、時間、心率），
跟 Garmin 已經同步過的同一趟重複時跳過。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import math
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from . import db
from .apple_ingest import RUN_KEYS
from .config import TZ
from .garmin_client import sport_info

MAX_FILES = 5000
MAX_MEMBER_BYTES = 50_000_000      # 壓縮檔裡單一檔案解壓後的上限

# Strava／GPX／TCX／FIT 的運動類型 → 我們的運動代碼
TYPE_MAP = {
    "run": "running", "running": "running", "virtual run": "treadmill_running", "virtualrun": "treadmill_running",
    "trail run": "trail_running", "trailrun": "trail_running", "trail_running": "trail_running", "treadmill": "treadmill_running",
    "treadmill_running": "treadmill_running", "ride": "cycling", "biking": "cycling", "cycling": "cycling",
    "virtual ride": "virtual_ride", "virtualride": "virtual_ride", "mountain bike ride": "mountain_biking",
    "gravel ride": "gravel_cycling", "e-bike ride": "e_bike_fitness", "swim": "lap_swimming", "swimming": "lap_swimming",
    "walk": "walking", "walking": "walking", "hike": "hiking", "hiking": "hiking", "weight training": "strength_training",
    "workout": "cardio", "crossfit": "hiit", "hiit": "hiit", "elliptical": "elliptical", "rowing": "indoor_rowing",
    "yoga": "yoga", "training": "cardio", "fitness_equipment": "fitness_equipment",
}


def _sport(raw: str | None) -> str:
    low = (raw or "").strip().lower()
    if low in TYPE_MAP:
        return TYPE_MAP[low]
    if "run" in low or "跑" in low:
        return "running"
    if "ride" in low or "bik" in low or "cycl" in low or any(x in low for x in ("騎", "单车", "單車", "自行車", "自行车", "サイクリング", "ライド")):
        return "cycling"
    if "swim" in low or "游泳" in low or "泳" in low or "スイム" in low:
        return "lap_swimming"
    if "hike" in low or "健行" in low or "登山" in low or "徒步" in low or "ハイキング" in low:
        return "hiking"
    if "walk" in low or "步行" in low or "健走" in low or "ウォーク" in low:
        return "walking"
    if "weight" in low or "重訓" in low or "重量訓練" in low or "力量训练" in low:
        return "strength_training"
    if "ラン" in low or "ランニング" in low:
        return "running"
    return "other"


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).replace(tzinfo=None)


def _iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _make(source: str, ident: str, sport: str, start: datetime, dist_m, dur_s, avg_hr=None, max_hr=None,
          name=None, cadence=None, kcal=None, elev=None) -> dict | None:
    """整理成跟 Garmin 同步一樣的格式；時間或距離明顯不合理的丟掉。"""
    if not start or not dur_s or dur_s < 60 or dur_s > 24 * 3600:
        return None
    st = _local(start)
    dist_m = dist_m if dist_m and dist_m > 0 else None
    aid = f"imp-{source}-" + (ident or hashlib.sha1(f"{sport}{st.isoformat()}".encode()).hexdigest()[:16])
    base = {"activity_id": aid, "date": st.date().isoformat(), "start_local": st.strftime("%Y-%m-%d %H:%M:%S"),
            "name": name or ("跑步" if sport in RUN_KEYS else sport_info(sport)[0]), "distance_m": dist_m and round(dist_m),
            "duration_s": round(dur_s), "avg_hr": avg_hr or None, "max_hr": max_hr or None, "calories": kcal,
            "elevation_gain": elev}
    if sport in RUN_KEYS:
        if dist_m and not 120 <= dur_s / (dist_m / 1000) <= 1200:      # 每公里 2–20 分以外：多半是記錄錯誤
            return None
        return {**base, "type": sport, "training_load": None, "avg_speed": dist_m / dur_s if dist_m else None,
                "avg_cadence": cadence}
    label, group = sport_info(sport)
    return {**base, "cross": True, "sport": sport, "sport_label": label, "sport_group": group, "training_load": None}


# ───────────────────────────── Strava activities.csv ─────────────────────────────

DATE_FORMATS = ("%b %d, %Y, %I:%M:%S %p", "%d %b %Y, %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%b %d, %Y %I:%M:%S %p",
                "%d %b %Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d.%m.%Y, %H:%M:%S", "%d/%m/%Y, %H:%M:%S")

# Strava 的 activities.csv 欄位名稱會跟著帳號語言（英文、繁中、簡中、日文…），欄位順序前 8 欄是固定的
COLS = {
    "id": ["Activity ID", "活動 ID", "活動ID", "活动 ID", "活动ID", "アクティビティID"],
    "date": ["Activity Date", "活動日期", "活动日期", "アクティビティの日付"],
    "name": ["Activity Name", "活動名稱", "活动名称", "アクティビティ名"],
    "type": ["Activity Type", "活動類型", "活動類別", "活动类型", "アクティビティタイプ"],
    "elapsed": ["Elapsed Time", "經過時間", "耗時", "总时间", "經過的時間", "所要時間"],
    "moving": ["Moving Time", "移動時間", "移动时间", "运动时间", "移動時間（秒）"],
    "distance": ["Distance", "距離", "距离"],
    "max_hr": ["Max Heart Rate", "最大心率", "最高心率", "最大心拍数"],
    "avg_hr": ["Average Heart Rate", "平均心率", "平均心拍数"],
    "cadence": ["Average Cadence", "平均步頻", "平均踏頻", "平均步频", "平均ケイデンス"],
    "kcal": ["Calories", "卡路里", "熱量", "热量", "カロリー"],
    "elev": ["Elevation Gain", "爬升高度", "總爬升", "海拔增益", "累積標高（上り）"],
}
POS = {"id": 0, "date": 1, "name": 2, "type": 3, "elapsed": 5, "distance": 6, "max_hr": 7}


def _parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # 中文／日文：「2025年11月3日 下午10:15:45」「2025/11/3 下午 10:15:45」
    m = re.search(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})", s)
    t = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s[m.end():] if m else s)
    if not m or not t:
        return None
    h = int(t.group(1))
    if any(x in s for x in ("下午", "PM", "pm", "午後", "晚上")) and h < 12:
        h += 12
    if any(x in s for x in ("上午", "AM", "am", "午前", "凌晨")) and h == 12:
        h = 0
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), h, int(t.group(2)), int(t.group(3) or 0), tzinfo=timezone.utc)
    except ValueError:
        return None


def _num_loc(v) -> float | None:
    """數字：也接受「10,02」這種逗號小數（歐洲語系）。"""
    if v is None:
        return None
    t = str(v).strip()
    if re.fullmatch(r"-?\d+,\d{1,2}", t):
        t = t.replace(",", ".")
    return _num(t)


def parse_strava_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = [h.strip().lstrip("\ufeff") for h in rows[0]]

    def col(key, nth=0):
        idx = [i for i, h in enumerate(head) if h in COLS[key]]
        if len(idx) > nth:
            return idx[nth]
        if nth == 0 and key in POS and POS[key] < len(head):
            return POS[key]           # 看不懂欄名：用 Strava 固定的欄位順序
        return None

    c_id, c_date, c_name, c_type = col("id"), col("date"), col("name"), col("type")
    c_el, c_mv = col("elapsed"), col("moving")
    c_d1, c_d2 = col("distance"), col("distance", 1)
    c_hr, c_mx = col("avg_hr"), col("max_hr")
    if c_hr is not None and c_hr == c_mx:
        c_hr = None
    c_cad, c_kcal, c_elev = col("cadence"), col("kcal"), col("elev")
    if len(head) < 8:
        raise ValueError(f"activities.csv 的欄位看不懂（只有 {len(head)} 欄：{'、'.join(head[:6])}）")
    out, bad_dates = [], 0
    for r in rows[1:]:
        g = lambda c: r[c] if c is not None and c < len(r) else None  # noqa: E731
        start = _parse_date(g(c_date) or "")
        if not start:
            bad_dates += 1
            continue
        sport = _sport(g(c_type))
        d2 = _num_loc(g(c_d2)) if c_d2 is not None else None
        dist = d2 if d2 else ((_num_loc(g(c_d1)) or 0) * 1000 or None)
        dur = _num_loc(g(c_mv)) or _num_loc(g(c_el))
        cad = _num_loc(g(c_cad))
        if cad and sport in RUN_KEYS and cad < 120:          # Strava 的跑步步頻是單腳
            cad *= 2
        a = _make("strava", (g(c_id) or "").strip(), sport, start, dist, dur, _num_loc(g(c_hr)), _num_loc(g(c_mx)),
                  (g(c_name) or "").strip() or None, cad, _num_loc(g(c_kcal)), _num_loc(g(c_elev)))
        if a:
            out.append(a)
    if not out and bad_dates and len(rows) > 1:
        raise ValueError(f"activities.csv 的日期格式看不懂（例如「{(rows[1][c_date] if c_date is not None and c_date < len(rows[1]) else '')[:30]}」）")
    return out


# ───────────────────────────── GPX／TCX／FIT ─────────────────────────────

def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _hav(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def parse_gpx(data: bytes, ident: str) -> list[dict]:
    root = _strip_ns(ET.fromstring(data))
    out = []
    for trk in root.iter("trk"):
        pts = []
        for p in trk.iter("trkpt"):
            t = _iso((p.findtext("time") or ""))
            if not t:
                continue
            hr = _num(next((e.text for e in p.iter("hr")), None))
            pts.append((float(p.get("lat")), float(p.get("lon")), t, hr))
        if len(pts) < 2:
            continue
        dist = sum(_hav(a, b) for a, b in zip(pts, pts[1:]))
        dur = (pts[-1][2] - pts[0][2]).total_seconds()
        hrs = [p[3] for p in pts if p[3]]
        a = _make("file", f"{ident}-{len(out)}", _sport(trk.findtext("type")), pts[0][2], dist, dur,
                  round(sum(hrs) / len(hrs)) if hrs else None, max(hrs) if hrs else None, trk.findtext("name"))
        if a:
            out.append(a)
    return out


def parse_tcx(data: bytes, ident: str) -> list[dict]:
    root = _strip_ns(ET.fromstring(data))
    out = []
    for act in root.iter("Activity"):
        start = _iso(act.findtext("Id") or "")
        laps = list(act.iter("Lap"))
        dur = sum(_num(l.findtext("TotalTimeSeconds")) or 0 for l in laps)
        dist = sum(_num(l.findtext("DistanceMeters")) or 0 for l in laps)
        hr_w = [(_num(l.findtext("AverageHeartRateBpm/Value")), _num(l.findtext("TotalTimeSeconds")) or 0) for l in laps]
        hr_w = [(h, w) for h, w in hr_w if h]
        avg = round(sum(h * w for h, w in hr_w) / sum(w for _, w in hr_w)) if hr_w and sum(w for _, w in hr_w) else None
        mx = max([_num(l.findtext("MaximumHeartRateBpm/Value")) or 0 for l in laps] or [0]) or None
        if not start and laps:
            start = _iso(laps[0].get("StartTime") or "")
        a = _make("file", f"{ident}-{len(out)}", _sport(act.get("Sport")), start, dist, dur, avg, mx)
        if a:
            out.append(a)
    return out


def parse_fit(data: bytes, ident: str) -> list[dict]:
    import fitdecode

    out = []
    with fitdecode.FitReader(io.BytesIO(data)) as fr:
        for frame in fr:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "session":
                continue
            g = lambda k: frame.get_value(k, fallback=None)  # noqa: E731
            sport = _sport(str(g("sport") or ""))
            sub = str(g("sub_sport") or "")
            if sport == "running" and sub in ("treadmill", "trail"):
                sport = "treadmill_running" if sub == "treadmill" else "trail_running"
            cad = _num(g("avg_running_cadence") or g("avg_cadence"))
            if cad and sport in RUN_KEYS:
                cad = cad * 2 + (_num(g("avg_fractional_cadence")) or 0) * 2
            start = g("start_time")
            a = _make("file", f"{ident}-{len(out)}", sport, start if isinstance(start, datetime) else None,
                      _num(g("total_distance")), _num(g("total_timer_time")) or _num(g("total_elapsed_time")),
                      _num(g("avg_heart_rate")), _num(g("max_heart_rate")), None, cad, _num(g("total_calories")), _num(g("total_ascent")))
            if a:
                out.append(a)
    return out


def _one(name: str, data: bytes) -> list[dict]:
    low = name.lower()
    if low.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            data = gz.read(MAX_MEMBER_BYTES + 1)
        if len(data) > MAX_MEMBER_BYTES:
            raise ValueError("檔案解壓後太大")
        low = low[:-3]
    ident = hashlib.sha1(data[:4096] + name.encode()).hexdigest()[:16]
    if low.endswith(".fit"):
        return parse_fit(data, ident)
    if low.endswith(".gpx"):
        return parse_gpx(data, ident)
    if low.endswith(".tcx"):
        return parse_tcx(data.lstrip(), ident)
    return []


def parse_upload(name: str, path: str) -> tuple[list[dict], int]:
    """回傳（活動、看不懂的檔案數）。Strava 封存檔優先讀 activities.csv（一次就有全部摘要）。"""
    bad = 0
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            csvs = [n for n in names if n.rsplit("/", 1)[-1].lower() == "activities.csv"]
            if csvs:
                if z.getinfo(csvs[0]).file_size > MAX_MEMBER_BYTES:
                    raise ValueError("activities.csv 解壓後超過 50 MB，請確認檔案")
                return parse_strava_csv(z.read(csvs[0]).decode("utf-8-sig", errors="replace")), 0
            out = []
            files = [n for n in names if n.lower().endswith((".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz"))][:MAX_FILES]
            if not files:
                raise ValueError("壓縮檔裡沒有 activities.csv，也沒有 .fit／.gpx／.tcx 檔")
            for n in files:
                if z.getinfo(n).file_size > MAX_MEMBER_BYTES:      # 解壓後太大（或壓縮炸彈）：跳過
                    bad += 1
                    continue
                try:
                    out += _one(n, z.read(n))
                except Exception:
                    bad += 1
            return out, bad
    with open(path, "rb") as f:
        data = f.read()
    if name.lower().endswith(".csv"):
        return parse_strava_csv(data.decode("utf-8-sig", errors="replace")), 0
    try:
        acts = _one(name, data)
    except Exception:
        raise ValueError("這個檔案打不開，請確認是 .fit、.gpx、.tcx 或 Strava 的封存檔 .zip")
    if not acts and not name.lower().endswith((".fit", ".gpx", ".tcx", ".gz")):
        raise ValueError("只收 Strava 封存檔 .zip，或 .fit／.gpx／.tcx 檔")
    return acts, bad


def dedupe(user_id: int, acts: list[dict]) -> tuple[list[dict], int]:
    """同一趟已經從 Garmin／Apple 進來了就跳過：開始時間差 10 分鐘內，或同一天距離和時間都差不多。"""
    if not acts:
        return [], 0
    lo, hi = min(a["date"] for a in acts), max(a["date"] for a in acts)
    have = db.q("SELECT activity_id, date, start_local, distance_m, duration_s FROM activities WHERE user_id=? AND date BETWEEN ? AND ? "
                "UNION ALL SELECT activity_id, date, start_local, distance_m, duration_s FROM cross_activities WHERE user_id=? AND date BETWEEN ? AND ?",
                (user_id, lo, hi, user_id, lo, hi))
    by_date: dict[str, list] = {}
    for h in have:
        by_date.setdefault(h["date"], []).append(h)
    keep, dup, seen = [], 0, set()
    for a in sorted(acts, key=lambda x: x["start_local"]):
        if a["activity_id"] in seen:
            dup += 1
            continue
        same = False
        for h in by_date.get(a["date"], []):
            if h["activity_id"] == a["activity_id"]:
                same = False      # 重複匯入同一個檔：覆寫即可，不算重複
                break
            if h.get("start_local") and a.get("start_local"):
                dt = abs((datetime.fromisoformat(h["start_local"]) - datetime.fromisoformat(a["start_local"])).total_seconds())
                if dt <= 600:
                    same = True
                    break
            if h.get("distance_m") and a.get("distance_m") and h.get("duration_s"):
                if abs(h["distance_m"] - a["distance_m"]) <= 0.03 * a["distance_m"] and abs(h["duration_s"] - a["duration_s"]) <= 0.05 * a["duration_s"]:
                    same = True
                    break
        if same:
            dup += 1
            continue
        seen.add(a["activity_id"])
        keep.append(a)
        by_date.setdefault(a["date"], []).append(a)
    return keep, dup
