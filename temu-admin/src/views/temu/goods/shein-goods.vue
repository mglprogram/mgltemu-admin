<!--
  SHEIN 在售款明细（BS / BD / BAN）

  数据来自 public/data/shein-goods.json —— scripts/goods/fetch_shein_sales.py 生成：
    product/query（商品列表）→ spu-info（款号/标题/尺码）→ query-sku-sales（销量）
    → stock-query（线上可用库存）

  口径：
    · 一行 = 一个款号（supplierCode，如 BD-202506-5SetA）；展开看每个尺码
    · 近7天 / 近30天 / 昨日 都是 SHEIN 平台口径（query-sku-sales，数据日期见 salesDate）
    · 线上可用 = 半托管虚拟库存的可用数（stock-query）
-->
<template>
  <div class="shein-goods">
    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      show-icon
      :title="error"
      description="请先跑 python scripts/goods/fetch_shein_sales.py 生成 public/data/shein-goods.json"
    />

    <template v-else>
      <div class="sg-bar">
        <el-radio-group v-model="store">
          <el-radio-button value="">
            全部
            <span class="sg-badge">{{ totalStyles }}</span>
          </el-radio-button>
          <el-radio-button v-for="k in storeKeys" :key="k" :value="k">
            {{ data.stores[k].label }}
            <span class="sg-badge">{{ data.stores[k].counts.style }}</span>
          </el-radio-button>
        </el-radio-group>

        <div class="sg-bar__right">
          <el-input
            v-model="keyword"
            placeholder="款号 / SKC / 标题"
            clearable
            style="width: 240px"
          />
          <el-tag v-if="salesDate" type="info" effect="plain" size="small">
            销量日期 {{ salesDate }}
          </el-tag>
          <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
            抓取 {{ data.generatedAt }}
          </el-tag>
          <el-button :loading="loading" size="small" @click="load">
            <el-icon><Refresh /></el-icon><span>刷新</span>
          </el-button>
        </div>
      </div>

      <div class="sg-stats">
        <div v-for="s in stats" :key="s.label" class="sg-stat">
          <div class="sg-stat__v" :style="{ color: s.color || undefined }">{{ s.value }}</div>
          <div class="sg-stat__l">{{ s.label }}</div>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="rows"
        row-key="rowKey"
        border
        size="small"
        :default-sort="{ prop: 'sales7', order: 'descending' }"
        class="sg-table"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="sg-sizes">
              <el-table :data="row.sizes" size="small" border>
                <el-table-column prop="size" label="尺码" width="90" />
                <el-table-column prop="supplierSku" label="商家 SKU" min-width="200" show-overflow-tooltip />
                <el-table-column prop="skuCode" label="平台 SKU" width="140" />
                <el-table-column prop="salesYd" label="昨日" width="80" align="right" />
                <el-table-column prop="sales7" label="近7天" width="90" align="right" />
                <el-table-column prop="sales30" label="近30天" width="90" align="right" />
                <el-table-column prop="stock" label="线上可用" width="100" align="right" />
                <el-table-column prop="stockTotal" label="线上总库存" width="110" align="right" />
                <el-table-column label="供货价" width="100" align="right">
                  <template #default="{ row: s }">
                    {{ s.price == null ? "—" : `$${s.price}` }}
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>

        <el-table-column v-if="!store" prop="storeLabel" label="店铺" width="90" fixed="left" />
        <el-table-column prop="code" label="款号" min-width="210" fixed="left" show-overflow-tooltip />
        <el-table-column label="SKC" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="sg-skc">{{ row.skc.join(", ") }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="商品标题" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || "—" }}</template>
        </el-table-column>
        <el-table-column prop="sizeCount" label="尺码数" width="90" align="right" sortable />
        <el-table-column prop="stock" label="线上可用" width="110" align="right" sortable />
        <el-table-column prop="salesYd" label="昨日销量" width="110" align="right" sortable />
        <el-table-column prop="sales7" label="近7天销量" width="120" align="right" sortable />
        <el-table-column prop="sales30" label="近30天销量" width="125" align="right" sortable />
        <el-table-column label="均价" width="100" align="right">
          <template #default="{ row }">
            {{ row.priceMin == null ? "—" : `$${row.priceMin}` }}
          </template>
        </el-table-column>
        <el-table-column prop="firstShelfTime" label="首次上架" width="160" />
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Refresh } from "@element-plus/icons-vue";

defineOptions({ name: "SheinGoodsOnSale" });

interface SheinSize {
  size: string;
  supplierSku: string;
  skuCode: string;
  sales7: number;
  sales30: number;
  salesYd: number;
  stock: number;
  stockTotal: number;
  price: number | null;
}
interface SheinItem {
  store: string;
  code: string;
  skc: string[];
  title: string;
  image: string;
  firstShelfTime: string;
  sizes: SheinSize[];
  sizeCount: number;
  stock: number;
  sales7: number;
  sales30: number;
  salesYd: number;
  priceMin: number | null;
  priceMax: number | null;
}
interface SheinStore {
  label: string;
  counts: { onSaleSkc: number; spu: number; sku: number; style: number; stock: number; sales7: number; sales30: number; salesYd: number; zeroStock: number };
  salesDate?: string;
  items: SheinItem[];
}
interface SheinData {
  generatedAt: string;
  platform: string;
  source: string;
  stores: Record<string, SheinStore>;
}

const DATA_URL = `${import.meta.env.BASE_URL}data/shein-goods.json`;

const data = ref<SheinData>({ generatedAt: "", platform: "SHEIN", source: "", stores: {} });
const loading = ref(false);
const error = ref("");
const store = ref("");
const keyword = ref("");

const storeKeys = computed(() => Object.keys(data.value.stores || {}));
const salesDate = computed(() => {
  const dates = storeKeys.value.map((k) => data.value.stores[k]?.salesDate).filter(Boolean) as string[];
  return dates.length ? dates.sort().slice(-1)[0] : "";
});

const scoped = computed(() => {
  const keys = store.value ? [store.value] : storeKeys.value;
  return keys.flatMap((k) => {
    const s = data.value.stores[k];
    if (!s) return [];
    return (s.items || []).map((it) => ({
      ...it,
      rowKey: `${k}-${it.code}`,
      storeKey: k,
      storeLabel: s.label,
    }));
  });
});

const rows = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return scoped.value;
  return scoped.value.filter((r) =>
    [r.code, r.title, ...(r.skc || [])].join(" ").toLowerCase().includes(kw),
  );
});

const totalStyles = computed(() =>
  storeKeys.value.reduce((n, k) => n + (data.value.stores[k]?.counts.style || 0), 0),
);

const stats = computed(() => {
  const rows0 = scoped.value;
  const sum = (f: (r: (typeof rows0)[number]) => number) => rows0.reduce((n, r) => n + f(r), 0);
  const sku = storeKeys.value
    .filter((k) => !store.value || k === store.value)
    .reduce((n, k) => n + (data.value.stores[k]?.counts.sku || 0), 0);
  const skc = storeKeys.value
    .filter((k) => !store.value || k === store.value)
    .reduce((n, k) => n + (data.value.stores[k]?.counts.onSaleSkc || 0), 0);
  const zero = storeKeys.value
    .filter((k) => !store.value || k === store.value)
    .reduce((n, k) => n + (data.value.stores[k]?.counts.zeroStock || 0), 0);
  const off = storeKeys.value
    .filter((k) => !store.value || k === store.value)
    .reduce((n, k) => n + (data.value.stores[k]?.counts.offShelf || 0), 0);
  return [
    { label: "已上架款数", value: rows0.length.toLocaleString() },
    { label: "已上架 SKC", value: skc.toLocaleString() },
    { label: "已上架 SKU", value: sku.toLocaleString() },
    { label: "线上可用", value: sum((r) => r.stock).toLocaleString(), color: "#599ce7" },
    { label: "昨日销量", value: sum((r) => r.salesYd).toLocaleString() },
    { label: "近7天销量", value: sum((r) => r.sales7).toLocaleString() },
    { label: "近30天销量", value: sum((r) => r.sales30).toLocaleString() },
    { label: "断货尺码", value: zero.toLocaleString(), color: zero ? "#e05561" : "#2fa37c" },
    { label: "未上架 SKC", value: off.toLocaleString(), color: "#909399" },
  ];
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const res = await fetch(DATA_URL, { cache: "no-cache" });
    if (!res.ok) throw new Error(`加载 shein-goods.json 失败：HTTP ${res.status}`);
    data.value = (await res.json()) as SheinData;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped lang="scss">
.shein-goods {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sg-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;

  &__right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.sg-badge {
  margin-left: 4px;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--el-fill-color);
  color: var(--el-text-color-regular);
  font-size: 12px;
}

/* 与页面其它统计条（.goods-pinbar）一致：跟主题走，不用死白底 */
.sg-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 8px;
  padding: 12px;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--card-radius, 6px);
  box-shadow: var(--card-shadow, 0 2px 8px rgb(0 0 0 / 6%));
}

.sg-stat {
  text-align: center;

  &__v {
    font-size: 20px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  &__l {
    margin-top: 2px;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
}

.sg-table {
  background: transparent;
}

.sg-sizes {
  padding: 8px 16px 12px;
  background: var(--el-fill-color-lighter);
}

.sg-skc {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
