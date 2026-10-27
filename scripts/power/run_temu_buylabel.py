# -*- coding: utf-8 -*-
"""run_temu_buylabel.py — TEMU 美国站「购买面单」参数化入口（批量在线下单）。

用法（AI 只改 data/*.json，不碰本脚本结构）:
    # 0) 只出计划，不碰浏览器（最常用，先把重量/仓库核对一遍）
    python scripts/power/run_temu_buylabel.py --action plan --file "C:\\path\\派送订单.xlsx"

    # 1) 打开批量页填好（不点提交），停在「确认下单」前
    python scripts/power/run_temu_buylabel.py --action fill --store BHY --file "C:\\path\\派送订单.xlsx"

    # 2) 【整链】**只走流水线**（用户 2026-09-24 令「只使用 PIPELINE」）
    #     顺序＝plan → screen → widen → fill → place_only → confirm → verify → record；
    #     某步失败即停（HARD STOP 42）。改顺序只改 data/temu_buylabel_pipeline.json。
    #     ⚠ 旧的 `--action all`（自开新页+重填）**已停用**（跑它会 HARD STOP 42）。
    python scripts/power/run_temu_buylabel.py --action pipeline --store BEL --file "C:\\path\\派送订单.xlsx" --confirm

    # 3) 只确认发货 / 只核验（核验两条腿：verify 读运单号；record 比"下单总数==成功数"）
    python scripts/power/run_temu_buylabel.py --action confirm --store BHY --orders "PO-... PO-..." --confirm
    python scripts/power/run_temu_buylabel.py --action verify  --store BHY --orders "PO-... PO-..."
    python scripts/power/run_temu_buylabel.py --action record  --store BHY     # 只读：在线下单记录里 下单总数==成功数（不进详情）

    # 4) 复用**已经开着**的批量页（不新开页；--reuse-page 给订单号片段或 online-shipping 页序号）
    python scripts/power/run_temu_buylabel.py --action place_only --store BHY --orders "PO-..." --reuse-page PO-211-00591977513590598 --confirm

    # 5) **只走最后一步**：页面已填好停在「确认下单」前时，不新开页、不重填，只补最后一下
    #    5a) 先只检查（不点按钮）+ 落盘「确认前状态」到 out/<日期>/buylabel/09_ready_to_place.{png,json}
    python scripts/power/run_temu_buylabel.py --action place_only --store BHY --orders "PO-..." --reuse-page <本页URL片段如 _x_sessn_id> --check-only
    #    5b) 确认要提交时才真下单（会过 judge_filled 闸门：未填好/串单直接拒）
    python scripts/power/run_temu_buylabel.py --action place_only --store BHY --orders "PO-..." --reuse-page <同一片段> --confirm

口径：scripts/power/data/temu_buylabel.json（尺寸/单件重量/仓库映射/物流商/取件策略/异常分支）。
整链：**只走 `--action pipeline`**（顺序＝data/temu_buylabel_pipeline.json；2026-09-24 用户令「只使用 PIPELINE」）；
     旧 `--action all` 已停用（HARD STOP 42）。
写操作闸门：place / place_only / confirm / pipeline 必须显式 --confirm，否则 HARD_STOP(42)——提交与改状态不由 LLM 自行决定。
日志：_logs/temu-buylabel/<日期>.jsonl；证据截图：scripts/power/out/<日期>/buylabel/。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

def _wrap_stdout():
    """仅在直接运行时包装 stdout（import 时包装会踩 pytest 的捕获流）。"""
    global sys
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_INC_ROOT = os.path.dirname(_HERE)                 # scripts/（自包含 lib 落点，迁移后无 scripts/lib 时仍可用）
_WS = os.path.dirname(_INC_ROOT)                   # 工作区根
# 自包含 lib 优先（包内 scripts/lib），再本域 lib（power/lib），最后方法论共享库
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT")
                or os.path.join(os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))

from lib import preflight  # noqa: E402
from lib import quiet      # noqa: E402  # 子进程静默起进程（不弹 CMD 黑框）

sys.path.insert(0, os.path.join(_HERE, "lib"))
import temu_buylabel_driver as tbd  # noqa: E402

DEFAULT_CONFIG = os.path.join(_HERE, "data", "temu_buylabel.json")
DEFAULT_STEPS_CONFIG = os.path.join(_HERE, "data", "temu_buylabel_pipeline.json")
STORE_MAP = os.path.join(_HERE, "data", "ziniao_power_map.json")

WRITE_ACTIONS = ("place", "place_only", "all", "confirm", "pickup", "pipeline", "reorder", "overdue")


def _log_run(action: str, scope: str, result, verdict: str = "ok", error=None) -> None:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    rec = {"ts": ts, "agent": "TEMU购买面单", "action": action, "scope": scope,
           "result": str(result)[:600], "verdict": verdict, "error": error}
    d = os.path.join(_WS, "_logs", "temu-buylabel")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def _find_store_port(window_name: str) -> int:
    """按紫鸟窗口名（进程 MainWindowTitle）现场探测 CDP 端口（禁止写死端口）。"""
    try:
        out = quiet.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):   # 只吞"探测失败"，不吞代码错（NameError 之类要炸出来）
        return 0
    try:
        ps = quiet.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } | Select-Object -ExpandProperty Id" % window_name],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0
    pids = {int(x) for x in ps.split() if x.strip().isdigit()}
    if not pids:
        return 0
    import urllib.request
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                if int(parts[4]) in pids:
                    port = int(parts[1].rsplit(":", 1)[1])
                    if 10000 < port < 70000:
                        v = json.loads(urllib.request.urlopen(
                            "http://127.0.0.1:%s/json/version" % port, timeout=2).read())
                        if v.get("webSocketDebuggerUrl"):
                            return port
            except Exception:
                continue
    return 0


def _store_entry(store: str):
    with open(STORE_MAP, encoding="utf-8") as f:
        m = json.load(f)
    for e in m.get("mapping", []):
        if store in (e.get("export_suffix"), e.get("power_name"), e.get("ziniao_account")):
            return e
    return None


def _require_all_miss(out) -> list:
    """`verify --require-all` 的判据（纯函数，可单测）：返回缺运单号的单号列表；空＝达标。

    交付闸门 `delivery.check` 就调这条命令——缺号即非 0，卡留在进行中、不伪装完成。
    """
    return list((out or {}).get("miss") or [])


def _noop_verdict(out) -> str:
    """分诊路线判「这批没得买」时**自报**的那一行 `[VERDICT]`；不是 noop 返回空串（纯函数，可单测）。

    为什么要有它：作业层（`面单/scripts/mcp/run_job.extract_verdict`）认这一行，认了就按技能自己的
    判据判达标；否则会回落到「三号齐（TK/1Z/PK）」——那对「本批单全已取消/已发货、没得买」这种
    正常收工就是假判据，会回出一张「⚠️ 成功但缺号」的卡。
    """
    if not (isinstance(out, dict) and out.get("noop")):
        return ""
    return "[VERDICT] " + json.dumps(
        {"complete": True, "missing": [],
         "judged_by": "买面单分诊路线（%s）" % (out.get("route") or "stop_report"),
         "summary": str(out.get("reason") or "本批没有可买的单")}, ensure_ascii=False)


# ---------------------------------------------------------------- 同店窗口单飞锁
# 2026-09-28 立（当天 47 次跑 / 24 次失败，其中 8 次是**同一店窗口被多路作业抢**：4 次
# `attached=true 活连接 N 个` 被守卫拒绝、4 次「批量页开不出来」；还有 15:03 BEL 那轮把**别人的
# 206 行列表**当成本批结果 ⇒ 234 单全被判成「不在待发货」）。窗口只有一个，谁碰谁独占。
# 拿不到锁＝HARD STOP 42（**不硬插、不双跑**）：双跑会互相读错列表、互相清障，代价是白跑甚至重复下单。
_LOCK_DIR = os.path.join(_WS, "_logs", "temu-buylabel", "locks")


class StoreBusy(RuntimeError):
    """同店窗口上已有作业在跑。"""


def _pid_alive(pid: int) -> bool:
    """进程还在不在。**判不了就当活着**——宁可拒绝一条作业，也不允许两条同时碰一个窗口。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = quiet.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=15).stdout or ""
            return str(pid) in out
        os.kill(pid, 0)                     # 非 Windows：0 号信号＝只探活
        return True
    except ProcessLookupError:
        return False
    except Exception:                       # noqa: BLE001  探不了（权限/命令缺失）→ 保守当作活着
        return True


def _lock_is_stale(rec: dict, now: float, stale_seconds: float) -> bool:
    """陈旧锁判据（纯函数，可单测）：记事里的进程没了，或记事超时（崩了没清锁）。"""
    if not isinstance(rec, dict):
        return True
    if not _pid_alive(int(rec.get("pid") or 0)):
        return True
    return (now - float(rec.get("started_at") or 0)) > float(stale_seconds or 0)


def _read_lock(path: str) -> dict:
    """读记事件；读不出/空的返回 {}（调用方按「可能刚建还没写」处理，不直接判死）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.loads(f.read() or "{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def _store_lock(key: str, action: str, cfg: dict):
    """同店窗口单飞锁（见上）。`key`＝店后缀（或没给店时的 CDP 端口）。"""
    lock_cfg = cfg.get("lock") or {}
    if not key or not lock_cfg.get("enabled", True):
        yield
        return
    stale_seconds = float(lock_cfg.get("stale_minutes", 60) or 60) * 60
    os.makedirs(_LOCK_DIR, exist_ok=True)
    path = os.path.join(_LOCK_DIR, re.sub(r"[^0-9A-Za-z_.-]", "_", str(key)) + ".lock")
    me = {"pid": os.getpid(), "key": str(key), "action": action,
          "started_at": time.time(), "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(me, f, ensure_ascii=False)
            break
        except FileExistsError:
            old = _read_lock(path)
            if not old:            # 可能是别人刚建好还没写进内容（O_EXCL 与 write 之间有窗口）→ 等一下再看
                time.sleep(0.5)
                old = _read_lock(path)
            if not _lock_is_stale(old, time.time(), stale_seconds):
                raise StoreBusy(
                    "同店窗口 %s 上已有作业在跑（pid=%s action=%s 起于 %s，已 %.0f 分钟）→ 等它跑完再跑；"
                    "同一时刻只允许一条（窗口只有一个，双跑会互相读错列表/互相清障）。"
                    "确认是死锁可删 %s" % (key, old.get("pid"), old.get("action"), old.get("started"),
                                          (time.time() - float(old.get("started_at") or 0)) / 60, path))
            print("[锁] 接管陈旧锁 %s（pid=%s 已不在或超 %d 分钟）"
                  % (path, old.get("pid"), int(stale_seconds // 60)))
            try:
                os.remove(path)
            except OSError:
                pass
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main() -> int:
    _wrap_stdout()
    ap = argparse.ArgumentParser(description="TEMU 购买面单（批量在线下单）")
    ap.add_argument("--action", required=True,
                    choices=["plan", "screen", "widen", "fill", "weight_fix", "place", "place_only",
                             "confirm", "verify", "record", "record_detail", "reorder", "pickup",
                             "all", "pipeline", "auth", "overdue"])
    ap.add_argument("--file", default=None, help="派送订单 xlsx（plan/fill/place/all 必需）")
    ap.add_argument("--orders", default=None, help="订单号（空格/逗号分隔；confirm/verify 必需，可覆盖 --file）")
    ap.add_argument("--task", default=None, help="在线下单任务号（record_detail 必需，如 TK-0858483830292533163）")
    ap.add_argument("--package", default=None, help="包裹号（pickup 必需，如 PK-0858483556352133163）")
    ap.add_argument("--reuse-page", default=None,
                    help="复用已存在的批量在线下单页（订单号片段 / online-shipping 页序号 1,2,3…）；给了就不新开页")
    ap.add_argument("--store", default=None, help="店后缀（如 BHY；从 ziniao_power_map.json 查窗口并现场探测端口）")
    ap.add_argument("--port", type=int, default=None, help="紫鸟窗口 CDP 端口（现场探测/传入）")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="口径 json（缺省 data/temu_buylabel.json）")
    ap.add_argument("--steps-config", default=DEFAULT_STEPS_CONFIG,
                    help="流水线步骤定义 json（--action pipeline 用；缺省 data/temu_buylabel_pipeline.json）")
    ap.add_argument("--confirm", action="store_true", help="写操作闸门：place/place_only/all/confirm 必须显式给")
    ap.add_argument("--check-only", action="store_true",
                    help="place_only 专用：只校验目标页是否「填好可下单」并落盘确认前状态，不点任何按钮")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的，不碰浏览器")
    ap.add_argument("--no-evidence", action="store_true", help="不存截图证据")
    ap.add_argument("--require-all", action="store_true",
                    help="只对 verify：全部单都必须有运单号，缺任一即 FAIL(42)——给交付闸门 delivery.check 当程序判据用")
    ap.add_argument("--with-label", action="store_true",
                    help="overdue 专用：把待办里「缺面单」那批按页面行生成派送表后，**直接走整链 pipeline 下单**"
                         "（不写这个参数就只取单+分流+生成表，不下单）")
    # ★ 2026-10-09 加（用户当天口径：重量 1.3、且只执行到「填充长宽高」为止）：
    #   ① --weight-per-unit：**本次调用**覆盖基准单件重，不改 data/temu_buylabel.json 的口径
    #      （配置里 1.1 是 2026-09-24 确认的口径，不能因为一次调用就改掉）。
    #   ② --stop-after dims：只做「顶部四维+仓库 → 勾选 → 批量填充 → 逐单修正」，**跳过物流服务一键填充**，
    #      停在「确认下单」之前交给用户接手。默认空＝现有全链行为，一字不改。
    ap.add_argument("--weight-per-unit", type=float, default=None,
                    help="本次调用的基准单件重（lb），覆盖口径 weight_per_unit_lb；多件单仍按 ×数量 修正")
    ap.add_argument("--stop-after", choices=["", "dims"], default="",
                    help="fill 专用：dims＝只填到长宽高/重量/仓库就停（不做物流服务一键填充）；缺省＝跑完整条 fill")
    # ★ 2026-10-09（用户当天口径）：默认**不加闸门**（改不动也继续下单）。这个开关留给
    #   需要严格把关的场景显式打开：仍不匹配 → HARD STOP 42。
    ap.add_argument("--stop-on-unfixed", action="store_true",
                    help="weight_fix 专用：仍有重量改不动的单就 HARD STOP（缺省不拦，只记账留痕）")
    # ★ 2026-10-10 用户口径：「所有跟 temu 有关的脚本都兜底一次 auth」。
    #   默认开：业务动作前先跑 run_temu_auth.py（幂等，已登录秒过），治「窗口重开必掉登录态」。
    ap.add_argument("--no-auth-guard", action="store_true",
                    help="关掉登录态兜底（默认会先跑一次 auth；已登录秒过、失败不阻断）")
    args = ap.parse_args()

    cfg = tbd.load_config(args.config)
    action = args.action

    # `--confirm` 闸门已按用户 2026-09-14 口径移除（"你是自动处理机器人，所有工作都是完全固定的，
    # 即使你做不做都不会影响"）——不再把下单当作需要人拍板的动作。`--confirm` 参数保留但不再拦。
    _ = args.confirm

    # ★ 2026-09-24 用户令「那就只使用 PIPELINE」：整链**只走 `--action pipeline`**（顺序在
    #   data/temu_buylabel_pipeline.json，开头自带 screen 分诊，下单只在 fill 留下的那张页上补最后一下）。
    #   原「`all`＝自开新页+重填」这条整链入口**停用**——它就是今天「窗口刚开就报找不到筛选框」「同一批单被
    #   重复投递」两起失败的形态（缺开头分诊）。要单步仍可：plan / screen / fill / place_only / confirm / verify。
    if action == "all":
        msg = ("action=all（自开新页+重填的旧整链）已按用户 2026-09-24 指令停用；整链请用 "
               "`--action pipeline --store <店> --file <表.xlsx> --confirm`（顺序见 "
               "data/temu_buylabel_pipeline.json）。要单步：plan/screen/widen/fill/place_only/confirm/verify。")
        print("[HARD STOP] " + msg, file=sys.stderr)
        _log_run("all", "blocked", msg, verdict="blocked")
        return preflight.HARD_STOP_CODE

    orders = []
    if args.orders:
        orders = [x.strip() for x in args.orders.replace(",", " ").split() if x.strip()]

    # ---- 计划（无需浏览器）----
    if action == "plan":
        if not args.file:
            print("[HARD STOP] plan 需要 --file", file=sys.stderr)
            return preflight.HARD_STOP_CODE
        rows = tbd.parse_delivery_orders(args.file)
        plan = tbd.build_plan(rows, cfg)
        print(tbd.plan_summary(plan))
        _log_run("plan", "file=%s" % os.path.basename(args.file), "plan %d 单" % len(plan))
        return 0

    # record / record_detail / pickup / widen / reorder / auth / overdue = 不需要 --orders/--file
    # （widen 是窗口级步骤；reorder 由 --task 指定要重下的任务；auth 只要 --store＝做一次正常点击登录；
    #   overdue 是「待办即将逾期未发货」整条：单子由页面待办现取，不由外部给）
    if action not in ("record", "record_detail", "pickup", "widen", "reorder", "auth", "overdue"):
        if not orders and args.file:
            orders = [p["order_no"] for p in tbd.build_plan(tbd.parse_delivery_orders(args.file), cfg)]
        if not orders:
            print("[HARD STOP] 需要 --file 或 --orders", file=sys.stderr)
            return preflight.HARD_STOP_CODE

    # ---- 端口（现场探测，禁止写死）----
    port = args.port
    if port is None and args.store:
        e = _store_entry(args.store)
        if e is None:
            msg = "未知店后缀 %s（看 data/ziniao_power_map.json）" % args.store
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        port = _find_store_port(e["ziniao_window_name"])
        if not port:
            # ★ 2026-09-24：窗口没开就**自己开窗**再重探（对齐合作仓域入口的做法；用户 2026-09-22 定
            #   「整链＝从开紫鸟窗口到后面一整条路」）。原来这里直接 HARD STOP ⇒ 无人值守时会死在第一步
            #   （定时卡/作业在早上窗口没开时必然失败）。
            print("[窗] 未探测到窗口 %s → 自动开窗后重探端口（≤90s）" % e["ziniao_window_name"])
            try:
                import ziniao_store as _zs
                _zs.open_store(e.get("ziniao_window_store_id"))
                _t0 = time.time()
                while time.time() - _t0 < 90:
                    time.sleep(3)
                    port = _find_store_port(e["ziniao_window_name"])
                    if port:
                        break
            except Exception as ex:  # noqa: BLE001
                print("[窗] 开窗失败：%s" % str(ex)[:100])
        if not port:
            msg = "未探测到窗口 %s 的 CDP 端口（自动开窗后仍无；确认紫鸟客户端已启动）" % e["ziniao_window_name"]
            print("[HARD STOP] " + msg, file=sys.stderr)
            _log_run(action, "port", msg, verdict="fail")
            return preflight.HARD_STOP_CODE

    # ---- ★ 登录态兜底（2026-10-10 用户口径：「所有跟 temu 有关的脚本都兜底一次 auth」）----
    #   为什么：**窗口重开必掉登录态**——上面「窗口没开就自己开窗」之后，TEMU 侧登录态是空的，
    #   页面停在 auth/authentication。单步动作（overdue/confirm/tracking/record/…）不会自己补授权，
    #   于是无人值守每天失败（2026-10-10 11:00 BHY 就是这么挂的）。
    #   这里在业务动作之前跑一次唯一实现 run_temu_auth.py：已登录秒过、未登录自动点授权；
    #   **失败不阻断**（交业务报它真实遇到的错，诊断更准）。
    if not args.dry_run and not args.no_auth_guard:
        try:
            # ⚠ 不能用 `from lib import auth_guard`：本域有两个 lib ——
            #   `scripts/lib`（共享闸门库，靠 sys.path 的 _INC_ROOT 排在前面）会**遮蔽**
            #   `scripts/power/lib`（域 lib）。2026-10-10 实测报
            #   `cannot import name 'auth_guard' from 'lib' (scripts\lib\__init__.py)`。
            #   本模块已把 `power/lib` 插进 sys.path（见文件头的 sys.path.insert），
            #   故直接 `import auth_guard` 命中 power/lib 那份。
            import auth_guard
            auth_guard.ensure_auth(args.store or "", port=port or 0, log=print)
        except Exception as _ag:                                    # noqa: BLE001
            print("[授权兜底] ⚠ 跳过（%s）" % str(_ag)[:100])

    if args.dry_run:
        print("[OK] dry-run: action=%s store=%s port=%s 单数=%d" % (action, args.store or "-", port, len(orders)))
        print("  " + " ".join(orders))
        _log_run(action, "dry", "dry-run 单数=%d" % len(orders))
        return 0

    if not port:
        msg = "缺 CDP 端口（--port 或 --store <店后缀>）"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE
    try:
        import urllib.request
        json.loads(urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=4).read())
    except Exception as ex:
        msg = "CDP 端口 %s 不可连: %s" % (port, str(ex)[:80])
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE

    ev = None
    if cfg.get("evidence", {}).get("enabled", True) and not args.no_evidence:
        day = datetime.now().strftime("%Y-%m-%d")
        base = cfg.get("evidence", {}).get("dir", "scripts/power/out")
        ev = os.path.join(_WS, base, day, "buylabel") if not os.path.isabs(base) else os.path.join(base, day, "buylabel")

    logs = []
    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    base = {"cfg": cfg, "log": log, "port": port, "file": args.file,
            "orders": orders, "evidence_dir": ev, "confirm": args.confirm,
            "reuse_page": args.reuse_page, "check_only": args.check_only,
            "task": args.task, "package": args.package,
            "store": args.store,     # 分店覆盖 + 授权命令要用店后缀
            "weight_per_unit": args.weight_per_unit,   # ★ 2026-10-09：本次调用的基准单件重（None＝用口径）
            "stop_after": args.stop_after,             # ★ 2026-10-09：dims＝只填到长宽高就停
            "stop_on_unfixed": args.stop_on_unfixed}   # ★ 2026-10-09：重量仍有不匹配是否 HARD STOP
    try:
        with _store_lock(args.store or ("port%s" % port), action, cfg):
            if action == "pipeline":
                # 顺序来自数据（ai-python-methodology 流水线规定 D1）：steps 在 --steps-config 指向的 json 里，
                # 本脚本只负责按序驱动 perform()；某步失败由 run_steps 包上步骤号抛出 → HARD STOP(42)。
                with open(args.steps_config, encoding="utf-8") as f:
                    spec = json.load(f)
                out = tbd.run_steps(spec.get("steps"), base, tbd.perform, log=log)
            elif action == "overdue":
                # 待办「即将逾期未发货」整条（2026-09-30 用户指的流程）：
                #   取单 → 分流（有号的确认发货 / 缺面单的生成派送表）→ 缺面单那批**喂给同一条整链 pipeline**
                #   （用户 2026-09-24 令「只使用 PIPELINE」——不在别处再写一遍下单流程）。
                out = tbd.perform("overdue", base)
                _f = (out or {}).get("delivery_file")
                if _f and args.with_label:
                    _need = ((out or {}).get("split") or {}).get("need_label") or []
                    log("[待办] 缺面单 %d 单 → 按整链 PIPELINE 下单（%s）" % (len(_need), _f))
                    with open(args.steps_config, encoding="utf-8") as f:
                        spec = json.load(f)
                    sub = dict(base)
                    sub["file"] = _f
                    sub["orders"] = _need
                    out["label_pipeline"] = tbd.run_steps(spec.get("steps"), sub, tbd.perform, log=log)
                elif _f:
                    log("[待办] 派送表已生成但**未下单**（没给 --with-label）：%s" % _f)
            else:
                out = tbd.perform(action, base)
    except StoreBusy as e:
        # 同店窗口已被别的作业占着 → 不硬插、不双跑（2026-09-28 立，见 _store_lock）
        msg = "同店窗口占用：" + str(e)
        print("[HARD STOP] " + msg, file=sys.stderr)
        _log_run(action, "lock", msg[:300], verdict="blocked")
        return preflight.HARD_STOP_CODE
    except Exception as e:
        # ⚠ 2026-10-27 修：`TimeoutError` 的 `str(e)` 是**空字符串**，于是日志只写
        #   `[FAIL] action=overdue 失败: `（后面什么都没有），排查时完全看不出真因。
        #   这里补一层：消息为空时用异常类型 + 一点上下文顶上。
        _detail = str(e).strip()
        if not _detail:
            _detail = "%s（无消息；多为页面渲染进程卡死或 CDP 调用超时）" % type(e).__name__
        msg = "action=%s 失败: %s" % (action, _detail[:300])
        print("[FAIL] " + msg, file=sys.stderr)
        _log_run(action, "orders=%d" % len(orders), "\n".join(logs[-6:]),
                 verdict="fail", error=_detail[:300])
        return 42

    # 分诊说「这批没得买」（全已取消/已发货）→ 按路线正常结束：**不是失败**，别开失败卡。
    # 自报 [VERDICT]（通用接口，面单/scripts/mcp/run_job.extract_verdict 认这一行），
    # 免得 MCP 层又按「三号齐（TK/1Z/PK）」把它判成「成功但缺号」。
    _v = _noop_verdict(out)
    if _v:
        print(_v)

    print("[OK] " + json.dumps(out, ensure_ascii=False)[:1500])
    # scope 带上**店名**（2026-09-30 加）：看板卡的交付闸门要能按店核（「BHY 和 BEL 两个店都要跑」），
    # 光数记录条数分不出是哪个店。
    _log_run(action, "store=%s orders=%d" % (args.store or "-", len(orders)),
             json.dumps(out, ensure_ascii=False)[:600])

    # 交付闸门：verify --require-all —— 缺任一运单号即判失败（给看板 delivery.check 当程序判据）
    if action == "verify" and args.require_all:
        miss = _require_all_miss(out)
        if miss:
            print("[FAIL] 交付未达标：%d 单缺运单号 %s" % (len(miss), ", ".join(miss)[:300]), file=sys.stderr)
            _log_run(action, "require-all", "缺运单号 %d 单" % len(miss), verdict="fail")
            return 42
        print("[OK] 交付达标：%d 单全部有运单号" % len((out or {}).get("ok") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
