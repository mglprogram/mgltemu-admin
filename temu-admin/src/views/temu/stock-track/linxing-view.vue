<!--
  领星总库存（恒信 LM739，全量、可查询）+ 帕沃尔两个仓并排看

  2026-11-02 用户要求：把两个店铺的总库存放到库存跟踪页下（现 /product/stock-track；
  当天稍后又把「库存跟踪 / 货件跟踪」整组搬到与 TEMU 运营同级的「产品-库存」），
  加查询功能，并细分「产品 - 尺码 - 哪个账号」的库存。

  ⚠ 2026-11-02 晚用户通知：**领星 14450（帕沃尔）账号已停用**，以后只去 LM739 校验；
  加州3 的库存**改从帕沃尔库存链接更新**。所以本视图的数据源变成两份：
    ① public/data/linxing-stock.json  —— 领星 LM739（恒信）
    ② public/data/pw-stock.json       —— 帕沃尔库存页 加州3仓 + kansas仓
  两个帕沃尔仓各占一列，跟领星口径并排对照，不相加。

  三种看法（切换）：
    ① 按产品：同一个 SKU 的多仓行合并成一行，右侧拆「领星账号 / 帕沃尔两仓」几列 ——
       这就是用户要的「细分到产品-尺码-哪个账号」。
    ② 按仓库：每仓一行，看货具体压在哪个仓。
    ③ 原始明细：接口返回什么就显示什么，便于对账。
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
              height="560" class="lx-table" :default-sort="{ prop: 'sizeOrder', order: 'ascending' }">
      <el-table-column prop="productName" label="产品名（领星）" min-width="210" fixed show-overflow-tooltip />
      <el-table-column prop="code" label="款号" width="92" sortable />
      <el-table-column prop="variant" label="套系" width="100" />
      <!--
        尺码：显示用 size，排序用隐藏的 sizeOrder（XS→S→M→L→XL→XXL→3XL…）。
        默认就按它升序，所以搜一个款时看到的就是从小码到大码，不再乱序。
      -->
      <el-table-column prop="sizeOrder" label="尺码" width="80" align="center" sortable>
        <template #default="{ row }">{{ row.size || "—" }}</template>
      </el-table-column>
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
        帕沃尔两个仓各一列，和领星口径并排看。
        领星 14450（帕沃尔）2026-11-02 停用后，加州3 只能从这里看到；
        Kansas 一直只有帕沃尔后台的数据是准的。两个仓不相加。
      -->
      <el-table-column label="帕沃尔 加州3" width="112" align="right" sortable
                       :sort-by="(row: never) => pwSortKey('加州3仓', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row, '加州3仓')">
            <span v-if="pwAt('加州3仓', row) === null" class="lx-dim">—</span>
            <span v-else class="lx-pw">{{ num(pwAt('加州3仓', row)) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="帕沃尔 Kansas" width="122" align="right" sortable
                       :sort-by="(row: never) => pwSortKey('kansas仓', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row, 'kansas仓')">
            <span v-if="pwAt('kansas仓', row) === null" class="lx-dim">—</span>
            <span v-else class="lx-pw">{{ num(pwAt('kansas仓', row)) }}</span>
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
      <p><b>领星账号</b>：<span v-for="a in data.accounts" :key="a.tag" class="lx-acct-tag">
        {{ a.label }}（{{ a.tag }}）{{ a.ok ? `${a.rows} 行` : "拉取失败" }}
      </span><span class="lx-dim">14450（帕沃尔）2026-11-02 起已停用，不再拉取</span></p>
      <p><b>帕沃尔</b>：<span v-for="w in pw.whs || []" :key="w.wh" class="lx-acct-tag">
        {{ w.wh }} {{ w.rows }} 行 / 代发库存 {{ num(w.stock) }}
      </span><span class="lx-dim">抓取时间 {{ pw.generatedAt || "—" }}；加州3 由帕沃尔库存页提供</span></p>
      <p><b>口径</b>：{{ data.rule.note }}</p>
      <p><b>数据源</b>：{{ data.rule.source }}；{{ pw.rule.source }}</p>
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

  /** 帕沃尔库存（加州3仓 + kansas仓，独立数据源，用于并排那两列） */
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
   * 帕沃尔索引：先按仓分，再按「款号|变体|尺码」归一化后聚合。
   *
   * 两边的 SKU 写法不同（领星 `BS2-204203-4SetA-M`、帕沃尔 `BS-207220-4SetA-XXL`），
   * 不能直接拿 productName 对。帕沃尔那边也已经解析出 code/variant/size 三个字段
   * （fetch_pw_stock.py 里复用同一套 split_sku），所以按三元组对齐最稳。
   * 解析不出来的（非我们款号体系的老 SKU）保留原始名做兜底匹配。
   */
  const pwIndex = computed(() => {
    const m = new Map<string, Map<string, number>>();
    for (const i of pw.value.items || []) {
      const wh = String(i.wh || "").trim();
      let byWh = m.get(wh);
      if (!byWh) {
        byWh = new Map<string, number>();
        m.set(wh, byWh);
      }
      const key =
        i.code && i.variant && i.size
          ? `${i.code}|${i.variant}|${i.size}`
          : `name|${String(i.sku || "").trim()}`;
      byWh.set(key, (byWh.get(key) || 0) + (Number(i.stock) || 0));
    }
    return m;
  });

  /** 帕沃尔「款|变体|尺码」→ { 仓: 代发库存 }（合成一行时用，与 pwIndex 同源） */
  const pwRowIndex = computed(() => {
    const m = new Map<string, Record<string, number>>();
    for (const [wh, byWh] of pwIndex.value) {
      for (const [key, v] of byWh) {
        if (key.startsWith("name|") || !v) continue;
        const d = m.get(key) || {};
        d[wh] = (d[wh] || 0) + v;
        m.set(key, d);
      }
    }
    return m;
  });

  /** 某行在指定帕沃尔仓的代发库存；匹配不到返回 null（显示 —） */
  const pwAt = (wh: string, row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string; productName?: string };
    const byWh = pwIndex.value.get(wh);
    if (!byWh) return null;
    if (r.code && r.variant && r.size) {
      const v = byWh.get(`${r.code}|${r.variant}|${r.size}`);
      if (v !== undefined) return v;
    }
    const v2 = byWh.get(`name|${String(r.productName || "").trim()}`);
    return v2 === undefined ? null : v2;
  };

  /** 排序键：el-table 的 sort-by 只接受字符串，用补零让数字按大小排 */
  const pwSortKey = (wh: string, row: unknown) =>
    String(pwAt(wh, row) ?? -1).padStart(8, "0");

  const pwTip = (row: unknown, wh: string) => {
    const v = pwAt(wh, row);
    if (v === null) {
      return `帕沃尔 ${wh} 没有这个 SKU（或该 SKU 已零库存被过滤）`;
    }
    return `帕沃尔 ${wh} 代发库存 ${num(v)} 件（现货，可发）\n` +
      (wh === "加州3仓"
        ? "领星 14450（帕沃尔）已停用，加州3 以帕沃尔库存页为准"
        : "领星是把各仓汇总的，这里单看 Kansas 仓");
  };

  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  /** 帕沃尔某仓的代发库存合计 / 有货 SKU 数（统计卡用） */
  const pwWhSum = (wh: string) =>
    (pw.value.items || [])
      .filter((i) => String(i.wh || "").trim() === wh)
      .reduce((s, i) => s + (Number(i.stock) || 0), 0);
  const pwWhHas = (wh: string) =>
    (pw.value.items || []).filter(
      (i) => String(i.wh || "").trim() === wh && (Number(i.stock) || 0) > 0
    ).length;

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
    const accts = d.accounts.map((a) => `${a.label} ${a.ok ? a.rows + " 行" : "失败"}`).join(" / ");
    return [
      {
        label: "总库存（领星恒信）",
        value: num(d.totalQty),
        hint: accts || "领星全部仓库合计",
        color: "#0f766e",
      },
      { label: "可用库存", value: num(d.availQty), hint: "可立即发", color: "#2563eb" },
      { label: "明细行数", value: num(d.total), hint: "账号×SKU×仓库", color: "#7c3aed" },
      {
        label: "帕沃尔 加州3",
        value: num(pwWhSum("加州3仓")),
        hint: `${pwWhHas("加州3仓")} 个 SKU 有货（领星 14450 已停用，改看这里）`,
        color: "#0891b2",
      },
      {
        label: "帕沃尔 Kansas",
        value: num(pwWhSum("kansas仓")),
        hint: `${pwWhHas("kansas仓")} 个 SKU 有货`,
        color: "#16a34a",
      },
      {
        label: "仓库数",
        value: `${d.warehouses.length}`,
        hint: d.warehouses.map(shortWh).join(" / ") || "—",
        color: "#64748b",
      },
    ];
  });

  // ---------------------------------------------------------------- 过滤
  /**
   * 尺码排序键（XS→S→M→L→XL→XXL→3XL…）。
   *
   * 用户 2026-11-04：搜某个款时希望按 S/XS → XXL 的顺序排，而不是乱序。
   * 直接按字符串排会得到 "L, M, S, XL, XXL, XS"，所以给每个行算一个数字键，
   * 页面用隐藏的 sizeOrder 列做默认排序（el-table 自己按数字排）。
   * 认不出的尺码（空格 / "0" / 自由尺码）排最后，按原字符串兜底。
   */
  const SIZE_ORDER = [
    "XXS", "2XS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "XXXL",
    "4XL", "5XL", "6XL",
  ];
  const sizeOrder = (size: string) => {
    const s = String(size || "").trim().toUpperCase();
    const i = SIZE_ORDER.indexOf(s);
    return i >= 0 ? i : SIZE_ORDER.length;
  };

  /** 是否补「帕沃尔独有尺码」行（关掉就只看领星那批） */
  const shownPwOnly = ref(true);

  const filtered = computed<LinxingItem[]>(() => {
    const kw = keyword.value.trim().toLowerCase();
    const rows = data.value.items
      .filter((i) => {
        if (acctPick.value && i.acct !== acctPick.value) return false;
        if (whPick.value && i.wh !== whPick.value) return false;
        if (minQty.value > 0 && i.total < minQty.value) return false;
        if (!kw) return true;
        return [i.productName, i.code, i.variant, i.size, i.sku, i.wh]
          .some((t) => String(t || "").toLowerCase().includes(kw));
      })
      .map((i) => ({ ...i, sizeOrder: sizeOrder(i.size) } as LinxingItem));

    // ── 帕沃尔独有的尺码补进来（领星没有、但加州3/Kansas 有货）──
    // 只在没选账号（或选了帕沃尔）时补；关键词同时匹配「款-套系-尺码」，
    // 这样搜 204203-4SetA 就能看到 XL 这一行，而不是被悄悄吞掉。
    if (shownPwOnly.value && (!acctPick.value || acctPick.value === "pw")) {
      const seen = new Set(rows.map((i) => rowKeyOf(i)));
      for (const [key, byWh] of pwRowIndex.value) {
        if (seen.has(key)) continue;
        const [code, variant, size] = key.split("|");
        const name = `${code}-${variant}-${size}`;
        if (kw && !`${name} ${code} ${variant} ${size}`.toLowerCase().includes(kw)) continue;
        const stock = Object.values(byWh).reduce((s, v) => s + v, 0);
        if (stock <= 0) continue;
        rows.push({
          acct: "", acctLabel: "帕沃尔", acctShort: "pw", wh: "", whCode: "",
          sku: "", productName: name, code, variant, size,
          sizeOrder: sizeOrder(size),
          total: 0, avail: 0, lock: 0, transit: 0, box: 0, fbaReturn: 0,
          stockType: null, operateTime: "",
        } as LinxingItem);
      }
    }
    return rows;
  });

  type Row = LinxingItem & { _whs?: { wh: string; total: number }[] };

  /**
   * 行的合并键 = 「款号|套系|尺码」三元组（解析不出时退回原始产品名）。
   *
   * ⚠ 2026-11-04 修：原来按 productName（领星原始名）合并，而帕沃尔那两列
   * 是另一份数据（pw-stock.json），SKU 前缀跟领星不是一一对应
   * （同一款在帕沃尔还有 BS- / BH- / BS2- / BK- 并存），
   * 于是领星没有、帕沃尔有的尺码永远进不来 ——
   * 典型现象：204203-4SetA 少了 XL（领星确实没这个 XL，只有帕沃尔加州3 有 282 件）。
   * 改成按三元组合并后，帕沃尔独有的尺码会以「总库存 0 / 可用 0、加州3 有数」的行出现。
   */
  const rowKeyOf = (i: {
    code?: string;
    variant?: string;
    size?: string;
    productName?: string;
    sku?: string;
  }) =>
    i.code && i.variant && i.size
      ? `${i.code}|${i.variant}|${i.size}`
      : `name|${i.productName || i.sku || ""}`;

  const shown = computed<Row[]>(() => {
    if (view.value !== "product") return filtered.value as Row[];
    const map = new Map<string, Row & { _acctQty: Record<string, number>; _whMap: Record<string, number> }>();
    for (const i of filtered.value) {
      const key = rowKeyOf(i);
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

    return [...map.values()]
      .sort(
        (a, b) =>
          String(a.code || a.productName || "").localeCompare(String(b.code || b.productName || "")) ||
          String(a.variant || "").localeCompare(String(b.variant || "")) ||
          (a.sizeOrder ?? 99) - (b.sizeOrder ?? 99)
      )
      .map((r) => ({
        ...r,
        _whs: Object.entries(r._whMap)
          .map(([wh, total]) => ({ wh, total }))
          .sort((x, y) => y.total - x.total),
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

  /* 帕沃尔两个仓列：与领星账号区分开，用绿色 */
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
