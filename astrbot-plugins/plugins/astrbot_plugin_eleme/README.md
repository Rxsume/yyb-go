# astrbot_plugin_eleme（饿了么）

TG 机器人 `@GoliaQL_bot` 的饿了么功能，搬到 AstrBot（QQ 官方 Bot）。

## 命令

- `菜单` / `帮助` / `饿了么教程`
- `饿了么登录` / `登录`：`[1]` 短信验证码登录、`[2]` 微信扫码登录（YYB 同步）、`[3]` CK 登录；`[s]` 跳过当前账号（顺序登录中）
- `手机号登录`：QQ 上不可用（容器无 Chromium），会提示改用 `[1]/[2]/[3]`
- `同步检查`：列青龙 ELM_COOKIE 各槽位备注（只读）
- `饿了么查询`：`[1]` 幸运星 / `[2]` 奶茶（`[3]` 物流还没接通，不搬）
- `幸运星`：选账号查余额和今日获取
- `饿了么兑换`：选账号 → 商品商城（分页）→ `[1]` 立即兑换 / `[2]` 提交定时抢兑
- `定时兑换` / `饿了么抢兑`：查看 / 按序号取消已安排的定时兑换
- `饿了么管理` / `Cookie 检查`：`[s]` 查失效、`[r]` 一键重登失效账号
- `青龙任务` / `青龙日志 <id>` / `青龙执行/停止/启用/禁用 <id>` / `青龙变量` / `青龙脚本`
- 流程中随时回 `q` 退出；“跑幸运星”用“青龙执行 163”触发

## 配置（`_conf_schema.json`）

`ql_base_url`、`ql_token`（secret）、`elm_phones`、`elm_cookie_task`、
`elm_cookie_timeout`、`yyb_base_url`、`yyb_ref`、`remark_phones`。
管理员在 AstrBot 后台全局管理员列表里配 openid（不是 QQ 号），见仓库
根 README。

## 文件

- `main.py`：Star 子类 + 统一分发器（命中命令 stop_event，未命中放行 LLM）
- `core.py`：流程状态机（由 TG `app/bot.py` 的饿了么部分改写）
- `texts.py`：菜单 / 帮助 / 教程文案
- vendored：`qinglong.py`、`lucky_star.py`、`exchange.py`、`eleme_login.py`、
  `yyb.py`、`cookie_watch.py`、`scheduled_exchange.py`、`state.py`
  （只换了分发层和传输层；`scheduled_exchange.py` 的 chat_id 由
  Telegram int 改成 QQ 会话 UMO 字符串）

定时兑换存在插件数据目录 `data/plugin_data/astrbot_plugin_eleme/`，
不进青龙建任务；兑换响应异常绝不自动重试。
