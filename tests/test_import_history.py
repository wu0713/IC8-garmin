"""匯入舊紀錄（Strava 封存檔、.fit／.gpx／.tcx）與新手問卷。"""
import gzip
import io
import os
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["SYNC_HOURS"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import athlete, db, file_import  # noqa: E402
from app.garmin_client import today_local  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def strava_csv(days_back=(10, 13, 17, 20, 24, 27, 31, 34, 38, 41)):
    head = ("Activity ID,Activity Date,Activity Name,Activity Type,Activity Description,Elapsed Time,Distance,Max Heart Rate,"
            "Relative Effort,Commute,Activity Private Note,Activity Gear,Filename,Athlete Weight,Bike Weight,Elapsed Time,"
            "Moving Time,Distance,Max Speed,Average Speed,Elevation Gain,Elevation Loss,Elevation Low,Elevation High,Max Grade,"
            "Average Grade,Average Positive Grade,Average Negative Grade,Max Cadence,Average Cadence,Max Heart Rate,Average Heart Rate")
    lines = [head]
    for i, b in enumerate(days_back):
        d = today_local() - timedelta(days=b)
        ds = d.strftime("%b %d, %Y, 10:15:00 PM").replace(" 0", " ")
        lines.append(f'{1000 + i},"{ds}",Morning Run,Run,,3100,"10.02",175,,false,,,activities/{1000 + i}.fit.gz,,,3100.0,'
                     f'3000.0,10020.0,4.5,3.34,50,50,10,20,5,0,1,1,95,84.0,175.0,148.0')
    lines.append(f'2000,"{(today_local() - timedelta(days=12)).strftime("%b %d, %Y, 9:00:00 AM").replace(" 0", " ")}",Ride,Ride,,'
                 f'5400,"40.1",160,,false,,,,,,5400.0,5300.0,40100.0,12,7.5,300,300,0,0,0,0,0,0,0,0,160.0,130.0')
    return "\n".join(lines)


GPX = """<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"
 xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><name>River run</name><type>running</type><trkseg>
<trkpt lat="25.0000" lon="121.5000"><time>2026-08-01T22:00:00Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>140</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
<trkpt lat="25.0200" lon="121.5000"><time>2026-08-01T22:12:00Z</time><extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>150</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
</trkseg></trk></gpx>"""

TCX = """<?xml version="1.0"?><TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
<Activities><Activity Sport="Running"><Id>2026-07-01T22:00:00Z</Id>
<Lap StartTime="2026-07-01T22:00:00Z"><TotalTimeSeconds>1500</TotalTimeSeconds><DistanceMeters>5000</DistanceMeters>
<AverageHeartRateBpm><Value>150</Value></AverageHeartRateBpm><MaximumHeartRateBpm><Value>165</Value></MaximumHeartRateBpm></Lap>
<Lap StartTime="2026-07-01T22:25:00Z"><TotalTimeSeconds>1500</TotalTimeSeconds><DistanceMeters>5000</DistanceMeters>
<AverageHeartRateBpm><Value>160</Value></AverageHeartRateBpm><MaximumHeartRateBpm><Value>175</Value></MaximumHeartRateBpm></Lap>
</Activity></Activities></TrainingCenterDatabase>"""


def test_parse_strava_csv():
    acts = file_import.parse_strava_csv(strava_csv())
    runs = [a for a in acts if not a.get("cross")]
    assert len(runs) == 10 and runs[0]["distance_m"] == 10020 and runs[0]["duration_s"] == 3000
    assert runs[0]["avg_hr"] == 148 and runs[0]["avg_cadence"] == 168          # 單腳步頻 × 2
    assert runs[0]["start_local"].endswith("06:15:00")                         # UTC 22:15 → 台北隔天 06:15
    ride = [a for a in acts if a.get("cross")][0]
    assert ride["sport"] == "cycling" and ride["distance_m"] == 40100


def test_parse_gpx_tcx_fit():
    g = file_import.parse_gpx(GPX.encode(), "g")[0]
    assert 2100 < g["distance_m"] < 2300 and g["duration_s"] == 720 and g["avg_hr"] == 145 and g["date"] == "2026-08-02"
    t = file_import.parse_tcx(TCX.encode(), "t")[0]
    assert t["distance_m"] == 10000 and t["duration_s"] == 3000 and t["avg_hr"] == 155 and t["max_hr"] == 175
    f = file_import.parse_fit((FIX / "run.fit").read_bytes(), "f")[0]
    assert f["distance_m"] == 10000 and f["avg_cadence"] == 170 and f["type"] == "running"


def test_parse_upload_zip_without_csv(tmp_path):
    p = tmp_path / "export.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("activities/1.gpx.gz", gzip.compress(GPX.encode()))
        z.writestr("activities/2.tcx", TCX)
        z.writestr("activities/3.fit", (FIX / "run.fit").read_bytes())
        z.writestr("activities/4.fit", b"garbage")
        z.writestr("media/photo.jpg", b"x")
    acts, bad = file_import.parse_upload("export.zip", str(p))
    assert len(acts) == 3 and bad == 1


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/register", json={"username": "histimp", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        uid = db.q1("SELECT id FROM users WHERE username=?", ("histimp",))["id"]
        yield c, h, uid


def test_import_strava_zip_dedupes_and_rediagnoses(client):
    c, h, uid = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export_123/activities.csv", strava_csv())
    hdr = {**h, "Content-Type": "application/octet-stream", "X-Filename": "export_123.zip"}
    r = c.post("/api/import-history", headers=hdr, content=buf.getvalue()).json()
    assert r["runs"] == 10 and r["others"] == 1 and r["duplicates"] == 0 and r["run_km"] > 100
    r2 = c.post("/api/import-history", headers=hdr, content=buf.getvalue()).json()      # 同一個檔再傳一次：覆寫，不會多出來
    assert db.q1("SELECT COUNT(*) AS n FROM activities WHERE user_id=?", (uid,))["n"] == 10 and r2["runs"] == 10
    ath = athlete.build(uid, today_local())
    assert ath["history_weeks"] >= 5 and sum(ath["weekly_km"][:5]) > 50


def test_import_rejects_garbage(client):
    c, h, uid = client
    r = c.post("/api/import-history", headers={**h, "X-Filename": "notes.txt"}, content=b"hello")
    assert r.status_code == 400


def test_duplicate_of_garmin_activity_skipped(client):
    c, h, uid = client
    a = file_import.parse_tcx(TCX.encode(), "dupe")[0]
    db.q("SELECT 1")
    from app import sync
    sync.store(uid, [{**a, "activity_id": "garmin-1", "start_local": a["start_local"][:-2] + "40"}], [])
    keep, dup = file_import.dedupe(uid, [a])
    assert not keep and dup == 1


def test_self_report_used_when_history_short(client):
    c, h, uid = client
    c.post("/api/register", json={"username": "histnew", "password": "secret12", "invite": os.environ.get("INVITE_CODE")})
    tok = c.post("/api/login", json={"username": "histnew", "password": "secret12"}).json()["token"]
    hh = {"Authorization": f"Bearer {tok}"}
    nid = db.q1("SELECT id FROM users WHERE username=?", ("histnew",))["id"]
    assert c.put("/api/prefs", headers=hh, json={"self_report": {"weekly_km": 35, "longest_km": 16, "hard_per_week": 2, "years": "1to3",
                                                                   "race_km": 10, "race_time": "45:00"}}).status_code == 200
    ath = athlete.build(nid, today_local())
    assert ath["self_report"] and ath["weekly_km"][0] == 35 and not ath["novice"]
    assert ath["best_effort"]["source"] == "self_report" and ath["recent_longest_km"] == 16
    bad = c.put("/api/prefs", headers=hh, json={"self_report": {"weekly_km": 20, "race_km": 10, "race_time": "5:00"}})
    assert bad.status_code == 400


def test_parse_strava_csv_chinese_headers_and_dates():
    head = "活動 ID,活動日期,活動名稱,活動類型,活動說明,經過時間,距離,最大心率,相對費力指數,通勤,私人筆記,裝備,檔案名稱,運動員體重,自行車重量,經過時間,移動時間,距離,最大速度,平均速度,平均心率"
    rows = [head,
            '111,"2026年11月3日 下午10:15:45",晨跑,跑步,,3100,"10.02",175,,false,,,,,,3100,3000,10020.0,4.5,3.34,148.0',
            '112,"2026/11/5 上午6:01:00",通勤,騎乘,,3600,"20.1",150,,true,,,,,,3600,3500,20100.0,9,5.7,120.0']
    acts = file_import.parse_strava_csv("\n".join(rows))
    run = next(a for a in acts if not a.get("cross"))
    assert run["distance_m"] == 10020 and run["duration_s"] == 3000 and run["avg_hr"] == 148
    assert run["start_local"] == "2026-11-04 06:15:45"                  # UTC 22:15 → 台北隔天 06:15
    ride = next(a for a in acts if a.get("cross"))
    assert ride["sport"] == "cycling"


def test_parse_strava_csv_unknown_headers_fall_back_to_positions():
    head = "A,B,C,D,E,F,G,H,I,J"
    row = '9,"Nov 3, 2026, 10:15:45 PM",Run,Run,,3100,"10.02",175,,false'
    a = file_import.parse_strava_csv(head + "\n" + row)[0]
    assert a["distance_m"] == 10020 and a["duration_s"] == 3100


def test_zip_bomb_member_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(file_import, "MAX_MEMBER_BYTES", 1000)
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("activities/big.gpx", "x" * 5000)
        z.writestr("activities/ok.tcx", TCX[:900] if len(TCX) < 1000 else TCX)
    acts, bad = file_import.parse_upload("bomb.zip", str(p))
    assert bad >= 1
