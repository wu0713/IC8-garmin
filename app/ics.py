"""產生 iCalendar (.ics) 訂閱檔。

iPhone「設定 → 行事曆 → 帳號 → 加入帳號 → 其他 → 加入已訂閱的行事曆」貼上網址即可，
之後課表一變動，手機行事曆會自動跟著更新（iOS 預設約每小時抓一次，可在設定改）。
每個事件的 UID 固定為「使用者 + 日期」，所以重排課表時是「更新」而不是新增重複事件。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from icalendar import Alarm, Calendar, Event

from .config import TZ, TZ_NAME
from .planner import describe_steps


def build_ics(user: dict, days: list[dict], prefs: dict, base_url: str = "") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Garmin-IC8 Run Coach//TW")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", f"跑步課表 · {user.get('display_name') or user['username']}")
    cal.add("x-wr-timezone", TZ_NAME)
    cal.add("refresh-interval;value=duration", "PT1H")
    cal.add("x-published-ttl", "PT1H")

    alarm_min = int(prefs.get("calendar_alarm_min") or 0)
    for d in days:
        if d["kind"] in ("rest",):
            continue
        ev = Event()
        ev.add("uid", f"ic8-{user['id']}-{d['date']}@garmin-ic8")
        ev.add("dtstamp", datetime.now(TZ))
        icon = {"easy": "🟢", "recovery": "🟢", "long": "🔵", "done": "", "race": "", "event": "", "strength": "💪", "test": "", "custom": "🟠"}.get(d["kind"], "🔴")
        dist = f" {d['distance_km']:g} km" if d.get("distance_km") and d["kind"] not in ("done", "race", "event") else ""
        ev.add("summary", f"{icon} {d['title']}{dist}".strip())

        if d["kind"] in ("done", "race", "event"):
            ev.add("dtstart", datetime.fromisoformat(d["date"]).date())
            ev.add("dtend", datetime.fromisoformat(d["date"]).date() + timedelta(days=1))
        else:
            hh, mm = (d.get("time") or "06:30").split(":")
            start = datetime.fromisoformat(d["date"]).replace(hour=int(hh), minute=int(mm), tzinfo=TZ)
            ev.add("dtstart", start)
            ev.add("dtend", start + timedelta(minutes=max(15, d.get("minutes") or 30) + 10))
            if alarm_min:
                al = Alarm()
                al.add("action", "DISPLAY")
                al.add("description", d["title"])
                al.add("trigger", timedelta(minutes=-alarm_min))
                ev.add_component(al)

        desc = [d.get("summary", "")] + ([f"為什麼：{d['why']}"] if d.get("why") else [])
        if d.get("steps"):
            desc += ["", *describe_steps(d["steps"])]
        if base_url:
            desc += ["", f"打開 App：{base_url}/"]
        ev.add("description", "\n".join(desc))
        cal.add_component(ev)
    return cal.to_ical()
