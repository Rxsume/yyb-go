"""纯函数：解析 WeChatPadPro 同步消息载荷。

不依赖任何第三方库，可在任何地方单测。

线协议要点（详见 README）：
- WeChatPadPro 用三种方式下发消息：WebSocket ``/ws/GetSyncMsg``、HTTP
  轮询 ``/message/HttpSyncMsg``、Webhook 回调。本插件默认走 WebSocket。
- 载荷形态不固定：可能是单条消息 dict、``{"AddMsgs": [...]}``、
  ``{"Data": {"AddMsgs": [...]}}`` 或 ``{"Data": [...]}``。
- 字段名随版本/通道而异：PascalCase（FromUserName/Content/MsgType）
  或 camelCase（fromUserName/msgContent/msg_type）；字符串可能是裸值，
  也可能是 ``{"string": "..."}`` 包裹。以下解析器对所有已知变体都兼容。
- 群消息：FromUserName 以 ``@chatroom`` 结尾；Content 以
  ``<发送者wxid>:\\n<正文>`` 开头；PushContent 为 ``<昵称>: <正文>``。
- MsgType：1 文本、3 图片、34 语音、43 视频、47 表情、49 应用消息、
  10000 系统提示。本插件只处理 1 和 3，其余类型由适配器跳过。
"""

from __future__ import annotations

import re

# WeChatPadPro 消息类型
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_VIDEO = 43
MSG_TYPE_EMOJI = 47
MSG_TYPE_APP = 49
MSG_TYPE_SYSTEM = 10000

SUPPORTED_MSG_TYPES = (MSG_TYPE_TEXT, MSG_TYPE_IMAGE)

_CHATROOM_SUFFIX = "@chatroom"
_GROUP_SENDER_RE = re.compile(r"^([A-Za-z0-9_@.\-]+):\n(.*)$", re.DOTALL)


def _unwrap(value):
    """把可能是 {"string": "..."} 包裹的值还原成字符串；其余原样返回。"""
    if isinstance(value, dict):
        for key in ("string", "String", "str", "value"):
            if key in value:
                return value[key]
        return None
    return value


def _pick(mapping: dict, *names):
    """按顺序尝试多个字段名，返回第一个存在且非 None 的值（经 _unwrap）。"""
    if not isinstance(mapping, dict):
        return None
    for name in names:
        if name in mapping and mapping[name] is not None:
            value = _unwrap(mapping[name])
            if value is not None:
                return value
    return None


def _pick_str(mapping: dict, *names) -> str:
    value = _pick(mapping, *names)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _pick_int(mapping: dict, *names) -> int:
    value = _pick(mapping, *names)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_group_wxid(wxid: str) -> bool:
    """群 wxid 以 @chatroom 结尾。"""
    return bool(wxid) and wxid.endswith(_CHATROOM_SUFFIX)


def parse_sync_payload(raw) -> list[dict]:
    """把一次同步下发的载荷还原成消息 dict 列表。

    接受：单条消息 dict、list、{"AddMsgs": [...]}、
    {"Data": {...}} / {"Data": [...]} 等嵌套形态。
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[dict] = []
        for item in raw:
            out.extend(parse_sync_payload(item))
        return out
    if isinstance(raw, str):
        return []
    if not isinstance(raw, dict):
        return []
    # 本身就是一条消息（有 FromUserName/fromUserName 或 MsgType）
    if _pick(raw, "FromUserName", "fromUserName", "from_username") is not None:
        return [raw]
    for key in ("AddMsgs", "addMsgs", "add_msgs", "Msgs", "msgs", "Data", "data"):
        if key in raw and raw[key] is not None:
            nested = parse_sync_payload(raw[key])
            if nested:
                return nested
    return []


def _parse_group_sender(content: str) -> tuple[str, str]:
    """群消息 Content 形如 "wxid_xxx:\\n正文"，拆出 (发送者wxid, 正文)。"""
    match = _GROUP_SENDER_RE.match(content)
    if match:
        return match.group(1), match.group(2)
    return "", content


def _parse_push_nickname(push_content: str, body: str) -> str:
    """PushContent 形如 "昵称: 正文"，取冒号前的昵称（正文须对得上才采信）。"""
    if not push_content or ": " not in push_content:
        return ""
    nickname, _, rest = push_content.partition(": ")
    if rest and (rest == body or body.startswith(rest) or rest.startswith(body)):
        return nickname.strip()
    return ""


def _detect_at_me(body: str, msg_source: str, self_wxid: str, bot_nickname: str) -> tuple[bool, str]:
    """判断群消息是否 @ 了机器人，并把开头的 @ 文本剥掉。

    返回 (at_me, 剥离后的正文)。
    """
    at_me = False
    text = body
    if bot_nickname:
        for marker in (f"@{bot_nickname} ", f"@{bot_nickname}\u2005", f"@{bot_nickname}"):
            if marker in text:
                at_me = True
                text = text.replace(marker, "", 1).strip()
                break
    if self_wxid and msg_source:
        # MsgSource XML 里形如 <atuserlist><atuser>wxid_x</atuser>...</atuserlist>
        if self_wxid in msg_source:
            at_me = True
    return at_me, text


def normalize_message(raw: dict, *, self_wxid: str = "", bot_nickname: str = "") -> dict | None:
    """把一条原始同步消息归一化成 dict；无法解析返回 None。

    返回字段：
    - msg_id / msg_type / from_wxid / to_wxid / content（已剥群前缀与 @）
    - raw_content（剥群前缀前的原始 Content）
    - is_group / group_id / sender_wxid / sender_nickname
    - at_me / create_time
    """
    if not isinstance(raw, dict):
        return None
    from_wxid = _pick_str(raw, "FromUserName", "fromUserName", "from_username")
    to_wxid = _pick_str(raw, "ToUserName", "toUserName", "to_username")
    if not from_wxid:
        return None
    msg_type = _pick_int(raw, "MsgType", "msgType", "msg_type", "Msgtype")
    content = _pick_str(raw, "Content", "content", "msgContent", "msg_content")
    push_content = _pick_str(raw, "PushContent", "pushContent", "push_content")
    msg_source = _pick_str(raw, "MsgSource", "msgSource", "msg_source")
    msg_id = _pick_str(raw, "MsgId", "msgId", "msg_id", "NewMsgId", "newMsgId")
    create_time = _pick_int(raw, "CreateTime", "createTime", "create_time")
    total_len = _pick_int(raw, "TotalLen", "totalLen", "total_len")

    group = is_group_wxid(from_wxid)
    group_id = from_wxid if group else ""
    sender_wxid = ""
    body = content
    if group:
        sender_wxid, body = _parse_group_sender(content)
        if not sender_wxid:
            # 兜底：没有 "wxid:\n" 前缀时，发送者未知，正文原样保留。
            sender_wxid = ""
    else:
        sender_wxid = from_wxid

    sender_nickname = _parse_push_nickname(push_content, body)
    at_me = False
    if group:
        at_me, body = _detect_at_me(body, msg_source, self_wxid, bot_nickname)

    return {
        "msg_id": msg_id,
        "msg_type": msg_type,
        "from_wxid": from_wxid,
        "to_wxid": to_wxid,
        "content": body,
        "raw_content": content,
        "is_group": group,
        "group_id": group_id,
        "sender_wxid": sender_wxid,
        "sender_nickname": sender_nickname,
        "at_me": at_me,
        "create_time": create_time,
        "total_len": total_len,
    }
