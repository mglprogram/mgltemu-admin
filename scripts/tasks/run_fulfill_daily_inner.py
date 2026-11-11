# -*- coding: utf-8 -*-
"""run_fulfill_daily_inner.py — 每日发货任务的**业务执行体**（被 dsh_run.py 调用）。

## 用户口径（2026-11-10 定稿）

「设一个 4 点钟执行发货任务，主会话主要负责修复和监控；开五个子代理去跑发货
 （分别有五个店铺的发货 BHY BEL、BEGL BANGL BSGL）」

⇒ 本文件的职责＝**调度**，不是执行：
  ① 五个店**并行**，一店一个子进程（子进程带 `--agent <店>`，就是"该店的子代理"）；
  ② SHEIN 店先 `export_orders` 自己出表（TEMU 店仍由本文件用
     `temu_export_by_select.py` 从紫鸟窗口导出，那条路 SHEIN 店用不了）；
  ③ 失败隔离：一个店挂了不许拖死别的店；五店跑完才返回总码。

店序不再重要（真并行），但日志仍按固定顺序打印，便于对照。

## 窗口与互斥

- **BHY / BEL**：各占一扇 TEMU 紫鸟窗口（`TM-BHY-YJF` / `TM-BEL-MGL`），互不相干 ⇒ 可并行。
  外层 `run_with_window_lock.py` 仍按 `BHY+BEL` 取锁，挡住别的 TEMU 任务。
- **BEGL**：走**网页导出**（无 OpenAPI 密钥），借 `TK-Savonire-YJF` 窗口的 sellerhub 登录态；
  同窗口只允许一个店动 ⇒ 由 `EXCLUSIVE_GROUPS` 里的 `shen-web` 组保证**组内串行**。
- **BANGL / BSGL**：全走 OpenAPI，不占窗口，随便并行。

## 退出码

    0   五店（或已启用的店）全成功
    1   部分失败（详见日志每店 rc）
    5   外层窗口锁被占（本文件不产生）
    42  全失败
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import threading
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POWER = os.path.join(PROJ, "scripts", "power")

#: 五店（用户 2026-11-10 指定）。BSGL 是用户对 SHEIN 那边 BS 店的叫法，
#: 系统内 store 键是 `BS`（帕沃尔店 8540141066，主体 TELALEO）。
STORES = ("BHY", "BEL", "BEGL", "BANGL", "BSGL")

#: 用户叫法 → 系统店键
#: · BSGL＝SHEIN 那边的 BS 店（帕沃尔 8540141066，主体 TELALEO）
#: · BANGL＝SHEIN 那边的 BAN 店（帕沃尔 1205650698，主体 BAN）
#:   ⚠ `run_temu_fulfill.py` 只认 `BAN`，写 `BANGL` 会 `[HARD STOP] 未知店后缀`
ALIAS = {"BSGL": "BS", "BANGL": "BAN"}

#: 每店平台（决定走哪条导表）
PLATFORM = {"BHY": "TEMU", "BEL": "TEMU",
            "BEGL": "SHEIN", "BANGL": "SHEIN", "BSGL": "SHEIN"}

#: 紫鸟窗口名（TEMU 导出用；SHEIN 网页导出用 shein_web_window）
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

#: 互斥组：同组内的店**串行**跑（共用同一扇紫鸟窗口的登录态）。
#: ★ BEGL 走网页导出时占 `TK-Savonire-YJF`；BANGL 现在走 API 不进本组，
#:   但若哪天退回网页导出，必须把它也加进 `shen-web` 组。
EXCLUSIVE_GROUPS = {
    "shen-web": ("BEGL",),
}

#: 每店超时（秒）。BHY 历史上最慢到 23 分钟，给足余量。
STORE_TIMEOUT = 3 * 3600

_PRINT_LOCK = threading.Lock()


def log(msg: str, store: str = "") -> None:
    """带店前缀的线程安全打印（五店并行时日志会交叉，必须加锁）。"""
    tag = ("[%s] " % store) if store else ""
    with _PRINT_LOCK:
        print("%s%s" % (tag, msg), flush=True)


def _run(args: list, timeout: int = STORE_TIMEOUT, env_extra: dict | None = None):
    cmd = [sys.executable] + args
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(cmd, cwd=PROJ, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + ("\n[stderr]\n" + p.stderr if p.returncode else "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "超时（%ds）" % timeout


def export_temu(store: str) -> tuple[int, str]:
    """TEMU 店：从紫鸟窗口导出「待发货」表 → (rc, xlsx 绝对路径)。"""
    win = WINDOWS.get(store, store)
    win_dir = os.path.join(os.environ.get("APPDATA", ""), "ziniaobrowserdatas",
                           "ziniao browser", win)
    before = {}
    if os.path.isdir(win_dir):
        for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
            try:
                before[f] = os.path.getmtime(f)
            except OSError:
                pass

    rc, out = _run([os.path.join(POWER, "temu_export_by_select.py"), "--store", store],
                   timeout=1800)
    if rc != 0:
        log("⚠ 导出失败 rc=%s，输出尾部：" % rc, store)
        for line in (out or "").strip().splitlines()[-8:]:
            log("   " + line[:300], store)
        return rc, ""

    newest = ""
    for line in reversed(out.splitlines()):
        if "[RESULT]" in line:
            try:
                newest = str(json.loads(line.split("[RESULT]", 1)[1]).get("file") or "")
            except Exception:  # noqa: BLE001
                pass
            break
    if not newest or not os.path.isfile(newest):
        cands = []
        if os.path.isdir(win_dir):
            for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if before.get(f) != mt:
                    cands.append((mt, f))
        if not cands:
            return 42, ""
        newest = max(cands)[1]
    return 0, newest


def run_store(store: str) -> dict:
    """一个店 → 一个子进程（＝该店的子代理）。返回该店结论。"""
    key = ALIAS.get(store, store)
    t0 = time.time()
    log("=" * 60, store)
    log("开始（系统店键 %s / 平台 %s）" % (key, PLATFORM.get(store, "?")), store)

    args = [os.path.join(POWER, "run_temu_fulfill.py"), "--store", key,
            "--agent", store, "--confirm", "--subdir-by-store", "--skip-notify"]

    if PLATFORM.get(store) == "TEMU":
        rc_e, xlsx = export_temu(key)
        if rc_e != 0 or not xlsx:
            log("✗ 导出失败（rc=%s）→ 本店跳过" % rc_e, store)
            return {"store": store, "ok": False, "stage": "export",
                    "rc": rc_e or 42, "seconds": round(time.time() - t0, 1)}
        log("导出：%s" % os.path.basename(xlsx), store)
        args += ["--file", xlsx]
    # SHEIN 店不预导出：整链第一步就是 export_orders（API 或网页导出，各店自己决定）

    rc, out = _run(args, timeout=STORE_TIMEOUT)
    tail = [x for x in out.strip().splitlines() if x.strip()][-8:]
    for line in tail:
        log(line[:300], store)

    ok = rc == 0
    log("%s rc=%s %.1f 分钟" % ("✅" if ok else "✗", rc, (time.time() - t0) / 60), store)
    res = {"store": store, "key": key, "ok": ok, "stage": "fulfill", "rc": rc,
           "seconds": round(time.time() - t0, 1)}
    # 把 run.json 的 summary 带回来，主会话只看这一份就知道哪步软失败
    rj = os.path.join(POWER, "out", time.strftime("%Y-%m-%d"), "fulfill", key, "run.json")
    try:
        with open(rj, encoding="utf-8") as f:
            res["summary"] = (json.load(f) or {}).get("summary") or {}
        res["run_json"] = rj
    except Exception:  # noqa: BLE001
        pass
    return res


def _group_of(store: str) -> str:
    for g, members in EXCLUSIVE_GROUPS.items():
        if store in members:
            return g
    return ""


def run_all() -> list:
    """五店并行；互斥组内串行。返回 [{store, ok, rc, seconds, ...}]。"""
    results = []
    res_lock = threading.Lock()

    def work(store):
        try:
            r = run_store(store)
        except Exception as ex:  # noqa: BLE001
            log("✗ 异常：%s" % str(ex)[:200], store)
            r = {"store": store, "ok": False, "stage": "exception", "rc": 1,
                 "why": str(ex)[:200]}
        with res_lock:
            results.append(r)

    # 先把互斥组串成一条链，再把每条链当一个"任务"并发
    chains = []
    grouped = set()
    for g, members in EXCLUSIVE_GROUPS.items():
        chains.append([s for s in STORES if s in members])
        grouped.update(members)
    for s in STORES:
        if s not in grouped:
            chains.append([s])

    def run_chain(chain):
        for s in chain:
            work(s)

    log("[fulfill-daily] 并行组：%s"
        % " | ".join(" → ".join(c) for c in chains))
    threads = [threading.Thread(target=run_chain, args=(c,), daemon=True) for c in chains]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    order = {s: i for i, s in enumerate(STORES)}
    results.sort(key=lambda r: order.get(r["store"], 99))
    return results


def push_reports() -> dict:
    """收尾：把各店「缺货/失败订单」表推 TEMU 群 + 传云盘（永不抛）。"""
    log("[push] 推 TEMU 群 + 上传云盘…")
    rc, out = _run([os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "push_fulfill_report.py")], timeout=1200)
    lines = [x for x in (out or "").strip().splitlines() if x.strip()]
    for ln in lines[-10:]:
        log("   " + ln[:220])
    res = {"rc": rc}
    for ln in reversed(lines):
        if "[RESULT]" in ln:
            try:
                res.update(json.loads(ln.split("[RESULT]", 1)[1]))
            except Exception:  # noqa: BLE001
                pass
            break
    return res


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    log("[fulfill-daily] %s 开始，五店并行：%s"
        % (time.strftime("%Y-%m-%d %H:%M:%S"), " / ".join(STORES)))
    results = run_all()

    log("")
    log("[fulfill-daily] 汇总：")
    for r in results:
        log("   %-6s %s rc=%-3s %5.1f 分钟  %s"
            % (r["store"], "✅" if r.get("ok") else "✗", r.get("rc"),
               (r.get("seconds") or 0) / 60,
               (("软失败: " + " / ".join((r.get("summary") or {}).get("soft_failed") or []))
                if (r.get("summary") or {}).get("soft_failed") else "")))
    bad = [r for r in results if not r.get("ok")]

    # 推送/上传失败不改本任务退出码（发货已完成，只是通知没到）
    push_result = {}
    try:
        push_result = push_reports()
    except Exception as ex:  # noqa: BLE001
        log("[push] ✗ 收尾推送异常（不影响发货结论）：%s" % str(ex)[:200])
        push_result = {"ok": False, "why": str(ex)[:200]}

    log("\n[RESULT] " + json.dumps({"results": results, "bad": len(bad),
                                   "push": push_result}, ensure_ascii=False))
    if not bad:
        return 0
    return 42 if len(bad) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
