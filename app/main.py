"""FastAPI 入口：API + PWA 靜態檔 + 行事曆訂閱 + 每日排程。

啟動：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from starlette.concurrency import run_in_threadpool
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, apple_ingest, auth, coach, briefs, file_import, db, game, pet_voice, plan_import, planner, push, runner, season, strength, sync, views
from .config import ALLOW_DEMO, INVITE_CODE, PUBLIC_BASE_URL, SYNC_HOURS, TZ, WEB_DIR
from .garmin_client import MfaRequired, RealGarmin, today_local
from .ics import build_ics
from . import plan_tables
from .planner import GOAL_TYPES, MANUAL_KINDS, WORKOUT_LABELS, default_prefs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ic8")
try:
    from logging.handlers import RotatingFileHandler
    from .config import DATA_DIR
    _fh = RotatingFileHandler(DATA_DIR / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
except OSError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    sched = None
    if SYNC_HOURS:
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler(timezone=TZ)
        sched.add_job(sync.sync_all, "cron", hour=",".join(map(str, SYNC_HOURS)), minute=7,
                      id="sync_all", max_instances=1, coalesce=True)
        sched.add_job(briefs.tick, "cron", minute="*/10", id="briefs", max_instances=1, coalesce=True)
        sched.start()
        log.info("每日自動同步時間：%s 點；晨報、晚報、週報每 10 分鐘檢查一次", SYNC_HOURS)
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Garmin-IC8 Run Coach", lifespan=lifespan)


# ───────────────────────────── 帳號 ─────────────────────────────

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None
    invite: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


def _set_cookie(request: Request, resp: Response, token: str):
    resp.set_cookie("ic8_session", token, max_age=60 * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")


@app.post("/api/register")
def register(body: RegisterIn, request: Request, response: Response):
    if INVITE_CODE and body.invite != INVITE_CODE:
        raise HTTPException(403, "邀請碼不正確")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", body.username):
        raise HTTPException(400, "帳號只能用英數字與 _ . -")
    if db.q1("SELECT 1 AS x FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "這個帳號已經有人用了")
    uid = auth.create_user(body.username, body.password, body.display_name)
    token = auth.create_session(uid)
    _set_cookie(request, response, token)
    return {"token": token}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    u = db.q1("SELECT * FROM users WHERE username=?", (body.username,))
    if not u or not auth.verify_pw(body.password, u["pw_hash"], u["pw_salt"]):
        raise HTTPException(401, "帳號或密碼錯誤")
    token = auth.create_session(u["id"])
    _set_cookie(request, response, token)
    return {"token": token}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    tok = request.cookies.get("ic8_session") or (request.headers.get("authorization") or "")[7:]
    if tok:
        auth.delete_session(tok)
    response.delete_cookie("ic8_session")
    return {"ok": True}


def _base_url(request: Request) -> str:
    return PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


@app.get("/api/me")
def me(request: Request, user=Depends(auth.current_user)):
    base = _base_url(request)
    https_url = f"{base}/cal/{user['cal_token']}.ics"
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "provider": user["provider"],
        "garmin_email": user["garmin_email"],
        "mfa_pending": bool(user["mfa_state"]),
        "last_sync": user["last_sync"],
        "last_sync_error": user["last_sync_error"],
        "calendar_url": https_url,
        "calendar_webcal": re.sub(r"^https?://", "webcal://", https_url),
    }


# ───────────────────────────── Garmin 連結 ─────────────────────────────

class GarminLoginIn(BaseModel):
    email: str
    password: str


class MfaIn(BaseModel):
    code: str


DATA_TABLES = ("activities", "cross_activities", "briefs", "runner_metrics", "strength_log", "daily", "plan", "plan_meta", "checkins", "records", "device", "activity_series", "plan_overrides", "season_state")


def _wipe(c, uid: int):
    for t in DATA_TABLES:
        c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))


def _linked(uid: int, email: str, tokens: str):
    with db.tx() as c:
        if (c.execute("SELECT provider FROM users WHERE id=?", (uid,)).fetchone() or ["none"])[0] != "garmin":
            _wipe(c, uid)   # 從範例資料換成真的 Garmin：先清掉範例資料
        c.execute("UPDATE users SET provider='garmin', garmin_email=?, garmin_tokens=?, mfa_state=NULL, "
                  "last_sync_error=NULL WHERE id=?", (email, auth.encrypt(tokens), uid))


@app.post("/api/garmin/login")
def garmin_login(body: GarminLoginIn, user=Depends(auth.current_user)):
    """Garmin 密碼只在這一次請求中使用，換到 token 後就丟掉，不會存起來。"""
    try:
        tokens = RealGarmin.start_login(body.email, body.password)
    except MfaRequired as m:
        with db.tx() as c:
            c.execute("UPDATE users SET mfa_state=?, garmin_email=? WHERE id=?",
                      (auth.encrypt(json.dumps(m.state)), body.email, user["id"]))
        return {"mfa": True}
    except Exception as e:
        log.warning("Garmin 登入失敗: %s", e)
        raise HTTPException(400, f"Garmin 登入失敗：{e}")
    _linked(user["id"], body.email, tokens)
    return {"ok": True}


@app.post("/api/garmin/mfa")
def garmin_mfa(body: MfaIn, user=Depends(auth.current_user)):
    state = auth.decrypt(user["mfa_state"])
    if not state:
        raise HTTPException(400, "沒有等待中的驗證，請重新登入 Garmin")
    try:
        tokens = RealGarmin.finish_mfa(json.loads(state), body.code.strip())
    except Exception as e:
        raise HTTPException(400, f"驗證碼錯誤或已過期：{e}")
    _linked(user["id"], user["garmin_email"], tokens)
    return {"ok": True}


@app.post("/api/garmin/demo")
def use_demo(user=Depends(auth.current_user)):
    if not ALLOW_DEMO:
        raise HTTPException(403, "管理員關閉了 Demo 模式")
    with db.tx() as c:
        if user["provider"] != "demo":
            _wipe(c, user["id"])
        c.execute("UPDATE users SET provider='demo', garmin_email=NULL, garmin_tokens=NULL, mfa_state=NULL WHERE id=?",
                  (user["id"],))
    return {"ok": True}


def _real(user) -> RealGarmin:
    if user["provider"] != "garmin":
        raise HTTPException(409, "目前不是連結真正的 Garmin 帳號（是範例資料）。請先到「手錶與資料來源」連結 Garmin。")
    try:
        return RealGarmin(auth.decrypt(user["garmin_tokens"]))
    except Exception as e:
        raise HTTPException(502, f"Garmin 登入憑證失效，請重新連結：{e}")


def _save_tokens(user, g: RealGarmin):
    with db.tx() as c:
        c.execute("UPDATE users SET garmin_tokens=? WHERE id=?", (auth.encrypt(g.tokens()), user["id"]))


@app.get("/api/garmin/diagnose")
def garmin_diagnose(user=Depends(auth.current_user)):
    g = _real(user)
    checks = g.diagnose()
    _save_tokens(user, g)
    log.info("連線檢查 user=%s: %s", user["id"], [(c["key"], c["ok"], c["detail"]) for c in checks])
    return {"checks": checks}


@app.post("/api/garmin/push-test")
def garmin_push_test(user=Depends(auth.current_user)):
    g = _real(user)
    try:
        res = g.push_test()
    except Exception as e:
        log.exception("測試推課表失敗")
        raise HTTPException(502, f"上傳課表到 Garmin 失敗：{e}")
    _save_tokens(user, g)
    log.info("測試推課表 user=%s: %s", user["id"], res)
    return res


@app.delete("/api/garmin/push-test/{workout_id}")
def garmin_push_test_delete(workout_id: str, user=Depends(auth.current_user)):
    g = _real(user)
    try:
        g.delete_test(workout_id)
    except Exception as e:
        raise HTTPException(400, f"刪除失敗：{e}")
    return {"ok": True}


# ───────────────────────────── Apple Watch（iPhone 捷徑上傳） ─────────────────────────────

def _apple_info(user_id: int, request: Request) -> dict:
    row = apple_ingest.link(user_id)
    base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    last = json.loads(row["last_summary"]) if row.get("last_summary") else None
    return {"url": f"{base}/api/ingest/apple/{row['token']}", "last_at": row.get("last_at"), "last": last,
            "https": base.startswith("https://")}


@app.post("/api/apple/link")
def apple_link(request: Request, user=Depends(auth.current_user)):
    """改用 Apple Watch：清掉其他來源的資料，產生專屬上傳網址。"""
    with db.tx() as c:
        if user["provider"] != "apple":
            _wipe(c, user["id"])
        c.execute("UPDATE users SET provider='apple', garmin_email=NULL, garmin_tokens=NULL, mfa_state=NULL WHERE id=?",
                  (user["id"],))
    return _apple_info(user["id"], request)


@app.get("/api/apple")
def apple_get(request: Request, user=Depends(auth.current_user)):
    if user["provider"] != "apple":
        raise HTTPException(400, "還沒選擇 Apple Watch")
    return _apple_info(user["id"], request)


@app.post("/api/apple/rotate")
def apple_rotate(request: Request, user=Depends(auth.current_user)):
    if user["provider"] != "apple":
        raise HTTPException(400, "還沒選擇 Apple Watch")
    apple_ingest.link(user["id"], rotate=True)
    return _apple_info(user["id"], request)


@app.post("/api/ingest/apple/{token}")
async def apple_upload(token: str, request: Request):
    """iPhone 捷徑送資料進來。不需要登入，靠網址裡的密鑰辨識是誰。"""
    uid = apple_ingest.user_for_token(token)
    if not uid:
        raise HTTPException(404, "上傳網址不正確或已經換新，請到 App 的「Apple Watch 設定」複製新網址")
    raw = await request.body()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "資料太大，請只送最近幾天")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "收到的不是 JSON。捷徑的「取得 URL 內容」要選「要求本文：JSON」")
    try:
        s = apple_ingest.ingest(uid, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    parts = [f"{len(s['days'])} 天身體資料" if s["days"] else "", f"{s['runs']} 筆跑步" if s["runs"] else "",
             f"{s['others']} 筆其他運動" if s["others"] else ""]
    return {"ok": True, "message": "已收到：" + "、".join(p for p in parts if p), **s}


@app.post("/api/garmin/unlink")
def unlink(user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute("UPDATE users SET provider='none', garmin_email=NULL, garmin_tokens=NULL, mfa_state=NULL WHERE id=?",
                  (user["id"],))
        _wipe(c, user["id"])
    return {"ok": True}


# ───────────────────────────── 資料 / 課表 ─────────────────────────────

@app.post("/api/sync")
def do_sync(full: bool = False, user=Depends(auth.current_user)):
    try:
        r = sync.sync_user(user["id"], full=full)
    except Exception as e:
        raise HTTPException(502, f"同步失敗：{e}")
    return {"ok": True, "synced": r.get("synced"), "garmin_push": r.get("garmin_push")}


@app.get("/api/dashboard")
def dashboard(user=Depends(auth.current_user)):
    if user["provider"] == "none":
        return {"needs_link": True}
    r = sync.replan(user["id"])  # 每次打開都依最新資料重排並存檔，確保行事曆一致
    return r


@app.get("/api/meta")
def meta():
    return {"goal_types": GOAL_TYPES, "workouts": WORKOUT_LABELS, "levels": planner.LEVELS, "defaults": default_prefs(),
            "event_types": {k: {"label": v["label"], "damage": v["damage"]} for k, v in plan_tables.EVENT_TYPES.items() if k != "race_a"},
            "pain_sites": plan_tables.PAIN_SITES,
            "cards": views.CARD_LABELS, "default_cards": views.DEFAULT_CARDS,
            "invite_required": bool(INVITE_CODE), "allow_demo": ALLOW_DEMO}


@app.get("/api/prefs")
def get_prefs(user=Depends(auth.current_user)):
    return db.get_prefs(user["id"])


def _clean_self_report(sr) -> dict:
    """新手問卷：只收合理範圍的數字，填錯的欄位直接丟掉。"""
    if not isinstance(sr, dict):
        return {}

    def num(k, lo, hi):
        try:
            v = float(sr.get(k))
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None

    out = {"weekly_km": num("weekly_km", 0, 250), "longest_km": num("longest_km", 0, 100),
           "hard_per_week": num("hard_per_week", 0, 7), "race_km": num("race_km", 1, 100),
           "years": sr.get("years") if sr.get("years") in ("new", "lt1", "1to3", "gt3") else None}
    rt = sr.get("race_time")
    secs = planner.parse_hms(rt) if rt else None
    if out["race_km"] and secs:
        if not 2.5 * 60 * out["race_km"] <= secs <= 12 * 60 * out["race_km"]:     # 每公里 2:30–12:00 之外多半填錯
            raise HTTPException(400, "比賽成績看起來不對，請寫成 25:30 或 1:52:00")
        out["race_s"], out["race_time"] = secs, rt
    else:
        out["race_km"] = None
    out = {k: v for k, v in out.items() if v is not None}
    if out:
        out["updated"] = today_local().isoformat()
    return out


@app.put("/api/prefs")
def put_prefs(body: dict, user=Depends(auth.current_user)):
    prefs = db.get_prefs(user["id"])
    old_race = (prefs.get("goal") or {}).get("event_date")
    allowed = set(default_prefs())
    for k, v in body.items():
        if k in allowed:
            if isinstance(prefs.get(k), dict) and isinstance(v, dict):
                prefs[k].update(v)
            else:
                prefs[k] = v
    # 基本檢查
    prefs["days"] = sorted({int(d) for d in prefs.get("days", []) if 0 <= int(d) <= 6})
    if not prefs["days"]:
        raise HTTPException(400, "至少要選一天可以跑步")
    prefs["long_run_day"] = int(prefs.get("long_run_day", 6)) % 7
    prefs["max_minutes_weekday"] = max(20, min(240, int(prefs.get("max_minutes_weekday") or 70)))
    prefs["max_long_minutes"] = max(30, min(400, int(prefs.get("max_long_minutes") or 150)))
    if prefs.get("level") not in planner.LEVELS:
        prefs["level"] = "auto"
    if body.get("excused_weeks") is not None:
        ok = []
        for w in body["excused_weeks"] if isinstance(body["excused_weeks"], list) else []:
            try:
                d = date.fromisoformat(str(w))
            except ValueError:
                continue
            if d.weekday() == 0 and d <= today_local():
                ok.append(d.isoformat())
        prefs["excused_weeks"] = sorted(set(ok))[-26:]
    if body.get("self_report") is not None:
        prefs["self_report"] = _clean_self_report(body["self_report"])
    mode = prefs.get("plan_mode")
    prefs["plan_mode"] = mode if mode == "manual" or (mode == "imported" and prefs.get("imported")) else "auto"
    tpl = {}
    for k, v in (prefs.get("template") or {}).items():
        if str(k) in "0123456" and isinstance(v, dict) and v.get("kind") in MANUAL_KINDS:
            km = v.get("km")
            tpl[str(k)] = {"kind": v["kind"], "km": max(1.0, min(60.0, float(km))) if km else None,
                           "minutes": max(10, min(300, float(v.get("minutes") or 40)))}
    prefs["template"] = tpl
    g = prefs["goal"]
    if g.get("type") == "race":
        if not g.get("event_date") or not g.get("event_distance_km"):
            raise HTTPException(400, "準備比賽要填比賽日期和距離")
        try:
            rd = date.fromisoformat(g["event_date"])
        except ValueError:
            raise HTTPException(400, "比賽日期格式錯誤")
        if rd < today_local() and g["event_date"] != old_race:     # 新填的日期已經過了，多半是年份打錯
            raise HTTPException(400, f"比賽日期 {g['event_date'].replace('-', '/')} 已經過了，請確認年份")
    if g.get("target_time") and planner.parse_hms(g["target_time"]) is None:
        raise HTTPException(400, "目標時間請寫成 1:45:00 或 50:00")
    events = []
    for e in (prefs.get("events") or [])[:30]:
        try:
            date.fromisoformat(e["date"])
            if e.get("end_date"):
                date.fromisoformat(e["end_date"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "事件日期格式錯誤")
        if e.get("type") not in plan_tables.EVENT_TYPES or e.get("type") == "race_a":
            raise HTTPException(400, "事件類型不對")
        end = e.get("end_date") if e.get("end_date") and e["end_date"] >= e["date"] else None
        events.append({"type": e["type"], "name": str(e.get("name") or "")[:30], "date": e["date"], "end_date": end,
                       "distance_km": float(e["distance_km"]) if e.get("distance_km") else None,
                       "target_time": str(e.get("target_time") or "")[:10]})
    prefs["events"] = sorted(events, key=lambda e: e["date"])
    if prefs.get("age") not in (None, ""):
        prefs["age"] = max(10, min(95, int(prefs["age"])))
    if prefs.get("dashboard") is not None:
        prefs["dashboard"] = [k for k in prefs["dashboard"] if k in views.CARD_LABELS][:16]
    for k in ("run_time", "brief_morning", "brief_evening", "brief_weekly"):
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", prefs.get(k) or ""):
            raise HTTPException(400, "時間格式請用 HH:MM")
    for k in ("brief_enabled", "strength_enabled", "weekly_review"):
        prefs[k] = bool(prefs.get(k, True))
    db.save_prefs(user["id"], prefs)
    if user["provider"] != "none":
        sync.replan(user["id"])
    return prefs


class CheckinIn(BaseModel):
    date: str | None = None
    feeling: int | None = Field(default=None, ge=1, le=5)
    skip: bool = False
    note: str | None = None
    pain: str | None = None     # 哪裡痛（plan_tables.PAIN_SITES）；"none" = 不痛


@app.post("/api/checkin")
def checkin(body: CheckinIn, user=Depends(auth.current_user)):
    d = body.date or today_local().isoformat()
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "日期格式錯誤")
    if not (today_local() <= dd <= today_local() + timedelta(days=14)):
        raise HTTPException(400, "只能設定今天到未來兩週")
    if body.pain is not None and body.pain not in (*plan_tables.PAIN_SITES, "none"):
        raise HTTPException(400, "沒有這個部位")
    pain = None if body.pain in (None, "none") else body.pain
    with db.tx() as c:
        c.execute("INSERT INTO checkins(user_id, date, feeling, skip, note, pain) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET feeling=COALESCE(excluded.feeling, feeling), "
                  "skip=CASE WHEN ? THEN excluded.skip ELSE skip END, note=COALESCE(excluded.note, note), "
                  "pain=CASE WHEN ? THEN excluded.pain ELSE pain END",
                  (user["id"], d, body.feeling, int(body.skip), body.note, pain, body.skip or body.feeling is None and body.pain is None,
                   body.pain is not None))
    out = sync.replan(user["id"])
    if d == today_local().isoformat() and (body.feeling is not None or body.pain is not None):
        ck = db.q1("SELECT feeling, pain FROM checkins WHERE user_id=? AND date=?", (user["id"], d)) or {}
        out["advice"] = coach.checkin_advice(user["id"], d, ck.get("feeling"), ck.get("pain"))
    return out


class DecisionIn(BaseModel):
    choice: str                  # achievable | postpone | accept_risk
    date: str | None = None


@app.post("/api/plan/decision")
def plan_decision(body: DecisionIn, user=Depends(auth.current_user)):
    """可行性不通過時，使用者自己選怎麼處理（規格書：把權衡展示給使用者選，不要替他決定）。"""
    _need_link(user)
    try:
        season.decide(user["id"], body.choice, body.date)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sync.replan(user["id"])
    return season.season_plan(user["id"])


class CheckpointIn(BaseModel):
    date: str


@app.post("/api/plan/checkpoint/accept")
def accept_checkpoint(body: CheckpointIn, user=Depends(auth.current_user)):
    _need_link(user)
    try:
        season.accept_checkpoint(user["id"], body.date)
    except KeyError:
        raise HTTPException(404, "找不到這個檢核點的結果")
    sync.replan(user["id"])
    return season.season_plan(user["id"])


@app.post("/api/plan/resume")
def resume_plan(user=Depends(auth.current_user)):
    """疼痛停跑後恢復課表。"""
    season.resume_after_pain(user["id"])
    return sync.replan(user["id"])


# ───────────────────────────── 分析 / 長期規劃 / 角色 ─────────────────────────────

def _need_link(user):
    if user["provider"] == "none":
        raise HTTPException(409, "請先連結 Garmin 或使用範例資料")


@app.get("/api/analysis")
def get_analysis(user=Depends(auth.current_user)):
    _need_link(user)
    return analysis.analyze(user["id"])


@app.get("/api/season")
def get_season(user=Depends(auth.current_user)):
    _need_link(user)
    return season.season_plan(user["id"])


@app.get("/api/month")
def get_month(ym: str | None = None, user=Depends(auth.current_user)):
    _need_link(user)
    ym = ym or today_local().isoformat()[:7]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ym):
        raise HTTPException(400, "月份格式請用 YYYY-MM")
    return season.month(user["id"], ym)


@app.get("/api/game")
def get_game(user=Depends(auth.current_user)):
    return {**game.compute(user["id"]), "rules": game.rules(), "species": game.SPECIES}


class CharacterIn(BaseModel):
    species: str
    nickname: str | None = Field(default=None, max_length=12)


@app.put("/api/character")
def put_character(body: CharacterIn, user=Depends(auth.current_user)):
    if body.species not in game.SPECIES:
        raise HTTPException(400, "沒有這種角色")
    nick = (body.nickname or "").strip() or None
    with db.tx() as c:
        c.execute("INSERT INTO character(user_id, species, nickname) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET species=excluded.species, nickname=excluded.nickname",
                  (user["id"], body.species, nick))
    return game.compute(user["id"])


@app.get("/api/summary")
def get_summary(user=Depends(auth.current_user)):
    _need_link(user)
    return views.summary(user["id"])


@app.get("/api/metric/{key}")
def get_metric(key: str, range: str = "W", user=Depends(auth.current_user)):
    _need_link(user)
    if range not in ("W", "M", "6M", "Y"):
        raise HTTPException(400, "range 只能是 W、M、6M、Y")
    try:
        return views.metric_series(user["id"], key, range)
    except KeyError:
        raise HTTPException(404, "沒有這個指標")


@app.get("/api/activities")
def get_activities(before: str | None = None, limit: int = 40, user=Depends(auth.current_user)):
    _need_link(user)
    return views.activity_overview(user["id"], before, max(1, min(limit, 100)))


@app.get("/api/activities/{activity_id}")
def get_activity(activity_id: str, user=Depends(auth.current_user)):
    _need_link(user)
    try:
        return views.activity_detail(user["id"], activity_id)
    except KeyError:
        raise HTTPException(404, "找不到這筆活動")


# ───────────────────────────── 晨報、晚報、推播 ─────────────────────────────

@app.get("/api/brief/{kind}")
def get_brief(kind: str, refresh: bool = False, user=Depends(auth.current_user)):
    _need_link(user)
    if kind not in briefs.BUILDERS:
        raise HTTPException(404, "沒有這種報告")
    if refresh:
        try:
            sync.sync_user(user["id"])
        except Exception as e:
            log.warning("報告前同步失敗: %s", e)
    ds = (briefs.review_week(today_local()) + timedelta(days=6)).isoformat() if kind == "weekly" else None
    b = None if refresh else briefs.get(user["id"], kind, ds)
    stale = b and (datetime.now(TZ) - datetime.fromisoformat(b["generated_at"])).total_seconds() > 600
    if b is None or stale:
        b = briefs.build_and_save(user["id"], kind)
    g = game.compute(user["id"])
    b["chat"] = {**pet_voice.chat(b, g["character"]), "stage": g["stage"]}
    prefs = db.get_prefs(user["id"])
    b["schedule"] = {"enabled": prefs.get("brief_enabled", True), "morning": prefs.get("brief_morning"), "evening": prefs.get("brief_evening"),
                     "weekly": prefs.get("brief_weekly") if prefs.get("weekly_review", True) else None}
    return b


class BriefReplyIn(BaseModel):
    type: str                    # feel | rpe | option | keep
    value: int | None = None
    label: str | None = None
    apply: dict | None = None


@app.post("/api/brief/{kind}/reply")
def brief_reply(kind: str, body: BriefReplyIn, user=Depends(auth.current_user)):
    """夥伴對話裡的按鈕：回答感覺、跑後感受，或套用改課選項。回傳夥伴接下來說的話。"""
    _need_link(user)
    if kind not in ("morning", "evening"):
        raise HTTPException(404, "沒有這種報告")
    uid, today = user["id"], today_local().isoformat()
    result: dict = {}
    if body.type == "feel":
        if body.value not in (1, 2, 3, 4, 5):
            raise HTTPException(400, "感覺要是 1–5")
        with db.tx() as c:
            c.execute("INSERT INTO checkins(user_id, date, feeling) VALUES(?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET feeling=excluded.feeling", (uid, today, body.value))
        sync.replan(uid)
        ck = db.q1("SELECT feeling, pain FROM checkins WHERE user_id=? AND date=?", (uid, today)) or {}
        result["advice"] = coach.checkin_advice(uid, today, ck.get("feeling"), ck.get("pain"))
    elif body.type == "rpe":
        if body.value not in (1, 2, 3, 4, 5):
            raise HTTPException(400, "感受要是 1–5")
        with db.tx() as c:
            c.execute("INSERT INTO checkins(user_id, date, rpe) VALUES(?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET rpe=excluded.rpe", (uid, today, body.value))
        b = briefs.build_and_save(uid, "evening")
        result["fatigue"] = b.get("fatigue")
    elif body.type == "option":
        ap = body.apply or {}
        if ap.get("type") == "override":
            put_override(OverrideIn(date=ap.get("date") or "", kind=ap.get("kind") or "",
                                    km=max(1.0, float(ap["km"])) if ap.get("km") else None), user)
        elif ap.get("type") == "swap":
            swap_days(SwapIn(a=ap.get("a") or "", b=ap.get("b") or ""), user)
        else:
            raise HTTPException(400, "看不懂這個選項")
        sync.replan(uid)
        briefs.build_and_save(uid, kind)
    elif body.type != "keep":
        raise HTTPException(400, "看不懂這個回答")
    g = game.compute(uid)
    return pet_voice.reply(kind, body.model_dump(), result, g["character"])


@app.post("/api/brief/morning/apply")
def apply_morning(user=Depends(auth.current_user)):
    _need_link(user)
    b = briefs.get(user["id"], "morning") or briefs.build_and_save(user["id"], "morning")
    s = b.get("suggestion")
    if not s:
        raise HTTPException(400, "今天沒有需要調整的地方")
    with db.tx() as c:
        c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes, km) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes, km=excluded.km",
                  (user["id"], b["date"], s["kind"], s.get("minutes"), s.get("km")))
    sync.replan(user["id"])
    return {"ok": True, "applied": s}


# ───────────────────────────── 休息日肌力／伸展 ─────────────────────────────

@app.get("/api/strength/{ds}")
def get_strength(ds: str, routine: str | None = None, user=Depends(auth.current_user)):
    try:
        d = date.fromisoformat(ds)
    except ValueError:
        raise HTTPException(400, "日期格式不對")
    logged = strength.done_dates(user["id"], ds, ds)
    if routine not in strength.ROUTINES:
        w0 = d - timedelta(days=d.weekday())
        routine = logged.get(ds) or strength.plan_week(strength.week_kinds(user["id"], w0)).get(ds) or "core"
    return {**strength.detail(routine), "date": ds, "done": ds in logged, "xp": strength.XP_STRENGTH,
            "others": [strength.summary_for(k) for k in strength.ROUTINES if k != routine]}


class StrengthIn(BaseModel):
    routine: str
    done: bool = True


@app.post("/api/strength/{ds}")
def done_strength(ds: str, body: StrengthIn, user=Depends(auth.current_user)):
    try:
        date.fromisoformat(ds)
        strength.mark(user["id"], ds, body.routine, body.done)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/push")
def push_info(user=Depends(auth.current_user)):
    return {"available": push.available(), "key": push.public_key(), "devices": push.count(user["id"]),
            "https": PUBLIC_BASE_URL.startswith("https://") if PUBLIC_BASE_URL else None}


class PushSubIn(BaseModel):
    subscription: dict


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubIn, user=Depends(auth.current_user)):
    try:
        push.subscribe(user["id"], body.subscription)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"devices": push.count(user["id"])}


@app.delete("/api/push/subscribe")
def push_unsubscribe(endpoint: str | None = None, user=Depends(auth.current_user)):
    push.unsubscribe(user["id"], endpoint)
    return {"devices": push.count(user["id"])}


@app.post("/api/push/test")
def push_test(user=Depends(auth.current_user)):
    n = push.send(user["id"], "跑課測試通知", "收到這則就代表晨報和晚報會準時送到。", "/#/brief/morning")
    if not n:
        raise HTTPException(400, "沒有送出：這支手機還沒開啟通知，或推播套件沒安裝")
    return {"sent": n}


@app.get("/api/week")
def get_week(user=Depends(auth.current_user)):
    _need_link(user)
    return views.this_week(user["id"])


class OverrideIn(BaseModel):
    date: str
    kind: str
    minutes: float | None = Field(default=None, ge=10, le=300)
    km: float | None = Field(default=None, ge=1, le=60)


def _editable_date(d: str, days: int = 60):
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "日期格式錯誤")
    if not (today_local() <= dd <= today_local() + timedelta(days=days)):
        raise HTTPException(400, f"只能調整今天到未來 {days} 天的課")
    return dd


MAX_IMPORT_BYTES = 5_000_000
MAX_HISTORY_BYTES = 600_000_000      # Strava 完整封存檔含照片時可能好幾百 MB


@app.post("/api/import-history")
async def import_history(request: Request, user=Depends(auth.current_user)):
    """上傳舊紀錄：Strava 封存檔 .zip，或 .fit／.gpx／.tcx（可以是 .gz）。請求內容就是檔案本身，檔名放在 X-Filename。"""
    import tempfile
    from urllib.parse import unquote

    name = unquote(request.headers.get("x-filename") or "upload")
    size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tmp:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_HISTORY_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(400, "檔案太大（上限 600 MB）。Strava 封存檔可以先解壓，只上傳 activities.csv")
            tmp.write(chunk)
        path = tmp.name
    try:
        if not size:
            raise HTTPException(400, "檔案是空的")
        try:
            acts, bad = await run_in_threadpool(file_import.parse_upload, name, path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            log.warning("匯入舊紀錄失敗 %s: %s", name, e)
            raise HTTPException(400, "這個檔案打不開，請確認是 Strava 封存檔 .zip，或 .fit／.gpx／.tcx 檔")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    keep, dup = file_import.dedupe(user["id"], acts)
    if keep:
        sync.store(user["id"], keep, [])
        season.reset(user["id"])          # 有了舊紀錄，重新診斷能力和起始跑量
        if user["provider"] != "none":
            sync.replan(user["id"])
    runs = [a for a in keep if not a.get("cross")]
    dates = sorted(a["date"] for a in keep)
    return {"ok": True, "runs": len(runs), "others": len(keep) - len(runs), "duplicates": dup, "unreadable": bad,
            "run_km": round(sum((a.get("distance_m") or 0) for a in runs) / 1000, 1),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None}


@app.post("/api/import-plan")
async def import_plan(request: Request, user=Depends(auth.current_user)):
    """上傳 .docx 逐日課表（請求內容就是檔案本身）。成功後切換成「照匯入的課表」。"""
    data = await request.body()
    if not data or len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(400, "檔案是空的或太大（上限 5 MB）")
    if data[:2] != b"PK":
        raise HTTPException(400, "請上傳 Word 的 .docx 檔")
    try:
        paras, tables = plan_import.read_docx(data)
        plan = plan_import.parse_plan(paras, tables, today_local())
    except ValueError as e:
        raise HTTPException(400, f"看不懂這份課表：{e}")
    except Exception as e:
        log.warning("匯入課表失敗: %s", e)
        raise HTTPException(400, "這個檔案打不開，請確認是 Word 的 .docx 檔")
    if not plan["n_days"]:
        raise HTTPException(400, "課表裡沒有讀到任何一天")
    plan_import.save(user["id"], plan)
    if user["provider"] != "none":
        sync.replan(user["id"])
    return {k: plan[k] for k in ("name", "start", "end", "n_days", "unknown")}


@app.delete("/api/import-plan")
def delete_import(user=Depends(auth.current_user)):
    plan_import.clear(user["id"])
    if user["provider"] != "none":
        sync.replan(user["id"])
    return {"ok": True}


@app.put("/api/override")
def put_override(body: OverrideIn, user=Depends(auth.current_user)):
    _editable_date(body.date)
    if body.kind not in {"rest", *MANUAL_KINDS}:
        raise HTTPException(400, "沒有這種課")
    with db.tx() as c:
        c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes, km) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes, km=excluded.km",
                  (user["id"], body.date, body.kind, body.minutes, body.km))
        c.execute("UPDATE checkins SET skip=0 WHERE user_id=? AND date=?", (user["id"], body.date))
    return views.this_week(user["id"])


@app.delete("/api/override")
def delete_override(date: str, user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute("DELETE FROM plan_overrides WHERE user_id=? AND date=?", (user["id"], date))
        c.execute("UPDATE checkins SET skip=0 WHERE user_id=? AND date=?", (user["id"], date))
    return views.this_week(user["id"])


class SwapIn(BaseModel):
    a: str
    b: str


@app.post("/api/swap")
def swap_days(body: SwapIn, user=Depends(auth.current_user)):
    """兩天的課互換（例如週四的間歇跟週五的休息對調）。"""
    _editable_date(body.a, 6), _editable_date(body.b, 6)
    plan = {d["date"]: d for d in sync.replan(user["id"])["plan"]["days"]}
    pa, pb = plan.get(body.a), plan.get(body.b)
    if not pa or not pb:
        raise HTTPException(400, "找不到這兩天的課")
    kind_of = lambda p: "rest" if p["kind"] in ("rest", "done", "race", "event", "test") else (
        "long" if p["kind"] == "long_m" else p["kind"])
    with db.tx() as c:
        for d, p in ((body.a, pb), (body.b, pa)):
            c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes, km) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes, km=excluded.km",
                      (user["id"], d, kind_of(p), None, p.get("distance_km") or None))
    return views.this_week(user["id"])


@app.get("/api/runner")
def runner_profile(user=Depends(auth.current_user)):
    _need_link(user)
    return runner.profile(user["id"])


@app.get("/api/profile")
def get_profile(user=Depends(auth.current_user)):
    _need_link(user)
    return views.ability_profile(user["id"])


@app.get("/api/health")
def get_health(user=Depends(auth.current_user)):
    _need_link(user)
    return views.health_ranges(user["id"])


@app.get("/api/leaderboard")
def get_leaderboard(user=Depends(auth.current_user)):
    rows = game.leaderboard()
    for r in rows:
        r["me"] = r.pop("user_id") == user["id"]
    return rows


# ───────────────────────────── 行事曆訂閱 ─────────────────────────────

@app.get("/cal/{token}.ics")
def calendar(token: str, request: Request):
    user = db.q1("SELECT * FROM users WHERE cal_token=?", (token,))
    if not user:
        raise HTTPException(404)
    since = (today_local() - timedelta(days=21)).isoformat()
    days = [json.loads(r["data"]) for r in db.q("SELECT data FROM plan WHERE user_id=? AND date>=? ORDER BY date",
                                                  (user["id"], since))]
    body = build_ics(user, days, db.get_prefs(user["id"]), _base_url(request))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "Content-Disposition": 'inline; filename="runplan.ics"'})


@app.post("/api/calendar/rotate")
def rotate_calendar(user=Depends(auth.current_user)):
    """訂閱網址外流時可以換一組。"""
    import secrets

    with db.tx() as c:
        c.execute("UPDATE users SET cal_token=? WHERE id=?", (secrets.token_urlsafe(24), user["id"]))
    return {"ok": True}


# ───────────────────────────── PWA 靜態檔 ─────────────────────────────

@app.get("/sw.js")
def sw():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def no_stale_shell(request, call_next):
    """程式檔（js／css／html／json）每次都向伺服器確認有沒有新版，避免更新後手機還在用舊程式。
    沒變的話伺服器回 304，不會多花流量。圖片照常快取。"""
    resp = await call_next(request)
    path = request.url.path
    if request.method == "GET" and not path.startswith("/api/") and (
            path == "/" or path.endswith((".js", ".css", ".html", ".json", ".webmanifest"))):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
