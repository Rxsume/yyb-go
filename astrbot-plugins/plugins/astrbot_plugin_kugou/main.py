"""AstrBot 酷狗插件：概念版扫码登录（QR）/ 提现 / 定时提现。

QQ 官方 Bot（qq_official）专用。命令沿用 TG 机器人的中文纯文本习惯，
命中命令后 stop_event() 阻止进 LLM，未命中放行给 LLM。
权限：不设管理员限制（2026-11-24 用户要求群里所有人都能用），私聊和
群聊的任何人发命令都会执行。注意：群里任何人都能提现到你配好的微信、
操作青龙任务；只在自己人的群里用。

QQ 侧降级：token 不在 QQ 消息里贴明文。登录成功后只报 userid 与同步
状态；kg_sync_qinglong 打开时 token 自动同步进青龙 KGGNB。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools

from .core import KugouCore, KugouSettings
from .kugou_concept3114 import KugouConceptLogin3114
from .kugou_withdraw import KugouWithdraw
from .qinglong import QinglongClient

logger = logging.getLogger("astrbot")

# QQ 官方单条文本上限待实测，先按 1500 字符保守切分。
MAX_REPLY_CHARS = 1500


def _split_text(text: str, limit: int = MAX_REPLY_CHARS) -> list[str]:
    """按行切分长消息，每段不超过 limit 字符。"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            # 单行超长：硬切
            for i in range(0, len(line), limit):
                part = line[i:i + limit]
                if current and len(current) + len(part) > limit:
                    chunks.append(current)
                    current = ""
                current += part
            continue
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [text]


class KugouPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        # Star.__init__ 不保存 config，自己存一份。
        self.config = dict(config or {})
        data_dir = str(StarTools.get_data_dir("astrbot_plugin_kugou"))
        os.makedirs(data_dir, exist_ok=True)

        ql = QinglongClient(
            self.config.get("ql_base_url") or "http://192.168.31.2:5700",
            self.config.get("ql_token") or "",
        )
        cfg = KugouSettings(
            kg_sync_qinglong=bool(self.config.get("kg_sync_qinglong") or False),
        )
        self.core = KugouCore(
            qinglong=ql,
            kugou=KugouConceptLogin3114(),
            withdraw=KugouWithdraw(),
            cfg=cfg,
            data_dir=data_dir,
            push=self._push_sync,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self.core.start_scheduler()

    def _push_sync(self, umo: str, text: str) -> None:
        """后台线程调用：把推送排进 AstrBot 事件循环。

        私聊可主动推送；群聊只有在被动回复窗口内（有缓存的 msg_id）
        或开了群主动推送时才能推到，推不到时只记日志（结果也可通过
        “定时提现”命令随时查看）。
        """
        loop = self._loop
        if not umo or not text:
            return
        if loop is None or loop.is_closed():
            logger.warning("酷狗插件后台推送丢弃（事件循环不可用）：%s", umo)
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.context.send_message(umo, MessageChain([Plain(text)])), loop
            )
        except RuntimeError:
            logger.warning("酷狗插件后台推送失败：%s", umo, exc_info=True)

    async def terminate(self) -> None:
        self.core.stop_scheduler()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def dispatch(self, event: AstrMessageEvent):
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        text = (event.message_str or "").strip()
        if not text:
            return
        # 同步的 requests 逻辑包进线程池，别堵事件循环。
        reply = await asyncio.to_thread(
            self.core.handle_message,
            event.get_sender_id(),
            text,
            event.unified_msg_origin,
        )
        if reply is None:
            return  # 未命中命令，放行给 LLM
        event.stop_event()
        if reply.image:
            yield event.chain_result([
                Plain(reply.text),
                Image.fromBase64(base64.b64encode(reply.image).decode("ascii")),
            ])
            return
        if reply.text:
            for chunk in _split_text(reply.text):
                yield event.plain_result(chunk)
