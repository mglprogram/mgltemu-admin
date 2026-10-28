# -*- coding: utf-8 -*-
"""每周四 06:00 刷新「流量加速」数据（两店 × 已开启 / 待开启）并发布。

流程：
  1. 确保两个紫鸟店铺窗口已打开（ziniao-cli store open）
  2. 按窗口进程探测 CDP 端口 → 登录态失效则走授权（flux_auth，CDP 真实点击）
  3. 逐店逐板块抓取（fetch_flux_all.py，全程只读）
  4. 合并成前端静态数据（build_api_data.py --flux-only）
  5. 发布到 Cloudflare Pages 生产分支（publish_admin.py --build --production）

设计要点：
  - **单店失败不影响另一店**：抓取脚本只在成功时覆盖产物文件，
    失败时旧文件保持原样，合并阶段照旧可用。
  - 两店都失败 → 不发布，直接退出非零，避免把空数据发上去。
  - 登录态掉了会**自动授权**（紫鸟会自动填账密，无需人工）。

用法：
  python run_flux_weekly.py              # 全流程
  python run_flux_weekly.py --no-deploy  # 只抓取+合并，不发布
  python run_flux_weekly.py --store BEL  # 只处理一个店
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(r"D:\AI-workplace\TEMU")
LOGDIR = ROOT / "_logs" / "temu-flux"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import flux_auth  # noqa: E402

STORES = {
    "BEL": {"storeId": "27677489075954", "window": "TM-BEL-MGL"},
    "BHY": {"storeId": "27697436126374", "window": "TM-BHY-YJF"},
}
SECTIONS = ["accelerating", "pending"]
FLUX_URL = "https://agentseller-us.temu.com/main/flux-analysis"

PY = sys.executable


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=1800):
    """跑子进程并**永不抛异常**（超时也返回一个假的 CompletedProcess）。

    ★ 2026-10-23 事故：`watch_focus.py` 卡住 1800s，subprocess 抛 TimeoutExpired
      一路冒到 `main()` 外面 —— 整个脚本带着 traceback 崩掉（exit 1），
      前面已经抓好的 BEL/BHY 流量加速数据**一条都没发布**。
      而这些步骤的语义本来就是"失败不致命"（见各函数 docstring）。
      ⇒ 超时必须在**这里**被吃住，变成一个 rc=124 的正常返回值，
        让调用方按"这一步失败"处理，后面的合并/发布照常走。
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = ""
        for part in (getattr(e, "stdout", None), getattr(e, "stderr", None)):
            if part:
                out += part if isinstance(part, str) else part.decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            cmd, 124, out, "[timeout] 超过 %ss 未完成，按本步失败处理" % timeout)


# ---------------------------------------------------------------- 窗口与登录
def ensure_window(store: str) -> tuple[int, str]:
    """打开（或复用）店铺窗口并返回 (cdp端口, 说明)。

    开窗本身会失败：紫鸟有时刚起内核、调试端口还没监听
    （报 `Debug port xxxxx is not ready: connect ECONNREFUSED`）。
    所以开窗要重试，端口也要轮询等待。
    """
    st = STORES[store]
    last = ""
    for attempt in range(1, 4):
        r = run([_ziniao_cli(), "store", "open",
                 "--id", st["storeId"], "--expected-name", st["window"],
                 "--url", FLUX_URL], timeout=420)
        out = (r.stdout or "") + (r.stderr or "")
        last = out.strip()[-200:]
        if '"ok": true' in out or '"status": 3' in out:
            break
        log("  开窗第%d次未成功，等待后重试" % attempt)
        time.sleep(20)

    # 端口可能比开窗晚就绪，最多等 3 分钟
    deadline = time.time() + 180
    while time.time() < deadline:
        port = flux_auth.find_store_port(st["window"])
        if port:
            return port, "窗口就绪 port=%s" % port
        time.sleep(8)
    return 0, "未探测到 CDP 端口（窗口名 %s）｜开窗回包：%s" % (st["window"], last)


def ensure_login(port: int, store: str) -> tuple[bool, str]:
    return flux_auth.ensure_logged_in(port, store, timeout=240)


def goto_flux(store_id: str) -> None:
    """把窗口导航到「商品流量」页。

    必须显式导航：窗口复用时 `store open --url` 不一定真的跳转，
    而抓取脚本要求当前页就是 flux-analysis（否则直接 exit 3）。
    """
    run([_ziniao_cli(), "page", "visit", "--store-id", store_id,
         "--url", FLUX_URL, "--wait-until", "domcontentloaded"], timeout=300)


def flux_target(port: int) -> str:
    """取该窗口「商品流量」页的 targetId（CDP 口径，与桥的 targetId 一致）。"""
    t = flux_auth.find_tab(port, "flux-analysis")
    return (t or {}).get("id") or ""


# ---------------------------------------------------------------- 抓取
def scrape(store: str, section: str, target: str) -> tuple[bool, str, int]:
    """抓一个板块。返回 (是否完全成功, 日志尾巴, 退出码)。

    退出码 2 = 中途登录态掉线（已抓到的部分照常落盘），上层可重登后重跑。
    """
    cmd = [PY, str(HERE / "fetch_flux_all.py"),
           "--store", store, "--section", section]
    if target:
        cmd += ["--target", target]
    r = run(cmd, timeout=3600)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-5:])
    return r.returncode == 0, tail, r.returncode


def scrape_focus(store: str) -> tuple[bool, str]:
    """刷新该店的重点 SPU 名单（销量分析 · 近7日 有销量）。

    失败不致命：重点名单保留上一次的结果，限流监控照旧可用。
    """
    r = run([PY, str(HERE / "fetch_focus_spus.py"), "--store", store], timeout=1800)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, "\n".join(out.splitlines()[-4:])


def watch_focus() -> tuple[bool, str]:
    """用最新重点名单比对各店限流清单，重建重点款监控结果。

    退出码 1 = 有重点款被限流（是**告警**，不是失败），这里按成功处理。
    """
    r = run([PY, str(HERE / "watch_focus.py")], timeout=1800)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    ok = r.returncode in (0, 1)
    return ok, "\n".join(out.splitlines()[-3:])


def build_data() -> tuple[bool, str]:
    # 全量重建（限流 + 流量加速 + 自动化面板），不只 flux
    r = run([PY, str(HERE / "build_api_data.py")], timeout=600)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, "\n".join(out.splitlines()[-6:])


def deploy() -> tuple[bool, str]:
    r = run([PY, str(HERE / "publish_admin.py"), "--build", "--production"], timeout=2400)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    # 只保留关键行，日志别太长
    keep = [ln for ln in out.splitlines()
            if any(k in ln for k in ("[OK]", "[FAIL]", "https://", "上传"))]
    return r.returncode == 0, "\n".join(keep[-8:] or out.splitlines()[-6:])


def _ziniao_cli() -> str:
    """ziniao-cli 可执行入口（优先 PATH 上的 .cmd）。"""
    import shutil
    p = shutil.which("ziniao-cli")
    return p or "ziniao-cli"


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--store", choices=list(STORES), default=None)
    a = ap.parse_args()

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / ("flux-%s.log" % time.strftime("%Y%m%d"))

    stores = [a.store] if a.store else list(STORES)
    results: dict[str, dict[str, bool]] = {}
    auth_notes: dict[str, str] = {}

    log("=== 流量加速周更开始（%s）===" % ", ".join(stores))

    # ★ 窗口互斥**不在这里做**（2026-10-16 修，之前是我埋的自锁）。
    #   scripts\tasks\run_temu_flux_weekly.cmd 已经用 scripts\run_with_window_lock.py 上了同一把锁 ——
    #   window_lock 的键是**店铺**（BHY+BEL 与 BEL+BHY 归一成同一组
    #   BEL.lock/BHY.lock），所以这里再锁一次 === **自己抢自己的锁**：
    #   pid 还活着 ⇒ 不算陈旧 ⇒ WindowBusy ⇒ [HARD STOP] exit 4。
    #   实测 2026-10-16 06:00 就这么挂的，日志里同一个 pid 22924 先"已占住 BHY+BEL"
    #   紧接着又"已有作业在跑"。
    #   约定：**全项目统一由 .cmd 那层加锁，脚本内部不许再锁一次。**
    return _run_locked(stores, results, auth_notes, logfile, a)


def _run_locked(stores, results, auth_notes, logfile, a) -> int:
    """真正干活的部分（在窗口锁内跑）。拆出来是为了让 `main` 的锁作用域一目了然。"""
    for store in stores:
        log("--- %s ---" % store)
        # 先初始化，保证后续无论从哪个分支 continue 出去都有完整键
        results[store] = {s: False for s in SECTIONS}
        port, note = ensure_window(store)
        log("  " + note)
        if not port:
            auth_notes[store] = note
            continue

        # 用 CDP 直接导航到商品流量页并**验证页面真的渲染出快速筛选卡片**，
        # 避免「桥接当前页」与「CDP 找到的 tab」不是同一个导致的空抓取。
        target, note = flux_auth.ensure_flux_page(port, FLUX_URL, timeout=150)
        log("  页面: " + ("OK " if target else "FAIL ") + note)
        if not target:
            auth_notes[store] = note
            continue

        for sec in SECTIONS:
            ok, tail, rc = scrape(store, sec, target)
            for ln in tail.splitlines():
                log("      " + ln)

            # 中途掉线（rc=2）：重新登录一次，再补跑一遍这个板块。
            # 已抓到的部分已落盘，且抓取脚本会按 SPU 与旧产物合并，不会丢数据。
            if rc == 2:
                log("  %s/%s 中途掉线 → 重新登录后补跑一次" % (store, sec))
                again, msg = ensure_login(port, store)
                log("    重新登录: " + ("OK " if again else "FAIL ") + msg)
                if again:
                    goto_flux(STORES[store]["storeId"])
                    time.sleep(6)
                    target = flux_target(port) or target
                    ok, tail, rc = scrape(store, sec, target)
                    for ln in tail.splitlines():
                        log("      " + ln)

            results[store][sec] = ok
            log("  %s/%s: %s" % (store, sec, "OK" if ok else "FAIL"))

        # 顺带刷新该店的重点 SPU 名单（销量分析 · 近7日）
        log("  -- 刷新重点 SPU 名单（销量分析 近7日）")
        fok, ftail = scrape_focus(store)
        log("     重点名单: " + ("OK" if fok else "FAIL"))
        for ln in ftail.splitlines():
            log("     " + ln)

    # ---- 用最新重点名单重算重点款监控 ----
    log("--- 重算重点款监控 ---")
    wok, wtail = watch_focus()
    log("  " + ("OK" if wok else "FAIL"))
    for ln in wtail.splitlines():
        log("      " + ln)

    # ---- 合并 ----
    log("--- 合并静态数据 ---")
    ok_build, build_tail = build_data()
    log("  " + ("OK" if ok_build else "FAIL"))
    for ln in build_tail.splitlines():
        log("      " + ln)

    # ---- 判断是否有任一板块成功 ----
    any_ok = any(v for st in results.values() for v in st.values())
    if not any_ok:
        log("!! 全部板块抓取失败，跳过发布（保留上一次线上数据）")
        return 3

    if a.no_deploy:
        log("--no-deploy：已跳过发布")
        return 0

    if not ok_build:
        log("!! 数据合并失败，跳过发布")
        return 4

    log("--- 发布 ---")
    ok_dep, dep_tail = deploy()
    log("  " + ("OK" if ok_dep else "FAIL"))
    for ln in dep_tail.splitlines():
        log("      " + ln)

    # ---- 汇总 ----
    log("=== 汇总 ===")
    for store in stores:
        secs = results.get(store, {})
        line = "  %s: %s" % (store, " / ".join(
            "%s=%s" % (s, "OK" if secs.get(s) else "FAIL") for s in SECTIONS))
        log(line + ("  (登录: %s)" % auth_notes.get(store, "") if not all(secs.values()) else ""))

    failed = [f"{st}/{s}" for st in stores for s, v in results.get(st, {}).items() if not v]
    if failed:
        log("  未成功: " + ", ".join(failed))

    # 退出码：0 全成功；5 有板块失败但至少发布成功（部分成功，仍算跑通）
    if not ok_dep:
        return 6
    return 0 if not failed else 5


if __name__ == "__main__":
    raise SystemExit(main())