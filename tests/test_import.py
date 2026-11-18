"""匯入 Word 逐日課表：解析各種寫法、照表排課、API。"""
import io
import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""
os.environ["INVITE_CODE"] = "friends"

import docx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import plan_import as P  # noqa: E402
from app.garmin_client import today_local  # noqa: E402

CELLS = ["5 km E", "休", "肌力", "6 km 含節奏 2×8 分", "4 km E + 快步×4", "3 km E 鬆腿", "16 km E 長跑",
         "4 km E（心率 140 以下）", "7 km 含間歇 5×800 m", "10 km 含半馬配速 4×2 km", "24 km，末 5 km M",
         "5 km 測驗（暖身2+全力5+緩和2）", "Garmin 半馬（控速 1:57–2:02）", "跳繩 20 分鐘"]


def make_docx(start: date) -> bytes:
    d = docx.Document()
    d.add_paragraph("測試課表")
    d.add_paragraph(f"起算日 {start.year}/{start.month}/{start.day}")
    mid = start + timedelta(days=7)
    end = start + timedelta(days=60)
    t = d.add_table(rows=4, cols=5)
    head = ["強度", "用途", f"階段一 {start.month}/{start.day}–{mid.month}/{mid.day}",
            f"階段二 {(mid + timedelta(days=1)).month}/{(mid + timedelta(days=1)).day}–{end.month}/{end.day}", "心率上限"]
    rows = [["E 輕鬆跑", "", "6:40–7:10", "6:20–6:50", "150"], ["T 節奏跑", "", "5:30–5:40", "5:05–5:15", "180"],
            ["I 間歇", "", "—", "4:40–4:50", "188"]]
    for i, h in enumerate(head):
        t.rows[0].cells[i].text = h
    for r, row in enumerate(rows, 1):
        for i, v in enumerate(row):
            t.rows[r].cells[i].text = v
    monday = start - timedelta(days=start.weekday())
    cal = d.add_table(rows=3, cols=8)
    for i, h in enumerate(["週", "一", "二", "三", "四", "五", "六", "日"]):
        cal.rows[0].cells[i].text = h
    k = 0
    for w in range(2):
        cal.rows[w + 1].cells[0].text = f"W{w}"
        for i in range(7):
            day = monday + timedelta(days=w * 7 + i)
            cal.rows[w + 1].cells[i + 1].text = f"{day.month}/{day.day} {CELLS[k]}"
            k += 1
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_parse_cells():
    start = date(2026, 11, 16)
    paras, tables = P.read_docx(make_docx(start))
    plan = P.parse_plan(paras, tables, start)
    days = plan["days"]
    kinds = [days[(start + timedelta(days=i)).isoformat()]["kind"] for i in range(14)]
    assert kinds == ["easy", "rest", "strength", "tempo", "easy", "easy", "long", "easy", "intervals", "race_pace",
                     "long_m", "test", "race", "custom"]
    assert plan["unknown"] == [f"11/29 跳繩 20 分鐘"]
    tempo = days["2026-11-19"]
    assert tempo["steps"][1]["type"] == "repeat" and tempo["steps"][1]["n"] == 2
    assert tempo["steps"][1]["steps"][0]["pace_hi"] == 330 and abs(tempo["distance_km"] - 6) < 0.6
    assert days["2026-11-23"]["steps"][0]["hr_cap"] == 140          # 「心率 140 以下」
    assert days["2026-11-23"]["steps"][0]["pace_hi"] == 400         # 11/23 還在第一階段
    assert days["2026-11-24"]["steps"][1]["steps"][0]["pace_hi"] == 280   # 第二階段的 I 配速
    assert days["2026-11-20"]["title"] == "輕鬆跑＋快步"


def test_year_rollover():
    start = date(2026, 11, 5)
    assert P._md("1/31 10 km 測驗", start) == date(2027, 1, 31)
    assert P._md("12/26 x", start) == date(2026, 12, 26)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_import_flow(client):
    r = client.post("/api/register", json={"username": "importer", "password": "secret12", "invite": "friends"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post("/api/garmin/demo", headers=h)
    client.post("/api/sync", headers=h)
    today = today_local()
    assert client.post("/api/import-plan", headers=h, content=b"not a docx").status_code == 400
    r = client.post("/api/import-plan", headers={**h, "Content-Type": "application/octet-stream"}, content=make_docx(today))
    assert r.status_code == 200, r.text
    assert r.json()["n_days"] == 14
    prefs = client.get("/api/prefs", headers=h).json()
    assert prefs["plan_mode"] == "imported" and prefs["imported"]["n_days"] == 14
    plan = {d["date"]: d for d in client.get("/api/dashboard", headers=h).json()["plan"]["days"]}
    monday = today - timedelta(days=today.weekday())
    for i in range(7, 14):
        ds = (monday + timedelta(days=i)).isoformat()
        if ds in plan and ds != today.isoformat():
            exp = CELLS[i]
            assert plan[ds]["source_text"] == exp.split("（")[0].strip() or plan[ds]["source_text"] == exp
    assert client.get("/api/week", headers=h).status_code == 200
    ym = (monday + timedelta(days=13)).strftime("%Y-%m")
    assert client.get(f"/api/month?ym={ym}", headers=h).status_code == 200
    assert client.get("/api/season", headers=h).status_code == 200
    me = client.get("/api/me", headers=h).json()
    assert "BEGIN:VEVENT" in client.get(me["calendar_url"].split("testserver")[1]).text
    # 儲存設定時保留匯入模式
    body = {k: prefs[k] for k in ("days", "long_run_day", "run_time", "goal", "events")}
    assert client.put("/api/prefs", headers=h, json={**body, "plan_mode": "imported"}).status_code == 200
    assert client.get("/api/prefs", headers=h).json()["plan_mode"] == "imported"
    assert client.delete("/api/import-plan", headers=h).status_code == 200
    assert client.get("/api/prefs", headers=h).json()["plan_mode"] == "auto"
