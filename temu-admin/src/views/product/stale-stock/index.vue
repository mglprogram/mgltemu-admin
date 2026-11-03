<!--
  滞销库存（产品-库存 → 滞销库存）

  用户需求（2026-11-02）：把 TEMU Bottom 的滞销数据放到「产品-库存」下级，
  栏目命名「滞销库存」。

  数据来自 public/data/stale-stock.json
  （scripts/goods/build_stale_stock_data.py 从飞书导出）。

  口径与 temu-bottom-stock-sync 的通知/报表**完全一致**（别另起一套）：
    · 库存 = 帕沃尔 加州3仓 + kansas仓（两仓相加，单源）
    · 销量 = 帕沃尔导出「10天销量」列（领星 OMS 已于 2026-11-02 停用）
    · 可售天数 = (代发库存 + 代发途中) / (10天销量 / 10)
    · 有动销 = 10天销量 > 0；无动销 = 10天销量 = 0
    · 希音款抓不到数据 → 单独一档「未匹配」，**不能算滞销**
-->
<template>
  <div class="ss-page app-container">
    <!-- 页头 -->
    <div class="ss-header">
      <div>
        <h2 class="ss-header__title">滞销库存 · {{ data.summary.matched }} 款</h2>
        <p class="ss-header__desc">
          库存 = 帕沃尔 加州3仓 + kansas仓；销量 = 帕沃尔「10天销量」列。
          <b>无动销</b>按库存降序排在最前 —— 压得越多越该先处理。
        </p>
      </div>
      <div class="ss-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="data.summary.inactive > 0"
      type="warning"
      :closable="false"
      show-icon
      class="ss-alert"
      :title="`无动销 ${data.summary.inactive} 款压货 ${fmt(data.summary.inactivePipeline)} 件（含途中）`"
      :description="clearText"
    />

    <!-- 概览指标 -->
    <el-row :gutter="12" class="ss-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="ss-stat__label">{{ s.label }}</div>
          <div class="ss-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="ss-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 库存走势 -->
    <el-card v-if="trendOption.series.length" shadow="never" class="ss-card">
      <template #header>
        <div class="ss-card__head">
          <span class="ss-card__title">库存与销量走势（近 {{ data.days }} 天）</span>
          <span class="ss-card__hint">左轴库存 / 右轴 10天销量</span>
        </div>
      </template>
      <ECharts :options="trendOption" height="280px" />
    </el-card>

    <!-- 分类分布 -->
    <el-row :gutter="12">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="ss-card">
          <template #header><span class="ss-card__title">各品类库存</span></template>
          <ECharts v-if="catOption.series.length" :options="catOption" height="260px" />
          <el-empty v-else description="暂无数据" :image-size="60" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="ss-card">
          <template #header><span class="ss-card__title">库存健康度分布</span></template>
          <ECharts v-if="healthOption.series.length" :options="healthOption" height="260px" />
          <el-empty v-else description="暂无数据" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 明细表 -->
    <el-card shadow="never" class="ss-card">
      <template #header>
        <div class="ss-card__head">
          <span class="ss-card__title">滞销明细</span>
          <div class="ss-card__tools">
            <el-radio-group v-model="tab" size="small">
              <!-- 「全部」放在最前：不筛选，三类合在一起看（含未匹配）。
                   用户 2026-11-02 要求：在「无动销」前面加一个不分筛选的按钮。 -->
              <el-radio-button value="all">
                全部 <span class="ss-badge">{{ allRows.length }}</span>
              </el-radio-button>
              <el-radio-button value="inactive">
                无动销 <span class="ss-badge">{{ data.inactive.length }}</span>
              </el-radio-button>
              <el-radio-button value="active">
                有动销 <span class="ss-badge">{{ data.active.length }}</span>
              </el-radio-button>
              <el-radio-button value="missing">
                未匹配 <span class="ss-badge">{{ data.missing.length }}</span>
              </el-radio-button>
            </el-radio-group>
            <el-input
              v-model="keyword"
              placeholder="搜款号 / 类别"
              clearable
              size="small"
              style="width: 180px"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
        </div>
      </template>

      <el-table :data="rows" size="default" stripe :default-sort="{ prop: 'stock', order: 'descending' }">
        <el-table-column prop="link" label="款号" min-width="180" fixed show-overflow-tooltip />
        <el-table-column prop="cat" label="款式类别" min-width="150" show-overflow-tooltip />
        <el-table-column prop="stock" label="代发库存" width="100" align="right" sortable>
          <template #default="{ row }">{{ fmt(row.stock) }}</template>
        </el-table-column>
        <el-table-column prop="kansas" label="其中 Kansas" width="115" align="right" sortable>
          <template #default="{ row }">
            <span v-if="row.kansas" class="ss-kansas">{{ fmt(row.kansas) }}</span>
            <span v-else class="ss-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="transit" label="代发途中" width="100" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'ss-dim': !row.transit }">{{ fmt(row.transit) }}</span>
          </template>
        </el-table-column>
        <!-- 「合计」= 代发库存（已含 Kansas）+ 代发途中。加 tooltip 说明，
             免得再被问「Kansas 到底算进去了没」。 -->
        <el-table-column prop="pipeline" label="合计" width="95" align="right" sortable>
          <template #header>
            <el-tooltip
              content="合计 = 代发库存（帕沃尔 加州3仓 + kansas仓）+ 代发途中"
              placement="top"
            >
              <span class="ss-th-help">合计</span>
            </el-tooltip>
          </template>
          <template #default="{ row }">{{ fmt(row.pipeline) }}</template>
        </el-table-column>
        <el-table-column prop="sales10" label="10天销量" width="100" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'ss-zero': !row.sales10 }">{{ fmt(row.sales10) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sales3" label="3天销量" width="95" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'ss-zero': !row.sales3 }">{{ fmt(row.sales3) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="daily" label="日均" width="85" align="right">
          <template #default="{ row }">{{ row.daily ? row.daily.toFixed(1) : "—" }}</template>
        </el-table-column>
        <el-table-column prop="days" label="可售天数" width="105" align="right" sortable>
          <template #default="{ row }">
            <span :class="row.days === null ? 'ss-dim' : dayClass(row.days)">
              {{ row.days === null ? "—" : fmt(row.days) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="health" label="健康度" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="healthTag(row.health)" effect="plain" size="small">
              {{ row.health }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="数据状态" width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'ss-dim': row.status === '正常' }">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column label="近 30 天库存" width="150">
          <template #default="{ row }">
            <span v-if="row.trend.length" class="ss-spark">{{ spark(row.trend) }}</span>
            <span v-else class="ss-dim">—</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!rows.length"
        :description="tab === 'missing' ? '没有未匹配的款' : '暂无数据'"
        :image-size="70"
      />
    </el-card>

    <!-- 口径说明 -->
    <el-card shadow="never" class="ss-card ss-rule">
      <template #header><span class="ss-card__title">口径说明</span></template>
      <div class="ss-rule__body">
        <p><b>库存来源</b>：帕沃尔导出「加州3仓 + kansas仓」（两仓相加，不重复计算）。
          领星 OMS 账号已于 2026-11-02 停用，加州改回从帕沃尔取。</p>
        <p><b>「其中 Kansas」列</b>：从帕沃尔导出里单独拆出的 Kansas 仓库存 ——
          飞书的「代发库存」只有加州+Kansas 的合计，这一列才看得出哪部分是 Kansas 的
          （Kansas 的货通常要额外安排发货）。数据由每天 <b>17:00</b> 的同步任务顺带抓取。</p>
        <p><b>销量来源</b>：帕沃尔导出「10天销量」列（滚动近 10 天，含两仓全部销量）。</p>
        <p><b>可售天数</b> = (代发库存 + 代发途中) ÷ (10天销量 ÷ 10)。
          10天销量为 0 时算不出 → 显示 <code>—</code>，健康度记「无销量」。</p>
        <p><b>健康度</b>：可售 &lt;7 天 严重缺货 / 7–15 偏紧 / 15–60 健康 / &gt;60 积压。</p>
        <p><b>有动销 vs 无动销</b>：以 <b>10天销量是否 &gt; 0</b> 为界。</p>
        <p><b>未匹配</b>：希音渠道的 5 款在帕沃尔里根本不存在（不是卖不动），
          单列一档，<b>不计入滞销统计</b>。</p>
        <p><b>数据源</b>：{{ data.source }}。
          生成脚本 <code>scripts/goods/build_stale_stock_data.py</code>。</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import StaleStockAPI from "@/api/temu/stale-stock";
  import type { StaleStockData, StaleSku, StaleTrendPoint } from "@/api/temu/stale-stock";

  defineOptions({ name: "TemuStaleStock" });

  const loading = ref(false);
  // 默认「全部」：用户 2026-11-02 要求进入页面不要一上来就被筛成无动销，
  // 先看全貌（26 款），想聚焦再用按钮切。
  const tab = ref<"all" | "inactive" | "active" | "missing">("all");
  const keyword = ref("");

  const EMPTY: StaleStockData = {
    generatedAt: "",
    source: "",
    days: 30,
    summary: {
      skus: 0, matched: 0, missing: 0, active: 0, inactive: 0,
      stock: 0, transit: 0, pipeline: 0, sales10: 0, sales3: 0, daily: 0,
      inactiveStock: 0, inactiveTransit: 0, inactivePipeline: 0,
      inactiveClearDays: null,
      kansas: 0,
      inactiveKansas: 0,
      kansasExportAt: "",
    },
    healthCounts: {},
    active: [],
    inactive: [],
    missing: [],
    categories: [],
    trend: [],
    snapshots: [],
  };

  const data = ref<StaleStockData>(EMPTY);

  /** 千分位；给小数的字段保留 1 位 */
  function fmt(v: number | null | undefined): string {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return Math.abs(n - Math.round(n)) < 0.05
      ? Math.round(n).toLocaleString()
      : n.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  const clearText = computed(() => {
    const d = data.value.summary.inactiveClearDays;
    if (d === null || d === undefined) return "有动销款当前无销量，无法估算清货天数";
    return `按有动销款的日均 ${data.value.summary.daily} 件估算，这批货要 ${d} 天才清得完`;
  });

  const stats = computed(() => {
    const s = data.value.summary;
    return [
      { label: "追踪款数", value: String(s.matched), hint: `另有 ${s.missing} 款未匹配（希音渠道）`, color: "#599ce7" },
      {
        label: "库存合计",
        value: fmt(s.stock),
        hint: `已含 Kansas ${fmt(s.kansas)}；途中 ${fmt(s.transit)}，总管道 ${fmt(s.pipeline)}`,
        color: "#e6a23c",
      },
      {
        label: "其中 Kansas 仓",
        value: fmt(s.kansas),
        hint: s.kansasExportAt ? `帕沃尔导出 ${s.kansasExportAt}` : "帕沃尔导出未就绪",
        color: "#b88230",
      },
      { label: "10 天销量", value: fmt(s.sales10), hint: `日均 ${fmt(s.daily)} 件`, color: "#67c23a" },
      { label: "无动销压货", value: fmt(s.inactivePipeline), hint: `${s.inactive} 款（含途中）`, color: "#f56c6c" },
    ];
  });

  /**
   * 「全部」= 无动销 + 有动销 + 未匹配。
   *
   * ⚠️ 顺序有讲究：无动销排最前（压货多、最该处理），其次有动销，
   *    未匹配放最后（希音渠道，本来就不该参与判断）。
   *    这里不再二次排序，直接用后端已经排好的顺序，保证切换按钮时行序稳定。
   */
  const allRows = computed<StaleSku[]>(() => [
    ...data.value.inactive,
    ...data.value.active,
    ...data.value.missing,
  ]);

  const rows = computed<StaleSku[]>(() => {
    const base =
      tab.value === "all" ? allRows.value
        : tab.value === "inactive" ? data.value.inactive
          : tab.value === "active" ? data.value.active
            : data.value.missing;
    const kw = keyword.value.trim().toLowerCase();
    if (!kw) return base;
    return base.filter(
      (r) => r.link.toLowerCase().includes(kw) || (r.cat || "").toLowerCase().includes(kw),
    );
  });

  function healthTag(h: string): "success" | "warning" | "danger" | "info" {
    if (h === "健康") return "success";
    if (h === "积压" || h === "无销量") return "danger";
    if (h === "偏紧" || h === "严重缺货") return "warning";
    return "info";
  }

  /** 可售天数越大越该警惕 */
  function dayClass(d: number): string {
    if (d > 60) return "ss-over";
    if (d < 7) return "ss-tight";
    return "";
  }

  /** 极简迷你走势：用块字符表示，避免为一个单元格挂一堆 canvas */
  function spark(trend: StaleTrendPoint[]): string {
    const bars = "▁▂▃▄▅▆▇█";
    const vs = trend.map((p) => p.stock);
    const max = Math.max(...vs);
    const min = Math.min(...vs);
    const span = max - min || 1;
    return vs
      .map((v) => bars[Math.min(bars.length - 1, Math.floor(((v - min) / span) * bars.length))])
      .join("");
  }

  // ---------------- 图表 ----------------
  const trendOption = computed<any>(() => {
    const t = data.value.trend;
    if (!t.length) return { series: [] };
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["库存", "10天销量"], right: 10, top: 0 },
      grid: { left: 55, right: 55, top: 40, bottom: 30 },
      xAxis: { type: "category", data: t.map((p) => p.d.slice(5)) },
      yAxis: [
        { type: "value", name: "库存", axisLabel: { formatter: (v: number) => fmt(v) } },
        { type: "value", name: "10天销量", splitLine: { show: false } },
      ],
      series: [
        {
          name: "库存", type: "line", smooth: true, showSymbol: false,
          data: t.map((p) => p.stock),
          areaStyle: { opacity: 0.12 },
          itemStyle: { color: "#e6a23c" },
        },
        {
          name: "10天销量", type: "bar", yAxisIndex: 1,
          data: t.map((p) => p.sales10),
          itemStyle: { color: "#599ce7" },
        },
      ],
    };
  });

  const catOption = computed<any>(() => {
    const c = data.value.categories.filter((x) => x.stock > 0);
    if (!c.length) return { series: [] };
    return {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 8, right: 30, top: 10, bottom: 10, containLabel: true },
      xAxis: { type: "value" },
      yAxis: {
        type: "category",
        data: c.map((x) => x.cat).reverse(),
        axisLabel: { width: 110, overflow: "truncate" },
      },
      series: [{
        type: "bar",
        data: c.map((x) => x.stock).reverse(),
        itemStyle: { color: "#e6a23c" },
        label: { show: true, position: "right", formatter: (p: any) => fmt(p.value) },
      }],
    };
  });

  const healthOption = computed<any>(() => {
    const hc = data.value.healthCounts || {};
    const entries = Object.entries(hc).filter(([, v]) => v > 0);
    if (!entries.length) return { series: [] };
    const colorMap: Record<string, string> = {
      积压: "#f56c6c", 无销量: "#909399", 偏紧: "#e6a23c",
      健康: "#67c23a", 严重缺货: "#599ce7",
    };
    return {
      tooltip: { trigger: "item", formatter: "{b}: {c} 款 ({d}%)" },
      legend: { bottom: 0 },
      series: [{
        type: "pie",
        radius: ["42%", "68%"],
        center: ["50%", "45%"],
        avoidLabelOverlap: true,
        label: { formatter: "{b}\n{c} 款" },
        data: entries.map(([k, v]) => ({
          name: k, value: v, itemStyle: { color: colorMap[k] || "#909399" },
        })),
      }],
    };
  });

  async function load() {
    loading.value = true;
    try {
      data.value = await StaleStockAPI.getStaleStock();
    } catch (e: any) {
      ElMessage.error(e?.message || "加载滞销库存失败");
    } finally {
      loading.value = false;
    }
  }

  onMounted(load);
</script>

<style scoped lang="scss">
  .ss-page { padding-bottom: 24px; }

  .ss-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 14px;

    &__title { margin: 0 0 6px; font-size: 19px; font-weight: 600; }
    &__desc { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.6; }
    &__meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  }

  .ss-alert { margin-bottom: 14px; }

  .ss-stats { margin-bottom: 14px; }

  .ss-stat {
    &__label { font-size: 12px; color: var(--el-text-color-secondary); }
    &__value { margin: 4px 0 2px; font-size: 22px; font-weight: 600; }
    &__hint { font-size: 11px; color: var(--el-text-color-placeholder); }
  }

  .ss-card {
    margin-bottom: 14px;

    &__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    &__title { font-size: 14px; font-weight: 600; }
    &__hint { font-size: 12px; color: var(--el-text-color-placeholder); }
    &__tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  }

  .ss-badge {
    display: inline-block;
    margin-left: 4px;
    padding: 0 5px;
    border-radius: 8px;
    background: rgb(0 0 0 / 8%);
    font-size: 11px;
  }

  .ss-zero { color: var(--el-color-danger); font-weight: 600; }
  /* 带 tooltip 说明的表头，加个虚下划线提示可 hover */
  .ss-th-help { border-bottom: 1px dashed currentcolor; cursor: help; }
  /* Kansas 分仓：这一列是「要额外安排发货」的货，用棕色和主库存区分开 */
  .ss-kansas { color: #b88230; font-weight: 600; }
  .ss-dim { color: var(--el-text-color-placeholder); }
  .ss-over { color: var(--el-color-danger); }
  .ss-tight { color: var(--el-color-warning); }

  .ss-spark {
    font-family: monospace;
    letter-spacing: -1px;
    color: var(--el-color-primary);
  }

  .ss-rule {
    &__body {
      font-size: 13px;
      line-height: 1.85;
      color: var(--el-text-color-regular);

      p { margin: 0 0 6px; }
      code {
        padding: 1px 5px;
        border-radius: 4px;
        background: var(--el-fill-color-light);
        font-size: 12px;
      }
    }
  }
</style>
