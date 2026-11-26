# -*- coding: utf-8 -*-
"""批量买面单：按用户给的 KLS 单号清单跑（先 dry-run，再 --apply）。

用法：
    python pw_batch_label.py --store KINGLAMANSPORT --file pos.txt
    python pw_batch_label.py --store KINGLAMANSPORT --file pos.txt --apply
"""
import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RUNNER = os.path.join(_HERE, "run_temu_buylabel_api.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="KINGLAMANSPORT")
    ap.add_argument("--file", required=True, help="单号清单（一行一个）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 单（0=全部）")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    pos = [x.strip() for x in open(a.file, encoding="utf-8").read().splitlines()
           if x.strip().upper().startswith("PO-") or x.strip().upper().startswith("GSU")]
    # 去重保序（用户清单里有重复：PO-211-07263725619830385 出现 3 次）
    seen, uniq = set(), []
    for p in pos:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if a.limit:
        uniq = uniq[:a.limit]
    print("原始 %d 条 → 去重后 %d 条%s"
          % (len(pos), len(uniq), ("（取前 %d）" % a.limit) if a.limit else ""))
    if not uniq:
        return 1

    # ★ `--pos` 是单值参数 ⇒ 一次只收一个单号，必须逐个调（2026-11-26 实测）
    if a.apply:
        print("执行：%d 单，模式=真下单（逐单调用）\n" % len(uniq))
        ok = bad = 0
        for i, po in enumerate(uniq, 1):
            p = subprocess.run([PY, "-X", "utf8", RUNNER, "--store", a.store,
                                "--pos", po, "--apply"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
            line = next((x for x in tail if "下单成功" in x or "❌" in x), "")
            print("  [%d/%d] %-26s rc=%d %s"
                  % (i, len(uniq), po, p.returncode, line.strip()[:120]))
            if p.returncode == 0:
                ok += 1
            else:
                bad += 1
        print("\n汇总：成功 %d / 失败 %d / 共 %d" % (ok, bad, len(uniq)))
        return 0 if bad == 0 else 1

    print("执行：%d 单，模式=dry-run\n" % len(uniq))
    nok = nbad = 0
    for i, po in enumerate(uniq, 1):
        p = subprocess.run([PY, "-X", "utf8", RUNNER, "--store", a.store,
                            "--pos", po],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        line = next((x for x in tail if "可下单" in x), "")
        if not line:
            # ★ 2026-11-26：异常/未开窗也要打出来，别静默吞掉
            err = next((x for x in reversed(tail)
                        if "Error" in x or "错误" in x or "未开" in x or "❌" in x), "")
            line = (err or (tail[-1] if tail else "(无输出)"))
            nbad += 1
        else:
            nok += 1
        print("  [%d/%d] %-26s %s" % (i, len(uniq), po, line.strip()[:130]))
    print("\n汇总：可下单 %d / 有问题 %d / 共 %d" % (nok, nbad, len(uniq)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
