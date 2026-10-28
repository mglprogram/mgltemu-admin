# TEMU 运营后台（temu-admin）

基于 [vue3-element-admin](https://github.com/youlaitech/vue3-element-admin)（Vue 3 + Vite + TypeScript + Element Plus，MIT 协议）
改造的 TEMU 店铺运营后台，用来承接后续不断新增的运营功能模块。

- 线上地址：https://temu-dashboard.pages.dev （同项目别名 https://temu-dashboard-bjb.pages.dev ）
- 与旧的单文件看板（`data/temu_dashboard.html`）**并存**，两套互不影响
- 上游原始说明见 [README.upstream.md](./README.upstream.md)

## 与上游模版的差异

上游是一套「登录 + 后端下发菜单」的完整管理端。本项目**没有后端**，
数据是构建期由 Python 抓取脚本导出的静态 JSON，因此做了三处改造：

| 改造点 | 位置 | 说明 |
|---|---|---|
| 单机模式开关 | `.env.production` → `VITE_STANDALONE=true` | 跳过登录校验、跳过动态菜单请求、跳过 SSE |
| 守卫放行 | `src/router/guards.ts` | 单机模式直接 return，不要求 access token |
| 静态路由 | `src/router/index.ts` | 页面直接挂在静态 `constantRoutes` 下，不依赖后端菜单 |

相关的支持代码：

- `src/utils/standalone.ts` —— 单机模式判定与占位令牌
- `src/stores/user.ts` —— 单机模式返回本地用户信息（顶栏昵称/头像）
- `src/stores/permission.ts` —— 单机模式只用静态路由，标记「路由已生成」
- `src/layouts/components/LayoutToolbar.vue` —— 单机模式隐藏通知中心（它会请求后端接口）

**日后接真实后端**：把 `VITE_STANDALONE` 改成 `false`，恢复登录与后端菜单，
再把 `src/api/temu/*` 里的 `fetchJson` 换成 `request` 即可。

## 目录约定

```
temu-admin/
├─ public/data/            ← 构建期生成的静态数据（不进版本库）
│   ├─ limited.json        ← 限流：两店限流明细 + 重点款告警 + 趋势 + 反复限流排行
│   ├─ flux-boost.json     ← 流量加速：两店 × (已开启/待开启)
│   └─ goods.json          ← 在售款明细：两店「在售中」SPU/SKC/SKU 全量
├─ src/api/temu/           ← 业务数据接口层（按模块建目录）
│   ├─ limited/{index.ts,types.ts}
│   ├─ flux-boost/{index.ts,types.ts}
│   └─ goods/{index.ts,types.ts}
├─ src/views/temu/         ← 业务页面（按模块建目录）
│   ├─ limited/index.vue
│   ├─ flux-boost/index.vue
│   └─ goods/index.vue
└─ src/router/index.ts     ← 静态路由注册（新增页面改这里）
```

当前菜单结构：

```
首页
TEMU 运营
├─ 限流监控     /temu/limited
├─ 流量加速     /temu/flux-boost
└─ 在售款明细   /temu/on-sale        ← 页面顶部置顶「在售中」统计栏 + SPU/货号搜索
```

## 在售款明细数据怎么来的

两条通道，`build_goods_data.py` **自动挑更全的一条**（可用 `--source export|scrape` 强制）：

| 通道 | 入口 | 产物 | 说明 |
|---|---|---|---|
| ① 官方导出 | `scripts/goods/run_goods_export.py --store all` | `data/goods/<store>_goods_export.xlsx` | 商品列表页「下载查询结果」→ 勾「全部商品信息」→ 导出。**一次请求**，字段由平台保证 |
| ② 接口抓取 | `scripts/goods/run_goods_on_sale.py --store all` | `data/goods/<store>_goods_raw.json` | CDP 抓 `product/skc/pageQuery` 响应（含全部状态，按 `skcTopStatus=100` 过滤），字段更细 |

```powershell
# 取数（两条任选，或都跑）
python scripts/goods/run_goods_export.py --store all      # 官方导出
python scripts/goods/run_goods_on_sale.py --store all     # 接口抓取

# 规范化成前端要的 goods.json
python scripts/goods/build_goods_data.py
```

2026-10-12 实测两条通道**数量完全一致**，且与页面 tab 计数逐一吻合：
BEL 116 SKC / BHY 88 SKC（页面显示「在售中 116 / 88」）⇒ 合计 204 SKC · 194 SPU · 1,025 SKU。

> 抓取侧两个坑（都写进代码注释了）：`agentseller.temu.com` 是 qiankun 微前端，页面顶层改写
> `fetch/XMLHttpRequest` **抓不到子应用请求**（要用 CDP `Network.*`）；页面内同步 XHR 直调接口
> 缺风控头会 403。分页器只渲染当前附近几页，所以翻页要顺序点「下一页」而不是点页码。

### 每天 08:00 自动日更

Windows 计划任务 **`TEMU-Goods-OnSale-Daily`**（`tasks.json` 里登记，`manage.py list` 可查），
入口 `scripts\tasks\run_temu_goods_onsale.cmd`，日志 `_logs\goods-onsale\daily.log`：

```
08:00  run_goods_export.py --store all     官方导出两店「在售中」xlsx
       build_goods_data.py                 刷新 goods.json（含低库存清单）
       publish_admin.py --build --production  构建 + 发布 mgltemu.us.ci
```

导出失败（rc≠0）时**跳过发布**，线上保持上一次数据；`DASH_PASSWORD` 从用户级环境变量取。
`scripts/data/task_watch.json` 里也登记了它（`expect_by=08:40`），漏跑/失败会推飞书。

### 低库存（款·尺码 ≤ 10）+ 可加库存判定

`build_goods_data.py` 在构建期就算好低库存清单，阈值只写在 `LOW_STOCK_THRESHOLD`（当前 10，
口径是 **≤**，用户 2026-10-12 明确「小于等于10」）：

- `goods.json → lowStock.summary`：合计（几个款·尺码、涉及几个款、0 库存几个、分档 0/1-4/5-9）+ `top`；
- `goods.json → stores.<店>.lowStock.rows`：完整明细（款号 / 尺码 / 颜色 / TEMU库存 / 申报价 / SKU 货号），按库存升序；
- 面板：`在售款明细` 页切「低库存款·尺码」视图（也支持 `#/temu/on-sale?view=low` 直达）；
- 首页：`在售款 · 款-库存提醒` 卡片（断货→红、低库存→橙、充足→绿；点卡片直达低库存视图）。

在此之上，`scripts/goods/enrich_restock.py` 给每条低库存**判定能不能加库存**（口径在
`scripts/power/data/temu_restock.json`）：

| 数据源 | 取什么 | 用途 |
|---|---|---|
| 海外仓库存看板（warehouse-probe，`temu_stock_topup` 抠内嵌 SNAP） | `代发库存` / `近7天日均` / `代发途中` | 仓内现货、日均 |
| 飞书「领星到货计划甘特」 | 未入仓批次的 `发货批次` / `数量` / `到货日` / `渠道` / `入库单号` | 在途 + 到货日 |
| 飞书「领星库存覆盖甘特」 | `可用库存` / `在途件数` / `日耗` / `空窗天数` / `缺口件数` / `风险` | 日耗口径、风险 |
| 飞书「领星库存总表」 | `代发库存` / `代发途中` | 看板缺该款时的兜底 |

```
建议增加量 = round(日均 × 目标天数30) − 仓内现货(折 TEMU 单位) − TEMU 当前可售
结论：可加库存 / 仓里有货(未定量) / 仓里现货不足 / 待到货 / 需补货 / 未匹配
```

- 面板上多出「仓内现货 / 日均 / 建议加库存 / 结论 / 到货日」五列，并有**结论筛选**；
- 首页卡片显示「可加库存 N」和建议加量 Top4；
- 看板或飞书取不到时**不阻断发布**：面板仍出低库存清单，只是没有判定列。

> ⚠ `enrich_restock.py` 只吃 `build_goods_data.py` 刚生成的干净 `goods.json`（它会给每行写
> `whStock` 等字段）。别在已增强的文件上重复跑。




## 流量加速数据怎么来的

抓取脚本 `scripts/bel_goods/fetch_flux_all.py`（**全程只读**）：

```powershell
# 分店 × 分板块抓取，产物落 data/flux/<store>_<section>.json
python scripts/bel_goods/fetch_flux_all.py --store BEL --section accelerating
python scripts/bel_goods/fetch_flux_all.py --store BEL --section pending
python scripts/bel_goods/fetch_flux_all.py --store BHY --section accelerating
python scripts/bel_goods/fetch_flux_all.py --store BHY --section pending

# 合并成前端要的一份 JSON
python scripts/bel_goods/build_api_data.py --flux-only
```

| 板块 | 快速筛选 | 取数方式 |
|---|---|---|
| `accelerating` 已开启 | 流量加速中 | 逐 SPU 开「查看效果」抽屉 → 尺码级原日常申报价 / 调整后申报价 |
| `pending` 待开启 | 流量加速器待开启 | 直接读列表行（流量/转化指标 + 可选优化方式），无尺码价 |

**写操作红线**：脚本只点「查看效果」「查看详情」和分页；
「立即开启」「报名流量加速器」「一键提效」这类入口只作为文本记录下来展示，
**从不点击**。

抓取前需要两个 TEMU 店窗口处于已登录态（登录态会掉，掉了要重新授权）。

## 每周四 06:00 自动更新

Windows 计划任务 **`TEMU流量加速周更`**，入口 `scripts\tasks\run_temu_flux_weekly.cmd`，
日志落在 `_logs\temu-flux\weekly-YYYYMMDD.log`。

它按顺序做：

1. `run_flux_weekly.py` — 开窗 → **登录态失效则自动授权** → 逐店逐板块抓取
2. `build_api_data.py --flux-only` — 合并成 `public/data/flux-boost.json`
3. `publish_admin.py --build --production` — 构建 + 发布到 Cloudflare Pages

手动跑一次（不发布）：

```powershell
python scripts/bel_goods/run_flux_weekly.py --no-deploy
python scripts/bel_goods/run_flux_weekly.py --store BHY     # 只跑一个店
```

### 数据安全设计（都是踩过坑之后加的）

| 情况 | 行为 |
|---|---|
| 抓取跑到错误页面 / 解析到 0 条 | **不写产物文件**，返回失败，保留上一次数据 |
| 抓取中途登录态失效 | 中止该板块、保留已抓部分，重新登录后**自动补跑一次** |
| 某次抓取大面积缺价 | 与**旧产物按 SPU 合并**回填价格，不整体覆盖 |
| 合并时发现某板块产物为空 | 回退到旧格式产物（`<store>_flux_boost_price.json`） |
| 所有板块都失败 | **跳过发布**，线上保持上一次的数据 |

### 从历史部署恢复数据

Cloudflare Pages 保留历史部署，每个部署有独立 URL。万一产物被写坏，
可以从旧部署把合并后的数据取回来并转回抓取格式：

```powershell
python scripts/bel_goods/restore_flux.py https://<部署ID>.temu-dashboard-bjb.pages.dev BHY
```

### 登录态说明

TEMU 登录态**随时可能失效**（抓取途中也会）。周更脚本会自动走授权：
点「中国地区·商家中心」→（第 1 步）授权登录 →（第 2 步）勾条款 + 确认授权并前往。

**授权是两步的**，且认证页的区域入口**只认真实鼠标事件**（JS `.click()` 无效），
必须走 CDP；整组鼠标事件还要在**同一条 websocket** 上发，否则浏览器不合成点击。
这些都在 `scripts/bel_goods/flux_auth.py` 里处理。

自动授权**不是 100% 可靠**（TEMU 的页面跳转有随机性）。跑失败时日志会明确写原因，
线上数据不受影响；必要时手动跑一次 `python scripts/bel_goods/flux_auth.py TM-BHY-YJF`。

## 新增一个页面的步骤

1. **取数**：在 `scripts/bel_goods/build_api_data.py` 里加一个 `build_xxx()`
   并 `write("xxx.json", ...)`，数据落进 `public/data/`。
2. **接口层**：`src/api/temu/xxx/{index.ts,types.ts}`，照着 `flux-boost` 抄。
3. **页面**：`src/views/temu/xxx/index.vue`。
4. **路由**：`src/router/index.ts` 的 `constantRoutes` → `/` 的 children 里加一条。
5. **构建发布**：`python scripts/bel_goods/publish_admin.py --build`

## 构建与发布

```powershell
# 只本地构建（含数据导出 + 密码门禁注入）
$env:DASH_PASSWORD="你的密码"
python scripts/bel_goods/publish_admin.py --build-only

# 本地预览
cd temu-admin; pnpm preview

# 发布到预览分支（不动线上）
python scripts/bel_goods/publish_admin.py --build

# 发布到线上
python scripts/bel_goods/publish_admin.py --build --production
```

前置：Cloudflare 凭证。本机用**用户级环境变量 `CLOUDFLARE_API_TOKEN`**
（`publish_admin.py` 会自动读取并注入 wrangler；该 token 只有 Pages 权限，
所以 `wrangler login` 的 OAuth 交互登录会被它挡住，不必再走 OAuth）。

## 自定义域名

目标域名：`mgltemu.us.ci`（DNSHE 免费域名，与已生效的 `miaoglwudi.cc.cd` 同一家服务）。

**关键前提**：域名所属的「区」（zone）必须先加入同一个 Cloudflare 账户，
否则 Pages 上会一直停在 `pending`、`zone_tag` 为空，永远不会生效。

三步：

1. **Cloudflare 加站点**：控制台 → Add a site → `us.ci` → Free 套餐，
   记下它给的两个 NS（本账户现成的一对是 `bayan.ns.cloudflare.com` / `coraline.ns.cloudflare.com`）
2. **DNSHE 改 NS**：登录 DNSHE 后台，把 `us.ci` 的 NS 改成上面两个
3. **绑到 Pages**（可用脚本）：
   ```powershell
   python scripts/bel_goods/setup_domain.py --add mgltemu.us.ci
   python scripts/bel_goods/setup_domain.py --check mgltemu.us.ci   # 查解析与绑定状态
   python scripts/bel_goods/setup_domain.py --list
   ```

> 注：本机 API Token **没有 `zone:create` 权限**，第 1 步无法用脚本做，必须控制台点。

## 为什么有密码门禁

面板含店铺货号、库存、申报价这类经营数据，直接裸奔在公网不可接受。
`publish_admin.py` 在构建后往 `dist/index.html` 注入一层纯前端密码门禁
（与旧看板同款做法，只挡随手访问，不是真正的鉴权）。

## 环境要求

- Node.js ≥ 20.19（本项目实测 v24.19）
- pnpm（`corepack enable --install-directory %APPDATA%\npm`）
- Python 3（跑抓取与数据导出脚本）
