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
| `/dashboard` | 数据看板 | 概览指标 |
| `/temu/on-sale` | 在售款明细 | 货号 / 库存 / 申报价明细 |
| `/temu/limited` | 限流监控 | 限流款跟踪 |
| `/temu/flux-boost` | 流量加速 | 流量投放建议 |
| `/temu/presale` | 销量-库存 | 补货 / 预售判断 |
| `/automation` | 自动化任务 | 定时任务状态 |
| `/data-screen` | 数据大屏 | 全屏展示（`hidden`） |

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
└─ dsh/                     # DSH 任务集成
```

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
