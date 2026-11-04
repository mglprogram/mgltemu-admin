#!/usr/bin/env python3
"""钉住「业务通知必须走 TEMU 机器人群，而不是应用机器人私聊」。

2026-11-04 用户要求
-------------------
> 把推送到飞书应用的定时任务全部帮我转移到 TEMU 的那个机器人群去

机器上原本有**两条互不相通**的飞书通道：

| 通道 | 实现 | 收件人 | 谁在用 |
|---|---|---|---|
| 群机器人 webhook | `bot/v2/hook/<id>` | 群里所有人 | 看门狗告警、异常单导出 |
| 应用机器人私聊 | `im/v1/messages` | 本人的 `ou_...` | 预售提醒、在售款补货清单 |

后果：同一批定时任务，告警在群里、业务清单在你个人私聊里 —— 团队看得到"失败"，
看不到"今天该补哪些货"；而你自己漏看私聊就等于没收到。

⇒ 业务清单统一改走群机器人。但两条通道有**格式差异**，不能只是换个函数调用。

跑法：
    pytest scripts/tests/test_feishu_group_migration.py -q
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))

import notify_push as NP  # noqa: E402


# ---------------------------------------------------------------- 纯文本适配
class TestGroupTextFormatting:
    """群机器人的 `text` 消息**不解析 markdown**，发 `**粗体**` 就是满屏星号。"""

    def test_bold_markers_are_stripped(self):
        assert NP.to_group_text("**可加库存 3 个**") == "可加库存 3 个"

    def test_backticks_are_stripped(self):
        assert NP.to_group_text("由 `daily_list.py` 生成") == "由 daily_list.py 生成"

    def test_content_is_not_otherwise_changed(self):
        """只去标记，不能顺手改内容 —— 清单里的数字一个都不能动。"""
        src = "低库存 12 款·尺码 ｜ 0 库存 3\n加 25 件"
        assert NP.to_group_text(src) == src

    def test_long_message_is_truncated_not_dropped(self):
        """超长时截断并指路面板，而不是整条发不出去。"""
        long = "X" * (NP.GROUP_TEXT_MAX_CHARS + 500)
        out = NP.to_group_text(long)
        assert len(out) < NP.GROUP_TEXT_MAX_CHARS + 200
        assert "已截断" in out
        assert "面板" in out, "截断后要告诉人完整内容去哪看"

    def test_short_message_is_untouched(self):
        assert NP.to_group_text("短消息") == "短消息"

    def test_limit_leaves_room_for_multibyte(self):
        """飞书上限是 30 KB；中文一个字 3 字节，所以字符上限必须留足余量。"""
        assert NP.GROUP_TEXT_MAX_CHARS * 3 <= 30 * 1024


# ---------------------------------------------------------------- 真的走群
class TestPushGroupGoesToTheWebhook:
    def test_push_group_hits_the_bot_webhook(self, monkeypatch):
        """必须打 `bot/v2/hook`，不是 `im/v1/messages`。"""
        seen = {}

        class FakeResp:
            def read(self):
                return json.dumps({"code": 0, "msg": "success"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(req, timeout=None, **kw):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        monkeypatch.setattr(NP, "feishu_webhook", lambda: "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
        monkeypatch.setattr(NP.urllib.request, "urlopen", fake_open)
        ok, why = NP.push_group("TEMU 标题", "**正文**")

        assert ok, why
        assert "bot/v2/hook" in seen["url"]
        assert "im/v1/messages" not in seen["url"]
        sent = seen["body"]["content"]["text"]
        assert "**" not in sent, "群消息里还有 markdown 标记"
        assert "TEMU 标题" in sent

    def test_keyword_validation_failure_is_explained(self, monkeypatch):
        """群机器人开了关键词校验，命中不了回 19024 —— 要说清是关键词，不是网络。"""
        class FakeResp:
            def read(self):
                return json.dumps({"code": 19024, "msg": "Key Words Not Found"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(NP, "feishu_webhook", lambda: "https://hook/x")
        monkeypatch.setattr(NP.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        ok, why = NP.push_group("标题没有关键词", "正文")
        assert not ok
        assert "关键词" in why

    def test_no_webhook_is_a_clear_skip(self, monkeypatch):
        monkeypatch.setattr(NP, "feishu_webhook", lambda: "")
        ok, why = NP.push_group("t", "m")
        assert not ok and "FEISHU_WEBHOOK" in why

    def test_never_raises_on_network_error(self, monkeypatch):
        monkeypatch.setattr(NP, "feishu_webhook", lambda: "https://hook/x")

        def boom(*a, **kw):
            raise OSError("network down")

        monkeypatch.setattr(NP.urllib.request, "urlopen", boom)
        ok, why = NP.push_group("t", "m")
        assert not ok and why


# ---------------------------------------------------------------- 调用点
class TestCallSitesMovedToTheGroup:
    """两个业务脚本必须默认走群，且不能再默默发到个人私聊。"""

    def _src(self, rel):
        return (PROJ / rel).read_text(encoding="utf-8")

    def test_presale_notifies_the_group(self):
        src = self._src("scripts/bel_goods/build_presale_data.py")
        assert "push_group(" in src, "预售提醒没走机器人群"
        # 旧的个人 open_id 直发必须消失（否则又发回私聊）
        assert "FB.send_text(nc[" not in src, "还在用个人 open_id 直发"

    def test_daily_list_notifies_the_group(self):
        src = self._src("scripts/goods/daily_list.py")
        assert "push_group(" in src, "在售款清单没走机器人群"
        # 默认路径不能再从 temu_presale.json 读个人 receive_id
        seg = src[src.find("def push_summary"):]
        assert "temu_presale.json" not in seg, (
            "还在读个人 open_id 当默认收件人 —— 默认应该是机器人群")

    def test_daily_list_drops_markdown_from_its_text(self):
        """群消息不解析 markdown，正文里不能留 `**`。"""
        src = self._src("scripts/goods/daily_list.py")
        seg = src[src.find("def push_summary"):]
        body = seg[seg.find("lines = ["):seg.find("text = \"\\n\".join(lines)")]
        assert "**" not in body, "群消息正文里还有 ** 粗体标记"

    def test_explicit_receive_still_works(self):
        """保留"临时发个人"的口子：显式 --receive 时仍走应用机器人。"""
        src = self._src("scripts/goods/daily_list.py")
        seg = src[src.find("def push_summary"):]
        assert "if receive:" in seg, "把显式收件人的口子也删掉了"
        assert "FB.send_text(receive" in seg


class TestTheTwoChannelsStaySeparate:
    """别把群机器人当成应用机器人的替代品 —— 两者能力不同，都留着。"""

    def test_app_api_helper_is_untouched(self):
        p = PROJ / "scripts" / "power" / "lib" / "feishu_bitable.py"
        src = p.read_text(encoding="utf-8")
        assert "def send_text(" in src
        assert "im/v1/messages" in src, "应用机器人通道被误删（多维表格读写还要用它）"

    def test_webhook_helper_is_untouched(self):
        src = (PROJ / "scripts" / "notify_push.py").read_text(encoding="utf-8")
        assert "def push_feishu(" in src
        assert "bot/v2/hook" not in src, "webhook 地址不该硬编码进代码（走环境变量）"

    def test_group_feishu_still_uses_the_proxy(self):
        """飞书在墙外：群通道必须尊重 HTTP(S)_PROXY，不能像 MeoW 那样清空。"""
        src = (PROJ / "scripts" / "notify_push.py").read_text(encoding="utf-8")
        seg = src[src.find("def push_feishu"):src.find("#: 群机器人纯文本")]
        assert "ProxyHandler({})" not in seg, "群通道清空了代理 → 会连不上飞书"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
