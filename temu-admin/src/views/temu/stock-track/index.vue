<!--
  库存跟踪（九部 + 十部 · 6 个款号逐尺码）

  用户需求（2026-10-28）：每天 17:00 跟踪这 6 个款号**每个尺码**的库存
    204203 / 204207 / 204004 / 103903 / 103902 / 103901
  先只做了九部；当天追加「十部（warehouse-probe）也要跟踪同样这 6 个款」。

  数据来自 public/data/stock-track.json
  （scripts/goods/fetch_stock_track.py 抓两个看板生成）。

  口径（用户明确纠正过）：
    **实际库存 = 代发库存** —— 已经在海外仓、能直接发的件数。
    代发途中是还在路上的，不算可用；海外仓库存 = 实际库存 + 在途，只作参考。
-->
<template>
  <div class="st-page app-container">
    <!-- 页头 -->
    <div class="st-header">
      <div>
        <h2 class="st-header__title">库存跟踪 · {{ data.styles.length }} 款逐尺码</h2>
        <p class="st-header__desc">
          每天 17:00 抓一次九部 / 十部库存看板，按<b>款号 → 套系 → 尺码</b>摊开。
          <b>实际库存 = 代发库存</b>（已在海外仓、能直接发）；代发途中还在路上，<b>不算可用</b>。
        </p>
      </div>
      <div class="st-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          抓取 {{ data.generatedAt }}
        </el-tag>
        <el-tag v-if="cur.gen" type="success" effect="plain" size="small">
          看板快照 {{ cur.gen }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 部门切换：整页（指标/矩阵/明细/趋势）都跟着切 -->
    <div class="st-switch">
      <el-radio-group v-model="sourceKey" size="default">
        <el-radio-button v-for="k in sourceKeys" :key="k" :value="k">
          {{ data.sources[k].label }}
          <span class="st-switch__badge">{{ data.sources[k].totals.skus }}</span>
        </el-radio-button>
      </el-radio-group>
      <span class="st-switch__hint">
        跟踪款号：{{ data.styles.join(" / ") }}
      </span>
    </div>

    <!-- 看板还没更新到今天 -->
    <el-alert
      v-if="cur.stale"
      type="warning"
      :closable="false"
      show-icon
      class="st-alert"
      :title="`${cur.short}看板数据不是今天（${cur.gen || '无时间'}）—— 看板自己还没重算，下面数字是它上一版的`"
    />
    <!-- 某个源抓失败 -->
    <el-alert
      v-if="data.failed.length"
      type="error"
      :closable="false"
      show-icon
      class="st-alert"
      :title="`本次抓取失败：${data.failed.map(f => f === 'nine' ? '九部' : '十部').join('、')}（该部门数据是上一次的）`"
    />

    <!-- 合计 -->
    <el-row :gutter="12" class="st-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '12px 14px' }">
          <div class="st-stat__label">{{ s.label }}</div>
          <div class="st-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="st-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 筛选 -->
    <div class="st-bar">
      <el-radio-group v-model="mode" size="default">
        <el-radio-button value="matrix">尺码矩阵</el-radio-button>
        <el-radio-button value="detail">明细表</el-radio-button>
        <el-radio-button value="trend">趋势</el-radio-button>
      </el-radio-group>
      <el-select v-model="stylePick" size="default" style="width: 150px" placeholder="全部款号">
        <el-option label="全部款号" value="" />
        <el-option v-for="c in data.styles" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="statusPick" size="default" style="width: 150px">
        <el-option label="全部状态" value="" />
        <el-option label="现货已断" value="现货已断" />
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
        style="width: 250px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-switch v-model="onlyRisk" active-text="只看需补货 / 已断货" size="default" />
      <el-button size="default" plain @click="copyTodo">复制补货清单</el-button>
    </div>

    <!-- ① 尺码矩阵 -->
    <el-card v-if="mode === 'matrix'" shadow="never" class="st-card">
      <template #header>
        <div class="st-card__head">
          <span class="st-card__title">
            {{ cur.short }} · 尺码矩阵 · 每格 = <b>实际库存</b>（代发库存，已在海外仓可发）
          </span>
          <span class="st-hint">
            格子里：<b>上</b>=实际库存 ｜ <b>下</b>=在途 / 七天日均；
            底色：深红=现货已断，红=紧急补货，橙=正常补货，绿=够用，灰=滞销
          </span>
        </div>
      </template>

      <el-empty v-if="!matrix.cols.length" description="没有符合条件的尺码" :image-size="70" />
      <div v-else class="st-mx-wrap">
        <table class="st-mx">
          <thead>
            <tr>
              <th class="st-mx__sz">尺码</th>
              <th v-for="c in matrix.cols" :key="c.key" class="st-mx__h">
                <div class="st-mx__h-code">{{ c.code }}</div>
                <div class="st-mx__h-var">{{ c.variant }}<span v-if="c.note"> · {{ c.note }}</span></div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="sz in matrix.sizes" :key="sz">
              <th class="st-mx__sz">{{ sz }}</th>
              <td
                v-for="c in matrix.cols"
                :key="c.key + sz"
                :class="['st-mx__c', cellClass(matrix.cells[c.key + '|' + sz])]"
              >
                <template v-if="matrix.cells[c.key + '|' + sz]">
                  <div class="st-mx__v">{{ num(matrix.cells[c.key + '|' + sz]!.stock) }}</div>
                  <div class="st-mx__sub">
                    在途 {{ num(matrix.cells[c.key + '|' + sz]!.transit) }} ·
                    日均 {{ num(matrix.cells[c.key + '|' + sz]!.daily) }}
                  </div>
                </template>
                <span v-else class="st-mx__na">—</span>
              </td>
            </tr>
            <tr class="st-mx__sum">
              <th class="st-mx__sz">合计</th>
              <td v-for="c in matrix.cols" :key="'sum' + c.key" class="st-mx__c">
                <div class="st-mx__v">{{ num(matrix.colTotals[c.key]?.stock ?? 0) }}</div>
                <div class="st-mx__sub">
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
    <el-card v-else-if="mode === 'detail'" shadow="never" class="st-card">
      <template #header>
        <div class="st-card__head">
          <span class="st-card__title">{{ cur.short }} · 逐尺码明细（{{ detailRows.length }} 行）</span>
          <span class="st-hint">
            「比上次」= 与上一份快照的实际库存对比<template v-if="curDerived">；本部门看板无「可售天数/补货数量/库存状态」，为本页按九部同口径自算</template>
          </span>
        </div>
      </template>
      <el-table :data="detailRows" size="small" stripe border height="640" class="st-table">
        <el-table-column prop="code" label="款号" width="88" fixed sortable />
        <el-table-column prop="variant" label="套系" width="108" fixed>
          <template #default="{ row }">
            <span class="st-code">{{ row.variant }}</span>
            <span v-if="variantNote(row.variant)" class="st-code__note">{{ variantNote(row.variant) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="尺码" width="66" fixed align="center" />
        <!-- 十部的 SKU 是跨店聚合出来的，店铺要跟尺码挨着看，别塞到最右边 -->
        <el-table-column v-if="sourceKey === 'ten'" label="店铺" width="120" fixed>
          <template #default="{ row }">
            <span class="st-dim">{{ joinShops(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="库存状态" width="130">
          <template #default="{ row }">
            <el-tooltip
              v-if="isZeroBroken(asRow(row))"
              :content="`实际库存 0 件，现在发不出去。看板是拿含在途的口径算天数的，在途一多就可能被判成滞销/无需补货。`"
              placement="top"
            >
              <el-tag type="danger" size="small" effect="dark">现货已断</el-tag>
            </el-tooltip>
            <el-tag v-else :type="statusType(row.status)" size="small" effect="light">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stock" label="实际库存" width="118" align="right" sortable>
          <template #default="{ row }">
            <b :class="{ 'st-bad': row.stock <= 0 }">{{ num(row.stock) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="比上次" width="84" align="right">
          <template #default="{ row }">
            <span v-if="row.prevStock == null" class="st-dim">—</span>
            <span v-else :class="deltaClass(row.stock - row.prevStock)">
              {{ deltaText(row.stock - row.prevStock) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="transit" label="代发途中" width="98" align="right" sortable>
          <template #default="{ row }">{{ num(row.transit) }}</template>
        </el-table-column>
        <el-table-column label="海外仓库存" width="108" align="right" sortable :sort-by="'overseas'">
          <template #default="{ row }">
            <el-tooltip content="实际库存 + 代发途中（在途货还在路上，不能发）" placement="top">
              <span class="st-dim">{{ num(row.overseas) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="daily" label="七天日均" width="92" align="right" sortable>
          <template #default="{ row }">{{ row.daily ? row.daily.toFixed(1) : "—" }}</template>
        </el-table-column>
        <el-table-column prop="sellDays" label="可售天数" width="92" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'st-bad': row.daily > 0 && row.sellDays < 30 }">
              {{ row.daily > 0 ? row.sellDays : "—" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="whDays" label="现货可售天数" width="112" align="right" sortable>
          <template #default="{ row }">
            <el-tooltip content="只算实际库存，不含在途" placement="top">
              <span :class="{ 'st-bad': row.daily > 0 && row.whDays < 30 }">
                {{ row.daily > 0 ? row.whDays : "—" }}
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="refill" label="补货数量" width="98" align="right" sortable>
          <template #default="{ row }">
            <span :class="{ 'st-bad': row.refill > 0 }">{{ num(row.refill) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="openOrders" label="未交货订单" width="104" align="right" sortable>
          <template #default="{ row }">{{ num(row.openOrders) }}</template>
        </el-table-column>
        <el-table-column
          v-if="sourceKey === 'ten'"
          prop="sales10"
          label="10天销量"
          width="96"
          align="right"
          sortable
        >
          <template #default="{ row }">{{ num(row.sales10) }}</template>
        </el-table-column>
        <el-table-column v-if="sourceKey === 'nine'" label="预计断货日" width="112" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.stockout"
              :content="`不含在途：${row.stockoutNoTransit || '—'}`"
              placement="top"
            >
              <span :class="{ 'st-bad': isSoon(row.stockout) }">{{ shortDate(row.stockout) }}</span>
            </el-tooltip>
            <span v-else class="st-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="逐仓实际库存" min-width="220">
          <template #default="{ row }">
            <span v-if="!Object.keys(row.wh || {}).length" class="st-dim">—</span>
            <span v-for="(v, k) in row.wh" v-else :key="k" class="st-wh">
              {{ shortWh(String(k)) }} <b>{{ num(v) }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="sku" label="SKU" width="160" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- ③ 趋势 -->
    <template v-else>
      <el-card shadow="never" class="st-card">
        <template #header>
          <span class="st-card__title">
            {{ cur.short }} · 实际库存走势（近 {{ cur.history.dates.length }} 天）
          </span>
        </template>
        <ECharts v-if="cur.history.dates.length > 1" :options="trendChart" height="340px" />
        <el-empty
          v-else
          :description="`历史快照只有 ${cur.history.dates.length} 天，明天起开始形成走势`"
          :image-size="70"
        />
      </el-card>
      <el-card shadow="never" class="st-card">
        <template #header>
          <span class="st-card__title">{{ cur.short }} · 各款合计</span>
        </template>
        <el-table :data="styleTotals" size="small" stripe border>
          <el-table-column prop="code" label="款号" width="96" />
          <el-table-column label="套系" min-width="170">
            <template #default="{ row }">
              <span class="st-code">{{ row.variants }}</span>
            </template>
          </el-table-column>
          <el-table-column label="尺码数" width="80" align="right">
            <template #default="{ row }">{{ row.totals.skus }}</template>
          </el-table-column>
          <el-table-column label="实际库存" width="106" align="right">
            <template #default="{ row }"><b>{{ num(row.totals.stock) }}</b></template>
          </el-table-column>
          <el-table-column label="代发途中" width="104" align="right">
            <template #default="{ row }">{{ num(row.totals.transit) }}</template>
          </el-table-column>
          <el-table-column label="海外仓库存" width="112" align="right">
            <template #default="{ row }">
              <span class="st-dim">{{ num(row.totals.overseas) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="未交货订单" width="108" align="right">
            <template #default="{ row }">{{ num(row.totals.openOrders) }}</template>
          </el-table-column>
          <el-table-column label="状态分布" min-width="260">
            <template #default="{ row }">
              <el-tag v-if="row.totals.zero" type="danger" size="small" effect="dark">
                断 {{ row.totals.zero }}
              </el-tag>
              <el-tag v-if="row.totals.urgent" type="danger" size="small" effect="plain" class="st-tag-gap">
                紧急 {{ row.totals.urgent }}
              </el-tag>
              <el-tag v-if="row.totals.normal" type="warning" size="small" effect="plain" class="st-tag-gap">
                正常 {{ row.totals.normal }}
              </el-tag>
              <el-tag v-if="row.totals.ok" type="success" size="small" effect="plain" class="st-tag-gap">
                无需 {{ row.totals.ok }}
              </el-tag>
              <el-tag v-if="row.totals.dead" type="info" size="small" effect="plain" class="st-tag-gap">
                滞销 {{ row.totals.dead }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 口径 -->
    <el-card shadow="never" class="st-card st-rule">
      <template #header>
        <span class="st-card__title">口径与数据源</span>
      </template>
      <div class="st-rule__body">
        <p><b>取数范围</b>：{{ data.styles.join(" / ") }} 共 {{ data.styles.length }} 个款号。
          {{ cur.short }}看板全量 {{ cur.totalRows }} 行里命中 {{ cur.totals.skus }} 个尺码。</p>
        <p><b>实际库存（本页主口径）= 代发库存</b>：已经在海外仓、可以直接发的件数。
          <b>代发途中</b>还在路上，<b>不能当可用量</b>；
          <b>海外仓库存 = 实际库存 + 代发途中</b>，只作参考（页面上用浅灰显示）。</p>
        <p><b>{{ cur.short }}的口径</b>：{{ cur.note }}</p>
        <p><b>数据源</b>：<a :href="cur.url" target="_blank" rel="noreferrer">{{ cur.url }}</a></p>
        <p v-if="cur.verify"><b>看板自检</b>：{{ cur.verify }}</p>
        <p v-if="droppedRows.length">
          <b>已剔除的脏行</b>：<code>{{ droppedRows.join("、") }}</code>
          —— 看板源表里的残留 SKU（只有一个尺码、店铺为 BLA、库存/在途/销量/订单全为 0），
          不属于该款的正常变体，剔除以避免出现一个只有 0 的空列。
          规则见 <code>fetch_stock_track.py</code> 的 <code>TEN_DROP_ROWS</code>。
        </p>
        <p><b>每日 17:00 自动抓取</b>：定时任务 <code>TEMU-Stock-Track-Daily</code>，
          抓完刷新本面板（<code>scripts/goods/fetch_stock_track.py</code>）。</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import StockTrackAPI from "@/api/temu/stock-track";
  import type { StockTrackData, TrackRow, TrackSource, TrackTotals } from "@/api/temu/stock-track";

  defineOptions({ name: "TemuStockTrack" });

  const loading = ref(false);
  const mode = ref<"matrix" | "detail" | "trend">("matrix");
  const sourceKey = ref<string>("nine");
  const stylePick = ref("");
  const statusPick = ref("");
  const keyword = ref("");
  const onlyRisk = ref(false);

  const emptyTotals = (): TrackTotals => ({
    stock: 0, transit: 0, overseas: 0, openOrders: 0, refill: 0, daily: 0,
    sales10: 0, skus: 0, sizes: 0, urgent: 0, normal: 0, ok: 0, dead: 0, zero: 0,
  });

  const emptySource = (): TrackSource => ({
    key: "nine", label: "", short: "", url: "", gen: "", totalRows: 0, verify: "",
    day: "", stale: false, styles: [], totals: emptyTotals(),
    history: { dates: [], styles: {} }, note: "",
  });

  const data = ref<StockTrackData>({
    generatedAt: "",
    styles: [],
    sources: {},
    failed: [],
    rule: { note: "", source: "" },
  });

  async function load() {
    loading.value = true;
    try {
      data.value = await StockTrackAPI.getStockTrack();
      // 默认停在有数据的那个部门（万一某个源抓失败）
      const keys = Object.keys(data.value.sources);
      if (keys.length && !data.value.sources[sourceKey.value]) sourceKey.value = keys[0];
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载库存跟踪数据失败");
    } finally {
      loading.value = false;
    }
  }
  onMounted(load);

  const sourceKeys = computed(() => Object.keys(data.value.sources));
  const cur = computed<TrackSource>(() => data.value.sources[sourceKey.value] ?? emptySource());
  /** 这个部门的天数/补货/状态是不是本页自算的（十部看板没有这三列） */
  const curDerived = computed(() =>
    cur.value.styles.some((st) => st.variants.some((v) => v.rows.some((r) => r.derived)))
  );

  /** 被剔除的脏行（目前只有十部有），页面上明写出来，免得以后数字对不上以为出错了 */
  const droppedRows = computed(() => (cur.value as { dropped?: string[] }).dropped || []);

  // ---------------------------------------------------------------- 数字与格式
  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  const shortDate = (d?: string) => (d ? String(d).slice(5) : "—");

  const isSoon = (d?: string) => {
    if (!d) return false;
    const t = new Date(`${d}T00:00:00`).getTime();
    if (Number.isNaN(t)) return false;
    return t - Date.now() < 7 * 86400000;
  };

  const deltaText = (d: number) => (d > 0 ? `+${Math.round(d)}` : `${Math.round(d)}`);
  const deltaClass = (d: number) => (d > 0 ? "st-up" : d < 0 ? "st-down" : "st-dim");

  const statusType = (s: string) =>
    s === "现货已断" || s === "紧急补货"
      ? "danger"
      : s === "正常补货"
        ? "warning"
        : s === "无需补货"
          ? "success"
          : "info";

  /** 实际库存 0 但看板没判成"紧急补货" ⇒ 要额外喊出来 */
  const isZeroBroken = (row: TrackRow) => row.stock <= 0 && row.status !== "紧急补货";

  /** el-table 插槽给的是宽类型 `DefaultRow`，统一按 TrackRow 接 */
  const asRow = (row: unknown) => row as TrackRow;
  const joinShops = (row: unknown) => (asRow(row).shops || []).join(" / ") || "—";

  /**
   * 十部的仓库名很长（`帕沃尔·加州3仓` / `恒信·ZOO WAREHOUSE ⑥美西`），
   * 表里一格里塞 3~4 个仓会把行撑成一团。这里只留能区分的短名：
   *   帕沃尔·加州3仓 → 加州3
   *   帕沃尔·kansas仓 → kansas
   *   帕沃尔·新泽西2仓 → 新泽西2
   *   恒信·ZOO WAREHOUSE ⑥美西 → 恒信美西
   */
  const shortWh = (k: string) => {
    const s = String(k || "");
    if (s.includes("ZOO") || s.includes("⑥")) return "恒信美西";
    const tail = s.includes("·") ? (s.split("·").pop() || s) : s;
    return tail.replace(/仓$/, "");
  };

  const VARIANT_NOTE: Record<string, string> = {
    "4SetA": "4件套A", "4SetB": "4件套B", "4SetC": "4件套C",
    "5SetA": "5件套A", "5SetB": "5件套B", "5SetC": "5件套C",
    "5B": "5件套B", "1B": "1件套B", "2B": "2件套B",
    "2BBh": "2件套B(黑)", "2BH": "2件套B(黑)", "2H": "2件套H", "2L": "2件套L",
    "4B": "4件套B",
    "5BBBBhBh": "5件套(黑黑黑白黑)",
  };
  const variantNote = (v: string) => VARIANT_NOTE[v] || "";

  // ---------------------------------------------------------------- 全部行
  const allRows = computed<(TrackRow & { note: string })[]>(() =>
    cur.value.styles.flatMap((st) =>
      st.variants.flatMap((v) => v.rows.map((r) => ({ ...r, note: v.note })))
    )
  );

  // ---------------------------------------------------------------- 汇总指标
  const stats = computed(() => {
    const t = cur.value.totals;
    return [
      { label: "实际库存", value: num(t.stock), hint: "代发库存 · 已在海外仓，可直接发", color: "#0f766e" },
      { label: "代发途中", value: num(t.transit), hint: "在路上，尚不可发", color: "#b45309" },
      { label: "海外仓库存", value: num(t.overseas), hint: "实际库存 + 在途（含未到货）", color: "#2563eb" },
      { label: "未交货订单", value: num(t.openOrders), hint: "已下单待发", color: "#7c3aed" },
      {
        label: "现货已断（0 件）",
        value: `${t.zero}`,
        hint: "实际库存 = 0，现在一件都发不出去",
        color: t.zero ? "#991b1b" : "#16a34a",
      },
      { label: "紧急补货", value: `${t.urgent}`, hint: "可售 < 30 天", color: "#dc2626" },
      { label: "正常补货", value: `${t.normal}`, hint: "可售 30–60 天", color: "#d97706" },
      { label: "建议补货量", value: num(t.refill), hint: `${t.skus} 个尺码合计`, color: "#0891b2" },
    ];
  });

  // ---------------------------------------------------------------- 过滤
  const filteredRows = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return allRows.value.filter((r) => {
      if (stylePick.value && r.code !== stylePick.value) return false;
      if (statusPick.value && r.status !== statusPick.value) return false;
      // 「已断货」按**实际库存**判（代发库存 ≤ 0 就是真发不出去，
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
    const cols: { key: string; code: string; variant: string; note: string }[] = [];
    const seen = new Set<string>();
    for (const st of cur.value.styles) {
      if (stylePick.value && st.code !== stylePick.value) continue;
      for (const v of st.variants) {
        const key = `${st.code}|${v.variant}`;
        if (seen.has(key)) continue;
        if (!rows.some((r) => r.code === st.code && r.variant === v.variant)) continue;
        seen.add(key);
        cols.push({ key, code: st.code, variant: v.variant, note: v.note });
      }
    }
    const cells: Record<string, TrackRow | undefined> = {};
    const colTotals: Record<string, TrackTotals> = {};
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
   * 格子配色。实际库存 = 0 优先于看板状态：
   * 看板用「含在途」的口径算天数，在途一多就会把已断货的尺码判成"滞销/无需补货"，
   * 跟着涂灰/涂绿会让人以为有货 —— 这是最危险的"看着没事"。
   */
  const cellClass = (r?: TrackRow) => {
    if (!r) return "is-na";
    if (r.stock <= 0) return "is-zero";
    if (r.status === "紧急补货") return "is-urgent";
    if (r.status === "正常补货") return "is-normal";
    if (r.status === "无需补货") return "is-ok";
    return "is-dead";
  };

  // ---------------------------------------------------------------- 趋势
  /** 主线 = 实际库存；含在途的用同色虚线做对照 */
  const trendChart = computed(() => {
    const h = cur.value.history;
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
    cur.value.styles.map((st) => ({
      code: st.code,
      variants: st.variants.map((v) => v.variant).join(" / "),
      totals: st.totals,
    }))
  );

  // ---------------------------------------------------------------- 复制清单
  function copyTodo() {
    const risky = allRows.value.filter((r) => r.refill > 0 || r.stock <= 0);
    if (!risky.length) {
      ElMessage.info("当前没有需要补货或已断货的尺码");
      return;
    }
    const head = "部门\t款号\t套系\t尺码\t实际库存\t代发途中\t七天日均\t可售天数\t补货数量\t库存状态";
    const body = risky
      .sort((a, b) => b.refill - a.refill || a.stock - b.stock)
      .map((r) =>
        [cur.value.short, r.code, r.variant, r.size, r.stock, r.transit,
         r.daily.toFixed(1), r.sellDays, r.refill, r.status].join("\t")
      );
    const text = [head, ...body].join("\n");
    navigator.clipboard
      ?.writeText(text)
      .then(() => ElMessage.success(`已复制 ${risky.length} 个需补货 / 已断货尺码`))
      .catch(() => ElMessage.error("复制失败，请检查浏览器剪贴板权限"));
  }
</script>

<style lang="scss" scoped>
  .st-page {
    padding-bottom: 24px;
  }

  .st-header {
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
      max-width: 980px;
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

  .st-switch {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    margin-bottom: 12px;

    &__badge {
      margin-left: 4px;
      font-size: 11px;
      opacity: 0.75;
    }

    &__hint {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }

  .st-alert {
    margin-bottom: 12px;
  }

  .st-stats {
    margin-bottom: 12px;
  }

  .st-stat {
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

  .st-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 12px;
  }

  .st-card {
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

  .st-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  /* ---------------- 尺码矩阵 ---------------- */
  .st-mx-wrap {
    max-height: 660px;
    overflow: auto;
  }

  .st-mx {
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
      min-width: 106px;
      border-left: 2px solid var(--el-border-color);

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
      min-width: 106px;

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

      /* 实际库存 = 0：现货发不出去，比「紧急补货」更该被看见 */
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
  .st-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }
  }

  .st-code {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }

  .st-code__note {
    margin-left: 4px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }

  .st-bad {
    color: var(--el-color-danger);
    font-weight: 600;
  }

  .st-up {
    color: var(--el-color-success);
  }

  .st-down {
    color: var(--el-color-danger);
  }

  .st-dim {
    color: var(--el-text-color-placeholder);
  }

  .st-wh {
    display: inline-block;
    margin-right: 8px;
    font-size: 11.5px;
    color: var(--el-text-color-secondary);
    /* 仓库名之间不许断行 —— 一格里 3~4 个仓，断了会撑成三行、整表跟着变高 */
    white-space: nowrap;

    b {
      color: var(--el-text-color-primary);
    }
  }

  .st-tag-gap {
    margin-left: 4px;
  }

  .st-rule {
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
