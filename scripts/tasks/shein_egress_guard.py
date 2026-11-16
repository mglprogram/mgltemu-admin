# -*- coding: utf-8 -*-
"""shein_egress_guard.py — SHEIN 出口 IP 白名单**体检 + 自愈**（一条命令）。

为什么需要它（2026-11-15）
--------------------------
本机是「路由器双 WAN 按连接 50/50 出口」，且家宽是动态 IP：拨号后同一 /24 内也会换
（实测 `113.102.239.52/.163/.187`、`116.4.96.94/.251`）。
SHEIN 开放平台按**出口 IP 白名单**放行，没加白就整店导表被拒（`401 openapi00002`），
发货链会在 `split` 处结构性停链 —— 2026-11-15 就是这么让 BANGL / BE 两店各丢一天。

用法
----
  python scripts/tasks/shein_egress_guard.py                # 只体检（不写任何东西）
  python scripts/tasks/shein_egress_guard.py --fix          # 未加白就自动把**当前出口 IP**加白
  python scripts/tasks/shein_egress_guard.py --cidr 113.102.239.0/24 --fix
                                                            # 加整段（人显式决定；
                                                            # 平台限制：白名单 IP 总数 < 300，
                                                            # 一个 /24 就吃 256 个名额）
  python scripts/tasks/shein_egress_guard.py --fix --push   # 定时任务口径：自愈 + 容量告警推 TEMU 群

★ 2026-11-16 用户令：「加一个 03:50 的 --fix（撞车前先把当前 IP 加白），外加容量告警到 TEMU 飞书群」
  ⇒ ① 新增白名单**容量**体检（条数 / 覆盖 IP 数 / 余量，口径同平台「覆盖 IP 总数 < 300」）；
    ② 新增 `--push`：**只在"有事"时推群**（有主体不通 / 刚自愈过 / 余量 ≤ 阈值），
       全好又是老样子就不推，免得每天一条噪音；`--always` 强制推（自测用）。
  ⇒ 排班：`TEMU-Shein-Egress-Guard`，每天 03:50（在 04:00 发货链开跑前把 IP 加白）。

退出码：0=全部主体都能调（已加白）｜2=仍有主体未加白｜3=环境/配置问题
"""

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
SKILL = os.path.join(os.path.expanduser("~"), ".agents", "skills", "shein-buylabel")
CFG = os.path.join(SKILL, "config.json")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

#: 只看这几个主体的密钥（都在 config.shein_openapi.stores 里）
SUBJECTS = ("TELALEO", "BD", "BAN", "BE")


def probe(subject: str, tries: int = 3):
    """打一次只读接口判白名单 → `(状态, 说明)`；状态 = ok / not_whitelisted / error。"""
    try:
        import shein_client as sc  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return "error", "导入 shein_client 失败：%s" % str(e)[:120]
    cfg = (json.load(open(CFG, encoding="utf-8")).get("shein_openapi") or {})
    kid, sec, _name = sc.get_keys(cfg, subject)
    if not kid:
        return "error", "config 里没有 %s 的密钥" % subject
    last = ""
    for _ in range(tries):
        try:
            sc.signed_post(sc.API_BASE, "/open-api/gsp/warehouse-address", kid, sec, {},
                           retry=0)
            return "ok", "只读接口通过"
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if "openapi00002" not in last:
                return "error", last[:160]
    ip = ""
    if "whitelist: " in last:
        ip = last.split("whitelist: ")[-1].split('"')[0]
    return "not_whitelisted", ip or last[:160]


def add_whitelist(cidr: str = "") -> int:
    """调 shein_whitelist.py 加白：给网段就加网段，否则加**当前出口 IP**。"""
    script = os.path.join(SKILL, "scripts", "shein_whitelist.py")
    if not os.path.exists(script):
        print("❌ 找不到 %s" % script)
        return 3
    import subprocess  # noqa: PLC0415
    cmd = [sys.executable, script, "add"]
    if cidr:
        cmd += ["--ips", cidr]
    print("→ 执行：%s" % " ".join(cmd))
    return subprocess.call(cmd)


#: 余量告警阈值（覆盖 IP 数距平台上限还剩多少就喊人）。默认 40：
#: 单条动态 IP 只吃 1 个名额，40 个够漂 ~40 次；低于它说明该换「固定一条 WAN + /24」了。
MIN_REMAIN_DEFAULT = 40


def whitelist_capacity(verbose: bool = True):
    """登录开放平台读白名单容量 → `(entries, covered, remain, err)`。

    ★ 2026-11-16：用户要「容量告警」。平台硬约束是**覆盖 IP 总数 < 300**
      （网段按实际覆盖数计），所以只看条数会误判（一条 /24 就吃 256）。
      口径直接复用 `shein_whitelist.covered_ips` / `MAX_COVERED`，别自己再写一份。
    读失败（登录过不去 / 页面改版）**不算故障**：返回 err，调用方降级成「⚠ 无法读取」。
    """
    try:
        import shein_whitelist as sw  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return [], 0, None, "导入 shein_whitelist 失败：%s" % str(e)[:120]
    try:
        user, pwd, _ = sw.load_portal_cfg()
        if not user or not pwd:
            return [], 0, None, "config.json 缺 shein_openapi_portal 账号/密码"
        sw._login_and_open(user, pwd, verbose=verbose)  # noqa: SLF001
        rows, _raw = sw.list_whitelist(verbose=verbose)
    except Exception as e:  # noqa: BLE001
        return [], 0, None, "读白名单失败：%s" % str(e)[:160]
    entries = [str(ip) for _id, ip in rows]
    covered = sw.covered_ips(entries)
    remain = sw.MAX_COVERED - 1 - covered          # 平台要求 < 300 ⇒ 最多覆盖 299
    return entries, covered, remain, ""


def _current_ip() -> str:
    try:
        import shein_whitelist as sw  # noqa: PLC0415
        ip, _desc = sw.current_ip()
        s = str(ip or "").strip()
        # ★ 2026-11-16 真机：`ipc.probe_ip()` 有时把结果包成「IP：116.4.96.94」，
        #   直接塞进消息会变成「出口 IP：IP：116.4.96.94」。取冒号后的最后一段。
        if "：" in s or ":" in s:
            s = s.replace("：", ":").rsplit(":", 1)[-1].strip()
        return s
    except Exception:  # noqa: BLE001
        return ""


def push_group_text(title: str, msg: str) -> tuple:
    """推**发货专用**飞书群（`notify_push.push_fulfill`）。

    ★ 2026-11-16 用户令：「把关于发货的通知发到这个新建的机器群」⇒ 出口 IP 预检
      属于发货前置，跟着走 `FEISHU_WEBHOOK_FULFILL`（没配则退回默认业务群）。
    群机器人不解析 markdown，正文别写 `**`（`to_group_text` 也会兜底剥掉）。
    """
    try:
        sys.path.insert(0, os.path.join(_PROJ, "scripts"))
        import notify_push  # noqa: PLC0415
        return notify_push.push_fulfill(title, msg)
    except Exception as e:  # noqa: BLE001
        return False, "导入/调用 notify_push 失败：%s" % str(e)[:160]


def _fmt_status(status: dict) -> str:
    mark = {"ok": "✅", "not_whitelisted": "❌", "error": "⚠"}
    return " / ".join("%s %s" % (s, mark[st]) for s, (st, _d) in status.items())


def _build_msg(subjects, status, ip, fixed, cap, cap_err, remain, min_remain, rc, still) -> str:
    """群消息正文（纯文本：群机器人不解析 markdown，别写 **）。"""
    import time  # noqa: PLC0415

    entries, covered = cap
    lines = [
        "时间：%s" % time.strftime("%Y-%m-%d %H:%M"),
        "出口 IP：%s" % (ip or "（探测失败）"),
        "主体：%s" % _fmt_status(status),
    ]
    if still:
        lines.append("自愈：❌ 加白后仍有 %d 个主体不通（%s）" % (len(still), ", ".join(sorted(still))))
    elif fixed:
        lines.append("自愈：✅ 已把当前出口 IP 加白并复测通过")
    elif any(st == "not_whitelisted" for st, _d in status.values()):
        lines.append("自愈：未执行（没加 --fix）")
    else:
        lines.append("自愈：无需（本来就在白名单）")

    if cap_err:
        lines.append("白名单：⚠ 容量读不到（%s）" % cap_err)
    elif remain is None:
        lines.append("白名单：未体检（--no-cap）")
    else:
        lines.append("白名单：%d 条 / 覆盖 %d IP / 余量 %d（平台上限 <300，告警阈值 %d）"
                     % (len(entries), covered, remain, min_remain))

    if rc == 0 and not still:
        lines.append("结论：✅ 通，今天 04:00 六店开跑不受影响")
    elif still or any(st == "not_whitelisted" for st, _d in status.values()):
        bad = sorted(s for s, (st, _d) in status.items() if st != "ok")
        lines.append("结论：❌ %s 不通 ⇒ 该店导表会 401 (openapi00002) 且 split 停链，需人工介入"
                     % ", ".join(bad))
    else:
        bad = sorted(s for s, (st, _d) in status.items() if st != "ok")
        lines.append("结论：⚠ %s 探测失败（网络抖动/密钥/接口异常）⇒ 白名单状态未知，"
                     "04:00 前建议手跑一次复核" % ", ".join(bad))
    if remain is not None and remain <= min_remain:
        lines.append("⚠ 余量告急：只剩 %d 个 IP 名额 ⇒ 该上「固定一条 WAN + /24」或固定出口了"
                     % remain)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="SHEIN 出口 IP 白名单体检/自愈 + 容量告警")
    ap.add_argument("--fix", action="store_true", help="未加白时自动加白（幂等）")
    ap.add_argument("--cidr", default="", help="显式加网段，如 113.102.239.0/24（慎用，占名额）")
    ap.add_argument("--subjects", default="", help="只查这些主体（逗号分隔）")
    ap.add_argument("--push", action="store_true",
                    help="只在「有事」时推 TEMU 飞书群（有主体不通 / 刚自愈过 / 余量告急）")
    ap.add_argument("--always", action="store_true", help="无视上一行，一律推（自测用）")
    ap.add_argument("--no-cap", action="store_true",
                    help="跳过白名单容量体检（不登录开放平台后台，最省时间）")
    ap.add_argument("--min-remain", type=int, default=MIN_REMAIN_DEFAULT,
                    help="余量告警阈值，默认 %d 个 IP" % MIN_REMAIN_DEFAULT)
    ap.add_argument("--title", default="", help="推送标题；默认「TEMU 发货 · SHEIN 出口 IP 预检」")
    a = ap.parse_args()

    subjects = tuple(x.strip() for x in a.subjects.split(",") if x.strip()) or SUBJECTS
    ip = _current_ip()
    print("SHEIN 出口 IP 白名单体检（%d 个主体）｜出口 IP = %s" % (len(subjects), ip or "（未知）"))

    status = {}
    for s in subjects:
        st, detail = probe(s)
        status[s] = (st, detail)
        print("  %s %-8s %s" % ({"ok": "✅", "not_whitelisted": "❌",
                                 "error": "⚠"}[st], s, detail))

    bad = {s for s, (st, _d) in status.items() if st == "not_whitelisted"}
    errs = {s for s, (st, _d) in status.items() if st == "error"}
    fixed = False
    still = {}
    rc = 0
    # ★ 2026-11-16：探到 `error`（网络抖动/接口异常）**不能当通过** ——
    #   白名单状态未知，当天可能照样 401。没别的毛病时按「环境问题」返回 3 并推群，
    #   但不算 2（2 只留给「确认未加白」，那个必然停链）。
    if errs and not bad:
        rc = 3
        print("\n⚠ %d 个主体探测失败（非白名单问题）：%s"
              % (len(errs), ", ".join(sorted(errs))))
        print("   白名单状态未知 —— 稍后重跑本脚本复核，别当成「已经通过」。")

    if not bad and not errs:
        print("\n✅ 全部主体都能调（出口 IP 已在白名单）")
    elif not bad:
        pass          # 只有探测失败：上面已说明，别在这里再喊「全部能调」
    else:
        print("\n%d 个主体未加白：%s" % (len(bad), ", ".join(sorted(bad))))
        print("   未加白 = 该店导表会被 SHEIN 拒绝（openapi00002），发货链会在 split 停链。")
        if not a.fix:
            print("   👉 修法：本脚本加 --fix（自动加当前出口 IP）"
                  "，或人工去 https://open.sheincorp.com/backstage/white-list 加白。")
            rc = 2
        else:
            frc = add_whitelist(a.cidr)
            if frc != 0:
                print("❌ 加白失败（rc=%s）—— 多半是开放平台后台登录过不去（验证码/密码变更）"
                      % frc)
                rc = 2
            else:
                print("\n重测…")
                for s in subjects:
                    st, detail = probe(s)
                    status[s] = (st, detail)
                    print("  %s %-8s %s" % ({"ok": "✅", "not_whitelisted": "❌",
                                             "error": "⚠"}[st], s, detail))
                still = {s for s, (st, _d) in status.items() if st != "ok"}
                if still:
                    print("\n❌ 仍有主体不通：%s（加白可能还没生效，等 1~2 分钟再跑一次）"
                          % ", ".join(sorted(still)))
                    rc = 2
                else:
                    print("\n✅ 已自愈：所有主体都能调")
                    fixed = True
                    ip = _current_ip() or ip

    # ---- 容量体检（2026-11-16 用户令：容量告警） -------------------------
    entries, covered, remain = [], 0, None
    cap_err = ""
    if a.no_cap:
        print("\n（--no-cap：跳过白名单容量体检）")
    else:
        print("\n白名单容量体检（登录开放平台后台读列表）…")
        entries, covered, remain, cap_err = whitelist_capacity()
        if cap_err:
            print("  ⚠ %s" % cap_err)
        else:
            print("  %d 条 / 覆盖 %d IP / 余量 %d（上限 <300，阈值 %d）"
                  % (len(entries), covered, remain, a.min_remain))
            if remain <= a.min_remain:
                print("  ⚠ 余量告急：只剩 %d 个名额 ⇒ 该上「固定一条 WAN + /24」或固定出口"
                      % remain)

    # ---- 推送（只在「有事」时） -----------------------------------------
    low = remain is not None and remain <= a.min_remain
    need = bool(still) or fixed or rc != 0 or low or a.always
    if a.push:
        if not need:
            print("\n（--push：一切正常且余量充足，按口径不推群）")
        else:
            title = a.title or "TEMU 发货 · SHEIN 出口 IP 预检"
            msg = _build_msg(subjects, status, ip, fixed, (entries, covered), cap_err,
                             remain, a.min_remain, rc, still)
            print("\n--- 推送正文 ---\n%s\n----------------" % msg)
            okp, errp = push_group_text(title, msg)
            print("推送：%s%s" % ("✅ 已发发货专用群" if okp else "❌ 失败 ", "" if okp else errp))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
