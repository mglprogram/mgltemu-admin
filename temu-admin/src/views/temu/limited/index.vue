<!--
  TEMU 限流监控（商品流量视角 · 待处理商品）

  数据来自 public/data/limited.json（由 scripts/bel_goods/build_api_data.py 导出），
  静态托管，无后端依赖。
-->
<template>
  <div class="lim-page app-container">
    <!-- 页头 -->
    <div class="lim-header">
      <div>
        <h2 class="lim-header__title">限流监控 · 商品流量视角</h2>
        <p class="lim-header__desc">
          两店「待处理商品」全量 + 重点 SPU 告警 + 历史趋势
        </p>
      </div>
      <div class="lim-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据时间 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 重点款告警 -->
    <el-alert
      v-if="totalHits > 0"
      type="error"
      :closable="false"
      show-icon
      class="lim-alert"
      :title="`重点 SPU 告警：${totalHits} 个被限流，需在 24 小时内调整申报价`"
      :description="hitSummary"
    />
    <el-alert
      v-else-if="data.watch.length"
      type="success"
      :closable="false"
      show-icon
      class="lim-alert"
      title="重点 SPU 全部正常"
    />

    <!-- 店铺切换：整页（指标/告警/图表/排行/明细）都跟着切 -->
    <div class="lim-switch">
      <el-radio-group v-model="store" size="default">
        <el-radio-button value="">
          全部
          <span class="lim-switch__badge">{{ totalAll }}</span>
        </el-radio-button>
        <el-radio-button v-for="k in storeKeys" :key="k" :value="k">
          {{ data.stores[k].label }}
          <span class="lim-switch__badge">{{ data.stores[k].total }}</span>
        </el-radio-button>
      </el-radio-group>
      <span class="lim-switch__scope">{{ scopeText }}</span>
      <span v-if="focusSource" class="lim-switch__source">重点名单：{{ focusSource }}</span>
    </div>

    <!-- 概览指标 -->
    <el-row :gutter="12" class="lim-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="lim-stat__label">{{ s.label }}</div>
          <div class="lim-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="lim-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 重点 SPU 命中表 -->
    <el-card v-if="hitRows.length" shadow="never" class="lim-card">
      <template #header>
        <span class="lim-card-title">重点关注 SPU · 限流告警（{{ hitRows.length }}）</span>
      </template>
      <el-table :data="hitRows" size="default" stripe>
        <el-table-column prop="label" label="店铺" width="90" />
        <el-table-column prop="spu_id" label="SPU ID" width="130" />
        <el-table-column prop="sku_code" label="货号(款号)" width="180" show-overflow-tooltip />
        <el-table-column prop="title" label="商品标题" min-width="260" show-overflow-tooltip />
        <el-table-column label="当前申报价" width="110" align="right">
          <template #default="{ row }">{{ money(row.declare_price) }}</template>
        </el-table-column>
        <el-table-column label="建议日常价" width="110" align="right">
          <template #default="{ row }">{{ money(row.target_price) }}</template>
        </el-table-column>
        <el-table-column label="需降" width="100" align="right">
          <template #default="{ row }">
            <span class="lim-gap">{{ gapText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="流量状态" width="130">
          <template #default="{ row }">
            <el-tag type="danger" size="small" effect="light">{{ row.flow_status }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 图表 -->
    <el-row :gutter="12" class="lim-charts">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="lim-card-title">限流商品总数走势（个）</span></template>
          <ECharts
            v-if="trendDays.length"
            :key="'trend-' + trendChart.series.length + '-' + trendDays.length"
            :options="trendChart"
            height="300px"
          />
          <el-empty v-else description="历史快照不足，明天起形成走势" :image-size="70" />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="lim-card-title">流量状态分布（个）</span></template>
          <ECharts v-if="flowChart.series.length" :options="flowChart.options" height="300px" />
          <el-empty v-else description="暂无数据" :image-size="70" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 重点 SPU 逐次监控 -->
    <el-card v-if="data.trend.focusTrack.length" shadow="never" class="lim-card">
      <template #header>
        <span class="lim-card-title">
          重点 SPU 逐次监控状态（绿=正常 / 红=被限流 · 共 {{ data.trend.snapshotCount }} 次快照）
        </span>
      </template>
      <el-table :data="focusTracks" size="small">
        <el-table-column prop="store" label="店铺" width="80" />
        <el-table-column prop="spu" label="SPU ID" width="130" />
        <el-table-column label="货号(款号)" width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="lim-code">{{ row.sku_code || "—" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态序列（由左至右）" min-width="300">
          <template #default="{ row }">
            <span class="lim-strip">
              <i
                v-for="(t, i) in row.timeline"
                :key="i"
                :class="['lim-strip__dot', t.limited ? 'is-bad' : 'is-ok']"
                :title="`${t.at} ${t.limited ? '限流' : '正常'}`"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column label="限流/检查" width="110" align="center">
          <template #default="{ row }">{{ row.hit_times }}/{{ row.checks }}</template>
        </el-table-column>
        <el-table-column label="当前" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.current ? 'danger' : 'success'" size="small" effect="light">
              {{ row.current ? "限流中" : "正常" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last" label="最近检查" min-width="160" />
      </el-table>
    </el-card>

    <!-- 调价空间 Top -->
    <el-card shadow="never" class="lim-card">
      <template #header>
        <div class="lim-card-head">
          <span class="lim-card-title">
            调价空间 Top10（USD）{{ store ? "· " + data.stores[store]?.label : "· 两店合计" }}
          </span>
        </div>
      </template>
      <el-table :data="gapTop" size="default" stripe>
        <el-table-column v-if="!store" prop="storeLabel" label="店铺" width="90" />
        <el-table-column prop="code" label="货号(款号)" width="200" show-overflow-tooltip />
        <el-table-column label="当前申报价" width="120" align="right">
          <template #default="{ row }">{{ money(row.declarePrice) }}</template>
        </el-table-column>
        <el-table-column label="建议日常价" width="120" align="right">
          <template #default="{ row }">{{ money(row.dailyTarget) }}</template>
        </el-table-column>
        <el-table-column label="需降" width="110" align="right">
          <template #default="{ row }">
            <span class="lim-gap">{{ money(row.priceGap) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="降幅" width="90" align="right">
          <template #default="{ row }">{{ gapPct(row) }}</template>
        </el-table-column>
        <el-table-column prop="flowStatus" label="流量状态" min-width="130" />
      </el-table>
    </el-card>

    <!-- 反复被限流 -->
    <el-card shadow="never" class="lim-card">
      <template #header>
        <span class="lim-card-title">
          反复被限流货号排行（{{ recurring.length }}）· 跨天数越高越优先处理
        </span>
      </template>
      <el-table :data="recurringPage" size="small" stripe>
        <el-table-column prop="sku_code" label="货号(款号)" width="190" show-overflow-tooltip />
        <el-table-column prop="spu_id" label="SPU ID" width="120" />
        <el-table-column prop="appear" label="出现次数" width="100" align="center" sortable />
        <el-table-column prop="days" label="跨天数" width="90" align="center" sortable />
        <el-table-column label="首次 → 最近" min-width="230">
          <template #default="{ row }">
            <span class="lim-time">{{ row.first_seen }} → {{ row.last_seen }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="flow_status" label="流量状态" width="130" />
        <el-table-column prop="sales7" label="近7日销量" width="100" align="right" />
        <el-table-column label="申报价 → 建议" width="160" align="right">
          <template #default="{ row }">
            <span class="lim-time">{{ money(row.declare_price) }} → {{ money(row.target_price) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <Pagination
        v-model:page="recPage"
        v-model:limit="recSize"
        :total="recurring.length"
        :page-sizes="[10, 20, 50]"
      />
    </el-card>

    <!-- 限流商品明细 -->
    <el-card shadow="never" class="lim-card">
      <template #header>
        <div class="lim-card-head">
          <span class="lim-card-title">
            限流商品明细（{{ filteredItems.length }} / {{ allItems.length }}）
            {{ store ? "· " + data.stores[store]?.label : "· 两店合计" }}
          </span>
          <div class="lim-card-head__filters">
            <el-input
              v-model="keyword"
              placeholder="搜索货号 / SPU / 标题"
              clearable
              class="lim-filter-input"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select v-model="flowFilter" placeholder="流量状态" clearable class="lim-filter-select">
              <el-option v-for="f in flowOptions" :key="f" :label="f" :value="f" />
            </el-select>
          </div>
        </div>
      </template>
      <el-table v-loading="loading" :data="pagedItems" size="default" stripe>
        <el-table-column v-if="!store" prop="storeLabel" label="店铺" width="90" fixed="left" />
        <el-table-column prop="code" label="货号(款号)" width="190" show-overflow-tooltip sortable />
        <el-table-column prop="spuId" label="SPU ID" width="120" sortable />
        <el-table-column prop="title" label="商品标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="flowStatus" label="流量状态" width="130" sortable />
        <el-table-column label="申报价" width="100" align="right" sortable prop="declarePrice">
          <template #default="{ row }">{{ money(row.declarePrice) }}</template>
        </el-table-column>
        <el-table-column label="建议日常价" width="110" align="right" prop="dailyTarget" sortable>
          <template #default="{ row }">{{ money(row.dailyTarget) }}</template>
        </el-table-column>
        <el-table-column label="建议活动价" width="110" align="right">
          <template #default="{ row }">{{ money(row.activityTarget) }}</template>
        </el-table-column>
        <el-table-column label="需降" width="100" align="right" prop="priceGap" sortable>
          <template #default="{ row }">
            <span class="lim-gap">{{ money(row.priceGap) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sales7" label="近7日销量" width="100" align="right" sortable />
      </el-table>
      <Pagination
        v-model:page="pageNum"
        v-model:limit="pageSize"
        :total="filteredItems.length"
      />
    </el-card>

    <!-- 安全清单 -->
    <el-card v-if="safeRows.length" shadow="never" class="lim-card">
      <template #header>
        <span class="lim-card-title">重点 SPU · 未被限流（{{ safeRows.length }}）</span>
      </template>
      <div class="lim-safe">
        <el-tag v-for="s in safeRows" :key="s.store + s.spu" type="success" effect="plain" size="small">
          {{ s.label }} · {{ s.spu }}
        </el-tag>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import LimitedAPI from "@/api/temu/limited";
import type { LimitedData } from "@/api/temu/limited";

defineOptions({ name: "Limited" });

const loading = ref(false);
const data = ref<LimitedData>({
  generatedAt: null,
  source: "",
  stores: {},
  watch: [],
  trend: {
    days: [], timeline: [], recurring: [], focusTrack: [], resolved: [],
    snapshotCount: 0, firstCheck: null, lastCheck: null,
  },
});

async function load() {
  loading.value = true;
  try {
    const d = await LimitedAPI.getLimitedData();
    data.value = d;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "加载限流数据失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `$${Number(v).toFixed(2)}`;

const storeKeys = computed(() => Object.keys(data.value.stores));

// ---------------------------------------------------------------- 店铺维度
// "" = 全部（两店合计）；否则只看该店。整页都跟着它走。
const store = ref("");

/** 当前生效的店铺 key 列表 */
const scopedKeys = computed(() => (store.value ? [store.value] : storeKeys.value));

/** 当前生效的限流明细（跨店合并，并标注来自哪家店） */
const scopedItems = computed(() =>
  scopedKeys.value.flatMap((k) =>
    (data.value.stores[k]?.items ?? []).map((it) => ({
      ...it,
      storeKey: k,
      storeLabel: data.value.stores[k]?.label ?? k,
    }))
  )
);

/** 当前生效的重点款监控 */
const scopedWatch = computed(() =>
  data.value.watch.filter((w) => scopedKeys.value.includes(w.store))
);

const totalAll = computed(() =>
  storeKeys.value.reduce((a, k) => a + (data.value.stores[k]?.total || 0), 0)
);

const scopeText = computed(() =>
  store.value
    ? `当前只看 ${data.value.stores[store.value]?.label ?? store.value}`
    : "当前为两店合计；点上方店铺可单独查看"
);

/** 重点 SPU 名单来源（动态抓取 or 兜底写死） */
const focusSource = computed(() => {
  const srcs = scopedWatch.value.map((w) => w.focusSource).filter(Boolean);
  return srcs.length ? Array.from(new Set(srcs)).join(" / ") : "";
});

/** 反复限流记录没有 store 字段，用货号前缀判定（BEL-xxx / BHY-xxx） */
const storeOfCode = (code: string) => (code || "").slice(0, 3).toUpperCase();

// ---------------------------------------------------------------- 概览
const totalHits = computed(() =>
  scopedWatch.value.reduce((a, w) => a + (w.hitCount || 0), 0)
);

const hitSummary = computed(() =>
  scopedWatch.value
    .map((w) => `${w.label} ${w.hitCount}/${w.focusTotal}`)
    .join(" · ")
);

const stats = computed(() => {
  const stores = scopedKeys.value.map((k) => data.value.stores[k]).filter(Boolean);
  const total = stores.reduce((a, s) => a + s.total, 0);
  const focusTotal = scopedWatch.value.reduce((a, w) => a + (w.focusTotal || 0), 0);
  const withSales = stores.reduce((a, s) => a + s.hasSales, 0);
  const t = data.value.trend;
  return [
    {
      label: store.value ? `${data.value.stores[store.value]?.label} 限流商品` : "限流商品合计",
      value: String(total),
      hint: store.value
        ? `${stores[0]?.withDaily ?? 0} 个有调价建议`
        : stores.map((s) => `${s.label} ${s.total}`).join(" / "),
      color: "",
    },
    {
      label: "重点 SPU 被限流",
      value: `${totalHits.value} / ${focusTotal}`,
      hint: hitSummary.value || "—",
      color: totalHits.value ? "#e05561" : "#2fa37c",
    },
    { label: "限流且近7日有销量", value: String(withSales), hint: "优先处理，正在掉流量", color: "#e0a33e" },
    {
      label: "监控覆盖",
      value: `${t.snapshotCount} 次`,
      hint: t.days.length ? `${t.days.length} 天（${t.days[0]} ~ ${t.days[t.days.length - 1]}）` : "首次快照",
      color: "",
    },
  ];
});

// ---------------------------------------------------------------- 重点款
const hitRows = computed(() =>
  scopedWatch.value.flatMap((w) =>
    w.hits.map((h) => ({ ...h, label: w.label, store: w.store }))
  )
);

const safeRows = computed(() =>
  scopedWatch.value.flatMap((w) =>
    w.safe.map((spu) => ({ store: w.store, label: w.label, spu }))
  )
);

// el-table 插槽的 row 是 DefaultRow（宽松类型），这里用结构化入参接住
const gapText = (row: { declare_price?: number | null; target_price?: number | null }) => {
  const d = row.declare_price;
  const t = row.target_price;
  if (d === null || d === undefined || t === null || t === undefined) return "—";
  return money(d - t);
};

// ---------------------------------------------------------------- 图表
const trendDays = computed(() => data.value.trend.days);

const trendChart = computed(() => {
  // timeline 是「每次快照一条、按店拆分」的扁平列表：
  // 先按天聚合，同一天多次运行取最后一次（与旧看板口径一致）
  const tl = data.value.trend.timeline.filter((x) => scopedKeys.value.includes(x.store));
  const byDayStore = new Map<string, Map<string, number>>();
  for (const snap of tl) {
    const d = snap.day;
    if (!byDayStore.has(d)) byDayStore.set(d, new Map());
    byDayStore.get(d)!.set(snap.store, snap.limited_total ?? 0);
  }
  const days = Array.from(byDayStore.keys()).sort();
  const storeKeysInTrend = Array.from(
    new Set(tl.map((x) => x.store).filter(Boolean))
  );
  const palette = ["#e05561", "#599ce7", "#2fa37c", "#e0a33e"];
  return {
    animation: false,
    grid: { left: 46, right: 20, top: 34, bottom: 30 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    xAxis: {
      type: "category",
      data: days.map((d) => d.slice(5)),
      axisTick: { show: false },
    },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "#1e222b" } } },
    series: storeKeysInTrend.map((s, i) => ({
      name: data.value.stores[s]?.label ?? s,
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 6,
      data: days.map((d) => byDayStore.get(d)?.get(s) ?? 0),
      lineStyle: { width: 2, color: palette[i % palette.length] },
      itemStyle: { color: palette[i % palette.length] },
    })),
  };
});

const flowChart = computed(() => {
  const stores = scopedKeys.value
    .map((k) => [k, data.value.stores[k]] as const)
    .filter(([, s]) => !!s);
  const names = Array.from(new Set(stores.flatMap(([, s]) => s.flowDist.map((f) => f.name))));
  const palette = ["#e05561", "#e0a33e", "#599ce7", "#2fa37c", "#9b7fd4", "#4fb3c9", "#c98a4b", "#8a8f9a"];
  return {
    series: names,
    options: {
      animation: false,
      grid: { left: 120, right: 30, top: 10, bottom: 26 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { show: false },
      xAxis: { type: "value", splitLine: { lineStyle: { color: "#1e222b" } } },
      yAxis: {
        type: "category",
        data: names,
        axisLabel: { fontSize: 11 },
      },
      series: stores.map(([key, s], i) => ({
        name: s.label || key,
        type: "bar",
        stack: "flow",
        barMaxWidth: 18,
        data: names.map((n) => s.flowDist.find((f) => f.name === n)?.value ?? 0),
        itemStyle: { color: palette[i % palette.length] },
      })),
    },
  };
});

// ---------------------------------------------------------------- 调价空间
// 全部时按两店合并取 Top10；选单店时就是该店 Top10
const gapTop = computed(() =>
  [...scopedItems.value]
    .filter((i) => i.priceGap !== null)
    .sort((a, b) => (b.priceGap ?? 0) - (a.priceGap ?? 0))
    .slice(0, 10)
);

const gapPct = (row: { declarePrice?: number | null; priceGap?: number | null }) => {
  const p = row.declarePrice;
  const g = row.priceGap;
  if (!p || g === null || g === undefined) return "—";
  return `${((g / p) * 100).toFixed(0)}%`;
};

// ---------------------------------------------------------------- 反复限流
const recurring = computed(() =>
  data.value.trend.recurring.filter(
    (r) => !store.value || storeOfCode(r.sku_code) === store.value
  )
);
const recPage = ref(1);
const recSize = ref(10);
const recurringPage = computed(() => {
  const start = (recPage.value - 1) * recSize.value;
  return recurring.value.slice(start, start + recSize.value);
});

// ---------------------------------------------------------------- 明细
const keyword = ref("");
const flowFilter = ref("");
const pageNum = ref(1);
const pageSize = ref(10);

const allItems = computed(() => scopedItems.value);

const flowOptions = computed(() =>
  Array.from(new Set(allItems.value.map((i) => i.flowStatus).filter(Boolean) as string[]))
);

const filteredItems = computed(() => {
  let list = allItems.value;
  const kw = keyword.value.trim().toLowerCase();
  if (kw) {
    list = list.filter(
      (i) =>
        i.code.toLowerCase().includes(kw) ||
        i.spuId.includes(kw) ||
        (i.title ?? "").toLowerCase().includes(kw)
    );
  }
  if (flowFilter.value) {
    list = list.filter((i) => i.flowStatus === flowFilter.value);
  }
  return list;
});

const pagedItems = computed(() => {
  const start = (pageNum.value - 1) * pageSize.value;
  return filteredItems.value.slice(start, start + pageSize.value);
});

// 切店 / 改过滤条件后回到第一页，避免停在越界空页
watch([store, keyword, flowFilter], () => {
  pageNum.value = 1;
});

// 切店后排行分页同样回第一页
watch(store, () => {
  recPage.value = 1;
});

// ---------------------------------------------------------------- 逐次监控
const focusTracks = computed(() =>
  data.value.trend.focusTrack
    .filter((t) => scopedKeys.value.includes(t.store))
    .map((t) => ({
      ...t,
      store: data.value.stores[t.store]?.label ?? t.store,
    }))
);
</script>

<style lang="scss" scoped>
.lim-switch {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;

  &__badge {
    margin-left: 4px;
    font-size: 11px;
    opacity: 0.75;
  }

  &__scope {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }

  &__source {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }
}

.lim-header {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;

  &__title {
    margin: 0 0 4px;
    font-size: 18px;
    font-weight: 600;
  }

  &__desc {
    margin: 0;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }

  &__meta {
    display: flex;
    gap: 8px;
    align-items: center;
  }
}

.lim-alert {
  margin-bottom: 12px;
}

.lim-stats,
.lim-charts {
  margin-bottom: 12px;
}

.lim-stat {
  &__label {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__value {
    margin: 4px 0 2px;
    font-size: 22px;
    font-weight: 600;
  }

  &__hint {
    overflow: hidden;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.lim-card {
  margin-bottom: 12px;

  &-title {
    font-size: 14px;
    font-weight: 600;
  }

  &-head {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;

    &__filters {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
  }
}

.lim-filter-input {
  width: 220px;
}

.lim-filter-select {
  width: 150px;
}

.lim-gap {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--el-color-danger);
}

.lim-time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.lim-code {
  font-size: 12px;
  color: var(--el-text-color-regular);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

.lim-strip {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 2px;
  align-items: center;

  &__dot {
    display: inline-block;
    width: 8px;
    height: 14px;
    border-radius: 2px;

    &.is-ok {
      background: var(--el-color-success);
    }

    &.is-bad {
      background: var(--el-color-danger);
    }
  }
}

.lim-safe {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>