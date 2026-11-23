# astrbot_plugin_kugou（酷狗）

TG 机器人 `@GoliaQL_bot` 的酷狗功能，搬到 AstrBot（QQ 官方 Bot）。

## 命令

- `酷狗登录`：生成概念版扫码 QR（图片 + 链接，有效期约 2 分钟；`1` 换一张，`q` 取消），后台轮询扫码状态
- `酷狗提现`：选账号（`[0]` 全部）→ 选档位（1–5，立即写青龙 `KG_WITHDRAW_COINS`）→ `[1]` 立即提现 / `[2]` 提交定时抢兑
- `定时提现`：查看 / 按序号取消已安排的定时提现
- `酷狗帮助`

## 配置（`_conf_schema.json`）

`ql_base_url`、`ql_token`（secret）、`kg_sync_qinglong`（默认 `false`）。
管理员在 AstrBot 后台全局管理员列表里配 openid（不是 QQ 号），见仓库
根 README。

## QQ 侧降级

token **不在 QQ 消息里贴明文**（TG 版会发回私聊）。登录成功只报 userid
与同步状态；`kg_sync_qinglong` 打开时 token 自动同步进青龙 KGGNB，
关闭时请在配置里打开后再重新登录。

## 文件

- `main.py`：Star 子类 + 统一分发器（命中命令 stop_event，未命中放行 LLM）
- `core.py`：流程状态机（由 TG `app/bot.py` 的酷狗部分改写）
- `texts.py`：帮助文案
- vendored：`qinglong.py`、`kugou_concept3114.py`、`kugou_concept_client.py`、
  `kugou_concept_crypto.py`、`kugou_withdraw.py`、`kg_withdraw.py`（青龙
  任务 246 的提现脚本，v1.2）、`scheduled_withdrawal.py`、`state.py`
  （只换了分发层和传输层；`scheduled_withdrawal.py` 的 chat_id 由
  Telegram int 改成 QQ 会话 UMO 字符串）

定时提现存在插件数据目录 `data/plugin_data/astrbot_plugin_kugou/`，
不进青龙建任务；与青龙任务 246 并存时靠“今日已提次数”去重跳过。
