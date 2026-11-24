"""饿了么核心流程（由 TG 机器人 app/bot.py 的饿了么部分改写）。

传输层已换成 AstrBot：输入是 QQ 消息文本 + 发送者 openid + 会话 UMO；
输出是 Reply（text / image），由 main.py 的分发器发出去。后台推送
（定时兑换结果）走构造时传入的 push(umo, text)。
不依赖 Telegram / 微信 / 浏览器。浏览器登录（手机号登录）已砍掉。
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import state as st
from .cookie_watch import check_all
from .eleme_login import (
    ElemeLoginError,
    ElemeSmsLogin,
    SmsLoginUnavailable,
    check_cookie_live,
    cookie_has_required_fields,
    extract_remark,
    is_code,
    is_phone,
    validate_ele_cookie,
)
from .exchange import (
    MALL_PAGE_SIZE,
    MILK_TEA_EXCHANGE_ID,
    ExchangeError,
    describe_item,
    exchange_item as exchange_one_item,
    item_status_tag,
    list_mall_items,
    next_exchange_time,
)
from .lucky_star import (
    format_detail_lines,
    query_one_account,
    query_star_balance,
    query_star_homepage,
)
from .qinglong import QinglongClient, QinglongError
from .scheduled_exchange import (
    ScheduledExchangeRunner,
    ScheduledExchangeStore,
    format_target,
    new_scheduled_task,
    now_beijing,
    parse_hhmm,
    task_target,
)
from .texts import (
    COOKIE_MENU_TEXT,
    DIRECTORY_MENU_TEXT,
    ELEME_TUTORIAL_TEXT,
    HELP_TEXT,
    MENU_TEXT,
    QUERY_MENU_TEXT,
    TUTORIAL_PLACEHOLDERS,
    QL_ID_RE,
    _menu_text,
    mask_phone,
)
from .yyb import YybClient, YybError

log = logging.getLogger(__name__)


def _normal_log_lines(log_text: str) -> list[str]:
    """Script log lines only — Qinglong/shell artifacts are dropped."""
    out: list[str] = []
    for ln in (log_text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("##"):
            continue
        if ln.startswith("Terminated") or "$timeoutCmd" in ln or "$which_program" in ln:
            continue
        if "已停止" in ln:
            continue
        out.append(ln)
    return out


def _last_log_lines(log_text: str, n: int = 3, limit: int = 300) -> str:
    """Last ``n`` non-empty, normal log lines, capped at ``limit`` chars."""
    lines = _normal_log_lines(log_text)
    tail = "\n".join(lines[-n:])
    return tail[-limit:] if len(tail) > limit else tail


@dataclass
class Reply:
    """一次 QQ 回复。text 为空且 image 为空表示“已处理、无需回复”。

    core 返回 None = 未命中任何命令，调用方应放行给 LLM。
    """

    text: str = ""
    image: bytes | None = None


@dataclass
class ElemeSettings:
    elm_phones: list[str] = field(default_factory=list)
    elm_cookie_task: str = "elm_cookie"
    elm_cookie_timeout: int = 120
    yyb_ref: str = "1"
    yyb_console_url: str = "http://192.168.31.2:8000"
    # 备注 -> 手机号（[r] 一键重登时直接发码；不在表里的账号现问）。
    # 只放插件配置，不硬编码真实号码；成功短信绑定后也会自动记住到
    # remark_phones.json。
    remark_phones: dict[str, str] = field(default_factory=dict)


# Top-level commands (exactly the set the routing block in
# handle_message handles, plus the tutorial placeholders). Sending one
# mid-flow jumps straight there instead of being swallowed by the
# sub-menu's fallback — 2026-11-24: "饿了么登录" and "饿了么查询" sent
# while stuck in the Cookie-management menu both got the s/r/q fallback.
_TOP_LEVEL_COMMANDS = frozenset({
    "菜单", "饿了么登录", "饿了么", "管理", "饿了么教程", "登录",
    "手机号登录", "手机登录", "浏览器登录",
    "帮助", "help", "？", "?",
    "同步检查", "饿了么查询", "饿了么兑换", "定时兑换", "饿了么抢兑",
    "Cookie 检查", "Cookie 管理", "失效账号", "饿了么管理",
    "幸运星", "幸运星查询", "查幸运星",
    "青龙任务", "青龙变量", "青龙脚本",
    "青龙日志", "青龙执行", "青龙停止", "青龙启用", "青龙禁用",
}) | frozenset(TUTORIAL_PLACEHOLDERS)

# The Kugou plugin owns these; when one arrives mid Ele.me flow, drop
# our flow and let the event continue to it (handle_message returns None).
_KUGOU_TOP_LEVEL_COMMANDS = frozenset({
    "酷狗登录", "酷狗", "kugou", "Kugou", "酷狗提现", "定时提现",
    "酷狗帮助", "酷狗help",
})


def _is_top_level_command(text: str) -> bool:
    return text in _TOP_LEVEL_COMMANDS or bool(QL_ID_RE.match(text))


# 群聊 @机器人 后 message_str 开头会带 [At:...] 段（日志里见过
# "[At:qq_official] 饿了么登录"），匹配命令前先去掉。
_MENTION_PREFIX_RE = re.compile(r"^(?:\[At:[^\]]+\]\s*)+")


class ElemeCore:
    def __init__(self, *, qinglong, sms_login, yyb, cfg, data_dir, push):
        self.qinglong = qinglong
        self.sms_login = sms_login
        self.yyb = yyb
        self.cfg = cfg
        # push(umo, text): 后台线程（定时兑换）用的推送回调，由 main.py 提供
        # （内部用 run_coroutine_threadsafe 调 context.send_message）。
        self._push = push
        self.sessions = st.SessionStore()
        # 定时兑换：任务持久化 + 调度线程（main.py 里启动）。
        # data_dir 为空时只放内存（单测用）；正常运行走插件数据目录下的 JSON。
        self._sched_store = ScheduledExchangeStore(
            os.path.join(data_dir, "scheduled_exchanges.json")
            if data_dir else None)
        self._sched_runner: ScheduledExchangeRunner | None = None
        # remark -> phone, remembered from every successful SMS bind
        # (remark_phones.json). [r] consults it first, so an account once
        # logged in by phone gets its code re-sent directly instead of
        # asking for the phone again.
        self._remark_phones_path = (
            os.path.join(data_dir, "remark_phones.json") if data_dir else None)
        self.remark_phones: dict[str, str] = dict(cfg.remark_phones or {})
        if self._remark_phones_path:
            try:
                with open(self._remark_phones_path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.remark_phones.update(
                        {str(k): str(v) for k, v in loaded.items() if v})
            except (OSError, ValueError):
                pass

    @staticmethod
    def _as_reply(value):
        if value is None:
            return None
        if isinstance(value, Reply):
            return value
        return Reply(value)

    def start_scheduler(self) -> None:
        """启动定时兑换调度线程（main.py 在插件加载后调一次）。"""
        if self._sched_runner is not None and self._sched_runner.running:
            return
        self._sched_runner = ScheduledExchangeRunner(
            store=self._sched_store,
            build_picks=self._build_exchange_picks,
            execute=self._run_exchange,
            notify=self._push,
        )
        self._sched_runner.start()
        log.info("定时兑换调度线程已启动")

    def stop_scheduler(self) -> None:
        if self._sched_runner is not None:
            self._sched_runner.stop()
            self._sched_runner = None
            log.info("定时兑换调度线程已停止")

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
        # session sits in e.g. COOKIE_MENU is swallowed by that menu's
        # "请回复 s / r / q。" fallback.
        if _is_top_level_command(text):
            session.reset_flow()
        elif text in _KUGOU_TOP_LEVEL_COMMANDS:
            session.reset_flow()
            return None
        if session.state == st.SMS_PHONE:
            return self._as_reply(self._on_sms_phone(session, text))
        if session.state == st.SMS_CODE:
            return self._as_reply(self._on_sms_code(session, text))
        if session.state == st.CK_PASTE:
            return self._as_reply(self._on_ck_paste(session, text))
        if session.state in (st.QL_LOG_ID, st.QL_RUN_ID, st.QL_STOP_ID,
                             st.QL_ENABLE_ID, st.QL_DISABLE_ID):
            return self._as_reply(self._on_ql_id(session, text))
        if session.state == st.QUERY_MENU:
            return self._as_reply(self._on_query_menu_choice(session, text))
        if session.state == st.QUERY_ACCOUNT:
            return self._as_reply(self._on_query_account_choice(session, text))
        if session.state == st.EXCHANGE_ACCOUNTS:
            return self._as_reply(self._on_exchange_accounts_choice(session, text))
        if session.state == st.EXCHANGE_MALL:
            return self._as_reply(self._on_exchange_mall_choice(session, text))
        if session.state == st.EXCHANGE_METHOD:
            return self._as_reply(self._on_exchange_method_choice(session, text))
        if session.state == st.EXCHANGE_SUBMIT:
            return self._as_reply(self._on_exchange_submit_choice(session, text))
        if session.state == st.EXCHANGE_TIME:
            return self._as_reply(self._on_exchange_time_choice(session, text))
        if session.state == st.EXCHANGE_CONFIRM:
            return self._as_reply(self._on_exchange_confirm_choice(session, text))
        if session.state == st.EXCHANGE_SCHED_CONFIRM:
            return self._as_reply(self._on_exchange_sched_confirm_choice(session, text))
        if session.state == st.EXCHANGE_SCHED_LIST:
            return self._as_reply(self._on_exchange_sched_list_choice(session, text))
        if session.state == st.COOKIE_MENU:
            return self._as_reply(self._on_cookie_menu_choice(session, text))
        if session.state == st.REREGISTER_PHONE:
            return self._as_reply(self._on_reregister_phone(session, text))

        if text == "菜单":
            # Directory menu only: returns the service directory text and
            # resets any in-flight flow. It does NOT enter the MENU state.
            session.reset_flow()
            return Reply(DIRECTORY_MENU_TEXT)
        if text in ("饿了么登录", "饿了么", "管理"):
            return self._as_reply(self._start_manage(session))
        if text == "饿了么教程":
            return Reply(ELEME_TUTORIAL_TEXT)
        if text in TUTORIAL_PLACEHOLDERS:
            return Reply(TUTORIAL_PLACEHOLDERS[text])
        if text == "登录":
            return self._as_reply(self._start_login_default(session))
        if text in ("手机号登录", "手机登录", "浏览器登录"):
            return Reply(
                "❌ 浏览器登录在 QQ 机器人上不可用\n"
                "请用 [1] 短信验证码登录、[2] 微信扫码登录 或 [3] CK 登录。")
        if text in ("帮助", "help", "？", "?"):
            return Reply(HELP_TEXT)
        if text == "同步检查":
            return self._as_reply(self._cmd_ele_slots())
        if text == "饿了么查询":
            return self._as_reply(self._start_query_menu(session))
        if text == "饿了么兑换":
            return self._as_reply(self._start_exchange(session))
        if text in ("定时兑换", "饿了么抢兑"):
            return self._as_reply(self._start_sched_list(session))
        if text in ("Cookie 检查", "Cookie 管理", "失效账号"):
            return self._as_reply(self._start_cookie_menu(session))
        if text == "饿了么管理":
            return self._as_reply(self._start_eleme_manage(session))
        if text in ("幸运星", "幸运星查询", "查幸运星"):
            return self._as_reply(self._start_star_query_accounts(session))
        if text == "青龙任务":
            return self._as_reply(self._cmd_list_crons())
        if text == "青龙变量":
            return self._as_reply(self._cmd_list_envs())
        if text == "青龙脚本":
            return self._as_reply(self._cmd_list_scripts())
        if text in ("青龙日志", "青龙执行", "青龙停止", "青龙启用", "青龙禁用"):
            return self._as_reply(self._ask_ql_id(session, text))
        match = QL_ID_RE.match(text)
        if match:
            return self._as_reply(self._run_ql_action(match.group(1), int(match.group(2))))

        if session.state == st.MENU:
            return self._as_reply(self._on_menu_choice(session, text))
        return None

    def _remember_phone(self, remark: str, phone: str) -> None:
        """Persist remark -> phone after a successful SMS bind."""
        phone = (phone or "").strip()
        if not remark or not phone:
            return
        if self.remark_phones.get(remark) == phone:
            return
        self.remark_phones[remark] = phone
        if not self._remark_phones_path:
            return
        try:
            os.makedirs(os.path.dirname(self._remark_phones_path), exist_ok=True)
            with open(self._remark_phones_path, "w", encoding="utf-8") as fh:
                json.dump(self.remark_phones, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _start_manage(self, session: st.Session) -> str:
        phones = self.cfg.elm_phones
        session.accounts = list(phones)
        session.account_index = 0
        session.state = st.MENU
        if phones:
            return (
                f"======重新登录 1/{len(phones)}======\n"
                f"📱 当前账号：{mask_phone(phones[0])}\n"
                f"----------------\n{_menu_text(in_loop=True)}"
            )
        return _menu_text()

    def _on_menu_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        # Menu footer shortcuts: jump to another feature instead of logging in.
        if choice == "饿了么兑换":
            session.reset_flow()
            return self._start_exchange(session)
        if choice in ("定时兑换", "饿了么抢兑"):
            session.reset_flow()
            return self._start_sched_list(session)
        if choice == "饿了么查询":
            session.reset_flow()
            return self._start_query_menu(session)
        if choice in ("酷狗登录", "酷狗提现"):
            # 酷狗由 astrbot_plugin_kugou 接手：退出本流程，不拦截消息。
            session.reset_flow()
            return None
        if choice == "饿了么管理":
            session.reset_flow()
            return self._start_eleme_manage(session)
        if choice == "1" or choice == "登录":
            # [1] SMS login via the captured ipassport endpoints
            # (see eleme_login.py / SMS_RESEARCH.md §7).
            return self._start_sms(session)
        if choice == "2":
            return self._start_yyb_login(session)
        if choice == "3":
            session.state = st.CK_PASTE
            return "请直接粘贴抓到的饿了么 Cookie（一整行，含 _m_h5_tk），\n回复 q 取消。"
        if choice == "s":
            # 顺序登录中 [s] = 跳过当前账号；[s]/[r] 的 Cookie 检查与
            # 重登已挪到单独的「饿了么管理」菜单，登录菜单里不再提供。
            if session.total_accounts > 0:
                return self._advance(session, skipped=True)
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出操作"
        if session.total_accounts > 0:
            return "请回复 1 / 2 / 3 / s / q。"
        return "请回复 1 / 2 / 3 / q。"

    def _advance(self, session: st.Session, skipped: bool = False) -> str:
        """Move to the next account in the sequential re-login loop."""
        total = session.total_accounts
        if total == 0:
            session.reset_flow()
            return "✅ 已退出操作"
        idx = session.account_index + 1
        if idx >= total:
            session.reset_flow()
            note = "已跳过" if skipped else "已完成"
            return f"✅ 顺序登录结束（{total}/{total}），{note}最后一个账号。"
        session.account_index = idx
        session.state = st.MENU
        return (
            f"======重新登录 {idx + 1}/{total}======\n"
            f"📱 当前账号：{mask_phone(session.accounts[idx])}\n"
            f"----------------\n{_menu_text(in_loop=True)}"
        )

    def _start_sms(self, session: st.Session) -> str:
        if not self.sms_login.available:
            return (
                "❌ 短信登录暂未接通\n"
                "请先用 [2] 微信扫码登录 或 [3] CK 登录。"
            )
        phone = session.current_phone
        if phone:
            return self._send_sms(session, phone)
        session.state = st.SMS_PHONE
        return "请输入要登录的手机号（11 位）："

    def _send_sms(self, session: st.Session, phone: str) -> str:
        try:
            token = self.sms_login.send_code(phone)
        except (SmsLoginUnavailable, ElemeLoginError) as exc:
            if session.reregister_mode:
                session.reregister_mode = False
                session.state = st.COOKIE_MENU
                return f"❌ {exc}\n请稍后回复 [r] 重新开始（已登录好的账号不会重复发码）。"
            session.state = st.MENU
            return f"❌ {exc}"
        session.phone = phone
        session.validate_token = token
        session.state = st.SMS_CODE
        return (
            f"✅ 发送成功\n请把收到的短信验证码发给我（{mask_phone(phone)}）\n"
            f"回复 q 随时退出"
        )

    def _on_sms_phone(self, session: st.Session, text: str) -> str:
        if not is_phone(text):
            return "手机号不对，请输入 11 位手机号，或回复 q 退出。"
        return self._send_sms(session, text.strip())

    def _on_sms_code(self, session: st.Session, text: str) -> str:
        if not is_code(text):
            return "验证码不对，请输入 4~6 位数字验证码，或回复 q 退出。"
        try:
            cookie = self.sms_login.verify_code(session.phone, text.strip(), session.validate_token)
        except (SmsLoginUnavailable, ElemeLoginError) as exc:
            return f"❌ {exc}\n请重新发验证码，或回复 q 退出。"
        return self._bind_cookie(session, cookie)

    def _start_login_default(self, session: st.Session) -> str:
        """'登录' outside the menu: start (or continue) the manage loop
        and run the YYB login flow for the current account."""
        if not session.accounts and self.cfg.elm_phones:
            session.accounts = list(self.cfg.elm_phones)
            session.account_index = 0
        session.state = st.MENU
        return self._start_yyb_login(session)

    def _yyb_status(self) -> tuple[str, str]:
        """("ok" | "dead" | "unreachable", detail) for the YYB account."""
        if self.yyb is None:
            return "unreachable", "YYB 客户端未配置（YYB_BASE_URL 为空）"
        try:
            alive, detail = self.yyb.account_status(self.cfg.yyb_ref)
        except YybError as exc:
            return "unreachable", str(exc)
        return ("ok", detail) if alive else ("dead", detail)

    def _check_ele_slot(self, slot: int) -> tuple[bool, str, str]:
        """(ok, masked_remark, note) for one ELM_COOKIE slot.

        Never returns the cookie value; only the masked remark
        (USERID prefix) reaches the reply.
        """
        if self.qinglong is None:
            return False, "", "青龙未接通"
        try:
            cookie = self.qinglong.get_ele_slot_cookie(slot)
        except QinglongError as exc:
            return False, "", f"读青龙变量失败：{exc}"
        if not cookie:
            return False, "", "槽位里还没有 Cookie"
        ok, note = validate_ele_cookie(cookie)
        if not ok:
            return False, "", note
        return True, extract_remark(cookie, f"饿了么{slot}"), "OK"

    def _yyb_bound_reply(self, session: st.Session, slot: int, remark: str) -> str:
        phone = (session.current_phone or "").strip()
        account = mask_phone(phone) if (phone and len(phone) >= 7 and phone.isdigit()) else remark
        done = (
            f"======饿了么账号绑定======\n"
            f"📱 账号：{account}\n"
            f"🔐 登录状态：✅ 已登录"
        )
        if session.total_accounts:
            return done + "\n\n" + self._advance(session)
        session.reset_flow()
        return done

    def _start_yyb_login(self, session: st.Session) -> str:
        """Guided YYB + elm_cookie flow for the current account.

        1. If the slot is already valid, bind immediately (no trigger —
           one elm_cookie run syncs ALL YYB accounts, so a later account
           in the same round is usually already done).
        2. Else check the YYB account is alive, trigger the elm_cookie
           cron in Qinglong, wait for its log, then verify the slot.

        iLink is reply-only, so the whole run happens synchronously
        inside this one reply (up to ELM_COOKIE_TIMEOUT seconds).
        """
        if self.qinglong is None:
            session.state = st.MENU
            return (
                "❌ 青龙未接通：QL_TOKEN 未设置\n"
                "请先在插件配置里填好 ql_base_url 和 ql_token，"
                "再发“登录”。"
            )
        slot = session.account_index + 1 if session.total_accounts else 1
        ok, remark, note = self._check_ele_slot(slot)
        if ok:
            return self._yyb_bound_reply(session, slot, remark)

        status, detail = self._yyb_status()
        if status == "unreachable":
            session.state = st.MENU
            return (
                f"❌ {detail}\n"
                "请检查 YYB 容器在不在跑（路由器 Docker），"
                f"或打开控制台看看：{self.cfg.yyb_console_url}"
            )
        if status == "dead":
            session.state = st.MENU
            return (
                f"❌ {detail}\n"
                f"请到 YYB 控制台重新扫码登录：{self.cfg.yyb_console_url}\n"
                "扫完再发“登录”。"
            )

        cron = self.qinglong.find_cron_by_command(self.cfg.elm_cookie_task)
        if cron is None:
            session.state = st.MENU
            return (
                "❌ 青龙里没找到 elm_cookie 任务\n"
                "请先把 elm_cookie.py 传进青龙并建一个定时任务"
                "（命令 task elm_cookie.py），再发“登录”。"
            )
        try:
            result = self.qinglong.run_cron_and_wait(
                int(cron.get("id")), timeout=self.cfg.elm_cookie_timeout
            )
        except (QinglongError, TypeError, ValueError) as exc:
            session.state = st.MENU
            return f"❌ 触发 elm_cookie 失败：{exc}\n请检查 QL_TOKEN 和青龙状态，再发“登录”。"

        ok, remark, note = self._check_ele_slot(slot)
        if ok:
            return self._yyb_bound_reply(session, slot, remark)

        session.state = st.MENU
        summary = result.get("summary") or {}
        if result.get("timed_out"):
            head = "❌ elm_cookie 跑超时了（还没出结果）"
        elif summary.get("fail"):
            head = (
                f"❌ elm_cookie 同步失败"
                f"（成功 {summary.get('ok', 0)}，失败 {summary.get('fail')}）"
            )
        elif summary.get("human"):
            head = "⚠️ elm_cookie 需要人工处理（可能要手机验证）"
        else:
            head = "❌ 同步后槽位还是无效"
        lines = [head, f"槽位 {slot}：{note}"]
        tail = _last_log_lines(result.get("log") or "")
        if tail:
            lines.append(f"任务日志：\n{tail}")
        lines.append("请到 YYB 控制台确认账号在线，再发“登录”重跑一次；或改用 [3] / 手机号登录。")
        return "\n".join(lines)

    def _on_ck_paste(self, session: st.Session, text: str) -> str:
        cookie = text.strip()
        ok, note = cookie_has_required_fields(cookie)
        if not ok:
            return f"❌ Cookie 不可用：{note}\n请重新粘贴完整 Cookie，或回复 q 退出。"
        # Live check is best-effort: the router's egress is restricted, so a
        # failed check means "unverified", not "bad" — bind anyway, say so.
        live_ok, live_note = check_cookie_live(cookie)
        bind = self._bind_cookie(session, cookie)
        return f"{bind}\n⚠️ {live_note}" if not live_ok else bind

    def _bind_cookie(self, session: st.Session, cookie: str) -> str:
        # 备注优先用手机号后四位（家里的惯例：8164、0261 都是这么来的），
        # 拿不到手机号时再用 Cookie 里的 USERID 生成。
        # [r] 重登例外：备注必须沿用失效账号原来的备注——0261 的手机号
        # 后四位是 6591，用它会把备注改错位；且全部账号都在 ELM_COOKIE
        # 这一个变量里，重登一律写 slot 1。
        phone = (session.current_phone or "").strip()
        if session.reregister_mode and session.reregister_remark:
            remark = session.reregister_remark
        elif phone and len(phone) >= 4 and phone.isdigit():
            remark = phone[-4:]
        else:
            remark = extract_remark(cookie, f"饿了么{mask_phone(session.current_phone)}")
        slot = 1 if session.reregister_mode else (
            session.account_index + 1 if session.total_accounts else 1)
        session.last_remark = remark
        self._remember_phone(remark, phone)
        if self.qinglong is None:
            session.reset_flow()
            return (
                f"✅ 登录成功：{remark}\n"
                "⚠️ QL_TOKEN 未设置，Cookie 没同步进青龙，"
                "请配好后重登一次。"
            )
        try:
            self.qinglong.sync_ele_cookie(cookie, remark, slot=slot)
        except QinglongError as exc:
            session.reset_flow()
            return f"✅ 登录成功：{remark}\n❌ 同步青龙失败：{exc}\nCookie 未写入，请检查 QL_TOKEN 后重登。"
        account = mask_phone(phone) if (phone and len(phone) >= 7 and phone.isdigit()) else remark
        done = (
            f"======饿了么账号绑定======\n"
            f"📱 账号：{account}\n"
            f"🔐 登录状态：✅ 已登录"
        )
        if session.reregister_mode:
            return done + "\n\n" + self._reregister_advance(session)
        if session.total_accounts:
            return done + "\n\n" + self._advance(session)
        session.reset_flow()
        return done

    def _require_ql(self) -> QinglongClient:
        if self.qinglong is None:
            raise QinglongError("ql_token 未设置：请先在插件配置里填青龙应用 token")
        return self.qinglong

    def _cmd_ele_slots(self) -> str:
        try:
            ql = self._require_ql()
            slots = ql.list_ele_slots()
        except QinglongError as exc:
            return f"❌ {exc}"
        if not slots:
            return "青龙里还没有 ELM_COOKIE，去登录一个账号吧。"
        lines = ["======饿了么 Cookie 槽位======"]
        for s in slots:
            remarks = "、".join(s["remarks"]) if s["remarks"] else "（空）"
            lines.append(f"槽位 {s['slot']}（{s['name']}）：{remarks}")
        return "\n".join(lines)

    def _start_query_menu(self, session: st.Session) -> str:
        session.query_kind = ""
        session.query_accounts = []
        session.state = st.QUERY_MENU
        return QUERY_MENU_TEXT

    def _on_query_menu_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出查询"
        if choice == "1":
            session.query_kind = "star"
            return self._start_star_query_accounts(session)
        if choice == "2":
            session.query_kind = "milktea"
            return self._start_query_accounts(session, "milktea", "奶茶")
        if choice == "3":
            return "📦 物流查询还没接通，先选 [1] 幸运星吧。\n\n" + QUERY_MENU_TEXT
        return "请回复 1 / 2 / 3，或回复 q 退出。"

    def _account_phone(self, remark: str) -> str:
        """Masked phone for a remark, or "" when the account wasn't
        phone-logged-in (the report then falls back to the nickname)."""
        phone = self.remark_phones.get(remark) or ""
        return mask_phone(phone) if phone else ""

    def _start_star_query_accounts(self, session: st.Session) -> str:
        """选查询账号（幸运星）：[0] 全部账号 + 每号一个序号。"""
        return self._start_query_accounts(session, "star", "幸运星")

    def _start_query_accounts(self, session: st.Session,
                              kind: str, title: str) -> str:
        """选查询账号：[0] 全部账号 + 每号一个序号。

        Each line shows the masked phone (phone-logged-in accounts)
        or the remark, plus the USERID — so the user can tell the
        accounts apart before any query runs (nicknames aren't known
        until the homepage call).
        """
        if self.qinglong is None:
            session.state = st.IDLE
            return "❌ 青龙未接通"
        try:
            accounts = self.qinglong.list_ele_cookies()
        except QinglongError as exc:
            session.state = st.IDLE
            return f"❌ {exc}"
        if not accounts:
            session.state = st.IDLE
            return "青龙里还没有饿了么 Cookie，先登录一个账号吧。"
        session.query_kind = kind
        session.query_accounts = [a["remark"] for a in accounts]
        session.state = st.QUERY_ACCOUNT
        lines = [f"=====选择查询账号 ({title})=====",
                 "[0] 🔄 全部账号"]
        for i, acc in enumerate(accounts, start=1):
            remark = acc["remark"]
            label = self._account_phone(remark) or remark
            userid = acc.get("userid") or "未获取"
            lines.append(f"[{i}] {label}（{userid}）")
        lines += ["------------------",
                  "回复序号选择,回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_query_account_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出查询"
        if not choice.isdigit():
            return "请回复序号，或回复 q 退出。"
        idx = int(choice)
        if idx == 0:
            remarks = list(session.query_accounts)
        elif 1 <= idx <= len(session.query_accounts):
            remarks = [session.query_accounts[idx - 1]]
        else:
            return f"请回复 0~{len(session.query_accounts)}，或回复 q 退出。"
        kind = session.query_kind
        session.reset_flow()
        if kind == "milktea":
            return self._run_milk_tea_report(remarks)
        return self._run_star_report(remarks)

    def _run_star_report(self, remarks: list[str]) -> str:
        """每号一块（用户截图的格式）：账号 / USERID / 余额 / 今日获取。

        Accounts are queried in parallel (pool.map keeps the user's
        selection order); one failing account never blocks the rest.
        有手机号的账号行显示脱敏手机号（mask_phone，前 3 位 + **** +
        后 4 位），没有的显示闪购用户名（首页聚合里顺手取的
        nickname），都取不到时退回备注。余额和今日获取按截图格式，
        不带“星”单位、不带 + 号。收支明细（最新 15 条，App 格式）
        跟在每块后面。
        """
        if self.qinglong is None:
            return "❌ 青龙未接通"
        try:
            accounts = {a["remark"]: a
                        for a in self.qinglong.list_ele_cookies()}
        except QinglongError as exc:
            return f"❌ {exc}"

        def _report_one(remark: str) -> str:
            acc = accounts.get(remark) or {}
            row = query_one_account(remark, acc.get("cookie", ""))
            phone = self._account_phone(remark)
            label = phone or row.get("nickname") or remark
            userid = acc.get("userid") or "未获取"
            lines = [f"📱 账号：{label}",
                     f"🆔 USERID：{userid}"]
            if row["balance"] is not None:
                lines.append(f"⭐ 幸运星余额：{row['balance']}")
            else:
                lines.append(f"⭐ 幸运星余额：{row['error'] or '查询失败'}")
            if row["today"] is not None:
                lines.append(f"🎯 今日获取：{row['today']}")
            elif row["balance"] is not None:
                lines.append(f"🎯 今日获取：{row['today_error'] or '查询失败'}")
            if row["today"] is not None:
                lines.append("------------------")
                lines.append("📋 收支明细：")
                lines.extend(format_detail_lines(row.get("details"), limit=15))
            return "\n".join(lines)

        with ThreadPoolExecutor(max_workers=max(1, len(remarks))) as pool:
            blocks = list(pool.map(_report_one, remarks))
        return "\n\n".join(blocks)

    def _run_milk_tea_report(self, remarks: list[str]) -> str:
        """每号一块：奶茶免单卡的本月兑换状态（只读查询，不兑换）。

        Accounts are queried in parallel (pool.map keeps the user's
        selection order); one failing account never blocks the rest.
        账号标签与幸运星查询同一口径：脱敏手机号 > 闪购用户名 > 备注
        （没有手机号时顺手调一次首页拿闪购用户名），并附 🆔 USERID
        （青龙账号里取，取不到写“未获取”，和 [1] 幸运星报告同一格式）。
        状态用 item_status_tag：✅可兑 / ⏰MM-DD HH:MM 开抢 /
        ✅已兑换奶茶免单卡（18元） / 差 X 星 / 当前不可兑；已兑换过
        （OVER_LIMIT）的号附上 extInfo 的限额说明（limit_note）。
        """
        if self.qinglong is None:
            return "❌ 青龙未接通"
        try:
            accounts = {a["remark"]: a
                        for a in self.qinglong.list_ele_cookies()}
        except QinglongError as exc:
            return f"❌ {exc}"

        def _report_one(remark: str) -> str:
            acc = accounts.get(remark) or {}
            cookie = acc.get("cookie") or ""
            phone = self._account_phone(remark)
            nickname = ""
            if not phone and cookie:
                try:
                    _balance, nickname = query_star_homepage(cookie)
                except Exception:  # noqa: BLE001 - 昵称拿不到不拦查询
                    nickname = ""
            label = phone or nickname or remark
            userid = acc.get("userid") or "未获取"
            lines = [f"📱 账号：{label}",
                     f"🆔 USERID：{userid}"]
            if not cookie:
                lines.append("🎁 奶茶免单卡：没有 Cookie，查不到")
                return "\n".join(lines)
            try:
                items = list_mall_items(cookie)
            except ExchangeError as exc:
                lines.append(f"🎁 奶茶免单卡：{exc}")
                return "\n".join(lines)
            mine = [it for it in items
                    if it.get("exchange_id") == MILK_TEA_EXCHANGE_ID]
            if not mine:
                lines.append("🎁 奶茶免单卡：商城里没找到这件商品")
                return "\n".join(lines)
            item = mine[0]
            lines.append(f"🎁 商品：{item.get('name') or '奶茶免单卡'}")
            cost = item.get("cost")
            if cost is not None:
                lines.append(f"⭐ 消耗：{cost} 星")
            else:
                lines.append("⭐ 消耗：星数未知")
            lines.append(f"📦 状态：{item_status_tag(item)}")
            if item.get("status") == "OVER_LIMIT" and item.get("limit_note"):
                lines.append(f"ℹ️ {item['limit_note']}")
            return "\n".join(lines)

        with ThreadPoolExecutor(max_workers=max(1, len(remarks))) as pool:
            blocks = list(pool.map(_report_one, remarks))
        return "\n\n".join(blocks)

    def _cancel_to_menu(self, session: st.Session, text: str) -> str:
        """取消当前流程，回主菜单（顺序登录循环中只退回，不弹菜单）。"""
        session.reset_flow()
        if session.total_accounts > 0:
            return text
        session.state = st.MENU
        return f"{text}\n\n{_menu_text()}"

    def _start_exchange(self, session: st.Session) -> str:
        """兑换流程第 1 步：先出账号信息（并行查各号幸运星余额）。
        选好账号后进商品商城，再到兑换方式、提交定时抢兑。
        """
        if self.qinglong is None:
            return "❌ 青龙未接通"
        try:
            accounts = self.qinglong.list_ele_cookies()
        except QinglongError as exc:
            return f"❌ {exc}"
        if not accounts:
            return "青龙里还没有饿了么 Cookie，先登录一个账号吧。"
        session.exchange_items = []
        session.exchange_item = {}
        session.exchange_accounts = [a["remark"] for a in accounts]
        session.exchange_picks = {}
        session.exchange_remarks = []
        session.exchange_mode = ""
        session.exchange_target = None
        session.exchange_mall_page = 0
        session.exchange_account_balances = {}
        session.exchange_account_nicknames = {}

        def _balance_one(pair):
            remark, cookie = pair
            try:
                # 一次首页调用同时拿余额与闪购用户名（不另发请求）
                balance, nickname = query_star_homepage(cookie or "")
                return remark, balance, nickname
            except Exception:  # noqa: BLE001 - 单号查不到不拦整体
                return remark, None, ""

        with ThreadPoolExecutor(max_workers=max(1, len(accounts))) as pool:
            for remark, balance, nickname in pool.map(
                    _balance_one,
                    [(a["remark"], a.get("cookie") or "") for a in accounts]):
                session.exchange_account_balances[remark] = balance
                session.exchange_account_nicknames[remark] = nickname
        session.state = st.EXCHANGE_ACCOUNTS
        lines = ["=====兑换账号====="]
        for i, remark in enumerate(session.exchange_accounts, start=1):
            balance = session.exchange_account_balances.get(remark)
            tail = f"⭐ {balance} 星" if balance is not None else "⭐ 余额未知"
            # 与幸运星查询同一口径：脱敏手机号 > 闪购用户名 > 备注
            label = (self._account_phone(remark)
                     or session.exchange_account_nicknames.get(remark)
                     or remark)
            lines.append(f"[{i}] 📱 {label} {tail}")
        lines += ["------------------",
                  "[0] 全部账号",
                  "回复序号选择账号（可多选，用逗号分隔，如 1,2），回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_exchange_accounts_choice(self, session: st.Session, text: str) -> str:
        """兑换流程第 1 步：选账号 -> 进商品商城。"""
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出兑换"
        parts = [p.strip() for p in choice.split(",") if p.strip()]
        if not parts or not all(p.isdigit() for p in parts):
            return "请回复序号（可多选，用逗号分隔，如 1,2），或回复 q 退出。"
        idxs = [int(p) for p in parts]
        if 0 in idxs:
            remarks = list(session.exchange_accounts)
        else:
            if not all(1 <= i <= len(session.exchange_accounts) for i in idxs):
                return (f"请回复 0~{len(session.exchange_accounts)}，"
                        "或回复 q 退出。")
            remarks = [session.exchange_accounts[i - 1] for i in idxs]
        session.exchange_remarks = remarks
        # 换号必重拉：商城状态是按账号算的，缓存是上一个号的数据。
        session.exchange_items = []
        return self._start_exchange_mall(session, page=0)

    def _start_exchange_mall(self, session: st.Session, page: int = 0) -> str:
        """兑换流程第 2 步：商品商城（分页，每页 9 件，带状态标签）。

        商城状态是按账号算的（extInfo），所以用首选账号的 Cookie 拉；
        多号时按首选号显示，确认页再按各号逐号查（_build_exchange_picks）。
        """
        if self.qinglong is None:
            session.reset_flow()
            return "❌ 青龙未接通"
        first = (session.exchange_remarks or [""])[0]
        fallback_note = ""
        if not session.exchange_items:
            try:
                accounts = self.qinglong.list_ele_cookies()
            except QinglongError as exc:
                session.reset_flow()
                return f"❌ {exc}"
            if not accounts:
                session.reset_flow()
                return "青龙里还没有饿了么 Cookie，先登录一个账号吧。"
            # 必须用被选账号的 Cookie：这里曾写死 accounts[0]（8164），
            # 0261 选号后商城显示的却是 8164 的超限状态（2026-11-22 实测）。
            by_remark = {a.get("remark"): a for a in accounts}
            picked = by_remark.get(first) or {}
            cookie = picked.get("cookie") or ""
            if not cookie:
                cookie = accounts[0].get("cookie") or ""
                fallback_note = (
                    f"⚠ {first or '所选账号'}没取到 Cookie，"
                    "先按第一个账号的商城显示，状态可能不对。")
            try:
                session.exchange_items = list_mall_items(cookie)
            except ExchangeError as exc:
                session.reset_flow()
                return f"❌ {exc}"
            if not session.exchange_items:
                session.reset_flow()
                return "商城里一件商品都没拉到，请稍后再试。"
        total_pages = max(1, (len(session.exchange_items) + MALL_PAGE_SIZE - 1)
                          // MALL_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        session.exchange_mall_page = page
        session.state = st.EXCHANGE_MALL
        start = page * MALL_PAGE_SIZE
        chunk = session.exchange_items[start:start + MALL_PAGE_SIZE]
        lines = ["=====商品商城====="]
        if fallback_note:
            lines.append(fallback_note)
        elif first:
            label = (self._account_phone(first)
                     or session.exchange_account_nicknames.get(first)
                     or first)
            lines.append(f"📱 账号：{label}（状态按此号显示）")
        for i, item in enumerate(chunk, start=1):
            lines.append(f"[{i}] {describe_item(item)}")
        lines.append(f"📄 第 {page + 1}/{total_pages} 页")
        lines += ["------------------",
                  "回复序号选择商品，回复\"q\"退出",
                  "回复 n 下一页，p 上一页，t 查看已安排的定时兑换",
                  "=================="]
        return "\n".join(lines)

    def _on_exchange_mall_choice(self, session: st.Session, text: str) -> str:
        """兑换流程第 2 步：选商品 / 翻页 / 看定时列表。"""
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出兑换"
        if choice == "t":
            return self._start_sched_list(session)
        if choice == "n":
            return self._start_exchange_mall(session,
                                             session.exchange_mall_page + 1)
        if choice == "p":
            return self._start_exchange_mall(session,
                                             session.exchange_mall_page - 1)
        if not choice.isdigit():
            return "请回复序号选择商品，或 n/p 翻页、q 退出。"
        total_pages = max(1, (len(session.exchange_items) + MALL_PAGE_SIZE - 1)
                          // MALL_PAGE_SIZE)
        start = session.exchange_mall_page * MALL_PAGE_SIZE
        chunk = session.exchange_items[start:start + MALL_PAGE_SIZE]
        idx = int(choice)
        if not 1 <= idx <= len(chunk):
            return (f"请回复 1~{len(chunk)}，或 n/p 翻页、q 退出。")
        session.exchange_item = chunk[idx - 1]
        return self._start_exchange_method(session)

    def _start_exchange_method(self, session: st.Session) -> str:
        """兑换流程第 3 步：兑换方式（立即 / 提交定时抢兑）。"""
        item = session.exchange_item
        session.state = st.EXCHANGE_METHOD
        cost = item.get("cost")
        cost_text = f"{cost}幸运星" if cost is not None else "星数未知"
        status = item_status_tag(item)
        return "\n".join([
            "=====兑换方式=====",
            f"🎁 商品: {item.get('name')}",
            f"⭐ 消耗: {cost_text}",
            f"📦 状态: {status}",
            "------------------",
            "[1] 立即兑换",
            "[2] 提交定时抢兑 (网站按商品开抢时刻只抢一场, 结束后推送结果)",
            "------------------",
            "回复数字选择,回复\"q\"退出",
            "==================",
        ])

    def _on_exchange_method_choice(self, session: st.Session, text: str) -> str:
        """兑换流程第 3 步：1 立即兑、2 提交定时抢兑。"""
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出兑换"
        if choice == "1":
            session.exchange_mode = "now"
            return self._confirm_exchange(session, session.exchange_remarks)
        if choice == "2":
            session.exchange_mode = "scheduled"
            return self._start_exchange_submit(session)
        return "请回复 1 立即兑换、2 提交定时抢兑，或 q 退出。"

    def _start_exchange_submit(self, session: st.Session) -> str:
        """兑换流程第 4 步：提交定时抢兑（自动取下一次开抢时刻，等 y/n）。"""
        item = session.exchange_item
        target = next_exchange_time(item.get("exchange_start_time"))
        if target is None:
            # 拿不到开抢时刻：回退到手动输时间
            session.state = st.EXCHANGE_TIME
            return ("⚠ 这件商品没有开抢时刻，请手动输入兑换时间"
                    "（24 小时制，例如 15:00）。\n"
                    "时间已过会让你重输，不会挪到明天。\n"
                    "回复 q 取消。")
        session.exchange_target = target
        session.state = st.EXCHANGE_SUBMIT
        remarks = session.exchange_remarks
        return "\n".join([
            "=====提交定时抢兑=====",
            f"🎁 商品: {item.get('name')}({len(remarks)} 个账号)",
            f"🕒 抢兑时刻: 下一次 {format_target(target)} 那一场"
            " (只抢这一场, 以网站排定为准)",
            "------------------",
            "到点由网站自动突发兑换,结束后结果推送给你;"
            "同一账号已有的抢兑任务会被替换",
            "确认提交请回复[y],取消请回复[n]",
            "==================",
        ])

    def _on_exchange_submit_choice(self, session: st.Session, text: str) -> str:
        """兑换流程第 4 步：y 提交（替换同账号旧任务），n 取消。"""
        choice = text.strip().lower()
        if choice in ("n", "q"):
            return self._cancel_to_menu(session, "✅ 已取消定时抢兑")
        if choice != "y":
            return "请回复 y 确认提交，或回复 n 取消。"
        item = session.exchange_item
        remarks = list(session.exchange_remarks)
        target = session.exchange_target
        if not remarks or target is None:
            session.reset_flow()
            return "❌ 提交失败：商品或时间丢了，请重新发送“饿了么兑换”。"
        chat_id = session.umo or ""
        if not chat_id:
            session.reset_flow()
            return "❌ 提交失败：拿不到你的会话标识，请重新发送“饿了么兑换”。"
        replaced = self._sched_store.cancel_for_remarks(remarks)
        task = new_scheduled_task(item, remarks, target, chat_id)
        self._sched_store.add(task)
        log.info("已提交定时抢兑 %s：%s，账号 %s，目标 %s，替换旧任务 %d 个",
                 task["id"], item.get("name"), "、".join(remarks),
                 task["target_time"], len(replaced))
        reply = "\n".join([
            "⏰ 已提交定时抢兑：",
            f"🎁 商品：{item.get('name')}",
            f"📱 账号：{'、'.join(remarks)}",
            f"🕒 时间：{format_target(target)}",
            "到时间后自动兑换，结果会发到这里。",
            "回复“定时兑换”可查看/取消。",
        ])
        if replaced:
            reply += f"\n（已替换同一账号的 {len(replaced)} 个旧抢兑任务）"
        session.reset_flow()
        return reply

    def _on_exchange_time_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip()
        if choice.lower() == "q":
            session.reset_flow()
            return "✅ 已退出兑换"
        target, error = parse_hhmm(choice)
        if error:
            return f"❌ {error}\n请重新输入时间（例如 15:00），回复 q 取消。"
        session.exchange_target = target
        return self._confirm_scheduled(session)

    def _confirm_scheduled(self, session: st.Session) -> str:
        """定时汇总：先并行查各号当前状态，再出商品/账号/时间汇总，等 y/n。

        当前不可兑不拦安排（例如未开售的限量档，到点才开售）；
        执行时再重新查一遍实时状态。商城里根本没这件商品才取消。
        """
        item = session.exchange_item
        if self.qinglong is None:
            session.reset_flow()
            return "❌ 青龙未接通"
        lines, picks = self._build_exchange_picks(item, session.exchange_remarks)
        if not picks:
            session.reset_flow()
            return "\n".join(lines + ["商城里没找到这件商品，已取消。"])
        session.exchange_picks = picks
        session.state = st.EXCHANGE_SCHED_CONFIRM
        target = session.exchange_target
        cost = item.get("cost")
        cost_text = f"{cost} 星/件" if cost is not None else "星数未知"
        head = ["=====确认定时兑换=====",
                f"🎁 商品：{item.get('name')}",
                f"⭐ 所需：{cost_text}",
                f"🕒 时间：{format_target(target)}"]
        return "\n".join(head + lines + [
            "------------------",
            "回复 y 确认安排，回复 n 取消",
            "==================",
        ])

    def _on_exchange_sched_confirm_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "y":
            item = session.exchange_item
            remarks = list(session.exchange_picks.keys())
            target = session.exchange_target
            chat_id = session.umo or ""
            if not chat_id:
                session.reset_flow()
                return "❌ 安排失败：拿不到你的会话标识，请重新发送“饿了么兑换”。"
            task = new_scheduled_task(item, remarks, target, chat_id)
            self._sched_store.add(task)
            log.info("已安排定时兑换 %s：%s，账号 %s，目标 %s",
                     task["id"], item.get("name"), "、".join(remarks),
                     task["target_time"])
            reply = "\n".join([
                "⏰ 已安排定时兑换：",
                f"🎁 商品：{item.get('name')}",
                f"📱 账号：{'、'.join(remarks)}",
                f"🕒 时间：{format_target(target)}",
                "到时间后自动兑换，结果会发到这里。",
                "回复“定时兑换”可查看/取消。",
            ])
            session.reset_flow()
            return reply
        if choice in ("n", "q"):
            return self._cancel_to_menu(session, "✅ 已取消定时兑换")
        return "请回复 y 确认安排，或回复 n 取消。"

    def _build_exchange_picks(
        self, item: dict, remarks: list[str],
    ) -> tuple[list[str], dict[str, dict]]:
        """并行拉各号商城看该号能不能兑（状态 / 差星是每号自己的）。

        返回 (提示行, picks)。picks 只收商城里找得到的号（不管当前可不可兑；
        执行时 exchange_item 会再拦一次未就绪的）。每号一次只读请求，
        并行跑；回复顺序仍按用户选号的顺序。
        """
        if self.qinglong is None:
            return ["❌ 青龙未接通"], {}
        try:
            accounts = {a["remark"]: a["cookie"]
                        for a in self.qinglong.list_ele_cookies()}
        except QinglongError as exc:
            return [f"❌ {exc}"], {}

        def _confirm_one(remark: str) -> tuple[str, str, dict | None]:
            cookie = accounts.get(remark, "")
            if not cookie:
                return remark, f"📱 {remark}：没有 Cookie，跳过", None
            try:
                mine = [it for it in list_mall_items(cookie)
                        if it.get("exchange_id") == item.get("exchange_id")]
            except ExchangeError as exc:
                return remark, f"📱 {remark}：{exc}，跳过", None
            if not mine:
                return remark, f"📱 {remark}：商城里没找到这件商品，跳过", None
            mine = mine[0]
            state = "可兑换" if mine.get("ready") else (mine.get("reason") or "不可兑换")
            return remark, f"📱 {remark}：{state}", mine

        with ThreadPoolExecutor(max_workers=max(1, len(remarks))) as pool:
            results = list(pool.map(_confirm_one, remarks))
        lines: list[str] = []
        picks: dict[str, dict] = {}
        for remark, line, mine in results:
            lines.append(line)
            if mine is not None:
                picks[remark] = mine
        return lines, picks

    def _confirm_exchange(self, session: st.Session, remarks: list[str]) -> str:
        """确认页：并行拉各号商城看该号能不能兑，再等 y/n。"""
        item = session.exchange_item
        if self.qinglong is None:
            session.reset_flow()
            return "❌ 青龙未接通"
        cost = item.get("cost")
        cost_text = f"{cost} 星/件" if cost is not None else "星数未知"
        lines = ["=====确认兑换=====",
                 f"🎁 商品：{item.get('name')}",
                 f"⭐ 所需：{cost_text}"]
        confirm_lines, picks = self._build_exchange_picks(item, remarks)
        lines += confirm_lines
        if not picks:
            session.reset_flow()
            return "\n".join(lines + ["没有能兑换的账号，已取消。"])
        session.exchange_picks = picks
        session.state = st.EXCHANGE_CONFIRM
        lines += ["------------------",
                  "回复 y 确认兑换，回复 n 取消",
                  "=================="]
        return "\n".join(lines)

    def _on_exchange_confirm_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "y":
            picks = dict(session.exchange_picks)
            session.reset_flow()
            return self._run_exchange(picks)
        if choice in ("n", "q"):
            return self._cancel_to_menu(session, "✅ 已取消兑换")
        return "请回复 y 确认兑换，或回复 n 取消。"

    def _run_exchange(self, picks: dict[str, dict]) -> str:
        """并行兑换（只兑确认过的那一件），每号一块结果。

        每号只发一次兑换请求（响应异常不重试，结果未知时绝不补发）；
        回复顺序仍按确认页的顺序。
        """
        if self.qinglong is None:
            return "❌ 青龙未接通"
        try:
            accounts = {a["remark"]: a["cookie"]
                        for a in self.qinglong.list_ele_cookies()}
        except QinglongError as exc:
            return f"❌ {exc}"

        def _run_one(pair: tuple[str, dict]) -> str:
            remark, item = pair
            cookie = accounts.get(remark, "")
            if not cookie:
                return f"📱 账号：{remark}\n❌ 没有 Cookie，未兑换"
            result = exchange_one_item(cookie, item)
            lines = [f"📱 账号：{remark}", result]
            if result.startswith("✅"):
                try:
                    balance = query_star_balance(cookie)
                    lines.append(f"⭐ 兑换后余额：{balance} 星")
                except Exception:  # noqa: BLE001 - 余额查不到不影响兑换结果
                    pass
            return "\n".join(lines)

        with ThreadPoolExecutor(max_workers=max(1, len(picks))) as pool:
            blocks = list(pool.map(_run_one, list(picks.items())))
        return "\n\n".join(blocks)

    def _start_sched_list(self, session: st.Session) -> str:
        """列出待执行的定时兑换，可按编号取消。"""
        tasks = self._sched_store.pending()
        if not tasks:
            session.reset_flow()
            return "⏰ 没有已安排的定时兑换。"
        session.sched_list_ids = [t["id"] for t in tasks]
        session.state = st.EXCHANGE_SCHED_LIST
        now = now_beijing()
        lines = ["=====定时兑换====="]
        for i, task in enumerate(tasks, start=1):
            item = task.get("item") or {}
            target = task_target(task)
            when = format_target(target, now) if target else "时间未知"
            lines.append(
                f"[{i}] {item.get('name')} | "
                f"账号 {'、'.join(task.get('remarks') or [])} | "
                f"{when}")
        lines += ["------------------",
                  "回复序号取消该任务，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _on_exchange_sched_list_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "q":
            return self._cancel_to_menu(session, "✅ 已退出定时兑换")
        if not choice.isdigit():
            return "请回复序号，或回复 q 退出。"
        idx = int(choice)
        if not 1 <= idx <= len(session.sched_list_ids):
            return f"请回复 1~{len(session.sched_list_ids)}，或回复 q 退出。"
        task_id = session.sched_list_ids[idx - 1]
        task = self._sched_store.get(task_id)
        if task is None or task.get("status") not in ("pending", "prewarmed"):
            return "这个任务已经不在待执行里了，重新发送“定时兑换”查看。"
        task["status"] = "cancelled"
        self._sched_store.update(task)
        log.info("已取消定时兑换 %s：%s，目标 %s",
                 task["id"], (task.get("item") or {}).get("name"),
                 task.get("target_time"))
        remaining = self._sched_store.pending()
        if not remaining:
            session.reset_flow()
            return (f"✅ 已取消定时兑换：{(task.get('item') or {}).get('name')}。\n"
                    "没有已安排的定时兑换了。")
        session.sched_list_ids = [t["id"] for t in remaining]
        session.state = st.EXCHANGE_SCHED_LIST
        now = now_beijing()
        lines = ["=====定时兑换=====",
                 f"✅ 已取消：{(task.get('item') or {}).get('name')}"]
        for i, rest in enumerate(remaining, start=1):
            item = rest.get("item") or {}
            target = task_target(rest)
            when = format_target(target, now) if target else "时间未知"
            lines.append(
                f"[{i}] {item.get('name')} | "
                f"账号 {'、'.join(rest.get('remarks') or [])} | "
                f"{when}")
        lines += ["------------------",
                  "回复序号取消该任务，回复\"q\"退出",
                  "=================="]
        return "\n".join(lines)

    def _start_cookie_menu(self, session: st.Session) -> str:
        session.state = st.COOKIE_MENU
        return COOKIE_MENU_TEXT

    def _start_eleme_manage(self, session: st.Session) -> str:
        """「饿了么管理」：单独的 Cookie 管理菜单（图1）。

        只回菜单，不自动检查：检查（图2 的 Cookie 检查块）只在用户
        在菜单里按 [s] 后才跑、才显示；[r] 对查出的失效号（含无法
        验证）逐个走重登（复用 _start_reregister，不用手动挑号）。"""
        session.state = st.COOKIE_MENU
        return COOKIE_MENU_TEXT

    def _on_cookie_menu_choice(self, session: st.Session, text: str) -> str:
        choice = text.strip().lower()
        if choice == "q":
            session.reset_flow()
            return "✅ 已退出 Cookie 管理"
        if choice == "s":
            # 只回检查块（图2），后面不再重复贴菜单。
            return self._run_cookie_check()
        if choice == "饿了么管理":
            # 在菜单里重发命令也只回菜单，不自动检查。
            return COOKIE_MENU_TEXT
        if choice == "r":
            return self._start_reregister(session)
        return "请回复 s / r / q。\n\n" + COOKIE_MENU_TEXT

    def _run_cookie_check(self) -> str:
        if self.qinglong is None:
            return "❌ 青龙未接通"
        lines = ["=====Cookie 检查====="]
        for row in check_all(self.qinglong):
            mark = {"ok": "✅", "dead": "❌", "unknown": "❓"}.get(row["status"], "❓")
            label = {"ok": "正常", "dead": "已失效", "unknown": "无法验证"}.get(
                row["status"], row["status"])
            # 检查块按图2 原样：每号一行“状态（余额/原因）”——余额是
            # 判断活性的探针读数，[s] 的输出里保留（2026-11-22 用户
            # 两张图定版）；进菜单不自动检查，所以余额只在按 [s] 后出现。
            if row.get("note"):
                lines.append(f"{mark} {row['remark']}：{label}（{row['note']}）")
            else:
                lines.append(f"{mark} {row['remark']}：{label}")
        return "\n".join(lines)

    def _start_reregister(self, session: st.Session) -> str:
        if self.qinglong is None:
            return "❌ 青龙未接通"
        rows = check_all(self.qinglong)
        expired = [row["remark"] for row in rows if row["status"] == "dead"]
        unknown = [row for row in rows if row["status"] == "unknown"]
        if not expired and not unknown:
            return "✅ 没有失效的账号，全部正常。\n\n" + COOKIE_MENU_TEXT
        # “无法验证”的号也要重登确认：[s] 里 ❓ 的号在 [r] 里不能当不
        # 存在（2026-11-22：8164 无法验证，[r] 却回“全部正常”）。
        # 顺序：明确失效的在前，无法验证的在后。
        queue = expired + [row["remark"] for row in unknown]
        session.reregister_queue = queue
        session.reregister_mode = True
        session.reregister_had_unknown = bool(unknown)
        # Phone-logged-in accounts get their code re-sent directly:
        # phones remembered from earlier SMS binds first, then the
        # hardcoded defaults; unknown numbers are asked for inline.
        session.accounts = [
            self.remark_phones.get(remark) or ""
            for remark in queue]
        session.account_index = 0
        head = ""
        if expired:
            head += "🔑 开始重新登录失效账号：%s\n" % "、".join(expired)
        if unknown:
            notes = "；".join(f"{row['remark']}（{row['note']}）" for row in unknown)
            head += f"⚠️ 无法验证的账号也一并重新登录：{notes}\n"
        return head + self._reregister_next(session)

    def _reregister_next(self, session: st.Session) -> str:
        idx = session.account_index
        remark = session.reregister_queue[idx]
        session.reregister_remark = remark
        phone = session.accounts[idx] if idx < len(session.accounts) else ""
        if phone:
            return self._send_sms(session, phone)
        session.state = st.REREGISTER_PHONE
        return (f"📱 账号 {remark} 的手机号是多少？\n"
                "（以前没留过这个号，发我手机号，我发验证码给你）")

    def _reregister_advance(self, session: st.Session) -> str:
        session.account_index += 1
        if session.account_index >= len(session.reregister_queue):
            had_unknown = session.reregister_had_unknown
            session.reregister_mode = False
            session.reregister_queue = []
            session.reregister_had_unknown = False
            session.reset_flow()
            if had_unknown:
                return "✅ 账号已全部重新登录（无法验证的号已按失效处理）。"
            return "✅ 失效账号已全部重新登录。"
        return self._reregister_next(session)

    def _on_reregister_phone(self, session: st.Session, text: str) -> str:
        phone = text.strip()
        if not phone.isdigit() or len(phone) < 11:
            return "手机号看着不对，重新发一下 11 位手机号。"
        idx = session.account_index
        if idx < len(session.accounts):
            session.accounts[idx] = phone
        else:
            session.accounts.append(phone)
        return self._send_sms(session, phone)

    def _cmd_list_crons(self) -> str:
        try:
            crons = self._require_ql().list_crons()
        except QinglongError as exc:
            return f"❌ {exc}"
        if not crons:
            return "青龙里还没有定时任务。"
        lines = ["======青龙定时任务======"]
        for c in crons[:20]:
            state = "停用" if c.get("isDisabled") else "启用"
            lines.append(f"{c.get('id')} {c.get('name')} [{state}] {c.get('schedule') or ''}")
        if len(crons) > 20:
            lines.append(f"…共 {len(crons)} 个，只列前 20 个")
        return "\n".join(lines)

    def _cmd_list_envs(self) -> str:
        try:
            envs = self._require_ql().list_envs()
        except QinglongError as exc:
            return f"❌ {exc}"
        if not envs:
            return "青龙里还没有环境变量。"
        lines = ["======青龙环境变量======"]
        for e in envs[:30]:
            # Values are never shown; only the name (+ remarks) is listed.
            lines.append(f"{e.get('name')}（{e.get('remarks') or '无备注'}）")
        if len(envs) > 30:
            lines.append(f"…共 {len(envs)} 个，只列前 30 个")
        return "\n".join(lines)

    def _cmd_list_scripts(self) -> str:
        try:
            scripts = self._require_ql().list_scripts()
        except QinglongError as exc:
            return f"❌ {exc}"
        if not scripts:
            return "青龙里还没有脚本。"
        names = [str(s.get("name") or s) for s in scripts]
        lines = ["======青龙脚本======", *names[:30]]
        if len(names) > 30:
            lines.append(f"…共 {len(names)} 个，只列前 30 个")
        return "\n".join(lines)

    def _ask_ql_id(self, session: st.Session, cmd: str) -> str:
        mapping = {
            "青龙日志": st.QL_LOG_ID, "青龙执行": st.QL_RUN_ID,
            "青龙停止": st.QL_STOP_ID, "青龙启用": st.QL_ENABLE_ID,
            "青龙禁用": st.QL_DISABLE_ID,
        }
        session.state = mapping[cmd]
        return f"请发任务 ID（数字），如 66。回复 q 取消。"

    def _on_ql_id(self, session: st.Session, text: str) -> str:
        if not text.strip().isdigit():
            return "请发数字任务 ID，或回复 q 取消。"
        action = {
            st.QL_LOG_ID: "日志", st.QL_RUN_ID: "执行", st.QL_STOP_ID: "停止",
            st.QL_ENABLE_ID: "启用", st.QL_DISABLE_ID: "禁用",
        }[session.state]
        session.state = st.IDLE
        return self._run_ql_action(action, int(text.strip()))

    def _run_ql_action(self, action: str, cron_id: int) -> str:
        try:
            ql = self._require_ql()
            cron = ql.get_cron(cron_id)
            name = (cron or {}).get("name") or f"任务{cron_id}"
            if action == "日志":
                raw = ql.get_cron_log(cron_id)
                tail = "\n".join(raw.strip().splitlines()[-15:]) if raw.strip() else "（暂无日志）"
                if len(tail) > 1500:
                    tail = tail[-1500:]
                return f"======{name} 最近日志======\n{tail}"
            if action == "执行":
                ql.run_cron(cron_id)
                return f"✅ 已执行：{name}（{cron_id}）"
            if action == "停止":
                ql.stop_cron(cron_id)
                return f"✅ 已停止：{name}（{cron_id}）"
            if action == "启用":
                ql.enable_cron(cron_id)
                return f"✅ 已启用：{name}（{cron_id}）"
            if action == "禁用":
                ql.disable_cron(cron_id)
                return f"✅ 已禁用：{name}（{cron_id}）"
        except QinglongError as exc:
            return f"❌ {exc}"
        return f"❌ 未知操作：{action}"

