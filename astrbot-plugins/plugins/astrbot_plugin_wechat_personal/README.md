# 个人微信（小号）· AstrBot 平台适配器

给 AstrBot 另接一个个人微信小号，协议后端是 **WeChatPadPro（iPad 协议）**。
与 AstrBot 内置的 `weixin_oc` 适配器（大号）互不干扰，是独立的第二条线。

## 它怎么工作

```
小号微信 ──iPad 协议──> WeChatPadPro 服务 ──WebSocket /ws/GetSyncMsg──> 本插件适配器 ──> AstrBot
AstrBot 回复 ──> 本插件 ──POST /message/SendTextMessage（或 SendImageMessage）──> WeChatPadPro ──> 微信
```

- **收消息**：默认 WebSocket（与 AstrBot 官方 wechatpadpro 适配器相同）。
  WS 连续失败 5 次后自动切 HTTP 轮询 `/message/HttpSyncMsg`，恢复后切回。
  `receive_mode` 可改成 `ws`（只用 WebSocket）或 `poll`（只用轮询）。
  Webhook 回调方式不用：它要求 WeChatPadPro 能反向访问一个回调地址，
  WS 不需要入站端口。
- **发消息**：文本走 `SendTextMessage`，图片走 `SendImageMessage`（base64）。
  群聊发消息时 At 会带进 `AtWxIDList`。
- **登录**：适配器启动后先查登录状态；没登录就每 10 秒查一次，
  扫码上线后自动开始收消息，不用重启。
- **私聊/群聊**：群消息（FromUserName 以 `@chatroom` 结尾）标成
  `GROUP_MESSAGE`、会话为群 wxid；私聊标成 `FRIEND_MESSAGE`、会话为对方 wxid。
  群里要不要 @ 由 AstrBot 核心配置 `wake_prefix` 决定，不是本插件的事。
- **@ 检测**：群消息里 @ 机器人昵称时，消息链里会加一个 At 段，
  核心的唤醒检查据此放行；消息正文里的 `@昵称` 会被剥掉。

## 鉴权（重要）

- 本插件**不绕过、不剥离、不伪造 WeChatPadPro 的 AuthKey 授权**。
  AuthKey 是 WeChatPadPro 服务端自己的事（KnowHub 鉴权服务器）：
  用户按正常流程在 KnowHub 拿 AuthKey、配好服务端，插件只和已经
  授权好的服务端对话。本插件不调用任何 `/admin/*` 接口。
- 插件要的两个东西：
  1. `base_url`：WeChatPadPro 服务地址，如 `http://192.168.31.2:1238`。
  2. `account_key`：面板里该账号的 TOKEN_KEY（**不是 ADMIN_KEY**）。
     ADMIN_KEY 只用来登录 WeChatPadPro 面板，不用填进插件。
- 二维码只发给管理员私聊，群里发会被群友扫走登录，所以「微信登录」
  命令在群里会被拒绝。

## 部署（路由器）

### 1. 起 WeChatPadPro 服务

复用已有的 compose（`~/workspace/wechatpadpro-vm/docker-compose.yml`，
镜像 `ghcr.io/rxsume/wechatpadpro:latest`，host 网络，API 端口 1238，
MySQL/Redis 同机）。关键环境变量在 `.env` 里：`PORT=1238`、
`WECHAT_PORT=8080`、`ADMIN_KEY`（面板登录用）。

```bash
cd /root/wechatpadpro-vm   # 或 compose 所在目录
docker compose up -d
curl -s http://127.0.0.1:1238/login/GetLoginStatus | head -c 200
```

### 2. 面板里建账号、拿 key

1. 浏览器打开 `http://192.168.31.2:1238`，用 `ADMIN_KEY` 登录面板。
2. 面板里添加账号（按提示在 KnowHub 取 AuthKey、填进服务端配置——
   这是 WeChatPadPro 的正常授权流程）。
3. 账号建好后，在面板里复制该账号的 **TOKEN_KEY**（`account_key`）。

### 3. 装插件

```bash
# 插件目录拷到 AstrBot 的插件目录（与 astrbot_plugin_eleme 同级）
cp -r astrbot_plugin_wechat_personal /root/astrbot/data/plugins/
docker restart astrbot
```

### 4. 配平台

AstrBot 面板（`http://192.168.31.2:6185`）→ 消息平台 → 新增平台 →
选 `wechat_personal`（个人微信（小号，WeChatPadPro iPad 协议）），填：

- `base_url`：`http://192.168.31.2:1238`
- `account_key`：第 2 步复制的 TOKEN_KEY
- 其余留默认

插件配置页（_conf_schema.json）里填同一份 `base_url` / `account_key`，
供「微信登录」「微信状态」命令用。保存后重启 astrbot 容器。

### 5. 扫码登录

在已经通着的 QQ（或大号微信）上**私聊**机器人发：

```
微信登录
```

机器人回一张二维码（或面板扫码页链接），用**小号微信**扫码确认。
二维码约 2 分钟过期，过期重发一次命令。扫完后适配器自动上线，
不用重启。再发 `微信状态` 确认显示“在线”。

### 6. 首条消息测试

1. 用另一个微信给小号发一句 `你好`，看 AstrBot 是否回了（LLM 回复）。
2. 把小号拉进一个群，在群里 @小号 发一句，看是否回了。
3. 给小号发一张图片，看 LLM 是否收到了图片。
4. 看 AstrBot 日志：`docker logs astrbot | grep wechat_personal`。

## 命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `微信登录` | 仅管理员、私聊 | 取一张新的登录二维码 |
| `微信状态` | 仅管理员、私聊 | 查小号在线状态、昵称、wxid |

## 能做什么、不能做什么

能：
- 文本、图片收发；私聊、群聊；群 @ 检测；自动重连；扫码后自动上线。

不能（本次范围外）：
- 语音、视频、表情、应用消息（MsgType 34/43/47/49）：收到会跳过，
  只记一条 debug 日志，不会回“不支持”。
- 引用回复、撤回、拍一拍、朋友圈：没接。
- 主动推送：`send_by_session` 已实现（定时任务等可用），但微信侧
  没有 QQ 官方机器人那种“被动回复窗口”限制，正常发就行。

## 待首次真机确认的事（代码里已做兼容处理，但没实测过）

1. **同步消息的字段名**：WeChatPadPro 不同版本/通道的字段名可能是
   PascalCase（FromUserName/Content/MsgType）或 camelCase
   （fromUserName/msgContent/msg_type），字符串还可能是
   `{"string": "..."}` 包裹。解析器对这些变体都兼容，但第一次
   收到真消息时请看日志确认 `wechat_personal` 没有“无法解析”的警告。
2. **二维码返回形态**：`GetLoginQrCodePad` 可能直接给 base64 图片，
   也可能只给二维码内容 URL。命令会优先发图片，拿不到图片时给
   面板扫码页链接（ShowQrCode），两种都能扫。
3. **图片下载**：`GetMsgBigImg` 是分包下载（Section{StartPos, DataLen}），
   代码按 swagger 的 DownloadParam 实现；若服务端返回的包里没有
   base64 数据，会退化成只发 `[图片]`，并在日志里记警告。
4. **登录状态解析**：`GetLoginStatus` 的返回值形态没写进 swagger，
   解析器按常见形态（Status/online/IsLogin 等）兼容，解析不出时
   按在线处理并记日志。

## 文件

- `main.py`：Star 插件入口 + 微信登录/状态命令。
- `wechat_platform_adapter.py`：Platform 子类（注册名 `wechat_personal`）。
- `wechat_platform_event.py`：AstrMessageEvent 子类（send 走微信发）。
- `wechat_client.py`：WeChatPadPro HTTP + WS 客户端。
- `wechat_convert.py`：纯函数消息解析（无第三方依赖，可单测）。
- `tests/`：pytest 单测（在插件目录跑 `python -m pytest tests/ -q`）。
