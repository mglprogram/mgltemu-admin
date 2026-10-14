# -*- coding: utf-8 -*-
"""发布 TEMU 运营后台（Vue3 管理端）到 Cloudflare Pages。

与老的 publish_cf.py（发布单文件 HTML 看板）并存：
  - publish_cf.py    -> data/temu_dashboard.html（旧看板，仍在跑）
  - publish_admin.py -> temu-admin/dist（本 Vue 管理后台）

流程：
  1. build_api_data.py 刷新 public/data/*.json（静态数据层）
  2. pnpm build-only 产出 dist
  3. 往 dist/index.html 注入访问密码门禁（面板含店铺货号/库存/申报价，不能裸奔）
  4. wrangler pages deploy 上传

用法：
  python publish_admin.py --build                  # 构建 + 上传到预览分支（不动线上）
  python publish_admin.py --build --production     # 构建 + 上传到生产（会覆盖现有站点）
  python publish_admin.py --build-only             # 只本地构建，不发布
  python publish_admin.py --url-only               # 只打印默认地址

环境变量：
  DASH_PASSWORD    访问密码（必填；留空则拒绝发布，避免裸奔）
  CF_PROJECT_NAME  Pages 项目名（默认 temu-dashboard；线上同时挂在
                   temu-dashboard.pages.dev 与 temu-dashboard-bjb.pages.dev）
  CF_PREVIEW_BRANCH 预览分支名（默认 admin-preview）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
ADMIN = ROOT / "temu-admin"
DIST = ADMIN / "dist"
INDEX = DIST / "index.html"
DATA_SCRIPT = ROOT / "scripts" / "bel_goods" / "build_api_data.py"
AUTO_SCRIPT = ROOT / "scripts" / "bel_goods" / "build_automation_data.py"
STOCK_SCRIPT = ROOT / "scripts" / "bel_goods" / "build_presale_data.py"
GOODS_SCRIPT = ROOT / "scripts" / "goods" / "build_goods_data.py"
RESTOCK_SCRIPT = ROOT / "scripts" / "goods" / "enrich_restock.py"

CF_PROJECT = os.environ.get("CF_PROJECT_NAME", "temu-dashboard")
PREVIEW_BRANCH = os.environ.get("CF_PREVIEW_BRANCH", "admin-preview")

GREEN, RED, DIM = "\033[32m", "\033[31m", "\033[2m"
RESET = "\033[0m"


def step(msg: str) -> None:
    print(f"{DIM}->{RESET} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET} {msg}", flush=True)


def bad(msg: str) -> None:
    print(f"{RED}[FAIL]{RESET} {msg}", flush=True)


def run(cmd, cwd=None, env=None, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=env, timeout=timeout)


def node_exe() -> str:
    p = shutil.which("node")
    if p:
        return p
    cand = Path.home() / ".dsh" / "dsh-runtimes" / "dsh-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    if cand.exists():
        return str(cand)
    raise RuntimeError("未找到 node")


def pnpm_entry() -> Path:
    """pnpm 的真实 js 入口。

    Windows 上 pnpm 是 .cmd 包装，Python 不能直接 spawn，必须找到底层 .cjs/.js。
    优先 corepack 安装目录（本项目用 corepack 启用的 pnpm）。
    """
    # corepack 安装的 pnpm：%LOCALAPPDATA%\node\corepack\v1\pnpm\<ver>\bin\pnpm.cjs
    corepack_root = Path(os.environ.get("LOCALAPPDATA", "")) / "node" / "corepack" / "v1" / "pnpm"
    if corepack_root.exists():
        hits = sorted(corepack_root.glob("*/bin/pnpm.cjs"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]

    for c in [
        Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs",
        Path(os.environ.get("LOCALAPPDATA", "")) / "pnpm" / "pnpm.cjs",
    ]:
        if c.exists():
            return c

    # 兜底：从 .cmd shim 里正则抠出真实 js 路径
    shim = Path(os.environ.get("APPDATA", "")) / "npm" / "pnpm.cmd"
    if shim.exists():
        txt = shim.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"([^"]*pnpm[^"]*\.(?:cjs|js))"', txt):
            cand = Path(m.group(1))
            if cand.exists():
                return cand
    raise RuntimeError("未找到 pnpm，请先 corepack enable --install-directory %APPDATA%\\npm")


def npx_entry() -> Path:
    p = shutil.which("npx")
    if p and p.lower().endswith(".cmd"):
        txt = Path(p).read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"([^"]*npx[^"]*\.js)"', txt)
        if m and Path(m.group(1)).exists():
            return Path(m.group(1))
    for c in [
        Path(os.environ.get("APPDATA", "")) / "npm/node_modules/npm/bin/npx-cli.js",
        Path(r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js"),
    ]:
        if c.exists():
            return c
    raise RuntimeError("未找到 npx-cli.js")


CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "91c4d2f7d1b53f4465e6e442ce5f7481")


def cf_token() -> str:
    """取 Cloudflare API Token。

    优先用进程环境变量；没有就读**用户级**持久化变量
    （本机已把 token 存到用户环境，wrangler login 的 OAuth 会因为它而拒绝交互登录，
    所以这里直接复用同一个 token）。
    """
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if tok:
        return tok
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "CLOUDFLARE_API_TOKEN")[0]
    except Exception:  # noqa: BLE001
        return ""


def wrangler(args, timeout=900):
    env = dict(os.environ)
    env["CI"] = "true"
    env["npm_config_yes"] = "true"
    tok = cf_token()
    if tok:
        env["CLOUDFLARE_API_TOKEN"] = tok
        env["CLOUDFLARE_ACCOUNT_ID"] = CF_ACCOUNT_ID
    home = str(Path.home())
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = str(Path(home) / "AppData" / "Roaming" / "xdg.config")
    env["XDG_CACHE_HOME"] = str(Path(home) / ".wrangler-cache")
    env["XDG_DATA_HOME"] = str(Path(home) / ".wrangler-data")
    cmd = [node_exe(), str(npx_entry()), "--yes", "wrangler@latest", *args]
    return run(cmd, cwd=home, env=env, timeout=timeout)


# ---------------------------------------------------------------- 密码门禁
GATE = """
<style>
#gate{position:fixed;inset:0;background:#0f1115;z-index:99999;
display:flex;align-items:center;justify-content:center;font-family:system-ui}
#gate .box{background:#171a21;border:1px solid #272c36;border-radius:10px;
padding:28px 32px;width:340px;text-align:center}
#gate h3{margin:0 0 6px;color:#e6e8ec;font-size:17px}
#gate p{margin:0 0 16px;color:#6f7682;font-size:12px}
#gate input{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:6px;
border:1px solid #272c36;background:#0f1115;color:#e6e8ec;font-size:14px;outline:none}
#gate input:focus{border-color:#599ce7}
#gate button{width:100%;margin-top:12px;padding:9px;border:0;border-radius:6px;
background:#599ce7;color:#0f1115;font-size:14px;font-weight:600;cursor:pointer}
#gate .err{color:#e05561;font-size:12px;margin-top:10px;min-height:16px}
</style>
<div id="gate"><div class="box">
  <h3>TEMU 运营后台</h3>
  <p>请输入访问密码</p>
  <input id="gp" type="password" placeholder="密码" autocomplete="current-password">
  <button id="gb">进入</button>
  <div class="err" id="ge"></div>
</div></div>
<script>
(function(){
  var KEY=__PW_KEY__, want=__PW_VAL__;
  function pass(){
    var v=(document.getElementById('gp').value||'').trim();
    if(v===want){ try{ sessionStorage.setItem(KEY,'1'); }catch(e){}
                   document.getElementById('gate').remove(); }
    else { document.getElementById('ge').textContent='密码不正确'; }
  }
  try{ if(sessionStorage.getItem(KEY)==='1'){ document.getElementById('gate').remove(); } }catch(e){}
  document.getElementById('gb').onclick=pass;
  document.getElementById('gp').addEventListener('keydown',function(e){ if(e.key==='Enter') pass(); });
  document.getElementById('gp').focus();
})();
</script>
"""


def add_password(html: str, password: str) -> str:
    gate = GATE.replace("__PW_KEY__", json.dumps("temu_admin_gate_ok"))
    gate = gate.replace("__PW_VAL__", json.dumps(password))
    if "</head>" not in html:
        raise RuntimeError("index.html 里找不到 </head>，无法注入门禁")
    out = html.replace("</head>", gate + "</head>", 1)
    if "__PW_VAL__" in out or "__PW_KEY__" in out:
        raise RuntimeError("密码门禁注入失败：占位符未替换")
    return out


# ---------------------------------------------------------------- 构建
def build_data() -> None:
    step("刷新静态数据层 public/data/*.json")
    p = run([sys.executable, str(DATA_SCRIPT)], timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"数据导出失败：{p.stderr[-400:]}")
    print("   " + "\n   ".join((p.stdout or "").strip().splitlines()[-4:]))

    # 自动化任务数据（读 Windows 计划任务 + DSH 存储，失败不阻断整体发布）
    step("刷新自动化任务数据 public/data/automation.json")
    q = run([sys.executable, str(AUTO_SCRIPT)], timeout=600)
    if q.returncode != 0:
        print(f"   [warn] 自动化数据导出失败（不阻断发布）：{(q.stderr or '')[-300:]}")
    else:
        print("   " + "\n   ".join((q.stdout or "").strip().splitlines()[-3:]))

    # 预售建议数据（TEMU 销量分析 + 海外仓看板 + 飞书领星；窗口没开/飞书读不到都不阻断发布）
    step("刷新预售建议数据 public/data/presale.json")
    r = run([sys.executable, str(STOCK_SCRIPT)], timeout=900)
    if r.returncode != 0:
        print(f"   [warn] 预售数据导出失败（不阻断发布）：{(r.stderr or '')[-300:]}")
    else:
        print("   " + "\n   ".join((r.stdout or "").strip().splitlines()[-5:]))


    # 在售款明细（官方导出 xlsx 优先，其次接口抓取产物；两条都缺就不覆盖 goods.json）
    step("刷新在售款明细 public/data/goods.json")
    g = run([sys.executable, str(GOODS_SCRIPT)], timeout=300)
    if g.returncode != 0:
        print(f"   [warn] 在售款数据导出失败（不阻断发布）：{(g.stderr or '')[-300:]}")
    else:
        print("   " + "\n   ".join((g.stdout or "").strip().splitlines()[-4:]))
        # 可加库存判定（海外仓看板 + 飞书领星到货表；取不到不阻断，面板仍出低库存清单）
        step("判定低库存款能否加库存（看板 + 飞书）")
        e = run([sys.executable, str(RESTOCK_SCRIPT)], timeout=600)
        if e.returncode != 0:
            print(f"   [warn] 加库存判定失败（不阻断发布）：{(e.stderr or '')[-300:]}")
        else:
            print("   " + "\n   ".join((e.stdout or "").strip().splitlines()[-6:]))


def build_frontend() -> None:
    step("构建前端（pnpm build-only）")
    env = dict(os.environ)
    env["NODE_ENV"] = "production"
    p = run([node_exe(), str(pnpm_entry()), "run", "build-only"],
            cwd=str(ADMIN), env=env, timeout=1800)
    if p.returncode != 0 or not INDEX.exists():
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-15:]
        raise RuntimeError("前端构建失败：\n   " + "\n   ".join(tail))
    ok(f"构建完成 {DIST}")


def inject_gate(password: str) -> None:
    step("注入访问密码门禁")
    html = INDEX.read_text(encoding="utf-8")
    INDEX.write_text(add_password(html, password), encoding="utf-8")
    ok(f"门禁已注入（{INDEX.stat().st_size / 1024:.1f} KB）")


def check_login() -> bool:
    p = wrangler(["whoami"], timeout=420)
    out = ((p.stdout or "") + (p.stderr or "")).lower()
    if p.returncode == 0 and "account" in out:
        ok("Cloudflare 已登录")
        return True
    bad("未登录 Cloudflare")
    print("   请先运行：npx wrangler login")
    return False


def deploy(production: bool) -> str:
    branch = "main" if production else PREVIEW_BRANCH
    step(f"上传到 Cloudflare Pages：{CF_PROJECT}（分支 {branch}）")
    args = ["pages", "deploy", str(DIST),
            "--project-name", CF_PROJECT,
            "--branch", branch,
            "--commit-message", "TEMU admin: 加库存建议页面",
            "--commit-dirty=true"]
    p = wrangler(args, timeout=1200)
    out = ((p.stdout or "") + (p.stderr or ""))
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    if p.returncode != 0:
        bad("上传失败")
        print("   " + "\n   ".join(plain.strip().splitlines()[-12:]))
        return ""
    ok("上传成功")
    urls = re.findall(r"https://[^\s]+", plain)
    for u in urls:
        print("   " + u.rstrip("."))
    return urls[0].rstrip(".") if urls else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="构建并发布")
    ap.add_argument("--build-only", action="store_true", help="只本地构建")
    ap.add_argument("--production", action="store_true", help="发布到生产分支（覆盖线上站点）")
    ap.add_argument("--url-only", action="store_true")
    a = ap.parse_args()

    print()
    if a.url_only:
        print(f"   项目    : {CF_PROJECT}")
        print(f"   生产    : https://{CF_PROJECT}.pages.dev")
        print(f"   预览分支: https://{PREVIEW_BRANCH}.{CF_PROJECT}.pages.dev")
        return 0

    if not (a.build or a.build_only):
        ap.print_help()
        return 0

    pw = os.environ.get("DASH_PASSWORD", "")
    if not pw:
        bad("未设置 DASH_PASSWORD —— 面板含店铺货号/库存/申报价，不许裸奔")
        print('   PowerShell: $env:DASH_PASSWORD="你的密码"; python publish_admin.py --build')
        return 3

    try:
        build_data()
        build_frontend()
        inject_gate(pw)
    except Exception as e:  # noqa: BLE001
        bad(str(e))
        return 2

    if a.build_only:
        ok("仅本地构建完成（未发布）")
        print(f"   本地预览：cd {ADMIN} && pnpm preview")
        return 0

    if not check_login():
        return 2
    url = deploy(production=a.production)

    print()
    if url:
        ok("访问地址")
        print(f"   {url}")
        print(f"   访问密码: {pw}")
    if not a.production:
        print(f"\n   {DIM}当前是预览分支，线上 {CF_PROJECT}.pages.dev 未受影响。")
        print(f"   确认无误后加 --production 发布到线上。{RESET}")
    return 0 if url else 1


if __name__ == "__main__":
    raise SystemExit(main())