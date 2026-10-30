<!--
  货件跟踪（追踪表 × 恒信轨迹 × 领星 OMS 入库明细）

  用户需求（2026-10-30）
  ----------------------
  「加一栏：货件跟踪，把他更新到我的系统」
    · 数据源：博盈通豪一件代发 货件追踪表（每个 sheet 一个月）
    · **优先查到港日期有值的**，没到港的也要跟踪
    · 每票还要看「什么产品 / 什么尺码 / 多少件」→ 领星 OMS 入库单明细

  三个口径（都对得上原始数据）
  ---------------------------
  ① 到港 = 追踪表「到港日期」有值。这是业务口径，不是物流口径 ——
     所以会出现「到港了但物流还在转运中」（提柜待送仓），也会出现
     「物流已签收但表里没填到港」，后者单独列进「需关注」。
  ② 物流 = 恒信 http://hengxe.nextsls.com/tracking/app?inajax=1&tracking_number=<其他单号>
     最新节点 = traces[0]（接口按时间倒序给）。
  ③ 产品 = 领星 OMS 入库单明细的 productName，形如 BS2-203802-4SetB-L，
     末段是尺码，前面是款号+套系。表里「发货品名」只是粗分类（瑜伽裤 / 男士长袖），
     对不上真实 SKU —— 页面上两列都留着，对账以 OMS 为准。

  数据来自 public/data/shipment-track.json（scripts/goods/fetch_shipment_track.py 生成）。
-->
<template>
  <div class="sh-page app-container">
    <!-- 页头 -->
    <div class="sh-header">
      <div>
        <h2 class="sh-header__title">货件跟踪 · {{ data.summary.total }} 票</h2>
        <p class="sh-header__desc">
          追踪表 + 恒信轨迹 + 领星 OMS 入库明细三合一。<b>到港的优先看</b>，没到港的也逐票跟踪最新节点。
          产品 / 尺码 / 数量取<b>领星入库单明细</b>，不是表里的「发货品名」。
        </p>
      </div>
      <div class="sh-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          抓取 {{ data.generatedAt }}
        </el-tag>
        <el-tag v-if="data.source.workbookModified" type="success" effect="plain" size="small">
          追踪表 {{ data.source.workbookModified }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 数据源说明（一眼能看出这份数据是哪份表、怎么来的） -->
    <el-alert type="info" :closable="false" show-icon class="sh-alert" title="数据源">
      <div class="sh-alert__body">
        <div>
          <b>追踪表</b>：<code>{{ data.source.workbook }}</code>
          （sheet：{{ data.source.sheets.join(" / ") }}）
        </div>
        <div><b>物流</b>：{{ data.source.tracking }}</div>
        <div><b>产品</b>：{{ data.source.oms }}</div>
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
      <el-radio-group v-model="arrivePick" size="default">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="arrived">已到港</el-radio-button>
        <el-radio-button value="open">未到港</el-radio-button>
      </el-radio-group>
      <el-select v-model="sheetPick" size="default" style="width: 150px" placeholder="全部月份">
        <el-option label="全部月份" value="all" />
        <el-option v-for="s in data.sheets" :key="s" :label="s" :value="s" />
      </el-select>
      <el-select v-model="logPick" size="default" style="width: 150px" placeholder="全部物流状态">
        <el-option label="全部物流状态" value="all" />
        <el-option v-for="(n, k) in data.summary.byLogisticsStatus" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-select v-model="omsPick" size="default" style="width: 150px" placeholder="全部 OMS 状态">
        <el-option label="全部 OMS 状态" value="all" />
        <el-option v-for="(n, k) in data.summary.byOmsStatus" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 其他单号 / 运单号 / 品名 / SKU / 款号"
        style="width: 280px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-switch v-model="onlyIssue" active-text="只看需关注" size="default" />
      <el-switch v-model="onlyAbnormal" active-text="只看物流异常" size="default" />
      <el-button size="default" plain @click="copyTodo">复制清单</el-button>
    </div>

    <!-- 主体表 -->
    <el-card shadow="never" class="sh-card">
      <template #header>
        <div class="sh-card__head">
          <span class="sh-card__title">
            {{ viewTitle }} · {{ filtered.length }} 票
            <span class="sh-card__sub">（展开行看产品·尺码·数量，点「轨迹」看全链路）</span>
          </span>
          <span class="sh-hint">
            到港 <b>{{ filteredArrived }}</b> 票 ｜ 未到港 <b>{{ filteredOpen }}</b> 票
          </span>
        </div>
      </template>

      <el-table
        :data="paged"
        size="small"
        stripe
        border
        row-key="otherNo"
        :row-class-name="rowClass"
        class="sh-table"
      >
        <!-- 展开：产品明细（领星 OMS） -->
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="sh-exp">
              <div class="sh-exp__head">
                <b>{{ row.otherNo }}</b>
                <span class="sh-dim">{{ row.trackingNo }}</span>
                <el-tag v-if="!row.oms.found" type="danger" size="small" effect="plain">
                  OMS 无此入库单
                </el-tag>
                <template v-else>
                  <el-tag :type="omsTag(row.oms.status)" size="small" effect="light">
                    {{ row.oms.statusText }}
                  </el-tag>
                  <span class="sh-dim">
                    {{ row.oms.whName }} ｜ {{ row.oms.skuCount }} 个 SKU ｜ 共
                    <b>{{ num(row.oms.totalQty) }}</b> 件
                    <template v-if="row.oms.expectedDate"> ｜ OMS 预计到货 {{ row.oms.expectedDate }}</template>
                    <template v-if="row.oms.account"> ｜ 账号 {{ row.oms.account }}</template>
                  </span>
                </template>
              </div>

              <el-table v-if="row.oms.skus.length" :data="row.oms.skus" size="small" border class="sh-exp__t">
                <el-table-column prop="productName" label="产品名称" min-width="230" />
                <el-table-column prop="style" label="款号 / 套系" min-width="180" />
                <el-table-column prop="size" label="尺码" width="80" align="center" />
                <el-table-column prop="qty" label="数量" width="90" align="right">
                  <template #default="{ row: s }"><b>{{ num(s.qty) }}</b></template>
                </el-table-column>
                <el-table-column prop="sku" label="SKU" min-width="150" />
              </el-table>
              <el-empty v-else description="OMS 里没有这票的产品明细" :image-size="50" />
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="otherNo" label="其他单号" width="152" fixed>
          <template #default="{ row }">
            <span class="sh-mono">{{ row.otherNo }}</span>
          </template>
        </el-table-column>
        <el-table-column label="到港" width="76" align="center" fixed>
          <template #default="{ row }">
            <el-tag v-if="row.arrived" type="success" size="small" effect="dark">已到港</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">在途</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="到港日期" width="104" align="center">
          <template #default="{ row }">
            <span v-if="row.arriveDate">{{ row.arriveDate }}</span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="物流状态" width="86" align="center">
          <template #default="{ row }">
            <el-tooltip v-if="row.logistics.matchedBy === 'trackingNo'" content="恒信里这票是按运单号登记的，不是其他单号" placement="top">
              <el-tag :type="logTag(row.logistics.status)" size="small" effect="light">
                {{ row.logistics.statusText }} *
              </el-tag>
            </el-tooltip>
            <el-tag v-else :type="logTag(row.logistics.status)" size="small" effect="light">
              {{ row.logistics.statusText || "无轨迹" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="物流最新节点" min-width="290">
          <template #default="{ row }">
            <div class="sh-node" :class="{ 'is-bad': row.logistics.currentAbnormal }">
              {{ row.logistics.latest.info || "—" }}
            </div>
            <div class="sh-node__time">{{ row.logistics.latest.time }}</div>
          </template>
        </el-table-column>
        <el-table-column label="OMS" width="92" align="center">
          <template #default="{ row }">
            <el-tag :type="omsTag(row.oms.status)" size="small" effect="light">
              {{ row.oms.statusText }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="产品 · 尺码 · 数量" min-width="300">
          <template #default="{ row }">
            <template v-if="row.oms.skus.length">
              <span v-for="s in row.oms.skus" :key="s.sku" class="sh-sku">
                {{ s.productName }} <b>×{{ num(s.qty) }}</b>
              </span>
            </template>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="件数" width="74" align="right">
          <template #default="{ row }">
            <el-tooltip :content="`追踪表填 ${row.qty} 件（箱），OMS 明细合计 ${num(row.oms.totalQty)} 件（套）`" placement="top">
              <span>{{ row.qty ?? "—" }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="goods" label="表内品名" width="128" show-overflow-tooltip />
        <el-table-column prop="sheet" label="月份" width="106" />
        <el-table-column prop="channel" label="渠道" width="180" show-overflow-tooltip />
        <el-table-column prop="vessel" label="船名航次" width="170" show-overflow-tooltip />
        <el-table-column label="需关注" width="176">
          <template #default="{ row }">
            <template v-if="row.issues.length">
              <el-tag
                v-for="i in row.issues"
                :key="i"
                :type="issueTag(i)"
                size="small"
                effect="plain"
                class="sh-issue"
              >
                {{ i }}
              </el-tag>
            </template>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openTraces(asRow(row))">轨迹</el-button>
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
    <el-drawer v-model="traceOpen" :title="`物流轨迹 · ${traceRow?.otherNo || ''}`" size="560px">
      <template v-if="traceRow">
        <div class="sh-drawer__meta">
          <el-tag :type="logTag(traceRow.logistics.status)" size="small" effect="light">
            {{ traceRow.logistics.statusText || "无轨迹" }}
          </el-tag>
          <span class="sh-dim">
            运单号 {{ traceRow.trackingNo }}
            <template v-if="traceRow.logistics.shipmentId"> ｜ 恒信单 {{ traceRow.logistics.shipmentId }}</template>
            <template v-if="traceRow.logistics.parcels != null"> ｜ {{ traceRow.logistics.parcels }} 件</template>
            <template v-if="traceRow.logistics.country">
              ｜ {{ traceRow.logistics.country }} {{ traceRow.logistics.postcode }}
            </template>
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
          <b>到港</b>：取追踪表「到港日期」有值。业务口径，不是物流口径 ——
          所以「已到港」里会有一批物流还停在「提柜待送仓」，
          而「物流已签收但表里没填到港」的会单独进「需关注」。
        </p>
        <p>
          <b>物流最新节点</b>：恒信接口返回的 <code>traces[0]</code>（按时间倒序）。
          「物流当前异常」= 最新节点里带 <code>延迟 / 异常 / 转船 / 管控 / 查验 / 甩柜 / 退关</code>。
        </p>
        <p>
          <b>产品 · 尺码 · 数量</b>：领星 OMS 入库单明细 <code>inboundSkuVOList</code> 的
          <code>productName / quantity</code>。产品名形如 <code>BS2-203802-4SetB-L</code>，
          末段是尺码，前面是款号+套系。表里的「发货品名」只是粗分类（瑜伽裤 / 男士长袖），
          <b>对账以 OMS 为准</b>。
        </p>
        <p>
          <b>件数 vs 数量</b>：表格「件数」是追踪表的<b>箱数</b>；OMS 明细合计是
          <b>套/件数</b>（一个 4 件套 SKU 记 1 件）。两者不是同一个单位，别直接对。
        </p>
        <p>
          <b>带 * 的物流状态</b>：这票在恒信是按<b>运单号</b>登记的，用其他单号查不到，
          脚本自动退回运单号查（目前只有 2026-08 那两票 <code>HX00031RT/RV</code>）。
        </p>
        <p>
          <b>生成</b>：<code>python scripts/goods/fetch_shipment_track.py</code> ——
          自动挑微信缓存里最新一份「博盈通豪…货件追踪表」，逐票打恒信接口（并发 6），
          再批量查领星 OMS 入库明细（两个账号都试），落成 <code>shipment-track.json</code>。
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
    source: { workbook: "", workbookPath: "", workbookModified: "", sheets: [], tracking: "", oms: "" },
    summary: {
      total: 0, arrived: 0, notArrived: 0, delivered: 0, inTransit: 0, abnormal: 0, lag: 0,
      omsOnShelf: 0, omsPending: 0, omsCanceled: 0, omsMissing: 0, issues: 0,
      totalQty: 0, skuQty: 0, byLogisticsStatus: {}, byOmsStatus: {},
    },
    sheets: [],
    rows: [],
  });

  /** 到港优先是用户明确要求的默认视角：一进来先看已到港的 */
  const arrivePick = ref<"all" | "arrived" | "open">("arrived");
  const sheetPick = ref("all");
  /** 下拉的「全部」用 "all" 而不是 ""：空串会被 el-select 当成没选中，显示占位符「请选择」 */
  const logPick = ref("all");
  const omsPick = ref("all");
  const keyword = ref("");
  const onlyIssue = ref(false);
  const onlyAbnormal = ref(false);

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
  onMounted(load);

  // ---------------------------------------------------------------- 数字与标签
  const num = (v?: number | null) => (v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN"));

  /** el-table 插槽给的是宽类型 DefaultRow，统一按 ShipmentRow 接 */
  const asRow = (row: unknown) => row as ShipmentRow;

  const logTag = (s: string) =>
    s === "delivered" ? "success" : s === "in_transit" ? "warning" : s ? "info" : "danger";

  const omsTag = (s?: number) =>
    s === 4 ? "success" : s === 1 ? "warning" : s === 5 ? "danger" : s === 2 || s === 3 ? "primary" : "info";

  const issueTag = (i: string) =>
    i.includes("取消") ? "danger" : i.includes("异常") ? "danger" : i.includes("未上架") ? "warning" : "info";

  /** 异常行整行标红，扫表时不用逐格读 */
  const rowClass = ({ row }: { row: ShipmentRow }) =>
    row.logistics.currentAbnormal || row.issues.includes("OMS已取消但物流在途") ? "is-alert" : "";

  // ---------------------------------------------------------------- 指标
  const stats = computed(() => {
    const s = data.value.summary;
    return [
      { label: "总票数", value: num(s.total), hint: `追踪表全量 · 合计 ${num(s.totalQty)} 件(箱)`, color: "#0f766e" },
      {
        label: "已到港",
        value: num(s.arrived),
        hint: `占 ${s.total ? Math.round((s.arrived / s.total) * 100) : 0}% · 优先看这批`,
        color: "#16a34a",
      },
      { label: "未到港", value: num(s.notArrived), hint: "在途跟踪中（也已逐票取最新节点）", color: "#64748b" },
      { label: "物流已签收", value: num(s.delivered), hint: "恒信状态 delivered", color: "#0891b2" },
      {
        label: "物流异常",
        value: num(s.abnormal),
        hint: "最新节点是延迟 / 转船 / 查验",
        color: s.abnormal ? "#dc2626" : "#16a34a",
      },
      {
        label: "OMS 已上架",
        value: num(s.omsOnShelf),
        hint: `待入库 ${s.omsPending} ｜ 已取消 ${s.omsCanceled}`,
        color: "#2563eb",
      },
      {
        label: "表内轨迹落后",
        value: num(s.lag),
        hint: "表里「现轨迹」比恒信旧，建议刷新",
        color: "#d97706",
      },
      {
        label: "需关注",
        value: num(s.issues),
        hint: "已到港未上架 / 异常 / 已取消 / 无入库单",
        color: s.issues ? "#b91c1c" : "#16a34a",
      },
    ];
  });

  // ---------------------------------------------------------------- 过滤
  const filtered = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.rows.filter((r) => {
      if (arrivePick.value === "arrived" && !r.arrived) return false;
      if (arrivePick.value === "open" && r.arrived) return false;
      if (sheetPick.value && sheetPick.value !== "all" && r.sheet !== sheetPick.value) return false;
      if (logPick.value && logPick.value !== "all" && (r.logistics.statusText || "无轨迹") !== logPick.value) return false;
      if (omsPick.value && omsPick.value !== "all" && (r.oms.statusText || "—") !== omsPick.value) return false;
      if (onlyIssue.value && !r.issues.length) return false;
      if (onlyAbnormal.value && !r.logistics.currentAbnormal) return false;
      if (!kw) return true;
      return [
        r.otherNo, r.trackingNo, r.goods, r.sheet, r.channel, r.vessel,
        ...r.oms.skus.map((s) => `${s.sku} ${s.productName} ${s.style} ${s.size}`),
      ].some((t) => String(t || "").toLowerCase().includes(kw));
    });
  });

  const filteredArrived = computed(() => filtered.value.filter((r) => r.arrived).length);
  const filteredOpen = computed(() => filtered.value.length - filteredArrived.value);

  const viewTitle = computed(() => {
    if (arrivePick.value === "arrived") return "已到港货件";
    if (arrivePick.value === "open") return "未到港货件";
    return "全部货件";
  });

  const paged = computed(() => {
    const start = (page.value - 1) * pageSize.value;
    return filtered.value.slice(start, start + pageSize.value);
  });

  /** 筛选一变就回到第 1 页，否则会停在空白页 */
  watch([arrivePick, sheetPick, logPick, omsPick, keyword, onlyIssue, onlyAbnormal], () => {
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
    const head = "其他单号\t运单号\t到港\t到港日期\t物流状态\t最新节点\tOMS状态\t产品·尺码·数量\t需关注";
    const body = rows.map((r) =>
      [
        r.otherNo,
        r.trackingNo,
        r.arrived ? "已到港" : "在途",
        r.arriveDate || "",
        r.logistics.statusText,
        r.logistics.latest.info,
        r.oms.statusText,
        r.oms.skus.map((s) => `${s.productName}×${s.qty}`).join("; "),
        r.issues.join(" / "),
      ].join("\t")
    );
    navigator.clipboard
      ?.writeText([head, ...body].join("\n"))
      .then(() => ElMessage.success(`已复制 ${rows.length} 票`))
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
      max-width: 1000px;
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
      line-height: 1.8;

      code {
        padding: 1px 5px;
        font-size: 11.5px;
        background: var(--el-fill-color-light);
        border-radius: 4px;
        word-break: break-all;
      }
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

    /* 异常行：整行淡红底，扫表时不用逐格读 */
    :deep(.is-alert) {
      background: #fff5f5;
    }
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
    margin: 0 4px 2px 0;
  }

  .sh-dim {
    color: var(--el-text-color-placeholder);
  }

  .sh-bad {
    color: var(--el-color-danger);
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
      max-width: 900px;
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
