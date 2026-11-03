# -*- coding: utf-8 -*-
"""auto_repair.py — 定时任务失败后**自动重跑一次**（给看门狗用）。

用户 2026-11-03 的要求
-----------------------
> 你有一个 30 分钟刷新面板的看门狗任务，那你去刷新面板的时候，
> 当检测到又失败任务，你去自动修复失败的任务。

看门狗（`watch_tasks.py`）本来就每 30 分钟读一遍所有任务的状态、还能从日志里
抽出真因 —— 它已经知道"谁失败了、为什么失败"，比任何外部工具都更适合做这件事。
之前它只做到「告警 + 顺手刷面板」，等于看见火只喊不救。

为什么不能简单粗暴地"失败就重跑"
--------------------------------
四条红线，缺一条就会变成新的故障源：

① **自激**：面板刷新任务自己失败 → 触发它 → 再失败 → 再触发……无限循环。
   巡检任务自己失败同理。⇒ 这两个任务**永不重试**。

② **无限重试**：任务真的坏了（平台改版、账号停用），每 30 分钟重跑一次，
   一天 48 次，既烧 Cloudflare 额度又占着紫鸟窗口，还会把日志刷成噪声。
   ⇒ 每个任务**每天有次数上限**，且同一次失败（同一个 LastRunTime）只重试一次。

③ **越跑越糟**：内存不足时（`avail_phys_mb < MEM_TIGHT_MB`）重载页面会把别人的页
   挤出去，实测会让「1 张卡死页」变成「3 张」。这正是 2026-11-03 那次事故的形状。
   ⇒ 内存紧张时**不重试**，只如实说明。

④ **没意义的重试**：退出码 5/6 是"窗口锁/发布锁被占，按设计跳过"，下一班次自然补，
   重试只会又撞一次锁。⇒ 跳过码不重试（它们本来也不在告警里，见 `ok_results`）。

重试之后呢
----------
不假装已经修好。告警照发（"已自动重试第 N 次"写在正文里），下一轮巡检按新结果重判：
  · 重试成功 → 面板变绿，不再告警（新 key，历史上那条已经消费掉了）；
  · 重试仍失败 → 又是新 key，会在 **上限内** 再试一次，之后放弃并把次数说清楚。

也就是说：**它替人做的是"再跑一次"这件低风险、高收益的事，不替人判断业务对错。**
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime

#: 永不自动重试的任务：
#:   · 面板刷新 —— 重试它会再失败再触发，自激；
#:   · 巡检自身 —— 同上（而且它现在正在跑）。
NEVER_RETRY = frozenset({"AutomationPanel-Refresh", "TEMU-TaskWatch-30min"})

#: 「按设计跳过」的退出码：重试只会再撞一次锁。
SKIP_CODES = frozenset({5, 6})

#: 「今天压根没跑」用的伪结果码（`SCHED_S_HAS_NOT_RUN` = 267011）。
#: 漏跑比重跑更值得救：任务今天没执行过，补一次不可能造成重复动作。
#: 它必须**穿过** `SKIP_CODES` 判断之外的所有闸门（内存/次数/时机都照常管）。
NOT_RUN = 267011

#: 每天每个任务最多重试几次（默认值，可被配置覆盖）。
DEFAULT_MAX_PER_DAY = 3

#: 距下一次计划运行不足这么多分钟就不重试 —— 反正马上就跑了，别抢窗口。
DEFAULT_SKIP_IF_NEXT_WITHIN_MIN = 10


def max_per_day(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("auto_repair_max_per_day", DEFAULT_MAX_PER_DAY)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_DAY


def skip_if_next_within(cfg: dict) -> int:
    try:
        return max(0, int(cfg.get("auto_repair_skip_if_next_within_min",
                                  DEFAULT_SKIP_IF_NEXT_WITHIN_MIN)))
    except (TypeError, ValueError):
        return DEFAULT_SKIP_IF_NEXT_WITHIN_MIN


def retry_allowed(name: str, entry: dict | None) -> bool:
    """注册表里这个任务是否**允许**自动重试（默认不允许，必须显式开）。

    为什么默认关：重跑等于**替用户再执行一次业务动作**。有些动作重跑无害
    （读页面、导出、推消息），有些不行（重启 DSH、发提醒）。让不安全的默认
    是"不重试"，只有注册表里写了 `自动重试: true` 的才动。
    """
    if not name or name in NEVER_RETRY:
        return False
    return bool((entry or {}).get("自动重试"))


def decide(name: str, rc, cfg: dict, entry: dict | None, st: dict,
           now: datetime | None = None, *,
           mem_tight: bool = False, minutes_to_next: float | None = None,
           last_run_key: str = "") -> tuple[bool, str]:
    """该不该重试 → `(要不要, 为什么)`。

    纯函数（不碰网络/不起进程），所有输入都由调用方给，好写测试也好排查。
    顺序按"越靠前越致命"排：安全闸门先于收益判断。
    """
    now = now or datetime.now()

    if not retry_allowed(name, entry):
        if name in NEVER_RETRY:
            return False, "自激风险：这是巡检/面板刷新自己，重试会无限循环"
        return False, "未在注册表开启「自动重试」（重跑等于再执行一次业务动作，必须显式授权）"

    if rc in SKIP_CODES:
        return False, "rc=%s 是按设计跳过（锁被占），重试只会再撞一次锁" % rc

    if rc is None:
        return False, "读不到结果码，无法判断"

    if mem_tight:
        return False, ("本机内存紧张：重载页面会把别的页挤出去，"
                       "实测会让卡死页从 1 张变 3 张 —— 先解决内存，再谈重试")

    if minutes_to_next is not None and 0 <= minutes_to_next < skip_if_next_within(cfg):
        return False, ("距下次计划运行只有 %.0f 分钟，直接等它更稳（不必抢窗口）"
                       % minutes_to_next)

    # 同一次失败只重试一次：key 是 LastRunTime，重试会刷新它，于是天然去重。
    if last_run_key and st.get("autorepair_done", {}).get(name) == last_run_key:
        return False, "这一次失败已经重试过了（等下一轮结果再判）"

    day = now.date().isoformat()
    used = _count_today(st, name, day)
    cap = max_per_day(cfg)
    if used >= cap:
        return False, "今天已自动重试 %d 次（上限 %d），不再重跑 —— 需要人工看" % (used, cap)

    if rc == NOT_RUN:
        return True, "今天还没跑过，补跑一次（第 %d 次）" % (used + 1)
    return True, "第 %d 次自动重试" % (used + 1)


def _count_today(st: dict, name: str, day: str) -> int:
    """今天已经替这个任务重试了几次。"""
    rec = (st.get("autorepair") or {}).get(name) or {}
    if rec.get("day") != day:
        return 0
    try:
        return int(rec.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def record_attempt(st: dict, name: str, last_run_key: str,
                   now: datetime | None = None) -> None:
    """把"重试过一次"记进状态（调用方负责随后落盘）。"""
    now = now or datetime.now()
    day = now.date().isoformat()
    bucket = st.setdefault("autorepair", {})
    rec = bucket.get(name) or {}
    if rec.get("day") != day:
        rec = {"day": day, "count": 0}
    rec["count"] = int(rec.get("count") or 0) + 1
    rec["at"] = now.isoformat(timespec="seconds")
    bucket[name] = rec
    st.setdefault("autorepair_done", {})[name] = last_run_key


def trigger(name: str, timeout: float = 60.0) -> tuple[bool, str]:
    """异步触发一次任务（`Start-ScheduledTask`，不等结果）。

    为什么不等：一个任务可能要跑十几分钟甚至 165 分钟，看门狗必须马上返回，
    否则下一轮巡检会被自己堵住（`MultipleInstances=IgnoreNew` 会直接丢弃）。

    ⚠ 用参数化调用而不是拼字符串：任务名里有中文（`TEMU限流监控`）和空格，
    以前踩过 PowerShell 引号转义把命令搞坏、返回 127 的坑。
    """
    if not name:
        return False, "空任务名"
    ps = ("Start-ScheduledTask -TaskName '%s'"
          % name.replace("'", "''"))
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "触发超时（%ds）" % int(timeout)
    except Exception as e:                                     # noqa: BLE001
        return False, "触发失败：%s" % str(e)[:120]
    if p.returncode != 0:
        return False, ((p.stderr or p.stdout or "").strip()[:150] or "rc=%d" % p.returncode)
    return True, "已触发"


def mem_is_tight() -> bool:
    """本机可用物理内存是否低于 `auth_guard.MEM_TIGHT_MB`。取不到就当**紧张**。

    取不到时保守判"紧张"：宁可少重跑一次，也不要在内存已经很糟时
    再开两个页面把别人挤出去。
    """
    try:
        import sys
        _lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power", "lib")
        if _lib not in sys.path:
            sys.path.insert(0, _lib)
        import auth_guard                                 # noqa: PLC0415
        avail = auth_guard.avail_phys_mb()
        return avail is None or avail < int(auth_guard.MEM_TIGHT_MB)
    except Exception:                                          # noqa: BLE001
        return True


def minutes_until_next(next_iso: str, now: datetime | None = None) -> float | None:
    """距下一次计划运行还有几分钟（读不到返回 None = 不做这项判断）。

    ⚠ Windows 的 `NextRunTime.ToString('yyyy-MM-ddTHH:mm:ss')` 是秒级，但
    `Get-ScheduledTaskInfo` 原生格式带 **7 位**小数（`.0000000`），而
    `strptime` 的 `%f` 只吃 1~6 位 —— 不截断就解析失败，静默变成"不知道下次何时"，
    于是这条"快跑了就别抢窗口"的闸门形同虚设。2026-11-03 实测踩到。
    """
    now = now or datetime.now()
    s = str(next_iso or "").strip()
    if not s or s.startswith("0001-01-01"):
        return None
    s = s.split("+")[0].rstrip("Z")
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?$", s)
    if not m:
        return None
    frac = (m.group(2) or "")[:6].ljust(6, "0")      # 7 位→6 位（%f 的上限）
    try:
        nxt = datetime.strptime(m.group(1) + "." + frac, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        try:
            nxt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return (nxt - now).total_seconds() / 60.0
