# -*- coding: utf-8 -*-
"""push_fulfill_report.py — 发货任务收尾：**推飞书 TEMU 群 + 上传云盘**。

用户 2026-11-07 口径：
    「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群和云盘把两个表格」

「两个表格」＝ `report` 步产出的**每店一份** Excel：
    scripts/power/out/<日期>/BHY_缺货与失败订单_<日期>.xlsx
    scripts/power/out/<日期>/BEL_缺货与失败订单_<日期>.xlsx

三件事，缺一不可（顺序也是这个）：
    ① 找表 —— 从 `scripts/power/out/<日期>/` 找当天的两份 xlsx
                （`report` 若因文件被占用改存 `_v2`，也要认得）
    ② 推群 —— **发货专用机器人群**（`notify_push.push_fulfill()` → `FEISHU_WEBHOOK_FULFILL`，
                2026-11-16 用户令「把关于发货的通知发到这个新建的机器群」；
                没配这个环境变量时退回 `FEISHU_WEBHOOK` 业务大群），标题必须含 `TEMU`（关键词校验）
    ③ 传盘 —— `kdocs-cli` 上传到云盘「失败订单」文件夹，命名沿用既有约定
                `YYYY-MM-DD-失败订单-<店>.xlsx`

四条硬约束（都踩过）：
    · **永不抛异常**：业务（发货）已经做完了，推送/上传失败只能少一个通道，
      绝不能把已完成的任务搞成失败。
    · **选路统一交给 `net_route`**（2026-11-07 用户口径：国内 IP 直连、其他走代理）。
      以前这里硬编码 `127.0.0.1:7897`，理由是「进程环境的 10808 是废弃端口」——
      那是个**症状**，根因是判据错了：该看对面 IP 在不在国内，不是猜「这家的服务在哪」。
      实测 feishu/kdocs 都解析到国内段（0.17~0.55s 直连通），走代理反而多一跳；
      GitHub/Cloudflare 才真需要代理。现在 kdocs 与飞书都问 `net_route` 要 env/opener。
    · **群消息不解析 markdown**：`push_group()` 会自动去掉 `**`，正文别写。
    · **上传失败要说清是哪一步**：返回结构化结果，由调用方汇总进日志。

用法：
    python scripts/tasks/push_fulfill_report.py --date 2026-11-07
    python scripts/tasks/push_fulfill_report.py --date 2026-11-07 --dry-run
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_ROOT = os.path.join(PROJ, "scripts", "power", "out")
TASKS = os.path.join(PROJ, "scripts", "tasks")
if TASKS not in sys.path:
    sys.path.insert(0, TASKS)
if os.path.join(PROJ, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(PROJ, "scripts"))

#: 云盘「失败订单」文件夹（2026-11-07 实测；历史文件都在这里，命名 `YYYY-MM-DD-失败订单.xlsx`）
KDOCS_FAILED_FOLDER = "8yQgY1R4Q9MccNzrEbHWxxMoB2QDffeUr"
KDOCS_CLI = os.path.join(os.environ.get("LOCALAPPDATA", ""), "kdocs-cli", "kdocs-cli.exe")
#: kdocs 薄封装（处理无 BOM 写参与重试）
KD_WRAPPER = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                          "kdocs-cli", "scripts", "kd.py")
#: kdocs 接口域名 —— 只用来问 net_route 该直连还是走代理
KDOCS_HOST = "https://api.kdocs.cn/"

#: 飞书群里也有 webhook 域名，同样交给 net_route 判（见 `push_feishu_fixed`）
FEISHU_HOST = "https://open.feishu.cn/"

#: ★ 2026-11-11 补 BD：SHEIN 店 BD（帕沃尔 5965848967）每天也出表，
#:   原来不在这个清单里 ⇒ 表生成了但**从来不推群、不传云盘**（BD 第一次真跑整链才发现）。
#: ★ 2026-11-15 补 BE（用户令「BE 参与一整个发货流程」）：BE 现在跑整链（帕沃尔仓发货，
#:   跳过恒信领星），出的表同样要推群 + 传云盘，否则等于没参与。
STORES = ("BHY", "BEL", "BEGL", "BANGL", "BSGL", "BD", "BE",
          #: ★ 2026-11-17 用户令：「在发货总链加上一个 TM-KinglamanSport 店铺的发货」
          #:   出的表同样要推群 + 传云盘（不然等于白跑）。
          "KinglamanSport")

#: ★ 2026-11-15 用户令：「最后一步总结表，六个店铺不分成六个大表格，直接合在一个表格，
#:   每个店铺对应几个 sheet，一个个排好 sheet」⇒ 汇总表收这几家，顺序就是这个顺序。
#: ★ 2026-11-17 补 KinglamanSport（排在最后，不影响原有六家的排布口径）。
SUMMARY_STORES = ("BHY", "BEL", "BD", "BANGL", "BSGL", "BE", "KinglamanSport")

#: 用户叫法 → 系统店键。
#: ★ BSGL＝SHEIN 那边的 BS 店（帕沃尔 8540141066），产物文件名仍是 `BS_缺货与失败订单_*.xlsx`
#:   （`--store BS` 出的表），所以找文件要用系统键，展示用用户叫法。
STORE_ALIAS = {"BSGL": "BS", "BANGL": "BAN"}


def _net_route():
    """导入 net_route（`scripts/` 已在 sys.path 里）。失败返回 None，调用方回退直连。"""
    try:
        import net_route                            # noqa: PLC0415
        return net_route
    except Exception:                               # noqa: BLE001
        return None


def find_reports(day: str) -> dict:
    """找当天的两份 Excel → {店: 路径}（纯函数式，只读）。

    `report` 步在目标文件被 Excel 占用时会改存 `_v2/_v3`，所以按**前缀+日期**
    匹配，再取修改时间最新的那个，而不是死认一个文件名。
    """
    day_dir = os.path.join(OUT_ROOT, day)
    out = {}
    for store in STORES:
        key = STORE_ALIAS.get(store, store)
        pat = os.path.join(day_dir, "%s_缺货与失败订单_%s*.xlsx" % (key, day.replace("-", "")))
        cands = []
        for f in glob.glob(pat):
            try:
                cands.append((os.path.getmtime(f), f))
            except OSError:
                pass
        # ★ 2026-11-10：五店并行后产物在 `out/<日期>/fulfill/<店>/`，老路径（out/<日期>/）也认
        if not cands:
            pat2 = os.path.join(day_dir, "fulfill", key,
                                "%s_缺货与失败订单_%s*.xlsx" % (key, day.replace("-", "")))
            for f in glob.glob(pat2):
                try:
                    cands.append((os.path.getmtime(f), f))
                except OSError:
                    pass
        # ★ 再兜一层：`fulfill/<用户叫法>/`（调度器用 BANGL/BSGL 建目录）+ 只给日期不给店名段的
        if not cands:
            for sub in (store,):
                for f in glob.glob(os.path.join(day_dir, "fulfill", sub,
                                                "%s_缺货与失败订单_%s*.xlsx"
                                                % (key, day.replace("-", "")))):
                    try:
                        cands.append((os.path.getmtime(f), f))
                    except OSError:
                        pass
        if not cands:
            for f in glob.glob(os.path.join(day_dir, "fulfill", "*", "**",
                                            "%s_缺货与失败订单_%s*.xlsx"
                                            % (key, day.replace("-", ""))), recursive=True):
                try:
                    cands.append((os.path.getmtime(f), f))
                except OSError:
                    pass
        if cands:
            out[store] = max(cands)[1]
    return out


def _sheet_display_name(store: str, src_name: str) -> str:
    """把单店表里的 sheet 名改成汇总表里的名字（纯函数）。

    单店产物：`<系统键> 恒信仓 / <系统键> 帕沃尔仓 / <系统键> 缺货 / <系统键> 预售 / 失败订单`
    汇总产物：`<用户叫法> …`（BSGL / BANGL），`失败订单` 也要加店名前缀（否则六店撞名）。
    """
    key = STORE_ALIAS.get(store, store)
    name = str(src_name or "")
    if name.startswith(key + " "):
        name = store + name[len(key):]
    elif " " not in name:                  # 全局 sheet（失败订单）
        name = "%s %s" % (store, name)
    return name[:31]                        # Excel 硬限制 31 字


def merge_reports(day: str, files: dict) -> str:
    """把六店的单店表**合成一个汇总 xlsx**（用户 2026-11-15 令）。

    · sheet 顺序＝`SUMMARY_STORES` 顺序，每家店的 sheet 按它自己原来的顺序排下去；
    · 样式（表头底色/字号/列宽/冻结/筛选）一并搬过去，肉眼看和单店表一致；
    · 某店当天没有表（未出表/没单）就跳过它，不生成空 sheet。
    返回汇总文件路径；一份都没有时返回 ""。
    """
    from copy import copy  # noqa: PLC0415

    import openpyxl  # noqa: PLC0415

    have = [(s, files[s]) for s in SUMMARY_STORES if files.get(s)]
    if not have:
        return ""
    out = os.path.join(OUT_ROOT, day, "%s_发货汇总.xlsx" % day)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for store, path in have:
        src = openpyxl.load_workbook(path)
        for ws in src.worksheets:
            dst = wb.create_sheet(_sheet_display_name(store, ws.title))
            for row in ws.iter_rows():
                for cell in row:
                    nc = dst.cell(row=cell.row, column=cell.column, value=cell.value)
                    if cell.has_style:
                        # ⚠ 不能抄 `cell._style`（那是**源工作簿**里的样式表下标，搬到新簿里
                        #   保存时会 IndexError: wb._alignments[...]）。逐个属性 copy 才会
                        #   在新簿里重新登记（2026-11-15 真机踩过）。
                        nc.font = copy(cell.font)
                        nc.fill = copy(cell.fill)
                        nc.border = copy(cell.border)
                        nc.alignment = copy(cell.alignment)
                        nc.number_format = cell.number_format
            for col, dim in ws.column_dimensions.items():
                if dim.width:
                    dst.column_dimensions[col].width = dim.width
            if ws.freeze_panes:
                dst.freeze_panes = ws.freeze_panes
            if ws.auto_filter is not None and ws.auto_filter.ref:
                dst.auto_filter.ref = ws.auto_filter.ref
            if ws.row_dimensions[1].height:
                dst.row_dimensions[1].height = ws.row_dimensions[1].height
        src.close()

    os.makedirs(os.path.dirname(out), exist_ok=True)
    real = out
    for i in range(1, 6):                  # 目标被 Excel 打开就换名另存（同 report 步口径）
        try:
            wb.save(real)
            return real
        except PermissionError:
            stem, ext = os.path.splitext(out)
            real = "%s_v%d%s" % (stem, i + 1, ext)
    return ""


def _sheet_count(path: str) -> int:
    """汇总表里有几个 sheet（只读；读不出返回 -1，不影响推送）。"""
    try:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(path, read_only=True)
        n = len(wb.sheetnames)
        wb.close()
        return n
    except Exception:  # noqa: BLE001
        return -1


def summarize(path: str) -> dict:
    """读表拿两个 sheet 的行数（给人看的摘要）。读不出就返回空，不抛。"""
    try:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(path, read_only=True)
        got = {}
        for name in ("缺货", "失败订单"):
            if name in wb.sheetnames:
                ws = wb[name]
                got[name] = max(0, (ws.max_row or 1) - 1)
        wb.close()
        return got
    except Exception:  # noqa: BLE001
        return {}


def _fail_reason(store: str, day: str) -> str:
    """该店当天**为什么**没出表（读 run.json 的失败步骤，摘成一句话）。

    ★ 2026-11-15 用户追问「今天的发货任务为什么没有导出表格 - BANGL：未产出表格
      （该店本批可能无单或失败）」—— 旧文案把「接口报错」猜成「可能无单」，纯误导。
      现在的真原因是 SHEIN 开放平台 `401 openapi00002 IP is not in the whitelist: 116.4.96.94`。
      ⇒ 有 run.json 就照实写，没有才退回旧文案。
    """
    import re  # noqa: PLC0415
    key = STORE_ALIAS.get(store, store)
    for cand in (os.path.join(OUT_ROOT, day, "fulfill", key, "run.json"),
                 os.path.join(OUT_ROOT, day, "fulfill", store, "run.json")):
        try:
            with open(cand, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        bad = [s for s in (doc.get("steps") or []) if str(s.get("status")) == "fail"]
        if not bad:
            continue
        # ★ 取**第一个**失败的步骤：后面那步（split）往往只是「没有输入」的连带结果
        #   （真机 2026-11-15：export_orders 报 IP 未加白，split 报「没给 --file」，
        #    取最后一步就会把真原因盖掉）。
        step = bad[0]
        why = " ".join(str(step.get("why") or "").split())
        # `why` 里常被截断（run.json 只留 400 字），完整报错在 logs 里 ⇒ 两处都扫。
        hay = " ".join([why] + [" ".join(str(x).split()) for x in (doc.get("logs") or [])])
        m = re.search(r"IP is not in the whitelist: ([\d.]+)", hay)
        if m:
            return "出口 IP %s 未进 SHEIN 开放平台白名单，导表被拒" % m.group(1)
        if "openapi00002" in hay:
            return "出口 IP 不在 SHEIN 开放平台白名单，导表被拒"
        name = str(step.get("name") or "?")
        return "%s 失败：%s" % (name, why[:70] or "无报错文本")
    return ""


def build_message(day: str, files: dict, uploaded: dict,
                  merged_link: str = "", merged_name: str = "") -> tuple[str, str]:
    """拼标题与正文（纯函数）。标题必须含 TEMU（群机器人关键词校验）。

    ★ 2026-11-15 用户令：「六个店铺不分成六个大表格，直接合在一个表格」⇒ 传了
      `merged_link` 就只报**一个**汇总表链接；没合成（或上传失败）才回到逐店列链接。
    """
    title = "TEMU 每日发货完成 %s" % day
    lines = ["%s 发货任务已完成。" % day, ""]
    for store in SUMMARY_STORES:
        p = files.get(store)
        if not p:
            reason = _fail_reason(store, day)
            lines.append("%s：未产出表格（%s）"
                         % (store, reason or "该店本批可能无单或失败"))
            continue
        s = summarize(p)
        short = []
        if "缺货" in s:
            short.append("缺货 %d" % s["缺货"])
        if "失败订单" in s:
            short.append("失败 %d" % s["失败订单"])
        lines.append("%s：%s" % (store, " / ".join(short) or "见附件"))
        if not merged_link and store in uploaded:
            lines.append("   云盘：%s" % uploaded[store])
    if merged_link:
        lines += ["", "六店汇总表（一店一组 sheet，按顺序排好）：%s" % (merged_name or "已上传"),
                  "云盘：%s" % merged_link]
    lines += ["", "共 %d 个店；%s" % (len([s for s in SUMMARY_STORES if files.get(s)]),
                                     "汇总表已上传云盘「失败订单」文件夹" if merged_link else
                                     "表格已上传云盘「失败订单」文件夹"),
              "本地在 scripts/power/out/%s/（并行跑的店在 fulfill/<店>/ 下）" % day]
    return title, "\n".join(lines)


def kdocs_env() -> dict:
    """kdocs 子进程环境：问 `net_route` 该直连还是走代理。

    `api.kdocs.cn` 实测是 115.190.143.x（华为云国内）→ 直连，比走代理少一跳。
    判不出来（表缺失/解析失败）时回退**清空代理**再试一次：
    kdocs 是国内服务，直连是安全侧；而进程环境里那个 10808 是死端口，绝不能继承。
    """
    nr = _net_route()
    if nr is None:
        env = dict(os.environ)
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = ""
        env["NO_PROXY"] = env["no_proxy"] = "*"
    else:
        env = nr.env_for_host(KDOCS_HOST, dict(os.environ))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kd(args: list, timeout: int = 180) -> tuple[int, str]:
    """调 kdocs-cli（优先走 kd.py 封装：它处理无 BOM 写参）。"""
    if os.path.isfile(KD_WRAPPER):
        cmd = [sys.executable, KD_WRAPPER] + args + ["--compact"]
    else:
        cmd = [KDOCS_CLI] + args + ["--compact"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=kdocs_env())
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "kdocs-cli 超时（%ds）" % timeout


def _find_existing(name: str) -> str:
    """云盘「失败订单」文件夹里找同名文件 → 返回 file_id（没有就空串）。

    ★ 2026-11-07：上传用 `upload-new-file` 时云盘会自动加 `(1)(2)(3)`
      —— 我调试时跑了 4 次就堆出 8 个文件。定时任务每天跑一次不该这样，
      尤其「重跑」时（任务失败再跑）更容易堆。
      ⇒ 先查同名：有就 `upload-replace-file` 覆盖，没有才新建。
    """
    rc, out = _kd(["drive", "list-files", "parent_id=%s" % KDOCS_FAILED_FOLDER])
    if rc != 0:
        return ""
    try:
        d = json.loads(out)
        node = d.get("data") or {}
        node = node.get("data") if isinstance(node.get("data"), dict) else node
        for x in node.get("items") or []:
            if str(x.get("name") or "") == name and x.get("type") == "file":
                return str(x.get("id") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def upload(path: str, day: str, store: str, name: str = "") -> tuple[bool, str]:
    """上传/覆盖一份表 → 返回 (成功, 链接或说明)。

    命名沿用云盘既有约定 `YYYY-MM-DD-失败订单-<店>.xlsx`（汇总表另给 `name`）；
    **同名已存在就覆盖**，不产生 `(1)(2)` 副本。
    `content_base64` 走**参数文件**（无 BOM），不内联进命令行。
    """
    name = name or ("%s-失败订单-%s.xlsx" % (day, store))
    try:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except OSError as ex:
        return False, "读文件失败：%s" % ex

    existing = _find_existing(name)
    payload = {"name": name, "parent_id": KDOCS_FAILED_FOLDER, "content_base64": b64}
    action = "upload-new-file"
    if existing:
        payload["file_id"] = existing
        action = "upload-replace-file"

    tmp = os.path.join(os.path.dirname(path), "_kdocs_upload_%s.json" % store)
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:   # 无 BOM
            fh.write(json.dumps(payload, ensure_ascii=False))
        rc, out = _kd(["drive", action, "--file", tmp], timeout=300)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if rc != 0:
        return False, "上传失败（rc=%s）：%s" % (rc, out[:200])

    # 从返回里抠 file_id，再换在线链接（拿不到就算了，别把成功判成失败）
    fid = existing
    try:
        d = json.loads(out)
        node = d.get("data") or {}
        node = node.get("data") if isinstance(node.get("data"), dict) else node
        fid = str(node.get("id") or node.get("file_id") or fid or "")
    except Exception:  # noqa: BLE001
        pass
    link = ""
    if fid:
        rc2, out2 = _kd(["drive", "get-file-link", "file_id=%s" % fid])
        if rc2 == 0:
            try:
                d2 = json.loads(out2)
                n2 = d2.get("data") or {}
                n2 = n2.get("data") if isinstance(n2.get("data"), dict) else n2
                link = str(n2.get("link_url") or n2.get("url") or "")
            except Exception:  # noqa: BLE001
                pass
    tag = "已覆盖" if existing else "已上传"
    return True, (link or ("%s：%s" % (tag, name)))


def push_feishu_fixed(title: str, msg: str, tries: int = 3) -> tuple[bool, str]:
    """推 TEMU 群：选路交给 `net_route`，带重试。

    ★ 2026-11-07 真机：偶尔报 `[SSL: UNEXPECTED_EOF_WHILE_READING]
      EOF occurred in violation of protocol` —— 这是链路的瞬时抖动，
      同一条消息重试就成。飞书是「业务已完成后唯一的对外通道」，
      不能一次抖动就放弃。
    """
    import time as _t  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import notify_push  # noqa: PLC0415

    hook = notify_push.fulfill_webhook() or notify_push.feishu_webhook()
    if not hook:
        return False, "未设置 FEISHU_WEBHOOK_FULFILL / FEISHU_WEBHOOK，跳过"
    text = "%s\n%s" % (title, notify_push.to_group_text(msg))
    body = json.dumps({"msg_type": "text", "content": {"text": text}},
                      ensure_ascii=False).encode("utf-8")
    nr = _net_route()
    last = ""
    for i in range(1, max(1, tries) + 1):
        req = urllib.request.Request(
            hook, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            opener = nr.opener_for(hook)[0] if nr is not None else None
            if opener is None:
                with urllib.request.urlopen(req, timeout=notify_push.FEISHU_TIMEOUT) as r:
                    res = json.loads(r.read().decode("utf-8", "replace"))
            else:
                with opener.open(req, timeout=notify_push.FEISHU_TIMEOUT) as r:
                    res = json.loads(r.read().decode("utf-8", "replace"))
            code = res.get("code")
            if code == 0:
                return True, ("OK" if i == 1 else "OK（第 %d 次重试成功）" % i)
            if code == 19024:
                return False, "code=19024 关键词校验未通过（标题须含 TEMU）"
            return False, "code=%s %s" % (code, str(res.get("msg"))[:120])
        except Exception as ex:  # noqa: BLE001
            last = "%s: %s" % (type(ex).__name__, str(ex)[:140])
            if i < tries:
                _t.sleep(3)
    return False, last


def main() -> int:
    ap = argparse.ArgumentParser(description="发货收尾：推 TEMU 群 + 上传云盘")
    ap.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送不上传")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    import time as _t
    day = a.date or _t.strftime("%Y-%m-%d")

    files = find_reports(day)
    if not files:
        print("[push] %s 没有找到任何缺货/失败表 → 不推送（可能该批全成功或无单）" % day)
        return 0                       # 不是失败：没表就没得推

    print("[push] 找到 %d 份表：" % len(files))
    for k, v in files.items():
        print("   %-4s %s  %s" % (k, os.path.basename(v), summarize(v)))

    # ★ 2026-11-15 用户令：六店合成**一个**汇总表（一店一组 sheet，按顺序排）。
    merged, merged_name = "", ""
    try:
        merged = merge_reports(day, files)
    except Exception as ex:  # noqa: BLE001
        print("[push] ⚠ 合并汇总表失败（%s）→ 退回逐店上传" % str(ex)[:150])
    if merged:
        merged_name = os.path.basename(merged)
        print("[push] 汇总表：%s（%d 个 sheet）" % (merged, _sheet_count(merged)))

    if a.dry_run:
        title, msg = build_message(day, files, {}, merged_link="(dry-run)", 
                                   merged_name=merged_name)
        print("\n--- 标题 ---\n%s\n--- 正文 ---\n%s" % (title, msg))
        print("\n[dry-run] 未推送、未上传")
        return 0

    # ② 先传云盘（要把链接写进群消息）
    uploaded, failed, merged_link = {}, [], ""
    if merged:
        ok, info = upload(merged, day, "汇总", name="%s-发货汇总.xlsx" % day)
        print("[push] 云盘 汇总 %s %s" % ("✅" if ok else "✗", info[:160]))
        if ok:
            merged_link = info
        else:
            failed.append("汇总")
    else:
        # 退回逐店（合并失败时不能让当天一份都不传）
        for store in SUMMARY_STORES:
            p = files.get(store)
            if not p:
                continue
            ok, info = upload(p, day, store)
            print("[push] 云盘 %-4s %s %s" % (store, "✅" if ok else "✗", info[:160]))
            if ok:
                uploaded[store] = info
            else:
                failed.append(store)

    # ③ 再推群
    title, msg = build_message(day, files, uploaded, merged_link=merged_link,
                               merged_name=merged_name)
    ok_fs, info_fs = False, "未尝试"
    try:
        ok_fs, info_fs = push_feishu_fixed(title, msg)
    except Exception as ex:  # noqa: BLE001
        info_fs = "%s: %s" % (type(ex).__name__, str(ex)[:160])
    print("[push] 飞书群 %s %s" % ("✅" if ok_fs else "✗", str(info_fs)[:160]))

    print("\n[RESULT] " + json.dumps({
        "date": day, "files": {k: os.path.basename(v) for k, v in files.items()},
        "merged": merged_name, "merged_link": merged_link,
        "uploaded": sorted(uploaded), "upload_failed": failed,
        "feishu": bool(ok_fs),
        "ok": bool(ok_fs) and not failed,
    }, ensure_ascii=False))
    # ⚠ 永不因为推送/上传失败就返回非零：发货本身已经做完了。
    #   但把失败如实打出来，让日志和面板能看见。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
