"""網頁推播（Web Push）：免費、不用 Apple 開發者帳號。

iPhone 需要 iOS 16.4 以上，而且 App 必須從 Safari「加入主畫面」並用 https 網址開啟（Tailscale Funnel 那個網址）。
金鑰（VAPID）第一次使用時自動產生，存在 data/ 裡。
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime

from . import db
from .config import DATA_DIR, TZ

log = logging.getLogger("ic8.push")
KEY_PATH = os.path.join(DATA_DIR, "vapid_private.pem")
SUB = os.environ.get("VAPID_SUB", "mailto:ic8-runner@example.com")


def available() -> bool:
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def _vapid():
    from py_vapid import Vapid01

    if os.path.exists(KEY_PATH):
        return Vapid01.from_file(KEY_PATH)
    v = Vapid01()
    v.generate_keys()
    v.save_key(KEY_PATH)
    return v


def public_key() -> str | None:
    if not available():
        return None
    from cryptography.hazmat.primitives import serialization

    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def subscribe(user_id: int, sub: dict):
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") or not sub.get("keys"):
        raise ValueError("訂閱資料不正確")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO push_subs(user_id, endpoint, data, created_at) VALUES(?,?,?,?)",
                  (user_id, sub["endpoint"], json.dumps(sub), datetime.now(TZ).isoformat(timespec="seconds")))


def unsubscribe(user_id: int, endpoint: str | None = None):
    with db.tx() as c:
        if endpoint:
            c.execute("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", (user_id, endpoint))
        else:
            c.execute("DELETE FROM push_subs WHERE user_id=?", (user_id,))


def count(user_id: int) -> int:
    return db.q1("SELECT COUNT(*) n FROM push_subs WHERE user_id=?", (user_id,))["n"]


def send(user_id: int, title: str, body: str, url: str = "/") -> int:
    """推給這個人所有訂閱的裝置，回傳成功幾台。失效的訂閱自動刪掉。"""
    subs = db.q("SELECT endpoint, data FROM push_subs WHERE user_id=?", (user_id,))
    if not subs or not available():
        return 0
    from pywebpush import WebPushException, webpush

    _vapid()  # 確保金鑰存在
    ok = 0
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    for s in subs:
        try:
            webpush(json.loads(s["data"]), payload, vapid_private_key=KEY_PATH, vapid_claims={"sub": SUB}, ttl=4 * 3600)
            ok += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            log.warning("推播失敗 %s: %s", code, e)
            if code in (404, 410):
                unsubscribe(user_id, s["endpoint"])
    return ok
