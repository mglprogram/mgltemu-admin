# -*- coding: utf-8 -*-
"""panel_refresh.py — 「任务跑完刷一次面板」的统一入口（含**合并**与**互斥**）。

用户要求（2026-10-14）："认为可以定时任务每被执行一次，可以刷新一次这两个面板"（时间轴 + 任务列表）。

为什么不能简单地"每个任务跑完就 publish 一次"：
    · 一次发布实测 **2~4 分钟**（build + 注入门禁 + wrangler 上传）。
    · 09:00 有 **3 个任务同时触发**（WorkBuddyGrowthPoll / CreateDailyFolder / PWShipping），
      08:00~08:50 也是一串挨着的。各自 publish 会**并发部署到同一个 Pages 项目**，
      互相覆盖（`--branch main` 谁后上传谁生效），既浪费额度又可能把旧数据盖上去。
    · 上一轮实测过的同类事故：老看板与 admin 面板同时部署，admin 08:00:46 发、老看板 08:02:19 发，
      **后发的赢**，面板被旧数据覆盖。

所以这里做三件事：
    ① **合并（coalesce）**：任务跑完不直接发布，而是写一个"待刷新"标记；
       若已有发布在跑，就直接返回 —— 那次发布**在结束前会再检查一次标记**，
       有新改动就再发一轮。N 个任务挤在同一分钟 ⇒ 最多 2 次发布，而不是 N 次。
    ② **互斥（lock）**：同一时刻只允许一个发布进程（跨进程文件锁，进程死了能接管）。
    ③ **不阻塞业务**：调用方用 `Start-Process` 异步触发，发布失败只记日志，**绝不影响任务本身的退出码**。

用法（各 run_*.cmd / 任务末尾调用）：
    python scripts\\panel_refresh.py --request --reason "on-sale-daily"

`--request` 的行为：
    · 落标记（`_logs/panel-refresh/pending.json`）；
    · 若当前没有发布在跑 → **立刻**接过去跑（同时把标记清掉）；
    · 若已有发布在跑 → 立刻返回（由那次发布收尾时补跑）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "_logs", "panel-refresh")
PENDING = os.path.join(STATE_DIR, "pending.json")
LOCK = os.path.join(STATE_DIR, "publish.lock")
RUNLOG = os.path.join(STATE_DIR, "refresh.log")

#: 锁多久算陈旧（分钟）。一次发布 2~4 分钟；15 分钟足够覆盖慢网络，
#: 又不至于让一次崩溃把"任务跑完刷面板"永久卡死。
STALE_MINUTES = 15

#: 单次发布最长等多久（秒）。超过就放弃这一轮，避免把任务进程一直挂着。
PUBLISH_TIMEOUT = 1800


def _say(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(RUNLOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """进程还在不在。判不了就当活着（宁可少发一次，也不并发部署）。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=15).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:                                   # noqa: BLE001
        return True


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)                                # 原子替换，避免读到半个文件


# ------------------------------------------------------------------ 待刷新标记
def mark_pending(reason: str) -> dict:
    """落/追加一条待刷新标记 → 返回标记内容。"""
    old = _read_json(PENDING)
    reasons = [r for r in (old.get("reasons") or []) if isinstance(r, str)]
    if reason and reason not in reasons:
        reasons.append(reason)
    rec = {"requested_at": time.time(),
           "requested": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "reasons": reasons[-20:],                      # 只留最近 20 条，别把文件撑大
           "count": int(old.get("count") or 0) + 1}
    _write_json(PENDING, rec)
    return rec


def take_pending() -> dict:
    """取走标记（原子删）→ 返回被取走的内容（没有则 {}）。"""
    rec = _read_json(PENDING)
    if rec:
        try:
            os.remove(PENDING)
        except OSError:
            pass
    return rec


def has_pending() -> bool:
    return os.path.exists(PENDING)


# ------------------------------------------------------------------ 发布互斥锁
def acquire_lock() -> bool:
    """抢占发布锁。抢到 True；已被活着的进程占着 False；陈旧锁接管 True。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    me = {"pid": os.getpid(), "started_at": time.time(),
          "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(me, f, ensure_ascii=False)
            return True
        except FileExistsError:
            old = _read_json(LOCK)
            if not old:                                  # 刚建好还没写内容 → 等一下再看
                time.sleep(0.5)
                old = _read_json(LOCK)
            stale = (not _pid_alive(int(old.get("pid") or 0))
                     or (time.time() - float(old.get("started_at") or 0)) > STALE_MINUTES * 60)
            if not stale:
                return False
            _say("接管陈旧发布锁（pid=%s，起于 %s）" % (old.get("pid"), old.get("started")))
            try:
                os.remove(LOCK)
            except OSError:
                pass
    return False


def release_lock() -> None:
    """只删自己的锁（被别人接管时不偷走）。"""
    cur = _read_json(LOCK)
    if not cur or int(cur.get("pid") or 0) == os.getpid():
        try:
            os.remove(LOCK)
        except OSError:
            pass


@contextmanager
def publish_lock(reason: str = ""):
    """**给任何"要发布"的代码用的**互斥上下文（不只本模块内部）。

    为什么必须对外暴露（2026-10-15 实测事故）：发布不止一条路径 ——
    各任务收尾的 `notify_panel`、定时的 `run_panel_refresh`、`run_goods_daily` 自己那次、
    `refresh_today_sales` 的 `--data-only`。只要有人绕过锁直接调 `publish_admin.py`，
    就可能**并发部署到同一个 Pages 项目**：当天冷启动后早高峰 6 个任务同时跑，
    3 个发布进程重叠 ⇒ 两个 wrangler 上传失败（`Failed to upload files`）、
    还有一个 `wrangler whoami` 抖动被误判成"未登录 Cloudflare"。

    拿不到锁时抛 `PublishBusy`（调用方决定是排队、跳过还是报错），**不静默并发**。
    """
    if not acquire_lock():
        cur = _read_json(LOCK)
        raise PublishBusy(
            "已有发布在跑（pid=%s 起于 %s）→ 本次不并发发布"
            % (cur.get("pid"), cur.get("started")))
    _say("[发布锁] 已占住（%s）" % (reason or "?"))
    try:
        yield
    finally:
        release_lock()


class PublishBusy(RuntimeError):
    """已有发布在跑（并发发布会把部署互相覆盖）。"""


# ------------------------------------------------------------------ 真正发布
def _publish_once() -> tuple:
    """跑一次 publish_admin.py → (rc, 说明)。需要 DASH_PASSWORD。"""
    env = dict(os.environ)
    if not env.get("DASH_PASSWORD"):
        # 面板含店铺货号/库存/申报价，没有密码拒绝发布（与 run_panel_refresh.cmd 同口径）
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetEnvironmentVariable('DASH_PASSWORD','User')"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60).stdout
            if out and out.strip():
                env["DASH_PASSWORD"] = out.strip()
        except Exception:                                # noqa: BLE001
            pass
    if not env.get("DASH_PASSWORD"):
        return 3, "DASH_PASSWORD 未设置，拒绝发布"
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 关键：publish_loop 此刻**已经持有发布锁**，而 publish_admin.py 现在自己也要抢这把锁
    #   （2026-10-15 加的，防止别的路径绕过锁并发部署）。不告诉它一声，子进程会去抢
    #   自己父进程的锁 → 必然 PublishBusy → 发布全废。
    env["PANEL_PUBLISH_LOCK_HELD"] = "1"
    script = os.path.join(HERE, "bel_goods", "publish_admin.py")
    try:
        r = subprocess.run([sys.executable, script, "--build", "--production"],
                           env=env, cwd=ROOT, timeout=PUBLISH_TIMEOUT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 4, "发布超时（%ds）" % PUBLISH_TIMEOUT
    tail = (r.stdout or "").strip().splitlines()[-3:]
    return int(r.returncode or 0), " | ".join(tail)


def publish_loop(max_rounds: int = 3) -> int:
    """在锁内发布；**发布前后都检查标记**，有新请求就再发一轮（合并的关键）。

    返回最后一次的 rc。`max_rounds` 防死循环（每轮都会重新检查标记）。
    """
    if not acquire_lock():
        _say("已有发布在跑 → 本次合并进那一轮")
        return 0
    try:
        rc = 0
        for i in range(1, max_rounds + 1):
            took = take_pending()                        # 先清标记，再发布（发布期间的新请求会重新落标记）
            if i > 1 and not took:
                break                                    # 没有新请求了，收工
            why = "+".join(took.get("reasons") or []) or "timer"
            _say("发布第 %d 轮（触发：%s）" % (i, why))
            rc, note = _publish_once()
            _say("发布第 %d 轮结束 rc=%s%s" % (i, rc, ("（%s）" % note) if note else ""))
            if rc != 0:
                break                                    # 失败就别再叠一轮（省额度，留日志给人看）
            if not has_pending():
                break                                    # 发布期间没有新请求 → 不用再发
            _say("发布期间又收到刷新请求 → 再发一轮")
        return rc
    finally:
        release_lock()


def request(reason: str, wait: bool) -> int:
    """任务跑完调这个。`wait=True` 时同步等发布结束（默认异步/立即返回）。

    ★ **绝不抛异常、绝不返回非 0**：调用方是各定时任务的收尾步，刷面板是"附带好处"，
      不能因为它把业务任务的退出码带坏（那会让面板/TaskWatch 把成功误判成失败）。
      所以整个函数体包在 try 里，任何异常都只记日志。
    """
    try:
        mark_pending(reason)
    except Exception as e:                                # noqa: BLE001
        _say("落刷新标记失败（忽略）：%s" % str(e)[:120])
        return 0
    try:
        if acquire_lock():
            # 能拿到锁说明没人发 → 我们自己发（先把这次抢占放掉，交给 publish_loop 统一持锁）
            release_lock()
            if not wait:
                _spawn_background(reason)
                return 0
            return publish_loop()
        # 拿不到锁 ⇒ 已有发布在跑。标记已经落下，那一轮收尾会自动补跑。
        _say("已登记刷新请求（%s）→ 合并进正在跑的那一轮" % reason)
        return 0
    except Exception as e:                                # noqa: BLE001
        _say("刷新请求处理失败（忽略，不影响任务退出码）：%s" % str(e)[:120])
        return 0


# ---------------------------------------------------------------- 后台发布进程的启动标志
# ★ 2026-10-14 实测（用户报「为什么又会弹出 cmd」）：**绝不能用 DETACHED_PROCESS。**
#
#   `DETACHED_PROCESS` 的语义是「这个进程**完全没有控制台**」——不是「没有窗口」。
#   而发布链是：publisher(python) → publish_admin.py(python) → node/pnpm。
#   子孙进程里只要有一个需要控制台，Windows 就会给它**新分配一个可见的控制台窗口**
#   （窗口标题就是 python.exe 的完整路径）。用户截图里那个黑框就是这么来的。
#
#   实测对照（用**带孙进程**的探针复现；只 spawn 一个睡着的子进程是复现不出来的）：
#     DETACHED_PROCESS|NEW_PROCESS_GROUP  -> 弹出可见窗口（复现）
#     CREATE_NO_WINDOW                    -> 干净，无窗口
#     DETACHED|NEWGRP|CREATE_NO_WINDOW    -> **仍然弹窗**
#   最后一条是最阴的坑：官方文档写明 CREATE_NO_WINDOW「与 DETACHED_PROCESS 同用时被忽略」，
#   所以两者**必须二选一，且只能选 CREATE_NO_WINDOW**。
#
#   CREATE_NO_WINDOW 给进程一个「存在但无窗口」的控制台，子孙进程都继承它 ⇒ 再也不弹。
#
#   ⚠ 这不只是观感问题：那个可见控制台一旦被用户点 X 关掉，会**连带打断正在跑的部署**
#   （实测 16:57 那轮 rc=1，wrangler 日志是 libuv `async.c:94` 断言失败）。
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
#: 仅作反例留档：**不要用**（见上方说明）。单测会断言它不出现在实际标志里。
DETACHED_PROCESS = 0x00000008


def _background_creationflags() -> int:
    """后台发布进程的 creationflags。**纯函数**，便于单测钉死「不许再出现 DETACHED_PROCESS」。"""
    if os.name != "nt":
        return 0
    return CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def _spawn_background(reason: str) -> None:
    """异步起一个发布进程，调用方立刻返回（不拖累任务退出码）。失败只记日志。

    标志位的选择理由见上方 `CREATE_NO_WINDOW` 那段（2026-10-14 踩坑记录）。
    """
    try:
        creationflags = _background_creationflags()
        subprocess.Popen([sys.executable, os.path.abspath(__file__),
                          "--run", "--reason", reason],
                         cwd=ROOT, creationflags=creationflags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        _say("已登记刷新请求（%s）→ 后台发布已启动，立即返回" % reason)
    except Exception as e:                                # noqa: BLE001
        _say("后台发布启动失败（标记已留，下次刷新会带上）：%s" % str(e)[:120])


def status() -> dict:
    lock = _read_json(LOCK)
    return {"pending": _read_json(PENDING) if has_pending() else {},
            "lock": lock,
            "lock_alive": _pid_alive(int(lock.get("pid") or 0)) if lock else False}


def _main() -> int:
    ap = argparse.ArgumentParser(description="任务跑完刷一次面板（合并 + 互斥）")
    ap.add_argument("--request", action="store_true", help="登记一次刷新请求（各任务收尾调用）")
    ap.add_argument("--run", action="store_true", help="做实际发布（供内部/后台进程用）")
    ap.add_argument("--wait", action="store_true", help="同步等发布结束（默认异步立即返回）")
    ap.add_argument("--reason", default="", help="谁触发的（写进日志，便于追溯）")
    ap.add_argument("--status", action="store_true", help="看当前待刷新标记与锁")
    a = ap.parse_args()
    if a.status:
        s = status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    if a.run:
        return publish_loop()
    if a.request:
        return request(a.reason, a.wait)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_main())
