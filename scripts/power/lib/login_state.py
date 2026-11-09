# -*- coding: utf-8 -*-
"""login_state.py — TEMU 卖家**登录态指纹**：谁把会话弄掉的，用数据说话。

来历（用户 2026-11-05）
----------------------
「为什么你去执行 BEL 的任务时，莫名其妙的所有页面的登录态全部消失，要重新登录：修复」

排查结论先说：**cookie 不是被清的**。两侧店窗口的 `Network/Cookies` 里
`seller_temp`（到次日）、`sessionType`（到 11-08）、`mallid`（到 12-05）都在，
两店 × 两域（us / cc）的真请求探针也全是 `ok`。
⇒ 掉的是**会话（服务端）**，不是本地 cookie；能造成这种"全体页面同时失效"的动作只有两类：
  ① `ziniao-cli store close`（关窗 → 所有页消失、重开必掉登录态）；
  ② Seller Central 的「当前已有登录中的店铺，确认切换吗」被点了确认
     ⇒ 本窗口的卖家会话被切到另一个店铺，原店铺**所有页面当场掉登录**。

这两条以前都**没有留证**：出事之后只能猜。所以本模块只做一件事 ——
在关键动作前后各读一次 cookie 指纹，掉了就当场喊出来（带 tag），把"莫名其妙"变成可归因。

只读保证：只发 `Network.getCookies`，不写 cookie、不导航、不点。
任何异常都降级成"读不到"，绝不阻断业务。
"""
from __future__ import annotations

import contextlib
import hashlib
import sys

#: 能代表"卖家会话还在"的 cookie 名（读不到其中任何一个 ⇒ 会话基本没了）。
#: `seller_temp` 是最直接的会话票；`mallid`/`sessionType` 是登录后才会有的店铺/会话标记。
SESSION_COOKIES = ("seller_temp", "mallid", "sessionType")

#: 采集范围（只要 host 命中其一即可）。
_HOST_HINTS = ("temu.com", "kuajingmaihuo.com")


def _log(msg: str, log=None) -> None:
    try:
        (log or print)(msg)
    except Exception:  # noqa: BLE001 - 打日志永远不能把业务弄崩
        pass


def _default_fetch(port: int, timeout: float = 8.0) -> list:
    """用**页面级** CDP 会话读 cookie（浏览器级不实现 `Storage.getCookies`，实测返回 0 条）。

    ⚠ 坑一：`cdp_transport.Cdp.send()` 返回的是**整条报文**，结果在 `["result"]` 里。
      直接按顶层取 `cookies` 会永远得到 0 条 —— 看起来像"cookie 全被清了"。

    ⚠ 坑二（2026-11-09 实测）：店里可能有**紫鸟自己的 `error.html`** 页
      （`chrome-extension://…/error.html?url=…`，网络被重置时出现）。
      在那种页上读 cookie 读到的是 **0 条** ⇒ 「会话本来好好的」被报成
      「seller_temp 无」，比不报还误导人。必须优先挑**店铺自己域**上的页来读。
    """
    sys.path.insert(0, __file__.rsplit("\\", 1)[0])
    import cdp_transport  # noqa: PLC0415

    pages = [t for t in cdp_transport.list_pages(int(port or 0))
             if t.get("type", "page") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return []

    def _rank(t):
        """越小越优先：店铺域 > 其它 http(s) 页 > 扩展/错误页。"""
        u = str(t.get("url") or "")
        if any(h in u for h in _HOST_HINTS):
            return 0
        if u.startswith("http"):
            return 1
        return 2

    pages.sort(key=_rank)
    r = cdp_transport.Cdp(pages[0]["webSocketDebuggerUrl"]).send(
        "Network.getCookies", {}, timeout=timeout) or {}
    return (r.get("result") or {}).get("cookies") or []


def fingerprint(port: int, fetch=None, timeout: float = 8.0) -> dict:
    """读一次卖家 cookie 指纹 → `{ok, cookies, session, digest, error}`。

    `session` 是 `{name: 是否在}`；`digest` 是名字集合的短哈希（值不落日志，避免泄密）。
    `fetch(port)` 是测试注入点，缺省走真实 CDP。
    """
    out = {"ok": False, "cookies": 0, "session": {}, "digest": "", "mallid": "", "error": None}
    if not port:
        out["error"] = "no-port"
        return out
    try:
        cs = (fetch or _default_fetch)(int(port), timeout)
    except TypeError:
        try:
            cs = (fetch or _default_fetch)(int(port))
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:80]
            return out
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:80]
        return out
    hit = [c for c in (cs or [])
           if any(h in str(c.get("domain") or "") for h in _HOST_HINTS)]
    names = sorted({str(c.get("name") or "") for c in hit if c.get("name")})
    out["ok"] = True
    out["cookies"] = len(hit)
    out["session"] = {k: (k in names) for k in SESSION_COOKIES}
    # mallid = 当前登录的店铺 ID。它**变了**（不是没了）就是"被切到别的店"，
    # 这正是 TEMU「当前已有登录中的店铺，确认切换吗」那个弹窗的后果。
    out["mallid"] = next((str(c.get("value") or "") for c in hit
                          if c.get("name") == "mallid"), "")
    # 会话票的过期时刻：TEMU 的 `seller_temp` 实测**只有 24 小时寿命**（登录后逐次续期）。
    # 过期时间落进日志，"为什么某天早上一来就要重新登录"就有了时间证据。
    out["expires"] = {str(c.get("name")): float(c.get("expires") or -1)
                      for c in hit if c.get("name") in SESSION_COOKIES}
    out["digest"] = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:10]
    return out


def lost(before: dict, after: dict) -> list:
    """前后对比：返回**会话变化**说明（空列表＝没变）。纯函数。

    两种变化都要抓：
      · 会话 cookie 消失 → 还没登录（关窗/重开/退出）；
      · `mallid` **换了值** → 被切到另一个店铺 —— 这是最阴的一种：
        cookie 看着齐全、探针也可能显示 ok，但原来的店铺会话已经没了，
        用户看到的就是「所有页面登录态全消失」。光比"有没有"抓不到它。
    """
    if not (before or {}).get("ok") or not (after or {}).get("ok"):
        return []                      # 有一侧读不到 ⇒ 不下结论（不制造假告警）
    b = before.get("session") or {}
    a = after.get("session") or {}
    gone = [k for k in SESSION_COOKIES if b.get(k) and not a.get(k)]
    bm, am = str((before or {}).get("mallid") or ""), str((after or {}).get("mallid") or "")
    if bm and am and bm != am:
        gone.append("mallid:%s→%s" % (bm[:6], am[:6]))
    return gone


#: 会话票剩余寿命低于这个小时数就**提前告警**。
#:
#: 实测（2026-11-05 本机）：`seller_temp` 的有效期恒等于**上次成功授权时刻 + 24 小时**
#: （BEL 14:29 授权 → exp 次日 14:29；BHY 08:53 授权 → exp 次日 08:53）。
#: ⇒ 这解释了"莫名其妙"的那一半：**没人动它，到点也会被服务端踢下线**，
#:   表现就是所有页面同时失效、要重新登录。提前几小时喊一声，就不会再是"突然"。
EXPIRE_WARN_HOURS = 3.0


def hours_left(fp: dict, name: str = "seller_temp") -> float:
    """会话票还剩多少小时（读不到/无该 cookie/已过期 → -1 / 0）。"""
    import time as _t
    exp = ((fp or {}).get("expires") or {}).get(name) or -1
    try:
        exp = float(exp)
    except (TypeError, ValueError):
        return -1.0
    if exp <= 0:
        return -1.0
    return max(0.0, (exp - _t.time()) / 3600.0)


def expiring(fp: dict, within_hours: float = EXPIRE_WARN_HOURS) -> str:
    """会话票是否即将到期 → 返回告警文案（"不用告警"返回 ""）。纯函数。"""
    if not (fp or {}).get("ok"):
        return ""
    h = hours_left(fp)
    if h < 0:
        return ""
    if h > within_hours:
        return ""
    return ("会话票 seller_temp 只剩 %.1f 小时（< %.1f h）→ 到时服务端会把"
            "**所有页面**一起踢下线。想避开就提前跑一次 run_temu_auth.py（会换新票）"
            % (h, within_hours))


def describe(fp: dict) -> str:
    """一行摘要，写日志用。"""
    if not (fp or {}).get("ok"):
        return "读不到（%s）" % ((fp or {}).get("error") or "?")

    def _m(k):
        return "有" if (fp.get("session") or {}).get(k) else "无"
    exp = (fp.get("expires") or {}).get("seller_temp") or -1
    exp_s = "?"
    if exp and exp > 0:
        import time as _t
        exp_s = _t.strftime("%m-%d %H:%M", _t.localtime(exp))
    return "cookie %d 条｜seller_temp=%s(到 %s) mallid=%s sessionType=%s｜%s" % (
        fp.get("cookies") or 0, _m("seller_temp"), exp_s,
        _m("mallid"), _m("sessionType"), fp.get("digest"))


@contextlib.contextmanager
def watch(port: int, tag: str, log=None, fetch=None):
    """关键动作前后各记一次登录态；**掉了就当场喊**（不阻断动作）。

        with login_state.watch(port, "run_temu_auth"):
            ...

    为什么放在 lib 而不是散落到各脚本：掉登录态的嫌疑动作有十几处
    （关窗/重开/清障/授权/切换店铺），逐处手写必然漏。包装成上下文，接一次管一段。
    """
    before = fingerprint(port, fetch=fetch)
    _log("[登录态] %s 前：%s" % (tag, describe(before)), log)
    try:
        yield before
    finally:
        after = fingerprint(port, fetch=fetch)
        gone = lost(before, after)
        if gone:
            _log("[登录态] ⚠ %s 期间丢了 %s —— 这就是「要重新登录」的直接原因"
                 "（前：%s ／ 后：%s）" % (tag, "/".join(gone), describe(before), describe(after)),
                 log)
        else:
            _log("[登录态] %s 后：%s" % (tag, describe(after)), log)
