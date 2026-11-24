"""WeChatPadPro HTTP + WebSocket 客户端（aiohttp）。

鉴权说明（重要）：
- 每个接口只带一个 ``?key=<账号 key>`` 参数，key 是 WeChatPadPro 面板里
  该账号的 TOKEN_KEY（面板登录用 ADMIN_KEY，见 README）。
- 本客户端**不调用任何 /admin/* 接口，不碰 AuthKey 的生成/校验**。
  AuthKey 授权是 WeChatPadPro 服务端自己的事（KnowHub 鉴权服务器），
  用户按正常流程在 KnowHub 拿 AuthKey、配好服务端后，本客户端只和
  已经授权好的服务端对话。绝不绕过、剥离或伪造授权。

接口来源：本地 wcp/static/swagger/swagger.json（194 个路径），
版本 ios18.61-861。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

import aiohttp

logger = logging.getLogger("astrbot")

# 图片分包下载：单包不超过 65535 字节（swagger DataSection 说明）。
_IMAGE_CHUNK_SIZE = 61440

_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]{200,}$")


class WechatPadProError(Exception):
    """WeChatPadPro 调用失败（网络错误、服务端报错、登录态失效等）。"""


def _envelope(resp: object) -> object:
    """解开 WeChatPadPro 的响应信封。

    常见形态：{"Code": 0, "Message": "...", "Data": ...}，
    也兼容 {"code": 0, "data": ...} 与裸数据。
    Code 非 0 时抛错。
    """
    if isinstance(resp, dict):
        code = resp.get("Code", resp.get("code"))
        if code is not None and int(code) != 0:
            raise WechatPadProError(
                f"WeChatPadPro 返回错误 Code={code}: "
                f"{resp.get('Message') or resp.get('message') or resp}"
            )
        for key in ("Data", "data"):
            if key in resp:
                return resp[key]
    return resp


def _find_first_str(data: object, *names: str) -> str:
    """在 dict 里按多个字段名找第一个非空字符串；支持嵌套一层 Data。"""
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            if isinstance(value, str) and value:
                return value
        nested = data.get("Data", data.get("data"))
        if isinstance(nested, dict):
            return _find_first_str(nested, *names)
    return ""


def _parse_online_status(data: object) -> bool | None:
    """解析登录状态。返回 True/False；解析不出返回 None（调用方自行决定）。"""
    if isinstance(data, bool):
        return data
    if isinstance(data, (int, float)):
        return bool(data)
    if isinstance(data, str):
        return data.strip().lower() in ("1", "true", "online", "on", "logined", "login")
    if isinstance(data, dict):
        for key in ("Status", "status", "Online", "online", "IsOnline", "isOnline",
                    "LoginStatus", "loginStatus", "IsLogin", "isLogin"):
            if key in data:
                parsed = _parse_online_status(data[key])
                if parsed is not None:
                    return parsed
        nested = data.get("Data", data.get("data"))
        if nested is not None and nested is not data:
            return _parse_online_status(nested)
    return None


class WechatPadProClient:
    """单个 WeChatPadPro 账号的异步客户端。"""

    def __init__(self, base_url: str, account_key: str, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.account_key = account_key or ""
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    # -- 基础 --
    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _params(self) -> dict:
        return {"key": self.account_key}

    async def _get(self, path: str) -> object:
        session = await self._ensure_session()
        try:
            async with session.get(self._url(path), params=self._params()) as resp:
                return _envelope(await self._read_json(resp))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise WechatPadProError(f"GET {path} 失败: {exc}") from exc

    async def _post(self, path: str, body: dict | None = None) -> object:
        session = await self._ensure_session()
        try:
            async with session.post(
                self._url(path), params=self._params(), json=body or {}
            ) as resp:
                return _envelope(await self._read_json(resp))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise WechatPadProError(f"POST {path} 失败: {exc}") from exc

    @staticmethod
    async def _read_json(resp: aiohttp.ClientResponse):
        text = await resp.text()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw_text": text}

    def ws_url(self) -> str:
        """把 http(s) base_url 换成 ws(s)，拼出 /ws/GetSyncMsg 地址。"""
        base = self.base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return f"{base}/ws/GetSyncMsg?key={self.account_key}"

    # -- 登录 --
    async def get_login_qr(self) -> dict:
        """取 iPad 登录二维码。

        POST /login/GetLoginQrCodePad。返回 dict：
        - image_base64：二维码图片的 base64（若服务端直接给了图片）
        - qr_url：二维码内容 URL（一般是 https://weixin.qq.com/x/...）
        - show_url：面板上的 ShowQrCode 页面地址（浏览器打开直接扫码）
        - uuid：本次登录的 uuid（若有）
        具体字段随服务端版本而异，解析不到图片时至少保证 show_url 可用。
        """
        data = await self._post("/login/GetLoginQrCodePad", {})
        qr_image = _find_first_str(
            data, "QrCode", "qrCode", "QRCode", "qrcode", "QrCodeBase64", "Image", "image"
        )
        qr_url = _find_first_str(data, "QrCodeUrl", "qrCodeUrl", "Url", "url", "QrUrl")
        uuid = _find_first_str(data, "Uuid", "uuid", "UUID")
        image_base64 = ""
        content_url = ""
        for candidate in (qr_image, qr_url):
            if not candidate:
                continue
            if candidate.startswith(("http://", "https://")):
                content_url = candidate
            elif candidate.startswith("data:image"):
                image_base64 = candidate.split(",", 1)[-1]
            elif _B64_RE.match(candidate.replace("\n", "")):
                image_base64 = candidate
            else:
                content_url = candidate
        return {
            "image_base64": image_base64,
            "qr_url": content_url,
            "uuid": uuid,
            "show_url": f"{self.base_url}/login/ShowQrCode?key={self.account_key}",
            "raw": data,
        }

    async def get_login_status(self) -> dict:
        """GET /login/GetLoginStatus。返回 {"online": bool|None, "raw": ...}。"""
        data = await self._get("/login/GetLoginStatus")
        return {"online": _parse_online_status(data), "raw": data}

    async def get_profile(self) -> dict:
        """GET /user/GetProfile。返回 {"wxid": ..., "nickname": ..., "raw": ...}。"""
        data = await self._get("/user/GetProfile")
        wxid = _find_first_str(data, "UserName", "userName", "wxid", "WxId", "Wxid")
        nickname = _find_first_str(
            data, "NickName", "nickName", "nickname", "Nick", "Alias", "alias"
        )
        return {"wxid": wxid, "nickname": nickname, "raw": data}

    # -- 发消息 --
    async def send_text(
        self, to_wxid: str, text: str, at_wxid_list: list[str] | None = None
    ) -> object:
        """POST /message/SendTextMessage。"""
        item: dict = {"MsgType": 1, "ToUserName": to_wxid, "TextContent": text}
        if at_wxid_list:
            item["AtWxIDList"] = list(at_wxid_list)
        return await self._post("/message/SendTextMessage", {"MsgItem": [item]})

    async def send_image(self, to_wxid: str, image_bytes: bytes) -> object:
        """POST /message/SendImageMessage，图片走 base64（ImageContent）。"""
        item = {
            "MsgType": 2,
            "ToUserName": to_wxid,
            "ImageContent": base64.b64encode(image_bytes).decode("ascii"),
        }
        return await self._post("/message/SendImageMessage", {"MsgItem": [item]})

    # -- 收消息 --
    async def sync_messages(self, count: int = 0) -> list[dict]:
        """POST /message/HttpSyncMsg，HTTP 轮询取新消息。返回原始消息 dict 列表。"""
        from .wechat_convert import parse_sync_payload

        data = await self._post("/message/HttpSyncMsg", {"Count": count})
        return parse_sync_payload(data)

    async def ws_messages(self):
        """异步生成器：连 /ws/GetSyncMsg，逐条产出原始同步载荷。

        连接断开即结束（由调用方决定重连）。心跳 30 秒。
        """
        session = await self._ensure_session()
        async with session.ws_connect(self.ws_url(), heartbeat=30) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    yield msg.data
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSE,
                ):
                    break

    async def get_big_image(
        self,
        from_wxid: str,
        to_wxid: str,
        msg_id: str,
        total_len: int = 0,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> bytes | None:
        """分包下载高清图片（POST /message/GetMsgBigImg）。

        swagger 的 DownloadParam：{CompressType, FromUserName, MsgId（注意
        是 msg_id 不是 new_msg_id）, Section{StartPos, DataLen<=65535},
        ToUserName, TotalLen}。TotalLen 未知时只取一包。
        返回图片字节串；失败返回 None。
        """
        try:
            msg_id_int = int(msg_id)
        except (TypeError, ValueError):
            logger.warning("下载图片失败：msg_id 不是数字：%r", msg_id)
            return None
        out = bytearray()
        start = 0
        while True:
            if len(out) >= max_bytes:
                logger.warning("图片超过 %d 字节上限，放弃下载", max_bytes)
                return None
            body = {
                "CompressType": 0,
                "FromUserName": from_wxid,
                "ToUserName": to_wxid,
                "MsgId": msg_id_int,
                "Section": {"StartPos": start, "DataLen": _IMAGE_CHUNK_SIZE},
                "TotalLen": total_len,
            }
            try:
                data = await self._post("/message/GetMsgBigImg", body)
            except WechatPadProError:
                logger.warning("GetMsgBigImg 请求失败（start=%d）", start, exc_info=True)
                return None
            chunk_b64 = _find_first_str(data, "Data", "data", "Buffer", "buffer")
            if not chunk_b64:
                # 响应里没有数据：若已拿到一部分就返回，否则算失败。
                return bytes(out) if out else None
            try:
                chunk = base64.b64decode(chunk_b64)
            except Exception:
                logger.warning("GetMsgBigImg 返回的数据不是合法 base64", exc_info=True)
                return bytes(out) if out else None
            out.extend(chunk)
            start += len(chunk)
            if total_len and len(out) >= total_len:
                return bytes(out)
            if len(chunk) < _IMAGE_CHUNK_SIZE:
                # 最后一包（服务端没更多数据了）。
                return bytes(out)


async def send_message_chain(client: WechatPadProClient, target_wxid: str, chain) -> None:
    """把一条 AstrBot 消息链发到指定 wxid（私聊对方 wxid 或群 wxid）。

    - 相邻 Plain 合并成一次 SendTextMessage；At 收集成 AtWxIDList 一起发。
    - Image 转成本地文件读出字节、base64 发 SendImageMessage。
    - Reply/Record/Video 等暂不支持：只记日志，不阻塞发送。
    astrbot 的组件类在这里才 import，避免本模块在非 AstrBot 环境
    （如单测）里硬依赖 astrbot。
    """
    from astrbot.api.message_components import At, Image, Plain

    texts: list[str] = []
    at_list: list[str] = []

    async def flush_text() -> None:
        if not texts:
            return
        await client.send_text(target_wxid, "".join(texts), at_wxid_list=at_list or None)
        texts.clear()
        at_list.clear()

    for seg in chain.chain:
        if isinstance(seg, Plain):
            texts.append(seg.text or "")
        elif isinstance(seg, At):
            if getattr(seg, "qq", ""):
                at_list.append(str(seg.qq))
        elif isinstance(seg, Image):
            await flush_text()
            path = await seg.convert_to_file_path()
            try:
                with open(path, "rb") as f:
                    image_bytes = f.read()
            except OSError:
                logger.warning("图片发送失败：读不到文件 %s", path)
                continue
            await client.send_image(target_wxid, image_bytes)
        else:
            logger.debug("wechat_personal 暂不支持发送 %s，已跳过", type(seg).__name__)
    await flush_text()

