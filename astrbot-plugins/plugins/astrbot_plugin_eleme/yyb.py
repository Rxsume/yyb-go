"""YYB (SuperNaiBA/YYB_GO, original) status client.

The household's YYB service runs on the router
(``http://192.168.31.2:8000``) and holds the logged-in WeChat account
that ``elm_cookie.py`` uses to fetch Ele.me cookies. This client only
READS account status — it never triggers logins or scans. The actual
cookie sync is done by the ``elm_cookie.py`` cron in Qinglong
(see ``app/qinglong.py`` ``run_cron_and_wait`` and the [1]/[2] flow
in ``app/bot.py``).

The original YYB answers ``GET /accounts`` with::

    {"code": 0, "msg": "success", "data": [{"id": 1, "status": "alive", ...}]}

``ref`` "1" resolves to the first account (numeric id 1), matching
how ``elm_cookie.py`` reads ``YYB_SERVER`` (``host@ref``).
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class YybError(Exception):
    """YYB unreachable or returned something unexpected."""


class YybClient:
    def __init__(self, base_url: str, ref: str = "1", timeout: int = 10):
        self.base_url = (base_url or "").rstrip("/")
        self.ref = (ref or "1").strip() or "1"
        self.timeout = timeout

    # -- raw ------------------------------------------------------------
    def list_accounts(self) -> list[dict]:
        """GET /accounts. Raises YybError if YYB cannot be reached."""
        if not self.base_url:
            raise YybError("YYB 地址未配置（YYB_BASE_URL 为空）")
        try:
            resp = requests.get(f"{self.base_url}/accounts", timeout=self.timeout)
        except requests.RequestException as exc:
            raise YybError(f"连不上 YYB（{self.base_url}）：{exc}") from exc
        if resp.status_code != 200:
            raise YybError(f"YYB 返回 HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise YybError("YYB 返回异常（不是 JSON）") from exc
        data = body.get("data") if isinstance(body, dict) else body
        if isinstance(data, dict):
            for key in ("accounts", "list", "data"):
                if isinstance(data.get(key), list):
                    return [a for a in data[key] if isinstance(a, dict)]
            # A single account object.
            return [data] if data.get("id") is not None else []
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
        return []

    # -- one account ------------------------------------------------------
    def account_status(self, ref: str | None = None) -> tuple[bool, str]:
        """(alive, detail) for one YYB account.

        Raises YybError only when YYB itself cannot be reached. A
        missing or dropped account is (False, detail), not an error.
        """
        want = (ref or self.ref or "1").strip()
        accounts = self.list_accounts()
        if not accounts:
            return False, "YYB 里没有任何微信账号，请先扫码添加"
        target = None
        for acc in accounts:
            if str(acc.get("id")) == want or str(acc.get("ref")) == want:
                target = acc
                break
        if target is None and len(accounts) == 1:
            # The household runs a single YYB account; don't fail on a
            # ref/id mismatch when there is only one to pick.
            target = accounts[0]
        if target is None:
            return False, f"YYB 里没有账号 {want}（当前共 {len(accounts)} 个），请先扫码添加"
        status = str(target.get("status") or target.get("state") or "").strip().lower()
        if status == "alive":
            return True, f"账号 {want} 在线（alive）"
        return False, f"账号 {want} 状态：{status or '未知'}，需要重新扫码"
