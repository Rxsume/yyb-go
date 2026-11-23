"""Read-only 幸运星 queries: balance + today's earnings.

Two mtop calls per account, using the same signing as elmxyx 1.5.33
(which documents both as read-only):

- balance: ``mtop.alsc.interact.et.interact.center.homepage``,
  ``data.property.data.STAR.amount`` (fallback
  ``data.exchangeV2.data.propertyInfo.remainAmount``).
- today: ``mtop.koubei.interaction.center.common.querypropertydetail``
  with ``convertType=INTERACT_CENTER``; today's GRANT records are
  ``[{name, time, amount}]`` newest first — ``name`` is
  ``extInfo.desc`` (e.g. 浏览页面得奖励）, ``time`` is ``gmtCreateStr``.
  The same page-1 records feed both the 今日获取 total and the
  收支明细 list shown in the report (same list the Ele.me app shows
  on its 幸运星记录 page).

The cookie is never logged. Only the remark, the numbers and short
error notes leave this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

APP_KEY = "12574478"
APP_VERSION = "12.8.8"
APP_ID = "wxece3a9a4c82f58c9"
GATEWAY = "https://waimai-guide.ele.me/h5/"
HOMEPAGE_API = "mtop.alsc.interact.et.interact.center.homepage"
PROPERTY_DETAIL_API = "mtop.koubei.interaction.center.common.querypropertydetail"
PAGE_VERSION = "1.4.27"
CPN_CODES = [
    "PLAY_NOTICE_CPN",
    "INTERACT_RESOURCE_CPN",
    "MORE_MENU_CPN",
    "STAR_MSG_CONTENT_CPN",
    "PLAY_RESOURCE_CPN",
    "INTERACT_CENTER_SKIN_COMPONENT",
    "INTERACT_CENTER_BUBBLE",
    "INTERACT_CENTER_LOTTERY",
]
DEFAULT_LAT = 21.72025
DEFAULT_LNG = 108.677691
SHANGHAI = timezone(timedelta(hours=8))


class LuckyStarError(Exception):
    """A query failed (message is safe to show the user)."""


def _cookie_get(cookie: str, key: str) -> str:
    from urllib.parse import unquote

    m = re.search(rf"(?:^|;\s*){re.escape(key)}=([^;]*)", cookie)
    return unquote(m.group(1)) if m else ""


def _sign_token(cookie: str) -> str:
    """mtop signing token: the part of _m_h5_tk before the first '_'."""
    m = re.search(r"(?:^|;\s*)_?m_h5_tk=([^;]+)", cookie)
    return m.group(1).split("_")[0] if m else ""


def _star_from(data: dict) -> int | None:
    v = (((data.get("property") or {}).get("data") or {}).get("STAR") or {}).get("amount")
    if v in (None, ""):
        v = (((data.get("exchangeV2") or {}).get("data") or {})
             .get("propertyInfo") or {}).get("remainAmount")
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _refresh_cookie(cookie: str, set_cookie: str) -> str:
    """Harvest a rotated _m_h5_tk/_m_h5_tk_enc from a Set-Cookie header.

    Same idea as elmxyx's _update_cookie: the mtop gateway rotates
    the signing token and hands the new one back in Set-Cookie.
    Returns the updated cookie string (unchanged if nothing new).
    """
    fields = [f.strip() for f in cookie.split(";") if f.strip()]
    by_key: dict[str, str] = {}
    order: list[str] = []
    for f in fields:
        k = f.split("=", 1)[0].strip()
        if k not in by_key:
            order.append(k)
        by_key[k] = f
    changed = False
    for key in ("_m_h5_tk", "_m_h5_tk_enc"):
        # key= must follow immediately: _m_h5_tk must not match _m_h5_tk_enc.
        m = re.search(rf"{re.escape(key)}=([^;,\s]+)", set_cookie or "")
        if not m:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = f"{key}={m.group(1)}"
        changed = True
    if not changed:
        return cookie
    return "; ".join(by_key[k] for k in order)


_TOKEN_EXPIRED_RE = re.compile(r"TOKEN_EXOIRED|TOKEN_EXPIRED|TOKEN_EMPTY", re.I)


def _mtop_post(
    api: str, data: dict, cookie: str, timeout: int = 15,
    extra_headers: dict | None = None,
) -> dict:
    """One signed mtop POST. Returns the unwrapped data dict.

    ``extra_headers`` are merged into the request headers (e.g. the
    mall ``asac`` header the exchange call needs).

    Like elmxyx's mtop, a TOKEN_EXPIRED reply is retried once when
    the response carried a rotated token in Set-Cookie: the stored
    _m_h5_tk goes stale after ~a day, but the session (SID) stays
    valid, so the gateway hands back a fresh token and the same call
    succeeds. Without the retry the bot wrongly reports a live
    session as expired (the 2026-11-20 false alarm: the 09:01 elmxyx
    run worked fine while this call said TOKEN_EXPIRED).

    Raises LuckyStarError on a dead session, a server refusal, or a
    non-JSON response. The mtop envelope wraps one extra level:
    the payload lives at ``data.data`` (same unwrap as elmxyx).
    """
    current_cookie = cookie
    for attempt in range(2):
        token = _sign_token(current_cookie)
        if not token:
            raise LuckyStarError("Cookie 里没有 _m_h5_tk，请重新登录")
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        t = str(int(time.time() * 1000))
        query = {
            "jsv": "2.7.5",
            "appKey": APP_KEY,
            "t": t,
            "sign": hashlib.md5(f"{token}&{t}&{APP_KEY}&{payload}".encode()).hexdigest(),
            "api": api,
            "v": "1.0",
            "type": "originaljson",
            "dataType": "json",
            "data": payload,
            "platform": "android",
            "product": "eleme",
            "prodVersion": APP_VERSION,
            "ttid": f"android@eleme_android_{APP_VERSION}",
        }
        url = f"{GATEWAY}{api}/1.0/5.0/?" + urlencode(query)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 "
                f"MicroMessenger/8.0.58 miniProgram/{APP_ID}"
            ),
            "Referer": f"https://servicewechat.com/{APP_ID}/831/page-frame.html",
            "Origin": "https://servicewechat.com",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "x-tap": "wx",
            "Cookie": current_cookie.strip(),
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = requests.post(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise LuckyStarError(f"网络请求失败：{exc}") from exc
        # Harvest a rotated token before parsing, like elmxyx.
        refreshed_cookie = _refresh_cookie(
            current_cookie, resp.headers.get("Set-Cookie", ""))
        try:
            js = resp.json()
        except ValueError:
            raise LuckyStarError("服务端返回非 JSON，可能需要手机验证") from None
        ret = js.get("ret")
        ret0 = ret[0] if isinstance(ret, list) and ret else str(ret or "")
        if re.search(r"SUCCESS", str(ret0), re.I):
            raw = js.get("data") or {}
            return raw.get("data", raw) if isinstance(raw, dict) else raw
        if (_TOKEN_EXPIRED_RE.search(str(ret0)) and attempt == 0
                and refreshed_cookie != current_cookie):
            current_cookie = refreshed_cookie
            continue  # token 已刷新，重试一次
        if _TOKEN_EXPIRED_RE.search(str(ret0)) or re.search(r"SESSION", str(ret0), re.I):
            raise LuckyStarError("Cookie 已失效，请重新登录")
        raise LuckyStarError(f"服务端拒绝：{str(ret0)[:60]}")
    raise LuckyStarError("请求重试次数耗尽")


def _nickname_from(data: dict) -> str:
    """闪购用户名：首页聚合响应的 shareInfo.data.nickname.

    Same homepage call as the balance — no extra request. Empty
    string when the field is absent (not every account has one).
    """
    nick = (((data.get("shareInfo") or {}).get("data") or {}).get("nickname") or "")
    return nick.strip() if isinstance(nick, str) else ""


def query_star_homepage(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> tuple[int, str]:
    """One homepage call: (幸运星 balance, 闪购用户名）.

    The nickname rides on the same response as the balance, so
    query_one_account gets both without a second request.
    """
    data = {
        "bizScene": "interact_center",
        "version": PAGE_VERSION,
        "latitude": lat,
        "longitude": lng,
        "cpnCodes": json.dumps(CPN_CODES, separators=(",", ":")),
    }
    resp = _mtop_post(HOMEPAGE_API, data, cookie, timeout)
    balance = _star_from(resp)
    if balance is None:
        raise LuckyStarError("没取到余额字段，服务端可能改版了")
    return balance, _nickname_from(resp)


def query_star_balance(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> int:
    """Return the 幸运星 balance for one account's session cookie."""
    balance, _nickname = query_star_homepage(cookie, lat, lng, timeout)
    return balance


def query_today_details(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> list[dict]:
    """Today's 幸运星 income records, newest first.

    Pages through querypropertydetail (20 per page, newest first)
    until the records stop being today's, and returns every
    ``detailType == "GRANT"`` record whose ``gmtCreateStr`` starts
    with today's date (Asia/Shanghai).

    The old version read only page 1, so an account with more than
    20 grants in a day (a normal full lucky-star run claims 30+)
    had its "今日获取" undercounted — e.g. 8164 on 2026-11-20
    really earned 3593 but page 1 alone summed to 1465.
    """
    today = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    out: list[dict] = []
    page = 1
    while page <= 10:  # safety cap: 10 pages = 200 records, far above a day's grants
        data = {
            "bizScene": "interact_center",
            "version": PAGE_VERSION,
            "latitude": lat,
            "longitude": lng,
            "convertType": "INTERACT_CENTER",
            "pageNo": page,
            "pageSize": 20,
        }
        detail = _mtop_post(PROPERTY_DETAIL_API, data, cookie, timeout)
        items = detail.get("list") or []
        if not items:
            break
        saw_today = False
        for it in items:
            if not str(it.get("gmtCreateStr", "")).startswith(today):
                continue
            saw_today = True
            if it.get("detailType") != "GRANT":
                continue
            try:
                amount = int(float(it.get("amount") or 0))
            except (TypeError, ValueError):
                continue
            ext = it.get("extInfo") or {}
            name = (ext.get("desc") or "").strip() or "幸运星奖励"
            out.append({"name": name, "time": str(it.get("gmtCreateStr", "")),
                        "amount": amount})
        if not saw_today:
            break  # records are newest-first: later pages are all older
        if not detail.get("hasMore"):
            break
        page += 1
    return out


def _detail_ext_info(record: dict) -> dict:
    """querypropertydetail 一条记录的 extInfo（防御：可能是 dict、
    JSON 字符串、缺失或别的类型；解析失败返回 {}，调用方跳过那条）。"""
    ext = record.get("extInfo")
    if isinstance(ext, dict):
        return ext
    if isinstance(ext, str):
        try:
            parsed = json.loads(ext)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def query_month_redeems(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
    max_pages: int = 6,
    page_size: int = 50,
) -> set[str]:
    """本月已兑换的商品名集合（只读）。

    翻 querypropertydetail（最新在前），只收当月 1 日及以后的记录：
    碰到更早月份的记录就停（后面的只会更早）。收 type == "REDUCE"
    且 bizType == "EXCHANGE" 且 desc 以「兑换」开头的记录，去掉
    「兑换」前缀得商品名（实测：2026-11-22 10:00:13 的 REDUCE 记录，
    desc 是「兑换美食免单卡（18元）」，对应商品名「美食免单卡（18元）」）。

    用途：有些商品（如美食免单卡）兑完后 extInfo 从不带 OVER_LIMIT，
    商城看不出已兑换；收支明细里的兑换记录才是可靠信号。不要用兑换
    请求本身去探测是否已兑（有副作用）。
    """
    month = datetime.now(SHANGHAI).strftime("%Y-%m")
    out: set[str] = set()
    page = 1
    while page <= max_pages:
        data = {
            "bizScene": "interact_center",
            "version": PAGE_VERSION,
            "latitude": lat,
            "longitude": lng,
            "convertType": "INTERACT_CENTER",
            "pageNo": page,
            "pageSize": page_size,
        }
        detail = _mtop_post(PROPERTY_DETAIL_API, data, cookie, timeout)
        items = detail.get("list") or []
        if not items:
            break
        for it in items:
            if not isinstance(it, dict):
                continue
            when = str(it.get("gmtCreateStr") or "")
            try:
                day = datetime.strptime(when[:10], "%Y-%m-%d").date()
            except ValueError:
                continue  # 日期格式不对，无法判断月份，跳过这条
            if day.strftime("%Y-%m") < month:
                return out  # 更早月份：后面的只会更早，停
            if it.get("detailType") != "REDUCE":
                continue
            ext = _detail_ext_info(it)
            if ext.get("bizType") != "EXCHANGE":
                continue
            desc = str(ext.get("desc") or "").strip()
            if not desc.startswith("兑换"):
                continue
            name = desc[len("兑换"):].strip()
            if name:
                out.add(name)
        if not detail.get("hasMore"):
            break
        page += 1
    return out


def format_detail_lines(details: list[dict] | None, limit: int = 10) -> list[str]:
    """Render detail records the way the Ele.me app lists them.

    One line per record: ``浏览页面得奖励 2026-11-19 16:47:21 +80``.
    """
    return [f"{d['name']} {d['time']} +{d['amount']}"
            for d in (details or [])[:limit]]


def query_today_earned(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> int:
    """Return today's 幸运星 earnings (GRANT records dated today)."""
    return sum(d["amount"] for d in query_today_details(cookie, lat, lng, timeout))


def query_one_account(remark: str, cookie: str) -> dict:
    """Balance + today's earnings + today's detail records for one account.

    Returns {remark, balance | None, today | None, details,
    error | None, today_error | None, nickname}. The nickname comes
    from the same homepage call as the balance (no extra request).
    The detail records come from the same querypropertydetail call
    as the today total, so one mtop call feeds both.
    """
    row: dict = {"remark": remark, "balance": None, "today": None,
                 "details": [], "error": None, "today_error": None,
                 "nickname": ""}
    if not cookie:
        row["error"] = "Cookie 为空"
        return row
    try:
        row["balance"], row["nickname"] = query_star_homepage(cookie)
    except LuckyStarError as exc:
        row["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad account must not block the rest
        row["error"] = f"查询失败：{exc}"
    if row["error"] is None:
        try:
            details = query_today_details(cookie)
            row["today"] = sum(d["amount"] for d in details)
            row["details"] = details
        except LuckyStarError as exc:
            row["today_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            row["today_error"] = f"查询失败：{exc}"
    return row


def query_all_stars(qinglong: Any) -> list[dict]:
    """Balance + today's earnings + today's detail records for every
    Ele.me account in Qinglong.

    Returns [{remark, balance | None, today | None, details,
    error | None, today_error | None, nickname}] in slot order.
    Reads cookies through the Qinglong client; never logs them.
    Accounts are independent mtop sessions, so they are queried in
    parallel (pool.map keeps slot order); one slow account no longer
    blocks the other nine.
    """
    try:
        accounts = qinglong.list_ele_cookies()
    except Exception as exc:  # noqa: BLE001 - surface as a per-line error
        return [{"remark": "青龙", "balance": None, "today": None,
                 "details": [], "error": f"读青龙变量失败：{exc}",
                 "today_error": None, "nickname": ""}]
    if not accounts:
        return []
    with ThreadPoolExecutor(max_workers=max(1, len(accounts))) as pool:
        return list(pool.map(
            lambda acc: query_one_account(
                acc.get("remark") or "账号", acc.get("cookie") or ""),
            accounts))
