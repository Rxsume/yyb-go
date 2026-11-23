#!/usr/bin/env python3
"""Unit tests for the two AstrBot plugins (eleme + kugou).

No network, no router, no Qinglong: Qinglong / SMS / YYB / Kugou clients
are faked. Run: python3 -m unittest discover -s tests -v
(from ~/workspace/astrbot-plugins).
"""
import asyncio
import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

FAKE_COOKIE = "_m_h5_tk=abc123_1234567890; USERID=1000000000001; cookie2=testcookie2"
FAKE_COOKIE_2 = "_m_h5_tk=def456_1234567890; USERID=1000000000002; cookie2=testcookie2b"
UMO = "qq_official:friend:OPENID-TEST-USER"
SENDER = "OPENID-TEST-USER"


# ---------------------------------------------------------------- fake QL
class FakeQinglong:
    """Minimal QinglongClient stand-in recording writes."""

    def __init__(self, cookies=None, envs=None, crons=None):
        self.cookies = list(cookies or [])  # [{remark, cookie, userid}]
        self.envs = dict(envs if envs is not None else {"ELM_COOKIE": "x"})
        self.crons = list(crons or [
            {"id": 163, "name": "elmxyx 幸运星", "isDisabled": False,
             "command": "task elmxyx.py", "schedule": "10 0,9,18 * * *"},
            {"id": 56, "name": "elm_cookie", "isDisabled": False,
             "command": "task elm_cookie.py", "schedule": "0 0 * * *"},
        ])
        self.synced = []       # (cookie, remark, slot)
        self.upserted = {}     # name -> value
        self.ran = []          # cron ids run
        self.kggnb_lines = []  # (line, userid, remark)

    # -- envs
    def list_envs(self, search_key=""):
        out = [{"name": k, "value": v, "remarks": ""} for k, v in self.envs.items()]
        if search_key:
            out = [e for e in out if search_key in e["name"]]
        return out

    def upsert_env(self, name, value, remarks=""):
        self.envs[name] = value
        self.upserted[name] = value
        return {"name": name, "value": value}

    # -- eleme cookies
    def list_ele_cookies(self):
        return [{"remark": c["remark"], "cookie": c["cookie"],
                 "userid": c.get("userid", "")} for c in self.cookies]

    def get_ele_slot_cookie(self, slot):
        idx = slot - 1
        if 0 <= idx < len(self.cookies):
            return self.cookies[idx]["cookie"]
        return None

    def list_ele_slots(self):
        return [{"slot": i + 1, "name": "ELM_COOKIE" if i == 0 else f"ELM_COOKIE{i + 1}",
                 "remarks": [c["remark"]]} for i, c in enumerate(self.cookies)]

    def sync_ele_cookie(self, cookie, remark, slot=1):
        self.synced.append((cookie, remark, slot))
        idx = slot - 1
        while len(self.cookies) <= idx:
            self.cookies.append({"remark": "", "cookie": "", "userid": ""})
        self.cookies[idx] = {"remark": remark, "cookie": cookie, "userid": "1000000000001"}
        return {"ok": True}

    def sync_kugou_line(self, line, userid, remark):
        self.kggnb_lines.append((line, userid, remark))
        return {"remark": remark}

    # -- crons
    def list_crons(self):
        return list(self.crons)

    def get_cron(self, cron_id):
        for c in self.crons:
            if str(c.get("id")) == str(cron_id):
                return c
        return None

    def find_cron_by_command(self, substr):
        for c in self.crons:
            if substr in str(c.get("command") or ""):
                return c
        return None

    def run_cron(self, cron_id):
        self.ran.append(cron_id)

    def run_cron_and_wait(self, cron_id, timeout=120):
        self.ran.append(cron_id)
        return {"finished": True, "timed_out": False,
                "log": self.get_cron_log(cron_id), "summary": {"ok": 1}}

    def get_cron_log(self, cron_id):
        return "line1\nline2\n结果汇总：成功 1，失败 0"

    def stop_cron(self, cron_id):
        pass

    def enable_cron(self, cron_id):
        pass

    def disable_cron(self, cron_id):
        pass

    def list_scripts(self):
        return [{"name": "elmxyx.py"}, {"name": "elm_cookie.py"}]


class FakeSms:
    available = True

    def __init__(self):
        self.sent = []

    def send_code(self, phone):
        self.sent.append(phone)
        return "sms-token-1"

    def verify_code(self, phone, code, token):
        assert token == "sms-token-1"
        return FAKE_COOKIE


class FakeYyb:
    def account_status(self, ref=None):
        return True, "账号 1 在线（alive）"


def make_eleme_core(ql=None, sms=None, yyb=None, phones=None, tmpdir=None):
    import astrbot_plugin_eleme.core as core
    ql = ql if ql is not None else FakeQinglong()
    return core.ElemeCore(
        qinglong=ql,
        sms_login=sms if sms is not None else FakeSms(),
        yyb=yyb if yyb is not None else FakeYyb(),
        cfg=core.ElemeSettings(elm_phones=list(phones or [])),
        data_dir=tmpdir,
        push=lambda umo, text: None,
    ), ql


# ---------------------------------------------------------------- eleme
class TestElemeDispatcher(unittest.TestCase):
    def test_help_menu_tutorial(self):
        core, _ = make_eleme_core()
        self.assertIn("饿了么登录", core.handle_message(SENDER, "帮助", UMO).text)
        self.assertIn("羊毛", core.handle_message(SENDER, "菜单", UMO).text)
        self.assertIn("幸运星", core.handle_message(SENDER, "饿了么教程", UMO).text)
        self.assertEqual("「酷狗教程」还没写好。", core.handle_message(SENDER, "酷狗教程", UMO).text)

    def test_unknown_falls_through_to_llm(self):
        core, _ = make_eleme_core()
        self.assertIsNone(core.handle_message(SENDER, "你好呀", UMO))
        self.assertIsNone(core.handle_message(SENDER, "", UMO))

    def test_browser_login_cut(self):
        core, _ = make_eleme_core()
        reply = core.handle_message(SENDER, "手机号登录", UMO)
        self.assertIn("不可用", reply.text)
        self.assertIn("[1]", reply.text)

    def test_q_exits_flow(self):
        core, _ = make_eleme_core(phones=["13800138000"])
        core.handle_message(SENDER, "饿了么登录", UMO)
        core.handle_message(SENDER, "1", UMO)  # SMS: asks phone (no, phone known -> sends code)
        reply = core.handle_message(SENDER, "q", UMO)
        self.assertEqual("✅ 已退出操作", reply.text)

    def test_sms_flow_binds_cookie(self):
        ql = FakeQinglong()
        sms = FakeSms()
        core, _ = make_eleme_core(ql=ql, sms=sms)
        self.assertIn("短信验证码登录", core.handle_message(SENDER, "饿了么登录", UMO).text)
        self.assertIn("手机号", core.handle_message(SENDER, "1", UMO).text)
        self.assertIn("验证码", core.handle_message(SENDER, "13800138000", UMO).text)
        self.assertEqual(["13800138000"], sms.sent)
        reply = core.handle_message(SENDER, "123456", UMO)
        self.assertIn("已同步进青龙", reply.text)
        self.assertEqual(1, len(ql.synced))
        self.assertEqual(FAKE_COOKIE, ql.synced[0][0])

    def test_ck_paste_flow(self):
        import astrbot_plugin_eleme.core as core_mod
        ql = FakeQinglong()
        core, _ = make_eleme_core(ql=ql)
        core.handle_message(SENDER, "饿了么登录", UMO)
        self.assertIn("粘贴", core.handle_message(SENDER, "3", UMO).text)
        with mock.patch.object(core_mod, "check_cookie_live", return_value=(True, "验证通过")):
            reply = core.handle_message(SENDER, FAKE_COOKIE, UMO)
        self.assertIn("已同步进青龙", reply.text)
        self.assertEqual(FAKE_COOKIE, ql.synced[0][0])

    def test_ck_paste_live_check_failure_still_binds(self):
        import astrbot_plugin_eleme.core as core_mod
        ql = FakeQinglong()
        core, _ = make_eleme_core(ql=ql)
        core.handle_message(SENDER, "饿了么登录", UMO)
        core.handle_message(SENDER, "3", UMO)
        with mock.patch.object(core_mod, "check_cookie_live",
                               return_value=(False, "联网验证失败（x），只做了格式检查")):
            reply = core.handle_message(SENDER, FAKE_COOKIE, UMO)
        self.assertIn("已同步进青龙", reply.text)
        self.assertIn("联网验证失败", reply.text)

    def test_yyb_login_slot_already_valid(self):
        ql = FakeQinglong(cookies=[{"remark": "8164", "cookie": FAKE_COOKIE, "userid": "1000000000001"}])
        core, _ = make_eleme_core(ql=ql, phones=["13800138000"])
        reply = core.handle_message(SENDER, "登录", UMO)
        self.assertIn("已授权", reply.text)
        # _check_ele_slot returns the cookie-derived remark, not the QL remark
        self.assertIn("饿了么-100000000000", reply.text)
        self.assertIn("槽位 1", reply.text)
        self.assertEqual([], ql.ran)  # no elm_cookie run needed

    def test_yyb_login_triggers_elm_cookie_when_slot_invalid(self):
        ql = FakeQinglong(cookies=[])  # slot empty
        core, _ = make_eleme_core(ql=ql, phones=["13800138000"])
        reply = core.handle_message(SENDER, "登录", UMO)
        # slot still invalid after the run -> error reply with log tail
        self.assertIn("同步后槽位还是无效", reply.text)
        self.assertIn(56, ql.ran)
        self.assertIn("line1", reply.text)

    def test_qinglong_commands(self):
        ql = FakeQinglong()
        core, _ = make_eleme_core(ql=ql)
        self.assertIn("elmxyx", core.handle_message(SENDER, "青龙任务", UMO).text)
        self.assertIn("ELM_COOKIE", core.handle_message(SENDER, "青龙变量", UMO).text)
        self.assertIn("elmxyx.py", core.handle_message(SENDER, "青龙脚本", UMO).text)
        log = core.handle_message(SENDER, "青龙日志 163", UMO)
        self.assertIn("line1", log.text)
        run = core.handle_message(SENDER, "青龙执行 163", UMO)
        self.assertIn("已执行", run.text)
        self.assertIn(163, ql.ran)
        # two-step form: bare command asks for the id
        self.assertIn("任务 ID", core.handle_message(SENDER, "青龙日志", UMO).text)
        self.assertIn("line2", core.handle_message(SENDER, "163", UMO).text)

    def test_star_query_flow(self):
        import astrbot_plugin_eleme.core as core_mod
        ql = FakeQinglong(cookies=[
            {"remark": "8164", "cookie": FAKE_COOKIE, "userid": "1000000000001"},
            {"remark": "0261", "cookie": FAKE_COOKIE_2, "userid": "1000000000002"},
        ])
        core, _ = make_eleme_core(ql=ql)
        menu = core.handle_message(SENDER, "幸运星", UMO)
        self.assertIn("[0]", menu.text)
        self.assertIn("8164", menu.text)
        with mock.patch.object(
            core_mod, "query_one_account",
            side_effect=lambda remark, cookie: {
                "balance": 1234, "today": 55, "details": [], "nickname": "",
                "error": None, "today_error": None,
            },
        ):
            report = core.handle_message(SENDER, "0", UMO)
        self.assertIn("1234", report.text)
        self.assertIn("55", report.text)

    def test_exchange_start_lists_balances(self):
        import astrbot_plugin_eleme.core as core_mod
        ql = FakeQinglong(cookies=[
            {"remark": "8164", "cookie": FAKE_COOKIE, "userid": "1000000000001"},
        ])
        core, _ = make_eleme_core(ql=ql)
        with mock.patch.object(core_mod, "query_star_homepage", return_value=(777, "闪购用户")):
            reply = core.handle_message(SENDER, "饿了么兑换", UMO)
        self.assertIn("777", reply.text)
        self.assertIn("[0] 全部账号", reply.text)

    def test_cookie_menu_check(self):
        import astrbot_plugin_eleme.core as core_mod
        ql = FakeQinglong(cookies=[{"remark": "8164", "cookie": FAKE_COOKIE, "userid": "1"}])
        core, _ = make_eleme_core(ql=ql)
        self.assertIn("饿了么管理", core.handle_message(SENDER, "饿了么管理", UMO).text)
        with mock.patch.object(core_mod, "check_all", return_value=[
            {"remark": "8164", "status": "ok", "note": "余额 1234"},
        ]):
            reply = core.handle_message(SENDER, "s", UMO)
        self.assertIn("✅ 8164", reply.text)
        self.assertIn("正常", reply.text)


class TestScheduledExchangeStore(unittest.TestCase):
    def test_umo_chat_id_roundtrip(self):
        import astrbot_plugin_eleme.scheduled_exchange as se
        with tempfile.TemporaryDirectory() as d:
            store = se.ScheduledExchangeStore(os.path.join(d, "s.json"))
            item = {"exchange_id": "x1", "name": "奶茶免单卡", "cost": 988,
                    "exchange_start_time": None}
            target = se.now_beijing()
            task = se.new_scheduled_task(item, ["8164"], target, UMO)
            self.assertEqual(UMO, task["chat_id"])  # str UMO, no int() crash
            store.add(task)
            self.assertEqual(1, len(store.pending()))
            self.assertEqual([task["id"]], [t["id"] for t in store.cancel_for_remarks(["8164"])])
            self.assertEqual([], store.pending())

    def test_runner_executes_due_task_and_notifies_umo(self):
        import astrbot_plugin_eleme.scheduled_exchange as se
        pushed = []
        with tempfile.TemporaryDirectory() as d:
            store = se.ScheduledExchangeStore(os.path.join(d, "s.json"))
            item = {"exchange_id": "x1", "name": "奶茶免单卡", "cost": 988,
                    "exchange_start_time": None}
            past = se.now_beijing()
            task = se.new_scheduled_task(item, ["8164"], past, UMO)
            store.add(task)
            runner = se.ScheduledExchangeRunner(
                store=store,
                build_picks=lambda it, remarks: (["📱 8164：可兑换"], {"8164": it}),
                execute=lambda picks: "✅ 兑换成功",
                notify=lambda umo, text: pushed.append((umo, text)),
            )
            runner.process_once()
        self.assertEqual(1, len(pushed))
        self.assertEqual(UMO, pushed[0][0])
        self.assertIn("兑换成功", pushed[0][1])


# ---------------------------------------------------------------- kugou
class FakeKugouLogin:
    def __init__(self, qr_state="success"):
        self.qr_state = qr_state
        self.calls = 0

    @property
    def available(self):
        return True

    def new_state(self):
        return {"qr_key": "KEY1", "device": {}}

    def get_qr_key(self, state):
        return "KEY1", "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode(), "https://h5.kugou.com/qr/KEY1"

    def check_qr(self, state):
        self.calls += 1
        if self.qr_state == "success":
            return "success", "", {"token": "SECRET-TOKEN-123", "userid": "999888"}
        return "waiting", "", {}

    def qr_image_bytes(self, img):
        import astrbot_plugin_kugou.kugou_concept3114 as m
        return m.KugouConceptLogin3114().qr_image_bytes(img)

    def build_kggnb_line(self, token, userid, remark, device):
        return f"{remark}#token#{userid}#mid#dfid"


class FakeWithdraw:
    available = True

    def load_accounts(self, kggnb, wx):
        return [{"remark": "酷狗1", "wx": None}, {"remark": "酷狗2", "wx": None}]

    def check_accounts(self, accounts, coins):
        return [{"remark": a["remark"], "ok": True, "has_wx": True,
                 "day_apply_count": 0, "max_apply_count": 1,
                 "target": coins, "balance_coins": 999999, "coins": coins}
                for a in accounts]

    def grab_accounts(self, accounts, coins, at_dt=None, wait_secs=0, poll_secs=0):
        return [{"remark": a["remark"], "ok": True, "text": "✅ 提现成功"} for a in accounts]


def make_kugou_core(ql=None, kugou=None, withdraw=None, sync=False, tmpdir=None):
    import astrbot_plugin_kugou.core as core
    ql = ql if ql is not None else FakeQinglong(envs={"KGGNB": "x", "KG_WITHDRAW_WX": "y"})
    return core.KugouCore(
        qinglong=ql,
        kugou=kugou if kugou is not None else FakeKugouLogin(),
        withdraw=withdraw if withdraw is not None else FakeWithdraw(),
        cfg=core.KugouSettings(kg_sync_qinglong=sync),
        data_dir=tmpdir,
        push=lambda umo, text: None,
    ), ql


class TestKugouQR(unittest.TestCase):
    def test_qr_reply_has_image_and_no_token(self):
        pushed = []
        core, _ = make_kugou_core()
        core._push = lambda umo, text: pushed.append((umo, text))
        core._kg_poll_interval = 0.01
        core._kg_poll_timeout = 5
        reply = core.handle_message(SENDER, "酷狗登录", UMO)
        self.assertEqual(b"PNGDATA", reply.image)
        self.assertIn("扫码", reply.text)
        # wait for the poll thread to report success
        for _ in range(200):
            if pushed:
                break
            import time; time.sleep(0.02)
        self.assertTrue(pushed, "poll thread did not push login result")
        text = pushed[0][1]
        self.assertIn("999888", text)               # userid shown
        self.assertNotIn("SECRET-TOKEN-123", text)  # token never in plaintext
        self.assertNotIn("KGGNB 行", text)
        self.assertIn("kg_sync_qinglong", text)     # sync is off: tells how to enable

    def test_qr_refresh_with_1(self):
        # qr_state="waiting" so the session stays in KG_QR_WAIT
        core, _ = make_kugou_core(kugou=FakeKugouLogin(qr_state="waiting"))
        core._kg_poll_interval = 60
        core._kg_poll_timeout = 60
        core.handle_message(SENDER, "酷狗登录", UMO)
        reply = core.handle_message(SENDER, "1", UMO)
        self.assertIsNotNone(reply.image)
        self.assertIn("扫码", reply.text)

    def test_qr_cancel_with_q(self):
        core, _ = make_kugou_core(kugou=FakeKugouLogin(qr_state="waiting"))
        core._kg_poll_interval = 60
        core._kg_poll_timeout = 60
        core.handle_message(SENDER, "酷狗登录", UMO)
        reply = core.handle_message(SENDER, "q", UMO)
        self.assertEqual("✅ 已退出操作", reply.text)

    def test_login_success_syncs_qinglong_when_enabled(self):
        pushed = []
        ql = FakeQinglong(envs={"KGGNB": "x"})
        core, _ = make_kugou_core(ql=ql, sync=True)
        core._push = lambda umo, text: pushed.append((umo, text))
        core._kg_poll_interval = 0.01
        core._kg_poll_timeout = 5
        core.handle_message(SENDER, "酷狗登录", UMO)
        for _ in range(200):
            if pushed:
                break
            import time; time.sleep(0.02)
        self.assertTrue(pushed)
        self.assertIn("已同步进青龙", pushed[0][1])
        self.assertEqual(1, len(ql.kggnb_lines))
        # the synced line itself carries the token (that is the point of
        # syncing), but it is never printed into the QQ reply:
        self.assertNotIn("SECRET-TOKEN-123", pushed[0][1])

    def test_unknown_falls_through(self):
        core, _ = make_kugou_core()
        self.assertIsNone(core.handle_message(SENDER, "你好", UMO))
        self.assertIn("酷狗", core.handle_message(SENDER, "酷狗帮助", UMO).text)


class TestKugouWithdraw(unittest.TestCase):
    def test_withdraw_flow_to_scheduled_job(self):
        ql = FakeQinglong(envs={"KGGNB": "x", "KG_WITHDRAW_WX": "y"})
        with tempfile.TemporaryDirectory() as d:
            core, _ = make_kugou_core(ql=ql, tmpdir=d)
            accounts = core.handle_message(SENDER, "酷狗提现", UMO)
            self.assertIn("酷狗1", accounts.text)
            tier = core.handle_message(SENDER, "0", UMO)
            self.assertIn("选档位", tier.text)
            mode = core.handle_message(SENDER, "1", UMO)
            self.assertIn("立即提现", mode.text)
            # TIERS = (3000, 5000, 10000, 50000, 100000): tier 1 is 3000 coins
            self.assertEqual("3000", ql.upserted.get("KG_WITHDRAW_COINS"))
            done = core.handle_message(SENDER, "2", UMO)
            self.assertIn("已安排酷狗定时提现", done.text)
            listed = core.handle_message(SENDER, "定时提现", UMO)
            self.assertIn("3000金币", listed.text)
            cancelled = core.handle_message(SENDER, "1", UMO)
            self.assertIn("已取消定时提现", cancelled.text)

    def test_withdrawal_store_umo(self):
        import astrbot_plugin_kugou.scheduled_withdrawal as sw
        with tempfile.TemporaryDirectory() as d:
            store = sw.ScheduledWithdrawalStore(os.path.join(d, "w.json"))
            job = sw.new_withdrawal_job(["酷狗1"], 50000, UMO)
            self.assertEqual(UMO, job["chat_id"])
            store.add(job)
            self.assertEqual(1, len(store.active()))

    def test_immediate_withdraw_pushes_result(self):
        pushed = []
        ql = FakeQinglong(envs={"KGGNB": "x", "KG_WITHDRAW_WX": "y"})
        core, _ = make_kugou_core(ql=ql)
        core._push = lambda umo, text: pushed.append((umo, text))
        core.handle_message(SENDER, "酷狗提现", UMO)
        core.handle_message(SENDER, "0", UMO)
        core.handle_message(SENDER, "1", UMO)
        reply = core.handle_message(SENDER, "1", UMO)  # [1] 立即提现
        self.assertIn("正在立即提现", reply.text)
        for _ in range(200):
            if pushed:
                break
            import time; time.sleep(0.02)
        self.assertTrue(pushed, "immediate withdraw thread did not push")
        self.assertEqual(UMO, pushed[0][0])
        self.assertIn("已提现", pushed[0][1])


# ---------------------------------------------------------------- main.py wiring (stubbed astrbot)
def install_astrbot_stubs():
    """Minimal astrbot package stubs so main.py can be imported on the VM."""
    import types
    if "astrbot" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    msg_comp = types.ModuleType("astrbot.api.message_components")
    star_mod = types.ModuleType("astrbot.api.star")

    class FakeFilter:
        class EventMessageType:
            ALL = "ALL"
        class PermissionType:
            ADMIN = "ADMIN"
        @staticmethod
        def event_message_type(*a, **k):
            def deco(fn):
                return fn
            return deco
        @staticmethod
        def permission_type(*a, **k):
            def deco(fn):
                return fn
            return deco

    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        def __init__(self, b64):
            self.b64 = b64
        @staticmethod
        def fromBase64(b64):
            return Image(b64)

    class MessageChain(list):
        def __init__(self, items=None):
            super().__init__(items or [])

    class AstrMessageEvent:
        def plain_result(self, text):
            return ("plain", text)
        def chain_result(self, chain):
            return ("chain", chain)

    class Star:
        def __init__(self, context, config=None):
            self.context = context
        async def terminate(self):
            pass

    class StarTools:
        @classmethod
        def get_data_dir(cls, plugin_name=None):
            return tempfile.mkdtemp(prefix=f"astrbot-test-{plugin_name}-")

    class Context:
        def __init__(self):
            self.sent = []
        async def send_message(self, session, chain):
            self.sent.append((session, chain))
            return True

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = FakeFilter
    msg_comp.Plain = Plain
    msg_comp.Image = Image
    msg_comp.MessageChain = MessageChain
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools
    api.event = event_mod
    api.message_components = msg_comp
    api.star = star_mod
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = msg_comp
    sys.modules["astrbot.api.star"] = star_mod


class FakeEvent:
    def __init__(self, text, sender=SENDER, umo=UMO):
        self.message_str = text
        self._sender = sender
        self.unified_msg_origin = umo
        self.stopped = False
        self.results = []

    def get_sender_id(self):
        return self._sender

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


async def drive_dispatch(plugin, event):
    async for result in plugin.dispatch(event):
        event.results.append(result)


class TestMainDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_astrbot_stubs()

    def test_eleme_help_yields_and_stops(self):
        import astrbot_plugin_eleme.main as m
        from astrbot.api.star import Context
        plugin = m.ElemePlugin(Context(), {"ql_token": "", "elm_phones": []})
        event = FakeEvent("帮助")
        asyncio.run(drive_dispatch(plugin, event))
        self.assertTrue(event.stopped)
        self.assertTrue(any(r[0] == "plain" and "饿了么登录" in r[1] for r in event.results))

    def test_eleme_unknown_passes_through(self):
        import astrbot_plugin_eleme.main as m
        from astrbot.api.star import Context
        plugin = m.ElemePlugin(Context(), {"ql_token": "", "elm_phones": []})
        event = FakeEvent("今天天气怎么样")
        asyncio.run(drive_dispatch(plugin, event))
        self.assertFalse(event.stopped)
        self.assertEqual([], event.results)

    def test_kugou_qr_yields_image_chain(self):
        import astrbot_plugin_kugou.main as m
        from astrbot.api.star import Context
        plugin = m.KugouPlugin(Context(), {"ql_token": ""})
        # fake the kugou client so no network happens
        plugin.core.kugou = FakeKugouLogin()
        plugin.core._kg_poll_interval = 60
        plugin.core._kg_poll_timeout = 60
        event = FakeEvent("酷狗登录")
        asyncio.run(drive_dispatch(plugin, event))
        self.assertTrue(event.stopped)
        self.assertEqual(1, len(event.results))
        kind, chain = event.results[0]
        self.assertEqual("chain", kind)
        self.assertTrue(any(hasattr(c, "b64") for c in chain))

    def test_split_text(self):
        import astrbot_plugin_eleme.main as m
        long_text = "\n".join(f"第{i}行内容填充" for i in range(500))
        chunks = m._split_text(long_text, 1500)
        self.assertTrue(len(chunks) > 1)
        self.assertTrue(all(len(c) <= 1500 for c in chunks))
        self.assertEqual(long_text, "".join(chunks))


class TestPluginFiles(unittest.TestCase):
    def test_metadata_and_schema(self):
        import yaml
        for name in ("astrbot_plugin_eleme", "astrbot_plugin_kugou"):
            d = os.path.join(PLUGINS_DIR, name)
            meta = yaml.safe_load(open(os.path.join(d, "metadata.yaml"), encoding="utf-8"))
            for field in ("name", "desc", "version", "author"):
                self.assertTrue(meta.get(field), f"{name} metadata missing {field}")
            self.assertEqual(["qq_official"], meta["support_platforms"])
            schema = json.load(open(os.path.join(d, "_conf_schema.json"), encoding="utf-8"))
            self.assertIn("ql_base_url", schema)
            self.assertIn("ql_token", schema)
            req = open(os.path.join(d, "requirements.txt"), encoding="utf-8").read()
            self.assertIn("requests", req)
            for f in ("main.py", "core.py", "texts.py", "metadata.yaml",
                      "_conf_schema.json", "requirements.txt", "qinglong.py", "state.py"):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f"{name}/{f} missing")

    def test_no_real_secrets_in_plugin_code(self):
        import re
        secret_pat = re.compile(r"(ELM_COOKIE\s*=\s*['\"][^'\"]{20,}|ql_token['\"]?\s*:\s*['\"][^'\"]{10,}|Bearer [A-Za-z0-9_\-]{10,})")
        for root, _, files in os.walk(PLUGINS_DIR):
            for f in files:
                if f.endswith(".py") or f.endswith(".yaml") or f.endswith(".json"):
                    p = os.path.join(root, f)
                    content = open(p, encoding="utf-8", errors="ignore").read()
                    self.assertIsNone(secret_pat.search(content), f"possible secret in {p}")

    def test_vendored_modules_import(self):
        for mod in (
            "astrbot_plugin_eleme.qinglong",
            "astrbot_plugin_eleme.lucky_star",
            "astrbot_plugin_eleme.exchange",
            "astrbot_plugin_eleme.eleme_login",
            "astrbot_plugin_eleme.yyb",
            "astrbot_plugin_eleme.cookie_watch",
            "astrbot_plugin_eleme.scheduled_exchange",
            "astrbot_plugin_kugou.qinglong",
            "astrbot_plugin_kugou.kugou_concept3114",
            "astrbot_plugin_kugou.kugou_concept_client",
            "astrbot_plugin_kugou.kugou_concept_crypto",
            "astrbot_plugin_kugou.kugou_withdraw",
            "astrbot_plugin_kugou.kg_withdraw",
            "astrbot_plugin_kugou.scheduled_withdrawal",
        ):
            importlib.import_module(mod)
        import astrbot_plugin_kugou.kugou_withdraw as kw
        self.assertTrue(kw.KugouWithdraw().available)


# ------------------------------------------------------- real QL client
class TestRealQinglongClient(unittest.TestCase):
    """Regression tests for the real QinglongClient (no network).

    Covers the two live failures of 2026-11-24 01:09:
    - list_ele_cookies was called by core.py but did not exist
      ('QinglongClient' object has no attribute 'list_ele_cookies').
    - upsert_env echoed the whole GET object back on PUT, which
      Qinglong rejects with HTTP 400 "Validation failed". The panel
      wants a single object {id, name, value, remarks}, not an array
      (proven live 2026-11-24: array bodies fail with '"value" must
      be of type object', single object succeeds).
    """

    def _client_with_envs(self, envs):
        from astrbot_plugin_eleme.qinglong import QinglongClient
        client = QinglongClient("http://127.0.0.1:5700", "token")
        calls = []

        def fake_req(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "/open/envs":
                return [dict(e) for e in envs]
            return {"ok": True}

        client._req = fake_req
        return client, calls

    def test_list_ele_cookies_parses_slots_and_userid(self):
        client, _ = self._client_with_envs([
            {"id": 7, "name": "ELM_COOKIE2", "value": "7636#_m_h5_tk=x; USERID=222; cookie2=y", "remarks": ""},
            {"id": 3, "name": "ELM_COOKIE", "value": "8164#_m_h5_tk=a; USERID=111; cookie2=z\n0261#_m_h5_tk=b; utuser=333", "remarks": ""},
            {"id": 9, "name": "OTHER_VAR", "value": "zzz", "remarks": ""},
        ])
        got = client.list_ele_cookies()
        self.assertEqual([a["remark"] for a in got], ["8164", "0261", "7636"])
        self.assertEqual([a["slot"] for a in got], [1, 1, 2])
        self.assertEqual([a["userid"] for a in got], ["111", "333", "222"])
        self.assertTrue(got[0]["cookie"].startswith("_m_h5_tk=a"))
        self.assertEqual(got[0]["name"], "ELM_COOKIE")

    def test_upsert_env_put_sends_single_object(self):
        client, calls = self._client_with_envs([
            {"id": 42, "name": "ELM_COOKIE2", "value": "old",
             "remarks": "old", "status": 0,
             "createdAt": "2026-01-01", "updatedAt": "2026-01-02",
             "timestamp": "2026-01-02"},
        ])
        result = client.upsert_env("ELM_COOKIE2", "7636#newcookie", remarks="微信机器人同步 7636")
        self.assertEqual(result["action"], "更新")
        puts = [c for c in calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 1)
        # Single object, NOT a list: this panel rejects an array body
        # with 400 '"value" must be of type object'.
        payload = puts[0][2]["json"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(set(payload.keys()), {"id", "name", "value", "remarks"})
        self.assertEqual(payload["id"], 42)
        self.assertEqual(payload["value"], "7636#newcookie")

    def test_upsert_env_create_posts_minimal_body(self):
        client, calls = self._client_with_envs([])
        result = client.upsert_env("ELM_COOKIE3", "8311#newcookie")
        self.assertEqual(result["action"], "新建")
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["json"],
                         [{"name": "ELM_COOKIE3", "value": "8311#newcookie", "remarks": ""}])


# ------------------------------------------------- top-level escape
class TestTopLevelEscape(unittest.TestCase):
    """Top-level commands must escape any sub-flow (2026-11-24).

    Live failure: with the session sitting in the Cookie-management
    menu, "饿了么登录" and "饿了么查询" were both swallowed by that
    menu's "请回复 s / r / q。" fallback instead of jumping to their
    own menus.
    """

    def test_eleme_cookie_menu_escapes_to_login(self):
        core, _ = make_eleme_core()
        self.assertIn("饿了么管理",
                      core.handle_message(SENDER, "饿了么管理", UMO).text)
        reply = core.handle_message(SENDER, "饿了么登录", UMO)
        self.assertIn("======饿了么登录======", reply.text)
        self.assertIn("[1] 短信验证码登录", reply.text)
        self.assertNotIn("请回复 s / r / q", reply.text)

    def test_eleme_cookie_menu_escapes_to_query(self):
        core, _ = make_eleme_core()
        core.handle_message(SENDER, "饿了么管理", UMO)
        reply = core.handle_message(SENDER, "饿了么查询", UMO)
        self.assertIn("=====饿了么查询=====", reply.text)
        self.assertNotIn("请回复 s / r / q", reply.text)

    def test_eleme_cookie_menu_escapes_to_kugou(self):
        # A Kugou command mid Ele.me flow: drop our flow, return None
        # so the event continues to the Kugou plugin.
        core, _ = make_eleme_core()
        core.handle_message(SENDER, "饿了么管理", UMO)
        self.assertIsNone(core.handle_message(SENDER, "酷狗登录", UMO))

    def test_menu_state_numbers_still_work(self):
        # 1/2/3 are menu choices, not top-level commands: they must
        # still reach _on_menu_choice.
        core, _ = make_eleme_core()
        core.handle_message(SENDER, "饿了么登录", UMO)
        reply = core.handle_message(SENDER, "1", UMO)
        self.assertIn("手机号", reply.text)

    def test_kugou_withdraw_flow_escapes_to_login(self):
        core, _ = make_kugou_core()
        core.handle_message(SENDER, "酷狗提现", UMO)
        reply = core.handle_message(SENDER, "酷狗登录", UMO)
        self.assertIn("二维码已生成", reply.text)


if __name__ == "__main__":
    unittest.main()
