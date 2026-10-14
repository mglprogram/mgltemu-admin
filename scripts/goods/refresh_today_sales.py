# -*- coding: utf-8 -*-
"""刷新「今日销量」→ 重建 goods.json → 只发数据（线上大屏的销量排行要用）。

给计划任务 `TEMU-Today-Sales`（08:15 / 14:00 / 18:45）用。三步：

  ① fetch_focus_spus.py --store BHY / BEL   —— 同一页点「近7日」抓一遍、再点「今日」抓一遍
     （近7日仍喂给 focus 名单；今日喂给大屏的「今日销量」列与排序）
  ② build_goods_data.py                     —— 把两个口径 join 回 goods.json
  ③ publish_admin.py --data-only --production —— 只换数据，不重构建前端（省 4 分钟）

失败策略：某店抓不到就跳过那家（另一家照常），goods.json 会保留那一店的旧销量并在
payload 里标出来源时间；不让一次抓取失败把整个任务判成失败。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
FETCH = ROOT / "scripts" / "bel_goods" / "fetch_focus_spus.py"
GOODS = ROOT / "scripts" / "goods" / "build_goods_data.py"
PUBLISH = ROOT / "scripts" / "bel_goods" / "publish_admin.py"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(m: str) -> None:
    print(m, flush=True)


def run(args: list, timeout: int, what: str) -> tuple[bool, str]:
    log("\n-> %s" % what)
    log("   " + " ".join(str(a) for a in args[2:]))
    t0 = time.time()
    p = subprocess.run([sys.executable] + [str(a) for a in args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.strip().splitlines()[-14:]:
        log("   " + line)
    log("   （%.0f 秒，退出码 %s）" % (time.time() - t0, p.returncode))
    return p.returncode == 0, out


def ensure_gate_password() -> str:
    """把 DASH_PASSWORD 从**用户级环境变量**捞进当前进程（发布时要注入门禁）。

    在 .cmd 里用 `for /f` + powershell 去取会多起一个子进程（本项目 bat 红线：一个 .cmd 只允许
    一个子进程、且不能阻塞），所以改成在 Python 里读注册表 —— 与 publish_admin 读
    CLOUDFLARE_API_TOKEN 同一套写法，子进程直接继承环境变量。
    """
    pw = os.environ.get("DASH_PASSWORD", "")
    if pw:
        return pw
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            pw = winreg.QueryValueEx(k, "DASH_PASSWORD")[0]
    except Exception:  # noqa: BLE001
        return ""
    if pw:
        os.environ["DASH_PASSWORD"] = pw
    return pw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="BHY,BEL")
    ap.add_argument("--no-publish", action="store_true", help="只刷数据文件，不发布")
    ap.add_argument("--skip-fetch", action="store_true", help="跳过抓取（用现有 focus 文件重建）")
    a = ap.parse_args()

    stores = [s.strip().upper() for s in a.stores.split(",") if s.strip()]
    fetched = []
    for st in stores:
        ok, out = run([FETCH, "--store", st], timeout=1800, what="抓 %s 销量（近7日 + 今日）" % st)
        if ok:
            fetched.append(st)
        else:
            log("   [warn] %s 抓取失败（继续，另一家照常）" % st)

    if not fetched and not a.skip_fetch:
        log("\n!! 两店都没抓到 —— 不重建 goods.json（避免把旧数据标成新）")
        return 1

    ok, _ = run([GOODS], timeout=600, what="重建 goods.json（join 今日/近7日销量）")
    if not ok:
        log("\n!! goods.json 重建失败")
        return 1

    if a.no_publish:
        log("\n--no-publish：到此为止（数据文件已更新，未发布）")
        return 0

    if not ensure_gate_password():
        log("\n!! 没有 DASH_PASSWORD（用户级环境变量）—— 不能发布：面板含货号/库存/价格，不许裸奔")
        return 3

    ok, _ = run([PUBLISH, "--data-only", "--production"], timeout=1800,
                what="只发数据到线上（不重构建前端）")
    if not ok:
        log("\n!! 发布失败（数据文件已就绪，下次发布会带上）")
        return 1
    log("\n完成：今日销量已刷新并上线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
