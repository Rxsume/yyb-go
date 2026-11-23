#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
饿了么「幸运星」会员权益中心自动化 · 青龙面板 / 多账号 / Python3
================================================================

逆向目标：https://tb.ele.me/app/TBTakeout/engage-hub/home
项目形态：TBTakeout/engage-hub（阿里 ICE + mtop 协议），签到奖励币种 prizeType = STAR（幸运星）

流程
----
  首页聚合（顺带取幸运星余额并播报）→ 签到 → 夺宝/抽奖 → 下单免单抽奖 → 答题
  → 查询任务 → 浏览/点击埋点 → 重新查询 → 领取任务奖励
  → 刷新余额 + 收益明细 → 兑换商城 → 汇总推送（每个号都带「现有多少星」）

修复说明（2026-11-17）
----------------------
  兼容备注#完整Cookie、多账号换行、以及同一账号Cookie被#分成片段的情况。
  日志隐藏凭据；FAIL_SYS_USER_VALIDATE等验证/登录错误立即停止该账号。
  不提供验证码或风控绕过；请本人在官方页面完成登录与验证。
  保留定向商城功能；自动兑换须指定商品ID或完整名称。
  加入截图任务分类和只读任务清单；不代表具备支付宝/闲鱼独立授权流程。
  1.5.4（2026-11-17）：新增天天免单集合 2602（ETMD/ETMD_CHANNEL），淘宝闪购与
  支付宝闪购是同一后端两个入口；入口路径任务不再一律跳过，THIRD 不再只认
  commercial_click/inter_change。仍不能自动做的：真实下单、真实邀请助力、
  支付宝激励广告（需 my.createRewardedAd 真播）。
  1.5.5（2026-11-17）：09:00 实跑证明 2602 只有 6 个任务、奖励是 +1 非星，
  截图那批（55–100 星/个）不在其中；改为跑 ETMD 首页里的全部任务集合
  （missionCollectionMax=4），按 missionDefId 去重。累计任务保护同 1.5.4。
  1.5.7-exec（2026-11-17）：回调/点击/浏览执行器恢复并默认启用
  （v1.5.5-autoflow 那版停用了 THIRD 通用执行器、跳过路径 PAGEVIEW、
  且不跑 2602，导致 0 执行 0 领奖）；新增接取/进度/领取确认计数，
  任务汇总改为 autoflow 格式；启动时打印执行器状态，免得静默停用。
  仍不能自动做的：真实下单、真实邀请助力、支付宝激励广告；付费投放
  的 THIRD 任务服务端可能 417 拦截，失败一次即停手该任务。
  1.5.8-h5exec（2026-11-17）：1.5.7-exec 的执行器/确认计数 + 官方 H5
  身份（platform=ios、prodVersion=1.4.27、ttid=H5@Web_iphone_1.4.27，
  launchChannels 数组 []），专配 H5 会话 Cookie 用——用它跑 3112
  才能看到官方 H5 那 32 项（含 13 个 55–100 星任务）。注意：必须配
  H5 会话 Cookie（iPhone Safari 登录的那个），用 Android Cookie 跑
  它会报错或只回 2 项；ELM_TASK_H5=0 可退回 Android 身份。
  1.5.8-fix1（2026-11-17）：修 1.5.8 用 Android Cookie 跑时 3112 和
  2602 都报 FAIL_SYS_BIZPARAM_MISSED 的问题。根因：launchChannels
  被全局改成数组 []，但 2602（ETMD）走 Android 身份、服务端只认
  字符串 "[]"；3112 用 H5 身份撞上 Android 会话也被拒。改动：按身
  份选 launchChannels（H5 用数组、Android 用字符串）；3112 查询若
  报 FAIL_SYS_BIZPARAM_MISSED 且当前是 H5 身份，自动退回 Android
  身份重试一次（至少按 1.5.7 的 23 项跑，不整轮失败）。
  1.5.9-h5list（2026-11-17）：把 20:30 Stream HAR（Safari H5 会话
  querytask 3112，35 项）里未做的 18 项写进脚本（KNOWN_H5_TASKS，
  含 pageSpm 与每任务 missionXId）。querytask 只回 23 项时按
  missionDefId 去重并入，一起尝试：PAGEVIEW 走 pageview、THIRD 走
  trigger。注意：这只是把任务“递给”执行器，服务端仍可能按身份/
  付费投放拦 trigger（417），成败看实跑日志；H5 Cookie 仍是正路。
  1.5.10-h5claim（2026-11-17）：修 1.5.9 实跑“8 个接口接受、0 领
  奖”的问题。根因：重新查询（Android 会话）看不到并入的 H5 任务，
  它们在第二轮就丢了，且进度永远确认不了、领奖走不到。改动：上报被
  接受的已知 H5 任务在本地标一个阶段 FINISH（下一轮当 reward 去领
  奖），重新查询后重新并入（带本地进度）；领奖成功把该阶段标
  SUCCESS，领奖失败把阶段打回 RUNNING 重试。手机截图已证明百度地
  图触发后变“领取奖励”，这版就是让脚本自己把这一步领了。
  1.5.11-stagefix（2026-11-17）：修 1.5.10 实跑“8 次领奖全被
  CLIENT_PARAM_STAGE_ERROR 拒”。根因：KNOWN_H5_TASKS 的阶段只
  存了 status/rewardStatus，没带 stageCount，领奖请求缺 count 参
  数。已从 20:30 HAR 原数据把每阶段的 stageCount（单阶段=1、多阶
  段=1..N）与 sourceAction 子类型补进 KNOWN_H5_TASKS；receive_prize
  缺 stageCount 时按阶段序号兜底 count=idx+1。另：日志里的“H5 身份
  被服务端拒绝”不是故障，是当前 Cookie 为 Android 会话、H5 身份必
  然被拒，脚本正确退回 Android 身份继续跑；要 H5 身份直通须把
  Stream 抓到的 H5 会话 Cookie 另加为青龙新账号。
  1.5.12-sync（2026-11-17）：把 KNOWN_H5_TASKS 同步到 21:18 最新
  HAR 的状态。8 个已领任务（淘宝芭芭农场等，合计 540 星已到账）的
  阶段标成 FINISH/SUCCESS，脚本不再重复上报它们；剩下 10 项未做
  （8 个 417 + 点外卖 + 邀请好友）保留 RUNNING/TODO。另：支付宝
  截图里的 5 个新任务不在饿了么 H5 清单里，是支付宝渠道专属，支付
  宝 App 的 querytask 抓不到（证书锁定），没有任务编号加不进去。
  1.5.13-stagefix2（2026-11-17）：修领奖报
  RECEIVE_MISSION_REWARD_STAGE_NOT_FINISH_ERROR（阶段进度已满、
  但服务端阶段仍是 RUNNING，尚未翻成 FINISH）。根因：上报被接受
  后本地标 FINISH、立即领奖，服务端还没处理完，领奖时阶段仍是
  RUNNING；旧逻辑把这次失败当死失败停轮，任务再也没机会重试。
  改为：该错不算死失败、不停轮，本地阶段打回 RUNNING，下一轮重
  新上报再试（上报次数仍受 ELM_TASK_TIMES 限制），轮末指纹没变
  也不因此停轮。missionXId 已在 trigger 与 receiveprize 里都带
  了，这错与 missionXId 无关，改它修不了。H5 身份被拒退回
  Android 与上报完成自动领奖在 1.5.8/1.5.10 已有，本版未改。
  1.5.14-claimwait（2026-11-17）：领奖前等一等（ELM_CLAIM_WAIT，
  默认 5 秒，0 为不等待）。上报接受后服务端把阶段翻成 FINISH 要
  时间，马上领会撞 STAGE_NOT_FINISH_ERROR；H5 会话也会报这错，
  说明与身份/会话无关，纯是服务端翻阶段慢，等几秒再领更可能一次
  过。配合 1.5.13 的不停轮重试，两层兜底。
  1.5.15-multiacct（2026-11-17）：修多账号误跳过。1.5.12 把 21:18
  HAR（8164 号会话）的 8 个已领任务在 KNOWN_H5_TASKS 里标成
  FINISH/SUCCESS，但 KNOWN 是多账号共用的模板，状态是按账号走的；
  0261 号其实没做这 8 个，脚本却按 KNOWN 直接跳过，导致“任务都
  完成不了”。改为 KNOWN 里 18 项全回 RUNNING/TODO（只保留定义：
  missionXId、pageSpm、stageCount），做没做以服务端实时返回为准，
  上报/领奖结果说了算。8164 号重跑这 8 个会多几次尝试，服务端会
  判已领，无害。
  1.5.16-callback（2026-11-17）：试解 417。扒官方 H5 的 JS
  （engage-hub 1.4.27）发现：H5 页面根本不调 event.trigger（全
  部 16 个 JS 里 0 次），它调的是 mtop.ele.biz.growth.task.event
  .new.callback v1.0（任务上报接口，ALSC_TASK_REPORT）。trigger
  是原生 App 的接口，服务端按 bizScene（任务 ID）拦付费投放任
  务。改为 THIRD 任务 trigger 报 417 时，改调 new.callback 兜底
  （bizScene 用 interact_center，不用任务 ID），接受即按已上报
  处理、继续走领奖；不接受则记原 417。成败以实跑日志为准。
  1.5.17-source（2026-11-17）：回调缺 source。1.5.16 实跑：9 个
  417 任务调 new.callback 全被拒，FAIL_SYS_BIZPARAM_MISSED::
  缺少业务参数source。解 21:18 HAR（zstd 解压）得：source 是任务
  的 ext.extValue.interchangeBizType（互换渠道码）：CDQC（成都
  趣创：飞猪/滴滴/动动有金/闲鱼看视频/生活缴费）、SHLQ（上海龄
  巧：天天打卡/页面点击/猜价格）、SECONDFISH_GAME（闲鱼小游戏）。
  新版回调带上 source=interchangeBizType（KNOWN 表里已补；实时
  列表任务从其 ext 里读）。若服务端再报缺别的参数，按报错续补。
  1.5.18-alipay（2026-11-17）：加 5 个支付宝专属任务。用户用
  Quantumult X 抓到支付宝闪购小程序（MiniAppId 2021005151656223）
  的 querytask 全量 24 项，含此前 H5/Android 清单里没有的 5 个：
  48532001 浏览页面得奖励（PAGEVIEW，3 阶段各 +80）、48228002
  去一键领取优惠得奖励（THIRD，+60，SHLQ）、49238001 点击领取
  2 个红包得奖励（THIRD，+80，SZYC 新渠道码）、48390007 去玩赚
  现金红包（PAGEVIEW，+55）、47858001 动动手指赚现金（PAGEVIEW，
  +55）。合计最多 +490 星。已写进 KNOWN_H5_TASKS（与 18 项同表
  合并），THIRD 的 2 个走回调兜底（source 按渠道码）。48532001
  是支付宝激励广告（名含“灯火-激励广告”），若服务端要求真播广
  告则 pageview/领奖会失败，以实跑为准。是否需支付宝会话 Cookie
  另加账号，也以实跑为准（H5 已知任务在 Android 会话下能领，这
  5 个或也一样）。24 个 missionId 全量已记
  ~/workspace/elm_missionIds_alipay_20261117.json。
  1.5.19-token（2026-11-17）：回调缺 token。1.5.18 实跑：5 个支付
  宝任务全领到（+430，余额 8349→8779，48532001 三阶段全 +80、
  49238001 走 trigger 直通 +80、48390007/47858001 各 +55），但
  11 个 417 任务的回调全被拒，FAIL_SYS_BIZPARAM_MISSED::缺少业
  务参数token（source 已过）。token 在任务 ext.extValue 的跳转
  链接里（missionActionUrl/alipayActionLink/elemeActionLink/
  taobaoActionLink，token= 或 flashSaleToken=，171 字符 base64，
  约 128 字节）。注意 token 是会话相关的：同一任务在 21:18 H5
  会话与 23:41 支付宝会话里的 token 完全不同，硬编码别会话的
  token 没用，必须取当前会话的。改法：report_callback 先从任务
  自身 ext 里取 token（实时列表任务自带）；KNOWN 任务没有 ext
  时，用 missionIds 定向查一次 querytask 拿当前会话的 token
  （官方 H5/支付宝都支持 missionIds 定向查）。取不到时仍发回
  调（会报缺 token，但不崩），并在日志里提示。
  1.5.20-multi（2026-11-17）：换打法，不再逐个猜参数。1.5.19
  实跑（双账号）：9 个 417 任务分两拨——6 个取不到 token（H5
  专属任务，Android 会话看不到，定向查询也拿不到），3 个 token
  有了但缺 plat（滴滴/飞猪/闲鱼玩1关）。三处一起改：
  （a）trigger 先换 bizScene 重试：417 是按任务级 bizScene
  过滤，改用集合级 interact_center（回调用的那个）再调一次
  trigger，绕过过滤；通了就按已上报处理，不通再走回调。
  （b）回调 token 加硬编码兜底：取不到时用 HAR 里抓到的 token
  （21:18 H5 会话 + 23:41 支付宝会话，10 个任务有值）。token
  会话相关，硬编码未必生效，生效与否看实跑——能过就过，
  不过仍报缺 token/无效 token。
  （c）回调补 plat：取当前身份的 platform（H5 身份 ios、
  Android 身份 android），环境变量 ELM_CALLBACK_PLAT 可改。
  若服务端再报缺别的参数，按报错续补。
  1.5.21-callback（2026-11-18）：1.5.20 实跑（双账号，+2948/
  +2518）看着全通，实则 9 个 417 任务一个没领到——“换 bizScene
  后 trigger 接受”是假成功：服务端收了 interact_center 的
  trigger（不报 417），但不算任务完成，阶段永远不翻 FINISH。
  证据：实时列表里的 3 个（滴滴/飞猪/闲鱼玩1关）上报后指纹不
  变，第一轮末就被 halt，之后再没出现；KNOWN 里的 6 个靠本地
  标 FINISH 走到领奖，三轮全撞 STAGE_NOT_FINISH。根因：官方 H5
  根本不调 trigger，它调 new.callback——trigger 是原生 App 接
  口，inter_change 任务只能走回调完成。改法：417 直接调回调，
  不再试 trigger 重试；回调接受后记进 not_ready（指纹没变也不
  halt，给服务端翻阶段留一轮）；回调被拒则记原错 halt（报错会
  点名下一个缺参）。另：missionXId 每天都变（用户确认；00:18
  支付宝 HAR 与 21:18 H5 HAR 对比，23 项 KNOWN 里 13 项已变），
  本版把 KNOWN 里 13 项的 missionXId 同步到 00:18 HAR 的新值；
  49016001 在实时列表里、每次现查现取，不用硬编码；48602001
  不在 00:18 HAR 里，仍用旧值，待用户新抓。plat 默认是回退后
  的 Android 身份 platform=android（H5 身份先被拒才回退），
  若回调报 plat 不对，依次试 ELM_CALLBACK_PLAT=ios / eleme /
  alipay。
  1.5.22-plat（2026-11-18）：1.5.21 实跑（00:33，双账号，0 领，
  耗时 283 秒）：9 个回调全报 500::系统错误，不再是缺参。plat
  默认是回退后的 android，服务端对这个值直接 500。改法：回调
  里按顺序自动试 plat——ELM_CALLBACK_PLAT（若设了）→ ios →
  eleme → alipay → android；只有 500 才换下一个，缺参等别的错
  立即返回。另把 CALLBACK_TOKEN 里 8 项同步到 00:18 HAR 的新值
  （token 也每天变，旧的 8 个全变了），新加 44914006；48602001
  与 49016001 不在 00:18 HAR 里，仍用旧 token。待实跑看哪个
  plat 通。
  1.5.23-sync（2026-11-18）：用户新抓的 querytask（00:43）里 26 项
  missionXId 全变（相对 00:18 HAR），token 全没变（与 00:18 HAR
  相同，CALLBACK_TOKEN 不用动）。把 KNOWN 里 13 项 missionXId
  同步到这份新值；missionXId 解开看是前缀相同、尾部不同（加密
  实例 ID 带随机尾），所以旧值未必失效，但用最新值最稳。另 3
  个新 THIRD（44914003/44914006/48516001）都在 Android 实时列表
  里，不用进 KNOWN。
  1.5.24-config（2026-11-18 01:10）：用户发的 page.recall HAR
  （011041，幸运星页加载）里直接拿到了服务端下发的回调配置
  emod-afun-task-report-config（组件版 0.0.1）：回调数据只由
  mapRules 拼，bizScene 是常量 "INTERACT_CENTER"（大写），其余
  token/source/plat/key1~key6 全取自做完任务跳回的落地页 URL
  query。1.5.23 错在三处：bizScene 发了小写 interact_center；
  多发了官方回调根本不带的字段（eventType/eventId/collectionId
  /missionId/missionXId/lat/lng/riskScene 等）；plat 试的是
  ios/eleme/alipay/android，根本不是平台标识，真正的 plat 是
  secondfish/alipay_task/alipay_welfare/baidu/taotian_guang/
  taote/zijie/wlj/ast/huahuaka 这类第三方平台名（按落地页 URL
  的 plat= 匹配规则）。本版回调只发 bizScene/token/source/plat
  四个字段，plat 按任务映射（闲鱼任务 secondfish，其余先
  alipay_task），500 再按序重试。另把 KNOWN 里 13 项 missionXId
  同步到 00:57 全量 querytask（010033）的新值；token 与 00:18/
  00:43 一致，CALLBACK_TOKEN 不用动。
  1.5.25-nocommon（2026-11-18 01:16）：1.5.24 实跑（01:13，双账号，
  0261 余额 11787 不变，0 领）：9 个回调全报 400::参数校验出错
  （不再是 500，服务端开始校验而不是崩溃），但仍一个没通。根因：
  mtop() 会把 task_common_data 并进所有任务接口的 data，而官方
  H5 回调的 data JSON 只含 mapRules 字段——1.5.24 虽然在
  report_callback 里只拼了 4 个字段，mtop 又把 accountPlan/
  lat/lng/locationInfos/platform/product/prodVersion/ttid 8 个字
  段并了进去，服务端校验 data 时报 400。本版 mtop 对 callback
  不再并 common 字段（身份字段仍留在 query 与签名里，query 的
  locationInfos 照常带）；report_callback 本身不变。另：本轮 12
  次领奖全撞 RECEIVE_MISSION_REWARD_TARGET_HAD_RECEIVE_ERROR
  （本地标 FINISH 的任务服务端早已领完），属重复领奖尝试，非回
  调问题。
  1.5.26-retry400（2026-11-18 01:19）：1.5.25 实跑（01:18，0261
  余额 11787 不变，0 领）：9 个回调仍全报 400::参数校验出错，
  去掉 common 字段没能修掉 400，说明 400 不是 data 里多字段的
  错，而是某个值的错（token/plat/source 其中之一）。本版两处改：
  （a）plat 在 400 时也重试（之前只在 500 时重试；1.5.22 的
  500 结论来自旧 data，新 data 下错 plat 可能报 400），按序试
  完映射值与重试表；（b）从跳转链接取 tagType（如
  rookieCallbackTransfer?tagType=didi / fish）也当 plat 候选
  加进去（落地页 plat 可能就是它；闲鱼的 tagType=fish 但配置
  规则是 secondfish，两者都试）。若 10 余个 plat 全 400，则
  plat 不是原因，剩 token（跳转链接 token 与落地页 token 可能
  不是同一个）或缺 key1~key6，只能靠一条真的回调来对。
  1.5.27-welfare（2026-11-18 01:25）：1.5.26 实跑（01:20，双账号）
  大突破：9 个回调全被接受，plat 全是 alipay_welfare——
  alipay_task/secondfish 各报 400、didi 报 500，只有
  alipay_welfare 通。plat 问题至此解决：这 9 个任务（包括闲鱼
  两个）走的都是 alipay_welfare 规则（配置里 note=
  alipay_welfare，token 取 userToken、key1=source、key2=
  iepTaskSceneCode、key3=iepTaskType）。本版把 CALLBACK_PLAT
  全改成 alipay_welfare 打头，不再先试 alipay_task/secondfish
  浪费两次 400。但回调接受后阶段仍不翻 FINISH，领奖全撞
  RECEIVE_MISSION_REWARD_STAGE_NOT_FINISH_ERROR（服务端阶段仍
  RUNNING），0261 余额 11787 不变、0 领，8164 余额 63377（+140，
  是签到/夺宝等别的进账）。阶段不翻的可能原因：服务端要拿
  key2/key3（iepTaskSceneCode/iepTaskType，落地页才有，脚本没
  有）去结算，或要与三方核实用户真做完了任务——回调接受不
  等于任务完成（与 1.5.20 trigger 假成功同类）。本版只改 plat
  顺序，不猜 key2/key3；若再跑阶段仍不翻，就得靠一条真的回调
  或用户在手机上真做一次来对。
  1.5.28-keys（2026-11-18 01:32）：1.5.27 实跑（01:26，双账号）：
  回调直接 alipay_welfare 一次通，不再撞 400，但阶段仍不翻，
  0261 余额 11787 不变、0 领；用户截图证实手机上这 9 个仍是
  “去浏览”未完成。根因锁定在 key 参数：alipay_welfare 规则
  的 mapRules 除了 bizScene/token/source/plat，还有 key1=
  source、key2=iepTaskSceneCode、key3=iepTaskType——脚本一直
  没发 key1~key3，服务端收了回调（SUCCESS）却无法把上报对应
  到具体的 IEP 任务实例，所以阶段不翻。本版补发 key1~key3：
  key1=source（与 source 同值），key2 默认 inter_change（任务
  的 scenceCode），key3 默认 THIRD（任务类型），另有环境变量
  ELM_CALLBACK_KEY2/ELM_CALLBACK_KEY3 可改。mock 42/42。若这
  版阶段仍不翻，说明 key2/key3 的真值不在任务数据里（只在落
  地页 URL），或服务端要与三方核实真做完——那时这 9 个只能手
  机上点“去浏览”真做，或靠一条真的回调来对。
  1.5.29-ids（2026-11-18 01:44）：用户发来 8 条新 HAR（01:39–
  01:43），仍无 new.callback 请求（是 querytask/receiveprize/
  homepage/登录检查）。但 014009/014305 两次全量 querytask
  （8164 支付宝会话，26 项）确认：天天打卡 48432001 已是
  SUCCESS（用户手机上真做完了），生活缴费、一键领取优惠也已
  SUCCESS；剩余 5 个 417 任务仍 TODO（页面点击/飞猪/猜价格/
  滴滴/闲鱼看视频）；49016001、48602001 不在支付宝清单里
  （H5 专属）。新任务 2 个已加进 KNOWN：45224001 逛商家积分
  兑好礼（PAGEVIEW，+55，用户 01:42 已领）、46172002 搜一搜
  【领优惠】（PAGEVIEW，+55，未做）；KNOWN 现 25 项。另：
  missionXId 每次 querytask 都变（014009 与 014305 的 26 项
  全变），所以 KNOWN 里的 missionXId 只是模板，运行时按
  missionIds 定向查询取现值；本次同步用 014305（最新）的值。
  1.5.33-pagespm（2026-11-18 09:50）：修 1.5.32 实跑里“开启签到订阅”出现
  两条的问题：KNOWN 里新加的两个 PAGEVIEW（45226021 闪购会员日/47754001
  开启签到订阅）漏了 pageSpm/pageScene，合并后那条报“PAGEVIEW 缺
  collectionId/missionId/pageSpm，不能上报”。现已补上 pageSpm
  a2ogi.bx1500380、pageScene COMMON。其余与 1.5.32 相同。
  1.5.34-partnercb（2026-11-19）：用户 17:16 Quantumult X HAR
  （quantumult-x-2026-11-19-171630.har，3254 条）实抓结论：App 完成
  THIRD 任务根本不调 new.callback，而是打开任务跳转链接里的合作方
  rookieCallbackTransfer（滴滴 tagType=didi/channelId=255、闲鱼
  tagType=fish/channelId=272，token 在链接里），经支付宝 OAuth 后由
  合作方回传完成；这就是 new.callback“接受却不翻阶段”的根因。
  改动：① KNOWN_H5_TASKS 的 26 个 missionXId 全部刷新为本次 HAR 值，
  新增 49270001 点外卖送大额幸运星；② CALLBACK_TOKEN 滴滴/闲鱼换
  新鲜 token；③ 新增 PARTNER_CALLBACK 表（host/tagType/channelId）
  与 POP2ME_PAGES 表，并实现 report_partner_callback：417 后先 GET
  跳转链接里的 rookieCallbackTransfer（直连失败则走 sing-box 代理
  127.0.0.1:1080），再走 new.callback；sign 头含密钥、脚本伪造不
  了，无 sign 的 GET 会 302 到支付宝授权，是否翻阶段看重新查询；
  ④ 多阶段任务次数按剩余阶段数放开（ELM_TASK_TIMES 默认 3→5，
  单任务上限取 max(默认, 剩余阶段数)，封顶 10）；⑤ 猜价格接 AI：
  新增 ai_guess_price（OpenAI 兼容接口，ELM_AI_API_BASE +
  ELM_AI_API_KEY 配置），但本次 HAR 只抓到游戏页打开与
  GuessPriceInfo，未抓到提交猜价那一步（提交接口名未知），AI 猜价
  的提交待用户玩一局猜价格并抓 HAR 后再接；现版本先记游戏信息并
  走原回调路径。
  1.5.35-times7（2026-11-19）：用户令把默认浏览/点击次数加到 7 次防漏。
  ELM_TASK_TIMES 默认 5→7（青龙未设该变量，以代码默认 7 为准）；单任务上限
  仍取 max(默认, 剩余阶段数)，封顶 10；外层轮数随 cap 变为 7*3+3=24 轮。
  20:01 实跑：8164 领了 5 份（含之前卡住的猜价格/闲鱼看视频/动动有金），
  但 0261/7636/1880 仍 0 份、浏览点击 5 个 4 号全卡 STAGE_NOT_FINISH，
  次数不是唯一瓶颈，服务端翻阶段仍需 App 内真做或多次上报。
  1.5.36-count1（2026-11-19）：用户问浏览页面为什么一轮只加一次
  次数、闲鱼视频/页面点击为什么没修。查实：① missionIds 全在，
  47 项 KNOWN 里有 48596001/48324002/48532001，回调也都接了，
  不是没加，是加了也翻不过去；② 一轮只加一次是脚本自己的 bug：
  领奖报 TARGET_HAD_RECEIVE_ERROR 就 halt 任务，本版改为不 halt，
  下一轮重新上报再试，一轮可连推多阶段；③ 领奖 count 固定发 1：
  App 的 4 次成功领奖 count 全是 1（含多阶段 48228002），脚本
  之前按阶段号发（第 4 阶段发 4），后阶段全撞 TARGET_HAD_RECEIVE
  很可能就是这个原因；④ pop2me 真路走不通：网关 Start 实测
  200 但 read userId fail——网关会话要 GetDeviceId，而
  GetDeviceId 要支付宝 authCode，脚本拿不到，旧 deviceId 复用
  也失败（会话绑定）；⑤ App 里 8164 的 1/3 显示是页面缓存没刷
  新，20:01 那轮其实已领 +80，杀掉重进活动页即刷新。
  1.5.37-fishcb（2026-11-19）：用户发来 19:06 HAR
  （quantumult-x-2026-11-19-190614.har，1130 条）让把回调加进去修。
  实抓结论：① 闲鱼看视频的真实完成信号是小游戏页 POST
  mobile.yundps.com/IdleFish/callback，请求体只有
  {"taskSign": "other-272-8 位活动号-跳转链接 token-7 位随机数"}，
  返回 {"code":0} 即完成；token 与 rookieCallbackTransfer 链接里
  的是同一个，运行时现取。已实现 report_fish_callback，417 后先
  GET rookieCallbackTransfer，再 POST IdleFish/callback，最后走
  new.callback；② 1.5.36 的 count=1 是错的：19:06 HAR 里 App 的
  5 次成功领奖 count 是 2/3/4/5（阶段号），1.5.36 实跑 8164 领 0
  份、155 次领奖请求全失败——页面点击 2/3（第 3 阶段未领）用
  count=1 也报 TARGET_HAD_RECEIVE（服务端把 1 认作第 1 阶段）。
  count 改回阶段号（idx+1）；③ TARGET_HAD_RECEIVE 不再打回
  RUNNING 死磕同一阶段：该阶段标 SUCCESS、下一轮看下一阶段，
  去重标记不清掉（1.5.36 同一阶段重复领 7 次、一轮 24 分钟）；
  STAGE_NOT_FINISH 仍按 1.5.13 打回重试；④ KNOWN 27 个 missionXId
  全部刷新为本次 HAR 值，CALLBACK_TOKEN 闲鱼换新鲜 token；⑤ 本
  次 HAR 没抓到猜价格提交（0 条 GuessPrice，玩的是闲鱼视频/页面
  点击），也没抓到滴滴回调——滴滴仍只有 rookieCallbackTransfer
  GET（302 到 OAuth），猜价提交仍待一次完整猜价抓包。
  1.5.32-allids（2026-11-18 09:36）：用户令把抓到的 missionIds 全部加进来。
  08:11 H5 全量 36 项里此前未进 KNOWN 的 8 项全部补进（45226007 看淘宝视频/
  47854001 快手/45226015 闲鱼领现金/47908001 一淘/47846001 百度极速版/
  44920003 飞猪领现金 六个 THIRD inter_change + 45226021 闪购会员日/
  47754001 开启签到订阅 两个 PAGEVIEW），另补支付宝清单里的 49020001
  今日累计任务奖励（THIRD，scenceCode third，非 inter_change，3 阶段）。
  KNOWN 现 46 项。新增 THIRD 的 interchangeBizType（ET_TAB_2/KUAISHOU/
  ET_SECONDFISH/ETAO/BAIDU_APP/FLIGGY）已进 CALLBACK_SOURCE；45226015 的
  08:11 新鲜 token 已在 CALLBACK_TOKEN（1.5.31 已同步）。上报与领奖走原有
  流程：THIRD 先 new.callback 自动上报，阶段翻 FINISH 后 receiveprize
  自动领取；PAGEVIEW 走 pageview；累计任务直接领奖。
  1.5.31-freshtoken（2026-11-18 08:11）：用户 08:02 截图（8164，
  63627）+ 08:11 H5 全量 querytask（36 项，ttid H5@Web）：新任务
  48674001 浏览点击5个不同商品（THIRD，inter_change，新渠道码
  XMHD，+80）已加进 KNOWN；36 项 missionXId 同步到 08:11 新值；
  CALLBACK_TOKEN 同步到 08:11 H5 新鲜 token（14 项）。1.5.30 的
  跳过已撤掉：1.5.29 用的是 00:18/00:43 的旧 token（7 小时+），
  跨会话失效的结论可能只是 token 过期；08:11 的 token 是新鲜
  的（几分钟），先发回调试一次——若新鲜 token 能领，说明 token
  是有时效而非会话锁定；若仍不翻，才是会话锁定，那时再补 H5/
  支付宝 Cookie。另：48324002 页面点击已到 1/3（第 1 阶段 SUCCESS，
  手机上也显示 1/3），说明当前会话 token 的回调确实能推进阶段。
  9 条 08:04–08:08 的 HAR 与任务无关（local.tktab/common.composite/
  play.delivery/ab.shunt/trigger.opportunity），无 callback。
  1.5.30-staletoken（2026-11-18 02:05）：1.5.29 实跑（01:55，
  双账号）领到 5 份：0261 +305（搜一搜 +55、逛商家积分 +55、
  闪购品牌日 +60、猜价格 +80——9 个里第一个被回调领到的），
  8164 +55（搜一搜）。key1/key2/key3 是对的：当前会话 token
  的任务，回调后阶段会翻、能领到。但动动有金/一键领取优惠/
  闲鱼看视频三轮全撞 STAGE_NOT_FINISH：它们的 token 全是 HAR
  硬编码兜底（旧支付宝会话），服务端只接受回调格式、不把任务
  记完成——这就是“回调接受”却永远不领的根因，日志不是假的，
  是 token 跨会话失效。本版：token 若来自硬编码兜底，不发回调
  、直接跳过并明说（需当前会话 Cookie 或手机手动做），不再每
  轮刷“回调接受”。修这 3 个的办法：把支付宝会话 Cookie 另加
  为青龙新账号（48228002/48596001 在支付宝清单里），动动有金
  48602001 是 H5 专属、需 Safari H5 Cookie 或手机手动做。

环境变量（账号）
----------------
  ELM_COOKIE        本人完整登录Cookie，多账号换行分隔；缺少_m_h5_tk时会尝试申领
  ELEME_COOKIE      旧版变量兼容入口；建议迁移到ELM_COOKIE、ELM_COOKIE2...
  ELM_COOKIE2/3...20 第二、第三等账号（可选；也可ELM_COOKIE内每行一个）
  LUCKYSTAR         兼容入口，等价于 ELM_COOKIE
  XX_ACCOUNTS / LS_ACCOUNTS / LUCKYSTAR_ACCOUNTS  同义兼容
  每行写法（两种都认）：
      备注#cookie        ← 推荐（与原 JS 插件一致，形如 小号A#cookie2=xxx; _m_h5_tk=yyy; ...）
      cookie             ← 裸 Cookie，脚本自动从 userId / munb 里取备注

环境变量（参数）
----------------
  ELM_LAT / ELM_LNG            经纬度，默认上海 31.2304 / 121.4737
  ELM_APPKEY                   mtop appKey，默认 12574478（web 端）
  ELM_GATEWAY                  mtop 网关，默认 https://waimai-guide.ele.me/h5/
  ELM_APP_VERSION              aladdin prodVersion，默认 12.8.8
  ELM_TASK_H5=0                3112 任务接口改回旧版 Android 身份（默认用官方 H5 身份）
  ELM_CLAIM_WAIT               领奖前等待秒数，默认 5；0 为不等待（服务端翻阶段慢时可调大）
  ELM_CLAIM_RETRIES            同一阶段领奖报 STAGE_NOT_FINISH 最多试几次，默认 2；达到后本轮不再领
  ELM_MAX_WORKERS              多账号并行路数：0/空=自动（min(账号数,8)），1=串行，2-16=指定路数
  ELM_TASK_TIMES               标准任务最多查询/补跑轮数，默认7；进度未确认即停
  ELM_SKIP_SIGNIN=1            跳过签到
  ELM_SKIP_TASKS=1             跳过浏览/点击任务；抽奖须另设跳过参数
  ELM_SKIP_MIANDAN=1           只跑 3112，不跑天天免单 2602
  ELM_MIANDAN_ONLY=1           只跑天天免单 2602，不跑 3112 任务（签到/抽奖照常）
  ELM_SKIP_DRAW=1              跳过夺宝/抽奖
  ELM_EXCHANGE=1               允许自动兑换商城（**会消耗幸运星**，默认只报告不兑）
  ELM_EXCHANGE_ID              指定目标商品 exchangeId（推荐，精确匹配）
  ELM_EXCHANGE_NAME            指定目标商品完整名称（精确匹配，可与 ID 同时校验）
  ELM_EXCHANGE_MAX             正整数允许兑换；定向版每个账号每轮最多兑换目标1件
  ELM_MALL_ONLY=1              只列商城商品名/ID/所需星/状态，绝不兑换
  ELM_MALL_EXCHANGE_ONLY=1     只运行商城与指定商品兑换，不签到或抽奖
  ELM_SKIP_ORDERFREE=1         跳过下单免单抽奖
  ELM_EXTERNAL_PAGEVIEW=0      跳过外部任务的通用PAGEVIEW/THIRD上报；默认1
  ELM_QUIZ_ACTID               趣味话题答题的 actId（在答题页 URL 上，不传则不跑）
  ELM_DRAW_TIMES               每个夺宝组件最多抽几次，默认 1
  ELM_NOTIFY=1                 开启推送（默认关闭；须先修复面板通知配置）
  ELM_CHECK_ACCOUNTS=1         只离线检查多账号格式，不发请求
  ELM_SELFTEST=1               只查询首页验证登录，不签到/抽奖/兑换
  ELM_TASKS_ONLY=1             只列出接口实际返回的任务和跳过原因，不上报/领奖
  ELM_TASKS_EXPORT=1           只读导出完整任务结构（脱敏JSON），自动进入任务清单模式
  ELM_TASKS_OUTPUT_DIR         导出目录，默认脚本旁elm_task_reports
  ELM_DEBUG=1                  调试日志（不会输出原始Cookie）；完整任务请用只读导出
  ELM_PROXY                    代理，逗号/换行分隔。三种格式：
                                ① IP|端口|用户|密码|到期日  如 223.15.244.28|9347|4363991|ab7ac092c|2026-10-23
                                  （到期自动停用并警告，过期条目不会用）
                                ② http://user:pass@ip:port
                                ③ ip:port
                                多条时按账号稳定分配（同账号永远同出口 IP）
  ELM_DEVICE_OS                指纹机型：android（默认，与自建面板一致）| ios
  ELM_RESET_DEVICE=1           重置本账号设备指纹（等价面板「重置设备」）
  ELM_DEVICE_API               指纹服务地址（fingerd），如 http://192.168.3.174:18911
                             填了就走服务领指纹（同账号稳定、跨账号/跨项目绝不重复）；
                             留空则本地按面板同款规则生成
  ELM_DEVICE_API_KEY           指纹服务的 X-API-Key（服务端设了 FP_KEY 时必填）
  ELM_DEVICE_SCENE             场景名，默认 eleme（一个服务可给多项目分池统计）
  LUCKYSTAR_DRYRUN=1           只打印将要发送的请求与签名，不实际发出（排查用）

通知
----
  默认仅输出日志；设置ELM_NOTIFY=1后优先用青龙notify，再尝试内置推送渠道

登录与执行
----------
  Cookie须来自本人有效登录；_m_h5_tk只是签名token，不代表已登录。
  遇用户验证时停止请求，请在官方活动页面完成验证后更新完整Cookie。
  每日状态逐账号保存；默认随机等待0~900秒，调试可设ELM_OFFSET_MAX=0。

定时任务
--------
  cron: 30 8 * * *
  task 幸运星.py

用法
----
  python 幸运星.py                 # 正常运行（多账号并行，默认最多 8 路）
  python 幸运星.py --dryrun        # 打印首个账号的请求构造结果，不发请求
  python elmxyx_fixed.py --check-accounts  # 只检查格式，不输出Cookie
  python elmxyx_fixed.py --selftest        # 只查询首页检查登录
  python elmxyx_fixed.py --mall-list       # 只列商城商品名和ID
  python elmxyx_fixed.py --tasks-list      # 只读任务清单与支持情况
  python elmxyx_fixed.py --tasks-export    # 只读导出任务结构、奖励和进度
  python 幸运星.py --dump-account  # 打印解析到的账号（脱敏）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import random
import re
import string
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl, unquote
import tempfile

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 1.5.43：新增打毛线任务 49274001（2026-11-22）：用户两次抓包（02:41 QX 包与
# 02:58 SwissTransfer 包，同一局）都证实：从幸运星点进闲鱼小游戏时，落地页
# fy27-fish-game 的 URL 里带 missioncollectid=3112&missionid=49274001
# &bizscene=interact_center，游戏 appKey=35432404（dmx.xyx.mmhygame.com，
# 与 49016001 同一套闲鱼小游戏回调链路）。注意：两包都没有 querytask 响应体
# （QX 包根本没 querytask；HTTPS Capture 包的 querytask 响应全空/403），所以
# 任务标题、missionXId、interchangeBizType、token 都没在包里——missionDefId
# 是 HAR 实测值；标题“去闲鱼玩打毛线”取用户截图；interchangeBizType/source/
# plat/channelId 全按 49016001 同平台沿用（SECONDFISH_GAME/
# blessings.yundps.com/tagType=fish/channelId=272/alipay_welfare），taskSign
# 沿用 other-272-53081152-<token>-<7 位随机数>构造；missionXId 留空、token
# 运行时按 missionIds 定向查询现取。49274001 与 49016001 是两个任务，别混用。
# 成败看实跑日志：定向查询能否取到 token、IdleFish/callback 是否 code=0、
# 阶段是否翻 FINISH。
# 1.5.47：修 1.5.46 的 3112 查询竞态（2026-11-22 10:04 那轮实测）：
# query_tasks 的 H5→Android 退回重试直接改 os.environ 全局变量
# ELM_TASK_H5，多账号并行（默认 8 路）时别的线程会读到被改掉的值：
# ① 重试判断在第一次 mtop 返回后才重读环境，别的线程的重试正把标志
# 置 0 时本号直接跳过重试、报“查询任务失败”；② _build 里 platform
# 与 launchChannels、mtop 里 platform 是三次分开读环境，读到的值不
# 一致就拼出混搭身份，服务端报 FAIL_SYS_BIZPARAM_MISSED。那轮 7 个号
# 中招（9318/6591/4305 首查失败只跑 2602；8396/8164/8311/0261 定向
# 查询失败；7636 重查失败）。改动：身份改成本实例粘性标志 self._task_h5
# （初值取 ELM_TASK_H5，默认 H5；各号实例只被自己的线程用），退回时只
# 改本实例标志、不碰全局环境；一旦证实是 Android 会话，本轮余下查询
# 直接走 Android，不再每查一次都先撞一次 H5（那轮白撞 162 次）。ELM_TASK_H5=0
# 仍可全局关 H5。其余逻辑与 1.5.46 完全一致（兑换、P2P 助力、pop2me 两进式均未动）。
# 1.5.48：滴滴/飞猪加真网点击（2026-11-22，用户令：把两条支付宝小程序
# 分享链接写进脚本、模拟真网真人点击领取）。链接 1=滴滴 48044001
# （ur.alipay.com/_6IobETdFtOzL2uXKhxv6bZ）、链接 2=飞猪 48282001
# （ur.alipay.com/_61HzKBQBv0rODHfm8cnU2Z），按用户发来的顺序；短链
# 302 到 render.alipay.com 落地页（200），再由 JS 唤起 alipays://
# 小程序。流程：THIRD 任务原有尝试（滴滴 rookieCallbackTransfer、
# 飞猪 polymorphism）之后、new.callback 之前，真 GET 一次短链并跟
# 跳转（直连失败走 ELM_PARTNER_PROXY 再试），落地 200 或跳转到
# alipays:// 即算点开，停 3 秒模拟人在小程序里，再走 new.callback，
# 阶段翻不翻仍看重新查询。先说清楚：脚本无支付宝会话，点击是匿名
# 打开，服务端不认分享参数就不算数——这版是实验，18:59 实跑见分晓；
# 若阶段不翻，说明匿名点不算，须另想办法。其余逻辑与 1.5.47 完全一致。
# 1.5.45：新增 2 项（2026-11-22，用户令：QX 明文任务列表里不需要 deviceId 的全写进脚本）：
# ① 49180001 拍立淘逛感兴趣的宝贝（PAGEVIEW，pageSpm a2ogi.bx1500380、pageScene
# COMMON、finalStageCount 1，+55 星）：与脚本里十几个同 pageSpm 的 PAGEVIEW 任务
# （45986001、47200001、45358002 等）同一机制，走现有 pageview 上报；
# ② 49192002 去领取3次优惠得奖励（THIRD，scenceCode inter_change、finalStageCount 1，
# +80 星，RUNNING）：此前搁置的任务，04:37 明文 querytask（39 条）里重新出现；
# 走 THIRD 的 trigger/new.callback 路子实试一次（按用户“不试试怎么知道”）。
# 两项均非 pop2me 类型（无 GetDeviceId/DhTask/BrowseTask），都不需要 deviceId；
# missionXId 按 HAR 原值照抄（脚本已有条目的做法）。KNOWN_H5_TASKS 48 项 → 50 项。
# 09:59 实跑验证：49180001 的 pageview 是否翻阶段、49192002 是否被 417 拦。
# 1.5.44：邀请好友助力 44914004 互助框架（2026-11-22，用户令加）：
# 用户发来 querytask 3112 全量 JSON（28 项）并令把 44914004 写进去、
# 用自己的号互相助力、默认先助满顺序第一个号再往下。先说审计结论：
# 这 28 项全已在 KNOWN_H5_TASKS（48 项）里，actionType/finalStageCount/
# 阶段数/interchangeBizType/scenceCode 逐条比对全一致——没有新任务可加，
# 1.5.44 不新增任何任务条目。44914004 本体也早在表里（P2P，10 阶段，
# 每阶段 +80 星），但 task_plan 一直判 skip（“需要真实邀请/好友助力”）。
# 本版加的是互助框架：① run() 里实时采集各号 44914004 的助力信息
# （shareId/p2pId/actId/missionXId/已助几人，全部实时取、不硬编码）；
# ② main() 跑完各号后按策略排计划：目标=ELM_COOKIE 顺序第一个未满的号，
# 助满（10/10）再按顺序助下一个；每号每天最多助一次、同一对每天最多助
# 一次（保守规则，JSON 里没写限制，这是假设）；③ 助力历史记进
# lucky_state.json 的 p2p_assist_days（按天），成功助力才写。
# **待实现点（重要）**：JSON 里没有助力接口——只有 shareId/p2pId/actId、
# inviteLink（邀请记录页地址，无助力参数）与分享配置（Alipay bizType
# ztokenV0_EXQzTUMO 等，只描述怎么展示分享），找不到“帮别人助力”的
# API。所以 attempt_p2p_assist 目前是空实现：只记日志、不发任何请求、
# 不编造端点、不把计划计为已助力。以后抓到真实助力接口（例如某号打开
# 另一号分享链接时的请求）再把 endpoint 填进去，计划与历史逻辑不用改。
# ELM_P2P_ASSIST=0 可关掉互助排计划（默认 1 开）。
# 1.5.42：余额行保底（2026-11-21）：1.5.40 首跑 4305 的日志缺最后的余额行——
# 首页聚合没返回 star 字段时 star_start/star_end 都是 None，finish() 静默
# 不打印余额。改为：finish() 里 star_end 为空时再补拉一次首页，仍拿不到
# 就打印“💰 幸运星余额：未知（首页未返回余额）”，保证每个账号都有余额行；
# result["余额"] 同步保底为“未知”，不再被 pop 掉。其余逻辑与 1.5.41 完全一致
# （含 48432001 打卡、49270001/49238001 新任务 ID 均已在 KNOWN_H5_TASKS 里）。
# 1.5.41：pop2me 打卡（48432001）用 elmxyx_pop2me.py 的签名
# （X-Cy-Sign，key=deviceId）与 account_identity_cache.py 的身份缓存
# （按账号+小程序缓存 deviceId，6666 时刷新一次）。这两个文件须与本文件
# 同目录（青龙脚本目录）。加载失败时打卡走旧路（new.callback），
# 其余逻辑不受影响——1.5.41 在无 pop2me 模块时与 1.5.40 等价。
try:
    _POP2ME_DIR = Path(__file__).resolve().parent
    if str(_POP2ME_DIR) not in sys.path:
        sys.path.insert(0, str(_POP2ME_DIR))
    import elmxyx_pop2me as _pop2me
    from account_identity_cache import (AuthorizationRequired, IdentityCache,
                                        IdentityRejected,
                                        make_get_device_id_exchange,
                                        observed_identity_invalid)
    _POP2ME_OK = True
except Exception:
    _pop2me = None  # type: ignore
    _POP2ME_OK = False

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_VERSION = "1.5.53"      # 1.5.53：Cookie 失效账号跳过（2026-11-23 用户要求：失效的号本轮不用跑）。登录失效（NOT_LOGIN/SESSION_EXPIRED/TOKEN_ILLEGAL 等）确认后记进 lucky_state.json 的 dead_cookies（按完整 Cookie 哈希），下轮零请求直接跳过；用户重新登录更新 Cookie 后哈希变了自动恢复。USER_VALIDATE 不记死号（App 内验证通过后同一 Cookie 还能用）；RISK_USER 不是死号。其余与 1.5.52 完全一致。
#   BrowseTaskStart → 等 Seconds → BrowseTaskComplete；签名与身份缓存复用
#   elmxyx_pop2me.py / account_identity_cache.py）。其余逻辑与 1.5.40 完全一致。
#   1.5.40：领奖防重复（已领阶段本轮只领一次；
#   STAGE_NOT_FINISH 同一阶段最多试 ELM_CLAIM_RETRIES 次，默认 2 次，之后本轮不再
#   领奖；多账号并行（ELM_MAX_WORKERS，默认最多 8 路），8 个号约 30-40 分钟跑完。
#   1.5.38 实跑教训：13:29 那轮 375 次领奖请求全失败（213 次 STAGE_NOT_FINISH、
#   162 次 TARGET_HAD_RECEIVE），43 次成功只领到 2849 星；串行 8 个号要 3 小时+。
START_TS = time.time()

# 1.5.40 多账号并行用的两把锁：设备指纹文件是多账号共享的 JSON，alloc_device
# 是读-改-写，并行时必须串行化（否则丢更新、甚至两个号分到同一指纹）；日志
# print 加锁是防止多线程的行互相插进同一行。两个锁都只包很短的临界区，不影响速度。
_DEVICE_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()


# =========================================================================== #
#  常量（逆向自 engage-hub v1.3.5）
# =========================================================================== #

API = {
    "homepage":     "mtop.alsc.interact.et.interact.center.homepage",
    "querytask":    "mtop.ele.biz.growth.task.core.querytask",
    "receivetask":  "mtop.ele.biz.growth.task.core.receivetask",
    "receiveprize": "mtop.ele.biz.growth.task.core.receiveprize",
    "pageview":     "mtop.ele.biz.growth.task.event.pageview",
    "trigger":      "mtop.ele.biz.growth.task.event.trigger",
    "callback":     "mtop.ele.biz.growth.task.event.new.callback",
    # 签到并领奖 / 独立签到
    "signin":       "mtop.alsc.interact.playapp.signin.component.signinandreceive",
    "recordsignin": "mtop.alsc.interact.playapp.signin.component.recordsignin",
    # 夺宝/抽奖（1.4.2 前端包逆向）
    "lottery_draw": "mtop.alsc.interact.playapp.reward.lottery.draw",
    # 幸运星资产/明细、兑换商城
    "property_detail": "mtop.koubei.interaction.center.common.querypropertydetail",
    "mall_list":      "mtop.alsc.interact.playapp.reward.right.mallrecall",
    "mall_exchange":  "mtop.alsc.interact.playapp.reward.right.exchange",
    # 下单免单抽奖
    "orderfree_draw": "mtop.alsc.interact.et.orderfree.draw",
    # 趣味话题答题
    "quiz_query":  "mtop.alsc.interact.playapp.quiz.query",
    "quiz_submit": "mtop.alsc.interact.playapp.quiz.submit",
    "quiz_award":  "mtop.alsc.interact.playapp.quiz.award",
}

#: 风控 asac token（逆向自 engage-hub 1.4.2 前端包常量表）
ASAC = {
    "SIGNIN":          "alsc3Lhy681SA5TT4iHgL3",
    "PRIZE":           "alscadOjfleDPawx9zVoT0",
    "PAGEVIEW":        "alscFS8BNTO6jivYS7XOAM",
    "TRIGGER":         "2A21607NIIT1ND5C4YXJ6C",
    "LOTTERY_DRAW":    "alscdZLQXT9DEAlXvQQOLO",   # 夺宝/抽奖
    "MIANDAN_DRAW":    "alsc6AD8Rni21gsqJ5rceg",   # 免单抽奖
    "ORDER_FREE_DRAW": "alscrX2P4rmvl0NhmQgr9x",   # 下单免单
    "MALL_EXCHANGE":   "alsc5KvbdX5mHl3sdv4guV",   # 幸运星兑换商城
    "SNS_TRIGGER":     "alsc8tok9QFxdUiCPfmpbK",   # 分享触发
    "QUIZ_AWARD":      "alsc0Ue9L6Q0YtTZjC3Ovx",   # 答题领奖
}

BIZ_SCENE = "interact_center"
ACCOUNT_PLAN = "HAVANA_COMMON"
DEFAULT_PAGESPM = "a2ogi.bx1500380"  # engage-hub 本页；全部 50 个已知 PAGEVIEW 任务都用它，新任务缺 pageSpm 时兜底。
# 天天免单（et-miandan-site）是另一套任务集合，不在幸运星首页聚合里。
# 首页：mtop.alsc.interact.et.md.homepage v1.0，bizScene=ETMD，version=1.10.27，
#   任务集合在 mainPage.data.popupShareWinV2.missionCollectionId（实测 2602）。
# 任务查询：同一 querytask 接口，bizScene=ETMD_CHANNEL，其余字段同幸运星版。
# 淘宝闪购 / 支付宝闪购只是同一天天免单活动的两个入口，后端是同一套 2602。
ETMD_BIZ_SCENE = "ETMD"
ETMD_TASK_BIZ_SCENE = "ETMD_CHANNEL"
ETMD_HOMEPAGE_API = "mtop.alsc.interact.et.md.homepage"
ETMD_HOMEPAGE_VERSION = "1.10.27"
ETMD_ASAC = "alsc6AD8Rni21gsqJ5rceg"
PAGE_VERSION = "1.4.28"         # 2026-11-17核对的官方前端版本
# 2026-11-17 官方 H5 实测：engage-hub 1.4.27 的 querytask 请求身份。
H5_TASK_PLATFORM = "ios"
H5_TASK_TTID = "H5@Web_iphone_1.4.28"

# 2026-11-17 20:30 Stream HAR（Safari H5 会话 querytask 3112，35 项）里未做的 18 项。
# 用途：querytask 只回 23 项（Android 会话）时，把这批 H5 才看得到的任务也并进去尝试，
# 不再只等 H5 Cookie。注意：服务端仍可能按身份/付费投放拦 trigger（417），成败看实跑日志。
# pageSpm 统一是 engage-hub 本页 a2ogi.bx1500380；missionXId 是每任务的长编码（HAR 原值）。
# 1.5.17：new.callback 的 source 参数（互换渠道码）。KNOWN 表里
# 已给 8 个 417 任务写了 interchangeBizType；实时列表任务从其 ext
# 里读；这里是最后的兜底（生活缴费 48516001 不在 KNOWN 表里）。
CALLBACK_SOURCE = {
    "49016001": "SECONDFISH_GAME",
    "49274001": "SECONDFISH_GAME",  # 1.5.43：打毛线（同 49016001 平台，沿用，待实跑确认）
    "48432001": "SHLQ",
    "48324002": "SHLQ",
    "48282001": "CDQC",
    "48152003": "SHLQ",
    "48044001": "CDQC",
    "48602001": "CDQC",
    "48596001": "CDQC",
    "48516001": "CDQC",
    "48228002": "SHLQ",
    "49238001": "SZYC",
    "48674001": "XMHD",
    "45226007": "ET_TAB_2",
    "47854001": "KUAISHOU",
    "45226015": "ET_SECONDFISH",
    "47908001": "ETAO",
    "47846001": "BAIDU_APP",
    "44920003": "FLIGGY",
}

# 1.5.24：回调 plat 是第三方平台标识（服务端配置
# emod-afun-task-report-config 的规则按落地页 URL 的 plat= 匹配），
# 不是容器名 ios/eleme/alipay/android。闲鱼任务走 secondfish，其余
# 支付宝系任务先走 alipay_task；500 时再按 CALLBACK_PLAT_RETRY 试。
CALLBACK_PLAT = {
    # 1.5.27：1.5.26 实跑证实这 9 个任务（含闲鱼两个）回调全走
    # alipay_welfare 才通，alipay_task/secondfish 各报 400、didi
    # 报 500。alipay_welfare 打头，不再先试错的两个浪费 400。
    "49016001": "alipay_welfare",  # 闲鱼玩1关
    "49274001": "alipay_welfare",  # 1.5.43：打毛线（同 49016001 平台，沿用，待实跑确认）
    "48596001": "alipay_welfare",  # 闲鱼看视频
    "48516001": "alipay_welfare",  # 生活缴费
    "48432001": "alipay_welfare",  # 天天打卡
    "48324002": "alipay_welfare",  # 页面点击
    "48282001": "alipay_welfare",  # 飞猪
    "48152003": "alipay_welfare",  # 猜价格
    "48044001": "alipay_welfare",  # 滴滴
    "48602001": "alipay_welfare",  # 动动有金
    "48228002": "alipay_welfare",  # 一键领取优惠
    "49238001": "alipay_welfare",  # 点击领取2个红包
}
CALLBACK_PLAT_RETRY = ["alipay_task", "secondfish", "alipay_welfare", "baidu", "taotian_guang", "taote", "zijie", "wlj", "ast", "huahuaka"]

# 1.5.20：回调 token 硬编码兜底（HAR 里抓到的）。token 会话相关，
# 硬编码未必生效；实时 ext / 定向查询优先，这里只是最后兜底。
CALLBACK_TOKEN = {
    "44914006": "ZUYzTTQ3RnJyT1llcmtSMHljZXVUWm5PcXZkazMrRUpQZE1yVnNzU2wzaEFKditGdnVBL0NGb2V1eVJQRzNrV2drdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "45108002": "cXFDU1didUNaVUdGT1RLWTVRcmRKWm5PcXZkazMrRUpQZE1yVnNzU2wzaXJUcTJyOXA0NCt1WG5YWG1QbWd2ZWdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "45226015": "Q2hidGUwdzRhbjFyWURkTVpMTzZVWm5PcXZkazMrRUpQZE1yVnNzU2wzaTludVBnTTJuekJ4TUdSVHlncmFJQWdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "46254001": "N1IzdXlzZ2ZjTTRVSzgzSnV4RnNnNW5PcXZkazMrRUpQZE1yVnNzU2wzang1ZWNmdVpxeUlxVFdKRzU0NHFVemdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48044001": "OG05T3FZUFBCNkI3WWtTbEk1OGlkWm5PcXZkazMrRUpQZE1yVnNzU2wzZ2tZR1J2cU5YK3FLZWtiNnBPVXVzWGdrdGpMZHNtVkVBeWZ4NzgrZkVjeHJ0Rms3U3hsbzdFRXFrRzYyVXFUK2ROYUV5Q0Zxendwd3MvK0k3enJRWGI",
    "48152003": "TFBWeE9rekhBUjJnd0kyNGRRSW1PSm5PcXZkazMrRUpQZE1yVnNzU2wzaDJRak1MSm1FdmdJMElhVHJUNGFsT2drdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48228002": "TlJublNwMHBEUkJVamhGYmNkZnQrcG5PcXZkazMrRUpQZE1yVnNzU2wzaUdXMC90Wkd4VC9YOUNSREFXQzhRK2drdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ1o5d0xEYnB6ck41dVZRUjZ4RnBXQ3Y",
    "48282001": "N05iU2xDdDlWc2FWMC9mTTB2RDFJWm5PcXZkazMrRUpQZE1yVnNzU2wzaTlBQjNQWXEvSmFkY0JzQXZVSGJNM2drdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48324002": "UHEwUnA3bndhV2VZUkdhTGhZendLWm5PcXZkazMrRUpQZE1yVnNzU2wzaHRNelpYRkdmckZOMlFWQmhnZU9TTmdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48432001": "YXZhN2lvekd4QVBiMnpmUVZISUlrWm5PcXZkazMrRUpQZE1yVnNzU2wzaG1Ia00rYTAwUEwrZkVRQjRTVTZzS2drdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48516001": "R1RSemtaaEp0K3V4ZFNoUzdJRk01cG5PcXZkazMrRUpQZE1yVnNzU2wzaDVCTEFYMHRUTmlHazhXandGcW5qMGdrdGpMZHNtVkVBeWZ4NzgrZkVjeGdlMnpWZ3VXUnBKY1VIRWpEWGJlOHhxL0tESW5kZ2lpQllKUk9vUmlwT2M",
    "48596001": "WTkxSGphcFhleElod2lBZUdEZzdsSm5PcXZkazMrRUpQZE1yVnNzU2wzaWdROFlzUXF4MGIzaUllNi9TNnJqc2drdGpMZHNtVkVBeWZ4NzgrZkVjeHJ0Rms3U3hsbzdFRXFrRzYyVXFUK2ROYUV5Q0Zxendwd3MvK0k3enJRWGI",
    "48602001": "SDN2dHg5TWVRRVZWUzZuVnVpOSsxWm5PcXZkazMrRUpQZE1yVnNzU2wzaHZxMEt3TkVhdFBXOVVjc2dvZVhjOGdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "48674001": "WmlURzY1ODE4cFdrZkxEdDFvdTdmcG5PcXZkazMrRUpQZE1yVnNzU2wzaHFsa2ZJTjhMR1RxZkxKc3JaZlBUZWdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
    "49016001": "N0VmNkkvZlNHS1Uyb2dlMmpyMEZNNW5PcXZkazMrRUpQZE1yVnNzU2wzaVNPWE9HdGFzakdmeGpRc3YyaTIzRWdrdGpMZHNtVkVBeWZ4NzgrZkVjeHRpU2tsYURVKzhuSHA3VGdvRlZnZ2FJM2F1ZjllRnIxL3pzK2pVanNtT0s",
}

# 1.5.34：合作方回调记录（2026-11-19 17:16 HAR 实抓）。
# App 完成 THIRD 任务不调 new.callback，而是打开任务跳转链接里的
# rookieCallbackTransfer（带 tagType/channelId/token），经支付宝
# OAuth 后由合作方回传完成。token 在实时跳转链接里，运行时取；
# 这里只记录不变的 host/tagType/channelId，供核对与兜底。
PARTNER_CALLBACK = {
    "48044001": {"host": "general.yundps.com", "tagType": "didi", "channelId": "255"},  # 去【一键收下】滴滴打车红包
    "48596001": {"host": "blessings.yundps.com", "tagType": "fish", "channelId": "272"},  # 去闲鱼看视频领奖励
    "49016001": {"host": "blessings.yundps.com", "tagType": "fish", "channelId": "272"},  # 1.5.38b：去闲鱼玩1关消了还想消（用户 2026-11-20 指出有数据；19:06 HAR 两次回调都是看视频的 token，玩一关的回调从没发过——它不在此表里。先按同一活动号/通道试，code 看实跑）
    "49274001": {"host": "blessings.yundps.com", "tagType": "fish", "channelId": "272"},  # 1.5.43：去闲鱼玩打毛线（2026-11-22 HAR 实测 missionid；host/tagType/channelId 按 49016001 同平台沿用，待实跑确认）
}

# 1.5.44：邀请好友助力（44914004）互助框架。见文件头：JSON 里有
# shareId/p2pId/actId，没有助力接口；计划与历史逻辑已实现，真正发请
# 求的 attempt_p2p_assist 是待实现点（不发请求、不编造端点）。
P2P_INVITE_DEF_ID = "44914004"
P2P_INVITE_STAGES = 10  # finalStageCount=10，每阶段 +80 星，助满 +800

# 1.5.37：闲鱼 IdleFish 回调（2026-11-19 19:06 HAR 实抓）。
# taskSign = other-channelId-8 位活动号-跳转链接 token-7 位随机数。
# 8 位活动号在 HAR 两次回调里一致，也见于小游戏的上报请求，按活动常量记。
FISH_CALLBACK_URL = "https://mobile.yundps.com/IdleFish/callback"
FISH_SIGN_MID = "53081152"

# 1.5.34：SHLQ（生活圈）任务走 pop2me 小程序页（跳转链接
# m.pop2me.com/elemeweb/<page>?ch=3&source=SHLQ&token=…）：
# page3 猜价格 / page4 天天打卡 / page5/5 页面点击 / page6/2 一键领取优惠。
POP2ME_PAGES = {
    "48152003": "page3",    # 点击商品猜价格
    "48432001": "page4",    # 天天打卡赚现金
    "48324002": "page5/5",  # 页面点击领奖励
    "48228002": "page6/2",  # 去一键领取优惠得奖励
}

# 1.5.39：00:31 原始 HAR 实抓的完成信号（57MB，
# shared-357B2FFA_quantumult-x-2026-11-20-003121.har，
# sha256 b96c8da9…，条目 1226/1523 与 Browse/Dh 全是 HTTP 200）。
# 参数原样记在这里。结论先说清楚——这四条是真完成信号，但都走
# 支付宝小程序会话，脚本侧（青龙，无支付宝会话）无法直接复用：
# pop2me 三条要小程序会话 token（LEMEWEBEo:… 形，GetDeviceId+
# authCode 现取，脚本没有）与 X-Cy-Sign（SHA256，密钥未知；20 余
# 种无密钥组合——含带 token 的——全不匹配）；飞猪 polymorphism
# 要 MD5 sign（密钥未知；20 余种无密钥组合全不匹配），无有效
# sign 服务端只回 -2001 签名错误（探针 228 已证实）。滴滴的
# partner 回调只 302 到支付宝 OAuth，须用户亲自授权，脚本走不通。
# 所以主流程不自动调这些（调了也只会会话错/-2001，白白多请求）；
# 下面的常量与方法供拿到会话/密钥时直接启用，飞猪那条已接进 417
# 流程作真实尝试（用实时 callbackToken，sign 缺失时服务端回
# -2001，日志会明说，不影响其他任务）。
POP2ME_GATEWAY = "https://gw.pop2me.com"
POP2ME_BROWSE = "/gateway/r/m/earn-gold-coins_1.0_EarnGoldCoinsApi.BrowseTaskComplete"
POP2ME_DH_GOLD = "/gateway/r/m/gold-quotation_1.0_GoldQuotationApi.DhTaskComplete"
POP2ME_DH_RECIPE = "/gateway/r/m/recipe_1.0_RecipeApi.DhTaskComplete"
POP2ME_GUESS = "/gateway/r/m/earn-gold-coins_1.0_EarnGoldCoinsApi.GuessPriceCallbackByMe"
# 1.5.41：48432001 天天打卡（pop2me，2026-11-21 实抓链路）。
# 小程序 2021004180625470（与猜价格同一小程序）→ pages/index/index，
# channel=eleme、bt="1"。BrowseTaskStart 回 ONGOING + Seconds=15，
# 等 15 秒后 BrowseTaskComplete 回 FINISHED。body 只有 token/channel/bt 三键。
POP2ME_CHECKIN_APP_ID = "2021004180625470"
POP2ME_CHECKIN_ROUTE = "earn-gold-coins_1.0_EarnGoldCoinsApi"
POP2ME_CHECKIN_PAGE = "pages/index/index"
POP2ME_CHECKIN_CHANNEL = "eleme"
POP2ME_CHECKIN_BT = "1"
# 1.5.46：deviceId pop2me 调用重试上限（用户令：运行一次脚本重试三次——
# 单次运行内、单个 deviceId 调用失败时就地重试最多 3 次，不是整轮重跑 3 遍；
# 只重试带 deviceId 的调用，CroInfo/GuessPriceInfo、DhTaskStart/DhTaskComplete、
# BrowseTaskStart/BrowseTaskComplete；其他任务/调用保持原有行为不动。
# 3 次都失败就跳过该任务、记一行日志、继续跑其他任务，绝不报错中断整轮）。
POP2ME_MAX_ATTEMPTS = 3
POP2ME_RETRY_WAIT = 1
# 1.5.46：两进式第二进的领奖重试（用户睡前补充：返回饿了么 App 后多点两次
# “领取”才提示完成——pop2me 做完后服务端阶段可能还没翻成 FINISH）。
# 每次领奖前先重新拉一次任务列表确认状态；第一次领奖失败就等一会儿再试，
# 最多 POP2ME_CLAIM_MAX_ATTEMPTS 次。只用于 POP2ME_CLAIM_RETRY_IDS 的任务。
POP2ME_CLAIM_MAX_ATTEMPTS = 3
POP2ME_CLAIM_WAIT = 3
POP2ME_CLAIM_RETRY_IDS = {"48152003", "48324002"}
# 猜价格 Item 模板（HAR 1226 条实抓；AdBizId/spaceCode 随任务变，
# 其余展示字段原样带上即可；顶层 AdBizId 是 32 位 hex 的 base64）。
GUESS_ITEM_TEMPLATE = {
    "adSupplyType": "Bidding",
    "adBizId": "6bef575cda2741eaae7343d4989e6a00",
    "taskTitle": "去答题猜一猜商品价格",
    "taskSubTitle": "猜对得奖励",
    "adMerchantLogo": "https://mdn.alipayobjects.com/huamei_ouecfj/afts/img/A*-iB_Sa_QnqwAAAAAQCAAAAgAesqsAQ/original",
    "adMerchantName": "",
    "taskRewardName": "",
    "taskRewardAmount": "2",
    "taskClickButtonDescription": "去完成",
    "taskStatus": "INIT",
    "taskCategory": "PageView",
    "adType": "XL",
    "spaceCode": "42_2026051324202138193",
}
# 飞猪 polymorphism（HAR 1523 条实抓）：GET + callbackToken，
# 头带 appid/sign/timestamp/nonce/adzone-id/v/os；sign 是 MD5，
# 密钥未知。adzone-id=283，v=0.5.9，os=aliapp。
POLYMORPHISM_URL = "https://quchuang.yundps.com/Interaction/polymorphismChannelCallback"
POLYMORPHISM_APPID = "2021005173621564"
POLYMORPHISM_ADZONE = "283"
POLYMORPHISM_V = "0.5.9"
# 1.5.48：滴滴/飞猪小程序分享链接真网点击（用户 2026-11-22 11:36 发来，
# 链接 1=滴滴、链接 2=飞猪，按发来顺序）。短链 302 到 render.alipay.com
# 落地页（200），再由 JS 唤起 alipays:// 小程序。脚本无支付宝会话，
# 点击是匿名打开——服务端不认分享参数就不算数，这版先当实验验证。
MINIAPP_CLICK_URLS = {
    "48044001": "https://ur.alipay.com/_6IobETdFtOzL2uXKhxv6bZ",  # 滴滴：去【一键收下】滴滴打车红包
    "48282001": "https://ur.alipay.com/_61HzKBQBv0rODHfm8cnU2Z",  # 飞猪：去逛逛飞猪领取红包
}
MINIAPP_CLICK_STAY_SECONDS = 3  # 点开后模拟人在小程序里停留的秒数，再回饿了么走 new.callback

KNOWN_H5_TASKS = [
    {"missionCollectionId": "3112", "missionDefId": "45986001", "showTitle": "去淘宝芭芭农场逛逛", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302608, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWHlQMG12d2lieEZkMGxLUnorSVZEa1p1a2pDSXVJSEwrbG9HUU9YaEF2VjhpUXZOMU0zRXVXUG5MdFF1cEZPSHc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914025", "showTitle": "去「支付宝芭芭农场」逛逛", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298523, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYTl2dEhrMnBGUEpJVW9UdDI2Q0ZXY3JHNlB6Ui9CZENkK0xLSm02eStVNmgrdEdnZ1RnQWpEK0NYckRhbm1UbWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47200001", "showTitle": "逛一逛领专属红包", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302676, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVVkvSWE0ZklNT2V6T3doTkVPUDBiNXhWWU16MmtGYzRMbUYwdVhoOUlaNjhZM092blhMQUNMcUpzV210MndNZnc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "45264001", "showTitle": "点一份外卖，实付满10元", "actionConfig": {"actionType": "ORDER", "actionValue": {"finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302314, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLV2ExL3NrQ3U5aTE2VGFsZUdnS0VMYnBZYmswRlE5SnpBc1VpT25XdW51bjNnNEd3eEJydEgxbVkyOExZVVdYVkE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49016001", "interchangeBizType": "SECONDFISH_GAME", "showTitle": "去闲鱼玩1关消了还想消", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303453, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYm9qancxQ25hbGpST0lvS1RBZmRUTGZXYVhQTlRXTlZZbE9maTVTc3A2aHpUS3lRL29semhNdGVDZWVNcm9vV1E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49274001", "interchangeBizType": "SECONDFISH_GAME", "showTitle": "去闲鱼玩打毛线", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": ""},
    {"missionCollectionId": "3112", "missionDefId": "48432001", "interchangeBizType": "SHLQ", "showTitle": "天天打卡赚现金", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300211, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVlRPN1cvZU0wR3l5RnVwanBDVmd4SWt2ZXNpOUlGalRzTXdpb25JNitDVGtwaS9SMFVqNFdzMXJyNUE4V2pkQkE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48324002", "interchangeBizType": "SHLQ", "showTitle": "页面点击领奖励({current}/{total})", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 3, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300974, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 2, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300975, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 3, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300976, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYTAvQUVBcG56OUd5OWo1aHNWZndLVzNiay9Fbkc5cVdwTkZHYXFpbzdWRGZqbm56QWVWTGZoNzZaQm1XdkZBcGc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48282001", "interchangeBizType": "CDQC", "showTitle": "去逛逛飞猪领取红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302606, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZUdZdzNxOE14bHdSblJvZWdkZEJZY3VZVy9hOU42SU00N1NwS21Sd2htdnYrQk54K212cTFMMjZGaVcxU2NVQlE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48152003", "interchangeBizType": "SHLQ", "showTitle": "点击商品猜价格", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 299792, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZFI0YloyNlN5QytuYldibzR1NUphOUhTeUlEajBUYW4zQXNhc1I0ZThQQnRSZUpxYUlkeTJVVnRPWURiRTRNMGc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48044001", "interchangeBizType": "CDQC", "showTitle": "去【一键收下】滴滴打车红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 299581, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYUdzY2syQWZ2aGZ3TXBCNXJqSFgzTSt6WCtUVkYvYkZScHNhcUN1dEd6bE1pdDBrSGZxbS81a3Z2UThYYm5DUVE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48602001", "interchangeBizType": "CDQC", "showTitle": "去动动有金领红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300826, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLY05tcy9CQlBBYXo2YVpSN2ZUVzZOZ3MyQVIrQTJFT0NYWkV6ZGljMkJkVDRmMHZDb21CeUhTUnIxZkhJbjFkUWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48596001", "interchangeBizType": "CDQC", "showTitle": "去闲鱼看视频领奖励({current}/{total})", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 3, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300305, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 2, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300306, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 3, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 300307, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLU3lqMi9GNzhoYURxL1p2ZktIMFI4Rll0ZXlWaXJVdzl2ZXk5eC9GeldhMy9BdmU5VGloNHpPT0YzV3VLVUhtTVE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "46254001", "showTitle": "去百度地图领现金", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 299758, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWVBQVWFkRlFqWU1UR2gvSlUrQWNjV3h0cGxPSHBSQVVHQ28xNE9NdDdDTmtTWmJobmNkZU8rZjh2QTBMV3c0Q1E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49182001", "showTitle": "玩5s闲鱼小游戏", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303411, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYWJ2RkxCcDVDSHQ0TXVoVEw3STZ4VWdZQmttL0RiUlBnS2ZWYXQ5dXd0bTNzek8xSGtoZEdOcjlxajVFcW5IZGc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "45358002", "showTitle": "来淘金币常逛更省钱", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 301588, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWGlEMXdXUVk4REdUeFlkcUtsMjVhbjhOKytnYU5yQlRiMVkvQ29yK1NVMmM0a2prdERkRjFHQ0JVYUp3eDdQRHc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48834001", "showTitle": "淘宝点击两个商品", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303470, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLY0YvempLYVhxTzBFaEdlNVk0Y3dnSkczZ0tORWlUWDkxTGtndmx5UGQ2MWp1dWxpVHMzeFJLZU10dlFuVmlxYUE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "45108002", "showTitle": "双11寻宝中国抢4999元", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303331, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWENkOVQ4eWdZa1lVR0VWN0pSSUdCSFlRRCs4cjE2ZDQzWk1mYUZtTWcwc2hlaFBrR2g4dVFMVGxQYXJ4RTJsMFE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914004", "showTitle": "邀请好友助力({current}/{total})", "actionConfig": {"actionType": "P2P", "actionValue": {"finalStageCount": 10}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298508, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 2, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298509, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 3, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298510, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 4, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298511, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 5, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298512, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 6, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298513, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 7, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298514, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 8, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298515, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 9, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298516, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 10, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298517, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWFiby8zeDdiZjV1cjcxYkJsWWdyd3U0bzM4UkpxSHpUTXNrYXc1WktueGIzZkt0Ym9MQU4xM0J3YUV5amJsR0E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48532001", "showTitle": "浏览页面得奖励({current}/{total})", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "finalStageCount": 5}}, "missionStageDTOS": [{"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 1, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 2, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 3, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 4, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 5, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWTBpU3MrdE1MTElYN01Vc09uNlAxUTcvQnpSVzFqN0hjWjl3OGhXWkFpMm94VVhrVkRMcWt0cU9DNSt5SlpkQkE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48228002", "interchangeBizType": "SHLQ", "showTitle": "去一键领取优惠得奖励", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 1, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZFBoa0VyUUU4L2xhenhXMzVpVjExaGdrWnJqUW94TGtyWGg4cmZZcEZ2VEJQR0VWNDVZSWlzVnVsaHhwbTduZUE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49238001", "interchangeBizType": "SZYC", "showTitle": "点击领取2个红包得奖励", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 1, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLUVFTYTNmUkU2cGt5ZmxJRGFkUEIvcER1czd4VSsreHFiWFJ2cEc5YUtrdUVNMXhRZERzN2JDUHNHdXJBR0J1NWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48390007", "showTitle": "去玩赚现金红包", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "finalStageCount": 1}}, "missionStageDTOS": [{"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 1, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLUjB4OUpyVXY0V2NZb2FyMVFqMDNFTEpTQXhhTG1xK1Z4bVN0cHRoK3ZON3NiRTJKL2lXY0VIMGZkcGM0Y2lJRXc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47858001", "showTitle": "动动手指赚现金", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "finalStageCount": 1}}, "missionStageDTOS": [{"status": "RUNNING", "rewardStatus": "TODO", "stageCount": 1, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWZLYW9NckUwSXZVUnhiQUo3cy9oZ1c0U2wxMC91M0FMWjc1azFDZHkvdFlocys1bVp3aXkvUzRESjV0a2dGSkE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "46172002", "showTitle": "搜一搜【领优惠】", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298543, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLUWhOd2dnWDE4dGEzWmp5Umk3aXptSVVuL1F6M0c2bGJZeVhGR0JTTXlXaHpEMHJnT29ZNCtrQ3ZLTDdHV3k3cWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "45224001", "showTitle": "逛商家积分兑好礼，包邮到家", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302388, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLU3k3eVppQXBUb1c1dnBYV2g0bkIrajRXR2VnSTNINkNyNTFjM3FBRmZMQ1NBU25OaEpySTdremx0MklRdlRZN2c9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914003", "showTitle": "在页面内点击3个店铺", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 3, "scenceCode": "commercial_click"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 301361, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWFiby8zeDdiZjV1cjcxYkJsWWdyek13MU5aaExENjRqdC9OSmhqTjVwdG1oYUJSMEx2MjN1bmYwdlByRHNuZXc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "46230001", "interchangeBizType": "ANT_MANOR", "showTitle": "逛一逛蚂蚁庄园", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298544, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVmkrU3VGbmhIemVUaE1WemdRRnhUVlkwa2NpTW1yaE5DdWhRLzhyNjA0TGpIVU5nZDVlZmRpajZVREtQcUViYkE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914006", "interchangeBizType": "ET_ANT_FOREST", "showTitle": "来蚂蚁森林最多抽1吨能量", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303332, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWFiby8zeDdiZjV1cjcxYkJsWWdyekdUbmd3S3EwNGFwVHZTS0lUc0djeW9zQWYwSHc1cGoxYmNJbno4SDJPdWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48516001", "interchangeBizType": "CDQC", "showTitle": "点【立即领取】生活缴费红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302425, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYVA2dDhwUFJSNWFUSHhEY2VkL2U3Wm1PajcxMU9ya3pDdlJ5cUlyTWwzUFBOQmw3eUNHVnZzUnNwSlVEWEhic0E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914019", "showTitle": "去「闪购品牌日」逛一逛", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298522, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYXcveTlyaDRYbVJSOUZxc2NyazNweERac2NVV1NDQ04xSExrZ2l5L1kvL3l0ODdIc2M4dzVxaFdXb29LRlJEVnc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44914007", "showTitle": "浏览外卖品质好店", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303414, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWFiby8zeDdiZjV1cjcxYkJsWWdyd1ltclhUVGVtZVhDdjcrZUFGbHVuanVMS0JGWERqL3dlcWNXSnpFK1VpNUE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47798001", "showTitle": "开启签到订阅", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298553, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVWxvN2JzaUdOYW14b2ZWclU1TzF6aGU3TDhPdnpsTWhsWS9SbHE3dXF0MStmNTJmR280TVpYV3k2WXRkNE41S0E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "45474001", "showTitle": "免费领超时免单卡 最高赔100", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298538, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLV2lyZ0lEa2YvZitwTVpaMVU4OHc1c3huR3dtTFAxREgwREZIM0VvaUJlN243OXNSL1lodmJoRXhudkVmVlBFVGc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47202001", "showTitle": "抽惊喜免单", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 296221, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLYmxZbEdDbWZqR1ZqRWM1VEp0dHQvczBzemFacHF3cVZZaFBYbG41YUN6QWRUV0Y2NlhmS3RSckcvZEFlUWZKMFE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44684004", "showTitle": "领水果", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302385, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWlAvRmt3VHFNdzc2T3ZnV1ZaaEJPek9NYUw0TU9UMnIxZ2ZkMENJQkNPeW5NSnduVGlWSndZdWN5TVBDVThxR1E9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48220001", "showTitle": "天天夺宝", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 301099, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZWhSa0VPMGVWQ1BMb0EzUXkvWE9nNGNML3N2ek5TOUZnOGRSZmM0VGoxeXNzcTRBcUp6M2p2V01YMHhObjcwaGc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "48674001", "interchangeBizType": "XMHD", "showTitle": "浏览点击5个不同商品", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303015, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWmtKbTJKeVJlWmNKN0x5RXJvZTErc0Fwd0hyWW8vOEhPNHhuQm8yTytMMm9YL1k5RXFWTXVOOUlWU2E0UlIzcWc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "44920003", "showTitle": "去飞猪领现金红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298525, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWG51Y2hrWFhudjZ2VkViWXZRYUF5UE1heGN4ak9mRmprMHp4eUNTWHNyZ2RRdldtaGJnaXFRMzVyMFExOWJQOWc9PQ", "interchangeBizType": "FLIGGY"},
    {"missionCollectionId": "3112", "missionDefId": "45226007", "showTitle": "看淘宝视频领红包赚钱", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302678, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLZjR1QytCMUNsbUtwUTl0ODdsb2NHZGliTWdKcUpOWm9PaklTdm85QUNjVG9LUkh0TVN0VmFvLzVVZXhrOVpiMnc9PQ", "interchangeBizType": "ET_TAB_2"},
    {"missionCollectionId": "3112", "missionDefId": "45226015", "showTitle": "去闲鱼领现金红包", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298531, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVUZHYXg5K2d2VTVESkVjcHhYQTI4VkljY0IrUktIeUh1TnEwZGw3Vzg2WURoR0FWM3Z3TUgwM2ZnbHpGcjhYa0E9PQ", "interchangeBizType": "ET_SECONDFISH"},
    {"missionCollectionId": "3112", "missionDefId": "45226021", "showTitle": "闪购会员日领1对1急送券", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"finalStageCount": 1, "pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302389, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLV0dxVFhHNFpkZUhtNU9iTVUrQStrNGlCYS82S2ZxbldLUGRSNEFhYWU5Z0prbExNNllDamRRb1BzRmxZVHM0Nnc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47754001", "showTitle": "开启签到订阅", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"finalStageCount": 1, "pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298551, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLV1hBYStrYW1NOVRnZ1MxV0hHVG9Dd3M3d2NGNmFmdTNSNG5TR0wzMDBKNjVZNlA2YzliN3FUeUc3bG9GL2JPMlE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "47846001", "showTitle": "去百度极速版领现金", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298568, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVWxKSElhemxNWUpiTFg4c09VcXB6Sk1vUmZVV1cxaFRXSzc4WEY1eFBJNmJqTkhOK2RHK2h0L1JWNTc2Z29xZ3c9PQ", "interchangeBizType": "BAIDU_APP"},
    {"missionCollectionId": "3112", "missionDefId": "47854001", "showTitle": "去快手极速打卡领手机", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298564, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWjl0WWc5QzdVZ0hkZzNFYWNsS1hQMkg5cWdUenFxRjNaN3B6dEN4RTdKaUdTMCtsaExsd3puQzJscnpqOFZxQVE9PQ", "interchangeBizType": "KUAISHOU"},
    {"missionCollectionId": "3112", "missionDefId": "47908001", "showTitle": "去一淘签到领现金", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 298566, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLV0g4Z3g2QVhyT2xKeHBjNzV0Y1ZqYlhobldCSnBIVmlxVWJBTlZMSnlxTlhmRmh6VUtuOVdzSUNaR1BmU1hRNFE9PQ", "interchangeBizType": "ETAO"},
    {"missionCollectionId": "3112", "missionDefId": "49020001", "showTitle": "今日累计任务奖励", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 9, "scenceCode": "third"}}, "missionStageDTOS": [{"stageCount": 3, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302393, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 6, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302394, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}, {"stageCount": 9, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 302395, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "eDhNaXF4dExNeTRMM00rZng0K3dWd2QzWEtZNkJ0U0x5UjRiNFR1V2Y2TXpYdHZyY0RvMWovSi9GQnJhOU5Jekh5dnYzeEdJZGN6MnpLMURXTzhDTHc9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49270001", "showTitle": "点外卖送大额幸运星【限时】", "actionConfig": {"actionType": "ORDER", "actionValue": {"finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWEhYd2NUWnE1NjBBOG82andSdW5JQ3lKbCs0dlFLSVNBZG03QmNvRUk4L0pXYkdGS0lVNHhNY3VHM0J5M3ZWTUE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49180001", "showTitle": "拍立淘逛感兴趣的宝贝", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303751, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVGhkc1pIb1VhUk5pd3NPcHNHbkRiTW5sT2xSVmlmSlFYeXhKOHd2cHpYUkRQZGtRWUxqNmFYRldiYkRiQlE1Q3c9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "49192002", "showTitle": "去领取3次优惠得奖励", "actionConfig": {"actionType": "THIRD", "actionValue": {"finalStageCount": 1, "scenceCode": "inter_change"}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303488, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLWXZpUlYyQUYrb3laa2NYdmg2dURGMjN0U01Tb09PQjVQYVpSS09paExvM01Rd3grbm96d3JHMWxKajdCTVFPblE9PQ"},
    {"missionCollectionId": "3112", "missionDefId": "46152001", "showTitle": "玩薅羊毛领奖励", "actionConfig": {"actionType": "PAGEVIEW", "actionValue": {"pageSpm": "a2ogi.bx1500380", "pageScene": "COMMON", "finalStageCount": 1}}, "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO", "sourceActionId": 303334, "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}], "receiveStatus": "TORECEIVE", "missionXId": "ZFprcSs2OXZKR2ROV1YrenpnVmFLVnNyd1h0OStKcDhYcWVDZXYyNldwbG5PK2wwa2FyTWVHK2VNblAvYlRaNlBPeGdzN0RlNUQ1TjZVVnkwMHJFcnc9PQ"},
]

CPN_CODES = [
    "PLAY_NOTICE_CPN",
    "INTERACT_RESOURCE_CPN",
    "MORE_MENU_CPN",
    "STAR_MSG_CONTENT_CPN",
    "PLAY_RESOURCE_CPN",
    "INTERACT_CENTER_SKIN_COMPONENT",
    "INTERACT_CENTER_BUBBLE",
    "INTERACT_CENTER_LOTTERY",
    "INTERACT_CENTER_DRAW_BUBBLE",
    "INTERACT_CENTER_CDP_CONFIG",
    "INTERACT_CENTER_MODAL_TASK",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

DEBUG = os.environ.get("ELM_DEBUG", "") == "1"
DRYRUN = os.environ.get("LUCKYSTAR_DRYRUN", "") == "1" or "--dryrun" in sys.argv[1:]
#: 拟人化行为层（默认开；ELM_HUMAN=0 关闭）
HUMAN = os.environ.get("ELM_HUMAN", "1") != "0"
#: 伪装层（默认开；ELM_STEALTH=0 关闭）
STEALTH = os.environ.get("ELM_STEALTH", "1") != "0"
#: 每日只跑一次（默认开，防止青龙重复触发/手动重跑被判定异常；ELM_FORCE=1 强制）
ONCE_PER_DAY = os.environ.get("ELM_ONCE_PER_DAY", "1") != "0"
STATE_FILE = Path(__file__).resolve().parent / "lucky_state.json"
#: 账号级风控标记（命中即中止本轮，不再发写请求）
RISK_RE = re.compile(r"FAIL_SYS_USER_VALIDATE|FAIL_SYS_SESSION_EXPIRED|FAIL_SYS_SESSION_INVALID|FAIL_SYS_TOKEN_ILLEGAL|FAIL_SYS_ILLEGAL_ACCESS|NOT_LOGIN|LOGIN_REQUIRED|FAIL_BIZ_USER_RISK|用户存在风险|USER_RISK", re.I)

# ---- 设备指纹：与自建指纹面板（192.168.3.174:18903）同一套池与「不复用」规则 ----
DEVICE_MODELS = ["22081212C", "PHB110", "V2134A", "SM-S9080",
                 "PJZ110", "LIO-AN00", "M2011K2C", "PGT-AN10"]
DEVICE_VERSIONS = ["13", "12", "14", "11"]
DEVICE_CHANNELS = ["channel35", "channel50", "channel60"]
DEVICE_FILE = Path(__file__).resolve().parent / "lucky_devices.json"


def random_hex(nbytes: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(nbytes))


def obfuscate_id(raw: str) -> str:
    """与面板同款混淆：逐字符 +2（不可逆）。"""
    return "".join(chr((ord(c) + 2) & 0xFFFF) for c in raw)


def make_device() -> dict:
    """生成一个设备指纹：机型/版本/渠道从池里随机，ID 全随机（同面板 makeDevice）。"""
    rid = random_hex(16)
    model = random.choice(DEVICE_MODELS)
    ver = random.choice(DEVICE_VERSIONS)
    ch = random.choice(DEVICE_CHANNELS)
    wxv = cfg("ELM_WECHAT_VERSION", "8.0.58")
    return {
        "androidIdRaw": rid,
        "androidIdObfuscated": obfuscate_id(rid),
        "clientType": f"#11.3.0#{ch}#{model}#",
        "androidVersion": ver,
        "model": model,
        "channel": ch,
        "wechatVersion": wxv,
        "os": cfg("ELM_DEVICE_OS", "android"),
        "ua": (f"Mozilla/5.0 (Linux; Android {ver}; {model} Build/TKQ1.220829.002; wv) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/107.0.0.0 "
               f"Mobile Safari/537.36 XWEB/1160 MMWEBSDK/20230805 MicroMessenger/{wxv} "
               "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"),
    }


def device_key(d: dict) -> str:
    return f"{d.get('clientType', '')}#{d.get('androidIdObfuscated', '')}"


def remote_device(api: str, key: str, reset: bool = False) -> dict | None:
    """
    向指纹服务（fingerd / 自建面板）申请设备：同一个 key 会拿回同一台（服务端保证）。
    服务不可用或返回异常时返回 None，调用方自动退回本地生成。
    """
    if not api:
        return None
    url = api if api.rstrip("/").endswith("gen_device") else api.rstrip("/") + "/api/gen_device"
    headers = {}
    ak = cfg("ELM_DEVICE_API_KEY")
    if ak:
        headers["X-API-Key"] = ak
    try:
        r = requests.post(url, timeout=6, headers=headers, json={
            "key": key, "scene": cfg("ELM_DEVICE_SCENE", "eleme"),
            "os": cfg("ELM_DEVICE_OS", "android"), "reset": bool(reset)})
        j = r.json() or {}
        d = j.get("data") or j
        if isinstance(d, dict) and (d.get("androidIdObfuscated") or d.get("clientType")):
            log(f"[指纹] 领取自 {api} → {d.get('model')} / Android {d.get('androidVersion')}"
                f" / {d.get('channel')}")
            return d
        log(f"[指纹] 服务返回异常（{str(j)[:80]}），改用本地生成")
    except Exception as exc:                                # noqa: BLE001
        log(f"[指纹] 服务不可用（{str(exc)[:60]}），改用本地生成")
    return None


def alloc_device(account_key: str, reset: bool = False) -> dict:
    """
    给账号分配**独占且不复用**的设备指纹，并持久化：
      · 同一账号下次运行拿到同一指纹（设备稳定，不会天天换机）
      · 不同账号绝不共用（避开"一机多号"关联）
      · ELM_RESET_DEVICE=1 相当于面板的「重置设备」
    """
    if DRYRUN:
        dev = make_device()
        dev["utdid"] = "DRYRUN-LOCAL-ONLY"
        return dev
    store: dict = {}
    try:
        if DEVICE_FILE.is_file():
            store = json.loads(DEVICE_FILE.read_text(encoding="utf-8")) or {}
    except Exception:                                       # noqa: BLE001
        store = {}
    accs = store.setdefault("accounts", {})
    used = set(store.get("used") or [])
    dev = None if reset else accs.get(account_key)
    if dev is None:
        api = cfg("ELM_DEVICE_API")
        dev = remote_device(api, account_key, reset) if api else None
        if not dev:
            for _ in range(100):                            # 同面板：最多试 100 次找未用过的
                cand = make_device()
                if device_key(cand) not in used:
                    dev = cand
                    break
            else:
                dev = make_device()
        used.add(device_key(dev))
        accs[account_key] = dev
        store["used"] = sorted(used)[-2000:]
        if not dev.get("utdid"):
            dev["utdid"] = ("WX" + hashlib.sha1(
                (dev["androidIdRaw"] + dev["channel"]).encode()).hexdigest()[:30]).upper()
        try:
            DEVICE_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass
    return dev


def redact(message: str) -> str:
    """日志不打印登录Cookie、签名和推送令牌。"""
    text = str(message)
    text = re.sub(r"(?im)\b(Cookie|Set-Cookie|Authorization)\s*:\s*[^\r\n]+", r"\1: [已隐藏]", text)
    fields = (r"cookie2|_m_h5_tk(?:_enc)?|sid|utuser|userid|munb|unb|sgcookie|_tb_token_|"
              r"_nk_|tfstk|isg|cna|x5check_ele|track_id|_c_WBKFRo|__ebg_uid|__ebg_utdid|"
              r"access_token|refresh_token|token|sign|password|secret|corpsecret")
    text = re.sub(r"(?i)(\b(?:" + fields + r")\s*=\s*)[^;\s&\]】,]+", r"\1[已隐藏]", text)
    return text


def log(msg: str = "") -> None:
    with _LOG_LOCK:
        print(f"{datetime.now():%H:%M:%S} {redact(msg)}", flush=True)


def nap(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def nap_human(mean: float = 2.0, sigma: float = 0.6, cap: float = 4.0) -> None:
    """对数正态停顿：多数短、偶发长，比均匀分布更像真人手速。"""
    if HUMAN:
        time.sleep(min(random.lognormvariate(math.log(max(mean, 0.2)), sigma), mean * cap))


def jitter(lat: float, lng: float, km: float = 1.2) -> tuple[float, float]:
    """在常用坐标附近做小范围抖动，避免每次请求经纬度完全一致。"""
    if not HUMAN or km <= 0:
        return lat, lng
    r = km / 111.0
    return round(lat + random.uniform(-r, r), 6), round(lng + random.uniform(-r, r) / max(0.2, math.cos(math.radians(lat))), 6)


def _scene_code_of(task: dict) -> str:
    """任务的 scenceCode（actionValue.scenceCode），如 inter_change。"""
    av = (task.get("actionConfig") or {}).get("actionValue") or {}
    return str(av.get("scenceCode") or "")


def extract_action_token(task: dict) -> str | None:
    """从任务 ext.extValue 的跳转链接里取回调要的 token。

    官方把 token 嵌在跳转链接的 token= / flashSaleToken= 参数里
    （missionActionUrl / alipayActionLink / elemeActionLink /
    taobaoActionLink），171 字符 base64，约 128 字节。链接多层
    URL 编码，先反复解码再找参数。token 是会话相关的，别会话
    的 token 不能用，所以只取当前会话任务自带的。"""
    ev = (task.get("ext") or {}).get("extValue") or {}
    for key in ("missionActionUrl", "alipayActionLink", "elemeActionLink", "taobaoActionLink"):
        url = ev.get(key)
        if not url:
            continue
        u = str(url)
        for _ in range(3):
            u2 = unquote(u)
            if u2 == u:
                break
            u = u2
        m = re.search(r"[?&](?:token|flashSaleToken)=([^&]+)", u)
        if m:
            return m.group(1)
    return None


def cfg(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return v.strip() if v and v.strip() else default


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# =========================================================================== #
#  账号解析
# =========================================================================== #

def parse_cookie_line(line: str) -> tuple[str, str]:
    """每行一个账号，备注#完整Cookie；兼容Cookie#备注及Cookie片段#片段。"""
    raw = str(line or "").strip().replace("\u3000", " ")
    if not raw:
        return "", ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].strip()
    chunks, labels = [], []
    for part in raw.split("#"):
        part = re.sub(r"(?i)^\s*cookie\s*:\s*", "", part.strip())
        # 兼容导出文件行首时间和标签，但绝不把key=value部分当备注。
        match = re.search(r"(?:^|\s)([A-Za-z0-9_!$%&'*+.^`|~-]+=)", part)
        if match:
            fragment = part[match.start(1):].strip().strip(";")
            chunks.append(fragment)
        elif part and "=" not in part and ";" not in part:
            labels.append(part)
    if not chunks:
        return "", ""
    values, conflicts = {}, set()
    for chunk in chunks:
        for part in chunk.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and re.fullmatch(r"[A-Za-z0-9_!$%&'*+.^`|~-]+", key):
                if "\r" in value or "\n" in value:
                    continue
                value = value.strip()
                if key in values and values[key] != value:
                    conflicts.add(key)
                values[key] = value
    if conflicts:
        raise ValueError("同一行存在冲突的Cookie字段或用户ID；多账号必须换行，不能用#拼接")
    cookie = "; ".join(f"{key}={value}" for key, value in values.items())
    remark = labels[0] if labels else cookie_remark(cookie)
    if "=" in remark or ";" in remark or re.search(r"1[3-9]\d{9}", remark):
        remark = cookie_remark(cookie)
    remark = re.sub(r"[\r\n\t]", " ", remark).strip()[:32] or "账号"
    return remark, cookie


def cookie_remark(cookie: str) -> str:
    """自动备注只保留用户ID尾四位，不回显Cookie或手机号。"""
    match = re.search(r"(?:^|;\s*)(?:UTUSER|userId|USERID|unb)=([^;]+)", cookie)
    value = match.group(1) if match else ""
    return "账号" + ("***" + value[-4:] if value else "")


def load_accounts() -> list[tuple[str, str]]:
    """优先ELM_COOKIE（每行一号），额外支持ELM_COOKIE2/3等独立变量。"""
    raw = ""
    for name in ("ELM_COOKIE", "ELEME_COOKIE", "LUCKYSTAR", "XX_ACCOUNTS", "LS_ACCOUNTS", "LUCKYSTAR_ACCOUNTS"):
        if cfg(name):
            raw = cfg(name)
            break
    extras = [cfg(f"ELM_COOKIE{i}") for i in range(2, 21) if cfg(f"ELM_COOKIE{i}")]
    if extras:
        raw = "\n".join([raw] + extras)
    if not raw.strip():
        name = cfg("ELM_COOKIE_FILE", "eleme_cookie.txt")
        here = Path(__file__).resolve().parent
        for candidate in (Path(name), here / name, Path("/ql/data") / name, Path("/ql/data/scripts") / name):
            try:
                if candidate.is_file():
                    raw = candidate.read_text(encoding="utf-8-sig")
                    if raw.strip():
                        log("📂 已读取本地Cookie文件（不输出内容）")
                        break
            except (OSError, UnicodeError):
                continue
    accounts, seen = [], set()
    for row, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            remark, cookie = parse_cookie_line(line)
        except ValueError:
            log(f"⚠ 第{row}行有冲突的Cookie字段，已跳过；请把不同账号分别换行")
            continue
        if not cookie or len(cookie.split(";")) < 2:
            log(f"⚠ 第{row}行不是完整Cookie，已跳过；每行填写 备注#完整Cookie")
            continue
        canonical = ";".join(sorted(part.strip() for part in cookie.split(";") if part.strip()))
        if canonical in seen:
            continue
        seen.add(canonical)
        keys = {part.partition("=")[0].strip().lower() for part in cookie.split(";")}
        if keys <= {"_m_h5_tk", "_m_h5_tk_enc", "m_h5_tk", "m_h5_tk_enc"}:
            log(f"⚠ 第{row}行只有mtop签名token，缺少登录Cookie，已跳过")
            continue
        accounts.append((remark, cookie))
    return accounts


# =========================================================================== #
#  网络层：mtop
# =========================================================================== #

# Task diagnostics: preserve task structure while hiding unknown/sensitive values.
_TASK_SAFE_FIELDS = set(('missionCollectionId missionCollectId missionDefId missionId id instanceId '
    'missionName showTitle taskName subTitle name title description actionType pageSpm pageStageTime '
    'finalStageCount scenceCode sceneCode executeOpportunity receiveType receiveStatus status rewardStatus '
    'count total current amount prizeType prizeValue equityValue stageCount stageSum actionSubtype '
    'actionSubSubType sourceActionType todayDone cycleType startTime endTime startDate endDate cycleDate '
    'isEntryPathTask alipayRewardAdSpaceCode subscribeScene triggerScene actionCode sync code errorCode '
    'taskType taskScene source channel bizScene accountPlan platform product prodVersion ttid version '
    'schemaVersion sourceLabel accountIndex taskCount category support reason capturedAt value '
    'type unit rewardDesc dayLimitCount dayLimitDay applyClients cdpClients cdpChannels platforms '
    'interchangeBizType missionBizType finishStatus buttonTitle finishButtonTitle missionRewardButton').split())
_TASK_PRIVATE_FIELDS = re.compile(r'cookie|token|authorization|password|secret|csrf|signature|^sign$|'
    r'openid|userid|utuser|^unb$|^munb$|^sid$|phone|mobile|email|nickname|avatar|'
    r'utdid|deviceid|androidid|idfa|imei|latitude|longitude|^lat$|^lng$|locationinfos|'
    r'missionxid|session|encrypted|ticket|traceid', re.I)


def sanitize_task_data(value, key="", known_secrets=(), depth=0):
    """Unknown scalar values are hidden; safe task IDs, stages and rewards remain."""
    hidden = "[已隐藏]"
    if _TASK_PRIVATE_FIELDS.search(re.sub(r"[_-]", "", str(key))):
        return hidden
    if depth > 18:
        return "[层级超限]"
    if not isinstance(value, (dict, list)) and any(len(str(secret)) >= 6 and str(secret) == str(value) for secret in known_secrets):
        return hidden
    if isinstance(value, dict):
        return {str(k): sanitize_task_data(v, str(k), known_secrets, depth+1) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_task_data(v, key, known_secrets, depth+1) for v in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return sanitize_task_data(json.loads(text), key, known_secrets, depth+1)
            except (TypeError, ValueError):
                pass
        for secret in known_secrets:
            if len(str(secret)) >= 6:
                text = text.replace(str(secret), hidden)
        if re.match(r'^(?:https?|alipays?|weixin|afun)://', text, re.I):
            # Keep route origin and names of URL arguments, not account tickets or redirects.
            try:
                url = urlsplit(text)
                path = '/'.join('[已隐藏]' if len(s) > 32 or re.fullmatch(r'\d{7,}', s) else s
                                for s in url.path.split('/'))
                args = urlencode([(k, '[已隐藏]') for k, _ in parse_qsl(url.query, keep_blank_values=True)])
                host = url.hostname or ''
                return urlunsplit((url.scheme, host, path, args, ''))
            except ValueError:
                return '[链接已隐藏]'
        if key not in _TASK_SAFE_FIELDS and key != 'ret':
            return '[未审阅字段值已隐藏]'
        if key == 'ret':
            return text.split('::', 1)[0] if re.fullmatch(r'[A-Z0-9_]+(?:::[\s\S]*)?', text) else hidden
        text = re.sub(r'(?i)\b(?:SID|cookie2|_m_h5_tk(?:_enc)?|token|sign|secret|password|UTUSER|USERID)\s*[:=]\s*[^;\s,&]+', hidden, text)
        text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[手机号已隐藏]', text)
        return text
    return value if key in _TASK_SAFE_FIELDS and (value is None or isinstance(value, (int, float, bool))) else "[未审阅字段值已隐藏]"


def make_task_snapshot(tasks, source_label='qinglong', account_index=1, collection_id=None,
                       known_secrets=(), ret_codes=None):
    prepared = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        prepared.append(sanitize_task_data(task, known_secrets=known_secrets))
    return {
        'schemaVersion': 1, 'capturedAt': datetime.now().isoformat(timespec='seconds'),
        'sourceLabel': source_label, 'accountIndex': account_index,
        'missionCollectionId': collection_id, 'taskCount': len(prepared),
        'ret': [str(x).split('::', 1)[0] for x in (ret_codes or [])
                if re.fullmatch(r'[A-Z0-9_]+(?:::[\s\S]*)?', str(x))],
        'scope': '仅当前账号、入口和时间实际返回的任务；不代表其它App或全部渠道任务',
        'redaction': '登录凭据、个人信息、missionXId、URL参数值及未审阅字段值已隐藏；不得直接重放此诊断文件',
        'tasks': prepared,
    }


def write_task_snapshot(report, output_dir):
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # New unique file per capture; never overwrite an earlier account's report.
    fd, path = tempfile.mkstemp(prefix=f"elm_tasks_account{report.get('accountIndex', 1)}_",
                                suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return Path(path)


def account_state_key(cookie: str) -> str:
    match = re.search(r"(?:^|;\s*)(?:UTUSER|userId|USERID|munb|unb)=([^;]+)", cookie)
    identity = match.group(1) if match else cookie
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


# 1.5.53：登录失效（会话/Token 已死，须重新登录换 Cookie）才算死号。
# USER_VALIDATE（需用户验证）不算——App 内验证通过后同一 Cookie 还能用，
# 不能因一次验证就永久跳过。RISK_USER 更不是死号（只是 H5 渠道领不到）。
LOGIN_DEAD_RE = re.compile(
    r"NOT_LOGIN|LOGIN_REQUIRED|SESSION_EXPIRED|SESSION_INVALID|"
    r"TOKEN_ILLEGAL|TOKEN_EXPIRED|TOKEN_EMPTY|FAIL_BIZ_NOT_LOGIN", re.I)


def cookie_fingerprint(cookie: str) -> str:
    """Cookie 指纹：完整 Cookie 的哈希。用户重新登录更新 Cookie 后指纹会变，
    失效账号自动恢复运行；只认指纹，不认账号名。"""
    return hashlib.sha256(cookie.strip().encode("utf-8")).hexdigest()[:24]


def is_login_dead_reason(reason: str) -> bool:
    """stop_reason 是不是登录失效（须换 Cookie）。"""
    return bool(reason and LOGIN_DEAD_RE.search(reason))


class MtopError(Exception):
    pass


class ElmAccount:
    """单账号：mtop 会话 + 业务编排。"""

    def __init__(self, remark: str, cookie: str) -> None:
        self.remark = ("账号" if "=" in remark or ";" in remark else remark[:32])
        self.cookie = cookie.strip()
        self.lat = float(cfg("ELM_LAT", "31.2304"))
        self.lng = float(cfg("ELM_LNG", "121.4737"))
        self.app_key = cfg("ELM_APPKEY", "12574478")
        self.gateway = cfg("ELM_GATEWAY", "https://waimai-guide.ele.me/h5/").rstrip("/") + "/"
        self.app_version = cfg("ELM_APP_VERSION", "12.8.8")
        self.task_times = int(cfg("ELM_TASK_TIMES", "7") or 7)
        self.session = requests.Session()
        self.proxies = self._proxies()
        self.result: dict[str, str] = {}
        # 1.5.40 领奖防重复：本账号本轮已领过 / 已判定领不到的阶段，键是
        # (missionDefId, stageCount)，跨 3112 与天天免单的各轮 process_tasks
        # 共用（同一任务常同时出现在两个集合里，旧版两轮各领一次，服务端
        # 第二次必报 TARGET_HAD_RECEIVE；13:29 那轮因此白打了 162 次）。
        self._claimed_stages: set[tuple[str, int]] = set()
        # 同一阶段领奖报 STAGE_NOT_FINISH 的次数；达到 ELM_CLAIM_RETRIES
        # （默认 2）后该阶段本轮不再尝试领奖，也不再乐观标 FINISH
        # （13:29 那轮六个顽固任务因此白打了 213 次）。
        self._claim_not_finish: dict[tuple[str, int], int] = {}
        self._claim_blocked: set[tuple[str, int]] = set()
        self.risk_flagged = False
        self.stop_reason = ""
        # 1.5.47：3112 的 H5/Android 身份粘性标志（初值取 ELM_TASK_H5，默认
        # H5）。1.5.46 在 query_tasks 里直接改 os.environ 全局变量做退回，
        # 多账号并行时别的线程会读到被改掉的值，导致重试被跳过或身份混搭，
        # 服务端报 FAIL_SYS_BIZPARAM_MISSED。各号实例只被自己的线程用，
        # 实例标志无跨线程干扰；一旦证实是 Android 会话就一直走 Android。
        self._task_h5 = cfg("ELM_TASK_H5", "1") != "0"
        #: 幸运星余额（跑之前 / 跑之后），用于日志里给每个号播报「现有多少币」
        self.star_start: int | None = None
        self.star_end: int | None = None
        #: 1.5.44：本号 44914004 的实时助力信息（run() 里 querytask 后采
        #: 集；main() 的互助排计划用。无则 None，不参与互助）。
        self.p2p_invite: dict | None = None
        # ★ 实测关键：必须伪装成微信小程序环境，否则所有写接口返回 405::行为受限
        self.appid = cfg("ELEME_APPID", "wxece3a9a4c82f58c9")
        self.uid = self._cookie_get("UTUSER") or self._cookie_get("userId")
        # 设备指纹：按账号独占、跨账号不复用、跨运行稳定（详见 alloc_device）。
        # key 优先用 munb/UTUSER/userId —— 可读且与其他项目（共用 fingerd 时）对得上，
        # 取不到才退回 Cookie 哈希。
        self.account_key = (self._cookie_get("munb") or self._cookie_get("UTUSER")
                            or self._cookie_get("userId")
                            or hashlib.md5(self.cookie.encode()).hexdigest()[:12])
        # 1.5.40：设备指纹文件多账号共享，并行时 alloc_device 的读-改-写必须
        # 串行化，否则两个号可能分到同一指纹（关联风控）或丢更新。
        with _DEVICE_LOCK:
            self.dev = alloc_device(self.account_key,
                                   reset=os.environ.get("ELM_RESET_DEVICE") == "1")
        self.wechat_version = self.dev.get("wechatVersion") or cfg("ELM_WECHAT_VERSION", "8.0.58")
        self.utdid = self.dev.get("utdid", "") if STEALTH else ""
        self.hdr = {
            "User-Agent": self.dev["ua"] if STEALTH else
                          ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 Mobile/15E148 "
                           f"MicroMessenger/{self.wechat_version} miniProgram/{self.appid}"),
            "Referer": f"https://servicewechat.com/{self.appid}/831/page-frame.html",
            "Origin": "https://servicewechat.com",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "x-tap": "wx",
        }
        if STEALTH:
            # 真实小程序环境必带的设备/链路头（格式逆向自登录 CLI）。
            # 账号间 utdid 互不相同 —— 同一台机器多账号共用指纹是典型的关联风控特征。
            self.hdr.update({
                "x-utdid": self.utdid,
                "x-ele-ua": " ".join(filter(None, [
                    "RenderWay/miniProgram",
                    f"MiniAppId/{self.appid}",
                    f"MiniAppVersion/{self.app_version}",
                    f"DeviceId/{self.uid}" if self.uid else "",
                    "AppName/Wechat",
                    (f"Xiaomi/{self.dev['model']}/Android {self.dev['androidVersion']}"
                     if self.dev.get("os") == "android"
                     else "Apple/iPhone/iOS 17.0"),
                    f"Wechat/{self.wechat_version}",
                    "channel/wechat_app",
                    "subChannel/wechat_app.default",
                    f"Longitude/{self.lng:.4f}",
                    f"Latitude/{self.lat:.4f}",
                ])),
            })
            if self.uid:
                self.hdr["x-uid"] = self.uid

    # ---------- 基础 ----------
    def _proxies(self) -> dict | None:
        """
        解析代理环境变量，支持三种格式（逗号/换行分隔多条）：
          1. 长效代理：IP|端口|用户名|密码|过期日期
             如 223.15.244.28|9347|4363991|ab7ac092c|2026-10-23
          2. http://user:pass@ip:port  /  http://ip:port
          3. ip:port
        规则：
          · 过期条目自动剔除（打警告，绝不用过期代理跑请求）
          · 多条时按账号 cookie 哈希稳定选取（同账号永远同一出口 IP）
        """
        raw = cfg("ELM_PROXY") or cfg("LUCKYSTAR_PROXY") or cfg("PROXY") or cfg("GLOBAL_PROXY")
        if not raw or not raw.strip():
            return None
        entries = [e.strip() for e in re.split(r"[,\n]+", raw) if e.strip()]
        today = datetime.now().strftime("%Y-%m-%d")
        usable: list[str] = []
        for e in entries:
            parts = e.split("|")
            if len(parts) == 5:                          # 长效格式 IP|PORT|USER|PASS|EXPIRE
                ip, port, user, pwd, expire = (x.strip() for x in parts)
                if expire and expire < today:            # 字符串比较即日期比较
                    log(f"⚠ 代理 {ip}:{port} 已于 {expire} 过期，跳过")
                    continue
                e = f"http://{quote(user)}:{quote(pwd)}@{ip}:{port}"
            elif "://" not in e:
                e = "http://" + e
            usable.append(e)
        if not usable:
            log("⚠ 所有代理条目均已过期，本次直连运行")
            return None
        pick = usable[0] if len(usable) == 1 else \
            usable[int(hashlib.md5(self.cookie.encode()).hexdigest(), 16) % len(usable)]
        return {"http": pick, "https": pick}

    def _log(self, msg: str) -> None:
        log(f"【{self.remark}】{msg}")

    def _cookie_get(self, key: str) -> str:
        from urllib.parse import unquote                             # noqa: PLC0415
        m = re.search(rf"(?:^|;\s*){re.escape(key)}=([^;]*)", self.cookie)
        return unquote(m.group(1)) if m else ""

    @property
    def token(self) -> str:
        """mtop 签名 token = Cookie 里 _m_h5_tk 的下划线前段。"""
        m = re.search(r"(?:^|;\s*)_?m_h5_tk=([^;]+)", self.cookie)
        return m.group(1).split("_")[0] if m else ""

    def _update_cookie(self, set_cookie: str) -> bool:
        if not set_cookie:
            return False
        upd = []
        for k in ("_m_h5_tk", "_m_h5_tk_enc"):
            m = re.search(rf"{k}=([^;,\s]+)", set_cookie)
            if m:
                upd.append(f"{k}={m.group(1)}")
        if not upd:
            return False
        clean = "; ".join(part.strip() for part in self.cookie.split(";")
                          if part.strip() and not re.match(r"_m_h5_tk(?:_enc)?=", part.strip(), re.I))
        self.cookie = clean.rstrip("; ") + "; " + "; ".join(upd)
        return True

    @staticmethod
    def _grab_set_cookie(resp) -> str:
        """尽量把响应里所有 Set-Cookie 都收齐（requests 合并后常被截断）。"""
        parts = []
        try:
            parts.extend(resp.raw.headers.getlist("Set-Cookie"))
        except Exception:                                       # noqa: BLE001
            pass
        single = resp.headers.get("Set-Cookie", "")
        if single and single not in parts:
            parts.append(single)
        return "; ".join(parts)

    def prime_token(self) -> bool:
        if self.risk_flagged:
            return False
        if self.token or DRYRUN:
            return True
        self._log("当前Cookie不含mtop签名token，向官方查询接口申领一次")
        try:
            self.mtop(API["querytask"], "1.0", {"bizScene": BIZ_SCENE}, retry=2)
        except MtopError:
            pass
        if self.risk_flagged:
            return False
        if self.token:
            self._log("✅ 已取得mtop签名token（不输出token值）")
            return True
        self._log("❌ 未取得签名token，请从官方活动页面获取完整Cookie")
        return False

    # ---------- mtop 请求 ----------
    def mtop(self, api: str, version: str, data: dict,
             extra_headers: dict | None = None, retry: int = 3) -> dict:
        """POST + subVersion 5.0，自动处理 token 刷新与过期重试。"""
        if self.risk_flagged:
            raise MtopError("本账号已要求登录/用户验证，本轮停止请求")
        task_api = api in {API[k] for k in ("querytask", "receivetask", "receiveprize", "pageview", "trigger", "callback")}
        common = None
        if task_api:
            # 按请求自身的 bizScene 选身份：3112 用 H5 身份，ETMD_CHANNEL 用 Android。
            common = self.task_common_data(data.get("bizScene"))
            if api == API["callback"]:
                # 1.5.25：回调 data 只含 mapRules 字段（bizScene/token/
                # source/plat）。官方 H5 回调的 data JSON 不带
                # task_common_data 的 accountPlan/lat/lng/locationInfos/
                # platform/product/prodVersion/ttid；1.5.24 把它们并进
                # data，服务端报 400 参数校验出错。身份字段仍留在 query
                # 与签名里（common 照常用于 query）。
                pass
            else:
                data = {**common, **data}
                # Keep these platform/location fields identical across every task endpoint.
                for field in ("platform", "product", "prodVersion", "ttid", "longitude", "latitude", "locationInfos"):
                    data[field] = common[field]
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(retry):
            t = str(int(time.time() * 1000))
            query = {
                "jsv": "2.7.5",
                "appKey": self.app_key,
                "t": t,
                "sign": md5(f"{self.token}&{t}&{self.app_key}&{payload}"),
                "api": api,
                "v": version,
                "type": "originaljson",
                "dataType": "json",
                "data": payload,
                # aladdin 投放校验（App 原生环境注入，缺一不可）
                # 任务接口：query 与签名 data 必须用同一套身份（3112 用 H5 身份，
                # ETMD 用 Android）；非任务接口保持 Android。
                "platform": common["platform"] if task_api else "android",
                "product": "eleme",
                "prodVersion": common["prodVersion"] if task_api else self.app_version,
                "ttid": common["ttid"] if task_api else f"android@eleme_android_{self.app_version}",
            }
            # Fix 2026-11-17: aladdin 校验查的是 URL query，不是 body。
            # v1.5.3 把 platform/product/prodVersion/ttid 从 query 删掉、
            # 又不给 query 加 locationInfos，导致 querytask 报
            # CLIENT_PARAM_ALADDIN_DEVICEINFO_ERROR。恢复 v1.5.1 的做法：
            # 这 5 个参数必须留在 query 里，body 里的 task_common_data 照旧。
            if "locationInfos" in data or api == API["callback"]:
                query["locationInfos"] = json.dumps([{"lng": self.lng, "lat": self.lat}],
                                                    separators=(",", ":"))
            url = f"{self.gateway}{api}/{version}/5.0/?" + urlencode(query)

            if DRYRUN:
                self._log(f"[dryrun] {api} v{version}")
                self._log("         token/sign 已隐藏")
                self._log(f"         data={payload[:200]}")
                return {"ret": ["SUCCESS::dryrun"], "data": {"data": {}}}

            headers = dict(self.hdr)
            headers["Cookie"] = self.cookie
            if STEALTH:                     # 链路追踪 id 必须每次不同，否则一眼假
                headers["x-eleme-requestid"] = (
                    hashlib.sha1(os.urandom(16)).hexdigest().upper() + "|" + t)
            headers.update(extra_headers or {})
            try:
                resp = self.session.post(url, headers=headers, timeout=15, verify=True,
                                         proxies=self.proxies)
            except Exception as exc:                            # noqa: BLE001
                if attempt == retry - 1:
                    raise MtopError("网络请求失败，请检查网络/证书/代理配置") from None
                time.sleep(1.5 * (attempt + 1))
                continue

            refreshed = self._update_cookie(self._grab_set_cookie(resp))
            try:
                js = resp.json()
            except Exception:                                   # noqa: BLE001
                raise MtopError("服务端响应非JSON，可能需要在官方页面完成用户验证") from None
            if not isinstance(js, dict):
                raise MtopError("服务端响应结构异常")
            ret = js.get("ret")
            ret0 = ret[0] if isinstance(ret, list) and ret else ""
            if re.search(r"TOKEN_EXOIRED|TOKEN_EXPIRED|TOKEN_EMPTY", str(ret0), re.I):
                if refreshed:
                    time.sleep(0.8)
                    continue                                    # token 已刷新，重试
                raise MtopError(f"token 失效且未回传新 token：{ret0}")
            self.parse_ret(js)  # 在请求层识别验证错误，所有调用路径立即受保护
            return js
        raise MtopError("请求重试次数耗尽")

    def parse_ret(self, res: dict) -> dict:
        """识别用户验证/登录失效/风险；命中即中止当前账号，不尝试绕过验证。"""
        if not isinstance(res, dict) or not res:
            return {"ok": False, "msg": "空响应或响应格式错误", "data": {}}
        ret = res.get("ret")
        ret0 = ret[0] if isinstance(ret, list) and ret else str(ret or "")
        raw = res.get("data") or {}
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        out = {"ok": str(ret0).startswith("SUCCESS"), "msg": str(ret0), "data": data}
        if RISK_RE.search(str(ret0)):
            first = not self.risk_flagged
            self.risk_flagged = True
            self.stop_reason = str(ret0).split("::", 1)[0]
            if "USER_VALIDATE" in self.stop_reason:
                message = "⛔ 需要用户验证（FAIL_SYS_USER_VALIDATE）：请在官方页面完成登录/验证后重新获取完整Cookie"
            elif re.search(r"SESSION|NOT_LOGIN|LOGIN_REQUIRED|TOKEN_ILLEGAL", self.stop_reason):
                message = "⛔ 登录已失效，请在官方页面重新登录后更新完整Cookie"
            else:
                message = "⛔ 账号被平台限制，请在官方页面确认账号状态，本轮停止请求"
            self.result["Cookie"] = message
            if first:
                self._log(message)
                self._log("已停止本账号后续请求；不会继续签到、抽奖、任务或兑换")
        return out

    # ---------- 业务 ----------
    def homepage(self) -> dict | None:
        self.lat, self.lng = jitter(self.lat, self.lng)
        data = {
            "bizScene": BIZ_SCENE,
            "version": PAGE_VERSION,
            "latitude": self.lat,
            "longitude": self.lng,
            "cpnCodes": json.dumps(CPN_CODES, separators=(",", ":")),
        }
        r = self.parse_ret(self.mtop(API["homepage"], "1.0", data))
        if not r["ok"]:
            self._log(f"❌ 首页聚合失败：{r['msg']}")
            return None
        d = r["data"] or {}
        # missionCollectionId: resource.data.INTERACT_RESOURCE_CPN[0].missionCollectionId
        cpns = ((d.get("resource") or {}).get("data") or {}).get("INTERACT_RESOURCE_CPN")
        first = cpns[0] if isinstance(cpns, list) and cpns else {}
        mission_collection_id = (first or {}).get("missionCollectionId", "")
        # 签到 copyId: signIn.data.signInQueryPrizeDTOS[today].extInfo.copyId
        signin_data = (d.get("signIn") or {}).get("data") or {}
        dtos = signin_data.get("signInQueryPrizeDTOS") or []
        today = next((x for x in dtos if isinstance(x, dict) and x.get("today")), {}) or {}
        copy_id = (((today.get("extInfo") or {}).get("copyId"))
                   or ((signin_data.get("extInfo") or {}).get("copyId")) or "")
        strategy = signin_data.get("signInRewardStrategy", "")
        # 今日奖励参数（actCode / sceneCode / prizeMark / signInDay）—— 签到接口必传，
        # 缺了会报 FAIL_BIZ_REWARD_MODE_NOT_SIGNIN_AND_RECEIVE
        awards = ((today.get("extInfo") or {}).get("stageAward")) or []
        award = awards[0] if awards else {}
        signed_today = bool(today.get("signIn"))
        if DEBUG:
            self._log(f"[调试] homepage signIn={json.dumps(d.get('signIn'), ensure_ascii=False)[:600]}")
        if mission_collection_id:
            self._log("✅ 首页聚合成功")
        else:
            self._log(f"⚠ 首页成功但无 missionCollectionId，业务字段：{list(d.keys())}")
        lotteries = ((d.get("resource") or {}).get("data") or {}).get("INTERACT_CENTER_LOTTERY") or []
        return {"missionCollectionId": mission_collection_id, "copyId": copy_id,
                "signInRewardStrategy": strategy, "award": award,
                "signedToday": signed_today, "lotteries": lotteries,
                "orderFree": d.get("orderFreeDraw"),
                "star": self._star_from(d)}

    @staticmethod
    def _star_from(d: dict) -> int | None:
        """
        从首页聚合 data 里取幸运星余额：
          主取 data.property.data.STAR.amount（实测 5600）
          兜底 data.exchangeV2.data.propertyInfo.remainAmount（兑换商城带的同值）
        """
        v = (((d.get("property") or {}).get("data") or {}).get("STAR") or {}).get("amount")
        if v in (None, ""):
            v = (((d.get("exchangeV2") or {}).get("data") or {})
                 .get("propertyInfo") or {}).get("remainAmount")
        try:
            return int(float(v)) if v not in (None, "") else None
        except Exception:                                   # noqa: BLE001
            return None

    def refresh_star(self) -> int | None:
        """重新拉一次首页聚合取余额（只读，不触发任何写行为）；失败返回 None。"""
        try:
            r = self.parse_ret(self.mtop(API["homepage"], "1.0", {
                "bizScene": BIZ_SCENE, "version": PAGE_VERSION,
                "latitude": self.lat, "longitude": self.lng,
                "cpnCodes": json.dumps(CPN_CODES, separators=(",", ":")),
            }))
        except Exception:                                   # noqa: BLE001
            return None
        return self._star_from(r.get("data") or {}) if r.get("ok") else None

    def signin(self, copy_id: str = "", strategy: str = "",
               award: dict | None = None, signed_today: bool = False) -> None:
        if self.risk_flagged:
            return
        if cfg("ELM_SKIP_SIGNIN") == "1":
            self.result["签到"] = "➖ 已跳过"
            return
        if signed_today:
            self.result["签到"] = "✅ 今日已签"
            return
        award = award or {}
        data = {"bizScene": BIZ_SCENE, "latitude": self.lat, "longitude": self.lng}
        if copy_id:
            data["copyId"] = copy_id
        # ★ 实测必传：今日奖励的 actCode / sceneCode / prizeMark / signInDay，
        #   缺任意一个都会 FAIL_BIZ_REWARD_MODE_NOT_SIGNIN_AND_RECEIVE
        for src, dst in (("actCode", "actCode"), ("sceneCode", "sceneCode"),
                         ("prizeMark", "prizeMark"), ("signInDay", "signInDay")):
            if award.get(src) not in (None, ""):
                data[dst] = award[src]
        order = ((API["signin"], API["recordsignin"]) if strategy == "SIGNIN_AND_RECEIVE"
                 else (API["recordsignin"], API["signin"]))
        r = {"ok": False, "msg": "未执行"}
        mode = ""
        for api in order:
            if self.risk_flagged:
                break
            try:
                r = self.parse_ret(self.mtop(api, "1.0", data, {"asac": ASAC["SIGNIN"]}))
            except Exception as exc:                            # noqa: BLE001
                r = {"ok": False, "msg": str(exc)}
            if r["ok"]:
                mode = "签到并领奖" if api == API["signin"] else "独立签到"
                break
            if re.search(r"已签到|REPEAT|ALREADY", str(r.get("msg", "")), re.I):
                break
        if not r["ok"]:
            msg = str(r.get("msg", ""))
            self.result["签到"] = ("✅ 今日已签" if re.search(r"已签到|REPEAT|ALREADY", msg, re.I)
                              else f"❌ {msg[:60]}")
            return
        if r["ok"]:
            rewards = (r["data"] or {}).get("rewards") or []
            total = sum(int(float(x.get("prizeValue") or x.get("equityValue") or 0))
                        for x in rewards if isinstance(x, dict))
            ptype = "幸运星" if (rewards and rewards[0].get("prizeType") == "STAR") else (
                (rewards[0].get("prizeType") if rewards else "") or "奖励")
            self.result["签到"] = f"✅ +{total} {ptype}（{mode}）"

    # ---------- 公共参数（前端 Kj()/定位 的等价物） ----------
    def _kj(self) -> dict:
        return {"bizScene": BIZ_SCENE, "version": PAGE_VERSION}

    def _loc(self) -> dict:
        return {"latitude": self.lat, "longitude": self.lng}

    def wallet(self) -> None:
        """幸运星资产：余额（首页 property）+ 今日收益（明细 querypropertydetail）。"""
        # 余额：此时今日收益已入账，重新拉一次首页拿最新值
        end = self.refresh_star()
        if end is not None:
            self.star_end = end
        try:
            r = self.parse_ret(self.mtop(API["property_detail"], "1.0", {
                **self._kj(), **self._loc(), "convertType": "INTERACT_CENTER",
                "pageNo": 1, "pageSize": 20}))
        except Exception as exc:                            # noqa: BLE001
            self.result["资产"] = f"❌ {str(exc)[:40]}"
            return
        if not r["ok"]:
            self.result["资产"] = f"❌ {str(r.get('msg'))[:40]}"
            return
        d = r["data"] or {}
        today = datetime.now().strftime("%Y-%m-%d")
        got = 0
        for it in (d.get("list") or []):
            if str(it.get("gmtCreateStr", "")).startswith(today) and it.get("detailType") == "GRANT":
                try:
                    got += int(float(it.get("amount") or 0))
                except Exception:                           # noqa: BLE001
                    pass
        self.result["资产"] = f"今日 +{got}"

    def mall(self, read_only: bool = False) -> None:
        """分页列商品；只读模式不兑换，自动兑换必须指定商品 ID 或完整名称。"""
        if self.risk_flagged:
            self.result["兑换"] = "⛔ 已有账号风险标记，跳过商城"
            return
        items, seen = [], set()
        for page in range(1, 21):
            try:
                r = self.parse_ret(self.mtop(API["mall_list"], "1.0", {
                    **self._kj(), **self._loc(), "propertyBizType": "STAR",
                    "pageNo": page, "pageSize": 10}))
            except Exception:
                self.result["兑换"] = "❌ 商城查询异常，未发起兑换"
                self._log(self.result["兑换"])
                return
            if not r["ok"] or self.risk_flagged:
                self.result["兑换"] = "❌ 商城查询失败：" + str(r.get("msg", ""))[:80]
                self._log(self.result["兑换"])
                return
            batch = (r["data"] or {}).get("list") or []
            if not isinstance(batch, list):
                self.result["兑换"] = "❌ 商品列表格式不符，未发起兑换"
                return
            added = 0
            for it in batch:
                if not isinstance(it, dict):
                    continue
                ident = str(it.get("exchangeId") or "")
                key = ident or json.dumps(it, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
                added += 1
            if len(batch) < 10 or not added:
                break
        else:
            self._log("⚠ 已查询至第20页，后续可能还有商品；本次仅展示，不兑换")
            read_only = True

        def normalize(value) -> str:
            # 完整名称匹配，仅忽略空白、英文字母大小写和全角字符差别。
            import unicodedata
            return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()

        def number(value):
            from decimal import Decimal, InvalidOperation
            try:
                value = Decimal(str(value))
                return value if value.is_finite() and value >= 0 else None
            except (InvalidOperation, TypeError, ValueError):
                return None

        rows = []
        self._log(f"🛒 商城商品清单：{len(items)} 件")
        for it in items:
            ex = it.get("exchangeInfo") or {}
            name = ((it.get("materialInfo") or {}).get("title")
                    or (it.get("rightMaterialInfoDTO") or {}).get("title")
                    or (it.get("bizExtInfo") or {}).get("exchangeIntroduction")
                    or "未命名商品")
            ident = str(it.get("exchangeId") or "").strip()
            cost = ex.get("consumeAmount")
            gap = number(ex.get("gapAmount", 0))
            status = str(ex.get("exchangeStatus") or "")
            ready = (status in ("INIT", "EXCHANGE_ABLE", "CAN_EXCHANGE", "ONLINE", "AVAILABLE")
                     and gap == 0 and number(cost) is not None and bool(ident))
            self._log(f"  名称={name} | ID={ident or '缺失'} | "
                      f"所需星={cost if cost is not None else '未知'} | "
                      f"状态={status or '未知'} | 差星={ex.get('gapAmount', 0)}")
            rows.append((str(name), ident, cost, ready, it))
        if read_only:
            self.result["兑换"] = f"➖ 仅查询，列出 {len(rows)} 件商品，未兑换"
            self._log(self.result["兑换"])
            return
        if cfg("ELM_EXCHANGE") != "1":
            self.result["兑换"] = f"➖ 已列出 {len(rows)} 件商品（自动兑换未开启）"
            return
        target_id = cfg("ELM_EXCHANGE_ID")
        target_name = cfg("ELM_EXCHANGE_NAME")
        if not target_id and not target_name:
            self.result["兑换"] = "⛔ 未指定 ELM_EXCHANGE_ID 或 ELM_EXCHANGE_NAME，跳过兑换"
            self._log(self.result["兑换"])
            return
        matches = [x for x in rows
                   if (not target_id or x[1] == target_id)
                   and (not target_name or normalize(x[0]) == normalize(target_name))]
        if len(matches) != 1:
            self.result["兑换"] = ("➖ 未找到指定商品，未兑换其他商品" if not matches
                                   else "⛔ 商品名称匹配多件，请指定 ELM_EXCHANGE_ID")
            self._log(self.result["兑换"])
            return
        name, ident, cost, ready, it = matches[0]
        if not ready:
            self.result["兑换"] = f"➖ {name} 当前不可兑换，未兑换其他商品"
            return
        try:
            limit = int(cfg("ELM_EXCHANGE_MAX", "1"))
        except ValueError:
            limit = 0
        if limit <= 0:
            self.result["兑换"] = "➖ ELM_EXCHANGE_MAX 未设为正整数，跳过兑换"
            return
        if self.risk_flagged:
            self.result["兑换"] = "⛔ 账号风险标记，跳过兑换"
            return
        required = ("exchangeActId", "exchangeCollectionId", "exchangeId", "actCode")
        if any(it.get(key) in (None, "") for key in required):
            self.result["兑换"] = "⛔ 目标商品缺少兑换参数，跳过兑换"
            return
        if DRYRUN:
            self.result["兑换"] = f"➖ [dryrun] 目标={name}，ID={ident}，未兑换"
            return
        try:
            rr = self.parse_ret(self.mtop(API["mall_exchange"], "1.0", {
                **self._kj(), **self._loc(), "actId": it["exchangeActId"],
                "exchangeCollectionId": it["exchangeCollectionId"],
                "exchangeId": it["exchangeId"], "actCode": it["actCode"],
                "propertyBizType": "STAR",
            }, {"asac": ASAC["MALL_EXCHANGE"]}, retry=1))
        except Exception:
            self.result["兑换"] = "⚠ 兑换响应异常，结果未知，请到官方页面核实后再运行"
            self._log(self.result["兑换"])
            return
        if rr["ok"]:
            self.result["兑换"] = f"✅ 已兑换：{name}（-{cost} 星），ID={ident}"
        else:
            self.result["兑换"] = "❌ 兑换未成功：" + str(rr.get("msg", ""))[:80]
        self._log(self.result["兑换"])

    def orderfree_draw(self, order_free: dict) -> None:
        """下单免单抽奖（需 cityId，通常要真下单才有机会；失败静默）。"""
        if self.risk_flagged or not order_free:
            return
        if cfg("ELM_SKIP_ORDERFREE") == "1":
            return
        data = (order_free or {}).get("data") or {}
        city_id = ((data.get("extInfo") or {}).get("cityId")
                   or cfg("ELM_CITY_ID", ""))
        payload = {**self._kj(), "bizScene": "order_free_draw",
                   "longitude": str(self.lng), "latitude": str(self.lat)}
        if city_id:
            payload["cityId"] = city_id
        try:
            r = self.parse_ret(self.mtop(API["orderfree_draw"], "1.0", payload,
                                         {"asac": ASAC["ORDER_FREE_DRAW"]}))
        except Exception:                                   # noqa: BLE001
            return
        if r["ok"]:
            self.result["免单抽"] = "✅ " + (self.lottery_prize(r["data"]) or "已抽")
            self._log(f"🎟 下单免单抽奖：{self.result['免单抽']}")
        elif DEBUG:
            self._log(f"🎟 下单免单抽奖未成：{str(r.get('msg'))[:60]}")

    def quiz(self) -> None:
        """
        趣味话题答题：actId 不在首页返回里（在答题页 URL 上），
        需用 ELM_QUIZ_ACTID 显式传入才执行。
        """
        if self.risk_flagged:
            return
        act = cfg("ELM_QUIZ_ACTID")
        if not act:
            return
        src = cfg("ELM_QUIZ_SOURCE", "INTERACT_CENTER")
        try:
            r = self.parse_ret(self.mtop(API["quiz_query"], "1.0", {
                "actId": act, "bizScene": src, "source": src, **self._loc()}))
            if not r["ok"]:
                self._log(f"❓ 答题取题失败：{str(r.get('msg'))[:50]}")
                return
            d = r["data"] or {}
            q = d.get("questionDTO") or (d.get("questionList") or [{}])[0]
            copy_id = d.get("copyId") or ""
            answered, correct = d.get("answeredCount", 0), d.get("correctCount", 0)
            self._log(f"❓ 答题 已答{answered}/对{correct}")
            qid = q.get("questionId")
            opts = q.get("optionList") or q.get("options") or []
            if not qid or not opts:
                self.result["答题"] = "➖ 今日无题"
                return
            oid = (opts[0] or {}).get("optionId") or (opts[0] or {}).get("id")
            rs = self.parse_ret(self.mtop(API["quiz_submit"], "1.0", {
                "actId": act, "bizScene": src, "source": src, "copyId": copy_id,
                "questionId": qid, "optionId": oid, **self._loc()}))
            if not rs["ok"]:
                self.result["答题"] = f"➖ {str(rs.get('msg'))[:30]}"
                return
            ra = self.parse_ret(self.mtop(API["quiz_award"], "1.0", {
                "actId": act, "bizScene": src, "source": src, "copyId": copy_id,
                **self._loc()}, {"asac": ASAC["QUIZ_AWARD"]}))
            self.result["答题"] = ("🎁 " + self.lottery_prize(ra["data"]) if ra["ok"]
                              else f"➖ {str(ra.get('msg'))[:30]}")
            self._log(f"❓ 答题结果：{self.result['答题']}")
        except Exception as exc:                            # noqa: BLE001
            self._log(f"❓ 答题异常：{str(exc)[:50]}")

    def draw_lottery(self, lotteries: list) -> None:
        """
        夺宝/抽奖（mtop.alsc.interact.playapp.reward.lottery.draw）。
        入参与风控头 asac 都逆向自 engage-hub 1.4.2 前端包；
        每个组件默认抽 ELM_DRAW_TIMES 次（默认 1），未中奖/无机会即停。
        """
        if cfg("ELM_SKIP_DRAW") == "1":
            self.result["夺宝"] = "➖ 已跳过"
            return
        if not lotteries:
            self.result["夺宝"] = "➖ 本次无夺宝入口"
            return
        times = max(1, int(cfg("ELM_DRAW_TIMES", "1") or 1))
        wins, notes = 0, []
        for c in lotteries:
            act_id = (c or {}).get("actId")
            comp_id = (c or {}).get("componentId")
            if not act_id or not comp_id:
                continue
            for k in range(times):
                if self.risk_flagged:
                    break
                data = {
                    "actId": act_id,
                    "componentId": comp_id,
                    "bizScene": "INTERACT_CENTER_LOT",
                    "bizCode": "INTERACT_CENTER_LOT",
                    "longitude": str(self.lng),
                    "latitude": str(self.lat),
                    "extParams": json.dumps({"desc": "幸运福利"}, ensure_ascii=False),
                }
                try:
                    r = self.parse_ret(self.mtop(API["lottery_draw"], "1.0", data,
                                                 {"asac": ASAC["LOTTERY_DRAW"]}))
                except Exception as exc:                    # noqa: BLE001
                    notes.append(f"异常{str(exc)[:30]}")
                    break
                if r["ok"]:
                    prize = self.lottery_prize(r["data"])
                    wins += 1
                    notes.append(prize)
                    self._log(f"🎰 夺宝中奖：{prize}")
                    nap_human(1.4)
                    continue
                msg = str(r.get("msg", ""))
                if re.search(r"NO_ENOUGH_REMAIN|CHANCE|已抽完|次数|日剩", msg, re.I):
                    self._log(f"🎰 夺宝 次数已用完（{msg.split('::')[-1][:20]}）")
                    notes.append("今日次数已用完")
                elif re.search(r"未中奖|NO_DECISION_STRATEGY|NOT_WINNING", msg, re.I):
                    self._log("🎰 夺宝 未中奖")
                    notes.append("未中奖")
                else:
                    self._log(f"🎰 夺宝 {msg[:40]}")
                    notes.append(msg[:40])
                break
        if wins:
            self.result["夺宝"] = f"✅ 中 {wins} 次：{'、'.join(notes[:4])}"
        elif notes:
            self.result["夺宝"] = f"➖ {notes[0]}"
        else:
            self.result["夺宝"] = "➖ 无机会"

    @staticmethod
    def lottery_prize(data) -> str:
        """从抽奖返回里提炼奖励描述（幸运星/券/红包）。"""
        items = data if isinstance(data, list) else [data]
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            dis = it.get("discountInfo") or {}
            info = it.get("materialInfo") or it.get("rightMaterialInfoDTO") or {}
            amount = dis.get("reductionYuan") or dis.get("reduction") or dis.get("amount")
            btype = str(dis.get("benefitType") or it.get("prizeType") or "")
            name = (info.get("title") if isinstance(info, dict) else "") or \
                {"VIRTUAL_INTEGRAL": "幸运星", "STAR": "幸运星", "COUPON": "优惠券",
                 "RED_PACKET": "红包"}.get(btype, btype or "奖励")
            if dis.get("benefitType") == "VIRTUAL_INTEGRAL":
                out.append(f"+{amount or ''} 幸运星".replace(" 幸运星", " 幸运星")
                           + (f"（{dis.get('reductionYuan')}元）" if dis.get("reductionYuan") else ""))
            elif name and amount and str(amount) not in str(name):
                out.append(f"{name} +{amount}")
            else:
                out.append(str(name))
        return "、".join(x for x in out if x) or "奖励"

    def query_tasks(self, mission_collection_id: str, biz_scene: str | None = None, mission_ids: list[str] | None = None) -> list[dict]:
        def _is_h5() -> bool:
            # 1.5.47：读本实例标志，不读全局环境（多线程下全局值会被别的
            # 线程的退回改掉）。
            return (biz_scene or BIZ_SCENE) == BIZ_SCENE and self._task_h5

        def _build() -> dict:
            # 按身份选 launchChannels：H5 身份用数组 []（官方 H5 格式），
            # Android 身份用字符串 "[]"（2602/ETMD 只认字符串，数组会报缺参数）。
            d = {**self.task_common_data(biz_scene),
                 "missionCollectionId": mission_collection_id,
                 "launchChannels": [] if _is_h5() else "[]"}
            if mission_ids:
                d["missionIds"] = mission_ids
            return d
        data = _build()
        r = self.parse_ret(self.mtop(API["querytask"], "1.0", data))
        if not r["ok"] and "FAIL_SYS_BIZPARAM_MISSED" in str(r.get("msg")) and _is_h5():
            # H5 身份被服务端拒绝：当前 Cookie 是 Android 会话，身份与会话
            # 不匹配。退回 Android 身份 + 字符串 launchChannels 再试一次，
            # 至少按 1.5.7 的 23 项跑，不整轮失败。
            # 1.5.47：只改本实例的粘性标志，不碰 os.environ 全局变量——
            # 1.5.46 改全局变量时，别的线程正好在判断重试或拼请求，会读到
            # 被改掉的值，导致重试被跳过（2026-11-22 10:04 那轮 7 个号中招）。
            # 标志一旦置 False，本轮余下查询直接走 Android。
            self._log("⚠ H5 身份被服务端拒绝（当前 Cookie 疑似 Android 会话），退回 Android 身份重试")
            self._task_h5 = False
            data = _build()
            r = self.parse_ret(self.mtop(API["querytask"], "1.0", data))
        if not r["ok"]:
            self._log(f"❌ 查询任务失败：{r['msg']}")
            if "ALADDIN_DEVICEINFO" in str(r.get("msg")):
                self._log("请核对非零定位、签名data中的platform/product/prodVersion/ttid及locationInfos字符串数组；该错误不等于Cookie过期")
            raise MtopError("任务查询未成功，停止本轮任务")
        lst = (r["data"] or {}).get("mlist")
        if not isinstance(lst, list):
            self._log("⚠ 查询响应缺少任务列表，停止本轮任务")
            raise MtopError("任务列表结构不符")
        if not all(isinstance(task, dict) for task in lst):
            raise MtopError("任务列表存在不支持的条目，停止本轮任务")
        return lst

    @staticmethod
    def merge_known_h5_tasks(tasks: list[dict], overrides: dict | None = None) -> list[dict]:
        """把 HAR 里已知的 H5 任务并进 querytask 返回的清单。

        Android 会话的 querytask 只回 23 项，缺 H5 才看得到的 18 项；
        这里按 missionDefId 去重补上，让执行器也尝试它们（PAGEVIEW 用
        pageview、THIRD 用 trigger）。服务端仍可能按身份/付费投放拦
        trigger（417），成败以重新查询和领奖结果为准。
        overrides：missionDefId -> 已更新的任务副本（阶段已推进），
        用于上报被接受后、重新查询又看不到这些任务时，把进度带回来。"""
        seen = set()
        for t in tasks:
            did = str(t.get("missionDefId") or t.get("missionId") or "")
            if did:
                seen.add(did)
        added = []
        for t in KNOWN_H5_TASKS:
            did = str(t.get("missionDefId"))
            if did in seen:
                continue
            if overrides and did in overrides:
                added.append(overrides[did])
            else:
                added.append(t)
        # 1.5.50：ELM_EXTRA_H5_TASKS 里人工补的 H5 新任务 ID 也并进来，
        # 与 KNOWN 同等待遇（重新查询里看不到时靠 known_overrides 带进度、
        # 领奖时按已知任务走），不用等改代码发版。
        for t in ElmAccount.extra_h5_tasks():
            did = str(t.get("missionDefId"))
            if did in seen:
                continue
            seen.add(did)
            if overrides and did in overrides:
                added.append(overrides[did])
            else:
                added.append(t)
        return list(tasks) + added if added else list(tasks)

    @staticmethod
    def extra_h5_tasks() -> list[dict]:
        """ELM_EXTRA_H5_TASKS：青龙变量，逗号分隔的 H5 新任务 ID。

        Android querytask 不回 H5 才看得到的任务（如“玩薅羊毛领奖励”），
        脚本也就永远看不到它，只能靠人工把 ID 写进来。格式：missionDefId，
        或 missionDefId:pageSpm（自定义上报页面），多个用逗号分隔。
        默认按 PAGEVIEW / 3112 / pageSpm=engage-hub 本页 /
        finalStageCount=1 构造；做没做以服务端重新查询和领奖结果为准。
        空变量返回 []，不影响原有流程。"""
        raw = cfg("ELM_EXTRA_H5_TASKS", "")
        out, seen = [], set()
        for part in re.split(r"[,\n\uff0c]+", raw or ""):
            part = (part or "").strip()
            if not part:
                continue
            did, _, spm = part.partition(":")
            did = did.strip()
            spm = spm.strip() or DEFAULT_PAGESPM
            if not re.fullmatch(r"\d{5,}", did) or did in seen:
                continue
            seen.add(did)
            out.append({
                "missionCollectionId": "3112", "missionDefId": did,
                "showTitle": f"新任务{did}",
                "actionConfig": {"actionType": "PAGEVIEW",
                                 "actionValue": {"pageSpm": spm, "pageScene": "COMMON", "finalStageCount": 1}},
                "missionStageDTOS": [{"stageCount": 1, "status": "RUNNING", "rewardStatus": "TODO",
                                      "sourceAction": {"actionSubtype": "REWARD_UPP", "actionSubSubType": "REWARD_SEND_MANUAL"}}],
                "receiveStatus": "TORECEIVE",
            })
        return out

    @staticmethod
    def _known_task_copy(mission_def_id: str) -> dict | None:
        """取 KNOWN_H5_TASKS 里某任务的深拷贝（改阶段时不污染原表）。"""
        for t in KNOWN_H5_TASKS:
            if str(t.get("missionDefId")) == str(mission_def_id):
                return json.loads(json.dumps(t, ensure_ascii=False))
        for t in ElmAccount.extra_h5_tasks():
            if str(t.get("missionDefId")) == str(mission_def_id):
                return json.loads(json.dumps(t, ensure_ascii=False))
        return None

    @staticmethod
    def _mark_stage(task: dict, status: str, reward_status: str | None = None) -> bool:
        """把任务第一个未完成阶段标成给定状态；返回是否标到了。"""
        for st in (task.get("missionStageDTOS") or []):
            if isinstance(st, dict) and st.get("status") != "FINISH":
                st["status"] = status
                if reward_status is not None:
                    st["rewardStatus"] = reward_status
                return True
        return False

    # ---- 任务分类、诊断和已有接口适配 ----
    @staticmethod
    def _task_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                pass
        return {}

    @classmethod
    def _task_parts(cls, task: dict) -> tuple[dict, dict]:
        task = cls._task_dict(task)
        action = cls._task_dict(task.get("actionConfig"))
        return action, cls._task_dict(action.get("actionValue"))

    @classmethod
    def task_ext_value(cls, task: dict, key: str):
        extra = cls._task_dict(cls._task_dict(task.get("ext")).get("extValue"))
        value = extra.get(key)
        return value.get("value") if isinstance(value, dict) and "value" in value else value

    @classmethod
    def task_state(cls, task: dict) -> str:
        """依据官方1.4.27普通任务阶段；只在有明确字段时作判断。"""
        if task.get("receiveType") == "RECEIVE" and task.get("receiveStatus") == "TORECEIVE":
            return "take"
        stages = task.get("missionStageDTOS")
        if isinstance(stages, list) and stages:
            if cls.reward_stage_index(task) >= 0:
                return "reward"
            if any(isinstance(s, dict) and s.get("status") == "RUNNING" for s in stages):
                if cls._task_dict(task.get("extInfo")).get("todayDone"):
                    return "cyclefinish"
                return "todo"
            if all(isinstance(s, dict) and s.get("status") == "FINISH" for s in stages):
                return "finish"
        if task.get("status") == "FINISH":
            return "finish"
        return "unknown"

    def task_common_data(self, biz_scene: str | None = None) -> dict:
        """有效定位及设备信息放进签名业务data，保持locationInfos内层为JSON字符串。"""
        try:
            lat, lng = float(self.lat), float(self.lng)
            if not all(math.isfinite(v) for v in (lat, lng)) or not (-90 <= lat <= 90 and -180 <= lng <= 180) or lat == 0 or lng == 0:
                raise ValueError
        except (TypeError, ValueError):
            raise MtopError("任务查询需要有效非零定位；请用实际所在地区的ELM_LAT/ELM_LNG，不能填0、0") from None
        # 3112（interact_center）默认用官方 H5 身份：2026-11-17 实测官方页面
        # querytask 即用 platform=ios、prodVersion=1.4.27、ttid=H5@Web_iphone_1.4.27；
        # 旧版 Android 身份只能查到 23/24 项，缺 13 项。天天免单（ETMD_CHANNEL）
        # 保持 Android 身份（2602 已能正常查到）。ELM_TASK_H5=0 恢复旧版 Android。
        # 1.5.47：读本实例粘性标志（见 __init__）；ELM_TASK_H5 只在实例
        # 创建时读一次做初值，不再每次调用都读全局环境。
        if (biz_scene or BIZ_SCENE) == BIZ_SCENE and self._task_h5:
            platform, version, ttid = H5_TASK_PLATFORM, PAGE_VERSION, H5_TASK_TTID
        else:
            platform, version = "android", self.app_version
            ttid = f"android@eleme_android_{version}"
        loc = json.dumps({"lng": lng, "lat": lat}, separators=(",", ":"))
        return {"bizScene": biz_scene or BIZ_SCENE, "accountPlan": ACCOUNT_PLAN,
                "longitude": lng, "latitude": lat,
                "locationInfos": json.dumps([loc], separators=(",", ":")),
                "platform": platform, "product": "eleme", "prodVersion": version,
                "ttid": ttid}

    def take_task(self, task: dict, biz_scene: str | None = None) -> dict:
        """仅接取服务端明确标记RECEIVE/TORECEIVE且已有支持处理器的任务。"""
        mode, reason = self.task_plan(task)
        if mode != "take":
            return {"ok": False, "skip": True, "msg": reason}
        data = {**self.task_common_data(biz_scene),
                "missionCollectionId": task.get("missionCollectionId") or task.get("missionCollectId"),
                "missionId": task.get("missionDefId") or task.get("missionId"),
                "missionXId": task.get("missionXId") or ""}
        return self.parse_ret(self.mtop(API["receivetask"], "1.0", data, retry=1))

    def export_tasks(self, tasks: list[dict], collection_id, account_index: int, output_dir):
        # Cookie只用于本机脱敏匹配，不输出、不写入报告。
        secrets = [part.partition("=")[2].strip() for part in self.cookie.split(";")
                   if "=" in part and len(part.partition("=")[2].strip()) >= 6]
        report = make_task_snapshot(tasks, 'qinglong-current-channel', account_index,
                                    collection_id, secrets, ['SUCCESS'])
        return write_task_snapshot(report, output_dir)

    @classmethod
    def task_title(cls, task: dict) -> str:
        task = cls._task_dict(task)
        extra = cls._task_dict(cls._task_dict(task.get("ext")).get("extValue"))
        pinned = cls._task_dict(extra.get("pinnedTaskTitle"))
        return str(pinned.get("value") or task.get("missionName") or task.get("showTitle")
                   or task.get("taskName") or task.get("name") or task.get("missionDefId") or "未知任务")

    @classmethod
    def task_category(cls, task: dict) -> str:
        """任务类型优先于名称；名称仅供分类，不证明执行所需的授权。"""
        task = cls._task_dict(task)
        action, value = cls._task_parts(task)
        kind = str(action.get("actionType") or "").upper()
        title = re.sub(r"\s+", "", cls.task_title(task))
        if cls.task_ext_value(task, "alipayRewardAdSpaceCode"):
            return "alipay_ad"
        if kind in ("ORDER", "PAY", "PURCHASE") or any(x in title for x in ("下单", "实付", "订单")):
            return "order"
        if kind in ("P2P", "INVITE", "SHARE") or any(x in title for x in ("邀请", "助力", "好友")):
            return "invite"
        if any(x in title for x in ("闲鱼", "看视频", "玩赚现金", "现金红包", "动动手指", "领优惠", "搜一搜", "积分兑好礼")):
            return "external"
        if any(x in title for x in ("浏览页面", "闪购品牌日", "外卖品质好店")):
            return "browse"
        channel = " ".join(str(task.get(k) or "") for k in ("source", "channel", "taskScene"))
        links = " ".join(str(value.get(k) or "") for k in ("url", "jumpUrl", "targetUrl", "link"))
        if re.search(r"支付宝|闲鱼|alipays?://|alipay\.com|goofish|xianyu", channel + " " + links, re.I):
            return "external"
        return "browse" if kind == "PAGEVIEW" else ("third" if kind == "THIRD" else "other")

    @classmethod
    def task_plan(cls, task: dict) -> tuple[str, str]:
        task = cls._task_dict(task)
        action, value = cls._task_parts(task)
        category = cls.task_category(task)
        if category == "alipay_ad":
            return "skip", "支付宝激励广告需my.createRewardedAd播放及onClose(isEnded=true)，不能用普通浏览上报代替"
        # Fix 1.5.4：入口路径任务（isEntryPathTask=true）不再一律跳过。
        # v1.5.3 这条 blanket skip 把“从闪购首页来访”等 PAGEVIEW 任务也拦了，
        # 但上报时本来就带 pageSpm（就是入口路径），且 process_tasks 会在上报后
        # 重新查询确认进度、没变化就停手不盲目补跑，所以交给服务端裁决更划算。
        if category == "order":
            return "skip", "需要真实下单/支付，不自动执行"
        if category == "invite":
            return "skip", "需要真实邀请/好友助力，不自动执行"
        if category == "external" and cfg("ELM_EXTERNAL_PAGEVIEW", "1") != "1":
            return "skip", "外部任务通用上报已关闭（ELM_EXTERNAL_PAGEVIEW=0）"
        state = cls.task_state(task)
        if state in ("finish", "cyclefinish"):
            return "skip", "任务/今日阶段已完成，不再上报"
        if state == "reward":
            return "skip", "已有完成阶段，等待领奖，不再上报"
        kind = str(action.get("actionType") or "").upper()
        collection = task.get("missionCollectionId") or task.get("missionCollectId")
        mission = task.get("missionId") or task.get("missionDefId")
        if kind == "PAGEVIEW":
            if not collection or not mission:
                return "skip", "PAGEVIEW缺collectionId/missionId，不能上报"
            # 1.5.50：新任务的 actionValue 可能缺 pageSpm（如 H5 新任务），
            # 不直接跳过——report_pageview 默认用 engage-hub 本页
            # a2ogi.bx1500380 上报（50 个已知 PAGEVIEW 全用它），服务端以
            # 重新查询裁决（没进度就停手，不盲目补跑）。
            if state == "take":
                return "take", "需先接取任务，刷新实例后再执行"
            return "pageview", "可使用现有PAGEVIEW接口；以重新查询和领奖结果为准"
        # Fix 1.5.4：THIRD 不再只认 commercial_click / inter_change 两个 scene。
        # trigger 上报根本不发 scene 值（只发 missionDefId/collectionId/event），
        # 2602 天天免单那批任务的 scene 未知，旧 gate 会把大部分直接判“未适配”。
        # 有 collection+mission id 就尝试上报，成败由重新查询确认。
        # 例外：累计奖励是里程碑（今日累计任务奖励），靠其它任务推进、本身没有
        # 可上报的动作；对它发 trigger 一旦失败会被 halted，之后变 reward 也领
        # 不了。实测 fix1 里它就是靠 claim 路径领了 +100/+200/+300，所以这里
        # 仍判 skip，process_tasks 会在 state==reward 时绕过 skip 去领奖。
        if kind == "THIRD" and "累计" in cls.task_title(task):
            return "skip", "累计奖励是里程碑，靠其它任务推进，完成后领奖，不单独上报"
        if kind == "THIRD":
            if not task.get("missionCollectionId") or not task.get("missionDefId"):
                return "skip", "THIRD缺missionCollectionId/missionDefId，不能上报"
            if state == "take":
                return "take", "需先接取任务，刷新实例后再执行"
            return "trigger", "可使用现有THIRD接口；以重新查询和领奖结果为准"
        return "skip", f"未适配类型 {kind or '未知'}；需核对官方页面流程"

    @classmethod
    def p2p_invite_info(cls, task: dict) -> dict | None:
        """从实时 querytask 的 44914004 任务里提取互助所需信息。

        返回 {missionDefId, missionXId, p2pId, actId, shareId, done, total}；
        不是 44914004，或缺 shareId/p2pId（没法被别人助力）时返回 None。
        shareId/p2pId/actId 均按账号实时取，不硬编码（每号不同、每天可能变）。
        done 优先读 stage.count，缺失时按 FINISH 阶段数反推。
        """
        task = cls._task_dict(task)
        if str(task.get("missionDefId") or task.get("missionId") or "") != P2P_INVITE_DEF_ID:
            return None
        action, value = cls._task_parts(task)
        ext = cls._task_dict(action.get("ext"))
        p2p_id = str(value.get("p2pId") or "")
        act_id = str(value.get("actId") or ext.get("actId") or "")
        share_id = str(ext.get("shareId") or "")
        if not p2p_id or not share_id:
            return None
        total = cls._task_count(value.get("finalStageCount")) or P2P_INVITE_STAGES
        done = cls._task_count(cls._task_dict(task.get("stage")).get("count"))
        if done is None:
            stages = task.get("missionStageDTOS") or []
            done = sum(1 for s in stages
                       if isinstance(s, dict) and str(s.get("status") or "").upper() == "FINISH")
        return {"missionDefId": P2P_INVITE_DEF_ID,
                "missionXId": str(task.get("missionXId") or ""),
                "p2pId": p2p_id, "actId": act_id, "shareId": share_id,
                "done": done, "total": total}

    @staticmethod
    def plan_p2p_assists(accounts: list[dict], history: set[tuple[str, str]]) -> list[dict]:
        """排互助计划（纯函数，无网络请求，可单测）。

        accounts 按 ELM_COOKIE 顺序，每项 {remark, done, total, shareId}；
        history 是今天已助力的 (helper_remark, target_remark) 对。
        策略（用户定）：先集中助顺序第一个未满的号，助满再按顺序助下一个；
        每号每天最多助一次、同一对每天最多助一次（保守规则，数据里没写
        限制，这是假设）。目标缺 shareId（没法被助力）或已满时跳过；助力
        者缺实时信息（本轮没查到任务）时跳过。返回 [{helper, target, shareId}]
        （shareId 是目标号的）。
        """
        plan: list[dict] = []
        helped_today = {h for (h, _t) in history}
        for target in accounts:
            if not target.get("shareId"):
                continue
            need = (target.get("total") or P2P_INVITE_STAGES) - (target.get("done") or 0)
            if need <= 0:
                continue
            for helper in accounts:
                if need <= 0:
                    break
                h_remark = helper.get("remark") or ""
                t_remark = target.get("remark") or ""
                if not h_remark or h_remark == t_remark:
                    continue
                if h_remark in helped_today:
                    continue
                if (h_remark, t_remark) in history:
                    continue
                if not helper.get("shareId"):
                    continue
                plan.append({"helper": h_remark, "target": t_remark,
                             "shareId": target.get("shareId") or ""})
                helped_today.add(h_remark)
                need -= 1
        return plan

    def attempt_p2p_assist(self, target: dict) -> dict:
        """TODO（1.5.44 待实现点）：用本号给目标号助力一次。

        缺真实助力接口：querytask JSON 里只有 shareId/p2pId/actId 与邀请
        记录页地址，没有助力端点/参数名/签名方式。在拿到真实助力接口前，
        这里不发任何请求，只记日志并返回 todo=True；调用方不得把这次计
        为已助力、不得写历史。以后抓到真实助力接口再填 endpoint，计划与
        历史逻辑不用改。
        """
        self._log(f"⏭ P2P 助力待实现：目标【{target.get('remark')}】缺助力接口"
                  f"（shareId/p2pId/actId 已取到）；未发起任何请求")
        return {"ok": False, "todo": True, "msg": "助力接口未知，未发起请求"}

    @classmethod
    def _is_pageview(cls, task: dict) -> bool:
        return cls.task_plan(task)[0] == "pageview"

    @classmethod
    def _is_trigger(cls, task: dict) -> bool:
        return cls.task_plan(task)[0] == "trigger"

    @classmethod
    def _task_count(cls, value) -> int | None:
        try:
            result = float(value)
            return int(result) if math.isfinite(result) and result >= 0 and result.is_integer() else None
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _task_remaining(cls, task: dict, cap: int) -> int:
        _, value = cls._task_parts(task)
        stage = cls._task_dict(task.get("stage"))
        total = cls._task_count(value.get("finalStageCount"))
        done = cls._task_count(stage.get("count"))
        if total is None or done is None:
            return 1
        return min(max(0, total - done), cap)

    @classmethod
    def _task_remaining_stages(cls, task: dict) -> int:
        """1.5.34：任务剩余未完成阶段数（RUNNING 的 stage 数量；未知时 1）。"""
        stages = task.get("missionStageDTOS") or []
        if not isinstance(stages, list) or not stages:
            return 1
        n = sum(1 for s in stages if isinstance(s, dict) and str(s.get("status") or "").upper() == "RUNNING")
        return n if n > 0 else 1

    def describe_tasks(self, tasks: list[dict]) -> dict:
        labels = {"browse": "活动浏览", "external": "外部入口（支付宝/闲鱼等）",
                  "order": "下单", "invite": "邀请/助力", "third": "点击", "other": "未适配", "alipay_ad": "支付宝原生激励广告"}
        counts = {"pageview": 0, "trigger": 0, "skip": 0, "take": 0}
        # 1.5.50：自动发现——不在 KNOWN_H5_TASKS 也不在 ELM_EXTRA_H5_TASKS
        # 的任务就是本轮 querytask 新出现的，标 🆕 让用户在日志里直接看到；
        # PAGEVIEW 类仍按 task_plan 自动上报+领奖，不用等人工加 ID。
        _known = {str(t.get("missionDefId")) for t in KNOWN_H5_TASKS}
        _known |= {str(t.get("missionDefId")) for t in self.extra_h5_tasks()}
        for task in tasks:
            action, value = self._task_parts(task)
            mode, reason = self.task_plan(task)
            counts[mode] += 1
            # 只显示选定字段，不输出跳转链接、签名、missionXId或原始actionValue。
            title = redact(self.task_title(task)).replace("\n", " ").replace("\r", " ")[:80]
            kind = re.sub(r"[^A-Za-z0-9_-]", "", str(action.get("actionType") or "未知"))[:30]
            _, fields = self._task_parts(task)
            _did = str(task.get("missionDefId") or task.get("missionId") or "")
            _new = "🆕 新任务" if _did and _did not in _known else ""
            self._log(f"任务[{title}] | {_new + ' ' if _new else ''}分类={labels[self.task_category(task)]} | 类型={kind} | "
                      f"pageSpm={'有' if fields.get('pageSpm') else '无'} | "
                      f"处理={'可上报' if mode != 'skip' else '跳过'}：{reason}")
        self._log(f"任务清单共{len(tasks)}项：PAGEVIEW {counts['pageview']}，THIRD {counts['trigger']}，跳过 {counts['skip']}；待接取 {counts['take']}")
        return counts

    def report_pageview(self, task: dict, biz_scene: str | None = None) -> dict:
        mode, reason = self.task_plan(task)
        if mode != "pageview":
            return {"ok": False, "skip": True, "msg": reason}
        action, value = self._task_parts(task)
        loc = json.dumps({"lng": self.lng, "lat": self.lat}, separators=(",", ":"))
        duration = self._task_count(value.get("pageStageTime"))
        # 1.5.50：缺 pageSpm 时用 engage-hub 本页兜底（见 task_plan）。
        spm = value.get("pageSpm") or DEFAULT_PAGESPM
        if not value.get("pageSpm"):
            self._log(f"ℹ 任务[{self.task_title(task)}] 缺 pageSpm，默认用 engage-hub 本页 {DEFAULT_PAGESPM} 上报")
        data = {
            "bizScene": biz_scene or BIZ_SCENE, "accountPlan": ACCOUNT_PLAN,
            "longitude": self.lng, "latitude": self.lat,
            "locationInfos": json.dumps([loc], separators=(",", ":")),
            "collectionId": task.get("missionCollectionId") or task.get("missionCollectId"),
            "missionId": task.get("missionId") or task.get("missionDefId"),
            "pageFrom": spm, "actionCode": "PAGEVIEW", "sync": True,
            "viewTime": max(3, duration) if duration is not None else 8,
        }
        if task.get("instanceId"):
            data["instanceId"] = task["instanceId"]
        return self.parse_ret(self.mtop(API["pageview"], "1.1", data, {"asac": ASAC["PAGEVIEW"]}, retry=1))

    def report_trigger(self, task: dict, biz_scene: str | None = None, biz_scene_override: str | None = None) -> dict:
        mode, reason = self.task_plan(task)
        if mode != "trigger":
            return {"ok": False, "skip": True, "msg": reason}
        # trigger 的 data 自带 bizScene=missionDefId（mtop 合并时调用方优先），
        # 所以集合场景不影响它；biz_scene 参数仅为与其它上报接口保持一致签名。
        # 1.5.20：biz_scene_override 供 417 后重试——改用集合级
        # interact_center 再调一次，绕过任务级 bizScene 过滤。
        now = int(time.time() * 1000)
        data = {"latitude": self.lat, "longitude": self.lng, "riskScene": "duobao_external",
                "bizScene": biz_scene_override or str(task["missionDefId"]), "eventType": "THIRD", "eventSubType": "EVENT_FINISH",
                "eventId": now, "eventIdemPotent": now, "eventTime": now, "triggerType": "SYNC",
                "collectionId": str(task["missionCollectionId"]), "missionId": str(task["missionDefId"]),
                "missionXId": task.get("missionXId") or ""}
        return self.parse_ret(self.mtop(API["trigger"], "1.1", data, retry=1))

    def fetch_task_token(self, mission_def_id: str, mission_collection_id: str = "3112") -> str | None:
        """用 missionIds 定向查一次 querytask，拿当前会话里该任务的 token。

        KNOWN 任务没有 ext，取不到 token；这里按官方 H5/支付宝的
        missionIds 定向查法现查一次。结果按任务缓存，本轮只查一次。
        查不到/无 token 时返回 None，不抛错（回调照发，会报缺 token）。"""
        cache = getattr(self, "_token_cache", None)
        if cache is None:
            cache = self._token_cache = {}
        did = str(mission_def_id)
        if did in cache:
            return cache[did]
        token = None
        try:
            lst = self.query_tasks(mission_collection_id, mission_ids=[did])
            for t in lst or []:
                if str(t.get("missionDefId") or t.get("missionId") or "") == did:
                    token = extract_action_token(t)
                    break
        except Exception as ex:
            self._log(f"⚠ 定向查询任务 {did} 的 token 失败：{ex}")
        cache[did] = token
        return token

    def extract_partner_callback_url(self, task: dict) -> str | None:
        """1.5.34：从任务跳转链接里取合作方 rookieCallbackTransfer 地址。

        App 里滴滴/闲鱼任务的 alipayActionLink 就是这个地址（带
        tagType/channelId/token）。只取地址，不输出 token。"""
        extv = (task.get("ext") or {}).get("extValue") or {}
        for key in ("alipayActionLink", "elemeActionLink", "taobaoActionLink"):
            link = extv.get(key) or ""
            if not isinstance(link, str):
                continue
            s = link
            for _ in range(3):
                s2 = unquote(s)
                if s2 == s:
                    break
                s = s2
            m = re.search(r"https?://[A-Za-z0-9.\-]*yundps\.com/General/rookieCallbackTransfer\?[^\s\"'<>]+", s)
            if m:
                return m.group(0)
        return None

    def report_partner_callback(self, task: dict) -> dict:
        """1.5.34：GET 合作方 rookieCallbackTransfer（App 的真实完成路径）。

        HAR 实测：该 GET 带 ts 头，返回 302 到支付宝 OAuth（sign 头含
        密钥、脚本伪造不了）；无 sign 的 GET 大概率同样 302。302 不代
        表完成，只代表合作方收到了这次回调；是否翻阶段看重新查询。
        直连失败（路由器出口受限）时走 ELM_PARTNER_PROXY（默认
        sing-box socks5h://127.0.0.1:1080）再试一次。"""
        url = self.extract_partner_callback_url(task)
        if not url:
            return {"ok": False, "msg": "任务无合作方回调链接"}
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "ts": str(int(time.time() * 1000)),
        }
        proxy = cfg("ELM_PARTNER_PROXY", "socks5h://127.0.0.1:1080") or ""
        last_err = None
        for attempt, proxies in enumerate((None, {"http": proxy, "https": proxy} if proxy else None)):
            if attempt == 1 and not proxy:
                break
            try:
                resp = self.session.get(url, headers=headers, timeout=15, allow_redirects=False, proxies=proxies)
                loc = resp.headers.get("Location", "") or ""
                if resp.status_code in (200, 301, 302, 303, 307, 308):
                    via = "代理" if proxies else "直连"
                    need_oauth = "openauth.alipay.com" in loc
                    self._log(f"合作方回调[{self.task_title(task)}]：{via} HTTP {resp.status_code}"
                              + (" → 支付宝授权（需 OAuth，阶段是否翻看重新查询）" if need_oauth else ""))
                    return {"ok": True, "msg": f"HTTP {resp.status_code}", "need_oauth": need_oauth}
                return {"ok": False, "msg": f"HTTP {resp.status_code}"}
            except Exception as ex:
                last_err = ex
                continue
        return {"ok": False, "msg": f"请求异常：{last_err}"}

    @staticmethod
    def _fish_task_sign(token: str, channel_id: str = "272") -> str:
        """1.5.37：拼闲鱼 taskSign（5 段，见 FISH_SIGN_MID 注释）。
        token 用跳转链接里的实时值；7 位尾数每次不同，随机生成。"""
        suffix = str(random.randint(1000000, 9999999))
        return "-".join(["other", str(channel_id), FISH_SIGN_MID, str(token), suffix])

    def report_fish_callback(self, task: dict) -> dict:
        """1.5.37：POST 闲鱼 IdleFish/callback（App 的真实完成信号）。

        19:06 HAR 实抓：看完视频后小游戏页 POST 该地址，请求体只有
        {"taskSign": ...}，返回 {"code":0,"msg":""} 即完成，随后
        receiveprize 领奖。token 与 rookieCallbackTransfer 链接里的
        是同一个，运行时从任务跳转链接现取（取不到再走定向查询与
        CALLBACK_TOKEN 兜底）。该 POST 不带 Cookie、不走 OAuth；
        服务端认不认脚本拼的 taskSign 只看返回 code，以日志为准。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        info = PARTNER_CALLBACK.get(did) or {}
        channel_id = str(info.get("channelId") or "272")
        token = None
        url = self.extract_partner_callback_url(task)
        if url:
            token = dict(parse_qsl(urlsplit(url).query)).get("token")
        if not token:
            token = self.fetch_task_token(did) or CALLBACK_TOKEN.get(did)
        if not token:
            return {"ok": False, "msg": "取不到回调 token"}
        sign = self._fish_task_sign(token, channel_id)
        headers = {
            "Content-Type": "application/json",
            "Referer": f"https://99992026090000{random.randint(10, 99)}.minigame.goofish.com/",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        }
        try:
            resp = self.session.post(FISH_CALLBACK_URL, headers=headers, timeout=15, json={"taskSign": sign})
            try:
                data = resp.json()
            except Exception:
                data = {}
            if resp.status_code == 200 and str(data.get("code")) == "0":
                self._log(f"✅ 闲鱼回调已发[{self.task_title(task)}]（IdleFish/callback code=0）；阶段是否翻看重新查询")
                return {"ok": True, "msg": "code=0"}
            return {"ok": False, "msg": f"HTTP {resp.status_code} code={data.get('code')}"}
        except Exception as ex:
            return {"ok": False, "msg": f"请求异常：{ex}"}

    # ---- 1.5.39：00:31 HAR 实抓的完成信号（参数见文件头 POP2ME_*
    # 与 POLYMORPHISM_* 常量）。pop2me 三条要支付宝小程序会话
    # token（LEMEWEBEo:…，脚本侧没有）与 X-Cy-Sign（SHA256 密钥
    # 未知），无 token 时直接跳过、不发请求；飞猪 polymorphism
    # 的 callbackToken 实时可取，真实尝试，sign 缺失时服务端回
    # -2001，日志明说，不影响其他任务。----
    def _pop2me_headers(self, body_text: str, app_version: str = "4") -> dict:
        """pop2me 网关请求头。X-Cy-Ts 是 13 位毫秒时间戳，X-Cy-Nonce
        是随机串，X-Device-Id 是小程序会话设备号（HAR 里三次各不
        相同，属会话值，脚本没有，用固定占位只会让签名对不上）。
        X-Cy-Sign 是 SHA256，密钥未知，这里不拼（拼错的 sign 与
        不带 sign 同样只会回签名错）。"""
        import time as _time
        return {
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "Accept-Charset": "utf-8",
            "X-Cy-Ts": str(int(_time.time() * 1000)),
            "X-Cy-Nonce": "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") for _ in range(32)),
            "X-App-Version": str(app_version),
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/24A435",
            "Accept": "*/*",
            "Connection": "Keep-Alive",
        }

    def report_pop2me_browse(self, task: dict, pop2me_token: str | None = None) -> dict:
        """1.5.39：POST pop2me BrowseTaskComplete（HAR 152 条实抓，
        页面点击/浏览点击的真完成信号，resp errorCode=0 且
        data.Status=FINISHED）。body={token, channel:"eleme",
        bt:"1"}。无小程序会话 token 时不发请求，直接返回阻断
        原因——token 是 GetDeviceId+authCode 现取的会话值，脚本
        侧没有，硬发也只会会话错。"""
        if not pop2me_token:
            return {"ok": False, "msg": "需支付宝小程序会话 token（LEMEWEBEo:…），脚本侧无此会话，跳过"}
        body = {"token": pop2me_token, "channel": "eleme", "bt": "1"}
        try:
            resp = self.session.post(POP2ME_GATEWAY + POP2ME_BROWSE,
                                     headers=self._pop2me_headers(json.dumps(body, ensure_ascii=False)),
                                     timeout=15, json=body)
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("errorCode") == 0:
                return {"ok": True, "msg": f"Status={((data.get('data') or {}).get('Status'))}"}
            return {"ok": False, "msg": f"HTTP {resp.status_code} errorCode={data.get('errorCode')}"}
        except Exception as ex:
            return {"ok": False, "msg": f"请求异常：{ex}"}

    def _pop2me_try(self, label: str, fn) -> dict:
        """1.5.46：只给带 deviceId 的 pop2me 调用用的就地重试（用户令：运行一次
        脚本重试三次——单次运行内、单个调用失败时就地最多 POP2ME_MAX_ATTEMPTS=3
        次，不是整轮重跑 3 遍；只限 CroInfo/GuessPriceInfo、DhTaskStart/
        DhTaskComplete、BrowseTaskStart/BrowseTaskComplete，其他调用不用它）。
        fn 返回 pop2me 网关响应 dict；errorCode==0 即成功立即返回，否则隔
        POP2ME_RETRY_WAIT 秒再试。3 次都失败返回最后一次响应（或合成的
        errorCode=-1），绝不抛异常——调用方据此跳过该任务、记日志、继续跑
        其他任务。注意：IdentityCache 在 6666 时还会自己刷新一次身份并重跑
        整个 operation（已有行为），那是换新鲜 deviceId，不是同一调用的重试。"""
        last: dict = {"errorCode": -1, "errorMsg": "未执行"}
        for attempt in range(1, POP2ME_MAX_ATTEMPTS + 1):
            try:
                resp = fn()
            except Exception as ex:
                last = {"errorCode": -1, "errorMsg": f"请求异常：{ex}"}
                resp = last
            if isinstance(resp, dict) and str(resp.get("errorCode")) in ("0",):
                return resp
            if isinstance(resp, dict):
                last = resp
            else:
                last = {"errorCode": -1, "errorMsg": f"响应不是 dict：{type(resp).__name__}"}
            if attempt < POP2ME_MAX_ATTEMPTS:
                self._log(f"⚠ pop2me {label} 第 {attempt} 次失败（errorCode={last.get('errorCode')}），{POP2ME_RETRY_WAIT} 秒后重试（最多 {POP2ME_MAX_ATTEMPTS} 次）")
                time.sleep(POP2ME_RETRY_WAIT)
        return last

    def report_pop2me_checkin(self, task: dict) -> dict:
        """1.5.41：48432001 天天打卡（pop2me 真完成链路，2026-11-21 实抓）。
        BrowseTaskStart → 等 Seconds（实抓 15 秒）→ BrowseTaskComplete。
        签名用 elmxyx_pop2me.sign_cy（X-Cy-Sign，key=deviceId），身份用
        account_identity_cache.IdentityCache（按账号+小程序缓存 deviceId，
        6666 时刷新一次）。body={token, channel:"eleme", bt:"1"}。
        无缓存又无 POP2ME_AUTHCODE_<appId> 时返回失败、不崩整轮——
        跑之前须先用 elmxyx_pop2me.py --seed 给该账号+小程序导入新鲜
        deviceId（deviceId 几分钟就过期，seed 后马上跑）。"""
        if not _POP2ME_OK:
            return {"ok": False, "msg": "pop2me 模块未加载（elmxyx_pop2me.py / account_identity_cache.py 不在同目录）"}
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        if did != "48432001":
            return {"ok": False, "msg": "不是 48432001"}
        token = extract_action_token(task) or ""
        if not token:
            try:
                token = self.fetch_task_token(did) or ""
            except Exception:
                token = ""
        if not token:
            return {"ok": False, "msg": "拿不到任务 token"}
        app_id = POP2ME_CHECKIN_APP_ID
        route = POP2ME_CHECKIN_ROUTE
        cache = IdentityCache(_pop2me.cache_dir())
        exchange = make_get_device_id_exchange()

        def authorize(account, app_id_):
            value = os.environ.get(f"POP2ME_AUTHCODE_{app_id_}", "").strip()
            if not value:
                raise AuthorizationRequired(
                    f"账号 {account} / 小程序 {app_id_} 的身份已失效，需新 authCode："
                    f"请在对应小程序里重新授权，把新 authCode 填入环境变量 "
                    f"POP2ME_AUTHCODE_{app_id_}（一次性，用后请清除），再跑一次")
            return value

        def _post(api, identity):
            body = {"token": token, "channel": POP2ME_CHECKIN_CHANNEL, "bt": POP2ME_CHECKIN_BT}
            body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ts_ms = str(int(time.time() * 1000))
            nonce = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
            sign = _pop2me.sign_cy(ts_ms, nonce, body_bytes, identity.device_id, app_id)
            app_version = os.environ.get(f"POP2ME_APP_VERSION_{app_id}", "")
            referer = _pop2me.build_referer({"page": POP2ME_CHECKIN_PAGE},
                                             {"token": token, "channel": POP2ME_CHECKIN_CHANNEL},
                                             app_id, app_version)
            headers = {
                "X-Cy-Nonce": nonce,
                "Content-Length": str(len(body_bytes)),
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
                "X-Cy-Ts": ts_ms,
                "Cookie": "",
                "Referer": referer,
                "X-Cy-Sign": sign,
                "X-Device-Id": identity.device_id,
                "X-App-Version": "2",
                "Accept-Charset": "utf-8",
                "User-Agent": _pop2me.USER_AGENT,
                "Accept": "*/*",
                "Connection": "Keep-Alive",
                "Host": _pop2me.POP2ME_HOST,
            }
            url = f"https://{_pop2me.POP2ME_HOST}/gateway/r/m/{route}.{api}"
            return _pop2me.post_json(url, headers, body_bytes)

        def operation(account, app_id_, identity):
            # 1.5.46：BrowseTaskStart/Complete 是带 deviceId 的调用，就地最多
            # 重试 3 次（_pop2me_try）；3 次都失败返回最后响应，跳过本任务。
            start = self._pop2me_try("BrowseTaskStart", lambda: _post("BrowseTaskStart", identity))
            if observed_identity_invalid(start):
                return start
            if str(start.get("errorCode")) not in ("0",):
                return start
            data = start.get("data") or {}
            seconds = start.get("Seconds") or data.get("Seconds") or 15
            try:
                seconds = float(seconds)
            except (TypeError, ValueError):
                seconds = 15
            time.sleep(max(0.0, seconds) + 1)
            return self._pop2me_try("BrowseTaskComplete", lambda: _post("BrowseTaskComplete", identity))

        try:
            reply = cache.run(self.remark, app_id, authorize=authorize,
                              exchange=exchange, operation=operation)
        except AuthorizationRequired as ex:
            return {"ok": False, "msg": str(ex)}
        except Exception as ex:
            return {"ok": False, "msg": f"身份处理失败：{ex}"}
        data = reply.get("data") or {}
        status = reply.get("Status") or data.get("Status") or ""
        if str(reply.get("errorCode")) in ("0",) and str(status).upper() == "FINISHED":
            return {"ok": True, "msg": "BrowseTaskComplete FINISHED"}
        return {"ok": False,
                "msg": f"errorCode={reply.get('errorCode')} Status={status or reply.get('errorMsg')}"}

    def report_pop2me_guess_price(self, task: dict) -> dict:
        """1.5.46：48152003 猜价格（pop2me 两进式第一进，2026-11-22 04:55 实抓）。
        CroInfo → GuessPriceInfo → GuessPriceCallbackByMe，全部带 deviceId 签名
        （网关版本 4：04:55 抓包离线验算 v4 MATCH、v2 不匹配），每个调用就地
        最多重试 POP2ME_MAX_ATTEMPTS=3 次（_pop2me_try，只限 deviceId 调用）。
        GuessPriceInfo 响应 data.config.spaceCode 与模块 DEFAULT_ITEM 的
        spaceCode 对不上只记日志、不中断（Item 可能轮换，回调仍用模块 Item）。
        TODO：提交猜价那一步 04:55 的包没录到（用户没点提交）——这里接的是
        模块已有的 GuessPriceCallbackByMe（2026-11-21 抓包的提交接口）；若
        09:59 实跑阶段不翻，须再录一次“真猜一次价格并点提交”的包再补。
        无缓存又无 POP2ME_AUTHCODE_<appId> 时返回失败、不崩整轮。"""
        if not _POP2ME_OK:
            return {"ok": False, "msg": "pop2me 模块未加载（elmxyx_pop2me.py / account_identity_cache.py 不在同目录）"}
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        if did != "48152003":
            return {"ok": False, "msg": "不是 48152003"}
        token = extract_action_token(task) or ""
        if not token:
            try:
                token = self.fetch_task_token(did) or ""
            except Exception:
                token = ""
        if not token:
            return {"ok": False, "msg": "拿不到任务 token"}
        cfg = _pop2me.POP2ME_TASKS["48152003"]
        app_id = cfg["app_id"]
        route = cfg["route"]
        params = {"token": token, "channel": _pop2me.DEFAULT_CHANNEL_48152003}
        cache = IdentityCache(_pop2me.cache_dir())
        exchange = make_get_device_id_exchange()

        def authorize(account, app_id_):
            value = os.environ.get(f"POP2ME_AUTHCODE_{app_id_}", "").strip()
            if not value:
                raise AuthorizationRequired(
                    f"账号 {account} / 小程序 {app_id_} 的身份已失效，需新 authCode："
                    f"请在对应小程序里重新授权，把新 authCode 填入环境变量 "
                    f"POP2ME_AUTHCODE_{app_id_}（一次性，用后请清除），再跑一次")
            return value

        def operation(account, app_id_, identity):
            cro = self._pop2me_try("CroInfo", lambda: _pop2me.call_cro_info(cfg, params, identity, app_id, route))
            if observed_identity_invalid(cro):
                return cro
            if str(cro.get("errorCode")) not in ("0",):
                return cro
            if not ((cro.get("data") or {}).get("IsPassAd")):
                return {"errorCode": -1, "errorMsg": "CroInfo IsPassAd=false"}
            info = self._pop2me_try("GuessPriceInfo", lambda: _pop2me.call_guess_price_info(cfg, params, identity, app_id, route))
            if observed_identity_invalid(info):
                return info
            if str(info.get("errorCode")) not in ("0",):
                return info
            data = info.get("data") or {}
            space_code = ((data.get("config") or {}).get("spaceCode") or data.get("spaceCode") or "")
            want = _pop2me.DEFAULT_ITEM_48152003.get("spaceCode", "")
            if space_code and want and space_code != want:
                self._log(f"⚠ 猜价格 spaceCode 与模块 Item 不一致（现取 {len(space_code)} 字符，模块 {len(want)} 字符），继续用模块 Item 提交")
            submit = _pop2me.make_operation(cfg, params, app_id, route,
                                            item=dict(_pop2me.DEFAULT_ITEM_48152003))
            return self._pop2me_try("GuessPriceCallbackByMe",
                                    lambda: submit(account, app_id_, identity))

        try:
            reply = cache.run(self.remark, app_id, authorize=authorize,
                              exchange=exchange, operation=operation)
        except AuthorizationRequired as ex:
            return {"ok": False, "msg": str(ex)}
        except Exception as ex:
            return {"ok": False, "msg": f"身份处理失败：{ex}"}
        if str(reply.get("errorCode")) in ("0",):
            return {"ok": True, "msg": "GuessPriceCallbackByMe errorCode=0"}
        return {"ok": False,
                "msg": f"errorCode={reply.get('errorCode')} {str(reply.get('errorMsg') or '')[:60]}"}

    def report_pop2me_dh_signed(self, task: dict) -> dict:
        """1.5.46：48324002 页面点击（pop2me 两进式第一进，2026-11-22 04:14 实抓）。
        DhTaskStart → ONGOING → DhTaskComplete → FINISHED，全部带 deviceId 签名
        （网关版本 2：04:14 抓包验算 v2 MATCH），每个调用就地最多重试
        POP2ME_MAX_ATTEMPTS=3 次（_pop2me_try，只限 deviceId 调用）。三支各用
        自己的小程序 appId/deviceId（deviceId 按支付宝用户 + 小程序 appId 绑定，
        不能跨支用）：先按任务跳转链接里的 appId 定支，链接里没有就按
        [菜谱（新）2021006191671004、菜谱 2021006136621641、金价 2021006191655125、
        房贷 2021006189633595] 顺序逐支试；某支授权失败（AuthorizationRequired/
        IdentityRejected）或调用失败就试下一支，全部失败才返回失败。
        DhTaskStart 模块里没有（与 Complete 同 body），这里像打卡的 _post 一样
        现拼请求。无缓存又无 authCode 时返回失败、不崩整轮。"""
        if not _POP2ME_OK:
            return {"ok": False, "msg": "pop2me 模块未加载（elmxyx_pop2me.py / account_identity_cache.py 不在同目录）"}
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        if did != "48324002":
            return {"ok": False, "msg": "不是 48324002"}
        token = extract_action_token(task) or ""
        if not token:
            try:
                token = self.fetch_task_token(did) or ""
            except Exception:
                token = ""
        if not token:
            return {"ok": False, "msg": "拿不到任务 token"}
        cfg = _pop2me.POP2ME_TASKS["48324002"]
        gw_version = str(cfg.get("gw_version", "2"))
        cache = IdentityCache(_pop2me.cache_dir())
        exchange = make_get_device_id_exchange()

        # 先按任务跳转链接里的 appId 定支（模块 parse_launch_params），定不到
        # 就按 VARIANTS_48324002 的顺序逐支试（菜谱（新）在首位）。
        link_app_id = ""
        link_params: dict = {}
        extv = (task.get("ext") or {}).get("extValue") or {}
        action = task.get("actionConfig") or {}
        value = action.get("actionValue") or {}
        links = []
        for k in ("alipayActionLink", "elemeActionLink", "taobaoActionLink"):
            if isinstance(extv.get(k), str) and extv[k]:
                links.append(extv[k])
        for k in ("url", "jumpUrl", "targetUrl", "link"):
            if isinstance(value.get(k), str) and value[k]:
                links.append(value[k])
        for link in links:
            _page, _params, _app = _pop2me.parse_launch_params(link)
            if _page:
                link_params, link_app_id = _params, _app
                break
        ordered = list(_pop2me.VARIANTS_48324002.keys())
        if link_app_id in ordered:
            ordered.remove(link_app_id)
            ordered.insert(0, link_app_id)

        def authorize(account, app_id_):
            value = os.environ.get(f"POP2ME_AUTHCODE_{app_id_}", "").strip()
            if not value:
                raise AuthorizationRequired(
                    f"账号 {account} / 小程序 {app_id_} 的身份已失效，需新 authCode："
                    f"请在对应小程序里重新授权，把新 authCode 填入环境变量 "
                    f"POP2ME_AUTHCODE_{app_id_}（一次性，用后请清除），再跑一次")
            return value

        def _post_dh(api, identity, app_id, route, params):
            body = {"token": params.get("token", ""), "tuwenId": params.get("tuwenId", ""),
                    "chapingId": params.get("chapingId", ""), "feedsId": params.get("feedsId", "")}
            body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ts_ms = str(int(time.time() * 1000))
            nonce = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
            sign = _pop2me.sign_cy(ts_ms, nonce, body_bytes, identity.device_id, app_id, gw_version)
            app_version = os.environ.get(f"POP2ME_APP_VERSION_{app_id}", "")
            referer = _pop2me.build_referer(cfg, params, app_id, app_version)
            headers = {
                "X-Cy-Nonce": nonce,
                "Content-Length": str(len(body_bytes)),
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
                "X-Cy-Ts": ts_ms,
                "Cookie": "",
                "Referer": referer,
                "X-Cy-Sign": sign,
                "X-Device-Id": identity.device_id,
                "X-App-Version": gw_version,
                "Accept-Charset": "utf-8",
                "User-Agent": _pop2me.USER_AGENT,
                "Accept": "*/*",
                "Connection": "Keep-Alive",
                "Host": _pop2me.POP2ME_HOST,
            }
            if cfg.get("has_app_id_header"):
                headers["X-App-Id"] = app_id
            url = f"https://{_pop2me.POP2ME_HOST}/gateway/r/m/{route}.{api}"
            return _pop2me.post_json(url, headers, body_bytes)

        last_msg = "未尝试任何分支"
        for branch_app_id in ordered:
            variant = _pop2me.VARIANTS_48324002[branch_app_id]
            norm = {"token": token,
                    "tuwenId": link_params.get("tuwen_id", ""),
                    "chapingId": link_params.get("chaping_id", ""),
                    "feedsId": link_params.get("feeds_id", ""),
                    "channel": link_params.get("channel", "")}
            notes: list = []
            _app, route, _ver = _pop2me.resolve_variant(did, branch_app_id, norm, notes)
            if not route:
                continue

            def operation(account, app_id_, identity, _route=route, _params=dict(norm)):
                start = self._pop2me_try("DhTaskStart", lambda: _post_dh("DhTaskStart", identity, app_id_, _route, _params))
                if observed_identity_invalid(start):
                    return start
                if str(start.get("errorCode")) not in ("0",):
                    return start
                return self._pop2me_try("DhTaskComplete", lambda: _post_dh("DhTaskComplete", identity, app_id_, _route, _params))

            try:
                reply = cache.run(self.remark, branch_app_id, authorize=authorize,
                                  exchange=exchange, operation=operation)
            except (AuthorizationRequired, IdentityRejected) as ex:
                self._log(f"⚠ 页面点击 {variant['label']}支授权失败（{str(ex)[:60]}），试下一支")
                last_msg = str(ex)[:80]
                continue
            except Exception as ex:
                self._log(f"⚠ 页面点击 {variant['label']}支异常（{str(ex)[:60]}），试下一支")
                last_msg = str(ex)[:80]
                continue
            data = reply.get("data") or {}
            status = reply.get("Status") or data.get("Status") or ""
            if str(reply.get("errorCode")) in ("0",) and str(status).upper() == "FINISHED":
                return {"ok": True, "msg": f"DhTaskComplete FINISHED（{variant['label']}支）"}
            last_msg = f"{variant['label']}支 errorCode={reply.get('errorCode')} Status={status or reply.get('errorMsg')}"
            self._log(f"⚠ 页面点击 {variant['label']}支未完成（{last_msg[:80]}），试下一支")
        return {"ok": False, "msg": last_msg}

    def report_pop2me_guess(self, task: dict, pop2me_token: str | None = None,
                            ad_biz_id: str | None = None) -> dict:
        """1.5.39：POST pop2me GuessPriceCallbackByMe（HAR 1226 条
        实抓，猜价格的真完成信号，resp errorCode=0）。body=
        {AdBizId, Token, Channel:"me", Item}，顶层 AdBizId 是
        32 位 hex 的 base64，Item 用 GUESS_ITEM_TEMPLATE，adBizId
        随任务变。无小程序会话 token 时不发请求。"""
        if not pop2me_token:
            return {"ok": False, "msg": "需支付宝小程序会话 token（LEMEWEBEo:…），脚本侧无此会话，跳过"}
        item = dict(GUESS_ITEM_TEMPLATE)
        if ad_biz_id:
            item["adBizId"] = ad_biz_id
        body = {"AdBizId": "", "Token": pop2me_token, "Channel": "me", "Item": item}
        try:
            resp = self.session.post(POP2ME_GATEWAY + POP2ME_GUESS,
                                     headers=self._pop2me_headers(json.dumps(body, ensure_ascii=False)),
                                     timeout=15, json=body)
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("errorCode") == 0:
                return {"ok": True, "msg": "errorCode=0"}
            return {"ok": False, "msg": f"HTTP {resp.status_code} errorCode={data.get('errorCode')}"}
        except Exception as ex:
            return {"ok": False, "msg": f"请求异常：{ex}"}

    def report_pop2me_dh(self, task: dict, pop2me_token: str | None = None,
                         api_path: str = "", tuwen_id: str = "", chaping_id: str = "") -> dict:
        """1.5.39：POST pop2me DhTaskComplete（HAR 257/425/542 条
        实抓，打卡的真完成信号，resp errorCode=0 且
        data.Status=FINISHED）。body={token, tuwenId, chapingId,
        feedsId:""}，api_path 取 POP2ME_DH_GOLD 或
        POP2ME_DH_RECIPE。无小程序会话 token 时不发请求。"""
        if not pop2me_token:
            return {"ok": False, "msg": "需支付宝小程序会话 token（LEMEWEBEo:…），脚本侧无此会话，跳过"}
        body = {"token": pop2me_token, "tuwenId": tuwen_id, "chapingId": chaping_id, "feedsId": ""}
        try:
            resp = self.session.post(POP2ME_GATEWAY + (api_path or POP2ME_DH_GOLD),
                                     headers=self._pop2me_headers(json.dumps(body, ensure_ascii=False), app_version="2"),
                                     timeout=15, json=body)
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("errorCode") == 0:
                return {"ok": True, "msg": f"Status={((data.get('data') or {}).get('Status'))}"}
            return {"ok": False, "msg": f"HTTP {resp.status_code} errorCode={data.get('errorCode')}"}
        except Exception as ex:
            return {"ok": False, "msg": f"请求异常：{ex}"}

    def report_polymorphism_callback(self, task: dict) -> dict:
        """1.5.39：GET 飞猪 polymorphismChannelCallback（HAR 1523
        条实抓，飞猪的真完成信号，resp code=200 时回一个
        blessings.yundps.com/Channel/centre 跳转 URL）。
        callbackToken 实时取（跳转链接→定向查询→硬编码兜底，
        与 report_callback 同一路）；其余头按 HAR 原样带
        （appid/timestamp/nonce/adzone-id/v/os）。sign 是 MD5，
        密钥未知，20 余种无密钥组合全不匹配，这里不带 sign——
        服务端会回 -2001 签名错误（探针 228 已证实），属已知
        阻断，日志明说；一旦拿到小程序签名密钥，把 sign 算上
        即可启用。返回 ok=True 仅当 code==200。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        token = extract_action_token(task)
        if not token:
            token = self.fetch_task_token(did, task.get("missionCollectionId") or task.get("missionCollectId") or "3112")
        if not token:
            token = CALLBACK_TOKEN.get(did)
        if not token:
            return {"ok": False, "msg": "取不到回调 token"}
        import time as _time
        url = POLYMORPHISM_URL + "?callbackToken=" + quote(token, safe="")
        headers = {
            "appid": POLYMORPHISM_APPID,
            "timestamp": str(int(_time.time())),
            "nonce": str(random.randint(1000, 9999)),
            "adzone-id": POLYMORPHISM_ADZONE,
            "v": POLYMORPHISM_V,
            "os": "aliapp",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip",
            "Accept-Charset": "utf-8",
            "Cache-Control": "no-cache",
            "Referer": f"https://{POLYMORPHISM_APPID}.hybrid.alipay-eco.com/{POLYMORPHISM_APPID}/0.2.2610291358.59/index.html#pages/index/index",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/24A435",
            "Accept": "*/*",
            "Connection": "Keep-Alive",
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=15)
            data = resp.json() if resp.content else {}
            code = data.get("code")
            if resp.status_code == 200 and code == 200:
                self._log(f"✅ 飞猪 polymorphism 已发[{self.task_title(task)}]（code=200）；阶段是否翻看重新查询")
                return {"ok": True, "msg": "code=200"}
            if code == -2001:
                return {"ok": False, "msg": "签名错误（-2001，需小程序 MD5 签名密钥，脚本侧无）"}
            return {"ok": False, "msg": f"HTTP {resp.status_code} code={code} msg={str(data.get('msg') or '')[:40]}"}
        except Exception as ex:
            return {"ok": False, "msg": f"请求异常：{ex}"}

    def click_miniapp_share(self, task: dict) -> dict:
        """1.5.48：真 GET 一次滴滴/飞猪小程序分享链接（模拟真人点击）。

        链接是用户发的支付宝小程序分享短链（ur.alipay.com，见
        MINIAPP_CLICK_URLS）：302 到 render.alipay.com 落地页
        （200），再由 JS 唤起 alipays:// 小程序。真浏览器就是这么
        点的：跟跳转、iPhone Safari UA、落地 200 即算点开；若某
        跳直接指到 alipays:// 也算点开（已解析到小程序）。
        直连失败走 ELM_PARTNER_PROXY（默认 sing-box
        socks5h://127.0.0.1:1080）再试一次，与 report_partner_callback
        同一路。点开后停 MINIAPP_CLICK_STAY_SECONDS 秒，模拟人在
        小程序里，再回饿了么走 new.callback。
        注意：脚本无支付宝会话，点击是匿名打开——服务端不认分享
        参数就不算数；ok=True 只代表链接点开了，阶段翻不翻看重新
        查询。永不抛异常（调用方也包了 try/except）。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        url = MINIAPP_CLICK_URLS.get(did) or ""
        if not url:
            return {"ok": False, "msg": "该任务无小程序分享链接"}
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        proxy = cfg("ELM_PARTNER_PROXY", "socks5h://127.0.0.1:1080") or ""
        last_err = None
        for attempt, proxies in enumerate((None, {"http": proxy, "https": proxy} if proxy else None)):
            if attempt == 1 and not proxy:
                break
            try:
                current = url
                for _hop in range(5):  # 最多跟 5 跳：短链 -> 落地页
                    resp = self.session.get(current, headers=headers, timeout=15,
                                            allow_redirects=False, proxies=proxies)
                    if resp.status_code == 200:
                        time.sleep(MINIAPP_CLICK_STAY_SECONDS)
                        return {"ok": True, "msg": "HTTP 200"}
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = (resp.headers.get("Location") or "").strip()
                        if loc.startswith("alipays://"):
                            time.sleep(MINIAPP_CLICK_STAY_SECONDS)
                            return {"ok": True, "msg": "已跳转到小程序"}
                        if loc.startswith("http://") or loc.startswith("https://"):
                            current = loc
                            continue
                        return {"ok": False, "msg": f"跳转地址不明：{loc[:40]}"}
                    return {"ok": False, "msg": f"HTTP {resp.status_code}"}
                return {"ok": False, "msg": "跳转超过 5 跳"}
            except Exception as ex:
                last_err = ex
                continue
        return {"ok": False, "msg": f"请求异常：{last_err}"}

    def ai_guess_price(self, product_text: str) -> dict:
        """1.5.34：AI 猜价。OpenAI 兼容接口，需配环境变量：
        ELM_AI_API_BASE（如 https://api.xxx.com/v1）、ELM_AI_API_KEY、
        ELM_AI_MODEL（默认 gpt-4o-mini）。未配置返回 ok=False，不抛错。"""
        base = (cfg("ELM_AI_API_BASE", "") or "").rstrip("/")
        key = cfg("ELM_AI_API_KEY", "") or ""
        model = cfg("ELM_AI_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
        if not base or not key:
            return {"ok": False, "msg": "未配置 ELM_AI_API_BASE/ELM_AI_API_KEY"}
        try:
            resp = requests.post(base + "/chat/completions", timeout=30, headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.2, "messages": [
                    {"role": "system", "content": "你是商品估价助手。只按用户给的商品信息估一个最可能的价格（人民币元，数字），先给数字再给一句话理由。"},
                    {"role": "user", "content": "请估计这个商品的价格（元）：\n" + product_text}]})
            data = resp.json()
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            m = re.search(r"(\d+(?:\.\d{1,2})?)", text)
            if not m:
                return {"ok": False, "msg": "AI 未返回数字价格"}
            return {"ok": True, "price": float(m.group(1)), "reason": text[:120]}
        except Exception as ex:
            return {"ok": False, "msg": f"AI 请求异常：{ex}"}

    def guess_price_ai(self, task: dict) -> dict:
        """1.5.34：猜价格任务的 AI 猜价入口（框架）。

        本次 HAR 只抓到游戏页打开与 GuessPriceInfo（取游戏配置），没
        抓到提交猜价那一步：提交接口名未知，且网关 Token 须经支付宝
        OAuth 换取、脚本拿不到。本版只记游戏信息（pop2me 页 + 跳转
        链接有无），不编提交接口；等用户玩一局猜价格并抓 HAR 后再接
        提交。仍走原回调路径完成本轮。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        if did != "48152003":
            return {"ok": False, "msg": "非猜价格任务"}
        extv = (task.get("ext") or {}).get("extValue") or {}
        page = POP2ME_PAGES.get(did, "page3")
        has_link = False
        for k in ("alipayActionLink", "elemeActionLink", "taobaoActionLink"):
            v = extv.get(k) or ""
            if not isinstance(v, str):
                continue
            s = v
            for _ in range(3):
                s2 = unquote(s)
                if s2 == s:
                    break
                s = s2
            if "pop2me.com/elemeweb/" in s:
                has_link = True
                break
        self._log(f"🎲 猜价格[{self.task_title(task)}]：pop2me 游戏页 {page}（跳转链接{'有' if has_link else '无'}）；提交接口待一次完整猜价抓包，见 header 1.5.34 说明")
        return {"ok": False, "msg": "猜价提交接口未知，待抓包"}

    def report_callback(self, task: dict, biz_scene: str | None = None) -> dict:
        """1.5.24：按服务端下发的回调配置 emod-afun-task-report-config
        重写。官方 H5 回调数据只由 mapRules 拼：bizScene 是常量
        "INTERACT_CENTER"（大写）；token/source/plat/key1~key6 取自
        做完任务跳回的落地页 URL query。1.5.23 错在三处：bizScene
        小写、多了官方不带的字段（eventType/eventId/collectionId/
        missionId/missionXId/lat/lng/riskScene 等）、plat 试的是容器
        名而非平台标识。本版只发 bizScene/token/source/plat 四字段；
        plat 先按 CALLBACK_PLAT 映射（闲鱼 secondfish，其余
        alipay_task），500 再按 CALLBACK_PLAT_RETRY 顺序试；
        ELM_CALLBACK_PLAT 仍可强制指定。缺参等非 500 错立即返回。
        token 取法不变：实时 ext 跳转链接 → missionIds 定向查询 →
        CALLBACK_TOKEN 硬编码兜底。1.5.26：plat 在 400 时也重试
        （错 plat 在新 data 下可能报 400 而非 500）；跳转链接里
        的 tagType（如 didi/fish）也当 plat 候选。1.5.28：补发
        scenceCode，key3=任务类型）。1.5.31：1.5.30 的跳过已撤掉——
        1.5.29 的硬编码 token 是 00:18/00:43 的旧值（7 小时+），
        失效可能只是过期；08:11 的 token 是新鲜的，先发回调试一次。
        若新鲜 token 能领，说明 token 有时效而非会话锁定。"""
        source = ((task.get("ext") or {}).get("extValue") or {}).get("interchangeBizType") \
            or task.get("interchangeBizType") \
            or CALLBACK_SOURCE.get(str(task.get("missionDefId") or task.get("missionId") or "")) \
            or "inter_change"
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        token = extract_action_token(task)
        if not token:
            token = self.fetch_task_token(did, task.get("missionCollectionId") or task.get("missionCollectId") or "3112")
        if not token:
            # 1.5.20：最后兜底——HAR 里抓到的 token（会话相关，未必生效）。
            token = CALLBACK_TOKEN.get(did)
            if token:
                self._log(f"⚠ 任务[{task.get('showTitle')}]：回调 token 用 HAR 硬编码兜底（当前会话取不到），是否生效看服务端")
        if not token:
            self._log(f"⚠ 任务[{task.get('showTitle')}]：回调缺 token（任务无跳转链接、定向查询与硬编码均未取到），本次回调大概率仍报缺 token")
        # 1.5.24：plat 是平台标识。ELM_CALLBACK_PLAT 优先；其次按任务
        # 映射；最后按重试表顺序。1.5.26：400 时也换下一个试（错 plat
        # 在新 data 下可能报 400 而非 500）；跳转链接里的 tagType
        # （如 didi/fish）也当 plat 候选。
        tag_plat = None
        _extv = (task.get("ext") or {}).get("extValue") or {}
        for _lk in ("alipayActionLink", "elemeActionLink", "taobaoActionLink"):
            _link = _extv.get(_lk) or ""
            _s = _link
            for _ in range(3):
                _s2 = unquote(_s)
                if _s2 == _s:
                    break
                _s = _s2
            _m = re.search(r"[?&]tagType=([A-Za-z0-9_]+)", _s)
            if _m:
                tag_plat = _m.group(1)
                break
        plats = []
        for p in [cfg("ELM_CALLBACK_PLAT", ""), CALLBACK_PLAT.get(did), tag_plat] + CALLBACK_PLAT_RETRY:
            if p and p not in plats:
                plats.append(p)
        # 官方回调只含 mapRules 的字段：bizScene（大写常量）/token/
        # source/plat/key1~key6。1.5.28：补发 key1~key3（alipay_welfare
        # 规则要的；key1=source，key2=iepTaskSceneCode，key3=
        # iepTaskType）。key2/key3 的真值只在落地页 URL 里，脚本没有，
        # 先用任务数据猜：key2=任务 scenceCode（默认 inter_change），
        # key3=任务类型（默认 THIRD）；ELM_CALLBACK_KEY2/
        # ELM_CALLBACK_KEY3 可改。key4~key6 只在 secondfish 规则里
        # 有（taskextkey4/5/6），alipay_welfare 不用，不发。之前多发
        # 的 eventType/eventId/collectionId/missionId/missionXId/
        # lat/lng/riskScene 等官方回调根本不带，本版仍不发。
        data = {"bizScene": "INTERACT_CENTER", "source": source}
        if token:
            data["token"] = token
        data["key1"] = source
        data["key2"] = cfg("ELM_CALLBACK_KEY2", "") or _scene_code_of(task) or "inter_change"
        data["key3"] = cfg("ELM_CALLBACK_KEY3", "") or (task.get("actionType") or task.get("taskType") or "THIRD")
        last = {"ok": False, "msg": "未发起回调"}
        for plat in plats:
            data["plat"] = plat
            try:
                res = self.parse_ret(self.mtop(API["callback"], "1.0", dict(data), retry=1))
            except Exception:
                res = {"ok": False, "msg": "请求异常，结果未知"}
            if res.get("ok"):
                if plat != plats[0]:
                    self._log(f"✅ 任务[{task.get('showTitle')}]：回调 plat={plat} 通了（之前 {plats[0]} 报 500）")
                return res
            last = res
            msg = str(res.get("msg") or "")
            if "500" in msg or "400" in msg:
                # 1.5.26：错 plat 在新 data 下也可能报 400，换下一个试。
                self._log(f"⚠ 任务[{task.get('showTitle')}]：回调 plat={plat} 报 {msg.split('::')[0]}，换下一个试")
                continue
            # 缺参等别的错与 plat 无关，不再试。
            return res
        return last

    def process_tasks(self, collection_id: str, tasks: list[dict], biz_scene: str | None = None) -> dict:
        """接取/上报/领奖分别查询确认；不使用过期实例，不重复未知结果的写请求。
        biz_scene：3112 用 interact_center（默认），2602 天天免单用 ETMD_CHANNEL。"""
        self.describe_tasks(tasks)
        stats = {"attempted": 0, "accepted": 0, "failed": 0, "skipped": 0, "rewards": 0, "taken": 0,
                 "confirmed": 0, "claims": 0}
        skipped, touched, halted = set(), set(), set()
        report_counts, claim_markers = {}, set()
        limited, unconfirmed = 0, set()
        # 1.5.13：领奖报 RECEIVE_MISSION_REWARD_STAGE_NOT_FINISH_ERROR
        # （阶段进度已满、但服务端阶段仍是 RUNNING，尚未翻成 FINISH）的
        # 任务不算死失败、不停轮：本地阶段打回 RUNNING，下一轮重新上报
        # 再试（上报次数仍受 cap 限制，不会无限循环）。not_ready 记下
        # 这些任务，轮末指纹没变也不因此停轮。
        not_ready = set()
        # 1.5.10：已知 H5 任务（HAR 并入的）在 Android 会话的重新查询里看不到，
        # 上报被接受后若不把进度带回来，既确认不了、也领不了奖（1.5.9 实跑：
        # 8 个接口接受、0 领奖，百度地图在手机上已变“领取奖励”但脚本没领）。
        # 所以用 known_overrides 在本地推进这些任务的阶段：上报接受 -> 标一
        # 个阶段 FINISH（下一轮当 reward 去领奖）；领奖成功 -> 该阶段标
        # SUCCESS；领奖失败 -> 阶段打回 RUNNING（下一轮可重新上报再试）。
        known_ids = {str(t.get("missionDefId")) for t in KNOWN_H5_TASKS}
        known_ids |= {str(t.get("missionDefId")) for t in self.extra_h5_tasks()}
        known_overrides: dict[str, dict] = {}
        current = tasks
        try:
            cap = max(1, min(int(self.task_times), 10))
        except (TypeError, ValueError):
            cap = 1

        def key(task):
            return str(task.get("missionCollectionId") or task.get("missionCollectId") or collection_id) + ":" + str(task.get("missionId") or task.get("missionDefId") or self.task_title(task))

        def fingerprint(task):
            return (self.task_state(task), task.get("receiveStatus"), task.get("id") or task.get("instanceId"),
                    self._task_count(self._task_dict(task.get("stage")).get("count")),
                    tuple((s.get("status"), s.get("rewardStatus"), s.get("stageCount"))
                          for s in (task.get("missionStageDTOS") or []) if isinstance(s, dict)))

        # Each report round may need a separate take/claim round, all are bounded.
        for _ in range(cap * 3 + 3):
            sent = {}
            ordered = list(current)
            if HUMAN:
                random.shuffle(ordered)
            for task in ordered:
                if self.risk_flagged or limited >= 2:
                    break
                ident = key(task)
                if ident in halted:
                    continue
                state = self.task_state(task)
                mode, reason = self.task_plan(task)
                cur_marker = None
                if state == "reward":
                    mode = "claim"  # The server already confirms completion, including manual tasks.
                    # 1.5.40：本轮已领过或已判定领不到的阶段不再领（跨集合去重 +
                    # NOT_FINISH 上限），否则 3112 与天天免单两轮、以及乐观
                    # FINISH 的死循环会把同一阶段反复领几十次。
                    _cidx = self.reward_stage_index(task)
                    if _cidx >= 0 and self._claim_stage_blocked(task, _cidx):
                        continue
                    marker = (ident, repr(fingerprint(task)), _cidx)
                    if marker in claim_markers:
                        continue
                    claim_markers.add(marker)
                    cur_marker = marker
                elif mode == "skip":
                    if ident not in skipped and ident not in touched:
                        self._log(f"⏭ 跳过任务[{self.task_title(task)}]：{reason}")
                        skipped.add(ident)
                    continue
                elif mode != "take":
                    # 1.5.34：单任务上限按剩余阶段数放开（多阶段任务多报几次），封顶 10。
                    task_cap = min(10, max(cap, self._task_remaining_stages(task)))
                    if report_counts.get(ident, 0) >= task_cap or self._task_remaining(task, task_cap) == 0:
                        continue
                    report_counts[ident] = report_counts.get(ident, 0) + 1
                touched.add(ident)
                sent[ident] = fingerprint(task)
                if mode == "claim":
                    stats["claims"] += 1
                else:
                    stats["attempted"] += 1
                try:
                    if mode == "take":
                        result = self.take_task(task, biz_scene)
                    elif mode == "claim":
                        # 1.5.46：48152003/48324002 两进式第二进的领奖——先重查
                        # 任务列表确认状态，失败就等一会儿再试，最多 3 次
                        # （用户：返回 App 后多点两次“领取”才提示完成）。
                        _did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if _did in POP2ME_CLAIM_RETRY_IDS:
                            result = self._claim_pop2me_with_retry(task, biz_scene, collection_id)
                        else:
                            result = self.receive_prize(task, biz_scene)
                    elif mode == "pageview":
                        result = self.report_pageview(task, biz_scene)
                    else:
                        result = self.report_trigger(task, biz_scene)
                except Exception:
                    result = {"ok": False, "msg": "请求异常，结果未知，不自动重试该任务"}
                if result.get("ok"):
                    if mode == "claim":
                        stats["rewards"] += 1
                        self._log(f"🎁 已领取[{self.task_title(task)}]：{self.prize_summary(result.get('data') or {}) or '奖励'}")
                        # 1.5.40：记下本轮已领的阶段，同一任务在天天免单集合里
                        # 再出现时不再重复领（否则服务端报 TARGET_HAD_RECEIVE）。
                        _ck = self._claim_key(task, self.reward_stage_index(task))
                        if _ck:
                            self._claimed_stages.add(_ck)
                        # 已知 H5 任务：领奖成功，把刚领的阶段标 SUCCESS，剩余
                        # RUNNING 阶段下一轮还能继续上报（多阶段任务 0/3 用）。
                        did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if did in known_ids:
                            base = known_overrides.get(did) or self._known_task_copy(did) or task
                            idx = self.reward_stage_index(base)
                            if idx >= 0:
                                (base.get("missionStageDTOS") or [])[idx]["rewardStatus"] = "SUCCESS"
                            known_overrides[did] = base
                    else:
                        stats["accepted"] += 1
                        if mode == "take":
                            stats["taken"] += 1
                        self._log(f"✅ 接口接受[{self.task_title(task)}]；继续查询进度，尚不认定任务完成")
                        # 已知 H5 任务：重新查询看不到它，接口接受即当完成一
                        # 个阶段（下一轮当 reward 去领奖）；服务端若其实没
                        # 完成，领奖会失败、阶段再打回 RUNNING 重试。
                        did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if did in known_ids and mode in ("pageview", "trigger"):
                            base = known_overrides.get(did) or self._known_task_copy(did) or task
                            if self._mark_stage_claimable(base):
                                known_overrides[did] = base
                else:
                    message = str(result.get("msg") or "未返回结果")
                    # 1.5.21：trigger 报 417（bizScene 过滤）时，直接调官方
                    # H5 的 new.callback，不再试 trigger 换 bizScene 重试。
                    # 1.5.20 实跑证明那重试是假成功：服务端收了
                    # interact_center 的 trigger（不报 417），但不算任务
                    # 完成，阶段永远不翻 FINISH——实时列表的 3 个因此在
                    # 轮末指纹不变被 halt，KNOWN 的 6 个三轮全撞
                    # STAGE_NOT_FINISH。官方 H5 根本不调 trigger，只调
                    # callback，inter_change 任务只能走回调完成。
                    if mode == "trigger" and "417" in message:
                        # 1.5.34：先试合作方回调（App 的真实完成路径），再走 new.callback。
                        try:
                            pc = self.report_partner_callback(task)
                        except Exception:
                            pc = {"ok": False, "msg": "请求异常，结果未知"}
                        if pc.get("ok"):
                            stats["accepted"] += 1
                            self._log(f"✅ 合作方回调已发[{self.task_title(task)}]（rookieCallbackTransfer）；继续走 new.callback，阶段是否翻看重新查询")
                            not_ready.add(ident)
                        # 1.5.37：闲鱼任务再试 IdleFish/callback（App 的真实
                        # 完成信号，19:06 HAR 实抓 code=0）；成功也当一阶
                        # 段完成，下一轮领奖。
                        _did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if (PARTNER_CALLBACK.get(_did) or {}).get("tagType") == "fish":
                            try:
                                fc = self.report_fish_callback(task)
                            except Exception:
                                fc = {"ok": False, "msg": "请求异常，结果未知"}
                            if fc.get("ok"):
                                stats["accepted"] += 1
                                if _did in known_ids:
                                    base = known_overrides.get(_did) or self._known_task_copy(_did) or task
                                    if self._mark_stage_claimable(base):
                                        known_overrides[_did] = base
                                not_ready.add(ident)
                        # 1.5.46：猜价格 48152003 先试 pop2me 两进式第一进
                        # （CroInfo→GuessPriceInfo→GuessPriceCallbackByMe，
                        # 2026-11-22 04:55 实抓，网关版本 4；deviceId 调用就地
                        # 最多重试 3 次，无新鲜 deviceId 时跳过、不崩整轮）。
                        # 成功也当一段完成，继续走 new.callback，阶段是否翻看重新查询。
                        if str(task.get("missionDefId") or task.get("missionId") or "") == "48152003":
                            try:
                                gp = self.report_pop2me_guess_price(task)
                            except Exception:
                                gp = {"ok": False, "msg": "请求异常，结果未知"}
                            if gp.get("ok"):
                                stats["accepted"] += 1
                                did = str(task.get("missionDefId") or task.get("missionId") or "")
                                if did in known_ids:
                                    base = known_overrides.get(did) or self._known_task_copy(did) or task
                                    if self._mark_stage_claimable(base):
                                        known_overrides[did] = base
                                not_ready.add(ident)
                                self._log(f"✅ 猜价格 pop2me 已完成[{self.task_title(task)}]（GuessPriceCallbackByMe errorCode=0）；继续走 new.callback，阶段是否翻看重新查询")
                            else:
                                self._log(f"⚠ 任务[{self.task_title(task)}]：猜价格 pop2me 未接受（{str(gp.get('msg') or '无返回')[:80]}），继续走 new.callback")
                        # 1.5.39：飞猪再试 polymorphism 回调（00:31 HAR
                        # 1523 条实抓的真完成信号，callbackToken 实时
                        # 取）。sign 是 MD5 密钥未知，服务端现阶段只
                        # 会回 -2001 签名错误，属已知阻断，不影响其他
                        # 任务；一旦拿到小程序签名密钥即可启用。
                        if str(task.get("missionDefId") or task.get("missionId") or "") == "48282001":
                            try:
                                pm = self.report_polymorphism_callback(task)
                            except Exception:
                                pm = {"ok": False, "msg": "请求异常，结果未知"}
                            if pm.get("ok"):
                                stats["accepted"] += 1
                                not_ready.add(ident)
                            else:
                                self._log(f"⚠ 任务[{self.task_title(task)}]：飞猪 polymorphism 未接受（{str(pm.get('msg') or '无返回')[:60]}），继续走 new.callback")
                        # 1.5.48：滴滴/飞猪真网点击（用户发的支付宝小程序分享
                        # 链接，见 MINIAPP_CLICK_URLS）：原有尝试之后、
                        # new.callback 之前，真 GET 一次短链并跟跳转，模拟
                        # 真人点开小程序再回来。点开也当一阶段完成（继续走
                        # new.callback，阶段是否翻看重新查询）；点不开只记
                        # 一行日志，不影响其他任务。
                        _did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if _did in MINIAPP_CLICK_URLS:
                            try:
                                ck = self.click_miniapp_share(task)
                            except Exception:
                                ck = {"ok": False, "msg": "请求异常，结果未知"}
                            if ck.get("ok"):
                                stats["accepted"] += 1
                                not_ready.add(ident)
                                self._log(f"✅ 小程序分享链接已点开[{self.task_title(task)}]（{ck.get('msg') or ''}）；继续走 new.callback，阶段是否翻看重新查询")
                            else:
                                self._log(f"⚠ 任务[{self.task_title(task)}]：小程序分享链接未点开（{str(ck.get('msg') or '无返回')[:60]}），继续走 new.callback")
                        # 1.5.41：天天打卡 48432001 再试 pop2me（BrowseTaskStart→等15秒→
                        # BrowseTaskComplete，2026-11-21 实抓链路已通；身份须新鲜，
                        # 无缓存又无 authCode 时跳过、不崩整轮）。
                        if str(task.get("missionDefId") or task.get("missionId") or "") == "48432001":
                            try:
                                ck = self.report_pop2me_checkin(task)
                            except Exception:
                                ck = {"ok": False, "msg": "请求异常，结果未知"}
                            if ck.get("ok"):
                                stats["accepted"] += 1
                                did = str(task.get("missionDefId") or task.get("missionId") or "")
                                if did in known_ids:
                                    base = known_overrides.get(did) or self._known_task_copy(did) or task
                                    if self._mark_stage_claimable(base):
                                        known_overrides[did] = base
                                not_ready.add(ident)
                                self._log(f"✅ 打卡 pop2me 已完成[{self.task_title(task)}]（BrowseTaskComplete FINISHED）；继续走 new.callback，阶段是否翻看重新查询")
                            else:
                                self._log(f"⚠ 任务[{self.task_title(task)}]：打卡 pop2me 未接受（{str(ck.get('msg') or '无返回')[:80]}），继续走 new.callback")
                        # 1.5.46：页面点击 48324002 再试 pop2me 两进式第一进
                        # （DhTaskStart→DhTaskComplete，2026-11-22 04:14 实抓，
                        # 三支全 FINISH；三支各用自己的 appId/deviceId，deviceId
                        # 调用就地最多重试 3 次，无新鲜 deviceId 时跳过、不崩整轮）。
                        if str(task.get("missionDefId") or task.get("missionId") or "") == "48324002":
                            try:
                                dh = self.report_pop2me_dh_signed(task)
                            except Exception:
                                dh = {"ok": False, "msg": "请求异常，结果未知"}
                            if dh.get("ok"):
                                stats["accepted"] += 1
                                did = str(task.get("missionDefId") or task.get("missionId") or "")
                                if did in known_ids:
                                    base = known_overrides.get(did) or self._known_task_copy(did) or task
                                    if self._mark_stage_claimable(base):
                                        known_overrides[did] = base
                                not_ready.add(ident)
                                self._log(f"✅ 页面点击 pop2me 已完成[{self.task_title(task)}]（DhTaskComplete FINISHED）；继续走 new.callback，阶段是否翻看重新查询")
                            else:
                                self._log(f"⚠ 任务[{self.task_title(task)}]：页面点击 pop2me 未接受（{str(dh.get('msg') or '无返回')[:80]}），继续走 new.callback")
                        try:
                            cb = self.report_callback(task, biz_scene)
                        except Exception:
                            cb = {"ok": False, "msg": "请求异常，结果未知"}
                        if cb.get("ok"):
                            stats["accepted"] += 1
                            self._log(f"✅ 回调接受[{self.task_title(task)}]（new.callback）；继续查询进度，尚不认定任务完成")
                            did = str(task.get("missionDefId") or task.get("missionId") or "")
                            if did in known_ids:
                                base = known_overrides.get(did) or self._known_task_copy(did) or task
                                if self._mark_stage_claimable(base):
                                    known_overrides[did] = base
                            # 回调接受不等于阶段已翻：服务端翻阶段可能慢一
                            # 轮，记进 not_ready，轮末指纹没变也不 halt。
                            not_ready.add(ident)
                            nap_human(1.6)
                            continue
                        self._log(f"⚠ 任务[{self.task_title(task)}]：回调亦拒（{str(cb.get('msg') or '无返回')[:60]}），保留原 417")
                    # 1.5.13：领奖报 STAGE_NOT_FINISH_ERROR（阶段进度已满、
                    # 但服务端阶段仍是 RUNNING）不是死失败：只是服务端还没
                    # 把阶段翻成 FINISH。记进 not_ready、不停轮，本地阶段
                    # 打回 RUNNING 后下一轮重新上报再试；轮末指纹没变也不
                    # 因此停轮。missionXId 已在 trigger/receiveprize 里都
                    # 带了，这错与 missionXId 无关，改它修不了。
                    stage_not_finish = (mode == "claim" and
                                        "RECEIVE_MISSION_REWARD_STAGE_NOT_FINISH_ERROR" in message)
                    # 1.5.37：TARGET_HAD_RECEIVE_ERROR（奖励已全部领奖）
                    # 的正确处理是把该阶段标成已领（SUCCESS），不再打回
                    # RUNNING 死磕同一阶段。1.5.36 的做法（按
                    # STAGE_NOT_FINISH 同样处理、下一轮重新上报再试）实
                    # 跑证明是错的：8164 同一阶段重复领 7 次、155 次领奖
                    # 请求全失败，一轮耗时 24 分钟、0 份领到。已领过的阶
                    # 段标 SUCCESS 后，下一轮直接看下一阶段，多阶段任务
                    # 才能一轮连推；去重标记不清掉，同一指纹+阶段不再重
                    # 复领奖。STAGE_NOT_FINISH 仍按 1.5.13 处理（服务端
                    # 还没翻阶段，打回 RUNNING 下一轮重新上报再试）。
                    already_received = (mode == "claim" and
                                        "RECEIVE_MISSION_REWARD_TARGET_HAD_RECEIVE_ERROR" in message)
                    # 1.5.52：RISK_USER（2026-11-23 用户纠正）：H5 网页领奖时
                    # 百川回 NO_DECISION_ITEM # 风控用户，ret 是
                    # RECEIVE_MISSION_REWARD_RECEIVE_ALL_ERROR。RISK_USER 只
                    # 在 data.REWARD_UPP 里，不在 ret 里，所以要连 data 一起
                    # 查。这不是账号被风控——同一任务 App 内可正常领取，只是
                    # H5/网页渠道领不到。不要把它当成账号风控、不要 halt 整号。
                    risk_user = (mode == "claim" and
                                 ("RISK_USER" in message or
                                  "RISK_USER" in json.dumps(result.get("data") or {}, ensure_ascii=False)))
                    # 1.5.49：领奖遇网络/请求异常（请求没到服务端或响应丢
                    # 了）不是服务端拒绝——之前走 else 直接 halt，本轮再也
                    # 不领，奖励就丢了。
                    claim_net_error = (mode == "claim" and
                                       ("请求异常" in message or "网络请求失败" in message))
                    if not result.get("skip"):
                        stats["failed"] += 1
                    if already_received:
                        not_ready.add(ident)
                        did = str(task.get("missionDefId") or task.get("missionId") or "")
                        idx = self.reward_stage_index(task)
                        if did in known_ids and did in known_overrides and idx >= 0:
                            sts = known_overrides[did].get("missionStageDTOS") or []
                            if idx < len(sts) and isinstance(sts[idx], dict):
                                sts[idx]["rewardStatus"] = "SUCCESS"
                        # 1.5.40：服务端说已领过就记下来，跨集合也不再重复领。
                        _ck = self._claim_key(task, idx)
                        if _ck:
                            self._claimed_stages.add(_ck)
                        self._log(f"⏳ 任务[{self.task_title(task)}]：该阶段奖励已领过，跳过本阶段看下一阶段")
                    elif risk_user:
                        not_ready.add(ident)
                        # 本轮封死该阶段（不再重试领奖），不 halt、不打回
                        # RUNNING；奖励若还没领，用户在 App 内领取。
                        _ck = self._claim_key(task, self.reward_stage_index(task))
                        if _ck:
                            self._claim_blocked.add(_ck)
                        self._log(f"ℹ️ 任务[{self.task_title(task)}]：H5 网页无法领取（App 可领），非账号风控；本轮不再试领奖，奖励请在 App 内领取")
                    elif stage_not_finish:
                        not_ready.add(ident)
                        # 1.5.13：把这次领奖的去重标记清掉，否则下一轮
                        # 阶段回到同一状态（FINISH/TODO）时会被当成重复
                        # 领奖跳过，永远领不到。
                        if cur_marker is not None:
                            claim_markers.discard(cur_marker)
                        # 1.5.40：同一阶段连续 NOT_FINISH 达到上限就本轮封死，
                        # 不再乐观标 FINISH、也不再领奖（六个顽固任务的阶段
                        # 服务端永远不翻，旧版每轮白打 10 次以上）。
                        _blocked_now = False
                        _ck = self._claim_key(task, self.reward_stage_index(task))
                        if _ck:
                            _n = self._claim_not_finish.get(_ck, 0) + 1
                            self._claim_not_finish[_ck] = _n
                            if _n >= self._claim_max_retries():
                                self._claim_blocked.add(_ck)
                                _blocked_now = True
                                self._log(f"⛔ 任务[{self.task_title(task)}]：阶段连续 {_n} 次领奖未完成（服务端未翻 FINISH），本轮不再尝试领奖")
                        if not _blocked_now:
                            self._log(f"⏳ 任务[{self.task_title(task)}]：阶段未完成（进度已满、服务端仍 RUNNING），下一轮重新上报再试")
                    elif claim_net_error:
                        not_ready.add(ident)
                        # 去重标记清掉，下一轮阶段回到同一状态时可重新领；
                        # 不计入 _claim_not_finish（不是阶段未翻），也不
                        # 打回 RUNNING（下面排除 claim_net_error），阶段
                        # 保持 FINISH，下一轮直接重试领奖，不用重新上报。
                        if cur_marker is not None:
                            claim_markers.discard(cur_marker)
                        self._log(f"⏳ 任务[{self.task_title(task)}]：领奖请求异常（网络），下一轮重试")
                    else:
                        halted.add(ident)
                    self._log(f"⚠ 任务[{self.task_title(task)}]：{message[:80]}")
                    if "405" in message or "受限" in message:
                        limited += 1
                    # 已知 H5 任务：领奖失败说明服务端还没算它完成，把刚标
                    # FINISH 的阶段打回 RUNNING，下一轮可重新上报再试。
                    # 1.5.37：already_received（奖励已领过）不打回——上面
                    # 已标 SUCCESS，打回会让同一阶段死循环重试。
                    # 1.5.49：claim_net_error（网络异常）也不打回——阶段
                    # 可能已在服务端翻成 FINISH，打回会让它重新上报白跑
                    # 一轮；保持 FINISH，下一轮直接重试领奖。
                    # 1.5.52：risk_user（H5 无法领取）也不打回——阶段已是
                    # FINISH，打回只会让它重新上报再撞一次 RISK_USER。
                    if mode == "claim" and not already_received and not claim_net_error and not risk_user:
                        did = str(task.get("missionDefId") or task.get("missionId") or "")
                        if did in known_ids and did in known_overrides:
                            base = known_overrides[did]
                            for st in (base.get("missionStageDTOS") or []):
                                if isinstance(st, dict) and st.get("status") == "FINISH" and st.get("rewardStatus") != "SUCCESS":
                                    st["status"] = "RUNNING"
                                    st["rewardStatus"] = "TODO"
                                    break
                nap_human(1.6)
            if self.risk_flagged or limited >= 2 or not sent:
                break
            try:
                current = self.query_tasks(collection_id, biz_scene)
            except Exception:
                self.result["任务"] = "❌ 重新查询任务失败，未继续上报或领奖"
                self.task_stats = stats
                return self.result
            if self.risk_flagged:
                break
            # 已知 H5 任务在重新查询里看不到，重新并入（带本地推进的阶段），
            # 否则它们在第二轮就丢了、永远领不了奖。
            if biz_scene in (None, BIZ_SCENE):
                current = self.merge_known_h5_tasks(current, known_overrides)
            indexed = {key(task): task for task in current}
            for ident, before in sent.items():
                if ident in indexed:
                    if fingerprint(indexed[ident]) == before:
                        # 1.5.13：STAGE_NOT_FINISH 的任务指纹没变是预期的
                        # （服务端还没翻阶段），不停轮、不记未确认，下一轮
                        # 重新上报再试；用完就从 not_ready 里清掉。
                        if ident in not_ready:
                            not_ready.discard(ident)
                            continue
                        halted.add(ident)
                        unconfirmed.add(ident)
                    else:
                        stats["confirmed"] += 1
        stats["skipped"] = len(skipped)
        self.task_stats = stats
        prefix = "⛔" if self.risk_flagged else ("⚠" if stats['failed'] or limited >= 2 else ("✅" if stats['rewards'] else "➖"))
        summary = (f"{prefix} 已领 {stats['rewards']} 份；接取确认 {stats['taken']} 项；"
                   f"任务进度确认 {stats['confirmed']} 次；领奖请求 {stats['claims']} 次；"
                   f"接取/执行 {stats['attempted']} 次/接口接受 {stats['accepted']} 次；"
                   f"失败 {stats['failed']} 次；明确跳过 {stats['skipped']} 项")
        if unconfirmed:
            summary += f"；{len(unconfirmed)} 项进度未确认，未盲目补跑"
        if limited >= 2:
            summary += "；行为受限已停止本轮"
        if self.risk_flagged:
            summary += "；用户验证要求，已停止后续请求"
        self.result["任务"] = summary
        return self.result

    # ---- 领奖 ----
    @staticmethod
    def reward_stage_index(task: dict) -> int:
        for i, st in enumerate(task.get("missionStageDTOS") or []):
            st = st or {}
            sa = st.get("sourceAction") or {}
            if (st.get("status") == "FINISH" and st.get("rewardStatus") != "SUCCESS"
                    and sa.get("actionSubtype") != "NO_REWARD"
                    and sa.get("actionSubSubType") not in ("REWARD_SEND_AUTO", "NOTIFY_REACH_STAGE")):
                return i
        return -1

    @staticmethod
    def _claim_key(task: dict, idx: int) -> tuple[str, int] | None:
        """领奖去重键：(missionDefId, stageCount)。stageCount 是服务端的阶段号，
        比下标稳定（单阶段任务 stageCount=3 但只有 1 个阶段，见 1.5.38）。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        stages = task.get("missionStageDTOS") or []
        if not did or idx < 0 or idx >= len(stages):
            return None
        st = stages[idx] or {}
        try:
            return (did, int(st.get("stageCount") or (idx + 1)))
        except (TypeError, ValueError):
            return (did, idx + 1)

    def _claim_max_retries(self) -> int:
        """同一阶段 STAGE_NOT_FINISH 最多试几次（ELM_CLAIM_RETRIES，默认 2）。
        5 秒 ELM_CLAIM_WAIT + 整轮间隔已够服务端翻阶段；两次都未翻就是真翻
        不了（六个顽固任务实测每轮都失败），再试只是白打请求。"""
        try:
            return max(1, int(cfg("ELM_CLAIM_RETRIES", "2") or 2))
        except (TypeError, ValueError):
            return 2

    def _claim_stage_blocked(self, task: dict, idx: int) -> bool:
        """该阶段本轮是否已领过或已判定领不到。"""
        key = self._claim_key(task, idx)
        return bool(key) and (key in self._claimed_stages or key in self._claim_blocked)

    def _mark_stage_claimable(self, task: dict) -> bool:
        """乐观标 FINISH 前先看下一个阶段是否已被判定领不到/已领过；是就
        不再标，避免下一轮白领一次（STAGE_NOT_FINISH / TARGET_HAD_RECEIVE）。
        阶段是顺序的：下一个阶段领不到，后面的也领不了，跳过整段标记是对的。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        for st in (task.get("missionStageDTOS") or []):
            if isinstance(st, dict) and st.get("status") != "FINISH":
                try:
                    sc = int(st.get("stageCount") or 0)
                except (TypeError, ValueError):
                    sc = 0
                if (did, sc) in self._claim_blocked or (did, sc) in self._claimed_stages:
                    return False
                break
        return self._mark_stage(task, "FINISH", "TODO")

    def _claim_pop2me_with_retry(self, task: dict, biz_scene: str | None = None,
                                 collection_id: str = "3112") -> dict:
        """1.5.46：48152003/48324002 两进式第二进的领奖（用户 2026-11-22 睡前
        补充：进入页面做完任务、返回饿了么 App 后，多点两次“领取”才提示完成）。
        pop2me 做完后服务端阶段可能还没翻成 FINISH，所以每次领奖前先重新拉
        一次任务列表确认状态；第一次领奖失败（无可领奖阶段 / STAGE_NOT_FINISH）
        就等 POP2ME_CLAIM_WAIT 秒再试，最多 POP2ME_CLAIM_MAX_ATTEMPTS=3 次，
        不要一次失败就放弃。只用于 POP2ME_CLAIM_RETRY_IDS 的任务；其他任务
        仍走 receive_prize 一次。重查失败不抛错，用上一份任务数据继续试一次。
        返回最后一次 receive_prize 的结果。"""
        did = str(task.get("missionDefId") or task.get("missionId") or "")
        last: dict = {"ok": False, "msg": "未尝试领奖"}
        cur = task
        for attempt in range(1, POP2ME_CLAIM_MAX_ATTEMPTS + 1):
            try:
                fresh = self.query_tasks(collection_id, biz_scene, mission_ids=[did])
                for t in fresh:
                    if str(t.get("missionDefId") or t.get("missionId") or "") == did:
                        cur = t
                        break
            except Exception as ex:
                self._log(f"⚠ 领奖前重查任务列表失败（第 {attempt} 次）：{ex}，用旧数据继续试")
            last = self.receive_prize(cur, biz_scene)
            if last.get("ok"):
                return last
            if attempt < POP2ME_CLAIM_MAX_ATTEMPTS:
                self._log(f"⚠ 领奖第 {attempt} 次未成功（{str(last.get('msg') or '')[:60]}），{POP2ME_CLAIM_WAIT} 秒后再试（最多 {POP2ME_CLAIM_MAX_ATTEMPTS} 次）")
                time.sleep(POP2ME_CLAIM_WAIT)
        return last

    def receive_prize(self, task: dict, biz_scene: str | None = None) -> dict:
        idx = self.reward_stage_index(task)
        if idx < 0:
            return {"ok": False, "skip": True, "msg": "无可领奖阶段（已领完或未完成）"}
        # 1.5.14：上报接受后服务端把阶段翻成 FINISH 要时间，领奖前等一
        # 等（默认 5 秒，ELM_CLAIM_WAIT 可调，0 为不等待），否则阶段仍
        # 是 RUNNING 会报 RECEIVE_MISSION_REWARD_STAGE_NOT_FINISH_ERROR。
        # H5 会话也会报这错，说明与身份/会话无关，纯是服务端翻阶段慢。
        try:
            wait = float(cfg("ELM_CLAIM_WAIT", "5"))
        except (TypeError, ValueError):
            wait = 5.0
        if wait > 0:
            time.sleep(wait)
        loc = json.dumps({"lng": self.lng, "lat": self.lat}, separators=(",", ":"))
        data = {
            "bizScene": biz_scene or BIZ_SCENE,
            "accountPlan": ACCOUNT_PLAN,
            "longitude": self.lng,
            "latitude": self.lat,
            "locationInfos": json.dumps([loc], separators=(",", ":")),
            "missionCollectionId": task.get("missionCollectionId") or task.get("missionCollectId"),
            "missionId": task.get("missionId") or task.get("missionDefId"),
            "missionXId": task.get("missionXId") or "",
            "extInfo": json.dumps({"triggerScene": "MISSION_LIST"}, separators=(",", ":")),
        }
        if task.get("instanceId") or task.get("id"):
            data["instanceId"] = task.get("instanceId") or task.get("id")
        st = (task.get("missionStageDTOS") or [])[idx] or {}
        # 1.5.11：领奖必须带阶段参数 count（不带会被
        # CLIENT_PARAM_STAGE_ERROR 拒）。1.5.37：count 发阶段号
        # （idx+1）——1.5.36 固定发 1 是错的：19:06 HAR 里 App 的 5 次
        # 成功领奖 count 是 2/3/4/5（阶段号）；1.5.36 实跑 8164 领 0 份、
        # 155 次领奖请求全失败，其中页面点击 2/3（第 3 阶段未领）用
        # count=1 也报 TARGET_HAD_RECEIVE（服务端把 1 认作第 1 阶段，
        # 该阶段奖励已领过）。17:16/11-18 HAR 的 4 次 count=1 都是第 1
        # 阶段或单阶段任务，不矛盾。阶段仍由 missionXId/instanceId 定位。
        # 1.5.38：count 发该阶段的 stageCount，不是 idx+1。阶段号不一定
        # 等于下标+1：单阶段任务（在页面内点击3个店铺，stageCount=3，
        # 只有 1 个阶段）与里程碑任务（今日累计任务奖励，stageCount
        # 3/6/9）用 idx+1 会发 count=1，服务端报
        # RECEIVE_MISSION_REWARD_STAGE_NOT_EXIST_ERROR，奖励永远领不到。
        # 19:06 HAR 里 App 的成功领奖 count 2/3/4/5 都是 stageCount；
        # 顺序任务 stageCount == idx+1，两者一致，不影响已跑通的任务。
        data["count"] = st.get("stageCount") or (idx + 1)
        if st.get("stageSum") is not None:
            data["sum"] = st["stageSum"]
        # 1.5.49：领奖重试由 1 改 3。mtop(retry=1) 只发一次，网络抖动
        # （超时/断连，青龙到 waimai-guide 的链路常有）就直接丢奖励；
        # 重试安全：第一次若已成功但响应丢了，重发服务端回
        # TARGET_HAD_RECEIVE，process_tasks 已按“已领过”处理，不会
        # 重复发奖。pageview/trigger/callback 仍保持 retry=1，避免
        # 重复上报多算进度。
        return self.parse_ret(self.mtop(API["receiveprize"], "1.0", data, {"asac": ASAC["PRIZE"]}, retry=3))


    @staticmethod
    def prize_name(item: dict) -> str:
        """奖励展示名：兼容 materialInfo / rightMaterialInfoDTO 两套字段，兜底用币种名。"""
        item = item or {}
        for holder in ("materialInfo", "rightMaterialInfoDTO", "prizeMaterialInfo"):
            title = ((item.get(holder) or {}).get("title")) or ""
            if title:
                break
        else:
            title = item.get("name") or ""
        if not title:
            ptype = item.get("prizeType") or ""
            title = {"STAR": "幸运星", "INTEGRAL_PROPERTY": "积分", "COUPON": "优惠券",
                     "RED_PACKET": "红包"}.get(ptype, ptype or "奖励")
        value = item.get("prizeValue") or item.get("equityValue") or item.get("value") or ""
        if value in ("", None):
            return title
        return title if re.search(r"\d", str(title)) else f"{title} +{value}"

    @classmethod
    def prize_summary(cls, data: dict) -> str:
        return "、".join(cls.prize_name(x) for x in ((data or {}).get("rlist") or [])[:4])

    # ---------- 天天免单（ETMD，多个任务集合） ----------
    def etmd_homepage_collections(self) -> list[str]:
        """取天天免单页的全部任务集合 ID（只读首页，不签到不领奖）。
        1.5.4 只取了第一个（popupShareWinV2 的 2602），但 09:00 实跑证明
        2602 只有 6 个任务、奖励还是 +1 非星，截图那批（闪购品牌日、闲鱼看
        视频、芭芭农场、淘金币、飞猪、滴滴等，55–100 星/个）不在其中。
        首页 missionCollectionMax=4，说明页里有多个集合，这里全部找出来
        （popupShareWinV2 的排第一），逐个查任务、按 missionDefId 去重后跑。
        失败返回 []，调用方跳过、不影响 3112 的结果。"""
        data = {"bizScene": ETMD_BIZ_SCENE, "version": ETMD_HOMEPAGE_VERSION,
                "latitude": self.lat, "longitude": self.lng}
        try:
            r = self.parse_ret(self.mtop(ETMD_HOMEPAGE_API, "1.0", data,
                                         extra_headers={"asac": ETMD_ASAC}))
        except Exception as exc:  # noqa: BLE001
            self._log(f"⚠ 天天免单首页异常：{exc}")
            return []
        if not r["ok"]:
            self._log(f"⚠ 天天免单首页失败：{r['msg']}，跳过天天免单")
            return []
        d = r["data"] or {}
        found: list[str] = []

        def add(v) -> None:
            if v is None:
                return
            s = str(v)
            if s and s not in found:
                found.append(s)

        # popupShareWinV2 的排第一（1.5.4 已验证能跑通）
        add((((d.get("mainPage") or {}).get("data") or {}).get("popupShareWinV2") or {}).get("missionCollectionId"))
        add((d.get("missionInfo") or {}).get("missionCollectionId"))

        def walk(obj) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "missionCollectionId":
                        add(v)
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(d)
        if found:
            self._log(f"天天免单任务集合：{', '.join(found)}")
        else:
            self._log("⚠ 天天免单首页未找到 missionCollectionId，跳过天天免单")
        return found

    # ---------- 编排 ----------
    def run(self) -> dict:
        self._log(f"开始｜设备 {self.dev.get('model')} / Android {self.dev.get('androidVersion')}"
                  f" / {self.dev.get('channel')}")
        if not self.token and not self.prime_token():
            self.result.setdefault("Cookie", "❌ 缺少 _m_h5_tk 且申领失败")
            self._log(self.result["Cookie"])
            return self.result

        mission_collection_id, copy_id, strategy, award, signed = "", "", "", {}, False
        hp: dict = {}
        try:
            hp = self.homepage() or {}
            mission_collection_id = hp.get("missionCollectionId", "")
            copy_id = hp.get("copyId", "")
            strategy = hp.get("signInRewardStrategy", "")
            award = hp.get("award") or {}
            signed = bool(hp.get("signedToday"))
        except Exception as exc:                                # noqa: BLE001
            self._log(f"❌ 首页聚合异常：{exc}")

        if self.risk_flagged:
            return self.finish(brief=True)
        if not hp:
            self.result["首页"] = "❌ 首页查询失败，停止本账号，未执行签到或兑换"
            return self.finish(brief=True)

        # 开跑前先播报本号现有幸运星（跑完会再报一次，方便看这轮薅到多少）
        self.star_start = hp.get("star")
        if self.star_start is None:
            self.star_start = self.refresh_star()
        if self.star_start is not None:
            self._log(f"💰 现有幸运星：{self.star_start}")
            self.result["余额"] = f"{self.star_start} 星"

        if self.risk_flagged:
            return self.finish(brief=True)
        self.signin(copy_id, strategy, award, signed)
        if self.risk_flagged:
            return self.finish(brief=True)

        # 夺宝/抽奖（首页聚合给出的 INTERACT_CENTER_LOTTERY 组件）
        try:
            self.draw_lottery(hp.get("lotteries") or [])
        except Exception as exc:                                # noqa: BLE001
            self.result["夺宝"] = f"❌ {exc}"
            self._log(f"⚠ 夺宝异常：{exc}")

        # 下单免单抽奖 / 答题（有入口才做）
        try:
            self.orderfree_draw(hp.get("orderFree") or {})
        except Exception as exc:                                # noqa: BLE001
            self._log(f"⚠ 免单抽异常：{str(exc)[:40]}")
        try:
            self.quiz()
        except Exception as exc:                                # noqa: BLE001
            self._log(f"⚠ 答题异常：{str(exc)[:40]}")

        if self.risk_flagged:                    # 账号已被标记，写请求一律不再发
            self.result["任务"] = "⛔ 风控中止（未再发写请求）"
            return self.finish(brief=True)      # 被标记就别再刷接口，只播报余额

        # 1.5.7-exec：执行器状态先打印出来，免得像 autoflow 那版一样静默停用。
        _ext = cfg("ELM_EXTERNAL_PAGEVIEW", "1")
        _skip_tasks = cfg("ELM_SKIP_TASKS") == "1"
        self._log(f"【执行器】回调/点击（THIRD trigger）与浏览（PAGEVIEW）已启用；"
                  f"外部任务通用上报 ELM_EXTERNAL_PAGEVIEW={_ext}；"
                  f"任务总开关 {'ELM_SKIP_TASKS=1（本轮不跑任务）' if _skip_tasks else '正常'}")

        # ---- 3112 幸运星任务集合（ELM_MIANDAN_ONLY=1 时只跑天天免单） ----
        tasks_3112_summary = ""
        if cfg("ELM_MIANDAN_ONLY") != "1" and cfg("ELM_SKIP_TASKS") != "1":
            if not mission_collection_id:
                tasks_3112_summary = "➖ 无 missionCollectionId，跳过"
                self._log("⚠ 未拿到 missionCollectionId，3112 任务链路终止")
            else:
                try:
                    tasks = self.query_tasks(mission_collection_id)
                    # 2026-11-17 20:30 HAR：Android 会话只回 23 项，H5 会话有 35 项。
                    # 把已知的 H5 任务并进来一起尝试（去重，不重复已返回的）。
                    merged = self.merge_known_h5_tasks(tasks)
                    if len(merged) > len(tasks):
                        self._log(f"📌 已并入 HAR 已知 H5 任务 {len(merged) - len(tasks)} 项（3112 共 {len(merged)} 项）")
                    self.process_tasks(mission_collection_id, merged)
                    # 1.5.44：实时采集本号 44914004 的助力信息，供
                    # main() 的互助排计划用（shareId 等每号不同、实时取）。
                    for _t in merged:
                        _info = self.p2p_invite_info(_t)
                        if _info:
                            _info["remark"] = self.remark
                            self.p2p_invite = _info
                            break
                    tasks_3112_summary = str(self.result.get("任务", ""))
                except Exception as exc:                                # noqa: BLE001
                    # 3112 失败不堵死天天免单：两套集合接口独立，继续跑 2602。
                    tasks_3112_summary = f"❌ 查询异常 {exc}"
                    self._log(f"⚠ 3112 任务失败，继续跑天天免单：{exc}")
        elif cfg("ELM_SKIP_TASKS") == "1":
            tasks_3112_summary = "➖ 已跳过"

        # ---- 天天免单任务集合（ETMD 首页里的全部集合，不只 2602） ----
        # 1.5.5：2602 只有 6 个任务且奖励是 +1 非星，截图那批不在其中；首页
        # missionCollectionMax=4，说明还有别的集合，这里逐个查、按任务去重后跑。
        # ELM_SKIP_MIANDAN=1 可关掉整段。
        miandan_summary = ""
        if cfg("ELM_SKIP_MIANDAN") != "1" and not self.risk_flagged:
            try:
                etmd_ids = self.etmd_homepage_collections()
                if not etmd_ids:
                    miandan_summary = "➖ 未拿到天天免单集合，跳过"
                else:
                    seen_def_ids: set[str] = set()
                    parts: list[str] = []
                    for etmd_id in etmd_ids:
                        try:
                            etmd_tasks = self.query_tasks(etmd_id, ETMD_TASK_BIZ_SCENE)
                            fresh = []
                            for t in etmd_tasks:
                                did = str(t.get("missionDefId") or t.get("missionId") or "")
                                if did and did in seen_def_ids:
                                    continue
                                if did:
                                    seen_def_ids.add(did)
                                fresh.append(t)
                            self.process_tasks(etmd_id, fresh, ETMD_TASK_BIZ_SCENE)
                            parts.append(f"{etmd_id}：{self.result.get('任务', '')}")
                        except Exception as exc:                    # noqa: BLE001
                            parts.append(f"{etmd_id}：❌ {exc}")
                            self._log(f"⚠ 天天免单集合 {etmd_id} 异常：{exc}")
                    miandan_summary = "；".join(parts)
            except Exception as exc:                                # noqa: BLE001
                miandan_summary = f"❌ 天天免单异常 {exc}"
                self._log(f"⚠ 天天免单异常：{exc}")

        if tasks_3112_summary and miandan_summary:
            self.result["任务"] = f"幸运星：{tasks_3112_summary}；天天免单：{miandan_summary}"
        elif miandan_summary:
            self.result["任务"] = f"天天免单：{miandan_summary}"
        elif tasks_3112_summary:
            self.result["任务"] = tasks_3112_summary
        if miandan_summary:
            self.result["天天免单任务"] = miandan_summary
        return self.finish(brief=self.risk_flagged)

    def finish(self, brief: bool = False) -> dict:
        """
        统一收尾（所有出口都走这里，保证每个账号的日志里都有余额一行）：
          资产/余额刷新 → 兑换商城 → 打印「现有 X → Y（本轮 +N）」→ 结果汇总
        brief=True（风控中止）时不再发任何请求，只播报已知余额。
        1.5.42：4305 那轮余额行缺失——首页没返回 star 字段时 star_start/
        star_end 都是 None，旧逻辑静默不打印。现 star_end 为空时再补拉
        一次首页，仍拿不到就打印“未知”，保证余额行永不缺失。
        """
        brief = brief or self.risk_flagged
        if not brief:
            try:
                self.wallet()                   # 内部会刷新余额（此时今日收益已入账）
            except Exception as exc:                            # noqa: BLE001
                self._log(f"⚠ 资产查询异常：{str(exc)[:40]}")
            try:
                self.mall()
            except Exception as exc:                            # noqa: BLE001
                self._log(f"⚠ 兑换商城异常：{str(exc)[:40]}")
            if self.star_end is None:
                try:
                    end = self.refresh_star()
                except Exception:                                # noqa: BLE001
                    end = None
                if end is not None:
                    self.star_end = end
        s, e = self.star_start, self.star_end
        if s is not None and e is not None and e != s:
            self._log(f"💰 幸运星 {s} → {e}（本轮 {'+' if e >= s else ''}{e - s}）")
        elif e is not None:
            self._log(f"💰 幸运星余额：{e}")
        elif s is not None:
            self._log(f"💰 幸运星余额：{s}")
        else:
            self._log("💰 幸运星余额：未知（首页未返回余额）")
        if s is not None and e is not None and e != s:
            self.result["余额"] = f"{s} → {e} 星"
        elif e is not None or s is not None:
            self.result["余额"] = f"{e if e is not None else s} 星"
        else:
            self.result["余额"] = "未知"
        line = " ".join(f"{k}:{v}" for k, v in self.result.items())
        if line:
            self._log(line)
        return self.result


# =========================================================================== #
#  通知
# =========================================================================== #

def send_notify(content: str) -> None:
    content = redact(content)
    log("\n" + "=" * 46)
    log(content)
    log("=" * 46)
    if cfg("ELM_NOTIFY", "0") != "1" or DRYRUN:
        log("[通知] 仅输出日志；需要推送请先修正通知配置，再设 ELM_NOTIFY=1")
        return
    try:
        import notify                                       # type: ignore # noqa: PLC0415
        notify.send("饿了么幸运星", content)
        log("[通知] 已通过青龙 sendNotify 推送")
        return
    except Exception:                                       # noqa: BLE001
        pass
    title = "饿了么幸运星"
    ok = False
    try:
        if cfg("PUSH_PLUS_TOKEN"):
            requests.post("https://www.pushplus.plus/send", timeout=10,
                          json={"token": cfg("PUSH_PLUS_TOKEN"), "title": title, "content": content})
            ok = True
        if cfg("PUSH_KEY"):
            requests.post(f"https://sctapi.ftqq.com/{cfg('PUSH_KEY')}.send", timeout=10,
                          data={"title": title, "desp": content})
            ok = True
        if cfg("WECOM_WEBHOOK") or cfg("QYWX_KEY"):
            url = cfg("WECOM_WEBHOOK") or cfg("QYWX_KEY")
            if not url.startswith("http"):
                url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={url}"
            requests.post(url, timeout=10, json={"msgtype": "text", "text": {"content": content}})
            ok = True
        if cfg("DINGTALK_WEBHOOK"):
            url, secret = cfg("DINGTALK_WEBHOOK"), cfg("DINGTALK_SECRET")
            if secret:
                ts = str(round(time.time() * 1000))
                sign = quote(base64.b64encode(hmac.new(secret.encode(), f"{ts}\n{secret}".encode(),
                                                       hashlib.sha256).digest()))
                url = f"{url}&timestamp={ts}&sign={sign}"
            requests.post(url, timeout=10, json={"msgtype": "text", "text": {"content": content}})
            ok = True
        if cfg("TG_BOT_TOKEN") and cfg("TG_CHAT_ID"):
            requests.post(f"https://api.telegram.org/bot{cfg('TG_BOT_TOKEN')}/sendMessage",
                          timeout=10, data={"chat_id": cfg("TG_CHAT_ID"), "text": content[:4000]})
            ok = True
        if cfg("BARK_PUSH") or cfg("BARK"):
            url = cfg("BARK_PUSH") or cfg("BARK")
            if not url.startswith("http"):
                url = f"https://api.day.app/{url}"
            requests.post(f"{url.rstrip('/')}/{quote(title)}/{quote(content[:400])}", timeout=10)
            ok = True
    except Exception as exc:                                # noqa: BLE001
        log(f"[通知] 推送异常：{exc}")
    if not ok:
        log("[通知] 未配置推送渠道，仅输出日志")


# =========================================================================== #
#  入口
# =========================================================================== #

def main() -> int:
    argv = sys.argv[1:]
    log(f"===== 饿了么幸运星 v{SCRIPT_VERSION}{' [DRYRUN]' if DRYRUN else ''} =====")
    accounts = load_accounts()
    if not accounts:
        log("❌ 未读到完整账号；ELM_COOKIE每行一个账号：备注#完整Cookie")
        return 1
    if "--dump-account" in argv or "--check-accounts" in argv or cfg("ELM_CHECK_ACCOUNTS") == "1":
        for index, (remark, cookie) in enumerate(accounts, 1):
            keys = {part.partition("=")[0].strip() for part in cookie.split(";")}
            token_present = bool(re.search(r"(?:^|;\s*)_?m_h5_tk=", cookie))
            login_present = bool({key.lower() for key in keys} & {"cookie2", "sid", "sgcookie", "utuser", "userid", "unb"})
            log(f"[{index}] 备注={remark} | Cookie字段数={len(keys)} | "
                f"登录相关字段={'有' if login_present else '未发现'} | "
                f"mtop签名token={'有' if token_present else '暂无'}")
        log(f"共 {len(accounts)} 个账号；仅离线检查格式，未验证登录有效性，未发送请求")
        return 0

    list_only = "--mall-list" in argv or cfg("ELM_MALL_ONLY") == "1"
    exchange_only = "--mall-exchange" in argv or cfg("ELM_MALL_EXCHANGE_ONLY") == "1"
    selftest = "--selftest" in argv or "--risk-check" in argv or cfg("ELM_SELFTEST") == "1"
    task_export = "--tasks-export" in argv or cfg("ELM_TASKS_EXPORT") == "1"
    task_list = "--tasks-list" in argv or cfg("ELM_TASKS_ONLY") == "1" or task_export
    if list_only or exchange_only or selftest or task_list:
        failed = False
        for account_index, (remark, cookie) in enumerate(accounts, 1):
            try:
                acc = ElmAccount(remark, cookie)
                if not acc.prime_token():
                    failed = True
                    continue
                if task_list:
                    hp = acc.homepage()
                    mid = (hp or {}).get("missionCollectionId")
                    if not hp or not mid or acc.risk_flagged:
                        log(f"【{remark}】❌ 未取得任务集合，请检查登录与当前入口")
                        failed = True
                        continue
                    tasks = acc.query_tasks(mid)
                    acc.describe_tasks(tasks)
                    if task_export:
                        directory = cfg("ELM_TASKS_OUTPUT_DIR", str(Path(__file__).resolve().parent / "elm_task_reports"))
                        path = acc.export_tasks(tasks, mid, account_index, directory)
                        log(f"【账号{account_index}】已保存脱敏任务JSON：{path}")
                    log(f"【{remark}】仅查询当前接口任务；未签到、上报、抽奖、领奖或兑换")
                elif selftest:
                    hp = acc.homepage()
                    success = bool(hp) and not acc.risk_flagged
                    log(f"【{remark}】{'✅ 首页查询通过' if success else '❌ 首页查询失败，请检查登录/用户验证状态'}")
                    failed = failed or not success
                else:
                    acc.mall(read_only=list_only)
                    message = acc.result.get("兑换", "")
                    acc._log(message)
                    failed = failed or acc.risk_flagged or message.startswith(("❌", "⛔", "⚠"))
            except Exception:
                log(f"【{remark}】❌ 查询异常，请检查网络、依赖和账号配置（不输出请求凭据）")
                failed = True
        return 1 if failed else 0

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.is_file() else {}
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    successes = state.get("account_success_days") or {}
    if not isinstance(successes, dict):
        successes = {}
    # 1.5.44：P2P 互助历史（按天），成功助力才写；本版助力接口未知、历史恒为空。
    p2p_history = state.get("p2p_assist_days") or {}
    if not isinstance(p2p_history, dict):
        p2p_history = {}
    # 1.5.53：失效 Cookie 账号（dead_cookies，按完整 Cookie 哈希）。
    # ELM_CLEAR_DEAD=1 可强制清空死号名单（下一轮全部重跑验证）。
    dead = state.get("dead_cookies") or {}
    if not isinstance(dead, dict):
        dead = {}
    if cfg("ELM_CLEAR_DEAD") == "1":
        dead = {}
        log("🧹 已清空失效 Cookie 名单，本轮全部账号重跑验证")
    # 新版逐账号记录，不再使用旧版全局last_success_day，避免一个成功导致其他号被跳过。
    pending = [(remark, cookie) for remark, cookie in accounts
               if not (ONCE_PER_DAY and not DRYRUN and cfg("ELM_FORCE") != "1"
                       and successes.get(account_state_key(cookie)) == today)]
    # 1.5.53：死号本轮零请求直接跳过（ELM_SKIP_DEAD=0 可关掉，默认开）。
    # 指纹是完整 Cookie 的哈希，用户重新登录更新 Cookie 后自动恢复运行。
    skipped_dead: list[tuple[str, str]] = []
    if cfg("ELM_SKIP_DEAD", "1") == "1" and dead:
        _still = []
        for _remark, _cookie in pending:
            _fp = cookie_fingerprint(_cookie)
            if _fp in dead:
                skipped_dead.append((_remark, str((dead.get(_fp) or {}).get("reason", "登录失效"))))
            else:
                _still.append((_remark, _cookie))
        pending = _still
    for _remark, _reason in skipped_dead:
        log(f"【{_remark}】⏭ Cookie 已失效（{_reason}），本轮跳过不跑；重新登录更新 Cookie 后自动恢复")
    log(f"账号 {len(accounts)} 个；待运行 {len(pending)} 个"
        f"{f'；跳过失效 {len(skipped_dead)} 个' if skipped_dead else ''}；"
        f"自动兑换{'开启（仅指定商品）' if cfg('ELM_EXCHANGE') == '1' else '关闭'}")
    if not pending:
        log("⏭ 所有账号今日已成功运行，跳过")
        return 0
    try:
        offset_max = max(0, int(cfg("ELM_OFFSET_MAX", "900")))
    except ValueError:
        log("❌ ELM_OFFSET_MAX须为非负整数，测试时可设0")
        return 1
    if HUMAN and not DRYRUN and offset_max:
        left = random.randint(0, offset_max)
        log(f"⏳ 随机等待 {left} 秒；如需立即运行请设 ELM_OFFSET_MAX=0")
        while left > 0:
            step = min(30, left)
            time.sleep(step)
            left -= step
            if left:
                log(f"⏳ 还剩 {left} 秒")

    # 1.5.40：多账号并行。每个账号一个 ElmAccount 实例（各用各的 Session、
    # 设备指纹、Cookie），只有设备指纹文件与日志 print 是共享的（各有锁），
    # 其余状态都在实例里，所以并行是安全的。串行 8 个号要 3 小时+，并行
    # （默认最多 8 路）约 30-40 分钟。ELM_MAX_WORKERS 可调：1 = 回到串行，
    # 0 或空 = 自动（min(账号数, 8)），2-16 = 指定路数。账号间 8-25 秒的
    # nap 只在串行时保留（并行时各线程本来就是错峰跑的）。
    def _run_one(index: int, remark: str, cookie: str) -> tuple[int, str, dict, bool, bool, dict | None, str]:
        log(f"开始账号 {index}/{len(pending)}：{remark}")
        p2p_info: dict | None = None
        login_dead_reason = ""
        try:
            acc = ElmAccount(remark, cookie)
            result = acc.run()
            p2p_info = acc.p2p_invite
            # 1.5.53：登录失效（须换 Cookie）才记死号；USER_VALIDATE/RISK_USER 不记。
            if acc.risk_flagged and is_login_dead_reason(acc.stop_reason):
                login_dead_reason = acc.stop_reason
            error = acc.risk_flagged or any(str(value).startswith(("❌", "⛔", "⚠")) for value in result.values())
            success = not error and (str(result.get("签到", "")).startswith("✅")
                                     or str(result.get("任务", "")).startswith("✅"))
        except Exception:
            result = {"错误": "❌ 执行异常，请检查网络与配置（不输出请求凭据）"}
            error, success = True, False
        return index, remark, result, error, success, p2p_info, login_dead_reason

    try:
        _w = int(cfg("ELM_MAX_WORKERS", "0") or 0)
    except (TypeError, ValueError):
        _w = 0
    workers = 1 if _w == 1 else (min(max(_w, 1), 16) if _w > 0 else min(len(pending), 8))
    log(f"并行路数 {workers}（ELM_MAX_WORKERS={'自动' if not _w else _w}，账号 {len(pending)} 个）"
        if workers > 1 else "单账号串行（ELM_MAX_WORKERS=1）")
    rows, ok_count, failed = [], 0, False
    p2p_infos: list[dict] = []  # 1.5.44：各号实时 44914004 助力信息（ELM_COOKIE 顺序）
    if workers <= 1:
        for index, (remark, cookie) in enumerate(pending, 1):
            _, remark, result, error, success, _p2p, _dead_reason = _run_one(index, remark, cookie)
            if _p2p:
                p2p_infos.append(_p2p)
            # 1.5.53：本轮新确认的死号记进名单，下轮零请求跳过。
            if _dead_reason and not DRYRUN:
                dead[cookie_fingerprint(cookie)] = {"remark": remark, "reason": _dead_reason, "day": today}
                log(f"【{remark}】⛔ Cookie 已失效（{_dead_reason}），已记进失效名单，下轮跳过")
            failed = failed or error
            if success:
                ok_count += 1
                if not DRYRUN:
                    successes[account_state_key(cookie)] = today
            rows.append((remark, result))
            if index < len(pending) and not DRYRUN:
                nap(8.0, 25.0)
    else:
        _futures = {}
        with ThreadPoolExecutor(max_workers=workers) as _pool:
            for index, (remark, cookie) in enumerate(pending, 1):
                _futures[_pool.submit(_run_one, index, remark, cookie)] = (index, remark, cookie)
            _done: dict[int, tuple[str, dict, bool, bool, dict | None, str]] = {}
            for _fut in as_completed(_futures):
                try:
                    _idx, _remark, _result, _error, _success, _p2p, _dead_reason = _fut.result()
                except Exception:
                    _idx, _remark, _cookie = _futures[_fut]
                    _result = {"错误": "❌ 执行异常，请检查网络与配置（不输出请求凭据）"}
                    _error, _success, _p2p, _dead_reason = True, False, None, ""
                _done[_idx] = (_remark, _result, _error, _success, _p2p, _dead_reason)
        for index, (remark, cookie) in enumerate(pending, 1):
            _remark, result, error, success, _p2p, _dead_reason = _done[index]
            if _p2p:
                p2p_infos.append(_p2p)
            # 1.5.53：本轮新确认的死号记进名单，下轮零请求跳过。
            if _dead_reason and not DRYRUN:
                dead[cookie_fingerprint(cookie)] = {"remark": _remark, "reason": _dead_reason, "day": today}
                log(f"【{_remark}】⛔ Cookie 已失效（{_dead_reason}），已记进失效名单，下轮跳过")
            failed = failed or error
            if success:
                ok_count += 1
                if not DRYRUN:
                    successes[account_state_key(cookie)] = today
            rows.append((_remark, result))
    # 1.5.44：P2P 互助排计划（44914004）。各号 run() 里已实时采集助力信息；
    # 策略：先助满 ELM_COOKIE 顺序第一个未满的号，再按顺序助下一个。助力接口
    # 未知，本版只排计划、记日志，不发任何请求（见 attempt_p2p_assist）。
    if cfg("ELM_P2P_ASSIST", "1") == "1" and p2p_infos:
        _today_pairs = {(str(_h), str(_t)) for _h, _tgts in (p2p_history.get(today) or {}).items()
                        for _t in (_tgts or [])}
        _plan = ElmAccount.plan_p2p_assists(p2p_infos, _today_pairs)
        if _plan:
            log(f"🤝 P2P 互助计划 {len(_plan)} 对（先助满顺序第一个未满的号）：")
            for _pair in _plan:
                log(f"   {_pair['helper']} → {_pair['target']}")
            log("⏭ 助力接口未知（JSON 里无助力端点），本轮未发起任何助力请求；"
                "抓到真实助力接口后填 attempt_p2p_assist 即可启用")
        else:
            log("🤝 P2P 互助：无可排计划（目标已满 / 助力者今天已助过 / 缺实时信息）")
    if not DRYRUN:
        try:
            STATE_FILE.write_text(json.dumps({"account_success_days": successes,
                                              "p2p_assist_days": p2p_history,
                                              "dead_cookies": dead}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log("⚠ 无法保存逐账号状态，下次运行可能重复执行")
    lines = [f"饿了么幸运星：{ok_count}/{len(pending)} 个账号成功"
               f"{f'；跳过失效 {len(skipped_dead)} 个' if skipped_dead else ''}；"
               f"耗时 {int(time.time()-START_TS)} 秒"]
    for remark, result in rows:
        lines.append(f"【{remark}】")
        lines.extend(f"  {key}：{value}" for key, value in result.items() if value)
    send_notify("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
