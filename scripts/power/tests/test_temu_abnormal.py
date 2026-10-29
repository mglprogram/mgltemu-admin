# -*- coding: utf-8 -*-
"""temu_abnormal 域测试：纯函数判据 + 口径/步骤结构闸门（不碰浏览器）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POWER = os.path.dirname(HERE)
for p in (os.path.join(POWER, "lib"), POWER):
    if p not in sys.path:
        sys.path.insert(0, p)

import temu_abnormal_driver as tad  # noqa: E402

DATA = os.path.join(POWER, "data")


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 纯函数
def test_normalize_orders_dedup_keep_order():
    assert tad.normalize_orders([" a ", "", "a", "b", None, "b", "c"]) == ["a", "b", "c"]
    assert tad.normalize_orders(None) == []


def test_pick_page_size_takes_max_not_over():
    assert tad.pick_page_size([10, 20, 30, 50, 100], 30) == 30
    assert tad.pick_page_size([10, 20, 30, 50, 100], 40) == 30
    assert tad.pick_page_size([10, 20], 5) == 10          # 都超了 → 取最小
    assert tad.pick_page_size([], 30) == 0
    assert tad.pick_page_size(["10", "x", "30"], 30) == 30


def test_diff_remaining_ok_and_left():
    rows = [{"po": "PO-2"}, {"po": "PO-9"}]
    r = tad.diff_remaining(["PO-1", "PO-2"], rows)
    assert r["gone"] == ["PO-1"] and r["remaining"] == ["PO-2"] and r["ok"] is False
    r2 = tad.diff_remaining(["PO-1"], rows)
    assert r2["ok"] is True and r2["remaining"] == []
    assert tad.diff_remaining([], rows)["ok"] is False     # 空清单不算达标


def test_toast_ok_is_content_assertion():
    assert tad.toast_ok(["操作成功", "其它"], "成功") is True
    assert tad.toast_ok(["失败：请重试"], "成功") is False
    assert tad.toast_ok([], "成功") is False
    assert tad.toast_ok(["成功"], "") is False


# ---------------------------------------------------------------- 口径 / 步骤结构
def test_config_required_keys():
    cfg = _load("temu_abnormal.json")
    for k in ("url", "url_key", "tab_abnormal", "page_size", "page_size_options",
              "labels", "popover", "waits", "toast_contains"):
        assert k in cfg, "口径缺键 %s" % k
    assert cfg["url_key"] in cfg["url"]
    assert cfg["page_size"] in cfg["page_size_options"] or cfg["page_size"] > 0
    for k in ("repush", "to_seller", "confirm", "cancel"):
        assert cfg["labels"].get(k), "labels 缺 %s" % k
    assert cfg["popover"].get("layer1_any") and cfg["popover"].get("layer2_any")
    # 第一层有两种标题（正常单 / 下过单失败的单），必须都覆盖
    assert "请选择重发方式" in cfg["popover"]["layer1_any"]
    assert any("下单失败" in x for x in cfg["popover"]["layer1_any"])


def test_pipeline_steps_are_known_actions():
    spec = _load("temu_abnormal_pipeline.json")
    steps = spec.get("steps")
    assert isinstance(steps, list) and steps
    names = []
    for st in steps:
        assert isinstance(st, dict) and st.get("name"), "step 缺 name"
        assert isinstance(st.get("params", {}), dict)
        assert st["name"] in tad.ACTIONS, "step %s 不在 driver.ACTIONS" % st["name"]
        names.append(st["name"])
    # 顺序即契约：先过授权 → 只读盘点 → 写 → 复核
    assert names[0] == "auth", "整链第一段必须是 auth（窗口重开必掉合作仓侧授权态）"
    assert names.index("list") < names.index("convert") < names.index("verify")


def test_auth_segment_config_and_actions():
    """整链第零/一段：开窗 + 过授权（用户 2026-09-22：「从打开紫鸟浏览器到授权后面一整条路径」）。"""
    cfg = _load("temu_abnormal.json")
    assert "auth" in tad.ACTIONS
    a = cfg.get("auth") or {}
    for k in ("region_goto_text", "confirm_texts", "max_rounds"):
        assert a.get(k), "auth 口径缺 %s" % k
    assert "确认授权并前往" in a["confirm_texts"]          # BEL 的按钮文案
    assert "授权登录" in a["confirm_texts"]                # BHY 等的按钮文案
    assert int((cfg.get("store") or {}).get("open_wait_sec") or 0) > 0
    src = open(os.path.join(POWER, "run_temu_abnormal.py"), encoding="utf-8").read()
    assert "open_store" in src and "probe_port_by_window" in src   # 窗口没开就开、端口现取


def test_write_actions_are_not_morally_gated_in_entry():
    """写操作**不由脚本做道德判断**（用户 2026-09-14／2026-09-22 口径）。

    口径：脚本对"能不能改平台状态"不作判断（--confirm 参数保留但不拦，与 run_temu_buylabel 一致）；
    是否放行由 A 机侧开关决定（hub.json → acl）。
    """
    src = open(os.path.join(POWER, "run_temu_abnormal.py"), encoding="utf-8").read()
    assert '"--confirm"' in src or "--confirm" in src, "参数要保留（调用方照旧可以传）"
    assert "必须显式 --confirm" not in src, "又加回了「必须 --confirm」的硬拦"
    assert "WRITE_ACTIONS" in src
    assert "preflight" in src                     # 起点闸门引用（PL-003）
    assert "probe_port_by_window" in src          # 端口现取，不写死


def test_success_judge_is_row_gone_not_toast():
    """成功判据＝该 PO 从异常列表消失（内容断言）；toast 文本只作日志留痕。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "_wait_row_gone" in src and "_row_present" in src
    cfg = _load("temu_abnormal.json")
    assert "row_gone_timeout" in cfg["waits"]
    assert "从异常列表消失" in cfg.get("_success_judge", "")


def test_convert_is_idempotent_for_already_converted():
    """列表里已转走的单要跳过（重跑不报错、不重复点）。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "已在异常列表外，跳过" in src and "skipped" in src


def test_convert_continues_after_single_failure():
    """**单条失败不得中断整批**（用户 2026-09-22：「一遇到问题就停止，然后也不回消息」）。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "记账后继续" in src
    assert "逐单处理中断于" not in src, "又回到 fail-fast（中途抛错 → 整批没有结论）"
    assert "_convert_one" in src, "逐单逻辑要能单独重试"


def test_entry_self_reports_verdict():
    """本域必须自报达标判据，别让作业层拿下单域的「三号齐」硬套出假结论。"""
    src = open(os.path.join(POWER, "run_temu_abnormal.py"), encoding="utf-8").read()
    assert "[VERDICT]" in src and "_domain_verdict" in src
    # 判据必须来自本域口径（异常清单是否清空），不是 TK/1Z/PK
    assert "原异常清单是否清空" in src


def test_screenshot_is_not_on_the_happy_path():
    """**截图不许放在主链路上**（2026-09-22 实测：这张页一次截图稳定 45s，5 处 ⇒ 白花 225s）。"""
    cfg = _load("temu_abnormal.json")
    ev = cfg.get("evidence") or {}
    assert ev.get("screenshot") == "fail_only", "截图默认必须是 fail_only（只在失败路径拍）"
    assert float(ev.get("screenshot_timeout_sec") or 0) > 0, "失败路径拍照也要有硬预算"
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "def shot(self, page, name: str, force: bool = False)" in src
    assert "force=True" in src, "失败路径要显式 force 才拍"
    # 正常路径的几处调用不许带 force
    for happy in ("01_abnormal_page", "02_list", "03_converted", "04_verify"):
        i = src.find('"%s"' % happy)
        assert i > 0
        seg = src[max(0, i - 60):i + 60]
        assert "force=True" not in seg, "%s 是正常路径，不该强制截图" % happy


def test_auth_checks_target_page_before_running_login():
    """已在目标页就**跳过登录子进程**（原来无条件先跑 ~60s 登录，跑完才发现"无需授权"）。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    i = src.find("跳过登录子进程")
    assert i > 0, "没有「先判目标页」的短路"
    seg = src[max(0, i - 900):i]
    assert '== "target"' in seg, "短路判据必须是「落在目标页」"


def test_popup_sweep_has_anti_thrash():
    """同一弹窗处置过一次还在 → 停手（刷新刷不掉的常驻面板不该连刷）。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "判定为非遮挡（不再重复处置）" in src and "tried" in src
    reg = _load("temu_abnormal_popups.json")
    hit = tad.match_popup("全部消息 99+ 异常订单消息通知 标记为已读", "全部消息", ["标记为已读"], reg)
    assert hit and hit.get("act") == "keep", "消息中心面板没登记成 keep（会连刷整页）"


def test_js_selectors_pin_the_temu_classes():
    """平台选择器契约：两种浮层文案 + 行内重发按钮文案写死在 JS 常量区。"""
    assert "popover" in tad._JS_CLICK_IN_POPOVER
    assert "重发" in tad._JS_CLICK_REPUSH
    assert "popoverTitle" in tad._JS_CLICK_IN_POPOVER   # 排除标题/内容容器，避免点错节点
    assert "getBoundingClientRect" in tad._JS_HAS_POPOVER  # portal 浮层不能用 offsetParent 判可见


# ---------------------------------------------------------------- 2026-09-24 假空事故（0 行≠清单为空）
_URL = "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order"


class _Page:
    def __init__(self, url=_URL):
        self.url = url


class _FakeDriver(tad.TemuAbnormalDriver):
    """最小假 driver：**继承真类**（拿 `_empty_guard` / `_blocking_layers` 的真实实现），只桩掉 IO。"""

    def __init__(self, row_reads, layers=None, swept=None, url=_URL, url_key="abnormal-order"):
        self.cfg = {"url_key": url_key}       # 不走真 __init__（不连浏览器）
        self._row_reads = list(row_reads)
        self._layers = layers or []
        self._swept = swept or {"swept": 0, "handled": []}
        self.shots = []
        self.applied = 0
        self.swept_stages = []
        self.page = _Page(url)

    def log(self, *a, **k):
        pass

    def shot(self, page, name, force=False):
        self.shots.append(name)
        return "/tmp/%s.png" % name

    def _js(self, page, script, arg=None, tries=3):
        if script is tad._JS_LAYERS:
            return self._layers
        return self._row_reads.pop(0) if self._row_reads else []

    def sweep_popups(self, stage=""):
        self.swept_stages.append(stage)
        return self._swept

    def _apply_list_view(self, page):
        self.applied += 1
        return 0


def _mk(row_reads, blockers=None, swept=None, url=_URL, layers=None, url_key="abnormal-order"):
    d = _FakeDriver(row_reads, layers=layers, swept=swept, url=url, url_key=url_key)
    d._blocking_layers = lambda page: list(blockers or [])
    return d


def test_empty_guard_rereads_instead_of_reporting_empty():
    """首读 0 行 → 重设列表视图后重读；拿到行就返回（**不判空**）。"""
    d = _mk([[{"po": "PO-1"}], [], [{"po": "PO-1"}]])
    rows = d._empty_guard(d.page, d._swept, "list")
    assert [r["po"] for r in rows] == ["PO-1"]
    assert d.applied == 1, "必须重设列表视图（切「异常」页签 + 页大小）后重读"


def test_empty_guard_refuses_to_report_empty_when_blocked():
    """两次都 0 行、但页面上还有**非 keep** 浮层 → 报「读不清」，绝不回「清单为空」。"""
    d = _mk([[], []], blockers=[{"text": "请先开通智能轨迹助手"}])
    try:
        d._empty_guard(d.page, d._swept, "list")
        raise AssertionError("被遮挡时不该返回空清单")
    except RuntimeError as e:
        assert "不判「清单为空」" in str(e), str(e)
    assert "02_list_unclear" in d.shots, "报读不清时必须留图"


def test_empty_guard_refuses_when_popup_sweep_refreshed():
    """本轮弹窗处置触发过刷新（unknown_act=refresh）→ 同样不许把 0 行当空（事故真因）。"""
    swept = {"swept": 1, "handled": [{"id": None, "act": "unknown",
                                      "text": "请先开通智能轨迹助手", "close": "refreshed(unknown)"}]}
    d = _mk([[], []], blockers=[], swept=swept)
    try:
        d._empty_guard(d.page, d._swept, "list")
        raise AssertionError("刷新后立刻读到 0 行不该被当成空清单")
    except RuntimeError as e:
        assert "触发了刷新" in str(e), str(e)


def test_empty_guard_accepts_true_empty():
    """身份对、重读仍 0、无遮挡、没刷新过 → 这才是真·空（不抛错、不落图）。"""
    d = _mk([[], []], blockers=[])
    assert d._empty_guard(d.page, d._swept, "list") == []
    assert d.shots == [], "真·空不该留失败图"


def test_empty_guard_rejects_wrong_page_identity():
    """0 行且页面已不在异常单页（url 不含 url_key）→ 报身份不符，不判空。"""
    d = _mk([[], []], url="https://agentseller.temu.com/mmsos/orders.html")
    try:
        d._empty_guard(d.page, d._swept, "list")
        raise AssertionError("页面身份不对时不该给结论")
    except RuntimeError as e:
        assert "身份不符" in str(e), str(e)
    assert "02_list_wrong_page" in d.shots


def test_blocking_layers_ignores_keep_panels():
    """`keep` 常驻面板（全部消息）不算遮挡 —— 否则真正清空时会误报"读不清"。"""
    reg = _load("temu_abnormal_popups.json")
    d = _FakeDriver([])
    d._js = lambda page, script, arg=None, tries=3: [
        {"text": "全部消息 99+ 异常订单消息通知 标记为已读", "buttons": ["标记为已读"]},
        {"text": "为了保证履约质量，请先开通智能轨迹助手 确认", "buttons": ["确认"]},
    ]
    blockers = d._blocking_layers(d.page)          # 真实现：走登记表 keep 过滤
    assert len(blockers) == 1, "keep 面板被算成遮挡了：%s" % [b.get("text") for b in blockers]
    assert "智能轨迹助手" in blockers[0]["text"]
    assert reg.get("unknown") is not None


def test_list_and_verify_both_go_through_the_empty_guard():
    """闸门接线检查：`read_list` 与 `verify` 读到 0 行都必须过 `_empty_guard`（否则又出假绿）。"""
    src = open(os.path.join(POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert src.count("self._empty_guard(") >= 2, "guard 没接在 list/verify 两条读列表的路径上"
