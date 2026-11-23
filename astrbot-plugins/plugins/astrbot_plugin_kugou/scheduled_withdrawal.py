"""酷狗定时提现：常驻任务 + 调度线程（供 Telegram 机器人用）。

一个定时提现任务 = 一批已选账号 + 一个目标档位（默认 50000 金币=5 元）。
与定时兑换（一次性）不同，这是常驻任务：每天 5 个场次
（0:00 / 8:00 / 12:00 / 16:00 / 20:00，北京时间）都自动抢，
直到用户取消。每个账号每天最多提 1 次——当天已提过的号，本场
跳过，下一场也不再试（第二天重置）。

机器人启动时从 ``data/scheduled_withdrawals.json`` 重载任务；
调度线程每 ``POLL_SECONDS`` 秒扫一遍：

- 场次前 3 分钟（PREHEAT_SECONDS）：开抢——起后台线程跑本场
  （预热在 do_grab 里：余额/档位/今日次数现查一遍；等到场次再抢）。
  主线程不阻塞，机器人继续回消息。
- 启动时才发现已过场次开始：5 分钟内（CATCHUP_SECONDS）补跑
  （do_grab 的轮询截止是场次 +300 秒，还有剩余时间）；
  超过 5 分钟跳过本场并通知用户错过（任务本身常驻，下一场继续）。
- 同一场次只开一次（last_window 记场次键），避免 15 秒轮询重复开抢。

安全规则与 kg_withdraw.py 一致：每账号每场只发一次申请；响应
异常/结果未知绝不自动重试；执行异常只记一次，不重复执行。
青龙任务 246（酷狗提现-定时抢5元）仍在跑同一件事：机器人开抢
前先查今日次数（do_grab 预热里），246 已提过的号会跳过，不会
重复提；服务器每天 1 次的限制也会挡住第二次申请（31201/31205）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .kugou_withdraw import (
    CATCHUP_SECONDS,
    PREHEAT_SECONDS,
    SLOT_HOURS,
    _as_beijing,
    format_window,
    next_window,
    now_beijing,
    previous_window,
    window_key,
)

log = logging.getLogger(__name__)

BEIJING = ZoneInfo("Asia/Shanghai")

POLL_SECONDS = 15

STATUS_ACTIVE = "active"        # 常驻，每天 5 场都抢
STATUS_CANCELLED = "cancelled"  # 用户取消


def new_withdrawal_job(remarks: list[str], coins: int, chat_id: str,
                       now: datetime | None = None) -> dict:
    """建一个常驻定时提现任务（只存 JSON 能写的东西）。"""
    now = now_beijing() if now is None else now
    return {
        "id": "kw-" + uuid.uuid4().hex[:10],
        "remarks": list(remarks),
        "coins": int(coins),
        "chat_id": str(chat_id),
        "created_at": now.isoformat(timespec="seconds"),
        "status": STATUS_ACTIVE,
        "last_window": None,   # 已开抢的最近场次键 "YYYY-MM-DD HH:MM"
        "last_result": None,   # 最近一场的结果摘要
    }


class ScheduledWithdrawalStore:
    """定时提现任务的 JSON 持久化（path 为 None 时只放内存，供测试用）。"""

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
                log.warning("定时提现任务文件损坏，视为空：%s", self.path)
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

    def active(self) -> list[dict]:
        """常驻任务（未取消），按创建时间排序。"""
        tasks = [t for t in self.load() if t.get("status") == STATUS_ACTIVE]
        tasks.sort(key=lambda t: str(t.get("created_at") or ""))
        return tasks

    def cancel_for_remarks(self, remarks: list[str]) -> list[dict]:
        """取消这些账号已有的常驻任务（提交新任务时替换旧任务）。
        返回被取消的任务。"""
        wanted = set(remarks)
        tasks = self.load()
        cancelled: list[dict] = []
        for task in tasks:
            if task.get("status") != STATUS_ACTIVE:
                continue
            if wanted & set(task.get("remarks") or []):
                task["status"] = STATUS_CANCELLED
                cancelled.append(task)
        if cancelled:
            self.save(tasks)
        return cancelled


class ScheduledWithdrawalRunner:
    """调度线程：轮询常驻任务，到场次开抢/补跑/跳过。

    run_window(job, window) -> str：跑一场（读青龙账号 → 现查状态 →
    并行 do_grab），返回发给用户的结果文本。notify(chat_id, text)：推送消息
    （chat_id 是 QQ 会话 UMO）。开抢在后台线程里跑，绝不阻塞 15 秒的轮询。
    """

    def __init__(
        self,
        store: ScheduledWithdrawalStore,
        run_window: Callable[[dict, datetime], str],
        notify: Callable[[int, str], None],
        poll_seconds: int = POLL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.run_window = run_window
        self.notify = notify
        self.poll_seconds = poll_seconds
        self.clock = clock or now_beijing
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._attempts: list[threading.Thread] = []

    # -- 单次扫描（可单测；线程循环只是定时调它） ------------------------------
    def process_once(self, now: datetime | None = None) -> None:
        now = now_beijing() if now is None else now
        if now.tzinfo is None:
            now = now.replace(tzinfo=BEIJING)
        for job in self.store.active():
            try:
                self._process_job(job, now)
            except Exception:  # noqa: BLE001 - 一个任务出错不影响其他任务
                log.exception("处理定时提现任务 %s 时出错", job.get("id"))

    def _process_job(self, job: dict, now: datetime) -> None:
        # Missed-window check: the most recent window, if it ended more
        # than CATCHUP_SECONDS ago and this job never handled it. Only
        # when the window is NEWER than the last one recorded (key
        # compare) — otherwise a job that already fired would be
        # re-marked and could fire the same window twice.
        prev = previous_window(SLOT_HOURS, now)
        prev_key = window_key(prev)
        last_key = job.get("last_window")
        if (last_key is None or prev_key > last_key) and (
                now > prev + timedelta(seconds=CATCHUP_SECONDS)):
            try:
                created = _as_beijing(datetime.fromisoformat(
                    str(job.get("created_at") or "")))
            except ValueError:
                created = None
            prev_fire = prev - timedelta(seconds=PREHEAT_SECONDS)
            job["last_window"] = prev_key
            if created is None or created <= prev_fire:
                # The job already existed when this window's preheat
                # came: the bot missed it (was down). Notify once.
                job["last_result"] = f"错过 {format_window(prev, now)}场"
                self.store.update(job)
                log.info("定时提现任务 %s 错过 %s 场",
                         job.get("id"), prev_key)
                self._notify_safe(
                    job["chat_id"],
                    f"⏰ 错过酷狗提现 {format_window(prev, now)}场"
                    "（机器人当时没在跑），下一场再试。\n"
                    "任务还在，回复“定时提现”可查看/取消。",
                )
            else:
                # The job was created after this window's preheat: it
                # never had a chance at it. Record it silently so the
                # check does not repeat, without notifying.
                self.store.update(job)
        window, fire = next_window(SLOT_HOURS, now)
        key = window_key(window)
        if job.get("last_window") == key:
            return  # 这场已开过（或已跳过），等下一场
        if now < fire:
            return  # 还没到预热时间
        # 开抢：先记场次（防 15 秒轮询重复开），再起后台线程跑。
        # (next_window only ever returns a window within the catchup
        # window or in the future, so a >5-min-late window never
        # reaches here — it is handled by the missed check above.)
        job["last_window"] = key
        self.store.update(job)
        log.info("定时提现任务 %s 开抢 %s 场", job.get("id"), key)
        thread = threading.Thread(
            target=self._run_window_thread, args=(job, window),
            name=f"kw-window-{key}", daemon=True)
        self._attempts.append(thread)
        thread.start()

    def _run_window_thread(self, job: dict, window: datetime) -> None:
        try:
            result = self.run_window(job, window)
        except Exception as exc:  # noqa: BLE001 - 执行异常只记一次，绝不重试
            log.exception("定时提现任务 %s 执行异常", job.get("id"))
            self._notify_safe(
                job["chat_id"],
                f"⏰ 酷狗提现 {format_window(window)}场执行异常：\n"
                f"❌ {exc}\n"
                "未重试，结果未知，请到 App 我的账单核实。",
            )
            return
        job["last_result"] = result.splitlines()[0] if result else ""
        self.store.update(job)
        self._notify_safe(job["chat_id"], result)

    def _notify_safe(self, chat_id: str, text: str) -> None:
        try:
            self.notify(chat_id, text)
        except Exception:  # noqa: BLE001 - 通知失败不影响任务状态
            log.exception("发送定时提现通知失败（chat %s）", chat_id)

    def join_attempts(self, timeout: float = 30.0) -> None:
        """等后台开抢线程跑完（单测用；生产不调）。"""
        for thread in self._attempts:
            thread.join(timeout=timeout)
        self._attempts = [t for t in self._attempts if t.is_alive()]

    # -- 线程 ---------------------------------------------------------------
    def _loop(self) -> None:
        started = now_beijing()
        log.info(
            "定时提现调度线程已启动（每 %s 秒扫一次；现在北京时间 %s，UTC %s）",
            self.poll_seconds,
            started.strftime("%Y-%m-%d %H:%M:%S"),
            started.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S"),
        )
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:  # noqa: BLE001 - 扫描出错不杀线程
                log.exception("定时提现调度扫描出错")
            self._stop.wait(self.poll_seconds)
        log.info("定时提现调度线程已停止")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="scheduled-withdrawal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
