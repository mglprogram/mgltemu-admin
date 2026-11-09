#!/usr/bin/env python3
"""钉住「所有会碰紫鸟窗口的业务脚本都必须有登录态兜底」。

为什么需要这条测试（2026-11-02 全量审计）
------------------------------------------
用户问「登录态这个问题为什么时常出现」。

真实原因**不是**授权代码不好 —— `auth_temu_driver` 的 `probe_login_state`
早就修对了（真开一页请求一次，而不是看窗口里有没有旧订单页）。
真正的问题是**大多数业务脚本根本没接兜底**：

    会碰紫鸟窗口的脚本              有授权兜底
    -----------------------------  ----------
    bel_goods/watch_focus.py      （限流监控）  ✗  ← 历史失败最多
    goods/refresh_today_sales.py  （今日销量）  ✗
    goods/run_goods_export.py     （在售款导出）✗
    power/run_temu_sales.py       （销量导出）  ✗
    power/run_temu_buylabel.py                  ✓
    power/run_temu_abnormal.py                  ✓

⇒ 窗口一重开（紫鸟重开必掉登录态），那 4 个脚本照旧失败，只能人工补授权。
   本测试把「必须接兜底」变成硬约束，防止以后新增脚本又漏掉。

跑法：
    pytest scripts/tests/test_auth_guard_coverage.py -q
"""
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
LIB = PROJ / "scripts" / "power" / "lib"
sys.path.insert(0, str(LIB))

import auth_guard  # noqa: E402

#: 会打开/操作紫鸟店铺窗口的**业务入口** → (用途, 用哪个域)
#:
#: 2026-11-02 用户要求「所有涉及打开店铺的页面都加入登录态兜底」——
#: 这里就是那份清单。新增碰窗口的脚本必须登记进来，漏了测试直接失败。
BROWSER_SCRIPTS = {
    "scripts/bel_goods/watch_focus.py": ("限流监控", "us"),
    "scripts/goods/refresh_today_sales.py": ("今日销量", "us"),
    "scripts/goods/run_goods_export.py": ("在售款导出", "us"),
    "scripts/power/run_temu_sales.py": ("销量导出", "us"),
    "scripts/power/run_temu_buylabel.py": ("买面单/确认", "us"),
    "scripts/power/run_temu_abnormal.py": ("异常出库单", "cc"),
    # ---- 2026-11-02 第二批（用户要求全覆盖）
    "scripts/bel_goods/run_flux_weekly.py": ("流量加速周更", "us"),
    "scripts/bel_goods/build_presale_data.py": ("预售数据构建", "us"),
    "scripts/power/run_temu_stock_topup.py": ("补货清单取数", "us"),
    "scripts/bel_goods/flux_auth.py": ("流量页登录态判定", "us"),
    # ---- 2026-11-09 补登（发货链导表，也会被单独手工跑）
    "scripts/power/temu_export_by_select.py": ("按单号勾选导出订单", "us"),
}

#: **确定性排除**：不是业务入口，或由调用方兜底。写清理由，别默默跳过。
EXCLUDED = {
    # 排障/校验小工具：人工一次性运行，不参与无人值守
    "scripts/bel_goods/cdp_visit.py": "排障工具",
    "scripts/bel_goods/headless_check.py": "排障工具",
    "scripts/bel_goods/verify_fluxall.py": "校验工具",
    "scripts/bel_goods/verify_weak.py": "校验工具",
    "scripts/power/clean_leaked_tabs.py": "清理工具",
    "scripts/ensure_store_pages.py": "开页工具（不做业务取数）",
    # 2026-11-09 补登：这三个是**发货链内部的单步入口**，由 run_temu_fulfill.py
    # 在整链开头统一兜底（每店先 guard_store 再跑 17 步），单步再兜一次只会重复触发授权。
    # `temu_export_by_select.py` 例外：它**也**被当作独立入口手工跑，已在自身 main() 里
    # 直接调 auth_guard.guard_store，所以放 BROWSER_SCRIPTS 而不是这里。
    "scripts/power/oms_ship_one.py": "发货链单步（hx_ship），整链 run_temu_fulfill 开头已兜底",
    "scripts/power/temu_confirm_ship.py": "发货链单步（temu_confirm），整链开头已兜底",
    # 只读工具：全文件没有任何 click / dispatchMouse* / insertText（见其文档串第 9 行），
    # 只 evaluate 读 DOM + screenshot，它没有"登录态掉了会做错事"的风险面。
    "scripts/power/peek_store_page.py": "只读排障工具（不点击、不写页面）",
    # 帕沃尔自己的 Chrome（独立 profile），不碰紫鸟、不碰 TEMU 卖家中心，
    # 登录由 pw_login 用账密自己做 —— 与紫鸟登录态无关。
    # 2026-11-09 起默认**有头**（用户令），仍然只是独立 profile，不影响这条豁免。
    "scripts/power/lib/pw_browser.py": "帕沃尔独立 Chrome（默认有头），非紫鸟窗口",
    # 通用包装器 / 原子能力：由**调用方**兜底
    "scripts/run_with_window_lock.py": "通用锁包装器，不碰页面",
    "scripts/power/lib/temu_presale.py": "被 build_presale_data 调，上层已兜底",
    "scripts/bel_goods/fetch_focus_spus.py": "被 watch_focus/run_flux_weekly 调，上层已兜底",
    # ↑ 两个 driver 也是库层：入口 run_temu_buylabel.py / run_temu_abnormal.py
    #   各自带 auth_guard，driver 自己再兜一次会重复触发授权。
    "scripts/power/lib/temu_buylabel_driver.py": "被 run_temu_buylabel.py 调，入口已兜底",
    "scripts/power/lib/temu_abnormal_driver.py": "被 run_temu_abnormal.py 调，入口已兜底",
    "scripts/power/lib/temu_sales_driver.py": "被 run_temu_sales.py 调，入口已兜底",
    # 库层
    "scripts/power/lib/cdp_transport.py": "CDP 传输层",
    "scripts/power/lib/own_page.py": "自建标签页原子能力",
    "scripts/power/lib/store_pages.py": "开页原子能力",
    "scripts/power/lib/ziniao_store.py": "紫鸟桥原子能力",
    "scripts/power/lib/auth_temu_driver.py": "授权实现本体",
    "scripts/power/lib/auth_guard.py": "兜底实现本体",
    "scripts/goods/ziniao_port.py": "端口探测原子能力",
    "scripts/goods/zpage.py": "页面操作原子能力",
    "scripts/goods/cdp_net.py": "CDP 网络原子能力",
    "scripts/bel_goods/zpage.py": "页面操作原子能力",
    "scripts/bel_goods/zexec.py": "JS 执行原子能力",
    "scripts/lib/preflight.py": "预检原子能力",
    "scripts/lib/zport.py": "端口原子能力",
}


def _src(rel: str) -> str:
    return (PROJ / rel).read_text(encoding="utf-8", errors="replace")


def _has_auth_guard(src: str) -> bool:
    """这段源码有没有「登录态兜底」。

    认三种写法（都是合法的兜底，别只认 auth_guard 一个名字）：
      · `auth_guard` / `ensure_auth`   —— 标准入口
      · `probe_login_state`            —— 直接用真请求探针（flux_auth 就是这样）
      · `ensure_login`                 —— 本脚本自带的登录态保证（run_flux_weekly）
    """
    return any(k in src for k in ("auth_guard", "ensure_auth",
                                  "probe_login_state", "ensure_login"))


class TestNoUnregisteredBrowserScript:
    """**这条是「全覆盖」的兜底闸门**（2026-11-02 用户要求）。

    逐个手工维护清单迟早会漏（这正是登录态问题反复出现的根因）。
    这里反过来扫全仓：凡代码里出现「打开/导航店铺页面」的强特征、
    又不是库层/排除清单里的，就必须登记在 `BROWSER_SCRIPTS` 里。
    """

    #: 只认「真的会开/导航店铺页面」的强特征。
    #: 刻意**不用** `connect(`：它会命中 sqlite3 / urllib / websocket 的 connect。
    STRONG = (
        "probe_port_by_window", "open_tab(", "OwnPage(", "create_target(",
        "cdp_transport.connect(", "page visit", "page exec", "store open",
        "ziniao_store.open",
    )

    def test_every_window_touching_entry_is_registered(self):
        import glob
        import os
        unregistered = []
        for p in glob.glob(str(PROJ / "scripts" / "**" / "*.py"), recursive=True):
            rel = os.path.relpath(p, str(PROJ)).replace("\\", "/")
            if "/tests/" in rel or os.path.basename(p).startswith("test_"):
                continue
            if rel in BROWSER_SCRIPTS or rel in EXCLUDED:
                continue
            try:
                src = (PROJ / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(s in src for s in self.STRONG):
                unregistered.append(rel)
        assert not unregistered, (
            "这些脚本会打开/操作紫鸟店铺页面，但既没登记在 BROWSER_SCRIPTS、"
            "也没在 EXCLUDED 里写明理由：\n  "
            + "\n  ".join(sorted(unregistered))
            + "\n⇒ 要么给它加 auth_guard.guard_store()，要么登记进 EXCLUDED 并写清为什么不需要"
        )

    def test_excluded_entries_have_a_reason(self):
        bad = [k for k, v in EXCLUDED.items() if not str(v or "").strip()]
        assert not bad, "EXCLUDED 里这些条目没写理由：\n  " + "\n  ".join(bad)

    def test_registered_scripts_actually_exist(self):
        missing = [k for k in BROWSER_SCRIPTS if not (PROJ / k).is_file()]
        assert not missing, "BROWSER_SCRIPTS 里登记了不存在的文件：\n  " + "\n  ".join(missing)


class TestCoverage:
    def test_every_browser_script_guards_auth(self):
        """每个碰紫鸟窗口的脚本都要有登录态兜底（三种写法都认，见 _has_auth_guard）。"""
        missing = []
        for rel, (label, _dom) in BROWSER_SCRIPTS.items():
            p = PROJ / rel
            if not p.is_file():
                missing.append(f"{rel}（{label}）文件不存在")
                continue
            if not _has_auth_guard(_src(rel)):
                missing.append(f"{rel}（{label}）没有登录态兜底")
        assert not missing, (
            "这些会碰紫鸟窗口的脚本没有授权兜底，窗口一重开就必然失败：\n  "
            + "\n  ".join(missing)
            + "\n⇒ 在端口探到之后、真正干活之前加 `auth_guard.guard_store(<店>, log=...)`"
        )

    def test_guard_is_called_before_the_business_action(self):
        """兜底必须在**同一个函数里**、且早于业务动作。

        判据用 AST **按函数作用域**比顺序 —— 否则会把 `def _ensure_own_page()`
        这类**辅助函数定义**里的 `open_tab` 当成"业务动作在前面"（实测踩到两次）。
        真正的约束只有一条：**调兜底的那个函数里**，业务动作不能更早。

        ⚠ `probe_port_by_window` 不算"业务动作"：兜底自己也要探端口才能干活，
           `build_presale_data.read_temu_rows()` 就是先探端口、再兜底、再读数据。
        """
        GUARDS = {"guard_store", "ensure_auth", "probe_login_state", "ensure_login"}
        ACTS = {"open_tab", "visit", "create_target", "run_store", "run_driver"}
        bad = []
        for rel, (label, _d) in BROWSER_SCRIPTS.items():
            p = PROJ / rel
            if not p.is_file():
                continue
            tree = ast.parse(_src(rel), rel)
            for func in [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                seq = []                                  # (位置, 种类)
                for n in ast.walk(func):
                    if not isinstance(n, ast.Call):
                        continue
                    fn = n.func
                    name = getattr(fn, "id", "") or getattr(fn, "attr", "")
                    if name in GUARDS:
                        seq.append(((n.lineno, n.col_offset), "guard"))
                    elif name in ACTS:
                        seq.append(((n.lineno, n.col_offset), "act"))
                if not any(k == "guard" for _, k in seq):
                    continue
                seq.sort()
                first_guard = next(pos for pos, k in seq if k == "guard")
                before = [pos for pos, k in seq if k == "act" and pos < first_guard]
                if before:
                    bad.append(f"{rel}（{label}）.{func.name}()："
                               f"业务动作在第 {before[0][0]} 行，兜底在第 {first_guard[0]} 行")
        assert not bad, (
            "授权兜底放在了同一函数里业务动作之后（等于没兜底）：\n  "
            + "\n  ".join(bad))


class TestHungPageHealing:
    """`heal_own_hung_pages()` —— 登录态之外的第二大类反复故障。

    2026-11-02 实测（预售取数）：

        [TEMU] 「已售罄」→ 5 行
        [warn] 端口 50070 读取失败：server rejected WebSocket connection: HTTP 500

    `HTTP 500` = 那个 target 的**渲染进程已经死了**，CDP 连不上。
    它既不是登录态问题也不是网络问题，而 `build_presale_data` 完全没有自愈，
    于是每次都退化成读缓存（数据看着正常、其实是旧的）。
    """

    def test_never_closes_the_users_pages(self):
        """★ 最重要的一条：**永不关闭**未登记的页（用户的默认页）。

        2026-10-29 教训：旧实现遍历窗口所有页判 hung，把用户的 3 个页面关掉了
        （`goods/list` + 一个商品页 + 一个销量分析页），用户看到「我的页没了」。

        2026-11-03 补强（这次是"太保守"出了事故）：旧实现直接 `continue` 跳过未登记的页
        ⇒ 而卡死的恰恰是**用户的默认页**（商品列表 / 销量分析从不登记）
        ⇒ 永远修不到，而卡死页上读一次 `.url` 要卡 60 秒 ⇒ 授权流程被拖到超时、
        表现为「一直获取不到登录态」。现在改成：**别人的页也只导航修复、绝不关闭**。
        「跳过」不再是正确行为，「不关」才是硬约束。
        """
        import inspect
        src = inspect.getsource(auth_guard.heal_own_hung_pages)
        assert "registered_targets" in src, "必须用登记表区分归属"
        assert "if tid in mine" in src, "必须按归属分支处理"
        # 关页（recover_hung_page）只能出现在「自己的页」那一支
        own, _, foreign = src.partition("if not repair_foreign")
        assert "recover_hung_page" in own, "自有页应当关掉（可再生）"
        assert "recover_hung_page" not in foreign, (
            "关页动作跑到了「别人的页」那一支 —— 会关掉用户的默认页")
        assert "navigate_hung_page" in foreign, (
            "别人的卡死页必须走导航修复（不关页），否则用户默认页卡死后永远修不掉")

    def test_zero_port_is_a_noop(self):
        assert auth_guard.heal_own_hung_pages(0, log=lambda m: None) == 0

    def test_never_raises(self, monkeypatch):
        """cdp_transport 不可用 / 枚举失败都不能抛。"""
        r = auth_guard.heal_own_hung_pages(50070, log=lambda m: None, window="X")
        assert isinstance(r, int)

    def test_returns_a_usable_port(self):
        """成功时要把（可能变了的）端口返回给调用方。"""
        import inspect
        src = inspect.getsource(auth_guard.heal_own_hung_pages)
        assert "return new_port" in src, "关页后端口可能变，必须重探并返回"

    def test_presale_calls_heal_before_guard(self):
        """自愈要排在登录态兜底**之前** —— 挂死的页会让探针也连不上。"""
        src = _src("scripts/bel_goods/build_presale_data.py")
        heal_at = src.find("heal_own_hung_pages")
        guard_at = src.find("guard_store")
        assert heal_at != -1, "build_presale_data 没接自愈"
        assert guard_at != -1, "build_presale_data 没接登录态兜底"
        assert heal_at < guard_at, "自愈必须排在登录态兜底之前"


class TestGuardStoreContract:
    """`guard_store()` 的行为契约。"""

    def test_no_store_is_a_skip_not_a_crash(self):
        r = auth_guard.guard_store("", log=lambda m: None)
        self_ok = r["ran"] is False and r["skipped"] is True
        assert self_ok, r

    def test_disabled_is_a_skip(self):
        r = auth_guard.guard_store("BEL", enabled=False, log=lambda m: None)
        assert r["skipped"] is True
        assert "--no-auth-guard" in r["reason"]

    def test_missing_window_is_a_skip(self):
        """店名不在 STORE_WINDOWS 里 ⇒ 探不到窗口 ⇒ 跳过，不能抛。"""
        r = auth_guard.guard_store("NOSUCHSTORE", log=lambda m: None)
        assert r["ran"] is False
        assert r["skipped"] is True

    def test_never_raises_even_when_everything_is_broken(self, monkeypatch):
        monkeypatch.setattr(auth_guard, "STORE_WINDOWS", {"X": "NO-SUCH-WINDOW"})
        r = auth_guard.guard_store("X", log=lambda m: None)
        assert isinstance(r, dict)

    def test_store_windows_covers_the_two_real_stores(self):
        assert auth_guard.STORE_WINDOWS.get("BEL") == "TM-BEL-MGL"
        assert auth_guard.STORE_WINDOWS.get("BHY") == "TM-BHY-YJF"


class TestSkipRulesArePreserved:
    """旧契约不能因为新增 guard_store 而破坏。"""

    def test_should_guard_is_still_a_pure_function(self):
        assert auth_guard.should_guard("overdue", "BEL", 50070) is True
        assert auth_guard.should_guard("auth", "BEL", 50070) is False
        assert auth_guard.should_guard("plan", "BEL", 50070) is False
        assert auth_guard.should_guard("overdue", "", 50070) is False
        assert auth_guard.should_guard("overdue", "BEL", 0) is False

    def test_should_guard_has_no_side_effects(self):
        """纯函数：连着调多次结果一样，且不读文件/网络。"""
        for _ in range(3):
            assert auth_guard.should_guard("confirm", "BEL", 50070) is True


class TestAuthDriverImportPath:
    """两种情况都要能 import —— 业务脚本的 sys.path 摆法不一致。"""

    def test_importable_when_lib_is_on_sys_path(self):
        """watch_focus / run_goods_export 是 `import auth_guard`（lib 在 path 上）。"""
        import importlib
        m = importlib.import_module("auth_guard")
        assert hasattr(m, "guard_store")

    def test_importable_as_a_package(self):
        """run_temu_sales / buylabel 是 `from lib import auth_guard`。

        ⚠ 必须清掉 `sys.modules` 里可能已存在的两个名字再测：全套测试一起跑时，
        别的用例可能已经用 `import auth_guard`（lib 在 path 上）的方式加载过
        `auth_temu_driver`，于是 `from . import auth_temu_driver` 会拿到
        顶层模块而不是 `lib.*`，造成**只在全量运行时失败**的假阳性（实测踩到）。
        这里用子进程隔离，彻底不受导入顺序影响。
        """
        import subprocess
        code = (
            "import sys;"
            "sys.path.insert(0, r'%s');"
            "import importlib;"
            "m = importlib.import_module('lib.auth_guard');"
            "assert hasattr(m, 'guard_store');"
            "print('OK')"
        ) % (PROJ / "scripts" / "power")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        assert p.returncode == 0 and "OK" in (p.stdout or ""), \
            f"包方式导入失败：stdout={p.stdout!r} stderr={(p.stderr or '')[-400:]!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
