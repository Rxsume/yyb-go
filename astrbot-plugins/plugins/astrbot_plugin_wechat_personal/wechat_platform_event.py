"""AstrBot 事件：个人微信（小号）消息事件。

send() 直接走 WeChatPadPro 发到目标 wxid（私聊发给发送者，群聊发到群），
再调父类 send() 做收尾（AstrBot 核心要求）。
"""

from __future__ import annotations

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.platform import AstrBotMessage, PlatformMetadata

from .wechat_client import WechatPadProClient, send_message_chain


class WechatPersonalEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: WechatPadProClient,
        target_wxid: str,
    ):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self._client = client
        self._target_wxid = target_wxid

    async def send(self, message: MessageChain):
        if not self._target_wxid:
            logger.warning("wechat_personal 发送失败：目标 wxid 为空")
        else:
            try:
                await send_message_chain(self._client, self._target_wxid, message)
            except Exception:
                logger.warning("wechat_personal 发送失败", exc_info=True)
        await super().send(message)
