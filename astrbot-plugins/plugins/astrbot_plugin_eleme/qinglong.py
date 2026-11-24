"""Qinglong panel client (OpenAPI under /open, Bearer token auth).

Verified API surface (panel https://qlhbdx.tocmcc.cn/):

    GET  /open/crons                 list tasks; data is {"data": [...], "total": N}
    PUT  /open/crons/run             body: [id, ...]
    PUT  /open/crons/stop|enable|disable   body: [id, ...]
    GET  /open/crons/:id/log         task log (plain text in data)
    DELETE /open/crons               body: [id, ...]
    GET  /open/envs                  list env vars
    POST /open/envs                  create; body: [{name, value, remarks}]
    PUT  /open/envs                  update ONE var; body: {id, name, value,
                                     remarks} — a single object, NOT an array
                                     (this panel returns 400 '"value" must be
                                     of type object' for an array body;
                                     proven live 2026-11-24 on a throwaway var)
    GET  /open/scripts               list scripts

Notes:
- Do NOT read ``status`` as enabled/disabled: the detail object carries a
  separate ``isDisabled`` flag.
- The Bearer token is minted by the user in the panel
  (系统设置 → 应用设置 → app with 定时任务 + 环境变量 + 脚本管理,
  then GET /open/auth/token?client_id=...&client_secret=...).
  It arrives here ONLY via the QL_TOKEN env var and is never logged.
- Ele.me cookies live in ELM_COOKIE (slot 1) and ELM_COOKIE2..20
  (slots 2..20), one account per variable, value format ``备注#完整Cookie``.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

MAX_ELM_SLOTS = 20

# Kugou Concept (KGGNB) accounts live in this one var, one account per
# line, either KV format (``remark=...;token=...;userid=...;mid=...;
# dfid=...``) or ``备注#token#userid#mid#dfid``. Both kgylgnb45w.py
# (task 200) and kg_withdraw.py (task 246) read it with the same
# parser (first non-empty of KGGNB / kggnb / KGGNB_TASK / kugou_token
# / kwtx / KWTX wins).
KGGNB_ENV_NAME = "KGGNB"
KGGNB_ENV_REMARKS = "酷狗概念版"


def _kggnb_line_identity(line: str) -> tuple[str, str]:
    """(userid, remark) parsed from one KGGNB line.

    KV format: ``remark=...;token=...;userid=...;mid=...;dfid=...``.
    # format: ``备注#token#userid#mid#dfid`` (or the 32-hex-token-first
    variant ``<token>#<userid>``). Returns ("", "") when unparseable.
    Values are never returned — only the two identity fields.
    """
    line = (line or "").strip()
    if not line:
        return "", ""
    if "#" not in line and ("token=" in line or "userid=" in line):
        kv: dict[str, str] = {}
        for item in re.split(r"[;,]+", line):
            if "=" in item:
                key, _, value = item.partition("=")
                kv[key.strip().lower()] = value.strip()
        return kv.get("userid", ""), kv.get("remark", "")
    parts = [x for x in (x.strip() for x in line.split("#")) if x]
    if len(parts) >= 3:
        return parts[2], parts[0]
    if (len(parts) == 2 and len(parts[0]) == 32
            and all(c in "0123456789abcdefABCDEF" for c in parts[0])
            and parts[1].isdigit()):
        return parts[1], ""
    return "", ""




def merge_kggnb_lines(current: str, new_line: str, new_userid: str,
                      remark: str) -> tuple[str, str, bool]:
    """Merge one account's KGGNB line into a multi-account env value.

    Match by userid first; when no line carries that userid, fall
    back to the remark/username (both userid and username identify
    an account — a re-login with an empty userid still matches by
    username, never skips). A matched line is overwritten IN PLACE
    with the new line as-is: the new line's remark wins, never keep
    an old remark next to a new token. Only when neither key matches
    is the line appended (a genuinely new account). Other accounts
    and their remarks stay untouched, in their original order. A
    duplicate line for the same account is dropped, so one account
    never appears twice.

    Returns (merged_value, final_remark, replaced).
    """
    lines = [ln for ln in (current or "").splitlines() if ln.strip()]
    new_line = (new_line or "").strip()
    new_userid = (new_userid or "").strip()
    remark = (remark or "").strip()
    # userid match has priority; the remark/username fallback applies
    # only when no line carries this userid.
    userid_hit = bool(new_userid) and any(
        _kggnb_line_identity(ln)[0] == new_userid for ln in lines)
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        uid, rem = _kggnb_line_identity(line)
        if new_userid and uid and uid == new_userid:
            hit = True
        elif not userid_hit and remark and rem and rem == remark:
            hit = True
        else:
            hit = False
        if hit:
            if replaced:
                continue  # drop a duplicate line for the same account
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(new_line)
    return "\n".join(out_lines), remark, replaced

# elm_cookie.py prints this summary as its very last line, e.g.
#   结果汇总：✅ 成功 2 ｜ ⚠️ 需人工 0 ｜ ❌ 失败 0（共 2）
# The regex matches the Chinese labels + digits only, so it does not
# depend on the exact emoji bytes the script prints.
_ELM_SUMMARY_RE = re.compile(
    r"结果汇总：[^0-9]*成功\s*(\d+)[^0-9]*需人工\s*(\d+)[^0-9]*失败\s*(\d+)"
)


def parse_elm_cookie_summary(log_text: str) -> dict | None:
    """Parse the elm_cookie.py result summary out of a log.

    Returns {"ok": n, "human": n, "fail": n} or None when the script
    has not printed its summary yet (still running or crashed early).
    """
    m = _ELM_SUMMARY_RE.search(log_text or "")
    if not m:
        return None
    return {"ok": int(m.group(1)), "human": int(m.group(2)), "fail": int(m.group(3))}


class QinglongError(Exception):
    """A Qinglong API call failed (message never contains the token)."""


def _elm_slot_name(slot: int) -> str:
    if slot == 1:
        return "ELM_COOKIE"
    return f"ELM_COOKIE{slot}"


def _cookie_userid(cookie: str) -> str:
    """Best-effort USERID from a cookie string (USERID/utuser/userid)."""
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() in ("USERID", "utuser", "userid", "unb", "munb") and value.strip():
            return value.strip()
    return ""


def merge_cookie_lines(current: str, new_line: str, new_uid: str, remark: str) -> str:
    """Merge one account's cookie line into a multi-account env value.

    Same rule as merge_kggnb_lines (the household's convention since
    2026-11-19: every cookie line carries a ``备注#`` prefix, one
    account per line): match by USERID first; when no line carries
    that USERID, fall back to the leading remark/username (both
    USERID and username identify an account — a re-login with an
    empty USERID still matches by username, never skips). A matched
    line is overwritten IN PLACE with the new line as-is: the cookie
    changes, the remark (account ID) stays, and the account keeps its
    original position so the order never moves. Only when neither key
    matches is the line appended (a genuinely new account). Other
    accounts and their remarks stay untouched, in their original
    order. A duplicate line for the same account is dropped, so one
    account never appears twice. A wholesale overwrite here would
    delete the other accounts (2026-11-24 01:40: 1793 re-login wiped
    the other 12 accounts out of ELM_COOKIE).
    """
    lines = [l for l in (current or "").splitlines() if l.strip()]
    new_line = (new_line or "").strip()
    new_uid = (new_uid or "").strip()
    remark = (remark or "").strip()
    # USERID match has priority; the remark/username fallback applies
    # only when no line carries this USERID.
    uid_hit = bool(new_uid) and any(
        _cookie_userid(l) == new_uid for l in lines)
    out_lines: list[str] = []
    replaced = False
    for l in lines:
        if new_uid and _cookie_userid(l) == new_uid:
            hit = True
        elif (not uid_hit and remark and "#" in l
                and l.split("#", 1)[0] == remark):
            hit = True
        else:
            hit = False
        if hit:
            if replaced:
                continue  # drop a duplicate line for the same account
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(l)
    if not replaced:
        out_lines.append(new_line)
    return "\n".join(out_lines)


class QinglongClient:
    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- low level --------------------------------------------------------
    def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.token:
            raise QinglongError("青龙 token 未设置：请先在插件配置里填 ql_token")
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        try:
            resp = requests.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise QinglongError(f"连接青龙失败：{exc}") from exc
        if resp.status_code == 401:
            raise QinglongError("青龙 token 无效或已过期（401），请重新建应用拿 token")
        try:
            body = resp.json()
        except ValueError:
            if resp.status_code == 200:
                return resp.text
            raise QinglongError(f"青龙返回异常（HTTP {resp.status_code}）") from None
        if resp.status_code != 200 or body.get("code") != 200:
            raise QinglongError(
                f"青龙 {method} {path} 失败：HTTP {resp.status_code}，{body.get('message') or body}"
            )
        return body.get("data")

    @staticmethod
    def _as_list(data: Any) -> list:
        if isinstance(data, dict):
            return data.get("data") or []
        return data or []

    # -- crons --------------------------------------------------------------
    def list_crons(self) -> list[dict]:
        return self._as_list(self._req("GET", "/open/crons"))

    def get_cron(self, cron_id: int) -> dict | None:
        for cron in self.list_crons():
            if str(cron.get("id")) == str(cron_id):
                return cron
        return None

    def find_cron_by_command(self, substr: str) -> dict | None:
        """Find a cron whose command contains ``substr`` (elm_cookie.py
        runs as ``task elm_cookie.py``). Falls back to a name match.
        Returns None when no task matches."""
        needle = (substr or "").strip()
        if not needle:
            return None
        fallback = None
        for cron in self.list_crons():
            cmd = str(cron.get("command") or "")
            name = str(cron.get("name") or "")
            if needle in cmd:
                return cron
            if fallback is None and needle in name:
                fallback = cron
        return fallback

    def run_cron_and_wait(
        self, cron_id: int, timeout: int = 120, poll_interval: float = 5.0
    ) -> dict:
        """Run a cron and wait for its log to settle.

        Returns {"finished": bool, "timed_out": bool,
                 "log": str, "summary": dict | None}.

        "Finished" means the elm_cookie summary line appeared in the
        log written AFTER the trigger, or the log stopped growing for
        four consecutive polls (~20 s of silence — a login step rarely
        goes that long without printing). The summary is only searched
        in the new portion of the log, so an old summary from the
        previous scheduled run cannot fake a success. ``timed_out``
        means the wait hit ``timeout``; the task may still be running.
        """
        before = self.get_cron_log(cron_id)
        self.run_cron(cron_id)
        deadline = time.monotonic() + max(1, timeout)
        stable_polls = 0
        last_len = len(before)
        log_text = before
        while True:
            time.sleep(poll_interval)
            log_text = self.get_cron_log(cron_id)
            new_part = log_text[len(before):]
            summary = parse_elm_cookie_summary(new_part)
            if summary is not None:
                return {
                    "finished": True, "timed_out": False,
                    "log": log_text, "summary": summary,
                }
            if len(log_text) == last_len and len(log_text) > len(before):
                stable_polls += 1
                if stable_polls >= 4:
                    return {
                        "finished": True, "timed_out": False,
                        "log": log_text, "summary": None,
                    }
            else:
                stable_polls = 0
            last_len = len(log_text)
            if time.monotonic() >= deadline:
                return {
                    "finished": False, "timed_out": True,
                    "log": log_text, "summary": None,
                }

    def get_cron_log(self, cron_id: int) -> str:
        data = self._req("GET", f"/open/crons/{cron_id}/log")
        if isinstance(data, dict):
            return str(data.get("data") or data.get("log") or "")
        return str(data or "")

    def _cron_action(self, action: str, cron_ids: list[int]) -> Any:
        return self._req("PUT", f"/open/crons/{action}", json=list(cron_ids))

    def run_cron(self, cron_id: int) -> Any:
        return self._cron_action("run", [cron_id])

    def stop_cron(self, cron_id: int) -> Any:
        return self._cron_action("stop", [cron_id])

    def enable_cron(self, cron_id: int) -> Any:
        return self._cron_action("enable", [cron_id])

    def disable_cron(self, cron_id: int) -> Any:
        return self._cron_action("disable", [cron_id])

    def delete_crons(self, cron_ids: list[int]) -> Any:
        return self._req("DELETE", "/open/crons", json=list(cron_ids))

    # -- envs ---------------------------------------------------------------
    def list_envs(self, search_key: str = "") -> list[dict]:
        params = {"searchKey": search_key} if search_key else None
        return self._as_list(self._req("GET", "/open/envs", params=params))

    def upsert_env(self, name: str, value: str, remarks: str = "") -> dict:
        """Create the env var if missing, else update it in place."""
        existing = [e for e in self.list_envs() if e.get("name") == name]
        if existing:
            env = existing[0]
            # Qinglong's PUT /open/envs on this panel takes ONE env object,
            # not an array: an array body fails with HTTP 400 '"value" must
            # be of type object' (proven live 2026-11-24 on a throwaway
            # var — both the full GET object AND a minimal array fail).
            # Only the DTO fields may be sent; extra GET fields
            # (createdAt/updatedAt/timestamp/...) also fail validation.
            payload: dict[str, Any] = {
                "name": name,
                "value": value,
                "remarks": remarks if remarks else str(env.get("remarks") or ""),
            }
            env_id = env.get("id")
            if env_id is None:
                # No id to update by: create instead.
                self._req("POST", "/open/envs", json=[{"name": name, "value": value, "remarks": remarks}])
                return {"action": "新建", "name": name}
            payload["id"] = env_id
            self._req("PUT", "/open/envs", json=payload)
            return {"action": "更新", "name": name}
        self._req("POST", "/open/envs", json=[{"name": name, "value": value, "remarks": remarks}])
        return {"action": "新建", "name": name}

    # -- scripts ------------------------------------------------------------
    def list_scripts(self) -> list[dict]:
        return self._as_list(self._req("GET", "/open/scripts"))

    # -- Ele.me cookie slots -------------------------------------------------
    def get_ele_slot_cookie(self, slot: int) -> str | None:
        """Return the cookie part of ELM_COOKIE{slot} (after the '#'),
        or None when the variable is missing/empty.

        The full value is NEVER logged by this method; callers must
        treat the return value as a secret and never print it.
        """
        name = _elm_slot_name(slot)
        for env in self.list_envs():
            if str(env.get("name") or "") != name:
                continue
            value = str(env.get("value") or "")
            for line in value.splitlines():
                line = line.strip()
                if not line:
                    continue
                cookie = line.split("#", 1)[1].strip() if "#" in line else line
                return cookie or None
            return None
        return None

    def list_ele_slots(self) -> list[dict]:
        """[{slot, name, remark}] for every ELM_COOKIE* var that exists.

        Values are NEVER returned — only the 备注 part before '#'.
        """
        out = []
        for env in self.list_envs():
            name = str(env.get("name") or "")
            if name == "ELM_COOKIE":
                slot = 1
            elif name.startswith("ELM_COOKIE") and name[len("ELM_COOKIE"):].isdigit():
                slot = int(name[len("ELM_COOKIE"):])
            else:
                continue
            value = str(env.get("value") or "")
            lines = [ln for ln in value.splitlines() if ln.strip()]
            remarks = [(ln.split("#", 1)[0] if "#" in ln else f"账号{slot}") for ln in lines]
            out.append({"slot": slot, "name": name, "remarks": remarks, "count": len(lines)})
        return sorted(out, key=lambda x: x["slot"])

    def list_ele_cookies(self) -> list[dict]:
        """[{remark, cookie, userid, slot, name}] for every account line
        in ELM_COOKIE* vars, in slot order.

        Values are returned because callers need the cookie itself to
        query balances / exchange; callers must never log or echo them.
        """
        out = []
        for env in self.list_envs():
            name = str(env.get("name") or "")
            if name == "ELM_COOKIE":
                slot = 1
            elif name.startswith("ELM_COOKIE") and name[len("ELM_COOKIE"):].isdigit():
                slot = int(name[len("ELM_COOKIE"):])
            else:
                continue
            value = str(env.get("value") or "")
            for line in value.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "#" in line:
                    remark, cookie = line.split("#", 1)
                    remark, cookie = remark.strip(), cookie.strip()
                else:
                    remark, cookie = f"账号{slot}", line
                if not cookie:
                    continue
                out.append({
                    "remark": remark or f"账号{slot}",
                    "cookie": cookie,
                    "userid": _cookie_userid(cookie),
                    "slot": slot,
                    "name": name,
                })
        return sorted(out, key=lambda x: x["slot"])

    def sync_ele_cookie(self, cookie: str, remark: str, slot: int = 1) -> dict:
        """Write one account's cookie into ELM_COOKIE (slot 1) or
        ELM_COOKIE<n> (slots 2..20), value format ``备注#完整Cookie``.

        MERGE, never overwrite: ELM_COOKIE holds one line per account
        (household rule since 2026-11-19), so only this account's line
        is replaced in place (matched by USERID, else by the leading
        remark/username) and the other accounts' lines are kept, in
        their original order. A wholesale overwrite would delete the
        other accounts' cookies (2026-11-24 01:40: 1793 re-login wiped
        the other 12 accounts out of ELM_COOKIE).
        """
        if not 1 <= slot <= MAX_ELM_SLOTS:
            raise QinglongError(f"slot 只能是 1~{MAX_ELM_SLOTS}")
        name = _elm_slot_name(slot)
        current = ""
        for env in self.list_envs():
            if str(env.get("name") or "") == name:
                current = str(env.get("value") or "")
                break
        new_line = f"{remark}#{cookie.strip()}"
        value = merge_cookie_lines(current, new_line, _cookie_userid(cookie), remark)
        return self.upsert_env(name, value, remarks=f"微信机器人同步 {remark}")

    # -- Kugou (KGGNB) ------------------------------------------------------
    def sync_kugou_line(self, line: str, userid: str, remark: str) -> dict:
        """Merge one Kugou account's token line into the KGGNB env var.

        The line is replaced only for this account (matched by userid,
        else by remark/username); other accounts' lines are kept, and
        the new line's remark wins (see merge_kggnb_lines). Returns
        the upsert result plus the remark actually used and whether a
        line was replaced (vs appended).
        """
        current = ""
        remarks = KGGNB_ENV_REMARKS
        for env in self.list_envs():
            if str(env.get("name") or "") == KGGNB_ENV_NAME:
                current = str(env.get("value") or "")
                remarks = str(env.get("remarks") or remarks)
                break
        value, final_remark, replaced = merge_kggnb_lines(
            current, line, userid, remark)
        result = self.upsert_env(KGGNB_ENV_NAME, value, remarks=remarks)
        result["remark"] = final_remark
        result["replaced"] = replaced
        return result
