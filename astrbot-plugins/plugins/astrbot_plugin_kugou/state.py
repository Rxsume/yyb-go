"""Per-user conversation state.

Kept in memory only — a restart drops in-flight logins (the user just
re-sends the command). Nothing secret is ever written to disk here:
``validate_token`` lives only in the live Session object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# States
IDLE = "idle"
MENU = "menu"               # waiting for 1/2/3/s/q
SMS_PHONE = "sms_phone"     # waiting for the phone number
SMS_CODE = "sms_code"       # waiting for the 6-digit code
BROWSER_PHONE = "browser_phone"  # 手机号登录 waiting for the phone number
BROWSER_CODE = "browser_code"    # 手机号登录 waiting for the SMS code (driver held in BrowserLoginManager)
CK_PASTE = "ck_paste"       # waiting for a pasted cookie
QL_LOG_ID = "ql_log_id"     # waiting for a task id (log)
QL_RUN_ID = "ql_run_id"     # waiting for a task id (run)
QL_STOP_ID = "ql_stop_id"   # waiting for a task id (stop)
QL_ENABLE_ID = "ql_enable_id"
QL_DISABLE_ID = "ql_disable_id"
QUERY_MENU = "query_menu"   # 饿了么查询：waiting for 1/2/3/q
QUERY_ACCOUNT = "query_account"  # 选查询账号：waiting for 0/1..N/q
EXCHANGE_PRODUCT = "exchange_product"  # 选兑换商品：waiting for 1..N/q
EXCHANGE_ACCOUNT = "exchange_account"  # 选兑换账号：waiting for 0/1..N/q
EXCHANGE_MODE = "exchange_mode"      # 选兑换方式：waiting for 1=立即/2=定时/q
EXCHANGE_TIME = "exchange_time"      # 定时兑换：waiting for HH:MM/q
EXCHANGE_CONFIRM = "exchange_confirm"  # 确认兑换：waiting for y/n
EXCHANGE_SCHED_CONFIRM = "exchange_sched_confirm"  # 确认定时兑换：waiting for y/n
EXCHANGE_QUICK_MODE = "exchange_quick_mode"  # 快捷确认：选商品后自动填时间+默认账号，waiting for 1=立即/2=定时（默认）/3=自定义
EXCHANGE_ACCOUNTS = "exchange_accounts"  # 兑换：先出账号信息（带星余额），waiting for 0=全部/序号/q
EXCHANGE_MALL = "exchange_mall"  # 兑换：商品商城（分页），waiting for 序号/n=下一页/p=上一页/q
EXCHANGE_METHOD = "exchange_method"  # 兑换：兑换方式，waiting for 1=立即/2=定时抢兑/q
EXCHANGE_SUBMIT = "exchange_submit"  # 兑换：提交定时抢兑确认，waiting for y/n
EXCHANGE_SCHED_LIST = "exchange_sched_list"  # 查看/取消定时兑换：waiting for 序号/q
COOKIE_MENU = "cookie_menu"  # 饿了么管理（Cookie 管理）：waiting for s/r/q
REREGISTER_PHONE = "reregister_phone"  # [r] 重登：等该失效账号的手机号
KG_QR_WAIT = "kg_qr_wait"    # 酷狗概念版扫码：二维码已发出，后台轮询中；waiting for 1=换一张/q
KW_ACCOUNTS = "kw_accounts"  # 酷狗提现：waiting for 0=全部/序号/t=定时列表/q
KW_TIER = "kw_tier"          # 酷狗提现：waiting for 1-5=档位/q
KW_MODE = "kw_mode"          # 酷狗提现：waiting for 1=立即/2=定时抢兑/q
KW_LIST = "kw_list"          # 查看/取消定时提现：waiting for 序号/q


@dataclass
class Session:
    wxid: str
    state: str = IDLE
    # QQ 会话 UMO（AstrBot 传输层填）：定时任务的推送目标。TG 版这里是
    # Telegram chat id（int），QQ 版是 UMO 字符串。
    umo: str = ""
    # Sequential re-login loop ("重新登录 i/n")
    accounts: list[str] = field(default_factory=list)  # phones, in order
    account_index: int = 0                             # 0-based
    # In-flight SMS login
    phone: str = ""
    validate_token: str = ""
    # Last bound account, for the success message
    last_remark: str = ""
    # Query menu flow ("饿了么查询")
    query_kind: str = ""             # "star" | "milktea" | "logistics"
    query_accounts: list[str] = field(default_factory=list)  # remarks, in menu order
    # Exchange flow ("饿了么兑换")
    exchange_items: list[dict] = field(default_factory=list)  # 商品，菜单顺序
    exchange_item: dict = field(default_factory=dict)         # 选中的商品
    exchange_accounts: list[str] = field(default_factory=list)  # 备注，菜单顺序
    exchange_picks: dict = field(default_factory=dict)  # 备注 -> 该号的商品（含可兑状态）
    exchange_remarks: list[str] = field(default_factory=list)  # 已选的兑换账号
    exchange_mode: str = ""               # "now" 立即兑换 / "scheduled" 定时兑换
    exchange_target: Any = None           # 定时兑换目标时间（datetime，内存态）
    exchange_mall_page: int = 0           # 商城当前页（0 起）
    exchange_account_balances: dict = field(default_factory=dict)  # 备注 -> 幸运星余额
    exchange_account_nicknames: dict = field(default_factory=dict)  # 备注 -> 闪购用户名
    # Scheduled exchange list ("定时兑换" 查看/取消)
    sched_list_ids: list[str] = field(default_factory=list)  # 待执行任务 id，菜单顺序
    # Re-register flow ("[r] 一键重新登录失效账号")
    reregister_queue: list[str] = field(default_factory=list)  # 待重登的备注，按顺序
    reregister_mode: bool = False  # True 时绑好一个号自动给下一个发码
    reregister_remark: str = ""    # 正在重登的备注（备注优先于手机号后四位）
    reregister_had_unknown: bool = False  # 本轮队列里有没有“无法验证”的号（完成语要区分）
    # Kugou Concept login flow ("酷狗登录")
    kg_qr_key: str = ""        # 当前待扫的二维码 key（后台轮询用）
    kg_qr_gen: int = 0         # 二维码代数：换一张/取消/退出时 +1，旧轮询线程见代数变了就静默退出
    kg_scanned: bool = False   # 当前二维码已扫过（"已扫码"通知只发一次）
    kg_concept_state: dict = field(default_factory=dict)  # 概念版登录内存态（device + qr_key；token/userid 只在登录成功的瞬间经过，永不落盘）
    # Kugou withdrawal flow ("酷狗提现")
    kw_accounts: list[str] = field(default_factory=list)  # 备注，菜单顺序（KGGNB 行顺序）
    kw_remarks: list[str] = field(default_factory=list)   # 已选的提现账号
    kw_checks: dict = field(default_factory=dict)  # 备注 -> check_accounts 结果
    kw_coins: int = 0          # 目标档位金币数（默认 50000=5 元）
    kw_list_ids: list[str] = field(default_factory=list)  # 定时提现任务 id，菜单顺序

    @property
    def total_accounts(self) -> int:
        return len(self.accounts)

    @property
    def current_phone(self) -> str:
        if 0 <= self.account_index < len(self.accounts):
            return self.accounts[self.account_index]
        return self.phone

    def reset_flow(self) -> None:
        """Drop the in-flight login, keep nothing secret."""
        self.state = IDLE
        self.phone = ""
        self.validate_token = ""
        # Abandon any pending Kugou QR poll (its generation check exits it).
        self.kg_qr_gen += 1
        self.kg_qr_key = ""
        self.kg_scanned = False
        self.kg_concept_state = {}
        self.kw_accounts = []
        self.kw_remarks = []
        self.kw_checks = {}
        self.kw_coins = 0
        self.kw_list_ids = []


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, wxid: str) -> Session:
        session = self._sessions.get(wxid)
        if session is None:
            session = Session(wxid=wxid)
            self._sessions[wxid] = session
        return session

    def reset(self, wxid: str) -> Session:
        session = Session(wxid=wxid)
        self._sessions[wxid] = session
        return session
