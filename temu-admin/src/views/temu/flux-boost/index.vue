<!--
  TEMU 流量加速

  数据来自 public/data/flux-boost.json（由 scripts/bel_goods/build_api_data.py 合并
  data/flux/<store>_<section>.json 导出），静态托管，无后端依赖。

  两个板块：
    已开启 —— 逐 SPU 的尺码级申报价对照（原日常申报价 → 调整后申报价）
    待开启 —— 有加速资格但未开启的 SPU，含流量/转化指标与可选优化方式
-->
<template>
  <div class="flux-page app-container">
    <!-- 页头 -->
    <div class="flux-header">
      <div>
        <h2 class="flux-header__title">流量加速 · 申报价对照</h2>
        <p class="flux-header__desc">
          BEL / BHY 两店的「已开启」与「待开启」流量加速器商品
        </p>
      </div>
      <div class="flux-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据时间 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="data.source"
      type="info"
      :closable="false"
      show-icon
      class="flux-alert"
      :title="`数据来源：${data.source}`"
    />

    <!-- 店铺 + 板块切换 -->
    <div class="flux-switch">
      <el-radio-group v-model="store" size="default">
        <el-radio-button value="">
          全部
          <span class="flux-switch__badge">{{ totalAll }}</span>
        </el-radio-button>
        <el-radio-button v-for="k in storeKeys" :key="k" :value="k">
          {{ data.stores[k].label }}
          <span class="flux-switch__badge">{{ storeTotal(k) }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-radio-group v-model="section" size="default">
        <el-radio-button value="accelerating">
          已开启
          <span class="flux-switch__badge">{{ sectionOf(store, 'accelerating').total }}</span>
        </el-radio-button>
        <el-radio-button value="weak">
          动力不足
          <span class="flux-switch__badge">{{ sectionOf(store, 'weak').total }}</span>
        </el-radio-button>
        <el-radio-button value="pending">
          待开启
          <span class="flux-switch__badge">{{ sectionOf(store, 'pending').total }}</span>
        </el-radio-button>
      </el-radio-group>
      <span class="flux-switch__scope">{{ scopeText }}</span>
    </div>

    <el-alert
      v-if="section === 'weak'"
      type="warning"
      :closable="false"
      show-icon
      class="flux-alert"
      title="这些 SPU 的流量加速器开关已打开，但未达到生效条件（页面上显示「流量加速器动力不足，建议提效」），因此没有尺码级申报价明细。"
    />

    <!-- 概览指标 -->
    <el-row :gutter="12" class="flux-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="flux-stat__label">{{ s.label }}</div>
          <div class="flux-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="flux-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 已开启：降幅图 -->
    <el-row v-if="section === 'accelerating'" :gutter="12" class="flux-charts">
      <el-col :xs="24" :lg="14">
        <el-card shadow="never">
          <template #header>
            <span class="flux-card-title">降价幅度 Top15（%）</span>
          </template>
          <ECharts
            v-if="dropChart.list.length"
            :key="'drop-' + store + '-' + dropChart.list.length"
            :options="dropChart.options"
            height="340px"
          />
          <el-empty v-else description="暂无数据" :image-size="80" />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card shadow="never">
          <template #header>
            <span class="flux-card-title">降幅区间分布</span>
          </template>
          <ECharts
            v-if="bucketChart.list.length"
            :key="'bucket-' + store + '-' + bucketChart.list.length"
            :options="bucketChart.options"
            height="340px"
          />
          <el-empty v-else description="暂无数据" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 明细表 -->
    <el-card shadow="never" class="flux-table-card">
      <template #header>
        <div class="flux-table-head">
          <span class="flux-card-title">
            {{ store ? (data.stores[store]?.label ?? store) : "两店合计" }} ·
            {{ sectionOf(store, section).label }}（{{ filtered.length }}）
          </span>
          <div class="flux-table-head__filters">
            <el-input
              v-model="keyword"
              placeholder="搜索 SPU ID / 标题"
              clearable
              class="flux-filter-input"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </div>
        </div>
      </template>

      <!-- 已开启：尺码级申报价对照 -->
      <el-table
        v-if="section === 'accelerating'"
        v-loading="loading"
        :data="paged"
        row-key="spuId"
        stripe
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="flux-sku-box">
              <div class="flux-sku-box__title">
                {{ row.spuId }} · 尺码申报价明细（{{ row.skus.length }} 个尺码）
              </div>
              <el-table :data="row.skus" size="small" border>
                <el-table-column prop="color" label="颜色" min-width="90" />
                <el-table-column prop="size" label="尺码" min-width="70" />
                <el-table-column label="原日常申报价" align="right" min-width="120">
                  <template #default="{ row: s }">{{ money(s.origPrice) }}</template>
                </el-table-column>
                <el-table-column label="调整后申报价" align="right" min-width="120">
                  <template #default="{ row: s }">{{ money(s.afterPrice) }}</template>
                </el-table-column>
                <el-table-column label="降幅" align="right" min-width="90">
                  <template #default="{ row: s }">
                    <span :class="dropClass(s.origPrice, s.afterPrice)">
                      {{ dropText(s.origPrice, s.afterPrice) }}
                    </span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column v-if="!store" label="店铺" width="90" fixed="left">
          <template #default="{ row }">
            <span class="flux-store">{{ storeLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="spuId" label="SPU ID" width="130" fixed="left" />
        <el-table-column label="商品标题" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || "—" }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.state === '进行中' ? 'success' : 'info'" size="small" effect="light">
              {{ row.state || "—" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="periodTo" label="加速到期" width="170" />
        <el-table-column label="尺码数" width="80" align="center" prop="sizeCount" />
        <el-table-column label="原日常申报价" width="120" align="right">
          <template #default="{ row }">{{ money(row.origPrice) }}</template>
        </el-table-column>
        <el-table-column label="调整后申报价" width="120" align="right">
          <template #default="{ row }">{{ money(row.afterPrice) }}</template>
        </el-table-column>
        <el-table-column label="降幅" width="120" align="right">
          <template #default="{ row }">
            <span :class="dropClass(row.origPrice, row.afterPrice)">
              {{ dropText(row.origPrice, row.afterPrice) }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 动力不足：开关已开但未达生效条件，展示原因而不是一排空值 -->
      <el-table
        v-else-if="section === 'weak'"
        v-loading="loading"
        :data="paged"
        row-key="spuId"
        stripe
      >
        <el-table-column v-if="!store" label="店铺" width="90" fixed="left">
          <template #default="{ row }">
            <span class="flux-store">{{ storeLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="spuId" label="SPU ID" width="130" fixed="left" />
        <el-table-column label="商品标题" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || "—" }}</template>
        </el-table-column>
        <el-table-column label="类目" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.category || "—" }}</template>
        </el-table-column>
        <el-table-column label="状态" width="150" align="center">
          <template #default>
            <el-tag type="warning" size="small" effect="light">动力不足</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="平台建议" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="flux-optimize">{{ weakReason(row) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 待开启：流量/转化指标 + 可选优化方式 -->
      <el-table v-else v-loading="loading" :data="paged" row-key="spuId" stripe>
        <el-table-column v-if="!store" label="店铺" width="90" fixed="left">
          <template #default="{ row }">
            <span class="flux-store">{{ storeLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="spuId" label="SPU ID" width="130" fixed="left" />
        <el-table-column label="商品标题" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || "—" }}</template>
        </el-table-column>
        <el-table-column label="类目" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.category || "—" }}</template>
        </el-table-column>
        <el-table-column label="曝光量" width="100" align="right">
          <template #default="{ row }">{{ num(row.stats?.exposure) }}</template>
        </el-table-column>
        <el-table-column label="点击量" width="100" align="right">
          <template #default="{ row }">{{ num(row.stats?.clicks) }}</template>
        </el-table-column>
        <el-table-column label="访客数" width="100" align="right">
          <template #default="{ row }">{{ num(row.stats?.visitors) }}</template>
        </el-table-column>
        <el-table-column label="订单数" width="90" align="right">
          <template #default="{ row }">{{ num(row.stats?.orders) }}</template>
        </el-table-column>
        <el-table-column label="点击率" width="90" align="right">
          <template #default="{ row }">{{ pct(row.stats?.ctr) }}</template>
        </el-table-column>
        <el-table-column label="曝光订单转化率" width="130" align="right">
          <template #default="{ row }">{{ pct(row.stats?.exposureOrderConv) }}</template>
        </el-table-column>
        <el-table-column label="可优化方式" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="flux-optimize">{{ row.optimize || "—" }}</span>
          </template>
        </el-table-column>
      </el-table>

      <Pagination
        v-model:page="pageNum"
        v-model:limit="pageSize"
        :total="filtered.length"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import FluxBoostAPI from "@/api/temu/flux-boost";
import type { FluxBoostData, FluxBoostItem, FluxSection } from "@/api/temu/flux-boost";

defineOptions({ name: "FluxBoost" });

const loading = ref(false);
const data = ref<FluxBoostData>({
  generatedAt: null,
  source: "",
  total: 0,
  stores: {},
});

const store = ref("");
/** 三个板块：已开启（生效）/ 动力不足 / 待开启 */
type SectionKey = "accelerating" | "weak" | "pending";
const section = ref<SectionKey>("accelerating");

/** 空板块兜底，避免模板里到处判空 */
const EMPTY_SECTION: FluxSection = { label: "", total: 0, scrapedAt: null, list: [] };

const SECTION_ORDER: SectionKey[] = ["accelerating", "weak", "pending"];

async function load() {
  loading.value = true;
  try {
    const d = await FluxBoostAPI.getFluxBoostData();
    data.value = d;
    const keys = Object.keys(d.stores);
    if (keys.length && store.value && !d.stores[store.value]) store.value = keys[0];
    // 默认落在有数据的板块，避免一进来就是空表
    if (keys.length && !sectionOf(store.value, section.value).total) {
      const other = SECTION_ORDER.find(
        (s) => s !== section.value && sectionOf(store.value, s).total
      );
      if (other) section.value = other;
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "加载流量加速数据失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const storeKeys = computed(() => Object.keys(data.value.stores));

/** 当前生效的店铺 key 列表；"" = 全部 */
const scopedKeys = computed(() => (store.value ? [store.value] : storeKeys.value));

/** 全部模式下的 SPU 总数（两店两板块合计） */
const totalAll = computed(() =>
  storeKeys.value.reduce((a, k) => a + storeTotal(k), 0)
);

const scopeText = computed(() =>
  store.value
    ? `当前只看 ${data.value.stores[store.value]?.label ?? store.value}`
    : "当前为两店合计；点上方店铺可单独查看"
);

const SECTION_LABELS: Record<SectionKey, string> = {
  accelerating: "已开启",
  weak: "动力不足",
  pending: "待开启",
};

const sectionOf = (s: string, sec: string): FluxSection => {
  if (!s) {
    // 全部：跨店合并该板块
    const list = scopedKeys.value.flatMap((k) => sectionOf(k, sec).list);
    return {
      label: SECTION_LABELS[sec as SectionKey] ?? sec,
      total: list.length,
      scrapedAt: null,
      list,
    };
  }
  return data.value.stores[s]?.sections?.[sec as SectionKey] ?? EMPTY_SECTION;
};

/** 店铺徽标 = 三个板块之和 */
const storeTotal = (s: string) =>
  SECTION_ORDER.reduce((a, sec) => a + sectionOf(s, sec).total, 0);

/**
 * 动力不足行的「原因」文案。
 *
 * 抓下来的 optimize 形如「商品流量待关注 已暂时失去Best Seller同款权益
 * 流量加速器动力不足, 建议提效 ·永久提效 调价提效」；这里只留下与
 * 加速器相关的部分，过长时交给表格的 tooltip。
 */
const weakReason = (row: { optimize?: string | null; tags?: string | null }): string => {
  const t = (row?.optimize ?? "").trim();
  if (!t || t === "-") return "流量加速器动力不足";
  return t;
};

/**
 * 全部模式下列表是跨店合并的，用这个按 SPU ID 反查店铺来源，
 * 给表格加「店铺」列用（单店模式下该列隐藏）。
 *
 * 入参宽松：el-table 插槽的 row 是 DefaultRow（不保证带 spuId），
 * 这里用可选字段接住，运行时一定有值。
 */
const storeLabel = (row: { spuId?: string }): string => {
  const spuId = row?.spuId;
  if (!spuId) return "—";
  for (const k of storeKeys.value) {
    if (sectionOf(k, section.value).list.some((x) => x.spuId === spuId)) {
      return data.value.stores[k]?.label ?? k;
    }
  }
  return "—";
};

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `$${Number(v).toFixed(2)}`;

const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Number(v).toLocaleString();

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v}%`;

const dropText = (o?: number | null, a?: number | null) => {
  if (o === null || a === null || o === undefined || a === undefined || !o) return "—";
  const d = o - a;
  return `${d >= 0 ? "-" : "+"}$${Math.abs(d).toFixed(2)} (${((d / o) * 100).toFixed(1)}%)`;
};

/** 降幅配色：降得越多越红 */
const dropClass = (o?: number | null, a?: number | null) => {
  if (!o || a === null || a === undefined) return "";
  const p = ((o - a) / o) * 100;
  if (p >= 40) return "flux-drop flux-drop--high";
  if (p >= 20) return "flux-drop flux-drop--mid";
  if (p > 0) return "flux-drop flux-drop--low";
  return "flux-drop flux-drop--up";
};

// ---------------------------------------------------------------- 概览
const currentList = computed(() => sectionOf(store.value, section.value).list);

/** 指标卡 hint 用的范围标签 */
const scopeLabel = computed(() =>
  store.value ? (data.value.stores[store.value]?.label ?? store.value) : "两店合计"
);

const validItems = computed(() =>
  currentList.value.filter((i) => i.origPrice && i.afterPrice)
);

const stats = computed(() => {
  const list = validItems.value;

  // 动力不足：没有尺码价，指标看「有几个、有没有在正常卖」
  if (section.value === "weak") {
    const p = currentList.value;
    const exposure = p.reduce((a, i) => a + (i.stats?.exposure || 0), 0);
    const orders = p.reduce((a, i) => a + (i.stats?.orders || 0), 0);
    const withSales = p.filter((i) => (i.stats?.orders || 0) > 0).length;
    return [
      { label: "动力不足 SPU", value: String(p.length), hint: scopeLabel.value, color: "#e0a33e" },
      { label: "其中有订单", value: String(withSales), hint: "在卖但拿不到加速力度", color: "" },
      { label: "合计曝光量", value: exposure.toLocaleString(), hint: "近 1 日", color: "#599ce7" },
      { label: "合计订单数", value: orders.toLocaleString(), hint: "近 1 日", color: "#2fa37c" },
    ];
  }

  if (section.value === "pending") {
    const p = currentList.value;
    const exposure = p.reduce((a, i) => a + (i.stats?.exposure || 0), 0);
    const orders = p.reduce((a, i) => a + (i.stats?.orders || 0), 0);
    const withWrite = p.filter((i) => (i.writeActions || []).length).length;
    return [
      { label: "待开启 SPU", value: String(p.length), hint: scopeLabel.value, color: "" },
      { label: "合计曝光量", value: exposure.toLocaleString(), hint: "近 1 日", color: "#599ce7" },
      { label: "合计订单数", value: orders.toLocaleString(), hint: "近 1 日", color: "#2fa37c" },
      { label: "可开启加速器", value: String(withWrite), hint: "页面上有开启入口", color: "#e0a33e" },
    ];
  }
  const drops = list.map((i) => i.drop ?? 0);
  const avg = drops.length ? drops.reduce((a, b) => a + b, 0) / drops.length : 0;
  const max = drops.length ? Math.max(...drops) : 0;
  const maxItem = list.find((i) => (i.drop ?? 0) === max);
  const sizes = list.reduce((a, i) => a + i.sizeCount, 0);
  return [
    { label: "已开启 SPU", value: String(currentList.value.length), hint: `${sizes} 个尺码`, color: "" },
    { label: "平均降幅", value: avg ? `$${avg.toFixed(2)}` : "—", hint: `${list.length} 个可对比`, color: "#e05561" },
    { label: "最大降幅", value: max ? `$${max.toFixed(2)}` : "—", hint: maxItem?.spuId ?? "", color: "#e05561" },
    { label: "无价格明细", value: String(currentList.value.length - list.length), hint: "未能取到尺码价", color: "#8a8f9a" },
  ];
});

// ---------------------------------------------------------------- 图表
const dropChart = computed(() => {
  const list = [...validItems.value]
    .sort((a, b) => (b.dropPct ?? 0) - (a.dropPct ?? 0))
    .slice(0, 15);
  return {
    list,
    options: {
      animation: false,
      grid: { left: 8, right: 48, top: 16, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (p: unknown) => {
          const arr = p as { name: string; value: number }[];
          const it = list.find((x) => x.spuId === arr[0]?.name);
          return `${(it?.title ?? "").slice(0, 40)}<br/>SPU ${arr[0]?.name}<br/>降幅 ${arr[0]?.value}%`;
        },
      },
      xAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
      yAxis: {
        type: "category",
        data: list.map((i) => i.spuId).reverse(),
        axisLabel: { fontSize: 10 },
      },
      series: [
        {
          type: "bar",
          data: list.map((i) => i.dropPct ?? 0).reverse(),
          barMaxWidth: 14,
          itemStyle: { color: "#e05561", borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: "right", formatter: "{c}%", fontSize: 10 },
        },
      ],
    },
  };
});

const bucketChart = computed(() => {
  const buckets = [
    { label: "≥40%", min: 40, max: Infinity },
    { label: "20%~40%", min: 20, max: 40 },
    { label: "0~20%", min: 0.01, max: 20 },
    { label: "未降价", min: -Infinity, max: 0.01 },
  ];
  const list = buckets
    .map((b) => ({
      ...b,
      count: validItems.value.filter((i) => {
        const p = i.dropPct ?? 0;
        return p >= b.min && p < b.max;
      }).length,
    }))
    .filter((b) => b.count > 0);
  return {
    list,
    options: {
      animation: false,
      tooltip: { trigger: "item", formatter: "{b}<br/>{c} 个 SPU ({d}%)" },
      legend: { bottom: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
      series: [
        {
          type: "pie",
          radius: ["42%", "68%"],
          center: ["50%", "45%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: "transparent", borderWidth: 2 },
          label: { show: true, formatter: "{b}\n{c}", fontSize: 11 },
          data: list.map((b) => ({ name: b.label, value: b.count })),
        },
      ],
    },
  };
});

// ---------------------------------------------------------------- 表格
const keyword = ref("");
const pageNum = ref(1);
const pageSize = ref(10);

const filtered = computed(() => {
  let list: FluxBoostItem[] = currentList.value;
  const kw = keyword.value.trim().toLowerCase();
  if (kw) {
    list = list.filter(
      (i) => i.spuId.includes(kw) || (i.title ?? "").toLowerCase().includes(kw)
    );
  }
  return list;
});

const paged = computed(() => {
  const start = (pageNum.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});

// 切店/切板块/改搜索后回第一页，避免停在越界空页
watch([store, section, keyword], () => {
  pageNum.value = 1;
});
</script>

<style lang="scss" scoped>
.flux-header {
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

.flux-alert {
  margin-bottom: 12px;
}

.flux-switch {
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
}

// 全部模式下跨店合并时的店铺标注列
.flux-store {
  font-size: 12px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.flux-stats,
.flux-charts {
  margin-bottom: 12px;
}

.flux-stat {
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

.flux-card-title {
  font-size: 14px;
  font-weight: 600;
}

.flux-table-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;

  &__filters {
    display: flex;
    gap: 8px;
  }
}

.flux-filter-input {
  width: 240px;
}

.flux-sku-box {
  padding: 4px 12px 10px 48px;

  &__title {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
  }
}

.flux-optimize {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.flux-drop {
  font-variant-numeric: tabular-nums;

  &--high {
    font-weight: 600;
    color: var(--el-color-danger);
  }

  &--mid {
    color: var(--el-color-warning);
  }

  &--low {
    color: var(--el-text-color-regular);
  }

  &--up {
    color: var(--el-color-success);
  }
}
</style>
