<!--
  领星总库存（两个店铺账号，全量、可查询）

  2026-11-02 用户要求：把两个店铺的总库存放到库存跟踪页下（现 /product/stock-track；
  当天稍后又把「库存跟踪 / 货件跟踪」整组搬到与 TEMU 运营同级的「产品-库存」），
  加查询功能，并细分「产品 - 尺码 - 哪个账号」的库存。

  数据源 public/data/linxing-stock.json（scripts/goods/fetch_linxing_stock.py）：
    领星 OMS /openapi/v1/integratedInventory/pageOpen
    14450（帕沃尔）+ LM739（恒信）全量明细，账号 × 产品 × 尺码 × 仓库。

  三种看法（切换）：
    ① 按产品：同一个 SKU 的多个仓合并成一行，右侧拆「帕沃尔 / 恒信」两列 ——
       这就是用户要的「细分到产品-尺码-哪个账号」。
    ② 按仓库：每仓一行，看货具体压在哪个仓。
    ③ 原始明细：领星返回什么就显示什么，便于对账。
-->
<template>
  <div class="lx-panel">
    <!-- 汇总 -->
    <el-row :gutter="12" class="lx-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '10px 14px' }">
          <div class="lx-stat__label">{{ s.label }}</div>
          <div class="lx-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="lx-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 查询条 -->
    <div class="lx-bar">
      <el-radio-group v-model="view" size="default">
        <el-radio-button value="product">按产品</el-radio-button>
        <el-radio-button value="warehouse">按仓库</el-radio-button>
        <el-radio-button value="raw">原始明细</el-radio-button>
      </el-radio-group>

      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 产品名 / 款号 / 尺码 / SKU，如 204203 或 -XL"
        style="width: 320px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <el-select v-model="acctPick" size="default" style="width: 150px">
        <el-option label="全部账号" value="" />
        <el-option v-for="a in data.accounts" :key="a.tag"
                   :label="`${a.label}（${a.tag}）`" :value="a.tag" />
      </el-select>

      <el-select v-model="whPick" size="default" clearable style="width: 190px"
                 placeholder="全部仓库">
        <el-option v-for="w in data.warehouses" :key="w" :label="w" :value="w" />
      </el-select>

      <el-input-number v-model="minQty" :min="0" :step="10" size="default"
                       style="width: 130px" controls-position="right" />

      <span class="lx-bar__hint">总库存 ≥ {{ minQty }}</span>
      <el-button size="default" plain @click="reset">重置</el-button>
    </div>

    <div class="lx-count">
      命中 <b>{{ shown.length }}</b> 行 ｜ 总库存合计 <b>{{ numSum(shown, "total") }}</b> ｜
      可用合计 <b>{{ numSum(shown, "avail") }}</b>
      <span class="lx-tip">总库存 = 可用 + 锁定 + 在途 + 箱 + FBA 退货，所以比可用大</span>
    </div>

    <!-- ① 按产品：一个 SKU 一行，账号分开列 -->
    <el-table v-if="view === 'product'" :data="shown" size="small" stripe border
              height="560" class="lx-table">
      <el-table-column prop="productName" label="产品名（领星）" min-width="210" fixed show-overflow-tooltip />
      <el-table-column prop="code" label="款号" width="92" sortable />
      <el-table-column prop="variant" label="套系" width="100" />
      <el-table-column prop="size" label="尺码" width="72" align="center" />
      <el-table-column label="总库存" width="110" align="right" sortable :sort-by="'total'">
        <template #default="{ row }"><b>{{ num(row.total) }}</b></template>
      </el-table-column>
      <el-table-column label="可用" width="94" align="right" sortable :sort-by="'avail'">
        <template #default="{ row }">{{ num(row.avail) }}</template>
      </el-table-column>
      <el-table-column label="锁定" width="90" align="right">
        <template #default="{ row }">
          <span :class="{ 'lx-warn': row.lock > 0 }">{{ num(row.lock) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="在途" width="90" align="right">
        <template #default="{ row }">
          <span class="lx-dim">{{ num(row.transit) }}</span>
        </template>
      </el-table-column>
      <!-- 用户要的「细分到哪个账号」：两列并排 -->
      <el-table-column v-for="a in data.accounts" :key="a.tag" :label="a.label" width="104" align="right">
        <template #default="{ row }">
          <span v-if="!acctQty(row)[a.tag]" class="lx-dim">—</span>
          <span v-else class="lx-acct" :class="'lx-acct--' + a.short">
            {{ num(acctQty(row)[a.tag]) }}
          </span>
        </template>
      </el-table-column>
      <!--
        帕沃尔 Kansas 仓（单独一列，和领星口径并排看）
        帕沃尔后台的库存页只有 Kansas 仓数据有效，领星是全仓汇总，
        两者放一起才能看出"领星说有多少 vs Kansas 实际有多少"。
      -->
      <el-table-column label="帕沃尔 Kansas" width="122" align="right" sortable :sort-by="'pwStock'">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row)">
            <span v-if="pwStock(row) === null" class="lx-dim">—</span>
            <span v-else class="lx-pw">{{ num(pwStock(row)) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="仓库分布" min-width="200">
        <template #default="{ row }">
          <span v-if="!row._whs?.length" class="lx-dim">—</span>
          <span v-for="w in row._whs" v-else :key="w.wh" class="lx-wh">
            {{ shortWh(w.wh) }} <b>{{ num(w.total) }}</b>
          </span>
        </template>
      </el-table-column>
    </el-table>

    <!-- ② 按仓库 -->
    <el-table v-else-if="view === 'warehouse'" :data="shown" size="small" stripe border
              height="560" class="lx-table">
      <el-table-column prop="wh" label="仓库" width="190" fixed />
      <el-table-column prop="acctLabel" label="账号" width="94" fixed />
      <el-table-column prop="productName" label="产品名" min-width="210" show-overflow-tooltip />
      <el-table-column prop="code" label="款号" width="92" />
      <el-table-column prop="size" label="尺码" width="72" align="center" />
      <el-table-column label="总库存" width="110" align="right" sortable :sort-by="'total'">
        <template #default="{ row }"><b>{{ num(row.total) }}</b></template>
      </el-table-column>
      <el-table-column label="可用" width="94" align="right">
        <template #default="{ row }">{{ num(row.avail) }}</template>
      </el-table-column>
      <el-table-column label="锁定" width="90" align="right">
        <template #default="{ row }">{{ num(row.lock) }}</template>
      </el-table-column>
      <el-table-column label="在途" width="90" align="right">
        <template #default="{ row }">{{ num(row.transit) }}</template>
      </el-table-column>
      <el-table-column prop="sku" label="SKU" width="140" show-overflow-tooltip />
      <el-table-column prop="operateTime" label="最后变动" width="150" />
    </el-table>

    <!-- ③ 原始明细 -->
    <el-table v-else :data="shown" size="small" stripe border height="560" class="lx-table">
      <el-table-column prop="acctLabel" label="账号" width="90" fixed />
      <el-table-column prop="wh" label="仓库" width="180" fixed />
      <el-table-column prop="productName" label="产品名" min-width="220" show-overflow-tooltip />
      <el-table-column prop="sku" label="SKU" width="140" show-overflow-tooltip />
      <el-table-column label="总库存" width="104" align="right" sortable :sort-by="'total'">
        <template #default="{ row }"><b>{{ num(row.total) }}</b></template>
      </el-table-column>
      <el-table-column label="可用" width="88" align="right">
        <template #default="{ row }">{{ num(row.avail) }}</template>
      </el-table-column>
      <el-table-column label="锁定" width="88" align="right">
        <template #default="{ row }">{{ num(row.lock) }}</template>
      </el-table-column>
      <el-table-column label="在途" width="88" align="right">
        <template #default="{ row }">{{ num(row.transit) }}</template>
      </el-table-column>
      <el-table-column label="箱库存" width="88" align="right">
        <template #default="{ row }">{{ num(row.box) }}</template>
      </el-table-column>
      <el-table-column label="FBA退货" width="94" align="right">
        <template #default="{ row }">{{ num(row.fbaReturn) }}</template>
      </el-table-column>
      <el-table-column prop="operateTime" label="最后变动" width="150" />
    </el-table>

    <!-- 口径 -->
    <div class="lx-rule">
      <p><b>两个账号</b>：<span v-for="a in data.accounts" :key="a.tag" class="lx-acct-tag">
        {{ a.label }}（{{ a.tag }}）{{ a.ok ? `${a.rows} 行` : "拉取失败" }}
      </span></p>
      <p><b>口径</b>：{{ data.rule.note }}</p>
      <p><b>数据源</b>：{{ data.rule.source }}</p>
      <p><b>刷新</b>：随库存跟踪定时任务一起跑（每天 17:30），抓取时间 {{ data.generatedAt || "—" }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
  import StockTrackAPI from "@/api/temu/stock-track";
  import type { LinxingItem, LinxingStockData, PwStockData } from "@/api/temu/stock-track";

  defineOptions({ name: "LinxingStockPanel" });

  const loading = ref(false);
  const view = ref<"product" | "warehouse" | "raw">("product");
  const keyword = ref("");
  const acctPick = ref("");
  const whPick = ref("");
  const minQty = ref(0);

  const data = ref<LinxingStockData>({
    generatedAt: "",
    accounts: [],
    warehouses: [],
    total: 0,
    totalQty: 0,
    availQty: 0,
    items: [],
    rule: { note: "", source: "" },
  });

  /** 帕沃尔 Kansas 仓（独立数据源，用于并排那一列） */
  const pw = ref<PwStockData>({
    generatedAt: "",
    warehouse: "",
    total: 0,
    scanned: 0,
    pages: 0,
    items: [],
    rule: { note: "", source: "" },
  });

  async function load() {
    loading.value = true;
    try {
      data.value = await StockTrackAPI.getLinxingStock();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载领星库存失败");
    } finally {
      loading.value = false;
    }
    // Kansas 单独拉；失败不影响领星主体（那一列显示 —）
    try {
      pw.value = await StockTrackAPI.getPwStock();
    } catch {
      /* 静默：Kansas 列显示 — 即可，不用打断整个视图 */
    }
  }
  onMounted(load);

  /**
   * Kansas 索引：按「款号|变体|尺码」归一化后聚合。
   *
   * 两边的 SKU 写法不同（领星 `BS2-204203-4SetA-M`、帕沃尔 `BS-207220-4SetA-XXL`），
   * 不能直接拿 productName 对。帕沃尔那边也已经解析出 code/variant/size 三个字段
   * （fetch_pw_stock.py 里复用同一套 split_sku），所以按三元组对齐最稳。
   * 解析不出来的（非我们款号体系的老 SKU）保留原始名做兜底匹配。
   */
  const pwIndex = computed(() => {
    const m = new Map<string, number>();
    for (const i of pw.value.items || []) {
      const key =
        i.code && i.variant && i.size
          ? `${i.code}|${i.variant}|${i.size}`
          : `name|${String(i.sku || "").trim()}`;
      m.set(key, (m.get(key) || 0) + (Number(i.stock) || 0));
    }
    return m;
  });

  /** 某一行的 Kansas 代发库存；匹配不到返回 null（显示 —） */
  const pwStock = (row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string; productName?: string };
    if (r.code && r.variant && r.size) {
      const v = pwIndex.value.get(`${r.code}|${r.variant}|${r.size}`);
      if (v !== undefined) return v;
    }
    const v2 = pwIndex.value.get(`name|${String(r.productName || "").trim()}`);
    return v2 === undefined ? null : v2;
  };

  const pwTip = (row: unknown) => {
    const v = pwStock(row);
    if (v === null) {
      return "帕沃尔 Kansas 仓没有这个 SKU（或该 SKU 已零库存被过滤）";
    }
    return `帕沃尔 Kansas 仓代发库存 ${num(v)} 件（现货，可发）\n` +
      "领星是全仓汇总，这里单看 Kansas 仓";
  };

  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  /** 帕沃尔 Kansas 合计 / 有货 SKU 数（统计卡用） */
  const pwTotal = computed(() =>
    (pw.value.items || []).reduce((s, i) => s + (Number(i.stock) || 0), 0)
  );
  const pwWithStock = computed(
    () => (pw.value.items || []).filter((i) => (Number(i.stock) || 0) > 0).length
  );

  /** 十部的仓库名太长，表里只留能区分的短名 */
  const shortWh = (w: string) => {
    const s = String(w || "");
    if (s.includes("ZOO") || s.includes("⑥")) return "恒信美西";
    const tail = s.includes("·") ? (s.split("·").pop() || s) : s;
    return tail.replace(/仓$/, "");
  };

  // ---------------------------------------------------------------- 汇总
  const stats = computed(() => {
    const d = data.value;
    const perAcct = d.accounts.map((a) => {
      const rows = d.items.filter((i) => i.acct === a.tag);
      return `${a.label} ${num(rows.reduce((s, i) => s + i.total, 0))}`;
    });
    return [
      { label: "总库存（两账号）", value: num(d.totalQty), hint: "领星全部仓库合计", color: "#0f766e" },
      { label: "可用库存", value: num(d.availQty), hint: "可立即发", color: "#2563eb" },
      { label: "明细行数", value: num(d.total), hint: "账号×SKU×仓库", color: "#7c3aed" },
      {
        label: "帕沃尔（14450）",
        value: num(d.items.filter((i) => i.acct === "14450").reduce((s, i) => s + i.total, 0)),
        hint: perAcct[0] || "",
        color: "#0891b2",
      },
      {
        label: "恒信（LM739）",
        value: num(d.items.filter((i) => i.acct === "LM739").reduce((s, i) => s + i.total, 0)),
        hint: perAcct[1] || "",
        color: "#a855f7",
      },
      {
        label: "仓库数",
        value: `${d.warehouses.length}`,
        hint: d.warehouses.map(shortWh).join(" / ") || "—",
        color: "#64748b",
      },
      {
        label: "帕沃尔 Kansas",
        value: num(pwTotal.value),
        hint: `${pwWithStock.value} 个 SKU 有货（该仓库存才有效）`,
        color: "#16a34a",
      },
    ];
  });

  // ---------------------------------------------------------------- 过滤
  const filtered = computed<LinxingItem[]>(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.items.filter((i) => {
      if (acctPick.value && i.acct !== acctPick.value) return false;
      if (whPick.value && i.wh !== whPick.value) return false;
      if (minQty.value > 0 && i.total < minQty.value) return false;
      if (!kw) return true;
      return [i.productName, i.code, i.variant, i.size, i.sku, i.wh]
        .some((t) => String(t || "").toLowerCase().includes(kw));
    });
  });

  type Row = LinxingItem & { _whs?: { wh: string; total: number }[] };

  /** 按产品合并：同一 productName 的多仓行合成一行，并记住逐仓与逐账号 */
  const shown = computed<Row[]>(() => {
    if (view.value !== "product") return filtered.value as Row[];
    const map = new Map<string, Row & { _acctQty: Record<string, number>; _whMap: Record<string, number> }>();
    for (const i of filtered.value) {
      const key = i.productName || i.sku;
      let r = map.get(key);
      if (!r) {
        r = {
          ...i,
          total: 0, avail: 0, lock: 0, transit: 0, box: 0, fbaReturn: 0,
          _acctQty: {}, _whMap: {},
        };
        map.set(key, r);
      }
      r.total += i.total;
      r.avail += i.avail;
      r.lock += i.lock;
      r.transit += i.transit;
      r.box += i.box;
      r.fbaReturn += i.fbaReturn;
      r._acctQty[i.acct] = (r._acctQty[i.acct] || 0) + i.total;
      if (i.wh) r._whMap[i.wh] = (r._whMap[i.wh] || 0) + i.total;
    }
    return [...map.values()].map((r) => ({
      ...r,
      _whs: Object.entries(r._whMap)
        .map(([wh, total]) => ({ wh, total }))
        .sort((a, b) => b.total - a.total),
    }));
  });

  /** 某行的逐账号库存（el-table 插槽给的是宽类型，这里统一收窄） */
  const acctQty = (row: unknown) =>
    ((row as { _acctQty?: Record<string, number> })._acctQty || {}) as Record<string, number>;

  const numSum = (rows: Row[], key: "total" | "avail") =>
    Math.round(rows.reduce((s, r) => s + (Number(r[key]) || 0), 0)).toLocaleString("zh-CN");

  function reset() {
    keyword.value = "";
    acctPick.value = "";
    whPick.value = "";
    minQty.value = 0;
  }
</script>

<style lang="scss" scoped>
  .lx-panel {
    padding-top: 4px;
  }

  .lx-stats {
    margin-bottom: 12px;
  }

  .lx-stat {
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
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .lx-bar {
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

  .lx-count {
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--el-text-color-regular);
  }

  .lx-tip {
    margin-left: 10px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }

  .lx-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }
  }

  .lx-dim {
    color: var(--el-text-color-placeholder);
  }

  .lx-warn {
    color: var(--el-color-warning);
    font-weight: 600;
  }

  .lx-acct {
    font-weight: 600;

    &--pw {
      color: #0891b2;
    }

    &--hx {
      color: #a855f7;
    }
  }

  .lx-wh {
    display: inline-block;
    margin-right: 8px;
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    white-space: nowrap;

    b {
      color: var(--el-text-color-primary);
    }
  }

  /* 帕沃尔 Kansas 列：与领星两个账号区分开，用绿色 */
  .lx-pw {
    color: #16a34a;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    border-bottom: 1px dashed rgba(22, 163, 74, 0.45);
    cursor: help;
  }

  .lx-rule {
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.8;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-lighter);
    border-radius: 6px;

    p {
      margin: 0 0 2px;
    }
  }

  .lx-acct-tag {
    margin-right: 12px;
  }
</style>
