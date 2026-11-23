#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_withdraw.py v1.2 — 酷狗概念版提现脚本（独立脚本，与做任务的 kgylgnb45w.py 分开）
================================================================================
抓包结论（2026-11-20 HAR + 提现页 JS 反查，全部实测/实读）：
  提现接口：POST https://gateway.kugou.com/yutc/youth/v1/withdraw/apply
  查询档位：GET  https://gateway.kugou.com/yutc/youth/v1/withdraw/levels
  余额查询：GET  https://gateway.kugou.com/yutc/youth/v1/user/info
  提现记录：GET  https://gateway.kugou.com/yutc/youth/v1/withdraw/history
  签名与做任务脚本同一套（appid 3114 / clientver 12520 / SALT / md5），
  请求头带 KG-DEVID + KG-CLIENTTIMEMS。
  网关 400 教训（2026-11-20 青龙实测，开始误判成参数问题，
  第二轮对照锁定真因）：路径必须带 /yutc 前缀，即
  /yutc/youth/v1/user/info、/yutc/youth/v1/withdraw/levels、
  /yutc/youth/v1/withdraw/apply；不带 /yutc 的 /youth/v1/... 一律 400。
  参数形态照 api()：from=client，不发 clienttoken。
  另：家里 IPv6 出不去而 DNS 先返 IPv6，每次请求白耗 50 秒，
  本脚本强制只走 IPv4。

  档位（levels 实测 2026-11-20）：
    3000 金币 = 0.3 元（首次提现专享，level_id 9999，仅新号/首提）
    5000 金币 = 0.5 元
    10000 金币 = 1 元
    50000 金币 = 5 元   <- 定时抢的目标档
    100000 金币 = 10 元
  汇率 10000 金币 = 1 元。每天 0:00 / 8:00 / 12:00 / 16:00 / 20:00 五个时段
  放额度（levels.times），前一时段提完后下一时段更新；打上“已兑完”=该时段
  没了，“今日已提完”=该档今日没了。
  规则（提现页说明截图 2026-11-20 15:21）：
    同一个账号每天最多提现 1 次；同一个设备每天最多提现 1 次。
    提现到微信（channel=3），要先在 App 里实名认证 + 绑定微信。

  申请体（提现页 JS 实读）：{openid, nickname, total_fee, coins,
  channel:3, level_id?}，其中 total_fee = 金额(元)x100，coins = 金额(元)x10000。
  openid/nickname 是绑定的微信信息，脚本自己拿不到（App 里 AES+RSA 加密取），
  须由人工填 KG_WITHDRAW_WX（见下）。

  提现滑块：点提现后服务端可能要求滑块验证（腾讯滑块，errcode 50019）。
  滑块只能人手在 App 里滑，脚本过不了。遇到 50019 脚本直接停下并明说，
  去 App 提现页滑一次后，当天内再跑脚本一般可直接提。

  v1.1（2026-11-21）：修 0 点场永远抢不到——23:57 预热读到的“今日已提”是
  昨天的旧数，脚本直接退出，没等到午夜重置。现改为：目标时段在明天时
  预热不看旧数，等午夜后轮询再按新次数决定；刚过 0 点 90 秒内读到的
  “已提完”先当重置未到、重试不等退出。
  v1.2（2026-11-21）：破火爆——31218/31209 时开抢后 15 秒内直接 0.5 秒重申请，
  不绕回查档位（16:00 场三号全在开抢 6 秒内撞火爆、档位几秒售罄，2 秒轮询
  只够试 3 次）；15 秒后回到正常轮询。

环境变量（全部写清楚）：
  KGGNB              做任务脚本同款账号变量（备注#token#userid#mid#dfid 或 KV 格式）
  KG_WITHDRAW_WX     必填（否则只查不提）：每号一行「备注#openid#nickname」
                     openid 获取：清 App 缓存后进提现页抓一次包，取 openid；
                     或把新 HAR 发给助理提取后填入。
  KG_WITHDRAW_COINS  目标档位金币数，默认 50000（5 元）。3000=0.3元首提，
                     5000=0.5元，10000=1元，100000=10元。
  KG_WITHDRAW_ACCOUNT 只提指定备注，逗号分隔；默认全部账号。
  KG_WITHDRAW_AT     定时抢的时刻 HH:MM；默认取下一个放额度时段
                     （0:00 / 8:00 / 12:00 / 16:00 / 20:00）。
  KG_WITHDRAW_WAIT   开抢后轮询秒数，默认 300（5 分钟）。
  KG_WITHDRAW_POLL   轮询间隔秒，默认 2。
  KG_WITHDRAW_DRYRUN 设为 1 = 只查不提（演练）。

用法：
  task kg_withdraw.py --list              查看：余额/档位ID/今日次数/提现记录
  task kg_withdraw.py --grab              定时抢（默认模式）：等到下一时段，轮询抢档
  task kg_withdraw.py --grab --coins 5000 --at 16:00 --dryrun
  task kg_withdraw.py --grab --account 8164

青龙定时（5 个时段，提前 3 分钟预热，等时段一到就抢）：
  57 7,11,15,19,23 * * *   命令：task kg_withdraw.py --grab
  每个账号独立线程并行抢；一个账号每天只提 1 次，提完即停。

安全红线：
  申请 POST 只发一次。响应异常/结果未知时绝不自动重试，须到 App「我的账单」核实。
  50019（要滑块）/ 31201、31205（今日已提）/ 31203（额度抢完）直接停，不重试。
"""
import os
import sys
import re
import json
import time
import gzip
import socket
import string
import hashlib
import tempfile
import threading
import argparse
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# 家里 IPv6 出不去（2026-11-20 实测：有公网 IPv6 但流量出不去），而 DNS 会先返回
# IPv6 地址；每个死 IPv6 连接要耗掉整个超时才轮到 IPv4，一次请求白白多 50 秒。
# 本脚本只走 IPv4（青龙容器内实测：不加这段每次请求 50 秒，加了 <2 秒）。
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(*args, **kwargs):
    return [x for x in _orig_getaddrinfo(*args, **kwargs) if x[0] == socket.AF_INET]


socket.getaddrinfo = _ipv4_only_getaddrinfo

SCRIPT_VERSION = "1.2"

# ---- 与 kgylgnb45w.py 同一套（已验证） ----
BASE = "https://gateway.kugou.com"
YOUTH_APPID = "3114"
YOUTH_CLIENTVER = "12520"
SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
UA = ("Mozilla/5.0 (Linux; Android 14; 2203121C Build/UKQ1.231003.002; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.0.0 "
      "Mobile Safari/537.36 KugouYouth")
TIMEOUT = 25
LOCK_FILE = os.path.join(tempfile.gettempdir(), "kg_withdraw.lock")
LOCK_TTL = 1800

# ---- 档位与时段 ----
SLOT_HOURS = [0, 8, 12, 16, 20]          # 每天放额度的 5 个时段（levels.times 一致）
DEFAULT_COINS = 50000                     # 默认抢 5 元档
DEFAULT_WAIT = 300                        # 开抢后轮询 300 秒
DEFAULT_POLL = 2                          # 轮询间隔 2 秒
BUSY_BURST_SECS = 15                       # 火爆密集重试窗口：开抢后 15 秒
BUSY_RETRY_SECS = 0.5                      # 火爆时直接重申请间隔（不绕回查档位）

# 申请返回码（提现页 JS 实读）
ERR_BUSY = ("31218", "31209")             # 活动火爆/请稍后重试：可重试到截止
ERR_NEED_VERIFY = "50019"                 # 要滑块验证：脚本停，去 App 滑
ERR_ALREADY = ("31201", "31205")          # 今日已提现：停
ERR_NO_QUOTA = "31203"                    # 今日额度已用完：停

_tls = threading.local()
_GLOBAL_ACC = {}
_log_lock = threading.Lock()


class _AccountProxy:
    def _current(self):
        acc = getattr(_tls, "acc", None)
        return acc if acc is not None else _GLOBAL_ACC

    def get(self, key, default=None):
        return self._current().get(key, default)

    def __getitem__(self, key):
        return self._current()[key]

    def __setitem__(self, key, value):
        self._current()[key] = value

    def __contains__(self, key):
        return key in self._current()

    def __repr__(self):
        return repr(self._current())


ACC = _AccountProxy()


def set_current_account(acc):
    _tls.acc = acc
    _GLOBAL_ACC.clear()
    _GLOBAL_ACC.update(acc)


def log(msg, tag=None):
    if tag is None:
        cur = getattr(_tls, "acc", None) or _GLOBAL_ACC
        tag = cur.get("remark") if cur else ""
    prefix = f" [{tag}]" if tag else ""
    line = f"[{time.strftime('%H:%M:%S')}]{prefix} {msg}"
    with _log_lock:
        print(line, flush=True)


def derive_mid(token):
    n = int(hashlib.md5(("kg-mid:" + str(token)).encode()).hexdigest(), 16)
    return str(n % (10 ** 39)).zfill(39)


def derive_dfid(token):
    h = hashlib.sha256(("kg-dfid:" + str(token)).encode()).hexdigest()
    pool = string.ascii_letters + string.digits
    return "".join(pool[int(h[i * 2:i * 2 + 2], 16) % len(pool)] for i in range(23))


RE_MID = re.compile(r"^\d{25,45}$")


def parse_accounts():
    """解析 KGGNB（与 kgylgnb45w.py 同格式）：备注#token#userid#mid#dfid 或 KV。"""
    raw = ""
    for k in ("KGGNB", "kggnb", "KGGNB_TASK", "kugou_token", "kwtx", "KWTX"):
        val = (os.environ.get(k) or "").strip()
        if val:
            raw = val
            break
    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n")
    chunks = [p.strip() for p in re.split(r"[&\n]+", raw) if p.strip() and not p.strip().startswith("#")]
    accounts, seen = [], set()
    for line in chunks:
        if "#" not in line and ("token=" in line or "userid=" in line):
            kv = {}
            for item in re.split(r"[;,]+", line):
                if "=" in item:
                    k, v = item.split("=", 1)
                    kv[k.strip().lower()] = v.strip()
            token, userid = kv.get("token", ""), kv.get("userid", "")
            if token and userid and (userid, token) not in seen:
                seen.add((userid, token))
                accounts.append({
                    "remark": kv.get("remark") or kv.get("nickname") or f"账号_{userid}",
                    "token": token, "userid": userid,
                    "mid": kv.get("mid", "") or derive_mid(token),
                    "dfid": kv.get("dfid", "") or derive_dfid(token),
                    "devid": kv.get("devid", ""),
                })
            continue
        parts = [x for x in (x.strip() for x in line.split("#")) if x]
        if len(parts) < 2:
            continue
        if len(parts[0]) == 32 and all(c in string.hexdigits for c in parts[0]) and len(parts) > 1 and parts[1].isdigit():
            parts = [f"账号_{parts[1]}"] + parts
        remark, token = parts[0], parts[1]
        acc = {"remark": remark, "token": token, "userid": "",
               "mid": "", "dfid": "", "devid": ""}
        rest = parts[2:]
        if len(rest) >= 2 and RE_MID.match(rest[1]):
            acc["userid"] = rest[0]
            acc["mid"] = rest[1]
            acc["dfid"] = rest[2] if len(rest) > 2 else ""
            acc["devid"] = rest[3] if len(rest) > 3 else ""
        else:
            acc["userid"] = rest[0] if rest else ""
            if len(rest) > 1 and RE_MID.match(rest[1]):
                acc["mid"] = rest[1]
                acc["dfid"] = rest[2] if len(rest) > 2 else ""
                acc["devid"] = rest[3] if len(rest) > 3 else ""
        if not acc["token"] or not acc["userid"] or acc["token"] in ("填写", "xxx"):
            continue
        acc["mid"] = acc["mid"] or derive_mid(acc["token"])
        acc["dfid"] = acc["dfid"] or derive_dfid(acc["token"])
        acc["remark"] = acc["remark"] or acc["userid"]
        if (acc["userid"], acc["token"]) in seen:
            continue
        seen.add((acc["userid"], acc["token"]))
        accounts.append(acc)
    return accounts


def parse_wx():
    """解析 KG_WITHDRAW_WX：每号一行「备注#openid#nickname」，返回 {remark: (openid, nickname)}。"""
    raw = (os.environ.get("KG_WITHDRAW_WX") or "").strip()
    out = {}
    for line in raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x for x in (x.strip() for x in line.split("#")) if x]
        if len(parts) >= 3 and parts[0] and parts[1]:
            out[parts[0]] = (parts[1], parts[2])
    return out


def make_sign(params, raw_body=None):
    keys = sorted(params.keys())
    joined = "".join(f"{k}={params[k]}" for k in keys)
    if raw_body:
        joined += raw_body
    return hashlib.md5((SALT + joined + SALT).encode("utf-8")).hexdigest()


def _request(url, raw, method):
    req = urllib.request.Request(url, data=(raw.encode("utf-8") if raw else None), method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if ACC.get("devid"):
        req.add_header("KG-DEVID", ACC["devid"])
    req.add_header("KG-CLIENTTIMEMS", str(int(time.time() * 1000)))
    req.add_header("Accept-Encoding", "gzip")
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            return json.loads(body.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                if e.headers.get("Content-Encoding") == "gzip":
                    b = gzip.decompress(b)
            except Exception:
                pass
            last_err = f"HTTP {e.code}: {b[:200].decode('utf-8', 'replace')}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt == 0:
            time.sleep(1.5)
    raise RuntimeError(f"{method} {url.split('?')[0]} 失败 -> {last_err}")


def api(path, params=None, data=None, method="GET"):
    p = {
        "appid": YOUTH_APPID,
        "clientver": YOUTH_CLIENTVER,
        "clienttime": str(int(time.time() * 1000)),
        "mid": ACC.get("mid", "") or "-",
        "uuid": ACC.get("mid", "") or "-",
        "dfid": ACC.get("dfid", "") or "-",
        "srcappid": "2919",
        "userid": ACC.get("userid", ""),
        "token": ACC.get("token", ""),
        "from": "client",
    }
    if params:
        p.update({k: str(v) for k, v in params.items() if v is not None})
    raw = None
    if data is not None:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    p["signature"] = make_sign(p, raw)
    url = BASE + "/yutc" + path + "?" + urllib.parse.urlencode(p)
    return _request(url, raw, method)


def resp_ok(r):
    return int((r or {}).get("status") or 0) == 1


def err_text(r):
    return str((r or {}).get("error") or (r or {}).get("errcode") or "")


# ============================================================================
#  提现接口（抓包 + JS 实读）
# ============================================================================
def get_account():
    """user/info：余额/现金/是否新号。"""
    r = api("/youth/v1/user/info", params={"h5": 1, "spec": 31})
    data = r.get("data") or {}
    a = data.get("account") or {}
    return {
        "balance_coins": int(a.get("balance_coins") or 0),
        "cash": float(a.get("cash") or 0),
        "total_coins": int(a.get("total_coins") or 0),
        "rate": int(data.get("coin_rmb_rate") or 10000),
        "has_withdrawn": bool((data.get("state") or {}).get("has_withdrawn")),
        "is_new_user": bool((data.get("state") or {}).get("is_new_user")),
    }


def get_levels():
    """withdraw/levels：档位列表 + 今日次数 + 放额度时段。"""
    r = api("/youth/v1/withdraw/levels", params={"appver": 1000})
    data = r.get("data") or {}
    levels = []
    for it in data.get("list") or []:
        coins = int(it.get("coins") or 0)
        levels.append({
            "coins": coins,
            "money": coins / 10000,
            "level_id": it.get("level_id"),
            "taskid": it.get("taskid"),
            "tag": it.get("tag") or "",
            "state": int(it.get("state") or 0),       # 1=有额度 0=该时段已兑完
            "can_apply": bool(it.get("can_apply")),
            "no_stock": bool(it.get("no_stock")),
            "type": it.get("type"),
        })
    hours = []
    for t in data.get("times") or []:
        try:
            hours.append(int(t.get("hour")))
        except Exception:
            pass
    return {
        "levels": levels,
        "day_apply_count": int(data.get("day_apply_count") or 0),
        "max_apply_count": int(data.get("max_apply_count") or 1),
        "hours": sorted(set(hours)) or list(SLOT_HOURS),
        "server_time": int(data.get("server_time") or time.time()),
        "limit_type": data.get("limit_type") or "",
    }


def get_history(limit=5):
    """withdraw/history：提现记录（返回结构未知，按最常见两种形态解析）。"""
    try:
        r = api("/youth/v1/withdraw/history")
    except Exception as e:  # noqa: BLE001
        return {"items": [], "error": str(e)}
    data = r.get("data")
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("list") or data.get("records") or []
    out = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        out.append({
            "money": it.get("money", it.get("amount", it.get("coins", ""))),
            "time": it.get("time", it.get("create_time", it.get("date", ""))),
            "status": it.get("status", it.get("state", it.get("desc", ""))),
        })
    return {"items": out, "error": ""}


def apply_withdraw(wx_openid, wx_nickname, level, rate=10000):
    """POST withdraw/apply：只发一次。返回 (ok, errcode, tip)。
    ok=True=成功；ok=False + errcode=响应码；ok=None=结果未知（网络异常，绝不重试）。"""
    money = level["coins"] / 10000
    body = {
        "openid": wx_openid,
        "nickname": wx_nickname,
        "total_fee": int(100 * money),
        "coins": int(money * rate),
        "channel": 3,  # 3=微信
    }
    if level.get("level_id") is not None:
        body["level_id"] = level["level_id"]
    if level.get("type") == 1:
        body["coin_level"] = -1
    try:
        r = api("/youth/v1/withdraw/apply", data=body, method="POST")
    except Exception as e:  # noqa: BLE001
        return None, "UNKNOWN", f"请求异常，结果未知：{e}（须去 App 我的账单核实，勿重复提交）"
    if resp_ok(r):
        data = r.get("data") or {}
        tip = data.get("tip") or "预计在1个工作日内到账"
        return True, "0", str(tip)
    errcode = str(r.get("errcode") or "")
    return False, errcode, err_text(r)


def fmt_level(lv):
    lid = f"ID={lv['level_id']}" if lv.get("level_id") is not None else "ID=-"
    tag = f" {lv['tag']}" if lv.get("tag") else ""
    st = "可提" if lv["can_apply"] else ("无库存" if lv["no_stock"] or lv["state"] == 0 else "不可提")
    return (f"{lv['coins']}金币={lv['money']:.2f}元 {lid} 状态={st}{tag}")


def next_slot_dt(hours, now=None):
    """下一个放额度时段（今天还有就今天，否则明天第一个时段）。"""
    now = now or datetime.now()
    for h in sorted(hours):
        dt = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if dt > now:
            return dt
    return (now + timedelta(days=1)).replace(hour=sorted(hours)[0], minute=0, second=0, microsecond=0)


def wait_until(dt, label=""):
    now = datetime.now()
    secs = (dt - now).total_seconds()
    if secs <= 0:
        return
    log(f"⏳ 等待{label} {dt.strftime('%H:%M')}（{int(secs)} 秒）")
    time.sleep(secs)


# ============================================================================
#  查看（--list）：余额 / 档位ID / 今日次数 / 提现记录
# ============================================================================
def do_list(acc):
    set_current_account(acc)
    try:
        bal = get_account()
    except Exception as e:  # noqa: BLE001
        log(f"❌ 余额查询失败：{e}")
        return {"remark": acc["remark"], "ok": False}
    try:
        lv = get_levels()
    except Exception as e:  # noqa: BLE001
        log(f"💰 余额 {bal['balance_coins']} 金币（≈{bal['cash']:.2f}元）| ❌ 档位查询失败：{e}")
        return {"remark": acc["remark"], "ok": False}
    log(f"💰 余额 {bal['balance_coins']} 金币（≈{bal['cash']:.2f}元）| 今日已提 "
        f"{lv['day_apply_count']}/{lv['max_apply_count']} 次 | "
        f"时段 {','.join(str(h) + ':00' for h in lv['hours'])}")
    for item in lv["levels"]:
        log(f"   档位 {fmt_level(item)}")
    nxt = next_slot_dt(lv["hours"])
    log(f"   下一时段 {nxt.strftime('%m-%d %H:%M')}")
    hist = get_history()
    if hist["error"]:
        log(f"   账单查询失败：{hist['error']}")
    elif hist["items"]:
        for it in hist["items"]:
            log(f"   账单 {it['money']} | {it['time']} | {it['status']}")
    else:
        log("   账单：暂无记录")
    return {"remark": acc["remark"], "ok": True}


# ============================================================================
#  定时抢（--grab）：每个账号一个线程，并行
# ============================================================================
def do_grab(acc, wx_map, target_coins, at_dt, wait_secs, poll_secs, dryrun):
    remark = acc["remark"]
    set_current_account(acc)
    wx = wx_map.get(remark)
    if not wx:
        log("❌ 未填微信绑定（KG_WITHDRAW_WX 缺少该备注#openid#nickname），跳过")
        return {"remark": remark, "ok": False, "reason": "no_wx"}
    openid, nickname = wx

    # 预热：余额 + 档位
    try:
        bal = get_account()
        lv = get_levels()
    except Exception as e:  # noqa: BLE001
        log(f"❌ 预热查询失败：{e}")
        return {"remark": remark, "ok": False, "reason": "prewarm_fail"}
    rate = bal["rate"] or 10000
    log(f"💰 余额 {bal['balance_coins']} 金币（≈{bal['cash']:.2f}元）| "
        f"今日已提 {lv['day_apply_count']}/{lv['max_apply_count']} 次")

    target = next((x for x in lv["levels"] if x["coins"] == target_coins), None)
    if not target:
        avail = "、".join(f"{x['coins']}金币" for x in lv["levels"])
        log(f"❌ 没有 {target_coins}金币档，现有档位：{avail}")
        return {"remark": remark, "ok": False, "reason": "no_level"}
    log(f"🎯 目标 {fmt_level(target)}")
    if bal["balance_coins"] < target_coins:
        log(f"❌ 余额不足：需 {target_coins} 金币，现有 {bal['balance_coins']}")
        return {"remark": remark, "ok": False, "reason": "low_balance"}

    # 目标时段：--at 优先，否则下一放额度时段
    slot_dt = at_dt or next_slot_dt(lv["hours"])
    # 跨午夜：目标时段在明天，预热时读到的“今日已提”是昨天的旧数，
    # 不能据此退出；等过了午夜再由轮询重新读数决定（2026-11-21 修，
    # 23:57 预热曾因旧数 1/1 直接退出，错过 0 点场）。
    cross_midnight = slot_dt.date() > datetime.now().date()

    def try_apply(level):
        """发一次申请，返回 (done, result)。done=True 表示本号结束（成功或 terminal）。"""
        if dryrun:
            log(f"🧪 演练：本应申请 {level['coins']}金币={level['money']:.2f}元（未发请求）")
            return True, {"remark": remark, "ok": True, "reason": "dryrun"}
        ok, errcode, tip = apply_withdraw(openid, nickname, level, rate)
        if ok is True:
            log(f"✅ 已提现 {level['money']:.2f}元（{level['coins']}金币）| {tip}")
            return True, {"remark": remark, "ok": True, "reason": "success"}
        if ok is None:
            log(f"⚠️ {tip}")
            return True, {"remark": remark, "ok": False, "reason": "unknown"}
        if errcode == ERR_NEED_VERIFY:
            log("🛑 要滑块验证（50019）：脚本过不了滑块，去 App 提现页滑一次，"
                "当天内再跑脚本一般可直接提")
            return True, {"remark": remark, "ok": False, "reason": "need_verify"}
        if errcode in ERR_ALREADY:
            log("🛑 今日已提现（31201/31205），明天再来")
            return True, {"remark": remark, "ok": False, "reason": "already_done"}
        if errcode == ERR_NO_QUOTA:
            log("🛑 今日额度已抢完（31203）")
            return True, {"remark": remark, "ok": False, "reason": "no_quota"}
        if errcode in ERR_BUSY:
            log(f"⏳ 活动火爆（{errcode}），稍后重试：{tip}")
            return False, None  # 可重试
        log(f"❌ 申请被拒 errcode={errcode}：{tip}")
        return True, {"remark": remark, "ok": False, "reason": f"err_{errcode}"}

    # 档位现成就有，直接提（提的是今天的次数，先看今天次数）
    if target["can_apply"] and target["state"] == 1:
        if lv["day_apply_count"] >= lv["max_apply_count"]:
            log("🛑 今日已提完，明天再来")
            return {"remark": remark, "ok": False, "reason": "already_done"}
        _, res = try_apply(target)
        return res

    # 要等时段：同日时段先看今天次数；跨午夜时段不看旧数，等重置后轮询再定
    if not cross_midnight and lv["day_apply_count"] >= lv["max_apply_count"]:
        log("🛑 今日已提完，明天再来")
        return {"remark": remark, "ok": False, "reason": "already_done"}
    if cross_midnight:
        log(f"🌙 目标时段 {slot_dt.strftime('%m-%d %H:%M')} 在明天，预热次数是昨天的旧数，"
            f"先等午夜重置，轮询时再按新次数决定")

    # 等时段
    wait_until(slot_dt, label="下一时段")
    deadline = slot_dt + timedelta(seconds=wait_secs)
    log(f"🔫 开抢：目标 {target['coins']}金币，轮询到 {deadline.strftime('%H:%M:%S')}")
    # 跨午夜：刚过 0 点的 90 秒内读到的“已提完”可能是重置还没到，
    # 先重试不退出；90 秒后仍已提完才是真的。
    grace_until = slot_dt + timedelta(seconds=90) if cross_midnight else None
    while True:
        now = datetime.now()
        if now >= deadline:
            log(f"❌ 未抢到：{target['coins']}金币档在 {wait_secs} 秒内一直无库存")
            return {"remark": remark, "ok": False, "reason": "timeout"}
        try:
            lv2 = get_levels()
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ 档位查询失败：{e}，{poll_secs} 秒后重试")
            time.sleep(poll_secs)
            continue
        if lv2["day_apply_count"] >= lv2["max_apply_count"]:
            if grace_until is not None and now < grace_until:
                log(f"🌙 刚过午夜，次数还没重置（{lv2['day_apply_count']}/"
                    f"{lv2['max_apply_count']}），{poll_secs} 秒后重试")
                time.sleep(poll_secs)
                continue
            log("🛑 今日已提完")
            return {"remark": remark, "ok": False, "reason": "already_done"}
        cur = next((x for x in lv2["levels"] if x["coins"] == target_coins), None)
        if cur is None:
            log("❌ 档位消失，停止")
            return {"remark": remark, "ok": False, "reason": "no_level"}
        if cur["can_apply"] and cur["state"] == 1:
            done, res = try_apply(cur)
            if done:
                return res
            # 31218/31209 火爆：开抢后 15 秒内直接 0.5 秒重申请，
            # 不绕回查档位（档位在关键窗口几秒就没，得抢时间）；
            # 15 秒后回到正常轮询
            burst_until = slot_dt + timedelta(seconds=BUSY_BURST_SECS)
            while datetime.now() < burst_until and datetime.now() < deadline:
                time.sleep(BUSY_RETRY_SECS)
                done, res = try_apply(cur)
                if done:
                    return res
        time.sleep(poll_secs)


# ============================================================================
#  主入口
# ============================================================================
def acquire_lock():
    try:
        if os.path.exists(LOCK_FILE):
            age = time.time() - os.path.getmtime(LOCK_FILE)
            if age < LOCK_TTL:
                return False
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def parse_args(argv):
    ap = argparse.ArgumentParser(description="酷狗概念版提现脚本 v" + SCRIPT_VERSION)
    ap.add_argument("--list", action="store_true", help="查看：余额/档位ID/今日次数/提现记录")
    ap.add_argument("--grab", action="store_true", help="定时抢（默认模式）")
    ap.add_argument("--coins", type=int, default=None, help="目标档位金币数（默认看 KG_WITHDRAW_COINS，未设则 50000）")
    ap.add_argument("--at", default=None, help="抢购时刻 HH:MM（默认下一放额度时段）")
    ap.add_argument("--account", default=None, help="只跑指定备注，逗号分隔")
    ap.add_argument("--wait", type=int, default=None, help="开抢后轮询秒数（默认 300）")
    ap.add_argument("--poll", type=int, default=None, help="轮询间隔秒（默认 2）")
    ap.add_argument("--dryrun", action="store_true", help="只查不提（演练）")
    return ap.parse_args(argv)


def env_int(name, default):
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except Exception:
        return default


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    print(f"===== 酷狗概念版提现 v{SCRIPT_VERSION} =====")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]")

    accounts = parse_accounts()
    if not accounts:
        print("❌ KGGNB 为空或解析失败，先填账号变量")
        return 2
    only = set()
    if args.account:
        only = {x.strip() for x in args.account.split(",") if x.strip()}
    elif os.environ.get("KG_WITHDRAW_ACCOUNT"):
        only = {x.strip() for x in os.environ["KG_WITHDRAW_ACCOUNT"].split(",") if x.strip()}
    if only:
        accounts = [a for a in accounts if a["remark"] in only]
    if not accounts:
        print("❌ 指定账号不在 KGGNB 里")
        return 2
    print(f"账号 {len(accounts)} 个：{'、'.join(a['remark'] for a in accounts)}")

    if args.list:
        results = []
        with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
            futs = {pool.submit(do_list, acc): acc for acc in accounts}
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    results.append({"remark": futs[fut]["remark"], "ok": False})
        ok_n = sum(1 for r in results if r.get("ok"))
        print(f"===== 查看完成：{ok_n}/{len(results)} 个账号正常 =====")
        return 0 if ok_n else 1

    # grab 模式
    if not acquire_lock():
        print("❌ 已有实例在运行，本次退出")
        return 2
    try:
        target_coins = args.coins or env_int("KG_WITHDRAW_COINS", DEFAULT_COINS)
        wait_secs = args.wait if args.wait is not None else env_int("KG_WITHDRAW_WAIT", DEFAULT_WAIT)
        poll_secs = args.poll if args.poll is not None else env_int("KG_WITHDRAW_POLL", DEFAULT_POLL)
        dryrun = args.dryrun or (os.environ.get("KG_WITHDRAW_DRYRUN") or "").strip() == "1"
        at_raw = args.at or (os.environ.get("KG_WITHDRAW_AT") or "").strip()
        at_dt = None
        if at_raw:
            try:
                hh, mm = [int(x) for x in at_raw.split(":")]
                now = datetime.now()
                at_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if at_dt <= now:
                    at_dt = None  # 已过时刻：不等，直接按“现查现抢”走
                    print(f"时刻 {at_raw} 已过，直接现查现抢")
            except Exception:
                print(f"❌ --at/{at_raw} 格式不对，用 HH:MM")
                return 2
        wx_map = parse_wx()
        print(f"目标档位 {target_coins} 金币（={target_coins / 10000:.2f}元）| "
              f"轮询 {wait_secs} 秒/间隔 {poll_secs} 秒 | "
              f"{'演练（不发申请）' if dryrun else '真实提现'} | "
              f"多线程 {len(accounts)} 个账号并行")
        if not wx_map:
            print("⚠️ KG_WITHDRAW_WX 未填：所有账号都会因缺 openid 跳过，"
                  "填好「备注#openid#nickname」后再跑")
        results = []
        with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
            futs = {pool.submit(do_grab, acc, wx_map, target_coins,
                                at_dt, wait_secs, poll_secs, dryrun): acc
                    for acc in accounts}
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    acc = futs[fut]
                    log(f"❌ 线程异常：{e}", tag=acc["remark"])
                    results.append({"remark": acc["remark"], "ok": False, "reason": "exception"})
        by_remark = {r["remark"]: r for r in results}
        ok_list = [r["remark"] for r in results if r.get("ok")]
        fail_list = [f"{r['remark']}({r.get('reason', '')})" for r in results if not r.get("ok")]
        print("===== 汇总 =====")
        if ok_list:
            print(f"✅ 成功：{'、'.join(ok_list)}")
        if fail_list:
            print(f"❌ 未成：{'、'.join(fail_list)}")
        return 0 if ok_list else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
