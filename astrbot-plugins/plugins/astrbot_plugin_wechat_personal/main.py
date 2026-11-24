"""AstrBot 插件：个人微信（小号）平台适配器。

本文件只负责两件事：
1. import 适配器模块，触发 @register_platform_adapter 注册；
2. 管理员命令「微信登录」「微信状态」（仅管理员、私聊可用）：
   取 WeChatPadPro 的登录二维码、查小号在线状态。

二维码安全：二维码只能私聊发给管理员，群里发会被群友扫走登录，
所以命令在群里会直接拒绝。
"""

from __future__ import annotations

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star

from . import wechat_platform_adapter as adapter_mod
from .wechat_client import WechatPadProClient, WechatPadProError


class WechatPersonalPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = dict(config or {})
        # 把插件配置交给适配器作后备（平台配置优先）。
        adapter_mod.set_shared_config(self.config)
        self._client: WechatPadProClient | None = None

    def _get_client(self) -> WechatPadProClient:
        if self._client is None:
            self._client = WechatPadProClient(
                self.config.get("base_url") or "http://192.168.31.2:1238",
                self.config.get("account_key") or "",
            )
        return self._client

    async def terminate(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _admin_private_check(self, event: AstrMessageEvent) -> str | None:
        """管理员 + 私聊检查；不通过返回拒绝文案，通过返回 None。"""
        if not event.is_admin():
            return "只有管理员能用这个命令。"
        if event.message_obj.type != MessageType.FRIEND_MESSAGE:
            return "请私聊我发这个命令，二维码/账号信息不能发到群里。"
        return None

    @filter.command("微信登录")
    async def wechat_login(self, event: AstrMessageEvent):
        denied = self._admin_private_check(event)
        if denied:
            yield event.plain_result(denied)
            return
        client = self._get_client()
        if not client.account_key:
            yield event.plain_result(
                "还没填 account_key：先在 WeChatPadPro 面板里建好账号，"
                "把它的 key 填进插件配置再试。"
            )
            return
        try:
            qr = await client.get_login_qr()
        except WechatPadProError as exc:
            logger.warning("微信登录取二维码失败", exc_info=True)
            yield event.plain_result(f"取二维码失败：{exc}")
            return
        lines = [
            "请用小号微信扫码登录（二维码约 2 分钟过期，过期重发一次命令）：",
            f"面板扫码页：{qr['show_url']}",
        ]
        if qr.get("qr_url"):
            lines.append(f"二维码链接：{qr['qr_url']}")
        if qr.get("image_base64"):
            yield event.chain_result(
                [Plain("\n".join(lines)), Image.fromBase64(qr["image_base64"])]
            )
        else:
            lines.append("（服务端没直接给二维码图片，请打开上面的面板扫码页扫码）")
            yield event.plain_result("\n".join(lines))

    @filter.command("微信状态")
    async def wechat_status(self, event: AstrMessageEvent):
        denied = self._admin_private_check(event)
        if denied:
            yield event.plain_result(denied)
            return
        client = self._get_client()
        if not client.account_key:
            yield event.plain_result("还没填 account_key。")
            return
        try:
            status = await client.get_login_status()
            profile = await client.get_profile()
        except WechatPadProError as exc:
            logger.warning("微信状态查询失败", exc_info=True)
            yield event.plain_result(f"查状态失败：{exc}")
            return
        online = status.get("online")
        state = (
            "在线"
            if online is True
            else ("离线" if online is False else "未知（服务端没给明确状态）")
        )
        yield event.plain_result(
            "微信小号状态："
            f"{state}\n昵称：{profile.get('nickname') or '未知'}"
            f"\nwxid：{profile.get('wxid') or '未知'}"
        )
