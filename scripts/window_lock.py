# -*- coding: utf-8 -*-
"""window_lock.py — 紫鸟店铺窗口「跨进程单飞锁」（TEMU 全域共享实现）。

为什么有它（2026-10-14 立）：
    面板时间轴盘出 6 个任务会用到紫鸟店铺窗口（BHY / BEL）。目前**实测时刻是错开的**、真冲突 0，
    但那是"排班凑巧"，不是"机制保证"——任何一次改动（加任务、改时间、任务卡住拖到下一个时刻）
    都会让两条作业同时碰**同一个店窗口**。窗口只有一个，双跑会互相读错列表、互相清障，
    代价是白跑甚至重复下单（2026-09-28 买面单域已实测过一次，见 power/run_temu_buylabel.py）。

    本模块把那个域内锁**提升为全域共享实现**，供 TEMU 各 run_*.py 在碰窗口前统一取锁。

契约（与 power 域那份一致，逐条单测钉死）：
    ① 拿到锁＝落记事件（pid/键/动作/起跑时间），跑完＝清干净（不留垃圾锁把下一条挡在门外）；
    ② 同一把键上第二条作业**在碰窗口之前**就被拒（WindowBusy），不同店互不影响；
    ③ 陈旧锁（进程没了 / 记事超时）能自动接管 —— 否则崩一次就永久锁死这条链；
    ④ 关掉开关（enabled=false）＝退回"只靠各自守卫"，不落文件、不拦人。

与 power 域那份的关系：**键不同、互不干扰**。power 域的键是店后缀（BEL/BHY）落在
`_logs/temu-buylabel/locks`；本模块的键是**紫鸟窗口名**（如 `BHY+BEL`）落在
`_logs/window-locks`。买面单流程同时占两个窗口时，会同时持有两把锁 —— 这是有意的：
它确实同时用了两家店的窗口，就该按窗口粒度分别占住。

手工排查：
    python scripts/window_lock.py --status            # 看现在谁占着哪个窗口
    python scripts/window_lock.py --release "BHY+BEL" # 确认死锁后强制放锁
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime

_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCK_DIR = os.path.join(_WS, "_logs", "window-locks")

#: 缺省陈旧阈值（分钟）：记事里进程已不在，或记事超过这个时长 → 可接管。
#: 取 30 是有意的：最长的一条窗口任务超时上限是 45 分钟（Overdue 回到 45），
#: 而真正的浏览器任务实测都 ≤3 分钟；30 分钟足以覆盖"卡住但还活着"的边界，
#: 又不至于崩一次就把早高峰那一串全挡在门外。
DEFAULT_STALE_MINUTES = 30

#: 调度器任务的超时上限（分钟）。Task Scheduler 到点会**强杀任务**，但 python 子进程
#: 往往活下来变孤儿 —— 孤儿 pid 真实存在，`_pid_alive` 判"活着"，锁就永不过期。
#: 实测 2026-10-26：Overdue 11:00 触发、45 分钟上限 11:45 被强杀（rc=267014），
#: 但 3 个 python 子进程还活着并继续占着 BEL 锁 ⇒ 会阻塞**明天**所有用 BEL 窗口的任务。
#: ⇒ 只要记事比「任务超时上限 + 缓冲」还老，就一律判陈旧，不管 pid 在不在。
_GRACE_MINUTES = 10


def _task_timeout_minutes(action: str = "") -> int:
    """本任务自己的超时上限（分钟），按注册表的 `action` 精确匹配。

    ★ 为什么必须按任务取、不能取全局最大值（2026-10-26）：
      窗口任务里 Overdue 是 45 分钟、flux 是 60 分钟。若一律用 60+10=70 分钟判孤儿，
      Overdue 被强杀后要**白等 25 分钟**才有人能接管它的锁 —— 而它下一班就在明天。
      按 action 取到自己的上限（45），阈值 45+10=55，够用又不误判。

    `action` 是 `window_lock(key, action)` 传进来的动作名（如 `overdue-confirm`）。
    匹配不上就退回全局最大值（保守：宁可多等，也不误接管正在跑的任务）。
    """
    fallback = 45
    try:
        reg = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                           "scheduled-tasks", "tasks.json")
        with open(reg, encoding="utf-8-sig") as f:
            tasks = (json.load(f) or {}).get("tasks") or []
        vals = {}
        for t in tasks:
            if not t.get("窗口锁"):
                continue
            try:
                v = int(t.get("timeout_minutes") or 0)
            except (TypeError, ValueError):
                continue
            if v:
                vals[str(t.get("name") or "")] = v
        if not vals:
            return fallback
        # action 名与注册表任务名不是一一对应，用**显式映射表**（别靠子串猜 ——
        # 2026-10-26 实测踩到：`overdue-confirm` 的子串匹配误命中别的任务，
        # 于是 overdue 拿到 60 分钟、孤儿阈值变 70，被强杀后要白等 25 分钟）。
        _ACTION_TO_TASK = {
            "overdue-confirm": "TEMU-Overdue-Confirm-Daily",
            "flux-weekly": "TEMU流量加速周更",
            "bel-abnormal": "TEMU-BEL-Abnormal-Daily",
            "presale-notify": "TEMU-Presale-Notify",
            "limited-watch": "TEMU限流监控",
            "today-sales": "TEMU-Today-Sales",
            "goods-onsale": "TEMU-Goods-OnSale-Daily",
        }
        task_name = _ACTION_TO_TASK.get((action or "").strip().lower())
        if task_name and task_name in vals:
            return vals[task_name]
        # 表里没有：退回**最小**超时（保守 —— 孤儿占锁会挡住第二天整天，
        # 早接管更划算；误接管的风险由 stale_seconds 那道判定兜着）
        return min(vals.values())
    except Exception:                                        # noqa: BLE001
        return fallback


class WindowBusy(RuntimeError):
    """同一把窗口锁上已有作业在跑。"""


def _pid_alive(pid: int) -> bool:
    """进程还在不在。**判不了就当活着** —— 宁可拒绝一条作业，也不允许两条同时碰一个窗口。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=15).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)                     # 非 Windows：0 号信号＝只探活
        return True
    except ProcessLookupError:
        return False
    except Exception:                       # noqa: BLE001  探不了（权限/命令缺失）→ 保守当作活着
        return True


def _lock_is_stale(rec: dict, now: float, stale_seconds: float,
                   action: str = "") -> bool:
    """陈旧锁判据（纯函数，可单测）：记事里的进程没了，或记事超时（崩了没清锁）。

    ★ 2026-10-26 补第三种：**孤儿占锁**。Task Scheduler 到点强杀任务（rc=267014），
      但 python 子进程常常活下来 —— pid 真实存在，所以"进程没了"这条判不出来，
      锁会被一个**已经没有主人在跑**的进程永远占着，挡住第二天所有同窗口任务。
      ⇒ 记事超过「本任务超时上限 + 缓冲」一律判陈旧。
      （正常任务不可能超过自己的超时上限还在跑，超了就是已被调度器杀掉的孤儿。）
    """
    if not isinstance(rec, dict):
        return True
    age = now - float(rec.get("started_at") or 0)
    if age > float(stale_seconds or 0):
        return True
    if not _pid_alive(int(rec.get("pid") or 0)):
        return True
    # 孤儿：任务早该被调度器杀掉了，进程却还在
    if age > (_task_timeout_minutes(action) + _GRACE_MINUTES) * 60:
        return True
    return False


def _read_lock(path: str) -> dict:
    """读记事件；读不出/空的返回 {}（调用方按「可能刚建还没写」处理，不直接判死）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stores_of(key: str) -> tuple:
    """窗口键 → 涉及店铺集合，**排序去重**。`"BHY+BEL"` → `("BEL","BHY")`；`"BEL"` → `("BEL",)`。

    为什么不能拿字符串直接当锁名（2026-10-14 实测的口径陷阱）：
    注册表里同一扇 BEL 窗口有两种写法 —— 5 个任务写 `BHY+BEL`、`TEMU-BEL-Abnormal-Daily` 写 `BEL`。
    若按字符串直比，这两者**永不冲突**，而它们恰恰抢的是同一个窗口 ⇒ 锁形同虚设。
    所以锁的粒度是**店铺**，不是窗口名：同一组店＝同一把锁。
    """
    parts = [p.strip().upper() for p in str(key or "").split("+")]
    return tuple(sorted({p for p in parts if p}))


def lock_files(key: str) -> list:
    """窗口键 → **每店一把**锁文件，按店名排序后依次申请（`BHY+BEL` → 2 个文件）。

    为什么不合成一个文件：同店的两种写法（`BHY+BEL` / `BEL`）必须相撞，而"合起来"没法用一个
    文件名表达"我占了 BEL"和"我占了 BEL+BHY"两个不同范围。**按店一件锁**让重叠自动相撞，
    且与 power 域买面单锁的键（店后缀 BEL/BHY）粒度一致。
    """
    st = stores_of(key)
    if not st:
        return [os.path.join(_LOCK_DIR, re.sub(r"[^0-9A-Za-z_.-]", "_", str(key)) + ".lock")]
    return [os.path.join(_LOCK_DIR, re.sub(r"[^0-9A-Za-z_.-]", "_", s) + ".lock") for s in st]


def lock_path(key: str) -> str:
    """窗口键 → 主锁文件路径（**仅供排查显示**；多店时以 `lock_files` 为准）。"""
    return lock_files(key)[0]


@contextmanager
def window_lock(key: str, action: str, stale_minutes: float = DEFAULT_STALE_MINUTES,
                enabled: bool = True, wait_seconds: float = 0.0, log=None):
    """占住紫鸟窗口 `key`（如 `BHY+BEL`）跑一段作业；拿不到就抛 WindowBusy。

    `wait_seconds` > 0 时先等这么久（每 5 秒重试一次）再放弃 —— 给"我知道它可能刚跑完"的
    调用方用；**默认 0＝立刻拒绝**，因为定时任务错开时刻是设计意图，排队等待会挤掉后面的班次。

    `log(msg)` 可选，把取锁/接管/放锁写进调用方日志（留痕）。
    """
    def _say(msg: str) -> None:
        if log is None:
            return
        try:
            log(msg)
        except Exception:                       # noqa: BLE001
            pass

    if not key or not enabled:
        yield
        return

    stale_seconds = float(stale_minutes or DEFAULT_STALE_MINUTES) * 60
    os.makedirs(_LOCK_DIR, exist_ok=True)
    paths = lock_files(key)
    me = {"pid": os.getpid(), "key": str(key), "action": action,
          "started_at": time.time(),
          "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    deadline = time.time() + max(float(wait_seconds or 0), 0)
    while True:
        held: list = []                      # 本次已拿到的（用于失败时回滚，避免自己造成死锁）
        try:
            for path in paths:
                while True:
                    try:
                        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            json.dump(me, f, ensure_ascii=False)
                        held.append(path)
                        break
                    except FileExistsError:
                        old = _read_lock(path)
                        if not old:     # 别人刚建好还没写进内容（O_EXCL 与 write 之间有窗口）→ 等一下再看
                            time.sleep(0.5)
                            old = _read_lock(path)
                        if not _lock_is_stale(old, time.time(), stale_seconds, action):
                            raise WindowBusy(
                                "紫鸟窗口 %s 上已有作业在跑（pid=%s action=%s 起于 %s，已 %.0f 分钟）"
                                "→ 等它跑完再跑；同一时刻只允许一条（窗口只有一个，双跑会互相读错列表/"
                                "互相清障）。确认是死锁可跑：python scripts/window_lock.py --release \"%s\""
                                % (key, old.get("pid"), old.get("action"), old.get("started"),
                                   (time.time() - float(old.get("started_at") or 0)) / 60, key))
                        _say("[窗口锁] 接管陈旧锁 %s（pid=%s 已不在或超 %d 分钟）"
                             % (path, old.get("pid"), int(stale_seconds // 60)))
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            _say("[窗口锁] 已占住 %s（stores=%s action=%s pid=%d）"
                 % (key, "+".join(stores_of(key)) or key, action, os.getpid()))
            break
        except WindowBusy:
            for p in held:                  # ★ 回滚：没拿全就全放掉，否则"半占"会把别人挡在门外
                cur = _read_lock(p)
                if not cur or int(cur.get("pid") or 0) == os.getpid():
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            if time.time() < deadline:
                time.sleep(5)
                continue
            raise
    try:
        yield
    finally:
        # ★ 只删自己的锁：极端情况下"我"的锁被别人判陈旧后接管了，
        #   此时文件里是**别人的**记事，删掉等于把别人的锁偷走（双跑）。
        for path in paths:
            cur = _read_lock(path)
            if not cur or int(cur.get("pid") or 0) == os.getpid():
                try:
                    os.remove(path)
                except OSError:
                    pass
            else:
                _say("[窗口锁] %s 已被 pid=%s 接管，不删（避免偷走别人的锁）"
                     % (path, cur.get("pid")))
        _say("[窗口锁] 已释放 %s" % key)


def status() -> list:
    """列出当前所有锁 → [{"key","path","pid","action","started","started_at","stale","alive"}]。"""
    out = []
    if not os.path.isdir(_LOCK_DIR):
        return out
    now = time.time()
    for name in sorted(os.listdir(_LOCK_DIR)):
        if not name.endswith(".lock"):
            continue
        p = os.path.join(_LOCK_DIR, name)
        rec = _read_lock(p)
        rec.setdefault("key", name[:-5])
        rec["file"] = name[:-5]                 # 锁的文件名（＝店铺名），排查时比记事里的 key 更直接
        rec["path"] = p
        rec["stale"] = _lock_is_stale(rec, now, DEFAULT_STALE_MINUTES * 60, str(rec.get("action") or ""))
        rec["alive"] = _pid_alive(int(rec.get("pid") or 0))
        out.append(rec)
    return out


def release(key: str) -> bool:
    """强制放锁（**只给确认死锁后人工用**）。返回是否真的删掉了文件。"""
    ok = False
    for p in lock_files(key):
        try:
            os.remove(p)
            ok = True
        except OSError:
            pass
    return ok


def _main() -> int:
    ap = argparse.ArgumentParser(description="紫鸟窗口单飞锁（排查工具）")
    ap.add_argument("--status", action="store_true", help="看现在谁占着哪个窗口")
    ap.add_argument("--release", metavar="WINDOW", help="强制放锁（确认死锁后用）")
    a = ap.parse_args()
    if a.release:
        print("已放锁 %s" % a.release if release(a.release) else "没有这个锁：%s" % a.release)
        return 0
    rows = status()
    if not rows:
        print("当前没有窗口锁（空闲）")
        return 0
    print("当前占用：")
    for r in rows:
        state = "陈旧(可接管)" if r.get("stale") else "占用中"
        print("  %-8s %-12s pid=%-8s action=%-12s 起于 %-19s %s"
              % (r.get("file") or r.get("key"), state, r.get("pid"), r.get("action"),
                 r.get("started"), ("(窗口 %s)" % r["key"]) if r.get("key") and
                 r.get("key") != r.get("file") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
