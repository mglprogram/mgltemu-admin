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
        proc = subprocess.Popen(
            cmd,
            cwd=PROJ,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
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
