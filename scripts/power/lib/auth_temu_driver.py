# -*- coding: utf-8 -*-
"""auth_temu_driver.py — TEMU 商家中心登录/授权 driver（按 temu-login-auth SKILL 固化，临时用）。

流程：
    A. 若在认证页(agentseller-us.temu.com/auth/authentication, 区域选择)：
       点可用区域里「商家中心」(div.authentication_goto) → 打开授权确认。
    B. 授权确认(卖家登录 tab, seller.kuajingmaihuo.com/settle/seller-login 或含授权框的 tab)：
       点击授权条款 checkbox(input尺寸0, 点可见外层LABEL/DIV) → 点「确认授权并前往」。
    C. 验证进入**商家中心可用页**（`agentseller-us.temu.com` 的 `/mmsos/` 下、非 auth/login 页；
       落地 `orders.html` 或 `export-order.html` 均算成功。TargetClosedError 属预期跳转）。

用法: python auth_temu_driver.py <port> <店后缀>
"""
import sys, os, json, time
# 注：本模块顶层不包装 sys.stdout（避免被调用方入口再次包装后产生嵌套/closed file）。
# stdout 的 UTF-8 负责由调用方（run_temu_auth.py 已包装）或独立跑的 __main__ 入口兜底。
# ★ 2026-10-10（RL-013）：**不再 import playwright** —— 连接层已换成 target-level
#   `cdp_transport`（Playwright `connect_over_cdp` 连紫鸟窗口 15s 超时，授权链根本走不动）。
#   `cdp_transport` 补齐了 locator / get_by_text / mouse 等同形 API，本模块写法不变。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp_transport  # noqa: E402  （power/lib 同目录单点实现）


def _proj():
    """项目根——解析器优先；独立运行(无 lib 在 path)兜底 Path.home()/Desktop/团队工具开发。去用户名绑定。"""
    try:
        from lib import paths as p
        return p.PROJECT_ROOT
    except Exception:
        from pathlib import Path
        return Path.home() / "Desktop" / "团队工具开发"


LOG_DIR = str(_proj() / "_logs/temu-sales")
os.makedirs(LOG_DIR, exist_ok=True)


def _say(*a):
    print(*a)
    return a


def log_record(shop, stage, msg, verdict="ok"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(os.path.join(LOG_DIR, time.strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "agent": "TEMU-auth", "shop": shop, "stage": stage,
                                "result": str(msg)[:400], "verdict": verdict}, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _click_el(pg, sel, timeout=6000) -> bool:
    try:
        l = pg.locator(sel)
        if l.count():
            l.first.click(timeout=timeout)
            return True
    except Exception:
        pass
    return False


def _click_text(pg, texts, timeout=6000, exact=True) -> bool:
    for t in texts:
        try:
            l = pg.get_by_text(t, exact=exact)
            if l.count():
                l.first.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


#: 区域页「商家中心」可点节点的选择器候选（**TEMU 会改版，这里按实测追加，别删旧的**）：
#:   · `authentication_goto`      —— 2026-08-29 实测版（原实现只认它）
#:   · `authentication_regionSuffix` / `authentication_regionItem` —— **2026-09-24 实测版**
#:     （页面 DOM 现取：`authentication_regionList__Vaypv > authentication_regionItem__g1pZV`
#:       内是 `authentication_regionPre__GrP0Q`=中国地区 + `authentication_regionSuffix__XmfLn`=商家中心；
#:       **已无 `authentication_goto`** ⇒ 老选择器点了个空，表现是「未进入商家中心页」→ FAIL rc=42）
#: 认证页「点商家中心」的选择器候选，**按命中优先级排**。
#:
#: ⚠ 2026-10-27 实测（BEL，计时）：顺序放错会让 region 阶段**慢到 591 秒**。
#:   当时顺序是 regionSuffix → regionItem → goto，于是先对
#:   `regionSuffix`(2 个) / `regionItem`(2 个) 各试 JS+坐标 = 8 次全无效（约 10 分钟），
#:   最后才轮到真正可点的 `authentication_goto`（第 1 次坐标点击就成功）。
#:   ⇒ 把 `authentication_goto` 提到最前：它是**按钮本体**，另外两个只是文字片段。
#:   正常应在 1~2 次尝试、几秒内完成。
REGION_SELECTORS = (
    "div[class*=authentication_goto]",
    "div[class*=authentication_regionItem]",
    "div[class*=authentication_regionSuffix]",
)


def _real_click(pg, loc) -> bool:
    """**真实鼠标**点元素中心（先滚进视口；坐标现场取，不写死）。

    ⚠ 2026-10-10 实测（BHY 认证页，同一元素、同一时刻）：
        坐标点 `pg.mouse.click(...)` → **没反应**（页面 tab 数 2→2）
        JS 点 `el.click()`          → **成功**（tab 数 2→3，新开授权框）
     原因：本流程用的页多是 **后台标签页**（`new_page(background=True)` /
       `_navigate_to_orders` 新开），CDP 的 `Input.dispatchMouseEvent` 打在**非激活页**上
       不落到元素 → 坐标点在后台页**不可靠**。
     ⇒ 现在 `_click_region_goto` / `_check_auth_checkbox` / `_click_auth_confirm` 一律
       **先 JS 点、失败再退回坐标点**（见 `_js_click`）。本函数保留为兜底。
    """
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
        box = loc.bounding_box()
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return False
        pg.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return True
    except Exception:
        return False


#: JS 点击：命中元素若是最内层 SPAN/I，则改点它最近的 a/button/div
#: （TEMU 的可点元素常是外层容器；直接点文字节点不触发。照抄 temu_abnormal_driver 可用实现）。
_JS_CLICK_EL = """el => {
  const tag = el.tagName;
  const target = (tag === 'SPAN' || tag === 'I') ? (el.closest('a,button,div') || el.parentElement) : el;
  if (!target) return false;
  target.click();
  return true;
}"""


def _js_click(pg, loc) -> bool:
    """JS 点元素（后台标签页里比坐标点可靠 —— 见 `_real_click` 的实测注释）。"""
    try:
        return bool(loc.evaluate(_JS_CLICK_EL))
    except Exception:
        return False


def _region_advanced(pg, b=None) -> bool:
    """点了区域项之后页面有没有往下走。

    判据（任一成立即算推进）：
      ① 当前页 URL 离开认证页；
      ② 当前页出现授权确认框（有 checkbox）；
      ③ ★ 2026-10-10 加：**窗口里新出现了授权页**（`settle/seller-login`）——
         TEMU 点「商家中心」后是**新开一个 tab** 承载授权框，当前页 URL **根本不变**。
         旧判据只看当前页 ⇒ 把"其实点成功、新 tab 已开"误判成「没反应」（2026-10-10 实测：
         点完确实新开了 `seller.kuajingmaihuo.com/settle/seller-login`，但流程报「点到 goto 失败」）。
    """
    try:
        u = str(pg.url or "")
        if "auth/authentication" not in u:
            return True
    except Exception:
        return True
    try:
        if pg.locator("input[type=checkbox]").count() > 0:
            return True
    except Exception:
        pass
    # ③ 新 tab 判据：窗口里是否已有授权框页（含刚被我们点出来的那张）
    if b is not None:
        try:
            for ctx in b.contexts:
                for q in ctx.pages:
                    if "settle/seller-login" in (q.url or "").lower():
                        return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _click_region_goto(pg, b=None) -> bool:
    """认证页(区域选择)点可用区域里「商家中心」。

    2026-08-29 实测：KinglamanSport 会出现「Seller Central 当前已有登录中的店铺，确认切换吗」
    弹窗（切换 KinglamanSport → KinglamanSport local），**覆盖**区域 goto 导致点击失败——
    必须先点「确认切换」。
    2026-09-24 实测：区域项 class 改版（见 `REGION_SELECTORS` 注释），且**用真实鼠标点**才生效。
    2026-10-10：`b`（浏览器壳）传给 `_region_advanced` 做「新 tab」判据 —— TEMU 点商家中心是
    **新开 tab**，当前页 URL 不变，只看当前页会把成功误判成「没反应」。
    """
    # ① 若出现「确认切换吗」弹窗，先点「确认切换」(按钮文本在 footer)
    try:
        switch = pg.get_by_text("确认切换", exact=True)
        if switch.count():
            switch.first.click(timeout=4000)
            pg.wait_for_timeout(1500)
    except Exception:
        pass
    # ② 依次试选择器候选 → **先 JS 点（后台页可靠），失败再坐标点**；点完确认确实往下走了
    for sel in REGION_SELECTORS:
        try:
            loc = pg.locator(sel)
            if not loc.count():
                continue
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                for how, do in (("JS", _js_click), ("坐标", _real_click)):
                    if not do(pg, el):
                        continue
                    pg.wait_for_timeout(2500)
                    if _region_advanced(pg, b):
                        _say("[区域] %s点 %s（第 %d 个）→ 页面已推进" % (how, sel, i + 1))
                        return True
                    _say("[区域] %s点 %s（第 %d 个）没反应 → 试下一种" % (how, sel, i + 1))
        except Exception:
            continue
    # ③ 兜底：按文本点「商家中心」（可能命中标题节点，故每个候选都点一遍并复查是否推进）
    try:
        loc = pg.get_by_text("商家中心", exact=True)
        for i in range(min(loc.count(), 5)):
            el = loc.nth(i)
            for do in (_js_click, _real_click):
                if do(pg, el):
                    pg.wait_for_timeout(2000)
                    if _region_advanced(pg, b):
                        return True
    except Exception:
        pass
    return False


def _sync_input_state(pg) -> int:
    """把已填入 DOM 的账密同步进前端框架状态（React/Vue）。

    2026-09-28 实测（BHY）：紫鸟把账号/密码写进 DOM value 后**没有派发 input/change 事件**，
    React 受控组件的内部 state 仍是空串 ⇒ 点「登录/授权登录」时表单校验失败、处理器直接空转
    （页面不跳转、连一条 XHR 都不发）。用原生 value setter 重写当前值再派发 input/change，
    让框架 state 与 DOM 对齐，按钮才会真正提交。返回同步的输入框个数（0 = 无需同步）。
    """
    try:
        return int(pg.evaluate("""() => {
            const proto = window.HTMLInputElement.prototype;
            let n = 0;
            document.querySelectorAll('input').forEach(el => {
                if (el.type === 'checkbox' || el.type === 'radio' || el.readOnly) return;
                if (!el.value) return;
                try {
                    const set = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    set.call(el, el.value);
                } catch (e) { el.value = el.value; }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                n++;
            });
            return n;
        }""") or 0)
    except Exception:
        return 0


def _check_auth_checkbox(pg) -> bool:
    """勾选授权条款 checkbox（input 尺寸0，点可见外层 LABEL/DIV）。

    2026-09-28 实测（BHY 授权页）：在 evaluate 里做 DOM `label.click()` **勾不上**
    （该 UI 的 LABEL 点击不向内部 input 转发，React 也不认），界面始终显示未勾选；
    必须走 Playwright **真实鼠标**点 LABEL 才生效。故改为：真实鼠标点 LABEL → 复核
    `input.checked` 真勾上才算成功；不生效再退回 DOM 方案。已勾选则直接返回 True。
    """
    try:
        cbs = pg.locator("input[type=checkbox]")
        n = cbs.count()
    except Exception:
        return False
    if n == 0:
        return False

    def _checked() -> bool:
        for i in range(n):
            try:
                if cbs.nth(i).is_checked():
                    return True
            except Exception:
                continue
        return False

    if _checked():
        return True

    # ① **先 JS 点 LABEL**（2026-10-10：后台标签页里坐标点不生效；本域可用实现
    #    temu_abnormal_driver 正是 JS 点 LABEL），复核 `input.checked` 真勾上
    try:
        labels = pg.locator("label:has(input[type=checkbox])")
        for i in range(min(labels.count(), 3)):
            try:
                if _js_click(pg, labels.nth(i)):
                    pg.wait_for_timeout(400)
                    if _checked():
                        return True
            except Exception:
                continue
    except Exception:
        pass

    # ② 再用真实鼠标点 LABEL（前台页有效）
    try:
        labels = pg.locator("label:has(input[type=checkbox])")
        for i in range(min(labels.count(), 3)):
            try:
                labels.nth(i).click(timeout=4000)
                pg.wait_for_timeout(300)
                if _checked():
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # ② 兜底：DOM 点可见外层 LABEL/DIV
    for i in range(n):
        try:
            cbs.nth(i).evaluate("""el => {
                let node = el.parentElement;
                for (let k = 0; k < 6 && node; k++) {
                    if (node.tagName === 'LABEL') { node.click(); return true; }
                    node = node.parentElement;
                }
                return false;
            }""")
        except Exception:
            continue
        pg.wait_for_timeout(250)
        if _checked():
            return True
    return _checked()


# 授权/登录确认按钮候选：BHY=「授权登录」，BEL=「确认授权并前往」，兼容 BHY 新 label
# 2026-09-28 实测：BHY 授权页按钮文本只剩「登录」（手机号登录表单 + 授权条款 checkbox，
# 账密由紫鸟自动填好），原候选/正则都不含纯「登录」→ NOT_FOUND 卡死。故补末位候选。
_AUTH_CONFIRM_BTNS = ["授权登录", "确认授权并前往", "授权并前往", "确认授权", "登录"]
_AUTH_CONFIRM_RE = r"^授权的?(登录|并前往)|^确认授权|授权登录|^登录$"


def _dispatch_real_mouse(pg, loc) -> bool:
    """在元素中心派发**完整**鼠标事件序列（moved → pressed → released）。

    2026-10-27 加。`cdp_transport._Locator.click()` 只调 `page.mouse.click()`，
    而 `CdpPage.mouse.click` 发的是 mouseMoved/Pressed/Released **不带 `buttons`**。
    BEL 授权页实测：不带 `buttons` 的事件序列对「确认授权并前往」无效；
    带上 `buttons:1`（pressed 时）才真正触发跳转。故这里自己派发一遍。
    """
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    try:
        box = loc.bounding_box()
    except Exception:  # noqa: BLE001
        return False
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        return False
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    try:
        cdp = pg.cdp_session()
    except Exception:  # noqa: BLE001
        return False
    try:
        cdp.send("Input.dispatchMouseEvent",
                 {"type": "mouseMoved", "x": x, "y": y, "buttons": 0})
        cdp.send("Input.dispatchMouseEvent",
                 {"type": "mousePressed", "x": x, "y": y,
                  "button": "left", "buttons": 1, "clickCount": 1})
        cdp.send("Input.dispatchMouseEvent",
                 {"type": "mouseReleased", "x": x, "y": y,
                  "button": "left", "buttons": 0, "clickCount": 1})
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass


def _confirm_advanced(pg, before_url: str, before_n: int) -> bool:
    """点完确认按钮后，页面**真的**往下走了吗？

    判据（任一成立即算推进）：
      ① 当前页 URL 变了（离开 seller-login / 认证页）；
      ② 窗口页数变了（TEMU 会新开 tab 承载后续）；
      ③ 当前页已变成商家中心可用页。
    只看「点到了元素」是不够的 —— 见 `_click_auth_confirm` 的 2026-10-27 注释。
    """
    pg.wait_for_timeout(2500)
    try:
        u = str(pg.url or "")
        if u and u != before_url:
            return True
        if _url_is_seller_page(u):
            return True
    except Exception:  # noqa: BLE001
        # 页已关闭/跳转 ⇒ 本身就是推进的强信号
        return True
    try:
        n = len(_pages_via_cdp(getattr(pg, "port", 0)))
        if n != before_n:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _try_all_click_methods(pg, loc, label: str) -> bool:
    """依次用 JS → 真实鼠标(带 buttons) → locator.click 点，每一步都复核是否推进。

    返回 True 仅当**页面确实推进了**。全部方式都点不动则返回 False，
    由调用方上报真实失败（而不是谎报成功）。
    """
    before_url = ""
    before_n = 0
    try:
        before_url = str(pg.url or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        before_n = len(_pages_via_cdp(getattr(pg, "port", 0)))
    except Exception:  # noqa: BLE001
        pass

    for how, fn in (
        ("JS click", lambda: _js_click(pg, loc)),
        ("真实鼠标(buttons)", lambda: _dispatch_real_mouse(pg, loc)),
        ("locator.click", lambda: (loc.click(timeout=6000), True)[1]),
    ):
        try:
            if not fn():
                _say("[授权] %s %s 点不动 → 换下一种" % (how, label))
                continue
        except Exception as e:  # noqa: BLE001
            _say("[授权] %s %s 异常：%s" % (how, label, str(e)[:70]))
            continue
        if _confirm_advanced(pg, before_url, before_n):
            _say("[授权] %s 点 %s → 页面已推进" % (how, label))
            return True
        _say("[授权] %s 点 %s 没反应 → 换下一种" % (how, label))
        # 下一次尝试前刷新基线（页面可能已被上一次部分推进）
        try:
            before_url = str(pg.url or "")
            before_n = len(_pages_via_cdp(getattr(pg, "port", 0)))
        except Exception:  # noqa: BLE001
            pass
    return False


def _click_auth_confirm(pg, timeout=8000) -> bool:
    """点授权/登录确认按钮（候选文本 + 正则兜底）。

    返回 True **仅当页面确实推进了**（URL 变了 / 新开 tab / 已落商家中心页）。

    ⚠ 2026-10-27 修（BEL 实测，重要）：**「点了」不等于「生效」。**
      本函数原来只要 `_js_click()` 返回 True 就立刻 `return True` —— 而 `_js_click`
      只判断「元素存在且调用了 el.click()」，**从不看页面有没有动**。
      BEL 实测铁证：`[授权] JS 点「确认授权并前往」` 打了，url 一点没变；
      换成**真实鼠标事件**（mouseMoved + mousePressed + mouseReleased，带 `buttons`）
      立刻跳转成功。
      ⇒ 现在每种点击方式都用 `_try_all_click_methods()` 复核是否推进，
        不推进就换下一种；全都不推进则返回 False，由调用方如实上报。
      这是"静默失败"的典型：日志看着成功，实际卡死在授权页，无人值守每天挂。
    """
    for t in _AUTH_CONFIRM_BTNS:
        try:
            l = pg.get_by_text(t, exact=True)
            if not l.count():
                continue
            if _try_all_click_methods(pg, l.first, "「%s」" % t):
                return True
        except Exception:  # noqa: BLE001
            continue
    # 正则兜底（匹配「授权登录」「确认授权并前往」）
    try:
        els = pg.get_by_text(_AUTH_CONFIRM_RE)
        if els.count():
            if _try_all_click_methods(pg, els.first, "(正则)"):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


#: 商家中心「已登录」的合法区域前缀（host 必须是 agentseller-us.temu.com）。
#: ★ 2026-10-10 加 `/main/`：实测 BHY 窗口停在 `agentseller-us.temu.com/main/flux-analysis`
#:   （页面已显示店铺名 HYGINIX ⇒ **明明是登录态**），但旧判据只认 `/mmsos/` ⇒ 判成"未登录"
#:   ⇒ 反复触发不必要的授权 ⇒ 每次都可能踩 TEMU 风控「请访问agentSeller主页操作登录」。
#:   保留 `/mmsos/`（订单/履约域，授权后主要落地处）并补 `/main/`（数据/经营域）。
SELLER_PATH_PREFIXES = ("/mmsos/", "/main/")


def _url_is_seller_page(u: str) -> bool:
    """D4 判据：host=agentseller-us.temu.com 且 path 落在**已登录区域**且不是登录/认证页。

    沿革：
      · 2026-09-16 放宽：授权是从 `.../mmsos/export-order.html` 的 redirectUrl 进来的，
        点完授权后**落地页是 `export-order.html`** 而不是 `orders.html`；旧判据只认
        `orders.html` ⇒ 把**授权已成功**判成「未进入订单页」→ FAIL + exit 42（假失败）。
      · 2026-10-10 再放宽：加 `/main/`（见 `SELLER_PATH_PREFIXES` 注释）。

    仍**必须 host + path**（禁止整串 `in`）：认证页 URL 的 query 里带
    `redirectUrl=...%2Fmmsos%2Forders.html`，用整串包含判断会误报成功（TLA-011）；
    path 判断天然免疫该陷阱。
    """
    try:
        from urllib.parse import urlparse
        p = urlparse(str(u or ""))
        if p.netloc != "agentseller-us.temu.com":
            return False
        path = (p.path or "").lower()
        if not any(path.startswith(pref) for pref in SELLER_PATH_PREFIXES):
            return False
        return not any(k in path for k in ("auth", "login", "authentication"))
    except Exception:
        return False


def _page_is_seller(pg) -> bool:
    """页面是否已落在商家中心可用页（/mmsos/ 下非认证页）。"""
    try:
        return _url_is_seller_page(pg.url)
    except Exception:
        return False


def _wait_seller_page(port: int, deadline_s: float = 30.0, log=None, shop: str = "") -> str:
    """**轮询**等待窗口里出现「商家中心可用页」，返回其 URL；超时返回空串。

    ★ 2026-10-10 加：点完「授权登录」后跳转可能要**十几秒**。旧实现固定等 4s 只查一次，
    把**已经登录成功**误判成「授权未完成」→ rc=42（实测：报 FAIL 后窗口其实已在
    `agentseller-us.temu.com/mmsos/orders.html`）。
    这里每 3s 复查一次（用原始 CDP 列页，避免旧 page 已关闭导致异常），直到超时。
    """
    waited = 0.0
    while True:
        try:
            for t in _pages_via_cdp(port):
                u = str(t.get("url") or "")
                if _url_is_seller_page(u):
                    return u
        except Exception:                                      # noqa: BLE001
            pass
        if waited >= deadline_s:
            return ""
        time.sleep(3)
        waited += 3
        if log and waited % 9 == 0:
            try:
                log("[%s] 等商家中心页落地… 已等 %ds/%ds" % (shop, int(waited), int(deadline_s)))
            except Exception:                                  # noqa: BLE001
                pass


def _pages_via_cdp(port: int) -> list:
    """原始 CDP `/json/list` 列页面（不依赖 Playwright 对象——授权跳转后旧 page 会关闭，TLA-010）。"""
    import urllib.request
    raw = urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(port), timeout=5).read()
    return [t for t in json.loads(raw) if t.get("type") == "page"]


def _entered_seller_page_via_cdp(port: int, tries: int = 6, gap: float = 2.0) -> str:
    """等窗口落到商家中心可用页（/mmsos/ 下非认证页，含 orders.html / export-order.html），返回其 URL（超时返回空串）。

    为什么必须用原始 CDP 复核：点「授权登录」后页面**跳转/关闭**会让 Playwright 抛
    `Target page, context or browser has been closed`（TLA-010，属预期），此时旧 page 对象已不可用，
    只能从 `/json/list` 看窗口的真实落地页——否则授权明明成功也会被报成 ERR/exit 42（2026-09-15 实测）。
    """
    for _ in range(max(1, tries)):
        try:
            for t in _pages_via_cdp(port):
                if _url_is_seller_page(t.get("url") or ""):
                    return t.get("url") or ""
        except Exception:
            pass
        time.sleep(gap)
    return ""


def _page_has_auth_ui(pg) -> bool:
    """这页是不是**真的有授权界面**（条款 checkbox 或「授权」字样）——用来躲开遗留死 tab。"""
    try:
        if pg.locator("input[type=checkbox]").count() > 0:
            return True
        return pg.get_by_text("授权", exact=False).count() > 0
    except Exception:
        return False


def find_auth_page(b):
    """定位需要处理的页面：优先「授权确认框」tab（settle/seller-login，含条款checkbox+授权登录），其次 authentication 认证页。

    2026-08-29 实测坑：不要匹配 seller.kuajingmaihuo.com/login（卖家统一扫码/手机号登录页）——
    它没有自动填账密、没有授权条款 checkbox、没有「授权登录」按钮，是另一种入口；误抓它会导致授权失败。
    只认 settle/seller-login（授权确认框，BHY/BEL/KinglamanSport 授权走的都是它）。

    **2026-09-24 修**：窗口里常躺着**上一次授权遗留的旧 settle/seller-login 死 tab**，而
    「点区域页的商家中心」会**新开**一个真正的授权页。旧实现在这里取**第一个**命中 ⇒ 在死 tab 上
    勾条款/点授权（它什么都没有）⇒ 一律失败（今天 BEL 实测：点完确实新开了
    `seller.kuajingmaihuo` 授权页，但流程仍报「未进入商家中心页」）。现在：**优先有授权界面的那个，
    其次取最新打开的**（Playwright 的 pages 顺序＝打开顺序，新的在后）。
    """
    cands = []
    for ctx in b.contexts:
        for pg in ctx.pages:
            u = (pg.url or "").lower()
            if "settle/seller-login" in u or "settle%2fseller-login" in u or ("settle" in u and "seller-login" in u):
                cands.append(pg)
    if cands:
        for pg in reversed(cands):                 # 新的在后 → 从后往前找"真的有授权界面"的
            if _page_has_auth_ui(pg):
                return pg, "seller-login"
        return cands[-1], "seller-login"
    for ctx in b.contexts:
        for pg in ctx.pages:
            if "authentication" in (pg.url or "").lower():
                return pg, "authentication"
    return None, None


def _go_home_and_click_region(b, pg) -> bool:
    """★ 2026-10-10 加：把窗口拉回 **agentSeller 主页**，再点区域「商家中心」走正规链路。

    为什么必须这样：TEMU 风控会拦「直接停在授权框页点登录」的动作，页面提示
    「请访问agentSeller主页操作登录」且**不发请求**。平台要求的链路是：
        agentSeller 主页(区域页 /auth/authentication)
          → 点「中国地区 · 商家中心」(div[class*=authentication_goto])
          → 平台**自己新开**真正的授权框(settle/seller-login)
          → 在**那个**页上勾条款 + 点登录

    这里只负责前两步（导航 + 点区域）。返回 True 表示「已尝试点击」。

    ⚠ 用 `pg.goto` 而不是新开 tab：复用当前页，避免在窗口里再堆一个 tab；
      主页地址取**不带 redirectUrl** 的根（带 redirectUrl 会直接跳授权框，又踩风控）。
    """
    try:
        pg.goto("https://agentseller-us.temu.com/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4000)
    except Exception:                                   # noqa: BLE001
        return False
    # 主页上点「中国地区 · 商家中心」（走 REGION_SELECTORS 候选，同 _click_region_goto）
    try:
        return bool(_click_region_goto(pg, b))
    except Exception:                                   # noqa: BLE001
        return False


def _navigate_to_orders(b, ctx):
    """开新 tab 导航到 TEMU 订单页 URL（不带 uId，登录态有效→订单页；未登录→重定向 auth/authentication）。

    2026-08-29 实测：窗口重开后可能落在 login 扫码页 / 只有扩展 tab / 首页，
    find_auth_page 找不到认证页 → 自动去订单页触发登录校验，把窗口拉回认证流程。
    不要带 uId（uId 有的店专属，非 BHY 店带会跳首页）。

    ⚠ 2026-10-10：原签名收 Playwright 实例 `p`（只为了旧连接层）；换 cdp_transport 后
    已无用，故去掉。`ctx.new_page()` / `page.goto(...)` 由 `cdp_transport.CdpContext/CdpPage` 提供。
    """
    page = ctx.new_page()
    page.goto("https://agentseller-us.temu.com/mmsos/orders.html",
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    return page


def run_auth(port: int, shop: str) -> str:
    # ★ 2026-10-10 修（RL-013）：连接层从 Playwright `connect_over_cdp` 换成
    #   `cdp_transport.connect`（target-level 直连）。原写法实测
    #   `BrowserType.connect_over_cdp: Timeout 15000ms exceeded`（ws 已连、初始化不完成）
    #   ⇒ **整条授权链走不下去**，表现是「点商家中心 goto 失败」→ 无人值守每天挂。
    #   `cdp_transport` 是本域为绕开 RL-013 写的同形壳，且已补齐 locator/get_by_text
    #   （见 cdp_transport._CdpLocator），驱动代码无需改写法。
    #
    # ★ 2026-10-27：进来先记下**已经存在**的标签页，收尾时只关"本次新开的、商家中心域的"。
    #   用户口径：「每触发任务打开的新页面，用完即关，不能动我正在使用的页面」。
    #   `_navigate_to_orders()` 会 `ctx.new_page()` 开新 tab，旧实现从没关过 ⇒ 每跑一次漏一个。
    before, before_urls = cdp_transport.snapshot_tabs(port)
    b = cdp_transport.connect(port)
    try:
        # 前置：若窗口已在商家中心可用页（/mmsos/ 下非认证页，登录态有效），无需授权
        for ctx in b.contexts:
            for pp in ctx.pages:
                if _page_is_seller(pp):
                    _say("[%s] 已在商家中心页（登录态有效），无需授权: %s" % (shop, pp.url[:80]))
                    log_record(shop, "auth_skip", "已在 TEMU 商家中心页无需授权")
                    return "OK: 已登录，无需授权"
        # 引导导航：窗口无认证页（login 页/扩展 tab/首页）→ 去订单页触发登录校验
        pg, kind = find_auth_page(b)
        if pg is None:
            _say("[%s] 无认证/授权页，尝试去订单页触发登录校验..." % shop)
            try:
                ctx = b.contexts[0] if b.contexts else b.new_context()
                _navigate_to_orders(b, ctx)
                pg, kind = find_auth_page(b)
                if pg is None:
                    # 去订单页后仍无认证页且未落到商家中心页 → 可能登录态有效直接进页，重探
                    for ctx2 in b.contexts:
                        for pp in ctx2.pages:
                            if _page_is_seller(pp):
                                _say("[%s] 去订单页后已进入商家中心页，无需授权: %s" % (shop, pp.url[:80]))
                                log_record(shop, "auth_skip", "去订单页后已登录")
                                return "OK: 已登录，无需授权"
                    _say("[%s] 去订单页后仍无认证/授权页" % shop)
                    log_record(shop, "find_auth", "去订单页后仍无认证页", "fail")
                    return "FAIL: 去订单页后仍无认证页"
            except Exception as e:
                _say("[%s] 引导导航异常: %s" % (shop, str(e)[:80]))
                log_record(shop, "navigate", str(e)[:80], "fail")
        for attempt in range(1, 3):
            # 每轮先复查：授权可能已在上一轮生效（此时窗口里通常还留着一个空白 `settle/seller-login`
            # 死 tab，find_auth_page 会优先命中它 → 旧实现在死 tab 上"再授权"一次，最后报 FAIL；
            # 2026-09-16 三家实测）。已落在商家中心页就直接成功返回。
            for ctx0 in b.contexts:
                for pp0 in ctx0.pages:
                    if _page_is_seller(pp0):
                        _say("[%s] attempt%d 复查已在商家中心页: %s" % (shop, attempt, pp0.url[:80]))
                        log_record(shop, "auth", "进入 TEMU 商家中心页: %s" % pp0.url[:80])
                        return "OK: 进入订单页"
            pg, kind = find_auth_page(b)
            if pg is None:
                _say("[%s] attempt%d 未找到认证/授权页" % (shop, attempt))
                log_record(shop, "find_auth", "未找到认证/授权页", "fail")
                return "FAIL: 未找到认证/授权页"
            _say("[%s] attempt%d page=%s kind=%s url=%s" % (shop, attempt, pg.url[:60], kind, pg.url[:90]))

            # ★ 2026-10-10 加：**TEMU 风控拦截的识别与恢复**。
            #   现象：直接在 `settle/seller-login`（授权框页）上点「登录」，页面弹出
            #     「请访问agentSeller主页操作登录」
            #   且**不发任何 XHR**、URL 不变 —— 平台要求这条链必须从 **agentSeller 主页**
            #   （`agentseller-us.temu.com` 区域页）点「商家中心」走过来，不能直达。
            #   为什么会出现：窗口里可能躺着**上一次遗留的授权框死 tab**（`find_auth_page` 会优先
            #   命中它），或引导导航直接落在了 seller-login。此时在它上面点登录必然被拦。
            #   恢复：**回主页重走一遍**（主页 → 点区域「商家中心」→ 新开真正的授权框）。
            if kind == "seller-login":
                _rc = ""
                try:
                    _rc = str(pg.evaluate(
                        "() => (document.body.innerText||'').replace(/\\s+/g,' ')") or "")
                except Exception:                                   # noqa: BLE001
                    _rc = ""
                if "请访问agentSeller主页操作登录" in _rc or "请访问 agentSeller" in _rc:
                    _say("[%s] ⚠ 命中 TEMU 风控「请访问agentSeller主页操作登录」→ 回主页重走" % shop)
                    log_record(shop, "riskctl", "授权框页被风控拦截，回主页重走", "warn")
                    try:
                        _go_home_and_click_region(b, pg)            # 主页 → 点区域「商家中心」
                        pg.wait_for_timeout(3000)
                        b = cdp_transport.connect(port)
                        pg, kind = find_auth_page(b)
                    except Exception as _ge:                        # noqa: BLE001
                        _say("[%s] 回主页重走异常: %s" % (shop, str(_ge)[:80]))
                    if pg is None:
                        _say("[%s] 回主页后仍无授权页" % shop)
                        continue

            if kind == "authentication":
                # 区域选择页：点可用区域「商家中心」goto
                _say("[%s] 点 region goto(商家中心)..." % shop)
                if not _click_region_goto(pg, b):
                    _say("[%s] 点到 goto 失败" % shop)
                    log_record(shop, "goto", "点商家中心 goto 失败", "fail")
                    return "FAIL: 点商家中心 goto 失败"
                pg.wait_for_timeout(3000)
                # 跳转后可能出现 target 关闭或新 tab；重探（cdp_transport 每次新建连接）
                b = cdp_transport.connect(port)
                pg, kind = find_auth_page(b)
                if pg is None:
                    _say("[%s] goto 后无授权页，可能已直接跳订单页？待重探" % shop)
                else:
                    _say("[%s] goto 后 page=%s kind=%s url=%s" % (shop, pg.url[:60], kind, pg.url[:90]))

            if pg is None:
                continue
            # 等待紫鸟自动填账密完成（认证页/登录页勿抢点）——授权确认框出现前多等
            pg.wait_for_timeout(2500)

            # 先把紫鸟自动填入的账密同步进 React state（否则按钮处理器空转，见 _sync_input_state）
            synced = _sync_input_state(pg)
            if synced:
                _say("[%s] 已同步 %d 个输入框到前端状态" % (shop, synced))

            # 勾选授权条款 checkbox + 点授权/登录确认按钮（BHY=授权登录，BEL=确认授权并前往）
            _say("[%s] 勾选授权条款 checkbox..." % shop)
            if not _check_auth_checkbox(pg):
                _say("[%s] 未找到 checkbox（可能授权框在其它 tab 或已是授权页，继续点确认）" % shop)
            pg.wait_for_timeout(800)
            _say("[%s] 点授权/登录确认按钮..." % shop)
            if not _click_auth_confirm(pg):
                # ⚠ 2026-10-27 改：原来是「未找到按钮」也照样往下走，最后只报
                #   「授权未完成」，把真因（按钮存在但点不动）埋掉。现在如实上报，
                #   下一轮 attempt 会重来；两轮都这样才判 FAIL。
                _say("[%s] ⚠ 授权确认按钮点了没推进（三种点击方式都试过）" % shop)
                log_record(shop, "confirm_click", "确认按钮点不动/无推进", "warn")
            # 点完授权后页面会跳转/关闭 → 旧 page 的 wait/url 读取会抛（TLA-010 预期），就地吞掉。
            try:
                pg.wait_for_timeout(2000)
                _say("[%s] after-click url=%s" % (shop, pg.url[:100]))
            except Exception as e:
                _say("[%s] 点完授权后旧 page 已关闭（预期跳转）：%s" % (shop, str(e)[:80]))

            # ★ 验证进入商家中心可用页 —— **轮询等待**，不是查一次就下结论。
            #   ⚠ 2026-10-10 实测（无人值守真跑）：点完「授权登录」后跳转**可能要十几秒**，
            #   旧实现固定等 4s 只查一次 ⇒ 把**已经登录成功**误判成「授权未完成」→ rc=42。
            #   实测证据：那次报 FAIL 之后窗口 URL 已经是
            #   `agentseller-us.temu.com/mmsos/orders.html`（明明登录成功），重跑立刻显示
            #   「已在商家中心页（登录态有效），无需授权」。故改为**最长等 30s、每 3s 复查**。
            landed = _wait_seller_page(port, deadline_s=30.0, log=_say, shop=shop)
            if landed:
                _say("[%s] SUCCESS 进入商家中心页: %s" % (shop, landed[:80]))
                log_record(shop, "auth", "进入 TEMU 商家中心页: %s" % landed[:80])
                return "OK: 进入订单页"
            _say("[%s] 尚未进入商家中心页（可能需人工/验证码）" % shop)
            log_record(shop, "auth", "未进入 TEMU 商家中心页", "warn")
        # ★ 两次尝试都没「看到」成功 → 返回前**再做一次耐心复查**：
        #   跳转可能刚好在最后一次等待之后才落地；草率判 FAIL 会把成功说成失败（同上）。
        landed = _wait_seller_page(port, deadline_s=20.0, log=_say, shop=shop)
        if landed:
            _say("[%s] SUCCESS（收尾复查）进入商家中心页: %s" % (shop, landed[:80]))
            log_record(shop, "auth", "收尾复查进入 TEMU 商家中心页: %s" % landed[:80])
            return "OK: 进入订单页"
        return "FAIL: 授权未完成（可能需人工验证码/多次失败）"
    except Exception as e:
        # 跳转期异常（最大宗＝`Target page, context or browser has been closed`，TLA-010）
        # ⚠ 先按 D4 用**原始 CDP** 复核窗口真实落地页：授权成功却被报 ERR/exit 42，
        #   会把人（和上游自动化）误导成"登录失败"（2026-09-15 实测踩到）。
        landed = _entered_seller_page_via_cdp(port)
        if landed:
            _say("[%s] 跳转期异常但已进入商家中心页（D4 复核通过）: %s" % (shop, landed[:80]))
            log_record(shop, "auth", "跳转期异常后复核进入 TEMU 商家中心页: %s" % landed[:80])
            return "OK: 进入订单页（跳转期 TargetClosedError，已按 D4 复核）"
        log_record(shop, "err", str(e)[:200], "fail")
        return "ERR: %s" % str(e)[:200]
    finally:
        # 用完即关：只关**本次新开的、商家中心域的**标签页。
        # 用户的购物页（www.temu.com）、外部站、扩展页一律不碰；
        # 且窗口里至少留 1 个真实页（否则店铺窗口会整个消失）。
        try:
            closed = cdp_transport.close_new_seller_pages(
                port, before, keep_min_tabs=1,
                log=lambda m: _say("[%s] %s" % (shop, m)),
                before_urls=before_urls)
            if closed:
                log_record(shop, "cleanup", "关掉本次新开页 %d 个" % len(closed))
        except Exception:  # noqa: BLE001 - 收尾清理绝不能改变结果
            pass
        try:
            b.close()
        except Exception:
            pass


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    port = int(sys.argv[1])
    shop = sys.argv[2]
    print(run_auth(port, shop))
