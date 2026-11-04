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

    <!--
      ① 按产品：一个 SKU 一行，账号分开列。
      排序固定为「产品（款号+套系）→ 尺码 XS→XXL」：默认按 _product 排，
      点任何表头也只是在产品分组内重排，不会把同一产品的尺码拆散。
      ⚠ 2026-11-04 用户决定：这一视图改**分页**（每页 100 行），不用虚拟滚动 ——
      虚拟滚动下 tooltip 要等滚停才响应，体感迟滞；分页固定渲染 100 行，
      首屏快、悬停即出、每页数据也是完整的分组（产品不会被切在两页里）。
    -->
    <el-table v-if="view === 'product'" ref="tableRef" :data="paged" size="small" stripe border
              height="560" class="lx-table"
              :default-sort="{ prop: '_product', order: 'ascending' }">
      <el-table-column prop="productName" label="产品名（领星）" min-width="210" show-overflow-tooltip />
      <el-table-column prop="code" label="款号" width="92" sortable />
      <el-table-column prop="variant" label="套系" width="100" />
      <!--
        尺码：显示用 size，排序用隐藏的 _sizeKey（XS→S→M→L→XL→XXL→3XL…）。
        和 _product 一起构成固定排序，所以每个产品都是从小码排到大码。
      -->
      <el-table-column prop="_sizeKey" label="尺码" width="80" align="center" sortable>
        <template #default="{ row }">{{ row.size || "—" }}</template>
      </el-table-column>
      <!--
        ⚠ 2026-11-04 用户要求「留着红框的列」：总库存 / 可用 / 帕沃尔 加州3 / 帕沃尔 Kansas / 核对。
        锁定、在途、恒信、仓库分布几列都去掉了。
        「可用」改成 **恒信可用**（领星 LM739 的 availableAmount）—— 帕沃尔的现货看右面两列，
        不再混进这个数里。
      -->
      <el-table-column label="总库存" width="110" align="right" sortable :sort-by="'total'">
        <template #default="{ row }"><b>{{ num(row.total) }}</b></template>
      </el-table-column>
      <el-table-column label="恒信可用" width="110" align="right" sortable :sort-by="'avail'">
        <template #default="{ row }">
          <el-tooltip placement="top" content="领星 LM739（恒信）的可用库存，可立即发">
            <span>{{ num(row.avail) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <!--
        在途：领星 transportAmount（件数）。
        ⚠ 2026-11-04 用户要求：**不要悬浮提示**，点数字直接跳到货件跟踪页，
        并带上这一行的「款号-套系-尺码」关键词（?kw=），落地即筛出这几票货。
      -->
      <el-table-column label="在途" width="96" align="right" sortable :sort-by="'transit'">
        <template #default="{ row }">
          <el-link v-if="row.code && (row.transit || 0) > 0" type="info" :underline="false"
                   :href="'#'+shipmentUrl(row)">
            {{ num(row.transit) }}
          </el-link>
          <span v-else class="lx-dim">{{ num(row.transit) }}</span>
        </template>
      </el-table-column>
      <!--
        帕沃尔两个仓各一列，代发库存（现货，可发），取自
        https://www.powerwarehouse.net/stock.php?act=list。
        领星 14450（帕沃尔）2026-11-02 停用后，加州3 只能从这里看到；两个仓不相加。
      -->
      <el-table-column label="帕沃尔 加州3" width="112" align="right" sortable
                       :sort-by="(row: never) => pwSortKey('加州3仓', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row, '加州3仓')">
            <span v-if="pwAt('加州3仓', row) === null" class="lx-dim">—</span>
            <el-link v-else-if="(pwAt('加州3仓', row) || 0) > 0" type="success"
                     :underline="false" @click="openRefs('加州3仓', row)">
              {{ num(pwAt('加州3仓', row)) }}
            </el-link>
            <span v-else class="lx-dim">{{ num(pwAt('加州3仓', row)) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="帕沃尔 Kansas" width="122" align="right" sortable
                       :sort-by="(row: never) => pwSortKey('kansas仓', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row, 'kansas仓')">
            <span v-if="pwAt('kansas仓', row) === null" class="lx-dim">—</span>
            <el-link v-else-if="(pwAt('kansas仓', row) || 0) > 0" type="success"
                     :underline="false" @click="openRefs('kansas仓', row)">
              {{ num(pwAt('kansas仓', row)) }}
            </el-link>
            <span v-else class="lx-dim">{{ num(pwAt('kansas仓', row)) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <!-- 领星缺尺码 ≠ 没有这个 SKU（全仓 0 就不返回该行），点这里去产品库核对 -->
      <el-table-column label="核对" width="76" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="primary" :underline="false" target="_blank"
                   :href="productUrl(`${row.code}-${row.variant}-${row.size}`)">
            查产品
          </el-link>
          <span v-else class="lx-dim">—</span>
        </template>
      </el-table-column>
      <!-- 帕沃尔库存页核对：帕沃尔两列的数字以那个页面为准，点过去直接看 -->
      <el-table-column label="帕沃尔核对" width="104" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="success" :underline="false" target="_blank"
                   :href="PW_LIST_URL">
            查库存
          </el-link>
          <span v-else class="lx-dim">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-table v-else-if="view === 'warehouse'" :data="shown" size="small" stripe border
              height="560" virtual class="lx-table">
      <el-table-column prop="wh" label="仓库" width="190" />
      <el-table-column prop="acctLabel" label="账号" width="94" />
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
      <!--
        领星缺尺码 ≠ 没这个 SKU：integratedInventory 只返回「有库存记录」的行，
        某个尺码全仓为 0 时整行不出现（用户 2026-11-04 说明）。
        这里给一个直达 @oms.xlwms.com/product/list 的查询链接核对。
      -->
      <el-table-column label="核对" width="76" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="primary" :underline="false" target="_blank"
                   :href="productUrl(`${row.code}-${row.variant}-${row.size}`)">
            查产品
          </el-link>
          <span v-else class="lx-dim">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="sku" label="SKU" width="140" show-overflow-tooltip />
      <el-table-column prop="operateTime" label="最后变动" width="150" />
    </el-table>


    <!--
      分页条 + 代发明细弹窗放在三个表格之后 —— 它们不是 el-table 条件链的一部分，
    <!-- ③ 原始明细（同样虚拟滚动） -->
    <el-table v-else :data="shown" size="small" stripe border height="560" virtual class="lx-table">
      <el-table-column prop="acctLabel" label="账号" width="90" />
      <el-table-column prop="wh" label="仓库" width="180" />
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

    -->
    <!-- 分页条（只给「按产品」用；总数在筛选结果上算，搜索后自动回到第 1 页） -->
    <el-pagination
      v-if="view === 'product'"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      class="lx-pager"
      size="small"
      background
      layout="total, sizes, prev, pager, next, jumper"
      :total="totalRows"
      :page-sizes="[50, 100, 200, 500]"
    />

    <!--
      帕沃尔代发库存明细弹窗：点「帕沃尔 加州3 / Kansas」里大于 0 的数字打开。
      内容 = 帕沃尔后台点同一个数字看到的那张表，这里只留用户要的「参考单号」。
    -->
    <el-dialog v-model="refsOpen" width="720px" :title="refsTitle" append-to-body>
      <div class="lx-refs__meta">
        {{ refsWh }} · 代发库存 <b>{{ refsStock }}</b> 件 ·
        参考单号 <b>{{ refsList.length }}</b> 个 ·
        <span class="lx-dim">抓取 {{ refs.generatedAt || "—" }}</span>
      </div>
      <el-table :data="refsList" size="small" stripe border max-height="420">
        <el-table-column prop="ref" label="参考单号" width="130">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="copyText(row.ref)">
              {{ row.ref }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="qty" label="件数" width="80" align="right" />
        <el-table-column prop="left" label="剩余" width="80" align="right" />
        <el-table-column prop="in" label="入库时间" width="160" />
        <el-table-column label="箱号" min-width="240">
          <template #default="{ row }">
            <span class="lx-refs__box">{{ (row.boxes || []).join(" / ") || "—" }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!refsList.length" class="lx-refs__empty">
        {{ refs.generatedAt
          ? "还没抓到这个 SKU 的参考单号（pw-refs.json 里没有它，可能这版还没跑到）"
          : "参考单号还没生成（每天 17:30 随库存任务一起抓）" }}
      </div>
      <div class="lx-refs__tip">
        参考单号就是帕沃尔后台点「代发库存」数字弹出来的那张表里的「参考单号」，可直接复制去查。
        <el-link type="success" :underline="false" :href="PW_LIST_URL" target="_blank">
          去帕沃尔库存页 →
        </el-link>
      </div>
      <template #footer>
        <el-button @click="refsOpen = false">关闭</el-button>
      </template>
    </el-dialog>

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
      夹在 v-if / v-else 之间会让 Vue 编译报「v-else has no adjacent v-if」。

    <!-- ② 按仓库（虚拟滚动：原始明细行数上千，且没有 tooltip 重的列） -->

</template>

<script setup lang="ts">
  import StockTrackAPI from "@/api/temu/stock-track";
  import type {
    LinxingInboundData,
    LinxingItem,
    LinxingStockData,
    PwRefItem,
    PwRefsData,
    PwStockData,
  } from "@/api/temu/stock-track";

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
    // Kansas/加州3 单独拉；失败不影响领星主体（那几列显示 —）。
    // 拉回来立刻建索引（只建一次），避免首屏渲染时同步重算卡住主线程。
    try {
      pw.value = await StockTrackAPI.getPwStock();
      buildPwIndex(pw.value.items);
    } catch {
      /* 静默：帕沃尔两列显示 — 即可，不用打断整个视图 */
    }
    // 在途入库单（84 KB）：只用来做「在途」列的悬停明细，失败就显示"没生成"
    try {
      inbound.value = await StockTrackAPI.getLinxingInbound();
    } catch {
      /* 静默 */
    }
    // 帕沃尔参考单号（点代发库存数字用），失败时弹窗提示"还没生成"
    try {
      refs.value = await StockTrackAPI.getPwRefs();
    } catch {
      /* 静默 */
    }
  }
  onMounted(load);

  /**
   * 帕沃尔索引：先按仓分，再按「款号|变体|尺码」归一化后聚合。
   *
   * 两边的 SKU 写法不同（领星 `BS2-204203-4SetA-M`、帕沃尔 `BS-207220-4SetA-XXL`），
   * 不能直接拿 productName 对，所以按三元组对齐。
   * ⚠ 2026-11-04 性能：原来用 computed，进页面时第一次渲染要同步建两遍索引
   * （pwIndex + pwRowIndex，各 ~1300 行），再叠加 900+ 个产品分组的排序，
   * 主线程会卡住 1 秒以上 —— 这就是「一进页面就卡」的主因。
   * 现在改成加载完只建一次（立即执行，不进 computed），渲染时纯查表。
   */
  const pwIndex = shallowRef(new Map<string, Map<string, number>>());
  /** 帕沃尔「款|变体|尺码」→ { 仓: 代发库存 }（合成一行时用，与 pwIndex 同源） */
  const pwRowIndex = shallowRef(new Map<string, Record<string, number>>());

  function buildPwIndex(list: PwStockData["items"]) {
    const m = new Map<string, Map<string, number>>();
    const m2 = new Map<string, Record<string, number>>();
    for (const i of list || []) {
      const wh = String(i.wh || "").trim();
      const key =
        i.code && i.variant && i.size
          ? `${i.code}|${i.variant}|${i.size}`
          : `name|${String(i.sku || "").trim()}`;
      let byWh = m.get(wh);
      if (!byWh) {
        byWh = new Map<string, number>();
        m.set(wh, byWh);
      }
      byWh.set(key, (byWh.get(key) || 0) + (Number(i.stock) || 0));

      if (!key.startsWith("name|")) {
        const d = m2.get(key) || {};
        d[wh] = (d[wh] || 0) + (Number(i.stock) || 0);
        m2.set(key, d);
      }
    }
    pwIndex.value = m;
    pwRowIndex.value = m2;
  }

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

  /**
   * 「按产品」视图分页（用户 2026-11-04 决定：分页代替虚拟滚动）。
   *
   * 为什么：虚拟滚动下每行只在进入视口时才创建，悬停提示（tooltip）要等
   * 滚停后才正常响应，体感"迟滞"；而 900+ 行一次性渲染又会卡首屏。
   * 分页每页 100 行 —— 渲染量固定、tooltip 即点即出，翻页也不需要重新排序。
   */
  const pageSize = ref(100);
  const page = ref(1);

  // 筛选/搜索/排序一变就回到第 1 页，避免停在越界页码上出现空表
  watch([keyword, acctPick, whPick, minQty, view], () => {
    page.value = 1;
  });

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

  /**
   * 排序：**先按「产品」分组，组内再按尺码 XS→XXL**。
   *
   * ⚠ 2026-11-04（用户第三次纠偏）：光按「款号→套系→尺码」排还不够稳 ——
   * 表格点过「尺码」表头后，el-table 会按那一列整体重排，于是 4SetA 和 4SetC
   * 变成 S/S、M/M、L/L 交错（用户截图正是这个）。现在的做法：
   *   ① 用「款号+套系」拼成 _product，作为**产品分组键**，组间按它排；
   *   ② 组内按 _sizeKey 排（XS→XXL）；
   *   ③ 表格默认按 _product 排序，点表头排序时**永远先比较 _product**，
   *      所以无论怎么点，同一个产品的尺码都不会被拆开。
   */
  const PICK_CN = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

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
      .map((r) => ({
        ...r,
        // 产品分组键：款号 + 套系（解析不出三元组时退回产品名）
        _product: r.code ? `${r.code} ${r.variant}`.trim() : String(r.productName || ""),
        _sizeKey: String(r.sizeOrder ?? 99).padStart(3, "0"),
        _whs: Object.entries(r._whMap)
          .map(([wh, total]) => ({ wh, total }))
          .sort((x, y) => y.total - x.total),
      }))
      .sort(
        (a, b) =>
          PICK_CN.compare(a._product, b._product) || PICK_CN.compare(a._sizeKey, b._sizeKey)
      );
  });

  /** 领星产品库查询链接：缺尺码时去这里核对（用户 2026-11-04 给的路径） */
  const productUrl = (kw: string) =>
    `https://oms.xlwms.com/product/list?keyword=${encodeURIComponent(kw)}`;

  /** 帕沃尔库存页（「帕沃尔核对」列点过去看代发库存） */
  const PW_LIST_URL = "https://www.powerwarehouse.net/stock.php?act=list";

  /**
   * 「在途」列跳货件跟踪：带上这一行的「款号-套系」做关键词（**不带尺码**）。
   *
   * ⚠ 2026-11-04 用户反馈：原来带的是 `款号-套系-尺码`（如 103901-5SetC-S），
   * 落地后货件跟踪只筛出含这个尺码的票，跟用户在库存表看到的在途对不上 ——
   * 因为一票入库单通常混装同套系的多个尺码。改成只带 `款号-套系`
   * （如 103901-5SetC），落地显示这一套系的所有在途票，能看出整票的去向。
   */
  const shipmentUrl = (row: unknown) => {
    const r = row as { code?: string; variant?: string };
    const kw = r.code ? `${r.code}-${r.variant}` : "";
    return `/product/shipment-track?kw=${encodeURIComponent(kw)}`;
  };

  /**
   * 帕沃尔参考单号（点「代发库存」数字弹窗）。
   *
   * 数据来自 pw-refs.json：帕沃尔库存页点数字 → 弹窗
   * `stock.php?act=huojia_log&ck_sku=<Fnsku>&mdck=<仓ID>` 里那张表的「参考单号」，
   * 按 Fnsku 索引（同一个 Fnsku 在两个仓是两个不同入口，分开查）。
   */
  const refs = ref<PwRefsData>({
    generatedAt: "",
    skus: 0,
    rows: 0,
    map: {},
    byWh: {},
    rule: { note: "", source: "" },
  });
  const refsOpen = ref(false);
  const refsTitle = ref("");
  const refsWh = ref("");
  const refsStock = ref(0);
  const refsList = ref<PwRefItem[]>([]);

  /** 该行在指定仓的 Fnsku（弹窗/链接都用它，别用卖家 SKU） */
  const pwFnsku = (wh: string, row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string };
    if (!r.code || !r.variant || !r.size) return "";
    const key = `${r.code}|${r.variant}|${r.size}`;
    for (const i of pw.value.items || []) {
      if (
        String(i.wh || "").trim() === wh &&
        i.code &&
        `${i.code}|${i.variant}|${i.size}` === key
      ) {
        return String((i as { fnsku?: string }).fnsku || "");
      }
    }
    return "";
  };

  function openRefs(wh: string, row: unknown) {
    const r = row as { code?: string; variant?: string; size?: string };
    refsWh.value = wh;
    refsStock.value = pwAt(wh, row) ?? 0;
    refsTitle.value = `帕沃尔 ${wh} · ${r.code}-${r.variant}-${r.size} · 代发明细`;
    const fn = pwFnsku(wh, row);
    refsList.value = fn ? refs.value.map[fn] || [] : [];
    refsOpen.value = true;
  }

  /** 复制单号（点参考单号即复制，方便粘贴去查） */
  async function copyText(t: string) {
    try {
      await navigator.clipboard.writeText(t);
      ElMessage.success(`已复制 ${t}`);
    } catch {
      ElMessage.info(t);
    }
  }

  /** 领星在途入库单（{款|套系|尺码} → 单号列表），用于「在途」列的悬停明细 */
  const inbound = ref<LinxingInboundData>({
    generatedAt: "",
    orders: 0,
    open: 0,
    skus: 0,
    total: {},
    map: {},
    rule: { note: "", source: "" },
  });
  const inboundKey = (row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string };
    return r.code && r.variant && r.size ? `${r.code}|${r.variant}|${r.size}` : "";
  };

  /**
   * 「在途」悬停说明：直接列出在途落在哪几张入库单。
   * 领星库存接口只给在途件数、不给单号，这份单号表是
   * scripts/goods/fetch_linxing_inbound.py 每天跑一次生成的。
   */
  const inboundTip = (row: unknown) => {
    const t = Number((row as { transit?: number }).transit) || 0;
    const key = inboundKey(row);
    const list = key ? inbound.value.map[key] : undefined;
    if (!t && !list?.length) return "在途 0 件";
    const lines = [`在途 ${num(t)} 件（领星 LM739）`];
    if (list?.length) {
      lines.push("入库单：");
      for (const o of list.slice(0, 8)) {
        lines.push(
          `  ${o.no}  ${o.qty} 件  预计 ${o.expected || "—"}` +
          (o.late ? "  ⚠ 已逾期" : "") + `  ${o.status}`
        );
      }
      if (list.length > 8) lines.push(`  …另有 ${list.length - 8} 张单`);
    } else {
      lines.push(
        inbound.value.generatedAt
          ? "（这份在途没有找到对应的未到齐入库单，可能已在途中转仓/单号未关联）"
          : "（入库单明细还没生成）"
      );
    }
    return lines.join("\n");
  };

  /** 「按产品」表格实际渲染的行 = 排序结果按页切片（每页 100 行） */
  const paged = computed<Row[]>(() => {
    const start = (page.value - 1) * pageSize.value;
    return shown.value.slice(start, start + pageSize.value);
  });

  const totalRows = computed(() => shown.value.length);

  /** 翻页/改每页数量后把表格滚回顶部，不然会停在上一页的滚动位置 */
  const tableRef = ref();
  watch([page, pageSize], () => {
    tableRef.value?.setScrollTop?.(0);
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
  .lx-pw {    color: #16a34a;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    border-bottom: 1px dashed rgba(22, 163, 74, 0.45);
    cursor: help;
  }

  /* 分页条：紧跟在「按产品」表格下面 */
  .lx-pager {
    margin: 8px 0 4px;
    justify-content: flex-end;
  }

  /* 帕沃尔代发明细弹窗 */
  .lx-refs {
    &__meta {
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--el-text-color-regular);
    }

    &__box {
      font-size: 11.5px;
      color: var(--el-text-color-secondary);
      font-variant-numeric: tabular-nums;
    }

    &__empty {
      padding: 18px 0;
      font-size: 12.5px;
      text-align: center;
      color: var(--el-text-color-placeholder);
    }

    &__tip {
      margin-top: 10px;
      font-size: 11.5px;
      line-height: 1.7;
      color: var(--el-text-color-secondary);
    }
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
