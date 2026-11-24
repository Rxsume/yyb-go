"""AstrBot 平台适配器：个人微信（小号），协议后端为 WeChatPadPro（iPad 协议）。

收消息：默认走 WebSocket ``/ws/GetSyncMsg``（与 AstrBot 官方 wechatpadpro
适配器相同）；WS 连续失败时自动切 HTTP 轮询 ``/message/HttpSyncMsg``，
恢复后切回 WS。Webhook 回调方式不用：它要求 WeChatPadPro 能反向访问
一个公网回调地址，WS 不需要入站端口。
发消息：POST /message/SendTextMessage / SendImageMessage。

配置来源：平台配置（面板 → 消息平台 → wechat_personal）优先，
插件配置（_conf_schema.json）作后备——main.py 的 Star 会把插件配置
写进模块级共享配置，适配器启动时合并。两处填同一份值即可。

群里要不要 @ 由 AstrBot 核心配置 wake_prefix 决定，本适配器只负责
把私聊/群聊正确标成 FRIEND_MESSAGE / GROUP_MESSAGE、把会话映射对。
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections import deque

from astrbot import logger
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.message_session import MessageSesion

from .wechat_client import (
    WechatPadProClient,
    WechatPadProError,
    send_message_chain,
)
from .wechat_convert import (
    MSG_TYPE_IMAGE,
    MSG_TYPE_TEXT,
    SUPPORTED_MSG_TYPES,
    normalize_message,
    parse_sync_payload,
)
from .wechat_platform_event import WechatPersonalEvent

# 插件配置（_conf_schema.json）由 Star 写到这里，适配器启动时合并。
_SHARED_CONFIG: dict = {}

# WS 连续失败多少次后切 HTTP 轮询；轮询多久后重试 WS。
_WS_FAILS_BEFORE_POLL = 5
_POLL_FALLBACK_SECONDS = 300


def set_shared_config(config: dict | None) -> None:
    """Star 在 __init__ 里调用：把插件配置交给适配器作后备。"""
    _SHARED_CONFIG.clear()
    if config:
        _SHARED_CONFIG.update(config)


def get_shared_config() -> dict:
    return dict(_SHARED_CONFIG)


@register_platform_adapter(
    "wechat_personal",
    "个人微信（小号，WeChatPadPro iPad 协议）",
    default_config_tmpl={
        "base_url": "http://192.168.31.2:1238",
        "account_key": "",
        "bot_nickname": "",
        "receive_mode": "auto",
        "poll_interval_seconds": 3,
        "include_self_message": False,
        "max_image_bytes": 10485760,
    },
)
class WechatPersonalAdapter(Platform):
    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
        _client_override: WechatPadProClient | None = None,
    ) -> None:
        super().__init__(event_queue)
        merged: dict = {}
        merged.update(get_shared_config())
        for key, value in (platform_config or {}).items():
            if value is not None and value != "":
                merged[key] = value

        self.config = merged
        self.settings = platform_settings
        self.base_url = str(merged.get("base_url") or "http://192.168.31.2:1238")
        self.account_key = str(merged.get("account_key") or "")
        self.bot_nickname = str(merged.get("bot_nickname") or "")
        self.receive_mode = str(merged.get("receive_mode") or "auto").lower()
        if self.receive_mode not in ("auto", "ws", "poll"):
            self.receive_mode = "auto"
        self.poll_interval = max(int(merged.get("poll_interval_seconds") or 3), 1)
        self.include_self = bool(merged.get("include_self_message"))
        self.max_image_bytes = int(merged.get("max_image_bytes") or 10 * 1024 * 1024)

        self._client = _client_override or WechatPadProClient(
            self.base_url, self.account_key
        )
        self._running = False
        self._seen_ids: deque = deque(maxlen=2000)
        self._self_wxid = ""
        self._self_nickname = ""

    # -- AstrBot 要求的方法 --
    def meta(self) -> PlatformMetadata:
        return PlatformMetadata("wechat_personal", "个人微信（小号，WeChatPadPro）")

    async def terminate(self) -> None:
        self._running = False
        await self._client.close()

    async def send_by_session(
        self, session: MessageSesion, message_chain
    ) -> None:
        await super().send_by_session(session, message_chain)
        if session.message_type == MessageType.GROUP_MESSAGE:
            target = getattr(session, "group_id", None) or session.session_id
        else:
            target = session.session_id
        if not target:
            logger.warning("wechat_personal send_by_session：目标会话为空")
            return
        try:
            await send_message_chain(self._client, target, message_chain)
        except Exception:
            logger.warning("wechat_personal send_by_session 失败", exc_info=True)

    # -- 主循环 --
    async def run(self) -> None:
        self._running = True
        if not self.account_key:
            logger.error(
                "wechat_personal 未配置 account_key：请在消息平台 wechat_personal "
                "或插件配置里填 WeChatPadPro 账号 key。适配器未启动。"
            )
            return
        await self._wait_online()
        if not self._running:
            return
        if self.receive_mode == "poll":
            await self._poll_loop()
            return
        ws_failures = 0
        use_poll_until = 0.0
        while self._running:
            if self.receive_mode == "auto" and time.monotonic() < use_poll_until:
                await self._poll_loop(duration=60)
                continue
            try:
                await self._ws_loop_once()
                ws_failures = 0
            except Exception:
                ws_failures += 1
                logger.warning(
                    "wechat_personal WebSocket 断开（连续失败 %d 次）",
                    ws_failures,
                    exc_info=True,
                )
            if not self._running:
                break
            if self.receive_mode == "ws" or ws_failures < _WS_FAILS_BEFORE_POLL:
                await asyncio.sleep(min(5 * max(ws_failures, 1), 60))
            else:
                use_poll_until = time.monotonic() + _POLL_FALLBACK_SECONDS
                logger.warning(
                    "wechat_personal WebSocket 连续失败，切 HTTP 轮询 %d 秒",
                    _POLL_FALLBACK_SECONDS,
                )

    async def _wait_online(self) -> None:
        """等微信上线（扫码后自动开始收消息）。状态解析不出时按在线处理。"""
        while self._running:
            try:
                status = await self._client.get_login_status()
            except WechatPadProError:
                logger.warning("wechat_personal 查登录状态失败，10 秒后重试", exc_info=True)
                await asyncio.sleep(10)
                continue
            online = status.get("online")
            if online is True:
                await self._refresh_profile()
                logger.info(
                    "wechat_personal 微信已在线（wxid=%s，昵称=%s）",
                    self._self_wxid,
                    self._self_nickname,
                )
                return
            if online is False:
                logger.warning(
                    "wechat_personal 微信未登录：请用「微信登录」命令取二维码扫码，"
                    "上线后适配器自动开始收消息。"
                )
            else:
                logger.info("wechat_personal 登录状态无法解析，按在线处理：%s", status.get("raw"))
                await self._refresh_profile()
                return
            await asyncio.sleep(10)

    async def _refresh_profile(self) -> None:
        try:
            profile = await self._client.get_profile()
        except WechatPadProError:
            logger.debug("wechat_personal 获取个人资料失败", exc_info=True)
            return
        if profile.get("wxid"):
            self._self_wxid = profile["wxid"]
        if profile.get("nickname"):
            self._self_nickname = profile["nickname"]

    def _effective_nickname(self) -> str:
        return self.bot_nickname or self._self_nickname

    async def _ws_loop_once(self) -> None:
        """连一次 WS，收到消息逐条处理；连接结束即抛错，由 run() 重连。"""
        async for payload in self._client.ws_messages():
            if not self._running:
                return
            await self._handle_payload(payload)
        raise WechatPadProError("WebSocket 已关闭")

    async def _poll_loop(self, duration: float | None = None) -> None:
        started = time.monotonic()
        while self._running:
            if duration is not None and time.monotonic() - started >= duration:
                return
            try:
                raws = await self._client.sync_messages()
                for raw in raws:
                    await self._handle_raw(raw)
            except WechatPadProError:
                logger.warning("wechat_personal HTTP 轮询失败", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    # -- 消息处理 --
    async def _handle_payload(self, payload) -> None:
        import json

        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="ignore")
        if not isinstance(payload, str):
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("wechat_personal 收到非 JSON 的 WS 消息，已跳过")
            return
        for raw in parse_sync_payload(data):
            await self._handle_raw(raw)

    async def _handle_raw(self, raw: dict) -> None:
        norm = normalize_message(
            raw,
            self_wxid=self._self_wxid,
            bot_nickname=self._effective_nickname(),
        )
        if norm is None:
            return
        if norm["msg_type"] not in SUPPORTED_MSG_TYPES:
            logger.debug(
                "wechat_personal 跳过不支持的消息类型 MsgType=%s", norm["msg_type"]
            )
            return
        msg_id = norm["msg_id"]
        if msg_id:
            if msg_id in self._seen_ids:
                return
            self._seen_ids.append(msg_id)
        if (
            norm["sender_wxid"]
            and norm["sender_wxid"] == self._self_wxid
            and not self.include_self
        ):
            return
        abm = await self.convert_message(norm, raw)
        if abm is None:
            return
        await self.handle_msg(abm)

    async def convert_message(self, norm: dict, raw: dict) -> AstrBotMessage | None:
        abm = AstrBotMessage()
        abm.type = (
            MessageType.GROUP_MESSAGE if norm["is_group"] else MessageType.FRIEND_MESSAGE
        )
        if norm["is_group"]:
            abm.group_id = norm["group_id"]
        abm.sender = MessageMember(
            user_id=norm["sender_wxid"], nickname=norm["sender_nickname"]
        )
        abm.self_id = self._self_wxid
        abm.session_id = norm["group_id"] if norm["is_group"] else norm["sender_wxid"]
        abm.message_id = norm["msg_id"]
        abm.raw_message = raw
        if norm["msg_type"] == MSG_TYPE_TEXT:
            chain: list = [Plain(text=norm["content"])]
            if norm["at_me"] and self._self_wxid:
                chain.append(At(qq=self._self_wxid))
            abm.message = chain
            abm.message_str = norm["content"]
        elif norm["msg_type"] == MSG_TYPE_IMAGE:
            image_bytes = await self._fetch_image(norm)
            if image_bytes:
                abm.message = [
                    Image.fromBase64(base64.b64encode(image_bytes).decode("ascii"))
                ]
                abm.message_str = "[图片]"
            else:
                abm.message = [Plain(text="[图片]")]
                abm.message_str = "[图片]"
        else:
            return None
        return abm

    async def _fetch_image(self, norm: dict) -> bytes | None:
        """取图片字节串：Content 里若直接是 base64 就用它，否则分包下载。"""
        content = norm.get("raw_content") or ""
        if len(content) > 500:
            try:
                candidate = base64.b64decode(content, validate=True)
                if len(candidate) > 1000:
                    return candidate
            except Exception:
                pass
        try:
            return await self._client.get_big_image(
                from_wxid=norm["from_wxid"],
                to_wxid=norm["to_wxid"],
                msg_id=norm["msg_id"],
                total_len=norm.get("total_len") or 0,
                max_bytes=self.max_image_bytes,
            )
        except Exception:
            logger.warning("wechat_personal 图片下载失败", exc_info=True)
            return None

    async def handle_msg(self, message: AstrBotMessage) -> None:
        target = (
            message.group_id
            if message.type == MessageType.GROUP_MESSAGE
            else message.sender.user_id
        )
        event = WechatPersonalEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self._client,
            target_wxid=target,
        )
        self.commit_event(event)
