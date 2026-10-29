# -*- coding: utf-8 -*-
"""test_tab_openers.py — 开页侧契约：**即用即关** + 不抢前台 + 不复用用户的页。

用户口径（2026-10-29）：「把所有涉及打开新标签页的任务，可能会覆盖原页面的，自检并修复」。

开页有三个翻车面（都实测过）：

  ① **泄漏**：开了页不关。`run_stock_topup` 跑一次加库存就永久多一个标签页；
     `set_page_size` / `verify_row_lookup` / `verify_react_search` / `open_goods_list`
     也是开完不收。用户要自己手删。
  ② **抢前台**：`foreground=True` 把紫鸟窗口提到最前，打断用户正在做的事
     （2026-09-14 用户报「窗口会置顶」）。但后台页被 Chrome 节流、`Runtime.evaluate`
     会挂住 —— 所以这是**技术必需**，可以前台开，但必须配 finally 关页。
  ③ **复用用户的页**：`--attach` / "按 URL 挑第一个命中页" 会把用户的商品列表页当工作区，
     然后在上面点「修改库存」、填数量、点确认。

本文件钉住这三条，并校验扫描器本身能抓住它们。
运行: python -m pytest scripts/power/tests/test_tab_openers.py -q
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))


def _src(rel):
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


def _func(rel, name):
    tree = ast.parse(_src(rel))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("%s 里没有 %s" % (rel, name))


# ---------------------------------------------------------------- ① 泄漏
def test_stock_topup_closes_the_tab_it_opens():
    """加库存会点确认（写操作），开出来的页必须关——否则每跑一次留一页。"""
    src = _src("scripts/goods/run_stock_topup.py")
    body = src.split("async def run(", 1)[1].split("async def _run_on_target", 1)[0]
    assert "new_tab(" in body
    assert "finally" in body and "close_tab" in body, "run() 必须用 finally 关掉自己开的页"


def test_stock_topup_no_longer_attaches_to_user_tab():
    """`--attach` 原意是复用，但它把用户的页当工作区做写操作 ⇒ 必须停用。"""
    src = _src("scripts/goods/run_stock_topup.py")
    body = src.split("async def run(", 1)[1].split("async def _run_on_target", 1)[0]
    assert "cands[0]" not in body, "还在把窗口里第一个 goods/list 页当工作区"
    assert "已停用" in body, "要显式告知 --attach 不再复用用户的页"


def test_set_page_size_closes_its_tab():
    body = _src("scripts/goods/set_page_size.py").split("async def main", 1)[1]
    assert "finally" in body and "close_tab" in body


def test_verify_row_lookup_closes_its_tab():
    body = _src("scripts/goods/verify_row_lookup.py").split("async def main", 1)[1]
    assert "finally" in body and "close_tab" in body


def test_verify_react_search_closes_its_tab():
    body = _src("scripts/goods/verify_react_search.py").split("async def main", 1)[1]
    assert "finally" in body and "close_tab" in body


def test_board_page_close_really_closes_the_tab():
    """旧 `close()` 只 detach 连接 + 杀子进程，标签页留在窗口里。"""
    src = _src("scripts/goods/board_page.py")
    body = src.split("def close", 1)[1].split("def __exit__", 1)[0]
    assert "close_tab" in body, "close() 必须真的关页，不能只断连接"


def test_precheck_closes_per_style_tabs():
    """precheck 每款开一个页，必须每款关（它早就对了，别退化）。"""
    body = _src("scripts/goods/precheck.py").split("async def precheck_store", 1)[1]
    assert "finally" in body and "close_tab" in body


# ---------------------------------------------------------------- ② 前台是必需，但要说明
def test_foreground_opens_are_documented():
    """凡 foreground=True 都必须写清"为什么必需"或挂 noqa，避免后人以为可以随便改。"""
    for rel in ("scripts/goods/run_stock_topup.py", "scripts/goods/precheck.py",
                "scripts/goods/set_page_size.py", "scripts/goods/verify_row_lookup.py",
                "scripts/goods/verify_react_search.py", "scripts/goods/probe_open.py"):
        src = _src(rel)
        for line in src.splitlines():
            if "foreground=True" in line and "def " not in line:
                assert ("noqa: tab-safety" in line or "技术必需" in line
                        or "节流" in line), "%s 的 foreground=True 没说明理由：%s" % (rel, line.strip())


# ---------------------------------------------------------------- ③ 工具脚本的例外
def test_open_goods_list_is_marked_as_intentional():
    """这个脚本的产物就是"开着的页"，属于 即用即关 的明确例外，必须写清。"""
    src = _src("scripts/goods/open_goods_list.py")
    assert "noqa: tab-safety" in src and "故意留着" in src


# ---------------------------------------------------------------- ④ 扫描器自身
def test_opener_auditor_flags_a_leak(tmp_path):
    """把扫描器指向一个"开了不关"的假文件，必须报出来。"""
    p = tmp_path / "leaky.py"
    p.write_text("async def go(port):\n"
                 "    tid = await new_tab(port, 'x')\n"
                 "    return do_work(tid)\n", encoding="utf-8")
    sys.path.insert(0, os.path.expanduser("~/.agents/skills/ziniao-tab-safety/scripts"))
    import audit_tab_openers as aud  # noqa: PLC0415
    found = aud.scan(p)
    assert any(f["kind"] == "open-without-close" for f in found), \
        "扫描器没抓住泄漏：%s" % found


def test_opener_auditor_accepts_factory_handoff(tmp_path):
    """把 tid 返回给调用方（工厂约定）不算泄漏。"""
    p = tmp_path / "factory.py"
    p.write_text("def prepare(port):\n"
                 "    tid = create_target(port, 'x')\n"
                 "    return tid, 'ok'\n", encoding="utf-8")
    sys.path.insert(0, os.path.expanduser("~/.agents/skills/ziniao-tab-safety/scripts"))
    import audit_tab_openers as aud  # noqa: PLC0415
    assert [f for f in aud.scan(p) if f["kind"] == "open-without-close"] == []


def test_repo_is_clean_for_openers():
    """全仓必须干净（这是本轮自检的结论，回退会红）。"""
    sys.path.insert(0, os.path.expanduser("~/.agents/skills/ziniao-tab-safety/scripts"))
    import audit_tab_openers as aud  # noqa: PLC0415
    from pathlib import Path
    bad = []
    for q in aud._iter_files(Path(_REPO) / "scripts"):
        if q.name in aud.SKIP_FILES:
            continue
        bad += aud.scan(q)
    assert bad == [], "开页风险未清零：%s" % [(b["file"], b["line"], b["kind"]) for b in bad]
