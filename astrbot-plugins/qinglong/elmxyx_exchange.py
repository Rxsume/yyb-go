#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 饿了么幸运星兑换（单独脚本，青龙任务「饿了么幸运星兑换」）
# 独立提取版，编写于 2026-11-20：兑换链路（ElmAccount 的 mall/mtop/prime_token
# 及依赖）从 elmxyx-1.5.37-pagespm.py 原样提取，不依赖引擎文件，1.5.37 升级、
# 改名、删除都不影响本脚本。签到/任务/抽奖等代码未提取，本脚本只做兑换。
# 安全设计：兑换开关 ELM_EXCHANGE=1 只在本脚本进程内设置，绝不写进青龙环境变量
# （否则主任务 163 跑到商城那步也会跟着兑换）；目标商品只从 ELM_EXCHANGE_ID
# （精确 ID）或 ELM_EXCHANGE_NAME（完整名称）取，这两个变量建在青龙里是安全的；
# 每个账号每轮最多兑换 ELM_EXCHANGE_MAX 件（默认 1）；商品未到可兑状态
# （未开始/超限/星不够）时只报告、不兑换。本任务默认禁用、只手动跑。
# 用法：task elmxyx_exchange.py --id <商品ID>
#       task elmxyx_exchange.py --name "完整商品名"
#       task elmxyx_exchange.py --dryrun --id <ID>        # 演练，不真兑换
#       task elmxyx_exchange.py --account 8164,0261,7636 --id <ID>  # 只兑指定备注的账号（多个用英文逗号隔开）
#       task elmxyx_exchange.py --at 10:00 --id <ID>  # 限量抢兑：提前启动等到 10:00，多账号并发开抢
# 定时：青龙任务每天 09:59 启动（命令里带 --at 10:00），到点并发开抢；并发延迟靠提前启动抵消。
# 开抢后未到时间/查询异常时每 2 秒重试，默认重试窗口 300 秒（ELM_EXCHANGE_WAIT/--wait 可改）。
# 环境变量方式：ELM_EXCHANGE_ID / ELM_EXCHANGE_NAME / ELM_EXCHANGE_ACCOUNT /
# ELM_EXCHANGE_MAX / ELM_EXCHANGE_DRYRUN=1（只影响本次运行）。


from __future__ import annotations


import base64


import hashlib


import hmac


import json


import math


import os


import random


import re


import string


import sys


import time


import uuid


from datetime import datetime


from pathlib import Path


from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl, unquote


import tempfile


import requests


import urllib3


API = {
    "homepage":     "mtop.alsc.interact.et.interact.center.homepage",
    "querytask":    "mtop.ele.biz.growth.task.core.querytask",
    "receivetask":  "mtop.ele.biz.growth.task.core.receivetask",
    "receiveprize": "mtop.ele.biz.growth.task.core.receiveprize",
    "pageview":     "mtop.ele.biz.growth.task.event.pageview",
    "trigger":      "mtop.ele.biz.growth.task.event.trigger",
    "callback":     "mtop.ele.biz.growth.task.event.new.callback",
    # 签到并领奖 / 独立签到
    "signin":       "mtop.alsc.interact.playapp.signin.component.signinandreceive",
    "recordsignin": "mtop.alsc.interact.playapp.signin.component.recordsignin",
    # 夺宝/抽奖（1.4.2 前端包逆向）
    "lottery_draw": "mtop.alsc.interact.playapp.reward.lottery.draw",
    # 幸运星资产/明细、兑换商城
    "property_detail": "mtop.koubei.interaction.center.common.querypropertydetail",
    "mall_list":      "mtop.alsc.interact.playapp.reward.right.mallrecall",
    "mall_exchange":  "mtop.alsc.interact.playapp.reward.right.exchange",
    # 下单免单抽奖
    "orderfree_draw": "mtop.alsc.interact.et.orderfree.draw",
    # 趣味话题答题
    "quiz_query":  "mtop.alsc.interact.playapp.quiz.query",
    "quiz_submit": "mtop.alsc.interact.playapp.quiz.submit",
    "quiz_award":  "mtop.alsc.interact.playapp.quiz.award",
}


ASAC = {
    "SIGNIN":          "alsc3Lhy681SA5TT4iHgL3",
    "PRIZE":           "alscadOjfleDPawx9zVoT0",
    "PAGEVIEW":        "alscFS8BNTO6jivYS7XOAM",
    "TRIGGER":         "2A21607NIIT1ND5C4YXJ6C",
    "LOTTERY_DRAW":    "alscdZLQXT9DEAlXvQQOLO",   # 夺宝/抽奖
    "MIANDAN_DRAW":    "alsc6AD8Rni21gsqJ5rceg",   # 免单抽奖
    "ORDER_FREE_DRAW": "alscrX2P4rmvl0NhmQgr9x",   # 下单免单
    "MALL_EXCHANGE":   "alsc5KvbdX5mHl3sdv4guV",   # 幸运星兑换商城
    "SNS_TRIGGER":     "alsc8tok9QFxdUiCPfmpbK",   # 分享触发
    "QUIZ_AWARD":      "alsc0Ue9L6Q0YtTZjC3Ovx",   # 答题领奖
}


BIZ_SCENE = "interact_center"


ACCOUNT_PLAN = "HAVANA_COMMON"


PAGE_VERSION = "1.4.27"


H5_TASK_PLATFORM = "ios"


H5_TASK_TTID = "H5@Web_iphone_1.4.27"


DRYRUN = os.environ.get("LUCKYSTAR_DRYRUN", "") == "1" or "--dryrun" in sys.argv[1:]


STEALTH = os.environ.get("ELM_STEALTH", "1") != "0"


RISK_RE = re.compile(r"FAIL_SYS_USER_VALIDATE|FAIL_SYS_SESSION_EXPIRED|FAIL_SYS_SESSION_INVALID|FAIL_SYS_TOKEN_ILLEGAL|FAIL_SYS_ILLEGAL_ACCESS|NOT_LOGIN|LOGIN_REQUIRED|FAIL_BIZ_USER_RISK|用户存在风险|USER_RISK", re.I)


DEVICE_MODELS = ["22081212C", "PHB110", "V2134A", "SM-S9080",
                 "PJZ110", "LIO-AN00", "M2011K2C", "PGT-AN10"]


DEVICE_VERSIONS = ["13", "12", "14", "11"]


DEVICE_CHANNELS = ["channel35", "channel50", "channel60"]


DEVICE_FILE = Path(__file__).resolve().parent / "lucky_devices.json"


def random_hex(nbytes: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(nbytes))


def obfuscate_id(raw: str) -> str:
    """与面板同款混淆：逐字符 +2（不可逆）。"""
    return "".join(chr((ord(c) + 2) & 0xFFFF) for c in raw)


def make_device() -> dict:
    """生成一个设备指纹：机型/版本/渠道从池里随机，ID 全随机（同面板 makeDevice）。"""
    rid = random_hex(16)
    model = random.choice(DEVICE_MODELS)
    ver = random.choice(DEVICE_VERSIONS)
    ch = random.choice(DEVICE_CHANNELS)
    wxv = cfg("ELM_WECHAT_VERSION", "8.0.58")
    return {
        "androidIdRaw": rid,
        "androidIdObfuscated": obfuscate_id(rid),
        "clientType": f"#11.3.0#{ch}#{model}#",
        "androidVersion": ver,
        "model": model,
        "channel": ch,
        "wechatVersion": wxv,
        "os": cfg("ELM_DEVICE_OS", "android"),
        "ua": (f"Mozilla/5.0 (Linux; Android {ver}; {model} Build/TKQ1.220829.002; wv) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/107.0.0.0 "
               f"Mobile Safari/537.36 XWEB/1160 MMWEBSDK/20230805 MicroMessenger/{wxv} "
               "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"),
    }


def device_key(d: dict) -> str:
    return f"{d.get('clientType', '')}#{d.get('androidIdObfuscated', '')}"


def remote_device(api: str, key: str, reset: bool = False) -> dict | None:
    """
    向指纹服务（fingerd / 自建面板）申请设备：同一个 key 会拿回同一台（服务端保证）。
    服务不可用或返回异常时返回 None，调用方自动退回本地生成。
    """
    if not api:
        return None
    url = api if api.rstrip("/").endswith("gen_device") else api.rstrip("/") + "/api/gen_device"
    headers = {}
    ak = cfg("ELM_DEVICE_API_KEY")
    if ak:
        headers["X-API-Key"] = ak
    try:
        r = requests.post(url, timeout=6, headers=headers, json={
            "key": key, "scene": cfg("ELM_DEVICE_SCENE", "eleme"),
            "os": cfg("ELM_DEVICE_OS", "android"), "reset": bool(reset)})
        j = r.json() or {}
        d = j.get("data") or j
        if isinstance(d, dict) and (d.get("androidIdObfuscated") or d.get("clientType")):
            log(f"[指纹] 领取自 {api} → {d.get('model')} / Android {d.get('androidVersion')}"
                f" / {d.get('channel')}")
            return d
        log(f"[指纹] 服务返回异常（{str(j)[:80]}），改用本地生成")
    except Exception as exc:                                # noqa: BLE001
        log(f"[指纹] 服务不可用（{str(exc)[:60]}），改用本地生成")
    return None


def alloc_device(account_key: str, reset: bool = False) -> dict:
    """
    给账号分配**独占且不复用**的设备指纹，并持久化：
      · 同一账号下次运行拿到同一指纹（设备稳定，不会天天换机）
      · 不同账号绝不共用（避开"一机多号"关联）
      · ELM_RESET_DEVICE=1 相当于面板的「重置设备」
    """
    if DRYRUN:
        dev = make_device()
        dev["utdid"] = "DRYRUN-LOCAL-ONLY"
        return dev
    store: dict = {}
    try:
        if DEVICE_FILE.is_file():
            store = json.loads(DEVICE_FILE.read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001
        store = {}
    accs = store.setdefault("accounts", {})
    used = set(store.get("used") or [])
    dev = None if reset else accs.get(account_key)
    if dev is None:
        api = cfg("ELM_DEVICE_API")
        dev = remote_device(api, account_key, reset) if api else None
        if not dev:
            for _ in range(100):                            # 同面板：最多试 100 次找未用过的
                cand = make_device()
                if device_key(cand) not in used:
                    dev = cand
                    break
            else:
                dev = make_device()
        used.add(device_key(dev))
        accs[account_key] = dev
        store["used"] = sorted(used)[-2000:]
        if not dev.get("utdid"):
            dev["utdid"] = ("WX" + hashlib.sha1(
                (dev["androidIdRaw"] + dev["channel"]).encode()).hexdigest()[:30]).upper()
        try:
            DEVICE_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass
    return dev


def redact(message: str) -> str:
    """日志不打印登录Cookie、签名和推送令牌。"""
    text = str(message)
    text = re.sub(r"(?im)\b(Cookie|Set-Cookie|Authorization)\s*:\s*[^\r\n]+", r"\1: [已隐藏]", text)
    fields = (r"cookie2|_m_h5_tk(?:_enc)?|sid|utuser|userid|munb|unb|sgcookie|_tb_token_|"
              r"_nk_|tfstk|isg|cna|x5check_ele|track_id|_c_WBKFRo|__ebg_uid|__ebg_utdid|"
              r"access_token|refresh_token|token|sign|password|secret|corpsecret")
    text = re.sub(r"(?i)(\b(?:" + fields + r")\s*=\s*)[^;\s&\]】,]+", r"\1[已隐藏]", text)
    return text


def log(msg: str = "") -> None:
    print(f"{datetime.now():%H:%M:%S} {redact(msg)}", flush=True)


def cfg(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return v.strip() if v and v.strip() else default


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def parse_cookie_line(line: str) -> tuple[str, str]:
    """每行一个账号，备注#完整Cookie；兼容Cookie#备注及Cookie片段#片段。"""
    raw = str(line or "").strip().replace("\u3000", " ")
    if not raw:
        return "", ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].strip()
    chunks, labels = [], []
    for part in raw.split("#"):
        part = re.sub(r"(?i)^\s*cookie\s*:\s*", "", part.strip())
        # 兼容导出文件行首时间和标签，但绝不把key=value部分当备注。
        match = re.search(r"(?:^|\s)([A-Za-z0-9_!$%&'*+.^`|~-]+=)", part)
        if match:
            fragment = part[match.start(1):].strip().strip(";")
            chunks.append(fragment)
        elif part and "=" not in part and ";" not in part:
            labels.append(part)
    if not chunks:
        return "", ""
    values, conflicts = {}, set()
    for chunk in chunks:
        for part in chunk.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and re.fullmatch(r"[A-Za-z0-9_!$%&'*+.^`|~-]+", key):
                if "\r" in value or "\n" in value:
                    continue
                value = value.strip()
                if key in values and values[key] != value:
                    conflicts.add(key)
                values[key] = value
    if conflicts:
        raise ValueError("同一行存在冲突的Cookie字段或用户ID；多账号必须换行，不能用#拼接")
    cookie = "; ".join(f"{key}={value}" for key, value in values.items())
    remark = labels[0] if labels else cookie_remark(cookie)
    if "=" in remark or ";" in remark or re.search(r"1[3-9]\d{9}", remark):
        remark = cookie_remark(cookie)
    remark = re.sub(r"[\r\n\t]", " ", remark).strip()[:32] or "账号"
    return remark, cookie


def cookie_remark(cookie: str) -> str:
    """自动备注只保留用户ID尾四位，不回显Cookie或手机号。"""
    match = re.search(r"(?:^|;\s*)(?:UTUSER|userId|USERID|unb)=([^;]+)", cookie)
    value = match.group(1) if match else ""
    return "账号" + ("***" + value[-4:] if value else "")


def load_accounts() -> list[tuple[str, str]]:
    """优先ELM_COOKIE（每行一号），额外支持ELM_COOKIE2/3等独立变量。"""
    raw = ""
    for name in ("ELM_COOKIE", "ELEME_COOKIE", "LUCKYSTAR", "XX_ACCOUNTS", "LS_ACCOUNTS", "LUCKYSTAR_ACCOUNTS"):
        if cfg(name):
            raw = cfg(name)
            break
    extras = [cfg(f"ELM_COOKIE{i}") for i in range(2, 21) if cfg(f"ELM_COOKIE{i}")]
    if extras:
        raw = "\n".join([raw] + extras)
    if not raw.strip():
        name = cfg("ELM_COOKIE_FILE", "eleme_cookie.txt")
        here = Path(__file__).resolve().parent
        for candidate in (Path(name), here / name, Path("/ql/data") / name, Path("/ql/data/scripts") / name):
            try:
                if candidate.is_file():
                    raw = candidate.read_text(encoding="utf-8-sig")
                    if raw.strip():
                        log("📂 已读取本地Cookie文件（不输出内容）")
                        break
            except (OSError, UnicodeError):
                continue
    accounts, seen = [], set()
    for row, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            remark, cookie = parse_cookie_line(line)
        except ValueError:
            log(f"⚠ 第{row}行有冲突的Cookie字段，已跳过；请把不同账号分别换行")
            continue
        if not cookie or len(cookie.split(";")) < 2:
            log(f"⚠ 第{row}行不是完整Cookie，已跳过；每行填写 备注#完整Cookie")
            continue
        canonical = ";".join(sorted(part.strip() for part in cookie.split(";") if part.strip()))
        if canonical in seen:
            continue
        seen.add(canonical)
        keys = {part.partition("=")[0].strip().lower() for part in cookie.split(";")}
        if keys <= {"_m_h5_tk", "_m_h5_tk_enc", "m_h5_tk", "m_h5_tk_enc"}:
            log(f"⚠ 第{row}行只有mtop签名token，缺少登录Cookie，已跳过")
            continue
        accounts.append((remark, cookie))
    return accounts


class MtopError(Exception):
    pass


class ElmAccount:
    """单账号：mtop 会话 + 业务编排。"""
    def __init__(self, remark: str, cookie: str) -> None:
        self.remark = ("账号" if "=" in remark or ";" in remark else remark[:32])
        self.cookie = cookie.strip()
        self.lat = float(cfg("ELM_LAT", "31.2304"))
        self.lng = float(cfg("ELM_LNG", "121.4737"))
        self.app_key = cfg("ELM_APPKEY", "12574478")
        self.gateway = cfg("ELM_GATEWAY", "https://waimai-guide.ele.me/h5/").rstrip("/") + "/"
        self.app_version = cfg("ELM_APP_VERSION", "12.8.8")
        self.task_times = int(cfg("ELM_TASK_TIMES", "7") or 7)
        self.session = requests.Session()
        self.proxies = self._proxies()
        self.result: dict[str, str] = {}
        self.risk_flagged = False
        self.stop_reason = ""
        #: 幸运星余额（跑之前 / 跑之后），用于日志里给每个号播报「现有多少币」
        self.star_start: int | None = None
        self.star_end: int | None = None
        # ★ 实测关键：必须伪装成微信小程序环境，否则所有写接口返回 405::行为受限
        self.appid = cfg("ELEME_APPID", "wxece3a9a4c82f58c9")
        self.uid = self._cookie_get("UTUSER") or self._cookie_get("userId")
        # 设备指纹：按账号独占、跨账号不复用、跨运行稳定（详见 alloc_device）。
        # key 优先用 munb/UTUSER/userId —— 可读且与其他项目（共用 fingerd 时）对得上，
        # 取不到才退回 Cookie 哈希。
        self.account_key = (self._cookie_get("munb") or self._cookie_get("UTUSER")
                            or self._cookie_get("userId")
                            or hashlib.md5(self.cookie.encode()).hexdigest()[:12])
        self.dev = alloc_device(self.account_key,
                               reset=os.environ.get("ELM_RESET_DEVICE") == "1")
        self.wechat_version = self.dev.get("wechatVersion") or cfg("ELM_WECHAT_VERSION", "8.0.58")
        self.utdid = self.dev.get("utdid", "") if STEALTH else ""
        self.hdr = {
            "User-Agent": self.dev["ua"] if STEALTH else
                          ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 Mobile/15E148 "
                           f"MicroMessenger/{self.wechat_version} miniProgram/{self.appid}"),
            "Referer": f"https://servicewechat.com/{self.appid}/831/page-frame.html",
            "Origin": "https://servicewechat.com",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "x-tap": "wx",
        }
        if STEALTH:
            # 真实小程序环境必带的设备/链路头（格式逆向自登录 CLI）。
            # 账号间 utdid 互不相同 —— 同一台机器多账号共用指纹是典型的关联风控特征。
            self.hdr.update({
                "x-utdid": self.utdid,
                "x-ele-ua": " ".join(filter(None, [
                    "RenderWay/miniProgram",
                    f"MiniAppId/{self.appid}",
                    f"MiniAppVersion/{self.app_version}",
                    f"DeviceId/{self.uid}" if self.uid else "",
                    "AppName/Wechat",
                    (f"Xiaomi/{self.dev['model']}/Android {self.dev['androidVersion']}"
                     if self.dev.get("os") == "android"
                     else "Apple/iPhone/iOS 17.0"),
                    f"Wechat/{self.wechat_version}",
                    "channel/wechat_app",
                    "subChannel/wechat_app.default",
                    f"Longitude/{self.lng:.4f}",
                    f"Latitude/{self.lat:.4f}",
                ])),
            })
            if self.uid:
                self.hdr["x-uid"] = self.uid
    def _proxies(self) -> dict | None:
        """
        解析代理环境变量，支持三种格式（逗号/换行分隔多条）：
          1. 长效代理：IP|端口|用户名|密码|过期日期
             如 223.15.244.28|9347|4363991|ab7ac092c|2026-10-23
          2. http://user:pass@ip:port  /  http://ip:port
          3. ip:port
        规则：
          · 过期条目自动剔除（打警告，绝不用过期代理跑请求）
          · 多条时按账号 cookie 哈希稳定选取（同账号永远同一出口 IP）
        """
        raw = cfg("ELM_PROXY") or cfg("LUCKYSTAR_PROXY") or cfg("PROXY") or cfg("GLOBAL_PROXY")
        if not raw or not raw.strip():
            return None
        entries = [e.strip() for e in re.split(r"[,\n]+", raw) if e.strip()]
        today = datetime.now().strftime("%Y-%m-%d")
        usable: list[str] = []
        for e in entries:
            parts = e.split("|")
            if len(parts) == 5:                          # 长效格式 IP|PORT|USER|PASS|EXPIRE
                ip, port, user, pwd, expire = (x.strip() for x in parts)
                if expire and expire < today:            # 字符串比较即日期比较
                    log(f"⚠ 代理 {ip}:{port} 已于 {expire} 过期，跳过")
                    continue
                e = f"http://{quote(user)}:{quote(pwd)}@{ip}:{port}"
            elif "://" not in e:
                e = "http://" + e
            usable.append(e)
        if not usable:
            log("⚠ 所有代理条目均已过期，本次直连运行")
            return None
        pick = usable[0] if len(usable) == 1 else \
            usable[int(hashlib.md5(self.cookie.encode()).hexdigest(), 16) % len(usable)]
        return {"http": pick, "https": pick}
    def _log(self, msg: str) -> None:
        log(f"【{self.remark}】{msg}")
    def _cookie_get(self, key: str) -> str:
        from urllib.parse import unquote                             # noqa: PLC0415
        m = re.search(rf"(?:^|;\s*){re.escape(key)}=([^;]*)", self.cookie)
        return unquote(m.group(1)) if m else ""

    @property
    def token(self) -> str:
        """mtop 签名 token = Cookie 里 _m_h5_tk 的下划线前段。"""
        m = re.search(r"(?:^|;\s*)_?m_h5_tk=([^;]+)", self.cookie)
        return m.group(1).split("_")[0] if m else ""
    def _update_cookie(self, set_cookie: str) -> bool:
        if not set_cookie:
            return False
        upd = []
        for k in ("_m_h5_tk", "_m_h5_tk_enc"):
            m = re.search(rf"{k}=([^;,\s]+)", set_cookie)
            if m:
                upd.append(f"{k}={m.group(1)}")
        if not upd:
            return False
        clean = "; ".join(part.strip() for part in self.cookie.split(";")
                          if part.strip() and not re.match(r"_m_h5_tk(?:_enc)?=", part.strip(), re.I))
        self.cookie = clean.rstrip("; ") + "; " + "; ".join(upd)
        return True

    @staticmethod
    def _grab_set_cookie(resp) -> str:
        """尽量把响应里所有 Set-Cookie 都收齐（requests 合并后常被截断）。"""
        parts = []
        try:
            parts.extend(resp.raw.headers.getlist("Set-Cookie"))
        except Exception:                                       # noqa: BLE001
            pass
        single = resp.headers.get("Set-Cookie", "")
        if single and single not in parts:
            parts.append(single)
        return "; ".join(parts)
    def prime_token(self) -> bool:
        if self.risk_flagged:
            return False
        if self.token or DRYRUN:
            return True
        self._log("当前Cookie不含mtop签名token，向官方查询接口申领一次")
        try:
            self.mtop(API["querytask"], "1.0", {"bizScene": BIZ_SCENE}, retry=2)
        except MtopError:
            pass
        if self.risk_flagged:
            return False
        if self.token:
            self._log("✅ 已取得mtop签名token（不输出token值）")
            return True
        self._log("❌ 未取得签名token，请从官方活动页面获取完整Cookie")
        return False
    def mtop(self, api: str, version: str, data: dict,
             extra_headers: dict | None = None, retry: int = 3,
             timeout: int = 15) -> dict:
        """POST + subVersion 5.0，自动处理 token 刷新与过期重试。"""
        if self.risk_flagged:
            raise MtopError("本账号已要求登录/用户验证，本轮停止请求")
        task_api = api in {API[k] for k in ("querytask", "receivetask", "receiveprize", "pageview", "trigger", "callback")}
        common = None
        if task_api:
            # 按请求自身的 bizScene 选身份：3112 用 H5 身份，ETMD_CHANNEL 用 Android。
            common = self.task_common_data(data.get("bizScene"))
            if api == API["callback"]:
                # 1.5.25：回调 data 只含 mapRules 字段（bizScene/token/
                # source/plat）。官方 H5 回调的 data JSON 不带
                # task_common_data 的 accountPlan/lat/lng/locationInfos/
                # platform/product/prodVersion/ttid；1.5.24 把它们并进
                # data，服务端报 400 参数校验出错。身份字段仍留在 query
                # 与签名里（common 照常用于 query）。
                pass
            else:
                data = {**common, **data}
                # Keep these platform/location fields identical across every task endpoint.
                for field in ("platform", "product", "prodVersion", "ttid", "longitude", "latitude", "locationInfos"):
                    data[field] = common[field]
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(retry):
            t = str(int(time.time() * 1000))
            query = {
                "jsv": "2.7.5",
                "appKey": self.app_key,
                "t": t,
                "sign": md5(f"{self.token}&{t}&{self.app_key}&{payload}"),
                "api": api,
                "v": version,
                "type": "originaljson",
                "dataType": "json",
                "data": payload,
                # aladdin 投放校验（App 原生环境注入，缺一不可）
                # 任务接口：query 与签名 data 必须用同一套身份（3112 用 H5 身份，
                # ETMD 用 Android）；非任务接口保持 Android。
                "platform": common["platform"] if task_api else "android",
                "product": "eleme",
                "prodVersion": common["prodVersion"] if task_api else self.app_version,
                "ttid": common["ttid"] if task_api else f"android@eleme_android_{self.app_version}",
            }
            # Fix 2026-11-17: aladdin 校验查的是 URL query，不是 body。
            # v1.5.3 把 platform/product/prodVersion/ttid 从 query 删掉、
            # 又不给 query 加 locationInfos，导致 querytask 报
            # CLIENT_PARAM_ALADDIN_DEVICEINFO_ERROR。恢复 v1.5.1 的做法：
            # 这 5 个参数必须留在 query 里，body 里的 task_common_data 照旧。
            if "locationInfos" in data or api == API["callback"]:
                query["locationInfos"] = json.dumps([{"lng": self.lng, "lat": self.lat}],
                                                    separators=(",", ":"))
            url = f"{self.gateway}{api}/{version}/5.0/?" + urlencode(query)

            if DRYRUN:
                self._log(f"[dryrun] {api} v{version}")
                self._log("         token/sign 已隐藏")
                self._log(f"         data={payload[:200]}")
                return {"ret": ["SUCCESS::dryrun"], "data": {"data": {}}}

            headers = dict(self.hdr)
            headers["Cookie"] = self.cookie
            if STEALTH:                     # 链路追踪 id 必须每次不同，否则一眼假
                headers["x-eleme-requestid"] = (
                    hashlib.sha1(os.urandom(16)).hexdigest().upper() + "|" + t)
            headers.update(extra_headers or {})
            try:
                resp = self.session.post(url, headers=headers, timeout=timeout, verify=True,
                                         proxies=self.proxies)
            except Exception as exc:                            # noqa: BLE001
                if attempt == retry - 1:
                    raise MtopError("网络请求失败，请检查网络/证书/代理配置") from None
                time.sleep(1.5 * (attempt + 1))
                continue

            refreshed = self._update_cookie(self._grab_set_cookie(resp))
            try:
                js = resp.json()
            except Exception:                                   # noqa: BLE001
                raise MtopError("服务端响应非JSON，可能需要在官方页面完成用户验证") from None
            if not isinstance(js, dict):
                raise MtopError("服务端响应结构异常")
            ret = js.get("ret")
            ret0 = ret[0] if isinstance(ret, list) and ret else ""
            if re.search(r"TOKEN_EXOIRED|TOKEN_EXPIRED|TOKEN_EMPTY", str(ret0), re.I):
                if refreshed:
                    time.sleep(0.8)
                    continue                                    # token 已刷新，重试
                raise MtopError(f"token 失效且未回传新 token：{ret0}")
            self.parse_ret(js)  # 在请求层识别验证错误，所有调用路径立即受保护
            return js
        raise MtopError("请求重试次数耗尽")
    def parse_ret(self, res: dict) -> dict:
        """识别用户验证/登录失效/风险；命中即中止当前账号，不尝试绕过验证。"""
        if not isinstance(res, dict) or not res:
            return {"ok": False, "msg": "空响应或响应格式错误", "data": {}}
        ret = res.get("ret")
        ret0 = ret[0] if isinstance(ret, list) and ret else str(ret or "")
        raw = res.get("data") or {}
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        out = {"ok": str(ret0).startswith("SUCCESS"), "msg": str(ret0), "data": data}
        if RISK_RE.search(str(ret0)):
            first = not self.risk_flagged
            self.risk_flagged = True
            self.stop_reason = str(ret0).split("::", 1)[0]
            if "USER_VALIDATE" in self.stop_reason:
                message = "⛔ 需要用户验证（FAIL_SYS_USER_VALIDATE）：请在官方页面完成登录/验证后重新获取完整Cookie"
            elif re.search(r"SESSION|NOT_LOGIN|LOGIN_REQUIRED|TOKEN_ILLEGAL", self.stop_reason):
                message = "⛔ 登录已失效，请在官方页面重新登录后更新完整Cookie"
            else:
                message = "⛔ 账号被平台限制，请在官方页面确认账号状态，本轮停止请求"
            self.result["Cookie"] = message
            if first:
                self._log(message)
                self._log("已停止本账号后续请求；不会继续签到、抽奖、任务或兑换")
        return out
    def _kj(self) -> dict:
        return {"bizScene": BIZ_SCENE, "version": PAGE_VERSION}
    def _loc(self) -> dict:
        return {"latitude": self.lat, "longitude": self.lng}
    def _mall_rows(self, target_id: str = "", target_name: str = "", quiet: bool = False) -> tuple[list, bool]:
        """查商城列表并解析成 rows[(name, ident, cost, ready, item)]。
        返回 (rows, truncated)：truncated 表示查满 20 页、列表可能不完整。
        异常直接抛出，由调用方记录真实原因。
        日志只打目标商品一行（未找到时打一行），其他商品不打印；
        quiet=True 时连目标行也不打（重试循环里每 2 秒重查用）。"""
        if self.risk_flagged:
            raise MtopError("已有账号风险标记，跳过商城")
        items, seen = [], set()
        truncated = False
        for page in range(1, 21):
            r = self.parse_ret(self.mtop(API["mall_list"], "1.0", {
                **self._kj(), **self._loc(), "propertyBizType": "STAR",
                "pageNo": page, "pageSize": 10}))
            if not r["ok"] or self.risk_flagged:
                raise MtopError("商城查询失败：" + str(r.get("msg", ""))[:80])
            batch = (r["data"] or {}).get("list") or []
            if not isinstance(batch, list):
                raise MtopError("商品列表格式不符")
            added = 0
            for it in batch:
                if not isinstance(it, dict):
                    continue
                ident = str(it.get("exchangeId") or "")
                key = ident or json.dumps(it, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
                added += 1
            if len(batch) < 10 or not added:
                break
        else:
            truncated = True
            self._log("⚠ 已查询至第20页，后续可能还有商品；本次仅展示，不兑换")

        def number(value):
            from decimal import Decimal, InvalidOperation
            try:
                value = Decimal(str(value))
                return value if value.is_finite() and value >= 0 else None
            except (InvalidOperation, TypeError, ValueError):
                return None

        rows = []
        for it in items:
            ex = it.get("exchangeInfo") or {}
            name = ((it.get("materialInfo") or {}).get("title")
                    or (it.get("rightMaterialInfoDTO") or {}).get("title")
                    or (it.get("bizExtInfo") or {}).get("exchangeIntroduction")
                    or "未命名商品")
            ident = str(it.get("exchangeId") or "").strip()
            cost = ex.get("consumeAmount")
            gap = number(ex.get("gapAmount", 0))
            status = str(ex.get("exchangeStatus") or "")
            ready = (status in ("INIT", "EXCHANGE_ABLE", "CAN_EXCHANGE", "ONLINE", "AVAILABLE")
                     and gap == 0 and number(cost) is not None and bool(ident))
            rows.append((str(name), ident, cost, ready, it))
        # 日志只打目标商品一行，其他商品不打印（以前每号打 9 行，看着太乱）。
        if target_id or target_name:
            target = _match_row(rows, target_id, target_name)
            if target is not None:
                t_name, t_ident, t_cost, _t_ready, t_it = target
                t_ex = t_it.get("exchangeInfo") or {}
                if not quiet:
                    self._log(f"🎯 目标={t_name} | ID={t_ident} | 所需星={t_cost if t_cost is not None else '未知'} | "
                              f"状态={str(t_ex.get('exchangeStatus') or '未知')} | 差星={t_ex.get('gapAmount', 0)}")
            else:
                if not quiet:
                    self._log(f"🎯 未找到目标商品（ID={target_id or '未指定'}，商城共 {len(rows)} 件）")
        return rows, truncated

    def mall(self, read_only: bool = False) -> None:
        """分页列商品；只读模式不兑换，自动兑换必须指定商品 ID 或完整名称。"""
        if self.risk_flagged:
            self.result["兑换"] = "⛔ 已有账号风险标记，跳过商城"
            return
        try:
            rows, truncated = self._mall_rows(cfg("ELM_EXCHANGE_ID"), cfg("ELM_EXCHANGE_NAME"))
        except Exception as e:
            self.result["兑换"] = "❌ 商城查询异常，未发起兑换"
            self._log(f"{self.result['兑换']}（{type(e).__name__}: {str(e)[:120]}）")
            return
        if truncated:
            read_only = True
        if read_only:
            self.result["兑换"] = f"➖ 仅查询，列出 {len(rows)} 件商品，未兑换"
            self._log(self.result["兑换"])
            return
        if cfg("ELM_EXCHANGE") != "1":
            self.result["兑换"] = f"➖ 已列出 {len(rows)} 件商品（自动兑换未开启）"
            return
        target_id = cfg("ELM_EXCHANGE_ID")
        target_name = cfg("ELM_EXCHANGE_NAME")
        if not target_id and not target_name:
            self.result["兑换"] = "⛔ 未指定 ELM_EXCHANGE_ID 或 ELM_EXCHANGE_NAME，跳过兑换"
            self._log(self.result["兑换"])
            return
        matches = [x for x in rows
                   if (not target_id or x[1] == target_id)
                   and (not target_name or _name_match(x[0], target_name))]
        if len(matches) != 1:
            self.result["兑换"] = ("➖ 未找到指定商品，未兑换其他商品" if not matches
                                   else "⛔ 商品名称匹配多件，请指定 ELM_EXCHANGE_ID")
            self._log(self.result["兑换"])
            return
        self.exchange_row(matches[0])

    def exchange_row(self, row, force: bool = False, quiet: bool = False) -> str:
        """对已锁定的目标商品直接发兑换请求（不重新查商城）。
        force=True 时跳过“当前不可兑换”状态检查（开抢用：10:00 前商品常为
        SECKILL_NOT_STARTED，是否已开售由服务端判定），其余检查照常。
        quiet=True 时不打每一次尝试的日志（重试循环用，最终结果由调用方汇总打一行）。
        返回结果字符串，同时写进 self.result["兑换"]。"""
        name, ident, cost, ready, it = row
        if not ready and not force:
            status = str((it.get("exchangeInfo") or {}).get("exchangeStatus") or "状态未知")
            self.result["兑换"] = f"➖ {name} 当前不可兑换（{status}），未兑换其他商品"
            return self.result["兑换"]
        try:
            limit = int(cfg("ELM_EXCHANGE_MAX", "1"))
        except ValueError:
            limit = 0
        if limit <= 0:
            self.result["兑换"] = "➖ ELM_EXCHANGE_MAX 未设为正整数，跳过兑换"
            return self.result["兑换"]
        if self.risk_flagged:
            self.result["兑换"] = "⛔ 账号风险标记，跳过兑换"
            return self.result["兑换"]
        required = ("exchangeActId", "exchangeCollectionId", "exchangeId", "actCode")
        if any(it.get(key) in (None, "") for key in required):
            self.result["兑换"] = "⛔ 目标商品缺少兑换参数，跳过兑换"
            return self.result["兑换"]
        if DRYRUN:
            self.result["兑换"] = f"➖ [dryrun] 目标={name}，ID={ident}，未兑换"
            return self.result["兑换"]
        try:
            rr = self.parse_ret(self.mtop(API["mall_exchange"], "1.0", {
                **self._kj(), **self._loc(), "actId": it["exchangeActId"],
                "exchangeCollectionId": it["exchangeCollectionId"],
                # 2026-11-22 官方兑换请求实测：必须带 source（业务来源），
                # 否则报 FAIL_BIZ_ILLEGAL_ARGUMENT「业务来源不能为空」。
                "source": "INTERACT_CENTER_EXCHANGE_MALL",
                "exchangeId": it["exchangeId"], "actCode": it["actCode"],
                "propertyBizType": "STAR",
            }, {"asac": ASAC["MALL_EXCHANGE"]}, retry=1, timeout=10))
        except Exception:
            self.result["兑换"] = "⚠ 兑换响应异常，结果未知，请到官方页面核实后再运行"
            if not quiet:
                self._log(self.result["兑换"])
            return self.result["兑换"]
        if rr["ok"]:
            self.result["兑换"] = f"✅ 已兑换：{name}（-{cost} 星），ID={ident}"
        else:
            self.result["兑换"] = "❌ 兑换未成功：" + str(rr.get("msg", ""))[:80]
        if not quiet:
            self._log(self.result["兑换"])
        return self.result["兑换"]

    def task_common_data(self, biz_scene: str | None = None) -> dict:
        """有效定位及设备信息放进签名业务data，保持locationInfos内层为JSON字符串。"""
        try:
            lat, lng = float(self.lat), float(self.lng)
            if not all(math.isfinite(v) for v in (lat, lng)) or not (-90 <= lat <= 90 and -180 <= lng <= 180) or lat == 0 or lng == 0:
                raise ValueError
        except (TypeError, ValueError):
            raise MtopError("任务查询需要有效非零定位；请用实际所在地区的ELM_LAT/ELM_LNG，不能填0、0") from None
        # 3112（interact_center）默认用官方 H5 身份：2026-11-17 实测官方页面
        # querytask 即用 platform=ios、prodVersion=1.4.27、ttid=H5@Web_iphone_1.4.27；
        # 旧版 Android 身份只能查到 23/24 项，缺 13 项。天天免单（ETMD_CHANNEL）
        # 保持 Android 身份（2602 已能正常查到）。ELM_TASK_H5=0 恢复旧版 Android。
        if (biz_scene or BIZ_SCENE) == BIZ_SCENE and cfg("ELM_TASK_H5", "1") != "0":
            platform, version, ttid = H5_TASK_PLATFORM, PAGE_VERSION, H5_TASK_TTID
        else:
            platform, version = "android", self.app_version
            ttid = f"android@eleme_android_{version}"
        loc = json.dumps({"lng": lng, "lat": lat}, separators=(",", ":"))
        return {"bizScene": biz_scene or BIZ_SCENE, "accountPlan": ACCOUNT_PLAN,
                "longitude": lng, "latitude": lat,
                "locationInfos": json.dumps([loc], separators=(",", ":")),
                "platform": platform, "product": "eleme", "prodVersion": version,
                "ttid": ttid}


def _parse(argv):
    out = {"id": "", "name": "", "account": "", "dryrun": False, "at": "", "wait": ""}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dryrun":
            out["dryrun"] = True
        elif a in ("--id", "--name", "--account", "--at", "--wait") and i + 1 < len(argv):
            out[a[2:]] = argv[i + 1]
            i += 1
        elif a.startswith("--id="):
            out["id"] = a[len("--id="):]
        elif a.startswith("--name="):
            out["name"] = a[len("--name="):]
        elif a.startswith("--account="):
            out["account"] = a[len("--account="):]
        elif a.startswith("--at="):
            out["at"] = a[len("--at="):]
        elif a.startswith("--wait="):
            out["wait"] = a[len("--wait="):]
        i += 1
    return out


def _wait_until(target_hhmm: str) -> None:
    """等到今天目标时刻（如 10:00）再开抢；已过目标时刻则立即返回。"""
    try:
        hh, mm = target_hhmm.strip().split(":")
        now = datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        log(f"⚠ 开抢时刻 {target_hhmm} 格式不对（应为 HH:MM），直接开抢")
        return
    if target <= datetime.now():
        return
    secs = (target - datetime.now()).total_seconds()
    if secs > 900:
        log(f"⏰ 距开抢时刻 {target_hhmm} 还有约 {int(secs // 60)} 分钟（超过 15 分钟），不等直接跑一次")
        return
    log(f"⏰ 已提前启动，等待至 {target_hhmm} 开抢（约 {int(secs)} 秒）")
    time.sleep(max(0, secs))


def _normalize(value) -> str:
    """完整名称匹配，仅忽略空白、英文字母大小写和全角字符差别。"""
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def _name_match(catalog_name, target_name) -> bool:
    """名称匹配：精确相等，或一方包含另一方。商城商品名前常带【周五免单日】这类
    前缀，用户只写“10点限量兑18元免单卡”也能对上；写得太短命中多件时，
    调用方按“匹配多件”拒绝，不会误兑。"""
    a = _normalize(catalog_name)
    b = _normalize(target_name)
    return a == b or b in a or a in b


def _match_row(rows, target_id, target_name):
    """AND 匹配：ID 和名称须同时指向同一商品（与 mall() 一致）；唯一匹配才返回行，否则 None。"""
    matches = [x for x in rows
               if (not target_id or x[1] == target_id)
               and (not target_name or _name_match(x[0], target_name))]
    return matches[0] if len(matches) == 1 else None


def _prepare(remark: str, cookie: str, target_id: str, target_name: str) -> dict:
    """开抢前预热：建账号对象（会话预热）、查一次商城列表、锁定目标商品参数。
    返回 {"remark", "acc", "row", "error"}。预热失败不致命，开抢时回退到
    现查商城再兑换的老路（会话仍是预热过的）。"""
    try:
        acc = ElmAccount(remark, cookie)
    except Exception as e:
        return {"remark": remark, "acc": None, "row": None,
                "error": f"❌ 账号对象创建失败：{type(e).__name__}"}
    try:
        if not acc.prime_token():
            return {"remark": remark, "acc": acc, "row": None, "error": "❌ 未取得签名token"}
        rows, _truncated = acc._mall_rows(target_id, target_name)
    except Exception as e:
        return {"remark": remark, "acc": acc, "row": None,
                "error": f"❌ 商城查询异常，未发起兑换（{type(e).__name__}: {str(e)[:80]}）"}
    row = _match_row(rows, target_id, target_name)
    if row is None:
        return {"remark": remark, "acc": acc, "row": None,
                "error": "➖ 未找到指定商品，未兑换其他商品"}
    name, ident, cost, ready, it = row
    ex = it.get("exchangeInfo") or {}
    status = str(ex.get("exchangeStatus") or "")
    gap = ex.get("gapAmount", 0)
    # 预热时目标常为未开售（SECKILL_NOT_STARTED / EXCHANGE_NOT_STARTED，开抢时刻才开售）：
    # 只要星够就缓存参数，开抢时直接发兑换请求，让服务端判定是否已开售。
    if ready or (status in ("SECKILL_NOT_STARTED", "EXCHANGE_NOT_STARTED") and (gap or 0) == 0 and ident and cost is not None):
        acc._log("预热完成，开抢时直接兑换")
        return {"remark": remark, "acc": acc, "row": row, "error": ""}
    return {"remark": remark, "acc": acc, "row": None,
            "error": f"➖ {name} 当前不可兑换（状态={status}，差星={gap}），未兑换其他商品"}


def _exchange_once(prepared: dict, deadline: float, target_id: str = "", target_name: str = "") -> str:
    """单个账号兑换。会话已预热、目标商品参数已缓存（row）时直接发兑换请求，
    每 2 秒重试至 deadline；row 为空时回退到静默重查商城再兑换（找到就缓存参数，
    之后直接发请求；重查时不打目标行）。
    预热已判死刑的 ➖（已结束/超限/差星/预热时未找到）直接返回，不空转重试；
    循环里“未找到”是开抢瞬间目录刷新造成的短暂现象，继续静默重试到 deadline；
    只有 ❌（服务端拒绝/查询异常，等开售或补货）和未开售的 ➖ 才重试。
    重试时只打一行“重试中”，最终结果由 main() 汇总打一行。"""
    acc = prepared.get("acc")
    if acc is None:
        return prepared.get("error") or "❌ 未发起兑换"
    row = prepared.get("row")
    last = prepared.get("error") or "❌ 未发起兑换"
    # 预热刚查过商城就判了 ➖：状态是终态（已结束/超限/差星/未找到），重试也不会变。
    # 例外是 EXCHANGE_NOT_STARTED：还没开兑，值得等一等。
    if last.startswith("➖") and "EXCHANGE_NOT_STARTED" not in last:
        return last
    noted_retry = False
    while True:
        try:
            if row is not None:
                last = acc.exchange_row(row, force=True, quiet=True)
            else:
                rows, _tr = acc._mall_rows(target_id, target_name, quiet=True)
                found = _match_row(rows, target_id, target_name)
                if found is None:
                    last = "➖ 未找到指定商品，未兑换其他商品"
                else:
                    row = found
                    last = acc.exchange_row(row, force=True, quiet=True)
            if acc.risk_flagged:
                return last
        except Exception as e:
            last = f"❌ 查询异常：{type(e).__name__}（不输出请求凭据）"
        # ⚠ 兑换响应异常 = 请求可能已到账但回包丢了，绝不自动重试（会重复扣星），须到官方页面核实。
        # ⛔ 是配置/参数问题，重试也不会变，同样直接返回。
        # ➖ 里只有未开售（SECKILL_NOT_STARTED / EXCHANGE_NOT_STARTED）值得等开售重试，其余终态直接返回。
        if DRYRUN or last.startswith(("✅", "⚠", "⛔")):
            return last
        if last.startswith("➖") and "SECKILL_NOT_STARTED" not in last and "EXCHANGE_NOT_STARTED" not in last and "未找到指定商品" not in last:
            return last
        if time.time() >= deadline:
            return last
        if not noted_retry:
            acc._log(f"⏳ 重试中：{last}")
            noted_retry = True
        time.sleep(2)


def main() -> int:
    global DRYRUN
    args = _parse(sys.argv[1:])
    target_id = args["id"] or os.environ.get("ELM_EXCHANGE_ID", "").strip()
    target_name = args["name"] or os.environ.get("ELM_EXCHANGE_NAME", "").strip()
    account = args["account"] or os.environ.get("ELM_EXCHANGE_ACCOUNT", "").strip()
    dryrun = args["dryrun"] or os.environ.get("ELM_EXCHANGE_DRYRUN", "") == "1"
    at = args["at"] or os.environ.get("ELM_EXCHANGE_AT", "").strip()
    try:
        wait_s = int(args["wait"] or os.environ.get("ELM_EXCHANGE_WAIT", "300") or 300)
    except ValueError:
        wait_s = 300
    if not target_id and not target_name:
        print("❌ 未指定目标商品：请用 --id <商品ID> / --name <完整名称>，"
              "或在青龙里建环境变量 ELM_EXCHANGE_ID / ELM_EXCHANGE_NAME")
        print("   商品 ID 请看任务 55「饿了么查商品ID」的日志（每小时更新）")
        return 1
    os.environ["ELM_EXCHANGE"] = "1"
    if target_id:
        os.environ["ELM_EXCHANGE_ID"] = target_id
    if target_name:
        os.environ["ELM_EXCHANGE_NAME"] = target_name
    if dryrun:
        DRYRUN = True
        os.environ["LUCKYSTAR_DRYRUN"] = "1"
    accounts = load_accounts()
    if not accounts:
        log("❌ 未读到完整账号；ELM_COOKIE每行一个账号：备注#完整Cookie")
        return 1
    if account:
        want = [a.strip() for a in account.replace("，", ",").split(",") if a.strip()]
        matched = [a for a in accounts if a[0] in want]
        if not matched:
            print(f"❌ 未找到备注为 {', '.join(want)} 的账号（现有备注："
                  + "、".join(a[0] for a in accounts) + "）")
            return 1
        accounts = matched
    print("===== 饿了么幸运星兑换（单独脚本，只兑指定商品） =====")
    print(f"目标：ID={target_id or '（未指定）'} 名称={target_name or '（未指定）'}"
          f" | 账号={', '.join(a[0] for a in accounts)}（{len(accounts)} 个，并发）"
          f" | 每号最多={os.environ.get('ELM_EXCHANGE_MAX', '1')} 件"
          f" | {'【演练，不真兑换】' if dryrun else '【真实兑换】'}"
          f" | 开抢={at or '立即'} 重试窗口={wait_s} 秒")
    # 设备指纹文件多线程同时读写会互相覆盖：账号对象创建时的指纹分配加锁串行。
    import threading
    _device_lock = threading.Lock()
    _orig_alloc = alloc_device

    def _locked_alloc(key, reset=False):
        with _device_lock:
            return _orig_alloc(key, reset)

    globals()["alloc_device"] = _locked_alloc
    # 预热（开抢前，并行）：建账号对象、查商城、锁定目标商品参数；会话预热后，
    # 10:00 直接发兑换请求，不再现查商城（冷会话首次请求慢，是之前 10:02 才
    # 开跑的原因之一）。预热失败的账号开抢时回退到现查商城再兑换。
    from concurrent.futures import ThreadPoolExecutor, as_completed
    prepared: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(accounts)) as ex:
        futs = {ex.submit(_prepare, remark, cookie, target_id, target_name): remark
                for remark, cookie in accounts}
        for fut in as_completed(futs):
            try:
                prepared.append(fut.result())
            except Exception as e:
                prepared.append({"remark": futs[fut], "acc": None, "row": None,
                                 "error": f"❌ 预热异常：{type(e).__name__}"})
    order = {remark: i for i, (remark, _) in enumerate(accounts)}
    prepared.sort(key=lambda p: order.get(p["remark"], 99))
    if at and not dryrun:
        _wait_until(at)
    deadline = time.time() + wait_s
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(prepared)) as ex:
        futs = {ex.submit(_exchange_once, p, deadline, target_id, target_name): p["remark"] for p in prepared}
        for fut in as_completed(futs):
            remark = futs[fut]
            try:
                msg = fut.result()
            except Exception:
                msg = "❌ 查询异常，请检查网络、依赖和账号配置（不输出请求凭据）"
            results[remark] = msg
            log(f"【{remark}】{msg}")
    failed = any(m.startswith(("❌", "⛔", "⚠")) for m in results.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
