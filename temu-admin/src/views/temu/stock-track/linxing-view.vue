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
        ⚠ 2026-11-05 用户要求：「总库存」= 恒信可用 + 帕沃尔两仓代发库存。
        原来是领星 totalAmount（含在途/箱/退货），跟"能发出去的货"不是一回事。
      -->
      <el-table-column label="总库存" width="120" align="right" sortable
                       :sort-by="(row: never) => grandTotalKey(row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="grandTotalTip(row)">
            <b>{{ num(grandTotal(row)) }}</b>
          </el-tooltip>
        </template>
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
        用 `target="_blank"` 开新标签：货件跟踪是 keepAlive 页面（缓存键 = fullPath），
        在同一标签里跳过去会命中那个「上次无关键词」的旧缓存实例，搜索框不更新。
      -->
      <el-table-column label="在途" width="96" align="right" sortable :sort-by="'transit'">
        <template #default="{ row }">
          <el-link v-if="row.code && (row.transit || 0) > 0" type="info" :underline="false"
                   target="_blank" rel="noopener" :href="'#'+shipmentUrl(row)">
            {{ num(row.transit) }}
          </el-link>
          <span v-else class="lx-dim">{{ num(row.transit) }}</span>
        </template>
      </el-table-column>
      <!--
        帕沃尔在途：帕沃尔库存页的「代发途中」两仓之和（加州3 + Kansas）。
        ⚠ 2026-11-04 用户要求：悬浮显示**参考单号 + 日期**（点数字弹窗里那张表的内容）。
        领星的「在途」不含帕沃尔的货，这一列补上那一块，避免看着像"帕沃尔没在途"。
      -->
      <!--
        7天日均：来自 warehouse-probe 看板的「近7天日均」（件/天，款级字段）。
        用户 2026-11-05 要求把它加进领星总库存，方便和总库存一起看「还能卖几天」。
      -->
      <el-table-column label="7天日均" width="100" align="right" sortable :sort-by="'daily'">
        <template #default="{ row }">
          <el-tooltip placement="top" content="warehouse-probe 看板的「近7天日均」（件/天）。领星自身的库存接口不含销量，这一列是按「款号-套系-尺码」对齐过来的。">
            <span :class="{ 'lx-dim': !row.daily }">
              {{ row.daily ? row.daily.toFixed(1) : "—" }}
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="帕沃尔在途" width="112" align="right" sortable
                       :sort-by="(row: never) => pwSortKeyBy('transit', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTransitTip(row)" raw-content>
            <span :class="{ 'lx-dim': !asRow(row)._pwT, 'lx-pw': asRow(row)._pwT }">
              {{ num(asRow(row)._pwT) }}
            </span>
          </el-tooltip>
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
            <span v-if="asRow(row)._ca3 == null" class="lx-dim">—</span>
            <el-link v-else-if="(asRow(row)._ca3 ?? 0) > 0" type="success"
                     :underline="false" @click="openRefs('加州3仓', row)">
              {{ num(asRow(row)._ca3) }}
            </el-link>
            <span v-else class="lx-dim">{{ num(asRow(row)._ca3) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="帕沃尔 Kansas" width="122" align="right" sortable
                       :sort-by="(row: never) => pwSortKey('kansas仓', row)">
        <template #default="{ row }">
          <el-tooltip placement="top" :content="pwTip(row, 'kansas仓')">
            <span v-if="asRow(row)._ks == null" class="lx-dim">—</span>
            <el-link v-else-if="(asRow(row)._ks ?? 0) > 0" type="success"
                     :underline="false" @click="openRefs('kansas仓', row)">
              {{ num(asRow(row)._ks) }}
            </el-link>
            <span v-else class="lx-dim">{{ num(asRow(row)._ks) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <!--
        核对 = 恒信在领星的「产品库存」报表页（https://oms.xlwms.com/report/stock）。

        ⚠ 2026-11-04 用户实测定案：那页是 SPA，URL 参数**完全不起作用**
          （searchKey / keyword / productName 都试过，服务端返回同一个壳，
           前端也没有读 query 的逻辑），所以这里就做一个**纯跳转**，
          不要再塞假参数、也不弹"已复制"的提示 —— 用户体验就是"点了打开页面"。
          进页面后把搜索下拉切成「产品名称」再输入即可。
      -->
      <el-table-column label="核对" width="76" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="primary" :underline="false" target="_blank" rel="noopener"
                   :href="productUrl(lxSku(row) || rowKeyword(row))" @click="copyKeyword(row)">
            查产品
          </el-link>
          <span v-else class="lx-dim">—</span>
        </template>
      </el-table-column>
      <!--
        帕沃尔库存页核对：带「款号-套系-尺码」作为关键词过去搜
        （2026-11-04 用户要求：这一列也要携带关键词，而不是只打开库存首页）。
        帕沃尔页面的搜索参数实测是 `keywords`（stock.php 表单里的 name）。
      -->
      <el-table-column label="帕沃尔核对" width="104" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="success" :underline="false" target="_blank"
                   :href="pwSearchUrl(rowKeyword(row))">
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
        这里跳恒信产品库存页 report/stock 核对。
      -->
      <el-table-column label="核对" width="76" align="center">
        <template #default="{ row }">
          <el-link v-if="row.code" type="primary" :underline="false" target="_blank" rel="noopener"
                   :href="productUrl(lxSku(row) || rowKeyword(row))" @click="copyKeyword(row)">
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
    RefChannelData,
    PwStockData,
  } from "@/api/temu/stock-track";

  defineOptions({ name: "LinxingStockPanel" });

  /**
   * 父页面通过 ?kw= 传进来的关键词（库存跟踪页「查产品」跳过来时用）。
   * 数据本来就是我们自己拉的 306 行，不必跳去恒信网页版。
   */
  const props = defineProps<{ kw?: string }>();

  const loading = ref(false);
  const view = ref<"product" | "warehouse" | "raw">("product");
  const keyword = ref(props.kw || "");
  watch(
    () => props.kw,
    (v) => {
      keyword.value = String(v || "");
      page.value = 1;
    }
  );
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
    // ⚠ 2026-11-04 性能：四份 JSON 原来是**串行** await（99+205+249+84 KB，
    //   首屏实测 8.4 秒）。改成并发拉，只用「领星那份」决定首屏能不能出表，
    //   其余三份到了再补（都不阻塞）。帕沃尔索引在它自己的 promise 里建。
    const pLx = StockTrackAPI.getLinxingStock();
    const pPw = StockTrackAPI.getPwStock().then((d) => {
      pw.value = d;
      buildPwIndex(d.items);
      return d;
    });
    const pIn = StockTrackAPI.getLinxingInbound().then((d) => (inbound.value = d));
    const pRef = StockTrackAPI.getPwRefs().then((d) => (refs.value = d));
    const pCh = StockTrackAPI.getRefChannel().then((d) => (channel.value = d));

    try {
      data.value = await pLx;
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载领星库存失败");
    } finally {
      loading.value = false;
    }
    // 其余三份失败都不打断视图（对应列显示 —，悬浮提示"还没生成"）
    await Promise.allSettled([pPw, pIn, pRef, pCh]);
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
  /** 帕沃尔在途：仓 → 「款|变体|尺码」→ 代发途中件数（页面「帕沃尔在途」列） */
  const pwTransitIndex = shallowRef(new Map<string, Map<string, number>>());
  /**
   * 「仓|款|变体|尺码」→ Fnsku 数组。
   *
   * ⚠ 2026-11-04 性能：原来 pwFnskuAll() **每次都遍历 1,295 行** pw.items 找 Fnsku，
   *   而它被悬浮提示、弹窗、搜索过滤反复调用 —— 实测输入一个字符要 243~1400ms，
   *   首屏还有个 861ms 长任务。改成加载时一次建档，运行时 O(1) 查表。
   */
  const pwFnskuIndex = shallowRef(new Map<string, string[]>());

  function buildPwIndex(list: PwStockData["items"]) {
    const m = new Map<string, Map<string, number>>();
    const m2 = new Map<string, Record<string, number>>();
    const mt = new Map<string, Map<string, number>>();
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

      // 代发途中（在途）单独一张表 —— 与代发库存是两回事，不能混
      const tr = Number(i.transit) || 0;
      if (tr) {
        let tByWh = mt.get(wh);
        if (!tByWh) {
          tByWh = new Map<string, number>();
          mt.set(wh, tByWh);
        }
        tByWh.set(key, (tByWh.get(key) || 0) + tr);
      }

      if (!key.startsWith("name|")) {
        const d = m2.get(key) || {};
        d[wh] = (d[wh] || 0) + (Number(i.stock) || 0);
        m2.set(key, d);
      }
    }
    const fi = new Map<string, string[]>();
    for (const i of list || []) {
      const fn = String((i as { fnsku?: string }).fnsku || "");
      if (!fn || !i.code || !i.variant || !i.size) continue;
      const k = String(i.wh || "").trim() + "|" + i.code + "|" + i.variant + "|" + i.size;
      const arr = fi.get(k);
      if (!arr) fi.set(k, [fn]);
      else if (!arr.includes(fn)) arr.push(fn);
    }
    pwFnskuIndex.value = fi;
    pwIndex.value = m;
    pwRowIndex.value = m2;
    pwTransitIndex.value = mt;
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

  /** 某行在指定帕沃尔仓的**在途**（代发途中）；没有返回 0 */
  const pwTransitAt = (wh: string, row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string; productName?: string };
    const byWh = pwTransitIndex.value.get(wh);
    if (!byWh) return 0;
    if (r.code && r.variant && r.size) {
      const v = byWh.get(`${r.code}|${r.variant}|${r.size}`);
      if (v !== undefined) return v;
    }
    const v2 = byWh.get(`name|${String(r.productName || "").trim()}`);
    return v2 === undefined ? 0 : v2;
  };

  /** 排序键：el-table 的 sort-by 只接受字符串，用补零让数字按大小排 */
  const pwSortKey = (wh: string, row: unknown) =>
    String(pwAt(wh, row) ?? -1).padStart(8, "0");

  /** 帕沃尔在途 = 两仓「代发途中」之和 */
  const pwTransit = (row: unknown) =>
    (pwTransitAt("加州3仓", row) || 0) + (pwTransitAt("kansas仓", row) || 0);

  const pwSortKeyBy = (field: "transit", row: unknown) =>
    String(field === "transit" ? pwTransit(row) : 0).padStart(8, "0");

  /**
   * 「帕沃尔在途」列悬浮 = 一张小表：参考单号 / 日期 / 件数 / 仓库。
   *
   * ⚠ 2026-11-04 用户反馈"这排版看不懂"：原来是四个值用 `·` 串成一行，
   * 而且 Kansas 仓有些「参考单号」其实是 **20 位的快递/监管号**
   * （如 96310913502…），串在一起完全读不出来。现在改成：
   *   · 表格对齐（单号 / 日期 / 件数 / 仓），一眼能对号；
   *   · 超长单号**截断显示**（保留后 8 位），完整值放 title 里，鼠标停上去能看全；
   *   · 按日期倒序，最多 8 条，超出提示还有几条。
   */
  const pwTransitTip = (row: unknown) => {
    const t = pwTransit(row);
    const all: PwRefItem[] = [];
    // ⚠ 用 transitMap（代发途中那批），不是 map（代发库存那批）——
    //   两者是两批不同的货，混用会显示成错误的单号。
    // ⚠ 2026-11-04 用户报错：同一个「款-套系-尺码」在帕沃尔有**多个 Fnsku**
    //   （不同店铺前缀 BH-/BK-/BS-…，各自一行），原来把它们的单号直接拼起来，
    //   于是同一个 144503067 出现两次。这里按「单号」去重，件数取最大值
    //   （同一单号在 2 个 Fnsku 下各记一份，不代表货有两份）。
    const byRef = new Map<string, PwRefItem>();
    seenPwFnskus(row).forEach((fn) => {
      for (const it of refs.value.transitMap?.[fn] || []) {
        const prev = byRef.get(it.ref);
        if (!prev) byRef.set(it.ref, { ...it });
        else prev.qty = Math.max(prev.qty, it.qty);
      }
    });
    all.push(...byRef.values());
    if (!all.length) {
      return t
        ? `<b>${num(t)}</b> 件在途<br><span style="color:#94a3b8">这批还没抓到入库单号，稍后同步</span>`
        : "0 件在途";
    }
    // 日期倒序（新单在上），日期取入库时间的前 10 位
    all.sort((a, b) => String(b.in || "").localeCompare(String(a.in || "")));
    const show = all.slice(0, 8);
    const cell = "padding:2px 8px;white-space:nowrap;";
    // 渠道列：帕沃尔系统里没有，来自金山「2026发货申请表」（用户 2026-11-05 要求）
    const head =
      `<tr style="color:#94a3b8"><td style="${cell}">参考单号</td>` +
      `<td style="${cell}">渠道</td>` +
      `<td style="${cell}">日期</td><td style="${cell}text-align:right">件数</td>` +
      `<td style="${cell}">仓</td></tr>`;
    const body = show
      .map((it) => {
        const full = String(it.ref || "");
        // 非业务单号（20 位快递/监管号）只显示后 8 位并淡化，一长串读者根本看不懂
        const fake = it.isRef === false || full.length > 12;
        const shown = fake ? "…" + full.slice(-8) : full;
        const st = fake ? `${cell};color:#94a3b8` : cell;
        const chan = channel.value.map[full] || "";
        const chanStyle = chan
          ? `${cell};color:#16a34a;font-weight:600`
          : `${cell};color:#94a3b8`;
        return (
          `<tr><td style="${st}" title="${full}">${shown}</td>` +
          `<td style="${chanStyle}">${chan || "—"}</td>` +
          `<td style="${cell}">${String(it.in || "—").slice(0, 10)}</td>` +
          `<td style="${cell}text-align:right">${num(it.qty)}</td>` +
          `<td style="${cell}">${(it.wh || "").replace("仓", "")}</td></tr>`
        );
      })
      .join("");
    const more =
      all.length > 8
        ? `<div style="color:#94a3b8;padding-top:2px">…另有 ${all.length - 8} 条</div>`
        : "";
    return (
      `<div style="font-size:12px">在途合计 <b>${num(t)}</b> 件` +
      `（${all.length} 个单号）</div>` +
      `<table style="margin-top:4px;border-collapse:collapse;font-size:12px">${head}${body}</table>` +
      more
    );
  };

  /** 这一行在帕沃尔两个仓的 Fnsku 列表（取参考单号用） */
  const seenPwFnskus = (row: unknown) => {
    const out: string[] = [];
    for (const wh of ["加州3仓", "kansas仓"]) {
      for (const fn of pwFnskuAll(wh, row)) {
        if (!out.includes(fn)) out.push(fn);
      }
    }
    return out;
  };

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

  /**
   * 「总库存」= 恒信可用 + 帕沃尔加州3 + 帕沃尔 Kansas（三者都是"现在能发出去的货"）。
   *
   * 用户 2026-11-05 明确要求。原来是领星 totalAmount（含在途/箱库存/FBA退货），
   * 口径和这列的名字对不上；现在三个加数都是可用/现货。
   */
  const grandTotal = (row: unknown) => {
    const r = asRow(row);
    return (Number(r.avail) || 0) + (Number(r._ca3) || 0) + (Number(r._ks) || 0);
  };

  /** 排序键：el-table 的 sort-by 只认字符串，补零让数字按大小排 */
  const grandTotalKey = (row: unknown) =>
    String(Math.round(grandTotal(row))).padStart(9, "0");

  /** 「总库存」的悬停：把三个加数摊开，方便对账 */
  const grandTotalTip = (row: unknown) => {
    const r = asRow(row);
    return [
      `总库存 ${num(grandTotal(row))} 件 = 恒信可用 + 帕沃尔两仓现货`,
      `  恒信可用 ${num(r.avail)}`,
      `  帕沃尔 加州3 ${num(r._ca3 ?? 0)}`,
      `  帕沃尔 Kansas ${num(r._ks ?? 0)}`,
    ].join("\n");
  };

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

  /** el-table 插槽给的是宽类型（有时被推断成 never），统一收窄成 Row */
  const asRow = (row: unknown) => row as Row;

  type Row = LinxingItem & {
    _whs?: { wh: string; total: number }[];
    /** 预先算好的帕沃尔加州3 代发库存（null = 没有这个 SKU） */
    _ca3?: number | null;
    /** 预先算好的帕沃尔 Kansas 代发库存 */
    _ks?: number | null;
    /** 预先算好的帕沃尔两仓在途合计 */
    _pwT?: number;
  };

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
      )
      .map((r) => {
        // ⚠ 2026-11-04 性能：模板里每个格子要调 pwAt() 2~3 次（判空/判>0/取值），
        // 一页 100 行 × 多列就是上千次 Map 查找 + 对象解构，首屏出现 861ms 长任务。
        // 这里一次性算好挂到行上，模板直接读字段。
        (r as Row & Record<string, unknown>)._ca3 = pwAt("加州3仓", r);
        (r as Row & Record<string, unknown>)._ks = pwAt("kansas仓", r);
        (r as Row & Record<string, unknown>)._pwT = pwTransit(r);
        return r;
      });
  });

  /**
   * 一行统一的搜索关键词 = 「款号-套系-尺码」。
   *
   * 三处外链（查产品 / 帕沃尔查库存 / 在途跳货件）都用它，保证口径一致。
   * code 为空时从原始 SKU 按 `-` 切后三段兜底（**绝不能取页面截断显示的产品名**，
   * 那会变成 `2-4SetA-S`，2026-11-04 踩过）。
   */
  const rowKeyword = (row: unknown) => {
    const r = row as {
      code?: string;
      variant?: string;
      size?: string;
      sku?: string;
      productName?: string;
    };
    if (r.code && r.variant && r.size) return `${r.code}-${r.variant}-${r.size}`;
    const raw = String(r.sku || r.productName || "");
    const parts = raw.split("-").filter(Boolean);
    return parts.length >= 3 ? parts.slice(-3).join("-") : raw;
  };

  /**
   * 「查产品」= 恒信在领星的**产品库存页** `https://oms.xlwms.com/report/stock`。
   *
   * ⚠ 2026-11-04 用户实测纠正：这页的 `searchKey` **只按 SKU 匹配**，
   *   传「款号-套系-尺码」（204203-4SetA-L）筛不出东西；要传**领星 SKU**
   *   （如 D14460JZCFT，就是页面上「SKU」那一列），落地才是那一行。
   *   所以优先用本行的领星 `sku`，没有才退回拼接词。
   */
  const productUrl = (kw: string) =>
    `https://oms.xlwms.com/report/stock?searchKey=${encodeURIComponent(kw)}`;

  /** 这一行的领星 SKU（查恒信产品库存要用它，不是产品名） */
  const lxSku = (row: unknown) => {
    const r = row as { code?: string; variant?: string; size?: string; sku?: string };
    if (r.sku) return String(r.sku);
    if (!r.code) return "";
    const hit = (data.value.items || []).find(
      (i) => i.code === r.code && i.variant === r.variant && i.size === r.size
    );
    return hit ? String(hit.sku || "") : "";
  };

  /** 点「查产品」：恒信那页只认 SKU，顺手把 SKU 复制到剪贴板 */
  function copyKeyword(row: unknown) {
    const kw = lxSku(row) || rowKeyword(row);
    navigator.clipboard?.writeText(kw).then(
      () => ElMessage.success("已复制 SKU " + kw),
      () => ElMessage.info(kw)
    );
  }

  /** 帕沃尔库存页（带关键词直接搜） */
  const PW_LIST_URL = "https://www.powerwarehouse.net/stock.php?act=list";
  const pwSearchUrl = (kw: string) =>
    `${PW_LIST_URL}&keywords=${encodeURIComponent(kw)}`;

  /**
   * 「在途」列跳货件跟踪：带这一行的「款号-套系-尺码」。
   *
   * ⚠ 2026-11-04 第五轮定位：截图里搜的是 `2-4SetA-S` —— 不是「款号截断」，
   * 而是这一行的 code 为空时兜底取了 productName，而 productName 在**表格里
   * 被 el-table 截断显示**（`2-4SetA-S` 是 `BS2-204002-4SetA-S` 的可视尾部）。
   * 现在兜底改为**从原始 SKU 里按 - 分段取**，并且校验拼出来的词至少含 3 位数字，
   * 不满足就退回产品名的完整值（不截断）。
   */
  const shipmentUrl = (row: unknown) => {
    const r = row as {
      code?: string;
      variant?: string;
      size?: string;
      sku?: string;
      productName?: string;
    };
    // ① 正常路径：款号/套系/尺码都有 → 直接拼
    let kw = r.code && r.variant && r.size ? `${r.code}-${r.variant}-${r.size}` : "";
    // ② 兜底：从原始 SKU / 产品名里按 - 切出后三段（不受表格显示截断影响）
    if (!kw) {
      const raw = String(r.sku || r.productName || "");
      const parts = raw.split("-").filter(Boolean);
      if (parts.length >= 3) kw = parts.slice(-3).join("-");
      else kw = raw;
    }
    return `/product/shipment-track?view=all&kw=${encodeURIComponent(kw)}`;
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
    transitMap: {},
    byWh: {},
    rule: { note: "", source: "" },
  });
  const refsOpen = ref(false);
  const refsTitle = ref("");
  const refsWh = ref("");
  const refsStock = ref(0);
  const refsList = ref<PwRefItem[]>([]);

  /**
   * 该行在指定仓的**全部** Fnsku（弹窗/链接都用它，别用卖家 SKU）。
   *
   * ⚠ 2026-11-04 用户报错：原来只返回**第一个**匹配到的 Fnsku。
   * 同一个「款-套系-尺码」在帕沃尔常有多个店铺 SKU（BH-/BK-/BS-/BLA-… 各一个 Fnsku），
   * 在途可能挂在**没有库存的那个** Fnsku 上（如 1026-3BHW-XXL：
   * BK-R1026 有 56 件现货、BS-R1026 有 30 件在途），只取第一个就漏了在途单号。
   * 现在返回数组，调用方自己合并并按单号去重。
   */
  const pwFnskuAll = (wh: string, row: unknown): string[] => {
    const r = row as { code?: string; variant?: string; size?: string };
    if (!r.code || !r.variant || !r.size) return [];
    const k = wh + "|" + r.code + "|" + r.variant + "|" + r.size;
    return pwFnskuIndex.value.get(k) || [];
  };

  function openRefs(wh: string, row: unknown) {
    const r = row as { code?: string; variant?: string; size?: string };
    refsWh.value = wh;
    refsStock.value = pwAt(wh, row) ?? 0;
    refsTitle.value = `帕沃尔 ${wh} · ${r.code}-${r.variant}-${r.size} · 代发明细`;
    // 合并该行所有 Fnsku 的代发库存单号（按单号去重，件数取最大）
    const byRef = new Map<string, PwRefItem>();
    for (const fn of pwFnskuAll(wh, row)) {
      for (const it of refs.value.map[fn] || []) {
        const prev = byRef.get(it.ref);
        if (!prev) byRef.set(it.ref, { ...it });
        else prev.qty = Math.max(prev.qty, it.qty);
      }
    }
    refsList.value = [...byRef.values()];
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

  /**
   * 参考单号 → 运输渠道（美森/以星/盐田）。
   * 帕沃尔系统里没有渠道信息，这份来自金山「2026发货申请表」（用户 2026-11-05 要求）。
   */
  const channel = ref<RefChannelData>({
    generatedAt: "", count: 0, map: {}, raw: {}, rule: { note: "", source: "" },
  } as RefChannelData);

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
