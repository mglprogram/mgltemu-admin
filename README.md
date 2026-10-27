# TEMU 运营后台 + 自动化脚本

TEMU 店铺运营的数据看板与自动化采集/操作脚本集合。

- **`temu-admin/`** — 运营后台前端（Vue 3 + Vite + TypeScript + Element Plus），
  即 <https://mgltemu.us.ci/#/dashboard> 对应的单页应用。
- **`scripts/`** — Python 自动化脚本：销量导出、面单购买、异常单处理、数据构建与发布。
- **`agents/`** — DSH Agent 定义（每日导出销量、多设备迁移）。

---

## 一、运营后台 `temu-admin`

### 技术栈

| 项 | 版本 / 说明 |
|---|---|
| 框架 | Vue 3.5 + Vite 8 + TypeScript 6 |
| UI | Element Plus 2.14、UnoCSS |
| 状态 / 路由 | Pinia、Vue Router 5 |
| 图表 / 表格 | ECharts 6、vxe-table |
| 包管理 | **pnpm**（`preinstall` 强制，勿用 npm/yarn） |
| Node | `^20.19.0 \|\| >=22.12.0` |

### 运行模式：单机（standalone）

本项目是**纯静态前端**，没有后端服务。设置 `VITE_STANDALONE=true` 后：

1. 路由守卫直接放行，不再要求 access token；
2. 权限 store 直接标记「路由已生成」，不请求 `/menus/routes`；
3. 菜单只渲染 `src/router/index.ts` 中的静态路由。

页面数据来自构建期导出的静态 JSON（`public/data/*.json`），
由 `scripts/bel_goods/` 下的构建脚本生成。日后接入真实后端时，把该开关关掉即可
恢复完整的登录 + 动态菜单流程（见 `src/utils/standalone.ts`）。

### 页面路由

| 路径 | 页面 | 说明 |
|---|---|---|
| `/dashboard` | 数据大屏 | 全屏展示（含「订单分布·美国地图」面板） |
| `/console` | 运营总览 | 白底卡片页（原 `/dashboard`） |
| `/temu/on-sale` | 在售款明细 | 货号 / 库存 / 申报价明细 |
| `/temu/limited` | 限流监控 | 限流款跟踪 |
| `/temu/flux-boost` | 流量加速 | 流量投放建议 |
| `/temu/presale` | 销量-库存 | 补货 / 预售判断 |
| `/temu/order-geo` | 订单分布 | 美国州/市出单分布（数据源=帕沃尔海外仓，见下） |
| `/automation` | 自动化任务 | 定时任务状态 |
| `/data-screen` | 数据大屏 | 旧地址，重定向到 `/dashboard` |

### 本地开发

```bash
cd temu-admin
pnpm install
pnpm dev            # 开发服务器，默认 :3000
pnpm build          # 类型检查 + 生产构建 → dist/
pnpm build-only     # 仅构建，跳过 vue-tsc
pnpm type-check     # 仅类型检查
pnpm lint           # eslint + prettier + stylelint
```

### 访问门禁

线上站点含店铺货号 / 库存 / 申报价，**不能裸奔**。
发布脚本 `scripts/bel_goods/publish_admin.py` 会在构建后
往 `dist/index.html` 注入访问密码门禁，密码取自环境变量 `DASH_PASSWORD`
（留空则拒绝发布）。

---

## 二、自动化脚本 `scripts/`

```
scripts/
├─ lib/                     # 共享闸门库（自包含）：preflight / uio / paths / zport / quiet / audit / rules
├─ data/                    # 闸门规则数据（lib 读取；缺失会报 RedlineNotFound）
├─ power/                   # ← TEMU 操作域本体
│  ├─ run_temu_sales.py         # 销量导出（TEMU 美国站订单 Excel）
│  ├─ run_temu_merge.py         # 多天导出按店合并（纯离线）
│  ├─ run_temu_sales_push.py    # 把某日导出发到企微会话（发群有硬闸门）
│  ├─ run_temu_auth.py          # 登录/授权（区域 → 商家中心 → 授权确认）
│  ├─ run_temu_buylabel.py      # 购买面单 / 批量在线下单（写操作）
│  ├─ run_temu_abnormal.py      # 合作仓异常出库单处理（写操作）
│  ├─ run_temu_stock_topup.py   # 库存补货
│  ├─ lib/                      # 域驱动（sales / buylabel / abnormal / auth / ziniao_store / cdp_transport）
│  ├─ data/                     # 口径配置 + 店铺映射 ziniao_power_map.json
│  └─ tests/                    # 26 个单测
├─ bel_goods/               # 看板数据构建 + 发布
│  ├─ build_api_data.py         # 生成 temu-admin/public/data/*.json
│  ├─ build_automation_data.py  # 自动化任务数据
│  ├─ build_presale_data.py     # 预售数据
│  ├─ build_dashboard.py / build_trend.py / build_bel_db.py
│  └─ publish_admin.py / publish_cf.py   # 发布到 Cloudflare Pages
├─ goods/                   # 商品数据构建
│  ├─ build_goods_data.py       # 在售款明细数据
│  └─ enrich_restock.py         # 补货信息补充
├─ pw_orders/               # ← 帕沃尔海外仓「订单地理分布」域（2026-10-14 新增）
│  ├─ run_pw_order_geo.py       # 入口：list / geo / export / build / all
│  ├─ lib/pw_session.py         # 帕沃尔后台会话（登录 · 直连 · 连接阶段重试）
│  ├─ lib/pw_order_geo.py       # 纯解析/聚合（发货列表行、收件地址、州-市汇总）
│  ├─ data/pw_shops.json        # 两家 TEMU 店的帕沃尔店铺ID
│  └─ tests/                    # 离线单测
├─ oms/                     # ← 领星 OMS(xlwms) 订单数据源域（2026-10-15 新增）
│  ├─ run_oms_orders.py         # 入口：sync / backfill / stats / build
│  ├─ lib/oms_client.py         # v1 OpenAPI 客户端（authcode 签名 + 分页/批量上限）
│  ├─ oms_account.local.json    # appKey/appSecret（**不入库**，.gitignore 挡）
│  └─ tests/                    # 离线单测
└─ dsh/                     # DSH 任务集成
```

### 订单地理分布（帕沃尔海外仓 → 看板）

「订单分布」页与数据大屏的美国地图**不是** TEMU 后台的数据，而是从
**帕沃尔海外仓** `powerwarehouse.net` 抓的：

| 步骤 | 接口 | 拿到什么 |
|---|---|---|
| `list` | `deliver.php?act=query_order_list`（每页 300 行，**按月切片**） | 两店(BEL/BHY) 全部出库单：出库单id / 参考单号 |
| `geo` | `deliver.php?act=order_info&id=<出库单id>` | 「交货信息」里的 **城市 + 州 + 邮编** |
| `products` | `deliver.php?act=order_info&type=view&id=<出库单id>` | [详情] 弹窗的产品表：**Fnsku / 产品名称 / 数量 / 货值**（一单可多品） |
| `export` | `deliver.php?act=daochu_excel`（自带「导出Excel」） | 数量 / 下单时间 / 出库仓库 / 出货渠道（**没有州-市**，**一单只出一行只带第一个产品**） |
| `build` | —— | `temu-admin/public/data/order-geo.json` + 全量订单明细 xlsx + 州汇总 md + **产品×州 xlsx/md** |

```bash
python scripts/pw_orders/run_pw_order_geo.py all          # 全流程（约 1.5~2 小时）
python scripts/pw_orders/run_pw_order_geo.py geo --limit 50   # 试跑 50 单
python -m pytest scripts/pw_orders/tests -q               # 离线单测
```

几个**实测踩过的坑**（都写进代码注释了）：

- 帕沃尔的「导出Excel」**不含收件州/市**，州-市只能逐单从详情页的「交货信息」取；
- **「导出Excel」一单只出一行、只带第一个产品** —— 实测约 **9.5~11% 的单挂 2 个产品**，
  所以产品维度必须走 [详情] 弹窗（`act=order_info&**type=view**&id=`，`type=view` 不能省，
  省了那块物料表是空的）；同理「数量」也要按弹窗里的每品数量，
  否则多品单会漏计；
- 详情页那行是 **`城市 州`**（`DALLAS Texas`），而被注释掉的一行是 `州 城市` —— 别拿注释行当主口径；
- 州名可能两个词（`North Carolina`），所以「按空格切两段」会切错，必须按州名表**后缀匹配**；
  同一站点还有第二种写法是**两字母州码**（`NATIONAL CITY CA`，实测约 22%），两种都要吃；
- 城市可能带 `*`（帕沃尔脱敏，如 `Sp**** *****`），**州名始终完整**；
- **全区间深分页会把服务端拖死**（BHY 第 22 页起挂到 120s 超时），`list` 必须**按月切片**，
  切完每个月只有 3~9 页、每页秒回；
- 自带导出的 `max_daochu_nums=20000` 会让服务端生成阶段 **500**，用 **10000**；
- `page_size` 服务端上限约 **300**（传 800 也只回同一批）；
- 服务端会偶发限流（连续几万个请求后单请求延迟飙到 ~30s 或整体降到 ~1 单/s），
  实测详情页并发 **18 路 ~14 单/s** 比 8 路快一倍；**再高也没用**（换第二账号也不行，限流是按 IP 的）。

### 领星 OMS 订单（`scripts/oms`，每天 15:00 中国时间）

领星 OMS（`oms.xlwms.com/platform/order/list`）的「平台订单」走**开放接口**取，不是网页爬取。
接口一次给全：**州-市-邮编 + 产品明细 + 店铺 + 跟踪号 + 面单 PDF 链接**（实测字段覆盖率 200/200）。

| 步骤 | 接口 | 拿到什么 |
|---|---|---|
| `list` | `POST /openapi/v1/outboundOrder/pageList` | 出库单号 / 平台单号 / 跟踪号 / 状态（`pageSize` **上限 50**） |
| `detail` | `POST /openapi/v1/outboundOrder/detail` | **店铺名 + 州/市/邮编 + productList + expressList(面单)**（一次最多 **50** 个单号） |
| `build` | —— | `temu-admin/public/data/oms-orders.json`（按店铺/州/款汇总） |

```bash
python scripts/oms/run_oms_orders.py sync --days 2          # 每天跑：抓「当天 + 昨天」
python scripts/oms/run_oms_orders.py backfill --start 2026-08-01
python scripts/oms/run_oms_orders.py stats
python -m pytest scripts/oms/tests -q
```

Windows 计划任务 **`TEMU-OMS-Orders-Daily`**（每天 **15:00 中国时间**）→ `scripts/run_oms_orders.cmd`
→ 日志 `_logs\oms\daily.log`；已登记进 `scripts/data/task_watch.json`（expect_by 22:40）。
重装任务：`powershell -File scripts/oms/register_task.ps1`。

**抓取窗口**（用户 2026-10-16 定的口径：「每天在全部页面下筛选 BHY 和 BEL 店铺，
默认订单同步时间，筛选当天的订单，在 15:00 定时抓取」）：
窗口 = **中国日历日的最近 N 天（含今天）**，与界面日期筛选同一套语义
（接口的 `orderCreateTime` 实测是**中国时间**）。`--window us` 可切回太平洋日。

- 默认 `--days 2`（当天 + 昨天）而不是只抓当天：任务 15:00 跑时当天还没过完，
  只抓 `[今天 00:00, 现在]` 会让**今天 15:00 之后同步进来的单永远丢掉**（下轮窗口已跳到明天）。
- **为什么定 15:00**：它 = `00:00 太平洋时间`，太平洋是全美最晚跨 0 点的时区，
  此刻美国前一天刚过完，所以这一轮同时把「美国前一天」抓全了。
- ⚠ **同一天的数会随时间长大**：出库单是单子从「待处理」推仓后才生成的，
  所以当天抓到的往往不全，后面几天会补齐 —— 这也是要连昨天一起重抓的原因。
  真出现隔了 3 天以上才推仓的单，把 `--days` 调大即可（每天成本约 1 分钟）。

**接口的覆盖边界（2026-10-16 实证）**：开放接口**只给已生成出库单的单**。
拿界面「待处理」列表里 11 个可见订单号去问（`outboundOrderNoList` / `referOrderNoList` /
`thirdOrderNoList` 三种键）**全部 0 条**；接口全量状态只有 已出库/已取消/异常，
**没有「待处理」**。界面「全部」页签里那些还没推仓的平台订单，接口拿不到 ——
要拿只能走网页端（登录有拖动拼图验证码，没法自动登）。
好在这与帕沃尔那份「发货列表」是同一类单，口径一致。

**采集范围**：**只收 BHY + BEL 两家店**（用户 2026-10-15：「只能抓 BHY 和 BEL 店铺的订单」）。
对应 OMS 界面「店铺」筛选里那两项，店铺ID 与帕沃尔那边是同一组
（BHY `634418228526171` / BEL `634418227477419`）。

> ⚠ 别再把这个「全部」理解成「全部店铺」：用户先前说的「要在全部的页面下」指的是
> OMS 界面上的**状态页签「全部」**（相对 待处理/待获取平台面单/处理中/已发货/已取消/异常）。

租户里其实还有 Temu-BK / TK-*(TikTok) / BS(SHEIN) / Savonire_US / BD 等 10 家店，
**不收**（采集时会下钻到它们、再按店铺丢掉）。
技术上没法少发请求：列表接口的 `storeName` 恒为空，只有详情才知道店铺。

**踩过的坑（都写进代码注释了）**：

- **v2 路径不存在**：同一个 appKey 打 `/openapi/v2/...` 一律返回 `11008`。别把它当「无接口权限」——
  **`11008 = 路径不存在`**。这个账号只有 **v1**（`/openapi/v1/outboundOrder/*`）。
- **v1 与 v2 签名完全不同**，别混：v1 是
  `authcode = HMAC-SHA256(appKey + compact_json(data,键排序) + reqTime, appSecret)` 放 **URL query**，
  body 是 `{appKey, reqTime, data}`；v2 才是 `{data, appKey, timestamp, sign}` 那一套。
- **权限校验在签名校验之前**：故意传错 sign 也返回 `11008`，所以「无接口权限」根本不能用来判断签名对不对
  （空 sign 才会报 `11010 sign不能为空`）。
- **列表接口的 `storeName` 恒为空**，只有**详情**里才有 → 想按店铺过滤必须下钻详情。
- **`timeType` 只认 `orderCreateTime`**；其它值（含 `updateTime`/`syncTime`）会被**静默忽略**，
  total 直接回退成全量 —— 所以别指望按「订单同步时间」增量。界面上那个「待处理」状态
  接口里根本没有（接口只有 新建/已取面单/仓库处理中/已出库/已取消/异常）。
- `pageSize` 上限 **50**（传 100 → records 空、total 变 null）；detail 批量上限 **50**（传 100 → `code 10002`）。
- 回填**按月**切窗（逐天要几百次调用且早期全空，且每月落一次盘防中断白跑）；数据只从 **2026-08** 起。
- `register_task.ps1` 必须存成 **UTF-8 with BOM** —— PowerShell 5.1 按 GBK 读无 BOM 的 .ps1，
  中文注释会把解析器搞崩（`string is missing the terminator`）。
- 计划任务的**触发器不能在 XML 里改**：PS 把 `Triggers.CalendarTrigger` 暴露成集合，
  对集合属性赋值不报错也不生效；必须 `Set-ScheduledTask -Trigger` 显式设（且别用反引号续行）。

### 两条数据源怎么合（订单分布 / 数据大屏）

`scripts/pw_orders/run_pw_order_geo.py build` 会把 OMS 的订单并进「订单分布」：

- **去重键 = 参考单号**：帕沃尔的 `ref_no` 与 OMS 的 `platformOrderNo` 都是 TEMU 的 `PO-211-…`；
  已在帕沃尔里的直接跳过（实测重复 37 单），避免重复计数。
- 合并后 **31,651 → 32,580 单**（并入 929），并在 `order-geo.json` 里写明
  `sourceNote` / `omsOrders`，页面「口径」区会显示这段说明。
- 大屏的地图与榜单读同一份 `order-geo.json`，所以自动跟着更新。
- 产物目录别弄混：本域是 `out/pw-orders`，OMS 域是 `out/oms`（`--oms-out` 可改）。

### 近 N 天 · 各州畅销产品 Top3（订单分布页）

用户 2026-10-27 要求：「在订单-产品页面加一个近七天每个州销量前三的产品，最好带搜索」。

- 构建期算好放进 `order-geo.json` 的 **`recent7`**（滚动窗口，默认 7 天，`--recent-days` 可调），
  前端只做筛选，不再二次聚合。
- 结构：`{start, end, days, orderCount, stateCount, states:[{name, code, orders,
  topProducts:[{name, code, orders, qty}] × 3}]}`。
- 排序按**该款在该州的单量**；口径与全量一致：一单多品各算 1 单，
  **同一单里同款不同尺码只算 1 单**（件数则各尺码累加 —— 2026-10-27 顺手修掉了原来只算第一个尺码的 bug）。
- 页面卡片「近 7 天 · 各州畅销产品 Top3」带搜索框，同时匹配**州名 / 州缩写 / 款号数字**
  （所以「哪几个州在卖 102105」直接搜数字就行）。

⚠ **`addtime` 的来源**（决定增量单能不能进窗口）：
老单的日期来自帕沃尔「导出Excel」（`read_exports`）；**增量抓的新单不在导出表里**，
所以 `geo` 步骤顺手把详情页的「添加时间」解析下来（`parse_addtime`），
build 里按 `导出表 addtime → 详情页 addtime` 兜底。
`geo` 的「已完成」判定也因此收紧：只有「有 addtime」或「在导出表里」才算完成，
否则重抓一次（实测只影响增量那 2 千多单，不是全量 3 万）。


### 依赖

```
python 3.12+        # 运行入口脚本
openpyxl            # run_temu_merge / run_temu_sales_push 读 xlsx
```

**紫鸟浏览器是硬依赖**：本域所有数据走店窗口 CDP，不走官方 API。
需要紫鸟客户端本体且目标店铺窗口已打开、`ziniao-cli`
（本地 Bridge `127.0.0.1:9481`）以及全局 SKILL
`ziniao-shared` / `ziniao-store` / `ziniao-cdp-port` / `ziniao-page` / `ziniao-scrape-basics`。

> ⚠️ `scripts/power/data/ziniao_power_map.json` 是「紫鸟账号 ↔ POWER 店铺」对应表，
> 所有入口靠它把 `--store <店后缀>` 解析成窗口名 → store_id → CDP 端口。
> 它含**店铺 ID**（`power_shop_id` / `ziniao_window_store_id`）——不是密钥，
> 但属内部业务标识。换机后第一个要核对的就是它：`ziniao_window_name`
> 必须与目标机紫鸟里的实际窗口名一致。

### 目录层级不可改

脚本按**自身目录**定位数据：`run_temu_*.py` 找 `<同目录>/data/*.json`，
`power/lib/*_driver.py` 找 `<power/lib>/../data/*.json`。
因此 `scripts/power/{run_*.py, lib/, data/, tests/}` 这一层结构不能拆散。

### 自检

```bash
python scripts/power/run_temu_sales.py --help
python scripts/power/run_temu_sales.py --list-stores   # 只读：列店
python scripts/power/run_temu_sales.py --scan          # 只读：探端口
python -m pytest scripts/power/tests -q                # 纯离线单测
```

更多迁移与换机说明见 [`README_迁移.md`](README_迁移.md)。

---

## 三、发布

```bash
# 后台（构建 + 注入密码门禁 + 上传 Cloudflare Pages）
$env:DASH_PASSWORD="***"; python scripts/bel_goods/publish_admin.py --build --production

# 旧版单文件看板
python scripts/bel_goods/publish_cf.py
```

`publish_admin.py` 默认项目名 `temu-dashboard`，同时挂在
`temu-dashboard.pages.dev` 与 `temu-dashboard-bjb.pages.dev`。

---

## 四、仓库说明

本仓库是**内部运营工具**，不含业务数据：

- **不入库**：`data/`、`temu-admin/public/data/`（店铺经营数据）、
  `out/`（Chrome 自动化配置与截图）、`_logs/`、`backups/`、
  `node_modules/`、`dist/`、`.wrangler/`。
- 因此**克隆后直接 `pnpm dev` 看不到真实数据**，
  需先在有数据的环境执行 `scripts/bel_goods/build_api_data.py` 等构建脚本生成 `public/data/*.json`。

`temu-admin` 前端基于开源模板
[vue3-element-admin](https://github.com/youlaitech/vue3-element-admin)（有来开源组织，MIT）
二次开发，已按 TEMU 运营场景改造。

## License

MIT（见 [`temu-admin/LICENSE`](temu-admin/LICENSE)）
