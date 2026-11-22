"""整季課表：開季快照、執行期反饋（檢核點、停損）、每週排程、月曆。

開季時把「診斷結果、跑量基線、可行性」凍結成快照，之後每天同步只排這週，不會因為多了一週資料就整張課表變樣
（規格書：使用者很在意課表突然整體變樣）。會重算的時機：
  - 改了目標類型、距離、可跑日 → 重新開季
  - 改目標時間或賽期、在可行性檢查做了選擇、接受檢核點的目標調整 → 保留開季日與基線，重算可行性
  - 檢核點測驗成績進來 → 配速區間自動依新成績重算；落後時跑量從下週起不再上調
  - 停損規則：連續 2 週完成度 < 80%、漂移連續偏高、整週沒跑 → 在本週插入重做／減量／重跑週
已經過去的週次不受影響（同一份快照、同樣的輸入，算出來就一樣）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import athlete as athlete_mod
from . import db, engine, plan_import, planner
from . import plan_tables as T
from .garmin_client import today_local
from .week import build_to_km, build_workout, schedule_week

PHASE_COLORS = {"base": "base", "build": "build", "specific": "specific", "taper": "taper", "race": "race", "hold": "hold", "recover": "taper"}


# ───────────────────────────── 快照 ─────────────────────────────

def _keys(prefs: dict, goal: dict) -> tuple[str, str]:
    hard = json.dumps([goal["type"], goal["distance_km"], bool(goal.get("race_date")), sorted(prefs["days"]),
                       prefs["long_run_day"], prefs.get("level") or "auto", sorted(prefs.get("excused_weeks") or []),
                       {k: v for k, v in (prefs.get("self_report") or {}).items() if k != "updated"}])
    soft = json.dumps([goal.get("race_date"), goal.get("target_s"), prefs.get("max_minutes_weekday"),
                       prefs.get("max_long_minutes")])
    return hard, soft


def load_snap(uid: int) -> dict | None:
    r = db.q1("SELECT data FROM season_state WHERE user_id=?", (uid,))
    return json.loads(r["data"]) if r else None


def reset(uid: int):
    """重新開季（例如匯入了舊紀錄）：下次排課時重新診斷能力、起始跑量和可行性。"""
    with db.tx() as c:
        c.execute("DELETE FROM season_state WHERE user_id=?", (uid,))


def save_snap(uid: int, snap: dict):
    with db.tx() as c:
        c.execute("INSERT INTO season_state(user_id, data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                  (uid, json.dumps(snap, ensure_ascii=False)))


def capacity_km(prefs: dict, diag: dict) -> float:
    e = engine.pace_at(diag.get("cur_vdot") or 35, 0.66)
    mins = float(prefs["max_minutes_weekday"]) * max(0, len(prefs["days"]) - 1) + float(prefs["max_long_minutes"])
    return mins * 60 / e


def weeks_until(plan_start: date, goal: dict) -> int:
    if not goal.get("race_date"):
        return 12
    return max(1, (engine.monday(date.fromisoformat(goal["race_date"])) - plan_start).days // 7 + 1)


def refresh_feasibility(snap: dict, prefs: dict, goal: dict):
    ps = date.fromisoformat(snap["plan_start"])
    n = weeks_until(ps, goal)
    snap["feasibility"] = (engine.feasibility(snap["diag"], goal, n, capacity_km(prefs, snap["diag"]), snap.get("level"))
                           if goal.get("race_date") else None)


def ensure_snap(uid: int, today: date, prefs: dict, goal: dict, ath: dict) -> dict:
    snap = load_snap(uid)
    hard, soft = _keys(prefs, goal)
    open_mode = not goal.get("race_date")
    stale = (not snap or snap.get("key_hard") != hard
             or (open_mode and today >= date.fromisoformat(snap["plan_start"]) + timedelta(weeks=12)))
    if stale:
        diag = engine.diagnose(ath, goal)
        snap = {"plan_start": engine.monday(today).isoformat(), "key_hard": hard, "key_soft": soft, "diag": diag,
                "v_base": max(diag["baseline_km"], 8.0), "start_long_km": max(ath["recent_longest_km"], 3.0),
                "decision": None, "insertions": {}, "freeze_from": None, "triggers": {}, "results": {},
                "resume_after": None, "created": today.isoformat()}
        lv = engine.athlete_level(ath, diag, prefs.get("level"))
        snap["level"], snap["level_info"] = lv["level"], lv
        refresh_feasibility(snap, prefs, goal)
        save_snap(uid, snap)
    elif not snap.get("level"):          # 舊版快照：補上程度（不重排整季）
        lv = engine.athlete_level(ath, snap["diag"], prefs.get("level"))
        snap["level"], snap["level_info"] = lv["level"], lv
        save_snap(uid, snap)
    if snap.get("key_soft") != soft:
        snap["key_soft"] = soft
        snap["decision"] = None
        refresh_feasibility(snap, prefs, goal)
        save_snap(uid, snap)
    return snap


# ───────────────────────────── 整季 + 排程 ─────────────────────────────

class Ctx:
    """一次請求內共用的東西：偏好、目標、跑者資料、快照、整季週表、配速區間快取。"""

    def __init__(self, uid: int, today: date | None = None):
        self.uid = uid
        self.today = today or today_local()
        self.prefs = db.get_prefs(uid)
        self.goal = planner.goal_from_prefs(self.prefs, self.today)
        self.ath = athlete_mod.build(uid, self.today, self.prefs)
        self.snap = ensure_snap(uid, self.today, self.prefs, self.goal, self.ath)
        self.level = self.snap.get("level") or "novice"
        self.plan_goal = dict(self.goal)
        self._runtime()
        self.season = engine.build_season(ath=self.ath, goal=self.goal, prefs=self.prefs, snap=self.snap, today=self.today)
        self.level = self.snap.get("level") or "novice"
        self.assess = engine.assess(diag=self.live_diag(), goal=self.goal, level=self.level, weeks=self.season["n_weeks"],
                                    feas=self.season["feasibility"], ath=self.ath, decision=self.snap.get("decision"))
        # 目標不切實際：配速照「做得到的成績」排，不照目標硬推
        self.plan_goal = dict(self.goal)
        if self.assess.get("verdict") == "unrealistic" and self.assess.get("realistic_s"):
            self.plan_goal["target_s"] = self.assess["realistic_s"]
        self.provisional_s = None
        feas = self.season.get("feasibility") or {}
        if self.season.get("provisional") and feas.get("achievable_s"):
            # 暫定成績：時間允許的跑量做得到的成績，但不比你現在的能力慢、也不比原目標快
            pv = feas["achievable_s"]
            if self.assess.get("pred_goal_s"):
                pv = min(pv, self.assess["pred_goal_s"])
            pv = max(pv, self.goal.get("target_s") or 0, self.plan_goal.get("target_s") or 0)
            self.plan_goal["target_s"] = self.provisional_s = round(pv)
        self.model = engine.fit_hr_pace(self.ath["easy_samples"])
        self._zones: dict[str, dict] = {}
        self._sched: dict[int, dict] = {}
        self.plan_start = date.fromisoformat(self.season["plan_start"])

    # 配速：E 用「現在」的能力（跟著新成績更新），強度課依階段往目標內推
    def live_diag(self) -> dict:
        d = dict(self.snap["diag"])
        b = self.ath.get("best_effort")
        if b:
            d["cur_vdot"] = round(engine.vdot(b["km"], b["seconds"]), 1)
        elif self.ath.get("fallback_vdot"):
            d["cur_vdot"] = d.get("cur_vdot") or self.ath["fallback_vdot"]
        d["easy_too_fast"] = bool(self.ath.get("easy_hr_share") and self.ath["easy_hr_share"] > T.EASY_TOO_FAST_HR)
        return d

    def zones(self, phase: str) -> dict:
        if phase not in self._zones:
            ph = "taper" if phase == "race" else phase
            self._zones[phase] = engine.derive_zones(self.live_diag(), self.plan_goal, ph, self.ath["max_hr"], self.model)
        return self._zones[phase]

    def week_idx(self, d: date) -> int:
        return (engine.monday(d) - self.plan_start).days // 7

    def week(self, i: int) -> dict | None:
        ws = self.season["weeks"]
        return ws[i] if 0 <= i < len(ws) else None

    def schedule(self, i: int) -> dict:
        """第 i 週排好的課（含上週尾巴的約束）。"""
        if i in self._sched:
            return self._sched[i]
        w = self.week(i)
        if not w:
            return {"days": {}, "notes": [], "check": {}}
        prev = self.schedule(i - 1) if i > 0 else {"days": {}}
        ws = date.fromisoformat(w["week_start"])
        tail = {k: v["kind"] for k, v in prev["days"].items() if k >= (ws - timedelta(days=3)).isoformat()}
        res = schedule_week(w, {
            "avail": set(int(x) for x in self.prefs["days"]), "long_run_day": self.prefs["long_run_day"],
            "zones": self.zones(w["phase"])["zones"], "diag": self.snap["diag"], "goal": self.plan_goal, "level": self.level,
            "event_days": self.season["event_days"], "prev_tail": tail,
            "max_minutes_weekday": self.prefs["max_minutes_weekday"],
            "liked": {k for k, v in self.prefs["workouts"].items() if v}, "max_hr": self.ath["max_hr"],
            "novice": self.ath["novice"] and w["idx"] < 3,
        })
        self._sched[i] = res
        return res

    def day(self, d: date) -> dict | None:
        i = self.week_idx(d)
        return self.schedule(i)["days"].get(d.isoformat()) if self.week(i) else None

    # ── 執行期反饋（第 8、9 層） ──
    def _runtime(self):
        snap, today = self.snap, self.today
        changed = False
        season = engine.build_season(ath=self.ath, goal=self.goal, prefs=self.prefs, snap=snap, today=today)
        ps = date.fromisoformat(season["plan_start"])
        cur = (engine.monday(today) - ps).days // 7
        # 檢核點：測驗日（前後一天）有夠長的跑步 → 判讀
        for cp in season["checkpoints"]:
            if cp["date"] > today.isoformat() or cp["date"] in snap["results"]:
                continue
            d = date.fromisoformat(cp["date"])
            acts = db.q("SELECT distance_m, duration_s, date FROM activities WHERE user_id=? AND date BETWEEN ? AND ?",
                        (self.uid, (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()))
            hit = max((a for a in acts if (a["distance_m"] or 0) / 1000 >= cp["test_km"] * 0.9 and a["duration_s"]),
                      key=lambda a: a["distance_m"], default=None)
            if not hit:
                continue
            res = engine.judge_checkpoint(cp, hit["duration_s"], hit["distance_m"] / 1000)
            res.update({"date": hit["date"], "test_km": cp["test_km"], "label": cp["label"], "accepted": False})
            snap["results"][cp["date"]] = res
            if res["action"] in ("slow_small", "slow_big"):
                snap["freeze_from"] = cp["week"] + 1
            changed = True
        # 停損：需要過去兩週「排了多少 vs 跑了多少」
        if 0 < cur < season["n_weeks"]:
            tmp = Ctx.__new__(Ctx)
            tmp.__dict__.update(self.__dict__)
            tmp.season, tmp.model, tmp._zones, tmp._sched, tmp.plan_start = season, engine.fit_hr_pace(self.ath["easy_samples"]), {}, {}, ps
            comp = []
            for i in range(max(0, cur - T.COMPLETION_WEEKS), cur):
                w = season["weeks"][i]
                ws = date.fromisoformat(w["week_start"])
                planned = sum(x["distance_km"] for x in tmp.schedule(i)["days"].values() if x["kind"] not in ("rest",))
                acts = db.q("SELECT distance_m FROM activities WHERE user_id=? AND date BETWEEN ? AND ?",
                            (self.uid, ws.isoformat(), (ws + timedelta(days=6)).isoformat()))
                excused = any(season["event_days"].get((ws + timedelta(days=k)).isoformat(), ("",))[0] in ("rest", "event")
                              for k in range(7)) or w["target_km"] < w["curve_km"] * 0.95
                comp.append({"week_start": w["week_start"], "planned_km": round(planned, 1), "runs": len(acts),
                             "actual_km": round(sum((a["distance_m"] or 0) for a in acts) / 1000, 1), "excused": excused})
            self.week_completion = comp
            if self.prefs.get("plan_mode") != "manual":
                trig = engine.evaluate_stoploss(today=today, checkins={}, rhr={}, week_completion=comp,
                                                drifts=self.ath["drifts_recent"], resume_after=None)
                pre_taper = sum(1 for w in season["weeks"] if w["phase"] not in ("taper", "race"))
                for t in trig:
                    key = None
                    if t["rule"] == "missed_week":
                        key, ins, at = f"missed:{t['week']}", "repeat", cur
                    elif t["rule"] == "completion":
                        key, ins, at = f"completion:{t['weeks'][-1]}", "phase_repeat", cur
                    elif t["rule"] == "drift":
                        recent = [k for k in snap["triggers"] if k.startswith("drift:") and k[6:] >= (today - timedelta(days=14)).isoformat()]
                        if recent:
                            continue
                        key, ins, at = f"drift:{today.isoformat()}", "deload", cur if today.weekday() <= 2 else cur + 1
                    if key and key not in snap["triggers"] and at < pre_taper:
                        snap["triggers"][key] = {"rule": t["rule"], "title": t["title"], "action": t["action"], "week": at,
                                                 "date": today.isoformat()}
                        snap["insertions"][str(at)] = ins
                        changed = True
        else:
            self.week_completion = []
        if changed:
            save_snap(self.uid, snap)

    def stoploss_now(self) -> list[dict]:
        """每天看的停損（疼痛、晨間心率）。"""
        ck = {r["date"]: r for r in db.q("SELECT * FROM checkins WHERE user_id=? AND date>=?",
                                          (self.uid, (self.today - timedelta(days=4)).isoformat()))}
        out = engine.evaluate_stoploss(today=self.today, checkins=ck, rhr=self.ath["rhr"], week_completion=[],
                                       drifts=[], resume_after=self.snap.get("resume_after"))
        return [t for t in out if t["rule"] in ("pain", "rhr")]


# ───────────────────────────── 每日課表 ─────────────────────────────

def daily_plan(uid: int, today: date, *, checkins: dict, done_dates: set[str], ready: dict | None = None) -> dict:
    cx = Ctx(uid, today)
    horizon = [today + timedelta(days=i) for i in range(7)]
    sched, week_target = {}, {}
    for d in horizon:
        w = cx.week(cx.week_idx(d))
        x = cx.day(d)
        if x:
            sched[d.isoformat()] = {**x, "phase": w["phase"]}
        if w:
            week_target[d.isoformat()] = w["target_km"]
    overrides = {r["date"]: r for r in db.q("SELECT date, kind, minutes, km FROM plan_overrides WHERE user_id=? AND date>=?",
                                             (uid, today.isoformat()))}
    stop = cx.stoploss_now()
    imp_on = plan_import.active(cx.prefs)
    imported = plan_import.load(uid, today.isoformat(), horizon[-1].isoformat()) if imp_on else None

    def zones_for(d):
        w = cx.week(cx.week_idx(d))
        return cx.zones(w["phase"] if w else "base")["zones"]

    days, notes = planner.assemble_days(today=today, horizon=horizon, prefs=cx.prefs, goal=cx.goal, sched=sched,
                                        zones_for=zones_for, stoploss=stop, checkins=checkins, done_dates=done_dates,
                                        overrides=overrides, week_target=week_target, imported=imported)
    if imp_on:
        return _imported_daily(cx, today, days, notes, stop)
    w = cx.week(cx.week_idx(today))
    s = cx.schedule(cx.week_idx(today)) if w else {"notes": [], "check": {}}
    diag = cx.snap["diag"]
    feas = cx.season["feasibility"] or {}
    if w:
        n_phase = sum(1 for x in cx.season["weeks"] if x["phase"] == w["phase"])
        notes.insert(0, f"{w['phase_label']}第 {w['week_in_phase'] + 1} 週（共 {n_phase} 週）：{T.PHASE_PURPOSE.get(w['phase'], '')}")
    notes.append(f"診斷：{diag['limiter_label']} → {diag['shape']}")
    if cx.season.get("provisional"):
        notes.append(f"原目標在剩下的時間內做不到：先照 {engine.fmt_time(cx.provisional_s) if cx.provisional_s else '做得到的成績'} 排，"
                     "要改成延後賽期或照原目標練，到「課表 → 整季規劃」選")
    elif feas.get("verdict") == "tight":
        notes.append("可行但沒有緩衝：照表走，錯過的課不要補")
    if w:
        notes += w["notes"] + s.get("notes", [])
    notes += cx.season["warnings"]
    notes += cx.zones(w["phase"] if w else "base")["notes"]
    trig = [t for t in cx.snap["triggers"].values() if t["date"] >= (today - timedelta(days=7)).isoformat()]
    for t in trig:
        notes.append(f"停損：{t['title']} → {t['action']}")
    nxt = next((cp for cp in cx.season["checkpoints"] if cp["date"] >= today.isoformat()), None)
    if ready and ready.get("score") is not None and ready["score"] < 40:
        q = next((d for d in days[:2] if d.get("intensity") == "hard"), None)
        if q:
            notes.append(f"恢復分數 {ready['score']} 偏低：{q['weekday']}的「{q['title']}」可以改成輕鬆跑（課表頁一鍵改）")
    planned = sum(d.get("distance_km") or 0 for d in days if d["kind"] not in ("race", "event", "done"))
    return {
        "generated_for": today.isoformat(),
        "phase": w["phase"] if w else None, "phase_label": w["phase_label"] if w else "—",
        "week_idx": w["idx"] if w else None, "n_weeks": cx.season["n_weeks"],
        "target_km": w["target_km"] if w else None, "planned_km": round(planned, 1),
        "check": s.get("check"), "limiter": diag["limiter"], "limiter_label": diag["limiter_label"],
        "verdict": feas.get("verdict"), "hold": cx.season["hold"], "decision": cx.snap.get("decision"),
        "provisional": cx.season.get("provisional"), "provisional_time": cx.provisional_s and engine.fmt_time(cx.provisional_s),
        "stoploss": stop, "next_checkpoint": nxt,
        "notes": list(dict.fromkeys(n for n in notes if n)), "days": days,
    }


def _imported_daily(cx, today: date, days: list[dict], notes: list[str], stop: list[dict]) -> dict:
    """照匯入課表時的每日結果：不顯示 App 自己的診斷、分期、可行性，只留停損提醒。"""
    meta = cx.prefs.get("imported") or {}
    head = [f"照你匯入的「{meta.get('name', '課表')}」排課（到 {meta.get('end', '')}）。App 不會自動改課；"
            "疼痛、晨間心率偏高這些停損規則觸發時才會暫停或減量。"]
    if meta.get("end") and meta["end"] < (today + timedelta(days=6)).isoformat():
        head.append(f"匯入的課表在 {meta['end']} 結束，之後的日子回到自動排課。到「我的 → 目標與課表偏好」可以匯入下一份。")
    cur = next((d for d in days if (d.get("why") or "").startswith("匯入課表")), None)
    if cur and cur.get("why"):
        head.append(cur["why"].replace("匯入課表 ", "本週："))
    trig = [t for t in cx.snap["triggers"].values() if t["date"] >= (today - timedelta(days=7)).isoformat()]
    notes = head + notes + [f"停損：{t['title']} → {t['action']}" for t in trig]
    planned = sum(d.get("distance_km") or 0 for d in days if d["kind"] not in ("race", "event", "done"))
    mon = today - timedelta(days=today.weekday())
    week = plan_import.load(cx.uid, mon.isoformat(), (mon + timedelta(days=6)).isoformat())
    week_km = sum(w.get("distance_km") or 0 for w in week.values() if w["kind"] not in ("race", "event"))
    idx = n = None
    if meta.get("start") and meta.get("end"):
        s0 = date.fromisoformat(meta["start"])
        s0 -= timedelta(days=s0.weekday())
        idx = max(0, (mon - s0).days // 7)
        n = (date.fromisoformat(meta["end"]) - s0).days // 7 + 1
    nxt = next(({"date": ds, "label": w["title"].lstrip("🎯🏁 "), "purpose": w.get("summary", "").rstrip("。")} for ds, w in sorted(
        plan_import.load(cx.uid, today.isoformat()).items()) if w["kind"] in ("test", "race")), None)
    label = (cur or {}).get("why", "").replace("匯入課表 ", "")
    return {
        "generated_for": today.isoformat(), "phase": None, "phase_label": "匯入課表", "week_idx": idx,
        "n_weeks": n, "target_km": round(week_km, 1) or None, "planned_km": round(planned, 1), "check": {}, "limiter": None,
        "limiter_label": label, "verdict": None, "hold": False, "stoploss": stop, "next_checkpoint": nxt,
        "imported": meta, "notes": list(dict.fromkeys(n for n in notes if n)), "days": days,
    }


# ───────────────────────────── 整季規劃（API） ─────────────────────────────

def season_plan(uid: int, today: date | None = None) -> dict:
    cx = Ctx(uid, today)
    today = cx.today
    acts = db.q("SELECT date, distance_m FROM activities WHERE user_id=? AND date>=?", (uid, cx.season["plan_start"]))
    weeks = []
    for w in cx.season["weeks"]:
        ws = date.fromisoformat(w["week_start"])
        s = cx.schedule(w["idx"])
        days = s["days"]
        we = (ws + timedelta(days=6)).isoformat()
        past = ws <= today
        evs = [e.get("name") or T.EVENT_TYPES.get(e.get("type"), {}).get("label", "") for e in cx.prefs.get("events") or []
               if w["week_start"] <= e["date"] <= we]
        q = [x for x in days.values() if x["kind"] in engine.QUALITY_KINDS]
        weeks.append({
            **{k: w[k] for k in ("idx", "week_start", "phase", "phase_label", "target_km", "long_km", "tag", "notes")},
            "planned_km": s["check"].get("total_km"), "runs": len(days),
            "quality": [x["title"] for x in q],
            "actual_km": round(sum((a["distance_m"] or 0) for a in acts if w["week_start"] <= a["date"] <= we) / 1000, 1) if past else None,
            "current": ws <= today <= ws + timedelta(days=6),
            "checkpoint": w.get("checkpoint") and {k: w["checkpoint"][k] for k in ("label", "date", "test_km", "purpose", "key")},
            "events": evs + ([cx.goal["name"]] if cx.goal.get("race_date") and w["week_start"] <= cx.goal["race_date"] <= we else []),
            "self_check": s["check"], "sched_notes": s["notes"],
        })
    phases = []
    for w in weeks:
        if not phases or phases[-1]["phase"] != w["phase"]:
            phases.append({"phase": w["phase"], "label": w["phase_label"], "start": w["week_start"], "weeks": 0,
                           "purpose": T.PHASE_PURPOSE.get(w["phase"], "")})
        phases[-1]["weeks"] += 1
    cur = cx.week(cx.week_idx(today))
    zinfo = cx.zones(cur["phase"] if cur else "base")
    cps = []
    for cp in cx.season["checkpoints"]:
        cps.append({**{k: cp.get(k) for k in ("label", "date", "test_km", "effort", "purpose", "key", "equivalent_s", "thresholds", "race_name")},
                    "result": cx.snap["results"].get(cp["date"])})
    feas = cx.season["feasibility"]
    g = cx.goal
    return {
        "mode": cx.season["mode"], "hold": cx.season["hold"], "provisional": cx.season.get("provisional"),
        "provisional_time": cx.provisional_s and engine.fmt_time(cx.provisional_s), "plan_start": cx.season["plan_start"], "n_weeks": cx.season["n_weeks"],
        "goal": {"name": g.get("name"), "distance_km": g["distance_km"], "race_date": g.get("race_date"),
                 "target_s": g.get("target_s"), "target": engine.fmt_time(g.get("target_s")) if g.get("target_s") else None,
                 "type": g["type"], "race_passed": g.get("race_passed")},
        "level": cx.snap.get("level_info") or {"level": cx.level, "label": T.LEVEL_ZH[cx.level]}, "assess": {
            **cx.assess, "realistic": cx.assess.get("realistic_s") and engine.fmt_time(cx.assess["realistic_s"]),
            "pred_goal": cx.assess.get("pred_goal_s") and engine.fmt_time(cx.assess["pred_goal_s"])},
        "distance_label": T.DIST_PROFILE[T.dist_key(g["distance_km"])]["label"],
        "excused_weeks": cx.prefs.get("excused_weeks") or [],
        "diag": cx.snap["diag"], "feasibility": feas and {**feas, "achievable": feas.get("achievable_s") and engine.fmt_time(feas["achievable_s"]),
                                                          "target": engine.fmt_time(feas["target_s"])},
        "decision": cx.snap.get("decision"), "warnings": goal_event_warnings(cx.prefs, cx.goal) + cx.season["warnings"], "phases": phases, "weeks": weeks,
        "checkpoints": cps, "zones": zinfo, "max_hr": round(cx.ath["max_hr"]), "max_hr_source": cx.ath["max_hr_source"],
        "model": cx.model, "triggers": list(cx.snap["triggers"].values())[-5:],
        "completion": getattr(cx, "week_completion", []),
    }


def goal_event_warnings(prefs: dict, goal: dict) -> list[str]:
    """行程裡有一場比目標長很多的比賽，但它不是「我的目標」：課表不會為它備賽，要提醒。"""
    out = []
    g = prefs.get("goal") or {}
    if goal.get("race_passed"):
        out.append(f"目標賽日期 {g.get('event_date', '').replace('-', '/')} 已經過了，現在是照「沒有比賽」排：以 12 週為一個週期、"
                   "跑量只小幅增加，長跑也不會往比賽距離拉長。要備下一場比賽，請到「目標與課表」更新比賽日期（注意年份）。")
    today = today_local().isoformat()
    for e in prefs.get("events") or []:
        km = float(e.get("distance_km") or 0)
        if e.get("type") in ("race_b", "race_c") and (e.get("date") or "") >= today and km >= 15 and km > goal["distance_km"] * 1.5:
            out.append(f"「{e.get('name') or '比賽'}」（{km:g} km，{e['date'][5:].replace('-', '/')}）只登記在固定行程，"
                       f"課表是照「{goal.get('name') or '目前的目標'}」（{goal['distance_km']:g} km）排的，不會為它拉長長跑或倒數減量。"
                       "要為它備賽，請到「目標與課表」把目標改成「準備一場比賽」並填這場的日期、距離。")
    return out


def projected_days(uid: int, upto: date) -> dict[str, dict]:
    """今天 +7 天之後的課：直接拿整季週表排出來（同一套規則）。到那一週時，執行期規則還是會再套一次。"""
    cx = Ctx(uid)
    today = cx.today
    out = {}
    ov = {r["date"]: r for r in db.q("SELECT date, kind, minutes, km FROM plan_overrides WHERE user_id=? AND date>=?",
                                      (uid, today.isoformat()))}
    imported = plan_import.load(uid, today.isoformat(), upto.isoformat()) if plan_import.active(cx.prefs) else {}
    d = today + timedelta(days=7)
    while d <= upto:
        w = cx.week(cx.week_idx(d))
        z = cx.zones(w["phase"] if w else "base")["zones"]
        ev = planner.event_day(d.isoformat(), cx.prefs, cx.goal)
        if not w and not ev and d.isoformat() not in imported and d.isoformat() not in ov:
            d += timedelta(days=1)          # 超過這個週期：還沒排，不要顯示成「休」
            continue
        x = imported.get(d.isoformat()) or ev or cx.day(d) or build_workout("rest", zones=z)
        if d.isoformat() in ov:
            o = ov[d.isoformat()]
            e_mid = (z["E"]["min"] + z["E"]["max"]) / 2
            km = o.get("km") or (o.get("minutes") and round(o["minutes"] * 60 / e_mid, 1)) or 8
            x = build_workout("rest", zones=z) if o["kind"] == "rest" else build_to_km(o["kind"], float(km), zones=z, goal=cx.goal)
            x["override"] = True
        out[d.isoformat()] = {**x, "date": d.isoformat(), "projected": d.isoformat() not in imported, "phase_label": (w or {}).get("phase_label"),
                              "week_tag": (w or {}).get("tag"),
                              "weekday": f"週{planner.WEEKDAY_ZH[d.weekday()]}",
                              "time": cx.prefs.get("run_time", "06:30")}
        d += timedelta(days=1)
    return out


def _month_totals(acts, cross) -> list[dict]:
    """這個月各運動的合計：有距離的顯示公里，沒距離的（重訓、瑜珈）顯示時間。"""
    out = []
    if acts:
        out.append({"group": "run", "label": "跑步", "count": len(acts),
                    "km": round(sum((a.get("distance_m") or 0) for a in acts) / 1000, 1),
                    "minutes": round(sum((a.get("duration_s") or 0) for a in acts) / 60)})
    by: dict[str, dict] = {}
    for c in cross:
        k = c.get("sport_label") or "其他運動"
        t = by.setdefault(k, {"group": c.get("sport_group") or "other", "label": k, "count": 0, "km": 0.0, "minutes": 0})
        t["count"] += 1
        t["km"] += (c.get("distance_m") or 0) / 1000
        t["minutes"] += round((c.get("duration_s") or 0) / 60)
    for t in by.values():
        t["km"] = round(t["km"], 1) or None
        out.append(t)
    return out


def month(user_id: int, ym: str) -> dict:
    y, m = (int(x) for x in ym.split("-"))
    first = date(y, m, 1)
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    acts = db.q("SELECT activity_id, date, distance_m, duration_s, name, avg_hr FROM activities WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date",
                (user_id, first.isoformat(), last.isoformat()))
    plans = {r["date"]: json.loads(r["data"]) for r in db.q(
        "SELECT date, data FROM plan WHERE user_id=? AND date BETWEEN ? AND ?", (user_id, first.isoformat(), last.isoformat()))}
    prefs = db.get_prefs(user_id)
    td = today_local()
    goal = planner.goal_from_prefs(prefs, td)
    today = td.isoformat()
    if last > td + timedelta(days=6) and last <= td + timedelta(days=200):
        for ds, d in projected_days(user_id, last).items():
            if first.isoformat() <= ds:
                plans[ds] = d
    cross = db.q("SELECT activity_id, date, sport_label, sport_group, name, duration_s, distance_m FROM cross_activities "
                 "WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY start_local", (user_id, first.isoformat(), last.isoformat()))
    keys = ("kind", "title", "minutes", "distance_km", "intensity", "summary", "time", "override", "projected", "why", "steps",
            "hr_cap", "pace_range")
    days = []
    d = first
    while d <= last:
        ds = d.isoformat()
        da = [a for a in acts if a["date"] == ds]
        p = plans.get(ds)
        show = p and p["kind"] != "done"
        ev = planner.event_day(ds, prefs, goal)
        days.append({
            "date": ds, "weekday": f"週{planner.WEEKDAY_ZH[d.weekday()]}",
            "actual_km": round(sum((a.get("distance_m") or 0) for a in da) / 1000, 1) if da else None,
            "actual_min": round(sum((a.get("duration_s") or 0) for a in da) / 60) if da else None,
            "activity_id": da[0]["activity_id"] if da else None,
            "runs": [{"id": a["activity_id"], "km": round((a.get("distance_m") or 0) / 1000, 1),
                      "minutes": round((a.get("duration_s") or 0) / 60)} for a in da],
            "others": [{"id": c["activity_id"], "label": c.get("sport_label") or "其他運動", "group": c.get("sport_group") or "other",
                        "name": c.get("name"), "minutes": round((c.get("duration_s") or 0) / 60),
                        "km": round((c.get("distance_m") or 0) / 1000, 1) or None} for c in cross if c["date"] == ds],
            "plan": {k: p.get(k) for k in keys} if show else None,
            "past": ds < today, "is_today": ds == today,
            "editable": today <= ds <= (td + timedelta(days=60)).isoformat(),
            "swappable": today <= ds <= (td + timedelta(days=6)).isoformat(),
            "event": bool(ev), "race": ev and {"name": ev["title"], "summary": ev["summary"], "kind": ev["kind"]},
        })
        d += timedelta(days=1)
    total = round(sum((a.get("distance_m") or 0) for a in acts) / 1000, 1)
    fut = [x for x in days if x["plan"] and not x["past"] and x["plan"]["kind"] not in ("rest", "race", "event")]
    unplanned = any(not x["past"] and not x["plan"] and not x["event"] and x["date"] > (td + timedelta(days=7)).isoformat() for x in days)
    notices = goal_event_warnings(prefs, goal)
    # 減量週：月曆上看起來「怎麼突然變少」，直接說明
    try:
        cx_m = Ctx(user_id)
        dl = []
        for w in cx_m.season["weeks"]:
            ws = date.fromisoformat(w["week_start"])
            if w.get("tag") == "deload" and first - timedelta(days=6) <= ws <= last and ws + timedelta(days=6) >= td:
                dl.append(ws.strftime("%m/%d").lstrip("0").replace("/0", "/"))
        if dl:
            every = 3 if cx_m.season.get("deload_every") == 3 else 4
            notices.append(f"{'、'.join(dl)} 那週是減量週：跑量少三成、長跑縮短，強度課保留但量減少。"
                           f"每 {every} 週一次，讓身體吸收前幾週的訓練，下一週再往上加。")
    except Exception:
        pass
    snap = load_snap(user_id) or {}
    feas = snap.get("feasibility") or {}
    if goal.get("race_date") and feas.get("verdict") == "infeasible" and not snap.get("decision"):
        cx = Ctx(user_id)
        pv = cx.provisional_s and engine.fmt_time(cx.provisional_s)
        notices.insert(0, f"時間不夠：照目前的跑量，目標 {prefs['goal'].get('target_time') or ''} 需要大約 {feas.get('w_need')} 週，"
                          f"離比賽只有 {feas.get('weeks_available')} 週。課表先照做得到的成績{f'（{pv}）' if pv else ''}排。"
                          "要延後賽期或照原目標練，到「整季規劃」選。")
    if unplanned:
        notices.append("空白的日子還沒排：沒有設定目標賽時，課表以 12 週為一個週期，下個週期開始時會依新資料重新排。"
                       if not goal.get("race_date") else "空白的日子在目標賽之後，比賽結束再重新設定目標。")
    return {"month": ym, "days": days, "total_km": total, "runs": len(acts), "notices": notices,
            "others": len(cross), "other_minutes": round(sum((c.get("duration_s") or 0) for c in cross) / 60),
            "totals": _month_totals(acts, cross),
            "planned_runs": len(fut), "planned_km": round(sum(x["plan"]["distance_km"] or 0 for x in fut), 1),
            "mode": prefs.get("plan_mode", "auto")}


# ───────────────────────────── 使用者的選擇 ─────────────────────────────

def decide(uid: int, choice: str, date_: str | None = None) -> dict:
    """可行性檢查不通過時，讓使用者選：採用可達成成績／延後賽期／接受較高受傷風險照原目標。"""
    cx = Ctx(uid)
    feas = cx.season["feasibility"] or {}
    prefs = cx.prefs
    if choice == "achievable" and feas.get("achievable_s"):
        prefs["goal"]["target_time"] = engine.fmt_time(feas["achievable_s"])
    elif choice == "postpone":
        nd = date_ or feas.get("suggested_date")
        if not nd:
            raise ValueError("需要新的比賽日期")
        prefs["goal"]["event_date"] = nd
    elif choice != "accept_risk":
        raise ValueError(choice)
    db.save_prefs(uid, prefs)
    goal = planner.goal_from_prefs(prefs, cx.today)
    snap = load_snap(uid)
    _, soft = _keys(prefs, goal)
    snap["key_soft"] = soft
    refresh_feasibility(snap, prefs, goal)
    snap["decision"] = "accept_risk" if choice == "accept_risk" else None
    save_snap(uid, snap)
    return snap["feasibility"] or {}


def accept_checkpoint(uid: int, cp_date: str) -> dict:
    """接受檢核點建議的目標調整（不重新開季）。"""
    snap = load_snap(uid)
    res = (snap or {}).get("results", {}).get(cp_date)
    if not res:
        raise KeyError(cp_date)
    prefs = db.get_prefs(uid)
    t = planner.parse_hms(prefs["goal"].get("target_time"))
    f = T.GOAL_SLOWDOWN.get(res["action"])
    if t and f:
        prefs["goal"]["target_time"] = engine.fmt_time(t * (1 + f))
        db.save_prefs(uid, prefs)
        goal = planner.goal_from_prefs(prefs, today_local())
        _, soft = _keys(prefs, goal)
        snap["key_soft"] = soft
        refresh_feasibility(snap, prefs, goal)
    res["accepted"] = True
    save_snap(uid, snap)
    return res


def resume_after_pain(uid: int):
    snap = load_snap(uid)
    if snap:
        snap["resume_after"] = today_local().isoformat()
        save_snap(uid, snap)
    with db.tx() as c:
        c.execute("UPDATE checkins SET pain=NULL WHERE user_id=? AND date>=?",
                  (uid, (today_local() - timedelta(days=T.PAIN_DAYS)).isoformat()))
