# -*- coding: utf-8 -*-
"""test_temu_buylabel_gates.py — TEMU 购面单链路的**爬虫合规静态闸门**（离线，不起进程）。

来历（2026-09-14 用户报障 → 经验飞轮 T1）：
    用户反馈「跑链路时会弹终端、紫鸟窗口被置顶」。根因：驱动里用了 Playwright 的
    `bring_to_front()` 与 `ctx.new_page()`（**前台**开页）→ 窗口被提到最前；入口曾带 UTF-8 BOM。
    规则不能只写在正文里靠自觉，故固化成闸门（经验飞轮：可程序判定 → T1）。

三条硬规则：
  1. **禁抢前台**：链路驱动里不得出现 `*.bring_to_front()` / `*.new_page()` 调用
     （AST 判定，故文档字符串里提到这些名字不算违规）。后台开页走 `new_tab_background()`，
     坐标点击走 `cdp_click()`（原生 CDP 派发，不依赖焦点）。
  2. **禁裸 spawn**：链路文件里的 `subprocess.run/Popen/call/check_output/check_call`
     必须带 `creationflags`（本项目统一走共享库 `lib/quiet`），否则 Windows 会弹黑框。
  3. **禁 BOM**：链路文件不得带 UTF-8 BOM（PS 文本命令改写会引入；BOM 会让 ast/json 解析踩坑）。

范围说明（EF-011/EF-016：恒红闸门＝失效）：本闸门覆盖**已整改的爬虫链路文件**（含 2026-09-22 起的
「合作对接仓异常单」链路）。`scripts/power` 全域的裸 spawn 由 `tests/test_silent_spawns.py`
按**技术债基线**（`data/console_debt.json`，只许减不许增）盯住——那份才是「新脚本不许弹黑框」的落点。
"""
import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)                       # scripts/power
_DRIVER = os.path.join(_POWER, "lib", "temu_buylabel_driver.py")
_ENTRY = os.path.join(_POWER, "run_temu_buylabel.py")
_CFG = os.path.join(_POWER, "data", "temu_buylabel.json")
#: 合作仓异常单链路（2026-09-22 加：同一套合规口径——不抢前台、不弹黑框、无 BOM）
_ABNORMAL_ENTRY = os.path.join(_POWER, "run_temu_abnormal.py")
_ABNORMAL_DRIVER = os.path.join(_POWER, "lib", "temu_abnormal_driver.py")

CHAIN = [_ENTRY, _DRIVER, _ABNORMAL_ENTRY, _ABNORMAL_DRIVER]
SPAWN_ATTRS = {"run", "Popen", "call", "check_output", "check_call"}
FOCUS_ATTRS = {"bring_to_front", "new_page"}          # 抢前台的两类调用


def _tree(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _focus_calls(path):
    """返回抢前台的调用 (行号, 名字)——AST 判定，注释/文档字符串不算。"""
    bad = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in FOCUS_ATTRS:
            bad.append((node.lineno, node.func.attr))
    return bad


def _bare_spawns(path):
    bad = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in SPAWN_ATTRS):
            continue
        base = fn.value
        if not (isinstance(base, ast.Name) and base.id == "subprocess"):
            continue
        if "creationflags" not in {k.arg for k in node.keywords if k.arg}:
            bad.append((node.lineno, "subprocess." + fn.attr))
    return bad


def test_no_focus_stealing():
    """链路里不许 bring_to_front / ctx.new_page（会把紫鸟窗口提到最前）。"""
    problems = []
    for p in CHAIN:
        for lineno, name in _focus_calls(p):
            problems.append("%s:%d 调了 %s（应改 new_tab_background / cdp_click，或复用现存页）"
                            % (os.path.basename(p), lineno, name))
    assert not problems, "存在抢前台调用：\n  " + "\n  ".join(problems)


def test_gate_detects_focus_stealing(tmp_path):
    """闸门自检：造一个含 bring_to_front 的文件必须被检出（防闸门恒绿）。"""
    f = tmp_path / "s.py"
    f.write_text("def a(p):\n    p.bring_to_front()\n", encoding="utf-8")
    assert _focus_calls(str(f)), "闸门对 bring_to_front 无感＝闸门失效"
    f2 = tmp_path / "s2.py"
    f2.write_text("def a(p):\n    p.wait_for_load_state()\n", encoding="utf-8")
    assert not _focus_calls(str(f2)), "正常调用被误判"


def test_chain_spawns_are_silent():
    """链路文件不许裸 spawn（Windows 会弹黑框；统一走 lib/quiet）。"""
    problems = []
    for p in CHAIN:
        for lineno, call in _bare_spawns(p):
            problems.append("%s:%d 裸 %s（应改用 lib/quiet）" % (os.path.basename(p), lineno, call))
    assert not problems, "存在会弹黑框的子进程调用：\n  " + "\n  ".join(problems)


def test_chain_files_have_no_bom():
    """链路文件不得带 UTF-8 BOM（2026-09-14 实测入口被 PS 改写引入过 BOM）。"""
    bad = [p for p in CHAIN + [_CFG]
           if open(p, "rb").read(3) == b"\xef\xbb\xbf"]
    assert not bad, "以下文件带 BOM：%s" % bad


def test_config_declares_view_and_guard():
    """口径必须显式声明前台/静默与占用守卫（缺了就等于没这条规则）。"""
    import json
    cfg = json.load(open(_CFG, encoding="utf-8"))
    assert cfg.get("view", {}).get("background_tabs") is True
    assert cfg.get("view", {}).get("block_images") is True
    assert isinstance(cfg.get("view", {}).get("min_action_gap_ms"), int)
    assert cfg.get("guard", {}).get("attached_check") is True


# ---------------------------------------------------------------- 2026-09-21 新增两条（错误→闸门）
def test_cdppage_accepts_raw_id_key():
    """`list_pages()` 直出 `/json/list`，键是 `id`；`CdpPage` 必须认它。

    2026-09-21 实测：只读 `targetId` ⇒ 经 `ctx.pages` 拿到的 `targetId` 恒 None，
    连带 `is_closed()` 恒 False、页 `close()` 静默失效（`Browser.getWindowForTarget`
    因此报 `No web contents in the target`）。这条把键名契约钉住。
    """
    import sys
    sys.path.insert(0, os.path.join(_POWER, "lib"))
    import cdp_transport
    p = cdp_transport.CdpPage(1, {"id": "RAW-ID-1", "url": "https://x", "webSocketDebuggerUrl": None})
    assert p.targetId == "RAW-ID-1", "CdpPage 没认 /json/list 的 `id` 键"
    p2 = cdp_transport.CdpPage(1, {"targetId": "TID-2", "url": "https://x"})
    assert p2.targetId == "TID-2", "显式 targetId 优先"


def test_open_batch_waits_for_selection_render():
    """「全选 → 点批量在线下单」之间必须**等勾选生效**，不许用固定 sleep 猜。

    2026-09-21 实测：全选后 1.5s 就点按钮 ⇒ 不开页 ⇒ 回落自拼 URL 页 ⇒ 白跑一轮。
    闸门判据＝`_open_batch_by_click` 里出现 `_wait_orders_selected(`（步骤），且
    `_JS_CHECK_ORDERS_ALL` 与其后的按钮点击之间不再夹固定 `time.sleep`。
    """
    src = open(_DRIVER, encoding="utf-8").read()
    i = src.find("def _open_batch_by_click")
    assert i >= 0, "找不到 _open_batch_by_click"
    body = src[i:i + 1200]
    assert "_wait_orders_selected(" in body, "开批量页前没有等待勾选生效的步骤"
    seg = body[body.find("_JS_CHECK_ORDERS_ALL"):body.find("批量在线下单")]
    assert "time.sleep(" not in seg, "全选与点击之间仍夹着固定 sleep（应改等勾选生效）"

