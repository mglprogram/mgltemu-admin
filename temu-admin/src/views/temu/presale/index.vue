<!--
  TEMU 销量-库存（原「预售建议」）

  口径（2026-10-10 用户确认）：
      可开预售 = 预计未来7日总销量 > 0
                且（看板代发库存 = 0 或 现货可售天数 < 阈值 7 天）
                且 领星在途 > 0 且 有到货日

  三方数据：
    · TEMU 销量分析页 —— 只取「已售罄 + 即将售罄」的尺码行，带每个尺码的预计未来7日销量
    · 海外仓看板 warehouse-probe —— 代发库存（已经在海外仓的现货）
    · 飞书领星多维表格 —— 到货计划甘特（未入仓批次 + 到货日）/ 库存覆盖甘特（空窗、缺口）

  数据来自 public/data/presale.json（scripts/bel_goods/build_presale_data.py 导出）。
-->
<template>
  <div class="pre-page app-container">
    <!-- 页头 -->
    <div class="pre-header">
      <div>
        <h2 class="pre-header__title">销量-库存 · 加库存提醒</h2>
        <p class="pre-header__desc">
          两店「已售罄 / 即将售罄」的尺码 → 对着海外仓现货 + 领星到货表查一遍：哪些能开预售、哪些能直接重新加库存
        </p>
      </div>
      <div class="pre-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据生成 {{ data.generatedAt }}
        </el-tag>
        <el-tag v-if="data.sources.看板.builtAt" type="success" effect="plain" size="small">
          看板快照 {{ data.sources.看板.builtAt }}
        </el-tag>
        <el-tag v-if="data.sources.飞书.fetchedAt" type="warning" effect="plain" size="small">
          领星 {{ data.sources.飞书.fetchedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 口径 -->
    <el-alert type="info" :closable="false" show-icon class="pre-alert" title="判定口径">
      <div class="pre-alert__body">
        <div>
          <b>可开预售</b>：{{ data.rule.text }}
        </div>
        <div>
          <b>可以重新加库存</b>：{{ data.rule.restock }}
        </div>
        <div>
          现货 = 海外仓看板「代发库存」（<b>已经在仓</b>，代发途中不算）；在途 = 飞书领星「库存覆盖甘特」的
          <b>在途件数</b>（与你在飞书里看到的同一个数），到货日取「到货计划甘特」的未入仓批次；阈值 = 现货可售天数 &lt;
          {{ data.rule.threshold }} 天。
        </div>
        <div>
          单位：<b>左边是领星/看板的原始件数（5Set），右边 ⇒ 后面是折成 TEMU 十件套的件数</b>（10SetA = 2 ×
          5SetA）。TEMU 侧是新开临时标签页读的，不会动你自己页面上正在用的查询和筛选。
        </div>
        <div v-if="data.sources.飞书.empty" class="pre-alert__warn">
          ⚠ 这次没读到飞书领星数据（{{ data.sources.飞书.base }}），在途/到货日列为空。
        </div>
      </div>
    </el-alert>

    <!-- 今天要处理的：两店合计 -->
    <el-card shadow="never" class="pre-card pre-todo">
      <template #header>
        <div class="pre-card__head">
          <span class="pre-card__title">
            今天要处理的（两店合计）· 可开预售 {{ presaleAll.length }} · 可加库存 {{ restockAll.length }}
          </span>
          <el-button size="small" type="primary" plain @click="copyTodo">复制待办清单</el-button>
        </div>
      </template>

      <div class="pre-todo__row">
        <el-tag type="danger" effect="dark" size="small" class="pre-todo__label">可开预售</el-tag>
        <span v-if="!presaleAll.length" class="pre-hint">
          没有 —— 需要「现货撑不住 + 在途有货且有到货日」三个条件同时满足
        </span>
        <el-tooltip v-for="r in presaleAll" :key="r.skuCode" placement="top" :content="r.reason">
          <el-tag class="pre-sku" type="danger" effect="plain" @click="copyRow(r)">
            {{ r.skuCode }}<span class="pre-sku__hint">（{{ r.status }} · 够 {{ days(r) }} 天 ·
            在途 {{ num(r.transitRaw) }} · {{ r.arrivalDate }} 到）</span>
          </el-tag>
        </el-tooltip>
      </div>

      <div class="pre-todo__row">
        <el-tag type="warning" effect="dark" size="small" class="pre-todo__label">可加库存</el-tag>
        <span v-if="!restockAll.length" class="pre-hint">没有 —— 仓里有现货且 TEMU 已断/快断的尺码才列</span>
        <el-tooltip v-for="r in restockAll" :key="r.skuCode" placement="top" :content="r.reason">
          <el-tag class="pre-sku" type="warning" effect="plain" @click="copyRow(r)">
            {{ r.skuCode }}<span class="pre-sku__hint">（{{ r.status }} · TEMU可售
            {{ num(r.available) }}<template v-if="r.availDays !== null && r.availDays !== undefined">（{{ r.availDays.toFixed(1) }} 天）</template>
            · 现货 {{ num(r.probeStockRaw) }} 件）</span>
          </el-tag>
        </el-tooltip>
      </div>
    </el-card>

    <!-- 店铺（含「全部」合看） -->
    <div class="pre-bar">
      <el-radio-group v-model="storeKey" size="small">
        <el-radio-button v-for="t in tabs" :key="t.key" :value="t.key">
          {{ t.label }}<span class="pre-bar__num">（{{ t.count }}）</span>
        </el-radio-button>
      </el-radio-group>
      <el-tag v-if="current" :type="current.live ? 'success' : 'warning'" effect="plain" size="small">
        数据来源：{{ current.source || "无" }}
      </el-tag>
    </div>

    <!-- 概览 -->
    <el-row :gutter="12" class="pre-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="pre-stat__label">{{ s.label }}</div>
          <div class="pre-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="pre-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 工具条 -->
    <el-card shadow="never" class="pre-card">
      <div class="pre-toolbar">
        <div class="pre-toolbar__filters">
          <el-radio-group v-model="filter" size="small">
            <el-radio-button value="presale">只看可开预售</el-radio-button>
            <el-radio-button value="todo">要处理的</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
          </el-radio-group>
          <el-input
            v-model="keyword"
            placeholder="搜 SKU货号 / 标题"
            clearable
            size="small"
            class="pre-search"
          />
        </div>
        <div class="pre-toolbar__ops">
          <el-button size="small" type="primary" plain @click="copyList">
            复制预售清单
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 明细 -->
    <el-card shadow="never" class="pre-card">
      <template #header>
        <div class="pre-card__head">
          <span class="pre-card__title">
            {{ current?.store || "—" }} · 尺码明细（{{ displayRows.length }} / {{ current?.rowCount ?? 0 }} 行）
          </span>
          <span class="pre-hint">
            按「可开预售 → 先加库存 → 要补货 → 不用开」排序，同组内现货天数少的在前
          </span>
        </div>
      </template>

      <el-empty
        v-if="!current || !current.rows.length"
        :image-size="70"
        description="这一店没有数据：先在紫鸟里打开该店窗口（停在销量分析页）再重跑导出脚本"
      />

      <el-table
        v-else
        :data="displayRows"
        size="default"
        stripe
        :row-class-name="rowClass"
        :default-sort="{ prop: 'stockDays', order: 'ascending' }"
      >
        <!--
          店铺不单独占一列：单店页签本来就只有一店，全部页签把店标塞进货号格。
          这样列宽在三个页签里完全一致 —— 原来单店页签少一列、全部页签多一列，
          多出来那一列就把表格挤到横向滚动，而 el-table 横向滚动时**固定列会盖住**
          后面的列（2026-10-15 用户截图：TEMU可售 被「操作」压住）。
        -->
        <!-- ⚠ 固定列必须给确定宽度（width 而不是 min-width）：min-width 时 el-table
             算不出固定层宽度，会把货号截成「BHY-MaternitySet...」还占着空位。 -->
        <el-table-column prop="skuCode" label="SKU货号" width="280" fixed show-overflow-tooltip>
          <template #default="{ row }">
            <span class="pre-sku" @click="copyRow(row)">
              <el-tag
                v-if="storeKey === ALL"
                size="small"
                effect="plain"
                class="pre-sku__store"
                :type="row.store === 'BHY' ? 'primary' : 'warning'"
              >
                {{ row.store }}
              </el-tag>
              {{ row.skuCode }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="尺码" width="54" align="center" />
        <el-table-column label="状态" width="86" align="center">
          <template #header>
            <span>状态</span>
            <el-tooltip placement="top" content="TEMU 侧的库存状态：已售罄 / 即将售罄。这一页只列这两类尺码。">
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-tag :type="row.status === '已售罄' ? 'danger' : 'warning'" size="small" effect="light">
              {{ row.status || "—" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="预测7日" width="82" align="right" sortable prop="forecast7">
          <template #header>
            <span>预测7日</span>
            <el-tooltip
              placement="top"
              content="TEMU 预测这个尺码未来 7 天卖多少（销量分析页「预计未来7日」）。日均 = 预测7日 ÷ 7，不再单列。"
            >
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">{{ num(row.forecast7) }}</template>
        </el-table-column>
        <!-- 今日销量（本尺码）+ 同款今日（整款合计）合成一列两行：列越少越不容易横向挤爆 -->
        <el-table-column
          label="今日销量"
          width="104"
          align="right"
          sortable
          prop="todaySales"
        >
          <template #header>
            <span>今日销量</span>
            <el-tooltip
              placement="top"
              content="上排 = 这个尺码今天卖了多少（TEMU 销量分析页「今日SKU销量」）；下排 = 同款今日（整个产品的不同变体/两店合计，鼠标悬浮看是哪些货号加起来的，⚠ 整款合计不摊到单个尺码）。"
            >
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span v-if="(row.todaySales ?? 0) > 0" class="pre-today">
              {{ num(row.todaySales) }}
            </span>
            <span v-else class="pre-hint">{{ row.todaySales === null || row.todaySales === undefined ? "—" : "0" }}</span>
            <span
              v-if="familyToday(row)"
              class="pre-family"
              :title="familyTitle(row)"
            >
              同款 {{ num(familyToday(row)!.todaySales) }}<template v-if="familyToday(row)!.members.length > 1"
              ><span class="pre-hint"> ·{{ familyToday(row)!.members.length }} 变体</span></template>
            </span>
            <span v-else class="pre-family pre-family--none">同款 —</span>
          </template>
        </el-table-column>
        <el-table-column
          label="可售/在仓/在途"
          min-width="232"
          align="right"
          sortable
          prop="available"
        >
          <template #header>
            <span>可售/在仓/在途</span>
            <el-tooltip placement="top" content="上排 = TEMU 申报的「销售可用库存」和它够卖几天（加库存看的就是它）；下排 = 海外仓现货（领星 5Set 原始件数 ⇒ 折 TEMU 10Set）+ 在途件数。仓库里再多货，没申报到 TEMU 也卖不出去。">
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span :class="{ 'pre-warn': (row.availDays ?? 99) < (data.rule.threshold || 7) }">
              {{ num(row.available) }}<span
                v-if="row.availDays !== null && row.availDays !== undefined"
                class="pre-hint pre-avail-days"
              >
                （{{ row.availDays.toFixed(1) }} 天）</span>
            </span>
            <span
              class="pre-stock2"
              :title="`海外仓现货 ${num(row.probeStockRaw)} 件（5Set）⇒ ${num(row.probeStock)}（10Set）；在途 ${num(row.transitRaw)} ⇒ ${num(row.transit)}；领星在仓 ${num(row.lingxingAvailable)}；看板代发途中 ${num(row.probeTransitRaw)}`"
            >
              <span :class="{ 'pre-zero': (row.probeStockRaw ?? 0) <= 0 }">
                仓 {{ num(row.probeStockRaw) }}<span class="pre-arrow"> ⇒ </span>{{ num(row.probeStock) }}
              </span>
              <span class="pre-stock2__sep">·</span>
              <span>途 {{ num(row.transitRaw) }}<span class="pre-arrow"> ⇒ </span>{{ num(row.transit) }}</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column
          label="现货天数"
          width="90"
          align="right"
          sortable
          prop="stockDays"
        >
          <template #header>
            <span>现货天数</span>
            <el-tooltip placement="top" content="海外仓现货能撑几天 = 折后现货 ÷ 日均需求；低于阈值的会标黄，这时才需要加库存 / 开预售。">
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span :class="{ 'pre-warn': isShort(row) }">
              {{ row.stockDays === null || row.stockDays === undefined ? "—" : row.stockDays.toFixed(1) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="建议加" width="82" align="right" sortable prop="topup.increase">
          <template #header>
            <span>建议加</span>
            <el-tooltip
              placement="top"
              content="加库存建议（只算不写）：目标 = min(30天需求, 海外仓现货)，增加量 = 目标 − TEMU可售。后台「立即加库存」由人手动点。"
            >
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span v-if="row.topup && row.topup.increase > 0" class="pre-topup">
              +{{ row.topup.increase }}
            </span>
            <el-tooltip v-else placement="left" :content="(row.topup && row.topup.skip) || '无需加库存'">
              <span class="pre-hint">—</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="到货" width="86" align="right" sortable prop="arrivalDays">
          <template #header>
            <span>到货</span>
            <el-tooltip placement="top" content="最近到货日 + 还有几天（负 = 逾期未入仓）；悬浮看该批次覆盖多少件、领星空窗与风险。">
              <el-icon class="pre-th-hint"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <span
              v-if="row.arrivalDate"
              :class="{ 'pre-warn': (row.arrivalDays ?? 0) < 0 }"
              :title="`最近到货 ${row.arrivalDate}${row.arrivalSource ? ' · ' + row.arrivalSource : ''}${row.arrivalTicketQty ? ' · 票级 ' + row.arrivalTicketQty + ' 件' : ''}｜领星空窗 ${num(row.lingxingGapDays)} 天 · 风险 ${row.lingxingRisk || '—'}`"
            >
              {{ shortDate(row.arrivalDate) }}<span
                v-if="row.arrivalDays !== null && row.arrivalDays !== undefined"
                class="pre-hint pre-arrival-days"
              >{{
                (row.arrivalDays ?? 0) < 0 ? `逾期 ${-(row.arrivalDays ?? 0)} 天` : `${row.arrivalDays} 天后`
              }}</span>
            </span>
            <span v-else class="pre-hint" :title="`来源：${row.arrivalSource || '—'}；领星空窗 ${num(row.lingxingGapDays)} 天 · 风险 ${row.lingxingRisk || '—'}`">
              {{ row.lingxingSegType || "—" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="动作" width="94" align="center" sortable :sort-method="byVerdict">
          <template #default="{ row }">
            <span v-if="row.actions && row.actions.length" class="pre-actions">
              <el-tag
                v-for="a in row.actions"
                :key="a"
                :type="a === '开预售' ? 'danger' : 'warning'"
                size="small"
                effect="dark"
                class="pre-action"
              >
                {{ a }}
              </el-tag>
            </span>
            <el-tag v-else-if="row.verdict === '要补货'" type="info" size="small" effect="plain">要补货</el-tag>
            <span v-else class="pre-hint">不用动</span>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="pre-hint">{{ row.reason }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import PresaleAPI from "@/api/temu/presale";
  import type { PresaleData, PresaleRow, PresaleStore } from "@/api/temu/presale";
  import GoodsAPI, { type GoodsData } from "@/api/temu/goods";
  import { storeOf, todaySalesIndex, type FamilyRow } from "@/api/temu/goods/product-family";
  import { useRoute } from "vue-router";

  defineOptions({ name: "TemuPresale" });

  /** 用于接收大屏点款号带过来的 ?code= 深链 */
  const route = useRoute();

  /** el-table 插槽给的是宽松的 DefaultRow，统一按「部分字段」接住 */
  type RowLike = Partial<PresaleRow>;

  const loading = ref(false);
  const data = ref<PresaleData>({
    generatedAt: "",
    rule: { text: "", restock: "", threshold: 7, forecast: "", note: "" },
    sources: {
      看板: { builtAt: "", rows: 0, url: "" },
      飞书: { base: "", fetchedAt: "", counts: {}, empty: false },
    },
    stores: [],
  });

  // ---------------------------------------------------------------- 同款今日销量
  // presale.json 只有「已售罄/即将售罄」的几行，且是按 SKU（尺码）给的今日销量。
  // 想看「这个产品今天一共卖了多少」，得回到 goods.json 的在售全量里按同款合并 ——
  // 合并规则与首页大屏共用一份（@/api/temu/goods/product-family）：
  //   同款变体相加；202504-10 全系列归一行；H103903-4B ×2 / 6B ×3 累加到 H103903-2B。
  // ⚠ 一个 SKC 会拆成 5 个尺码行，所以**只有合计列**（不折算到单个尺码）——按尺码摊分是编数。
  const goods = ref<GoodsData | null>(null);

  async function loadGoods() {
    try {
      goods.value = await GoodsAPI.getGoodsData();
    } catch {
      goods.value = null; // 拿不到就这一列显示「—」，不影响页面上其它判断
    }
  }

  /** `店铺|SKU货号` → 同款合计（含成员与倍率说明） */
  const familyIndex = computed(() => {
    const g = goods.value;
    if (!g?.stores) return new Map<string, FamilyRow>();
    const rows = Object.values(g.stores).flatMap((s) =>
      (s.items || []).map((it) => ({
        code: it.code,
        store: it.store || s.label || "",
        todaySales: it.todaySales ?? 0,
        sales7: it.sales7 ?? 0,
        stock: it.stock ?? 0,
      }))
    );
    return todaySalesIndex(rows);
  });

  /**
   * 当前尺码行的同款合计（查不到返回 undefined → 显示「—」）。
   *
   * ⚠ 这一页的行是 **SKU**（`BHY-MaternitySets-4SetA-XXL`），而 goods.json / 同款索引
   *   是按 **SKC**（`BHY-MaternitySets-4SetA`）建的 —— 直接拿 skuCode 查会全部查不到
   *   （2026-10-15 实测：整列显示「同款 —」）。所以先按 SKC 查，再兜底按 skuCode 查。
   */
  function familyToday(row: RowLike): FamilyRow | undefined {
    const sku = String(row.skuCode || "");
    if (!sku) return undefined;
    const idx = familyIndex.value;
    const store = row.store || storeOf(sku);
    const size = String(row.size || "");
    const skc = size && sku.endsWith(`-${size}`) ? sku.slice(0, -(size.length + 1)) : "";
    return (
      (skc ? idx.get(`${store}|${skc}`) : undefined) ||
      idx.get(`${store}|${sku}`) ||
      idx.get(`|${skc}`) ||
      idx.get(`|${sku}`)
    );
  }

  /** 悬浮说明：列出这个合计是哪些货号加起来的（带倍率） */
  function familyTitle(row: RowLike): string {
    const fam = familyToday(row);
    if (!fam) return "";
    const head = fam.ruleNote || "同款（同一产品的不同变体）今日销量合计";
    return `${head}\n今日合计 ${fam.todaySales} 件 = ${fam.members.join(" + ")}\n${fam.memberNotes.join("\n")}`;
  }

  const familyHint = computed(
    () =>
      "「今日销量」列里下排的「同款 N」= 这个产品（同一 SPU 下不同变体/店铺）今天的销量合计，" +
      "来自在售款明细的今日销量，按与大屏榜单相同的口径合并：" +
      "同款变体相加；202504-10 全系列累加到 BHY-202504-10SetA；" +
      "H103903-4B(×2) / 6B(×3) 累加到 H103903-2B。" +
      "⚠ 一个款有多个尺码，这一行是**整款合计**，不摊到单个尺码（点开悬浮看是哪些货号加起来的）。" +
      (goods.value ? `（在售数据 ${goods.value.generatedAt || "—"}）` : "（在售数据没加载到）")
  );

  /** 「全部」这个特殊页签的 key —— 数据是两店实时合并出来的，不在 presale.json 里 */
  const ALL = "__all__";

  const storeKey = ref(ALL);
  const filter = ref<"presale" | "todo" | "all">("todo");
  const keyword = ref("");

  const VERDICT_ORDER: Record<string, number> = {
    可开预售: 0,
    先加库存: 1,
    要补货: 2,
    不用开: 3,
    跳过: 4,
  };

  /** 页签：全部（行数合计）在前，后面各店（也显示行数，跟限流监控一致） */
  const tabs = computed(() => {
    const stores = data.value.stores;
    return [
      { key: ALL, label: "全部", count: stores.reduce((n, s) => n + s.rows.length, 0) },
      ...stores.map((s) => ({ key: s.store, label: s.store, count: s.rows.length })),
    ];
  });

  /** 当前页签对应的那份数据；「全部」是现合成的（每行补上 store，好加「店铺」列） */
  const current = computed<PresaleStore | undefined>(() => {
    if (storeKey.value === ALL) {
      const stores = data.value.stores;
      const rows = stores.flatMap((s) => s.rows.map((r) => ({ ...r, store: s.store })));
      // 有店用的是缓存（窗口没开 / 会话掉了）→ 把完整警告摆出来，别让人以为是最新的
      const bad = stores.filter((s) => (s.source || "").includes("⚠"));
      return {
        store: "全部",
        window: "两店合计",
        source: bad.length
          ? bad.map((s) => `${s.store}：${s.source}`).join("；")
          : stores.map((s) => `${s.store} ${s.live ? "实时" : "缓存"}`).join(" · "),
        live: stores.length > 0 && stores.every((s) => s.live),
        rowCount: rows.length,
        presaleCount: rows.filter((r) => r.canPresale).length,
        restockCount: rows.filter((r) => r.canRestock).length,
        rows,
      };
    }
    return (
      data.value.stores.find((s) => s.store === storeKey.value) || data.value.stores[0]
    );
  });

  async function load() {
    loading.value = true;
    try {
      const d = await PresaleAPI.getPresaleData();
      data.value = d;
      if (storeKey.value !== ALL && !d.stores.some((s) => s.store === storeKey.value)) {
        storeKey.value = ALL;
      }
      // 深链：大屏「补货与预售待办」点款号会带 ?code=BEL-xxxx 过来
      //   · 填进搜索框（keyword）
      //   · 并把筛选切到「全部」—— 默认的「待办」筛掉了一部分结论，
      //     不切的话点进来可能一片空白，让人以为没这个货号
      const code = String(route.query.code || "").trim();
      if (code) {
        keyword.value = code;
        filter.value = "all";
      }
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : "加载预售数据失败");
    } finally {
      loading.value = false;
    }
  }

  onMounted(load);
  onMounted(loadGoods);

  const num = (v: number | null | undefined, digits = 0) =>
    v === null || v === undefined || Number.isNaN(v)
      ? "—"
      : digits
        ? Number(v).toFixed(digits)
        : Math.round(Number(v)).toLocaleString("zh-CN");

  /** `2026-11-01` → `11-01`：表格里年份是噪音，省下来的宽度留给库存数字 */
  const shortDate = (d?: string) => (d ? String(d).slice(5) : "—");

  const isShort = (row: RowLike) =>
    row.verdict === "可开预售" ||
    ((row.stockDays ?? 99) < (data.value.rule.threshold || 7) && (row.forecast7 ?? 0) > 0);

  const verdictType = (v?: string) =>
    v === "可开预售" ? "danger" : v === "先加库存" ? "warning" : v === "要补货" ? "info" : "success";

  const byVerdict = (a: RowLike, b: RowLike) => {
    const d = (VERDICT_ORDER[a.verdict ?? ""] ?? 9) - (VERDICT_ORDER[b.verdict ?? ""] ?? 9);
    if (d !== 0) return d;
    return (a.stockDays ?? 0) - (b.stockDays ?? 0);
  };

  const displayRows = computed(() => {
    const rows = [...(current.value?.rows ?? [])];
    const kw = keyword.value.trim().toLowerCase();
    return rows
      .filter((r) => {
        if (filter.value === "presale" && !r.canPresale) return false;
        if (filter.value === "todo" && !r.canPresale && !r.canRestock && r.verdict !== "要补货") return false;
        if (!kw) return true;
        return [r.skuCode, r.title, r.probeSku].filter(Boolean).some((t) => String(t).toLowerCase().includes(kw));
      })
      .sort(byVerdict);
  });

  const rowClass = ({ row }: { row: RowLike }) =>
    row.canPresale ? "pre-row-presale" : row.canRestock ? "pre-row-restock" : "";

  /** 两店合计的「今天要处理」两张清单 */
  const allRows = computed(() => data.value.stores.flatMap((s) => s.rows));
  const presaleAll = computed(() => allRows.value.filter((r) => r.canPresale));
  const restockAll = computed(() => allRows.value.filter((r) => r.canRestock));

  const days = (r: RowLike) =>
    r.stockDays === null || r.stockDays === undefined ? "—" : r.stockDays.toFixed(1);

  const stats = computed(() => {
    const rows = current.value?.rows ?? [];
    const cnt = (v: string) => rows.filter((r) => r.verdict === v).length;
    const presale = rows.filter((r) => r.canPresale);
    const soonest = presale
      .map((r) => r.arrivalDays ?? 999)
      .filter((n) => n >= 0)
      .sort((a, b) => a - b)[0];
    return [
      {
        label: "本页尺码行",
        value: String(rows.length),
        hint: current.value?.window || "",
        color: "",
      },
      {
        label: "可开预售",
        value: String(presale.length),
        hint: soonest === undefined ? "没有符合条件的尺码" : `最近一批 ${soonest} 天后到仓`,
        color: presale.length ? "#e05561" : "#2fa37c",
      },
      {
        label: "可重新加库存",
        value: String(rows.filter((r) => r.canRestock).length),
        hint: "仓里有货，把 TEMU 可售库存加上去",
        color: rows.some((r) => r.canRestock) ? "#e0a33e" : "",
      },
      {
        label: "要补货（无在途）",
        value: String(cnt("要补货")),
        hint: "开预售也没货可发",
        color: cnt("要补货") ? "#599ce7" : "",
      },
    ];
  });

  // ---------------------------------------------------------------- 复制
  async function writeClipboard(text: string, tip: string) {
    try {
      await navigator.clipboard.writeText(text);
      ElMessage.success(tip);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        ElMessage.success(tip);
      } catch {
        ElMessage.error("复制失败，请手动选择");
      }
      document.body.removeChild(ta);
    }
  }

  const copyRow = (row: RowLike) => writeClipboard(String(row.skuCode), `已复制 ${row.skuCode}`);

  /** 「今天要处理」整段复制：两条清单，逐行带关键数 */
  const copyTodo = () => {
    if (!presaleAll.value.length && !restockAll.value.length) {
      return ElMessage.warning("今天两店都没有要处理的尺码");
    }
    const line = (r: typeof allRows.value[number]) =>
      `${r.skuCode}\t${r.status}\t今日 ${r.todaySales ?? "—"}\t预计7日 ${r.forecast7}\t现货 ${r.probeStockRaw}(折${r.probeStock})\t` +
      `够 ${days(r)} 天\t在途 ${r.transitRaw}(折${r.transit})\t到货 ${r.arrivalDate || "—"}`;
    const out = [];
    out.push(`【可开预售 ${presaleAll.value.length}】`);
    out.push(...(presaleAll.value.length ? presaleAll.value.map(line) : ["（无）"]));
    out.push("");
    out.push(`【可以重新加库存 ${restockAll.value.length}】`);
    out.push(...(restockAll.value.length ? restockAll.value.map(line) : ["（无）"]));
    writeClipboard(out.join("\n"), "已复制待办清单");
  };

  const copyList = () => {
    const rows = (current.value?.rows ?? []).filter((r) => r.canPresale);
    if (!rows.length) return ElMessage.warning("这一店没有可开预售的尺码");
    const lines = rows.map(
      (r) =>
        `${r.skuCode}\t${r.status}\t今日 ${r.todaySales ?? "—"}\t预测7日 ${r.forecast7}\t现货 ${r.probeStockRaw}(折${r.probeStock})\t` +
        `可售 ${r.stockDays?.toFixed(1)} 天\t在途 ${r.transitRaw}(折${r.transit})\t到货 ${r.arrivalDate}`
    );
    writeClipboard(lines.join("\n"), `已复制 ${rows.length} 个可开预售尺码`);
  };
</script>

<style lang="scss" scoped>
  .pre-page {
    padding-bottom: 16px;
  }

  .pre-header {
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
      max-width: 900px;
      margin: 0;
      font-size: 12px;
      line-height: 1.6;
      color: var(--el-text-color-secondary);
    }

    &__meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
  }

  .pre-alert {
    margin-bottom: 12px;

    &__body {
      font-size: 12px;
      line-height: 1.7;
      color: var(--el-text-color-regular);
    }

    &__warn {
      color: var(--el-color-warning);
    }
  }

  .pre-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 12px;

    &__num {
      font-size: 11px;
      opacity: 0.75;
    }
  }

  .pre-stats {
    margin-bottom: 12px;
  }

  .pre-stat {
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
      overflow: hidden;
      font-size: 11px;
      color: var(--el-text-color-placeholder);
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .pre-card {
    margin-bottom: 12px;

    &__head {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
    }

    &__title {
      font-size: 14px;
      font-weight: 600;
    }
  }

  .pre-toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;

    &__filters {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }
  }

  .pre-search {
    width: 220px;
  }

  .pre-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .pre-arrow {
    color: var(--el-text-color-placeholder);
  }

  .pre-avail-days {
    display: block;
    line-height: 1.3;
  }

  .pre-topup {
    color: var(--el-color-success);
    font-weight: 700;
  }

  .pre-today {
    color: var(--el-color-warning);
    font-weight: 700;
  }

  /* 「今日销量」列的下排：整款（同款变体/两店）合计，颜色区别于本尺码的今日销量 */
  .pre-family {
    display: block;
    margin-top: 1px;
    font-size: 11px;
    font-weight: 600;
    line-height: 1.25;
    color: var(--el-color-primary);
    cursor: help;
  }

  .pre-family--none {
    color: var(--el-text-color-placeholder);
    font-weight: 400;
    cursor: default;
  }

  /* 「可售 / 在仓 / 在途」列的下排：仓库侧数字，比 TEMU 可售次要一档 */
  .pre-stock2 {
    display: block;
    margin-top: 1px;
    font-size: 11px;
    line-height: 1.25;
    color: var(--el-text-color-regular);
    cursor: help;

    &__sep {
      margin: 0 3px;
      color: var(--el-text-color-placeholder);
    }
  }

  .pre-arrival-days {
    display: block;
    line-height: 1.3;
  }

  .pre-th-hint {
    margin-left: 2px;
    font-size: 12px;
    color: var(--el-text-color-placeholder);
    vertical-align: -2px;
  }

  .pre-warn {
    font-weight: 600;
    color: var(--el-color-warning);
  }

  .pre-zero {
    font-weight: 600;
    color: var(--el-color-danger);
  }

  :deep(.pre-row-presale) {
    --el-table-tr-bg-color: var(--el-color-danger-light-9);
  }

  :deep(.pre-row-restock) {
    --el-table-tr-bg-color: var(--el-color-warning-light-9);
  }

  .pre-todo {
    &__row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      padding: 4px 0;

      & + & {
        margin-top: 6px;
        border-top: 1px dashed var(--el-border-color-lighter);
        padding-top: 10px;
      }
    }

    &__label {
      flex: none;
    }
  }

  .pre-sku {
    cursor: pointer;
    font-variant-numeric: tabular-nums;

    &__store {
      margin-right: 4px;
      vertical-align: 1px;
    }

    &__hint {
      font-size: 11px;
      opacity: 0.8;
    }
  }

  .pre-action {
    margin: 0 1px;
  }

  .pre-actions {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 2px;
    align-items: center;
    justify-content: center;
  }
</style>
