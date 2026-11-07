# -*- coding: utf-8 -*-
"""add_fulfill_daily_task.py — 往注册表里加 `TEMU-Fulfill-Daily`（幂等）。

用户 2026-11-06：
    「设一个每天 6:30 的发货任务，这个定时任务用 dsh 会话激活；
      激活的会话在发货这个工作区出现」
    确认口径：跑**整链**，覆盖 **BHY + BEL**。

为什么用脚本改注册表而不是手写 JSON：
    `tasks.json` 是**唯一事实来源**，手改容易破坏编码（文件是 UTF-8 BOM）与缩进；
    这里读—改—写，并保证加完是幂等的（重复跑只更新那一条，不重复添加）。

用法：
    python scripts/tasks/add_fulfill_daily_task.py --dry-run
    python scripts/tasks/add_fulfill_daily_task.py --apply
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import shutil
import sys

REGISTRY = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                        "scheduled-tasks", "tasks.json")

TASK_NAME = "TEMU-Fulfill-Daily"
#: 工作区（会话落在这里）—— 用户明确要求
WORKSPACE = r"D:\AI-workplace\发货"
DOC = os.path.join(WORKSPACE, "TASK-DOC-fulfill-daily.md")
INNER = r"scripts\tasks\run_fulfill_daily_inner.py"
CMD = r"D:\AI-workplace\TEMU\scripts\tasks\run_fulfill_daily.cmd"
LOG = "D:\\AI-workplace\\TEMU\\_logs\\fulfill-daily\\"


def entry(registry: dict) -> dict:
    """构造注册表条目（纯函数）。"""
    hidden = (r"//nologo "
              r"\"C:\Users\saing\.agents\skills\scheduled-tasks\scripts\run_hidden.vbs\" "
              r"\"cmd /c \"\"%s\"\"\"" % CMD)
    return {
        "name": TASK_NAME,
        "label": "TEMU 每日发货整链（BHY+BEL）",
        "desc": ("每天 06:30 跑两个店的完整发货链：导出当期待发货 → 拉最新库存 → 拆仓 →"
                 "两仓买面单 → 帕沃尔 OMS 确认订单+取号 → 恒信领星 OMS 发货 →"
                 "抓失败单 → 查恒信 OMS 终态 → TEMU 批量确认发货 → 出"
                 "「缺货 + 两仓失败订单」两 sheet Excel。店序固定 BEL → BHY（串行）。"),
        "project": "TEMU",
        "managed": True,
        "enabled": True,
        "schedule": {"daily": ["04:00"]},
        "_schedule说明": ("用户 2026-11-06 指定 06:30，**2026-11-07 改为 04:00**"
                          "（用户原话：「那就改成4点发货」）。"
                          "改的原因是整链实测典型要 60 分钟（BEL 约 25 + BHY 约 120）、"
                          "最坏 145 分钟，而 06:00 起 `TEMU流量加速周更`/`TEMU限流监控`/"
                          "`TEMU-Goods-OnSale-Daily`/`TEMU-BEL-Abnoral`/`TEMU-Presale-Notify` "
                          "接连占着 BHY+BEL 窗口 —— 06:30 怎么排都会撞，"
                          "撞上就是窗口锁 exit 5、**当天不发货**。"
                          "04:00 是全天空档：典型 60 分钟到 05:00、上限 180 分钟到 07:00，"
                          "与 06:00（仅最坏才够到）和 07:30 都留出间隔。"),
        "action": {"execute": "wscript.exe", "arguments": hidden},
        "log": LOG,
        "since": "2026-11-06",
        "_窗口": "无控制台窗口（wscript + run_hidden.vbs，退出码照常透传）",
        "紫鸟窗口": "BHY+BEL",
        "窗口锁": "BHY+BEL",
        "_锁说明": ("整链同时驱动 BHY+BEL 两扇紫鸟窗口（导出、买面单、OMS 都在上面），"
                    "入口 run_fulfill_daily.cmd 用 run_with_window_lock.py 取锁；"
                    "拿不到 exit 5（按设计跳过，不算失败）。同一扇窗口上的其它任务"
                    "（Presale / 限流监控 / Today-Sales / Pending-Confirm）因此天然互斥。"),
        "实测耗时分钟": 60,
        "_实测耗时说明": ("按 2026-11-06 实测体量估：BEL 42 单整链约 25 分钟、"
                          "BHY 471 单约 120 分钟（买面单与取号占大头）。"
                          "登记 60 分钟作典型值。"
                          "⚠ 跑过几轮后要用 _logs/dsh-runner 的 elapsed 最大值重新校准。"),
        "timeout_minutes": 180,
        "_超时说明": ("上限 180 分钟（原 330，2026-11-07 随 04:00 排班一起收紧）。"
                      "04:00 起跑 + 180 分钟 = 07:00 必然收尾，"
                      "不会压到 06:00 的周更、也不会压到 07:30 的限流监控。"
                      "180 分钟对「两店串行 145 分钟最坏」仍留 35 分钟余量。"),
        "ok_results": [0, 5],
        "_ok_results说明": ("`0` 两店都成功；`5` = 没拿到 BHY+BEL 紫鸟窗口锁"
                            "（别的任务正占着，按设计跳过，下一班自然会跑）。"
                            "`1`（一个店失败）与 `42`（两店都失败）仍要报。必须含 0。"),
        "刷新面板": True,
        "自动重试": False,
        "_自动重试说明": ("不自动重试：本任务会**真下单、真买面单（花钱）**，"
                          "重跑可能重复扣费；且一次独占 BHY+BEL 最多 330 分钟。"
                          "失败交人工判断（面板 + 飞书告警）。"),
        "category": "temu-store",
        "dsh": {
            "inner": INNER,
            "enabled": True,
            "workspace": WORKSPACE,
            "doc": DOC,
            "_说明": ("经 run_via_dsh.cmd 交给 DSH headless 会话执行。"
                      "`workspace` 决定这条会话落在哪个 DSH 工作区 —— 用户 2026-11-06 要求"
                      "「激活的会话在发货这个工作区出现」，靠把 dsh 进程的 cwd 指到 "
                      "D:\\AI-workplace\\发货 实现（dsh_task.py --cwd）。"
                      "`doc` 是任务说明书，prompt 里会让 agent 先读它。"),
        },
        "note": ("06:30 两店串行。帕沃尔那三段（label_pw/pw_receive/pw_track）按用户铁律"
                 "应派子代理跑；16:30-19:00 帕沃尔禁下单，本时段不受影响。"
                 "产物：scripts/power/out/<日期>/<店>_缺货与失败订单_<日期>.xlsx"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="加入 TEMU-Fulfill-Daily 注册表条目")
    ap.add_argument("--apply", action="store_true", help="真写注册表（默认只 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写（默认行为）")
    ap.add_argument("--registry", default=REGISTRY)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not os.path.isfile(a.registry):
        print("[FAIL] 注册表不存在：%s" % a.registry, file=sys.stderr)
        return 42
    with io.open(a.registry, encoding="utf-8-sig") as fh:
        reg = json.load(fh)

    tasks = reg.get("tasks") or []
    new = entry(reg)
    idx = next((i for i, t in enumerate(tasks) if t.get("name") == TASK_NAME), -1)
    if idx >= 0:
        tasks[idx] = new
        print("[更新] %s（已存在，覆盖）" % TASK_NAME)
    else:
        tasks.append(new)
        print("[新增] %s" % TASK_NAME)

    print("  触发    : 每天 %s" % ",".join(new["schedule"]["daily"]))
    print("  工作区  : %s" % WORKSPACE)
    print("  说明书  : %s" % DOC)
    print("  inner   : %s" % INNER)
    print("  超时    : %s 分钟（agent 预算 %s 分钟）"
          % (new["timeout_minutes"], new["timeout_minutes"] - 1))

    missing = [p for p in (CMD, DOC) if not os.path.isfile(p)]
    if missing:
        print("\n[FAIL] 缺少文件，先补齐再 apply：")
        for m in missing:
            print("   - %s" % m)
        return 42

    if not a.apply:
        print("\n[dry-run] 未写入。加 --apply 生效。")
        return 0

    bak = a.registry + ".bak-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(a.registry, bak)
    reg["tasks"] = tasks
    with io.open(a.registry, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(json.dumps(reg, ensure_ascii=False, indent=2) + "\n")
    print("\n已写入（备份 %s）" % os.path.basename(bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
