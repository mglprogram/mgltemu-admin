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
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
ADMIN = ROOT / "temu-admin"

#: 发布互斥锁（见 main() 里的说明）。scripts/ 下的 panel_refresh.py 是锁的单一实现。
sys.path.insert(0, str(ROOT / "scripts"))
import panel_refresh  # noqa: E402
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


def dash_password() -> str:
    """取面板访问密码 `DASH_PASSWORD`（进程环境 → **用户级持久化变量**）。

    ⚠ 2026-10-29 事故（TEMU流量加速周更 周四 06:00 报 rc=6「发布失败」）：
      本函数以前只有 `os.environ.get("DASH_PASSWORD")`，**没有用户级回退**。
      而无人值守跑起来的环境，进程里根本没有这个变量：
        · DSH headless 会话（dsh_task.py）只透传自己那几个 env；
        · 计划任务 / wscript 隐藏窗口也不保证有用户变量。
      ⇒ `publish_admin` 直接判「未设置 DASH_PASSWORD」返回 3 ⇒ 发布失败。

      更阴的是**同一台机器上别的发布路径都有回退**，所以这个缺陷只在
      「直接调 publish_admin 的那条路」上暴露：
        · panel_refresh.py       —— powershell 读 User 环境  ✅
        · refresh_today_sales.py —— winreg 读 User 环境      ✅
        · run_panel_refresh.cmd  —— for /f + powershell      ✅
        · run_flux_weekly.py → 直接调 publish_admin.py       ❌ 本次踩到

      回退放在这里（所有发布路径的**唯一出口**）而不是逐个入口补：
      以后新增任何发布路径都自动带上，不会再出现"某条路忘了捞密码"。
    """
    pw = os.environ.get("DASH_PASSWORD", "")
    if pw:
        return pw
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, "DASH_PASSWORD")[0] or "")
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
  // 已输过密码：直接移除门禁并 **return**。
  // ⚠ 必须提前 return —— 原先这里 remove() 之后仍继续执行下面的
  //   document.getElementById('gb').onclick=... ，此时 #gb 已被移除，
  //   取到 null 会抛 TypeError（每个"回访用户"控制台都会报一次）。
  //   虽不致命（应用仍能挂载），但会污染控制台、掩盖真正的报错。
  try{ if(sessionStorage.getItem(KEY)==='1'){ document.getElementById('gate').remove(); return; } }catch(e){}
  var gb=document.getElementById('gb'), gp=document.getElementById('gp');
  if(!gb||!gp){ return; }   // 门禁 DOM 不在了就什么都别做
  gb.onclick=pass;
  gp.addEventListener('keydown',function(e){ if(e.key==='Enter') pass(); });
  gp.focus();
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

    # 销量-库存数据（TEMU 销量分析 + 海外仓看板 + 飞书领星；窗口没开/飞书读不到都不阻断发布）
    step("刷新销量-库存数据 public/data/presale.json")
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


def check_login(attempts: int = 3, wait: float = 20.0) -> bool:
    """确认 wrangler 能拿到账号信息。

    ★ 2026-10-15 实测：判定**必须重试**。原实现只跑一次 `wrangler whoami`，
    失败就报「未登录 Cloudflare / 请先运行 npx wrangler login」—— 而真实原因常常是
    **瞬时网络/代理抖动**（那次是冷启动后 09:01，代理刚起来；同样的调用单跑必成功，
    并发 3 个也全部成功）。这条假消息会把人带去重登 Cloudflare，方向完全错。

    所以：重试 `attempts` 次、每次间隔 `wait` 秒；全失败才判"登录态不可用"，
    并把话说清楚（是网络/代理问题，不是一定没登录）。
    """
    last = ""
    for i in range(1, max(1, attempts) + 1):
        p = wrangler(["whoami"], timeout=420)
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if p.returncode == 0 and "account" in out:
            ok("Cloudflare 已登录" + ("（第 %d 次尝试）" % i if i > 1 else ""))
            return True
        last = ((p.stdout or "") + (p.stderr or "")).strip().replace("\n", " ")[-160:]
        if i < attempts:
            print("   [warn] whoami 第 %d 次未通过（多为瞬时网络/代理抖动），%ds 后重试…"
                  % (i, int(wait)))
            time.sleep(wait)
    bad("Cloudflare 登录态不可用（已重试 %d 次）" % attempts)
    print("   常见原因：**网络/代理**（本机走 127.0.0.1:10808）不通，而不是真的没登录。")
    print("   先确认代理在跑，再决定是否需要 npx wrangler login。")
    if last:
        print("   最后一次输出：%s" % last)
    return False


def gate_present() -> bool:
    """dist/index.html 里有没有访问密码门禁。

    ⚠ 这道检查是 2026-10-28 踩坑后加的：`--data-only` 只是把 JSON 拷进 dist 再上传，
    它**不会**重新注入门禁。只要有人手工跑过一次 `pnpm build-only`（或 `pnpm build`），
    dist/index.html 就被重写成**没有门禁**的裸页面；此时任何一次 `--data-only` 发布
    都会把「店铺货号 / 库存 / 申报价」直接公开到线上 —— 而且整条链一声不响、rc=0。

    门禁标记取 publish_admin 自己注入的那串（`temu_admin_gate_ok`），
    用本地标记而不是去请求线上，避免把网络抖动当成"没门禁"。
    """
    try:
        html = INDEX.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "temu_admin_gate_ok" in html


def sync_data_to_dist() -> None:
    """把 public/data/*.json 覆盖到 dist/data/（给 `--data-only` 用：不重构建前端，只换数据）。

    为什么需要：一天要刷 3 次销量（08:15 / 14:00 / 18:45），每次都跑 `pnpm build`（约 4 分钟）
    纯属浪费 —— 前端产物没变，变的只有 JSON。
    """
    src = ADMIN / "public" / "data"
    dst = DIST / "data"
    if not (DIST / "index.html").exists():
        raise RuntimeError("dist/index.html 不存在 —— 先跑一次完整构建（--build）再用 --data-only")
    # ★ 门禁必须还在。不在就立刻中止：宁可不发布，也不能把没密码的面板推上线。
    if not gate_present():
        raise RuntimeError(
            "dist/index.html 里没有访问密码门禁（大概是有人手工跑过 pnpm build）——"
            "拒绝用 --data-only 发布裸面板。先跑一次 `publish_admin.py --build --production`"
            "重新注入门禁。"
        )
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("*.json")):
        shutil.copy2(f, dst / f.name)
        n += 1
    ok(f"数据已同步到 dist/data（{n} 个 JSON，不动前端产物）")


def deploy(production: bool) -> str:
    branch = "main" if production else PREVIEW_BRANCH
    step(f"上传到 Cloudflare Pages：{CF_PROJECT}（分支 {branch}）")
    args = ["pages", "deploy", str(DIST),
            "--project-name", CF_PROJECT,
            "--branch", branch,
            "--commit-message", "TEMU admin: 加库存建议页面",
            "--commit-dirty=true"]
    # 上传重试：Cloudflare API 会偶发超时（实测 2026-10-16 15:07
    # 「X [ERROR] The request to Cloudflare's API timed out.」），
    # 单次尝试时这一下就把整个定时任务判成失败（TEMU-OMS-Orders-Daily rc=1），
    # 但数据其实已经构建好了 —— 重试一次基本都能过。
    # 每次 deploy 会生成一个新部署记录，重试造出的多余部署无害。
    tries = 3
    for i in range(1, tries + 1):
        p = wrangler(args, timeout=1200)
        out = ((p.stdout or "") + (p.stderr or ""))
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        if p.returncode == 0:
            break
        if i < tries:
            print("   %s第 %d 次上传失败，%d 秒后重试…%s"
                  % (DIM, i, 45 * i, RESET))
            print("   " + "\n   ".join(plain.strip().splitlines()[-4:]))
            time.sleep(45 * i)
    if p.returncode != 0:
        bad("上传失败（已重试 %d 次）" % tries)
        print("   " + "\n   ".join(plain.strip().splitlines()[-12:]))
        return ""
    if i > 1:
        ok("上传成功（第 %d 次尝试）" % i)
    else:
        ok("上传成功")
    urls = re.findall(r"https://[^\s]+", plain)
    for u in urls:
        print("   " + u.rstrip("."))
    return urls[0].rstrip(".") if urls else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="构建并发布")
    ap.add_argument("--build-only", action="store_true", help="只本地构建")
    ap.add_argument("--skip-data", action="store_true",
                    help="跳过 build_data()（复用 public/data 里已有的 JSON）；"
                         "改前端/部署时用，避免白跑一遍爬取")
    ap.add_argument("--data-only", action="store_true",
                    help="只把 public/data 里最新的 JSON 同步到 dist 再发布（不爬取、不重构建前端）；"
                         "给「一天多刷几次销量」这种场景用")
    ap.add_argument("--production", action="store_true", help="发布到生产分支（覆盖线上站点）")
    ap.add_argument("--url-only", action="store_true")
    ap.add_argument("--no-lock", action="store_true",
                    help="跳过发布互斥锁（**只在排查时用**；正常路径都必须持锁，见下方说明）")
    a = ap.parse_args()

    # ★ 发布互斥（2026-10-15 实测事故后加）：发布有多条路径 —— 各任务收尾的 notify_panel、
    #   定时的 run_panel_refresh、run_goods_daily 自己那次、refresh_today_sales 的 --data-only。
    #   只要有人绕过锁，就可能并发部署到同一个 Pages 项目、互相覆盖。当天冷启动后早高峰
    #   6 个任务同时跑，3 个发布进程重叠 ⇒ 两个 wrangler 上传失败
    #   （`X [ERROR] Failed to upload files`）、另一个 `wrangler whoami` 抖动被误判成
    #   "未登录 Cloudflare"（那次 exit 2 就是假的）。
    #   锁放在 publish_admin 这个**唯一出口**上，任何调用路径都绕不过去。
    #
    # ⚠ 例外：panel_refresh.py 的 publish_loop 自己持锁后再 spawn 本脚本，
    #   所以它用 PANEL_PUBLISH_LOCK_HELD=1 告诉子进程"锁已经在父进程手里"，
    #   否则子进程会去抢自己父进程的锁、必然失败（发布全废）。
    parent_holds = os.environ.get("PANEL_PUBLISH_LOCK_HELD") == "1"
    if a.url_only or a.build_only or a.no_lock or parent_holds:
        return _main_body(a)
    try:
        with panel_refresh.publish_lock("publish_admin"):
            rc = _main_body(a)
        # ★ 发布成功就把"待刷新标记"清掉。
        #   为什么需要（2026-10-15 自查发现）：标记只有 panel_refresh.publish_loop() 会取走，
        #   而直接调 publish_admin 的路径（run_goods_daily / refresh_today_sales）不经过它。
        #   于是标记会一直挂着 ⇒ 后续 request() 看到 has_pending() 为真、又抢不到锁时
        #   会走"合并进正在跑的那一轮"分支并把请求**悄悄丢掉**（其实根本没有人在发）。
        if rc == 0:
            try:
                panel_refresh.take_pending()
            except Exception:                            # noqa: BLE001
                pass
        return rc
    except panel_refresh.PublishBusy as e:
        # ★ 6 = 发布被跳过（已有发布在跑）。**不是故障**：正在跑的那一轮会重建同样的
        #   数据（含 goods.json 等），所以本次的数据照样会上线。用 6 而不用 3，
        #   是因为 3 已被别的脚本用作真实失败（watch_focus/flux 的"部分/全部抓取失败"）。
        bad("已有发布在跑，本次并入那一轮（非故障）：%s" % str(e)[:140])
        return 6


def _main_body(a) -> int:

    print()
    if a.url_only:
        print(f"   项目    : {CF_PROJECT}")
        print(f"   生产    : https://{CF_PROJECT}.pages.dev")
        print(f"   预览分支: https://{PREVIEW_BRANCH}.{CF_PROJECT}.pages.dev")
        return 0

    if not (a.build or a.build_only or a.data_only):
        print("用法：publish_admin.py --build --production")
        print("      （或 --build-only / --data-only；见 argparse 的 --help）")
        return 0

    # 进程环境 → 用户级持久化变量（见 dash_password 的事故注释）。
    # 取到就回灌进 os.environ：后面注入门禁 / spawn 子进程都要用。
    pw = dash_password()
    if not pw:
        bad("未设置 DASH_PASSWORD —— 面板含店铺货号/库存/申报价，不许裸奔")
        print('   PowerShell: $env:DASH_PASSWORD="你的密码"; python publish_admin.py --build')
        print("   （无人值守场景请存成**用户级**环境变量："
              "[Environment]::SetEnvironmentVariable('DASH_PASSWORD','…','User')）")
        return 3
    os.environ["DASH_PASSWORD"] = pw

    try:
        if a.data_only:
            step("--data-only：不跑爬取、不重构建前端，只换数据")
            sync_data_to_dist()
        elif a.skip_data:
            step("--skip-data：跳过静态数据层刷新（沿用 public/data 现有 JSON）")
            # 订单分布地图（order-geo.json）由 scripts/pw_orders/run_pw_order_geo.py 单独构建：
            # 它是「发货列表 + 逐单收件地址」的重活（3 万单 ≈ 20 分钟），不进每日发布链路。
            geo = ADMIN / "public" / "data" / "order-geo.json"
            if geo.exists():
                print(f"   order-geo.json 已存在（{geo.stat().st_size / 1024:.1f} KB，"
                      f"{datetime.fromtimestamp(geo.stat().st_mtime):%Y-%m-%d %H:%M}）")
            else:
                print("   [warn] 缺 order-geo.json —— 「订单分布」页与美国地图会显示空数据")
        else:
            build_data()
        if a.data_only:
            pass          # dist 是上次构建的产物，门禁已在 index.html 里，不重复注入
        else:
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