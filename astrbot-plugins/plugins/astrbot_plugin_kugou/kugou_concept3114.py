"""Kugou Concept login for the Telegram bot: QR-code login, appid 3114.

Pure-HTTP MakcRe-style QR login for the REAL Kugou Concept app
(appid 3114, clientver 12520). This REPLACES the kgmusicbot path
(app/kugou_concept.py, appid 3116): 3116 is Kugou Lite, not Concept,
and its tokens are rejected by the Concept tasks (task 200 user/info
errcode=11002, proven 2026-11-22 00:37 and 00:47). The Qinglong
daily script (kgylgnb45w.py, the old 44851-byte copy task 200 runs)
calls user/info with appid=3114 — only a 3114 token passes. A
MakcRe-style 3114 QR login was proven live on 2026-11-22 00:53:
userid 645412819, task 200 passed (check-in OK, coins earned).

No device registration, no Node.js, no vendored client: the two
endpoints are plain HTTPS GETs with a web signature (salt
``NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt``). The QR PNG is generated
locally with the Python ``qrcode`` library.

State: login state (qr_key only) lives IN MEMORY per QR attempt,
held on the Session / passed to the poll thread. There is no device
to persist and no session file. A token/userid is NEVER written to
disk by the bot — it is returned to the user in their private
Telegram chat only.

KGGNB line: ``remark=<remark>;token=<token>;userid=<userid>`` — NO
mid/dfid. The Qinglong script derives mid/dfid from the token when
missing, and a real device mid/dfid in the line caused 11002.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

KG_QR_TIMEOUT = 300          # seconds to wait for a QR scan
KG_QR_POLL_INTERVAL = 2.0    # seconds between QR status checks
# Consecutive transient network errors (e.g. the server closing the
# connection) before the QR poll gives up. The Kugou login server
# drops connections intermittently (~1 in 3 polls on 2026-11-21), so
# a single blip must not kill the poll thread.
KG_QR_MAX_CONSECUTIVE_ERRORS = 5

# Real Concept app (the one Qinglong task 200's user/info uses).
CONCEPT_APPID = "3114"
CONCEPT_CLIENTVER = "12520"
# QR key request appid (same as MakcRe's login_qr_key).
QR_KEY_APPID = "1001"
SRCAPPID = "2919"
LOGIN_BASE = "https://login-user.kugou.com"
# Web signature salt (MakcRe helper.js signatureWebParams).
_WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
_QR_TEXT = ("https://h5.kugou.com/apps/loginQRCode/html/index.html"
            f"?appid={CONCEPT_APPID}&")
_UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
_HTTP_TIMEOUT = 15

try:
    import qrcode as _qrcode_lib  # noqa: E402
except ImportError:  # noqa: BLE001 - bot must boot without the lib
    _qrcode_lib = None

# Vendored status codes -> bot state names (0 expired, 1 waiting,
# 2/3 scanned, 4 success).
_CONCEPT_STATUS = {
    0: ("expired", "二维码已过期"),
    1: ("waiting", "等待扫码"),
    2: ("scanned", "已扫码，等待确认"),
    3: ("scanned", "已扫码，等待确认"),
    4: ("success", "登录成功"),
}


def web_sign(params: dict) -> str:
    """MakcRe web signature: MD5(salt + sorted ``k=v`` joined + salt).

    GET requests have an empty body, so nothing is appended between
    the params and the trailing salt.
    """
    params_string = "".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(
        f"{_WEB_SALT}{params_string}{_WEB_SALT}".encode()).hexdigest()


def _headers(clienttime: str) -> dict:
    return {
        "User-Agent": _UA,
        "dfid": "-",
        "clienttime": clienttime,
        "mid": "undefined",
        "kg-rc": "1",
        "kg-thash": "5d816a0",
        "kg-rec": "1",
        "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
    }


class KugouLoginError(Exception):
    """A Kugou Concept login step failed (bad response, expired QR)."""


class KugouLoginUnavailable(Exception):
    """The Concept login client is not importable."""


class KugouConceptLogin3114:
    """Concept (3114) QR login, pure HTTP.

    ``urlopen`` is the HTTP opener (default ``urllib.request.urlopen``;
    tests inject a fake). ``has_qrcode`` forces the QR-image
    availability flag (default: whether the ``qrcode`` library
    imported); without it the login still works via the URL, but the
    bot reports the login as unavailable, matching the old flow.
    No device is registered and nothing is persisted.
    """

    def __init__(self, urlopen=None, has_qrcode: bool | None = None):
        self._urlopen = urlopen or urllib.request.urlopen
        self._has_qrcode = (
            _qrcode_lib is not None if has_qrcode is None else has_qrcode)

    @property
    def available(self) -> bool:
        return bool(self._has_qrcode)

    def _require(self) -> None:
        if not self.available:
            raise KugouLoginUnavailable(
                "酷狗概念版登录客户端不在或导不进来："
                "app/kugou_concept3114.py（缺少 qrcode 依赖）")

    def new_state(self) -> dict:
        """Fresh in-memory login state for one QR attempt."""
        return {"qr_key": ""}

    # -- HTTP -------------------------------------------------------------
    def _get_json(self, path: str, params: dict) -> dict:
        """GET one login-user endpoint, return the parsed JSON dict."""
        params = dict(params)
        params["signature"] = web_sign(params)
        url = LOGIN_BASE + path + "?" + urllib.parse.urlencode(params)
        clienttime = params.get("clienttime", "")
        req = urllib.request.Request(
            url, headers=_headers(str(clienttime)))
        with self._urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    # -- QR login ---------------------------------------------------------
    def get_qr_key(self, state: dict) -> tuple[str, str, str]:
        """Generate a Concept (3114) QR. Returns (key, qrcode_img data
        URI, login URL). Network errors become KugouLoginError (the
        caller shows them and asks the user to retry); a missing key
        is a login error too."""
        self._require()
        clienttime = str(int(time.time()))
        params = {
            "dfid": "-",
            "mid": "undefined",
            "uuid": "-",
            "appid": QR_KEY_APPID,
            "clientver": CONCEPT_CLIENTVER,
            "clienttime": clienttime,
            "type": "1",
            "plat": "4",
            "qrcode_txt": _QR_TEXT,
            "srcappid": SRCAPPID,
        }
        try:
            data = self._get_json("/v2/qrcode", params)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise KugouLoginError(f"取二维码失败：{exc}") from exc
        key = ((data.get("data") or {}).get("qrcode") or "").strip()
        if not key:
            raise KugouLoginError("取二维码失败：服务器没给 key")
        url = (f"https://h5.kugou.com/apps/loginQRCode/html/index.html"
               f"?qrcode={key}")
        state["qr_key"] = key
        state["qr_url"] = url
        return key, self._qr_data_uri(url), url

    def check_qr(self, state: dict) -> tuple[str, str, dict]:
        """Poll one QR key. Returns (state, text, data).

        state: "waiting" | "scanned" | "success" | "expired" | "unknown".
        On success, data carries token + userid (validated here).
        Transient network errors propagate raw (the poll thread
        retries them); only login-level failures become
        KugouLoginError.
        """
        self._require()
        key = (state.get("qr_key") or "").strip()
        if not key:
            raise KugouLoginError("当前没有待扫码二维码")
        clienttime = str(int(time.time()))
        params = {
            "dfid": "-",
            "mid": "undefined",
            "uuid": "-",
            "appid": CONCEPT_APPID,
            "clientver": CONCEPT_CLIENTVER,
            "clienttime": clienttime,
            "plat": "4",
            "srcappid": SRCAPPID,
            "qrcode": key,
        }
        data = self._get_json("/v2/get_userinfo_qrcode", params)
        status = (data.get("data") or {}).get("status")
        state_name, text = _CONCEPT_STATUS.get(
            status, ("unknown", f"未知状态 {status}"))
        result: dict = {}
        if state_name == "success":
            token = ((data.get("data") or {}).get("token") or "").strip()
            userid = str((data.get("data") or {}).get("userid") or "").strip()
            if not token or not userid or userid == "0":
                raise KugouLoginError("扫码成功但没拿到 token/userid")
            result = {"token": token, "userid": userid,
                      "nickname": ((data.get("data") or {}).get("nickname")
                                   or "").strip()}
        return state_name, text, result

    def _qr_data_uri(self, url: str) -> str:
        """PNG data URI for a QR URL, or "" when the qrcode lib is
        missing (the caller falls back to sending the URL as text)."""
        if _qrcode_lib is None:
            return ""
        try:
            buf = io.BytesIO()
            _qrcode_lib.make(url).save(buf, format="PNG")
            return ("data:image/png;base64,"
                    + base64.b64encode(buf.getvalue()).decode())
        except Exception:  # noqa: BLE001 - caller falls back to URL
            log.warning("概念版二维码图生成失败，改发链接")
            return ""

    def qr_image_bytes(self, qrcode_img: str) -> bytes | None:
        """PNG bytes from a data URI, else None (the caller falls back
        to sending the login URL as text)."""
        if qrcode_img and qrcode_img.startswith("data:image"):
            try:
                return base64.b64decode(qrcode_img.split(",", 1)[1])
            except Exception:  # noqa: BLE001 - caller falls back to URL
                log.warning("概念版二维码图解析失败，改发链接")
        return None

    # -- KGGNB line ---------------------------------------------------------
    @staticmethod
    def build_kggnb_line(token: str, userid: str, remark: str,
                         device: dict | None = None) -> str:
        """One KGGNB line: ``remark=<remark>;token=<token>;userid=<userid>``.

        NO mid/dfid — ever. The Qinglong script derives them from the
        token when missing, and a real device mid/dfid in the line
        caused user/info 11002 (2026-11-22). The ``device`` argument
        is accepted for interface compatibility with the old wrapper
        and ignored.
        """
        return f"remark={remark};token={token};userid={userid}"
