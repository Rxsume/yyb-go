"""Cookie 失效检查（手动 [s]/[r] 菜单用，无自动提醒）。

失效判断跟 elmxyx 用的是同一个接口：幸运星的 mtop 首页查询
（app.lucky_star.query_star_balance），它跟 elmxyx 的 mtop 一样，
会从响应的 Set-Cookie 里取刷新后的 _m_h5_tk 再试一次。
所以存的 token 放了一天过期、但会话还活着时，这里照样判活，
不会再像之前那个 CURRENT_USER_URL 探活一样误报。

状态：
- ok：余额查到了，活着（note 带余额作证）
- dead：接口明确说 Cookie 已失效（刷新后仍过期）
- unknown：网络问题或服务端其他拒绝——不算失效，别误判

自动监控和提醒已撤（用户 2026-11-20 09:15 令关），只有用户
手动发 [s] 时才调这里。
"""

from __future__ import annotations


def check_all(qinglong) -> list[dict]:
    """Per account: {"remark", "status", "note"}; status is ok/dead/unknown."""
    from .lucky_star import LuckyStarError, query_star_balance

    rows = []
    for acc in qinglong.list_ele_cookies():
        remark = acc.get("remark") or "账号"
        cookie = acc.get("cookie") or ""
        if not cookie:
            rows.append({"remark": remark, "status": "dead", "note": "Cookie 为空"})
            continue
        try:
            balance = query_star_balance(cookie)
        except LuckyStarError as exc:
            msg = str(exc)
            if "已失效" in msg:
                rows.append({"remark": remark, "status": "dead", "note": msg})
            else:
                rows.append({"remark": remark, "status": "unknown", "note": msg})
        else:
            rows.append(
                {"remark": remark, "status": "ok", "note": f"余额 {balance} 星"})
    return rows
