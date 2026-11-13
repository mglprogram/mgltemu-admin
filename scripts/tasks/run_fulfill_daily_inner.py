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
    3   **只有软失败**（有单没买成 / 缺货 / 平台条件未满足），主体发货已完成
        ⇒ 业务告警，不是故障。面板显示琥珀色，飞书/手机**不推失败告警**。
    1   部分失败（有店是真的硬失败，详见日志每店 rc）
    5   外层窗口锁被占（本文件不产生）
    42  全失败

  ⚠ 为什么要有 3（2026-11-12 用户口径）：「软失败不该让整链判红 —— 现在 BAN
    只有软失败，你收到的却是一条『运行失败』告警；改成『带告警成功』」。
    判据**只听门店自己的 summary**：`hard_failed` 为空且 `soft_failed` 非空 ⇒ 软。
    没有 summary 的（导出就失败、抛异常）一律算硬，因为那说明这一店压根没跑起来。
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

#: 店清单（BEGL 除外）。BSGL 是用户对 SHEIN 那边 BS 店的叫法，
#: 系统内 store 键是 `BS`（帕沃尔店 8540141066，主体 TELALEO）。
#: ★ 2026-11-12 补 BD：SHEIN 有 API 的 BD 店（帕沃尔 5965848967，主体 BD）此前**不在这个表里**
#:   ⇒ 每天定时任务压根不跑 BD（11-12 那轮 BD 一单没发就是这个原因）。
STORES = ("BHY", "BEL", "BEGL", "BANGL", "BSGL", "BD", "BE")

#: ★ 2026-11-12 修：BEGL **不派子代理**（TASK-DOC §1 / 用户 2026-11-11 令「人工发现阶段」：
#:   没有 OpenAPI 密钥 ⇒ 整链对它直接 exit 2）。以前它还在 STORES 里 ⇒ 每天都产一条 rc=2，
#:   被算进 bad ⇒ 五店全绿也照样返回 rc=1。这里把它从调度表里摘掉（只保留在 STORES 里做排序）。
MANUAL_ONLY = ("BEGL",)
DISPATCH = tuple(s for s in STORES if s not in MANUAL_ONLY)

#: 用户叫法 → 系统店键
#: · BSGL＝SHEIN 那边的 BS 店（帕沃尔 8540141066，主体 TELALEO）
#: · BANGL＝SHEIN 那边的 BAN 店（帕沃尔 1205650698，主体 BAN）
#:   ⚠ `run_temu_fulfill.py` 只认 `BAN`，写 `BANGL` 会 `[HARD STOP] 未知店后缀`
ALIAS = {"BSGL": "BS", "BANGL": "BAN"}

#: 每店平台（决定走哪条导表）
#: ★ 2026-11-13 补 BE：帕沃尔店 1640478289，SHEIN 主体（appId 168B65A2…），库存全在帕沃尔仓。
PLATFORM = {"BHY": "TEMU", "BEL": "TEMU",
            "BEGL": "SHEIN", "BANGL": "SHEIN", "BSGL": "SHEIN", "BD": "SHEIN",
            "BE": "SHEIN"}

#: 紫鸟窗口名（TEMU 导出用；SHEIN 网页导出用 shein_web_window）
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

#: 互斥组：同组内的店**串行**跑（共用同一扇紫鸟窗口的登录态）。
#: ★ BEGL 走网页导出时占 `TK-Savonire-YJF`；BANGL 现在走 API 不进本组，
#:   但若哪天退回网页导出，必须把它也加进 `shen-web` 组。
EXCLUSIVE_GROUPS = {
    "shen-web": ("BEGL",),
    #: ★ 2026-11-12 修：BANGL / BSGL 的 `hx_ship` 打的是**同一扇**紫鸟窗口
    #:   （`hx_oms_ship.STORE_ID` 写死 TK-Savonire-YJF 27663658415319）。
    #:   并行跑＝互相抢页：一店粘贴的单号被另一店覆盖，两店 verify 都读到 0 单
    #:   （现象：hx_ship rc=1/42、found=0、全部「未查到」）。⇒ 必须串行。
    "hx-oms": ("BANGL", "BSGL", "BD"),
    #: ★ 2026-11-12 补 BD：BD 与 BAN 共用**同一个帕沃尔浏览器实例**（`pw_hwc_flow.PORT=9333`），
    #:   并行会互相抢页（粘贴的单号被覆盖、下单代发落到别人批次上）。
    #:   ⇒ 放在同一组里串行；组内顺序按 STORES 顺序（BANGL → BSGL → BD）。
    #: ★★ 2026-11-12 10:5x 再修：光把这几个店串起来**不够** —— BHY / BEL 同样打帕沃尔 9333，
    #:   它们不在本组 ⇒ 当天 BHY 61/61 + BEL 22/22 + BAN 2/2 帕沃尔下单全军覆没
    #:   （签名一致：查询回来的是整店默认列表 61 条，本批 2~61 单，判据不成立即 FATAL）。
    #:   ⇒ 现在帕沃尔页面的串行**下沉到 `run_pw_fulfill._pw_lock()`**（锁键 `PW`）：
    #:   不管谁调、几路并行，进帕沃尔段前都要拿这把锁，所以本表**不需要**再按店串行。
    #:   这里保留 `hx-oms` 组是因为恒信侧是**紫鸟窗口**，锁在窗口那一侧，两种锁互不相干。
}

#: 每店超时（秒）。BHY 历史上最慢到 23 分钟，给足余量。
STORE_TIMEOUT = 3 * 3600

#: ★ 2026-11-12 用户令：「帕沃尔串行发货：优先 SHEIN 店铺，其次 TEMU 店铺；比如当 BD 在帕沃尔仓
#:   发完货后紧接着去领星发货，不要等待所有店铺在帕沃尔发完才去领星发货」。
#: 落地方式：
#:   ① **线程启动顺序** = SHEIN 店先（BANGL / BSGL / BD），TEMU 店后（BHY / BEL）；
#:   ② TEMU 链路启动前先让帕沃尔页面给 SHEIN 店 `SHEIN_HEADSTART_S` 秒（可用环境变量
#:      `FULFILL_SHEIN_HEADSTART` 调，0 = 不等）；
#:   ③ 「帕沃尔发完立刻去领星」本来就这么跑：**每店一个子进程跑完整整链**，`hx_ship` 紧跟
#:      自己的 `pw_*` 之后，不会被别的店还没发完的帕沃尔步骤挡住。
#: ★ 2026-11-13 用户令再改：「五个店铺的帕沃尔发货改成无头，**并行**发货五个店铺」
#:   ⇒ 帕沃尔不再是「单实例单页」：每店一个 Chrome 端口 + 独立 profile
#:   （见 `run_temu_fulfill.PW_PORTS`），五店互不抢页面，SHEIN 店不必给 TEMU 店让路。
#:   所以默认值改成 0；要回退「先让 SHEIN 店占页面」的老行为，
#:   设 `FULFILL_SHEIN_HEADSTART=180`。
SHEIN_HEADSTART_S = float(os.environ.get("FULFILL_SHEIN_HEADSTART", "0"))

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
        chains.append([s for s in DISPATCH if s in members])
        grouped.update(members)
    for s in DISPATCH:
        if s not in grouped:
            chains.append([s])
    chains = [c for c in chains if c]
    # ★ 2026-11-12 用户令：帕沃尔串行「优先 SHEIN 店铺，其次 TEMU 店铺」⇒ 先启动 SHEIN 链路，
    #   让 BANGL/BSGL/BD 先拿到帕沃尔页面；TEMU（BHY/BEL）稍后再进（见 run_chain 的 HEADSTART）。
    chains.sort(key=lambda c: (0 if PLATFORM.get(c[0]) == "SHEIN" else 1))

    def run_chain(chain):
        if SHEIN_HEADSTART_S > 0 and PLATFORM.get(chain[0]) == "TEMU":
            log("[fulfill-daily] TEMU 店（%s）等 SHEIN 店先占帕沃尔 %.0fs"
                % (" → ".join(chain), SHEIN_HEADSTART_S))
            time.sleep(SHEIN_HEADSTART_S)
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


SOFT_ONLY = 3


def is_soft_only(r: dict) -> bool:
    """这一店是不是「只有软失败」（硬失败为空、软失败非空）。

    ⚠ 判据只认门店自己写的 summary，**不看 rc**：
      · BAN 那种 42 是「有步骤软失败」统一打出来的，本身分不出软硬；
      · 没有 summary 的（导出失败 / 抛异常）说明这店压根没跑起来 ⇒ 一律算硬。
    """
    if r.get("ok"):
        return False
    s = r.get("summary") or {}
    return not (s.get("hard_failed") or []) and bool(s.get("soft_failed"))


def verdict_code(results: list) -> int:
    """汇总退出码。纯函数（单测钉住）：0 全绿 / 3 只有软失败 / 1 部分硬失败 / 42 全失败。"""
    bad = [r for r in results if not r.get("ok")]
    if not bad:
        return 0
    hard_bad = [r for r in bad if not is_soft_only(r)]
    if not hard_bad:
        return SOFT_ONLY
    # 「全失败」＝**所有店都没发出去**（软硬混着也算），沿用原口径 42
    return 42 if len(bad) == len(results) else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    log("[fulfill-daily] %s 开始，并行店：%s（跳过 %s：整链 exit 2 / 人工发现阶段）"
        % (time.strftime("%Y-%m-%d %H:%M:%S"), " / ".join(DISPATCH),
           " / ".join(MANUAL_ONLY)))
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
                                   "soft_only": [r.get("store") for r in bad if is_soft_only(r)],
                                   "push": push_result}, ensure_ascii=False))
    code = verdict_code(results)
    if code == SOFT_ONLY:
        log("[fulfill-daily] 只有软失败（%s）→ rc=3 带告警成功：报表已推，飞书/手机不报失败"
            % " / ".join(str(r.get("store")) for r in bad))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
