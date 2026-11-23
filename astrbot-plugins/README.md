# AstrBot 饿了么 / 酷狗插件 + 青龙脚本

把 TG 机器人 `@GoliaQL_bot` 的饿了么 / 酷狗功能搬到 AstrBot（QQ 官方 Bot，
`qq_official` 适配器），和它依赖的青龙侧脚本放在同一个仓库里。

- `plugins/astrbot_plugin_eleme/` — 饿了么登录 / 管理 / 查询 / 兑换 / 定时兑换 + 青龙管理
- `plugins/astrbot_plugin_kugou/` — 酷狗概念版扫码登录 / 提现 / 定时提现
- `qinglong/` — 青龙侧脚本（幸运星、Cookie 同步、兑换、酷狗提现），见 `qinglong/README.md`
- `tests/` — 单元测试（VM 上跑，不用连路由器）

两个插件各自完全自包含（metadata.yaml、main.py、_conf_schema.json、
requirements.txt、vendored 模块都在各自文件夹根目录下），逻辑直接搬自
TG 机器人 `wecom-elm-bot/app/`（只换了分发层和传输层）。

## 安装（重要：一个链接装不上两个插件）

已按 AstrBot **4.29.4** 源码核实（`astrbot/core/star/updater.py`、
`astrbot/core/repository.py`）：

- 后台“从 GitHub 链接安装”只认**仓库根目录**的 `metadata.yaml`
  （`inspect_plugin_directory`，找不到就报“未在仓库根目录找到
  metadata.yaml”），一个链接只装**一个**插件；
- `https://github.com/owner/repo/tree/main/plugins/xxx` 这种带子目录的链接
  也装不上——`tree/` 后面整段会被当成分支名解析，下载 404。
- 所以这个单仓库（根目录没有 metadata.yaml）**不能用链接一次装完**，
  请用下面两种办法之一：

### 办法 A（推荐）：终端 clone 后复制

```bash
# 在路由器终端（AstrBot 容器所在的那台）
cd /root
git clone https://github.com/<owner>/<repo>.git astrbot-golia-plugins
cp -r astrbot-golia-plugins/plugins/astrbot_plugin_eleme astrbot-golia-plugins/plugins/astrbot_plugin_kugou /root/astrbot/data/plugins/
docker restart astrbot
```

### 办法 B：WebUI 上传压缩包（装两次）

把 `plugins/astrbot_plugin_eleme/` 打成一个 zip、
`plugins/astrbot_plugin_kugou/` 打成另一个 zip（zip 里根目录就是
metadata.yaml），在 AstrBot 后台“插件 → 安装插件 → 上传插件压缩包”
里各传一次，装完点“重载插件”。

### 更新

不要用插件页的“更新”按钮（它会按 metadata 里的仓库地址重新拉整个
仓库根目录，校验失败，不会更新）。更新时按办法 A 重新复制一遍（或
办法 B 重新上传），再重载插件。更新失败不会破坏已装的插件（校验在
删除旧目录之前做）。

## 配置

### 1. 管理员：填 openid，不是 QQ 号

两个插件都用 `@filter.permission_type(ADMIN)`。QQ 官方适配器的
sender_id 是**用户 openid**，不是 QQ 号——所以 AstrBot 后台全局管理
员列表（`admins_id`）里要填你的 **openid**，填 `530528018` 是匹配不上的。

openid 获取办法：先随便给机器人发一条消息，然后在 AstrBot 日志里找
这条消息的 sender id（就是 openid），贴进管理员列表。

### 2. 插件配置（WebUI 插件页 → 插件配置）

| 配置项 | 说明 |
|---|---|
| `ql_base_url` | 青龙面板地址，默认 `http://192.168.31.2:5700`（与 AstrBot 同机，走内网） |
| `ql_token` | 青龙应用 token：面板“系统设置 → 应用设置”建应用（定时任务 + 环境变量 + 脚本管理权限），再 `GET /open/auth/token?client_id=…&client_secret=…` 换 Bearer token |
| `elm_phones`（饿了么） | 顺序重登的手机号列表 |
| `elm_cookie_task`（饿了么） | 青龙里 elm_cookie 任务的命令子串，默认 `elm_cookie` |
| `elm_cookie_timeout`（饿了么） | 触发 elm_cookie 后等日志的超时（秒），默认 120 |
| `yyb_base_url`（饿了么） | YYB 服务地址，默认 `http://192.168.31.2:8000` |
| `remark_phones`（饿了么） | 备注 → 手机号（`[r]` 一键重登时直接发码；成功短信绑定后会自动记住） |
| `kg_sync_qinglong`（酷狗） | 酷狗 token 是否自动同步青龙 KGGNB，默认 `false`（先测试机器人；打开需用户确认） |

**不要把任何真实密钥、token、Cookie 写进仓库里的任何文件。**
token 只填在 WebUI 配置里（`ql_token` 是 secret 遮罩显示）。

### 3. 模型（与插件并行，不阻塞）

QQ 侧聊天（非命令消息）需要模型：WebUI 配星驰 OpenAI 兼容提供商，
`https://api.xingchiapi.com/v1`，模型 `gpt-6.1-sol`，API Key 由用户
自己在 WebUI 粘贴。插件命令不依赖 LLM。

## 命令

私聊机器人直接发（群里需 @机器人）。未命中的消息放行给 LLM。

饿了么（`astrbot_plugin_eleme`）：`菜单`、`帮助`、`饿了么登录`、`登录`、
`手机号登录`（QQ 上不可用，会提示改用短信/扫码/CK）、`饿了么教程`、
`同步检查`、`饿了么查询`、`幸运星`、`饿了么兑换`、`定时兑换` / `饿了么抢兑`、
`饿了么管理` / `Cookie 检查`、`青龙任务`、`青龙日志 <id>`、`青龙执行/停止/启用/禁用 <id>`、
`青龙变量`、`青龙脚本`；流程中随时回 `q` 退出。

酷狗（`astrbot_plugin_kugou`）：`酷狗登录`、`酷狗提现`、`定时提现`、`酷狗帮助`。

“跑幸运星”没有独立命令，QQ 端同样用“青龙执行 163”触发（青龙任务 163
定时跑）。

## 与 TG 版不同的地方

- 浏览器登录（手机号登录）砍掉：容器无 Chromium。保留短信 `[1]` /
  YYB 微信扫码 `[2]` / CK `[3]` 三路。
- 酷狗 token **不在 QQ 消息里贴明文**（TG 版会发回私聊）。登录成功只报
  userid 与同步状态；`kg_sync_qinglong` 打开时 token 自动同步进青龙
  KGGNB，关闭时请在配置里打开后再重新登录。
- QR / token 消息的 120 秒自删在 QQ 侧没有等价物（群消息可撤回、私聊
  撤回待实测），降级为不贴 token 明文；二维码图片照常发。
- 多轮流程沿用 TG 的会话状态机（`state.py`），没改写成 session_waiter：
  兑换等流程状态多（9 个），状态机是已验证的逻辑，改写风险大于收益；
  分发器同样做到“命中命令 stop_event、不进 LLM，未命中放行”。
- 定时兑换 / 定时提现只在插件进程内跑（JSON 存在插件数据目录
  `data/plugin_data/astrbot_plugin_eleme/scheduled_exchanges.json`、
  `data/plugin_data/astrbot_plugin_kugou/scheduled_withdrawals.json`），
  不进青龙建任务。主动推送走私聊；群聊推送只在被动回复窗口内有效，
  推不到时只记日志，结果随时可用“定时兑换 / 定时提现”命令查看。
- 兑换安全红线不变：兑换响应异常绝不自动重试，结果未知时绝不补发。
- 备注 → 手机号的默认值（原来硬编码在 TG 代码里的 8164/7636）改到插件
  配置 `remark_phones` 里，不再写进代码；成功短信绑定后会自动记住。

## 测试

```bash
cd ~/workspace/astrbot-plugins   # 本地开发目录
python3 -m unittest discover -s tests -v
```

28 个测试：分发器（命中/放行/q 退出）、短信登录、CK 登录、YYB 槽位
已有效、青龙命令、幸运星查询、兑换起步、Cookie 检查、定时兑换 store
（UMO 字符串 chat_id）、酷狗 QR（图片 + token 不贴明文 + 同步开关）、
酷狗提现流程、main.py 分发器（stub 的 astrbot）、metadata/schema、
vendored 模块导入。不连路由器、不连青龙、不发真实网络请求。

## 待 QQ 实测（适配器已通，装完即测）

- 群 @ 触发与 `message_str` 形态（源码已确认适配器会剥掉 @ 段，但需实测确认）
- 酷狗 QR 图片在 QQ 里能否长按识别
- 长消息分段阈值（当前按 1500 字符保守切）
- 定时兑换 / 提现结果能否按时推到私聊
- 配了星驰模型后，命中命令必须 `stop_event`、不进 LLM
- 管理员 openid 是否生效（见上“填 openid”）

TG 机器人保持运行，双跑一段时间再决定 TG 是否退役，不要立刻关 TG。
