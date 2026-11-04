#!/usr/bin/env python3
"""钉住「BH-2 库存提醒」这条一次性任务。

用户 2026-11-04
---------------
> 这三张图中 BH-2 开头的产品，你在 12 月 23 日设个定时任务在那天的 10 点
> 推送到我飞书的 temu 群（以后默认推送这个机器群，那个飞书应用作为 dsh 跟飞书的桥接）

两件事必须分开钉住：
  ① **数据是快照**（用户明确选了"就按三张图上的数字发"），而且必须说明
     它是哪天的数字 —— 12/23 推 11/04 的库存，不说清楚就是误导。
  ② **一次性**。写成 daily 的话会天天往群里推同一批过期数字。

跑法：
    pytest scripts/tests/test_bh2_snapshot.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))

import push_bh2_snapshot as P  # noqa: E402

REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
SNAP = PROJ / "scripts" / "payloads" / "bh2_stock_snapshot.json"
TASK = "TEMU-BH2-Stock-Notice"

#: 用户三张截图里的原始数字 —— 一个都不能错
EXPECTED = {
    "TM-BH-202102-5SetA-S": 59,
    "TM-BH-T3033-3BHL-XS": 49,
    "TM-BH-T3033-3BHL-S": 50,
    "TM-BH-2072-4SetA-S": 0,
    "TM-BH-2072-4SetA-XL": 70,
    "TM-BH-207213-3SetA-M": 8,
    "TM-BH-207213-3SetA-L": 9,
    "TM-BH-207213-3SetA-XL": 1,
}


def _snap() -> dict:
    return json.loads(SNAP.read_text(encoding="utf-8-sig"))


def _entry() -> dict:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    for t in reg["tasks"]:
        if t["name"] == TASK:
            return t
    raise AssertionError("注册表里没有 %s" % TASK)


# ---------------------------------------------------------------- 数据
class TestTheNumbersMatchTheScreenshots:
    def test_every_sku_and_number_is_exact(self):
        got = {g["sku"]: int(g["库存"]) for g in _snap()["商品"]}
        assert got == EXPECTED, "快照和图上的数字对不上"

    def test_all_are_the_bh2_family(self):
        for g in _snap()["商品"]:
            assert g["sku"].startswith("TM-BH-"), "混进了非 BH 的 SKU：%s" % g["sku"]

    def test_snapshot_is_tracked_by_git(self):
        """数据放 `scripts/data/` 会被 .gitignore 吃掉，换台机器任务就没数据了。"""
        assert "payloads" in SNAP.as_posix(), "快照不该放在 scripts/data/（被 gitignore 覆盖）"
        import subprocess
        p = subprocess.run(["git", "check-ignore", "-q", str(SNAP)], cwd=str(PROJ))
        assert p.returncode != 0, "快照文件被 gitignore 覆盖了"

    def test_records_when_the_screenshot_was_taken(self):
        """12/23 推的是 11/04 的数字，必须能说清"这是哪天的"。"""
        assert _snap().get("截图日期"), "没记截图日期，几天后就没人知道这数多旧了"


# ---------------------------------------------------------------- 消息
class TestMessageIsHonestAboutBeingASnapshot:
    def _msg(self):
        return P.build_message(_snap(), "2026-12-23")

    def test_says_it_is_a_snapshot_not_live(self):
        m = self._msg()
        assert "快照" in m
        assert "不是当天实时" in m, "必须明说这不是实时数据，否则会被当成今天的库存"

    def test_uses_the_screenshot_date_not_the_push_date(self):
        m = self._msg()
        assert "2026-11-04" in m, "没写截图日期"
        assert "2026-12-23 的截图" not in m, (
            "把推送日当成截图日，等于说这是当天查的")

    def test_zero_stock_is_called_out(self):
        m = self._msg()
        assert "断货" in m
        assert "TM-BH-2072-4SetA-S" in m

    def test_no_markdown(self):
        """群机器人的 text 消息不解析 markdown。"""
        assert "**" not in self._msg()

    def test_groups_by_style(self):
        """8 个尺码平铺很难读，按款号分组才看得出结构。"""
        m = self._msg()
        for style in ("TM-BH-202102-5SetA", "TM-BH-T3033-3BHL",
                      "TM-BH-2072-4SetA", "TM-BH-207213-3SetA"):
            assert style in m

    def test_every_number_appears(self):
        m = self._msg()
        for sku, n in EXPECTED.items():
            style, size = sku.rsplit("-", 1)
            assert size in m, "少了尺码 %s" % size
        assert "59" in m and "70" in m and "49" in m and "50" in m


# ---------------------------------------------------------------- 通道
class TestItGoesToTheGroup:
    def test_uses_push_group_not_the_app_api(self):
        src = (PROJ / "scripts" / "push_bh2_snapshot.py").read_text(encoding="utf-8")
        assert "push_group(" in src, "没走机器人群"
        assert "send_text(" not in src, "还在用应用机器人私聊"
        assert "im/v1/messages" not in src

    def test_fails_loudly_if_the_group_channel_is_missing(self):
        """群通道坏了不能静默成功，更不能退化成私聊。"""
        src = (PROJ / "scripts" / "push_bh2_snapshot.py").read_text(encoding="utf-8")
        assert "不会退化成私聊" in src

    def test_empty_snapshot_is_not_pushed(self):
        """空清单别发一条"共 0 个尺码"进群。"""
        src = (PROJ / "scripts" / "push_bh2_snapshot.py").read_text(encoding="utf-8")
        assert "不发空消息" in src


# ---------------------------------------------------------------- 排程
class TestItRunsExactlyOnceOnDec23:
    def test_schedule_is_once_not_daily(self):
        s = _entry()["schedule"]
        assert "once" in s, "写成 daily 会天天推同一批过期数字"
        assert "daily" not in s and "weekly" not in s

    def test_fires_on_dec_23_at_10(self):
        at = _entry()["schedule"]["once"]["at"]
        assert at == "2026-12-23 10:00", "时间和用户要求不符：%r" % at

    def test_is_managed_and_enabled(self):
        e = _entry()
        assert e.get("managed") is True, "managed=false 的条目 manage.py 不会创建"
        assert e.get("enabled") is True

    def test_does_not_occupy_a_ziniao_window(self):
        """纯推送，不该进窗口冲突矩阵。"""
        assert _entry().get("紫鸟窗口") == ""

    def test_does_not_trigger_a_panel_publish(self):
        assert _entry().get("刷新面板") is False

    def test_does_not_auto_retry(self):
        """一次性提醒重跑＝往群里重复发同一条。"""
        assert _entry().get("自动重试") is False


class TestLiveRegistration:
    """真的在 Windows 计划任务里存在，且下次运行时间对得上。"""

    def _live(self):
        import subprocess
        ps = ("$t=Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue;"
              "if(-not $t){'MISSING';exit};"
              "$i=Get-ScheduledTaskInfo -TaskName '%s';"
              "$o=[PSCustomObject]@{"
              "kind=[string]$t.Triggers[0].CimClass.CimClassName;"
              "start=([datetime]$t.Triggers[0].StartBoundary).ToString('yyyy-MM-dd HH:mm');"
              "next=$(if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}else{''});"
              "state=[string]$t.State;"
              "action=(($t.Actions | ForEach-Object { $_.Arguments }) -join ' ')};"
              "$o|ConvertTo-Json -Compress" % (TASK, TASK))
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90)
        return json.loads((p.stdout or "{}").strip() or "{}")

    def test_task_exists(self):
        live = self._live()
        assert live and live.get("state"), "计划任务不存在（manage.py apply 没跑？）"

    def test_live_trigger_is_a_one_shot_time_trigger(self):
        live = self._live()
        assert "TimeTrigger" in (live.get("kind") or ""), (
            "实际触发器不是 TimeTrigger（被写成每天/每周了？）：%s" % live.get("kind"))

    def test_live_next_run_is_dec_23_10_00(self):
        live = self._live()
        assert live.get("next", "").startswith("2026-12-23 10:00"), (
            "下次运行 %r，应为 2026-12-23 10:00" % live.get("next"))

    def test_the_launcher_path_in_the_real_task_actually_exists(self):
        """★ 2026-11-04 实测踩到：注册表里把 `D:\\AI-workplace\\TEMU` 写成了
        `C:\\AI-workplace\\TEMU`，于是**每次由计划任务触发**都死在
        「The system cannot find the path specified」(rc=1)，日志一个字都不写；
        而我手工用相对路径跑却一直是通的 —— 两条路径不同源，就是这么被瞒过去的。

        ⇒ 钉住：实际任务 Action 里的 .cmd 路径必须真实存在。
        """
        import re
        live = self._live()
        args = live.get("action") or ""
        m = re.search(r'(?:cmd /c )?"?([A-Za-z]:\\[^"]*?\.cmd)"?\s*$', args, re.I)
        assert m, "任务 Action 里找不到 .cmd 路径：%r" % args
        cmd_path = m.group(1)
        assert Path(cmd_path).is_file(), (
            "任务实际执行的 .cmd 不存在：%s\n"
            "   → 十有八九是注册表里的盘符/目录写错了（手工相对路径跑通不代表任务能跑）"
            % cmd_path)

    def test_registry_and_live_agree_on_the_drive(self):
        """注册表写的路径也得是那个真实存在的（防止改了一边没改另一边）。"""
        e = _entry()
        args = (e.get("action") or {}).get("arguments") or ""
        assert "D:\\AI-workplace\\TEMU" in args, (
            "注册表 Action 里的项目路径不是 D:\\AI-workplace\\TEMU：%r" % args)
        assert "C:\\AI-workplace\\TEMU" not in args, "盘符写错成 C: 了"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
