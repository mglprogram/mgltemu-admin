"""把「图库/<款号>/ 文件夹」里的原图全部规整成 TEMU 商品图（3:4、>=1340x1785、<=2MB）。

用法:
    python scripts/goods/prep_images.py --src "D:/together/.../图库/202301/202301-10SetA" \
        --out "out/202301-10SetA/img"

规则（2026-11-26 用户要求「以后把该文件夹的图片全部上传」）:
  · 目录里**所有** png/jpg/jpeg/webp 一律处理，不挑不筛
  · 输出统一 1340x1785，比例不符时先等比放大再居中裁切（不拉伸变形）
  · 输出顺序与源文件名排序一致；可用 --main 指定主图（默认字典序第一个）
  · 结果落盘 manifest.json：{src, out, width, height, bytes}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

TARGET_W, TARGET_H = 1340, 1785
MAX_BYTES = 2 * 1024 * 1024
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def fit_to_34(src: Path, dst: Path, quality: int = 92) -> dict:
    im = Image.open(src)
    src_size = im.size
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    scale = max(TARGET_W / w, TARGET_H / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    # 缩放时做一次抗锯齿；放大用 LANCZOS 比默认 BICUBIC 更锐
    im2 = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - TARGET_W) // 2, (nh - TARGET_H) // 2
    out = im2.crop((left, top, left + TARGET_W, top + TARGET_H))
    dst.parent.mkdir(parents=True, exist_ok=True)
    for q in (quality, 88, 84, 80, 75):
        out.save(dst, "JPEG", quality=q, optimize=True)
        if dst.stat().st_size <= MAX_BYTES:
            break
    return {
        "src": str(src),
        "out": str(dst),
        "src_size": list(src_size),
        "width": TARGET_W,
        "height": TARGET_H,
        "bytes": dst.stat().st_size,
        "quality": q,
    }


def collect(src_dir: Path) -> list[Path]:
    return sorted(
        (p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in EXTS),
        key=lambda p: p.name,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="图库文件夹 → TEMU 3:4 商品图")
    ap.add_argument("--src", required=True, help="图库里的款号文件夹（全部图片都会处理）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--main", default="", help="主图文件名（默认排序第一个）")
    ap.add_argument("--quality", type=int, default=92)
    args = ap.parse_args(argv)

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print("源目录不存在: %s" % src_dir, file=sys.stderr)
        return 2
    files = collect(src_dir)
    if not files:
        print("目录里没有图片: %s" % src_dir, file=sys.stderr)
        return 2

    # 主图排第一，其余保持字典序
    if args.main:
        main_path = src_dir / args.main
        files = [p for p in files if p.name == args.main] + [p for p in files if p.name != args.main]
        if files[0].name != args.main:
            print("主图不在目录里: %s" % main_path, file=sys.stderr)
            return 2

    out_dir = Path(args.out)
    rows = []
    for i, p in enumerate(files, 1):
        dst = out_dir / ("%02d_%s.jpg" % (i, p.stem[:40]))
        meta = fit_to_34(p, dst, quality=args.quality)
        rows.append(meta)
        print("  %-2d %-46s %sx%s -> %sx%s  %d KB"
              % (i, p.name, meta["src_size"][0], meta["src_size"][1],
                 meta["width"], meta["height"], meta["bytes"] // 1024))

    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("共 %d 张，清单: %s" % (len(rows), manifest))
    print("写进 spec 的 images 顺序（主图在前）:")
    for r in rows:
        print("  " + r["out"].replace("\\", "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
