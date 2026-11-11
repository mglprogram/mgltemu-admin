# -*- coding: utf-8 -*-
"""shein_fill_stock_template.py — 往 SHEIN「库存模板」(SKU/仓库编码/数量) 里批量填行。

为什么不用 openpyxl 存盘：
    2026-11-11 实测 —— SHEIN 商家库存导入（BD 店）**只认自己导出的 XML 写法**。
    用 openpyxl 读进来再 save，会把 inlineStr 变成 sharedStrings、丢掉原始样式节点，
    上传就报「导入信息失败 请填写库存数据」（它按 inlineStr 找文字，结果全空）。
    本脚本因此**只改 zip 里的两个 XML**（sharedStrings.xml + 首个 sheet），
    其余条目按原字节写回，结构对 SHEIN 完全透明。

用法：
    # 往模板里填 5 行：SKU:仓库编码:数量
    python scripts\\goods\\shein_fill_stock_template.py \
        --template "D:\\...\\import-model.xlsx" \
        --out "D:\\...\\import-model_清零.xlsx" \
        --set I1mmdaw68crpmt:PS271880016572:0 \
        --set I6mlhtn9dgy8na:PS271880016572:0

    # 从文件批量读（每行 SKU,仓库编码,数量，允许表头）
    python scripts\\goods\\shein_fill_stock_template.py --template tpl.xlsx --out o.xlsx --csv rows.csv

    # 只预览不写文件
    python scripts\\goods\\shein_fill_stock_template.py ... --dry-run

⚠ 模板里的「SKU」列是 **平台 SKU 码**（形如 I1mmdaw68crpmt），不是商家 SKU（BD-…）。
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import zipfile
from xml.sax.saxutils import escape


# ───────────────────────── xlsx 结构定位 ─────────────────────────
def _first_sheet_path(z: zipfile.ZipFile) -> str:
    """workbook.xml + rels → 第一个 sheet 的 xml 路径（不靠 sheet1.xml 这种约定）。"""
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = re.search(r'<sheet\b[^>]*r:id="([^"]+)"', wb)
    if not rid:
        raise SystemExit("workbook.xml 里找不到 sheet")
    rel = re.search(r'<Relationship\b[^>]*Id="%s"[^>]*Target="([^"]+)"' % re.escape(rid.group(1)), rels)
    if not rel:
        raise SystemExit("workbook.xml.rels 里找不到 rId=%s" % rid.group(1))
    target = rel.group(1).lstrip("/")
    if target.startswith("xl/"):
        return target
    return posixpath.normpath(posixpath.join("xl", target))


def _last_row(xml: str) -> int:
    rows = [int(x) for x in re.findall(r'<row r="(\d+)"', xml)]
    return max(rows) if rows else 0


# ───────────────────────── sharedStrings ─────────────────────────
def _parse_sst(xml: str):
    """返回 (头部字符串, [si...], 尾部字符串, (count, uniqueCount) 匹配对象)。"""
    m = re.search(r"<sst\b([^>]*)>(.*)</sst>", xml, re.S)
    if not m:
        raise SystemExit("sharedStrings.xml 结构不认识")
    attrs, body = m.group(1), m.group(2)
    items = re.findall(r"<si\b.*?</si>|<si\s*/>", body, re.S)
    nums = re.search(r'count="(\d+)"\s+uniqueCount="(\d+)"', attrs) or \
        re.search(r'uniqueCount="(\d+)"\s+count="(\d+)"', attrs)
    if not nums:
        raise SystemExit("sharedStrings 缺 count/uniqueCount")
    a, b = nums.groups()
    # 顺序可能是 count,uniqueCount 或反
    if attrs.find("count=") < attrs.find("uniqueCount="):
        count, uniq = int(a), int(b)
    else:
        count, uniq = int(b), int(a)
    return m.start(2), m.end(2), items, count, uniq


def _si_text(item: str) -> str:
    t = re.search(r"<t\b[^>]*>(.*?)</t>", item, re.S)
    return t.group(1) if t else ""


# ───────────────────────── 主流程 ─────────────────────────
def fill(template: str, out: str, rows: list[tuple[str, str, float]],
         start_row: int | None = None, dry_run: bool = False) -> str:
    zin = zipfile.ZipFile(template, "r")
    names = zin.namelist()
    sheet_path = _first_sheet_path(zin)
    sh = zin.read(sheet_path).decode("utf-8")
    sst = zin.read("xl/sharedStrings.xml").decode("utf-8")

    body_start, body_end, items, count, uniq = _parse_sst(sst)
    index: dict[str, int] = {}
    for i, it in enumerate(items):
        index.setdefault(_si_text(it), i)

    def sid(text: str) -> int:
        if text in index:
            return index[text]
        index[text] = len(items)
        items.append('<si><t xml:space="preserve">%s</t></si>' % escape(text))
        return index[text]

    first = start_row or (_last_row(sh) + 1)
    new_rows: list[str] = []
    for i, (sku, wh, qty) in enumerate(rows):
        r = first + i
        q = ("%g" % float(qty))
        new_rows.append(
            '<row r="%d" spans="1:3">'
            '<c r="A%d" t="s"><v>%d</v></c>'
            '<c r="B%d" t="s"><v>%d</v></c>'
            '<c r="C%d"><v>%s</v></c>'
            "</row>" % (r, r, sid(sku), r, sid(wh), r, q))

    last = first + len(rows) - 1
    dim = re.search(r'<dimension ref="([A-Z]+\d+):([A-Z]+\d+)"\s*/>', sh)
    if dim:
        sh = sh[:dim.start()] + '<dimension ref="A1:C%d"/>' % last + sh[dim.end():]

    sh = sh.replace("</sheetData>", "".join(new_rows) + "</sheetData>")

    new_body = "".join(items)
    new_sst = sst[:body_start] + new_body + sst[body_end:]
    add_cells = 2 * len(rows)                       # 每行 A/B 两个字符串单元格
    new_sst = re.sub(r'count="\d+"', 'count="%d"' % (count + add_cells), new_sst, count=1)
    new_sst = re.sub(r'uniqueCount="\d+"', 'uniqueCount="%d"' % len(items), new_sst, count=1)

    print("[fill] 模板 %s" % os.path.basename(template))
    print("[fill] sheet=%s 原数据行=%d → 追加 %d 行（行号 %d~%d）"
          % (sheet_path, _last_row(zin.read(sheet_path).decode("utf-8")), len(rows), first, last))
    for (sku, wh, qty), r in zip(rows, range(first, last + 1)):
        print("   %-16s %-16s %-6g (第 %d 行)" % (sku, wh, qty, r))

    if dry_run:
        print("[fill] --dry-run：不写文件")
        return ""

    tmp = out + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            if n == sheet_path:
                data = sh.encode("utf-8")
            elif n == "xl/sharedStrings.xml":
                data = new_sst.encode("utf-8")
            else:
                data = zin.read(n)
            zout.writestr(n, data)
    zin.close()
    os.replace(tmp, out)
    print("[fill] 写出 %s（%d 字节）" % (out, os.path.getsize(out)))
    return out


def verify(path: str) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    print("[verify] sheets=%s 首表=%s 行数=%d" % (wb.sheetnames, ws.title, ws.max_row))
    for r in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
        print("   ", r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="往 SHEIN 库存模板里填 SKU/仓库编码/数量")
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="SKU:仓库编码:数量")
    ap.add_argument("--csv", default="", help="每行 SKU,仓库编码,数量（可带表头）")
    ap.add_argument("--start-row", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="写完后回读打印")
    a = ap.parse_args(argv)

    rows: list[tuple[str, str, float]] = []
    for s in a.set:
        parts = s.split(":")
        if len(parts) != 3:
            raise SystemExit("--set 要写成 SKU:仓库编码:数量，收到 %r" % s)
        rows.append((parts[0].strip(), parts[1].strip(), float(parts[2])))
    if a.csv:
        with open(a.csv, encoding="utf-8-sig") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                p = [x.strip() for x in re.split(r"[,\t]", ln)]
                if len(p) < 3 or p[2].lower() in ("数量", "qty"):
                    continue
                rows.append((p[0], p[1], float(p[2])))
    if not rows:
        raise SystemExit("没有任何要填的行（--set / --csv）")

    out = fill(a.template, a.out, rows, a.start_row or None, a.dry_run)
    if out and a.verify:
        verify(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
