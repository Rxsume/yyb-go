#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""elmxyx pop2me 完成回调模块 v0.2（页面参数已抓通，待实测）

目标：把六个顽固任务里已映射的 2 个（48324002 页面点击、48152003 猜价格），
用模板 account_identity_cache.py 的身份缓存 + gw.pop2me.com 完成回调去做。
这是 1.5.40 的外挂，不是替代：1.5.40 的 mtop 领奖流程不动。

已接通（2026-11-21 09:04–09:15 新抓包核验，见 parameters.json / evidence.json）：
  - 任务 token：elmxyx 引擎 fetch_task_token() 现查（与回调 body 的 token 逐字同一值，已验证）
  - 签名：X-Cy-Sign 算法已验证（见 sign_cy()），线上对照实验证实已过网关验证
  - 身份：IdentityCache 按（账号备注 + 小程序 appId）缓存 deviceId；只在服务端明确报
    errorCode 6666 "read userId fail" 时才刷新；网络/签名错误保留缓存不重试
  - 请求：按抓包结构组装（接口路径、头字段、body 字段、Referer、UA），值全部现算
  - 48324002 页面参数：已抓到，tuwenId/chapingId/feedsId（feedsId 为空字符串）；
    但任务分流到 3 个小程序（菜谱/金价/房贷），各用不同的 tuwenId/chapingId，
    无通用参数——运行时跟入口 URL → H5 LINK 判定分流到哪支，再取对应参数。
    DhTaskComplete 三支都回 FINISHED，官方 querytask 全程显示 FINISH，机制已证实。
    注意：API 正文无 channel 字段（只有 H5 的 ch=3），不要乱填 channel。
  - 48152003 猜价格：channel="me" 全链确认（H5 内层页、GuessPriceInfo、
    GuessPriceCallbackByMe），13 键 Item 已完整抓到（见 DEFAULT_ITEM_48152003），
    spaceCode=42_2026051324202138193（GuessPriceInfo 多次确认同一值）。
    链路已修正为标准小程序 2021004180625470 / earn-gold-coins_1.0_EarnGoldCoinsApi /
    GuessPriceCallbackByMe（旧版错指 lq 变体 2021005106657176，那是另一条链）。
    顶层 AdBizId（Base64 解出 32 位十六进制）≠ Item.adBizId，不可互换；
    外层 AdBizId 仍由 simpleEncode(Item.adBizId) 算出（已验证一致）。

未接通（明确缺口，做不成之前不发真实请求）：
  1. 身份 deviceId 须新鲜：GetDeviceId 要 authCode（用户在对应小程序里合法授权后
     提供，环境变量 POP2ME_AUTHCODE_<appId>，一次性，用后请清除）；已有本账号的
     deviceId 也可用 --seed 导入（须确认属于该账号/该小程序；下一次业务响应才决定
     能否继续复用）。deviceId 各小程序不同、无证据可长期复用，别从旧抓包重放。
  2. 48152003 回调是否真翻阶段仍未证实：回调只回 errorCode=0/data=null（仅证明
     收到），抓包内 querytask 快照都在回调之前、48152003 全是 RUNNING。须实测一次
     再查 querytask，确认翻到 FINISH 才算通。
  3. 48324002 的 tuwenId/chapingId 带内容日期，可能随内容轮换——优先用运行时
     H5 LINK 现取，内置的 3 组值只作 dryrun 对照 / 入口抓不到时的后备。

签名（已解决，源码+独立核验双重证实）：
  小程序包 index.worker.js 模块 4866/560 的原始代码（见 guessprice_verified_algorithms.zip
  的 signing_source_excerpt.js）：
    message = f"X-App-Version={v};X-Device-Id={deviceId};X-Cy-Nonce={nonce};"
              f"X-Cy-Ts={ts};{rawBody}" 再整体 .lower()
    sign    = HMAC-SHA256(key=deviceId 原值不转小写，message UTF-8).hex()
  无固定密钥、无 URL/方法/X-App-Id/Referer 参与签名；HMAC key 就是 deviceId 本身。
  独立核验：用该算法对已抓 4 条完成回调重算，4/4 一致；包内 11 条带签名请求 11/11 一致。
  另证实：deviceId 由小程序存在本地存储长期复用，服务端报 6666 时才删存储重授权——
  与本模板的缓存策略完全一致，身份有效期以服务端 6666 为准。

安全红线：本文件不含任何抓包原值（token/sign/deviceId/authCode 均无）；
运行日志只打字段名与值长度，绝不输出凭证值；缓存目录权限 0700、文件 0600，
默认 /ql/data/config/miniapp_identity_cache（青龙持久化挂载）。

用法（青龙脚本目录内，与 elmxyx-1.5.40-pagespm.py、account_identity_cache.py 同目录）：
  python3 elmxyx_pop2me.py --account 8164 --task 48324002 --dryrun   # 默认：只解析+展示，不发请求
  python3 elmxyx_pop2me.py --account 8164 --task 48324002 --run      # 真跑（需身份缓存或新 authCode）
  python3 elmxyx_pop2me.py --account 8164 --task 48324002 --seed <deviceId> --app-id 2021006136621641
  python3 elmxyx_pop2me.py --account 8164 --task 48152003 --run      # 猜价格：Item 与 channel=me 已内置默认
"""
import base64
import gzip
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import socket
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --- 家里 IPv6 出不去、DNS 先返 IPv6 会白耗超时：强制只走 IPv4（酷狗提现同款教训） ---
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    except socket.gaierror:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from account_identity_cache import (  # noqa: E402
    AuthorizationRequired,
    IdentityCache,
    make_get_device_id_exchange,
    observed_identity_invalid,
)

SCRIPT_VERSION = "0.3"
# v0.3（2026-11-22，配合 elmxyx 1.5.46 两进式）：
# ① sign_cy 接受 app_version 参数（默认 "2"）：猜价格 CroInfo/GuessPriceInfo/
# GuessPriceCallbackByMe 用 4（2026-11-22 04:55 抓包离线验算 MATCH，v2 不匹配），
# 打卡/页面点击用 2（04:14 DhTaskStart 与 2026-11-21 打卡实测均为 v2 MATCH）。
# ② 新增 CroInfo（空体 {}，IsPassAd，猜价格第一步）。
# ③ VARIANTS_48324002 补菜谱新 appId 2021006191671004（2026-11-22 04:14 实抓，
# 旧菜谱 2021006136621641 疑似已轮换，保留作后备）。
POP2ME_HOST = "gw.pop2me.com"
REQUEST_TIMEOUT = 15
# 抓包里的支付宝容器 UA（客户端标识，非凭证）；小程序版本段会变，可用环境变量覆盖
USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Mobile/20G75 ChannelId(152) Ariver/1.1.0 "
              "AliApp(AP/12.12.16.6000) Nebula WK RVKType(0) AlipayDefined(nt:WIFI,ws:42")

# 48324002 分流到的 3 个小程序（2026-11-21 09:04–09:15 抓包核验）：
# 各支的 tuwenId/chapingId 不同，feedsId 均为空字符串；DhTaskComplete 三支都回 FINISHED。
# 运行时以入口 H5 LINK 里的 appId 为准；内置值只作 dryrun 对照 / 入口抓不到时的后备。
VARIANTS_48324002 = {
    "2021006191671004": {
        "label": "菜谱（新）",
        "route": "recipe_1.0_RecipeApi",
        "tuwenId": "ad_tiny_2021006191671004_202609052202176222",
        "chapingId": "50_2026090525002176341",
        "feedsId": "",
    },
    "2021006136621641": {
        "label": "菜谱",
        "route": "recipe_1.0_RecipeApi",
        "tuwenId": "ad_tiny_2021006136621641_202604122202130698",
        "chapingId": "50_2026041225002130731",
        "feedsId": "",
    },
    "2021006191655125": {
        "label": "金价",
        "route": "gold-quotation_1.0_GoldQuotationApi",
        "tuwenId": "ad_tiny_2021006191655125_202610172202192957",
        "chapingId": "50_2026101725002192971",
        "feedsId": "",
    },
    "2021006189633595": {
        "label": "房贷",
        "route": "mortgage-calculator_1.0_MortgageCalculator",
        "tuwenId": "ad_tiny_2021006189633595_202609052202176340",
        "chapingId": "50_2026090525002176307",
        "feedsId": "",
    },
}

# 48152003 的 13 键 Item（2026-11-21 短包 #122 实抓，标准链路 appId 2021004180625470）。
# 注意：顶层 AdBizId（Base64 解出 32 位十六进制）≠ Item.adBizId，不可互换；
# 外层 AdBizId 由 simpleEncode(Item.adBizId) 现算。Item 可能随商品轮换，
# --item-json 可覆盖；GuessPriceInfo 也能现取 spaceCode 校验。
DEFAULT_ITEM_48152003 = {
    "adSupplyType": "Bidding",
    "adBizId": "bdf4abbb7b70b484ecfc49340bdcbc96",
    "taskTitle": "去答题猜一猜商品价格",
    "taskSubTitle": "猜对得奖励",
    "adMerchantLogo": "https://mdn.alipayobjects.com/huamei_ouecfj/afts/img/A*-iB_Sa_QnqwAAAAAQCAAAAgAesqsAQ/original",
    "adMerchantName": "",
    "taskRewardName": "",
    "taskRewardAmount": "2",
    "taskClickButtonDescription": "去完成",
    "taskStatus": "INIT",
    "taskCategory": "PageView",
    "adType": "XL",
    "spaceCode": "42_2026051324202138193",
}
DEFAULT_CHANNEL_48152003 = "me"

# 任务映射（2026-11-21 抓包修正）
POP2ME_TASKS = {
    "48324002": {
        "title": "页面点击领奖励",
        # app_id / route 按运行时 H5 LINK 的 appId 在 VARIANTS_48324002 里取；
        # 旧版写死的 2021006188699901（HerbalQueryApi）是 2026-11-20 16:36 抓包的
        # 另一支，已不用。
        "api": "DhTaskComplete",
        "gw_version": "2",  # 2026-11-22 04:14 抓包验算：DhTaskStart v2 MATCH
        "page": "pages/tuwen/tuwen20261010",
        "has_app_id_header": True,
        "body_fields": ["token", "tuwenId", "chapingId", "feedsId"],
        "page_params": ["token", "tuwen_id", "chaping_id", "feeds_id"],
    },
    "48152003": {
        "title": "点击商品猜价格",
        "app_id": "2021004180625470",
        "route": "earn-gold-coins_1.0_EarnGoldCoinsApi",
        "api": "GuessPriceCallbackByMe",
        "gw_version": "4",  # 2026-11-22 04:55 抓包验算：CroInfo/GuessPriceInfo v4 MATCH
        "page": "pages/guessprice/guessprice",
        "app_version": os.environ.get("POP2ME_APP_VERSION_2021004180625470", ""),
        "has_app_id_header": False,
        "body_fields": ["AdBizId", "Token", "Channel", "Item"],
        "page_params": ["channel", "token"],
    },
}


def mask_len(value):
    """日志只给长度，绝不输出值本身。"""
    if value is None:
        return "<空>"
    if isinstance(value, (dict, list)):
        return f"<{type(value).__name__} {len(value)} 项>"
    return f"<{len(str(value))} 字符>"


def simple_encode(ad_biz_id):
    """外层 AdBizId = simpleEncode(Item.adBizId)（小程序 index.worker.js 模块 560 原样）。

    1. 逐字符替换 a<->b、c<->d、e<->f、g<->h；2. 相邻字符两两交换；
    3. Base64。已对抓包值验证一致。不是直接 Base64，也不是 MD5。"""
    if not isinstance(ad_biz_id, str) or not ad_biz_id:
        raise ValueError("Item.adBizId 为空，无法生成外层 AdBizId")
    swap = str.maketrans("abcdefgh", "badcfehg")
    chars = list(ad_biz_id.translate(swap))
    for i in range(1, len(chars), 2):
        chars[i - 1], chars[i] = chars[i], chars[i - 1]
    return base64.b64encode("".join(chars).encode("latin-1")).decode("ascii")


def sign_cy(ts_ms, nonce, body_bytes, device_id, app_id, app_version="2"):
    """X-Cy-Sign（已验证算法；小程序 index.worker.js 模块 4866/560 原始代码）。

    message = "X-App-Version={v};X-Device-Id={deviceId};X-Cy-Nonce={nonce};"
              "X-Cy-Ts={ts};{rawBody}" 再整体转小写（含 JSON 键与字符串内容）；
    sign = HMAC-SHA256(key=deviceId 原值不转小写，message UTF-8).hex()。
    body_bytes 必须是实际发出的原始 JSON 字节（JSON.stringify 结果），
    签完发同一份，不要重新序列化。无固定密钥，无 URL/方法/X-App-Id/Referer 参与。
    app_version 按实抓版本传：打卡/页面点击用 "2"，猜价格用 "4"
    （2026-11-22 04:55 抓包离线验算：CroInfo/GuessPriceInfo v4 MATCH、v2 不匹配）。"""
    raw_body = body_bytes.decode("utf-8") if isinstance(body_bytes, bytes) else str(body_bytes)
    message = (f"X-App-Version={app_version};X-Device-Id={device_id};X-Cy-Nonce={nonce};"
               f"X-Cy-Ts={ts_ms};{raw_body}").lower()
    return hmac.new(device_id.encode("utf-8"), message.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def cache_dir():
    if os.environ.get("IDENTITY_CACHE_DIR"):
        return os.environ["IDENTITY_CACHE_DIR"]
    if Path("/ql/data").is_dir():
        return "/ql/data/config/miniapp_identity_cache"
    return str(HERE / "private_cache")


def load_engine(engine_path=None):
    """加载 elmxyx 引擎（复用它的 ELM_COOKIE 会话与 fetch_task_token）。

    关键：pop2me 自身的 --dryrun/--run 不能留在 sys.argv 里——引擎在 import 时
    按 `"--dryrun" in sys.argv[1:]` 判定 DRYRUN（引擎 line 782），一旦误判，
    mtop() 只返回假空响应、不真发请求，query_tasks 就报“任务列表结构不符”。
    pop2me 的 dryrun 只表示“不发 pop2me 回调”，引擎仍须真查任务列表。"""
    if engine_path:
        path = Path(engine_path)
    else:
        cands = sorted(HERE.glob("elmxyx-*-pagespm.py"))
        path = cands[-1] if cands else None
    if not path or not Path(path).is_file():
        raise FileNotFoundError("找不到 elmxyx 引擎（elmxyx-*-pagespm.py），用 --engine 指定")
    sys.argv = [a for a in sys.argv if a not in ("--dryrun", "--run")]
    spec = importlib.util.spec_from_file_location("elmxyx_engine", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_account(engine, remark):
    for r, cookie in engine.load_accounts():
        if r == remark:
            return engine.ElmAccount(r, cookie)
    raise ValueError(f"ELM_COOKIE 里没有备注为 {remark} 的账号")


def _strings_in(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _strings_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _strings_in(v, out)
    elif isinstance(obj, str):
        out.append(obj)


def _unquote_n(s, n=3):
    for _ in range(n):
        s2 = urllib.parse.unquote(s)
        if s2 == s:
            break
        s = s2
    return s


def parse_launch_params(link):
    """从 alipays:// 落地链接里解析 page 路径、页面参数与小程序 appId。

    返回 (page, params, app_id)。appId 用于 48324002 判定分流到三支中的哪一支。"""
    if not link:
        return None, {}, ""
    s = _unquote_n(str(link))
    m = re.search(r"alipays://platformapi/startapp\?(.+)$", s)
    if not m:
        return None, {}, ""
    qs = urllib.parse.parse_qs(m.group(1))
    outer = {k: v[0] for k, v in qs.items()}
    app_id = outer.get("appId", "")
    page_raw = _unquote_n(outer.get("page", ""))
    if "?" in page_raw:
        page, query = page_raw.split("?", 1)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    else:
        page, params = page_raw, {}
    # 注意：整条链接解码后，page 值里嵌的 ?token=..&tuwen_id=.. 会散成外层参数，
    # 所以外层参数也要并进来（page 自带的参数优先，值相同）。
    merged = dict(outer)
    merged.update(params)
    merged.pop("page", None)
    merged.pop("appId", None)
    return page, merged, app_id


def extract_entry_url(link):
    """从任务跳转链接里取出真正的 m.pop2me.com 入口页地址。

    任务给的链接是 render.alipay.com 包装：scheme=alipays://platformapi/startapp?
    saId=10000007&qrcode=<m.pop2me.com 入口页>，没有 appId/page。appId 在入口页
    的 HTML（或跳转）里，不在任务链接里。返回入口页 URL，取不到返回 None。"""
    if not link:
        return None
    s = _unquote_n(str(link))
    # 直接就是 m.pop2me.com 入口页
    m = re.search(r"https?://m\.pop2me\.com[^\s\"'<>]+", s)
    if m:
        return m.group(0)
    # render 包装：解开后找 qrcode 参数（入口页地址）
    m2 = re.search(r"alipays://platformapi/startapp\?(.+)$", s)
    if m2:
        qs = urllib.parse.parse_qs(m2.group(1))
        for key in ("qrcode", "url"):
            if key in qs and qs[key]:
                cand = qs[key][0]
                if "pop2me.com" in cand:
                    return cand
    return None


def resolve_entry_url(entry_url):
    """入口页（m.pop2me.com）只读抓取一次，找里面的 alipays 落地链接。

    入口页可能 302 跳到 alipays://（urllib 跟不了非 http(s) 跳转），也可能
    在 HTML 里嵌 landing 链接；两种都处理。返回 (landing_link, error)。"""
    if not entry_url:
        return None, "入口页地址为空"
    # 自定义跳转处理：不跟非 http(s) 跳转，改为捕获 Location
    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if newurl.startswith("alipays://"):
                raise _AlipayRedirect(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class _AlipayRedirect(Exception):
        def __init__(self, url):
            self.url = url

    opener = urllib.request.build_opener(_NoFollow)
    try:
        req = urllib.request.Request(entry_url, headers={"User-Agent": USER_AGENT}, method="GET")
        with opener.open(req, timeout=10) as resp:
            text = resp.read(256 * 1024).decode("utf-8", "ignore")
    except _AlipayRedirect as ex:
        return _unquote_n(ex.url), ""
    except Exception as ex:
        return None, f"入口页抓取失败：{ex}"
    m = re.search(r"alipays://platformapi/startapp\?[^\"'<> \s]+", text)
    if not m:
        return None, "入口页里没找到 alipays 落地链接"
    return _unquote_n(m.group(0)), ""


def resolve_variant(mission_def_id, app_id, norm, notes):
    """48324002：按 H5 LINK 的 appId 定分流；48152003：固定标准链路。

    返回 (app_id, route, app_version)。48324002 若 appId 不在三支里，route 为空。"""
    cfg = POP2ME_TASKS[mission_def_id]
    if mission_def_id == "48152003":
        return cfg["app_id"], cfg["route"], cfg.get("app_version", "")
    variant = VARIANTS_48324002.get(app_id or "")
    if variant is None:
        return app_id or "", "", ""
    # 运行时 H5 LINK 没给全参数时，用该支已抓的值作后备（并注明）。
    for key in ("tuwenId", "chapingId", "feedsId"):
        if not norm.get(key) and variant.get(key) is not None:
            norm[key] = variant[key]
            notes.append(f"{key} 用已抓后备值（{variant['label']}支）")
    app_version = os.environ.get(f"POP2ME_APP_VERSION_{app_id}", "")
    return app_id, variant["route"], app_version


def load_task_params(engine, acc, mission_def_id):
    """现查任务：token + 跳转链接里的落地页参数。返回 (params, missing, notes, app_id, route, app_version)。

    注意：不用 missionIds 定向查询——2026-11-21 10:04 实测它对 0261 返“任务列表结构不符”
    （mlist 不是列表），而无过滤的 query_tasks("3112") 正常。token 优先从任务自带的
    跳转链接取（extract_action_token），定向查询只作后备。"""
    cfg = POP2ME_TASKS[mission_def_id]
    notes = []
    try:
        tasks = acc.query_tasks("3112") or []
    except Exception as ex:
        return {"token": ""}, [f"查任务列表失败：{ex}"], notes, "", "", ""
    task = None
    for t in tasks:
        if str(t.get("missionDefId") or t.get("missionId") or "") == mission_def_id:
            task = t
            break
    if task is None:
        return {"token": ""}, ["任务不在当前 3112 列表里"], ["定向查询未返回该任务"], "", "", ""
    # token：先取任务自带跳转链接里的（最可靠），再试定向查询
    token = ""
    try:
        token = engine.extract_action_token(task) or ""
    except Exception:
        token = ""
    if not token:
        try:
            token = acc.fetch_task_token(mission_def_id) or ""
        except Exception:
            token = ""
    extv = (task.get("ext") or {}).get("extValue") or {}
    action = task.get("actionConfig") or {}
    value = action.get("actionValue") or {}
    links = []
    for k in ("alipayActionLink", "elemeActionLink", "taobaoActionLink"):
        if isinstance(extv.get(k), str) and extv[k]:
            links.append(extv[k])
    for k in ("url", "jumpUrl", "targetUrl", "link"):
        if isinstance(value.get(k), str) and value[k]:
            links.append(value[k])
    page, params, app_id = None, {}, ""
    for link in links:
        page, params, app_id = parse_launch_params(link)
        if page:
            break
    if not page:
        # 跳转链接是 render 包装或入口页地址时：先取出真正的 m.pop2me.com 入口页，
        # 再读一次入口页找 alipays 落地链接（appId/page 在入口页里，不在任务链接里）。
        for link in links:
            entry = extract_entry_url(link)
            if not entry:
                continue
            resolved, err = resolve_entry_url(entry)
            if resolved:
                page, params, app_id = parse_launch_params(resolved)
                notes.append("落地页参数来自入口页抓取")
                break
            notes.append(err)
    params = dict(params)
    if token:
        params.setdefault("token", token)
    # 统一键名：tuwen_id -> tuwenId 等
    norm = {
        "token": params.get("token", ""),
        "tuwenId": params.get("tuwen_id", ""),
        "chapingId": params.get("chaping_id", ""),
        "feedsId": params.get("feeds_id", ""),
        "channel": params.get("channel", ""),
    }
    app_id, route, app_version = resolve_variant(mission_def_id, app_id, norm, notes)
    missing = []
    if mission_def_id == "48324002":
        if not route:
            missing.append(f"未知分流 appId={app_id or '<空>'}（不在菜谱/金价/房贷三支里）")
        for k in ("token", "tuwenId", "chapingId"):
            if not norm.get(k):
                missing.append(k)
    else:
        if not norm.get("channel"):
            norm["channel"] = DEFAULT_CHANNEL_48152003
            notes.append("channel 用已抓默认值 me")
        for k in ("token", "channel"):
            if not norm.get(k):
                missing.append(k)
    return norm, missing, notes, app_id, route, app_version


def build_referer(cfg, params, app_id, app_version=""):
    qs = {"token": params.get("token", "")}
    if cfg["page"].startswith("pages/tuwen"):
        qs.update({"tuwen_id": params.get("tuwenId", ""),
                   "chaping_id": params.get("chapingId", ""),
                   "feeds_id": params.get("feedsId", "")})
    else:
        qs.update({"channel": params.get("channel", "")})
    frag = cfg["page"] + "?" + urllib.parse.urlencode(qs)
    ver = app_version or cfg.get("app_version", "") or "0.0.0"
    return (f"https://{app_id}.hybrid.alipay-eco.com/{app_id}/"
            f"{ver}/index.html#{frag}")


def build_guess_price_info_request(cfg, params, identity, app_id, route, app_version=""):
    """组装 GuessPriceInfo 请求（取当前随机商品）。请求体 {Token, Channel, Bch}，
    Bch 固定 "default"。签名与回调同一套（X-Cy-Sign，key=deviceId），
    网关版本取 cfg["gw_version"]（猜价格 "4"，2026-11-22 04:55 抓包验算 MATCH）。"""
    body = {"Token": params.get("token", ""),
            "Channel": params.get("channel", "") or DEFAULT_CHANNEL_48152003,
            "Bch": "default"}
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts_ms = str(int(time.time() * 1000))
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    gw_version = str(cfg.get("gw_version", "2"))
    sign = sign_cy(ts_ms, nonce, body_bytes, identity.device_id, app_id, gw_version)
    headers = {
        "X-Cy-Nonce": nonce,
        "Content-Length": str(len(body_bytes)),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Cy-Ts": ts_ms,
        "Cookie": "",
        "Referer": build_referer(cfg, params, app_id, app_version),
        "X-Cy-Sign": sign,
        "X-Device-Id": identity.device_id,
        "X-App-Version": gw_version,
        "Accept-Charset": "utf-8",
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "Keep-Alive",
        "Host": POP2ME_HOST,
    }
    url = f"https://{POP2ME_HOST}/gateway/r/m/{route}.GuessPriceInfo"
    return url, headers, body_bytes


def build_cro_info_request(cfg, params, identity, app_id, route, app_version=""):
    """组装 CroInfo 请求（猜价格第一步，2026-11-22 04:55 实抓）。
    请求体为空 JSON 对象 {}，响应 data.IsPassAd。签名与 GuessPriceInfo
    同一套（gw_version 取 cfg，猜价格 "4"）。"""
    body_bytes = b"{}"
    ts_ms = str(int(time.time() * 1000))
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    gw_version = str(cfg.get("gw_version", "2"))
    sign = sign_cy(ts_ms, nonce, body_bytes, identity.device_id, app_id, gw_version)
    headers = {
        "X-Cy-Nonce": nonce,
        "Content-Length": str(len(body_bytes)),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Cy-Ts": ts_ms,
        "Cookie": "",
        "Referer": build_referer(cfg, params, app_id, app_version),
        "X-Cy-Sign": sign,
        "X-Device-Id": identity.device_id,
        "X-App-Version": gw_version,
        "Accept-Charset": "utf-8",
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "Keep-Alive",
        "Host": POP2ME_HOST,
    }
    url = f"https://{POP2ME_HOST}/gateway/r/m/{route}.CroInfo"
    return url, headers, body_bytes


def call_cro_info(cfg, params, identity, app_id, route, app_version=""):
    """调 CroInfo，返回解析后的响应 dict（data.IsPassAd）。"""
    url, headers, body_bytes = build_cro_info_request(
        cfg, params, identity, app_id, route, app_version)
    return post_json(url, headers, body_bytes)


def call_guess_price_info(cfg, params, identity, app_id, route, app_version=""):
    """调 GuessPriceInfo，返回解析后的响应 dict（data.config 里是当前随机商品）。"""
    url, headers, body_bytes = build_guess_price_info_request(
        cfg, params, identity, app_id, route, app_version)
    return post_json(url, headers, body_bytes)


def build_request(cfg, params, identity, app_id, route, app_version="", ad_biz_id=None, item=None):
    """组装完成回调请求。app_id/route/app_version 由运行时分流决定（48324002 三支）。"""
    if cfg["api"] == "DhTaskComplete":
        body = {"token": params.get("token", ""), "tuwenId": params.get("tuwenId", ""),
                "chapingId": params.get("chapingId", ""), "feedsId": params.get("feedsId", "")}
    else:
        if item is None:
            raise ValueError("猜价格缺 Item：须用 --item-json 提供当前任务的真实 Item")
        outer_ad_biz_id = ad_biz_id or simple_encode(item.get("adBizId", ""))
        body = {"AdBizId": outer_ad_biz_id, "Token": params.get("token", ""),
                "Channel": params.get("channel", ""), "Item": item}
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts_ms = str(int(time.time() * 1000))
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    gw_version = str(cfg.get("gw_version", "2"))
    sign = sign_cy(ts_ms, nonce, body_bytes, identity.device_id, app_id, gw_version)
    headers = {
        "X-Cy-Nonce": nonce,
        "Content-Length": str(len(body_bytes)),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        "X-Cy-Ts": ts_ms,
        "Cookie": "",
        "Referer": build_referer(cfg, params, app_id, app_version),
        "X-Cy-Sign": sign,
        "X-Device-Id": identity.device_id,
        "X-App-Version": gw_version,
        "Accept-Charset": "utf-8",
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "Keep-Alive",
        "Host": POP2ME_HOST,
    }
    if cfg["has_app_id_header"]:
        headers["X-App-Id"] = app_id
    url = f"https://{POP2ME_HOST}/gateway/r/m/{route}.{cfg['api']}"
    return url, headers, body_bytes


def post_json(url, headers, body_bytes):
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(1024 * 1024)
            enc = resp.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as ex:
        raw = ex.read(1024 * 1024)
        enc = ex.headers.get("Content-Encoding", "") if ex.headers else ""
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise ConnectionError(f"网络异常（保留身份缓存，不重试）：{ex}") from None
    if "gzip" in (enc or "").lower():
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        return {"errorCode": -1, "errorMsg": "non-json reply", "raw_len": len(raw),
                "raw_preview": raw[:200].decode("utf-8", "replace")}


def authorize(account, app_id):
    """新 authCode 来源：环境变量（用户在小程序里合法授权后填入，一次性）。"""
    value = os.environ.get(f"POP2ME_AUTHCODE_{app_id}", "").strip()
    if not value:
        raise AuthorizationRequired(
            f"账号 {account} / 小程序 {app_id} 的身份已失效，需新 authCode："
            f"请在对应小程序里重新授权，把新 authCode 填入环境变量 "
            f"POP2ME_AUTHCODE_{app_id}（一次性，用后请清除），再跑一次")
    return value


def make_operation(cfg, params, app_id, route, app_version="", ad_biz_id=None, item=None):
    def operation(account, _app_id, identity):
        url, headers, body_bytes = build_request(cfg, params, identity, app_id, route,
                                                 app_version, ad_biz_id, item)
        return post_json(url, headers, body_bytes)
    return operation


def _parse_args(argv):
    out = {"account": "", "task": "", "mode": "dryrun", "seed": "",
           "ad_biz_id": "", "item_json": "", "engine": "", "app_id": "",
           "guess_info": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--account" and i + 1 < len(argv):
            out["account"] = argv[i + 1]; i += 2
        elif a == "--task" and i + 1 < len(argv):
            out["task"] = argv[i + 1]; i += 2
        elif a == "--run":
            out["mode"] = "run"; i += 1
        elif a == "--dryrun":
            out["mode"] = "dryrun"; i += 1
        elif a == "--guess-info":
            out["guess_info"] = True; i += 1
        elif a == "--seed" and i + 1 < len(argv):
            out["seed"] = argv[i + 1]; i += 2
        elif a == "--ad-biz-id" and i + 1 < len(argv):
            out["ad_biz_id"] = argv[i + 1]; i += 2
        elif a == "--item-json" and i + 1 < len(argv):
            out["item_json"] = argv[i + 1]; i += 2
        elif a == "--engine" and i + 1 < len(argv):
            out["engine"] = argv[i + 1]; i += 2
        elif a == "--app-id" and i + 1 < len(argv):
            out["app_id"] = argv[i + 1]; i += 2
        else:
            i += 1
    return out


def main(argv):
    args = _parse_args(argv)
    print(f"===== elmxyx pop2me v{SCRIPT_VERSION} =====")
    if not args["account"] or args["task"] not in POP2ME_TASKS:
        print("用法：--account <备注> --task 48324002|48152003 [--dryrun|--run] "
              "[--seed <deviceId>] [--app-id <小程序>] [--ad-biz-id <id> --item-json <file>] [--engine <文件>]")
        return 2
    cfg = POP2ME_TASKS[args["task"]]
    cache = IdentityCache(cache_dir())
    # --seed：导入本账号本小程序的 deviceId（模板 seed 语义：只存，不验证）。
    # 48324002 须用 --app-id 指明三支中的哪一支；48152003 默认标准小程序。
    if args["seed"]:
        seed_app = args["app_id"] or cfg.get("app_id", "")
        if not seed_app:
            print("❌ --seed 须配 --app-id（48324002 有菜谱/金价/房贷三支，deviceId 各不相同）")
            return 2
        cache.seed(args["account"], seed_app, args["seed"], replace=True)
        print(f"✅ 已导入身份缓存：账号={args['account']} 小程序={seed_app} "
              f"（未验证，下一次业务响应才决定能否复用）")
        return 0
    engine = load_engine(args["engine"] or None)
    acc = get_account(engine, args["account"])
    params, missing, notes, app_id, route, app_version = load_task_params(engine, acc, args["task"])
    item = None
    if args["task"] == "48152003":
        if args["item_json"]:
            item = json.loads(Path(args["item_json"]).read_text(encoding="utf-8"))
            notes.append("Item 用 --item-json 覆盖")
        else:
            item = dict(DEFAULT_ITEM_48152003)
            notes.append("Item 用 2026-11-21 已抓默认值（可 --item-json 覆盖）")
    # 身份缓存状态（只读，不触发授权）
    path, _lock = cache._paths(args["account"], app_id) if app_id else (None, None)
    cached = cache._load(path) if path is not None and path.exists() else None
    variant_label = ""
    if args["task"] == "48324002" and app_id in VARIANTS_48324002:
        variant_label = f"（{VARIANTS_48324002[app_id]['label']}支）"
    print(f"🎯 任务={cfg['title']}({args['task']}) | 小程序={app_id}{variant_label} | "
          f"身份={'已缓存 ' + mask_len(cached.device_id) if cached else '无缓存'} | "
          f"接口={route}.{cfg['api']}")
    print(f"   参数：token={mask_len(params.get('token'))} "
          f"tuwenId={mask_len(params.get('tuwenId'))} chapingId={mask_len(params.get('chapingId'))} "
          f"feedsId={mask_len(params.get('feedsId'))} channel={mask_len(params.get('channel'))}")
    for n in notes:
        print(f"   备注：{n}")
    gaps = []
    if missing:
        gaps.append("缺页面参数：" + "、".join(missing))
    if args["guess_info"]:
        # 只调 GuessPriceInfo 看当前随机商品（不发回调）。须已有缓存身份。
        # --guess-info 本身就是一次真实请求，不受 dryrun 拦截。
        if gaps:
            print("   ❌ 不能查，缺口：" + "；".join(gaps))
            return 3
        if cached is None:
            print("   ❌ 无缓存身份，先 --seed 导入 deviceId")
            return 3
        info = call_guess_price_info(cfg, params, cached, app_id, route, app_version)
        print(f"   GuessPriceInfo：errorCode={info.get('errorCode')} errorMsg={info.get('errorMsg')}")
        if info.get("raw_preview"):
            print(f"   原始响应前 200 字：{info.get('raw_preview')}")
        data = info.get("data") or {}
        print(f"   data 键：{sorted(data.keys())}")
        config = data.get("config") or {}
        if isinstance(config, dict):
            print(f"   config 键：{sorted(config.keys())}")
            # 全部打出来（内容 ID 与界面文案，无 token/签名类值）
            for key in sorted(config.keys()):
                print(f"   config.{key} = {str(config[key])[:300]}")
        print(f"   spaceCode = {data.get('spaceCode')}")
        return 0
    if args["mode"] == "dryrun":
        print("   模式=dryrun：不发请求" + ("；缺口：" + "；".join(gaps) if gaps else "；请求结构已就绪"))
        return 0 if not gaps else 3
    if gaps:
        print("   ❌ 不能真跑，缺口：" + "；".join(gaps))
        return 3
    exchange = make_get_device_id_exchange()
    reply = cache.run(args["account"], app_id,
                      authorize=authorize, exchange=exchange,
                      operation=make_operation(cfg, params, app_id, route, app_version,
                                               args["ad_biz_id"], item))
    print(f"   响应：errorCode={reply.get('errorCode')} errorMsg={reply.get('errorMsg')} "
          f"data={mask_len(reply.get('data'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
