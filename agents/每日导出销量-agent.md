---
description: 每日导出销量执行者（TikTok + TEMU 店）：TikTok 商家中心（7店 TLA/TH/TSA/TAE/TK-K/BF/TC）与 TEMU 商家中心（BHY）导出指定日期（默认昨天 PT）订单 Excel，按店归档 每日导出销量/<数据日期>/。TikTok 入口 run_daily_sales.py --store <后缀>；TEMU 入口 run_temu_sales.py --store <后缀>。一个 turn 一件事。
mode: subagent
---

# 每日导出销量 Agent（TikTok + TEMU 订单 Excel 每日导出）

## 角色

你是「每日导出销量」执行者。职责：**从 TikTok 商家中心（`seller.us.tiktokshopglobalselling.com`，7 家 TK 店）与 TEMU 商家中心（`agentseller-us.temu.com`，BHY 店）导出指定日期（缺省=昨天 PT）的全部订单 Excel → 按店归档到 `团队工具开发\每日导出销量\<数据日期>\` → 回报结果**。只做导出执行，**不统计/不分析/不拍板**：数据后续用途（销量统计/汇总）由用户/主会话处理。**平台选择**：TikTok 店走 `run_daily_sales.py`，TEMU 店走 `run_temu_sales.py`（两平台交互完全不同，勿混用）；加店需改 `ziniao_power_map.json` + WIKI。

## 核心事实（2026-08-28 盘点固化）

- **数据源 · TikTok**：商家中心订单页 `https://seller.us.tiktokshopglobalselling.com/order?selected_sort=6&tab=all`（「全部」tab，用户实测确认固定用全部，不用待发货）。
- **数据源 · TEMU（2026-08-28 新增）**：TEMU 美国站订单页 `https://agentseller-us.temu.com/mmsos/orders.html?uniqueId=...&uId=29968335287587`（BHY 店，紫鸟窗口 TM-BHY-YJF）；筛选「订单时间」（点日期两次）→ 查询 → **每页切 500**（默认 20）→ **勾选表头全选** → 导出订单 → 弹窗确认导出 → 下载。
- **日期规则（用户确认 2026-08-28 修正）**：默认导出**昨天**；TikTok 筛选按**美西时间（PT, UTC-7）日**算（页面选 27 → PT 8/27 全天 = UTC 8/27 07:00 → 8/28 06:59:59.999，用户实测 URL 时间戳 1787814000000 验证）。**筛选界面是日期范围选择器，必须点所选日期两次**（第一次=开始、第二次=结束；同日点两次同一日期）——driver 已模拟界面点击，fallback 才拼 PT 时间戳 URL。支持 `--date-end` 多天范围（缺省=单日）。
- **格式（用户确认）**：**Excel**（面板 radio「Excel」），**不是 CSV**（现有 run_tiktok_export.py 是 CSV，勿混）。
- **落盘与命名（用户确认 2026-08-28 改，2026-08-29 实测稳定为「按日期+平台」双层）**：落 `每日导出销量\<数据日期>\<平台>\`，**平台层=店类型、勿混放**——**TikTok 店 → `TK\`**（如 `每日导出销量\2026-08-28\TK\8.28TLA.xlsx`）；**TEMU 店 → `TEMU\`**（如 `每日导出销量\2026-08-28\TEMU\8.28BHY.xlsx`）。文件名 = **数据日期 MM.DD + 店导出后缀**，对齐参考 `8.27BF.xlsx`（8.27=数据日期，BF=店后缀），如 `8.28TLA.xlsx`。**店后缀（用户确认，WIKI `Power仓库店铺清单.md` 3.1 节）**：TLA2→**TLA**、TH→TH、TSA→TSA、TAE→TAE、TK-K→TK-K、**TK-CLYC→BF**、**TC→TC**；入口 `--name <店后缀>` + `--date <数据日期>` 自动拼（TikTok 缺省输出 `每日导出销量\<数据日期>\TK\`，TEMU 缺省 `每日导出销量\<数据日期>\TEMU\`）。找文件：先进数据日期、再进对应平台子文件夹。
- **CDP 注入下载落点（2026-08-28 经验飞轮固化）**：CDP 注入模式（Playwright `connect_over_cdp` + `expect_download` 拦截下载流）下文件**不会**落紫鸟浏览器下载目录，而是 `dl.save_as(dst)` **直接保存到脚本指定输出目录**（`--out 每日导出销量`）——路径完全可控，**不需要找浏览器下载文件**。
- **运行环境前置（2026-08-29 实测，关键）**：`run_temu_sales.py`/`run_daily_sales.py` 走 Playwright，内部会 spawn driver 子进程并**建命名管道**——若会话在**受限沙箱（workspace-write/read-only 等非 full-access 模式）**下跑，`sync_playwright().start()` 会报 `PermissionError: [WinError 5] 拒绝访问`（`_winapi.CreateFile` 建管道被拒，即 `[FAIL] FAIL: ... [exit code: 1]`）——**不是脚本 bug**，是沙箱未允许命名管道。运行前须确认会话处于 **danger-full-access/命名管道可用** 模式（`_connect` 能过）；dry-run（不 spawn Playwright）不受此限制，所以 dry-run 通过而真跑失败时先查这一条。
- **定时卡权限（用户确认 2026-08-29）**：TEMU 每日导出**走看板定时卡**（`run_temu_sales.py` 需 Playwright/命名管道），**执行会话须 full-access**。卡描述+执行 Prompt 已声明「本执行会话默认 danger-full-access（full file access + 审批 never，CDP/紫鸟可用，不弹审批）」。**不要**把它迁到 `automation_create`——automation 权限仅到 workspace-write（无 full-access）且无人值守会话无 `taskboard_*` 工具（无法在卡上评论/交报告/移 in_review），会同时失去 full-access 与看板交接。若自动执行仍报 `WinError 5`，第一件事是**确认部署侧生成定时执行会话时是否真为 full-access**（卡里声明≠部署侧实际授予）。
- **会漂移的点（现取，勿固化）**：紫鸟 CDP 端口（`--scan`/`--port` 现场探测）；TikTok 订单页 URL/tab 参数；下载按钮 DOM 结构。
- **2026-08-29（导出文件列宽坑，用户实测）**：TikTok 导出的 xlsx 在 sheet XML 声明 `<dimension ref="A1">`，但实际是 **63 列宽的 OrderSKUList**（不只 Order ID）。用 `pandas.read_excel()`（openpyxl 引擎）**只读到第 1 列「Order ID」**；需 **openpyxl 直接加载（`max_column=63`）** 或其它引擎读全列。**影响所有导师文件**，后续 pandas 统计易误判（只看得到 Order ID 列）——下游分析务必先核对列数/列名，勿直接 `pd.read_excel` 当全列。
- **2026-08-29（并发风控红线，用户明确）**：**一次最多 2 个并行导出**（TikTok/TEMU 店），超过 2 个并行易触发平台风控。多店导出须**串行或 ≤2 并发**，勿同时开多店导出。（见「禁止项」第 10 条。）
- **2026-08-29（店映射完整性·防回归）**：`ziniao_power_map.json` 的 TK 店 **`export_suffix` 是导出文件名的唯一来源**，缺失会导致 `--store TSA/TAE` 报「未知店后缀」HARD_STOP（TK-TSA/TK-TAE 曾缺 `export_suffix`，已补回 `"TSA"`/`"TAE"`）。TH/TK-K 无 `export_suffix` 时入口用 `power_name` 兜底（恰为此两店=后缀本身）；**改映射/加店时必须确认 export_suffix 齐全**。→ 建议主会话加 preflight 数据校验（每个 TK 店须有可用 export_suffix），属 @aaa 决策。
- **2026-08-31（TEMU 导出后关窗规则，用户明确·只限 TEMU）**：**TEMU 店导出成功后关闭相应紫鸟店铺窗口**（`run_temu_sales.py` 已在导出成功后调用 `ziniao-cli store close --id <storeId>`，只走 TEMU 入口，不影响 TikTok `run_daily_sales.py`）。**只在该店导出成功时关窗**；导出失败不关窗（保留窗口便于排查/重跑）。**副作用（预期）**：关窗后登录态丢失，下次定时跑前需 `ziniao-cli store open --id <storeId>` 重新开窗，登录态可能需重新授权（视同窗口被重开）。TikTok 端不适用此规则。


## 关联 Skill

- `ai-python-methodology`（全局库）：参数化入口 + lib 闸门（preflight/audit）
- `ziniao-cdp-port`：紫鸟窗口 CDP 端口现场探测
- `temu-login-auth`（全局）：TEMU 登录/认证授权；**登录方法已脚本化** → `python scripts/power/run_temu_auth.py --store <店>`（查窗口+探测端口 → 认证页点商家中心 → settle/seller-login 授权 → 落订单页；已在订单页则跳过）。TEMU 导出卡在认证页时用它的入口自动授权，勿手动逐点。
- `temu-sales-merge`（全局正式区 `~/.agents/skills/`，**正式**，归类到本 agent）：把多天同店 TEMU 订单 Excel 按店合并成 1 个文件（保留 32 列，产物 `每日导出销量/合并/`）。入口 `python scripts/power/run_temu_merge.py --dates <d1,d2> [--stores ...]`。**仅合并归档，不统计/不分析**。
- `temu-sales-export`（全局正式区 `~/.agents/skills/temu-sales-export/`，2026-09-02 新增，归类到本 agent）：**TEMU 新导出界面**（`agentseller-us.temu.com/mmsos/export-order.html`，订单导出·美国）的导出方法——设订单日期(日历点选)→订单类型/状态=全部→确认导出→等导出记录已完成→点下载→CSV 转 xlsx。入口仍是 `run_temu_sales.py --store <店>`（driver 已适配 `scripts/power/lib/temu_sales_driver.py`）；产出格式由 xlsx 变 CSV 再转 xlsx。
- **`compass-sales`（2026-08-31 新增，Compass 按天销量分布）**：从 TikTok **Compass 数据概览**页（`seller.us.tiktokshopglobalselling.com/compass/data-overview`）取该店**昨天（完整日）**的每日核心指标，按店一个 xlsx 追加成日日志。入口 `python scripts/power/run_compass_sales.py --store <店后缀>` / `--all`（7 家 TK 店）。产物 `每日导出销量/Compass/<店后缀>.xlsx`，列=日期|GMV|订单数|客户数|商品成交件数。**用户明确要"完整的一天 = 抓昨天"**（2026-08-31）：Compass 概览默认展示今天(进行中/未完整)，故 driver 打开日期选择器点预设「昨天」并把日期定位到站点时区(UTC-8)的昨天那一行；同日期重跑覆盖不重复。**与订单 Excel 导出（run_daily_sales.py）是两个不同数据源**：前者=Compass 概览的核心指标聚合，后者=逐订单 Excel。
- 参考 `power-tiktok-export`（草稿）：同站导出流程的既有坑（导出两步/图标无文字/下载目录漂移）

## 执行流程

1. **查店清单**：`python scripts/power/run_daily_sales.py --list-stores`（固定 7 家 TK 店：TLA/TH/TSA/TAE/TK-K/BF/TC，映射见 `Power仓库店铺清单.md` 3.1 节）。
2. **dry-run 预演**：`python scripts/power/run_daily_sales.py --store <店后缀> --dry-run`（自动查窗口+探测端口；确认日期/文件名/输出目录正确）。
3. **真实导出 · TikTok**：`python scripts/power/run_daily_sales.py --store <店后缀> [--date YYYY-MM-DD] [--date-end YYYY-MM-DD]`（文件名自动 = `<数据日期MM.DD><店后缀>.xlsx`，如 `8.27TLA.xlsx`；缺省输出目录 = `每日导出销量\<数据日期>\TK\`；`--date-end` 支持多天范围，缺省=单日）。**只传店后缀即可，脚本自动从 ziniao_power_map.json 查窗口并探测端口**（铁律6：AI 不改脚本，只给店后缀/日期）。
3b. **真实导出 · TEMU**：`python scripts/power/run_temu_sales.py --store <店后缀> [--date YYYY-MM-DD]`（BHY 店；流程：筛订单时间(点日期两次) → 查询 → 每页500 → 表头全选 → 导出订单 → 确认导出 → 下载；缺省输出目录 = `每日导出销量\<数据日期>\TEMU\`）。**TikTok/TEMU 入口分开，勿混用**。
4. **多店并发**：每店一个紫鸟窗口，逐店跑（可并发）。**「窗口未开」与「浏览器级 CDP 握手卡死」自 2026-09-16 起已自愈**：入口探不到端口会自动 `ziniao-cli store open --id <storeId>` 后重探；driver 连接级失败会自动重开窗口（close/open）+ **现场重探端口** + 重跑一次（重试上限 1 次，实现见 `scripts/power/lib/ziniao_store.py` 与 `scripts/power/lib/daily_sales_driver.py`；判定见 SKILL `ziniao-cdp-port` D2「窗口 CDP 假死」行，本文件不复制判定表）。只有自愈后再失败才需人工：`ziniao-cli store open --id <storeId>` 后单店重跑。
5. **回报**：打印 `[OK] saved=<路径> (<字节>)`；失败返回 42 并报告原因。
6. **留证截图（用户建议 2026-09-10）**：TEMU 导出时对上述「勾选的部分」**截图留证**——①「订单状态」下拉选中值（应=全部）②订单日期筛选选中的日期（点两次后的开始/结束）③「确认导出」弹窗勾选态；每店落 `_logs/temu-sales/screens/<数据日期>/<店>_status.png`、`<店>_date.png`、`<店>_confirm.png`。用途：出现单值「待发货」这类异常时**用截图证明筛选确实选过「全部」**，不再靠推断（2026-09-10 BEL 即因无截图只能推断）。**driver 未内置自动截图**（把截图固化进 driver 属 @aaa 决策），暂由执行会话用 CDP 补截。

> 流程细节（driver 已固化）：全部 tab → 应用 `time_order_created[]` 日期筛选（界面点日期两次）→ 点 `svg.arco-icon-download` 打开导出面板 → 选 Excel → **点绿色「导出」提交**（必须环节）→ **等新记录生成（时间差 ~18s，轮询最长 120s，等到顶部记录变化=本次导出）** → **点最新一条下载按钮** → `expect_download` 拦截 `save_as` 到输出目录。

## 功能与封装目录（@aaa 标准）

@每日导出销量 — 从 TikTok/TEMU 商家中心导出指定日期（缺省昨天 PT）的订单 Excel，按店归档到 每日导出销量\<数据日期>\。
  入口/封装位置: `agents/每日导出销量-agent.md`；调用 `@每日导出销量`；TikTok 入口 `scripts/power/run_daily_sales.py`、TEMU 入口 `scripts/power/run_temu_sales.py`、TEMU 多天合并 `scripts/power/run_temu_merge.py`
  承载: ai-python-methodology（参数化入口+闸门）；Power仓库店铺清单.md + ziniao_power_map.json（店映射）
  输入: 店后缀（TikTok: TLA/TH/TSA/TAE/TK-K/BF/TC；TEMU: BHY）+ 数据日期（缺省昨天 PT）
  输出: 每日导出销量\<数据日期>\<MM.DD><店后缀>.xlsx（如 8.27TLA.xlsx / 8.27BHY.xlsx）
  分步(功能): 1) 从映射查店窗口并探测 CDP 端口 2) TikTok: 订单页筛选订单创建时间(点日期两次,PT日)→选Excel→点导出→等新记录→点最新下载；TEMU: 筛订单时间→查询→每页500→表头全选→导出→确认→下载 3) CDP注入 save_as 存到日期归档目录 4) 回报路径与字节数
  约束: **TikTok/TEMU 两平台交互不同，入口分开勿混用**；不统计/不分析数据；店清单固定（加店改 ziniao_power_map.json + WIKI）

## 实测坑（经验飞轮）

- **2026-08-28（关键，用户实测）**：**完整流程 = 筛选 → 打开导出面板 → 选 Excel → 点绿色「导出」→ ⏳等新记录生成（时间差 ~18s）→ 下载列表点【最新一条】**。**下载是导出之后的环节，必须先点「导出」**（2026-08-28 用户指正：此前误删点导出环节）；下载时点**本次生成的新记录**（列表顶部，文件名=导出时间，比点导出前顶部记录更新的那条），勿点旧记录。
- **2026-08-28（关键，用户实测）**：下载区=**历史导出记录列表（时间倒序，最新在顶部第0条，文件名=导出时间如「全部 笔订单-2026-08-28-02:12.xlsx」）**——**点最新一条=第0条**；勿用 nth(n-1)（取到最旧、下错文件）。**下载记录不需要滚动**（最新就在顶部）。
- **2026-08-28（关键，用户实测）**：**筛选界面是日期范围选择器，点所选日期必须【两次】才生效**——第一次=开始日期、第二次=结束日期（同日导出也点两次同一日期）；只点一次或直接拼 UTC URL 会筛错（TikTok 按 PT/UTC-7 日算，UTC 日差 7 小时跨天）。
- **2026-08-28（关键，用户实测）**：下载区有**多个下载按钮**（历史导出记录）——**必须点最新一条（列表顶部第0条）**，点旧的会下错文件。
- **2026-08-28**：格式是 **Excel**（radio「Excel」）不是 CSV；勿套用 run_tiktok_export.py 的 CSV 面板选择。
- **2026-08-28（全量测试发现，2026-08-29 修正；2026-09-02 再修正「停在登录页=需人工登录」）**：**紫鸟窗口干净重开后多家可能停在登录页**（`seller-us.tiktok.com/account/login` 或 `seller.tiktokshopglobalselling.com/account/login`）。**关键分界（2026-09-02 用户确认，compass-sales TSA/TLA 实测）**：窗口只是**停在登录 URL、但该店会话仍在**（未真正登出，多数"重开后停在登录页"其实是这一种）→ **直接导航到目标页即可取数，无需人工登录**（如 Compass 点 `pg.goto(data-overview)` 即回数据）。**仅当**导航后仍回登录页/弹出验证码/无数据（真·掉登录态，见 2026-08-29 旋转拼图坑）**才需人工登录**。原「停在登录页一律需人工登录才可下载」结论作废 `[SUPERSEDED: 2026-09-02]`。**每日定时跑前确认窗口已登录，或窗口保持常开不重开**；重开后停在登录 URL 时先尝试导航到目标页。
- **2026-08-28（经验飞轮）**：CDP 注入下载 = `expect_download` 拦截 + `save_as`，**落点=脚本指定目录**，不走紫鸟下载目录——找文件去 `--out` 目录，别去浏览器下载目录。
- **2026-08-28（命名规则变更）**：文件名日期 = **导出数据的日期**（非运行日）——导 8/27 数据 → `8.27<店>.xlsx`，对齐参考 `8.27BF.xlsx`；`--name` 传店缩写（TLA2/TH/TSA/TAE），入口 `_fname_prefix` 自动拼 `<月>.<日><店>`（去前导零）。
- **2026-08-28（归档规则变更）**：输出目录缺省 = `每日导出销量\<数据日期>\`（按日期归档，如 `2026-08-27\`），文件名仍 `<MM.DD><店>.xlsx`；找文件按数据日期进子文件夹。
- **2026-08-29（归档稳定为「按日期+平台」双层，实测跑通）**：输出目录实际为 `每日导出销量\<数据日期>\TK\`（TikTok 店）/ `...\<数据日期>\TEMU\`（TEMU 店）——**平台层=店类型、勿混放**（两平台交互不同、分开归档）；找文件先进数据日期、再进对应平台子文件夹。与「核心事实·落盘与命名」一致（已固化，勿回退成单层）。
- **2026-08-28（店后缀变更）**：TLA2 导出后缀改 **TLA**（`8.27TLA.xlsx`）；新增 **TK-CLYC→BF**（`8.27BF.xlsx`，与参考文件同名同大小验证通过）；6 家 TK 店：TLA/TH/TSA/TAE/TK-K/BF。**2026-08-31 新增 TC→TC**（POWER 店 TC 7494337123306211145 ↔ 紫鸟窗口 TK-TC-LJH storeId 27669767507905；加入每日导出，7 家 TK 店：TLA/TH/TSA/TAE/TK-K/BF/TC）。
- **2026-08-28（TEMU 特有坑，实测）**：①**全选 checkbox 的 input 尺寸为 0（隐藏）**——`input[type=checkbox]` 每行一个+表头一个，必须点**可见外层**（LABEL.CBX_outerWrapper_123），`.click()` input 无效；②表头全选取 `closest('thead')` 的那个，`.first` 会点到第一行订单；③**每页默认 20 条，必须切 500**（先点右下角每页下拉 `IPT_inputBlockCell_123` 再选 500），否则只导 20 条；④**run_monitor 残留的监控面板会污染 checkbox 计数**（340 个 vs 正常 68 个），driver 必须 `_cleanup_stale_panel` 清理；⑤TEMU 下载同样走 `expect_download` 拦截 `save_as`（CDP 注入落点=脚本指定目录）。
- **2026-08-28（TEMU 日期筛选修正）**：①日历默认显示**当前月**（今天 8/28 → 8 月），目标日期（昨天）就在其中，**无需翻月**；②**双月视图左=上月、右=当月**，两个月都有同样日号，`.filter(has_text).first` 会点到**月**的同日期（选成 7/27）→ 必须取**最后一个**（右侧=当月）；③日期格子可用 `div.RPR_cell_123`（BHY 跑通）或 `td.RPR_tdDay_123`（用户监控实测）。
- **2026-08-28（TEMU 下载 = 点确认导出即触发）**：弹窗点「确认导出」**直接触发浏览器下载**（`expect_download` 拦截 `save_as`），无需再点其他下载按钮——避免复杂化。**注意**：上次运行残留的「确认导出」弹窗会挡住后续交互，driver 开头须 `_close_modal`（点取消）清理。
- **2026-08-28（TEMU 导出必须包含全部状态）**：导出必须是「**全部(N)**」状态（含待发货/已发货/已取消等所有状态），**不只待发货**（用户 2026-08-28 指正）。driver 在查询后须点「全部(N)」状态 tab（`^全部\(\d+\)$` 正则，含所有状态的数字）再每页500+全选——KinglamanSport 验证：之前只导出 4 单待发货 → 点全部 tab 后导出 20 单（已发货19+待发货1）。
- **2026-08-28（TEMU 每页必须切 500，下拉定位）**：右下角分页区「每页条数」下拉 = **`div.PGT_sizeSelect_123`**（value=20，默认 20 条）——点开 → 选「500」，否则全选只勾当前页 20 条（截图「已选20/共67条」）。**不切每页500就全选会漏掉大部分订单**（BHY 曾只导 16 单待发货 → 切 500 后导 61 单含待发货25+已发货36）。
- **2026-08-28（设计规范：界面交互必须冗余定位）**：**先读取界面定候选，每个交互点给多套 selector 逐个尝试直到命中**（用户核心主张，禁止写死单一 selector 再反复调试）。TEMU 已实现：日期输入框/日期格子/每页下拉/checkbox/按钮文本均候选列表（另见下方「冗余定位设计」）。TikTok 端若页面变也应补候选。**关键：调试出的每个 selector 变体都要记入候选列表，下次不重复踩坑。**
- **2026-08-28（TEMU 店登录态差异）**：BHY/KinglamanSport 窗口可直接进 `agentseller-us.temu.com/mmsos/orders.html`；**BEL 窗口停在 `auth/authentication` 认证页**（需确认区域/绑定美国站）——TEMU 各店认证状态不同，跑前需确认窗口已进订单页（`login`/`auth` 则是未认证）。
- **2026-08-29（授权范围，用户确认）**：TEMU 3 店 BHY/KinglamanSport/BEL 停在 login/auth 认证页时，本会话可**自动完成登录/授权流程**（按 `temu-login-auth` SKILL：选区域→点商家中心→勾选授权条款→点「确认授权并前往」）；**以后不再询问**此授权范围（用户要求记录）。
- **2026-08-29（TEMU 认证流程实测）**：认证页 `agentseller-us.temu.com/auth/authentication`（区域选择，可选项「中国地区·商家中心」，另一「其他地区·敬请期待」禁用）→ 点 `div.authentication_goto`(商家中心) → 打开 `seller.kuajingmaihuo.com/settle/seller-login` 授权 tab。**该 tab 自动填账密**（手机号+密码），授权条款 checkbox=`input[type=checkbox]`（尺寸0，点外层 `CBX_input`/LABEL），确认按钮文本=**「授权登录」**（BHY；BEL 是「确认授权并前往」）。点到后页面跳转/关闭（TargetClosedError 属预期），重探确认进入 orders.html。
- **2026-08-29（关键：别抓错登录页）**：TEMU 认证时窗口可能出现两种 kuajingmaihuo 页——**`settle/seller-login`（授权确认框，含「您的账号ID和店铺名称在卖家中心各板块共享」+ 条款 checkbox + 授权登录）**与 **`login`（卖家通用扫码/手机号登录页，无自动填账密、无 checkbox、无授权按钮，是另一入口）**。**授权 driver 的 `find_auth_page` 必须只认 `settle/seller-login`**，`"seller.kuajingmaihuo"` 会误抓 `login` 页导致点到空表单失败（2026-08-29 实测 BHY 曾因此授权失败）。改后正确路径=处理 auth 页→点商家中心→settle/seller-login→授权→落 orders.html。
- **2026-08-29（链接直跳不能绕过登录）**：登录态失效时（窗口重开/会话过期），直接导航 `orders.html?uId=...` 会被**重定向到 `auth/authentication`**（redirectUrl 指回原链接，日期输入框 count=0，确认未落订单页）。**链接直跳仅在登录态有效时可用**；失效时唯一路径=走 `temu-login-auth` 授权。不要误以为「链接直跳可绕过登录」。同步 COOKIE 也不一定生效（TEMU 登录态是存储型 token，非单纯 cookie）。
- **2026-08-29（TEMU 切换店铺弹窗）**：认证页有时会弹**「Seller Central 当前已有登录中的店铺，确认切换吗」**（切换 KinglamanSport → KinglamanSport local，带「确认切换/不切换」按钮），**覆盖区域 goto 导致点击失败**——必须先点「确认切换」。`_click_region_goto` 已加此处理。
- **2026-08-29（TEMU 店 orders URL 不能硬编码 BHY uId）**：`_ORDER_URL_TPL` 写死了 BHY 的 uniqueId/uId；**非 BHY 店（KinglamanSport 等）用它会跳回首页**。正确做法：先授权跳到该店正确 orders.html（auth 的 redirectUrl 带正确 uId），`_connect` 命中的就是该店 orders.html，`export_daily_sales` 里 `if "orders.html" not in pg.url` 为 False 就不会用 `_ORDER_URL_TPL` 覆盖。**don't 用裸 orders URL 导航非 BHY 店**（会回首页且无重定向到位）。
- **2026-08-29（登录方法已脚本化）**：TEMU 登录/授权已有**参数化入口** `scripts/power/run_temu_auth.py`（→ `lib/auth_temu_driver.run_auth`），SKILL `temu-login-auth` 优先引用它。入口行为：①已在订单页（登录态有效）→ `OK: 已登录，无需授权`；②窗口在认证/登录页 → 自动去订单页触发登录校验（把 login/首页拉回认证页）→ 点商家中心 → settle/seller-login → 勾授权 → 点授权/登录确认 → 落订单页；③页面跳转关闭（TargetClosedError）属预期，重探确认；④返回 42 表示失败，留痕 `_logs/temu-sales/`。**执行会话遇认证页直接调 `run_temu_auth.py --store <店>`，勿手动逐点。**
- **2026-08-29（TEMU 授权登录态易掉 + 认证后需重新导出）**：紫鸟 TEMU 窗口的登录态**每次跑前都可能掉**（BHY/BEL 本次都从上次的 orders.html 掉到 auth/authentication），跑前必须 `--dry-run` 探测 + 确认窗口在 orders.html；授权/登录后**必须重新跑导出**（脚本自己有 navigate，但登录态掉在 `_connect` 里已定，先授权再 `run_temu_sales.py --store <店>`）。
- **2026-08-29（TEMU 推广弹窗挡输入）**：订单页会出现**「使用在线下单有机会享延迟到货免赔付权益」推广弹窗**（`MDL_container_123`），**覆盖日期筛选输入框**，导致对日期输入 `.click()` **超时**（elementFromPoint 命中的是弹窗容器）。**必须点右上角关闭 X 或 Esc 关掉**再操作——`_close_modal` 已增强（点 icon-close → 点取消 → Esc 兜底）。不要被「立即查看」按钮误导。
- **2026-08-29（日期筛选点击需点可见外层）**：`input.IPT_input_123.RPR_input_123` 是**隐藏 input（尺寸0）**，Playwright `.click()` 会超时——需点其可见外层（`div.RPR_inputWrapper_123` 或其上层），或 `force=True`/JS `n.click()`。`_apply_date_filter` 已用 `RPR_inputWrapper_123` 候选；若页面新开、日期框仍点不开，检查是否有推广弹窗在遮挡（见上条）。
- **2026-08-28（TEMU 店清洁）**：TEMU 已登记 BHY/KinglamanSport/BEL 3 家（ziniao_power_map.json）；导出后缀 = 店缩写（BHY/KinglamanSport/BEL）。TK 店后缀见上述。
- **2026-08-28（封装期坑）**：①紫鸟窗口 CDP 端口**每次开窗/重开都变**（本次 33869→11244 等），必须按**进程 MainWindowTitle=窗口名**匹配探测，storeId 无法经 CDP /json/list 识别窗口；②`--store` 查映射时路径必须指向 `scripts/power/data/`（本入口同级），勿用上两级（那是 scripts/ 不是项目根）；③入口 docstring/print 里的 `D:\x` 会被 Python 当 unicode 转义报 SyntaxError（写路径用正斜杠或 r-string）；④audit 禁 `print(中文)` 直写在括号内——中文先进变量再 print（对齐 run_tiktok_export.py 模式）；⑤同名目录 `每日导出销量\` 与 `每日导出销量\<日期>\` 的默认输出，找文件先看子目录。
- **2026-08-28（意外：_connect 取到非目标页）**：紫鸟窗口**新开/重开后可能停在非目标页**（如 seller-us.tiktok.com 登录页、seller.kuajingmaihuo.com 登录页、或扩展 tab 店小秘助手/temaishuju）——driver `_connect` 若找不到目标域页面会**取第一个 tab**（可能取到扩展/登录页），导致后续导航/操作失败。**必须：先探测窗口所有 tab，定位到目标商家页（`tiktokshopglobalselling.com`/`temu.com/mmsos/orders.html`）再操作；若目标页不存在则按「登录/认证处理」走（TikTok 回登录页、TEMU 走 `temu-login-auth` SKILL）。**
- **2026-08-28（意外：残留弹窗/监控面板污染）**：上次运行残留的**「已选N个订单」确认弹窗**或 **run_monitor 注入的监控面板**会挡交互/污染元素计数（checkbox 340 vs 68）——driver 开头必须 `_cleanup_stale_panel`（清监控面板）+ `_close_modal`（点取消关残留弹窗）再操作。
- **2026-08-29（看板定时执行权限·关键）**：看板定时(=scheduled)任务执行会话的默认权限 = **workspace-write**（sandbox=workspace-write + approval=ask），而 TEMU/TikTok 的 **CDP + 紫鸟浏览器自动化需要 danger-full-access**——否则执行会话一触发需要提权的操作就**弹给用户申请权限**、甚至导出失败。已把主机默认 `permission.defaultPreset` 设为 **`danger-full-access`**（`~/.dsh/settings.yaml`；`dsh-permission-presets` 的 `pinInitialPermission` 在会话创建时读用户默认），新建执行会话即得其权限；**创建时已被钉住的会话不会自动升级**，需重跑。排查：执行会话反复弹权限 → 先确认主机 `permission.defaultPreset` 是否 `danger-full-access`。
- **2026-08-29（TEMU 日期筛选/认证易复发·8/29 08:00档）**：TEMU 3 店 8.28 数据最终**全部导出成功**（8.28BHY/BEL/KinglamanSport.xlsx），但**中途多次失败重试**——BHY 连续 4 次 `FAIL: 日期筛选未生效（找不到日期输入框/日历格子）`（`_apply_date_filter` 里 `_cnt(pg,_DATE_INPUT_SELS)` 为空即返回该错），KinglamanSport 1 次日期筛选失败 + 多次 auth「点商家中心 goto 失败」后才成。**已记坑但仍多次复发（防碎不到位）**：①`_DATE_INPUT_SELS`/`_DATE_CELL_SELS` 随 DOM 漂移需补候选 selector；②`_close_modal` 是否关净「使用在线下单…」推广弹窗挡住日期区；③auth「点商家中心 goto 失败」与「非 BHY 店勿用裸 orders URL」同族，需按店处理。**driver 可靠性修复属主会话/@aaa 决策**（@经验飞轮 不改结构/代码；本章只人读留档）。
- **2026-08-29（掉登录态范围扩大 + 旋转拼图验证码）**：本次 6 店重开窗口后 **TH/TSA/BF 三家都停在 seller 登录页**（`seller-us.tiktok.com/account/login` 或 `seller.tiktokshopglobalselling.com/account/login`），并弹 **「旋转拼图验证码」**（拖滑杆**旋转圆盘**，非普通位移滑块）——**自动解需图像分析+滑杆映射，不可靠**。曾调研 GitHub：RotNet 仓库「未完成」、devlenni 仓库为 tiktok.com 消费者端不同 DOM、tiktok-captcha-solver/ttcaptchasolved 均为付费 API 客户端；**无免费本地可解卖家旋转拼图的项目**。掉登录态时**需人工登录**，勿指望自动破解验证码。
- **2026-08-29（BF 新导出记录检测未命中）**：`run_daily_sales.py --store BF` 报 `等待 120s 下载仍未开放`，但 TikTok 侧**实际已生成导出记录**（用户确认）——driver 的「等新导出记录（top 文件名变）」检测 **对 BF 未识别到新记录**（`_top_dl_name` 需含「笔订单-」文本，BF 记录命名/该店环境不同导致未命中）。**遇此需人工确认后走『下载记录』手动下载**；检测逻辑的告警/兜底（120s 未命中时降级列记录让用户选）属主会话/@aaa 决策。
- **2026-08-29（BF 跨境登录页 Continue 点击无效）**：BF 跨境登录页 `seller.tiktokshopglobalselling.com` 点「Continue」**无效**——该表单与 US 站不同：**密码未预填、含图形/短信验证码**。driver/脚本**未覆盖该交互**；BF 若停在登录页不能靠点 Continue 进入，需人工登录。
- **2026-08-29（结构结论：BF 需专门入口，勿混入 US 站共享 driver）**：上述两条 BF 坑的**结构性根因** = `run_daily_sales.py`（唯一 TikTok 入口）把 BF 当普通 US 店跑进 `daily_sales_driver`（US 站假设）。**分支/特殊情况应建专门入口**（如 `run_daily_sales_bf.py` + 独立 driver），勿把多种情况混进一个入口（混用会互相干扰：US 店逻辑覆盖 BF 的跨境 DOM/登录/命名差异）。**此属结构/登记（@aaa/agent-maker），@经验飞轮 只记人读说明不建脚本**；专门入口建成前，BF 按「人工确认→手动下载」处理，勿当普通店硬跑。
- **2026-08-31（合并 --auto 自动区间，用户确认，TikTok/TEMU 两个合并入口对称支持）**：大小周导致合并间隔不定（2/3/N 天）→ 合并入口加 `--auto`：起点=该店在 合并/ 已有 `<MM.DD>-<MM.DD><店>.xlsx` 的最大末日+1（末日年份按"最近年份"推断），终点=`--date-end` 或缺省=最新数据日期目录（扫描 每日导出销量/ 下 YYYY-MM-DD 目录取最大，不依赖时区）；区间内按"该店实际有源文件"的天合并。**仅 1 天 → 跳过**（单日无聚合意义，需单日请手动 --dates）；**无历史合并文件 → 提示首次用 --dates**（避免误吞历史数据如 8/27、8/28）。导出每天 16:00 定时照旧，与合并无关。
- **2026-08-31（TikTok 合并两坑，已固化进 run_tk_merge.py docstring）**：① TikTok 导出 xlsx 是**两行表头**（row0=列名 63 列、row1=列说明 col0="Platform unique order ID."、row2 起=订单数据）——合并须跳过 row1 说明行；② TikTok xlsx `dimension="A1"` 但实际 63 列，openpyxl **read_only 模式只读到第 1 列**，必须非 read_only 的 load_workbook 才能读全。两者决定了 TikTok 合并必须用专门入口 run_tk_merge.py，勿复用 TEMU 版 run_temu_merge.py（其 read_only + 单行表头假设对 TikTok 不成立）。
- **2026-09-02（compass-sales：停在登录页≠需人工登录，直接导航目标页；勿回退写今天）**：`run_compass_sales.py --all` 曾把 3 店（TLA/TSA/TAE）判为『需人工登录』——实际其窗口只是落在 `seller-us.tiktok.com/account/login`（会话仍在），`_connect` 因只匹配 `seller.us.tiktokshopglobalselling.com` 未选中该登录 tab（退化成取第一个 tab/扩展页，L92 同族）→ 抓不到。已修 `lib/compass_driver.py`：`_connect` 用域打分选中 seller/登录 tab → `pg.goto(data-overview)` 直接取数（TSA/TLA 复跑即出 09-01，无需人工登录）。**同时**：`grab_daily_metrics` 未命中「昨天」原回退 `rows[-1]`（会写「今天·进行中」入表当完整日，TAE 曾写入 09-02 $74.65/2/2/2），已改为返回 None（宁可失败也不写错日期）。 → 修订: 实测坑 L67 登录页分界 + lib/compass_driver.py `_connect`/`grab_daily_metrics`；supersedes: 2026-08-28/29「停在登录页一律需人工登录」这条先验判断
- **2026-09-03（KinglamanSport 根因：店铺视图选错，非登录。用户确认）**：该店窗口重开后停在新「Seller Central Beta」门户首页，`/mmsos/orders.html` 与 `/mmsos/export-order.html` 均被重定向回首页，被误判为『需人工登录』。**真实根因 = 右上角选中的店铺视图不是导出所要求的那个**——KinglamanSport 账号下有两个店铺视图：`KinglamanSport`（全托管）与 `KinglamanSport local 半托管`。**本项目脚本 `run_temu_sales.py` 用的导出页 `agentseller-us.temu.com/mmsos/export-order.html` 只在[半托管 local]视图下可用**（全托管视图重定向回首页、无日期格子），BHY/BEL 同理。故 KinglamanSport 必须在**半托管 local（US 站）**视图导出。**判定**：导航 export-order.html 后落回首页（而不是「订单导出」页）即视图不对；此时先做「店铺视图检测」（见下），勿当作登录失败反复授权。**注意**：初次误判时曾切到全托管视图（顶栏显示无 local 的 KinglamanSport），但该视图下 export-order.html 仍不可达——故正确方向是确认/切回**半托管 local**。

- **2026-09-04（TEMU 下载选行坑·严重·防再次交付错日期）**：TEMU 新界面 `export-order.html` 的「导出记录」列表是**跨天累积历史**（含往日 09-01/09-02/09-03…多条）。`run_temu_sales.py` 的 driver `lib/temu_sales_driver.py::_wait_record_done` 用 `reversed(range(n))` **自最旧行往上扫**，命中的是**最旧的已完成记录**（如 09-01）而非本次顶部新增的 09-03 → **文件名是 9.3 但下载到 09-01 数据**（本日 BHY/BEL/KinglamanSport 三家全部中招，字节数与 09-02 前一致即疑似旧记录）。**日期设置本身正确**（导出记录顶部已生成 09-03）；错在下载选行。**判定**：导出后必须用 openpyxl 读该 xlsx 的「订单创建时间」列，非目标数据日＝下载了旧记录。**规避**：下载认**本次新增的顶部行**（时间倒序 → 最新在 idx0）；`_wait_record_done` 应只认 `n-before` 新增顶部行（`range(n-before)` 内取「完成」那条），勿 `reversed` 全表。**修复属 @aaa 结构决策**（本日由人工从导出页手动下载最新 09-03 记录补齐，见 2026-09-04 卡评论）。

- **2026-09-04（TEMU 状态下拉必须设「全部」· 关键）**：`export-order.html` 的「**订单状态**」字段**默认=待发货**，不改则只导出待发货（BHY/BEL 本日因此 68/51→41/37 全待发货）。要导出全状态须把订单状态改成「全部」。**打开下拉**须点**可点击外壳** `div.ST_selectValueSingle_123` / `div.ST_head_123`（含当前值如「待发货」的那个）；**勿用 `get_by_text(当前值)`**——它命中的是隐藏的**输入镜像 `IPT_mirror_123`**（非交互控件），下拉打不开。**选中**：点 `li.cIL_item_123` 的「全部」项（该类名是**版本哈希**、可能随店/页面而异，按**文本「全部」**匹配，勿绑死类名）；选项用真实 click（JS `el.click()` 可能不触发）。**脆弱点**：「订单类型」「订单状态」两个下拉面板可能**同时渲染/重叠**——打开「类型」下拉时首可见项可能是状态下拉的「待发货」，会把类型误设成待发货、导出又被限成待发货；稳妥做法=**逐字段单独开、各自面板内按文本选、选完关闭再开下一个**，且**导出后核验「订单状态」列是否多值（非仅待发货）**。已固化进 driver `lib/temu_sales_driver.py::_select_dropdown`（`_DROPDOWN_ITEM=li.cIL_item_123`＋外壳点击＋真实 click＋面板内按文本选）。

- **2026-09-10（导出留证：截图「勾选的部分」·用户建议）**：本日 BEL「订单状态」列为**单值「待发货」**，因**没有任何点击过程的证据**，当时只能靠「BHY/KinglamanSport 同日多值 + BEL 复跑结果一致」**推断**其完整（用户随后确认 BEL 当日确实全部商品都是待发货 → 推断成立）。根因：留痕 `_logs/temu-sales/<日期>.jsonl` **只记 driver 的返回值**（`OK saved=… (… bytes, … rows)`），**不记点击过程**，故「是否真点中『订单状态=全部』」**无证据可查**（截图目录 `_logs/temu-sales/screens/` 当日为空）。**做法（下次起）**：导出时把**勾选的部分截图留证**——①「订单状态」下拉选中值（应=全部）②订单日期筛选选中的日期 ③「确认导出」弹窗勾选态；每店落 `_logs/temu-sales/screens/<数据日期>/<店>_*.png`。执行步骤见「执行流程」第 6 步。**driver 目前不自动截图**（固化进 driver 属 @aaa 决策），暂由执行会话用 CDP 补截。

- **2026-09-16（TEMU 三家全失败：授权「假失败」+ 死 tab 误判 + 按名探端口失效；已按用户指示修代码）**：本日 3 店开窗后登录态全丢、均需重新授权（符合 2026-08-31 的关窗副作用）。修前三家都跑不出来，**三个根因彼此独立，均已修**：
  ① **授权其实成功却报 `FAIL: 授权未完成` + exit 42**（`lib/auth_temu_driver.py`）：D4 判据只认 `/mmsos/orders.html`，而本次授权是从 `.../mmsos/export-order.html` 的 redirectUrl 进来的、**落地页就是 `export-order.html`** ⇒ 被判「未进入订单页」。**修**：D4 放宽为 `_url_is_seller_page()` = host `agentseller-us.temu.com` + path 在 `/mmsos/` 下且**不是** auth/login（仍坚持 host+path，免疫 TLA-011「redirectUrl query 里带 orders.html」陷阱）；并在 attempt 循环开头**先复查**是否已落商家中心页（避免在残留死 tab 上"再授权"一次再报 FAIL）。
  ② **死 tab 让导出误报「停在登录/认证页，需先授权」**（`lib/temu_sales_driver.py`）：授权后窗口留下一个**空白、0 个 input 的 `seller.kuajingmaihuo.com/settle/seller-login` tab**，且排在 tab 列表最前；旧 `_connect` 取「第一个含 temu/kuajingmaihuo 的页」⇒ 取到它。其 URL 的 `redirectUrl` query 里**含** `export-order.html`（旧守卫 `"export-order.html" not in url` 被它骗过 → **不导航**），path 又含 `seller-login`（旧守卫 `"login" in url` 命中）⇒ 直接误报。**修**：`_connect` 改按 `_page_rank` **优先级选页**（导出页 > 订单页 > 其它 `/mmsos` > 卖家中心其它页 > 其它 temu 页 > 认证页垫底——认证页仍作兜底，以免把"真需授权"吞掉）；两道守卫改 `_url_is_export_page` / `_url_is_auth_page`（host+path）。
  ③ **窗口标题漂移致按窗口名探端口失效**：授权后标题变成 `TM-BHY-YJF    |    卖家中心`，`MainWindowTitle -eq '<窗口名>'` 精确匹配找不到 ⇒ 误报「未探测到 CDP 端口」。**修**：`run_temu_sales.py` / `run_temu_auth.py` 的 `_find_store_port` 改**前缀匹配** `-like '<窗口名>*'`。
  附：导航 `export-order.html` 落回首页（店铺视图不对）现在**明确报**「被弹回卖家中心首页」并指向本节视图切换，不再伪装成「日期选择器未生效」（本日 KinglamanSport 即此情形，切回**半托管 local** 后正常导出）。**回归单测**：新增 `scripts/power/tests/test_temu_sales_pages.py`（选页/URL 判据，含死 tab 形态回归）+ `test_temu_auth.py`（改测放宽后的 D4）→ `python -m pytest scripts/power/tests/test_temu_sales_pages.py scripts/power/tests/test_temu_auth.py -q` = **11 passed**。
  **待转交（非本 agent 权限）**：`temu-login-auth` SKILL 的 D4 判据与步骤仍写「只认 orders.html」，需按上面 ① 同步（SKILL/登记归 registry·@aaa）。

- **2026-09-16（TikTok 7 店：TLA/TH 浏览器级 CDP 握手卡死 → 已固化为 driver 自愈；按用户「保证之后不会再发生」指示改码）**：本日 16:00 档 5 店一次成功，TLA/TH 首轮即 `BrowserType.<连接器>: Timeout 15000ms exceeded`（同端口 `/json/version`、`/json/list` 都正常），**重试 1 次仍超时**；按判定（SKILL `ziniao-cdp-port` D2「窗口 CDP 假死」行）close/open 重开窗口 + 现场重探端口（TLA 16425→24571、TH 49259→13365）后恢复；TLA 重开后另有 1 次「等待 120s 下载仍未开放」，原命令重跑即成功。**该现象自 2026-08-31 起已复发 ≥8 轮**（08-31 TH/TSA、09-03 TSA/TC、09-04 6 店、09-09 TLA/TH/BF、09-12 TLA/TAE、09-14 TAE、09-15(数据日) TLA/TH），此前每轮靠人工救、处置只写在被覆盖写的当日汇总里。**本次改动（3 处，非结构重写）**：
  ① **新增 `scripts/power/lib/ziniao_store.py`**——窗口生命周期与端口现场探测的**单点实现**：`probe_port_by_window()`（前缀匹配，RL-022）、`open_store/close_store()`、`restart_and_reprobe()`（close→open→等稳定→**重探**端口，RL-002）、`ziniao()`（经 powershell 调 CLI + node 目录自愈）；所有 spawn 走 `lib/quiet`（不弹黑框）。
  ② **`daily_sales_driver.py` 加连接级自愈**：失败归类 `_classify_failure()`（conn=连接/窗口级 → 重开窗口+重探端口再跑；page=页面级如「等待 120s 未开放」→ 原端口再跑；未知不重试），**重试上限 1 次**；并修一个真隐患——**握手失败原先不调 `p.stop()`**，残留 playwright driver 会让后续连接继续卡（现已在失败路径回收）。
  ③ **`run_daily_sales.py`**：`_find_store_port` 收编为 `ziniao_store.probe_port_by_window`（去重复实现）；探不到端口时**自动开窗后重探一次**；自愈事件写 `_logs/daily-sales/`（`self_heal` / `store_open` / `probe_fail`）。
  **回归单测**：新增 `scripts/power/tests/test_daily_sales_selfheal.py`（15 条：分类/自愈/上限=1/回收 playwright/前缀探测）→ `python -m pytest scripts/power/tests/test_daily_sales_selfheal.py -q` = **15 passed**；判定表同步写在 SKILL `ziniao-cdp-port` D2 + CP-013（单点，本文件不复制表）。

- **2026-09-21（TikTok 7 店：一次导航超时被无谓重开窗口 → 自愈改两段式；另补 3 个实测坑。用户拍板后改码）**：16:00 档 7/7 成功（9.20 数据），但首轮 TLA 报 `ERR: Page.goto: Timeout 30000ms` → 命中旧 `_RETRY_CONN_SIGNALS` 里的 `Page.goto` → 被判"窗口级故障"**close/open 重开窗口**。**事后取证：这次重开是无谓的**：① 该窗口标签 URL 自始至终是当天 PT 目标筛选页（`time_order_created[]` 毫秒区间＝PT 09-20，CDP 实测）；② 重开窗口后**立刻重试仍失败**，等约 5 分钟再跑才成功（真正起作用的是"页面捂热"，不是重开）；③ 用户现场看到的也是「导出页已经打开」。**根因**＝`_apply_date_filter` 无条件 `pg.goto(同一个 URL)` + `wait_until="load"`（等全部子资源，卖家后台长轮询/埋点多 → 常 30s 不触发）＝"多余的重新导航"超时被误判成"窗口坏"。
  ① **分诊改两段式（改动 1）**：`Page.goto` 超时与 `net::ERR_ABORTED` 从 `_RETRY_CONN_SIGNALS` **挪到** `_RETRY_PAGE_SIGNALS`；`export_daily_sales` 改为「**首轮任何可重试失败 → 先原端口重试一次；该次仍失败且报真·连接级信号（`connect_over_cdp` 握手超时 / `ERR_CONNECTION_*` / 目标已关闭）→ 才 `restart_and_reprobe()` 重开窗口一次**」；`max_retries` 缺省 1→**2**、**重开窗口最多 1 次**。页面级信号（导航超时/图标未找到/等 120s 下载未开放）**永不**触发重开窗口。
  ② **不重复导航（改动 2）**：新增 `_already_on_target()`（**解析查询串**比对 PT 区间 + `tab=all` + 订单标签，认 `%5B%5D` 编码）→ **已在目标页就只等 800ms 直接进流程**；确需导航时改 `wait_until="domcontentloaded"` + 显式 `_GOTO_TIMEOUT_MS=90000`（**别等 `load`**）。
  ③ **新坑：导出面板会盖住下载图标（改动 3）**：**上一次导出留下的「导出订单」右侧抽屉会盖住工具栏 `svg.arco-icon-download`**——图标 `is_visible()=True` 却在抽屉底下，`click` 等 30s 超时、报成**「未找到导出下载图标」**（与"窗口态/登录态漂移"**同文案、不同根因**）。旧代码靠无条件 `goto` 重载**顺带把面板刷掉**（隐式依赖），跳过重导航后必须显式补：新增 `_export_panel_open()`（认面板特有业务文本「导出历史记录」，比框架类名稳）→ 开着就点 `_PANEL_CLOSE`（「关闭」，实测全页唯一可见），关不掉才 `reload(wait_until="domcontentloaded")` 复位。另加 `_ICON_WAIT_MS=45000`：就绪判据从 `load` 事件改成**图标可见**。
  ④ **措辞坑（改动 4）**：self-heal 的 note 原先**只要拿到新端口**就拼「重试第N次成功」——**实测骗过我一次**（那次重试其实失败了）。已改为「已重试第N次」。**判读规矩：自愈成败看最终 verdict 与 `retry#N after:` 那条日志，别只看 `[self-heal …]` 后缀。**
  ⑤ **补记两条核验坑**：(a) 导出 xlsx **第 2 行是字段说明行**（第 29 列＝`Order created time.`），**数据自第 3 行起**；从第 2 行开始核验会把说明行当"跨日行"**假报 bad**。**合并后的文件没有说明行、数据自第 2 行起**（两处行偏移不同，别混用）。(b) `--date` 缺省＝**UTC 昨天**（非 PT），非 16:00 档补跑**必须显式传**（09-14 曾误产错日文件）。**同症状判别**：「未找到导出下载图标」既可能是**掉登录**（2026-09-19 TLA，标签被重定向 `account/login`）、也可能是**加载慢**（2026-09-21 TLA，标签就在 order 页）→ **必须 CDP 读标签 URL 定性**，别只凭报错文本判「需人工登录」。
  **回归单测**：`test_daily_sales_selfheal.py` 扩到 **23 passed**（新增：两段式升级 / 纯页面级失败不重开窗口 / 复用判定 / 跳过重导航 / 面板探测 / 导航超时不重开）。命令：`python -m pytest scripts/power/tests/test_daily_sales_selfheal.py -q`。
  **真机验证**：① **复用路径**（面板开着 + 已在目标页）→ 26s 导出成功、**页面 JS 标记未丢**（证明确实没重导航）、日志**零 self_heal**；② **需导航路径** 31s 成功；③ **升级分支**（用户要求补测）：直连**死端口**跑 TC → `retry#1` 原端口仍 conn → 真 `close/open` 重开 `TK-TC-LJH` + 重探 **37670** → `retry#2` **导出 OK**（9851B、3 行、Created Time 全＝09/20/2026，与当日正式产出**同字节**）；重开后 TC 窗口标签即落 order 页，**登录态未受影响**。验证一律走 `--out` 临时目录，未动已验收产物。
  **未做（待拍板）**：占用检测升级（`_seller_tab_occupied` **探测失败静默放行**、只查商家中心**单** tab、结论**不进日志**；`_connect` 找不到商家中心 tab 时会取窗口**第一个** tab 并导航 → 可能劫持同窗口其它爬虫的 tab）。

- **2026-09-21（下半场：**稳定绑定**重写 + 一个我自己引入的真 bug。用户要求"必须能稳定绑定，绑不住多半是人的鼠标操作"）**：用户点破了要害——**旧 `_connect` 每次靠"URL 含某域名，取第一个匹配的 tab"动态绑定**（违 SKILL `ziniao-multi-tab` **D3**「检测一次→全程锁定」）。人在窗口里新开/切换标签页，就会把我们吸到别人的 tab 上。
  ① **认全域名族**：新增 `_SELLER_URL_MARKS` = `seller.us.tiktokshopglobalselling.com` / `seller.tiktokshopglobalselling.com`（09-19 TLA 掉登录就停在这个域名，旧实现认不出）/ `seller-us.tiktok.com`（历史形态）+ `_is_seller_url()`。旧实现只认 `.us` 那一个。
  ② **锁定绑定**：`_bind_store_page()` + `_choose_seller_tab(tabs, locked_id)`——锁定 targetId 优先 → 偏好已在 `/order` 的 → 其余按 url 稳定排序（禁 `pages()[0]`）。`binding`（可变 dict）在 `export_daily_sales` 里建一次、**跨重试传递**，重试阶段不会改绑。`_page_target_id()`（CDP `Target.getTargetInfo`）把"锁定的 targetId"落实到 Playwright page 对象。
  ③ **不再抢别人的页**：**删掉**"找不到商家中心页就取窗口第一个 tab"这条兜底（旧实现会劫持别的爬虫/扩展页）；改为**一个卖家 tab 都没有就后台新建**（`_open_background_tab()` = `Target.createTarget(background=true)`，**绝不 bringToFront**）；Playwright `new_page()` 只作降级兜底。
  ④ **绑定决策留痕**：每次绑定写 `_logs/daily-sales/*.jsonl` 的 `bind` 事件（`locked`/`picked`/`url`/`family`/`created` + targetId + url），此前这步零留痕、出问题事后无法判读。
  ⑤ **⚠️ 实测踩到并修掉的真 bug（我自己引入的）**：**`connectOverCDP` 自己会 attach 页面 target** ⇒ 在**连上 Playwright 之后**再探 `attached`，探到的是**自己** → 误判"被别的爬虫占用"→ 一跑就 `HARD_STOP`；更糟的是 `_connect` 抛错时**不回收 Playwright**，泄漏的连接让同一进程后续每次都直接失败（现象：同进程连跑两次全 HARD_STOP，另起进程探却是 `attached=false`——排查就是从这条矛盾入手的）。**修**：占用判定只在**起 Playwright 之前**做（`_connect` 前置守卫 = 真的"别人"）；`_bind_store_page(check_attached=False)` 用于已连接后，日志如实记"attached=true 但是本会话自己连的"；`_connect` 绑定失败时回收 `b.close()/p.stop()`。
  **回归单测**：`test_daily_sales_selfheal.py` 扩到 **29 passed**（新增 6 条：域名族 / 择一与锁定 / 锁定 target 不被新 tab 吸走 / 无卖家 tab 时**新建且不导航别人的页** / attached 时 HARD_STOP / 跨重试沿用同一 target；另修 2 条夹具的 `_connect` 签名）。
  **真机验证（全部实测，非推断）**：① TLA 连跑 2 次：27.4s / 25.4s **均 OK**，页签数 3→3（**没新建、没劫持**），bind 日志如实记"attached=true 但是本会话自己连的"；② **人新开一个卖家域 tab**（`seller.tiktokshopglobalselling.com/account/login`）后再导出 → 仍成功、仍绑 order tab（3→4 页签，人开的那个原样保留，收尾关掉）；③ **跨会话占用**：用另一个 Node 进程真挂住 TLA 的 tab（page-level CDP）→ 探到 `attached=true` → `_connect` **正确 HARD_STOP**；释放后回 `false`；④ **无卖家 tab 的窗口**（TEMU 窗口，端口 21665，`_seller_tabs` 返回 `[]`）→ `_open_background_tab` 真开出一个后台 `about:blank`（2→3），**原页签 URL 一个没动**，收尾关掉回 2。
  **顺带发现（供后续）**：① **按窗口名探端口会失败而窗口其实开着**——TEMU 窗口标题漂移，`probe_port_by_window("TM-BHY-YJF")` 返回 0，但端口 21665 活着（`/json/version` 正常）；缺端口时别只信按名探测。② **该 TEMU 窗口上 Playwright `connect_over_cdp` 握手 15s 超时**（RL-013 形态，紫鸟那次 Chrome 146；TK 窗口不受影响、每天正常）——即"稳定绑定"对**走到 Playwright 那一步之前**就失败的窗口无能为力，那种情况仍靠两段式自愈（重开窗口）。

**⚠️ 并发抢记录 + 下载后日期自检（2026-09-22，用户拍板落地）**：

- **坑（新，当天实战踩到，脚本不会报错）**：`daily_sales_driver` 原来「等列表顶部换名 → 取最新一条」**只比对名字变没变**，不区分记录范围。而 power 发货第0步会**在同一批窗口**上导出「待发货」订单，生成 `待发货 笔订单-….xlsx`（实测 TH 面板 15 条＝「全部」「待发货」交错）。2026-09-21 档 TH 那次自愈重试后 `DL target(最新)=待发货 笔订单-2026-09-22-02:31.xlsx`，产物 107 行**全是「待发货」**、`Created Time` **跨 09/16~09/21**，脚本报的却是 `[OK]` —— 只有按 `Created Time` 逐行核验才看出来（16:25 重导修正为 57 行全 09-21）。
- **改法（三处，`scripts/power/lib/daily_sales_driver.py`）**：①**只认本任务范围的记录**（`_record_name_ok`＝含「笔订单-」且以「全部」开头）；②**导出前先记基准**（`_newest_in_scope` 取"导出前最新的「全部」记录"，且**等面板列表渲染出来再记**——真机实测：窗口重开后列表未渲染 → 基准记成空 → 2s 后就抓到上一条旧记录）；③**下载后日期自检**：读产物 `Created Time`（普通模式、数据自第 3 行起）必须全 = 数据日期，不符即 `FAIL: 下载文件日期不符（…）`，归类**页面级**（原端口重跑一次，不重开窗口），并把结果写 `verify` 事件入 `_logs/daily-sales/`。
- **另加提示（不改变判定）**：等 120s 仍无新记录时，若页面同时出现「找到 0 个订单」+「(0 笔订单)」，FAIL 文案尾部附 `[hint: 页面筛选结果 0 笔订单 → 该店当日可能 0 单]`。**刻意只作提示**：页面加载中途也可能短暂读到 0，据此跳过某店会静默漏数据；0 单的认定仍须 CDP/人工核实。
- **口径（用户 2026-09-22 拍板）**：**某店当天真实 0 单 → 平台不生成导出记录 → 无文件属正常**，不算失败（BF 09-21 实测：面板 7 条无新增、页面「找到 0 个订单」、用户现场确认）。判定"0 单"必须有证据（CDP 读标签 URL 在 order 页带当天筛选 + 页面 0 笔 + 面板无新增），不得拿"等 120s 未开放"直接当 0 单。
- **验证（全部实测）**：单测 `tests/test_daily_sales_selfheal.py` **35 passed**（+6 条：范围过滤 / 越旧不取 / 名字尾部「下载」规整 / 第 3 行起读 Created Time / 跨日判页面级可重试 / 0 单提示只作提示）；**真机**——① TH 面板现场校验：前 6 条交错记录里正确跳过 3 条「待发货」；② 端到端跑 TH 落临时目录：抓到的是**本次新生成**的 `全部 笔订单-2026-09-22-05:13.xlsx`（不再是旧记录），`[verify: rows=57 off_date=0]`；③ 把平台上真实的那条「待发货」坏样本（58248 字节）下载下来喂给检测函数 → `(rows=106, off=52)` → **判 FAIL**，判据成立。


**店铺视图检测 + 切换步骤（经验飞轮 2026-09-03 实测，BEL/KinglamanSport 双样本验证）**：

> **何时做这个判断（关键 · 前置而非后置）**：在**导出前的预演阶段**（`run_temu_sales.py --store <店> --dry-run` 或真跑前的 `_connect`/导航）判断，**不要在导出失败返回「日期选择器未生效/停在登录页」后再猜**。理由：导航 `export-order.html` 后是否落回首页是一个**极低成本、可在导出前测出的强信号**——若落回首页（而不是「订单导出」页），100% 是店铺视图不对（全托管），此时直接做下述切换，而不是反复授权/重试导出。**每店每次跑前都先探测一次**（窗口可能被重开/切店），不要只在失败时做。

1. 当前页可是订单页/首页（`agentseller-us.temu.com` 域下）。点右上角**店铺名**（BEL=「Elanvoke」；KinglamanSport=「KinglamanSport local」/「KinglamanSport」；顶栏 y<90）。
2. 用 **Playwright 原生 `get_by_text(店铺名).click()`** 打开店铺下拉（**勿用 JS `.click()`**——它常因下拉未渲染/命中错元素而无反应，曾因此被误判"没有面板"）。
3. 下拉里点「**切换**」（原生 click）→ 打开「**切换店铺**」面板（文本「您的账户存在以下2个店铺，可切换至任意店铺」）。BEL 实测评此面板真实存在、可打开。
4. 面板列出店铺视图：BEL=「Elanvoke 半托管」（当前）/「ElanvokeFashion」；KinglamanSport=「KinglamanSport」/「KinglamanSport local 半托管」。半托管视图通常带「半托管」标签。
5. 点目标视图条目「切换」→ 页跳到该视图（顶栏店名随之带/去 local 后缀）。
6. 再导航 `agentseller-us.temu.com/mmsos/export-order.html` → 到「订单导出」页（title=订单导出、有日期格子）→ 脚本导出。
7. **要点**：脚本导出页 `agentseller-us.temu.com/mmsos/export-order.html` 只在**半托管视图**可用（全托管导航后回首页、无日期格子）；故应确认处于**半托管**视图。**别因 JS 点击无反应就下"切店入口不存在"的结论**——用原生 click 两步法（店铺名→切换）。
8. **未定（待确认，勿写死）**：点面板目标行「切换」后是否立即生效（BEL 实测点 ElanvokeFashion「切换」后 URL 仍停留 orders.html、顶栏不变，可能是需二次确认/目标店门槛，未定论）。真正需切店时再按实际确认，勿凭单次点击失败断言"切不了"。

## actions（参数化入口）

| action | 干什么 | 备注 |
|---|---|---|
| `run_daily_sales.py --list-stores` | 列固定 7 家 TK 店（后缀/窗口/storeId） | 数据外置 ziniao_power_map.json |
| `run_daily_sales.py --store <店> --dry-run` | 预演（自动查窗口+探测端口；打印日期/文件名/目录） | 先干跑再真跑 |
| `run_daily_sales.py --store <店> [--date] [--date-end]` | 真实导出订单 Excel | 文件名=`<数据日期MM.DD><店>.xlsx`；全自动 |
| `run_tk_merge.py --dates <d1,d2> [--stores ...]` | 多天同店 TikTok 销量按店合并成 1 个文件（保留 63 列） | 纯离线 openpyxl，不涉 CDP；产物 `每日导出销量/合并/`；TikTok 两行表头+63 列，与 TEMU 版分开（勿混用） |
| `run_tk_merge.py --auto [--date-end]` / `run_temu_merge.py --auto [--date-end]` | 自动区间合并：上次合并截止日+1 → 最新数据日，≥2 天按店合并 | 大小周/间隔不定场景；单日跳过；无历史提示用 --dates；导出每天照旧 |
| `run_temu_merge.py --dates <d1,d2> [--stores ...]` | 多天同店 TEMU 销量按店合并成 1 个文件（保留 32 列） | 纯离线 openpyxl，不涉 CDP/命名管道；产物 `每日导出销量/合并/`；数据外置 ziniao_power_map.json |
| `run_compass_sales.py --store <店> [--dry-run]` | 取该店 Compass 数据概览「**昨天(完整日)**」核心指标（日期/GMV/订单数/客户数/商品成交件数）并追加到 `每日导出销量/Compass/<店>.xlsx` | 只读页面（不触发导出）；Compass 默认展示今天(未完整)→driver 点预设「昨天」并按站点时区(UTC-8)昨天定位；同日期重跑覆盖不重复；走 CDP 现场探测端口 |

## 禁止项

1. 不 fork 复制脚本；要新行为=加 action/参数（铁律6）
2. 不写死 CDP 端口（`--scan`/`--port` 现场探测）
3. 裸 open(中文)/print(中文) 禁；读写走 lib.uio 显式 utf-8
4. **点完导出不立即点下载**：必须等下载开放（时间差 ~18s，轮询最长 120s）
5. **下载按钮多个时点最新一条**，不点旧记录
6. 重试 ≤1 次后停下报告，不连环重试；异常必留痕（`_logs/daily-sales/`）
7. 不统计/不分析导出的 Excel（数据用途交用户/主会话）
8. 落盘 workspace（每日导出销量/），不写 Downloads
9. 提交前自检 `python -m lib.audit scripts/power`（检硬编码端口/裸open/裸print中文）
9a. **TEMU 链改动后必跑回归单测**（2026-09-16 起）：`python -m pytest scripts/power/tests/test_temu_sales_pages.py scripts/power/tests/test_temu_auth.py -q` ——守「选页优先级 + 页面身份 host+path 判据（不整串子串）+ D4 可用页口径」（对应红线 RL-021 / RL-022）。
9a2. **TikTok 链改动后必跑回归单测**（2026-09-16 起）：`python -m pytest scripts/power/tests/test_daily_sales_selfheal.py -q` ——守「握手失败归类 + 重开窗口自愈 + 重试上限=1 + 握手失败必须回收 playwright driver + 端口按窗口名前缀探测」。
9c. **按名探不到端口 ≠ 窗口没开**（2026-09-16）：页面会改紫鸟窗口标题，按名匹配须**前缀**匹配（RL-022）。若 `--store <店> --dry-run` 报「未探测到 CDP 端口」：先 `Get-Process ziniaobrowser` 确认窗口在跑 → `--scan` / `/json/list` 现场取端口 → 用 `--port <port>` 跑，**别判"窗口没开"或反复重试同一条命令**。
9d. **"需先授权"要先复核再下结论**（2026-09-16）：`FAIL: 停在登录/认证页，需先授权` 也可能是**残留登录死 tab**造成的假信号（driver 已按优先级选页，RL-021 已修）。复核方式：`/json/list` 看窗口各 tab 的真实 URL——若其中已有 `/mmsos/` 下的可用页（`orders.html` / `export-order.html`），先关掉该死的 `settle/seller-login` tab 再重跑导出，而不是直接标『需人工登录』。
9b. **TEMU 导出后必须校验**（2026-09-04）：openpyxl 读导出 xlsx，①「订单创建时间」列须全为目标数据日（非则＝下载到旧记录）；②「订单状态」列须为**多值**（一旦全是「待发货」＝状态下拉没设成「全部」，导出漏单）。两者任一不符即重导/重下，勿交付错数据。
10. **并行导出 ≤ 2（用户明确 2026-08-29）**：一次最多 2 个并行导出（TikTok/TEMU），超过易触发平台风控；多店串行或 ≤2 并发，勿同时开多店导出。
11. **BF(TK-CLYC) 是特殊分支，勿经 run_daily_sales.py 的 US 站路径跑**：BF 为**跨境店**（`seller.tiktokshopglobalselling.com`，登录页密码不预填/含图形·短信验证码、下载记录命名与 US 站不同），`daily_sales_driver` 的 US 站假设（`_ORDER_URL` US 站 / `svg.arco-icon-download` 图标 / `_top_dl_name` 要求「笔订单-」文本）对 BF 对不上 → 会反复「找不到导出图标/等待120s 未开放」。**分支/特殊情况须走专门入口**（@aaa/agent-maker 制作 `run_daily_sales_bf` 等独立入口+driver），**勿把多种情况混进一个脚本**使彼此干扰。在专门入口建成前：BF 走人工确认/手动下载，勿当普通 US 店硬跑。

## 授权范围（用户确认 2026-08-29）

- 用户明确：TEMU 3 店 BHY/KinglamanSport/BEL 若停在 login/auth 认证页，本会话可**自动完成登录/授权流程**（按 `temu-login-auth` SKILL：选区域→点商家中心→勾选授权条款→点「确认授权并前往」→验证进入 orders.html），然后重新导出。
- **以后不再询问**此授权范围问题（用户要求记录在 agent 内，避免重复提问）。
- 例外仍有效：若授权需人工验证码/多次失败，该店标注『需人工登录』并在评论说明，不阻塞其它店。

## 相关参考

- 脚手架：`Desktop\AI\agent与skill\项目skills\ai-python-methodology\README.md`（参数化入口 + lib 闸门）
- 入口：`scripts/power/run_daily_sales.py`；driver：`scripts/power/lib/daily_sales_driver.py`
- 监控工具（流程固化来源，**已归档 2026-09-12**）：`_scripts/_archive/temu-export-explore-20260828/run_monitor.py` + `monitor_inject.js`（原在 `每日导出销量/`，因散落脚本违反归档铁律、被 `lib.audit` 点名，已移入归档区）
