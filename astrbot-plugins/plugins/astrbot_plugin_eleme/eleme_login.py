"""Ele.me login helpers: SMS-code login + CK (cookie) login.

SMS LOGIN — CAPTURED 2026-11-19, SEE SMS_RESEARCH.md §7
-------------------------------------------------------
The 2019 H5 endpoints are DEAD::

    POST https://h5.ele.me/restapi/eus/login/mobile_send_code   -> HTTP 503
    POST https://h5.ele.me/restapi/eus/login/login_by_mobile

The live SMS login (captured 2026-11-19 13:52 via Quantumult X, H5 page
in Safari, user logged out first) uses Ele.me's own ipassport service::

    POST https://ipassport.ele.me/newlogin/sms/send.do?appName=eleme&fromSite=25&_bx-v=2.5.11
        form: phoneCode=86, loginId=<phone>, countryCode=CN, codeLength=6, ...
        -> {"content": {"data": {"resultCode": 100, "smsToken": "idc_..."}}}
    POST https://ipassport.ele.me/newlogin/sms/login.do?appName=eleme&fromSite=25&_bx-v=2.5.11
        form: loginId, phoneCode=86, countryCode=CN, smsCode=<code>,
              smsToken=<from send>, keepLogin=false, ...
        -> {"content": {"data": {"resultCode": 100, "loginResult": "success",
              "sid": ..., "user_id": ...}}} + Set-Cookie (full session)

Login page: https://ipassport.ele.me/mini_login.htm?lang=zh_cn&appName=eleme
    &appEntrance=eleme_sms_h5&styleType=vertical&bizParams=
    &notLoadSsoView=true&notKeepLogin=false&isMobile=true&rnd=<random>

CAVEAT: the captured requests carry JS-generated device fingerprints
(``bx-ua`` / ``ua``, AWSC) and page-issued tokens (``_csrf_token`` /
``umidToken`` / ``hsiz``). :meth:`ElemeSmsLogin.prepare` GETs the login
page first to collect cookies/tokens; ``bx-ua`` / ``ua`` are sent empty
until the AWSC bridge is available. If the server rejects requests
without them, fall back to browser login (手机号登录, ``browser_login.py``),
which lets the real page generate everything.

DO NOT "fix" this by re-enabling the 2019 URLs — they return 503.
"""

from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)

# The 2019 H5 endpoints, kept here ONLY as a record of what is dead.
DEAD_2019_SEND_URL = "https://h5.ele.me/restapi/eus/login/mobile_send_code"
DEAD_2019_VERIFY_URL = "https://h5.ele.me/restapi/eus/login/login_by_mobile"

# Live SMS login endpoints, captured 2026-11-19 13:52 (Quantumult X, H5 page).
# Query string is part of the endpoint: appName=eleme, fromSite=25, _bx-v=2.5.11.
IPASSPORT_SEND_URL = (
    "https://ipassport.ele.me/newlogin/sms/send.do"
    "?appName=eleme&fromSite=25&_bx-v=2.5.11"
)
IPASSPORT_VERIFY_URL = (
    "https://ipassport.ele.me/newlogin/sms/login.do"
    "?appName=eleme&fromSite=25&_bx-v=2.5.11"
)
IPASSPORT_LOGIN_PAGE = (
    "https://ipassport.ele.me/mini_login.htm"
    "?lang=zh_cn&appName=eleme&appEntrance=eleme_sms_h5"
    "&styleType=vertical&bizParams=&notLoadSsoView=true"
    "&notKeepLogin=false&isMobile=true"
)
# iPhone Safari UA, as captured. The server keys risk checks off it.
IPASSPORT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 "
    "Mobile/15E148 Safari/604.1"
)

# Fields a usable Ele.me session cookie must contain.
REQUIRED_COOKIE_FIELDS = ("_m_h5_tk",)
IDENTITY_COOKIE_FIELDS = ("USERID", "utuser", "userid", "munb", "unb", "SID", "cookie2")

# Used only as a live check for a pasted cookie; failure is reported, not fatal.
# NOTE: www.ele.me/restapi/... now returns the Taobao Flash Sale HTML
# landing page (HTTP 200, text/html) instead of JSON, so it can never
# validate a cookie — every paste got "服务端返回异常". h5.ele.me
# returns real JSON (a user object for a live cookie, `0` for a dead one).
CURRENT_USER_URL = "https://h5.ele.me/restapi/eus/v1/current_user"

PHONE_RE = re.compile(r"^1\d{10}$")
CODE_RE = re.compile(r"^\d{4,6}$")


class SmsLoginUnavailable(Exception):
    """SMS login is not wired up (no working endpoint known)."""


class ElemeLoginError(Exception):
    """A login step failed (bad code, bad cookie, network, ...)."""


def is_phone(text: str) -> bool:
    return bool(PHONE_RE.match((text or "").strip()))


def is_code(text: str) -> bool:
    return bool(CODE_RE.match((text or "").strip()))


def parse_cookie_fields(cookie: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def extract_remark(cookie: str, fallback: str = "饿了么账号") -> str:
    """Best-effort display name for a cookie: USERID/utuser, else fallback."""
    fields = parse_cookie_fields(cookie)
    for key in ("USERID", "utuser", "userid", "unb", "munb"):
        if fields.get(key):
            return f"饿了么-{fields[key][:12]}"
    return fallback


def cookie_has_required_fields(cookie: str) -> tuple[bool, str]:
    fields = parse_cookie_fields(cookie)
    missing = [f for f in REQUIRED_COOKIE_FIELDS if not fields.get(f)]
    if missing:
        return False, f"缺少必要字段: {', '.join(missing)}（需要 _m_h5_tk）"
    return True, "格式OK"


def validate_ele_cookie(cookie: str) -> tuple[bool, str]:
    """Full check for a usable Ele.me session cookie: it must carry
    ``_m_h5_tk`` AND at least one identity field (USERID/SID/...).

    Returns (ok, note); note is "OK" or the missing-field reason.
    Never logs or returns the cookie itself.
    """
    ok, note = cookie_has_required_fields(cookie)
    if not ok:
        return False, note
    fields = parse_cookie_fields(cookie)
    if not any(fields.get(f) for f in IDENTITY_COOKIE_FIELDS):
        return False, "缺少身份字段（USERID/SID 等），不是完整登录态"
    return True, "OK"


def check_cookie_live(cookie: str, timeout: int = 10) -> tuple[bool, str]:
    """GET current_user with the cookie. Returns (ok, human-readable note).

    Never raises; a network/server failure is reported as '无法验证', the
    caller decides whether to accept the cookie anyway.
    """
    try:
        resp = requests.get(
            CURRENT_USER_URL,
            headers={
                "Cookie": cookie.strip(),
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
                ),
                "Referer": "https://h5.ele.me/",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return False, f"联网验证失败（{exc}），只做了格式检查"
    if resp.status_code != 200:
        return False, f"服务端返回 HTTP {resp.status_code}，Cookie 可能已失效"
    try:
        data = resp.json()
    except ValueError:
        # HTTP 200 but not JSON (e.g. an HTML landing/error page): the
        # endpoint is not answering as an API, so this proves nothing
        # about the cookie — report "cannot verify", not "dead".
        return False, "服务端返回非 JSON（无法验证），Cookie 已同步，下一场任务以实际结果为准"
    if isinstance(data, dict) and data.get("user_id"):
        return True, f"验证通过，用户ID {data.get('user_id')}"
    return False, "服务端未认出该登录态，Cookie 可能已失效"


class ElemeSmsLogin:
    """SMS-code login against Ele.me's ipassport service.

    Flow (captured 2026-11-19, see SMS_RESEARCH.md §7):
      1. :meth:`prepare` GETs the login page once per session to collect
         cookies (``_csrf_token`` / ``umidToken`` / ``hsiz`` when the
         server sets them as cookies).
      2. :meth:`send_code` POSTs form-encoded data to ``send.do`` and
         returns the ``smsToken`` from ``content.data``.
      3. :meth:`verify_code` POSTs form-encoded data (``smsCode`` +
         ``smsToken``) to ``login.do`` and returns the full session
         cookie line from the ``Set-Cookie`` headers.

    ``bx-ua`` / ``ua`` are AWSC device fingerprints generated by the
    login page's JS. They are sent empty here until the AWSC bridge is
    available; if the server rejects requests without them, use browser
    browser login (手机号登录) instead (the real page generates them).
    """

    def __init__(self, send_url: str = "", verify_url: str = "",
                 login_page_url: str = "", timeout: int = 10):
        # Default to the captured ipassport endpoints; env vars override.
        self.send_url = (send_url or "").strip() or IPASSPORT_SEND_URL
        self.verify_url = (verify_url or "").strip() or IPASSPORT_VERIFY_URL
        self.login_page_url = (login_page_url or "").strip() or IPASSPORT_LOGIN_PAGE
        self.timeout = timeout
        self._session: requests.Session | None = None
        self._prepared = False
        # Page-issued tokens, filled by prepare() when the server sets them.
        self._csrf_token = ""
        self._umid_token = ""
        self._hsiz = ""

    @property
    def available(self) -> bool:
        return bool(self.send_url and self.verify_url)

    def _require_available(self) -> None:
        if not self.available:
            raise SmsLoginUnavailable("短信登录未配置发短信/验证接口。")

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _headers(self) -> dict[str, str]:
        # Referer must be the mini_login.htm page with appEntrance=eleme_sms_h5.
        import random

        referer = self.login_page_url + f"&rnd={random.random():.16f}"
        headers: dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://ipassport.ele.me",
            "Referer": referer,
            "User-Agent": IPASSPORT_UA,
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "bx-v": "2.5.11",
        }
        return headers

    def _common_fields(self) -> dict[str, str]:
        """Device/page fields sent with both send and verify requests."""
        return {
            "phoneCode": "86",
            "countryCode": "CN",
            "ua": "",  # AWSC fingerprint; empty until the bridge is available.
            "umidGetStatusVal": "4",
            "screenPixel": "414x896",
            "navlanguage": "zh-CN",
            "navUserAgent": IPASSPORT_UA,
            "navPlatform": "iPhone",
            "appName": "eleme",
            "appEntrance": "eleme_sms_h5",
            "_csrf_token": self._csrf_token,
            "umidToken": self._umid_token,
            "isMobile": "true",
            "lang": "zh_CN",
            "returnUrl": "",
            "hsiz": self._hsiz,
            "fromSite": "25",
            "bizParams": "",
            "umidTag": "SERVER",
            "weiBoMpBridge": "",
            "deviceId": "",
            "bx-ua": "",  # AWSC fingerprint; empty until the bridge is available.
        }

    def prepare(self) -> None:
        """GET the login page once to collect session cookies/tokens.

        Best-effort: a failure here does not abort the flow, the send
        step may still work; but without the page's cookies the server
        is more likely to flag the request.
        """
        if self._prepared:
            return
        self._prepared = True
        try:
            import random

            url = self.login_page_url + f"&rnd={random.random():.16f}"
            resp = self.session.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "User-Agent": IPASSPORT_UA,
                    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
                },
                timeout=self.timeout,
            )
            # Pick up page-issued tokens from the cookies, when present.
            jar = self.session.cookies
            for name in ("_csrf_token", "csrf_token", "XSRF-TOKEN"):
                if jar.get(name):
                    self._csrf_token = jar.get(name) or ""
                    break
            for name in ("umidToken", "umid", "UMID"):
                if jar.get(name):
                    self._umid_token = jar.get(name) or ""
                    break
            for name in ("hsiz", "SID", "sid"):
                if jar.get(name):
                    self._hsiz = jar.get(name) or ""
                    break
            # _csrf_token is sometimes embedded in the page, not a cookie.
            if not self._csrf_token and resp.text:
                m = re.search(r'"_csrf_token"\s*:\s*"([^"]+)"', resp.text)
                if m:
                    self._csrf_token = m.group(1)
                else:
                    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', resp.text)
                    if m:
                        self._csrf_token = m.group(1)
            log.info("SMS login prepare: page=%s csrf=%s umid=%s",
                     resp.status_code, bool(self._csrf_token), bool(self._umid_token))
        except requests.RequestException as exc:
            log.warning("SMS login prepare failed (continuing anyway): %s", exc)

    def _check_result(self, data: dict, what: str) -> dict:
        """Unwrap {"content": {"data": {...}, "status": 0, "success": true}}.

        Raises ElemeLoginError unless resultCode == 100.
        """
        content = data.get("content") if isinstance(data, dict) else None
        inner = content.get("data") if isinstance(content, dict) else None
        if not isinstance(inner, dict):
            raise ElemeLoginError(f"{what}失败：服务端返回异常：{str(data)[:200]}")
        result_code = inner.get("resultCode")
        if result_code != 100:
            raise ElemeLoginError(
                f"{what}失败：resultCode={result_code}，返回：{str(inner)[:200]}"
            )
        return inner

    def send_code(self, mobile: str) -> str:
        """Send the SMS code. Returns the smsToken for the verify step."""
        self._require_available()
        if not is_phone(mobile):
            raise ElemeLoginError(f"手机号格式不对：{mobile}")
        self.prepare()
        fields = self._common_fields()
        fields.update({
            "loginId": mobile.strip(),
            "codeLength": "6",
        })
        try:
            resp = self.session.post(
                self.send_url,
                data=fields,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ElemeLoginError(f"发短信请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise ElemeLoginError(f"发短信失败：服务端返回 HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ElemeLoginError("发短信失败：服务端返回异常") from exc
        inner = self._check_result(data, "发短信")
        token = (inner.get("smsToken") or "").strip()
        if not token:
            raise ElemeLoginError(f"发短信失败：没拿到 smsToken：{str(inner)[:200]}")
        return token

    def _fetch_h5_token(self) -> None:
        """Prime ``_m_h5_tk`` via one mtop call (same scheme elmxyx's
        ``prime_token`` uses — verified 2026-11-19 against the live
        ELM_COOKIE, which also lacks the token: the gateway issues
        ``_m_h5_tk`` + ``_m_h5_tk_enc`` in the Set-Cookie of the first
        mtop request, signed with an empty token).

        login.do does not set ``_m_h5_tk``, and plain GETs of H5 pages
        don't either (verified: no cookies at all). The token only
        comes from an mtop request made WITH a logged-in session
        (anonymous calls are rejected with Session过期).
        """
        import hashlib
        import json as _json
        import time
        from urllib.parse import urlencode

        api = "mtop.ele.biz.growth.task.core.querytask"
        payload = _json.dumps(
            {"bizScene": "interact_center"}, ensure_ascii=False, separators=(",", ":")
        )
        app_key = "12574478"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 "
                "MicroMessenger/8.0.58 miniProgram/wxece3a9a4c82f58c9"
            ),
            "Referer": "https://servicewechat.com/wxece3a9a4c82f58c9/831/page-frame.html",
            "Origin": "https://servicewechat.com",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "x-tap": "wx",
        }
        for attempt in range(3):
            t = str(int(time.time() * 1000))
            sign = hashlib.md5(f"&{t}&{app_key}&{payload}".encode()).hexdigest()
            query = {
                "jsv": "2.7.5",
                "appKey": app_key,
                "t": t,
                "sign": sign,
                "api": api,
                "v": "1.0",
                "type": "originaljson",
                "dataType": "json",
                "data": payload,
                "platform": "android",
                "product": "eleme",
                "prodVersion": "12.8.8",
                "ttid": "android@eleme_android_12.8.8",
            }
            url = f"https://waimai-guide.ele.me/h5/{api}/1.0/5.0/?" + urlencode(query)
            try:
                resp = self.session.post(url, headers=headers, timeout=self.timeout)
                if self.session.cookies.get("_m_h5_tk"):
                    log.info("mtop prime: got _m_h5_tk on attempt %s", attempt + 1)
                    return
                log.info(
                    "mtop prime attempt %s: HTTP %s, no _m_h5_tk yet",
                    attempt + 1, resp.status_code,
                )
            except requests.RequestException as exc:
                log.warning("mtop prime attempt %s failed: %s", attempt + 1, exc)
            time.sleep(1.0)

    def session_cookie(self) -> str:
        """Current session jar as a cookie line (names+values joined).

        Used to salvage a login whose identity cookies arrived but
        ``_m_h5_tk`` did not — the caller can retry the H5 visit later
        instead of losing the whole session.
        """
        return "; ".join(
            f"{c.name}={c.value}" for c in self.session.cookies if c.name and c.value
        )

    def verify_code(self, mobile: str, code: str, validate_token: str) -> str:
        """Verify the code. Returns the full session cookie line.

        ``validate_token`` here is the ``smsToken`` returned by
        :meth:`send_code` (bot.py stores it under that name).

        After login.do succeeds, an H5 page is visited once to collect
        ``_m_h5_tk`` (login.do does not set it). The returned cookie
        is built from the whole session jar, not just the login
        response, so it includes every cookie issued along the way.
        """
        self._require_available()
        if not is_code(code):
            raise ElemeLoginError(f"验证码格式不对：{code}")
        self.prepare()
        fields = self._common_fields()
        fields.update({
            "loginId": mobile.strip(),
            "smsCode": code.strip(),
            "smsToken": (validate_token or "").strip(),
            "keepLogin": "false",
        })
        try:
            resp = self.session.post(
                self.verify_url,
                data=fields,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ElemeLoginError(f"验证请求失败：{exc}") from exc
        if resp.status_code != 200:
            raise ElemeLoginError(f"验证码错误或已过期（HTTP {resp.status_code}）")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ElemeLoginError("验证失败：服务端返回异常") from exc
        inner = self._check_result(data, "验证登录")
        if inner.get("loginResult") != "success":
            raise ElemeLoginError(
                f"验证失败：loginResult={inner.get('loginResult')}：{str(inner)[:200]}"
            )
        # login.do does not set _m_h5_tk; fetch an H5 page to get it.
        self._fetch_h5_token()
        # Build from the whole session jar (prepare + login + H5 visit).
        cookie = "; ".join(
            f"{c.name}={c.value}" for c in self.session.cookies if c.name and c.value
        )
        ok, note = cookie_has_required_fields(cookie)
        if not ok:
            raise ElemeLoginError(f"登录未拿到有效会话：{note}")
        return cookie
