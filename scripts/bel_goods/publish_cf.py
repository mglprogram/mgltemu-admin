# -*- coding: utf-8 -*-
"""把 TEMU 运营看板发布到 Cloudflare Pages。

前置：
  1) 域名已把 NS 改到 Cloudflare（控制台会给你两个 NS 服务器地址）
  2) 本机已登录 Cloudflare：npx wrangler login

用法：
  python publish_cf.py --build      # 生成带访问密码的页面并上传
  python publish_cf.py --url-only   # 只打印该绑定的访问 URL
  python publish_cf.py --check      # 只检查登录态与配置，不上传

环境变量：
  CF_PROJECT_NAME   Pages 项目名（默认 temu-dashboard）
  CF_ACCOUNT_ID     Cloudflare 账户 ID（多账户时必填）
  DASH_PASSWORD     访问密码（留空则不加密码保护）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
DASH = ROOT / "data" / "temu_dashboard.html"
SITE = ROOT / "data" / "_cf_site"
BUILD = ROOT / "scripts" / "bel_goods" / "build_dashboard.py"
CF_PROJECT = os.environ.get("CF_PROJECT_NAME", "temu-dashboard")
DOMAIN = "mgltemu.us.ci"

GREEN, RED, YEL, DIM = "\033[32m", "\033[31m", "\033[33m", "\033[2m"
RESET = "\033[0m"


def run(cmd, cwd=None, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd, **kw)


def _node_exe() -> str:
    """定位 node：优先 PATH，其次 DSH 捆绑 runtime。"""
    import shutil
    p = shutil.which("node")
    if p:
        return p
    cand = (Path.home() / ".dsh" / "dsh-runtimes" / "dsh-primary-runtime"
            / "dependencies" / "node" / "bin" / "node.exe")
    if cand.exists():
        return str(cand)
    raise RuntimeError("未找到 node，请先安装 Node.js 或检查 PATH")


def _npx_entry() -> Path | None:
    """找 npx-cli.js（npx 的真实入口）。npx 本身是 .cmd 包装，Python 不能直接 spawn。"""
    import shutil
    cands: list[Path] = []
    npx = shutil.which("npx")
    if npx and npx.lower().endswith(".cmd"):
        try:
            txt = Path(npx).read_text(encoding="utf-8", errors="replace")
            import re
            m = re.search(r'"([^"]*npx[^"]*\.js)"', txt)
            if m:
                cands.append(Path(m.group(1)))
        except OSError:
            pass
    cands += [
        Path(os.environ.get("APPDATA", "")) / "npm/node_modules/npm/bin/npx-cli.js",
        Path(r"C:\Program Files\nodejs\node_modules\npm\bin\npx-cli.js"),
        Path.home() / ".dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/node_modules/npm/bin/npx-cli.js",
    ]
    for c in cands:
        if c.exists():
            return c
    return None


def wrangler(args, timeout=900):
    r"""调 wrangler。npx 是 .cmd 包装，Python 直接 spawn 在 Windows 会报 FileNotFound，
    故统一走 node + npx-cli.js 入口。

    另外：计划任务里 CWD 可能是 System32（无写权限），
    wrangler 会尝试在 CWD 下建 .wrangler\cache 而失败 —— 故显式指定
    XDG/HOME 到用户目录，并指定可写的 cache 根。
    """
    env = dict(os.environ)
    env["CI"] = "true"
    env["npm_config_yes"] = "true"

    # 指向用户可写目录，避免落到 System32
    home = str(Path.home())
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = str(Path(home) / "AppData" / "Roaming" / "xdg.config")
    env["XDG_CACHE_HOME"] = str(Path(home) / ".wrangler-cache")
    env["XDG_DATA_HOME"] = str(Path(home) / ".wrangler-data")

    npx_js = _npx_entry()
    if not npx_js:
        raise RuntimeError("未找到 npx-cli.js")
    cmd = [_node_exe(), str(npx_js), "--yes", "wrangler@latest", *args]
    # cwd 用一个确定可写的目录
    return run(cmd, timeout=timeout, env=env, cwd=str(Path(home)))


def step(msg):
    print(f"{DIM}->{RESET} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{RESET} {msg}")


def bad(msg):
    print(f"{RED}[FAIL]{RESET} {msg}")


def check_login():
    p = wrangler(["whoami"])
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 0 and "Account" in out or "account" in out.lower():
        ok("Cloudflare 已登录")
        for line in out.splitlines():
            if "Account ID" in line or "Account Name" in line:
                print(f"   {line.strip()}")
        return True
    bad("未登录 Cloudflare")
    print("   请先运行：npx wrangler login（会弹浏览器授权）")
    return False


def add_password(html: str, password: str) -> str:
    """在 </head> 前插入轻量访问密码门禁（纯前端，仅挡 casual 浏览）。"""
    gate = """
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
  <h3>TEMU 运营看板</h3>
  <p>请输入访问密码</p>
  <input id="gp" type="password" placeholder="密码" autocomplete="current-password">
  <button id="gb">进入</button>
  <div class="err" id="ge"></div>
</div></div>
<script>
(function(){
  var KEY=__PW_KEY__;
  var want=__PW_VAL__;
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
    # 用 JSON.stringify 转义，密码里的引号/反斜杠/换行都不会破坏 JS
    import json as _json
    gate = gate.replace("__PW_KEY__", _json.dumps("temu_gate_ok"))
    gate = gate.replace("__PW_VAL__", _json.dumps(password))
    out = html.replace("</head>", gate + "</head>", 1)
    if "__PW_VAL__" in out or "__PW_KEY__" in out:
        raise RuntimeError("密码门禁注入失败：占位符未替换")
    return out


def prepare_site(password: str) -> Path:
    step("生成本地看板")
    p = run([sys.executable, str(BUILD)], timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"看板生成失败: {p.stderr[-300:]}")
    html = DASH.read_text(encoding="utf-8")

    if password:
        step("注入访问密码")
        html = add_password(html, password)

    if SITE.exists():
        for f in SITE.iterdir():
            f.unlink()
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    ok(f"站点目录就绪 {SITE}（{len(html.encode('utf-8'))/1024/1024:.2f} MB）")
    return SITE


def ensure_project() -> bool:
    """Pages 项目是否存在。**不负责创建** —— 创建是一次性动作，
    放到部署失败时按需提示，避免每次定时任务都去创建而撞文件锁。"""
    r = wrangler(["pages", "project", "list"], timeout=420)
    if r.returncode == 0:
        import re as _re
        plain = _re.sub(r"\x1b\[[0-9;]*m", "", r.stdout or "")
        if CF_PROJECT in plain:
            ok(f"Pages 项目已存在：{CF_PROJECT}")
            return True
    # list 失败/未命中时不阻断，直接尝试 deploy（deploy 对不存在的项目会明确报错）
    print(f"   {DIM}未能确认项目存在，继续尝试上传{RESET}")
    return True


def deploy():
    step(f"上传到 Cloudflare Pages：{CF_PROJECT}")
    # 注意：`pages deploy` 子命令不接受 --account-id（账户走全局 wrangler 配置），
    # 传了会报 "Unknown arguments: account-id"。
    args = ["pages", "deploy", str(SITE),
            "--project-name", CF_PROJECT,
            "--commit-message", "TEMU dashboard update",
            "--commit-dirty=true"]
    p = wrangler(args, timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        bad("上传失败")
        out_plain = __import__("re").sub(r"\x1b\[[0-9;]*m", "", out)
        if "does not exist" in out_plain:
            step("项目不存在，尝试创建")
            c = wrangler(["pages", "project", "create", CF_PROJECT,
                          "--production-branch", "main"], timeout=420)
            if c.returncode == 0:
                ok("项目已创建，重试上传")
                return deploy()          # 一次重试后不再递归
            print("   创建失败：" + "\n   ".join(
                ((c.stdout or "") + (c.stderr or "")).strip().splitlines()[-6:]))
            print(f"   请手动创建：Cloudflare 控制台 → Workers 和 Pages → Create → Pages")
        else:
            print("   " + "\n   ".join(out_plain.strip().splitlines()[-12:]))
        return None
    ok("上传成功")
    url = ""
    for line in out.splitlines():
        if "https://" in line and ("pages.dev" in line or "deployment" in line.lower()):
            import re
            m = re.search(r"https://[^\s]+", line)
            if m:
                url = m.group(0).rstrip(".")
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--url-only", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    print()
    if not check_login():
        return 2

    if a.url_only:
        print(f"   默认地址: https://{CF_PROJECT}.pages.dev")
        print(f"   自定义域: https://{DOMAIN}")
        return 0

    if a.check:
        print(f"   项目名  : {CF_PROJECT}")
        print(f"   自定义域: https://{DOMAIN}")
        return 0

    pw = os.environ.get("DASH_PASSWORD", "")
    if not pw:
        print()
        bad("未设置 DASH_PASSWORD —— 面板含店铺货号/库存/申报价，不加密码会裸奔在公网")
        print("   PowerShell 示例：")
        print(f'     $env:DASH_PASSWORD="你的密码"; $env:CF_ACCOUNT_ID="你的账户ID"; '
              f'python publish_cf.py --build')
        return 3

    site = prepare_site(pw)
    if not ensure_project():
        return 1
    url = deploy()
    print()
    if url:
        ok("访问地址")
        print(f"   {url}")
        print(f"   https://{DOMAIN}   （需在 Pages→Custom domains 里绑定）")
        print(f"   访问密码: {pw}")
    print()
    print(f"后续每次更新：先跑 scripts\tasks\run_temu_limited_watch.cmd（会自动刷新看板），再跑本脚本 --build")
    return 0 if url else 1


if __name__ == "__main__":
    raise SystemExit(main())