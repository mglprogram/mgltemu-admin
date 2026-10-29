# -*- coding: utf-8 -*-
"""test_own_page_targeting.py — 钉死「桥调用必须带 target-id」这条规矩。

来历（用户 2026-10-29）：「上午执行的定时任务还存在覆盖页面的情况」。

前一轮修的是**误杀**（关窗/关页）。这一轮是另一个破坏面：**误覆盖**。
实测证据（2026-10-29 现场复现，非推断）：
  · `ziniao-cli page visit --store-id X --url U` **不带 `--target-id`** 时，Bridge 操作的是
    窗口的**当前页** —— 用户正开着的那一页。对一个 about:blank 用户页发 visit，
    该页 URL 从 `mmsos/orders.html` 变成目标 URL，其它页不动。
  · 带 `--target-id <自建页>` 时，只有自建页变，用户页一个都没动。
  ⇒ 定时任务（导出/限流/流量周更）全都在用不带 target 的桥调用，
    所以「跑个采集」就等于「把用户开着的页换掉」。

本文件守的是：**任何会导航/点击页面的路径，都必须先开自己的页并带 target-id。**
运行: python -m pytest scripts/power/tests/test_own_page_targeting.py -q
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import own_page  # noqa: E402


def _src(rel):
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- ① 单点实现
def test_visit_and_exec_always_carry_target_id():
    """`OwnPage` 的两个桥调用都必须带 `--target-id`（少了就等于操作用户的当前页）。"""
    src = _src("scripts/power/lib/own_page.py")
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run":
            calls.append(node)
    assert calls, "own_page 里应当有 _run 调用"
    # visit 走 subprocess 单独实现（stdout 为空，不能用 _run），单独校验
    assert '"page", "visit"' in src and '"--target-id", self.target_id' in src
    for c in calls:
        names = [getattr(a, "value", None) for a in c.args if isinstance(a, ast.Constant)]
        flat = " ".join(str(x) for x in names if x)
        if '"page", "exec"' in flat or "'page', 'exec'" in flat:
            assert '"--target-id"' in flat, "page exec 必须带 --target-id"


def test_own_page_opens_and_closes_its_own_tab(monkeypatch):
    """开自己的页 → 记 targetId → 退出时只关这一张。"""
    import ziniao_store as zs

    opened, closed = [], []
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 57964)
    monkeypatch.setattr(zs, "open_tab", lambda p, u: opened.append((p, u)) or "MYTID")
    monkeypatch.setattr(zs, "close_target", lambda p, t: closed.append((p, t)) or True)

    with own_page.OwnPage("27697436126374", "TM-BHY-YJF", "about:blank",
                          log=lambda m: None) as pg:
        assert pg.target_id == "MYTID"
    assert opened == [(57964, "about:blank")]
    assert closed == [(57964, "MYTID")], "只许关自己开的那张"


def test_own_page_without_port_fails_loudly(monkeypatch):
    """探不到端口就报错——绝不退化成「操作当前页」。"""
    import ziniao_store as zs
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 0)
    try:
        own_page.OwnPage("x", "TM-X", log=lambda m: None).__enter__()
    except RuntimeError as e:
        assert "CDP 端口" in str(e)
    else:
        raise AssertionError("没有端口时必须抛错，不能默默去动当前页")


# ---------------------------------------------------------------- ② 各域接线
def test_goods_export_opens_own_page_before_visiting():
    src = _src("scripts/goods/run_goods_export.py")
    assert "OwnPage(" in src, "导出必须先开自己的页"
    assert "zpage.use_target(pg.target_id)" in src, "必须把桥限定到自建页"
    assert src.index("OwnPage(") < src.index("page_visit(sid, URL")


def test_goods_zpage_supports_target_scoping():
    src = _src("scripts/goods/zpage.py")
    assert "def use_target" in src, "zpage 必须能限定 target"
    for fn in ("def page_visit", "def page_exec_str", "def page_exec", "def page_click"):
        seg = src.split(fn, 1)[1][:900]
        assert "--target-id" in seg, "%s 必须带 --target-id" % fn


def test_goods_zpage_visit_accepts_empty_stdout():
    """`page visit` 成功时 stdout 为空、只有 stderr 一行 —— 旧实现把它判成失败。"""
    src = _src("scripts/goods/zpage.py")
    seg = src.split("def page_visit", 1)[1][:1200]
    assert "stderr" in seg and "returncode" in seg, "不能用只认 stdout JSON 的 _run"


def test_watch_focus_uses_own_tab_for_visit_and_exec():
    src = _src("scripts/bel_goods/watch_focus.py")
    assert "def _ensure_own_page" in src and "_close_own_pages" in src
    assert '"--target-id", own_target(store_id)' in src, "visit 必须带 target"
    assert '"--target-id", tid' in src, "exec 必须带 target"
    assert "BEL_TARGET_ID" in src, "子进程抓取必须继承 targetId"


def test_fetch_traffic_accepts_target_from_env():
    src = _src("scripts/bel_goods/fetch_traffic.py")
    assert "BEL_TARGET_ID" in src
    assert '"--target-id", TARGET_ID' in src


def test_flux_auth_never_falls_back_to_first_tab():
    """旧兜底 `list_tabs(port)[0]` 就是"拿用户第一页来导航"——必须消失。"""
    src = _src("scripts/bel_goods/flux_auth.py")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "list_tabs(port) or [None]" not in code, "还有拿窗口第一页当工作面的兜底"
    assert "def new_tab" in src and "def close_tab" in src
    assert 'find_tab_by_id(port, own_tid)' in src


def test_flux_weekly_visit_carries_target():
    src = _src("scripts/bel_goods/run_flux_weekly.py")
    seg = src.split("def goto_flux", 1)[1][:1400]
    assert "--target-id" in seg, "周更导航必须带 target"
    assert "open_tab" in seg, "必须自己开页"


# ---------------------------------------------------------------- ③ 文档口径
def test_skill_documents_the_overwrite_fix():
    p = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                     "ziniao-tab-safety", "SKILL.md")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        s = f.read()
    assert "误覆盖" in s and "target-id" in s
