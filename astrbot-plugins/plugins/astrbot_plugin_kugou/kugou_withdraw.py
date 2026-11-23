"""酷狗提现（KGGNB）：复用 kg_withdraw.py v1.2 的抢兑逻辑 + 机器人调度。

kg_withdraw.py 是青龙里的独立提现脚本（任务 246 每天 5 个时段跑它）。
这里不重新推导逻辑，只把它包一层给机器人用：

- load_accounts：读青龙 KGGNB + KG_WITHDRAW_WX（只读），用脚本原生的
  parse_accounts / parse_wx 解析，顺序 = KGGNB 行顺序（= 登录顺序）。
- check_accounts：只读查余额 / 档位 / 今日次数（--list 的等价物），
  决定哪些号本场可提、哪些跳过（已提完 / 余额不足 / 未填微信）。
- grab_accounts：调脚本原生的 do_grab（预热 → 等时段 → 开抢轮询；
  31218/31209 火爆时开抢后 15 秒内 0.5 秒重申请，15 秒后回正常轮询；
  跨午夜 0 点场有 90 秒重置宽限）。每账号一个线程并行。

时区注意：kg_withdraw.py 全用 naive 的 datetime.now()，写的时候默认
跑在北京时间主机上（青龙容器）。机器人跑在 UTC 的云 VM 上，naive
now() 会晚 8 小时——0 点场的跨午夜判断会错（预热读到昨天 1/1 就直接
退出，错过 0 点场，正是 v1.1 修过的那个 bug）。所以这里只给这个模块
打一个补丁：把模块里的 datetime 换成北京时间 naive 版（_BeijingDatetime），
不碰机器人其他代码（定时兑换那边已经是 Asia/Shanghai aware 时间，不受影响）。

安全红线（与脚本一致）：apply_withdraw 只发一次；响应异常/结果未知
绝不自动重试；50019（要滑块）/ 31201、31205（今日已提）/ 31203
（额度抢完）直接停，不重试。机器人只在用户明确选了「立即提现」
或到了已安排的定时场次时才调用 grab_accounts。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

BEIJING = ZoneInfo("Asia/Shanghai")

# 每天放额度的 5 个时段（与 levels.times 一致，北京时间）。
SLOT_HOURS = [0, 8, 12, 16, 20]
# 开抢前多少秒预热（任务 246 的 cron 是 :57，即提前 3 分钟）。
PREHEAT_SECONDS = 180
# 过了场次开始多少秒还可补跑（与 do_grab 默认轮询 300 秒一致）。
CATCHUP_SECONDS = 300
DEFAULT_COINS = 50000
# 提现档位（金币数），按序号 1-5 选：3000=0.3元首提、5000=0.5元、
# 10000=1元、50000=5元、100000=10元。
TIERS = (3000, 5000, 10000, 50000, 100000)
DEFAULT_WAIT = 300
DEFAULT_POLL = 2

KG_WITHDRAW_WX_ENV = "KG_WITHDRAW_WX"
KG_WITHDRAW_COINS_ENV = "KG_WITHDRAW_COINS"
KG_WITHDRAW_WAIT_ENV = "KG_WITHDRAW_WAIT"
KG_WITHDRAW_POLL_ENV = "KG_WITHDRAW_POLL"

_KG_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # vendored kg_withdraw.py lives beside this module
_KG_ENV_NAMES = ("KGGNB", "kggnb", "KGGNB_TASK", "kugou_token", "kwtx", "KWTX")


def now_beijing() -> datetime:
    """现在（北京时间，带时区）。"""
    return datetime.now(BEIJING)


def _as_beijing(dt: datetime | None) -> datetime | None:
    """naive 时间按北京时间理解；aware 时间转成北京时间。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING)


class _BeijingDatetime(_dt.datetime):
    """naive now() 返回北京时间的 datetime 子类。

    只给 kg_withdraw 模块用：它内部的 next_slot_dt / wait_until /
    do_grab 全调模块全局的 datetime.now()，换成这个类后它们都在北
    京 naive 时间里算，0 点场的跨午夜判断（slot_dt.date() >
    now.date()）才正确。带 tz 参数的 now(tz) 保持标准行为。
    """

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - signature mirrors datetime.now
        if tz is None:
            return _dt.datetime.now(_dt.timezone.utc).astimezone(
                BEIJING).replace(tzinfo=None)
        return _dt.datetime.now(tz)


def _load_script():
    """Import kg_withdraw.py (v1.2) once; None when it cannot be imported."""
    if _KG_SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _KG_SCRIPT_DIR)
    try:
        import kg_withdraw as module
    except Exception:  # noqa: BLE001 - 脚本缺失/损坏都按不可用处理
        log.exception("酷狗提现脚本导不进来")
        return None
    # 时区补丁：只换这个模块的 datetime（见模块头注释）。
    module.datetime = _BeijingDatetime
    return module


class KugouWithdrawUnavailable(Exception):
    """The kg_withdraw.py script is missing or cannot be imported."""


def read_kg_env(qinglong: Any, name: str) -> str:
    """Read one env var's value from Qinglong (read-only, no writes).

    Both QinglongClient and QinglongCliClient expose list_envs(); the
    wrapper stays client-agnostic. Returns "" when the var is absent.
    """
    if qinglong is None:
        return ""
    for env in qinglong.list_envs():
        if str(env.get("name") or "") == name:
            return str(env.get("value") or "")
    return ""


def read_kg_env_int(qinglong: Any, name: str, default: int) -> int:
    """Read an int env var from Qinglong (read-only); default on bad value."""
    raw = read_kg_env(qinglong, name).strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def display_remark(remark: str) -> str:
    """Account label for the menu: strip the auto-generated 账号_ prefix."""
    remark = (remark or "").strip()
    if remark.startswith("账号_"):
        return remark[len("账号_"):]
    return remark or "未知账号"


def parse_account_selection(text: str, count: int) -> tuple[list[int] | None, str | None]:
    """Parse the account-pick reply (same convention as the exchange flow).

    "0" -> all indices; "1,2" -> those; anything else -> (None, error).
    Returns (indices, None) or (None, error message).
    """
    parts = [p.strip() for p in (text or "").strip().split(",") if p.strip()]
    if not parts or not all(p.isdigit() for p in parts):
        return None, "请回复序号（可多选，用逗号分隔，如 1,2），或回复 q 退出。"
    idxs = [int(p) for p in parts]
    if 0 in idxs:
        return list(range(count)), None
    if not all(1 <= i <= count for i in idxs):
        return None, f"请回复 0~{count}，或回复 q 退出。"
    return [i - 1 for i in idxs], None


def next_window(hours: list[int], now: datetime | None = None) -> tuple[datetime, datetime]:
    """Next withdrawal window (Beijing aware) and its preheat fire time.

    If a window started within the last CATCHUP_SECONDS, that window is
    still the next one (late start: the grab's poll loop still has its
    remaining time). Otherwise the next slot strictly after now, or
    tomorrow's first slot.
    """
    now = _as_beijing(now or now_beijing())
    hours = sorted(hours)
    today = [now.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in hours]
    for slot in reversed(today):
        if timedelta(0) <= now - slot <= timedelta(seconds=CATCHUP_SECONDS):
            return slot, slot - timedelta(seconds=PREHEAT_SECONDS)
    for slot in today:
        if slot > now:
            return slot, slot - timedelta(seconds=PREHEAT_SECONDS)
    first = (now + timedelta(days=1)).replace(
        hour=hours[0], minute=0, second=0, microsecond=0)
    return first, first - timedelta(seconds=PREHEAT_SECONDS)


def previous_window(hours: list[int], now: datetime | None = None) -> datetime:
    """Most recent withdrawal window at or before now (Beijing aware).

    Used by the scheduler to notice a missed window: when the bot
    comes back more than CATCHUP_SECONDS after a window started, the
    next_window() above already points at the FOLLOWING window, so the
    missed one is only visible here.
    """
    now = _as_beijing(now or now_beijing())
    hours = sorted(hours)
    today = [now.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in hours]
    past = [s for s in today if s <= now]
    if past:
        return past[-1]
    return (now - timedelta(days=1)).replace(
        hour=hours[-1], minute=0, second=0, microsecond=0)


def window_key(window: datetime) -> str:
    """Stable id for one window, e.g. '2026-11-21 16:00' (Beijing)."""
    return _as_beijing(window).strftime("%Y-%m-%d %H:%M")


def format_window(window: datetime, now: datetime | None = None) -> str:
    """「今天 16:00」/「明天 00:00」这种给用户看的场次时间（北京时间）。"""
    window = _as_beijing(window)
    now = _as_beijing(now or now_beijing())
    day = "今天" if window.date() == now.date() else "明天"
    return f"{day} {window.strftime('%H:%M')}"


def format_windows(now: datetime | None = None) -> str:
    """「0:00 / 8:00 / 12:00 / 16:00 / 20:00」."""
    return " / ".join(f"{h}:00" for h in SLOT_HOURS)


def skip_reason(check: dict) -> str | None:
    """Why this account is skipped this window (None = eligible)."""
    if not check.get("ok"):
        return f"查询失败（{check.get('error') or '未知错误'}）"
    if not check.get("has_wx"):
        return "未填微信绑定（KG_WITHDRAW_WX 缺少该备注#openid#nickname）"
    if check.get("day_apply_count", 0) >= check.get("max_apply_count", 1):
        return "今日已提完"
    if check.get("target") is None:
        return f"没有 {check.get('coins')}金币档"
    if (check.get("balance_coins") or 0) < (check.get("coins") or 0):
        return f"余额不足（{check.get('balance_coins')} 金币）"
    return None


class KugouWithdraw:
    """Thin wrapper over kg_withdraw.py v1.2 (injectable _api for tests)."""

    def __init__(self, api: Any = None):
        self._api = api if api is not None else _load_script()

    @property
    def available(self) -> bool:
        return self._api is not None

    def _require(self) -> Any:
        if self._api is None:
            raise KugouWithdrawUnavailable(
                "酷狗提现脚本不在或导不进来，请检查 "
                "~/workspace/your_files/kg_withdraw.py 后重试。")
        return self._api

    # -- accounts ---------------------------------------------------------
    def load_accounts(self, kggnb_value: str, wx_value: str) -> list[dict]:
        """Parse KGGNB + KG_WITHDRAW_WX into account dicts (logged-in order).

        Uses the script's own parse_accounts / parse_wx (temp env vars,
        restored afterwards). Each account gets "wx" = (openid, nickname)
        or None.
        """
        api = self._require()
        saved = {k: os.environ.get(k)
                 for k in _KG_ENV_NAMES + (KG_WITHDRAW_WX_ENV,)}
        try:
            for k in _KG_ENV_NAMES:
                os.environ.pop(k, None)
            os.environ["KGGNB"] = kggnb_value or ""
            os.environ[KG_WITHDRAW_WX_ENV] = wx_value or ""
            accounts = api.parse_accounts()
            wx_map = api.parse_wx()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for acc in accounts:
            acc["wx"] = wx_map.get(acc.get("remark"))
        return accounts

    # -- read-only check (--list equivalent) -------------------------------
    def check_accounts(self, accounts: list[dict], coins: int) -> list[dict]:
        """Per-account balance / levels / today's count (read-only).

        Parallel, one thread per account (same as the script's --list).
        Never submits a withdrawal.
        """
        api = self._require()
        if not accounts:
            return []

        def _check_one(acc: dict) -> dict:
            remark = acc.get("remark") or ""
            base = {"remark": remark, "userid": acc.get("userid") or "",
                    "coins": coins, "ok": False,
                    "has_wx": bool(acc.get("wx"))}
            api.set_current_account(acc)
            try:
                bal = api.get_account()
                lv = api.get_levels()
            except Exception as exc:  # noqa: BLE001 - 单号查不到不拦整体
                base["error"] = str(exc)
                return base
            target = next((x for x in lv.get("levels") or []
                           if x.get("coins") == coins), None)
            base.update({
                "ok": True,
                "balance_coins": int(bal.get("balance_coins") or 0),
                "cash": float(bal.get("cash") or 0),
                "day_apply_count": int(lv.get("day_apply_count") or 0),
                "max_apply_count": int(lv.get("max_apply_count") or 1),
                "target": target,
                "hours": lv.get("hours") or list(SLOT_HOURS),
            })
            return base

        with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
            results = list(pool.map(_check_one, accounts))
        # Keep the account order (KGGNB line order = logged-in order).
        order = {a.get("remark"): i for i, a in enumerate(accounts)}
        results.sort(key=lambda r: order.get(r["remark"], 0))
        return results

    # -- grab (v1.2 do_grab, verbatim) --------------------------------------
    def grab_accounts(self, accounts: list[dict], coins: int,
                      at_dt: datetime | None, wait_secs: int = DEFAULT_WAIT,
                      poll_secs: int = DEFAULT_POLL,
                      dryrun: bool = False) -> list[dict]:
        """Run do_grab for each account in parallel (one thread each).

        at_dt is the target window in Beijing naive time (the module's
        patched datetime frame) — None means "next slot" (do_grab's own
        next_slot_dt). For an immediate attempt pass now (Beijing naive):
        the tier is applied at once when available, else polled for
        wait_secs. Returns do_grab's result dicts (remark/ok/reason).
        """
        api = self._require()
        if not accounts:
            return []
        wx_map = {a["remark"]: a["wx"] for a in accounts if a.get("wx")}
        naive_at = None
        if at_dt is not None:
            naive_at = _as_beijing(at_dt).replace(tzinfo=None)

        def _grab_one(acc: dict) -> dict:
            try:
                return api.do_grab(acc, wx_map, coins, naive_at,
                                   wait_secs, poll_secs, dryrun)
            except Exception as exc:  # noqa: BLE001 - 单号异常不拦整体
                return {"remark": acc.get("remark") or "",
                        "ok": False, "reason": f"exception: {exc}"}

        with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
            futs = {pool.submit(_grab_one, acc): acc for acc in accounts}
            results = []
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    results.append({"remark": futs[fut].get("remark") or "",
                                    "ok": False,
                                    "reason": f"exception: {exc}"})
        order = {a.get("remark"): i for i, a in enumerate(accounts)}
        results.sort(key=lambda r: order.get(r["remark"], 0))
        return results


def format_account_list(accounts: list[dict], checks: list[dict],
                        coins: int) -> str:
    """The withdrawal account menu (exchange list convention)."""
    by_remark = {c["remark"]: c for c in checks}
    lines = ["=====酷狗提现====="]
    for i, acc in enumerate(accounts, start=1):
        remark = acc.get("remark") or ""
        check = by_remark.get(remark) or {}
        label = display_remark(remark)
        if not check.get("ok"):
            tail = "💰 余额未知"
        else:
            tail = (f"💰 {check.get('balance_coins')} 金币"
                    f"（≈{check.get('cash', 0):.2f}元）"
                    f" | 今日已提 {check.get('day_apply_count')}"
                    f"/{check.get('max_apply_count')} 次")
        skip = skip_reason(check) if check else "查询失败"
        if skip:
            tail += f" ⏭️ {skip}"
        lines.append(f"[{i}] 📱 {label} {tail}")
    lines += ["------------------",
              "[0] 全部账号",
              "回复序号选择账号（可多选，用逗号分隔，如 1,2），回复\"q\"退出",
              "回复 t 查看已安排的定时提现",
              "=================="]
    return "\n".join(lines)


def format_window_result(window: datetime | None, results: list[dict],
                         skipped: list[tuple[str, str]]) -> str:
    """The per-window result message (which accounts won/lost and why).

    window=None means an immediate attempt (not a scheduled window).
    """
    if window is None:
        lines = ["⏰ 酷狗立即提现结果："]
    else:
        lines = [f"⏰ 酷狗提现 {format_window(window)}场结果："]
    for res in results:
        remark = display_remark(res.get("remark") or "")
        reason = res.get("reason") or ""
        if res.get("ok"):
            lines.append(f"✅ {remark}：已提现")
        elif reason == "already_done":
            lines.append(f"⏭️ {remark}：今日已提完")
        elif reason == "no_wx":
            lines.append(f"❌ {remark}：未填微信绑定")
        elif reason == "low_balance":
            lines.append(f"❌ {remark}：余额不足")
        elif reason == "no_level":
            lines.append(f"❌ {remark}：没有该档位")
        elif reason == "no_quota":
            lines.append(f"❌ {remark}：今日额度已抢完")
        elif reason == "need_verify":
            lines.append(f"🛑 {remark}：要滑块验证，去 App 提现页滑一次")
        elif reason == "timeout":
            lines.append(f"❌ {remark}：档位一直无库存，未抢到")
        elif reason == "unknown":
            lines.append(f"⚠️ {remark}：申请结果未知，去 App 我的账单核实，勿重复提交")
        elif reason == "dryrun":
            lines.append(f"🧪 {remark}：演练，未发申请")
        else:
            lines.append(f"❌ {remark}：{reason or '失败'}")
    for remark, why in skipped:
        lines.append(f"⏭️ {display_remark(remark)}：{why}（本场跳过）")
    return "\n".join(lines)
