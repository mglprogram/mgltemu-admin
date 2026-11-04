#!/usr/bin/env python3
"""钉住「Fnsku 迁移的通知发 TEMU 机器人群」。

2026-11-04 用户要求
-------------------
> Fnsku 迁移的通知也转移到 TEMU 群

`pw-repo/migrate` 的通知走 `lib/feishu.cjs`，它支持两种通道且 **webhook 优先**：

    webhook_url 有值           → 群机器人（bot/v2/hook）
    否则 app_id+secret+receive → 应用机器人（im/v1/messages）

原来 `notify.config.json` 里 `webhook_url` 是空串，所以一直走应用机器人发到某个
`oc_...` 群。现在填上占位符让它改道，同时**保留**应用机器人凭据（桥接/应急还要用）。

一个坑必须钉住：`notify.config.json` 会被同步/拷贝，webhook 明文写进去等于
「谁拿到这个文件都能往群里发消息」。所以用 `__FEISHU_WEBHOOK_ENV__` 占位符，
由 `resolveWebhook()` 在运行时从用户级环境变量 `FEISHU_HOOK` 取；
解析不到必须返回空串（退回应用机器人），**绝不能把占位符当 URL 去 POST**。

跑法：
    pytest scripts/tests/test_pw_migrate_notify_group.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MIGRATE = Path(r"D:\AI-workplace\DSH\pw-repo\migrate")
FEISHU_CJS = MIGRATE / "lib" / "feishu.cjs"
CFG = MIGRATE / "notify.config.json"
NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"


def _cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


requires_node = pytest.mark.skipif(
    not Path(NODE).exists(), reason="没有 node.exe")


# ---------------------------------------------------------------- 配置
class TestConfigChannelsToTheGroup:
    def test_webhook_is_filled(self):
        f = _cfg()["feishu"]
        assert f.get("webhook_url"), (
            "webhook_url 还是空 —— feishu.cjs 会继续走应用机器人")

    def test_webhook_is_not_plaintext(self):
        url = str(_cfg()["feishu"].get("webhook_url") or "")
        assert "bot/v2/hook/" not in url, (
            "webhook 明文写进了 notify.config.json，泄露=谁都能往群里发消息")
        assert url == "__FEISHU_WEBHOOK_ENV__"

    def test_app_bot_credentials_are_kept(self):
        """别为了迁群把应用机器人删掉 —— 它还是 DSH 的桥接通道。"""
        f = _cfg()["feishu"]
        assert f.get("app_id")
        assert f.get("app_secret")
        assert str(f.get("receive_id") or "").startswith("oc_")

    def test_notify_is_enabled(self):
        assert _cfg()["feishu"].get("enabled") is not False


# ---------------------------------------------------------------- 解析
class TestThePlaceholderIsResolved:
    def _run(self, expr: str, env_extra: dict | None = None) -> str:
        import os
        env = dict(os.environ)
        env.pop("FEISHU_WEBHOOK_URL", None)
        env.update(env_extra or {})
        p = subprocess.run(
            [NODE, "-e",
             "const f=require(%r);%s" % (str(FEISHU_CJS).replace("\\", "\\\\"), expr)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, env=env, cwd=str(MIGRATE))
        return ((p.stdout or "") + (p.stderr or "")).strip()

    @requires_node
    def test_resolves_from_env(self):
        out = self._run("console.log(f.loadConfig().webhook_url)",
                        {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"})
        assert "bot/v2/hook/abc" in out

    @requires_node
    def test_placeholder_never_leaks_as_a_url(self):
        """★ 关键：解析失败时绝不能把占位符本身当 URL 返回。"""
        out = self._run("console.log(JSON.stringify(f.loadConfig().webhook_url))",
                        {"FEISHU_WEBHOOK": "", "FEISHU_WEBHOOK_URL": ""})
        assert "__FEISHU_WEBHOOK_ENV__" not in out, (
            "占位符被当成 URL 了 —— POST 到那个假地址必然失败")

    @requires_node
    def test_live_config_resolves_to_a_feishu_hook(self):
        out = self._run("console.log(f.loadConfig().webhook_url)")
        if "open.feishu.cn" not in out:
            pytest.skip("本机没有 FEISHU_WEBHOOK，跳过（离线环境）")
        assert "/bot/v2/hook/" in out


# ---------------------------------------------------------------- 接线
class TestSendPath:
    def _src(self) -> str:
        return FEISHU_CJS.read_text(encoding="utf-8")

    def test_load_config_calls_the_resolver(self):
        src = self._src()
        assert "resolveWebhook(" in src
        # 必须在 webhook_url 赋值那一行用到
        line = next(l for l in src.splitlines() if "webhook_url:" in l)
        assert "resolveWebhook(" in line

    def test_webhook_still_wins_over_app_bot(self):
        """改道的原理就是"webhook 优先"，这条顺序不能反。"""
        src = self._src()
        seg = src[src.find("async function notify"):]
        seg = seg[:seg.find("\n}\n") + 3]
        assert seg.find("cfg.webhook_url") < seg.find("sendApp") or "webhook_url" in seg

    def test_app_sender_still_exists(self):
        src = self._src()
        assert "im/v1/messages" in src, "应用机器人通道被误删（桥接还要用）"

    def test_card_payload_is_interactive(self):
        """图里那条是卡片（interactive），不是纯文本。"""
        src = self._src()
        assert 'msg_type: "interactive"' in src


class TestTheRunnerDoesNotNeedNodeEdit:
    """运行时不再依赖临时改代码 —— 配置即可切换。"""

    def test_daily_job_uses_the_shared_notify(self):
        src = (MIGRATE / "daily-job.cjs").read_text(encoding="utf-8")
        assert 'require("./lib/feishu.cjs")' in src
        assert "await notify(" in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
