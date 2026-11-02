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

#: 会打开/操作紫鸟店铺窗口的业务脚本 → (用途, 用哪个域)
BROWSER_SCRIPTS = {
    "scripts/bel_goods/watch_focus.py": ("限流监控", "us"),
    "scripts/goods/refresh_today_sales.py": ("今日销量", "us"),
    "scripts/goods/run_goods_export.py": ("在售款导出", "us"),
    "scripts/power/run_temu_sales.py": ("销量导出", "us"),
    "scripts/power/run_temu_buylabel.py": ("买面单/确认", "us"),
    "scripts/power/run_temu_abnormal.py": ("异常出库单", "cc"),
}


def _src(rel: str) -> str:
    return (PROJ / rel).read_text(encoding="utf-8", errors="replace")


class TestCoverage:
    def test_every_browser_script_guards_auth(self):
        """每个碰紫鸟窗口的脚本都要引用 auth_guard / ensure_auth。"""
        missing = []
        for rel, (label, _dom) in BROWSER_SCRIPTS.items():
            p = PROJ / rel
            if not p.is_file():
                missing.append(f"{rel}（{label}）文件不存在")
                continue
            src = _src(rel)
            if "auth_guard" not in src and "ensure_auth" not in src:
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
        真正的约束只有一条：**调 guard_store 的那个函数里**，业务动作不能更早。
        """
        ACTS = {"open_tab", "visit", "probe_port_by_window", "create_target",
                "run_store", "run_driver"}
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
                    if name == "guard_store":
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
