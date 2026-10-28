#!/usr/bin/env python3
"""Run ONE scheduled task through a DSH headless agent session, keeping a real exit code.

WHY THIS EXISTS
---------------
`dsh --profile headless "<task>"` ALWAYS exits 0. Measured 2026-10-26: even when
the only tool call inside the turn failed with `[exit code: 5]`, the process
returned 0, because "the turn completed" is what rc=0 means there.

Every piece of the alerting chain keys off the process exit code:

    run_*.cmd -> exit /b <rc>
        |- manage.py health          -> 成功 / 失败
        |- watch_tasks.py            -> Feishu alert
        |- build_automation_data.py  -> RC_MAP 5/6 = waiting, else failed
        `- panel timeline            -> green / amber / red

So handing a task to an agent would silently paint every failure green. This
orchestrator restores the contract with a two-part sentinel:

  1. attempts.jsonl  - written by `dsh_run.py`, i.e. by CODE, on every attempt.
                       Ground truth. The model cannot forge it.
  2. verdict.json    - written by the model at the end, declaring the final rc.

Trust rules (deliberately paranoid):
  * no verdict          -> 70  (agent never finished the protocol)
  * no attempt at all   -> 71  (agent never actually ran anything)
  * verdict says 0 but the last attempt failed -> 72 unless the model marked
    `resolved: true` AND explained how in `resolution`
  * otherwise           -> the verdict's rc, passed straight through

Usage:
    python scripts\\dsh_task.py --task TEMU-Goods-DailyList --inner scripts\\tasks\\run_temu_goods_topup.cmd
    python scripts\\dsh_task.py --task X --inner Y --timeout 45 --dry-run

Exit codes: the task's real rc; 70/71/72 as above; 124 = orchestrator timeout.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTINEL_ROOT = os.path.join(PROJ, "_logs", "dsh-sentinel")

DSH_CMD = r"D:\DSH-O\resources\runtime\cli\bin\dsh.cmd"
PROFILE = "headless"

NO_VERDICT = 70
NO_ATTEMPT = 71
UNRESOLVED = 72
BAD_USAGE = 90
TIMEOUT = 124


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sentinel_dir(task: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task) or "task"
    return os.path.join(SENTINEL_ROOT, safe)


def reset_sentinel(task: str) -> str:
    d = sentinel_dir(task)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def read_attempts(d: str) -> list[dict]:
    path = os.path.join(d, "attempts.jsonl")
    if not os.path.isfile(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"rc": None, "note": "unparseable attempt line"})
    return out


def read_verdict(d: str) -> dict | None:
    """读 agent 写的 verdict.json。

    ⚠ 必须用 `utf-8-sig`：agent 常用 PowerShell 写这个文件（`Set-Content` /
      `Out-File` 默认带 BOM），而 `json.load` 用严格 `utf-8` 读 BOM 会抛
      `Unexpected UTF-8 BOM`。2026-10-28 实测：TEMU-Today-Sales 18:45 那轮
      verdict.json 明明写好了（rc=0，BOM 开头 EF BB BF），这里却解析失败返回
      None ⇒ 判成「agent 没写 verdict」⇒ 面板报 rc=70「失败」——**假告警**，
      实际任务成功。`utf-8-sig` 对无 BOM 的文件行为完全一样，所以直接换掉。
    """
    path = os.path.join(d, "verdict.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def verdict_problem(d: str) -> str:
    """verdict.json 存在但读不出来时，给出**人话原因**（而不是静默当"没写"）。

    2026-10-28 的事故就是因为"解析失败"和"文件不存在"被压成同一个 None，
    面板只能说"agent 没写 verdict"，把真因（BOM）埋掉了。

    ⚠ 只有**真的读不出来**才返回问题描述。BOM 现已被 `utf-8-sig` 兼容，
      能读出来就不算问题（否则每次正常带 BOM 的文件都会误报）。
    """
    path = os.path.join(d, "verdict.json")
    if not os.path.isfile(path):
        return ""
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return "verdict.json 存在但读不出（%s）" % exc

    has_bom = raw[:3] == b"\xef\xbb\xbf"
    try:
        data = json.loads(raw.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError as exc:
        hint = "（带 BOM，已按 utf-8-sig 解码后仍失败）" if has_bom else ""
        return "verdict.json 不是合法 JSON%s：%s" % (hint, str(exc)[:120])

    if not isinstance(data, dict):
        return "verdict.json 存在但结构不是对象（实际是 %s）" % type(data).__name__
    return ""


def registry_path() -> str:
    env = os.environ.get("SCHEDULED_TASKS_REGISTRY")
    if env:
        return env
    return os.path.join(
        os.path.expanduser("~"), ".agents", "skills", "scheduled-tasks", "tasks.json"
    )


def load_entry(task: str) -> dict | None:
    """Find one task in the scheduled-tasks registry."""
    path = registry_path()
    try:
        # utf-8-sig: the registry is written by PowerShell, which prepends a BOM.
        with open(path, encoding="utf-8-sig") as fh:
            reg = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[dsh_task] cannot read registry {path}: {exc}", file=sys.stderr, flush=True)
        return None
    for entry in reg.get("tasks", []):
        if entry.get("name") == task:
            return entry
    return None


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def read_credential_key() -> str:
    """Pull the DEEPSEEK_API_KEY out of ~/.dsh/.credentials.yaml `refs:`.

    Deliberately a hand-rolled scan rather than a YAML dependency: the file is
    tiny, the shape is stable, and a malformed file must not raise here.
    """
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    path = os.path.join(home, ".credentials.yaml")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    for line in text.splitlines():
        m = re.match(r"\s*DEEPSEEK_API_KEY\s*:\s*(\S+)\s*$", line)
        if m:
            return m.group(1).strip("'\"")
    return ""


def build_prompt(task: str, inner: str, sentinel: str) -> str:
    """The whole protocol, spelled out. Short on purpose: less room to improvise."""
    run_py = os.path.join(PROJ, "scripts", "dsh_run.py")
    inner_abs = inner if os.path.isabs(inner) else os.path.join(PROJ, inner)
    return f"""你是定时任务「{task}」的无人值守执行器。严格按下面 4 步做，不要做任何额外的事。

第 1 步（唯一允许的运行方式）：
在 {PROJ} 目录下执行：
python "{run_py}" --task "{task}" -- "{inner_abs}"

这个包装器会把**真实退出码**写进 attempts.jsonl，并把它作为自己的退出码返回。
不要绕过它直接跑业务脚本，也不要自己编造退出码。

第 2 步：看第 1 步的退出码。
- 如果是 0：直接进第 4 步。
- 如果非 0：读日志（{PROJ}\\_logs\\ 下与本任务相关的目录）判断原因，最多再重试 1 次
  （用同一个包装器命令，可加 --note 说明这次为什么重试）。
  注意退出码 5 / 6 表示「窗口锁/发布锁被占，按设计跳过」，不是故障，**不要重试**。

第 3 步：如果两次都失败，且你判断是环境类的临时问题（登录态、网络、页面没加载），
可以尝试修复后重跑；如果是业务规则或平台侧问题，不要硬试，保留失败。

第 4 步（必须做）：把结论写进这个文件（UTF-8，JSON）：
{sentinel}\\verdict.json
格式：
{{"rc": <最终退出码，整数>, "resolved": <true|false>, "resolution": "<若 rc=0 但曾失败，说明你做了什么让它成功；否则空串>", "diagnosis": "<一句话结论，中文，给运维看>"}}

字段口径：
- rc 必须等于最后一次 attempts.jsonl 里的真实退出码（如果你放弃了，就是那个失败码）。
- 只有「你确实做了什么让任务成功」才允许 rc=0 且 resolved=true；什么都不做就写 rc=0 是错的。
- diagnosis 要具体，不要写「任务失败」这种废话；写清哪一步、什么原因。

最后只回复一行：VERDICT rc=<rc>"""


def run_agent(prompt: str, timeout_s: int, env: dict) -> tuple[int, str]:
    """Run the headless agent. Returns (dsh_rc, combined_tail).

    The prompt goes in over STDIN (`-`), never as an argument: it is multi-line
    and full of quotes/braces, which `cmd /c` would mangle.
    """
    cmd = ["cmd", "/c", DSH_CMD, "--profile", PROFILE, "--json", "-"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJ,
            env=env,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT, f"orchestrator timeout after {timeout_s}s"
    raw = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return proc.returncode, (raw + "\n" + err).strip()


def summarize_events(raw: str) -> dict:
    """Pull the useful bits out of the --json event stream for the log."""
    info: dict = {"tools": [], "final": "", "turn_reason": ""}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "tool_call":
            info["tools"].append(str(ev.get("tool") or "?"))
        elif kind == "final":
            info["final"] = str(ev.get("text") or "")
        elif kind == "status" and ev.get("phase") == "turn_end":
            reason = ev.get("reason") or {}
            if isinstance(reason, dict):
                info["turn_reason"] = str(reason.get("kind") or "")
    return info


def decide(attempts: list[dict], verdict: dict | None,
           verdict_issue: str = "") -> tuple[int, str]:
    """Apply the trust rules. Returns (exit_code, explanation).

    The single hard rule: **rc=0 is only credible when the LAST recorded attempt
    also returned 0.** A genuine repair always appends a fresh attempt via
    dsh_run.py, so there is never a reason to accept "trust me, it worked" on top
    of a failed record. `resolved` / `resolution` are then purely descriptive.

    `verdict_issue` 是 verdict.json 存在却读不出来时的人话原因。带上它，
    这样面板/日志能显示真因（如 BOM），而不是笼统的"agent 没写 verdict"。
    """
    if verdict is None:
        detail = ("；" + verdict_issue) if verdict_issue else ""
        if not attempts:
            return NO_ATTEMPT, "agent 既没跑包装器、也没写 verdict —— 这一轮等于没执行" + detail
        last = _as_int(attempts[-1].get("rc"))
        return NO_VERDICT, f"agent 没写 verdict（最后一次真实退出码 {last}）" + detail

    rc = _as_int(verdict.get("rc"))
    if rc is None:
        return NO_VERDICT, f"verdict.json 里的 rc 不是整数：{verdict.get('rc')!r}"

    if not attempts:
        return NO_ATTEMPT, f"verdict 声明 rc={rc}，但 attempts.jsonl 是空的 —— 没有真实执行记录"

    last_rc = _as_int(attempts[-1].get("rc"))
    prior_failures = [a.get("rc") for a in attempts[:-1] if _as_int(a.get("rc")) not in (0, None)]

    if rc == 0:
        if last_rc != 0:
            return (
                UNRESOLVED,
                f"verdict 声明成功，但最后一次真实退出码是 {last_rc}，"
                f"且没有追加一次成功的执行记录（resolved={verdict.get('resolved')}）",
            )
        if prior_failures:
            note = str(verdict.get("resolution") or "").strip()
            suffix = f"：{note}" if note else "（未说明如何解决）"
            return 0, f"前 {len(prior_failures)} 次失败 {prior_failures} 后重跑成功{suffix}"
        return 0, "成功"

    diagnosis = str(verdict.get("diagnosis") or "").strip()
    return rc, diagnosis or f"退出码 {rc}"


def refresh_panel_if_needed(task: str) -> None:
    """Fire the panel-refresh hook from OUTSIDE the agent's job object.

    WHY this cannot stay in the inner .cmd (measured 2026-10-26):
      DSH wraps every command it spawns in a `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
      job. `notify_panel.cmd` works by spawning a *detached* publisher and
      returning immediately, so the publish outlives the task. Under the agent
      that detached child is inside the job and is killed the moment the session
      ends - the marker got dropped, but nothing ever published. Verified with a
      probe: the child's `CHILD-FINISHED` line never appeared.

      dsh_task.py is the PARENT of the dsh process, so it sits outside that job.
      Spawning the hook here keeps the original fire-and-forget behaviour.

    The inner .cmd still calls notify_panel.cmd; that call is neutralised by
    DSH_AGENT_INNER (see notify_panel.cmd) so a run never publishes twice.
    """
    entry = load_entry(task)
    if entry is None:
        return
    if not entry.get("刷新面板", False):
        # Includes the panel-refresh task itself: hooking it would self-excite.
        return
    script = os.path.join(PROJ, "scripts", "panel_refresh.py")
    if not os.path.isfile(script):
        return
    try:
        subprocess.Popen(
            [sys.executable, script, "--request", "--reason", f"dsh:{task}"],
            cwd=PROJ,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[dsh_task] panel refresh requested ({task})", flush=True)
    except Exception as exc:  # noqa: BLE001 - never let this change the task rc
        print(f"[dsh_task] panel refresh request failed: {exc!r}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--task", required=True, help="registry task name")
    ap.add_argument("--inner", default="", help="existing run_*.cmd; default: from registry `dsh.inner`")
    ap.add_argument("--timeout", type=int, default=0, help="minutes; 0 = registry/default")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and exit")
    args = ap.parse_args(argv)

    inner = args.inner
    timeout_min = args.timeout
    if not inner or timeout_min <= 0:
        entry = load_entry(args.task)
        dsh_cfg = (entry or {}).get("dsh") or {}
        if not inner:
            inner = str(dsh_cfg.get("inner") or "").strip()
        if timeout_min <= 0:
            # The agent needs headroom over the task's own limit: it has to read
            # logs and possibly retry once. 1.5x the ExecutionTimeLimit, min 15.
            base = int((entry or {}).get("timeout_minutes") or 30)
            timeout_min = max(15, int(base * 1.5) + 5)
    if not inner:
        print(
            f"[dsh_task] no inner command: pass --inner or set `dsh.inner` on task {args.task!r}",
            file=sys.stderr,
            flush=True,
        )
        return BAD_USAGE

    sentinel = reset_sentinel(args.task)
    prompt = build_prompt(args.task, inner, sentinel)

    if args.dry_run:
        print(prompt)
        print("\n--- sentinel dir:", sentinel)
        print("--- inner:", inner, "timeout_min:", timeout_min)
        return 0

    # dsh needs a real key; the headless profile cannot resolve the
    # `refs:` block of ~/.dsh/.credentials.yaml by itself (MISSING_CREDENTIAL).
    env = dict(os.environ)
    if not env.get("DEEPSEEK_API_KEY"):
        key = read_credential_key()
        if not key:
            print(
                "[dsh_task] no DEEPSEEK_API_KEY in the environment and none found in "
                "~/.dsh/.credentials.yaml; the headless profile will fail with "
                "MISSING_CREDENTIAL.",
                file=sys.stderr,
                flush=True,
            )
            return BAD_USAGE
        env["DEEPSEEK_API_KEY"] = key
    # Sandbox mode.
    #
    # Was danger-full-access: workspace-write could not provision on this
    # workspace at all (SetNamedSecurityInfoW failed Win32 5). Root cause found
    # 2026-10-26: DSH's grantWrite() also writes an integrity LABEL (SACL), which
    # needs WRITE_OWNER - and neither `Modify` nor owner-implicit rights include
    # it. Granting TakeOwnership/ChangePermissions on the workspace root made the
    # label write succeed (rc=5 -> rc=0) and workspace-write now works.
    #
    # workspace-write also flips the approval policy from `never` back to `ask`,
    # i.e. an unattended agent can no longer act without approval. Prefer it.
    env.setdefault("DSH_PERMISSION_MODE", "workspace-write")
    # Tell notify_panel.cmd to stay quiet: we fire the hook ourselves, outside
    # the agent's job object (otherwise the publish is killed on session exit).
    env["DSH_AGENT_INNER"] = "1"

    timeout_s = timeout_min * 60

    print(f"[dsh_task] task={args.task} inner={inner} timeout={timeout_s}s", flush=True)
    started = time.time()
    dsh_rc, raw = run_agent(prompt, timeout_s, env)
    elapsed = round(time.time() - started, 1)

    attempts = read_attempts(sentinel)
    verdict = read_verdict(sentinel)
    vissue = verdict_problem(sentinel) if verdict is None else ""
    events = summarize_events(raw)

    if dsh_rc == TIMEOUT:
        print(f"[dsh_task] TIMEOUT after {elapsed}s", flush=True)
        return TIMEOUT

    code, why = decide(attempts, verdict, vissue)

    print(f"[dsh_task] dsh_rc={dsh_rc} elapsed={elapsed}s", flush=True)
    print(f"[dsh_task] attempts={len(attempts)} rcs={[a.get('rc') for a in attempts]}", flush=True)
    if verdict is not None:
        print(f"[dsh_task] verdict={json.dumps(verdict, ensure_ascii=False)}", flush=True)
    print(f"[dsh_task] tools={events['tools']} turn={events['turn_reason'] or '-'}", flush=True)
    if events["final"]:
        tail = events["final"].strip().replace("\n", " ")[:400]
        print(f"[dsh_task] agent_said={tail}", flush=True)
    print(f"[dsh_task] VERDICT rc={code} ({why})", flush=True)

    # Keep the raw event stream for post-mortems; the panel/health only read the rc.
    try:
        with open(os.path.join(sentinel, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "task": args.task,
                    "inner": inner,
                    "started": _now(),
                    "elapsed_s": elapsed,
                    "dsh_rc": dsh_rc,
                    "attempts": attempts,
                    "verdict": verdict,
                    "final_rc": code,
                    "explanation": why,
                    "agent_final": events["final"][:2000],
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    except OSError:
        pass

    # Must run while the agent's job object is already gone (see the docstring):
    # a detached publisher spawned from inside the job would be killed.
    refresh_panel_if_needed(args.task)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
