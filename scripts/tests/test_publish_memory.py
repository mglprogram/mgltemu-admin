#!/usr/bin/env python3
"""钉住「发布链自己不该是内存大户」。

2026-11-03 追查「卡死页反复出现」时发现的第二个内存来源 ——
**我们自己的定时发布**：

    定时刷新（AutomationPanel-Refresh，每天 07:55 / 13:05 / 19:05）
      → panel_refresh._publish_once()
        → publish_admin.py --build
          → pnpm build → vite build          ← 实测 1.2 ~ 1.6 GB

而前端产物**根本没变**，变的只有 `public/data/*.json`。
这台机器 16 GB、当时可用只剩 130~440 MB，一次 vite 构建就把它推过临界点
⇒ 紫鸟窗口里的页面被换页出去 ⇒ 任务报「卡死 / 拿不到登录态 / HTTP 500」。

修法：定时刷新优先走 `--data-only`（只拷 JSON 进 dist，不构建）。

⚠ 但绝不能为了省内存就把两种危险上线：
  ① **没门禁的裸面板**（dist/index.html 里没有 `temu_admin_gate_ok`）
  ② **早已过期的数据**（`--data-only` 不跑抓取，只重发现有 JSON；
     抓取任务连挂几天时，它会一次次把旧数据报成功重发）

⇒ 两条前置都满足才走 `--data-only`，否则退回 `--build`。

跑法：
    pytest scripts/tests/test_publish_memory.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))

import panel_refresh as P


class TestDataOnlyIsTheDefault:
    def test_publish_prefers_data_only(self):
        src = inspect.getsource(P._publish_once)
        assert '"--data-only"' in src, "定时发布又回到无条件 --build 了"
        assert '"--build"' in src, "--build 兜底不能删（dist 缺失/门禁没了要用它）"
        # --data-only 必须出现在 --build 之前（作为首选）
        assert src.find('"--data-only"') < src.find('"--build"'), (
            "--data-only 应是首选，--build 是兜底")

    def test_decision_is_logged(self):
        """到底走了哪条路必须写进 refresh.log，否则省没省内存无从核实。"""
        src = inspect.getsource(P._publish_once)
        assert "发布方式" in src, "没记录选了哪种发布方式"


class TestSafetyGatesForDataOnly:
    """两个门都必须真的挡在前面。"""

    def test_requires_dist_and_gate(self):
        ok, why = P._dist_is_usable()
        src = inspect.getsource(P._dist_is_usable)
        assert "index.html" in src
        assert "temu_admin_gate_ok" in src, (
            "没检查门禁 —— 可能把访问密码门禁缺失的裸面板推上线")
        assert isinstance(ok, bool) and isinstance(why, str)

    def test_requires_fresh_data(self):
        ok, why = P._data_is_fresh()
        src = inspect.getsource(P._data_is_fresh)
        assert "STALE_DATA_MINUTES" in src, "没有新鲜度阈值"
        assert isinstance(ok, bool) and isinstance(why, str)

    def test_stale_threshold_is_reasonable(self):
        v = int(P.STALE_DATA_MINUTES)
        assert 10 <= v <= 24 * 60, (
            "阈值 %s 分钟不合理：太松会把旧数据重发上线，太紧会天天退回 --build" % v)

    def test_freshness_covers_automation_and_goods(self):
        """这两份是面板最重要的两块数据；漏了它们等于没检查。"""
        names = set(P.FRESHNESS_FILES)
        assert "automation.json" in names, "没检查自动化任务数据的新鲜度"
        assert "goods.json" in names, "没检查在售款数据的新鲜度"

    def test_both_gates_are_wired_together(self):
        src = inspect.getsource(P._publish_once)
        assert "_dist_is_usable()" in src
        assert "_data_is_fresh()" in src, "新鲜度检查算了但没接进决策"
        # 新鲜度必须在选定 --data-only 之前判定
        assert src.find("_data_is_fresh()") < src.find('"--data-only"')


class TestGatesFailSafely:
    def test_missing_dist_is_not_usable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P, "ROOT", str(tmp_path))
        ok, why = P._dist_is_usable()
        assert ok is False and why

    def test_missing_data_is_not_fresh(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P, "ROOT", str(tmp_path))
        ok, why = P._data_is_fresh()
        assert ok is False and why

    def test_never_raises(self, monkeypatch, tmp_path):
        """两个检查都跑在发布主链路上，坏了只能退回 --build，不能抛。"""
        monkeypatch.setattr(P, "ROOT", str(tmp_path / "nope"))
        assert isinstance(P._dist_is_usable()[0], bool)
        assert isinstance(P._data_is_fresh()[0], bool)


class TestNoOtherScheduledPathBuilds:
    """别的定时任务不该再有隐藏的 vite 构建。

    `run_flux_weekly.py`（每周四 06:00）保留 `--build` 是**有意**的：
    它是周更，且会真的改前端需要的静态资源，不属于"每天多次"的浪费。
    """

    def test_only_the_weekly_flux_job_builds(self):
        allowed = {"scripts/bel_goods/run_flux_weekly.py",
                   # 发布决策本身：它是唯一**允许**在兜底时选 --build 的地方
                   # （前面两条门禁都不过时才会走到），单独由
                   # TestDataOnlyIsTheDefault 钉住「首选是 --data-only」。
                   "scripts/panel_refresh.py"}
        bad = []
        for p in sorted((PROJ / "scripts").rglob("*.py")):
            rel = p.relative_to(PROJ).as_posix()
            if rel in allowed or rel.endswith("publish_admin.py"):
                continue
            if "/tests/" in rel or rel.startswith("scripts/tests/"):
                continue                       # 测试自己的源码文本不算调用
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:                                  # noqa: BLE001
                continue
            if '"--build"' in txt and "publish_admin" in txt:
                bad.append(rel)
        assert not bad, (
            "这些脚本还在调 publish_admin --build（每天多次跑会吃 1.5GB 内存）：\n  "
            + "\n  ".join(bad)
            + "\n⇒ 只换数据的话用 --data-only")
