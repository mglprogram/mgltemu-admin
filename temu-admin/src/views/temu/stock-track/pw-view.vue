<!--
  帕沃尔海外仓 · Kansas 仓库存（可查询）

  2026-11-02 用户要求：去帕沃尔库存链接抓 Kansas 仓有库存的产品。
  `https://www.powerwarehouse.net/stock.php?act=list`

  ⚠ 帕沃尔后台的库存页**只有 Kansas 仓的数据是有效的**（用户明确说明），
    其它仓（加州3仓 / 新泽西2仓）本脚本已直接丢弃，不参与统计。

  数据源 public/data/pw-stock.json（scripts/goods/fetch_pw_stock.py）
-->
<template>
  <div class="pw-panel">
    <el-row :gutter="12" class="pw-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '10px 14px' }">
          <div class="pw-stat__label">{{ s.label }}</div>
          <div class="pw-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="pw-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="pw-bar">
      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 Seller sku / 产品名 / Fnsku，如 204203"
        style="width: 330px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-checkbox v-model="onlyStock" label="只看有库存（代发库存 > 0）" />
      <el-input-number v-model="minQty" :min="0" :step="10" size="default"
                       style="width: 130px" controls-position="right" />
      <span class="pw-bar__hint">代发库存 ≥ {{ minQty }}</span>
      <el-button size="default" plain @click="reset">重置</el-button>
    </div>

    <div class="pw-count">
      命中 <b>{{ shown.length }}</b> 行 ｜ 代发库存合计 <b>{{ sumOf("stock") }}</b> ｜
      代发途中合计 <b>{{ sumOf("transit") }}</b>
      <span class="pw-tip">仓库：{{ data.warehouse || "全部" }}（其它仓数据无效，已剔除）</span>
    </div>

    <el-table :data="shown" size="small" stripe border height="560" class="pw-table">
      <el-table-column prop="sku" label="Seller sku" min-width="200" fixed show-overflow-tooltip />
      <el-table-column prop="fnsku" label="Fnsku" width="150" show-overflow-tooltip />
      <el-table-column label="代发库存" width="104" align="right" sortable :sort-by="'stock'">
        <template #default="{ row }">
          <b :class="{ 'pw-zero': !row.stock }">{{ num(row.stock) }}</b>
        </template>
      </el-table-column>
      <el-table-column label="代发途中" width="100" align="right" sortable :sort-by="'transit'">
        <template #default="{ row }">
          <span class="pw-dim">{{ num(row.transit) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="待处理" width="92" align="right">
        <template #default="{ row }">{{ num(row.pending) }}</template>
      </el-table-column>
      <el-table-column label="中转库存" width="98" align="right">
        <template #default="{ row }">{{ num(row.transferStock) }}</template>
      </el-table-column>
      <el-table-column label="10天销量" width="96" align="right" sortable :sort-by="'sales10'">
        <template #default="{ row }">{{ num(row.sales10) }}</template>
      </el-table-column>
      <el-table-column label="30天销量" width="96" align="right" sortable :sort-by="'sales30'">
        <template #default="{ row }">{{ num(row.sales30) }}</template>
      </el-table-column>
      <el-table-column label="库龄(天)" width="94" align="right" sortable :sort-by="'ageDays'">
        <template #default="{ row }">{{ num(row.ageDays) }}</template>
      </el-table-column>
      <el-table-column label="体积(ft³)" width="100" align="right">
        <template #default="{ row }">{{ row.volume }}</template>
      </el-table-column>
      <el-table-column label="仓储费($)" width="98" align="right">
        <template #default="{ row }">{{ row.fee }}</template>
      </el-table-column>
      <el-table-column prop="warn" label="预警" width="100" show-overflow-tooltip />
      <el-table-column prop="inboundAt" label="入仓时间" width="150" />
    </el-table>

    <div class="pw-rule">
      <p><b>仓库筛选</b>：只保留 <code>kansas仓</code>；全量扫过 {{ data.scanned }} 行，
        命中 {{ data.total }} 行（{{ data.pages }} 页）。</p>
      <p><b>口径</b>：{{ data.rule.note }}</p>
      <p><b>数据源</b>：{{ data.rule.source }}</p>
      <p><b>抓取时间</b>：{{ data.generatedAt || "—" }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
  import StockTrackAPI from "@/api/temu/stock-track";
  import type { PwStockData, PwStockItem } from "@/api/temu/stock-track";

  defineOptions({ name: "PwStockPanel" });

  const keyword = ref("");
  const onlyStock = ref(true);
  const minQty = ref(0);

  const data = ref<PwStockData>({
    generatedAt: "",
    warehouse: "",
    total: 0,
    scanned: 0,
    pages: 0,
    items: [],
    rule: { note: "", source: "" },
  });

  async function load() {
    try {
      data.value = await StockTrackAPI.getPwStock();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载帕沃尔库存失败");
    }
  }
  onMounted(load);

  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  const shown = computed<PwStockItem[]>(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.items.filter((i) => {
      if (onlyStock.value && !(i.stock > 0)) return false;
      if (minQty.value > 0 && i.stock < minQty.value) return false;
      if (!kw) return true;
      return [i.sku, i.name, i.nameEn, i.fnsku]
        .some((t) => String(t || "").toLowerCase().includes(kw));
    });
  });

  const sumOf = (key: "stock" | "transit") =>
    Math.round(shown.value.reduce((s, i) => s + (Number(i[key]) || 0), 0))
      .toLocaleString("zh-CN");

  const stats = computed(() => {
    const items = data.value.items;
    const withStock = items.filter((i) => i.stock > 0);
    return [
      {
        label: "Kansas 代发库存",
        value: num(items.reduce((s, i) => s + i.stock, 0)),
        hint: `${withStock.length} 个 SKU 有货`,
        color: "#0f766e",
      },
      {
        label: "代发途中",
        value: num(items.reduce((s, i) => s + i.transit, 0)),
        hint: "在路上，尚不可发",
        color: "#b45309",
      },
      {
        label: "SKU 总数",
        value: num(items.length),
        hint: `有货 ${withStock.length} / 零库存 ${items.length - withStock.length}`,
        color: "#2563eb",
      },
      {
        label: "30天销量合计",
        value: num(items.reduce((s, i) => s + i.sales30, 0)),
        hint: "Kansas 仓口径",
        color: "#7c3aed",
      },
      {
        label: "仓储费合计",
        value: `$${items.reduce((s, i) => s + (i.fee || 0), 0).toFixed(2)}`,
        hint: "当月",
        color: "#64748b",
      },
    ];
  });

  function reset() {
    keyword.value = "";
    onlyStock.value = true;
    minQty.value = 0;
  }
</script>

<style lang="scss" scoped>
  .pw-panel {
    padding-top: 4px;
  }

  .pw-stats {
    margin-bottom: 12px;
  }

  .pw-stat {
    &__label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }

    &__value {
      margin: 2px 0;
      font-size: 19px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }

    &__hint {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }
  }

  .pw-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 8px;

    &__hint {
      font-size: 12px;
      color: var(--el-text-color-secondary);
      white-space: nowrap;
    }
  }

  .pw-count {
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--el-text-color-regular);
  }

  .pw-tip {
    margin-left: 10px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }

  .pw-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }
  }

  .pw-dim {
    color: var(--el-text-color-placeholder);
  }

  .pw-zero {
    color: var(--el-text-color-placeholder);
    font-weight: 400;
  }

  .pw-rule {
    margin-top: 10px;
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.8;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-lighter);
    border-radius: 6px;

    p {
      margin: 0 0 2px;
    }

    code {
      padding: 1px 5px;
      font-size: 11.5px;
      background: var(--el-fill-color-light);
      border-radius: 4px;
    }
  }
</style>
