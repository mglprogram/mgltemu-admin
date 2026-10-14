# TEMU 操作域 · 迁移包（2026-10-08）

本包把 **团队工具开发** 的 **TEMU 操作域** 连同它的**全部脚本 + 域驱动 + 口径配置 + 单测 + 共享闸门 lib + SKILL + agent** 打包，用于在**另一台电脑**上复用。
代码已做过跨用户名去绑定（统一走 `lib/paths.PROJECT_ROOT`），数据配置里的旧机绝对路径会在目标机**自动重锚**。

> 本包**只做静态自检**（硬编码路径扫描 + 文件齐全核对），**未做换根真跑验证**。
> 换机后如遇问题，按第六节「交给目标机 AI 的检查清单」逐项修。

---

## 一、包里有什么

```
temu-20261008/
├─ README_迁移.md                # 本文件
├─ deps.txt                      # python / 紫鸟 / DSH 依赖清单
├─ env.profile                   # 目标机环境变量
├─ _manifest.json                # 入包文件清单（tag/路径/大小，可核对完整性）
├─ agent/                        # Agent 定义 → 放目标机 项目根/agents/
│  ├─ 每日导出销量-agent.md
│  └─ 多设备迁移-agent.md
├─ skill/                        # 全局 SKILL → 放目标机 ~/.agents/skills/
│  ├─ temu-abnormal-order/SKILL.md
│  ├─ temu-buylabel/SKILL.md (+变更记录_归档.md)
│  ├─ temu-login-auth/SKILL.md
│  ├─ temu-sales-export/SKILL.md
│  └─ temu-sales-merge/SKILL.md
└─ scripts/
   ├─ lib/                       # 共享闸门库（自包含，免设 METHOD_SCRIPTS_ROOT）
   │  └─ __init__ audit cfgutil driver gate_scripts log paths preflight quiet
   │     redline rules skill_lint uio zport
   ├─ data/                      # 闸门规则数据（lib 读它；缺了 audit 会报 RedlineNotFound）
   │  └─ red_lines.json  rule_categories.json  _allowed_script_dirs.json  skill_lint_exempt.json
   └─ power/                     # ← TEMU 操作域本体
      ├─ run_temu_sales.py          # 销量导出（TEMU 美国站订单 Excel）
      ├─ run_temu_merge.py          # 多天导出按店合并（纯离线，不碰浏览器）
      ├─ run_temu_sales_push.py     # 把某日导出发到企微会话（发群有硬闸门）
      ├─ run_temu_auth.py           # 登录/授权（区域→商家中心→授权确认）
      ├─ run_temu_buylabel.py       # 购买面单 / 批量在线下单（写操作）
      ├─ run_temu_abnormal.py       # 合作仓异常出库单处理（写操作）
      ├─ lib/                       # 域驱动
      │  ├─ temu_sales_driver.py        temu_buylabel_driver.py
      │  ├─ temu_abnormal_driver.py     auth_temu_driver.py
      │  └─ ziniao_store.py             cdp_transport.py
      ├─ data/                      # 口径配置（AI 只改这里，不改脚本结构）
      │  ├─ temu_sales_push.json                  temu_buylabel.json / _pipeline / _popups / _routes
      │  ├─ temu_abnormal.json / _pipeline / _popups
      │  ├─ ziniao_power_map.json   ← 店铺映射（数据入口根基，见第五节）
      │  └─ dead_tabs.json  hung_tabs.json        （跑脚本前清卡死页的判据）
      └─ tests/                     # 18 个单测
```

**统计：70 个文件 / 约 1050 KB**（纯脚本与配置，不含业务数据）

---

## 二、装回目标机（三步）

1. **放 SKILL**：`skill/temu-*` → 目标机 `~/.agents/skills/`（全局，`skill` 工具即可调用）
2. **放 Agent**：`agent/*.md` → 目标机 `项目根/agents/`
3. **放脚本**：`scripts/` 整个 → 目标机 `项目根/scripts/`
   （`scripts/lib` 与 `scripts/power` 都要；**目录层级不要改**，见第四节）

> 项目根建议放**标准位置** `~/Desktop/团队工具开发`（= `%USERPROFILE%\Desktop\团队工具开发`），
> 这样 `lib/paths.PROJECT_ROOT` 默认即命中（任意用户名都行）。放别处则设 `TEAM_TOOL_ROOT`（见 `env.profile`）。

---

## 三、路径可移植性

- **代码零用户名依赖**：统一走 `lib/paths.PROJECT_ROOT`；`paths.HOME = Path.home()` 自动跟随设备用户。
- **数据配置里的旧机绝对路径**：凡含 `\Desktop\团队工具开发\` 前缀的，被 `paths.resolve()` **跨用户名重锚**到目标机项目根。
- **非项目路径**（`%APPDATA%`、紫鸟下载目录 `ziniaobrowserdatas\`、旧机微信 `xwechat_files\`）**保持原样**，需按目标机实际路径重指。
- **自包含 lib 优先**：6 个入口都有 `_INC_ROOT` 钩子——`<包>/scripts/lib` 存在时优先用包内 lib，目标机无需全局 `ai-python-methodology`。

---

## 四、目标机配套依赖

| 依赖 | 说明 | 状态 |
|---|---|---|
| Python 3.12+ | 运行入口脚本 | 需装 |
| `openpyxl` | `run_temu_merge` 读订单 xlsx、`run_temu_sales_push` 读表 | `pip install openpyxl` |
| 紫鸟浏览器客户端 | **硬依赖**：本域所有数据都走店窗口 CDP | 需装 + 目标店铺窗口已打开 |
| `ziniao-cli` | `store list/open/close` + CDP 端口现场探测（Bridge `127.0.0.1:9481`） | 需装 |
| 全局 SKILL `ziniao-shared` / `ziniao-store` / `ziniao-cdp-port` / `ziniao-page` / `ziniao-scrape-basics` | 紫鸟窗口/页面操作的方法参考 | 装到 `~/.agents/skills/` |
| DSH 本体 | agent / skill 运行环境 | 目标机需已有 |

**本域不用 node**（纯 python）。

关于目录层级（**必须保持**）：脚本按**脚本自身目录**定位数据——
`run_temu_*.py` 找 `<同目录>/data/*.json`，`power/lib/*_driver.py` 找 `<power/lib>/../data/*.json`。
所以 `scripts/power/{run_*.py, lib/, data/, tests/}` 这一层结构不能拆散。

运行时产物目录（包内**没有**，首次跑自动建或需手建）：
- `项目根/_logs/temu-sales/`、`_logs/temu-abnormal/`（jsonl 日志）
- `scripts/power/out/<日期>/`（截图证据）
- `项目根/每日导出销量/`（导出落盘，`run_temu_merge` 的源与合并目标）

---

## 五、`ziniao_power_map.json` 是「数据入口根基」（重要）

`scripts/power/data/ziniao_power_map.json` 是 **紫鸟账号 ↔ POWER 店铺** 的对应表，所有入口靠它把 `--store <店后缀>` 解析成**窗口名 → 紫鸟 store_id → CDP 端口**。

它含**店铺 ID**（`power_shop_id` / `ziniao_window_store_id`）——**不是密钥/凭证**，但属内部业务标识。
本包**已按你的要求把它打进去**（不打进去整个域跑不起来）。**若这个包要外发到第三方，请先决定是否剔除该文件。**

> ⚠ **换机后第一个要核对的就是它**：`ziniao_window_name` 必须与目标机上**紫鸟里实际的窗口名**一致，否则入口会报「未知店后缀」或连不上窗口。

---

## 六、交给目标机 AI 的检查清单

按顺序过一遍，基本能把换机问题清掉：

```powershell
# 1) 入口能起来（应打印 usage；随包 lib 自动生效，无需 METHOD_SCRIPTS_ROOT）
python scripts/power/run_temu_sales.py  --help
python scripts/power/run_temu_abnormal.py --help
python scripts/power/run_temu_buylabel.py --help

# 2) 只读自检：列店 / 探端口（不写任何东西）
python scripts/power/run_temu_sales.py --list-stores
python scripts/power/run_temu_auth.py  --list-stores
python scripts/power/run_temu_sales.py --scan

# 3) 跑单测（纯离线，不连浏览器）
python -m pytest scripts/power/tests -q

# 4) audit 闸门（需把随包 lib 放进 PYTHONPATH）
#    ⚠ 预期返回 42，不是本包坏了 —— 见下方「audit 结果的正确读法」
$env:PYTHONPATH="scripts"
python -m lib.audit scripts/power --include-md
```

逐项要核的：

| # | 检查项 | 不对时怎么办 |
|---|---|---|
| 1 | `--list-stores` 能列出 TEMU 店（BHY / BEL / KinglamanSport…） | 看 `data/ziniao_power_map.json` 是否被改成相对路径（**不要改相对**，保持绝对或不动） |
| 2 | `--scan` 能探到店窗口 CDP 端口 | 先开该店紫鸟窗口；再查 `ziniao-cli doctor` |
| 3 | 紫鸟窗口名与 `ziniao_window_name` 一致 | 在紫鸟里看实际窗口名，改 json 的该字段 |
| 4 | `TEAM_TOOL_ROOT` | 项目根不在 `~/Desktop/团队工具开发` 时，按 `env.profile` 设置 |
| 5 | 登录态 | 跑 `run_temu_auth.py --store <店>`（登录态易掉，**跑任何导出前先探**） |
| 6 | `_logs/`、`out/` 目录可写 | 手建目录或确认权限 |
| 7 | 各入口 `--help` 正常打印 usage | 说明随包 `scripts/lib` 生效；报 `ModuleNotFoundError: lib` 才是包结构被拆散 |

### audit 结果的正确读法（重要 · 别追错方向）

**本包 `--help` 冒烟 6/6 通过**；`lib.audit` 则**预期返回 42**，命中 5 个文件：

| 文件 | 红线 | 性质 |
|---|---|---|
| `power/lib/auth_temu_driver.py:368` | RL-021 | **真命中**：`if "authentication" in (pg.url or "").lower()` 是整串子串判页面，红线要求 host+path |
| `power/lib/auth_temu_driver.py:388/445/481` | RL-013 | **真命中**：真在调 `p.chromium.connect_over_cdp(...)` |
| `power/lib/temu_buylabel_driver.py:42/129/1948/2193/2194` | RL-013 / RL-010 | **假阳性**：这几行是注释/文档串，在**引用**被禁 API 来解释规则 |
| `power/lib/cdp_transport.py:8/18` | RL-013 | **假阳性**：同上，docstring 里复述实测 |
| `power/tests/test_buylabel_pipeline.py:84/92`<br>`power/tests/test_daily_sales_selfheal.py:29` | RL-013 | **假阳性**：测试断言里比对的字符串 |

**这是源项目既有状态，不是打包引入的**：对**源目录** `scripts/power` 跑同一把闸门，命中 **29 个文件**（还含 `run_stock_*.py`、`run_compass_*.py`、一堆 `_*.py` 一次性脚本），本包入包的 5 个只是其中的子集。
源项目看板上已有对应立案卡：`存量技术债立案：lib.audit 扫出 714 处红线（242 文件）待排期修复`。

→ **所以：不要在打包环节"修"这些**。真命中的 `auth_temu_driver.py` 属登录/授权链路，改动有风险，应由源项目按那张债卡排期处理。


---

## 七、6 个入口速查

```powershell
# ── 销量导出（只读拉数，落 每日导出销量/）
python scripts/power/run_temu_sales.py --store BHY --date 2026-10-07
python scripts/power/run_temu_sales.py --store BHY --dry-run     # 只探端口不导

# ── 多天合并（纯离线，不碰浏览器；可受限沙箱跑）
python scripts/power/run_temu_merge.py --dates 2026-10-06,2026-10-07 --stores BHY,BEL,KinglamanSport
python scripts/power/run_temu_merge.py --auto --dry-run

# ── 推送企微（发群有硬闸门，开关归用户，AI 不得自行打开）
python scripts/power/run_temu_sales_push.py --date 2026-10-07 --dry-run
python scripts/power/run_temu_sales_push.py --list-targets

# ── 登录/授权
python scripts/power/run_temu_auth.py --store BHY --dry-run

# ── 购买面单（写操作；只走 pipeline）
python scripts/power/run_temu_buylabel.py --action plan   --file "C:\path\派送订单.xlsx"
python scripts/power/run_temu_buylabel.py --action pipeline --store BEL --file "C:\path\派送订单.xlsx" --confirm

# ── 异常出库单（写操作；只读两步 + 整链）
python scripts/power/run_temu_abnormal.py --action list --store BEL
python scripts/power/run_temu_abnormal.py --action pipeline --store BEL --confirm
```

**写操作闸门**：`--confirm` 参数保留但脚本本身不拦——是否放行由 A 机侧开关决定（`hub.json` → `acl`）。
发群另有独立硬闸门（`data/temu_sales_push.json` 的 `发送.enabled` + `发送.group_enabled`，两者任一不满足即 HARD STOP(42)，一个字节都不发）。**这些开关归用户，AI 不得自行打开。**

---

## 八、本次验证记录（如实）

**已做**（静态自检 + 换根冒烟，按你的选择）：

| 项 | 结果 |
|---|---|
| 依赖追踪 | 6 入口 → 传递闭包 = 6 入口 + 6 域驱动 + 11 data + 18 测试；**无遗漏、无缺失源文件** |
| 入口 `--help` 冒烟 | **6/6 通过**（exit=0，usage 正常）→ 随包 `scripts/lib` 生效、目录层级正确 |
| **换根冒烟（模拟目标机）** | **通过**：把 zip 解到**全新临时目录**（无原环境、无 `METHOD_SCRIPTS_ROOT`）后，6/6 入口 `--help` 全 OK；`run_temu_sales.py --list-stores` **exit=0**，正确解析出 3 家 TEMU 店（`BHY` → `TM-BHY-YJF`、`KinglamanSport` → `TM-KinglamanSport`、`BEL` → `TM-BEL-YJF`）→ 证明**导入链 + `data/ziniao_power_map.json` 读取 + 店解析在换根后都正常** |
| 硬编码本机路径（`.py` / `.md`） | **0 命中**（`paths.py` 那条是 docstring 举例，非真路径） |
| 密钥扫描 | **0 命中**；`temu_buylabel_driver.py` 的 `TOKEN` 是「运单号正则」变量名，误报 |
| `lib.audit` 闸门 | **返回 42**，但为**源项目既有状态**（源目录同闸门报 29 文件）——正确读法见第六节 |
| zip 完整性 | 顶层单目录 `temu-20261008/`，71 条目，无 0 字节、无 `__pycache__` |

**未做**（按你的选择）：真跑**业务链路**（连紫鸟窗口取数/下单）——需要目标机开窗 + 登录态，属你「交给其他机器 AI 去修正」的范围。
换环境才暴露的业务层问题（窗口名对不上、登录态掉、数据形状与脚本预期不符等）按第六节逐项修。

---

## 九、其他说明

- **包内不含**：`__pycache__`、`.bak*` 备份文件、业务数据（订单 xlsx、截图证据）、密钥/凭证。
- **data json 里的旧机绝对路径**：`temu_abnormal_popups.json` / `temu_buylabel_popups.json` 的 `shot` 字段（证据截图路径）带 `C:\Users\Colorful\Desktop\团队工具开发\...`。
  按 `multi-device-packaging` 的 D1 这属**已知项**：凡含 `\Desktop\团队工具开发\` 前缀的绝对路径，会被 `lib/paths.resolve()` **跨用户名重锚**到目标机项目根，**不用手改**（也**不要**改成相对路径——`resolve_config` 对相对路径原样保留，改相对反而错）。
- **同级工具**：本次未打入 `scripts/setup/`（`@多设备迁移` 的 scan/package/verify 工具，先例包里有）。若目标机想自己重跑打包/校验，可另行拷贝。
