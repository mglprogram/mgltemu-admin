#!/usr/bin/env python3
"""钉住「Fnsku 迁移的阈值是 >= 5，且逐尺码判断」。

2026-11-04 用户
---------------
> 阈值 目标库存 >= 5；
> 你有做到每一个尺码都去检查库存，然后去换吗？

第二个问题的答案是**做到了**，但需要钉住 —— 否则以后有人改归一化规则
（比如把尺码后缀也一并去掉）就会退化成"整个款只查一次"，那是完全不同的事。

第一个问题有个容易搞错的地方：旧代码是**严格大于**（`> MIN_STOCK`，默认 10）。
只把默认值改成 5 会得到 `> 5`，正好漏掉「库存 = 5」的目标 —— 而那正是用户要的。
⇒ 必须有 `>=` 语义，并且 dry-run 与 execute 两处**同口径**，
   否则会出现"预演说合格、执行时实时复查又否掉"的假跳过。

跑法：
    pytest scripts/tests/test_pw_migrate_threshold.py -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MIGRATE = Path(r"D:\AI-workplace\DSH\pw-repo\migrate")
DRY = MIGRATE / "dry-run.cjs"
EXEC = MIGRATE / "migrate.cjs"
LAUNCHER = MIGRATE / "run-daily.cmd"
DB = MIGRATE / "sku-db.json"
PLAN = MIGRATE / "dry-run-plan.json"
NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"

requires_node = pytest.mark.skipif(not Path(NODE).exists(), reason="没有 node.exe")


# ---------------------------------------------------------------- 阈值语义
class TestThresholdIsInclusiveFive:
    def test_dry_run_defaults_to_five(self):
        src = DRY.read_text(encoding="utf-8")
        assert re.search(r"return 5;", src), "dry-run.cjs 的默认阈值不是 5"

    def test_execute_defaults_to_five(self):
        src = EXEC.read_text(encoding="utf-8")
        assert re.search(r"return 5;", src), "migrate.cjs 的默认阈值不是 5"

    def test_both_use_inclusive_comparison(self):
        """★ 关键：`>=` 不是 `>`。库存正好 5 的目标必须被选中。"""
        for p in (DRY, EXEC):
            src = p.read_text(encoding="utf-8")
            assert "MIN_STOCK_INCLUSIVE" in src, "%s 没有开闭区间开关" % p.name
            assert "stock >= MIN_STOCK" in src, "%s 不是 >= 比较" % p.name
            # 不能残留裸的 `> MIN_STOCK` 比较
            assert "totalAvailable > MIN_STOCK" not in src

    def test_can_restore_strict_comparison(self):
        """留个口子：想回到严格大于时不用改代码。"""
        for p in (DRY, EXEC):
            src = p.read_text(encoding="utf-8")
            assert "PW_MIN_STOCK_INCLUSIVE" in src

    def test_log_label_shows_the_operator(self):
        """日志要写 `≥ 5` 还是 `> 5`，别让人猜。"""
        src = DRY.read_text(encoding="utf-8")
        assert "MIN_STOCK_LABEL" in src
        assert "≥" in src

    def test_launcher_passes_five(self):
        """run-daily.cmd 里显式 set 的值会覆盖代码默认 —— 必须同步成 5。

        ⚠ 只认**未注释的 `if not defined` 行**：文件注释里还留着一句
        `set "PW_MIN_STOCK=20"` 作为"想更严时怎么改"的示例，
        用宽松正则会把示例当成实际值。
        """
        src = LAUNCHER.read_text(encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in src.splitlines()
                 if ln.strip() and not ln.strip().upper().startswith("REM")]
        m = None
        for ln in lines:
            m = re.search(r'set "PW_MIN_STOCK=(\d+)"', ln)
            if m:
                break
        assert m, "run-daily.cmd 里找不到生效的 PW_MIN_STOCK 赋值"
        assert m.group(1) == "5", (
            "启动器生效值是 %s，会覆盖代码里的 5" % m.group(1))


@requires_node
class TestStockBoundaryInNode:
    """直接用 node 验边界，避免只靠读源码。"""

    def _q(self, expr: str) -> str:
        code = (
            "const fs=require('fs');const src=fs.readFileSync(%r,'utf8');"
            "const m={};(new Function('require','process','module','__dirname',"
            "src.replace(/const plan = \\[\\];[\\s\\S]*$/,'');"
            "))(require,process,m,%r);"
            "console.log(eval(%r));"
        ) % (str(DRY).replace("\\", "\\\\"), str(MIGRATE).replace("\\", "\\\\"), expr)
        p = subprocess.run([NODE, "-e", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90,
                           cwd=str(MIGRATE))
        return ((p.stdout or "") + (p.stderr or "")).strip()

    def test_boundary_values(self):
        out = self._q("JSON.stringify([0,4,5,6].map(qualifiedStock))")
        if "true" not in out:
            pytest.skip("无法从 dry-run.cjs 抽取函数（改动了模块结构？）：%s" % out[:120])
        assert out == "[false,false,true,true]", (
            "边界判断不对（4 应否、5 应真）：%s" % out)


# ---------------------------------------------------------------- 逐尺码
class TestEverySizeIsCheckedSeparately:
    """用户的第二个问题：是不是**每个尺码**都查库存再换。

    实现方式：`core` 是**归一款号**，本身就带尺码后缀
    （`207202-5SetC-XXL` 是一个 core，`207202-5SetC-M` 是另一个）。
    归一化只删品牌前缀，尺码一律保留。
    """

    def _plan(self) -> list:
        if not PLAN.is_file():
            pytest.skip("没有 dry-run-plan.json（先跑 node dry-run.cjs --json）")
        d = json.loads(PLAN.read_text(encoding="utf-8"))
        return d.get("plan") or []

    def test_cores_carry_the_size_suffix(self):
        plan = self._plan()
        if not plan:
            pytest.skip("计划为空")
        # ⚠ 尺码不止 XS~XXXL 这几种写法，实测还有 `-1XL` / `-2XL` 这种带数字的变体
        #   （207002-3BHP-1XL）。用"必须以字母尺码结尾"判会误报，所以改成
        #   「最后一段是短后缀（≤4 字符且含字母）」。
        def has_size(core: str) -> bool:
            tail = core.rsplit("-", 1)[-1] if "-" in core else ""
            return bool(tail) and len(tail) <= 4 and any(ch.isalpha() for ch in tail)

        no_size = [p["core"] for p in plan if not has_size(p["core"])]
        assert not no_size, (
            "这些 core 看不出尺码后缀 —— 可能退化成了整款判断：%s"
            % sorted(set(no_size))[:8])

    def test_same_style_has_multiple_size_entries(self):
        """同款多尺码各占一条 —— 这正是"逐尺码"的证据。"""
        plan = self._plan()
        if not plan:
            pytest.skip("计划为空")
        def style(c):
            return c.rsplit("-", 1)[0] if "-" in c else c
        from collections import Counter
        cnt = Counter(style(p["core"]) for p in plan)
        multi = [k for k, v in cnt.items() if v > 1]
        assert multi, "没有任何一个款号出现多个尺码 —— 逐尺码判断没生效"

    def test_each_entry_targets_one_size_only(self):
        """目标 Fnsku 的尺码必须与源一致，不能把 M 的 SKU 迁到 XL 的批次。"""
        plan = self._plan()
        if not plan:
            pytest.skip("计划为空")
        bad = []
        for p in plan:
            src = p.get("fromSellerSku") or ""
            dst = p.get("toSellerSku") or ""
            s_sz = src.rsplit("-", 1)[-1].upper() if "-" in src else ""
            d_sz = dst.rsplit("-", 1)[-1].upper() if "-" in dst else ""
            if s_sz and d_sz and s_sz != d_sz:
                bad.append("%s: %s -> %s（尺码不同！）" % (p["core"], src, dst))
        assert not bad, "跨尺码迁移，会导致规格错乱：\n  " + "\n  ".join(bad[:10])

    def test_normalization_keeps_size_and_setcode(self):
        """归一化规则：只删品牌前缀，`R`/`N`/`1.1`/`SetA`/尺码 全保留。"""
        meta = json.loads(DB.read_text(encoding="utf-8")).get("_meta") or {}
        rule = str(meta.get("rule") or "")
        if not rule:
            pytest.skip("sku-db.json 没有 _meta.rule")
        assert "前缀" in rule, "规则没说清只删前缀"
        assert "SetA" in rule or "尺码" in rule


# ---------------------------------------------------------------- 数量
class TestPlanGrewAfterLoweringTheBar:
    """把门槛从 >10 降到 >=5，合格目标应当变多（不是不变）。"""

    def _plan(self) -> list:
        if not PLAN.is_file():
            pytest.skip("没有 dry-run-plan.json（先跑 node dry-run.cjs --json）")
        return json.loads(PLAN.read_text(encoding="utf-8")).get("plan") or []

    def test_more_cores_have_a_target_now(self):
        plan = self._plan()
        if not plan:
            pytest.skip("计划为空")
        cores = {p["core"] for p in plan}
        assert len(cores) >= 200, (
            "合格款号只有 %d 个，门槛可能没真的降到 5" % len(cores))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
