#!/usr/bin/env python3
"""钉住「Bottom 滞销库存的通知发 TEMU 机器人群」。

2026-11-04 用户要求
-------------------
> 把 bottom 滞销库存的通知转到 TEMU 群

这个任务（`TEMU_Bottom_DailyStockSync`）原来走**应用机器人私聊**
（`mode=app` + 本人 `ou_...`），只有开发者账号自己看得到；团队拿到的是
「库存表更新了」但没人收到对比摘要。改走群机器人 webhook 后与看门狗告警、
异常单导出同一条通道。

两个坑必须钉住：
  ① webhook 明文不能进 `config.json`（会被同步/拷贝，泄露了就谁都能往群里发）
     ⇒ 用 `__FEISHU_WEBHOOK_ENV__` 占位符，运行时从 `FEISHU_WEBHOOK` 取。
  ② `sync.py` 原来把 `webhook` 字段**原样**当 URL 用，占位符会被
     `_detect_channel()` 判成 unknown 而静默跳过 —— 通知看起来"发了"，其实没发。

跑法：
    pytest scripts/tests/test_bottom_notify_group.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SKILL = Path.home() / ".agents" / "skills" / "temu-bottom-stock-sync"
SYNC = SKILL / "scripts" / "sync.py"
CFG = SKILL / "config.json"


def _cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def mod():
    """按文件路径加载 sync.py（它不在 sys.path 上，且目录名带连字符）。"""
    if not SYNC.is_file():
        pytest.skip("sync.py 不存在")
    spec = importlib.util.spec_from_file_location("bt_sync_under_test", SYNC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- 配置
class TestConfigPointsAtTheGroup:
    def test_mode_is_webhook_not_app(self):
        n = _cfg()["notify"]
        assert n.get("mode") == "webhook", (
            "mode 还是 app —— 会继续发到个人私聊，团队看不到")
        assert n.get("enabled") is True

    def test_no_personal_open_id_left_as_the_target(self):
        n = _cfg()["notify"]
        assert "receive_id" not in n, (
            "还留着 receive_id：一旦 mode 回退成 app 就又发私聊了")
        assert "receive_id_type" not in n

    def test_webhook_is_not_plaintext_in_the_config(self):
        """config.json 会被同步/拷贝，明文 webhook 泄露 = 谁都能往群里发。"""
        url = str(_cfg()["notify"].get("webhook") or "")
        assert "bot/v2/hook/" not in url, (
            "webhook 明文写进了 config.json，应改用占位符")
        assert url == "__FEISHU_WEBHOOK_ENV__", "webhook 字段应指向环境变量占位符"

    def test_original_recipient_is_kept_for_reference(self):
        """旧收件人别丢，回滚时要知道原来发给谁。"""
        assert _cfg()["notify"].get("_原私聊收件人", "").startswith("ou_")


# ---------------------------------------------------------------- 解析
class TestThePlaceholderIsResolved:
    def test_placeholder_resolves_to_the_real_hook(self, mod, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
        got = mod._resolve_webhook("__FEISHU_WEBHOOK_ENV__")
        assert got == "https://open.feishu.cn/open-apis/bot/v2/hook/abc"

    def test_a_real_url_passes_through_untouched(self, mod):
        u = "https://open.feishu.cn/open-apis/bot/v2/hook/xyz"
        assert mod._resolve_webhook(u) == u

    def test_missing_env_yields_empty_not_the_placeholder(self, mod, monkeypatch):
        """取不到就必须返回空，绝不能把占位符当 URL 发出去。"""
        monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
        # 断掉 User 级兜底，模拟"哪儿都没有"
        monkeypatch.setattr(mod.subprocess, "run",
                            lambda *a, **kw: type("P", (), {"stdout": ""})())
        assert mod._resolve_webhook("__FEISHU_WEBHOOK_ENV__") == ""

    def test_resolved_url_is_recognised_as_feishu(self, mod, monkeypatch):
        """★ 关键：占位符不解析的话 `_detect_channel` 会判 unknown 并**静默跳过**。"""
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
        assert mod._detect_channel("__FEISHU_WEBHOOK_ENV__") == "unknown"   # 前提
        assert mod._detect_channel(mod._resolve_webhook("__FEISHU_WEBHOOK_ENV__")) == "feishu"

    def test_never_raises(self, mod):
        assert isinstance(mod._resolve_webhook(""), str)
        assert isinstance(mod._resolve_webhook(None), str)


# ---------------------------------------------------------------- 接线
class TestSendNotificationUsesTheResolver:
    def _src(self) -> str:
        return SYNC.read_text(encoding="utf-8")

    def test_send_notification_calls_the_resolver(self):
        src = self._src()
        seg = src[src.find("def send_notification"):]
        seg = seg[:seg.find("\ndef ", 1)]
        assert "_resolve_webhook(" in seg, (
            "send_notification 没走解析器 —— 占位符会被当 URL，通知静默丢失")

    def test_resolver_is_called_before_channel_detection(self):
        src = self._src()
        seg = src[src.find("def send_notification"):]
        seg = seg[:seg.find("\ndef ", 1)]
        assert seg.find("_resolve_webhook(") < seg.find("_detect_channel(")

    def test_app_mode_still_exists(self):
        """别为了迁群把应用机器人删掉 —— 个人/多维表格场景还要用。"""
        assert "def _feishu_app_send" in self._src()
        assert "im/v1/messages" in self._src()


class TestTheMessageSurvivesTheGroup:
    """群机器人的 text 消息不解析 markdown，但 ⚠/→/↓ 这些符号没问题。"""

    def test_arrow_and_warning_symbols_are_plain_text(self):
        """图里的正文用了这些符号，确认它们不是 markdown 标记。"""
        sample = "代发库存: 7667 ↓107 (-1.4%)\n⚠ 没动销 SKU 5 款"
        for ch in ("↓", "⚠", "→"):
            assert ch not in ("*", "_", "`", "#"), "%s 不该是 markdown 标记" % ch
        assert "**" not in sample

    def test_message_is_built_as_title_plus_lines(self, mod):
        """正文拼装方式：title + 逐行，保持不变。"""
        src = (SKILL / "scripts" / "sync.py").read_text(encoding="utf-8")
        seg = src[src.find("def send_notification"):]
        seg = seg[:seg.find("\ndef ", 1)]
        assert 'title + "\\n" + "\\n".join(lines)' in seg


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
