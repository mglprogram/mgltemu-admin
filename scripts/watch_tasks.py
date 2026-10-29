# -*- coding: utf-8 -*-
r"""定时任务看门狗：**任何业务定时任务失败 → 推飞书群**（2026-10-12 用户要求）。

为什么要有它（而不是让每个脚本自己上报）
------------------------------------------
2026-10-11 的教训：发货 bat 卡死在写日志之前（python `-c` 参数丢失 → REPL 挂住），
**整个 bat 一个字都没写**。那种情况下「脚本自己上报」这条链路根本走不到 ——
所以必须有一个**独立于业务脚本的外部巡检**，只读任务计划程序的状态来判断。

判据（两类，都报）
------------------
1. **失败**：任务最近一次运行 `LastTaskResult != 0` → 报（附结果码的可读解释）。
2. **漏跑**：配置了 `expect_by` 的任务，到了该时间点当天的 `LastRunTime` 仍是昨天/更早 → 报。
   （治「任务压根没触发」这类静默问题；也是 10-11 那种"跑了但没产出"的兜底信号源。）

去重
----
以 `(任务名, LastRunTime)` 为键存进状态文件；同一个「某次运行」只报一次，
所以本脚本可以每 30 分钟跑一次而不会刷屏。

配置
----
`data/task_watch.json`：`{"tasks":[{"name":..., "expect_by":"HH:MM", "label":...}]}`
随时可加任务，不用改代码。

只读（默认）
------------
默认只调 `Get-ScheduledTaskInfo`（只读）＋ POST 飞书；**不启停/不修改任何任务**。

发现异常时顺带刷新面板（2026-10-13 用户要求）
--------------------------------------------
存在的盲区：面板数据由 `AutomationPanel-Refresh`（07:55 / 13:05 / 19:05）生成，
而任务失败可能发生在两次刷新之间 —— 11:00 失败，面板要等到 13:05 才反映，
期间「面板说成功、飞书说失败」。
⇒ 本脚本**发现新异常时触发一次** `AutomationPanel-Refresh`，把盲区压到 ≤30 分钟
   （巡检间隔）。实测 2026-10-13：BHY 逾期确认 11:00 失败，飞书告警了，面板仍显示成功。

安全约束（缺一不可）：
  * 只在 **有告警**（且是去重后的新告警）时触发 —— 无异常的空跑不刷面板，省 Cloudflare 额度。
  * **排除自我**：若异常来自面板刷新任务本身，绝不触发它（否则失败→触发→再失败的自激）。
  * **冷却**：`panel_refresh_cooldown_minutes`（默认 20），避免短时间连刷。
  * 触发是**异步**的（只 `Start-ScheduledTask`，不等结果），且失败**不影响**告警推送。
  * 可用 `--no-panel-refresh` 或配置 `"panel_refresh": false` 关掉。
"""
import io
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, date

#: 同目录的小工具：从任务日志里抽出真正的原因（告警要带上它，别只说"失败"）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import log_cause  # noqa: E402

#: 手机推送（MeoW）。缺失时降级为"只推飞书"，绝不因为手机通道坏掉而丢掉告警。
try:
    import meow_notify  # noqa: E402
except Exception as _e:                                         # noqa: BLE001
    meow_notify = None
    print("[watch] meow_notify 不可用，手机推送将跳过：%s" % _e)

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "data", "task_watch.json")
STATE = os.path.join(os.path.dirname(HERE), "_logs", "task-watch", "state.json")

#: 常见结果码 → 人话（其余原样显示）
RESULT_HINTS = {
    0: "成功",
    1: "脚本自报失败（exit 1）",
    2: "命令/批处理执行错误（exit 2）",
    42: "脚本 HARD STOP（exit 42，多为前置闸门拦截）",
    267009: "任务仍在运行（0x41301）",
    267011: "任务尚未运行（0x41303）",
    267014: "任务被终止（0x41306）——常见于超时被杀或卡死后强杀",
}

#: **不是失败**的结果码：任务正在跑 / 还没跑 / 被禁用 / 已无后续排程。
#: 命中即跳过，且**不记 seen**（理由见 main() 里的注释：记了会让"跑完后的真失败"
#: 被当成"已报过"而漏报）。
#: 2026-10-12 用户指出误报后加：任务还是 Running 时被推了一条「❌ 运行失败」。
#: 这些是 Windows 任务计划程序 `SCHED_S_*` 状态码（0x41300~0x41306）：
#:   267008 0x41300 READY        任务就绪（尚未运行过）
#:   267009 0x41301 RUNNING      任务正在运行        ← 本次误报的元凶
#:   267010 0x41302 DISABLED     任务已禁用（人为关闭，不算异常）
#:   267011 0x41303 HAS_NOT_RUN  任务尚未运行
#:   267012 0x41304 NO_MORE_RUNS 已无后续排程
#:   267013 0x41305 NOT_SCHEDULED 未排程
#: 真正的失败长这样：0（成功）/ 1 / 2 / 42 / **267014 0x41306 TERMINATED**（超时被杀等）。
NOT_A_FAILURE_CODES = {267008, 267009, 267010, 267011, 267012, 267013}

#: 「外层进程被强杀」类结果码。它们的值**不代表业务成败** ——
#: `ExecutionTimeLimit` 到点、机器重启、手动结束都会落到这里，而业务脚本
#: 完全可能已经 rc=0 跑完。判它们之前必须回读 `dsh-sentinel/<task>/run.json`
#: 的 `final_rc`（见 main() 里的 ⓪b）。2026-10-29 两次假告警就是漏了这一步。
TERMINATED_CODES = {267014, 3221225786}

#: `--no-panel-refresh` 时置真：发现异常也不触发面板刷新
NO_PANEL_REFRESH = False


def _ps(cmd: str) -> str:
    """跑一段 PowerShell（只读），返回 stdout。"""
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def task_info(name: str) -> dict:
    """取任务状态（只读）。取不到 → {'missing': True}。

    ⚠ 2026-10-12 踩坑：PowerShell 的 `ConvertTo-Json` 把 DateTime 序列化成
    **`/Date(1791687601000)/`**（.NET JSON 日期），不是 ISO 串。若直接回传，
    Python 侧 `fromisoformat()` 解析失败 ⇒ `last_dt=None` ⇒ **所有判据被静默跳过**
    （症状：巡检显示「无新异常」，但明明有失败任务）。
    ⇒ 必须让 PowerShell 侧先 `.ToString('yyyy-MM-ddTHH:mm:ss')` 成 ISO 串再回传；
      同时 `State` 用 `[string]` 转（否则回的是枚举数字 3，而不是 'Ready'）。
    """
    esc = name.replace("'", "''")
    js = (
        "$t=Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue;"
        "if(-not $t){Write-Output 'MISSING';exit};"
        "$i=Get-ScheduledTaskInfo -TaskName '%s' -ErrorAction SilentlyContinue;"
        "$o=[PSCustomObject]@{"
        "State=[string]$t.State;"
        "Last=$(if($i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-ddTHH:mm:ss')}else{''});"
        "Result=[int]$i.LastTaskResult;"
        "Next=$(if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:ss')}else{''})"
        "};$o|ConvertTo-Json -Compress" % (esc, esc)
    )
    out = _ps(js)
    if not out or out == "MISSING":
        return {"missing": True}
    try:
        d = json.loads(out)
    except Exception:                                          # noqa: BLE001
        return {"missing": True}
    return d


def load_state() -> dict:
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"seen": {}}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    io.open(STATE, "w", encoding="utf-8").write(
        json.dumps(st, ensure_ascii=False, indent=1))


def send_feishu(text: str) -> None:
    url = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    if not url:
        print("[watch] 未设置 FEISHU_WEBHOOK，跳过推送")
        return
    body = json.dumps({"msg_type": "text", "content": {"text": text}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.loads(r.read().decode("utf-8"))
        print("[watch] 推送:%s" % ("OK" if res.get("code") == 0 else res))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 推送失败:%s" % str(e)[:120])


#: 面板地址（手机通知点开就跳到自动化面板）
PANEL_URL = "https://mgltemu.us.ci/#/automation/index"


def send_meow(alerts: list, stamp: str) -> None:
    """把同一批告警推到手机（MeoW）。

    与飞书同源同批：`send_feishu()` 之后调一次，内容压成手机能读的纯文本
    （去掉 Markdown 粗体；通知里 `**` 只会原样显示）。

    三条约束：
      * **失败不外抛**：它挂在告警链末尾，手机通道坏了也必须让飞书那条已经发出去的信息算数。
      * **不重试**：MeoW 的 429（IP/频率限制）重试只会更糟；失败就写日志。
      * **带面板链接**：点通知直接看面板，省一次找入口。

    实测（2026-10-28）：必须绕过本机 10808 代理 —— 详见 meow_notify.py 顶部注释。
    """
    if meow_notify is None:
        print("[watch] 手机推送：跳过（meow_notify 未导入）")
        return
    try:
        plain = [a.replace("**", "").strip() for a in alerts]
        msg = "\n\n".join(plain)
        n = len(plain)
        ok, detail = meow_notify.send(
            "定时任务告警 %s（%d 条）" % (stamp, n), msg, url=PANEL_URL)
        print("[watch] 手机推送:%s %s" % ("OK" if ok else "FAIL", detail))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 手机推送异常:%s" % str(e)[:160])


#: 面板刷新任务名（触发目标）。改这里即可换任务。
PANEL_TASK = "AutomationPanel-Refresh"


def maybe_refresh_panel(st: dict, alerts: list, cfg: dict,
                        forced_off: bool = False) -> None:
    """发现新异常时触发一次面板刷新，消除「面板数据落后于失败」的盲区。

    只在**确实有告警**时才触发 —— 无异常的空跑（每 30 分钟一次）不该刷面板，
    否则一天白烧几十次 Cloudflare 部署。

    安全：
      * 排除自我（面板刷新任务自己失败时不触发它，否则自激）
      * 冷却（默认 20 分钟）
      * 异步触发，异常不外抛（绝不能因为刷面板失败而丢掉告警）
    """
    if forced_off or cfg.get("panel_refresh") is False:
        print("[watch] 面板刷新：已关闭（--no-panel-refresh 或配置 panel_refresh=false）")
        return

    # 自我排除：面板刷新任务自身的告警不触发它
    hit_names = [a for a in alerts if ("**%s**" % PANEL_TASK) in a]
    if hit_names and len(hit_names) == len(alerts):
        print("[watch] 告警全部来自 %s 自身，跳过触发（防自激）" % PANEL_TASK)
        return

    # 冷却
    try:
        cooldown = int(cfg.get("panel_refresh_cooldown_minutes", 20))
    except Exception:                                          # noqa: BLE001
        cooldown = 20
    last = st.get("panel_refresh_at")
    now = datetime.now()
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            age_min = (now - last_dt).total_seconds() / 60.0
            if age_min < cooldown:
                print("[watch] 面板刷新：冷却中（%.1f/%d 分钟），跳过" % (age_min, cooldown))
                return
        except Exception:                                      # noqa: BLE001
            pass

    cmd = ("Start-ScheduledTask -TaskName '%s' -ErrorAction Stop"
           % PANEL_TASK.replace("'", "''"))
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        if p.returncode == 0:
            st["panel_refresh_at"] = now.isoformat(timespec="seconds")
            print("[watch] 已触发面板刷新 %s（因发现 %d 条新异常）" % (PANEL_TASK, len(alerts)))
        else:
            print("[watch] 触发面板刷新失败（不影响告警）：%s"
                  % ((p.stderr or p.stdout or "").strip()[:150]))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 触发面板刷新异常（不影响告警）：%s" % str(e)[:150])


def main() -> int:
    try:
        cfg = json.load(io.open(CFG, encoding="utf-8"))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 读不到配置 %s：%s" % (CFG, str(e)[:100]))
        return 0
    tasks = cfg.get("tasks") or []
    st = load_state()
    seen = st.setdefault("seen", {})
    now = datetime.now()
    today = now.date()

    alerts = []
    for t in tasks:
        name = t.get("name") or ""
        label = t.get("label") or name
        if not name:
            continue
        info = task_info(name)
        if info.get("missing"):
            key = "%s|missing" % name
            if key not in seen:
                seen[key] = now.isoformat(timespec="seconds")
                alerts.append("❓ **%s**：任务不存在（可能被删/改名）" % label)
            continue

        last_raw = str(info.get("Last") or "")
        result = int(info.get("Result") or 0)
        state = str(info.get("State") or "")
        last_dt = None
        if last_raw and not last_raw.startswith("1999"):
            try:
                last_dt = datetime.fromisoformat(last_raw.replace("Z", ""))
            except Exception:                                  # noqa: BLE001
                last_dt = None

        key = "%s|%s" % (name, last_raw)

        # ---- ⓪ 「还在跑 / 还没跑 / 被禁用」都不是失败（2026-10-12 用户指出误报后加）
        #   `267009`(0x41301)=任务仍在运行、`267011`(0x41303)=尚未运行 等 —— 都**不是失败**。
        #   旧实现只判 `!= 0` ⇒ 任务跑着跑着就误报一条
        #   「❌ 运行失败 · 结果码 267009（任务仍在运行）」。
        #   ⚠ **关键**：这种情况**绝不能写进 `seen`** —— 否则任务真正跑完时
        #   `LastRunTime` 不变（它是"最近一次启动时间"，跑完也不改），key 相同 ⇒
        #   会被当成"已报过"跳过 ⇒ **真失败反而漏报**。所以这里只 `continue`，不记 seen。
        if result in NOT_A_FAILURE_CODES or state.lower().startswith(("running", "disab")):
            continue

        # 每任务可声明「哪些结果码算正常」。默认 [0]。
        # ⚠ 必须支持非 0 的正常码：`TEMU限流监控` 的 exit=1 是**告警**（有重点款被限流），
        #   脚本自己写明「exit=1 means a focus SPU is traffic-limited (an ALERT, not a
        #   failure)」；若按默认判，每天都会误报一次失败。
        ok_codes = t.get("ok_results")
        if ok_codes is None:
            ok_codes = [0]

        # ---- ⓪b 被 Windows 强杀（267014）时，先问「业务到底做完没」
        #   ★ 2026-10-29 用户报「明明执行成功了，但是通知失败」：
        #     `ExecutionTimeLimit` 到点，Windows 杀掉整棵树并记 `267014`，
        #     而业务脚本可能早就 rc=0 跑完了 —— 真实结论在
        #     `_logs/dsh-sentinel/<task>/run.json` 的 `final_rc` 里。
        #     只看 LastTaskResult 就必然把「已完成」报成「失败」。
        #   ⚠ 只对**终止类**结果码生效。别的失败码（1/2/42）不在这里洗白：
        #     那是脚本自己报的，run.json 也只会记同一个值，没必要多读一次。
        if result in TERMINATED_CODES:
            real = log_cause.agent_final_rc(name)
            if real is not None:
                result = real
                if result in ok_codes and key not in seen:
                    seen[key] = now.isoformat(timespec="seconds")
                if result in ok_codes:
                    continue

        # ---- ① 失败
        if result not in ok_codes and last_dt is not None:
            if key not in seen:
                seen[key] = now.isoformat(timespec="seconds")
                hint = RESULT_HINTS.get(result, "")
                # ★ 带上**真正的原因**（2026-10-16，用户问「为什么没有解释」）：
                #   以前告警只说「结果码：1（脚本自报失败）」= 等于没解释，
                #   真因（certificate has expired / API timed out）躺在任务日志里。
                #   log_cause 会按注册表的 log 路径去尾部抽那一句。
                cause = log_cause.cause_for(name)
                body = ["❌ **%s** 运行失败" % label,
                        "　任务：%s" % name,
                        "　时间：%s" % last_dt.strftime("%m-%d %H:%M:%S"),
                        "　结果码：%s%s" % (result, ("（%s）" % hint) if hint else "")]
                if cause:
                    body.append("　原因：%s" % cause)
                body.append("　状态：%s" % state)
                alerts.append("\n".join(body))
            continue

        # ---- ①b 跑成功了也要记 seen（避免同一 key 被反复评估；也让"漏跑"判定读到最新状态）
        if result in ok_codes and key not in seen:
            seen[key] = now.isoformat(timespec="seconds")

        # ---- ② 漏跑（配了 expect_by 才判）
        eb = str(t.get("expect_by") or "").strip()
        if eb:
            try:
                hh, mm = [int(x) for x in eb.split(":")]
                due = datetime.combine(today, datetime.min.time()).replace(hour=hh, minute=mm)
            except Exception:                                  # noqa: BLE001
                continue
            if now >= due and (last_dt is None or last_dt.date() < today):
                # 任务被**停用**时不报漏跑：那是人为关闭，不是异常。
                if state.lower().startswith("disab"):
                    continue
                k2 = "%s|miss|%s" % (name, today.isoformat())
                if k2 not in seen:
                    seen[k2] = now.isoformat(timespec="seconds")
                    alerts.append(
                        "⏰ **%s** 今天（%s）到点未运行\n　任务：%s\n　应于 %s 前跑完，"
                        "最近一次运行：%s\n　状态：%s"
                        % (label, today.isoformat(), name, eb,
                           last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else "(从未)",
                           state))

    save_state(st)

    if not alerts:
        print("[watch] 巡检 %d 个任务，无新异常" % len(tasks))
        return 0

    stamp = now.strftime("%Y-%m-%d %H:%M")
    text = ("【TEMU 定时任务告警】%s\n\n" % stamp) + "\n\n".join(alerts)
    text += "\n\n（本消息由 watch_tasks.py 巡检发出；同一 次运行只报一次）"
    print("[watch] 发现 %d 条新异常：" % len(alerts))
    for a in alerts:
        print("   - " + a.replace("\n", " / ")[:160])

    # 先把状态落盘（含可能的 panel_refresh_at），再推飞书、再触发刷新
    maybe_refresh_panel(st, alerts, cfg,
                        forced_off=("--no-panel-refresh" in sys.argv))
    save_state(st)

    send_feishu(text)
    # 手机同批再推一条（2026-10-28 用户要求：失败/漏跑告警要能到手机）
    send_meow(alerts, stamp)
    return 0


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("选项：--no-panel-refresh  发现异常时不触发面板刷新")
        raise SystemExit(0)
    raise SystemExit(main())
