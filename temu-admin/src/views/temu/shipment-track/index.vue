<!--
  货件跟踪（以领星入库单号为主键 · 含到港→上架预测）

  2026-11-02 改版（用户要求）
  --------------------------
  「其他单号改成入库单号，因为可以通过领星查，所以直接从领星每天抓取入库单号跟踪数据更全」
  「根据到港日期和签收日期帮我预测到港之后，平均几天能签收送仓上架」

  所以这一页现在是：
    · 主键 = 领星入库单号（IB106… 美西 / IB003… 加州3仓），每天从领星全量拉
    · 到港 = 恒信【到港】节点（帕沃尔那批恒信不覆盖，回落到历史表的到港日期）
    · 预测 = 到港日 + 该渠道「到港→上架」中位天数（以星 8 / 美森 4 / 盐田 11）
    · 超期 = 今天已超过预测上架日、OMS 还没到「已上架」

  数据来自 public/data/shipment-track.json（scripts/goods/fetch_shipment_track.py）。
-->
<template>
  <div class="sh-page app-container">
    <!-- 页头 -->
    <div class="sh-header">
      <div>
        <h2 class="sh-header__title">货件跟踪 · {{ data.summary.total }} 票</h2>
        <p class="sh-header__desc">
          <b>LM739</b> 走领星 OMS 入库单号（IB106…），到港取恒信【到港】节点；
          <b>14450</b> 的领星已停用，改从<b>帕沃尔头程</b>抓（参考单号 14450…，含完整轨迹与预计到货）。
          <b>预测上架日 = 到港日（或帕沃尔 ETA）+ 该渠道历史中位天数</b>（以星 {{ leadOf("以星") }} 天 /
          美森 {{ leadOf("美森") }} 天 / 盐田 {{ leadOf("盐田") }} 天）。超过预测日还没上架的单独挑出来。
        </p>
      </div>
      <div class="sh-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          抓取 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 预测口径 -->
    <el-alert type="info" :closable="false" show-icon class="sh-alert" title="到港 → 送仓上架 的预测口径">
      <div class="sh-alert__body">
        <div>
          样本 <b>{{ data.leadTime.overall.n }}</b> 张（历史追踪表的「到港日期→签收日期」+
          领星「恒信到港→上架时间」），全体中位 <b>{{ data.leadTime.overall.median }}</b> 天，
          P80 {{ data.leadTime.overall.p80 }} 天，范围 {{ data.leadTime.overall.min }}~{{ data.leadTime.overall.max }} 天。
        </div>
        <div>
          按渠道：<span v-for="(v, k) in data.leadTime.channels" :key="k" class="sh-alert__ch">
            {{ chLabel(String(k)) }} 中位 <b>{{ v.median }}</b> 天（n={{ v.n }}）
          </span>
        </div>
        <div class="sh-dim">
          渠道取领星入库单的 remark（以星/盐田/美森），空则按全体中位 {{ data.leadTime.fallback }} 天算。
          {{ data.leadTime.note }}
        </div>
      </div>
    </el-alert>

    <!-- 合计 -->
    <el-row :gutter="12" class="sh-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '12px 14px' }" class="sh-stat">
          <div class="sh-stat__label">{{ s.label }}</div>
          <div class="sh-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="sh-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 筛选 -->
    <div class="sh-bar">
      <el-radio-group v-model="viewPick" size="default">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="overdue">超期未上架</el-radio-button>
        <el-radio-button value="arrived">已到港待上架</el-radio-button>
        <el-radio-button value="waiting">未到港</el-radio-button>
        <!-- 近 30 天已上架（2026-11-02 用户要求：跟大屏「近3天到港→上架」对齐，这里给 30 天） -->
        <el-radio-button value="shelved">已上架</el-radio-button>
      </el-radio-group>
      <el-select v-model="accountPick" size="default" style="width: 170px">
        <el-option label="全部账号/仓库" value="all" />
        <el-option v-for="a in accountOpts" :key="a.value" :label="a.label" :value="a.value" />
      </el-select>
      <el-select v-model="chPick" size="default" style="width: 150px">
        <el-option label="全部渠道" value="all" />
        <el-option v-for="(n, k) in data.summary.byChannel" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-select v-model="stPick" size="default" style="width: 140px">
        <el-option label="全部 OMS 状态" value="all" />
        <el-option v-for="(n, k) in data.summary.byStatus" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 入库单号 / 产品名 / SKU / 款号"
        style="width: 260px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-switch v-model="onlyIssue" active-text="只看需关注" size="default" />
      <el-button size="default" plain @click="copyTodo">复制清单</el-button>
    </div>

    <!-- 主体表 -->
    <el-card shadow="never" class="sh-card">
      <template #header>
        <div class="sh-card__head">
          <span class="sh-card__title">
            {{ viewTitle }} · {{ filtered.length }} 张
            <span class="sh-card__sub">（展开行看产品·尺码·数量，点「轨迹」看全链路）</span>
          </span>
          <span class="sh-hint">
            <template v-if="viewPick === 'shelved'">
              近 {{ SHELVED_DAYS }} 天已上架 <b>{{ filtered.length }}</b> 票
            </template>
            <template v-else>
              已到港未上架 <b>{{ filteredArrivedWaiting }}</b> ｜ 超期 <b>{{ filteredOverdue }}</b>
            </template>
          </span>
        </div>
      </template>

      <el-table
        :data="paged"
        size="small"
        stripe
        border
        row-key="inboundNo"
        :row-class-name="rowClass"
        class="sh-table"
      >
        <!-- 展开：产品明细 -->
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="sh-exp">
              <div class="sh-exp__head">
                <b>{{ row.inboundNo }}</b>
                <el-tag :type="omsTag(row.status)" size="small" effect="light">{{ row.statusText }}</el-tag>
                <span class="sh-dim">
                  {{ row.whName }} ｜ {{ row.skuCount }} 个 SKU ｜ 共
                  <b>{{ num(row.totalQty) }}</b> 件
                  <template v-if="row.omsAccount"> ｜ 账号 {{ row.account }}</template>
                  <template v-if="row.expectedDate"> ｜ OMS 预计到货 {{ row.expectedDate }}</template>
                  <template v-if="row.receivedTime"> ｜ 收货 {{ row.receivedTime.slice(0, 10) }}</template>
                  <template v-if="row.shelfTime"> ｜ 上架 {{ row.shelfTime.slice(0, 10) }}</template>
                </span>
              </div>

              <el-table v-if="row.skus.length" :data="row.skus" size="small" border class="sh-exp__t">
                <el-table-column prop="productName" label="产品名称" min-width="230" />
                <el-table-column prop="style" label="款号 / 套系" min-width="180" />
                <el-table-column prop="size" label="尺码" width="80" align="center" />
                <el-table-column prop="qty" label="数量" width="90" align="right">
                  <template #default="{ row: s }"><b>{{ num(s.qty) }}</b></template>
                </el-table-column>
                <el-table-column prop="receivedQty" label="已收货" width="90" align="right">
                  <template #default="{ row: s }">{{ num(s.receivedQty) }}</template>
                </el-table-column>
                <el-table-column prop="sku" label="SKU" min-width="150" />
              </el-table>
              <el-empty v-else description="这张入库单没有 SKU 明细" :image-size="50" />
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="inboundNo" label="入库单号" width="134" fixed>
          <template #default="{ row }">
            <span class="sh-mono">{{ row.inboundNo }}</span>
          </template>
        </el-table-column>
        <el-table-column label="OMS" width="84" align="center" fixed>
          <template #default="{ row }">
            <el-tag :type="omsTag(row.status)" size="small" effect="light">{{ row.statusText }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="到港日期" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.arriveDate">{{ row.arriveDate }}</span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="到港→上架 / 已等" width="86" align="right">
          <template #default="{ row }">
            <!-- 已上架的给「到港→上架 实际用了几天」；没上架才是「已经等了几天」 -->
            <template v-if="row.status === 4 && shelfDays(asRow(row)) != null">
              <span :class="shelfDays(asRow(row))! <= 8 ? 'sh-ok' : 'sh-warn'">{{ shelfDays(asRow(row)) }} 天</span>
              <div class="sh-dim sh-tiny">到港→上架</div>
            </template>
            <span v-else-if="row.waitDays != null" :class="{ 'sh-bad': row.overdueDays > 0 }">
              {{ row.waitDays }} 天
            </span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="预测 / 上架" width="96" align="center">
          <template #default="{ row }">
            <!-- 已经上架的就直接给**实际**上架日期，别再显示预测（那已经是过去式了） -->
            <template v-if="row.status === 4 && row.shelfTime">
              <div class="sh-ok">{{ String(row.shelfTime).slice(0, 10) }}</div>
              <div class="sh-dim sh-tiny">实际上架</div>
            </template>
            <template v-else-if="row.predictedShelf">
              <div>{{ row.predictedShelf }}</div>
              <div class="sh-dim sh-tiny">{{ row.predictedBasis }} + {{ row.leadDays }} 天</div>
            </template>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="超期" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.overdueDays > 0" type="danger" size="small" effect="dark">
              +{{ row.overdueDays }} 天
            </el-tag>
            <span v-else-if="row.predictedShelf && row.status !== 4" class="sh-dim">未超</span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="渠道" width="80">
          <template #default="{ row }">
            <span :class="row.channel ? '' : 'sh-dim'">{{ row.channelText }}</span>
          </template>
        </el-table-column>
        <el-table-column label="物流最新节点" min-width="170">
          <template #default="{ row }">
            <template v-if="row.logistics.ok">
              <div class="sh-node" :class="{ 'is-bad': row.logistics.currentAbnormal }">
                {{ row.logistics.latest.info || "—" }}
              </div>
              <div class="sh-node__time">{{ row.logistics.latest.time }}</div>
            </template>
            <!-- 14450 已停用领星（2026-11-02），改从帕沃尔头程抓，那边有完整轨迹，
                 所以正常会走上面的分支。真没轨迹时才落到这句说明。 -->
            <span v-else class="sh-dim">
              {{ row.source === "帕沃尔头程" ? "帕沃尔无可查轨迹" : "恒信无此单" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="产品 · 尺码 · 数量" min-width="160">
          <template #default="{ row }">
            <template v-if="row.skus.length">
              <span v-for="s in row.skus" :key="s.sku" class="sh-sku">
                {{ s.productName }} <b>×{{ num(s.qty) }}</b>
              </span>
            </template>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="whName" label="仓库" width="100" show-overflow-tooltip />
        <el-table-column label="需关注" width="110">
          <template #default="{ row }">
            <div v-if="row.issues.length" class="sh-issues">
              <el-tooltip
                v-for="i in row.issues"
                :key="i"
                :content="i"
                placement="top"
                :show-after="300"
              >
                <el-tag :type="issueTag(i)" size="small" effect="plain" class="sh-issue">
                  {{ i }}
                </el-tag>
              </el-tooltip>
            </div>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="76" align="center">
          <template #default="{ row }">
            <!-- 有轨迹 → 站内抽屉看全链路；没轨迹 → 直接开来源页（恒信查件 / 帕沃尔订单详情） -->
            <el-button
              v-if="row.logistics.traces.length"
              link
              type="primary"
              size="small"
              @click="openTraces(asRow(row))"
            >
              轨迹
            </el-button>
            <el-link
              v-else-if="row.trackUrl"
              type="primary"
              :underline="false"
              :href="row.trackUrl"
              target="_blank"
              style="font-size: 12px"
            >
              {{ row.source === "帕沃尔头程" ? "帕沃尔" : "去查件" }}
            </el-link>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="sh-pagebar">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="filtered.length"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <!-- 全链路轨迹 -->
    <el-drawer v-model="traceOpen" :title="`物流轨迹 · ${traceRow?.inboundNo || ''}`" size="560px">
      <template v-if="traceRow">
        <div class="sh-drawer__meta">
          <el-tag :type="logTag(traceRow.logistics.status)" size="small" effect="light">
            {{ traceRow.logistics.statusText || "无轨迹" }}
          </el-tag>
          <span class="sh-dim">
            <template v-if="traceRow.logistics.shipmentId">恒信单 {{ traceRow.logistics.shipmentId }} ｜ </template>
            <template v-if="traceRow.logistics.parcels != null">{{ traceRow.logistics.parcels }} 件 ｜ </template>
            到港 {{ traceRow.arriveDate || "—" }}
            <template v-if="traceRow.predictedShelf"> ｜ 预测上架 {{ traceRow.predictedShelf }}</template>
          </span>
        </div>
        <el-timeline v-if="traceRow.logistics.traces.length" class="sh-timeline">
          <el-timeline-item
            v-for="(t, i) in traceRow.logistics.traces"
            :key="i"
            :timestamp="t.time"
            :type="i === 0 ? 'primary' : ''"
            :hollow="i !== 0"
            placement="top"
          >
            <span :class="{ 'sh-bad': i === 0 && traceRow.logistics.currentAbnormal }">{{ t.info }}</span>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="恒信查不到这票的轨迹" :image-size="60" />
      </template>
    </el-drawer>

    <!-- 口径 -->
    <el-card shadow="never" class="sh-card sh-rule">
      <template #header>
        <span class="sh-card__title">口径与数据源</span>
      </template>
      <div class="sh-rule__body">
        <p>
          <b>主键</b>：领星入库单号。LM739（ZOO WAREHOUSE ⑥美西）是 <code>IB106…</code>，
          14450（帕沃尔 加州3仓）是 <code>IB003…</code>。每天从领星 OpenAPI 全量拉，
          不再依赖货代的 Excel。
        </p>
        <p>
          <b>到港</b>：恒信轨迹里的 <code>【到港】</code> 节点（它带真实到港时间）。
          恒信只覆盖 LM739；帕沃尔那批没有恒信轨迹，到港日期回落到历史追踪表，
          仍没有就留空，只用 OMS 状态看。
        </p>
        <p>
          <b>预测上架</b> = 到港日 + 该渠道历史中位天数。渠道取入库单 remark（以星/盐田/美森）。
          没有到港日期的，用 OMS「预计到货日」做基准，行内会标出基准来源。
        </p>
        <p>
          <b>超期</b>：只对<b>真的到港了</b>的单判 —— 今天超过预测上架日、且 OMS 还没到「已上架」。
          还没到港的单不参与超期判定（另有「超预计到货日未到港」单独提示）。
        </p>
        <p>
          <b>生成</b>：<code>python scripts/goods/fetch_shipment_track.py</code> ——
          拉两个账号的入库单 → 查恒信（只查 LM739）→ 读历史表标定 → 落成
          <code>shipment-track.json</code>。定时任务 <code>TEMU-Shipment-Track-Daily</code>。
        </p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import ShipmentTrackAPI from "@/api/temu/shipment-track";
  import type { ShipmentRow, ShipmentTrackData } from "@/api/temu/shipment-track";

  defineOptions({ name: "TemuShipmentTrack" });

  const loading = ref(false);
  const data = ref<ShipmentTrackData>({
    generatedAt: "",
    source: { primary: "", accounts: [], tracking: "", calibration: "" },
    leadTime: {
      overall: { n: 0, mean: null, median: 8, p80: null, min: null, max: null },
      channels: {},
      fallback: 8,
      note: "",
    },
    summary: {
      total: 0, waiting: 0, onShelf: 0, receiving: 0, received: 0, canceled: 0,
      arrived: 0, notArrived: 0, arrivedWaiting: 0, overdue: 0, abnormal: 0, noTrace: 0,
      issues: 0, totalQty: 0, waitingQty: 0, byStatus: {}, byChannel: {},
    },
    rows: [],
  });

  /**
   * 默认视角 = 全部。
   * （2026-11-04 用户要求：原来默认停在「超期未上架」，从库存跟踪的「在途」
   * 点过来时只看得到超期那几票，看着像"搜错了"。默认全量，想看超期再点一下。）
   */
  const viewPick = ref<"all" | "overdue" | "arrived" | "waiting" | "shelved">("all");
  const accountPick = ref("all");
  const chPick = ref("all");
  const stPick = ref("all");
  const keyword = ref("");
  const onlyIssue = ref(false);

  const page = ref(1);
  const pageSize = ref(50);

  const traceOpen = ref(false);
  const traceRow = ref<ShipmentRow | null>(null);

  async function load() {
    loading.value = true;
    try {
      data.value = await ShipmentTrackAPI.getShipmentTrack();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载货件跟踪数据失败");
    } finally {
      loading.value = false;
    }
  }

  /**
   * 支持用 ?view=xxx 直达某个视图。
   * 大屏面板上「近 3 天到港 → 上架」点进来就是 ?view=shelved（已上架），
   * 不然用户落地在「超期未上架」，还得自己再点一下。
   */
  const route = useRoute();
  const VIEWS = ["all", "overdue", "arrived", "waiting", "shelved"] as const;

  /**
   * 从 **URL 本身** 解析 query（而不是只信 useRoute）。
   *
   * ⚠ 2026-11-04 真正的根因（第四轮才定位到）：
   *   ① 本项目的 keep-alive 缓存键是 `route.fullPath` + 外层 `:key="route.fullPath"`
   *      （layouts/components/LayoutMain.vue），所以「带 query」和不带 query 是**两个实例**；
   *   ② 在 hash 模式下，页面之间复用同一个标签时 `useRoute().query` 有时仍是旧对象，
   *      组件里读到的 `kw` 是空 —— 搜索框自然还是上一次的词。
   * 解决办法：直接读 `location.hash` 里的 query，谁也不用重新挂载或重启。
   * 这一段不依赖 route 对象，`onMounted` / `onActivated` / `hashchange` 都会调用它。
   */
  function rawQuery(): Record<string, string> {
    const h = String(window.location.hash || "");
    const i = h.indexOf("?");
    const out: Record<string, string> = {};
    if (i < 0) return out;
    for (const part of h.slice(i + 1).split("&")) {
      if (!part) continue;
      const [k, v = ""] = part.split("=");
      out[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, " "));
    }
    return out;
  }

  function applyViewFromQuery() {
    const v = String(route.query.view || rawQuery().view || "");
    if ((VIEWS as readonly string[]).includes(v)) {
      viewPick.value = v as (typeof VIEWS)[number];
    }
  }

  /**
   * `?kw=` 关键词落地：库存跟踪页的「在途」列点过来时带的款号/套系/尺码，
   * 落地直接筛到那几票货，不用再手动搜一遍。
   * （2026-11-04 用户要求：在途列不要悬浮提示，直接跳到货件跟踪看这一款。）
   */
  function applyKwFromQuery() {
    const fromRoute = String(route.query.kw || "").trim();
    const v = fromRoute || String(rawQuery().kw || "").trim();
    // 没有 kw 参数时也清空：从别处带着旧词再进来，不能残留上一次的搜索
    if (v !== keyword.value) {
      keyword.value = v;
      page.value = 1;
    }
  }

  onMounted(() => {
    applyViewFromQuery();
    applyKwFromQuery();
    load();
  });

  /**
   * ⚠ 2026-11-04 实测定案：这个项目的 keep-alive 缓存键是 **route.fullPath**，
   * 而且外面还套了一层 `:key="route.fullPath"` 的 wrapper（见 layouts/components/LayoutMain.vue）。
   *
   *   · 从菜单进（fullPath 无 query）→ 缓存 A，实例 1
   *   · 从「在途」带 ?view=all&kw=… 进来 → fullPath 变了 → 生成**另一个 wrapper/实例 2**
   *   · 返回菜单再点货件跟踪 → fullPath 回到无 query → 命中缓存 A → 显示的是**实例 1**
   *     （它还保留着上一次的 keyword），而 inst 1 的 onActivated 只会重放它自己的旧 query
   *
   * 这就是「搜索框一直显示上一次的词」。修法：激活时**以当前路由为准**重新套用，
   * 而不是依赖组件内部保存的状态；同时无 kw 时清空。
   */
  onActivated(() => {
    applyViewFromQuery();
    applyKwFromQuery();
  });

  /** hash 里 query 变了也要跟上（同标签页内 $router.push 不会重建实例） */
  const onHashChange = () => {
    applyViewFromQuery();
    applyKwFromQuery();
  };
  onMounted(() => window.addEventListener("hashchange", onHashChange));
  onUnmounted(() => window.removeEventListener("hashchange", onHashChange));

  watch(
    () => [route.query.view, route.query.kw],
    () => {
      applyViewFromQuery();
      applyKwFromQuery();
    }
  );

  // ---------------------------------------------------------------- 数字与标签
  const num = (v?: number | null) => (v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN"));

  /** el-table 插槽给的是宽类型 DefaultRow，统一按 ShipmentRow 接 */
  const asRow = (row: unknown) => row as ShipmentRow;

  const logTag = (s: string) =>
    s === "delivered" ? "success" : s === "in_transit" || s === "picked" ? "warning" : s ? "info" : "danger";

  const omsTag = (s?: number) =>
    s === 4 ? "success" : s === 1 ? "warning" : s === 5 ? "danger" : s === 2 || s === 3 ? "primary" : "info";

  const issueTag = (i: string) =>
    i.includes("超期") ? "danger" : i.includes("取消") ? "danger" : i.includes("异常") ? "warning"
      : i.includes("未上架") ? "warning" : "info";

  const chLabel = (k: string) =>
    k === "以星" ? "以星 ZIMKP" : k === "盐田" ? "盐田 TRK" : k === "美森" ? "美森 MLLKP" : "未知";

  const leadOf = (ch: string) => data.value.leadTime.channels[ch]?.median ?? data.value.leadTime.fallback;

  /** 超期整行标红，扫表时不用逐格读 */
  const rowClass = ({ row }: { row: ShipmentRow }) =>
    row.overdueDays > 0 ? "is-alert" : "";

  // ---------------------------------------------------------------- 指标
  const stats = computed(() => {
    const s = data.value.summary;
    return [
      { label: "入库单总数", value: num(s.total), hint: `合计 ${num(s.totalQty)} 件`, color: "#0f766e" },
      {
        label: "超期未上架",
        value: num(s.overdue),
        hint: "过了预测上架日还没上架 ← 先看这个",
        color: s.overdue ? "#b91c1c" : "#16a34a",
      },
      {
        label: "已到港未上架",
        value: num(s.arrivedWaiting),
        hint: `已到港 ${s.arrived} ｜ 未到港 ${s.notArrived}`,
        color: "#d97706",
      },
      {
        label: "待入库",
        value: num(s.waiting),
        hint: `其中收货中 ${s.receiving}`,
        color: "#e6a23c",
      },
      { label: "已上架", value: num(s.onShelf), hint: "OMS 状态已上架", color: "#16a34a" },
      {
        label: "物流异常",
        value: num(s.abnormal),
        hint: "最新节点是延迟 / 转船 / 查验",
        color: s.abnormal ? "#dc2626" : "#16a34a",
      },
      { label: "已取消", value: num(s.canceled), hint: "OMS 已取消的入库单", color: "#64748b" },
      { label: "需关注", value: num(s.issues), hint: "超期 / 异常 / 已取消 / 未到港超期", color: "#7c3aed" },
    ];
  });

  const accountOpts = computed(() => {
    const m: Record<string, string> = {};
    for (const r of data.value.rows) {
      if (!r.account) continue;
      // 14450 已经不走领星了（2026-11-02 领星停用，改抓帕沃尔头程），
      // 标签直接点明来源，免得看的人以为 OMS 还在更新
      const tag = r.source === "帕沃尔头程" ? "帕沃尔头程" : r.whName || r.account;
      m[r.account] = tag;
    }
    return Object.keys(m).map((k) => ({ value: k, label: `${k} · ${m[k]}` }));
  });

  /** 已上架的单：到港 → 上架 实际用了几天（没有到港日就给 null） */
  function shelfDays(r: ShipmentRow): number | null {
    if (!r.shelfTime || !r.arriveDate) return null;
    const a = new Date(`${String(r.arriveDate).slice(0, 10)}T00:00:00`);
    const s = new Date(`${String(r.shelfTime).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(a.getTime()) || Number.isNaN(s.getTime())) return null;
    return Math.round((s.getTime() - a.getTime()) / 86400000);
  }

  /** 已上架视图的回看窗口：近 30 天（用户 2026-11-02 明确「抓取近30的已上架的单号」） */
  const SHELVED_DAYS = 30;

  /**
   * 上架日期是否落在近 N 天内（含今天）。
   * 按**上架日**算 —— 那才是「什么时候真的进仓能卖」。
   */
  function shelvedWithin(r: ShipmentRow, days: number): boolean {
    if (!r.shelfTime) return false;
    const d = new Date(`${String(r.shelfTime).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(d.getTime())) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const back = Math.round((today.getTime() - d.getTime()) / 86400000);
    return back >= 0 && back < days;
  }

  // ---------------------------------------------------------------- 过滤
  const filtered = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.rows
      .filter((r) => {
        if (viewPick.value === "overdue" && !(r.overdueDays > 0)) return false;
        if (viewPick.value === "arrived" && !(r.arriveDate && r.status !== 4 && r.status !== 5)) return false;
        if (viewPick.value === "waiting" && r.arriveDate) return false;
        // 已上架：只看真的上架了、且上架日在近 30 天内
        if (viewPick.value === "shelved" && !(r.status === 4 && shelvedWithin(r, SHELVED_DAYS))) return false;
        if (accountPick.value !== "all" && r.account !== accountPick.value) return false;
        if (chPick.value !== "all" && r.channelText !== chPick.value) return false;
        if (stPick.value !== "all" && r.statusText !== stPick.value) return false;
        if (onlyIssue.value && !r.issues.length) return false;
        if (!kw) return true;
        return [r.inboundNo, r.whName, r.channelText, ...r.skus.map((s) => `${s.sku} ${s.productName} ${s.style} ${s.size}`)]
          .some((t) => String(t || "").toLowerCase().includes(kw));
      })
      .sort((a, b) => {
        // 已上架视图：按上架时间倒序（最近进仓的先看），跟催不催无关
        if (viewPick.value === "shelved") {
          return String(b.shelfTime || "").localeCompare(String(a.shelfTime || ""));
        }
        // 其余：超期天数降序（最该催的在最上）→ 到港已等天数降序 → 入库单号
        const o = b.overdueDays - a.overdueDays;
        if (o) return o;
        const w = (b.waitDays ?? -1) - (a.waitDays ?? -1);
        if (w) return w;
        return a.inboundNo.localeCompare(b.inboundNo);
      });
  });

  const filteredOverdue = computed(() => filtered.value.filter((r) => r.overdueDays > 0).length);
  const filteredArrivedWaiting = computed(
    () => filtered.value.filter((r) => r.arriveDate && r.status !== 4 && r.status !== 5).length
  );

  const viewTitle = computed(() => {
    if (viewPick.value === "overdue") return "超期未上架";
    if (viewPick.value === "arrived") return "已到港待上架";
    if (viewPick.value === "waiting") return "未到港";
    if (viewPick.value === "shelved") return `近 ${SHELVED_DAYS} 天已上架`;
    return "全部入库单";
  });

  const paged = computed(() => {
    const start = (page.value - 1) * pageSize.value;
    return filtered.value.slice(start, start + pageSize.value);
  });

  watch([viewPick, accountPick, chPick, stPick, keyword, onlyIssue], () => {
    page.value = 1;
  });

  function onSizeChange() {
    page.value = 1;
  }

  function openTraces(row: ShipmentRow) {
    traceRow.value = row;
    traceOpen.value = true;
  }

  // ---------------------------------------------------------------- 复制清单
  function copyTodo() {
    const rows = filtered.value;
    if (!rows.length) {
      ElMessage.info("当前筛选下没有货件");
      return;
    }
    const head = "入库单号\tOMS状态\t渠道\t到港日期\t已等天数\t预测上架日\t超期天数\t产品·尺码·数量\t需关注";
    const body = rows.map((r) =>
      [
        r.inboundNo, r.statusText, r.channelText, r.arriveDate || "",
        r.waitDays ?? "", r.predictedShelf || "", r.overdueDays || "",
        r.skus.map((s) => `${s.productName}×${s.qty}`).join("; "),
        r.issues.join(" / "),
      ].join("\t")
    );
    navigator.clipboard
      ?.writeText([head, ...body].join("\n"))
      .then(() => ElMessage.success(`已复制 ${rows.length} 张`))
      .catch(() => ElMessage.error("复制失败，请检查浏览器剪贴板权限"));
  }
</script>

<style lang="scss" scoped>
  .sh-page {
    padding-bottom: 24px;
  }

  .sh-header {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 12px;

    &__title {
      margin: 0 0 4px;
      font-size: 20px;
      font-weight: 700;
    }

    &__desc {
      max-width: 1040px;
      margin: 0;
      font-size: 12px;
      line-height: 1.7;
      color: var(--el-text-color-secondary);
    }

    &__meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
  }

  .sh-alert {
    margin-bottom: 12px;

    &__body {
      font-size: 12px;
      line-height: 1.9;

      code {
        padding: 1px 5px;
        font-size: 11.5px;
        background: var(--el-fill-color-light);
        border-radius: 4px;
      }
    }

    &__ch {
      display: inline-block;
      margin-right: 12px;
    }
  }

  .sh-stats {
    margin-bottom: 12px;
  }

  .sh-stat {
    &__label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }

    &__value {
      margin: 2px 0;
      font-size: 20px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }

    &__hint {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }
  }

  .sh-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 12px;
  }

  .sh-card {
    margin-bottom: 12px;

    &__head {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: baseline;
      justify-content: space-between;
    }

    &__title {
      font-size: 14px;
      font-weight: 600;
    }

    &__sub {
      margin-left: 6px;
      font-size: 11.5px;
      font-weight: 400;
      color: var(--el-text-color-placeholder);
    }
  }

  .sh-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .sh-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }

    /**
     * 超期行：整行淡红底。
     * ⚠ 不能写死 #fff5f5 —— 面板支持深色主题，死白底 + 浅色字 = 看不清
     *   （2026-11-02 用户反馈「超期未上架的 css 过于白了，看不清」）。
     *   用 Element 自己的语义色变量，深浅主题各自会换算。
     */
    :deep(.el-table__row.is-alert) {
      --el-table-tr-bg-color: var(--el-color-danger-light-9);
    }

    :deep(.el-table__row.is-alert > td.el-table__cell) {
      background: var(--el-color-danger-light-9);
    }

    /* 悬停时别把超期底色盖掉（el-table 默认 hover 会覆盖成 --el-table-row-hover-bg-color） */
    :deep(.el-table__row.is-alert:hover > td.el-table__cell) {
      background: var(--el-color-danger-light-8);
    }
  }

  .sh-issues {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    min-width: 0;
  }

  .sh-mono {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }

  .sh-node {
    font-size: 12px;
    line-height: 1.4;

    &.is-bad {
      color: var(--el-color-danger);
      font-weight: 600;
    }

    &__time {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }

    /* 恒信查不到时那条「去货代查件页 →」链接 */
    &__link {
      display: inline-block;
      margin-top: 2px;
      font-size: 11.5px;
    }
  }

  .sh-sku {
    display: inline-block;
    margin-right: 10px;
    font-size: 11.5px;
    color: var(--el-text-color-regular);
    white-space: nowrap;

    b {
      color: var(--el-text-color-primary);
    }
  }

  .sh-issue {
    max-width: 100%;
    margin: 0;
    overflow: hidden;
    /* 不许被 flex 压扁 —— 不加这句标签会被挤成「超期 +」这种半截
       （2026-11-02 用户截图反馈）。宽度不够就让它们换行。 */
    flex: 0 0 auto;

    /* el-tag 默认 nowrap，长文案会顶出单元格、盖住右边的固定「操作」列。
       这里允许省略号，全文放悬停提示里。 */
    :deep(.el-tag__content) {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .sh-dim {
    color: var(--el-text-color-placeholder);
  }

  .sh-tiny {
    font-size: 10.5px;
  }

  .sh-bad {
    color: var(--el-color-danger);
    font-weight: 600;
  }

  .sh-ok {
    color: var(--el-color-success);
    font-weight: 600;
  }

  .sh-warn {
    color: var(--el-color-warning);
    font-weight: 600;
  }

  .sh-exp {
    padding: 4px 8px 8px 40px;

    &__head {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      font-size: 12px;
    }

    &__t {
      max-width: 980px;
    }
  }

  .sh-pagebar {
    display: flex;
    justify-content: flex-end;
    margin-top: 10px;
  }

  .sh-drawer__meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 14px;
    font-size: 12px;
  }

  .sh-timeline {
    padding-left: 4px;
    font-size: 12.5px;
  }

  .sh-rule {
    &__body {
      font-size: 12px;
      line-height: 1.85;
      color: var(--el-text-color-regular);

      p {
        margin: 0 0 4px;
      }

      code {
        padding: 1px 5px;
        font-size: 11.5px;
        background: var(--el-fill-color-light);
        border-radius: 4px;
      }
    }
  }
</style>
