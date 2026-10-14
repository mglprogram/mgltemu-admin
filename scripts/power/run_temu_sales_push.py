# -*- coding: utf-8 -*-
"""run_temu_sales_push.py — 把某日 TEMU 导出（3 店 xlsx）发到指定企微会话（默认群「TEMU订单」）。

用法:
    python scripts/power/run_temu_sales_push.py --date 2026-10-07            # 真实发送（受闸门管）
    python scripts/power/run_temu_sales_push.py --date 2026-10-07 --dry-run  # 只打印将发什么，一个字节都不发
    python scripts/power/run_temu_sales_push.py --list-targets               # 列当前可发会话（只读）
    python scripts/power/run_temu_sales_push.py --date 2026-10-07 --force    # 同日已发过也重发

工程控制:
    - 数据外置: 目标会话/文案模板/附件模板/开关 全在 data/temu_sales_push.json；AI 只改 json，不改本脚本结构。
    - 发群硬闸门(AGENTS.md T1): `发送.enabled` 总开关；`to=group` 还须 `发送.group_enabled`。
      两者任一不满足 → HARD STOP(42)，一个字节都不发。开关归用户，AI 不得自行打开。
    - 出口自检: 发出前读回 xlsx 校验（列数 +「订单创建时间」全 = 目标日）；缺件/日期不符 → 不发。
    - 幂等: 同日已成功发送则跳过（--force 覆盖）。
    - 静默: 子进程一律走 lib.quiet（不弹 CMD 黑框，项目铁律4）。
    - 留痕: _logs/temu-sales/push-<日期>.jsonl（每次跑一条）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

def _utf8_stdout() -> None:
    """UTF-8 安全 stdout。pytest 下不动它——替换 sys.stdout 会打乱 pytest 的 capture
    （2026-10-08 实测：无条件替换导致本模块的单测采集报 `I/O operation on closed file`）。"""
    try:
        if "pytest" in sys.modules or not hasattr(sys.stdout, "buffer"):
            return
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


_utf8_stdout()

_INC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT") or os.path.join(
    os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))

from lib import preflight, quiet  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CFG = os.path.join(HERE, "data", "temu_sales_push.json")


# ---------------------------------------------------------------- 基础工具
def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _log(action: str, scope: str, result: str, verdict: str = "ok", error=None) -> None:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    rec = {"ts": ts, "agent": "TEMU销量推送", "action": action, "scope": scope,
           "result": str(result)[:600], "verdict": verdict, "error": error}
    log_dir = os.path.join(WS, "_logs", "temu-sales")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def _default_date() -> str:
    """与导出同源（UTC 昨天）——不要另立口径，否则校验会跟导出差一天。"""
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _mmdd(day_s: str) -> str:
    y, m, d = day_s.split("-")  # noqa: F841
    return "%d.%d" % (int(m), int(d))


# ---------------------------------------------------------------- 配置 / 环境
def _load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _first_existing(cands: list[str]) -> str:
    for c in cands:
        p = os.path.expanduser(c)
        if os.path.isfile(p):
            return p
    return ""


def _resolve_node(cfg: dict) -> str:
    """node.exe 路径：配置优先（支持 ~），否则自动探测。不写死用户名/版本号。"""
    p = _first_existing([cfg.get("企微", {}).get("node_path") or ""])
    if p:
        return p
    base = os.path.expanduser("~/.workbuddy/binaries/node/versions")
    cands = []
    if os.path.isdir(base):
        for ver in sorted(os.listdir(base), reverse=True):
            cands.append(os.path.join(base, ver, "node.exe"))
    p = _first_existing(cands)
    if p:
        return p
    from shutil import which
    return which("node") or ""


def _resolve_wecom(cfg: dict) -> str:
    """wecom-cli 的 JS 入口：配置优先（支持 ~），否则按本机常见落点探测。"""
    return _first_existing([
        cfg.get("企微", {}).get("wecom_js") or "",
        "~/.workbuddy/binaries/node/cli-connector-packages/node_modules/@wecom/cli/bin/wecom.js",
    ])


def _cli(node: str, wecom_js: str, args: list[str]) -> tuple[int, str]:
    """调 wecom-cli（叶子进程 → CREATE_NO_WINDOW，见 lib.quiet）。返回 (rc, 合并输出)。"""
    r = quiet.run([node, wecom_js] + args, capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=180)
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))


def _pick_json(text: str):
    """从 CLI 输出里抠出 JSON 对象（输出里常混一行 stderr 警告，如 `[wecom] json repair: ...`）。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except Exception:
            pass
    if s >= 0:
        try:
            return json.JSONDecoder().raw_decode(t[s:])[0]
        except Exception:
            return None
    return None


def _is_success(obj) -> bool:
    """成功判据：`success: true`（aibot 接口的真实形状）或 errcode==0。

    2026-10-08 实测教训：本租户 aibot send 的成功响应**没有 errcode 字段**，只看 errcode
    会把成功读成失败 → 误判后重复补发。故两者都认。
    """
    if not isinstance(obj, dict):
        return False
    if obj.get("success") is True:
        return True
    return obj.get("errcode") == 0


# ---------------------------------------------------------------- 目标会话
def _sessions(node: str, wecom_js: str) -> tuple[list, str]:
    rc, out = _cli(node, wecom_js, ["message", "aibot", "sessions", "list"])
    obj = _pick_json(out)
    if obj is None or obj.get("sessions") is None:
        return [], out.strip()[:300]
    return obj["sessions"], ""


def _match_target(sessions: list, name: str, ctype: str) -> tuple[dict | None, str]:
    """按名称+类型精确匹配（群名接口可能为空 → 只能精确匹配，命中必须唯一）。"""
    hits = [s for s in sessions if (s.get("chat_name") or "") == name
            and (not ctype or s.get("chat_type") == ctype)]
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        names = "、".join((s.get("chat_name") or "(无群名)") for s in sessions) or "(空)"
        return None, "可发列表里没有名为「%s」的%s；当前可发对象：%s" % (
            name, "群聊" if ctype == "group" else "会话", names)
    return None, "名称「%s」命中 %d 个会话，无法确定目标（改名或指定唯一名称后再试）" % (name, len(hits))


# ---------------------------------------------------------------- 附件自检
def _attachments(cfg: dict, day_s: str) -> list[str]:
    a = cfg.get("附件", {})
    d = os.path.join(WS, (a.get("目录模板") or "").format(date=day_s))
    return [os.path.join(d, t.format(mmdd=_mmdd(day_s), date=day_s)) for t in (a.get("文件模板") or [])]


def _verify_xlsx(path: str, day_s: str, cfg: dict) -> tuple[bool, str]:
    """发出前读回校验：列数 +「订单创建时间」全 = 目标日。任一项不过 → 不发。"""
    v = cfg.get("附件", {}).get("校验", {}) or {}
    col = v.get("订单创建时间列") or "订单创建时间"
    want_cols = int(v.get("列数") or 0)
    try:
        import openpyxl
    except Exception as e:  # noqa: BLE001
        return False, "无法 import openpyxl（%s），校验跳过会被当成不合格" % str(e)[:60]
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(x) for x in next(rows)]
        wb.close()
    except Exception as e:  # noqa: BLE001
        return False, "读回失败: %s" % str(e)[:80]
    if want_cols and len(header) != want_cols:
        return False, "列数 %d ≠ 期望 %d" % (len(header), want_cols)
    if col not in header:
        return False, "缺列「%s」" % col
    idx = header.index(col)
    days = set()
    n = 0
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        # min_row=2：跳过表头行（否则表头文字会被当成一个"日期值"，2026-10-08 实测踩到）
        for r in wb.active.iter_rows(min_row=2, values_only=True):
            if r and r[0] is not None and len(r) > idx and r[idx] is not None:
                days.add(str(r[idx])[:10])
                n += 1
    finally:
        wb.close()
    if n == 0:
        return False, "0 行数据"
    bad = sorted(d for d in days if d != day_s)
    if bad:
        return False, "「%s」含非目标日 %s（目标 %s）" % (col, "、".join(bad[:3]), day_s)
    return True, "%d 行全部 = %s" % (n, day_s)


# ---------------------------------------------------------------- 幂等状态
def _state_path(cfg: dict) -> str:
    return os.path.join(WS, (cfg.get("幂等", {}).get("状态文件") or "_logs/temu-sales/push_state.json"))


def _load_state(cfg: dict) -> dict:
    p = _state_path(cfg)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _mark_sent(cfg: dict, day_s: str, detail: str) -> None:
    p = _state_path(cfg)
    st = _load_state(cfg)
    st[day_s] = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "detail": detail}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 发送
def _send_markdown(node: str, wecom_js: str, chat_id: str, text: str) -> tuple[bool, str]:
    rc, out = _cli(node, wecom_js, ["message", "aibot", "send", "--chat-id", chat_id,
                                    "--msg-type", "markdown", "--set", "markdown.content=" + text])
    obj = _pick_json(out)
    if rc == 0 and _is_success(obj):
        return True, "ok"
    return False, (out.strip()[:300] or "rc=%s" % rc)


def _send_file(node: str, wecom_js: str, chat_id: str, path: str) -> tuple[bool, str]:
    rc, out = _cli(node, wecom_js, ["media", "upload", "--file-path", path])
    up = _pick_json(out)
    if not isinstance(up, dict) or not up.get("media_id"):
        return False, "上传失败: %s" % (out.strip()[:250] or "rc=%s" % rc)
    rc2, out2 = _cli(node, wecom_js, ["message", "aibot", "send", "--chat-id", chat_id,
                                      "--msg-type", "file", "--set", "file.media_id=" + up["media_id"]])
    obj2 = _pick_json(out2)
    if rc2 == 0 and _is_success(obj2):
        return True, "ok"
    return False, (out2.strip()[:250] or "rc=%s" % rc2)


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="把某日 TEMU 导出（3 店 xlsx）发到指定企微会话")
    ap.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD（缺省=昨天 UTC，与导出口径同源）")
    ap.add_argument("--config", default=DEFAULT_CFG, help="配置 JSON 路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印将发什么，不发送、不上传")
    ap.add_argument("--force", action="store_true", help="同日已成功发送过也重发")
    ap.add_argument("--list-targets", action="store_true", help="只列当前可发会话（只读）")
    args = ap.parse_args()

    if not os.path.isfile(args.config):
        _out("[HARD STOP] 缺配置 %s" % args.config)
        return preflight.HARD_STOP_CODE
    cfg = _load_cfg(args.config)
    node = _resolve_node(cfg)
    wecom_js = _resolve_wecom(cfg)
    if not node or not wecom_js:
        _out("[HARD STOP] 找不到 node（%s）或 wecom-cli（%s）；已试配置值与自动探测" % (node or "空", wecom_js or "空"))
        return preflight.HARD_STOP_CODE

    send_cfg = cfg.get("发送", {}) or {}
    tgt = cfg.get("目标", {}) or {}
    day_s = args.date or _default_date()

    if args.list_targets:
        ss, why = _sessions(node, wecom_js)
        if why:
            _out("[FAIL] 取可发列表失败: %s" % why)
            return 1
        _out("可发会话 %d 个（顺序=最近往来）:" % len(ss))
        for i, s in enumerate(ss, 1):
            _out("  %d. %s / %s / 最后消息 %s" % (i, (s.get("chat_name") or "(无群名)"),
                                                  s.get("chat_type"), s.get("last_msg_time")))
        return 0

    dry = bool(args.dry_run or send_cfg.get("dry_run"))
    _out("数据日期=%s 目标=%s(%s) 开关 enabled=%s group_enabled=%s dry_run=%s"
         % (day_s, tgt.get("chat_name"), tgt.get("chat_type"),
            send_cfg.get("enabled"), send_cfg.get("group_enabled"), dry))

    # ① 附件齐不齐 + 内容对不对（不过就不发）
    files = _attachments(cfg, day_s)
    if not files:
        _out("[HARD STOP] 配置里没有附件模板")
        return preflight.HARD_STOP_CODE
    problems = []
    for p in files:
        if not os.path.isfile(p):
            problems.append("%s 不存在" % os.path.basename(p))
            continue
        if os.path.getsize(p) == 0:
            problems.append("%s 为 0 字节" % os.path.basename(p))
            continue
        ok, detail = _verify_xlsx(p, day_s, cfg)
        _out("  自检 %s: %s（%s）" % (os.path.basename(p), "OK" if ok else "不过", detail))
        if not ok:
            problems.append("%s：%s" % (os.path.basename(p), detail))
    if problems:
        msg = "交付物不合格，本次不发：" + "；".join(problems)
        _out("[FAIL] " + msg)
        _log("push_precheck", "date=%s" % day_s, msg, verdict="fail", error=msg)
        return 1

    # ② 幂等（dry-run 例外：预览不受"已发过"影响，否则没法预演）
    st = _load_state(cfg)
    if day_s in st and not args.force and not dry:
        _out("[SKIP] %s 已成功发送过（%s），本次跳过；要重发加 --force" % (day_s, st[day_s].get("at")))
        _log("push_skip", "date=%s" % day_s, "已发送过，跳过")
        return 0

    caption = (cfg.get("文案", {}).get("模板") or "").format(date=day_s)
    _out("将发文案: " + caption)
    for p in files:
        _out("将发文件: %s (%d B)" % (p, os.path.getsize(p)))

    # ③ 发群硬闸门（T1）——开关归用户，AI 不得自行打开
    if dry:
        _out("[dry-run] 闸门与目标未实测；以上为将要发送的内容（未上传、未发送）")
        return 0
    if not send_cfg.get("enabled"):
        _out("[HARD STOP] 发送总开关关着（发送.enabled=false）——本次不发。开关归用户。")
        return preflight.HARD_STOP_CODE
    if str(send_cfg.get("to") or "group") == "group" and not send_cfg.get("group_enabled"):
        _out("[HARD STOP] 群发硬闸门关着（发送.group_enabled=false）——发真实群＝用户当场拍板动作"
             "（AGENTS.md T1）。开关归用户/主会话，AI 不得自行打开。")
        return preflight.HARD_STOP_CODE

    # ④ 定位目标会话（名称精确匹配、命中唯一，否则停下）
    ss, why = _sessions(node, wecom_js)
    if why:
        _out("[FAIL] 取可发列表失败: %s" % why)
        _log("push_targets", "date=%s" % day_s, why, verdict="fail", error=why)
        return 1
    hit, why = _match_target(ss, tgt.get("chat_name") or "", tgt.get("chat_type") or "")
    if hit is None:
        _out("[FAIL] 目标未唯一命中: %s" % why)
        _log("push_targets", "date=%s" % day_s, why, verdict="fail", error=why)
        return 1

    # ⑤ 发：先文案，再逐个文件
    ok, why = _send_markdown(node, wecom_js, hit["chat_id"], caption)
    _out("[%s] 文案 → %s%s" % ("OK" if ok else "FAIL", hit.get("chat_name"), "" if ok else " " + why))
    _log("push_text", "date=%s chat=%s" % (day_s, hit.get("chat_name")), "ok" if ok else why,
         verdict="ok" if ok else "fail", error=None if ok else why)
    if not ok:
        return 1

    for p in files:
        ok2, why2 = _send_file(node, wecom_js, hit["chat_id"], p)
        _out("[%s] 文件 %s → %s%s" % ("OK" if ok2 else "FAIL", os.path.basename(p),
                                      hit.get("chat_name"), "" if ok2 else " " + why2))
        _log("push_file", "date=%s file=%s chat=%s" % (day_s, os.path.basename(p), hit.get("chat_name")),
             "ok" if ok2 else why2, verdict="ok" if ok2 else "fail", error=None if ok2 else why2)
        if not ok2:
            _out("[FAIL] 有文件没发出去（已发的无法撤回）；本次不记幂等，下次可 --force 重发")
            return 1

    _mark_sent(cfg, day_s, "文案+%d 文件 → %s" % (len(files), hit.get("chat_name")))
    _out("[OK] 已发送：文案 + %d 个文件 → %s" % (len(files), hit.get("chat_name")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
