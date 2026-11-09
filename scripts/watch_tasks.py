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

发现失败时自动重跑一次（2026-11-03 用户要求）
--------------------------------------------
> 「你有一个 30 分钟刷新面板的看门狗任务，那你去刷新面板的时候，
>   当检测到又失败任务，你去自动修复失败的任务。」

巡检本来就每 30 分钟读一遍全部任务、还能从日志里抽出真因 —— 它已经知道谁失败、
为什么失败，比任何外部工具都适合做这件事。全部规则与红线见 `scripts/auto_repair.py`，
一句话版：**只对注册表里显式写了 `自动重试: true` 的任务重跑一次**，且
永不重试巡检/面板刷新自己（自激）、同一次失败只试一次、每任务每天 ≤3 次、
内存紧张时不试（会把别人的页挤出去）、rc=5/6 跳过码不试、距下次跑 <10 分钟不试。
告警照发，正文里写清「已自动重试第 N 次」；能按 `--no-auto-repair` 或配置关掉。
"""
import io
import json
import os
import subprocess
import sys
import time
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


def _schtasks_exists(name: str) -> bool:
    """**第二条独立通道**确认任务是否存在（`schtasks`，不走 PowerShell 的 ScheduledTasks 模块）。

    为什么要两条路（2026-11-02 假告警）：`Get-ScheduledTask -TaskName X` 在被查询的那一瞬间
    可能返回 **null**（任务刚跑完、注册表正在刷新，或另一进程正在写它的 XML）——
    这时上层会把它判成「任务不存在（可能被删/改名）」并推一条飞书 + 手机告警。
    而那条告警是**假的**：15:27 下一轮巡检就正常了，任务一直在（14:45 触发、当时正在跑）。

    ⇒ `missing` 是高代价结论（会惊动人），必须**两个独立通道都说不存在**才认。
    返回 False 只在"确实查过且没找到"时；命令跑不起来时返回 True（宁可不报，不乱报）。
    """
    try:
        p = subprocess.run(["schtasks", "/Query", "/TN", name],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except Exception:                                          # noqa: BLE001
        return True                       # 命令都没跑起来 → 不能据此说"不存在"
    out = ((p.stdout or "") + (p.stderr or ""))
    if p.returncode == 0:
        return True
    # schtasks 找不到任务时会回 "ERROR: The system cannot find the file specified."
    if "cannot find" in out.lower() or "找不到" in out:
        return False
    return True                           # 其它错误（权限/本地化文案）一律不当"不存在"


def task_info(name: str) -> dict:
    """取任务状态（只读）。取不到 → {'missing': True}。

    ⚠ 2026-10-12 踩坑：PowerShell 的 `ConvertTo-Json` 把 DateTime 序列化成
    **`/Date(1791687601000)/`**（.NET JSON 日期），不是 ISO 串。若直接回传，
    Python 侧 `fromisoformat()` 解析失败 ⇒ `last_dt=None` ⇒ **所有判据被静默跳过**
    （症状：巡检显示「无新异常」，但明明有失败任务）。
    ⇒ 必须让 PowerShell 侧先 `.ToString('yyyy-MM-ddTHH:mm:ss')` 成 ISO 串再回传；
      同时 `State` 用 `[string]` 转（否则回的是枚举数字 3，而不是 'Ready'）。

    ⚠⚠ 2026-11-02 **真根因**（「任务不存在」假告警的来源，踩了一整轮）：
       `Result=[int]` 强转 —— **Int32 装不下被强杀的任务**。
       `STATUS_CONTROL_C_EXIT = 0xC000013A = 3221225786 > Int32.MaxValue`，
       强转直接抛 `Cannot convert value "3221225786" to type "System.Int32"`，
       **整个 `$o` 对象因此没有输出** ⇒ `_ps()` 拿到的是空/报错文本 ⇒ 上层判「任务不存在」。

       实测链条：我用 `taskkill /F /T` 杀过一轮 pending 任务（14:57 那轮告警之前），
       该任务的 `LastTaskResult` 变成 3221225786；下一次巡检读它就爆 Int32 → 假告警。
       `state.json` 里残留的 4 个 `*|missing` 键（PWShipping / Overdue-Confirm /
       WorkBuddy-Proxy-Sentinel / Pending）全是这个原因 —— 它们都是"被杀过"的任务。

       ⇒ 改 `[long]`（Int64）。**这类码恰恰是最该被报出来的**（超时被杀/强杀），
         结果旧实现把它变成了"任务不存在"，等于把真故障伪装成配置错误。
    """
    esc = name.replace("'", "''")
    js = (
        "$t=Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue;"
        "if(-not $t){Write-Output 'MISSING';exit};"
        "$i=Get-ScheduledTaskInfo -TaskName '%s' -ErrorAction SilentlyContinue;"
        "$o=[PSCustomObject]@{"
        "State=[string]$t.State;"
        "Last=$(if($i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-ddTHH:mm:ss')}else{''});"
        # ★ [long] 不是 [int]：0xC000013A(3221225786) 超 Int32，强转会抛异常并吞掉整个对象
        "Result=$(if($i.LastTaskResult -ne $null){[long]$i.LastTaskResult}else{0});"
        "Next=$(if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:ss')}else{''})"
        "};$o|ConvertTo-Json -Compress" % (esc, esc)
    )
    # ★ 2026-11-02：`missing` 是**高代价结论**（会推飞书 + 手机惊动人），
    #   所以先重试（PowerShell 偶发返回空），再用 schtasks 交叉确认。
    out = ""
    for attempt in range(3):
        out = _ps(js)
        if out and out != "MISSING":
            break
        time.sleep(1.5)

    if not out or out == "MISSING":
        # 第一条通道说"没有" —— 不等于真没有。用 schtasks 复核一次。
        if _schtasks_exists(name):
            print("[watch] %s：PowerShell 报 missing，但 schtasks 查到了 → 本轮跳过（不改判失败）"
                  % name)
            return {"unknown": True, "State": "", "Last": "", "Result": 0, "Next": ""}
        print("[watch] %s：两个通道都查不到 → 判 missing（ps_out=%r）"
              % (name, (out or "")[:80]))
        return {"missing": True}
    try:
        d = json.loads(out)
    except Exception:                                          # noqa: BLE001
        if _schtasks_exists(name):
            print("[watch] %s：PowerShell 输出无法解析，但 schtasks 查到了 → 本轮跳过：%r"
                  % (name, out[:80]))
            return {"unknown": True, "State": "", "Last": "", "Result": 0, "Next": ""}
        return {"missing": True}
    return d


def load_state() -> dict:
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"seen": {}}


#: scheduled-tasks 注册表：`ok_results` 的**唯一**事实来源
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                        "scheduled-tasks", "tasks.json")

_REGISTRY_OK_CACHE: dict | None = None


def registry_ok_results() -> dict:
    """从注册表读「每个任务哪些返回码算正常」。

    ★ 2026-10-30 修：`ok_results` 原来写在**两处** —— scheduled-tasks 的
      `tasks.json`（manage.py 用）和本目录的 `data/task_watch.json`（巡检用）。
      两份靠人手同步，结果 task_watch.json 只填了 5/15 个任务，其余全走默认 `[0]`。

      后果（用户 2026-10-30 08:27 收到的告警就是这一条）：
        `TEMU-Goods-OnSale-Daily` 08:00 那轮 export/build/enrich 全 rc=0、
        goods.json 已更新，只因发布阶段撞上另一发布进程返回 **rc=6**
        （按设计「并入那一轮」，注册表和面板都算正常），巡检却不知道 6 是正常码
        ⇒ 推了一条「❌ 运行失败」。

      ⇒ 注册表是单一事实来源，这里只做**读取+缓存**，不再维护第二份。
        `task_watch.json` 里的同名键若存在，作为**显式覆盖**保留（向后兼容），
        但正常情况下不该出现。
    """
    global _REGISTRY_OK_CACHE
    if _REGISTRY_OK_CACHE is not None:
        return _REGISTRY_OK_CACHE
    out: dict = {}
    try:
        reg = json.loads(io.open(REGISTRY, encoding="utf-8-sig").read())
        for e in reg.get("tasks") or []:
            name = e.get("name") or ""
            ok = e.get("ok_results")
            if name and ok:
                try:
                    out[name] = [int(x) for x in ok]
                except (TypeError, ValueError):
                    pass
    except Exception as exc:                                   # noqa: BLE001
        print("[watch] 读注册表失败，ok_results 全部退回默认 [0]：%s" % str(exc)[:120])
    _REGISTRY_OK_CACHE = out
    return out


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    io.open(STATE, "w", encoding="utf-8").write(
        json.dumps(st, ensure_ascii=False, indent=1))


def _open_for(url: str):
    """按 `net_route` 的结论取 opener；路由层不可用时回退默认 opener（不抛）。"""
    try:
        import net_route                                        # noqa: PLC0415
        return net_route.opener_for(url)[0]
    except Exception:                                           # noqa: BLE001
        return urllib.request.build_opener()


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
        # 选路交给 net_route（国内 IP 直连、其他走代理）。看门狗和业务推送
        # 走同一套判据，否则「告警发不出去」这种事只能靠人肉记忆排查。
        with _open_for(url).open(req, timeout=25) as r:
            res = json.loads(r.read().decode("utf-8"))
        print("[watch] 推送:%s" % ("OK" if res.get("code") == 0 else res))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 推送失败:%s" % str(e)[:120])


#: 面板地址（手机通知点开就跳到自动化面板）
PANEL_URL = "https://mgltemu.us.ci/#/automation/index"

#: 可用物理内存低于这个值就算"紧张"（MB）。与 `auth_guard.MEM_TIGHT_MB` **同一口径**
#: （测试 `test_memory_pressure.py` 钉住两者相等，防止一处改了另一处没改）。
#: 用途（2026-11-03 立）：内存耗尽是卡死页反复出现的真因（16 GB 机器可用只剩 133 MB），
#: 而它表现出来全是别的东西 ——
#:   · 任务报「登录态拿不到」（卡死页读一次 .url 要 60 秒）
#:   · 报 `server rejected WebSocket connection: HTTP 500`
#:   · 报「页面已死，换页后重试」然后那单失败
#: ⚠ 2026-11-09 用户要求**不再推送**这条告警（每小时两条、无法据此行动）。
#:   阈值本身仍然生效，只是从"发给人的告警"退化成"程序内部的行为开关"：
#:   紧张时 `auto_repair` 不自动重跑、`auth_guard` 不导航修复页面。
#:   要人工看：`python scripts/watch_tasks.py --memory`。
def _mem_tight_mb() -> int:
    """阈值取 auth_guard 的单点定义；那份模块拿不到时才退回 800。"""
    try:
        import sys as _sys
        _lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power", "lib")
        if _lib not in _sys.path:
            _sys.path.insert(0, _lib)
        import auth_guard as _ag
        return int(_ag.MEM_TIGHT_MB)
    except Exception:                                                  # noqa: BLE001
        return 800


MEM_ALERT_MB = _mem_tight_mb()

#: 这里只列**确认是主要占用者**的程序名（用于告警正文给出可操作建议）。
#: 不动它们，也不猜用户为什么开着 —— 只如实报数字。
MEM_HOGS = ("ziniaobrowser", "chrome", "msedge", "WorkBuddy", "ima.copilot",
            "Feishu", "WeChatAppEx", "Weixin", "wps", "DeepSeek Harness")


def _memory_alert() -> str:
    """可用内存过低时返回一条告警；否则返回空串。**永不抛异常**。"""
    try:
        import ctypes
        import ctypes.wintypes as _wt                                  # noqa: PLC0415

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", _wt.DWORD), ("dwMemoryLoad", _wt.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MS()
        st.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        mb = 1024 * 1024
        avail = int(st.ullAvailPhys // mb)
        total = int(st.ullTotalPhys // mb)
        commit_used = int((st.ullTotalPageFile - st.ullAvailPageFile) // mb)
        commit_max = int(st.ullTotalPageFile // mb)
    except Exception:                                                  # noqa: BLE001
        return ""
    if avail >= MEM_ALERT_MB:
        return ""

    # 大户（按进程名汇总提交内存）——给出"关哪个"的具体建议
    hogs = []
    try:
        import subprocess                                          # noqa: PLC0415
        names = ",".join("'%s'" % n for n in MEM_HOGS)
        ps = ("Get-Process -ErrorAction SilentlyContinue | "
              "Where-Object { %s -contains $_.ProcessName } | "
              "Group-Object ProcessName | ForEach-Object { "
              "'{0}={1}' -f $_.Name, [int]((($_.Group | "
              "Measure-Object PrivateMemorySize64 -Sum).Sum)/1MB) }"
              % ("@(" + names + ")"))
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=45)
        rows = []
        for line in (p.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    rows.append((k.strip(), int(v)))
                except ValueError:
                    continue
        rows.sort(key=lambda x: -x[1])
        hogs = ["%s %dMB" % (k, v) for k, v in rows[:5]]
    except Exception:                                                  # noqa: BLE001
        pass

    lines = [
        "⚠ **本机内存不足**（可用 %d MB / 共 %d MB，达到告警阈 %d MB）"
        % (avail, total, MEM_ALERT_MB),
        "　提交量：%d / %d MB" % (commit_used, commit_max),
        "　为什么这条重要：内存耗尽会让紫鸟窗口里的页面被换页出去，"
        "唤醒超时 ⇒ 表现为「卡死页 / 读不到登录态 / WebSocket HTTP 500」，"
        "而那些都不像内存问题。",
    ]
    if hogs:
        lines.append("　占用最多：%s" % "、".join(hogs))
    lines.append("　建议：关掉暂时不用的浏览器/程序；这不是脚本问题，反复重载页面只会更糟。")
    return "\n".join(lines)


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


def registry_entries() -> dict:
    """注册表 → {任务名: 条目}（自动重试要看条目里的 `自动重试` 开关）。"""
    out: dict = {}
    try:
        reg = json.loads(io.open(REGISTRY, encoding="utf-8-sig").read())
        for e in reg.get("tasks") or []:
            n = e.get("name")
            if n:
                out[n] = e
    except Exception:                                          # noqa: BLE001
        pass
    return out


#: 注册表 `schedule.weekly.days` 的写法 → `date.weekday()`（周一=0）。
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def scheduled_today(entry: dict, today) -> bool | None:
    """该任务**今天是否有排程**。纯函数。`None` ＝ 判不了（按"有"处理）。

    为什么必须有这条闸门（2026-11-09 用户报的假告警）
    ------------------------------------------------
    `Powerwarehouse-SKU-Migrate-Daily` 只在**工作日** 12:30/18:30 跑，
    但 `task_watch.json` 里给的 `expect_by: 13:00` 是**每天**都判。
    于是周六（11-07）和周日（11-08）连续两天推「⏰ 到点未运行，
    最近一次运行：11-06 18:30」——**任务根本不该跑**，那不是漏跑。

    用户收到这种告警的代价不是"多看一眼"，而是**他会开始忽略告警**，
    真正漏跑那天反而没人管。所以漏跑判定必须先问"今天轮不轮到它"。

    支持的写法（就是注册表里实际在用的四种）：
      · `{"daily": [...]}`               → 每天都算
      · `{"weekly": {"days": [...]}}`    → 看星期
      · `{"every_minutes": N}`           → 每天都算（跑得很勤，漏一次没意义）
      · `{"once": {"at": "YYYY-MM-DD"}}` → 只在那一天算
    其它/读不出 → `None`（不拦，沿用老行为）。
    """
    sc = (entry or {}).get("schedule") or {}
    if not isinstance(sc, dict) or not sc:
        return None
    if "daily" in sc or "every_minutes" in sc:
        return True
    if "once" in sc:
        at = str(((sc.get("once") or {}).get("at") or "")).strip()
        if not at:
            return None
        try:
            return at[:10] == today.isoformat()
        except Exception:                                      # noqa: BLE001
            return None
    wk = sc.get("weekly") or {}
    days = wk.get("days") or []
    idx = {_WEEKDAY_INDEX[str(d).strip().lower()] for d in days
           if str(d).strip().lower() in _WEEKDAY_INDEX}
    if not idx:
        return None
    return today.weekday() in idx


def maybe_repair(st: dict, name: str, rc, cfg: dict, entries: dict,
                 last_run_key: str, next_iso: str, *,
                 forced_off: bool = False, mem_tight: bool = None) -> str:
    """失败后自动重跑一次 → 返回一句给告警正文用的人话（不重试则返回空串）。

    用户 2026-11-03 的要求：看门狗既已知道谁失败、为什么失败，就该顺手救一次，
    而不是只喊不救。全部规则与红线见 `scripts/auto_repair.py` 顶部。
    """
    if forced_off or cfg.get("auto_repair") is False:
        return ""
    try:
        import auto_repair as AR                                # noqa: PLC0415
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 自动修复不可用（跳过）：%s" % str(e)[:120])
        return ""

    if mem_tight is None:
        mem_tight = AR.mem_is_tight()
    ok, why = AR.decide(
        name, rc, cfg, entries.get(name), st,
        mem_tight=mem_tight,
        minutes_to_next=AR.minutes_until_next(next_iso),
        last_run_key=last_run_key,
    )
    if not ok:
        print("[watch] %s 不自动重试：%s" % (name, why))
        return "　自动修复：未重试（%s）" % why

    done, detail = AR.trigger(name)
    if done:
        # 先落盘再返回：万一后面推飞书/刷面板炸了，"已经重试过"这条事实不能丢，
        # 否则下一轮会拿同一个 key 再试一次，把上限白烧掉。
        AR.record_attempt(st, name, last_run_key)
        save_state(st)
        print("[watch] %s 已触发自动重试（%s）" % (name, why))
        return "　自动修复：%s（%s，下一轮巡检按新结果重判）" % (why, detail)
    print("[watch] %s 自动重试触发失败：%s" % (name, detail))
    return "　自动修复：触发失败（%s）" % detail


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
        # utf-8-sig：容忍被 PowerShell 等工具写回文件时加的 BOM（2026-10-30 事故）
        cfg = json.load(io.open(CFG, encoding="utf-8-sig"))
    except Exception as e:                                     # noqa: BLE001
        print("[watch] 读不到配置 %s：%s" % (CFG, str(e)[:100]))
        # 不要 return 0：配置读不到 = 本轮什么都没巡检，静默成功会掩盖故障
        return 2
    tasks = cfg.get("tasks") or []
    st = load_state()
    seen = st.setdefault("seen", {})
    now = datetime.now()
    today = now.date()
    entries = registry_entries()

    alerts = []

    # ---- ⓪ 内存体检：**2026-11-09 用户要求关掉推送**。
    #   原话：「把这个定时任务删除，无意义的推送通知」。查下来它确实只是噪音：
    #   这台机器常驻可用内存就在 300~700 MB（16 GB 机器被紫鸟+Chrome+WPS 占满），
    #   阈值 800 MB ⇒ **几乎每轮都报**，一小时两条，而收件人既不会去关程序
    #   （那些正是干活要用的），也无法据此做任何决定。
    #   ⚠ 关的只是**推送**：内存数字仍被 `auto_repair.mem_is_tight()`/`auth_guard`
    #     用来决定"紧张时不要重载页面 / 不要自动重跑"，那部分行为完全不变。
    #   要临时看内存：`python scripts/watch_tasks.py --memory`。
    #   ⚠ 不要把这行改回 `_memory_alert()` —— 用户已经明确否掉这条推送两次了。

    # 重试前要看内存：紧张时重载页面会把别人的页挤出去（实测 1 张卡死页变 3 张）。
    # 只算一次，别在循环里反复读 —— 每次都要跑一次 ctypes，没必要。
    try:
        import auto_repair as _AR                                   # noqa: PLC0415
        _mem_tight = _AR.mem_is_tight()
    except Exception:                                          # noqa: BLE001
        _mem_tight = True                  # 取不到就当紧张（保守）

    for t in tasks:
        name = t.get("name") or ""
        label = t.get("label") or name
        if not name:
            continue
        info = task_info(name)
        if info.get("unknown"):
            # 两条通道结论不一致（PowerShell 说没、schtasks 说在）⇒ 本轮**不下结论**，
            # 更不写 seen：下一轮（30 分钟后）自然重判。宁可不报，也不推假告警。
            print("[watch] %s：本轮状态未知，跳过" % name)
            continue
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

        # 每任务可声明「哪些结果码算正常」。
        # ⚠ 必须支持非 0 的正常码：
        #   · `TEMU限流监控` 的 exit=1 是**告警**（有重点款被限流）
        #   · 5 = 紫鸟窗口被占（按设计跳过）
        #   · 6 = 发布锁被占（已并入那一轮，数据照样上线）
        # ★ 2026-10-30 起**从注册表读**（单一事实来源），不再依赖 task_watch.json
        #   里那份手工副本 —— 那份只填了 5/15 个任务，导致 rc=6 被当成失败误报。
        ok_codes = t.get("ok_results")                       # 显式覆盖（兼容旧配置）
        if ok_codes is None:
            ok_codes = registry_ok_results().get(name)
        if ok_codes is None:
            ok_codes = [0]
        ok_codes = [int(x) for x in ok_codes]

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
                # ---- ①c 自动重跑一次（2026-11-03 用户要求）----
                # 放在「记 seen」之后：即使重试触发失败，这条告警也算发过了，
                # 不会因为重试逻辑出错而把告警本身弄丢。
                fix = maybe_repair(st, name, result, cfg, entries, key,
                                   str(info.get("Next") or ""),
                                   forced_off=("--no-auto-repair" in sys.argv),
                                   mem_tight=_mem_tight)
                if fix:
                    body.append(fix)
                alerts.append("\n".join(body))
            continue

        # ---- ①b 跑成功了也要记 seen（避免同一 key 被反复评估；也让"漏跑"判定读到最新状态）
        if result in ok_codes and key not in seen:
            seen[key] = now.isoformat(timespec="seconds")

        # ---- ② 漏跑（配了 expect_by 才判）
        eb = str(t.get("expect_by") or "").strip()
        if eb:
            # ★ 2026-11-09：先问「今天轮不轮到它」。`expect_by` 只表达"几点该跑完"，
            #   不表达"哪天该跑"。周末给工作日任务报漏跑 = 假告警（用户已连收两天），
            #   而假告警真正的代价是让人开始忽略告警。
            _sch = scheduled_today(entries.get(name) or {}, today)
            if _sch is False:
                continue
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
                    body = ["⏰ **%s** 今天（%s）到点未运行" % (label, today.isoformat()),
                            "　任务：%s" % name,
                            "　应于 %s 前跑完，最近一次运行：%s"
                            % (eb, last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else "(从未)"),
                            "　状态：%s" % state]
                    # 漏跑比重跑更值得救：今天压根没执行过，补一次不可能造成重复。
                    # 传 rc=None 会走"读不到结果码"的分支，所以这里显式给一个
                    # 代表"没跑"的码 —— 用 267011（SCHED_S_HAS_NOT_RUN）。
                    fix = maybe_repair(st, name, 267011, cfg, entries, k2,
                                       str(info.get("Next") or ""),
                                       forced_off=("--no-auto-repair" in sys.argv),
                                       mem_tight=_mem_tight)
                    if fix:
                        body.append(fix)
                    alerts.append("\n".join(body))

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
    if "--memory" in sys.argv:
        # 临时看内存（2026-11-09 起不再自动推送，需要时手工跑这一条）。
        _m = _memory_alert()
        print(_m or "[watch] 内存充足（可用 ≥ %d MB），当前无告警" % MEM_ALERT_MB)
        raise SystemExit(0)
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("选项：--no-panel-refresh  发现异常时不触发面板刷新")
        print("      --no-auto-repair   发现失败时不自动重跑（本次生效，改配置见 task_watch.json）")
        print("      --memory           只打印当前内存状况（不再自动推送内存告警）")
        raise SystemExit(0)
    raise SystemExit(main())
