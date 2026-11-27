# -*- coding: utf-8 -*-
"""run_temu_auth.py — TEMU 商家中心登录/授权参数化入口（按 temu-login-auth SKILL 固化）。

用法:
    python scripts/power/run_temu_auth.py --store BHY            # 按店查窗口+探测端口 → 自动授权
    python scripts/power/run_temu_auth.py --store BHY --dry-run   # 只打印端口，不授权
    python scripts/power/run_temu_auth.py --list-stores           # 列 TEMU 店（后缀/窗口/storeId）

流程（由 lib/auth_temu_driver.run_auth 完成）:
    A. 认证页(agentseller-us.temu.com/auth/authentication, 区域选择) → 点可用区域「商家中心」
       (div.authentication_goto；若弹「确认切换吗」先点确认切换) → 打开授权确认框。
    B. 授权确认框(seller.kuajingmaihuo.com/settle/seller-login；只认这个，勿误抓 .com/login 扫码登录页)
       → 勾选授权条款 checkbox(input 尺寸0, 点可见外层 LABEL/DIV)
       → 点授权/登录确认（BEL=「确认授权并前往」；BHY/KinglamanSport=「授权登录」，候选列表）
    C. 验证进入 orders.html(host+path 判，避开 redirectUrl 里的 orders.html 误导；TargetClosedError 属预期)。

工程控制:
    - 起点闸门: CDP 端口须可连(现场探测/传入), 失败 HARD_STOP(42)
    - 端口不写死: --store 从 ziniao_power_map.json 查窗口+进程探测, 或 --port
    - 数据外置: 店后缀走参数; AI 只给 --store <店后缀>
    - 日志: _logs/temu-sales/<日期>.jsonl（TEMU-auth 记录）
"""
import sys, os, io, json, time, argparse, subprocess
from datetime import datetime

_enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
if _enc != "utf8":
    # 只在控制台不是 UTF-8 时才接管（cmd 默认 GBK）。
    # 2026-11-16：无条件包会把 pytest 的捕获流换掉（测试里 ValueError），
    # 而且 `-X utf8` / PYTHONIOENCODING=utf-8 起的环境本来就不需要包。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# 迁移包自包含: 附带 scripts/lib(共享闸门库) 时优先用包内 lib, 免设 METHOD_SCRIPTS_ROOT
_INC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_INC_ROOT, "lib")):
    sys.path.insert(0, _INC_ROOT)
sys.path.insert(0, os.environ.get("METHOD_SCRIPTS_ROOT") or os.path.join(os.path.expanduser("~"), ".agents", "skills", "ai-python-methodology", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from lib import preflight  # noqa: E402
from lib import quiet      # noqa: E402  # 子进程静默起（不弹 CMD 黑框）

# stdout 在此只包装一次。auth_temu_driver 顶层不再包装 stdout（避免嵌套/closed file），
# 其 print 依赖本入口已包装好的 stdout；独立跑 driver 时由它自己 __main__ 兜底。

WS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ziniao_power_map.json")
LOG_DIR = os.path.join(WS, "_logs", "temu-sales")


def _load_store_map():
    with open(MAP, encoding="utf-8") as f:
        return json.load(f)


#: 自动授权失败里**只有人工能解**的几种情况（验证码 / 扫码 / 账号风控）
_HUMAN_HINTS = ("需人工", "验证码", "授权未完成", "没推进", "goto 失败",
                "登录态失效", "seller-login")

#: ★ 2026-11-27 修：这几类失败**自动授权本来就能解**，不该报「需要人工登录」
#:   （真机 2026-11-27 KLS：告警说「自动授权没成功（多半是验证码/扫码）」，
#:    实际窗口一开、跑一次 `run_temu_auth.py` 就成功了，用户直接质疑
#:    「你不会自己拿授权登录的脚本去做兜底？」）
_AUTO_SOLVABLE_HINTS = ("未探测到", "窗口未开", "窗口关了", "CDP 端口",
                        "内存紧张", "探针", "超时")


def _why(store: str, detail: str) -> str:
    """把失败详情翻成「真正该谁去解」。

    ★ 2026-11-27 用户质疑后加的：以前一律写「自动授权没成功（多半是验证码/扫码）」，
      但绝大多数失败是**进程/窗口层面**的（没探测到端口、窗口被关、内存紧张），
      这些跑一次 `run_temu_auth.py` 就能好，让人去点页面纯属白跑一趟。
    """
    low = str(detail or "")
    if any(h in low for h in _HUMAN_HINTS):
        return ("自动授权试过了，卡在「%s」—— 这步需要人工：打开紫鸟窗口，"
                "点「商家中心」→ 勾条款 → 确认授权。" % (low[:60] or "未知"))
    if any(h in low for h in _AUTO_SOLVABLE_HINTS):
        return ("其实是进程/窗口层面的问题（`%s`），**不需要人工登录**。"
                "处理：重开窗口 `ziniao-cli store open --id <storeId>` → "
                "重探端口 → `python scripts/power/run_temu_auth.py --store %s`。"
                % (low[:60] or "未知", store))
    return ("自动授权没能完成（`%s`）。先重跑一次 "
            "`python scripts/power/run_temu_auth.py --store %s`；"
            "仍失败再人工确认。" % (low[:60] or "未知", store))


def _alert_human(store: str, detail: str) -> None:
    """自动授权失败且需要人 → 推一条明确的「去紫鸟窗口登录」，**每店每天只推一次**。

    为什么要有（2026-11-14~15 事故）：BEL 登录态失效后，自动授权卡在
    「授权确认按钮点了没推进」，之后十几个任务轮流 rc=42 跑了一整个周末，
    没有任何一条消息说「BEL 需要人登录」——用户周一才发现。
    去重是必须的：每个任务开跑前都会调一次授权兜底，不去重就是刷屏。
    """
    low = str(detail or "")
    if not any(h in low for h in _HUMAN_HINTS):
        return
    day = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(LOG_DIR, exist_ok=True)
    marker = os.path.join(LOG_DIR, "auth_alert_%s_%s.txt" % (store, day))
    if os.path.exists(marker):
        print("[告警] %s 今天已推过一次人工登录提醒，跳过" % store)
        return
    title = "TEMU %s 需要人工登录" % store
    msg = ("%s 的 TEMU 登录态失效。\n"
           "受影响：所有跑 %s 窗口的任务（订单/逾期/预售/在售/异常单…）都会 rc=42。\n"
           "%s\n"
           "详情：%s" % (store, store, _why(store, low), low[:300]))
    try:
        _scripts = os.path.join(WS, "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from notify_push import push_group, push_meow  # noqa: E402
        ok1, _m1 = push_group(title, msg)
        ok2, _m2 = push_meow(title, msg)
        print("[告警] 飞书=%s 手机=%s" % (ok1, ok2))
    except Exception as e:  # noqa: BLE001 - 推送失败不能影响授权返回码
        print("[告警] 推送失败：%s" % str(e)[:120])
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat(timespec="seconds") + "\n" + low[:400])
    except Exception:  # noqa: BLE001
        pass


def _store_by_suffix(suffix: str):
    for m in _load_store_map().get("mapping", []):
        if m.get("export_suffix") == suffix or m["power_name"] == suffix:
            return m
    return None


def _find_store_port(window_name: str) -> int:
    """按紫鸟窗口名（进程 MainWindowTitle）**前缀**探测 CDP 端口。

    为什么要前缀匹配（2026-09-16 实测）：授权后 TEMU 页面会改窗口标题
    （`TM-BHY-YJF    |    卖家中心`），`-eq` 精确匹配找不到窗口 → 误报「未探测到 CDP 端口」。

    ★ 2026-11-09 真机事故（BHY/BEL 整链全落空）：紫鸟窗口的调试端口是**随机分发**的，
      不一定按 PID 升序落（BEL 53980 / BHY 62154 就是这样）。而 `probe_port_by_window`
      取的是「遍历 netstat 第一行命中的端口」⇒ 本文件与 `ziniao_store` 可以返回**不同端口**，
      不同脚本各连各的窗口，整链炸在「窗口里没有本批订单」上。
      ⇒ 这里**复用** `ziniao_store.probe_port_by_window`（唯一实现），不再各写一份。
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from lib import ziniao_store  # noqa: PLC0415
        p = ziniao_store.probe_port_by_window(window_name)
        if p:
            return int(p)
    except Exception:  # noqa: BLE001
        pass
    try:
        # netstat＝**叶子进程**（不再派生）→ 无控制台的 `quiet.run` 即可，不弹窗。
        out = quiet.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except Exception:
        return 0
    try:
        # powershell＝**会再派生**的进程 → 用 `hidden_run`（隐藏控制台，后代继承不弹窗）。
        ps = quiet.hidden_run(["powershell", "-NoProfile", "-Command",
                               "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } | Select-Object -ExpandProperty Id" % window_name],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout or ""
    except Exception:
        return 0
    pids = {int(x) for x in ps.split() if x.strip().isdigit()}
    if not pids:
        return 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                if int(parts[4]) in pids:
                    port = int(parts[1].rsplit(":", 1)[1])
                    if 10000 < port < 70000:
                        import urllib.request
                        try:
                            v = json.loads(urllib.request.urlopen(
                                "http://127.0.0.1:%s/json/version" % port, timeout=2).read())
                            if v.get("webSocketDebuggerUrl"):
                                return port
                        except Exception:
                            continue
            except Exception:
                continue
    return 0


#: 店铺视图判据：钉死了「必须是半托管那个」的店。
#:
#: ★ 2026-11-19 用户口径：「KLS 店铺执行 TEMU 定时任务，必须切换到 KinglamanSport local
#:   这个才是半托管店铺；为什么总是失败在授权登录」。
#:   实情：KLS 在 TEMU 侧有**两个视图**（全托管 `KinglamanSport` / 半托管 `KinglamanSport local`），
#:   窗口会话停在全托管时 `/mmsos/` 订单页进不去（导航被弹回卖家中心首页），
#:   下游只会报「日期选择器未生效 / 停在认证页 / 授权未完成」——**授权其实没坏，是视图错了**。
#:   动态判别（读顶栏有没有 `local`）对 BHY/BEL 同样适用（它们只有一个视图，读到了就算对），
#:   所以这里不是写死店名，而是"有多个视图的店必须落在半托管那个"。
_HALF_MANAGED_HINT = ("local", "半托管")

#: 视图锁定文件：`_logs/temu-sales/store_view_<店>.json`，记录**上一次确认正确**的视图名。
#: 有了它，任务开跑前就能判断"窗口现在是不是又被人切走了"，不用等下游报一堆假错。
_VIEW_LOCK_DIR = LOG_DIR


def _view_lock_path(store: str) -> str:
    return os.path.join(_VIEW_LOCK_DIR, "store_view_%s.json" % store)


def _read_view_lock(store: str) -> dict:
    try:
        with open(_view_lock_path(store), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_view_lock(store: str, banner: str) -> None:
    try:
        os.makedirs(_VIEW_LOCK_DIR, exist_ok=True)
        with open(_view_lock_path(store), "w", encoding="utf-8") as f:
            json.dump({"store": store, "banner": banner,
                       "at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _view_probe_page(port: int):
    """开一张**自己的**后台页（`/goods/list`，两家域都有）读视图名，读完立刻关。

    为什么用商品列表页：`agentseller.temu.com/goods/list` 与
    `agentseller-us.temu.com/goods/list` 都会渲染顶栏店铺视图，
    而且不像订单页那样在视图不对时被踢回首页 —— 拿来读视图最稳。
    为什么要自己开：用户的页面一律不碰（同 `temu-login-auth` 的既有约定）。
    """
    import cdp_transport  # noqa: PLC0415  # power/lib 已在 sys.path

    tid = ""
    try:
        tid = cdp_transport.create_target(port, "https://agentseller.temu.com/goods/list",
                                          background=True)
    except Exception as e:  # noqa: BLE001
        print("[视图] 建探针页失败（%s）" % str(e)[:80])
    if not tid:
        return ""
    banner = ""
    try:
        t0 = time.time()
        while time.time() - t0 < 30:
            time.sleep(2)
            hit = [t for t in cdp_transport.list_pages(port)
                   if (t.get("targetId") or t.get("id")) == tid]
            if not hit:
                continue
            pg = cdp_transport.CdpPage(port, hit[0])
            try:
                banner = _atd().read_store_banner(pg)
            except Exception:  # noqa: BLE001
                banner = ""
            if banner:
                break
    finally:
        try:
            cdp_transport.close_target(port, tid)
        except Exception:  # noqa: BLE001
            pass
    return banner


def _atd():
    import auth_temu_driver  # noqa: PLC0415
    return auth_temu_driver


def _choose_view_in_dropdown(port: int, want: str) -> bool:
    """在**顶栏切店下拉**里点那个 `want` 视图（`want` 含 `local` ⇒ 挑半托管那项）。

    ★ 与 `auth_temu_driver._dismiss_switch_dialog` 的区别（别把两件事搞混）：
      · 认证页的「当前已有登录中的店铺，确认切换吗」→ 点它会**踢掉当前会话**，默认必须关掉；
      · 这里的顶栏「切换店铺」下拉 → 这是 **TEMU 官方支持的视角切换**，用户明确要求切到
        `KinglamanSport local`，所以这里**允许**点。两者位置、含义都不同。
    """
    import cdp_transport  # noqa: PLC0415
    want_low = want.lower()
    try:
        tid = cdp_transport.create_target(port, "https://agentseller.temu.com/goods/list",
                                          background=True)
    except Exception:  # noqa: BLE001
        return False
    if not tid:
        return False
    JS_CLICK = """(wantLow) => {
      const t = (e) => (e.innerText || '').replace(/\\s+/g, ' ').trim();
      const items = Array.from(document.querySelectorAll(
        '[class*=storeItem],[class*=store-item],[class*=shopItem],[class*=mallItem],li,div'))
        .filter(e => e.offsetParent !== null && t(e).length > 0 && t(e).length < 80);
      const hit = items.find(e => {
        const s = t(e).toLowerCase();
        if (!s.includes(wantLow)) return false;
        return !items.some(o => o !== e && o.contains(e) && t(o).toLowerCase().includes(wantLow));
      });
      if (!hit) return '';
      hit.click();
      return t(hit);
    }"""
    try:
        t0 = time.time()
        pg = None
        while time.time() - t0 < 30:
            time.sleep(2)
            hit = [t for t in cdp_transport.list_pages(port)
                   if (t.get("targetId") or t.get("id")) == tid]
            if hit:
                pg = cdp_transport.CdpPage(port, hit[0])
                break
        if pg is None:
            return False
        # 打开切店面板：顶栏店铺名那颗
        for sel in ("[class*=storeName]", "[class*=store-name]", "[class*=mallName]",
                    "[class*=shopName]"):
            try:
                loc = pg.locator(sel)
                if loc.count():
                    loc.first.click(timeout=4000)
                    pg.wait_for_timeout(1500)
                    break
            except Exception:  # noqa: BLE001
                continue
        got = pg.evaluate(JS_CLICK, want_low) or ""
        pg.wait_for_timeout(3000)
        if got:
            print("[视图] 已在下拉里点「%s」" % got)
        return bool(got)
    except Exception as e:  # noqa: BLE001
        print("[视图] 下拉切换异常（%s）" % str(e)[:100])
        return False
    finally:
        try:
            cdp_transport.close_target(port, tid)
        except Exception:  # noqa: BLE001
            pass


def check_view(store: str, port: int, want_half_managed: bool = True) -> tuple:
    """确认窗口停在对的店铺视图上；不对就**尝试**切回来。返回 `(ok, banner)`。

    对 BHY / BEL 这类只有一个视图的店，只要求"读到了视图名"（它们本来就是半托管）。

    ★ 为什么要单独做这一步（2026-11-19 用户报 KLS 反复失败）：授权链本身是好的
      （当天 08:41 已记录 `OK: 已登录，无需授权`），坏的是**店铺视图**被切到全托管，
      于是下游所有 `mmsos` 页被弹回首页，任务把锅记在「授权登录」头上。
    """
    banner = _view_probe_page(port)
    if not banner:
        print("[视图] 读不到店铺视图（不阻断授权，交给下游报真因）")
        return True, ""
    prev = _read_view_lock(store).get("banner") or ""
    ok = _atd().banner_is_half_managed(banner) if want_half_managed else True
    if ok:
        if prev and prev != banner:
            print("[视图] %s 视图=%s（上次 %s）" % (store, banner, prev))
        else:
            print("[视图] %s 视图=%s ✓" % (store, banner))
        _write_view_lock(store, banner)
        _log_run(store, "view", "视图=%s" % banner, verdict="ok")
        return True, banner
    print("[视图] ⚠ %s 当前视图=%s，不是半托管那个 → 尝试切到含 %s 的视图"
          % (store, banner, "/".join(_HALF_MANAGED_HINT)))
    _log_run(store, "view", "视图错了：%s（期望半托管 local）" % banner, verdict="fail")
    for want in ("local", "半托管"):
        if _choose_view_in_dropdown(port, want):
            time.sleep(4)
            again = _view_probe_page(port)
            if again and _atd().banner_is_half_managed(again):
                print("[视图] 已切到 %s ✓（锁定）" % again)
                _write_view_lock(store, again)
                _log_run(store, "view", "已切到 %s" % again, verdict="ok")
                return True, again
            print("[视图] 点了「%s」但复查=%s，继续试" % (want, again or "读不到"))
    return False, banner


def _log_run(shop, stage, result, verdict="ok"):
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, ts[:10] + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "agent": "TEMU-auth", "shop": shop, "stage": stage,
                            "result": str(result)[:400], "verdict": verdict}, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 商家中心登录/授权参数化入口")
    ap.add_argument("--port", type=int, default=None, help="紫鸟窗口 CDP 端口(现场探测/传入)")
    ap.add_argument("--store", default=None, help="店后缀(如 BHY; 从 ziniao_power_map.json 查窗口并探测端口)")
    ap.add_argument("--dry-run", action="store_true", help="只打印端口/目标, 不操作浏览器")
    ap.add_argument("--list-stores", action="store_true", help="列出 TEMU 店(后缀/窗口/storeId)")
    ap.add_argument("--domain", default="us", choices=["us", "cc", "both"],
                    help="要过哪一套授权：us=美国站 agentseller-us.temu.com（默认）；"
                         "cc=合作仓 agentseller.temu.com；"
                         "**both=两套都过**（2026-11-09 加：窗口被关过重开后，两套都失效，"
                         "只补 us 会让 cc 域那几张页永远停在授权页 —— 用户看到的就是"
                         "「所有页面都要重新登录」）。两套是独立授权，别混。")
    ap.add_argument("--check-view", action="store_true",
                    help="只检查/纠正店铺视图（KLS 必须是 KinglamanSport local），不做授权")
    ap.add_argument("--allow-any-view", action="store_true",
                    help="不做店铺视图检查（排查时用；默认视图不对就切回半托管）")
    args = ap.parse_args()

    if args.list_stores:
        for m in _load_store_map().get("mapping", []):
            if m.get("power_platform") == "TEMU" or m.get("power_type") == "本土店":
                sfx = m.get("export_suffix") or m["power_name"]
                print("%-10s window=%-20s storeId=%s" % (sfx, m["ziniao_window_name"], m["ziniao_window_store_id"]))
        return 0

    port = args.port
    store = args.store or "?"
    if port is None and store != "?":
        m = _store_by_suffix(store)
        if m is None:
            msg = "未知店后缀 %s（可用 --list-stores 查）" % store
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE
        port = _find_store_port(m["ziniao_window_name"])
        if not port:
            msg = "未探测到窗口 %s 的 CDP 端口（先开该店窗口/紫鸟客户端已启动）" % m["ziniao_window_name"]
            print("[HARD STOP] " + msg, file=sys.stderr)
            return preflight.HARD_STOP_CODE

    if args.dry_run:
        drymsg = "[OK] dry-run: auth store=%s port=%s（按 temu-login-auth 完成 TEMU 登录/授权）" % (store, port)
        print(drymsg)
        _log_run(store, "auth_dry", "port=%s" % port)
        return 0

    if not port:
        msg = "缺 CDP 端口(--port 或 --store <店后缀>)"
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE
    try:
        import urllib.request
        json.loads(urllib.request.urlopen("http://127.0.0.1:%s/json/version" % port, timeout=4).read())
    except Exception as e:
        msg = "CDP 端口 %s 不可连: %s" % (port, str(e)[:80])
        print("[HARD STOP] " + msg, file=sys.stderr)
        return preflight.HARD_STOP_CODE

    import auth_temu_driver as atd  # noqa: E402

    # ---- 清障步（用户 2026-09-24：「跑脚本前先清卡死页」）----
    # 授权是本域**唯一**还用 Playwright browser-level attach 的一环（`auth_temu_driver`），
    # 也是被残留空页拖死的那一环；买面单/合作仓链路都会经它（子进程调用）⇒ 这里先清一次。
    # ⚠ 2026-09-24 事故：清障曾把窗口里**唯一一个真实标签页**（卖家中心登录页）当垃圾关掉 ⇒
    #   整个窗口消失 ⇒ 接着去连死端口（WinError 1225）。现在清障内部保证"最后一个真实标签页不关"，
    #   并在收尾自检端口是否还活；这里再做一次兜底：窗口没了就**重开一次**，绝不连死端口。
    try:
        import ziniao_store as _zs  # noqa: E402
        _cl = _zs.clean_dead_tabs(port=port, log=lambda x: print(x))
        _log_run(store, "clean_tabs",
                 "真实标签页 %s；关掉 %d 保留 %d；端口存活=%s"
                 % (_cl.get("real_tabs"), len(_cl.get("closed") or []),
                    len(_cl.get("kept") or []), _cl.get("window_alive")))
        if _cl.get("window_alive") is False:
            print("[清障] ⚠ 清障后窗口没了 → 重开窗口一次再连（不连死端口）")
            if store != "?":
                _m = _store_by_suffix(store) or {}
                # ★ 2026-10-29：进程还在就别开新窗（开窗会杀掉用户打开的页面）；只重探端口。
                if _zs.window_running(_m.get("ziniao_window_name") or ""):
                    print("[清障] 窗口进程还在（只是端口不答）→ 不开新窗，直接重探")
                else:
                    _zs.open_store(_m.get("ziniao_window_store_id"))
                _t0 = time.time()
                while time.time() - _t0 < 90:
                    time.sleep(3)
                    _p = _find_store_port((_m or {}).get("ziniao_window_name", ""))
                    if _p:
                        port = _p
                        print("[清障] 重开后新端口=%s" % port)
                        break
    except Exception as e:  # noqa: BLE001
        print("[清障] 跳过（%s）" % str(e)[:80])

    # ★ 2026-11-05：授权全程留登录态证据。`run_auth` 里会点「商家中心」并可能撞上
    #   「当前已有登录中的店铺，确认切换吗」弹窗 —— 这正是"跑完任务所有页面都要重新登录"
    #   的头号嫌疑。现在前后各记一次 cookie 指纹（seller_temp / mallid / sessionType），
    #   掉了就当场写进日志并指明是哪一段丢的，不再靠事后回忆。
    #   ⚠ 只包 import：`run_auth` 自己的异常必须原样抛出去（吞了会重跑一遍授权）。
    _ls = None
    try:
        import login_state as _ls  # noqa: E402
    except Exception:  # noqa: BLE001 - 留证模块缺失不能影响授权本身
        _ls = None

    # ★ 2026-11-09：`--domain both` —— 两套授权**逐套过**，任一失败即整体失败。
    #   为什么需要（用户当天报「BEL 所有页面登录态都失效」的真实成因）：
    #   11:14 自愈动作把 BEL 窗口 close→open 了一次（CDP 端口假死），窗口重开
    #   必然丢会话；而事后只补了 `us`（订单页那条线），`cc`（商品列表/销量分析/
    #   生命周期）**一直没人补** ⇒ 那三张页永远停在 auth/authentication，
    #   用户看到的就是"所有页面都要重新登录"。
    #   两套是独立授权（见 `run_auth` 文档），必须显式逐套过，不能指望一套带另一套。
    # ★ 2026-11-19 用户令：KLS（多视图店）跑任务前**必须**确认在 `KinglamanSport local`
    #   （半托管）视图。视图错了下游全是"假错"（订单导出/预售/在售都报「未生效/停在认证页」），
    #   而日志会把锅记在"授权登录"上 —— 这就是「为什么总是失败在授权登录」的真因。
    view_ok, view_banner = True, ""
    if not args.allow_any_view:
        view_ok, view_banner = check_view(store, port)
        if args.check_view:
            print("[%s] 视图=%s（%s）"
                  % ("OK" if view_ok else "FAIL", view_banner or "读不到",
                     "半托管" if view_ok else "不是半托管，需人工在顶栏切到 %s local" % store))
            return 0 if view_ok else 42
    elif args.check_view:
        print("[跳过] --allow-any-view")
        return 0

    domains = ["us", "cc"] if args.domain == "both" else [args.domain or "us"]
    results = []
    for dom in domains:
        if _ls is not None:
            with _ls.watch(port, "run_temu_auth(%s/%s)" % (store, dom),
                           log=lambda m: print(m)):
                r = atd.run_auth(port, store, domain=dom)
        else:
            r = atd.run_auth(port, store, domain=dom)
        results.append((dom, r))
        _log_run(store, "auth", r, verdict=("ok" if r.startswith("OK") else "fail"))
        print("[%s] %s: %s" % ("OK" if r.startswith("OK") else "FAIL", dom, r))
        if not r.startswith("OK"):
            # 一套失败就停：继续过下一套只会把窗口搅得更乱
            break
    result = results[-1][1] if results else "FAIL: 没跑任何域"
    verdict = "ok" if (len(results) == len(domains)
                       and all(r.startswith("OK") for _, r in results)) else "fail"
    # 视图不对但授权过了：**照样算失败**。理由：下游真正干活的是 mmsos 页，
    # 视图不在这条链一定白跑；报成成功只会让下一个任务以同样的假错再挂一次。
    if verdict == "ok" and not view_ok:
        verdict = "fail"
        result = "FAIL: 授权已过，但店铺视图不在半托管（当前=%s，需切到 %s local）" % (
            view_banner or "读不到", store)
    # 授权成功后顺手再清一次：把本次流程可能留下的**空白授权页**收掉（治本的一半——
    # 残留就是这么一页页攒起来的；不清就等下次开窗被紫鸟还原回来继续拖慢 attach）。
    # ★ 2026-11-09：清障**可能把窗口关没**（实测把仅剩的真实页也关掉后窗口消失，
    #   下游接着连死端口）。这里清完必须复检端口，掉了就当场重开一次，别把锅甩给下一个任务。
    if verdict == "ok":
        try:
            import ziniao_store as _zs  # noqa: E402
            _cl = _zs.clean_dead_tabs(port=port, log=lambda x: print(x))
            if _cl.get("closed"):
                _log_run(store, "clean_tabs_after_auth", "关掉空页 %d" % len(_cl["closed"]))
            if _cl.get("window_alive") is False:
                print("[清障] ⚠ 授权后清障把窗口关没了 → 重开一次（否则下一个任务连死端口）")
                _m = _store_by_suffix(store) or {}
                try:
                    _zs.open_store(_m.get("ziniao_window_store_id"))
                except Exception as e:  # noqa: BLE001
                    print("[清障] 重开失败（%s）" % str(e)[:80])
        except Exception as e:  # noqa: BLE001
            print("[清障] 授权后清理跳过（%s）" % str(e)[:80])
    print("[%s] %s" % ("OK" if verdict == "ok" else "FAIL",
                       result if len(results) == 1 else
                       "；".join("%s=%s" % (d, r[:40]) for d, r in results)))
    if verdict != "ok":
        # 需要人工（验证码/扫码）时当天只推一次，别让十几个任务各推一遍
        _alert_human(store, "；".join(r for _d, r in results))
    return 0 if verdict == "ok" else 42


if __name__ == "__main__":
    raise SystemExit(main())
