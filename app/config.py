"""集中管理設定，全部可用環境變數覆寫（見 .env.example）。"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "ic8.sqlite3"
WEB_DIR = BASE_DIR / "web"

TZ_NAME = os.getenv("TZ_NAME", "Asia/Taipei")
TZ = ZoneInfo(TZ_NAME)

# 註冊時需要的邀請碼：只有拿到這組碼的朋友才能開帳號。留空 = 不限制（不建議對外開放時留空）。
INVITE_CODE = os.getenv("INVITE_CODE", "")

# 每天自動同步的時間（24h，逗號分隔）
SYNC_HOURS = [int(h) for h in os.getenv("SYNC_HOURS", "5,13,21").split(",") if h.strip()]

# 自動偵測新資料：每隔幾分鐘問一次 Garmin「手錶最後上傳時間」，時間有變才真的同步（0 = 關閉）。
# 只在 POLL_HOURS 這段時間內偵測（例如 5-23 = 早上 5 點到晚上 11 點前）。
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
_ph = os.getenv("POLL_HOURS", "5-23").split("-")
POLL_HOURS = (int(_ph[0]), int(_ph[1])) if len(_ph) == 2 and all(x.strip().isdigit() for x in _ph) else (5, 23)

# 對外網址（用來產生行事曆訂閱連結），例如 https://my-pc.tailnet-xxxx.ts.net
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# 允許建立 Demo 帳號（假資料，方便試用）
ALLOW_DEMO = os.getenv("ALLOW_DEMO", "1") == "1"

SESSION_DAYS = 60
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "365"))  # 第一次同步抓多少天（趨勢頁的「年」需要）
PLAN_DAYS = 7           # 排幾天的課


def _load_secret() -> bytes:
    """用來加密 Garmin token 的金鑰。優先讀環境變數，否則自動產生並存在 data/。"""
    from cryptography.fernet import Fernet

    env = os.getenv("SECRET_KEY")
    if env:
        return env.encode()
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_bytes(Fernet.generate_key())
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_bytes().strip()


SECRET_KEY = _load_secret()
