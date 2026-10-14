# -*- coding: utf-8 -*-
"""run_temu_abnormal.py — 合作对接仓「异常出库单」处理参数化入口（TEMU，BEL/BHY…）。

用法（AI 只改 data/*.json，不碰本脚本结构）:
    # 0) 只读：进页 + 读异常清单（不写任何东西）
    python scripts/power/run_temu_abnormal.py --action open --store BEL
    python scripts/power/run_temu_abnormal.py --action list --store BEL

    # 1) 逐单转卖家履约（**写操作**，需 --confirm）：重发 → 转为卖家履约 → 确认
    python scripts/power/run_temu_abnormal.py --action convert --store BEL --confirm

    # 2) 只读复核：原清单里哪些已消失、还剩哪些
    python scripts/power/run_temu_abnormal.py --action verify --store BEL --orders "PO-..."

    # 3) 【整链】按 data/temu_abnormal_pipeline.json 的 steps 顺序跑（open→list→convert→verify）
    python scripts/power/run_temu_abnormal.py --action pipeline --store BEL --confirm

口径：scripts/power/data/temu_abnormal.json；顺序：scripts/power/data/temu_abnormal_pipeline.json。
写操作闸门：**不由脚本判断**（`--confirm` 参数保留但不拦，与 run_temu_buylabel 同口径）——
是否放行由 A 机侧开关决定（hub.json → acl）。
日志：_logs/temu-abnormal/<日期>.jsonl；证据：scripts/power/out/<日期>/abnormal/。
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_INC_ROOT = os.path.dirname(_HERE)                 # scripts/
_WS = os.path.dirname(_INC_ROOT)                   # 工作区根
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT")
                or os.path.join(os.path.expanduser("~"), ".agents", "skills",
                                "ai-python-methodology", "scripts"))

from lib import preflight          # noqa: E402

sys.path.insert(0, os.path.join(_HERE, "lib"))
import ziniao_store       # noqa: E402  # 窗口名 → CDP 端口现场探测（power/lib 单点实现）
import temu_abnormal_driver as tad  # noqa: E402

DEFAULT_CONFIG = os.path.join(_HERE, "data", "temu_abnormal.json")
DEFAULT_STEPS_CONFIG = os.path.join(_HERE, "data", "temu_abnormal_pipeline.json")
STORE_MAP = os.path.join(_HERE, "data", "ziniao_power_map.json")

# 会改平台状态的 action（`--confirm` 保留但**不拦**，见 main() 里的口径注释）。
# "能不能改"归 A 机侧开关（hub.json → acl），不归脚本。
WRITE_ACTIONS = ("convert", "pipeline")


def _wrap_stdout():
    """仅在直接运行时包装 stdout（import 时包装会踩 pytest 的捕获流）。"""
    global sys
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def _log_run(action: str, scope: str, result, verdict: str = "ok", error=None) -> None:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    rec = {"ts": ts, "agent": "TEMU合作仓异常单", "action": action, "scope": scope,
           "result": str(result)[:600], "verdict": verdict, "error": error}
    d = os.path.join(_WS, "_logs", "temu-abnormal")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _store_entry(store: str):
    with open(STORE_MAP, encoding="utf-8") as f:
        m = json.load(f)
    for e in m.get("mapping", []):
        if store in (e.get("export_suffix"), e.get("power_name"), e.get("ziniao_account")):
            return e
    return None


def _domain_verdict(action: str, out) -> dict:
    """本域**自报达标判据**（打成 `[VERDICT]` 一行喂给作业层的通用接口）。

    为什么要自报（2026-09-22）：作业层的缺省判据是下单/发货域的「三号齐」（TK/1Z/PK），
    给本域硬套会产出**假判据**——本域根本没有这三个号，于是每次都回「⚠️ 成功但缺号」。
    本域口径：**原异常清单里的单都从异常列表消失＝达标**；清单为空＝无事可做也算达标；
    有 failed[] 或 verify.remaining 非空＝未达标（如实标，不伪装成功）。
    """
    res = None
    if isinstance(out, dict):
        if action in ("convert", "verify"):
            res = out
        elif action == "pipeline":
            for st in (out.get("steps") or []):
                if st.get("name") == "verify":
                    res = st.get("out")
    if not isinstance(res, dict) or "targets" not in res:
        return None
    targets = res.get("targets") or []
    remaining = res.get("remaining")
    if remaining is None:                       # convert 的结果用 failed[] 判
        remaining = [f.get("po") for f in (res.get("failed") or [])]
    remaining = [x for x in remaining if x]
    complete = bool(res.get("ok")) or (not targets and not remaining)
    summary = ("异常清单 %d 单 → 已转出 %d / 仍异常 %d"
               % (len(targets), len(targets) - len(remaining), len(remaining))) if targets \
        else "异常清单为空，无需处理"
    return {"complete": bool(complete), "missing": [str(x) for x in remaining][:50],
            "summary": summary, "judged_by": "原异常清单是否清空（本域口径）"}


def main() -> int:
    _wrap_stdout()
    ap = argparse.ArgumentParser(description="TEMU 合作对接仓异常出库单处理（逐单转卖家履约）")
    ap.add_argument("--action", required=True,
                    choices=["auth", "open", "list", "convert", "verify", "pipeline"])
    ap.add_argument("--store", default=None, help="店后缀（如 BEL；从 ziniao_power_map.json 查窗口并现场探测端口）")
    ap.add_argument("--port", type=int, default=None, help="紫鸟窗口 CDP 端口（现场探测/传入）")
    ap.add_argument("--orders", default=None,
                    help="只处理这些 PO（空格/逗号分隔）；不给＝页面异常列表全量")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="口径 json（缺省 data/temu_abnormal.json）")
    ap.add_argument("--steps-config", default=DEFAULT_STEPS_CONFIG,
                    help="流水线步骤定义 json（--action pipeline 用）")
    ap.add_argument("--confirm", action="store_true",
                    help="兼容参数：保留但不校验（是否放行写操作由 A 机侧 ACL 决定，不由脚本判断）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的，不碰浏览器")
    ap.add_argument("--no-evidence", action="store_true", help="不存截图证据")
    args = ap.parse_args()

    cfg = load_config(args.config)
    action = args.action

    orders = [x.strip() for x in (args.orders or "").replace(",", " ").split() if x.strip()]

    # 写操作闸门已按用户 2026-09-14 口径**不拦**（原话：「你是自动处理机器人，所有工作都是完全固定的，
    # 即使你做不做都不会影响」）——与 `run_temu_buylabel.py` 同一口径：`--confirm` 参数保留但不校验。
    # 是否允许某调用方改平台状态，由 A 机侧**开关**决定（`hub.json → acl`），不由脚本里的"道德判断"决定。
    _ = args.confirm

    # ---- 端口（现场探测，禁止写死）----
    port = args.port
    if port is None and args.store:
        e = _store_entry(args.store)
        if e is None:
            msg = "未知店后缀 %s（看 data/ziniao_power_map.json）" % args.store
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        # 整链第零段：**窗口没开就开**（用户 2026-09-22：「这个管线应该是从打开紫鸟浏览器到授权
        # 后面一整条路径」）。必须放在端口解析之前——窗口刚开时进程起来了但 CDP 端口还没监听。
        win, sid = e["ziniao_window_name"], e.get("ziniao_window_store_id")
        wait_sec = float((cfg.get("store") or {}).get("open_wait_sec", 90))
        port = ziniao_store.probe_port_by_window(win)
        if not port:
            print("[店] 窗口未就绪 → 开窗：%s（storeId=%s）" % (win, sid))
            ziniao_store.open_store(sid)
        deadline = time.time() + wait_sec
        while True:
            port = ziniao_store.probe_port_by_window(win)
            if port or time.time() >= deadline:
                break
            time.sleep(3)
        if not port:
            msg = "未探测到窗口 %s 的 CDP 端口（等待 %ds 超时）" % (win, int(wait_sec))
            print("[HARD STOP] " + msg, file=sys.stderr)
            _log_run(action, "port", msg, verdict="fail")
            return preflight.HARD_STOP_CODE
    if not port:
        print("[HARD STOP] 缺 CDP 端口（--port 或 --store <店后缀>）", file=sys.stderr)
        return preflight.HARD_STOP_CODE

    if args.dry_run:
        print("[OK] dry-run: action=%s store=%s port=%s orders=%d"
              % (action, args.store or "-", port, len(orders)))
        return 0
    try:
        import urllib.request
        json.loads(urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=4).read())
    except Exception as ex:  # noqa: BLE001
        print("[HARD STOP] CDP 端口 %s 不可连: %s" % (port, str(ex)[:80]), file=sys.stderr)
        return preflight.HARD_STOP_CODE

    # ---- ★ 登录态兜底（2026-10-10 用户口径：「所有跟 temu 有关的脚本都兜底一次 auth」）----
    #   ⚠ **本域是合作仓**（agentseller.temu.com），授权走本域自己的 `--action auth`，
    #     **不是** `run_temu_auth.py`（那是美国站 agentseller-us.temu.com，修不了这边）。
    #     见 temu-login-auth / temu-abnormal-order 的「授权分域」口径。
    #   本域 auth 步幂等：已授权秒过；未授权才点。失败不阻断（交 business 报真实错）。
    if not args.dry_run and str(action) != "auth":
        try:
            # ⚠ 同上：`from lib import auth_guard` 会被 `scripts/lib` 遮蔽 ⇒ 直接 import。
            import auth_guard
            auth_guard.ensure_auth(args.store or "", port=port or 0, log=print,
                                   entry=os.path.join(_HERE, "run_temu_abnormal.py"),
                                   extra_args=["--action", "auth"])
        except Exception as _ag:                                    # noqa: BLE001
            print("[授权兜底] ⚠ 跳过（%s）" % str(_ag)[:100])

    ev = None
    if (cfg.get("evidence") or {}).get("enabled", True) and not args.no_evidence:
        day = datetime.now().strftime("%Y-%m-%d")
        base = (cfg.get("evidence") or {}).get("dir", "scripts/power/out")
        ev = os.path.join(_WS, base, day, "abnormal")

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    base = {"cfg": cfg, "log": log, "port": port, "orders": orders or None,
            "evidence_dir": ev, "confirm": bool(args.confirm), "store": args.store}
    try:
        if action == "pipeline":
            with open(args.steps_config, encoding="utf-8") as f:
                spec = json.load(f)
            out = tad.run_steps(spec.get("steps"), base, tad.perform, log=log)
        else:
            out = tad.perform(action, base)
    except Exception as e:  # noqa: BLE001
        msg = "action=%s 失败: %s" % (action, str(e)[:300])
        print("[FAIL] " + msg, file=sys.stderr)
        _log_run(action, "orders=%d" % len(orders), "\n".join(logs[-6:]),
                 verdict="fail", error=str(e)[:300])
        return 42

    print("[OK] " + json.dumps(out, ensure_ascii=False)[:1500])
    _log_run(action, "orders=%d" % len(orders), json.dumps(out, ensure_ascii=False)[:600])

    if action in ("convert", "verify", "pipeline") and isinstance(out, dict):
        v = _domain_verdict(action, out)
        if v:
            print("[VERDICT] " + json.dumps(v, ensure_ascii=False))
            if v["complete"]:
                print("[OK] 交付达标：%s" % v["summary"])
            else:
                print("[WARN] 未达标：%s%s"
                      % (v["summary"], ("；仍异常：" + ", ".join(v["missing"])[:300]) if v["missing"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
