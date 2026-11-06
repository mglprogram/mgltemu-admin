# -*- coding: utf-8 -*-
"""产品手册 → 面板静态数据。

面板位置：产品-库存 → 产品手册（/product/product-manual）。
源文件：D:\\together\\产品手册.xlsx（用户维护；WPS 内嵌图片按 DISPIMG 单元格聚合）

产出：
  temu-admin/public/data/product-manual.json   产品明细 / 款号规则
  temu-admin/public/manual-img/<ID>.png        从 xlsx 抽出的 WPS 内嵌图（产品图/尺码表/洗水唛/克重图）

⚠️ 价格默认**不导出**：出厂价进的是公开面板，只有加 --with-price 才会带上 出厂价/变动 字段。
    默认输出里连「🔒」占位都没有，避免误以为面板上有价格。

用法：
  python scripts/goods/build_product_manual.py            # 生成
  python scripts/goods/build_product_manual.py --dry-run  # 只看统计不写文件
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "temu-admin"
DATA_OUT = ADMIN / "public" / "data" / "product-manual.json"
IMG_OUT = ADMIN / "public" / "manual-img"
DEFAULT_SRC = Path(r"D:\together\产品手册.xlsx")

DISPIMG_RE = re.compile(r'DISPIMG\("(ID_[0-9A-Fa-f]+)"')


# ---------------------------------------------------------------- 图片
def load_images(src: Path) -> dict[str, bytes]:
    """ID_xxx -> 原始字节。读 WPS cellimages.xml + rels。"""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        if "xl/cellimages.xml" not in names:
            return out
        rels = dict(
            re.findall(
                r'Id="(rId\d+)"\s+Type="[^"]*image"\s+Target="([^"]+)"',
                z.read("xl/_rels/cellimages.xml.rels").decode("utf-8"),
            )
        )
        xml = z.read("xl/cellimages.xml").decode("utf-8")
        for blk in re.findall(r"<etc:cellImage>.*?</etc:cellImage>", xml, re.S):
            m = re.search(r'name="(ID_[0-9A-Fa-f]+)"', blk)
            r = re.search(r'r:embed="(rId\d+)"', blk)
            if not (m and r) or r.group(1) not in rels:
                continue
            target = "xl/" + rels[r.group(1)].lstrip("/")
            if target in names:
                out[m.group(1)] = z.read(target)
    return out


def save_webp(data: bytes, path: Path, max_side: int = 1200) -> None:
    """统一转 WebP（q=82，长边 ≤1200）。

    为什么不全存原图：源表里 100+ 张是手机原片（4000px、10~20 MB），
    313 张合计 149 MB，放到面板上首屏根本加载不动。1200 长边看尺码表绰绰有余，
    点开大图也不糊。
    """
    im = Image.open(io.BytesIO(data))
    if im.mode in ("P", "LA"):
        im = im.convert("RGBA")
    elif im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        k = max_side / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    im.save(path, format="WEBP", quality=82, method=4)


# ---------------------------------------------------------------- 单元格
def text_of(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def img_ids(v) -> list[str]:
    if not isinstance(v, str):
        return []
    return DISPIMG_RE.findall(v)


def clean_text(v) -> str:
    """价格列（公式 / 🔒 占位）一律不展示。"""
    t = text_of(v)
    if t.startswith("=") or t.startswith("🔒"):
        return ""
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-price", action="store_true",
                    help="导出出厂价/变动（仅在内网使用时才加；默认不导出）")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"[err] 源文件不存在：{src}", file=sys.stderr)
        return 2

    wb = openpyxl.load_workbook(src, data_only=False)
    images = load_images(src)
    print(f"[info] 内嵌图片 {len(images)} 张，工作表 {wb.sheetnames}")

    # ---------- 产品明细 ----------
    ws = wb["产品明细"]
    header = [text_of(c.value) for c in ws[1]]
    # 价格列（出厂价 / 变动）在源表里是 IF(...) 公式，只认表头
    price_cols = {i for i, h in enumerate(header, 1) if h in ("出厂价", "变动")}
    col = {h: i for i, h in enumerate(header, 1) if h}

    def col_idx(name: str) -> int | None:
        return col.get(name)

    c_name, c_style = col_idx("中文名称"), col_idx("款号")
    c_hs, c_fac = col_idx("报关编码"), col_idx("做货工厂")
    c_pic, c_size, c_comp = col_idx("图片"), col_idx("尺码表"), col_idx("产品克重")
    c_rules = col_idx("洗水唛成分（后台显示）")

    def clean(v) -> str:
        return clean_text(v)

    products = []
    used_ids: set[str] = set()
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        r = row[0].row
        values = {i: cell.value for i, cell in enumerate(row, 1)}
        style = text_of(values.get(c_style))
        name = text_of(values.get(c_name))
        if not style and not name:
            continue

        def imgs_of(i: int | None) -> list[str]:
            ids = [x for x in (img_ids(values.get(i)) if i else []) if x in images]
            used_ids.update(ids)
            return [f"/manual-img/{x}.webp" for x in ids]

        pic = imgs_of(c_pic)
        size_ids = imgs_of(c_size)
        comp_ids = imgs_of(c_rules)
        weight_ids = imgs_of(c_comp)

        item = {
            "row": r,
            "style": style,
            "name": name,
            "hs": text_of(values.get(c_hs)),
            "factory": text_of(values.get(c_fac)),
            "img": pic[0] if pic else None,
            # 尺码表列本身就是「尺码表 + 定做重量表」的合成图，原样展示
            "sizeImgs": size_ids,
            "compImgs": comp_ids,
            "weightImgs": weight_ids,
            # 文本列只在单元格不是图片公式时保留
            "weight": "" if weight_ids else clean_text(values.get(c_comp)),
            "careLabel": "" if comp_ids else clean_text(values.get(c_rules)),
        }
        if args.with_price:
            item["price"] = clean_text(values.get(col_idx("出厂价") or 0))
            item["priceChange"] = clean_text(values.get(col_idx("变动") or 0))
        products.append(item)

    # ---------- 款号命名规则（原样搬运网格） ----------
    rules_sheet = next((n for n in wb.sheetnames if "款号" in n and "规则" in n), None)
    rules_grid: list[list[str]] = []
    if rules_sheet:
        rs = wb[rules_sheet]
        for row in rs.iter_rows(min_row=1, max_row=rs.max_row):
            line = [text_of(c.value) for c in row]
            if any(line):
                rules_grid.append(line)

    # ---------- 数据 ----------
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "generatedAt": gen,
        "source": str(src),
        "sourceMtime": datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "withPrice": bool(args.with_price),
        "fields": [h for i, h in enumerate(header, 1) if h and i not in price_cols],
        "products": products,
        "rules": rules_grid,
    }

    n_img = len(used_ids)
    n_sec = sum(1 for p in products if p["sizeImgs"])
    print(f"[info] 产品 {len(products)} 条；带主图 {sum(1 for p in products if p['img'])}；"
          f"带尺码/洗水唛图 {n_sec}；引用图片 {n_img} 张")
    if args.dry_run:
        out = {k: payload[k] for k in ("generatedAt", "withPrice", "fields")}
        out["sample"] = products[:2]
        print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
        return 0

    IMG_OUT.mkdir(parents=True, exist_ok=True)

    # 只导出被引用到的图（其余几十张是草稿/重复图，不浪费体积）
    written = 0
    for iidd in sorted(used_ids):
        dst = IMG_OUT / f"{iidd}.webp"
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue
        save_webp(images[iidd], dst)
        written += 1

    # 清理不再引用的旧产物
    keep_img = {f"{i}.webp" for i in used_ids}
    for f in IMG_OUT.iterdir():
        if f.suffix.lower() in (".webp", ".png") and f.name not in keep_img:
            f.unlink()

    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    size_kb = DATA_OUT.stat().st_size / 1024
    total_mb = sum(f.stat().st_size for f in IMG_OUT.glob("*.webp")) / 1024 / 1024
    print(f"[ok] {DATA_OUT} ({size_kb:.1f} KB)，新/更新图 {written} 张，图片合计 {total_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
