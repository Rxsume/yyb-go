"""定时兑换：任务持久化 + 调度线程（供 Telegram 机器人用）。

一个定时兑换任务 = 一件已选好的商城商品 + 一批账号 + 一个目标时间。
机器人启动时从 ``data/scheduled_exchanges.json`` 重载未执行的任务；
调度线程每 ``POLL_SECONDS`` 秒扫一遍：

- 目标时间前 5 分钟（PREWARM_SECONDS）：预热——每账号并行拉一次商城
  列表（与青龙兑换脚本的预热一个思路：先把会话/令牌跑热，到点直接兑，
  不在开抢那一刻付首请求冷启动的代价）。预热只记状态，不锁兑换资格，
  执行时再重新查一遍各号实时状态。
- 到时间：重新查各号实时状态，走现成的并行兑换路径执行一次，
  结果用 Telegram 消息发给用户。
- 启动时才发现已过目标时间：不到 5 分钟立即补跑；超过 5 分钟标过期，
  并通知用户（不悄悄补兑）。

安全规则与立即兑换一致：每账号只发一次兑换请求；兑换响应异常
（结果未知）绝不自动重试；执行异常只记一次，不重复执行。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# 用户说的时间一律是北京时间；机器时钟是 UTC，naive 的 datetime.now()
# 会晚 8 小时（15:00 当成 15:00 UTC = 23:00 北京时间）。所有定时时间
# 一律用带 Asia/Shanghai 时区的 aware 时间；旧存的 naive 时间按北京
# 时间理解（兼容 se-5ecafbb35c 这类已存任务）。
BEIJING = ZoneInfo("Asia/Shanghai")


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

# 目标时间前多少秒预热；目标时间过后多少秒内还可补跑。
PREWARM_SECONDS = 300
CATCHUP_SECONDS = 300
POLL_SECONDS = 15
# 开售瞬间误判重查：执行时没有任何 ready 的 pick 且可能翻盘时，
# 等 RECHECK_SECONDS 秒重查一次 build_picks，最多 RECHECK_MAX 次
# （即约 +10 秒、+20 秒各查一次）。只重查只读的 build_picks，
# 绝不重试兑换请求本身。
RECHECK_SECONDS = 10
RECHECK_MAX = 2

STATUS_PENDING = "pending"        # 已安排，等时间到
STATUS_PREWARMED = "prewarmed"    # 已预热，等时间到
STATUS_DONE = "done"              # 已执行（结果见 result）
STATUS_EXPIRED = "expired"        # 过了补跑窗口，未执行
STATUS_CANCELLED = "cancelled"    # 用户取消
STATUS_ERROR = "error"            # 执行时抛异常（未重试，结果见 result）

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_PREWARMED)

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(text: str, now: datetime | None = None) -> tuple[datetime | None, str | None]:
    """解析「HH:MM」（今天）。返回 (目标时间, None) 或 (None, 错误原因）。

    时间已过不挪到明天：直接报错，让用户重输。
    """
    now = _as_beijing(now or now_beijing())
    match = _HHMM_RE.match((text or "").strip())
    if not match:
        return None, "时间格式不对，请用 24 小时制，例如 15:00。"
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None, "时间不对：小时 0~23，分钟 0~59，例如 15:00。"
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        return None, (
            f"这个时间已经过了（现在 {now.strftime('%H:%M')}），"
            "请重新输入今天稍晚的时间。"
        )
    return target, None


def format_target(target: datetime, now: datetime | None = None) -> str:
    """「今天 15:00」/「明天 00:05」这种给用户看的时间（北京时间）。"""
    target = _as_beijing(target)
    now = _as_beijing(now or now_beijing())
    day = "今天" if target.date() == now.date() else "明天"
    return f"{day} {target.strftime('%H:%M')}"


def new_scheduled_task(
    item: dict,
    remarks: list[str],
    target: datetime,
    chat_id: str,
    now: datetime | None = None,
) -> dict:
    """建一个待执行的定时兑换任务（只存 JSON 能写的东西）。"""
    now = _as_beijing(now or now_beijing())
    target = _as_beijing(target)
    return {
        "id": "se-" + uuid.uuid4().hex[:10],
        "item": {
            "name": item.get("name") or "商品",
            "exchange_id": item.get("exchange_id") or "",
            "cost": item.get("cost"),
            "exchange_act_id": item.get("exchange_act_id") or "",
            "exchange_collection_id": item.get("exchange_collection_id") or "",
            "act_code": item.get("act_code") or "",
        },
        "remarks": list(remarks),
        "target_time": target.isoformat(timespec="seconds"),
        "chat_id": str(chat_id),
        "created_at": now.isoformat(timespec="seconds"),
        "status": STATUS_PENDING,
        "prewarmed_at": None,
        "result": None,
    }


def task_target(task: dict) -> datetime | None:
    try:
        return _as_beijing(datetime.fromisoformat(str(task.get("target_time") or "")))
    except ValueError:
        return None


def is_due(task: dict, now: datetime) -> bool:
    """到执行时间了（且未执行过）。"""
    now = _as_beijing(now)
    target = task_target(task)
    return (
        task.get("status") in ACTIVE_STATUSES
        and target is not None
        and now >= target
    )


def is_catchup(task: dict, now: datetime) -> bool:
    """刚过目标时间不久（补跑窗口内）。"""
    now = _as_beijing(now)
    target = task_target(task)
    return (
        task.get("status") in ACTIVE_STATUSES
        and target is not None
        and timedelta(0) < now - target <= timedelta(seconds=CATCHUP_SECONDS)
    )


def is_stale(task: dict, now: datetime) -> bool:
    """过了补跑窗口：不再执行，标过期。"""
    now = _as_beijing(now)
    target = task_target(task)
    return (
        task.get("status") in ACTIVE_STATUSES
        and target is not None
        and now - target > timedelta(seconds=CATCHUP_SECONDS)
    )


def needs_prewarm(task: dict, now: datetime) -> bool:
    """进入预热窗口（目标时间前 5 分钟内）且还没预热过。"""
    now = _as_beijing(now)
    target = task_target(task)
    return (
        task.get("status") == STATUS_PENDING
        and target is not None
        and timedelta(0) <= target - now <= timedelta(seconds=PREWARM_SECONDS)
    )


def _any_ready(picks: dict) -> bool:
    """有没有至少一个 ready 的 pick。"""
    return any(bool(p.get("ready")) for p in picks.values())


def _may_flip(picks: dict) -> bool:
    """开售瞬间可能翻盘：商品还没进列表（picks 为空），或至少一号的
    原因是「已超限」/「未开售」/「EXCHANGE_NOT_STARTED」——开售那一秒
    服务端还没放开，读到的限额状态可能是误读（se-0a04e5f08f：机器人
    10:00:00 判已超限跳过，用户 13 秒后手动抢到了）。注意
    EXCHANGE_NOT_STARTED 是原样状态串：_readiness 只把
    SECKILL_NOT_STARTED 翻成「未开售」，EXCHANGE_NOT_STARTED（奶茶卡
    这类商品开售前的状态）是原样返回的，所以这里必须按原串认，否则
    奶茶卡在 10:00/15:00 整点那一秒读到它时不会重查——重查正是为这
    个开售瞬间竞态准备的。reason 是「差 X 星」开头的一律不翻盘
    （余额 20 秒内不会变），其他原因也不重查。"""
    if not picks:
        return True
    return any(
        str(p.get("reason") or "") in ("已超限", "未开售", "EXCHANGE_NOT_STARTED")
        for p in picks.values()
    )


class ScheduledExchangeStore:
    """定时兑换任务的 JSON 持久化（path 为 None 时只放内存，供测试用）。"""

    def __init__(self, path: str | None):
        self.path = path
        self._lock = threading.Lock()
        self._memory: list[dict] = []

    # -- 读写 ---------------------------------------------------------------
    def load(self) -> list[dict]:
        with self._lock:
            if self.path is None:
                return [dict(t) for t in self._memory]
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                return []
            except (OSError, ValueError):
                log.warning("定时兑换任务文件损坏，视为空：%s", self.path)
                return []
            if not isinstance(data, list):
                return []
            return [t for t in data if isinstance(t, dict)]

    def save(self, tasks: list[dict]) -> None:
        with self._lock:
            if self.path is None:
                self._memory = [dict(t) for t in tasks]
                return
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(tasks, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # -- 常用操作 -------------------------------------------------------------
    def add(self, task: dict) -> dict:
        tasks = self.load()
        tasks.append(task)
        self.save(tasks)
        return task

    def update(self, task: dict) -> None:
        tasks = self.load()
        for i, old in enumerate(tasks):
            if old.get("id") == task.get("id"):
                tasks[i] = task
                break
        else:
            tasks.append(task)
        self.save(tasks)

    def get(self, task_id: str) -> dict | None:
        for task in self.load():
            if task.get("id") == task_id:
                return task
        return None

    def pending(self) -> list[dict]:
        """未执行的任务（待执行 + 已预热），按目标时间排序。"""
        tasks = [t for t in self.load() if t.get("status") in ACTIVE_STATUSES]
        tasks.sort(key=lambda t: str(t.get("target_time") or ""))
        return tasks

    def cancel_for_remarks(self, remarks: list[str]) -> list[dict]:
        """取消这些账号已有的待执行任务（提交新抢兑时替换旧任务）。
        返回被取消的任务。"""
        wanted = set(remarks)
        tasks = self.load()
        cancelled: list[dict] = []
        for task in tasks:
            if task.get("status") not in ACTIVE_STATUSES:
                continue
            if wanted & set(task.get("remarks") or []):
                task["status"] = "cancelled"
                cancelled.append(task)
        if cancelled:
            self.save(tasks)
        return cancelled


class ScheduledExchangeRunner:
    """调度线程：轮询任务文件，到点预热/执行/过期处理。

    build_picks(item, remarks) -> (提示行, picks)：复用机器人确认页的
    并行 readiness 检查（只读）。execute(picks) -> str：复用机器人
    并行的 _run_exchange。notify(chat_id, text)：推送消息（chat_id 是 QQ 会话 UMO）。
    """

    def __init__(
        self,
        store: ScheduledExchangeStore,
        build_picks: Callable[[dict, list[str]], tuple[list[str], dict]],
        execute: Callable[..., str],
        notify: Callable[[int, str], None],
        poll_seconds: int = POLL_SECONDS,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.store = store
        self.build_picks = build_picks
        self.execute = execute
        self.notify = notify
        self.poll_seconds = poll_seconds
        self.clock = clock or now_beijing
        # 睡眠可注入（默认 time.sleep）：开售重查要在 _run 里等 10 秒，
        # 测试用假 sleeper 就不用真等。
        self.sleeper = sleeper or time.sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- 单次扫描（可单测；线程循环只是定时调它） ------------------------------
    def process_once(self, now: datetime | None = None) -> None:
        now = _as_beijing(now or self.clock())
        for task in self.store.pending():
            target = task_target(task)
            if target is None:
                continue
            try:
                if is_stale(task, now):
                    self._expire(task, target, now)
                elif is_due(task, now):
                    self._run(task, now)
                elif needs_prewarm(task, now):
                    self._prewarm(task, now)
            except Exception:  # noqa: BLE001 - 一个任务出错不影响其他任务
                log.exception("处理定时兑换任务 %s 时出错", task.get("id"))

    def _expire(self, task: dict, target: datetime, now: datetime) -> None:
        task["status"] = STATUS_EXPIRED
        task["result"] = f"已过期：目标时间 {format_target(target, now)}，已过 5 分钟未执行"
        self.store.update(task)
        log.info("定时兑换任务 %s 已过期（目标 %s）", task.get("id"), task.get("target_time"))
        self._notify_safe(
            task["chat_id"],
            f"⏰ 定时兑换已过期，未执行：\n🎁 商品：{task['item']['name']}\n"
            f"🕒 目标时间：{format_target(target, now)}\n"
            "机器人当时没在跑或错过了时间，需要的话重新安排一次。",
        )

    def _prewarm(self, task: dict, now: datetime) -> None:
        lines, picks = self.build_picks(task["item"], list(task["remarks"]))
        # 把商品参数缓存进任务（青龙兑换脚本 _prepare 的思路）：到点执行时
        # 直接用这份缓存发兑换请求，不再拉商城；预热时 ready=False 只是
        # 因为还没开售，执行时 force=True 绕过本地 ready 判断、由服务端定。
        task["prewarmed_picks"] = picks
        task["status"] = STATUS_PREWARMED
        task["prewarmed_at"] = now.isoformat(timespec="seconds")
        self.store.update(task)
        log.info(
            "定时兑换任务 %s 已预热（目标 %s）：%s",
            task.get("id"), task.get("target_time"), "；".join(lines),
        )

    def _run(self, task: dict, now: datetime) -> None:
        target = task_target(task) or now
        try:
            prewarmed = task.get("prewarmed_picks")
            if prewarmed:
                # 有预热缓存：直接用缓存的商品参数发兑换请求，不再拉商城；
                # force=True 绕过本地 ready 判断（预热时还没开售，ready
                # 一定是 False），由服务端决定是否可兑。开抢瞬间不再付
                # 拉商城的代价，与青龙兑换脚本的预热一个思路。
                picks = prewarmed
                result = self.execute(picks, force=True)
            else:
                _lines, picks = self.build_picks(task["item"], list(task["remarks"]))
                # 开售瞬间误判重查：没有任何 ready 的 pick 且可能翻盘时，
                # 只读重查 build_picks，最多 2 次（+10 秒、+20 秒各一次）；
                # 一旦有 ready 的 pick 就拿最新结果继续走正常执行；重查完
                # 仍无 ready 的 pick，就按原逻辑执行（各号拿到最终原因的
                # 报告，不吞报告）。只重查只读的 build_picks，绝不重试兑
                # 换请求本身；预热、补跑、过期逻辑不动。
                for _ in range(RECHECK_MAX):
                    if _any_ready(picks) or not _may_flip(picks):
                        break
                    self.sleeper(RECHECK_SECONDS)
                    _lines, picks = self.build_picks(task["item"], list(task["remarks"]))
                if not picks:
                    result = "没有能兑换的账号，未兑换。"
                else:
                    result = self.execute(picks)
        except Exception as exc:  # noqa: BLE001 - 执行异常只记一次，绝不重试
            task["status"] = STATUS_ERROR
            task["result"] = f"执行异常：{exc}（未重试）"
            self.store.update(task)
            log.exception("定时兑换任务 %s 执行异常", task.get("id"))
            self._notify_safe(
                task["chat_id"],
                f"⏰ 定时兑换执行异常（{format_target(target, now)}）：\n"
                f"🎁 商品：{task['item']['name']}\n"
                f"❌ {exc}\n"
                "未重试，结果未知，请到官方页面核实后再手动兑换。",
            )
            return
        task["status"] = STATUS_DONE
        task["result"] = result
        self.store.update(task)
        log.info("定时兑换任务 %s 已执行（目标 %s）", task.get("id"), task.get("target_time"))
        self._notify_safe(
            task["chat_id"],
            f"⏰ 定时兑换已执行（{format_target(target, now)}）：\n"
            f"🎁 商品：{task['item']['name']}\n\n{result}",
        )

    def _notify_safe(self, chat_id: str, text: str) -> None:
        try:
            self.notify(chat_id, text)
        except Exception:  # noqa: BLE001 - 通知失败不影响任务状态
            log.exception("发送定时兑换通知失败（chat %s）", chat_id)

    # -- 线程 ---------------------------------------------------------------
    def _loop(self) -> None:
        started = now_beijing()
        log.info(
            "定时兑换调度线程已启动（每 %s 秒扫一次；现在北京时间 %s，UTC %s）",
            self.poll_seconds,
            started.strftime("%Y-%m-%d %H:%M:%S"),
            started.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S"),
        )
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:  # noqa: BLE001 - 扫描出错不杀线程
                log.exception("定时兑换调度扫描出错")
            self._stop.wait(self.poll_seconds)
        log.info("定时兑换调度线程已停止")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="scheduled-exchange", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
