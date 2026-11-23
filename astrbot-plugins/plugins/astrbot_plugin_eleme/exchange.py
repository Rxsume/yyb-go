"""幸运星商城兑换：商品列表 + 真实兑换（与 elmxyx 1.5.37 mall() 同款请求）。

- 列表（两个来源合并去重，按 exchangeId；同一商品以新版首页为准）：
  1. 新版商城首页 exchangeV2（mtop.alsc.interact.et.interact.center.homepage，
     data 里 modules 点名要 exchangeV2，官方 App 首页的请求方式）；
  2. 旧 mallrecall（mtop.alsc.interact.playapp.reward.right.mallrecall），
     分页拉取，补全首页之外的商品，也做兜底。
- 兑换：mtop.alsc.interact.playapp.reward.right.exchange，带 asac
  alsc5KvbdX5mHl3sdv4guV；只兑调用方确认过的那一件，不会顺带兑别的。
- 安全规则（与 elmxyx 一致）：
  * 未开售 / 差星 / 超限的商品不发兑换请求；
  * 兑换响应异常（回包丢失）绝不自动重试——结果未知，必须到官方页面核实；
  * 服务端明确拒绝只报原因，不重试。

Cookie 永不记日志；只输出商品名、ID、星数与结果。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .lucky_star import (
    DEFAULT_LAT,
    DEFAULT_LNG,
    HOMEPAGE_API,
    PAGE_VERSION,
    LuckyStarError,
    _mtop_post,
    query_month_redeems,
)

BEIJING = ZoneInfo("Asia/Shanghai")

MALL_LIST_API = "mtop.alsc.interact.playapp.reward.right.mallrecall"
MALL_EXCHANGE_API = "mtop.alsc.interact.playapp.reward.right.exchange"
MALL_ASAC = "alsc5KvbdX5mHl3sdv4guV"  # 幸运星兑换商城（elmxyx ASAC["MALL_EXCHANGE"]）
# 新版商城首页：官方 App 请求首页时用的 version 与 modules（HAR 实测
# 2026-11-22，quantumult-x-20261122-090812.har）；其中 exchangeV2
# 就是首页的商城商品列表。
HOMEPAGE_EXCHANGE_VERSION = "1.4.32"
HOMEPAGE_EXCHANGE_MODULES = [
    "signIn", "property", "mdCard", "orderFreeDraw", "orderFreeMarquee",
    "exchangeBarrage", "exchangeV2",
]
READY_STATUSES = ("INIT", "EXCHANGE_ABLE", "CAN_EXCHANGE", "ONLINE", "AVAILABLE")
NOT_STARTED_STATUSES = ("SECKILL_NOT_STARTED", "EXCHANGE_NOT_STARTED")
MALL_PAGE_SIZE = 9  # 商城列表每页 9 件（与参考界面一致）
MILK_TEA_EXCHANGE_ID = "2026081216111590501323120613"  # 奶茶免单卡（18元），18888 星


def _parse_start_time(value: str | None) -> datetime | None:
    """exchangeStartTime（北京时间 "YYYY-MM-DD HH:MM:SS"）→ 带时区时间。"""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(),
                                 "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except ValueError:
        return None


def item_status_tag(item: dict, now: datetime | None = None) -> str:
    """商品状态标签（参考界面写法）：
    ✅可兑 / ⏰11-20 15:00 开抢 / 秒杀已结束 / ✅已兑换奶茶免单卡（18元） /
    差 X 星 / 当前不可兑。

    OVER_LIMIT 是按单品按账号算的（extInfo，本月已兑过这件商品），
    显示成“✅已兑换{商品名}”而不是笼统的“已超限”，免得没兑过的号
    也被误会。只影响显示，不影响 ready 判定，兑换拦截仍靠 ready。
    """
    if item.get("ready"):
        return "✅可兑"
    status = str(item.get("status") or "")
    if status in NOT_STARTED_STATUSES:
        start = _parse_start_time(item.get("exchange_start_time"))
        now = now or datetime.now(BEIJING)
        if start is not None and start > now:
            return f"⏰{start.strftime('%m-%d %H:%M')} 开抢"
        if start is not None:
            return "秒杀已结束"
        return "当前不可兑"
    if status == "OVER_LIMIT":
        return f"✅已兑换{item.get('name') or '商品'}"
    gap = item.get("gap") or 0
    if gap > 0:
        return f"差 {int(gap)} 星"
    return "当前不可兑"


class ExchangeError(Exception):
    """商城查询/兑换失败（消息可直接发给用户）。"""


def _number(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _item_name(raw: dict) -> str:
    return (
        ((raw.get("materialInfo") or {}).get("title"))
        or ((raw.get("rightMaterialInfoDTO") or {}).get("title"))
        or ((raw.get("bizExtInfo") or {}).get("exchangeIntroduction"))
        or "未命名商品"
    )


def _ext_info(raw: dict) -> dict:
    """extInfo（按账号算的限额信息）。

    缺失、非 dict、字符串解析失败时返回 {}，调用方按“无限额信息”处理。
    """
    ext = raw.get("extInfo")
    if isinstance(ext, dict):
        return ext
    if isinstance(ext, str):
        try:
            parsed = json.loads(ext)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _readiness(raw: dict) -> tuple[bool, str]:
    """（是否可兑，原因）。原因空字符串表示可兑。"""
    # extInfo 是按账号算的限额，与全局 exchangeStatus 无关（实测
    # 2026-11-22：1793 的 exchangeStatus 是 EXCHANGE_NOT_STARTED，
    # 但 extInfo.errorCode 是 OVER_LIMIT，本月已兑过）。
    if str(_ext_info(raw).get("errorCode") or "") == "OVER_LIMIT":
        return False, "已超限"
    ex = raw.get("exchangeInfo") or {}
    status = str(ex.get("exchangeStatus") or "")
    gap = _number(ex.get("gapAmount", 0))
    cost = _number(ex.get("consumeAmount"))
    ident = str(raw.get("exchangeId") or "").strip()
    if status == "SECKILL_NOT_STARTED":
        return False, "未开售"
    if status == "OVER_LIMIT":
        return False, "已超限"
    if gap is not None and gap > 0:
        return False, f"差 {int(gap)} 星"
    if status and status not in READY_STATUSES:
        return False, status
    if cost is None:
        return False, "所需星未知"
    if not ident:
        return False, "商品 ID 缺失"
    return True, ""


def _parse_item(raw: dict) -> dict:
    ready, reason = _readiness(raw)
    ex = raw.get("exchangeInfo") or {}
    ext = _ext_info(raw)
    cost = _number(ex.get("consumeAmount"))
    gap = _number(ex.get("gapAmount", 0))
    # 超限记到 status 上，让 item_status_tag 的“✅已兑换…”分支生效。
    status = str(ex.get("exchangeStatus") or "")
    if str(ext.get("errorCode") or "") == "OVER_LIMIT":
        status = "OVER_LIMIT"
    return {
        "name": _item_name(raw),
        "exchange_id": str(raw.get("exchangeId") or "").strip(),
        "cost": int(cost) if cost is not None else None,
        "gap": int(gap) if gap is not None else 0,
        "status": status,
        "ready": ready,
        "reason": reason,
        # 按账号的限额说明（extInfo.errorDesc），超限时报告里显示。
        "limit_note": str(ext.get("errorDesc") or "").strip(),
        "exchange_act_id": raw.get("exchangeActId") or "",
        "exchange_collection_id": raw.get("exchangeCollectionId") or "",
        "act_code": raw.get("actCode") or "",
        # 限量商品的开售时间（商城原字段 exchangeInfo.exchangeStartTime，
        # 北京时间 "YYYY-MM-DD HH:MM:SS"，如奶茶卡 "2026-11-21 15:00:00"）。
        "exchange_start_time": str(ex.get("exchangeStartTime") or "").strip(),
    }


def next_exchange_time(start_time_str: str | None,
                       now: datetime | None = None) -> datetime | None:
    """exchangeStartTime → 下一次开售时间（北京时间，带时区）。

    在未来就直接用；已过就顺延到明天同一时间（每日限量档）；无字段或
    解析失败返回 None（调用方回退到手动输时间）。
    """
    target = _parse_start_time(start_time_str)
    if target is None:
        return None
    now = now or datetime.now(BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    if target <= now:
        target = target + timedelta(days=1)
    return target


def _fetch_homepage_exchange_items(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> list[dict]:
    """新版商城首页 exchangeV2 列表（只读，第 1 页，9 件）。

    调的是和余额查询同一个首页接口（HOMEPAGE_API），只是 data 里用
    modules 参数点名要 exchangeV2——官方 App 首页就是这么请求的
    （HAR 实测 2026-11-22）。响应里 exchangeV2.data.list 就是 App
    首页的商城商品，条目结构和旧 mallrecall 一致，直接复用
    _parse_item。

    关于 HAR 里另外几个字段：
    * channel / spPageId / instance / cityName 不发——channel 是小程
      序入口渠道、spPageId 是每次打开页面现生成的页面实例 ID，都跟
      会话/页面有关，不能把 HAR 里那一份硬编码成死值；余额查询已证
      明首页接口不用它们也能返回 exchangeV2（_star_from 就是从那
      里读 remainAmount 的），modules 才是真正点名要 exchangeV2
      的参数。
    * 只取第 1 页：HAR 只有第 1 页，hasMore=true、totalRecords=61，
      但首页请求里没有 pageNo 之类的分页参数，exchangeV2 的分页方
      式还没搞清，留待后续用新的抓包确认；第 1 页之外的商品目前仍
      由旧 mallrecall 补。

    调用失败或响应里没有列表时返回 []，调用方用旧 mallrecall 兜底，
    商城不挂。
    """
    data = {
        "bizScene": "interact_center",
        "version": HOMEPAGE_EXCHANGE_VERSION,
        "modules": json.dumps(HOMEPAGE_EXCHANGE_MODULES, separators=(",", ":")),
        "latitude": lat,
        "longitude": lng,
    }
    try:
        resp = _mtop_post(HOMEPAGE_API, data, cookie, timeout)
    except LuckyStarError:
        return []
    listing = ((resp or {}).get("exchangeV2") or {}).get("data") or {}
    batch = listing.get("list") or []
    if not isinstance(batch, list):
        return []
    return [_parse_item(raw) for raw in batch if isinstance(raw, dict)]


def list_mall_items(
    cookie: str,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
    check_redeemed: bool = True,
) -> list[dict]:
    """拉取商城商品（只读）。失败时抛 ExchangeError。

    两个来源合并去重（按 exchangeId）：
    1. 新版商城首页 exchangeV2（第 1 页，9 件）——和官方 App 首页
       一致，排在前面；
    2. 旧 mallrecall 分页——补全首页之外的商品，也做兜底。
    同一商品以新版首页的数据为准；首页调用失败时只用旧接口结果，
    商城不挂。

    check_redeemed（默认开）：合并完后用本月星星收支明细里的兑换
    记录再认一遍已兑换——有些商品（如美食免单卡）兑完后 extInfo
    从不带 OVER_LIMIT，商城看不出已兑换，收支记录才是可靠信号。
    名字命中已兑集合的商品标成已兑换（status OVER_LIMIT、ready
    False、reason「已兑换」，现成的 item_status_tag 就会显示
    「✅已兑换{商品名}」）；收支查询失败时只丢下这步标记，商城照常返回。
    """
    items: list[dict] = []
    seen: set[str] = set()

    def _add(parsed: dict) -> bool:
        """并入一件已解析商品；重复的 exchangeId 跳过。返回是否并入。"""
        key = parsed.get("exchange_id") or ""
        if not key:
            # 无 ID 的脏数据不参与去重，直接并入（_parse_item 已标
            # 它“商品 ID 缺失”，兑换时会被拦住）。
            items.append(parsed)
            return True
        if key in seen:
            return False
        seen.add(key)
        items.append(parsed)
        return True

    # 来源 1：新版首页 exchangeV2（第 1 页；分页方式未知，见上）。
    for parsed in _fetch_homepage_exchange_items(cookie, lat, lng, timeout):
        _add(parsed)
    # 来源 2：旧 mallrecall 分页（兜底 + 补全）。
    # 停表条件（实测 2026-11-22）：响应带 hasMore 与 totalRecords，
    # 但每页条数不固定（第 1 页 9 条、第 2 页 6 条、第 3 页 10 条），
    # 不能只看 len(batch) < 10 就停——第一页不到 10 条就提前断页，
    # 商品拉不全（7636/9318/6591 因此在奶茶查询里找不到奶茶卡，
    # 商城也只有首页的 9 件而不是 55 件）。
    # 停表：空页，或服务端 hasMore=False，或 mallrecall 新收的去重后
    # 商品数达到 totalRecords，或连续两页都没新增商品（防重页死循环）；
    # 上限仍 20 页。hasMore/totalRecords 缺失时（旧桩）只靠空页与
    # 无新增页停表。
    no_new_pages = 0
    mall_new = 0  # mallrecall 本路新收的去重后商品数
    for page in range(1, 21):
        data = {
            "bizScene": "interact_center",
            "version": PAGE_VERSION,
            "latitude": lat,
            "longitude": lng,
            "propertyBizType": "STAR",
            "pageNo": page,
            "pageSize": 10,
        }
        try:
            detail = _mtop_post(MALL_LIST_API, data, cookie, timeout)
        except LuckyStarError as exc:
            raise ExchangeError(f"商城查询异常：{exc}") from exc
        batch = (detail or {}).get("list") or []
        if not isinstance(batch, list):
            raise ExchangeError("商城查询异常：商品列表格式不符")
        if not batch:
            break
        added = 0
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            if _add(_parse_item(raw)):
                added += 1
        mall_new += added
        if added == 0:
            no_new_pages += 1
            if no_new_pages >= 2:
                break
        else:
            no_new_pages = 0
        if (detail or {}).get("hasMore") is False:
            break
        total = _number((detail or {}).get("totalRecords"))
        if total is not None and mall_new >= int(total):
            break
    if check_redeemed:
        try:
            redeemed = query_month_redeems(cookie, lat, lng, timeout)
        except LuckyStarError:
            redeemed = set()  # 标记失败只丢下这步，商城照常返回
        if redeemed:
            for item in items:
                # 已是 OVER_LIMIT 的（奶茶卡原有路径）不动；只补标
                # extInfo 不带 OVER_LIMIT 的那类商品（如美食免单卡）。
                if (item.get("name") in redeemed
                        and item.get("status") != "OVER_LIMIT"):
                    item["status"] = "OVER_LIMIT"
                    item["ready"] = False
                    item["reason"] = "已兑换"
    return items


def describe_item(item: dict, now: datetime | None = None) -> str:
    """商城列表里的一行：名称 — 所需星[状态标签]（参考界面写法）。"""
    cost = f"{item['cost']}星" if item.get("cost") is not None else "星数未知"
    return f"{item['name']} — {cost}[{item_status_tag(item, now)}]"


def exchange_item(
    cookie: str,
    item: dict,
    lat: float = DEFAULT_LAT,
    lng: float = DEFAULT_LNG,
    timeout: int = 15,
) -> str:
    """兑换一件已确认的商品。返回给用户看的结果行。

    未就绪不发请求；响应异常不重试（结果未知）。
    """
    name = item.get("name") or "商品"
    if not item.get("ready"):
        return f"➖ {name} 当前不可兑换（{item.get('reason') or '状态未知'}），未兑换"
    missing = [k for k in ("exchange_act_id", "exchange_collection_id",
                           "exchange_id", "act_code") if not item.get(k)]
    if missing:
        return f"⛔ {name} 缺少兑换参数，未兑换"
    data = {
        "bizScene": "interact_center",
        # 2026-11-22 官方兑换请求实测：必须带 source（业务来源），
        # 否则报 FAIL_BIZ_ILLEGAL_ARGUMENT「业务来源不能为空」。
        "source": "INTERACT_CENTER_EXCHANGE_MALL",
        "version": PAGE_VERSION,
        "latitude": lat,
        "longitude": lng,
        "actId": item["exchange_act_id"],
        "exchangeCollectionId": item["exchange_collection_id"],
        "exchangeId": item["exchange_id"],
        "actCode": item["act_code"],
        "propertyBizType": "STAR",
    }
    try:
        _mtop_post(MALL_EXCHANGE_API, data, cookie, timeout,
                   extra_headers={"asac": MALL_ASAC})
    except LuckyStarError as exc:
        message = str(exc)
        if message.startswith("服务端拒绝："):
            return f"❌ 兑换未成功：{message[len('服务端拒绝：'):][:80]}"
        if "Cookie 已失效" in message:
            return f"❌ {message}"
        # 网络 / 非 JSON / 其他：响应丢了，结果未知，绝不重试。
        return "⚠ 兑换响应异常，结果未知，请到官方页面核实后再运行"
    cost = item.get("cost")
    tail = f"（-{cost} 星）" if cost is not None else ""
    return f"✅ 已兑换：{name}{tail}"
