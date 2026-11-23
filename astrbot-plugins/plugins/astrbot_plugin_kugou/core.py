"""酷狗核心流程（由 TG 机器人 app/bot.py 的酷狗部分改写）。

传输层已换成 AstrBot：输入是 QQ 消息文本 + 发送者 openid + 会话 UMO；
输出是 Reply（text / image），由 main.py 的分发器发出去。后台推送
（扫码轮询通知、定时提现结果）走构造时传入的 push(umo, text)。
不依赖 Telegram。

QQ 侧降级（方案 §2）：token 不在 QQ 消息里贴明文。登录成功后只报
userid 与同步状态；kg_sync_qinglong 打开时 token 自动同步进青龙
KGGNB，关闭时请在插件配置里打开后再重新登录。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from . import state as st
from .kugou_concept3114 import (
    KG_QR_MAX_CONSECUTIVE_ERRORS,
    KG_QR_POLL_INTERVAL,
    KG_QR_TIMEOUT,
    KugouConceptLogin3114,
    KugouLoginError,
    KugouLoginUnavailable,
)
from .kugou_withdraw import (
    DEFAULT_COINS as KW_DEFAULT_COINS,
    DEFAULT_POLL as KW_DEFAULT_POLL,
    DEFAULT_WAIT as KW_DEFAULT_WAIT,
    KG_WITHDRAW_COINS_ENV,
    KG_WITHDRAW_POLL_ENV,
    KG_WITHDRAW_WAIT_ENV,
    KG_WITHDRAW_WX_ENV,
    SLOT_HOURS as KW_SLOT_HOURS,
    TIERS as KW_TIERS,
    KugouWithdraw,
    KugouWithdrawUnavailable,
    display_remark as kw_display_remark,
    format_account_list as kw_format_account_list,
    format_window as kw_format_window,
    format_window_result as kw_format_window_result,
    format_windows as kw_format_windows,
    next_window as kw_next_window,
    parse_account_selection as kw_parse_account_selection,
    read_kg_env,
    read_kg_env_int,
    skip_reason as kw_skip_reason,
)
from .qinglong import QinglongClient, QinglongError
from .scheduled_withdrawal import (
    ScheduledWithdrawalRunner,
    ScheduledWithdrawalStore,
    new_withdrawal_job,
    now_beijing,
)
from .texts import KG_HELP_TEXT

log = logging.getLogger(__name__)


@dataclass
class Reply:
    """一次 QQ 回复。text 为空且 image 为空表示“已处理、无需回复”。

    core 返回 None = 未命中任何命令，调用方应放行给 LLM。
    """

    text: str = ""
    image: bytes | None = None


@dataclass
class KugouSettings:
    kg_sync_qinglong: bool = False


# Top-level commands (exactly the set the routing block in
# handle_message handles). Sending one mid-flow jumps straight there
# instead of being swallowed by the sub-menu's fallback — same class
# of bug as the Ele.me plugin's 2026-11-24 s/r/q trap.
_KUGOU_TOP_LEVEL_COMMANDS = frozenset({
    "酷狗登录", "酷狗", "kugou", "Kugou", "酷狗提现", "定时提现",
    "酷狗帮助", "酷狗help",
})

# The Ele.me plugin owns these (mirror of
# astrbot_plugin_eleme.core._TOP_LEVEL_COMMANDS — keep in sync); when
# one arrives mid Kugou flow, drop our flow and let the event continue
# to it (handle_message returns None).
_ELEME_TOP_LEVEL_COMMANDS = frozenset({
    "菜单", "饿了么登录", "饿了么", "管理", "饿了么教程", "登录",
    "手机号登录", "手机登录", "浏览器登录",
    "帮助", "help", "？", "?",
    "同步检查", "饿了么查询", "饿了么兑换", "定时兑换", "饿了么抢兑",
    "Cookie 检查", "Cookie 管理", "失效账号", "饿了么管理",
    "幸运星", "幸运星查询", "查幸运星",
    "青龙任务", "青龙变量", "青龙脚本",
    "青龙日志", "青龙执行", "青龙停止", "青龙启用", "青龙禁用",
    "酷狗教程", "电信教程", "联通教程", "移动云盘教程", "望潮教程",
    "顺丰教程", "美团领卷", "粉象教程", "朴朴超市教程",
})


# 群聊 @机器人 后 message_str 开头会带 [At:...] 段（日志里见过
# "[At:qq_official] 饿了么登录"），匹配命令前先去掉。
_MENTION_PREFIX_RE = re.compile(r"^(?:\[At:[^\]]+\]\s*)+")


class KugouCore:
    def __init__(self, *, qinglong, kugou, withdraw, cfg, data_dir, push):
        self.qinglong = qinglong
        self.kugou = kugou if kugou is not None else KugouConceptLogin3114()
        self.withdraw = withdraw if withdraw is not None else KugouWithdraw()
        self.cfg = cfg
        # push(umo, text): 后台线程（扫码轮询、定时提现）用的推送回调，
        # 由 main.py 提供（内部用 run_coroutine_threadsafe 调
        # context.send_message）。
        self._push = push
        # QR poll timing (defaults are the production values; tests
        # override them to keep the poll thread fast).
        self._kg_poll_interval = KG_QR_POLL_INTERVAL
        self._kg_poll_timeout = KG_QR_TIMEOUT
        self.sessions = st.SessionStore()
        # 酷狗定时提现：常驻任务持久化 + 调度线程（main.py 里启动）。
        # data_dir 为空时只放内存（单测用）；正常运行走插件数据目录下的 JSON。
        self._kw_store = ScheduledWithdrawalStore(
            os.path.join(data_dir, "scheduled_withdrawals.json")
            if data_dir else None)
        self._kw_runner: ScheduledWithdrawalRunner | None = None

    @staticmethod
    def _as_reply(value):
        if value is None:
            return None
        if isinstance(value, Reply):
            return value
        return Reply(value)

    def start_scheduler(self) -> None:
        """启动定时提现调度线程（main.py 在插件加载后调一次）。"""
        if self._kw_runner is not None and self._kw_runner.running:
            return
        self._kw_runner = ScheduledWithdrawalRunner(
            store=self._kw_store,
            run_window=self._run_kw_window,
            notify=self._push,
        )
        self._kw_runner.start()
        log.info("定时提现调度线程已启动")

    def stop_scheduler(self) -> None:
        if self._kw_runner is not None:
            self._kw_runner.stop()
            self._kw_runner = None
            log.info("定时提现调度线程已停止")

    # -- entry --------------------------------------------------------------
    def handle_message(self, sender_id: str, text: str, umo: str) -> Reply | None:
        """Return the reply, or None when the text is not a command
        (the caller lets it fall through to the LLM)."""
        text = (text or "").strip()
        # 群里 @机器人 后 message_str 开头带的 [At:...] 段先去掉再匹配。
        text = _MENTION_PREFIX_RE.sub("", text)
        if not text:
            return None

        session = self.sessions.get(sender_id)
        session.umo = umo or session.umo or ""

        # In-flight flows take priority over fresh commands (except q).
        if text.lower() == "q":
            session.reset_flow()
            return Reply("✅ 已退出操作")
        # Top-level commands always escape a sub-flow: reset first, then
        # fall through to the routing below (state is IDLE now, so no
        # state handler fires). Without this, a command sent while the
        # session sits in e.g. KW_TIER is swallowed by that menu's
        # fallback.
        if text in _KUGOU_TOP_LEVEL_COMMANDS:
            session.reset_flow()
        elif text in _ELEME_TOP_LEVEL_COMMANDS:
            session.reset_flow()
            return None
        if session.state == st.KG_QR_WAIT:
            return self._as_reply(self._on_kg_qr_wait(session, text))
        if session.state == st.KW_ACCOUNTS:
            return self._as_reply(self._on_kw_accounts_choice(session, text))
        if session.state == st.KW_TIER:
            return self._as_reply(self._on_kw_tier_choice(session, text))
        if session.state == st.KW_MODE:
            return self._as_reply(self._on_kw_mode_choice(session, text))
        if session.state == st.KW_LIST:
            return self._as_reply(self._on_kw_list_choice(session, text))

        if text in ("酷狗登录", "酷狗", "kugou", "Kugou"):
            return self._start_kg_qr(session)
        if text == "酷狗提现":
            return self._as_reply(self._start_kugou_withdraw(session))
        if text == "定时提现":
            return self._as_reply(self._start_kw_list(session))
        if text in ("酷狗帮助", "酷狗help"):
            return Reply(KG_HELP_TEXT)
        return None

    # -- 酷狗概念版扫码登录 ---------------------------------------------
    def _start_kg_qr(self, session: st.Session) -> Reply:
        """'酷狗登录': straight to a Concept QR (no menu, no SMS —
        the Concept flow has no SMS login, and a main-app token would
        not work for the Concept tasks). Starting over abandons any
        pending QR poll (its generation check exits it)."""
        if not self.kugou.available:
            return Reply("❌ 酷狗登录没接通\n"
                         "酷狗概念版登录客户端不在或导不进来，请检查插件文件后重试。")
        umo = session.umo or ""
        if not umo:
            return Reply("❌ 拿不到你的会话标识，请重新发送“酷狗登录”。")
        concept_state = self.kugou.new_state()
        try:
            key, img, url = self.kugou.get_qr_key(concept_state)
        except (KugouLoginError, KugouLoginUnavailable) as exc:
            return Reply(f"❌ {exc}\n请稍后重新发送“酷狗登录”再试。")
        # Abandon any older pending poll; this generation owns the new key.
        session.kg_qr_gen += 1
        gen = session.kg_qr_gen
        session.kg_qr_key = key
        session.kg_scanned = False
        session.kg_concept_state = concept_state
        session.state = st.KG_QR_WAIT
        # QQ 侧没有 TG 的 120 秒自删：二维码图片照常发，token 不贴明文。
        img_bytes = self.kugou.qr_image_bytes(img) if img else None
        lines = ["📱 二维码已生成，请用「酷狗概念版」App 扫码并点确认登录。"]
        if img_bytes:
            lines.append("（二维码见下图；图片看不到时用下面的链接打开扫码）")
        else:
            lines.append("（图片发不出来，用下面的链接打开扫码）")
        lines.append(f"🔗 {url}")
        lines.append("有效期约 2 分钟；回复 1 换一张新的，回复 q 取消。")
        thread = threading.Thread(
            target=self._kg_qr_poll, args=(session, umo, key, gen, concept_state),
            kwargs={"interval": self._kg_poll_interval,
                    "timeout": self._kg_poll_timeout},
            name="kugou-qr-poll", daemon=True)
        thread.start()
        return Reply("\n".join(lines), image=img_bytes)

    def _kg_login_success(self, session: st.Session, token: str,
                          userid: str, remark: str) -> str:
        """Shared login success: sync the KGGNB line to Qinglong,
        return the reply text (userid + sync status only).

        QQ 侧不贴 token 明文（TG 版会把 token + KGGNB 行发回私聊；
        QQ 私聊撤回待实测，降级为只同步青龙或提示去开同步）。
        The token is never logged.
        """
        device = (session.kg_concept_state or {}).get("device", {})
        line = self.kugou.build_kggnb_line(token, userid, remark, device)
        synced = False
        if not self.cfg.kg_sync_qinglong:
            # Qinglong sync is paused (user 2026-11-21: test the bot
            # first, hold the Qinglong part).
            sync_note = ("⏸️ 青龙同步已暂停（先测试机器人）。\n"
                         "token 不会在 QQ 里明文显示；请在插件配置里把 "
                         "kg_sync_qinglong 打开后重新登录，token 会自动同步进青龙 KGGNB。")
        elif self.qinglong is None:
            sync_note = ("⚠️ 青龙未接通，token 没同步。\n"
                         "请配好青龙（ql_base_url / ql_token）后重新登录。")
        else:
            try:
                result = self.qinglong.sync_kugou_line(line, userid, remark)
                used = result.get("remark") or remark
                sync_note = (f"✅ 已同步进青龙 KGGNB（备注 {used}，"
                             "其他账号不受影响）")
                synced = True
            except QinglongError as exc:
                sync_note = (f"❌ 同步青龙失败：{exc}\n"
                             "请检查青龙配置后重新登录。")
        session.reset_flow()
        lines = [
            "✅ 酷狗登录成功（酷狗概念版）",
            f"🆔 userid：{userid}",
            sync_note,
        ]
        if synced:
            lines.append(
                "概念版（appid 3114）token 已同步青龙 KGGNB（任务 200 / 提现都认）。"
                "贴完跑一次任务 200 验证。")
        return "\n".join(lines)

    def _kg_qr_poll(self, session: st.Session, umo: str, key: str,
                    gen: int, concept_state: dict | None = None,
                    interval: float = KG_QR_POLL_INTERVAL,
                    timeout: float = KG_QR_TIMEOUT) -> None:
        """Poll the Concept QR status in a background thread (never blocks the
        main polling loop). Notifies via _notify; exits silently when
        the user cancels (q), replaces the QR (new generation), or
        moves on to another flow. Transient network errors (the Kugou
        server drops connections intermittently) are retried; only a
        real login error or N consecutive network failures end the
        poll."""
        deadline = time.monotonic() + max(1.0, timeout)
        scanned = False
        consecutive_errors = 0
        state = (concept_state if concept_state is not None
                 else {"qr_key": key, "device": {}})
        while time.monotonic() < deadline:
            if session.kg_qr_gen != gen or session.state != st.KG_QR_WAIT:
                return
            try:
                state_name, _text, data = self.kugou.check_qr(state)
            except (KugouLoginError, KugouLoginUnavailable) as exc:
                self._push(umo, f"❌ 扫码状态查询失败：{exc}\n请重新发送“酷狗登录”再试。")
                self._kg_finish_poll(session, gen)
                return
            except Exception as exc:  # noqa: BLE001 - transient network blip; retry
                consecutive_errors += 1
                log.warning("酷狗扫码轮询网络异常（第 %d 次）：%s",
                            consecutive_errors, exc)
                if consecutive_errors >= KG_QR_MAX_CONSECUTIVE_ERRORS:
                    self._push(
                        umo,
                        "❌ 酷狗服务器网络不稳定，扫码状态查不到。\n"
                        "请重新发送“酷狗登录”生成一张新的二维码。")
                    self._kg_finish_poll(session, gen)
                    return
                time.sleep(interval)
                continue
            consecutive_errors = 0
            if state_name == "success":
                token = (data.get("token") or "").strip()
                userid = str(data.get("userid") or "").strip()
                reply = self._kg_login_success(session, token, userid, f"账号_{userid}")
                # The success message carries the token + KGGNB line:
                # auto-delete it after 120 s (enough to copy it).
                self._push(umo, reply)
                return
            if state_name == "expired":
                self._push(umo, "⏰ 二维码已过期，请重新发送“酷狗登录”生成一张新的。")
                self._kg_finish_poll(session, gen)
                return
            if state_name == "unknown":
                self._push(umo, "❌ 扫码状态未知，请重新发送“酷狗登录”再试。")
                self._kg_finish_poll(session, gen)
                return
            if state_name == "scanned" and not scanned:
                scanned = True
                session.kg_scanned = True
                self._push(umo, "📱 已扫码，请在酷狗概念版 App 里点确认登录。")
            time.sleep(interval)
        self._push(umo, "⏰ 等待扫码超时（5 分钟），请重新发送“酷狗登录”生成一张新的。")
        self._kg_finish_poll(session, gen)

    def _kg_finish_poll(self, session: st.Session, gen: int) -> None:
        """Drop the QR flow, but only if this poll is still the current one."""
        if session.kg_qr_gen == gen and session.state == st.KG_QR_WAIT:
            session.reset_flow()

    def _on_kg_qr_wait(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "1":
            # A fresh QR replaces the pending one (its poll exits via
            # the generation check); the old key is simply abandoned.
            return self._start_kg_qr(session)
        return ("二维码还在等扫码（已发出，见上图）。\n"
                "请用酷狗概念版 App 扫码并点确认登录。\n"
                "回复 1 换一张新的，回复 q 取消。")

    def _read_kw_envs(self) -> tuple[str, str, int, int, int]:
        """Read the withdrawal envs from Qinglong (read-only, no writes):
        KGGNB, KG_WITHDRAW_WX, KG_WITHDRAW_COINS/WAIT/POLL."""
        kggnb = read_kg_env(self.qinglong, "KGGNB")
        wx = read_kg_env(self.qinglong, KG_WITHDRAW_WX_ENV)
        coins = read_kg_env_int(self.qinglong, KG_WITHDRAW_COINS_ENV,
                                KW_DEFAULT_COINS)
        wait = read_kg_env_int(self.qinglong, KG_WITHDRAW_WAIT_ENV,
                               KW_DEFAULT_WAIT)
        poll = read_kg_env_int(self.qinglong, KG_WITHDRAW_POLL_ENV,
                               KW_DEFAULT_POLL)
        return kggnb, wx, coins, wait, poll

    def _start_kugou_withdraw(self, session: st.Session) -> str:
        """'酷狗提现': show the account list (logged-in order, balance
        when known), then pick accounts. Read-only so far."""
        if not self.withdraw.available:
            return ("❌ 酷狗提现没接通\n"
                    "酷狗提现脚本不在或导不进来，请检查 "
                    "~/workspace/your_files/kg_withdraw.py 后重试。")
        if self.qinglong is None:
            return "❌ 青龙未接通"
        try:
            kggnb, wx, coins, _wait, _poll = self._read_kw_envs()
            accounts = self.withdraw.load_accounts(kggnb, wx)
        except QinglongError as exc:
            return f"❌ {exc}"
        except KugouWithdrawUnavailable as exc:
            return f"❌ {exc}"
        if not accounts:
            return "青龙 KGGNB 里还没有酷狗账号，先用“酷狗登录”登录一个吧。"
        try:
            checks = self.withdraw.check_accounts(accounts, coins)
        except KugouWithdrawUnavailable as exc:
            return f"❌ {exc}"
        session.kw_accounts = [a["remark"] for a in accounts]
        session.kw_remarks = []
        session.kw_checks = {c["remark"]: c for c in checks}
        session.kw_coins = coins
        session.state = st.KW_ACCOUNTS
        return kw_format_account_list(accounts, checks, coins)

    def _on_kw_accounts_choice(self, session: st.Session, text: str) -> str:
        """酷狗提现第 1 步：选账号（0=全部，可多选）-> 选档位。"""
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出酷狗提现"
        if choice == "t":
            return self._start_kw_list(session)
        idxs, error = kw_parse_account_selection(
            choice, len(session.kw_accounts))
        if error:
            return error
        session.kw_remarks = [session.kw_accounts[i] for i in idxs]
        return self._start_kw_tier(session)

    def _start_kw_tier(self, session: st.Session) -> str:
        """酷狗提现第 2 步：选档位（1-5），选了立即写青龙变量。"""
        session.state = st.KW_TIER
        remarks = session.kw_remarks
        current = session.kw_coins or KW_DEFAULT_COINS
        lines = ["=====酷狗提现=====",
                 f"🎯 已选账号：{'、'.join(kw_display_remark(r) for r in remarks)}",
                 f"💰 当前档位：{current} 金币 = {current / 10000:g} 元"
                 f"（青龙 KG_WITHDRAW_COINS）",
                 "------------------",
                 "选档位（按序号，选了自动改青龙变量 KG_WITHDRAW_COINS）："]
        for i, coins in enumerate(KW_TIERS, start=1):
            label = "首提" if coins == KW_TIERS[0] else ""
            lines.append(f"{i}.{coins}={coins / 10000:g}元{label}")
        lines += ["------------------",
                  "回复序号选择档位，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_kw_tier_choice(self, session: st.Session, text: str) -> str:
        """酷狗提现第 2 步：1-5 选档位，立即写青龙变量，然后进方式选择。

        写变量不经过 KG_SYNC_QINGLONG 开关（那个开关只管 KGGNB 登录
        同步）；档位是用户当场选的，选了就改变量再提交。
        """
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出酷狗提现"
        if not choice.isdigit() or not 1 <= int(choice) <= len(KW_TIERS):
            return "请回复 1~5 选择档位，或回复 q 退出。"
        coins = KW_TIERS[int(choice) - 1]
        try:
            self.qinglong.upsert_env(KG_WITHDRAW_COINS_ENV, str(coins))
        except Exception as exc:  # noqa: BLE001 - 青龙写失败只报错，流程不丢
            log.exception("酷狗提现写档位失败")
            return (f"❌ 写青龙变量失败：{exc}\n"
                    "档位没改，请重选 1~5，或回复 q 退出。")
        session.kw_coins = coins
        return self._start_kw_mode(session)

    def _start_kw_mode(self, session: st.Session) -> str:
        """酷狗提现第 3 步：[1] 立即提现 / [2] 提交定时抢兑（默认）。"""
        session.state = st.KW_MODE
        remarks = session.kw_remarks
        coins = session.kw_coins or KW_DEFAULT_COINS
        lines = ["=====酷狗提现=====",
                 f"🎯 已选账号：{'、'.join(kw_display_remark(r) for r in remarks)}",
                 f"💰 目标档位：{coins} 金币 = {coins / 10000:.2f} 元",
                 f"🕒 定时场次：每天 {kw_format_windows()}（北京时间）"]
        for remark in remarks:
            why = kw_skip_reason(session.kw_checks.get(remark) or {})
            if why:
                lines.append(f"⏭️ {kw_display_remark(remark)}：{why}（本场跳过）")
        lines += ["------------------",
                  "[1] 立即提现（现在抢一次）",
                  "[2] 提交定时抢兑（每场自动抢，默认）",
                  "------------------",
                  "回复数字选择，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_kw_mode_choice(self, session: st.Session, text: str) -> str:
        """酷狗提现第 3 步：1 立即提（后台线程跑，结果推送）、2 提交定时抢兑。"""
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出酷狗提现"
        if choice == "1":
            remarks = list(session.kw_remarks)
            coins = session.kw_coins or KW_DEFAULT_COINS
            chat_id = session.umo or ""
            session.reset_flow()
            if not remarks or not chat_id:
                return "❌ 提交失败：账号或会话标识丢了，请重新发送“酷狗提现”。"
            thread = threading.Thread(
                target=self._run_kw_immediate_thread,
                args=(chat_id, remarks, coins),
                name="kw-immediate", daemon=True)
            thread.start()
            return "🔫 正在立即提现…结果稍后发来。"
        if choice == "2":
            return self._submit_kw_job(session)
        return "请回复 1 立即提现、2 提交定时抢兑，或 q 退出。"

    def _submit_kw_job(self, session: st.Session) -> str:
        """酷狗提现：提交常驻定时抢兑（替换同一账号的旧任务）。"""
        remarks = list(session.kw_remarks)
        coins = session.kw_coins or KW_DEFAULT_COINS
        chat_id = session.umo or ""
        if not remarks or not chat_id:
            session.reset_flow()
            return "❌ 提交失败：账号或会话标识丢了，请重新发送“酷狗提现”。"
        replaced = self._kw_store.cancel_for_remarks(remarks)
        job = new_withdrawal_job(remarks, coins, chat_id)
        self._kw_store.add(job)
        window, fire = kw_next_window(KW_SLOT_HOURS, now_beijing())
        reply = "\n".join([
            "⏰ 已安排酷狗定时提现：",
            f"📱 账号：{'、'.join(kw_display_remark(r) for r in remarks)}",
            f"💰 档位：{coins} 金币 = {coins / 10000:.2f} 元",
            f"🕒 场次：每天 {kw_format_windows()}（北京时间），每场自动抢",
            f"下一场：{kw_format_window(window)}"
            f"（{fire.strftime('%H:%M')} 预热）",
            "当天已提过的号当场跳过，第二天重置。",
            "回复“定时提现”可查看/取消。",
            "⚠️ 青龙任务 246（酷狗提现-定时抢5元）仍在跑；机器人开抢前会"
            "先查今日次数，已提过的号会跳过，不会重复提。想只留机器人跑"
            "的话，到青龙把 246 禁用。",
        ])
        if replaced:
            reply += f"\n（已替换同一账号的 {len(replaced)} 个旧定时提现任务）"
        session.reset_flow()
        return reply

    def _run_kw_immediate_thread(self, umo: str, remarks: list[str],
                                 coins: int) -> None:
        """Immediate attempt in a background thread (grab can poll up to
        ~5 min); the result is pushed via _push."""
        try:
            result = self._run_kw_attempt(remarks, coins, None)
        except Exception as exc:  # noqa: BLE001 - 执行异常只记一次，绝不重试
            log.exception("酷狗立即提现执行异常")
            self._push(
                umo,
                f"⏰ 酷狗立即提现执行异常：\n❌ {exc}\n"
                "未重试，结果未知，请到 App 我的账单核实。")
            return
        self._push(umo, result)

    def _run_kw_window(self, job: dict, window) -> str:
        """Scheduled-window callback for the withdrawal runner."""
        remarks = list(job.get("remarks") or [])
        coins = int(job.get("coins") or KW_DEFAULT_COINS)
        return self._run_kw_attempt(remarks, coins, window)

    def _run_kw_attempt(self, remarks: list[str], coins: int,
                        window) -> str:
        """One withdrawal attempt (immediate when window is None, else
        the scheduled window): read Qinglong (read-only) -> check live
        status -> skip ineligible -> parallel do_grab -> result text.

        The Qinglong task 246 overlap is handled by the live count
        check: an account task 246 already completed today reads
        day_apply_count 1/1 and is skipped here; the server's 1/day
        limit also rejects a second apply (31201/31205).
        """
        if self.qinglong is None:
            return "❌ 青龙未接通，提现没跑。"
        kggnb, wx, _coins, wait, poll = self._read_kw_envs()
        accounts = self.withdraw.load_accounts(kggnb, wx)
        wanted = set(remarks)
        accounts = [a for a in accounts if a.get("remark") in wanted]
        if not accounts:
            return "❌ 青龙 KGGNB 里找不到选中的账号，提现没跑。"
        checks = self.withdraw.check_accounts(accounts, coins)
        eligible, skipped = [], []
        for acc, check in zip(accounts, checks):
            why = kw_skip_reason(check)
            if why:
                skipped.append((acc["remark"], why))
            else:
                eligible.append(acc)
        if not eligible:
            return kw_format_window_result(window, [], skipped)
        results = self.withdraw.grab_accounts(
            eligible, coins, at_dt=window,
            wait_secs=wait, poll_secs=poll)
        return kw_format_window_result(window, results, skipped)

    def _start_kw_list(self, session: st.Session) -> str:
        """列出常驻定时提现任务，可按编号取消。"""
        jobs = self._kw_store.active()
        if not jobs:
            session.reset_flow()
            return "⏰ 没有已安排的定时提现。"
        session.kw_list_ids = [j["id"] for j in jobs]
        session.state = st.KW_LIST
        now = now_beijing()
        lines = ["=====定时提现====="]
        for i, job in enumerate(jobs, start=1):
            window, _fire = kw_next_window(KW_SLOT_HOURS, now)
            lines.append(
                f"[{i}] {job.get('coins')}金币 | "
                f"账号 {'、'.join(kw_display_remark(r) for r in job.get('remarks') or [])} | "
                f"每天 {kw_format_windows()} | "
                f"下一场 {kw_format_window(window, now)}")
        lines += ["------------------",
                  "回复序号取消该任务，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_kw_list_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "q":
            return self._cancel_to_menu(session, "✅ 已退出定时提现")
        if not choice.isdigit():
            return "请回复序号，或回复 q 退出。"
        idx = int(choice)
        if not 1 <= idx <= len(session.kw_list_ids):
            return f"请回复 1~{len(session.kw_list_ids)}，或回复 q 退出。"
        job_id = session.kw_list_ids[idx - 1]
        job = self._kw_store.get(job_id)
        if job is None or job.get("status") != "active":
            return "这个任务已经不在待执行里了，重新发送“定时提现”查看。"
        job["status"] = "cancelled"
        self._kw_store.update(job)
        log.info("已取消定时提现 %s：%s 金币，账号 %s",
                 job["id"], job.get("coins"),
                 "、".join(job.get("remarks") or []))
        remaining = self._kw_store.active()
        if not remaining:
            session.reset_flow()
            return (f"✅ 已取消定时提现：{job.get('coins')}金币，"
                    f"账号 {'、'.join(kw_display_remark(r) for r in job.get('remarks') or [])}。\n"
                    "没有已安排的定时提现了。")
        session.kw_list_ids = [j["id"] for j in remaining]
        session.state = st.KW_LIST
        now = now_beijing()
        lines = ["=====定时提现=====",
                 f"✅ 已取消：{job.get('coins')}金币"]
        for i, rest in enumerate(remaining, start=1):
            window, _fire = kw_next_window(KW_SLOT_HOURS, now)
            lines.append(
                f"[{i}] {rest.get('coins')}金币 | "
                f"账号 {'、'.join(kw_display_remark(r) for r in rest.get('remarks') or [])} | "
                f"每天 {kw_format_windows()} | "
                f"下一场 {kw_format_window(window, now)}")
        lines += ["------------------",
                  "回复序号取消该任务，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

