<!--
  九部库存跟踪（TTSA-9 库存管理看板 · 6 个款号逐尺码）

  用户需求（2026-10-28）：每天 17:00 跟踪九部这 6 个款号**每个尺码**的库存
  204203 / 204207 / 204004 / 103903 / 103902 / 103901，挂到运营后台 /temu 下面。

  数据来自 public/data/nine-inv.json
  （scripts/goods/fetch_nine_inv.py 抓 TTSA-9 看板内嵌的 `const INV` 生成）。

  这台「矩阵表」是这一页的核心：行 = 尺码，列 = 每个款号（再按套系分块），
  格 = 海外仓库存 + 代发库存/代发途中。一眼看出哪个款哪个码要断货。
-->
<template>
  <div class="nine-page app-container">
    <!-- 页头 -->
    <div class="nine-header">
      <div>
        <h2 class="nine-header__title">九部库存跟踪 · 6 款逐尺码</h2>
        <p class="nine-header__desc">
          每天 17:00 抓一次 TTSA-9 库存管理看板，按<b>款号 → 套系 → 尺码</b>摊开。
          <b>实际库存 = 代发库存</b>（已经在海外仓、能直接发）；
          代发途中是还在路上的，<b>不算可用</b>。
        </p>
      </div>
      <div class="nine-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          抓取 {{ data.generatedAt }}
        </el-tag>
        <el-tag v-if="data.board.gen" type="success" effect="plain" size="small">
          看板快照 {{ data.board.gen }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 看板还没更新到今天：必须显眼，否则会拿旧数字做决定 -->
    <el-alert
      v-if="data.board.stale"
      type="warning"
      :closable="false"
      show-icon
      class="nine-alert"
      :title="`看板数据不是今天（${data.board.gen || '无时间'}）—— 说明 TTSA-9 看板自己还没重算，下面数字是它上一版的`"
    />

    <!-- 合计 -->
    <el-row :gutter="12" class="nine-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '12px 14px' }">
          <div class="nine-stat__label">{{ s.label }}</div>
          <div class="nine-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="nine-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 筛选 -->
    <div class="nine-bar">
      <el-radio-group v-model="mode" size="default">
        <el-radio-button value="matrix">尺码矩阵</el-radio-button>
        <el-radio-button value="detail">明细表</el-radio-button>
        <el-radio-button value="trend">趋势</el-radio-button>
      </el-radio-group>
      <el-select v-model="stylePick" size="default" style="width: 190px" placeholder="全部款号">
        <el-option label="全部款号" value="" />
        <el-option v-for="s in data.styles" :key="s.code" :label="s.code" :value="s.code" />
      </el-select>
      <el-select v-model="statusPick" size="default" style="width: 150px">
        <el-option label="全部状态" value="" />
        <el-option label="紧急补货" value="紧急补货" />
        <el-option label="正常补货" value="正常补货" />
        <el-option label="无需补货" value="无需补货" />
        <el-option label="滞销库存" value="滞销库存" />
      </el-select>
      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 SKU / 套系 / 尺码，如 4SetA 或 -XL"
        style="width: 260px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-switch v-model="onlyRisk" active-text="只看需补货 / 已断货" size="default" />
      <el-button size="default" plain @click="copyTodo">复制补货清单</el-button>
    </div>

    <!-- ① 尺码矩阵：行=尺码，列=款号·套系 -->
    <el-card v-if="mode === 'matrix'" shadow="never" class="nine-card">
      <template #header>
        <div class="nine-card__head">
          <span class="nine-card__title">尺码矩阵 · 每格 = <b>实际库存</b>（代发库存，已在海外仓可发）</span>
          <span class="nine-hint">
            格子里：<b>上</b>=实际库存（代发库存）｜ <b>下</b>=在途 / 七天日均；
            底色：红色=紧急补货，橙色=正常补货，绿=够用，灰=滞销
          </span>
        </div>
      </template>

      <el-empty v-if="!matrix.cols.length" description="没有符合条件的尺码" :image-size="70" />
      <div v-else class="nine-mx-wrap">
        <table class="nine-mx">
          <thead>
            <tr>
              <th class="nine-mx__sz">尺码</th>
              <th v-for="c in matrix.cols" :key="c.key" :class="['nine-mx__h', { 'is-style': c.isFirstOfStyle }]">
                <div class="nine-mx__h-code">{{ c.code }}</div>
                <div class="nine-mx__h-var">{{ c.variant }}<span v-if="c.note"> · {{ c.note }}</span></div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sz in matrix.sizes" :key="sz">
              <th class="nine-mx__sz">{{ sz }}</th>
              <td
                v-for="c in matrix.cols"
                :key="c.key + sz"
                :class="['nine-mx__c', cellClass(matrix.cells[c.key + '|' + sz])]"
              >
                <template v-if="matrix.cells[c.key + '|' + sz]">
                  <div class="nine-mx__v">{{ num(matrix.cells[c.key + '|' + sz]!.stock) }}</div>
                  <div class="nine-mx__sub">
                    在途 {{ num(matrix.cells[c.key + '|' + sz]!.transit) }} ·
                    日均 {{ num(matrix.cells[c.key + '|' + sz]!.daily) }}
                  </div>
                </template>
                <span v-else class="nine-mx__na">—</span>
              </td>
            </tr>
            <tr class="nine-mx__sum">
              <th class="nine-mx__sz">合计</th>
              <td v-for="c in matrix.cols" :key="'sum' + c.key" class="nine-mx__c">
                <div class="nine-mx__v">{{ num(matrix.colTotals[c.key]?.stock ?? 0) }}</div>
                <div class="nine-mx__sub">
                  在途 {{ num(matrix.colTotals[c.key]?.transit ?? 0) }} ·
                  日均 {{ num(matrix.colTotals[c.key]?.daily ?? 0) }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </el-card>

    <!-- ② 明细表 -->
    <el-card v-else-if="mode === 'detail'" shadow="never" class="nine-card">
      <template #header>
        <div class="nine-card__head">
          <span class="nine-card__title">逐尺码明细（{{ detailRows.length }} 行）</span>
          <span class="nine-hint">点「比上次」看与上一份快照的增减</span>
        </div>
      </template>
      <el-table :data="detailRows" size="small" stripe border height="640" class="nine-table">
        <el-table-column prop="code" label="款号" width="92" fixed sortable />
        <el-table-column prop="variant" label="套系" width="110" fixed>
          <template #default="{ row }">
            <span class="nine-code">{{ row.variant }}</span>
            <span v-if="variantNote(row.variant)" class="nine-code__note">{{ variantNote(row.variant) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="尺码" width="72" fixed align="center" />
        <el-table-column label="库存状态" width="132">
          <template #default="{ row }">
            <!-- 实际库存 0 但看板说「滞销/无需补货」：这是最危险的一类，必须明说出来 -->
            <el-tooltip
              v-if="row.stock <= 0 && row.status !== '紧急补货'"
              content="实际库存 0 件，现在发不出去。看板是拿「海外仓库存（含在途）」算天数的，所以在途一多就会把它判成滞销/无需补货。"
              placement="top"
            >
              <el-tag type="danger" size="small" effect="dark">现货已断</el-tag>
            </el-tooltip>
            <el-tag v-else :type="statusType(row.status)" size="small" effect="light">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stock" label="实际库存" width="120" align="right" sortable>
          <template #default="{ row }">
            <b :class="{ 'nine-bad': row.stock <= 0 }">{{ num(row.stock) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="比上次" width="88" align="right">
          <template #default="{ row }">
            <span v-if="row.prevStock == null" class="nine-dim">—</span>
            <span v-else :class="deltaClass(row.stock - row.prevStock)">
              {{ deltaText(row.stock - row.prevStock) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="transit" label="代发途中" width="100" align="right" sortable>
          <template #default="{ row }">{{ num(row.transit) }}</template>
        </el-table-column>
        <el-table-column label="海外仓库存" width="112" align="right" sortable :sort-by="'overseas'">
          <template #default="{ row }">
            <el-tooltip content="实际库存 + 代发途中（在途货还在路上，不能发）" placement="top">
              <span class="nine-dim">{{ num(row.overseas) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="daily" label="七天日均" width="94" align="right" sortable />
        <el-table-column prop="sellDays" label="可售天数" width="94" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'nine-bad': row.sellDays < 30 }">{{ row.daily > 0 ? row.sellDays : "—" }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="whDays" label="现货可售天数" width="118" align="right" sortable>
          <template #default="{ row }">
            <el-tooltip content="只算实际库存（代发库存），不含在途" placement="top">
              <span :class="{ 'nine-bad': row.daily > 0 && row.whDays < 30 }">
                {{ row.daily > 0 ? row.whDays : "—" }}
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="refill" label="补货数量" width="100" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'nine-bad': row.refill > 0 }">{{ num(row.refill) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="openOrders" label="未交货订单" width="104" align="right" sortable>
          <template #default="{ row }">{{ num(row.openOrders) }}</template>
        </el-table-column>
        <el-table-column prop="orderDays" label="订单+库存天数" width="116" align="right" sortable />
        <el-table-column label="预计断货日" width="118" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.stockout"
              :content="`不含在途：${row.stockoutNoTransit || '—'}`"
              placement="top"
            >
              <span :class="{ 'nine-bad': isSoon(row.stockout) }">{{ shortDate(row.stockout) }}</span>
            </el-tooltip>
            <span v-else class="nine-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="逐仓代发库存" min-width="190">
          <template #default="{ row }">
            <span v-if="!Object.keys(row.wh || {}).length" class="nine-dim">—</span>
            <span v-for="(v, k) in row.wh" v-else :key="k" class="nine-wh">
              {{ k }} <b>{{ num(v) }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="sku" label="SKU" width="150" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- ③ 趋势 -->
    <template v-else>
      <el-card shadow="never" class="nine-card">
        <template #header>
          <span class="nine-card__title">实际库存走势（近 {{ data.history.dates.length }} 天）</span>
        </template>
        <ECharts v-if="data.history.dates.length > 1" :options="trendChart" height="340px" />
        <el-empty
          v-else
          :description="`历史快照只有 ${data.history.dates.length} 天，明天起开始形成走势`"
          :image-size="70"
        />
      </el-card>
      <el-card shadow="never" class="nine-card">
        <template #header>
          <span class="nine-card__title">各款合计</span>
        </template>
        <el-table :data="styleTotals" size="small" stripe border>
          <el-table-column prop="code" label="款号" width="100" />
          <el-table-column label="套系" min-width="180">
            <template #default="{ row }">
              <span class="nine-code">{{ row.variants }}</span>
            </template>
          </el-table-column>
          <el-table-column label="尺码数" width="86" align="right">
            <template #default="{ row }">{{ row.totals.skus }}</template>
          </el-table-column>
          <el-table-column label="实际库存" width="110" align="right">
            <template #default="{ row }"><b>{{ num(row.totals.stock) }}</b></template>
          </el-table-column>
          <el-table-column label="代发途中" width="110" align="right">
            <template #default="{ row }">{{ num(row.totals.transit) }}</template>
          </el-table-column>
          <el-table-column label="海外仓库存" width="120" align="right">
            <template #default="{ row }">
              <span class="nine-dim">{{ num(row.totals.overseas) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="未交货订单" width="116" align="right">
            <template #default="{ row }">{{ num(row.totals.openOrders) }}</template>
          </el-table-column>
          <el-table-column label="状态分布" min-width="230">
            <template #default="{ row }">
              <el-tag v-if="row.totals.urgent" type="danger" size="small" effect="plain">紧急 {{ row.totals.urgent }}</el-tag>
              <el-tag v-if="row.totals.normal" type="warning" size="small" effect="plain" class="nine-tag-gap">
                正常 {{ row.totals.normal }}
              </el-tag>
              <el-tag v-if="row.totals.ok" type="success" size="small" effect="plain" class="nine-tag-gap">
                无需 {{ row.totals.ok }}
              </el-tag>
              <el-tag v-if="row.totals.dead" type="info" size="small" effect="plain" class="nine-tag-gap">
                滞销 {{ row.totals.dead }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 口径 -->
    <el-card shadow="never" class="nine-card nine-rule">
      <template #header>
        <span class="nine-card__title">口径与数据源</span>
      </template>
      <div class="nine-rule__body">
        <p><b>取数范围</b>：204203 / 204207 / 204004 / 103903 / 103902 / 103901 共 6 个款号，
          看板全量 {{ data.board.rows }} 行里命中 {{ data.board.picked }} 个尺码。</p>
        <p><b>实际库存（本页主口径）= 代发库存</b>：已经在海外仓、可以直接发的件数。
          <b>代发途中</b>是还在路上/未上架的，<b>不能当可用量</b>；
          <b>海外仓库存 = 实际库存 + 代发途中</b>，所以它比实际库存大，只作参考（页面上用浅灰显示）。</p>
        <p><b>列口径</b>：{{ data.rule.note }}</p>
        <p><b>数据源</b>：<a :href="data.board.url" target="_blank" rel="noreferrer">{{ data.board.url }}</a>
          （{{ data.rule.source }}）</p>
        <p><b>看板自检</b>：{{ data.board.verify || "—" }}</p>
        <p><b>每日 17:00 自动抓取</b>：定时任务 <code>TEMU-Nine-Inventory-Daily</code>，
          抓完刷新本面板（<code>scripts/goods/fetch_nine_inv.py</code>）。</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import NineAPI from "@/api/temu/nine";
  import type { NineInvData, NineInvRow, NineInvTotals } from "@/api/temu/nine";

  defineOptions({ name: "TemuNineInv" });

  const loading = ref(false);
  const mode = ref<"matrix" | "detail" | "trend">("matrix");
  const stylePick = ref("");
  const statusPick = ref("");
  const keyword = ref("");
  const onlyRisk = ref(false);

  const emptyTotals = (): NineInvTotals => ({
    stock: 0, transit: 0, overseas: 0, openOrders: 0, refill: 0, daily: 0,
    skus: 0, sizes: 0, urgent: 0, normal: 0, ok: 0, dead: 0,
  });

  const data = ref<NineInvData>({
    generatedAt: "",
    board: { gen: "", url: "", rows: 0, picked: 0, verify: "", stale: false },
    styles: [],
    totals: emptyTotals(),
    history: { dates: [], styles: {} },
    rule: { note: "", source: "" },
  });

  async function load() {
    loading.value = true;
    try {
      data.value = await NineAPI.getNineInv();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载九部库存数据失败");
    } finally {
      loading.value = false;
    }
  }
  onMounted(load);

  // ---------------------------------------------------------------- 数字与文案
  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  const shortDate = (d?: string) => (d ? String(d).slice(5) : "—");

  /** 断货日在 7 天内 → 标红提醒 */
  const isSoon = (d?: string) => {
    if (!d) return false;
    const t = new Date(`${d}T00:00:00`).getTime();
    if (Number.isNaN(t)) return false;
    return t - Date.now() < 7 * 86400000;
  };

  const deltaText = (d: number) => (d > 0 ? `+${Math.round(d)}` : `${Math.round(d)}`);
  const deltaClass = (d: number) =>
    d > 0 ? "nine-up" : d < 0 ? "nine-down" : "nine-dim";

  const statusType = (s: string) =>
    s === "紧急补货" ? "danger" : s === "正常补货" ? "warning" : s === "无需补货" ? "success" : "info";

  /** 套系中文备注（与后端 VARIANT_NOTE 同源，页面单独兜一份） */
  const VARIANT_NOTE: Record<string, string> = {
    "4SetA": "4件套A", "4SetB": "4件套B",
    "5SetA": "5件套A", "5SetB": "5件套B", "5B": "5件套B",
    "2BBh": "2件套B(黑)", "2H": "2件套H", "2L": "2件套L",
    "5BBBBhBh": "5件套(黑黑黑白黑)",
  };
  const variantNote = (v: string) => VARIANT_NOTE[v] || "";

  // ---------------------------------------------------------------- 过滤后的行
  /** 全部款号 → 摊平成一个按「款号+套系」排列的行数组（带款号/套系信息） */
  const allRows = computed< (NineInvRow & { note: string })[]>(() =>
    data.value.styles.flatMap((st) =>
      st.variants.flatMap((v) =>
        v.rows.map((r) => ({ ...r, note: v.note }))
      )
    )
  );

  // ---------------------------------------------------------------- 汇总指标
  // 「实际库存」= 代发库存：用户 2026-10-28 明确纠正 ——「代发库存才是实际库存」。
  // 海外仓库存 = 代发库存 + 代发途中，把在途也算进去了；在途货还在路上、发不出去，
  // 所以它不是可用量，只作参考放在后面（页面上也是浅灰）。
  const stats = computed(() => {
    const t = data.value.totals;
    // 现货已断：实际库存 = 0。单独成一个 KPI —— 看板会给这类行标「滞销库存 / 无需补货」
    // （它拿含在途的口径算天数），照着看板走会漏掉真正发不出货的尺码。
    const zero = allRows.value.filter((r) => r.stock <= 0).length;
    return [
      { label: "实际库存", value: num(t.stock), hint: "代发库存 · 已在海外仓，可直接发", color: "#0f766e" },
      { label: "代发途中", value: num(t.transit), hint: "在路上，尚不可发", color: "#b45309" },
      { label: "海外仓库存", value: num(t.overseas), hint: "实际库存 + 在途（含未到货）", color: "#2563eb" },
      { label: "未交货订单", value: num(t.openOrders), hint: "已下单待发", color: "#7c3aed" },
      {
        label: "现货已断（0 件）",
        value: `${zero}`,
        hint: "实际库存 = 0，现在一件都发不出去",
        color: zero ? "#991b1b" : "#16a34a",
      },
      { label: "紧急补货", value: `${t.urgent}`, hint: "可售 < 30 天", color: "#dc2626" },
      { label: "正常补货", value: `${t.normal}`, hint: "可售 30–60 天", color: "#d97706" },
      { label: "建议补货量", value: num(t.refill), hint: `${t.skus} 个尺码合计`, color: "#0891b2" },
    ];
  });

  const filteredRows = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return allRows.value.filter((r) => {
      if (stylePick.value && r.code !== stylePick.value) return false;
      if (statusPick.value && r.status !== statusPick.value) return false;
      // 「已断货」按**实际库存**判（代发库存 ≤ 0 就是真的发不出去，
      // 不能拿海外仓库存判 —— 那个数把在途也算进去了）
      if (onlyRisk.value && !(r.refill > 0 || r.stock <= 0)) return false;
      if (!kw) return true;
      return [r.sku, r.variant, r.size, r.status].some((t) =>
        String(t || "").toLowerCase().includes(kw)
      );
    });
  });

  const detailRows = computed(() => filteredRows.value);

  // ---------------------------------------------------------------- 矩阵
  const SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"];
  const sizeRank = (s: string) => {
    const i = SIZE_ORDER.indexOf(String(s || "").toUpperCase());
    return i >= 0 ? i : 99;
  };

  const matrix = computed(() => {
    const rows = filteredRows.value;
    // 列 = 款号·套系（按 6 个款号的固定顺序，套系按尺码顺序）
    const cols: { key: string; code: string; variant: string; note: string; isFirstOfStyle: boolean }[] = [];
    const seen = new Set<string>();
    for (const st of data.value.styles) {
      if (stylePick.value && st.code !== stylePick.value) continue;
      for (const v of st.variants) {
        const key = `${st.code}|${v.variant}`;
        if (seen.has(key)) continue;
        // 这个套系在当前筛选下有没有行
        if (!rows.some((r) => r.code === st.code && r.variant === v.variant)) continue;
        seen.add(key);
        cols.push({ key, code: st.code, variant: v.variant, note: v.note, isFirstOfStyle: true });
      }
    }
    const cells: Record<string, NineInvRow | undefined> = {};
    const colTotals: Record<string, NineInvTotals> = {};
    const sizes = new Set<string>();
    for (const r of rows) {
      const key = `${r.code}|${r.variant}`;
      if (!seen.has(key)) continue;
      cells[`${key}|${r.size}`] = r;
      sizes.add(r.size);
      const t = (colTotals[key] ||= emptyTotals());
      t.overseas += r.overseas;
      t.stock += r.stock;
      t.transit += r.transit;
      t.daily += r.daily;
      t.openOrders += r.openOrders;
      t.refill += r.refill;
      t.skus += 1;
    }
    return {
      cols,
      cells,
      colTotals,
      sizes: [...sizes].sort((a, b) => sizeRank(a) - sizeRank(b)),
    };
  });

  /**
   * 格子配色：按这一行的库存状态。
   *
   * ⚠ 但「实际库存 = 0」必须单独标出来，优先级高于看板给的状态。
   * 实测（2026-10-28）有 6 个尺码实际库存为 0，看板却标「滞销库存 / 无需补货」——
   * 因为看板用「海外仓库存（= 实际 + 在途）」算可售天数：204207-4SetB-S 现货 0 件、
   * 在途 250 件 ⇒ 250 天 ⇒ 判成"货太多"。可实际上现在**一件都发不出去**。
   * 这种格子若跟着涂成灰/绿，看图的人会以为有货，正是最危险的那种"看着没事"。
   */
  const cellClass = (r?: NineInvRow) => {
    if (!r) return "is-na";
    if (r.stock <= 0) return "is-zero";
    if (r.status === "紧急补货") return "is-urgent";
    if (r.status === "正常补货") return "is-normal";
    if (r.status === "无需补货") return "is-ok";
    return "is-dead";
  };

  // ---------------------------------------------------------------- 趋势
  /** 趋势图画**实际库存**（代发库存）—— 用户 2026-10-28：「代发库存才是实际库存」。
   *  海外仓库存（含在途）保留为第二条：细虚线、同色，鼠标悬停能对比"在途到货后能到多少"，
   *  但它不该是主线，看走势的人关心的是"我现在有多少能发"。 */
  const trendChart = computed(() => {
    const h = data.value.history;
    const styles = Object.keys(h.styles);
    const series = styles.flatMap((s) => [
      {
        name: s,
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 4,
        data: h.styles[s].stock,
      },
      {
        name: `${s} 含在途`,
        type: "line",
        smooth: true,
        symbol: "none",
        lineStyle: { type: "dashed", width: 1, opacity: 0.45 },
        data: h.styles[s].overseas,
      },
    ]);
    return {
      tooltip: { trigger: "axis" },
      legend: { data: styles, top: 0, type: "scroll" },
      grid: { left: 58, right: 24, top: 40, bottom: 46 },
      xAxis: { type: "category", data: h.dates.map((d) => String(d).slice(5)) },
      yAxis: { type: "value", name: "件" },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 10 }],
      series,
    };
  });

  const styleTotals = computed(() =>
    data.value.styles.map((st) => ({
      code: st.code,
      variants: st.variants.map((v) => v.variant).join(" / "),
      totals: st.totals,
    }))
  );

  // ---------------------------------------------------------------- 复制清单
  function copyTodo() {
    const risky = allRows.value.filter((r) => r.refill > 0);
    if (!risky.length) {
      ElMessage.info("当前没有需要补货的尺码");
      return;
    }
    const head = "款号\t套系\t尺码\t海外仓库存\t代发库存\t代发途中\t七天日均\t可售天数\t补货数量\t库存状态";
    const body = risky
      .sort((a, b) => b.refill - a.refill)
      .map((r) =>
        [r.code, r.variant, r.size, r.overseas, r.stock, r.transit,
         r.daily, r.sellDays, r.refill, r.status].join("\t")
      );
    const text = [head, ...body].join("\n");
    navigator.clipboard
      ?.writeText(text)
      .then(() => ElMessage.success(`已复制 ${risky.length} 个需补货尺码`))
      .catch(() => ElMessage.error("复制失败，请检查浏览器剪贴板权限"));
  }
</script>

<style lang="scss" scoped>
  .nine-page {
    padding-bottom: 24px;
  }

  .nine-header {
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
      max-width: 960px;
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

  .nine-alert {
    margin-bottom: 12px;
  }

  .nine-stats {
    margin-bottom: 12px;
  }

  .nine-stat {
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

  .nine-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 12px;
  }

  .nine-card {
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
  }

  .nine-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  /* ---------------- 尺码矩阵 ---------------- */
  .nine-mx-wrap {
    max-height: 660px;
    overflow: auto;
  }

  .nine-mx {
    border-collapse: separate;
    border-spacing: 0;
    width: 100%;
    font-variant-numeric: tabular-nums;

    th,
    td {
      padding: 6px 8px;
      border-right: 1px solid var(--el-border-color-lighter);
      border-bottom: 1px solid var(--el-border-color-lighter);
      white-space: nowrap;
      text-align: center;
    }

    thead th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--el-bg-color-page);
    }

    &__sz {
      position: sticky;
      left: 0;
      z-index: 1;
      width: 66px;
      font-weight: 600;
      background: var(--el-bg-color-page);
    }

    thead &__sz {
      z-index: 3;
    }

    &__h {
      min-width: 108px;

      &.is-style {
        border-left: 2px solid var(--el-border-color);
      }

      &-code {
        font-size: 12.5px;
        font-weight: 700;
      }

      &-var {
        font-size: 11px;
        font-weight: 400;
        color: var(--el-text-color-secondary);
      }
    }

    &__c {
      min-width: 108px;

      &.is-urgent {
        background: #fef2f2;
        color: #b91c1c;
      }

      &.is-normal {
        background: #fffbeb;
        color: #b45309;
      }

      &.is-ok {
        background: #f0fdf4;
        color: #15803d;
      }

      &.is-dead {
        background: var(--el-fill-color-lighter);
        color: var(--el-text-color-secondary);
      }

      /* 实际库存 = 0：现货发不出去，比「紧急补货」更该被看见，
         用深红底 + 虚线边框，跟"紧急补货"的浅红区分开 */
      &.is-zero {
        background: #fde8e8;
        color: #991b1b;
        box-shadow: inset 0 0 0 1px #f0a0a0;
      }

      &.is-na {
        color: var(--el-text-color-placeholder);
      }
    }

    &__v {
      font-size: 14px;
      font-weight: 700;
    }

    &__sub {
      font-size: 10.5px;
      opacity: 0.8;
    }

    &__na {
      opacity: 0.5;
    }

    &__sum {
      th,
      td {
        position: sticky;
        bottom: 0;
        background: var(--el-bg-color-page);
        font-weight: 700;
      }
    }
  }

  /* ---------------- 明细表 ---------------- */
  .nine-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }
  }

  .nine-code {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }

  .nine-code__note {
    margin-left: 4px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }

  .nine-bad {
    color: var(--el-color-danger);
    font-weight: 600;
  }

  .nine-up {
    color: var(--el-color-success);
  }

  .nine-down {
    color: var(--el-color-danger);
  }

  .nine-dim {
    color: var(--el-text-color-placeholder);
  }

  .nine-wh {
    display: inline-block;
    margin-right: 8px;
    font-size: 11.5px;
    color: var(--el-text-color-secondary);

    b {
      color: var(--el-text-color-primary);
    }
  }

  .nine-tag-gap {
    margin-left: 4px;
  }

  .nine-rule {
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

      a {
        color: var(--el-color-primary);
        word-break: break-all;
      }
    }
  }
</style>
