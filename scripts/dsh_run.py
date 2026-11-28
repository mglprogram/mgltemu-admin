#!/usr/bin/env python3
"""Execute ONE business command and record its REAL exit code to a sentinel file.

This is the ONLY thing the DSH agent is allowed to use to run a task. It exists
because the agent cannot be trusted to report an exit code faithfully, and the
orchestrator (dsh_task.py) refuses to trust the model's word.

Contract:
  * Writes one JSON line to <sentinel>/attempts.jsonl per invocation.
  * That line holds the ground-truth `rc`, taken from the child process itself.
  * Passes the child's rc through as its own exit code.

Usage:
    python scripts\\dsh_run.py --task TEMU-Goods-DailyList -- <command...>

Exit codes: the child's own rc; 90 = bad usage.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sentinel_root() -> str:
    """哨兵根目录。优先 `DSH_SENTINEL_ROOT`，否则默认 `PROJ/_logs/dsh-sentinel`。

    ★ 2026-11-06 为什么必须可配置（真机踩到）：
      定时任务被要求「会话落在 D:\\AI-workplace\\发货 这个工作区」，
      而 DSH 的 `workspace-write` 沙箱只允许写**会话工作区**；
      agent 在沙箱里跑 `dsh_run.py` 时往 `D:\\AI-workplace\\TEMU\\_logs\\...`
      写 attempts.jsonl 会被拒（PermissionError Errno 13），包装器直接崩，
      于是 `attempts=0` ⇒ 任务被判「等于没执行」（rc=71）。
      正解：`dsh_task.py` 把哨兵根目录一并指到工作区内（它是 dsh 的父进程，
      不在沙箱里，读写不受限），父子两边读同一个环境变量。
    """
    return os.environ.get("DSH_SENTINEL_ROOT") or os.path.join(
        PROJ, "_logs", "dsh-sentinel")


SENTINEL_ROOT = _sentinel_root()

BAD_USAGE = 90


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _has_console() -> bool:
    """本进程有没有自己的控制台窗口。

    定时任务走 wscript + run_hidden.vbs ⇒ 没有控制台 ⇒ 子进程必须隐藏控制台，
    否则 Windows 会给 .cmd 新开一个可见的黑框（2026-11-27 用户截图报的就是这个：
    `[dsh_run] task=TEMU-Presale-Notify cmd=...` 那个 cmd.exe 窗口）。
    人工在终端里跑时不改，避免把调用者的控制台藏起来。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:  # noqa: BLE001 - 判不了就当无人值守（保守：不弹窗）
        return False


def sentinel_dir(task: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task) or "task"
    return os.path.join(SENTINEL_ROOT, safe)


def append_attempt(task: str, record: dict) -> str:
    d = sentinel_dir(task)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "attempts.jsonl")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path


def _resolvable(tok: str) -> bool:
    """这段命令能不能被执行：现存文件（含 .cmd/.py/.ps1）或 PATH 上找得到。

    Windows 下 `Popen` 对 `.cmd`/`.bat` 会走 cmd.exe、对 `.py` 不会（需先补解释器），
    所以这里只判"存不存在／找不找得到"，解释器补全交给上面那段已有逻辑。
    """
    t = str(tok or "").strip().strip('"')
    if not t:
        return False
    if os.path.isfile(t):
        return True
    if os.sep in t or "/" in t:          # 带路径但不是文件 → 明确不存在
        return False
    return bool(shutil.which(t))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--task", required=True, help="registry task name (sentinel folder)")
    ap.add_argument("--note", default="", help="why this attempt is being made")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="-- <command...>")
    args = ap.parse_args(argv)

    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("dsh_run.py: no command given (usage: ... --task NAME -- <cmd...>)", file=sys.stderr)
        return BAD_USAGE

    # ★ 2026-11-07 修（真机踩到，差点让每日发货任务每次首跑都失败）：
    #   注册表里 `dsh.inner`/prompt 给的是一个**裸 .py 路径**
    #   （如 `...\run_fulfill_daily_inner.py`）。`Popen` 直接执行它时
    #   Windows 不走 PATHEXT/关联，报 `OSError(8) 不是有效的 Win32 应用程序`，
    #   包装器 rc=1、attempts 一条都写不进去 ⇒ agent 被判「等于没执行」。
    #   这里自动补 `python` 解释器前缀：.py 用当前解释器，.ps1 用 powershell，
    #   .cmd/.bat 交给 cmd.exe（Popen 对 .cmd 本来就能跑，不加也行）。
    if cmd and not any(c in cmd[0] for c in (" ",)) and cmd[0].lower().endswith(".py") \
            and os.path.isfile(cmd[0]):
        cmd = [sys.executable] + cmd
        print(f"[dsh_run] injected interpreter for .py inner: {cmd[0]}", flush=True)
    elif cmd and cmd[0].lower().endswith(".ps1") and os.path.isfile(cmd[0]):
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File"] + cmd
        print("[dsh_run] injected powershell for .ps1 inner", flush=True)

    started = time.time()
    started_iso = _now()
    print(f"[dsh_run] task={args.task} cmd={' '.join(cmd)}", flush=True)

    # ★ 2026-11-19 加：**先验命令形状**，别让"agent 把一句话当命令"伪装成业务失败。
    #   真机证据（`_logs/dsh-sentinel/TEMU-Fulfill-Daily/attempts.jsonl` 2026-11-19）：
    #     "cmd": ["attempt1", "全体子进程", "04:01:56", "被外部", "Ctrl+C(3221225786)", ...]
    #   —— agent 把诊断结论当成命令行传进来，Popen 抛 FileNotFoundError ⇒ 记 rc=127。
    #   于是 127 被当成"业务脚本挂了"上报（今天的流量加速周更告警就是这么来的），
    #   真正的病根（命令没被正确执行）被埋掉。
    #   ⇒ 首段既不是现存文件、也不在 PATH 上 → 直接判**用法错(90)**，并在 stderr 明确
    #     告诉 agent 正确写法。attempts 里照样记一条（rc=90），所以不会被当成"没执行"。
    if not _resolvable(cmd[0]):
        print(f"[dsh_run] bad usage: 第一段命令不是可执行文件/现存脚本：{cmd[0]!r}\n"
              f"[dsh_run] 正确写法示例：python scripts\\dsh_run.py --task \"<任务名>\" -- "
              f"\"D:\\AI-workplace\\TEMU\\scripts\\tasks\\run_xxx.cmd\"",
              file=sys.stderr, flush=True)
        rc = BAD_USAGE
        record = {
            "ts": started_iso, "finished": _now(), "rc": rc, "elapsed_s": 0.0,
            "cmd": cmd,
            "note": (args.note + " | bad-usage: 第一段不是可执行命令").strip(" |"),
            "output_log": "",
        }
        print(f"[dsh_run] rc={rc} (没有真正执行任何业务命令) -> "
              f"{append_attempt(args.task, record)}", flush=True)
        return rc

    # Tee the child's output to a file as well as to our own stdout.
    #
    # WHY: the business .cmd already redirects its own log, but anything it
    # prints outside that redirect (and anything we print) only ever reached the
    # agent's context - it never hit disk. When a run went wrong the operator had
    # nothing to read. This file is the transcript of the attempt itself.
    out_path = os.path.join(sentinel_dir(args.task), "last_output.log")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        sink = open(out_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        sink = None

    def _tee(stream):
        def _run():
            try:
                for line in iter(stream.readline, ""):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    if sink is not None:
                        sink.write(line)
                        sink.flush()
            except Exception:  # noqa: BLE001 - a broken pipe must not change rc
                pass
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        return _run

    try:
        # ★ 2026-11-27 用户报「不让这种 cmd 弹窗出现」：
        #   截图里是 `[dsh_run] task=TEMU-Presale-Notify cmd=...` 那个黑框。
        #
        #   真因（比表面复杂）：`run_hidden.vbs` 起的是
        #   `WScript.Shell.Run(cmd, 0, True)` —— **窗口只是被隐藏，控制台仍然存在**。
        #   于是本进程继承了一个「有控制台但不可见」的环境，再用裸 `Popen` 起 `.cmd`
        #   时，Windows 给子进程**新分配一个可见控制台** ⇒ 弹黑框。
        #
        #   ⇒ 不能靠 `_has_console()` 判断（它恒为 True，会把问题放过去）。
        #     这里一律 `CREATE_NO_WINDOW`：子进程拿到「存在但无窗口」的控制台，
        #     cmd.exe 自己那层不弹；它再派生的 python 会继承同一个隐藏控制台，
        #     整链都不弹。这也是 `lib/quiet.py` 文档里的叶子/派生两用口径。
        #
        #   例外：`PW_SHOW_CONSOLE=1` 时保持原样（人肉调试想看中间输出）。
        _kw = dict(
            cwd=PROJ,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if os.name == "nt" and os.environ.get("PW_SHOW_CONSOLE") != "1":
            _kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, **_kw)
        # Drain on a thread so a chatty child cannot deadlock on a full pipe.
        import threading

        drain = threading.Thread(target=_tee(proc.stdout), daemon=True)
        drain.start()
        rc = proc.wait()
        drain.join(timeout=5)
    except FileNotFoundError as exc:
        rc = 127
        print(f"[dsh_run] command not found: {exc}", file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        rc = 126
        print(f"[dsh_run] failed to launch: {exc!r}", file=sys.stderr, flush=True)
    finally:
        if sink is not None:
            try:
                sink.close()
            except OSError:
                pass

    elapsed = round(time.time() - started, 1)
    record = {
        "ts": started_iso,
        "finished": _now(),
        "rc": rc,
        "elapsed_s": elapsed,
        "cmd": cmd,
        "note": args.note,
        "output_log": out_path,
    }
    path = append_attempt(args.task, record)
    print(f"[dsh_run] rc={rc} elapsed={elapsed}s -> {path}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
