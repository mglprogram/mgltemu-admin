# -*- coding: utf-8 -*-
"""导出「自动化任务」静态数据 -> temu-admin/public/data/automation.json

前端页面：#/automation  首页卡片：dashboard

数据来源（三处合并，缺一不可）：
  1. DSH schedule     ~/.dsh/storages/schedule.json
                      全局唯一文件，但每条带 sessionId；schedule_list 工具只显示当前会话的。
  2. Windows 计划任务  真正无人值守干活的全在这（buy-bill / 帕沃尔Fnsku / 滞销库存跟踪等
                      都是用 schtasks 或 Register-ScheduledTask 建的）。
  3. 会话日志          157 个多帧 zstd 日志，还原「谁建的、建过什么」，
                      含已被删除的任务（如 Powerwarehouse-SKU-Migrate-4H）。

每条任务输出：
  status     ok / fail / never / running / disabled / waiting
  reason     失败或未执行的原因（人话）
  lastRun    上次执行时间
  nextRun    下次执行时间
  rc         原始返回码
  origin     创建它的会话标题

用法：
  python build_automation_data.py            # 导出
  python build_automation_data.py --print    # 导出并打印摘要
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT = ROOT / "temu-admin" / "public" / "data" / "automation.json"

DSH_HOME = Path.home() / ".dsh"
SCHEDULE_STORE = DSH_HOME / "storages" / "schedule.json"
SESSIONS_DIR = DSH_HOME / "sessions"
PROJCACHE = DSH_HOME / "storages" / "session_projcache" / "sessions"
WORKBUDDY_DB = Path.home() / ".workbuddy" / "workbuddy.db"

# 只纳入这些关键词相关的 Windows 任务（排除 OneDrive/WPS/HP/Realtek 等系统噪音）
INTEREST_KEYWORDS = (
    "temu", "bel", "bhy", "pw", "powerwarehouse", "power", "fnsku", "sku",
    "滞销", "slow", "stock", "shipping", "fund", "copilot", "workbuddy",
    "reminder", "folder", "invoice", "overdue", "abnormal", "限流", "底部", "bottom",
    "tk-", "bd-", "archive-cleanup", "automationpanel",
)
EXCLUDE_KEYWORDS = (
    "onedrive", "wps", "office", "doubao", "rtkaud", "hp ", "printer",
    "defender", "windows", "edge", "nvidia", "adobe",
)

# ---------------------------------------------------------------- 中文标注
# 任务名 -> (中文名称, 一句话说明)
# 面板上英文任务名对非技术视角不友好，这里统一补中文；未命中的回退到原名。
LABELS: dict[str, tuple[str, str]] = {
    # --- TEMU 店铺 ---
    "TEMU-BEL-Abnormal-Daily": ("BEL 异常出库单处理", "每天自动把合作仓异常单转卖家履约"),
    "TEMU-Overdue-Confirm-Daily": ("TEMU 逾期未发货确认", "每天手动确认即将逾期未发货的订单"),
    "TEMU限流监控": ("TEMU 热销款限流监控", "监控热销款是否被限流并比价"),
    "TEMU_Bottom_DailyStockSync": ("Bottom 重点款库存同步", "抓帕沃尔库存/销量并写入飞书多维表格"),
    # --- 帕沃尔海外仓 ---
    "Powerwarehouse-SKU-Migrate-Daily": ("帕沃尔 Fnsku 迁移", "按库存阈值把 Fnsku 迁到有货仓库"),
    "PWShipping-BHYBEL-Daily": ("BHY/BEL 海外仓下单代发", "每天给 BHY、BEL 店跑下单代发"),
    "PWShippingCheck_0910": ("海外仓发货预警检查", "检查发货是否失败并告警（已停用）"),
    # --- 提醒类 ---
    "DailyReminder0900": ("每日必做提醒（上午 9 点）", "弹出当日必做任务清单"),
    "DailyReminder1440": ("OPERATION 提醒（14:40）", "提醒执行 OPERATION 事项"),
    "DailyReminder1500": ("每日必做提醒（下午 3 点）", "弹出当日必做任务清单"),
    "DailyReminder1900": ("每日必做提醒（晚上 7 点）", "弹出当日必做任务清单"),
    "OneShot_FriInvoice_0904": ("恒信发票一次性提醒", "把肇庆恒信发票发到 LM739 再导回仓群"),
    "CreateDailyFolder_Lehang_0900": ("乐航当日文件夹", "自动建当天日期文件夹，便于开单归档"),
    # --- 基金 ---
    "FundCopilot_MorningBrief": ("基金早间简报", "每天 9:00 推送基金持仓简报"),
    "FundCopilot_MorningBrief_Watchdog": ("基金早间简报·看门狗", "早间简报没跑成时自动补跑"),
    "FundCopilot_CloseBrief": ("基金尾盘建议", "交易日 14:40 推送尾盘操作建议"),
    "FundCopilot_CloseBrief_Watchdog": ("基金尾盘建议·看门狗", "尾盘建议没跑成时自动补跑"),
    # --- TikTok ---
    "TK-BD-Daily-Followup": ("TikTok 达人跟进", "每天跟进达人建联进度（已停用）"),
    "TK-Daily-Intel-Brief": ("TikTok 每日情报简报", "每天汇总 TikTok 运营情报（已停用）"),
    # --- WorkBuddy ---
    "WorkBuddyAutoSignin": ("WorkBuddy 自动签到", "每天 0:05 自动签到"),
    "WorkBuddyGrowthPoll": ("WorkBuddy 成长值轮询", "每 4 小时轮询一次成长值"),
    # --- DSH 会话内 ---
    "OPERATION 提醒 14:40": ("OPERATION 提醒（DSH）", "DSH 会话内提醒，非无人值守"),
    "乐航当日文件夹 09:00": ("乐航当日文件夹（DSH）", "DSH 会话内建目录，非无人值守"),
    # --- WorkBuddy ---
    "每日必做任务提醒 - 早9:00": ("每日必做提醒·早 9 点", "上午优先处理，给出执行顺序建议"),
    "每日必做任务提醒 - 下午15:00": ("每日必做提醒·下午 3 点", "重点跟进上午未完成事项"),
    "每日必做任务提醒 - 晚19:00": ("每日必做提醒·晚 7 点", "当日收尾检查，核对未闭环事项"),
    "每日发货 09:00（帕沃尔四店全流程）": ("每日发货（帕沃尔四店）", "四店全流程下单发货，9:00 主跑批"),
    "每日发货补跑 13:00（判定后按需补跑）": ("每日发货·13 点补跑", "判定主跑批是否成功，按需补跑"),
    "每周一清理：箱唛PDF + 发票集发票（保留7天）": ("每周清理箱唛/发票", "清理超 7 天的箱唛 PDF 和发票"),
    "失败订单落金山文档（每日发货后）": ("失败订单落金山文档", "把当日失败订单写入金山文档"),
    "Codex 会话请求体体检": ("Codex 请求体体检", "检查会话请求体体积"),
    "BS 取跟踪号补跑（09-13~09-14 剩余 2 单）": ("BS 取跟踪号补跑", "补取遗留跟踪号（一次性）"),
    # --- 新迁入 DSH 的（原 WorkBuddy 提醒）---
    "每日必做提醒 · 早 9 点": ("每日必做提醒·早 9 点（DSH）", "上午优先处理，给执行顺序建议"),
    "每日必做提醒 · 下午 3 点": ("每日必做提醒·下午 3 点（DSH）", "重点跟进上午未完成事项"),
    "每日必做提醒 · 晚 7 点": ("每日必做提醒·晚 7 点（DSH）", "当日收尾检查，核对未闭环事项"),
    # --- 新建的 Windows 任务 ---
    "Archive-Cleanup-Weekly": ("每周清理箱唛/发票", "清理超 7 天的箱唛 PDF 和发票（移动式，可回滚）"),
    "AutomationPanel-Refresh": ("自动化面板刷新", "每天 3 次刷新面板数据并发布"),
    "TEMU流量加速周更": ("TEMU 流量加速周更", "每周五抓取 BEL/BHY 流量加速状态并更新面板"),
    "TEMU-Presale-Notify": ("TEMU 预售/补货提醒", "刷新预售数据并推飞书提醒"),
    "TEMU-TaskWatch-30min": ("定时任务巡检", "每 30 分钟巡检业务任务，异常推飞书"),
}


def _load_task_registry() -> dict:
    """读「定时任务」注册表（skill: scheduled-tasks）里的中文名/说明，作为 LABELS 的上层覆盖。

    注册表是定时任务的事实来源（谁在跑、几点跑、干什么都在那），面板只做展示 ——
    所以名字/说明以注册表为准，不在注册表里的才回退到本文件的 LABELS。
    """
    p = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
    try:
        reg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for t in reg.get("tasks") or []:
        name = t.get("name")
        if name and (t.get("label") or t.get("desc")):
            out[name] = (t.get("label") or name, t.get("desc") or "")
    return out


REGISTRY_LABELS = _load_task_registry()


def _load_registry_field(field: str) -> dict:
    """从注册表读某个字段 → {任务名: 值}（只收有该字段的条目）。"""
    p = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
    try:
        reg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for t in reg.get("tasks") or []:
        n = t.get("name")
        if n and field in t:
            out[n] = t[field]
    return out


#: 任务占用的紫鸟店铺窗口（"" = 已核实不占窗口；不在表里 = 未核实）
REGISTRY_WINDOWS = _load_registry_field("紫鸟窗口")

#: 注册表声明的超时上限（分钟）—— 算「最坏占用」用
def _load_timeouts() -> dict:
    p = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
    try:
        reg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    return {t["name"]: t.get("timeout_minutes")
            for t in (reg.get("tasks") or []) if t.get("timeout_minutes")}


REGISTRY_TIMEOUTS = _load_timeouts()

#: 实测典型耗时（分钟）—— 用来区分「平时会不会撞」和「卡住时会不会压到下一个」
REGISTRY_TYPICAL = _load_registry_field("实测耗时分钟")


def label_for(name: str) -> tuple[str, str]:
    """返回 (中文名, 说明)；注册表优先，其次本文件的 LABELS，最后回退原名。"""
    if name in REGISTRY_LABELS:
        return REGISTRY_LABELS[name]
    if name in LABELS:
        return LABELS[name]
    # 模糊兜底：DSH 标题带时间后缀
    for k, v in {**LABELS, **REGISTRY_LABELS}.items():
        if k and name.startswith(k):
            return v
    return name, ""



RC_MAP = {
    0: ("ok", "执行成功"),
    267009: ("running", "正在运行中"),
    267010: ("disabled", "任务已被禁用"),
    267011: ("never", "从未运行过"),
    267014: ("waiting", "被中止：提醒窗口还开着等你按回车（非故障）"),
    3221225786: ("waiting", "被 Ctrl+C / 窗口关闭中断（非故障）"),
    3221225477: ("fail", "程序崩溃（访问冲突 0xC0000005）"),
    # ★ 5 = 紫鸟窗口互斥锁没拿到（别的任务正占着同一扇店窗口）。
    #   这是**按设计的跳过**，不是故障：这些任务都是"处理当前状态"而不是"处理今天新增的"，
    #   漏一次下一班会自然补齐。归到 waiting（不计入 summary.problem）。
    #   2026-10-15 立：当天冷启动后 StartWhenAvailable 把早高峰 6 个任务同时拉起，
    #   两个抢不到窗口的任务报 rc=5，被面板/health/飞书当成"失败"，报了假告警。
    #   ⚠ 注册表的 ok_results 也要同步放行 5（三方口径一致，见 SKILL）。
    5: ("waiting", "紫鸟窗口被其它任务占用，本次按设计跳过（非故障，下一班次会补）"),
    # ★ 6 = publish_admin 没抢到发布锁（已有发布在跑）。同样**不是故障**：
    #   正在跑的那一轮会重建**同一份**数据（含 goods.json / automation.json），
    #   所以本次的数据照样会上线。2026-10-15 立（当天冷启动后 3 个发布进程重叠）。
    6: ("waiting", "已有发布在跑，本次并入那一轮（非故障，数据由那一轮统一上线）"),
    1: ("fail", "脚本返回 1：执行过程中报错，需看日志"),
    2: ("fail", "脚本返回 2：执行过程中报错，需看日志"),
}


def log(m: str) -> None:
    print(m, flush=True)


# ---------------------------------------------------------------- 来源 1：DSH
def load_dsh() -> list[dict]:
    if not SCHEDULE_STORE.exists():
        return []
    try:
        data = json.loads(SCHEDULE_STORE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] schedule.json 解析失败: {e}")
        return []
    rows = []
    for tid, row in ((data.get("tables") or {}).get("tasks") or {}).items():
        rec = row.get("record") or {}
        hist = (row.get("deliveryHistory") or {}).get("records") or []
        last = hist[-1] if hist else None
        rows.append({
            "id": tid,
            "name": rec.get("title") or "(无标题)",
            "source": "DSH",
            "kind": rec.get("kind"),
            "rule": _rule_text(rec),
            "timezone": rec.get("timeZone") or "Asia/Shanghai",
            "scheduledAt": rec.get("scheduledAt"),
            "deliveries": len(hist),
            "lastDelivery": (last or {}).get("deliveredAt"),
            "sessionId": row.get("sessionId"),
            "state": row.get("status"),
        })
    return rows


def _rule_text(rec: dict) -> str:
    k = rec.get("kind")
    if k == "daily":
        return f"每天 {rec.get('time', '')}"
    if k == "weekly":
        wd = rec.get("weekdays") or []
        names = "一二三四五六日"
        s = "、".join(f"周{names[d - 1]}" for d in wd if 1 <= d <= 7)
        return f"每{s} {rec.get('time', '')}"
    if k == "every":
        sec = rec.get("everySeconds")
        return f"每 {sec} 秒" if sec else "固定间隔"
    if k == "after":
        return f"{rec.get('afterSeconds')} 秒后（一次性）"
    if k == "cron":
        return f"cron: {rec.get('expression', '')}"
    return k or "-"


# ---------------------------------------------------------------- 来源 2：Windows
PS_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
# 关键：把输出强制成 UTF-8，并且把非 ASCII 字符转义成 \uXXXX。
# 否则在 cmd.exe / 计划任务上下文里，PowerShell 用 OEM 代码页(GBK)输出，
# 中文任务名会在传输途中变成 U+FFFD 替换字符（面板上显示成 ????）。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out = New-Object System.Collections.ArrayList
foreach ($t in (Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\' })) {
  $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction SilentlyContinue
  $last = ''
  $next = ''
  $rc = $null
  if ($i) {
    if ($i.LastRunTime) { try { $last = $i.LastRunTime.ToString('o') } catch {} }
    if ($i.NextRunTime) { try { $next = $i.NextRunTime.ToString('o') } catch {} }
    $rc = $i.LastTaskResult
  }
  $trig = @()
  $trigKind = @()
  foreach ($tg in $t.Triggers) {
    $sb = $null
    try { $sb = $tg.StartBoundary } catch {}
    if (-not $sb) {
      try {
        $cim = $tg.CimInstanceProperties | Where-Object { $_.Name -eq 'StartBoundary' }
        if ($cim) { $sb = $cim.Value }
      } catch {}
    }
    if ($sb) {
      if ($sb -is [datetime]) { $trig += $sb.ToString('o') } else { $trig += [string]$sb }
      # 记录触发器类型 + 星期（周期信息不在 StartBoundary 里）
      $cls = ''
      try { $cls = [string]$tg.CimClass.CimClassName } catch {}
      $dow = ''
      try {
        if ($tg.DaysOfWeek) { $dow = [string]$tg.DaysOfWeek }
      } catch {}
      $trigKind += ("$cls|$dow")
    }
  }
  $acts = @()
  foreach ($ac in $t.Actions) {
    $s = ''
    if ($ac.Execute) { $s = $ac.Execute }
    if ($ac.Arguments) { $s = ($s + ' ' + $ac.Arguments).Trim() }
    $acts += $s
  }
  [void]$out.Add([PSCustomObject]@{
    Name     = [string]$t.TaskName
    State    = [string]$t.State
    Trigger  = ($trig -join ' ;; ')
    TrigKind = ($trigKind -join ' ;; ')
    Last     = $last
    Next     = $next
    RC       = $rc
    Action   = ($acts -join ' ;; ')
    Timeout  = [string]$t.Settings.ExecutionTimeLimit
  })
}
# ConvertTo-Json 会把中文原样输出；这里再手工转义一遍，确保纯 ASCII 传输
$json = ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
[regex]::Replace($json, '[^\x00-\x7F]', {
  param($m)
  $c = [int][char]$m.Value
  if ($c -gt 0xFFFF) {
    $c -= 0x10000
    $hi = 0xD800 + ($c -shr 10)
    $lo = 0xDC00 + ($c -band 0x3FF)
    '\u{0:x4}\u{1:x4}' -f $hi, $lo
  } else {
    '\u{0:x4}' -f $c
  }
})
"""


def load_windows() -> list[dict]:
    env = dict(os.environ)
    # 双保险：PowerShell 自己按 UTF-8 输出
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="strict",
            timeout=180, env=env,
        )
    except UnicodeDecodeError as e:
        log(f"  [warn] PowerShell 输出解码失败（编码问题）: {e}")
        # 退回宽松模式，至少不让整个导出失败
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, env=env,
        )
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] 读取 Windows 计划任务失败: {e}")
        return []
    txt = (p.stdout or "").strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] 解析计划任务 JSON 失败: {e}")
        return []
    if isinstance(data, dict):
        data = [data]
    # 自检：出现 U+FFFD 说明中文名已在传输中损坏
    bad = [w.get("Name") for w in data if "\ufffd" in (w.get("Name") or "")]
    if bad:
        log(f"  [warn] {len(bad)} 个任务名含替换字符，编码仍有问题: {bad[:3]}")
    return data


def relevant(name: str) -> bool:
    low = (name or "").lower()
    if any(k in low for k in EXCLUDE_KEYWORDS):
        return False
    return any(k.lower() in low for k in INTEREST_KEYWORDS)


# ---------------------------------------------------------------- 来源 2b：WorkBuddy
def load_workbuddy() -> list[dict]:
    """WorkBuddy 自带调度器（workbuddy.db 的 automations 表）。

    这是和 DSH / Windows 都不同的第三套调度系统，容易漏查。
    """
    if not WORKBUDDY_DB.exists():
        return []
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{WORKBUDDY_DB}?mode=ro", uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, name, status, schedule_type, next_run_at, last_run_at, "
            "rrule, scheduled_at, cwds, push_to_wechat, push_to_wecom_bot, deleted_at "
            "FROM automations WHERE deleted_at IS NULL ORDER BY created_at"
        ).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] 读取 WorkBuddy automations 失败: {e}")
        return []
    return [dict(r) for r in rows]


def wb_rule(rrule: str, schedule_type: str, scheduled_at: str) -> str:
    """RRULE -> 人话。"""
    if (schedule_type or "") == "once":
        return f"一次性 {scheduled_at or ''}"[:28]
    p = {}
    for seg in (rrule or "").split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            p[k.strip().upper()] = v.strip()
    freq = p.get("FREQ", "").upper()
    h, m = p.get("BYHOUR"), p.get("BYMINUTE")
    hhmm = f"{int(h):02d}:{int(m):02d}" if h and m else ""
    if freq == "DAILY":
        return f"每天 {hhmm}"
    if freq == "HOURLY":
        return f"每 {p.get('INTERVAL', '1')} 小时"
    if freq == "WEEKLY":
        wd = {"MO": "一", "TU": "二", "WE": "三", "TH": "四", "FR": "五", "SA": "六", "SU": "日"}
        d = wd.get(p.get("BYDAY", "").upper(), "?")
        return f"每周{d} {hhmm}"
    return rrule or (schedule_type or "-")



def extract_times(rule: str) -> list[str]:
    """从人话规则里抽出所有 HH:MM（时间轴/冲突检测用）。

    例："每天 08:00 / 12:35 / 18:35" -> ["08:00","12:35","18:35"]
        "每周一、二、三、四、五 12:30 / 18:30" -> ["12:30","18:30"]
    抽不到返回 []（该任务不进时间轴，但仍留在列表里）。
    """
    return sorted({f"{m.group(1)}:{m.group(2)}"
                   for m in re.finditer(r"(\d{1,2}):(\d{2})", rule or "")})


def extract_weekdays(rule: str) -> list[int]:
    """从规则里抽出适用的星期（1=周一 … 7=周日）；每天/抽不到返回 []（=每天都算）。

    认两种写法：
      "每周一、二、三、四、五 14:40"  -> [1,2,3,4,5]
      "每周五 06:00"                  -> [5]
    """
    WD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    m = re.search(r"每周([一二三四五六日、和及]+)", rule or "")
    if not m:
        return []
    out = []
    for ch in m.group(1):
        if ch in WD and WD[ch] not in out:
            out.append(WD[ch])
    return sorted(out)


def _dec(path: Path) -> str:
    import zstandard
    out = bytearray()
    with path.open("rb") as fh:
        r = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        while True:
            c = r.read(1 << 20)
            if not c:
                break
            out.extend(c)
    return out.decode("utf-8", errors="replace")


def live_titles() -> dict[str, str]:
    out = {}
    if not PROJCACHE.exists():
        return out
    for f in PROJCACHE.glob("*.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
            out[f.stem] = (((j.get("record") or {}).get("rows") or {}).get("title") or {}).get("val")
        except Exception:  # noqa: BLE001
            pass
    return out


def build_origin() -> dict[str, set[str]]:
    """任务名 -> 创建它的会话标题集合。"""
    titles = live_titles()
    origin: dict[str, set[str]] = {}
    if not SESSIONS_DIR.exists():
        return origin
    for zf in sorted(SESSIONS_DIR.rglob("*.zstd")):
        try:
            text = _dec(zf)
        except Exception:  # noqa: BLE001
            continue
        if "schtasks" not in text and "New-ScheduledTask" not in text:
            continue
        who = titles.get(zf.parent.name) or zf.parent.name[:24]
        for pat in (r'/TN\s+"?([A-Za-z0-9\u4e00-\u9fff\-_.]+)"?',
                    r'-TaskName\s+"?([A-Za-z0-9\u4e00-\u9fff\-_.]+)"?'):
            for m in re.finditer(pat, text):
                origin.setdefault(m.group(1), set()).add(who)
    return origin


# ---------------------------------------------------------------- 组装
def human_reason(status: str, rc, state: str, last: str, act: str) -> str:
    if status == "ok":
        return "上次执行成功"
    if status == "running":
        return "正在运行中，尚未结束"
    if status == "disabled":
        return "任务已被禁用，不会自动触发（如需恢复：Enable-ScheduledTask）"
    if status == "never":
        return "从未运行过（刚建好 / 触发时间还没到 / 历史记录被清）"
    if status == "waiting":
        return RC_MAP.get(rc, ("", "等待被中止"))[1]
    if status == "fail":
        low = (act or "").lower()
        if "fund" in low:
            hint = "常见原因：基金数据接口报错（akshare/腾讯行情），查 skill 日志"
        elif "run_temu_watch" in low or "限流" in low:
            hint = "常见原因：TEMU 登录态失效 / 页面选择器变化，查 _logs"
        elif "overdue" in low or "bel_abnormal" in low:
            hint = "常见原因：TEMU 登录态失效 / 待处理单为空导致脚本报错，查 _logs"
        elif "shipping" in low or "bhy" in low or "pw" in low:
            hint = "常见原因：帕沃尔 cookie 过期，查 ALERT.txt / logs"
        else:
            hint = "需看脚本日志确认"
        return f"{RC_MAP.get(rc, (None, f'返回码 {rc}'))[1]}；{hint}"
    return "未知状态"


# ---------------------------------------------------------------- 任务专属返回码语义
# 同一个返回码在不同任务里含义不同，默认的「非零即失败」会把业务告警误报成故障。
# 这里按任务名覆盖；未列出的任务继续用 RC_MAP 的默认语义。
TASK_RC_SEMANTICS: dict[str, dict[int, tuple[str, str]]] = {
    # watch_focus.py 的退出码是「告警等级」，不是「成功/失败」：
    #   0 全部重点款安全 / 1 有重点款被限流 / 2 全部店铺抓取失败 / 3 部分失败
    # 见 run_temu_watch.cmd 末尾的 [NOTE] 说明与 watch_focus.py 文档串。
    "TEMU限流监控": {
        0: ("ok", "全部重点款未被限流"),
        1: ("alert", "有重点款被限流，可按提示降价处理（业务告警，脚本本身正常）"),
        2: ("fail", "所有店铺都抓取失败：多为店铺窗口未打开或未登录，查 _logs/temu-watch"),
        3: ("warn", "部分店铺抓取失败，其余店铺结果仍有效"),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="show", action="store_true")
    a = ap.parse_args()

    log("导出自动化任务数据 ...")
    origin = build_origin()
    log(f"  会话来源映射：{len(origin)} 个任务名")

    tasks: list[dict] = []

    # --- DSH ---
    dsh = load_dsh()
    for r in dsh:
        # DSH 的 deliveryHistory 只记「投递」，不记「模型是否执行」
        delivered = r["deliveries"] > 0
        cn, desc = label_for(r["name"])
        tasks.append({
            "name": r["name"],
            "label": cn,
            "desc": desc,
            "source": "DSH",
            "rule": r["rule"],
            "timezone": r["timezone"],
            "enabled": r["state"] == "active",
            "lastRun": r["lastDelivery"],
            "nextRun": r["scheduledAt"],
            "rc": None,
            "status": "ok" if delivered else "never",
            "reason": ("已投递到会话 %d 次（DSH 只记投递，不记模型是否真的执行）" % r["deliveries"])
                      if delivered else "尚未触发过投递",
            "action": "DSH 会话内提醒",
            "session": r["sessionId"],
            "origin": "当前会话",
            "note": "DSH 定时任务按会话隔离，且只在会话存活时触发",
        })

    # --- Windows ---
    win = [w for w in load_windows() if relevant(w.get("Name", ""))]
    for w in win:
        name = w.get("Name") or ""
        state = w.get("State") or ""
        rc = w.get("RC")
        last = w.get("Last") or ""
        status, _ = RC_MAP.get(rc, ("fail", f"返回码 {rc}"))
        # 任务专属语义优先（如限流监控的 rc=1 是告警而非失败）
        reason_override = None
        per = TASK_RC_SEMANTICS.get(name)
        if per and rc in per:
            status, reason_override = per[rc]
        if state == "Disabled":
            status = "disabled"
        elif rc in (None, "") :
            status = "never"
        elif isinstance(last, str) and last.startswith("0001-01-01"):
            status = "never"

        who = origin.get(name)
        cn, desc = label_for(name)
        tasks.append({
            "name": name,
            "label": cn,
            "desc": desc,
            "source": "Windows",
            "rule": _win_rule(w),
            "timezone": "Asia/Shanghai",
            "enabled": state != "Disabled",
            "lastRun": last,
            "nextRun": w.get("Next") or "",
            "rc": rc,
            "status": status,
            "reason": reason_override or human_reason(status, rc, state, last, w.get("Action")),
            "action": (w.get("Action") or "").strip(),
            # 原始 ExecutionTimeLimit（PT30M 之类），下面统一解析成 timeoutMinutes
            "_timeoutRaw": (w.get("Timeout") or "").strip(),
            "session": None,
            "origin": "、".join(sorted(who)) if who else "",
            "note": "",
        })

    # --- WorkBuddy ---
    wb = load_workbuddy()
    for w in wb:
        cn, desc = label_for(w.get("name") or "")
        status_raw = (w.get("status") or "").upper()
        nra = w.get("next_run_at")
        lra = w.get("last_run_at")
        if status_raw == "PAUSED":
            st = "disabled"
        elif not lra:
            st = "never"
        else:
            st = "ok"
        pushers = []
        if w.get("push_to_wechat"):
            pushers.append("微信")
        if w.get("push_to_wecom_bot"):
            pushers.append("企微机器人")
        note = "WorkBuddy 调度器"
        if pushers:
            note += "，推送到 " + "/".join(pushers)
        tasks.append({
            "name": w.get("name") or "",
            "label": cn,
            "desc": desc,
            "source": "WorkBuddy",
            "rule": wb_rule(w.get("rrule") or "", w.get("schedule_type") or "",
                            w.get("scheduled_at") or ""),
            "timezone": "Asia/Shanghai",
            "enabled": status_raw == "ACTIVE",
            "lastRun": (datetime.fromtimestamp(lra / 1000).isoformat() if lra else None),
            "nextRun": (datetime.fromtimestamp(nra / 1000).isoformat() if nra else None),
            "rc": None,
            "status": st,
            "reason": ("已暂停，不会自动触发（在 WorkBuddy 里恢复）" if st == "disabled"
                       else "尚无执行记录" if st == "never"
                       else "上次执行成功"),
            "action": (w.get("cwds") or "").strip("[]").replace('"', ""),
            "session": None,
            "origin": "WorkBuddy",
            "note": note,
        })

    # --- 统一补充时间轴字段 + 统计 ---
    # 时间轴/冲突检测都靠 times[]；在这里集中算，避免三个来源各写一遍。
    for t in tasks:
        times = extract_times(t.get("rule") or "")
        t["times"] = times
        t["weekdays"] = extract_weekdays(t.get("rule") or "")
        # 占用的紫鸟店铺窗口（注册表声明）
        win = REGISTRY_WINDOWS.get(t["name"], "")
        t["browserWindow"] = win            # "" = 不占窗口
        t["windowVerified"] = t["name"] in REGISTRY_WINDOWS
        # 超时上限（分钟）：注册表优先，其次从 Windows 的 ExecutionTimeLimit 解析
        to = REGISTRY_TIMEOUTS.get(t["name"])
        if not to:
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", t.get("_timeoutRaw") or "")
            if m:
                to = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
        t["timeoutMinutes"] = to or None
        t["typicalMinutes"] = REGISTRY_TYPICAL.get(t["name"])

    def cnt(s: str) -> int:
        return sum(1 for t in tasks if t["status"] == s)

    def _stores(spec: str) -> set:
        return {x for x in (spec or "").replace(" ", "").split("+") if x}

    # 只把「真的占紫鸟窗口」的任务算进来 —— 同一时刻的另一个任务若走 HTTP/文件，
    # 根本不开浏览器，不算冲突（2026-10-14：原先只按时刻判，5 个冲突全是误报）。
    browsers = [t for t in tasks
                if t.get("browserWindow") and t.get("enabled") and t["status"] != "disabled"]

    # ---- 冲突 1：同一时刻、且**店铺窗口有交集** ----
    slot: dict[str, list[dict]] = {}
    for t in browsers:
        for hhmm in t["times"]:
            slot.setdefault(hhmm, []).append(t)

    conflicts = []
    for hhmm, items in sorted(slot.items()):
        if len(items) < 2:
            continue
        # 星期有交集才算（每天都算）
        wd_sets = [set(x["weekdays"]) or {1, 2, 3, 4, 5, 6, 7} for x in items]
        common = set.intersection(*wd_sets) if wd_sets else set()
        if not common:
            continue
        # 两两看店铺窗口是否真的有交集
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                shared = _stores(a["browserWindow"]) & _stores(b["browserWindow"])
                if shared:
                    pairs.append({
                        "a": a["name"], "b": b["name"],
                        "stores": sorted(shared),
                    })
        if not pairs:
            continue        # 同刻但店铺不同 → 各用各的窗口，不冲突
        conflicts.append({
            "time": hhmm,
            "count": len(items),
            "tasks": [x["name"] for x in items],
            "weekdays": sorted(common),
            "risk": "抢同一个紫鸟店铺窗口",
            "pairs": pairs,
        })

    # ---- 冲突 2：执行时段重叠 ----
    # 两个口径都要算，否则会把风险说反：
    #   * 典型耗时 → 平时会不会撞（实测都是几十秒~2.5 分钟，通常错得很开）
    #   * 超时上限 → 任务卡住时会不会压到下一个（有 30~240 分钟，这才是真风险）
    tight = []
    for wd in range(1, 8):        # 逐个星期判，避免周任务造成误报
        day = [t for t in browsers
               if not t["weekdays"] or wd in t["weekdays"]]
        occ = []
        for t in day:
            for hhmm in t["times"]:
                h, m = hhmm.split(":")
                start = int(h) * 60 + int(m)
                occ.append((start, t, hhmm))
        occ.sort()
        for i in range(len(occ)):
            s1, t1, h1 = occ[i]
            typ1 = t1["typicalMinutes"] or 0
            worst1 = t1["timeoutMinutes"] or 0
            for j in range(i + 1, len(occ)):
                s2, t2, h2 = occ[j]
                if not worst1 or s2 >= s1 + worst1:
                    break                      # 最坏情况都够不着，后面的更够不着
                shared = _stores(t1["browserWindow"]) & _stores(t2["browserWindow"])
                if not shared:
                    continue
                rec = {
                    "a": t1["name"], "b": t2["name"],
                    "aTime": h1, "bTime": h2,
                    "gapMinutes": s2 - s1,              # 两次触发相隔
                    "worstOverlap": max(0, (s1 + worst1) - s2),
                    "typicalOverlap": max(0, (s1 + typ1) - s2),
                    "aTimeout": worst1, "aTypical": typ1,
                    "weekdays": [wd],
                    "stores": sorted(shared),
                }
                key = (rec["a"], rec["b"], rec["aTime"], rec["bTime"])
                hit = next((x for x in tight
                            if (x["a"], x["b"], x["aTime"], x["bTime"]) == key), None)
                if hit:
                    if wd not in hit["weekdays"]:
                        hit["weekdays"].append(wd)
                    hit["worstOverlap"] = max(hit["worstOverlap"], rec["worstOverlap"])
                    hit["typicalOverlap"] = max(hit["typicalOverlap"], rec["typicalOverlap"])
                else:
                    tight.append(rec)
    for x in tight:
        x["weekdays"].sort()
        # 典型就重叠 = 真问题；只有最坏才重叠 = 卡住时才会发生
        x["severity"] = "typical" if x["typicalOverlap"] > 0 else "timeout-only"
        x["risk"] = ("典型耗时就会重叠，需错开"
                     if x["typicalOverlap"] > 0
                     else "仅当上一个卡住（超时 %d 分）才会压到，典型只要 %d 分"
                          % (x["aTimeout"], x["aTypical"]))
    tight.sort(key=lambda x: (0 if x["severity"] == "typical" else 1, -x["worstOverlap"]))

    # 一天里被占用过的时刻（含单任务），用于算空闲时段
    busy = sorted({hhmm for t in tasks
                   if t.get("enabled") and t["status"] != "disabled"
                   for hhmm in t["times"]})

    summary = {
        "total": len(tasks),
        "ok": cnt("ok"),
        "fail": cnt("fail"),
        "alert": cnt("alert"),
        "warn": cnt("warn"),
        "never": cnt("never"),
        "running": cnt("running"),
        "disabled": cnt("disabled"),
        "waiting": cnt("waiting"),
        "conflictSlots": len(conflicts),
        "tightSlots": len(tight),
        "browserTasks": len(browsers),
    }
    # 「需关注」= 故障 + 未执行 + 业务告警 + 部分失败（都已禁用/等待中的不算）
    summary["problem"] = summary["fail"] + summary["never"] + summary["alert"] + summary["warn"]

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        # 时间轴/冲突检测用
        "conflicts": conflicts,
        "tight": tight,
        "busySlots": busy,
        "sources": [
            {"name": "Windows 计划任务", "desc": "无人值守真正在跑的任务",
             "count": sum(1 for t in tasks if t["source"] == "Windows")},
            {"name": "WorkBuddy", "desc": "自带调度器，可推微信/企微",
             "count": sum(1 for t in tasks if t["source"] == "WorkBuddy")},
            {"name": "DSH 会话提醒", "desc": "会话内的 AI 提醒，非无人值守",
             "count": sum(1 for t in tasks if t["source"] == "DSH")},
        ],
        "tasks": sorted(tasks, key=lambda t: (
            {"fail": 0, "never": 1, "running": 2, "waiting": 3, "disabled": 4, "ok": 5}.get(t["status"], 9),
            t["name"],
        )),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  automation.json  {OUT.stat().st_size / 1024:.1f} KB")

    if a.show:
        s = summary
        log(f"\n  共 {s['total']} 个任务：成功 {s['ok']} / 失败 {s['fail']} / "
            f"未执行 {s['never']} / 运行中 {s['running']} / 已禁用 {s['disabled']} / 等待 {s['waiting']}")
        for t in payload["tasks"]:
            if t["status"] in ("fail", "never", "disabled"):
                log(f"    [{t['status']:8s}] {t['name']}  — {t['reason']}")

    log("完成")
    return 0


def _win_rule(w: dict) -> str:
    """把触发时间戳转成「每天 08:30 / 12:30」或「每周一 08:00」这种人话。

    注意：StartBoundary 本身不含周期信息（每天/每周），周期在触发器类型里
    （MSFT_TaskWeeklyTrigger / MSFT_TaskDailyTrigger），所以要看 TrigKind。
    """
    tr = (w.get("Trigger") or "").replace("/Date(", "").replace(")/", "")
    kinds = (w.get("TrigKind") or "").split(";;")
    is_weekly = any("Weekly" in k for k in kinds)

    parts = []
    for seg in tr.split(";;"):
        seg = seg.strip()
        if not seg:
            continue
        try:
            ms = int(seg.split(".")[0].rstrip("Z"))
            dt = datetime.fromtimestamp(ms / 1000)
            parts.append(dt)
        except Exception:  # noqa: BLE001
            if len(seg) >= 16:
                parts.append(seg[11:16])
    if not parts:
        return "见计划任务"

    times = sorted({p.strftime("%H:%M") for p in parts if hasattr(p, "strftime")} |
                   {p for p in parts if not hasattr(p, "strftime")})

    if is_weekly:
        # DaysOfWeek 是位掩码：Sun=1, Mon=2, Tue=4, Wed=8, Thu=16, Fri=32, Sat=64
        MASK = [(1, "日"), (2, "一"), (4, "二"), (8, "三"), (16, "四"), (32, "五"), (64, "六")]
        days = []
        for k in kinds:
            raw = k.split("|", 1)[1].strip() if "|" in k else ""
            if not raw:
                continue
            try:
                v = int(raw)
            except ValueError:
                continue
            for bit, cn in MASK:
                if v & bit and cn not in days:
                    days.append(cn)
        days.sort(key="一二三四五六日".index)
        if not days:
            # 兜底：用起始日期推断星期
            for p in parts:
                if hasattr(p, "strftime"):
                    days = ["一二三四五六日"[p.weekday()]]
                    break
        prefix = "每周" + "、".join(days) if days else "每周"
        return f"{prefix} {' / '.join(times)}"

    if len(times) == 1:
        return f"每天 {times[0]}"
    return "每天 " + " / ".join(times)


if __name__ == "__main__":
    raise SystemExit(main())
