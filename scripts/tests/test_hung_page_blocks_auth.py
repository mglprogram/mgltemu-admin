#!/usr/bin/env python3
"""钉住「卡死页不得拖垮授权流程 / 不得被关掉」。

事故（2026-11-03，用户报「执行去订单列表 一直获取不到登录态 修复」）
------------------------------------------------------------------
BEL 窗口 6 个页里**稳定有 2 个卡死**（`product/goods-data`、`goods/list`）。
卡死 = 浏览器层能答、JS 线程不应答。在卡死页上**读一次 `.url` 就卡满 60 秒**：

    find_auth_page(b)                     实测 360.2 秒
    for pg in ctx.pages: pg.url           实测 180.0 秒
    locator.count()                       实测  60.0 秒/页

`run_temu_auth.py` 的兜底预算是 300 秒 ⇒ 授权流程连第一步都没点到就被打断，
日志只剩 `FAIL: 授权未完成`。今天 08:31 / 08:46 两次失败都是这个，
而探针本身（`probe_login_state`）一直是好的 —— 所以看起来像"登录态怎么都获取不到"。

修法的三条不变量（本文件钉住它们，防止回退）
------------------------------------------
① **枚举不许走逐页 `.url`**：`find_auth_page` / `_region_advanced` 必须先走原始
   CDP `/json/list`（纯 HTTP，0.1s），只给命中的候选页建 `CdpPage`。
② **`.url` 必须有短预算**：`CdpPage.url` 不能退回到裸 `evaluate`（会卡 60 秒）。
③ **卡死页只能导航修复，不许关**：卡死的往往是用户的默认页（商品列表 / 销量分析）。
   `heal_own_hung_pages` 对我们自己登记的页可以关（可再生），对别人的页只能
   `navigate_hung_page`。2026-10-29 那次「我的页没了」就是关出来的。

跑法：
    pytest scripts/tests/test_hung_page_blocks_auth.py -q
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
LIB = PROJ / "scripts" / "power" / "lib"
sys.path.insert(0, str(LIB))

DRIVER = LIB / "auth_temu_driver.py"
TRANSPORT = LIB / "cdp_transport.py"
GUARD = LIB / "auth_guard.py"

import auth_guard as GUARD_MOD  # noqa: E402


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _func_src(p: Path, name: str) -> str:
    """取某个顶层函数的源码（按 AST 行号切片，比字符串 find 稳）。"""
    src = _src(p)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(src.splitlines()[node.lineno - 1:node.end_lineno])
    raise AssertionError("找不到函数 %s in %s" % (name, p))


# ---------------------------------------------------------------- ① 枚举路径
def test_find_auth_page_uses_raw_cdp_not_page_objects():
    """`find_auth_page` 必须优先用 `/json/list`，不能靠逐页 `.url` 筛选。

    它是每轮授权 attempt 的第一步，也是实测 360 秒的那个。
    """
    body = _func_src(DRIVER, "find_auth_page")
    assert "_pages_via_cdp" in body, (
        "find_auth_page 退回成逐页读 .url 了 —— 卡死页上每页卡 60 秒，"
        "实测整函数 360 秒，300 秒兜底预算烧光 ⇒ 报「授权未完成」")
    # 主路径必须在「兜底」之前就 return，即不能先遍历 b.contexts
    main, _, fallback = body.partition("兜底")
    assert "b.contexts" not in main, "主枚举路径又回到 b.contexts（逐页 .url）了"


def test_region_advanced_uses_raw_cdp():
    """`_region_advanced` 每次点击后都会调，不能走逐页 `.url`。"""
    body = _func_src(DRIVER, "_region_advanced")
    assert "_pages_via_cdp" in body
    assert "for ctx in b.contexts" not in body, (
        "_region_advanced 又在遍历 b.contexts 了（每次点击后都调，卡死页会拖死每轮授权）")


# ---------------------------------------------------------------- ② .url 预算
def test_page_url_has_short_budget():
    """`CdpPage.url` 必须带短预算 + 回退到 `/json/list` 的 URL。"""
    src = _src(TRANSPORT)
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CdpPage")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "url")
    body = "\n".join(src.splitlines()[fn.lineno - 1:fn.end_lineno])
    assert "_URL_PROBE_TIMEOUT" in body, (
        "CdpPage.url 又变回裸 evaluate —— 卡死页上会卡满 60 秒")
    assert "self._url" in body, "没有了回退：超时后应返回 /json/list 里的 url"


def test_url_probe_timeout_is_small():
    src = _src(TRANSPORT)
    tree = ast.parse(src)
    val = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_URL_PROBE_TIMEOUT":
                    val = ast.literal_eval(node.value)
    assert val is not None, "缺少 _URL_PROBE_TIMEOUT"
    assert 0 < float(val) <= 5.0, "读 url 的预算太大（%s 秒），枚举仍会被卡死页拖死" % val


def test_locator_count_does_not_block_60s():
    """`_Locator._count` 必须走短预算路径（实测卡死页 60.0s/页）。"""
    src = _src(TRANSPORT)
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Locator")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_count")
    body = "\n".join(src.splitlines()[fn.lineno - 1:fn.end_lineno])
    assert "_eval_with_fallback" in body, (
        "_Locator._count 又变回裸 evaluate（卡死页 60 秒/页）")


# ---------------------------------------------------------------- ③ 修复方式
def test_navigate_hung_page_uses_page_level_ws():
    """`Page.navigate` 只能发到**页面级** ws。

    发到浏览器级 ws 会回 `'Page.navigate' wasn't found`，被 except 吞掉 ⇒
    函数恒 False ⇒「自愈」其实一直是空转（2026-11-03 实测对照：改成页面级后 2 秒 alive）。
    """
    body = _func_src(TRANSPORT, "navigate_hung_page")
    assert "webSocketDebuggerUrl" in body, "navigate_hung_page 没拿到页面级 ws"
    assert 'http_version(port)["webSocketDebuggerUrl"]' not in body, (
        "又发到浏览器级 ws 了 —— Page.* 域只存在于页面会话，这个调用恒失败")


def test_heal_never_closes_foreign_pages():
    """`heal_own_hung_pages` 对**别人的**卡死页只能导航修复，绝不能关。"""
    body = _func_src(GUARD, "heal_own_hung_pages")
    assert "navigate_hung_page" in body, "没有导航修复路径，用户卡死页将永远修不掉"
    # 关页只能出现在 tid in mine 的分支里：mine 分支内 recover_hung_page 必须存在，
    # 且在「别人的页」那支（`continue` 之后）不能再出现
    lines = body.splitlines()
    idx_mine = next((i for i, l in enumerate(lines) if "if tid in mine" in l), None)
    assert idx_mine is not None, "归属分支没了 —— 会变成无差别关页（2026-10-29 事故）"
    tail = "\n".join(lines[idx_mine:])
    own_branch, _, foreign_branch = tail.partition("if not repair_foreign")
    assert "recover_hung_page" in own_branch, "自有页应关闭"
    assert "recover_hung_page" not in foreign_branch, (
        "recover_hung_page（关页）出现在别人的页那一支 —— 会关掉用户的默认页")
    assert "navigate_hung_page" in foreign_branch, "别人的页必须走导航修复"


def test_heal_does_not_early_return_when_no_own_pages():
    """没有登记页时**也要**继续修别人的卡死页（旧实现这里直接 return）。"""
    body = _func_src(GUARD, "heal_own_hung_pages")
    assert "if not mine:\n        return port" not in body, (
        "又回到「没有自有的页就直接返回」—— 而卡死的恰恰是用户的默认页")


def test_auth_entry_heals_hung_before_login_probe():
    """`run_auth` 必须在探针/枚举之前先救活卡死页。"""
    body = _func_src(DRIVER, "run_auth")
    i_heal = body.find("_heal_hung_before_auth")
    i_probe = body.find("probe_login_state")
    assert i_heal != -1, "run_auth 里没有卡死页自愈"
    assert i_probe != -1
    assert i_heal < i_probe, "自愈必须在登录态探针之前（否则探针照样超时）"


def test_guard_store_heals_before_probe():
    """`guard_store` 也要先自愈再探针（探针建页遇卡死窗口会 HTTP 500 ⇒ UNKNOWN）。"""
    body = _func_src(GUARD, "guard_store")
    # 按**调用**定位，别匹配注释里提到的名字
    i_heal = body.find("heal_own_hung_pages(port")
    i_probe = body.find("probe_login_state(port")
    assert i_heal != -1, "guard_store 没接卡死页自愈"
    assert i_probe != -1, "guard_store 没调探针？"
    assert i_heal < i_probe, "自愈必须在探针之前"


# ---------------------------------------------------------------- ④ 内存压力
class TestMemoryPressure:
    """**内存紧张时不许导航修复**（2026-11-03 实测，这条比前面的都重要）。

    时序（同一台机器，中间唯一动作就是 heal）：

        10:36  BEL 1 页卡死（goods/list），mall + goods-data 都还 alive
        10:39  heal「导航修复」goods-data + goods-list，当场报告已修复
        10:42  BEL **3 页**卡死（mall + goods-data + goods-list）

    病因不是页面 bug，而是**物理内存耗尽**：

        物理内存 16,069 MB，可用 **133 MB（99% 占用）**
        提交量 44,122 / 48,381 MB
        ziniaobrowser 提交 8,054 MB、chrome 4,683 MB、WorkBuddy 2,836 MB

    反直觉的一处正是证据：卡死页所在窗口的内存**更低**
    （BEL 375MB / 2 页卡死 vs BHY 999MB / 0 页卡死）—— 内存紧张时页面被换页出去，
    常驻内存变小，但唤醒要重新换入 ⇒ 响应超时 ⇒ 看起来就是"卡死"。

    ⇒ **导航修复＝重新加载＝抢内存**，紧张时是加害。「修好 2 个、弄死 3 个」。
    """

    def test_has_a_memory_probe(self):
        assert hasattr(GUARD_MOD, "avail_phys_mb"), "缺少 avail_phys_mb()"
        v = GUARD_MOD.avail_phys_mb()
        assert isinstance(v, int)
        assert v == -1 or v >= 0, "返回值只能是可用 MB 或 -1（读不到）"

    def test_threshold_is_documented_and_reasonable(self):
        assert hasattr(GUARD_MOD, "MEM_TIGHT_MB")
        v = int(GUARD_MOD.MEM_TIGHT_MB)
        assert 200 <= v <= 4000, (
            "阈值 %s MB 不合理：太小会在真紧张时还在导航，太大则白白放弃修复" % v)

    def test_heal_skips_navigation_when_memory_is_tight(self):
        body = _func_src(GUARD, "heal_own_hung_pages")
        assert "avail_phys_mb()" in body, "heal 没查内存就导航了"
        assert "MEM_TIGHT_MB" in body, "heal 没按阈值分流"
        # 紧张分支必须 continue，不能落到 navigate_hung_page
        tight_at = body.find("if tight:")
        assert tight_at != -1, "没有按内存紧张的专属分支"
        branch = body[tight_at:]
        first_cont = branch.split("continue")[0]
        assert "navigate_hung_page" not in first_cont, (
            "内存紧张分支里还在导航 —— 会把别的页挤死（实测 1 页变 3 页）")

    def test_auth_entry_skips_navigation_when_memory_is_tight(self):
        """授权入口（`_heal_hung_before_auth`）同样要跳过。"""
        body = _func_src(DRIVER, "_heal_hung_before_auth")
        assert "MEM_TIGHT_MB" in body, "授权入口没按内存分流"
        head = body.split("navigate_hung_page")[0]
        assert "MEM_TIGHT_MB" in head, (
            "内存检查必须在导航之前（顺序反了等于没检查）")
        assert "return 0" in head, "内存紧张应直接返回 0，不做任何导航"

    def test_own_pages_are_still_closed_when_tight(self):
        """内存紧张时**自有页更要关**（关掉＝释放内存）。"""
        body = _func_src(GUARD, "heal_own_hung_pages")
        own = body.split("if tid in mine")[1].split("continue")[0]
        assert "recover_hung_page" in own, "自有页应当照常关闭（顺便释放内存）"
        assert "tight" not in own, "自有页不该受内存阈值影响 —— 关掉正是要释放内存"


class TestProbeHasNoFalsePositive:
    """`probe_page_alive` 必须复核，防「忙页面」被误判成「死」（2026-11-03 实测）。

    同一张 `agentseller-us.temu.com/mmsos/orders.html`：

        预算 4s  → hung  (4.1s, 超时)
        预算 8s  → alive (5.3s, 答上了)

    也就是 **5.3 秒才答上的忙页面被 4 秒预算判成了"死"**，差一步就去导航用户的页。
    10:16 那次「两个店铺各 1 个卡死」里，至少有一个是这种假阳性。
    """

    def test_probe_confirms_before_reporting_hung(self):
        body = _func_src(TRANSPORT, "probe_page_alive")
        assert "_CONFIRM_HUNG_TIMEOUT" in body, (
            "probe_page_alive 没有放大预算复核 —— 忙页面会被误判成卡死")
        # 复核必须发生在返回 hung 之前
        assert body.find("_CONFIRM_HUNG_TIMEOUT") < body.rfind('return "hung"'), (
            "复核逻辑必须在最终 return hung 之前")

    def test_confirm_can_be_disabled_for_cheap_checks(self):
        """只看预算的地方（`_eval_with_fallback`）用 confirm=False，别白花 30 秒。"""
        body = _func_src(TRANSPORT, "_eval_with_fallback")
        assert "confirm=False" in body, (
            "_eval_with_fallback 用了带复核的探活 —— 每次 locator 取值会多花最多 30s")

    def test_true_hang_is_still_detected(self):
        """真卡死页三次都不答 ⇒ 仍要判 hung（不能因为加了复核就永远判 alive）。"""
        body = _func_src(TRANSPORT, "probe_page_alive")
        tail = body[body.find("if not confirm:"):]
        assert tail.count('return "hung"') >= 1
        assert "_js_answers(_CONFIRM_HUNG_TIMEOUT)" in body
    

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
