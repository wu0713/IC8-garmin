"""FastAPI 入口：API + PWA 靜態檔 + 行事曆訂閱 + 每日排程。

啟動：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, auth, db, game, season, sync, views
from . import briefs, pet_voice, push
from .config import ALLOW_DEMO, INVITE_CODE, PUBLIC_BASE_URL, SYNC_HOURS, TZ, WEB_DIR
from .garmin_client import MfaRequired, RealGarmin, today_local
from .ics import build_ics
from .planner import GOAL_TYPES, WORKOUT_LABELS, default_prefs

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
        log.info("每日自動同步時間：%s 點；晨報、晚報每 10 分鐘檢查一次", SYNC_HOURS)
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


DATA_TABLES = ("activities", "cross_activities", "briefs", "daily", "plan", "plan_meta", "checkins", "records", "device", "activity_series", "plan_overrides")


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
    return {"goal_types": GOAL_TYPES, "workouts": WORKOUT_LABELS, "defaults": default_prefs(),
            "cards": views.CARD_LABELS, "default_cards": views.DEFAULT_CARDS,
            "invite_required": bool(INVITE_CODE), "allow_demo": ALLOW_DEMO}


@app.get("/api/prefs")
def get_prefs(user=Depends(auth.current_user)):
    return db.get_prefs(user["id"])


@app.put("/api/prefs")
def put_prefs(body: dict, user=Depends(auth.current_user)):
    prefs = db.get_prefs(user["id"])
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
    prefs["intensity"] = max(0, min(100, int(prefs.get("intensity", 50))))
    prefs["plan_mode"] = "manual" if prefs.get("plan_mode") == "manual" else "auto"
    kinds_ok = {"rest", "easy", "recovery", "long", *WORKOUT_LABELS} - {"strides"}
    prefs["template"] = {str(k): {"kind": v["kind"], "minutes": max(10, min(300, float(v.get("minutes") or 40)))}
                         for k, v in (prefs.get("template") or {}).items()
                         if str(k) in "0123456" and isinstance(v, dict) and v.get("kind") in kinds_ok}
    races = []
    for r in (prefs.get("races") or [])[:20]:
        try:
            date.fromisoformat(r["date"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "比賽日期格式錯誤")
        races.append({"name": str(r.get("name") or "比賽")[:30], "date": r["date"],
                      "distance_km": float(r["distance_km"]) if r.get("distance_km") else None,
                      "target_time": str(r.get("target_time") or "")[:10]})
    prefs["races"] = sorted(races, key=lambda r: r["date"])
    if prefs.get("dashboard") is not None:
        prefs["dashboard"] = [k for k in prefs["dashboard"] if k in views.CARD_LABELS][:16]
    for k in ("run_time", "brief_morning", "brief_evening"):
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", prefs.get(k) or ""):
            raise HTTPException(400, "時間格式請用 HH:MM")
    prefs["brief_enabled"] = bool(prefs.get("brief_enabled", True))
    db.save_prefs(user["id"], prefs)
    if user["provider"] != "none":
        sync.replan(user["id"])
    return prefs


class CheckinIn(BaseModel):
    date: str | None = None
    feeling: int | None = Field(default=None, ge=1, le=5)
    skip: bool = False
    note: str | None = None


@app.post("/api/checkin")
def checkin(body: CheckinIn, user=Depends(auth.current_user)):
    d = body.date or today_local().isoformat()
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "日期格式錯誤")
    if not (today_local() <= dd <= today_local() + timedelta(days=14)):
        raise HTTPException(400, "只能設定今天到未來兩週")
    note = body.note
    if body.skip:   # 記下被跳過的是哪堂課，之後可以提議挪到別天
        old = db.q1("SELECT data FROM plan WHERE user_id=? AND date=?", (user["id"], d))
        if old:
            p = json.loads(old["data"])
            if p.get("kind") not in ("rest", "done", "race", None):
                note = json.dumps({"kind": p["kind"], "title": p["title"], "minutes": p.get("minutes")}, ensure_ascii=False)
    with db.tx() as c:
        c.execute("INSERT INTO checkins(user_id, date, feeling, skip, note) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET feeling=COALESCE(excluded.feeling, feeling), "
                  "skip=excluded.skip, note=COALESCE(excluded.note, note)",
                  (user["id"], d, body.feeling, int(body.skip), note))
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
    b = None if refresh else briefs.get(user["id"], kind)
    stale = b and (datetime.now(TZ) - datetime.fromisoformat(b["generated_at"])).total_seconds() > 600
    if b is None or stale:
        b = briefs.build_and_save(user["id"], kind)
    g = game.compute(user["id"])
    b["chat"] = {**pet_voice.chat(b, g["character"]), "stage": g["stage"]}
    prefs = db.get_prefs(user["id"])
    b["schedule"] = {"enabled": prefs.get("brief_enabled", True), "morning": prefs.get("brief_morning"), "evening": prefs.get("brief_evening")}
    return b


@app.post("/api/brief/morning/apply")
def apply_morning(user=Depends(auth.current_user)):
    _need_link(user)
    b = briefs.get(user["id"], "morning") or briefs.build_and_save(user["id"], "morning")
    s = b.get("suggestion")
    if not s:
        raise HTTPException(400, "今天沒有需要調整的地方")
    with db.tx() as c:
        c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes",
                  (user["id"], b["date"], s["kind"], s.get("minutes")))
    sync.replan(user["id"])
    return {"ok": True, "applied": s}


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


def _editable_date(d: str, days: int = 60):
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "日期格式錯誤")
    if not (today_local() <= dd <= today_local() + timedelta(days=days)):
        raise HTTPException(400, f"只能調整今天到未來 {days} 天的課")
    return dd


@app.put("/api/override")
def put_override(body: OverrideIn, user=Depends(auth.current_user)):
    _editable_date(body.date)
    if body.kind not in {"rest", "easy", "recovery", "long", *WORKOUT_LABELS} or body.kind == "strides":
        raise HTTPException(400, "沒有這種課")
    with db.tx() as c:
        c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes",
                  (user["id"], body.date, body.kind, body.minutes))
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
    kind_of = lambda p: "rest" if p["kind"] in ("rest", "done", "race") else p["kind"]
    with db.tx() as c:
        for d, p in ((body.a, pb), (body.b, pa)):
            c.execute("INSERT INTO plan_overrides(user_id, date, kind, minutes) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id, date) DO UPDATE SET kind=excluded.kind, minutes=excluded.minutes",
                      (user["id"], d, kind_of(p), p.get("minutes") or None))
    return views.this_week(user["id"])


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


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
